"""Procedure catalog: placed ``.py`` files and workspace code registered in PostgreSQL, run snapshots.

Registration only inspects source (AST); user code is executed exclusively inside the worker sandbox.
"""

from pathlib import Path

from . import db
from .engine import procedures


async def sync_files(c, root, owner_id=None):
    """Register every ``*.py`` under ``root`` as a file procedure (kind from its contract); returns ``{name: id}``."""
    ids = {}
    for f in sorted(Path(root).rglob("*.py")):
        code = f.read_text(encoding="utf-8")
        spec = procedures.inspect_source(code, str(f))
        name = str(f.relative_to(root))
        await db.store_code(c, spec["kind"], name, code, spec["sha"])
        row = await (await c.execute("""
            UPDATE procedures SET sha = %s, kind = %s, updated_at = now() WHERE origin = 'file' AND path = %s
            RETURNING id""", (spec["sha"], spec["kind"], name))).fetchone()
        if row is None:
            row = await (await c.execute("""
                INSERT INTO procedures (owner_id, kind, name, origin, path, sha) VALUES (%s, %s, %s, 'file', %s, %s)
                RETURNING id""", (owner_id, spec["kind"], spec["label"], name, spec["sha"]))).fetchone()
        ids[name] = row["id"]
    return ids


async def save_workspace(c, owner_id, code, proc_id=None):
    """Create or update a workspace procedure from code; validation errors raise ProcedureError."""
    spec = procedures.inspect_source(code)
    await db.store_code(c, spec["kind"], spec["id"], code, spec["sha"])
    if proc_id:
        row = await (await c.execute("""
            UPDATE procedures SET sha = %s, kind = %s, name = %s, updated_at = now()
            WHERE id = %s AND owner_id = %s AND origin = 'workspace' RETURNING id""",
                                     (spec["sha"], spec["kind"], spec["label"], proc_id, owner_id))).fetchone()
    else:
        row = await (await c.execute("""
            INSERT INTO procedures (owner_id, kind, name, origin, sha) VALUES (%s, %s, %s, 'workspace', %s)
            RETURNING id""", (owner_id, spec["kind"], spec["label"], spec["sha"]))).fetchone()
    return row and row["id"]


async def snapshot(c, selection, params=None):
    """Freeze ``{"normalize": id, "compare": id, "segments": id, "checks": [ids]}`` into code shas for a run."""
    params = params or {}

    async def one(pid, kind):
        row = await (await c.execute("SELECT sha, kind FROM procedures WHERE id = %s", (pid,))).fetchone()
        if row is None or row["kind"] != kind:
            raise procedures.ProcedureError(f"procedure {pid} is not a {kind} procedure")
        return {"sha": row["sha"], "params": params.get(str(pid), {})}

    snap = {k: await one(selection[k], k) for k in ("normalize", "compare", "segments")}
    snap["checks"] = [await one(pid, "check") for pid in selection.get("checks", [])]
    return snap
