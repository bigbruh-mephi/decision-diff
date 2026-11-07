"""Population what-if over a finished run: a user ``whatif`` procedure re-decides every application under new params.

Contract (``procedures/spr_whatif.py``): ``PROCEDURE = {"kind": "whatif", "unit_depth", "inputs", "outcome",
"params", "entrypoint"}``; everything except ``entrypoint`` must be literals, so trusted code reads the declaration
from the source without executing it. ``entrypoint(data, params)`` returns one outcome index per application
(``outcome["values"]`` order; ``len(values)`` = other).

- ``prepare`` (worker trusted pool, DuckDB, once per run x procedure sha x slice): each declared input becomes a
  numpy array aligned to units (unit = application x first ``unit_depth`` ARRAY_KEY components) under
  ``runs/<id>/whatif/<sha>/<slice>/``; an input deeper than the unit becomes ``{sub key: array}``, an app-level
  input is broadcast to its units, a missing value is NaN / "" / False. ARRAY_KEY is split on every ``|``, so an
  element key that itself contains an escaped ``|`` is misaligned (shortcut; switch to ``flatten.split_esc`` in
  Python if such keys appear).
- ``evaluate`` (worker launcher pool; ``numpy`` and ``mmap`` are imported here because the confined child cannot
  load new extension modules): validates the values against the declaration (trust boundary), runs the
  procedure via ``pipeline.confined`` for the defaults (fidelity) and the values, checks the returned codes.
- ``metrics`` (API, no user code): approval rate, swap matrices vs BEFORE / AFTER, fidelity and per-segment deltas
  from the ``RunReport`` bitmaps under a scope.
"""

import base64
import itertools
import mmap  # noqa: F401
import os
import shutil
import time
from pathlib import Path

import numpy as np
import orjson
from pyroaring import BitMap

from . import pipeline
from .engine import mart, procedures

SLICES = ("AFTER", "BEFORE")
CAST = {"float": "TRY_CAST(v.VALUE AS DOUBLE)", "str": "coalesce(v.VALUE, '')", "bool": "coalesce(v.VALUE = 'true', false)"}


def declaration(code, name="<workspace>"):
    """Literal what-if declaration of ``code`` (never executed here)."""
    _, d = procedures.declaration(code, name)
    if d.get("kind") != "whatif":
        raise procedures.ProcedureError(f"{name}: not a whatif procedure")
    missing = [k for k in ("unit_depth", "inputs", "outcome", "params") if k not in d]
    if missing:
        raise procedures.ProcedureError(f"{name}: whatif declaration needs literal {missing}")
    return d


def defaults(decl):
    return {k: p.get("default") for k, p in decl["params"].items()}


def _slice(decl, values):
    side = values.get("slice", decl["params"].get("slice", {}).get("default", "AFTER"))
    if side not in SLICES:
        raise ValueError(f"slice must be one of {SLICES}")
    return side


def prepare(run_file, code, cache_dir, values):
    """Arrays of the requested slice under ``cache_dir/<slice>`` (built once, atomically); returns its meta."""
    decl = declaration(code)
    out = Path(cache_dir) / _slice(decl, values)
    if not (out / "index.json").exists():
        _build(run_file, decl, out)
    return {**orjson.loads((out / "index.json").read_bytes()), "dir": str(out)}


