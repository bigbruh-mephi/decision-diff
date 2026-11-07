"""Chunk stage list: parse -> prepare -> flatten -> compare -> bucket -> segments -> Parquet.

One chunk is a set of applications with their BEFORE and AFTER documents. Output is a directory with
``mart.parquet`` (vertical datamart rows, both slices), ``paths.parquet`` (path dictionary of this chunk),
``segments.parquet`` and ``issues.parquet``. Paths are identified by their canonical jsonpath; the run-wide
integer ``path_id`` is assigned at finalize.
"""

import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .compare import SAME, bucket_label, compare
from .flatten import Paths, Trie, flatten

MART_COLS = ("APP_ID", "SOURCE", "JSONPATH", "ARRAY_KEY", "VALUE", "TYPE", "ADJUSTED_VALUE", "DIFF_FLAG",
             "CHANGE_TYPE", "TYPE_CHANGED")
PATH_COLS = ("JSONPATH", "PATH", "PREFIX", "CLASS", "SUFFIX", "KEY")


def make_paths(normalize, compare_proc=None):
    """Run-wide path trie built from the normalize profile and compare rules."""
    p = normalize.get("params", {})
    c = (compare_proc or {}).get("params", {})
    return Paths(arrays=p.get("arrays"), exclude=p.get("exclude", ()), buckets=p.get("buckets", ()),
                 rename=p.get("rename"), key_style=p.get("key_style"), equivalent=c.get("equivalent", ()))


def _flat(doc, P, A, prepare, params):
    if prepare:
        doc = prepare(doc, params)
    out, issues = flatten(doc, P, A)
    if any(i[0] == "float_beyond_int64" for i in issues):
        out, issues = flatten(json.loads(json.dumps(doc)), P, A)
    return out, issues


def process_apps(apps, normalize, compare_proc, segments_proc, P=None):
    """Process ``apps`` = iterable of ``(app_id, before_doc | None, after_doc | None)``.

    Returns ``(mart, paths, segments, issues, stats, timings)`` where the first four are Arrow tables.
    A missing slice yields only ATTRIBUTE_NEW/LOST rows and an ``app_missing_in_slice`` issue.
    """
    P = P or make_paths(normalize, compare_proc)
    prepare = normalize.get("entrypoint")
    nparams = normalize.get("params", {})
    eq = P.equivalent if (compare_proc or {}).get("params", {}).get("equivalent") else None
    seg_fn, seg_params = segments_proc["entrypoint"], segments_proc.get("params", {})
    cols = {c: [] for c in MART_COLS}
    seg_rows, issue_rows = [], []
    t = {"flatten": 0.0, "compare": 0.0, "segments": 0.0}
    changed_apps = n_apps = 0
    breaks_memo = {}
    for app_id, before, after in apps:
        n_apps += 1
        A = Trie()
        t0 = time.perf_counter()
        fb, ib = _flat(before, P, A, prepare, nparams) if before is not None else ({}, [("app_missing_in_slice", "BEFORE")])
        fa, ia = _flat(after, P, A, prepare, nparams) if after is not None else ({}, [("app_missing_in_slice", "AFTER")])
        t1 = time.perf_counter()
        res = compare(fb, fa, eq)
        app_changed = False
        for key, (ct, tch) in res.items():
            node, ak = key
            br = breaks_memo.get(node, 0)
            if br == 0:
                br = breaks_memo[node] = P.breaks(node)
            df = int(ct != SAME)
            app_changed |= bool(df)
            jp, akey = P.info(node)[0], A.akey(ak)
            for side, data in (("BEFORE", fb), ("AFTER", fa)):
                v = data.get(key)
                if v is None:
                    continue
                cols["APP_ID"].append(app_id)
                cols["SOURCE"].append(side)
                cols["JSONPATH"].append(jp)
                cols["ARRAY_KEY"].append(akey)
                cols["VALUE"].append(v[1])
                cols["TYPE"].append(v[0])
                cols["ADJUSTED_VALUE"].append(bucket_label(br, v[1]) if br else v[1])
                cols["DIFF_FLAG"].append(df)
                cols["CHANGE_TYPE"].append(ct)
                cols["TYPE_CHANGED"].append(tch)
        changed_apps += app_changed
        t2 = time.perf_counter()
        seg = seg_fn(before or {}, after or {}, seg_params) if (before or after) else {}
        seg_rows.append({**{k: None if v is None else str(v) for k, v in seg.items()}, "APP_ID": app_id})
        t3 = time.perf_counter()
        for side, iss in (("BEFORE", ib), ("AFTER", ia)):
            for i in iss:
                where = P.info(i[1])[0] if len(i) > 1 and isinstance(i[1], int) else ""
                issue_rows.append({"APP_ID": app_id, "SOURCE": side, "KIND": i[0], "JSONPATH": where,
                                   "DETAIL": " ".join(map(str, i[2:]))})
        t["flatten"] += t1 - t0
        t["compare"] += t2 - t1
        t["segments"] += t3 - t2
    mart = pa.table({c: pa.array(v, pa.uint8() if c in ("DIFF_FLAG", "TYPE_CHANGED") else pa.string())
                     for c, v in cols.items()})
    used = set(cols["JSONPATH"])
    paths = {c: [] for c in PATH_COLS}
    for node in range(1, len(P.seg)):
        info = P.info(node)
        if info[0] in used:
            for c, v in zip(PATH_COLS, info):
                paths[c].append(v)
            used.discard(info[0])
    seg_cols = sorted({k for r in seg_rows for k in r} - {"APP_ID"})
    segments = pa.table({"APP_ID": [r["APP_ID"] for r in seg_rows],
                         **{c: pa.array([r.get(c) for r in seg_rows], pa.string()) for c in seg_cols}})
    issues = pa.table({k: pa.array([r[k] for r in issue_rows], pa.string())
                       for k in ("APP_ID", "SOURCE", "KIND", "JSONPATH", "DETAIL")})
    stats = {"apps": n_apps, "rows": mart.num_rows, "changed_apps": changed_apps, "issues": len(issue_rows)}
    return mart, pa.table(paths), segments, issues, stats, t


def write_chunk(out_dir, mart, paths, segments, issues):
    """Write chunk outputs as zstd Parquet into ``out_dir`` (one directory per attempt)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, table in (("mart", mart), ("paths", paths), ("segments", segments), ("issues", issues)):
        pq.write_table(table, out / f"{name}.parquet", compression="zstd")
    return out
