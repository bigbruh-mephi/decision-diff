"""CPU-bound stage functions executed in worker processes. Inputs and outputs are plain picklable values.

Procedure code travels as ``{kind: {"sha", "code", "name", "params"}}`` snapshots so a run always executes the code
version recorded when it started; loaded modules are cached per process by sha.
"""

import time
from pathlib import Path

from .engine import chunk, finalize, ingest, mart, procedures

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


def run_chunk(input_uri, out_dir, snaps):
    """Parse, normalize, compare and write one chunk; returns ``(output_uri, stats, stages)``."""
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
    return str(out_dir), stats, stages


def run_finalize(chunk_dirs, run_file, snaps):
    """Build the run file and execute the configured checks; returns ``(stats, check_results, stages)``."""
    seg = _proc(snaps["segments"])
    t0 = time.time()
    stats = finalize.build(chunk_dirs, run_file, seg["params"])
    stages = [_stage("finalize", None, t0, time.time(), len(chunk_dirs), stats["mart_rows"])]
    return stats, run_checks(run_file, snaps.get("checks", [])), stages


def run_checks(run_file, check_snaps):
    """Execute checks over a finished run file; every check result or error becomes one entry."""
    con = mart.open_run(run_file)
    try:
        M = mart.Mart(con)
        out = []
        for snap in check_snaps:
            t0 = time.time()
            try:
                chk = _proc({**snap, "kind": "check"})
                rows = procedures.validate_check_rows(chk["id"], chk["entrypoint"](M, chk["params"]))
                out.append({"check_id": chk["id"], "sha": snap["sha"], "label": chk["label"], "rows": rows,
                            "duration_ms": (time.time() - t0) * 1000, "error": None})
            except Exception as e:
                out.append({"check_id": snap.get("id") or snap["name"], "sha": snap["sha"], "label": snap["name"],
                            "rows": [], "duration_ms": (time.time() - t0) * 1000,
                            "error": f"{type(e).__name__}: {e}"})
        return out
    finally:
        con.close()