def _build(run_file, decl, out):
    d = int(decl["unit_depth"])
    side = out.name
    tmp = out.with_name(f"{out.name}.tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    con = mart.open_run(run_file, threads=2)
    try:
        n_apps = con.execute("SELECT count(*) FROM apps").fetchone()[0]
        con.execute(f"""CREATE TEMP TABLE units AS
            SELECT row_number() OVER (ORDER BY app_ord, ukey) - 1 AS uid, app_ord, ukey FROM (
              SELECT DISTINCT m.app_ord, array_to_string(string_split(m.ARRAY_KEY, '|')[1:{d}], '|') AS ukey
              FROM mart m JOIN attrs t USING (path_id)
              WHERE t.PATH IN (SELECT unnest(?::VARCHAR[])) AND m.SOURCE = ? AND m.ARRAY_KEY <> ''
                AND len(string_split(m.ARRAY_KEY, '|')) >= {d})""",
                    [[i["path"] for i in decl["inputs"].values()], side])
        n_units = con.execute("SELECT count(*) FROM units").fetchone()[0]
        arrays = {"unit_app": np.array([r[0] for r in con.execute("SELECT app_ord FROM units ORDER BY uid").fetchall()],
                                       np.int32)}
        for name, inp in decl["inputs"].items():
            rows = con.execute(f"""
                WITH v AS (SELECT m.app_ord, string_split(m.ARRAY_KEY, '|') AS parts, m.VALUE
                           FROM mart m JOIN attrs t USING (path_id) WHERE t.PATH = ? AND m.SOURCE = ?)
                SELECT array_to_string(v.parts[{d + 1}:], '|') AS sub, list(u.uid ORDER BY u.uid),
                       list({CAST[inp["type"]]} ORDER BY u.uid)
                FROM v JOIN units u ON u.app_ord = v.app_ord
                     AND (u.ukey = array_to_string(v.parts[1:{d}], '|') OR len(v.parts) < {d} OR v.parts = [''])
                GROUP BY ALL""", [inp["path"], side]).fetchall()
            dtype, fill = {"float": (np.float64, np.nan), "str": (object, ""), "bool": (bool, False)}[inp["type"]]
            for sub, uids, vals in rows:
                a = np.full(n_units, fill, dtype)
                a[np.array(uids, np.int64)] = [fill if v is None else v for v in vals]
                arrays[f"{name}|{sub}" if sub else name] = a.astype(str) if inp["type"] == "str" else a
    finally:
        con.close()
    index = {}
    for i, (k, a) in enumerate(arrays.items()):
        np.save(tmp / f"{i}.npy", a, allow_pickle=False)
        index[k] = f"{i}.npy"
    choices = {}
    for r in {p.get("values_from") or p.get("keys_from") for p in decl["params"].values()} - {None}:
        subs = sorted(k.split("|", 1)[1] for k in arrays if k.startswith(r + "|"))
        choices[r] = subs if subs or r not in arrays else sorted(set(arrays[r].tolist()) - {""})
    (tmp / "index.json").write_bytes(orjson.dumps({"slice": side, "n_apps": n_apps, "n_units": n_units,
                                                   "arrays": index, "choices": choices}))
    try:
        os.replace(tmp, out)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)


def validate(decl, choices, values):
    """Fill defaults and check each value against its declared type, range and allowed values."""
    unknown = set(values) - set(decl["params"])
    if unknown:
        raise ValueError(f"unknown what-if params {sorted(unknown)}")
    out = {}
    for name, p in decl["params"].items():
        v = values.get(name, p.get("default"))
        allowed = p.get("values") or choices.get(p.get("values_from") or p.get("keys_from"), [])
        kind = p.get("type")
        if kind == "float":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not p["min"] <= v <= p["max"]:
                raise ValueError(f"{name}={v!r} must be a number in [{p['min']}, {p['max']}]")
            v = float(v)
        elif kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{name} must be true or false")
        elif kind == "enum":
            if v != p.get("default") and (not isinstance(v, str) or v not in allowed):
                raise ValueError(f"{name}={v!r} not in {allowed}")
        elif kind == "multi":
            if not isinstance(v, list) or not all(isinstance(x, str) and x in allowed for x in v):
                raise ValueError(f"{name}: every value must be one of {allowed}")
        elif kind == "float_map":
            if not isinstance(v, dict) or not all(k in allowed and not isinstance(x, bool) and isinstance(x, (int, float))
                                                  and p["min"] <= x <= p["max"] for k, x in v.items()):
                raise ValueError(f"{name}: keys must be in {allowed}, values in [{p['min']}, {p['max']}]")
            v = {k: float(x) for k, x in v.items()}
        else:
            raise ValueError(f"{name}: unknown param type {kind!r}")
        out[name] = v
    return out


def evaluate(code, name, meta, values):
    """Validate ``values``, run the procedure in the sandbox for the defaults and the values, check the codes."""
    decl = declaration(code, name)
    params = [validate(decl, meta["choices"], {"slice": meta["slice"]} if "slice" in decl["params"] else {}),
              validate(decl, meta["choices"], values)]
    t0 = time.perf_counter()
    res = pipeline.confined(_evaluate_body, (code, name, meta["dir"], params), read=[meta["dir"]])
    ms = (time.perf_counter() - t0) * 1000
    n_values = len(decl["outcome"]["values"])
    if not isinstance(res, list) or len(res) != 2 or not all(isinstance(r, str) for r in res):
        raise procedures.ProcedureError(f"{name}: sandbox returned an invalid result")
    codes = [base64.b64decode(r, validate=True) for r in res]
    for c in codes:
        if len(c) != meta["n_apps"] or (c and max(c) > n_values):
            raise procedures.ProcedureError(f"{name}: evaluate must return {meta['n_apps']} outcome indices in "
                                            f"0..{n_values}")
    return {"slice": meta["slice"], "outcome": decl["outcome"], "params": params[1], "choices": meta["choices"],
            "default_codes": res[0], "codes": res[1], "sandbox_ms": round(ms, 1)}


