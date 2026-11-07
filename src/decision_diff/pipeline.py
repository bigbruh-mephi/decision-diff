"""CPU-bound stage functions executed in worker processes. Inputs and outputs are plain picklable values.

Procedure code travels as ``{kind: {"sha", "code", "name", "params"}}`` snapshots so a run always executes the code
version recorded when it started; loaded modules are cached per process by sha.
"""

import os
import sys
import time
from pathlib import Path

import orjson

from . import sandbox
from .engine import chunk, finalize, ingest, mart, procedures
from .settings import settings

_cache = {}
APP_IDS_PREVIEW = 20


def _proc(snap):
    p = _cache.get(snap["sha"])
    if p is None:
        p = _cache[snap["sha"]] = procedures.load(snap["code"], snap["name"], snap["kind"])
    return {**p, "params": {**p["params"], **(snap.get("params") or {})}}


def _stage(name, sha, t0, t1, rows_in=None, rows_out=None, status="done", error=None):
    return {"stage": name, "code_sha": sha, "status": status, "started_at": t0, "duration_ms": (t1 - t0) * 1000,
            "rows_in": rows_in, "rows_out": rows_out, "error": error}


def list_pairs(source, sample=None):
    """Paired ``(app_id, before_path, after_path)`` of a run folder, optionally only a sample of ``sample`` of them."""
    return ingest.sample(ingest.pairs(source), sample)


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
    report = {**s["params"], "lineage": _proc(snaps["lineage"])["params"]} if snaps.get("lineage") else s["params"]
    return {**stats, "report_params": report}, stages


def build_run(chunk_dirs, run_file, report_params):
    """Build the run file (trusted code, no user procedure runs here); returns ``(stats, stages)``."""
    t0 = time.time()
    stats = finalize.build(chunk_dirs, run_file, report_params)
    return stats, [_stage("finalize", None, t0, time.time(), len(chunk_dirs), stats["mart_rows"])]


def run_checks(run_file, check_snaps):
    """Execute each check in its own sandbox over the finished run file; one result entry per check.

    The platform asks every check for all its APP_IDS (``app_ids_limit`` override, which the contract allows): the
    full lists go to ``check_apps/<sha>.json`` next to the run file for drill-down and app linkage, and the rows keep
    only the check's own ``app_ids_limit`` as a preview.
    """
    out, causes = [], _causes(run_file)
    for snap in check_snaps:
        t0 = time.time()
        try:
            chk_id, label, rows, preview = confined(_check_body, (run_file, snap, causes), read=[run_file])
            rows = procedures.validate_check_rows(chk_id, rows)
            _store_app_ids(run_file, snap["sha"], [r["APP_IDS"] for r in rows])
            out.append({"check_id": chk_id, "sha": snap["sha"], "label": label, "started_at": t0,
                        "rows": [{**r, "APP_IDS": r["APP_IDS"][:preview]} for r in rows],
                        "duration_ms": (time.time() - t0) * 1000, "error": None})
        except Exception as e:
            out.append({"check_id": snap.get("id") or snap["name"], "sha": snap["sha"], "label": snap["name"],
                        "started_at": t0, "rows": [], "duration_ms": (time.time() - t0) * 1000,
                        "error": f"{type(e).__name__}: {e}"})
    return out


def _store_app_ids(run_file, sha, ids):
    d = Path(run_file).parent / "check_apps"
    d.mkdir(exist_ok=True)
    tmp = d / f"{sha}.{os.getpid()}.tmp"
    tmp.write_bytes(orjson.dumps(ids))
    tmp.replace(d / f"{sha}.json")


def _causes(run_file):
    """``{app_id: cause label}`` of the run's decision flips from ``attribution.json`` (``{}`` when there is none)."""
    f = Path(run_file).parent / "attribution.json"
    return {a: c["label"] for c in orjson.loads(f.read_bytes())["causes"] for a in c["app_ids"]} if f.is_file() else {}


def _check_body(run_file, snap, causes):
    chk = _proc({**snap, "kind": "check"})
    preview = chk["params"].get("app_ids_limit", APP_IDS_PREVIEW)
    con = mart.open_run(run_file, threads=1)
    try:
        params = {**_with_lineage(con, chk["params"], causes), "app_ids_limit": sys.maxsize}
        rows = chk["entrypoint"](mart.Mart(con), params)
        return chk["id"], chk["label"], rows, preview if isinstance(preview, int) and preview >= 0 else APP_IDS_PREVIEW
    finally:
        con.close()


def _with_lineage(con, params, causes=None):
    """Replace ``"@lineage"`` param values with the run's lineage param of the same name (stored at finalize) and
    ``"@attribution"`` with the run's ``{app_id: cause label}`` of decision flips (see :func:`_causes`)."""
    params = {k: (causes or {}) if v == "@attribution" else v for k, v in params.items()}
    if "@lineage" not in params.values():
        return params
    row = con.execute("SELECT value FROM run_meta WHERE key = 'report_params'").fetchone()
    lineage = orjson.loads(row[0]).get("lineage") if row else None
    missing = [k for k, v in params.items() if v == "@lineage" and k not in (lineage or {})]
    if missing:
        raise procedures.ProcedureError(f"проверке нужна цепочка решения (lineage) прогона для параметров "
                                        f"{', '.join(missing)}: выберите процедуру цепочки при запуске")
    return {k: lineage[k] if v == "@lineage" else v for k, v in params.items()}


def run_cell(code, run_file):
    """Execute workspace code in the sandbox with ``mart`` bound to the run; returns stdout, last value, check rows."""
    read = [run_file] if run_file else []
    return confined(_cell_body, (code, run_file, _causes(run_file) if run_file else {}), read=read)


def _cell_body(code, run_file, causes):
    import ast
    import contextlib
    import io

    out, con = io.StringIO(), None
    env = {"__name__": "__cell__"}
    if run_file:
        con = mart.open_run(run_file, threads=1)
        env["mart"] = mart.Mart(con)
    res = {"stdout": "", "value": None, "check": None, "error": None}
    try:
        tree = ast.parse(code, "cell")
        last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        with contextlib.redirect_stdout(out):
            exec(compile(tree, "cell", "exec"), env)
            if last is not None:
                res["value"] = _preview(eval(compile(ast.Expression(last.value), "cell", "eval"), env))
            chk = env.get("CHECK")
            if isinstance(chk, dict) and callable(chk.get("entrypoint")) and "mart" in env:
                rows = chk["entrypoint"](env["mart"], _with_lineage(con, chk.get("params", {}), causes))
                res["check"] = {"id": chk.get("id"), "label": chk.get("label"),
                                "rows": procedures.validate_check_rows(chk.get("id", "cell"), rows)}
    except Exception as e:
        import traceback

        res["error"] = f"{type(e).__name__}: {e}"
        res["traceback"] = [f for f in traceback.format_exc().splitlines() if "cell" in f or not f.startswith("  File")]
    finally:
        if con:
            con.close()
    res["stdout"] = out.getvalue()[-200_000:]
    return res


def _preview(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return {"type": "scalar", "repr": repr(v)}
    if hasattr(v, "__iter__") and not isinstance(v, (dict, bytes)):
        items = list(v)[:201]
        if items and all(isinstance(x, dict) for x in items):
            return {"type": "table", "rows": items[:200], "more": len(items) > 200}
        return {"type": "list", "repr": repr(items[:200])[:20_000]}
    return {"type": "scalar", "repr": repr(v)[:20_000]}
