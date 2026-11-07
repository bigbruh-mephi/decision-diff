"""Worker: consumes broker tasks, runs CPU stages in a process pool, commits state to PostgreSQL, then acks.

Every handler is idempotent: duplicates are recognised by state guards in PostgreSQL, so at-least-once delivery
gives exactly-once effects. Deterministic errors (bad procedure, bad input) fail the chunk immediately; transient
errors are rejected for broker-delayed retry and dead-lettered after the delivery limit.
"""

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import socket
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

import orjson
from psycopg.types.json import Jsonb

from . import ai, db, pipeline, queue, whatif
from .engine.formats import FormatError
from .engine.procedures import ProcedureError
from .sandbox import SandboxError
from .settings import settings

log = logging.getLogger("dd.worker")
DETERMINISTIC = (ProcedureError, FormatError, FileNotFoundError, ValueError, SandboxError)


class Worker:
    def __init__(self, pool, ch, procs):
        self.pool, self.ch = pool, ch
        self.ex = ProcessPoolExecutor(procs, mp_context=multiprocessing.get_context("forkserver"))
        self.trusted = ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("forkserver"))
        self.name = f"{socket.gethostname()}:{os.getpid()}"

    async def cpu(self, fn, *args):
        """Sandbox launchers: these pool processes never run DuckDB themselves and stay single-threaded."""
        return await asyncio.get_running_loop().run_in_executor(self.ex, fn, *args)

    async def cpu_trusted(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.trusted, fn, *args)

    async def publish_all(self, tasks):
        for t in tasks:
            await queue.publish(self.ch, queue.WORK, t)

    async def dispatch(self, user_id):
        async with self.pool.connection() as c:
            tasks = await db.dispatch(c, user_id, settings.run_window)
        await self.publish_all(tasks)

    async def finalize_now(self, run_id, user_id):
        await queue.publish(self.ch, queue.FINALIZE, {"kind": "finalize", "run_id": run_id, "user_id": user_id})

    async def on_ingest(self, t):
        run_id = t["run_id"]
        async with self.pool.connection() as c:
            run = await (await c.execute("SELECT state FROM runs WHERE id = %s", (run_id,))).fetchone()
            if not run or run["state"] != "receiving":
                return
            first, have, done = await db.open_batch(c, run_id, t["source"])
            if not done:
                await db.event(c, run_id, "ingest.started", {"source": t["source"], "resumed": bool(have)})
        try:
            items = await self.cpu_trusted(pipeline.list_pairs, t["source"], t.get("sample"))
        except DETERMINISTIC as e:
            async with self.pool.connection() as c:
                await c.execute("UPDATE runs SET state = 'failed', error = %s, finished_at = now() WHERE id = %s",
                                (f"ingest: {e}", run_id))
                await db.event(c, run_id, "run.failed", {"error": f"ingest: {e}"})
            return
        size, in_dir = settings.chunk_apps, settings.run_dir(run_id) / "in"
        for k in range(0, len(items), size):
            n = first + k // size
            if n in have:
                continue
            uri = str(in_dir / f"chunk-{n:06d}.zip")
            apps = await self.cpu_trusted(pipeline.cut_chunk, items[k:k + size], uri)
            async with self.pool.connection() as c:
                await db.add_chunks(c, run_id, [(n, uri, apps)])
            await self.dispatch(t["user_id"])
        requested = False
        async with self.pool.connection() as c:
            if not done:
                requested = await db.close_batch(c, run_id, t["source"], -(-len(items) // size))
                await db.event(c, run_id, "ingest.done", {"chunks": -(-len(items) // size), "apps": len(items)})
        if t.get("seal") or requested:
            async with self.pool.connection() as c:
                flip = await db.seal(c, run_id)
            if flip:
                await self.finalize_now(run_id, t["user_id"])

    async def on_chunk(self, t):
        run_id, no = t["run_id"], t["chunk_no"]
        async with self.pool.connection() as c:
            claim = await db.claim_chunk(c, run_id, no, self.name)
        if not claim:
            return
        out_dir = settings.run_dir(run_id) / "out" / f"chunk-{no:06d}.{claim['attempts']}"
        try:
            out, stats, stages = await self.cpu(pipeline.run_chunk, t["input_uri"], str(out_dir),
                                                await self.snapshots(claim["procedures"]))
        except DETERMINISTIC as e:
            async with self.pool.connection() as c:
                flip = await db.fail_chunk(c, run_id, no, f"{type(e).__name__}: {e}")
            if flip:
                await self.finalize_now(run_id, t["user_id"])
            await self.dispatch(t["user_id"])
            return
        async with self.pool.connection() as c:
            flip = await db.complete_chunk(c, run_id, no, out, stats, stages)
        if flip:
            await self.finalize_now(run_id, t["user_id"])
        await self.dispatch(t["user_id"])

    async def on_finalize(self, t):
        run_id = t["run_id"]
        async with self.pool.connection() as c:
            got = await (await c.execute("SELECT pg_try_advisory_lock(hashtextextended(%s::text, 9)) AS ok",
                                         (run_id,))).fetchone()
            if not got["ok"]:
                return
            try:
                run = await (await c.execute("SELECT state, procedures, report_params FROM runs WHERE id = %s",
                                             (run_id,))).fetchone()
                if not run or run["state"] != "finalizing":
                    return
                dirs = [r["output_uri"] for r in await (await c.execute(
                    "SELECT output_uri FROM chunks WHERE run_id = %s AND state = 'done' ORDER BY chunk_no",
                    (run_id,))).fetchall()]
                await db.event(c, run_id, "finalize.started", {"chunks": len(dirs)})
                await c.commit()
                run_file = settings.run_dir(run_id) / "run.duckdb"
                try:
                    if not dirs:
                        raise ValueError("no chunk finished successfully")
                    snaps = await self.snapshots(run["procedures"])
                    stats, stages = await self.cpu_trusted(pipeline.build_run, dirs, str(run_file),
                                                           run["report_params"] or {})
                    t_checks = time.time()
                    checks = [r for part in await asyncio.gather(*(
                        self.cpu(pipeline.run_checks, str(run_file), [snap]) for snap in snaps["checks"])) for r in part]
                    stages.append({"stage": "checks", "code_sha": None, "status": "done", "started_at": t_checks,
                                   "duration_ms": (time.time() - t_checks) * 1000, "rows_in": len(snaps["checks"]),
                                   "rows_out": sum(len(r["rows"]) for r in checks), "error": None})
                except Exception as e:
                    await c.execute("UPDATE runs SET state = 'failed', error = %s, finished_at = now() WHERE id = %s",
                                    (f"finalize: {type(e).__name__}: {e}", run_id))
                    await db.event(c, run_id, "run.failed", {"error": str(e)})
                    await c.commit()
                    return
                await db.journal(c, run_id, -1, stages)
                for r in checks:
                    await c.execute("""
                        INSERT INTO check_results (run_id, check_id, check_sha, label, rows, duration_ms, error)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id, check_id, check_sha) DO UPDATE
                        SET rows = excluded.rows, duration_ms = excluded.duration_ms, error = excluded.error""",
                                    (run_id, r["check_id"], r["sha"], r["label"], Jsonb(r["rows"]), r["duration_ms"],
                                     r["error"]))
                await c.execute("UPDATE runs SET state = 'done', stats = %s, finished_at = now() WHERE id = %s",
                                (Jsonb(stats), run_id))
                await db.event(c, run_id, "run.done", stats)
                if settings.llm_enabled:
                    await c.execute("""INSERT INTO ai_opinions (run_id, state, model) VALUES (%s, 'pending', %s)
                                       ON CONFLICT (run_id) DO NOTHING""", (run_id, settings.llm_model))
                await c.commit()
                if settings.llm_enabled:
                    await queue.publish(self.ch, queue.INTERACTIVE, {"kind": "ai", "run_id": run_id})
            finally:
                await c.rollback()
                await c.execute("SELECT pg_advisory_unlock(hashtextextended(%s::text, 9))", (run_id,))
                await c.commit()

    async def snapshots(self, procs):
        """Attach code to the run's procedure snapshot ``{kind: {sha, params}}`` (checks: list)."""
        async with self.pool.connection() as c:
            async def full(s, kind):
                row = await db.code(c, s["sha"])
                return {**s, "kind": kind, "code": row["code"], "name": row["name"]}
            out = {k: await full(v, k) for k, v in procs.items() if k != "checks"}
            out["checks"] = [await full(v, "check") for v in procs.get("checks", [])]
            return out

    async def on_check(self, t):
        """Re-run one check on a finished run (interactive queue); rows join the run's check table."""
        run_id = t["run_id"]
        async with self.pool.connection() as c:
            row = await db.code(c, t["sha"])
        snap = {"sha": t["sha"], "code": row["code"], "name": row["name"], "params": t.get("params") or {}}
        res = (await self.cpu(pipeline.run_checks, str(settings.run_dir(run_id) / "run.duckdb"), [snap]))[0]
        async with self.pool.connection() as c:
            await c.execute("""
                INSERT INTO check_results (run_id, check_id, check_sha, label, rows, duration_ms, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id, check_id, check_sha) DO UPDATE
                SET rows = excluded.rows, duration_ms = excluded.duration_ms, error = excluded.error,
                    created_at = now()""",
                            (run_id, res["check_id"], res["sha"], res["label"], Jsonb(res["rows"]), res["duration_ms"],
                             res["error"]))
            await db.event(c, run_id, "check.done", {"check_id": res["check_id"], "error": res["error"]})

    async def on_cell(self, t):
        """Run workspace code in the sandbox; the result is stored for the editor to poll."""
        async with self.pool.connection() as c:
            got = await (await c.execute("UPDATE cell_runs SET state = 'running' WHERE id = %s AND state = 'queued' "
                                         "RETURNING id", (t["exec_id"],))).fetchone()
            row = await db.code(c, t["sha"])
        if not got:
            return
        run_file = str(settings.run_dir(t["run_id"]) / "run.duckdb") if t.get("run_id") else None
        try:
            res = await self.cpu(pipeline.run_cell, row["code"], run_file)
            state = "failed" if res.get("error") else "done"
        except Exception as e:
            res, state = {"error": f"{type(e).__name__}: {e}", "stdout": ""}, "failed"
        async with self.pool.connection() as c:
            await c.execute("UPDATE cell_runs SET state = %s, result = %s, finished_at = now() WHERE id = %s",
                            (state, Jsonb(res), t["exec_id"]))

    async def on_ai(self, t):
        """AI second opinion of a finished run in the trusted pool (no user code); the result lands in ai_opinions."""
        run_id = t["run_id"]
        async with self.pool.connection() as c:
            row = await (await c.execute("""SELECT r.report_params FROM ai_opinions a JOIN runs r ON r.id = a.run_id
                                            WHERE a.run_id = %s AND a.state = 'pending' AND r.state = 'done'""",
                                         (run_id,))).fetchone()
            if not row:
                return
            checks = await (await c.execute("""SELECT DISTINCT ON (check_id) label, rows FROM check_results
                                               WHERE run_id = %s ORDER BY check_id, created_at DESC""",
                                            (run_id,))).fetchall()
        try:
            res = await self.cpu_trusted(ai.opinion, str(settings.run_dir(run_id) / "run.duckdb"),
                                         row["report_params"] or {}, checks)
        except Exception as e:
            res = {"state": "failed", "model": settings.llm_model, "findings": [], "error": f"{type(e).__name__}: {e}"}
        async with self.pool.connection() as c:
            await c.execute("UPDATE ai_opinions SET state = %s, model = %s, result = %s, finished_at = now() "
                            "WHERE run_id = %s", (res["state"], res["model"], Jsonb(res), run_id))
            await db.event(c, run_id, "ai.done", {"state": res["state"], "findings": len(res["findings"])})

    async def on_whatif(self, t):
        """What-if on a finished run: arrays prepared once per procedure version (trusted pool), then the procedure
        runs in the sandbox; the outcome codes land in ``whatif_runs`` for the API to poll and aggregate."""
        async with self.pool.connection() as c:
            got = await (await c.execute("UPDATE whatif_runs SET state = 'running' WHERE id = %s AND state = 'queued' "
                                         "RETURNING run_id, code_sha, \"values\"", (t["exec_id"],))).fetchone()
            row = got and await db.code(c, got["code_sha"])
        if not got:
            return
        run_dir = settings.run_dir(got["run_id"])
        try:
            meta = await self.cpu_trusted(whatif.prepare, str(run_dir / "run.duckdb"), row["code"],
                                          str(run_dir / "whatif" / got["code_sha"]), got["values"])
            res = await self.cpu(whatif.evaluate, row["code"], row["name"], meta, got["values"])
            state = "done"
        except Exception as e:
            res, state = {"error": f"{type(e).__name__}: {e}"}, "failed"
        async with self.pool.connection() as c:
            await c.execute("UPDATE whatif_runs SET state = %s, result = %s, finished_at = now() WHERE id = %s",
                            (state, Jsonb(res), t["exec_id"]))

    async def on_dead(self, t):
        if t.get("kind") == "chunk":
            async with self.pool.connection() as c:
                flip = await db.fail_chunk(c, t["run_id"], t["chunk_no"], "delivery limit exceeded")
            if flip:
                await self.finalize_now(t["run_id"], t["user_id"])
            await self.dispatch(t["user_id"])

    async def handle(self, message):
        async with message.process(requeue=True, ignore_processed=True):
            t = orjson.loads(message.body)
            try:
                await getattr(self, f"on_{t['kind']}")(t)
            except Exception:
                log.error("task %s failed:\n%s", message.message_id, traceback.format_exc())
                raise

    async def handle_dead(self, message):
        async with message.process(requeue=True):
            await self.on_dead(orjson.loads(message.body))


async def sweep(w, every=15):
    """Leader-elected sweeper: republish chunks queued for too long and runs stuck in finalizing."""
    while True:
        await asyncio.sleep(every)
        try:
            async with w.pool.connection() as c:
                got = await (await c.execute("SELECT pg_try_advisory_xact_lock(4243) AS ok")).fetchone()
                if not got["ok"]:
                    continue
                stale = await (await c.execute("""
                    SELECT c.run_id, c.chunk_no, c.input_uri, c.apps, r.owner_id FROM chunks c JOIN runs r ON r.id = c.run_id
                    WHERE c.state = 'queued' AND c.queued_at < now() - interval '10 minutes'""")).fetchall()
                stuck = await (await c.execute("""
                    SELECT id, owner_id FROM runs WHERE state = 'finalizing'
                    AND NOT EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = runs.id
                                    AND e.ts > now() - interval '10 minutes')""")).fetchall()
                await c.execute("UPDATE chunks SET queued_at = now() WHERE state = 'queued' "
                                "AND queued_at < now() - interval '10 minutes'")
            await w.publish_all([{"kind": "chunk", "run_id": str(r["run_id"]), "user_id": str(r["owner_id"]),
                                  "chunk_no": r["chunk_no"], "input_uri": r["input_uri"], "apps": r["apps"]}
                                 for r in stale])
            for r in stuck:
                await w.finalize_now(str(r["id"]), str(r["owner_id"]))
        except Exception:
            log.exception("sweeper")


async def main(queues, procs):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = await db.pool(max_size=procs + 4)
    await db.migrate(p)
    conn, ch = await queue.connect()
    w = Worker(p, ch, procs)
    consume = await conn.channel()
    await consume.set_qos(prefetch_count=procs)
    for q in queues:
        await (await consume.get_queue(q)).consume(w.handle)
    await (await consume.get_queue(queue.DEAD)).consume(w.handle_dead)
    log.info("worker %s consuming %s with %d processes", w.name, queues, procs)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    sweeper = asyncio.create_task(sweep(w))
    await stop.wait()
    log.info("worker %s stopping", w.name)
    sweeper.cancel()
    await consume.close()
    w.ex.shutdown(wait=True, cancel_futures=True)
    w.trusted.shutdown(wait=True, cancel_futures=True)
    await conn.close()
    await p.close()


def cli():
    ap = argparse.ArgumentParser(prog="dd-worker")
    ap.add_argument("--queues", default=f"{queue.WORK},{queue.FINALIZE},{queue.INTERACTIVE}")
    ap.add_argument("--procs", type=int, default=settings.worker_procs)
    a = ap.parse_args()
    asyncio.run(main(a.queues.split(","), a.procs))


if __name__ == "__main__":
    cli()