def _evaluate_body(code, name, slice_dir, params_list):
    spec = procedures.load(code, name, "whatif")
    meta = orjson.loads((Path(slice_dir) / "index.json").read_bytes())
    data = {"n_apps": meta["n_apps"], **{k: {} for k in spec["inputs"]}}
    for key, f in meta["arrays"].items():
        a = np.load(Path(slice_dir) / f, mmap_mode="r", allow_pickle=False)
        base, _, sub = key.partition("|")
        if sub:
            data[base][sub] = a
        else:
            data[base] = a
    out = []
    for params in params_list:
        o = np.asarray(spec["entrypoint"](data, params))
        if o.shape != (meta["n_apps"],) or o.dtype.kind not in "biu" or (o.size and (o.min() < 0 or o.max() > 255)):
            raise ValueError(f"evaluate must return {meta['n_apps']} integer outcome indices, got {o.dtype} {o.shape}")
        out.append(base64.b64encode(o.astype(np.uint8).tobytes()).decode())
    return out


def _bitmaps(codes, k):
    idx = [[] for _ in range(k)]
    for i, c in enumerate(codes):
        idx[c].append(i)
    return [BitMap(x) for x in idx]


def metrics(rep, res, spec, max_values=50, top=30, max_ids=500):
    """Approval rate, swaps, fidelity and per-segment deltas of a what-if result under scope ``spec``.

    ``moved_ids`` lists the first ``max_ids`` applications whose decision would move, so the number drills into the
    application list; the cap keeps the id list small enough for a URL scope (``moved`` is always exact).
    """
    s = rep.scope(spec)
    values = res["outcome"]["values"]
    labels = [*values, "(other)"]
    field = res["outcome"]["field"]
    new = _bitmaps(base64.b64decode(res["codes"]), len(labels))
    dflt = _bitmaps(base64.b64decode(res["default_codes"]), len(labels))
    before, after = rep.seg.get(f"{field}_BEFORE", {}), rep.seg.get(f"{field}_AFTER", {})
    own = after if res["slice"] == "AFTER" else before
    positive = rep.params.get("positive_decision")
    pos = values.index(positive) if positive in values else 0
    n = len(s)
    agree = BitMap.union(BitMap(), *(dflt[i] & own.get(v, BitMap()) for i, v in enumerate(values)))
    same = BitMap.union(BitMap(), *(new[i] & own.get(v, BitMap()) for i, v in enumerate(values)))
    moved = s - same

    def rate(bm):
        return round(len(bm & s) / n, 4) if n else None

    def swap(side):
        return [{"before": v, "after": labels[i], "apps": c} for v, b in sorted(side.items())
                for i, w in enumerate(new) if (c := len(b & w & s))]

    segs = []
    for f, vals in sorted(rep.seg.items()):
        if len(vals) > max_values:
            continue
        for v, bm in vals.items():
            b = bm & s
            if b:
                segs.append({"field": f, "value": v, "apps": len(b),
                             "approval_slice": round(len(b & own.get(values[pos], BitMap())) / len(b), 4),
                             "approval_whatif": round(len(b & new[pos]) / len(b), 4), "moved": len(b & moved)})
    segs.sort(key=lambda r: (-r["moved"], r["field"], r["value"]))
    return {"apps": n, "slice": res["slice"], "positive": values[pos],
            "fidelity": round(len(agree & s) / n, 4) if n else None,
            "approval_rate": {"before": rate(before.get(values[pos], BitMap())),
                              "after": rate(after.get(values[pos], BitMap())), "whatif": rate(new[pos])},
            "moved": len(moved), "moved_ids": [rep.app_id[i] for i in itertools.islice(moved, max_ids)],
            "swap_vs_before": swap(before), "swap_vs_after": swap(after),
            "segments": segs[:top]}
