"""Procedure catalog: placed ``.py`` files and workspace code registered in PostgreSQL, run snapshots.

Registration only inspects source (AST); user code is executed exclusively inside the worker sandbox.
"""

import asyncio
import logging
from pathlib import Path

from . import db
from .engine import procedures

log = logging.getLogger("dd.catalog")
_seen = {}


async def sync_files(c, root, owner_id=None):
    """Register every ``*.py`` under ``root`` as a file procedure (kind from its contract); returns ``{name: id}``.

    A file that does not satisfy the contract is logged and skipped (its previous registration, if any, stays);
    procedures whose file is gone are removed, their code versions stay for the history of past runs.
    """
    if not Path(root).is_dir():
        log.warning("procedures directory %s does not exist; placed procedures left as they are", root)
        return {}
    await c.execute("SELECT pg_advisory_xact_lock(4243)")
    ids, present = {}, []
    for f in sorted(Path(root).rglob("*.py")):
        name = str(f.relative_to(root))
        present.append(name)
        try:
            code = f.read_text(encoding="utf-8")
            spec = procedures.inspect_source(code, str(f))
        except (OSError, ValueError, RecursionError, MemoryError) as e:
            log.warning("placed procedure %s skipped: %s", name, e)
            continue
        await db.store_code(c, spec["kind"], name, code, spec["sha"])
        row = await (await c.execute("""
            UPDATE procedures SET sha = %s, kind = %s, name = %s, updated_at = now() WHERE origin = 'file' AND path = %s
            RETURNING id""", (spec["sha"], spec["kind"], spec["label"], name))).fetchone()
        if row is None:
            row = await (await c.execute("""
                INSERT INTO procedures (owner_id, kind, name, origin, path, sha) VALUES (%s, %s, %s, 'file', %s, %s)
                RETURNING id""", (owner_id, spec["kind"], spec["label"], name, spec["sha"]))).fetchone()
        ids[name] = row["id"]
    await c.execute("DELETE FROM procedures WHERE origin = 'file' AND NOT (path = ANY(%s))", (present,))
    return ids


def _signature(root):
    return sorted((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in Path(root).rglob("*.py"))


async def sync_changed(c, root):
    """Re-sync placed files only when a ``*.py`` under ``root`` was added, changed or removed (mtime check)."""
    sig = await asyncio.to_thread(_signature, root)
    if _seen.get(str(root)) != sig:
        await sync_files(c, root)
        await c.commit()
        _seen[str(root)] = sig


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


async def snapshot(c, selection, params=None, owner_id=None):
    """Freeze ``{"normalize": id, "compare": id, "segments": id, "checks": [ids]}`` into code shas for a run.

    Only placed files and the owner's own workspace procedures can be selected. ``whatif`` (attribution of decision
    flips at finalize) defaults to the procedure the what-if tab opens with: the most recently updated one.
    """
    params = params or {}

    async def one(pid, kind):
        row = await (await c.execute("""SELECT sha, kind FROM procedures WHERE id = %s
                                        AND (origin = 'file' OR owner_id = %s OR %s::uuid IS NULL)""",
                                     (pid, owner_id, owner_id))).fetchone()
        if row is None or row["kind"] != kind:
            raise procedures.ProcedureError(f"процедура {pid} не найдена или ее вид не {kind!r}: выберите процедуру "
                                            "из списка заново")
        return {"sha": row["sha"], "params": params.get(str(pid), {})}

    snap = {k: await one(selection[k], k) for k in ("normalize", "compare", "segments")}
    if selection.get("lineage"):
        snap["lineage"] = await one(selection["lineage"], "lineage")
    whatif = selection.get("whatif")
    if not whatif:
        row = await (await c.execute("""SELECT id FROM procedures WHERE kind = 'whatif'
                                        AND (origin = 'file' OR owner_id = %s) ORDER BY updated_at DESC LIMIT 1""",
                                     (owner_id,))).fetchone()
        whatif = row and row["id"]
    if whatif:
        snap["whatif"] = await one(whatif, "whatif")
    snap["checks"] = [await one(pid, "check") for pid in selection.get("checks", [])]
    return snap
