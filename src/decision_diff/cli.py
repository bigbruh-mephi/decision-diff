"""Command line: ``dd submit <run_dir>`` registers placed procedures, creates a run and submits it to the broker."""

import argparse
import asyncio
import time

from . import catalog, db, queue
from .settings import settings


async def submit(source, name, owner, wait):
    p = await db.pool()
    await db.migrate(p)
    async with p.connection() as c:
        user = await db.ensure_user(c, owner)
        ids = await catalog.sync_files(c, settings.procedures_dir, user)
        kinds = {}
        for pid in ids.values():
            kind = (await (await c.execute("SELECT kind FROM procedures WHERE id = %s", (pid,))).fetchone())["kind"]
            kinds.setdefault(kind, []).append(pid)
        sel = {k: kinds[k][0] for k in ("normalize", "compare", "segments")}
        sel["checks"] = kinds.get("check", [])
        run_id = await db.create_run(c, user, name, await catalog.snapshot(c, sel))
    conn, ch = await queue.connect()
    t0 = time.perf_counter()
    await queue.publish(ch, queue.WORK, {"kind": "ingest", "run_id": str(run_id), "user_id": str(user),
                                         "source": str(source), "seal": True})
    await conn.close()
    print(f"run {run_id} submitted")
    while wait:
        async with p.connection() as c:
            r = await (await c.execute("SELECT state, done_chunks, total_chunks, stats, error FROM runs WHERE id = %s",
                                       (run_id,))).fetchone()
        if r["state"] in db.TERMINAL:
            print(f"{r['state']} in {time.perf_counter() - t0:.2f} s", r["stats"], r["error"] or "")
            break
        await asyncio.sleep(0.2)
    await p.close()


def cli():
    ap = argparse.ArgumentParser(prog="dd")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("source")
    s.add_argument("--name", default=None)
    s.add_argument("--owner", default="local")
    s.add_argument("--wait", action="store_true")
    a = ap.parse_args()
    asyncio.run(submit(a.source, a.name or a.source.rstrip("/").rsplit("/", 1)[-1], a.owner, a.wait))


if __name__ == "__main__":
    cli()
