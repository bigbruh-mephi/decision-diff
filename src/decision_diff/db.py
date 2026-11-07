"""PostgreSQL access: schema migration, run state machine and journal shared by the API and the workers.

Run states: receiving -> sealed -> finalizing -> done | failed; cancelled from any non-terminal state.
The ``sealed -> finalizing`` flip is one conditional UPDATE, so finalize is triggered exactly once even under
at-least-once delivery of chunk tasks.
"""

from importlib.resources import files

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .settings import settings

TERMINAL = ("done", "failed", "cancelled")


async def pool(min_size=1, max_size=10):
    p = AsyncConnectionPool(settings.pg_url, min_size=min_size, max_size=max_size, open=False,
                            kwargs={"row_factory": dict_row, "autocommit": False})
    await p.open()
    return p


async def migrate(p):
    async with p.connection() as c:
        await c.execute("SELECT pg_advisory_xact_lock(4242)")
        await c.execute(files("decision_diff").joinpath("schema.sql").read_text())


async def event(c, run_id, type_, data=None):
    await c.execute("INSERT INTO run_events (run_id, type, data) VALUES (%s, %s, %s)",
                    (run_id, type_, Jsonb(data or {})))


async def store_code(c, kind, name, code, sha):
    await c.execute("INSERT INTO code_versions (sha, kind, name, code) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (sha) DO NOTHING", (sha, kind, name, code))


async def code(c, sha):
    row = await (await c.execute("SELECT code, name FROM code_versions WHERE sha = %s", (sha,))).fetchone()
    return row


async def add_chunks(c, run_id, chunks):
    """Insert ``[(chunk_no, input_uri, apps)]`` as ready chunks and count their apps into the run."""
    async with c.cursor() as cur:
        await cur.executemany("INSERT INTO chunks (run_id, chunk_no, input_uri, apps) VALUES (%s, %s, %s, %s) "
                              "ON CONFLICT DO NOTHING", [(run_id, n, uri, a) for n, uri, a in chunks])
    await c.execute("UPDATE runs SET apps = (SELECT coalesce(sum(apps), 0) FROM chunks WHERE run_id = %s) "
                    "WHERE id = %s", (run_id, run_id))


async def open_batch(c, run_id, source):
    """Register an ingest batch once; returns ``(first_chunk_no, existing chunk numbers, done)`` for resuming."""
    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 5))", (run_id,))
    await c.execute("""
        INSERT INTO batches (run_id, source, first_chunk_no)
        SELECT %(r)s, %(s)s, coalesce((SELECT max(first_chunk_no + coalesce(chunks, 1000000)) FROM batches
                                       WHERE run_id = %(r)s), 0)
        ON CONFLICT (run_id, source) DO NOTHING""", {"r": run_id, "s": source})
    b = await (await c.execute("SELECT first_chunk_no, done FROM batches WHERE run_id = %s AND source = %s",
                               (run_id, source))).fetchone()
    have = {r["chunk_no"] for r in await (await c.execute(
        "SELECT chunk_no FROM chunks WHERE run_id = %s", (run_id,))).fetchall()}
    return b["first_chunk_no"], have, b["done"]


async def close_batch(c, run_id, source, chunks):
    await c.execute("UPDATE batches SET chunks = %s, done = true WHERE run_id = %s AND source = %s",
                    (chunks, run_id, source))


async def dispatch(c, run_id, window):
    """Move up to ``window - in_flight`` ready chunks of the run to queued; returns chunk tasks to publish.

    The per-run in-flight cap interleaves runs of different users in the queue, so a large run never blocks others.
    """
    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 7))", (run_id,))
    rows = await (await c.execute("""
        WITH r AS (SELECT owner_id FROM runs WHERE id = %(r)s AND state IN ('receiving', 'sealed')),
        busy AS (SELECT count(*) AS n FROM chunks WHERE run_id = %(r)s AND state IN ('queued', 'running')),
        pick AS (SELECT chunk_no FROM chunks WHERE run_id = %(r)s AND state = 'ready' ORDER BY chunk_no
                 LIMIT greatest(0, %(w)s - (SELECT n FROM busy)) FOR UPDATE SKIP LOCKED)
        UPDATE chunks c SET state = 'queued', queued_at = now() FROM pick, r
        WHERE c.run_id = %(r)s AND c.chunk_no = pick.chunk_no
        RETURNING c.chunk_no, c.input_uri, c.apps, r.owner_id""", {"r": run_id, "w": window})).fetchall()
    return [{"kind": "chunk", "run_id": str(run_id), "user_id": str(r["owner_id"]), "chunk_no": r["chunk_no"],
             "input_uri": r["input_uri"], "apps": r["apps"]} for r in rows]


