"""What-if HTTP routes: declared params of a ``whatif`` procedure, queued evaluations and their aggregated results.

The API never runs the procedure: the declaration is read from source literals, evaluation is a task on the
interactive queue handled by the worker sandbox (``worker.on_whatif``), and the API only aggregates the returned
outcome codes over the report bitmaps (``whatif.metrics``).
"""

import asyncio
import uuid

import orjson
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import queue, whatif
from .api import STATE, _own_run, _report, j, pool, session, user
from .engine import procedures
from .settings import settings

router = APIRouter()


async def _procedure(c, s, pid):
    """The requested whatif procedure, or the most recently updated one visible to the user."""
    row = await (await c.execute("""
        SELECT p.id, p.name, p.sha, v.code FROM procedures p JOIN code_versions v USING (sha)
        WHERE p.kind = 'whatif' AND (p.origin = 'file' OR p.owner_id = %s) AND (%s::uuid IS NULL OR p.id = %s)
        ORDER BY p.updated_at DESC LIMIT 1""", (s["user_id"], pid, pid))).fetchone()
    if not row:
        raise HTTPException(404, "no whatif procedure")
    try:
        return row, whatif.declaration(row["code"], row["name"])
    except procedures.ProcedureError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/api/runs/{run_id}/whatif")
async def get_whatif(run_id: uuid.UUID, procedure_id: uuid.UUID | None = None, s=Depends(session)):
    """Declared params with defaults, the user's last submitted values and, once prepared, the allowed choices."""
    await _own_run(run_id, s, states=("done",))
    async with pool().connection() as c:
        row, decl = await _procedure(c, s, procedure_id)
        last = await (await c.execute("""SELECT "values" FROM whatif_runs WHERE run_id = %s AND user_id = %s
                                         AND code_sha = %s ORDER BY created_at DESC LIMIT 1""",
                                      (run_id, s["user_id"], row["sha"]))).fetchone()
    values = {**whatif.defaults(decl), **(last["values"] if last else {})}
    side = values.get("slice") if values.get("slice") in whatif.SLICES else "AFTER"
    index = settings.run_dir(run_id) / "whatif" / row["sha"] / side / "index.json"
    return j({"procedure": {"id": row["id"], "name": row["name"], "sha": row["sha"]}, "label": decl.get("label"),
              "params": decl["params"], "outcome": decl["outcome"], "values": values,
              "choices": orjson.loads(index.read_bytes())["choices"] if index.exists() else None})


class WhatIfBody(BaseModel):
    values: dict = {}
    scope: dict = {}
    procedure_id: uuid.UUID | None = None


@router.post("/api/runs/{run_id}/whatif")
async def post_whatif(run_id: uuid.UUID, body: WhatIfBody, s=Depends(user)):
    """Queue an evaluation of ``values`` (validated by the worker against the declaration); poll its id."""
    await _own_run(run_id, s, states=("done",))
    async with pool().connection() as c:
        row, _ = await _procedure(c, s, body.procedure_id)
        got = await (await c.execute("""INSERT INTO whatif_runs (run_id, user_id, code_sha, "values", scope)
                                        VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                                     (run_id, s["user_id"], row["sha"], Jsonb(body.values), Jsonb(body.scope)))
                     ).fetchone()
    await queue.publish(STATE["ch"], queue.INTERACTIVE, {"kind": "whatif", "exec_id": str(got["id"])})
    return {"id": str(got["id"])}


@router.get("/api/whatif/{exec_id}")
async def get_whatif_result(exec_id: uuid.UUID, s=Depends(user)):
    """State of one evaluation; when done, approval rate, swaps, fidelity and segment deltas under its scope."""
    async with pool().connection() as c:
        row = await (await c.execute("SELECT * FROM whatif_runs WHERE id = %s AND user_id = %s",
                                     (exec_id, s["user_id"]))).fetchone()
    if not row:
        raise HTTPException(404, "what-if evaluation not found")
    out = {k: row[k] for k in ("id", "run_id", "state", "values", "scope", "created_at", "finished_at")}
    res = row["result"] or {}
    if row["state"] == "done":
        rep, _ = await _report(row["run_id"], s)
        out["result"] = {**await asyncio.to_thread(whatif.metrics, rep, res, row["scope"]),
                         "params": res["params"], "choices": res["choices"], "sandbox_ms": res["sandbox_ms"]}
    out["error"] = res.get("error")
    return j(out)
