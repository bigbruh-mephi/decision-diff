"""CPU-bound stage functions executed in worker processes. Inputs and outputs are plain picklable values.

Procedure code travels as ``{kind: {"sha", "code", "name", "params"}}`` snapshots so a run always executes the code
version recorded when it started; loaded modules are cached per process by sha.
"""

import time
from pathlib import Path

from . import sandbox
from .engine import chunk, finalize, ingest, mart, procedures
from .settings import settings

_cache = {}


def _proc(snap):
    p = _cache.get(snap["sha"])
    if p is None:
        p = _cache[snap["sha"]] = procedures.load(snap["code"], snap["name"], snap["kind"])
    return {**p, "params": {**p["params"], **(snap.get("params") or {})}}


def _stage(name, sha, t0, t1, rows_in=None, rows_out=None, status="done", error=None):
    return {"stage": name, "code_sha": sha, "status": status, "started_at": t0, "duration_ms": (t1 - t0) * 1000,
            "rows_in": rows_in, "rows_out": rows_out, "error": error}


def list_pairs(source):
    """Paired ``(app_id, before_path, after_path)`` of a run folder."""
    return ingest.pairs(source)


def cut_chunk(items, path):
    """Write one chunk zip of raw files; returns the number of applications."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ingest.write_chunk_zip(path, items)
    return len(items)


def confined(target, args, read=(), write=()):
    """Run user-code ``target`` in the sandbox (``DD_SANDBOX=strict``) or in-process (``off``, development only)."""
    if settings.sandbox == "off":
        return target(*args)
    return sandbox.run(target, args, read=read, write=write, wall_s=settings.sandbox_wall_s,
                       mem_mb=settings.sandbox_mem_mb, uid=settings.sandbox_uid)


def run_chunk(input_uri, out_dir, snaps):
    """Parse, normalize, compare and write one chunk inside the sandbox; returns ``(output_uri, stats, stages)``."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stats, stages = confined(_chunk_body, (input_uri, out_dir, snaps), read=[input_uri], write=[out_dir])
    return str(out_dir), stats, stages


def _chunk_body(input_uri, out_dir, snaps):
    n, c, s = (_proc(snaps[k]) for k in ("normalize", "compare", "segments"))
    norm = n["params"]
    t0 = time.time()
    apps = list(ingest.read_chunk_zip(Path(input_uri).read_bytes(), norm.get("id_field"),
                                      norm.get("xml_array_tags", ())))
    t1 = time.time()
    m, p, sg, iss, stats, t = chunk.process_apps(apps, n, c, s)
    t2 = time.time()
    chunk.write_chunk(out_dir, m, p, sg, iss)
    t3 = time.time()
    a = t1 + t["flatten"]
    b = a + t["compare"]
    stages = [_stage("parse", None, t0, t1, rows_out=len(apps)),
              _stage("normalize", n["sha"], t1, a, len(apps), m.num_rows),
              _stage("compare", c["sha"], a, b, m.num_rows, m.num_rows),
              _stage("segments", s["sha"], b, b + t["segments"], len(apps), sg.num_rows),
              _stage("write", None, t2, t3, rows_out=m.num_rows)]
    return stats, stages


def build_run(chunk_dirs, run_file, snaps):
    """Build the run file (trusted code, no user procedure runs here); returns ``(stats, stages)``."""
    seg = procedures.load(snaps["segments"]["code"], snaps["segments"]["name"], "segments")
    t0 = time.time()
    stats = finalize.build(chunk_dirs, run_file, {**seg["params"], **(snaps["segments"].get("params") or {})})
    return stats, [_stage("finalize", None, t0, time.time(), len(chunk_dirs), stats["mart_rows"])]


def run_checks(run_file, check_snaps):
    """Execute each check in its own sandbox over the finished run file; one result entry per check."""
    out = []
    for snap in check_snaps:
        t0 = time.time()
        try:
            chk_id, label, rows = confined(_check_body, (run_file, snap), read=[run_file])
            out.append({"check_id": chk_id, "sha": snap["sha"], "label": label,
                        "rows": procedures.validate_check_rows(chk_id, rows),
                        "duration_ms": (time.time() - t0) * 1000, "error": None})
        except Exception as e:
            out.append({"check_id": snap.get("id") or snap["name"], "sha": snap["sha"], "label": snap["name"],
                        "rows": [], "duration_ms": (time.time() - t0) * 1000, "error": f"{type(e).__name__}: {e}"})
    return out


def _check_body(run_file, snap):
    chk = _proc({**snap, "kind": "check"})
    con = mart.open_run(run_file, threads=1)
    try:
        rows = chk["entrypoint"](mart.Mart(con), chk["params"])
        return chk["id"], chk["label"], rows
    finally:
        con.close()