async def flip_finalize(c, run_id):
    """``sealed -> finalizing`` once every chunk is done or failed. True for exactly one caller."""
    row = await (await c.execute("""
        UPDATE runs SET state = 'finalizing' WHERE id = %s AND state = 'sealed'
          AND done_chunks + failed_chunks = total_chunks RETURNING id""", (run_id,))).fetchone()
    return row is not None


async def seal(c, run_id):
    """Close a receiving run for new batches; returns True if finalize must be published now."""
    row = await (await c.execute("""
        UPDATE runs SET state = 'sealed', sealed_at = now(),
               total_chunks = (SELECT count(*) FROM chunks WHERE run_id = %(r)s)
        WHERE id = %(r)s AND state = 'receiving' RETURNING total_chunks""", {"r": run_id})).fetchone()
    if row is None:
        return False
    await event(c, run_id, "run.sealed", {"total_chunks": row["total_chunks"]})
    return await flip_finalize(c, run_id)


async def claim_chunk(c, run_id, chunk_no, worker):
    """Mark a chunk running; returns None for duplicates or cancelled runs."""
    return await (await c.execute("""
        UPDATE chunks c SET state = 'running', attempts = attempts + 1, started_at = now(), worker = %s
        FROM runs r WHERE c.run_id = %s AND c.chunk_no = %s AND c.state IN ('queued', 'running')
          AND r.id = c.run_id AND r.state IN ('receiving', 'sealed')
        RETURNING c.attempts, r.procedures, r.params""", (worker, run_id, chunk_no))).fetchone()


async def complete_chunk(c, run_id, chunk_no, output_uri, stats, stages):
    """Record a finished chunk and its stage journal in one transaction; True if finalize must be published."""
    row = await (await c.execute("""
        UPDATE chunks SET state = 'done', output_uri = %s, stats = %s, finished_at = now()
        WHERE run_id = %s AND chunk_no = %s AND state = 'running' RETURNING chunk_no""",
                                 (output_uri, Jsonb(stats), run_id, chunk_no))).fetchone()
    if row is None:
        return None
    await journal(c, run_id, chunk_no, stages)
    await c.execute("UPDATE runs SET done_chunks = done_chunks + 1 WHERE id = %s", (run_id,))
    await event(c, run_id, "chunk.done", {"chunk_no": chunk_no, **stats})
    return await flip_finalize(c, run_id)


async def fail_chunk(c, run_id, chunk_no, error):
    row = await (await c.execute("""
        UPDATE chunks SET state = 'failed', error = %s, finished_at = now()
        WHERE run_id = %s AND chunk_no = %s AND state NOT IN ('done', 'failed') RETURNING chunk_no""",
                                 (error, run_id, chunk_no))).fetchone()
    if row is None:
        return False
    await c.execute("UPDATE runs SET failed_chunks = failed_chunks + 1 WHERE id = %s", (run_id,))
    await event(c, run_id, "chunk.failed", {"chunk_no": chunk_no, "error": error})
    return await flip_finalize(c, run_id)


async def journal(c, run_id, chunk_no, stages):
    """Insert stage rows ``{stage, code_sha, status, started_at, duration_ms, rows_in, rows_out, error}``."""
    async with c.cursor() as cur:
        await cur.executemany("""
            INSERT INTO stage_runs (run_id, chunk_no, stage, code_sha, status, started_at, duration_ms, rows_in,
                                    rows_out, error)
            VALUES (%s, %s, %s, %s, %s, to_timestamp(%s), %s, %s, %s, %s)
            ON CONFLICT (run_id, chunk_no, stage) DO UPDATE SET status = excluded.status,
                duration_ms = excluded.duration_ms, rows_out = excluded.rows_out, error = excluded.error""",
                              [(run_id, chunk_no, s["stage"], s.get("code_sha"), s["status"], s["started_at"],
                                s["duration_ms"], s.get("rows_in"), s.get("rows_out"), s.get("error"))
                               for s in stages])


async def ensure_user(c, login, password_hash="!"):
    row = await (await c.execute("""
        INSERT INTO users (login, password_hash) VALUES (%s, %s)
        ON CONFLICT (login) DO UPDATE SET login = excluded.login RETURNING id""", (login, password_hash))).fetchone()
    return row["id"]


async def create_run(c, owner_id, name, snapshot, params=None, config_id=None):
    row = await (await c.execute("""
        INSERT INTO runs (owner_id, config_id, name, procedures, params) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                                 (owner_id, config_id, name, Jsonb(snapshot), Jsonb(params or {})))).fetchone()
    await event(c, row["id"], "run.created", {"name": name})
    return row["id"]
