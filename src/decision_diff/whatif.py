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
- ``prepare_factors`` + ``attribute`` (worker, at finalize): an optional literal ``factors`` section
  ``{name: {"label", "inputs": [input names], "hint", "strategy"}}`` names what can be restored from BEFORE; every
  flip gets the single strategy factor that reverts it, stored in ``attribution.json`` next to the run file
  (``RunReport.causes``) with the emulation's fidelity.
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
MAX_FACTORS = 6
CAST = {"float": "TRY_CAST(v.VALUE AS DOUBLE)", "str": "coalesce(v.VALUE, '')", "bool": "coalesce(v.VALUE = 'true', false)"}


def declaration(code, name="<workspace>"):
    """Literal what-if declaration of ``code`` (never executed here)."""
    _, d = procedures.declaration(code, name)
    if d.get("kind") != "whatif":
        raise procedures.ProcedureError(f"{name}: это не процедура «что если» (нужен kind: \"whatif\")")
    missing = [k for k in ("unit_depth", "inputs", "outcome", "params") if k not in d]
    if missing:
        raise procedures.ProcedureError(f"{name}: в объявлении «что если» не хватает литералов {', '.join(missing)}")
    return d


def defaults(decl):
    return {k: p.get("default") for k, p in decl["params"].items()}


def _slice(decl, values):
    side = values.get("slice", decl["params"].get("slice", {}).get("default", "AFTER"))
    if side not in SLICES:
        raise ValueError(f"срез должен быть одним из: {', '.join(SLICES)}")
    return side


def prepare(run_file, code, cache_dir, values):
    """Arrays of the requested slice under ``cache_dir/<slice>`` (built once, atomically); returns its meta."""
    decl = declaration(code)
    side = _slice(decl, values)
    return _prepared(run_file, decl, Path(cache_dir) / side, side, (side,))


def _prepared(run_file, decl, out, side, unit_sides):
    if not (out / "index.json").exists():
        _build(run_file, decl, out, side, unit_sides)
    return {**orjson.loads((out / "index.json").read_bytes()), "dir": str(out)}


def _build(run_file, decl, out, side, unit_sides):
    d = int(decl["unit_depth"])
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
              WHERE t.PATH IN (SELECT unnest(?::VARCHAR[])) AND m.SOURCE IN (SELECT unnest(?::VARCHAR[]))
                AND m.ARRAY_KEY <> '' AND len(string_split(m.ARRAY_KEY, '|')) >= {d})""",
                    [[i["path"] for i in decl["inputs"].values()], list(unit_sides)])
        n_units = con.execute("SELECT count(*) FROM units").fetchone()[0]
        arrays = {"unit_app": np.array([r[0] for r in con.execute("SELECT app_ord FROM units ORDER BY uid").fetchall()],
                                       np.int32)}
        for name, inp in decl["inputs"].items():
            got = con.execute(f"""
                WITH v AS (SELECT m.app_ord, string_split(m.ARRAY_KEY, '|') AS parts, m.VALUE
                           FROM mart m JOIN attrs t USING (path_id) WHERE t.PATH = ? AND m.SOURCE = ?)
                SELECT array_to_string(v.parts[{d + 1}:], '|') AS sub, u.uid, {CAST[inp["type"]]} AS val
                FROM v JOIN units u ON u.app_ord = v.app_ord
                     AND (u.ukey = array_to_string(v.parts[1:{d}], '|') OR len(v.parts) < {d} OR v.parts = [''])""",
                              [inp["path"], side]).fetchnumpy()
            dtype, fill = {"float": (np.float64, np.nan), "str": (object, ""), "bool": (bool, False)}[inp["type"]]
            subs, vals = np.asarray(got["sub"], object), np.ma.filled(got["val"], fill)
            for sub in sorted(set(subs.tolist())):
                pick = subs == sub
                a = np.full(n_units, fill, dtype)
                a[np.ma.getdata(got["uid"])[pick]] = vals[pick]
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
    """Fill defaults and check each value against its declared type, range and allowed values; the error names the
    param by its label (and the key of a map entry), in Russian, for the user."""
    unknown = set(values) - set(decl["params"])
    if unknown:
        raise ValueError(f"неизвестные параметры «что если»: {', '.join(sorted(unknown))}")
    out = {}
    for name, p in decl["params"].items():
        v = values.get(name, p.get("default"))
        allowed = p.get("values") or choices.get(p.get("values_from") or p.get("keys_from"), [])
        kind, label = p.get("type"), f"«{p.get('label') or name}»"
        span = f"нужно число от {p['min']:g} до {p['max']:g}" if kind in ("float", "float_map") else ""
        if kind == "float":
            if not _in_range(v, p):
                raise ValueError(f"{label}: значение {v} вне допустимого диапазона, {span}")
            v = float(v)
        elif kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{label}: нужно true или false")
        elif kind == "enum":
            if v != p.get("default") and (not isinstance(v, str) or v not in allowed):
                raise ValueError(f"{label}: значения {v!r} нет среди допустимых ({_few(allowed)})")
        elif kind == "multi":
            bad = [x for x in v if not isinstance(x, str) or x not in allowed] if isinstance(v, list) else [v]
            if bad:
                raise ValueError(f"{label}: значения {bad[0]!r} нет среди допустимых ({_few(allowed)})")
        elif kind == "float_map":
            if not isinstance(v, dict):
                raise ValueError(f"{label}: нужен объект {{имя: число}}")
            for k, x in v.items():
                if k not in allowed:
                    raise ValueError(f"{label}: неизвестное имя {k!r}, допустимы: {_few(allowed)}")
                if not _in_range(x, p):
                    raise ValueError(f"{label}, {k}: значение {x} вне допустимого диапазона, {span}")
            v = {k: float(x) for k, x in v.items()}
        else:
            raise ValueError(f"{label}: неизвестный тип параметра {kind!r} (поддерживаются float, bool, enum, multi, "
                             "float_map)")
        out[name] = v
    return out


def _in_range(x, p):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and p["min"] <= x <= p["max"]


def _few(values, k=8):
    """``a, b, c и еще 5`` for an error message."""
    return ", ".join(map(str, values[:k])) + (f" и еще {len(values) - k}" if len(values) > k else "") or "список пуст"


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
    return {"slice": meta["slice"], "outcome": decl["outcome"], "by_segment": decl.get("by_segment"),
            "params": params[1], "choices": meta["choices"],
            "default_codes": res[0], "codes": res[1], "sandbox_ms": round(ms, 1)}


def _load(spec, slice_dir):
    meta = orjson.loads((Path(slice_dir) / "index.json").read_bytes())
    data = {"n_apps": meta["n_apps"], **{k: {} for k in spec["inputs"]}}
    for key, f in meta["arrays"].items():
        a = np.load(Path(slice_dir) / f, mmap_mode="r", allow_pickle=False)
        base, _, sub = key.partition("|")
        if sub:
            data[base][sub] = a
        else:
            data[base] = a
    return data


def _codes(spec, data, params):
    o = np.asarray(spec["entrypoint"](data, params))
    n = data["n_apps"]
    if o.shape != (n,) or o.dtype.kind not in "biu" or (o.size and (o.min() < 0 or o.max() > 255)):
        raise ValueError(f"evaluate must return {n} integer outcome indices, got {o.dtype} {o.shape}")
    return base64.b64encode(o.astype(np.uint8).tobytes()).decode()


def _evaluate_body(code, name, slice_dir, params_list):
    spec = procedures.load(code, name, "whatif")
    data = _load(spec, slice_dir)
    return [_codes(spec, data, params) for params in params_list]


def prepare_factors(run_file, code, cache_dir):
    """Both slices over the union of their units (so one factor's inputs can come from BEFORE while the others stay
    AFTER), each application's id and its actual outcome index per slice (segments ``<outcome field>_<slice>``);
    ``None`` when the procedure declares no ``factors``."""
    decl = declaration(code)
    if not decl.get("factors"):
        return None
    metas = {side: _prepared(run_file, decl, Path(cache_dir) / f"factors-{side}", side, SLICES) for side in SLICES}
    values, field = decl["outcome"]["values"], decl["outcome"]["field"]
    cols = ", ".join('"' + f"{field}_{side}".replace('"', '""') + '"' for side in SLICES)
    con = mart.open_run(run_file, threads=2)
    try:
        rows = con.execute(f"SELECT APP_ID, {cols} FROM segments ORDER BY app_ord").fetchall()
    finally:
        con.close()
    index = {v: i for i, v in enumerate(values)}
    return {"metas": metas, "app_ids": [r[0] for r in rows],
            "actual": {side: bytes(index.get(r[k + 1], len(values)) for r in rows) for k, side in enumerate(SLICES)}}


def attribute(code, name, prep, out_file):
    """Causes of every decision flip over the declared ``factors`` ``{name: {"label", "inputs", "hint"?,
    "strategy"?}}``.

    For every subset of factors the procedure re-decides with those factors' inputs taken from BEFORE and all other
    inputs from AFTER (defaults for params). A factor with ``"strategy": false`` (input data) is never a cause. A flip
    gets the one strategy factor whose restoring alone brings back the BEFORE outcome; when none does on the AFTER
    input, the same test runs with the input factors restored too. Otherwise the cause is ``input`` (restoring the
    input alone reverts it: no strategy change), ``several`` (no single strategy factor or several of them revert it)
    or ``unreproduced`` (the procedure does not reproduce the AFTER outcome). Every flip gets exactly one cause.
    ``fidelity`` is the share of all applications whose AFTER outcome the procedure reproduces: causes come from an
    emulation of the strategy, so the report shows them only at 1.0. Writes ``out_file``; returns the number of flips
    and causes.
    """
    decl = declaration(code, name)
    factors = decl.get("factors") or {}
    if not 0 < len(factors) <= MAX_FACTORS or any(set(f.get("inputs", ())) - set(decl["inputs"]) for f in factors.values()):
        raise procedures.ProcedureError(f"{name}: factors must be 1..{MAX_FACTORS} entries whose inputs are declared")
    names, metas = list(factors), prep["metas"]
    params = validate(decl, metas["AFTER"]["choices"], {})
    dirs = [metas[side]["dir"] for side in SLICES]
    t0 = time.perf_counter()
    res = pipeline.confined(_factors_body, (code, name, dirs, [factors[f]["inputs"] for f in names], params), read=dirs)
    ms = (time.perf_counter() - t0) * 1000
    after, before = (np.frombuffer(prep["actual"][side], np.uint8) for side in SLICES)
    if not isinstance(res, list) or len(res) != 1 << len(names) or not all(isinstance(r, str) for r in res):
        raise procedures.ProcedureError(f"{name}: sandbox returned an invalid result")
    codes = np.stack([np.frombuffer(base64.b64decode(r, validate=True), np.uint8) for r in res])
    if codes.shape[1] != len(after):
        raise procedures.ProcedureError(f"{name}: evaluate must return {len(after)} outcome indices")
    strategy = [j for j, f in enumerate(names) if factors[f].get("strategy", True)]
    data = sum(1 << j for j, f in enumerate(names) if not factors[f].get("strategy", True))
    key = single_causes(codes == before, strategy, data)
    flips = np.flatnonzero(after != before)
    groups = {}
    for i in flips:
        groups.setdefault("unreproduced" if codes[0, i] != after[i] else
                          names[key[i]] if key[i] >= 0 else META[key[i]][0], []).append(prep["app_ids"][i])
    meta = {**dict(META.values()), "unreproduced": "процедура не воспроизводит решение"}
    causes = [{"key": k, "label": factors[k].get("label", k) if k in factors else meta[k],
               "hint": factors[k].get("hint") if k in factors else None, "app_ids": ids}
              for k, ids in sorted(groups.items(), key=lambda g: (g[0] in meta, -len(g[1])))]
    out = Path(out_file)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    tmp.write_bytes(orjson.dumps({"factors": [{"name": f, "label": factors[f].get("label", f),
                                               "strategy": factors[f].get("strategy", True)} for f in names],
                                  "flips": len(flips), "fidelity": round(float(np.mean(codes[0] == after)), 4)
                                  if len(after) else 1.0, "sandbox_ms": round(ms, 1), "causes": causes}))
    tmp.replace(out)
    return {"flips": len(flips), "causes": len(causes)}


META = {-1: ("input", "без изменения стратегии"), -2: ("several", "несколько изменений сразу")}


def single_causes(rev, strategy, data):
    """Per application the index of the only strategy factor whose restoring alone reverts it (``rev`` is ``(2**k,
    n)`` booleans indexed by subset bitmask), first on the AFTER input, then with the ``data`` mask restored;
    ``-1`` when restoring the input alone reverts it, ``-2`` when no single strategy factor or several do."""
    key = np.full(rev.shape[1], -2, np.int8)
    todo = np.ones(rev.shape[1], bool)
    for base in (0, data) if data else (0,):
        if base:
            key[todo & rev[base]] = -1
            todo &= ~rev[base]
        hits = np.stack([rev[base | 1 << j] for j in strategy]) if strategy else np.zeros((0, rev.shape[1]), bool)
        one = todo & (hits.sum(0) == 1)
        key[one] = np.asarray(strategy)[hits.argmax(0)][one]
        todo &= hits.sum(0) == 0
    return key


def _factors_body(code, name, dirs, factor_inputs, params):
    spec = procedures.load(code, name, "whatif")
    after, before = (_load(spec, d) for d in dirs)
    out = []
    for mask in range(1 << len(factor_inputs)):
        restored = {i for j, inputs in enumerate(factor_inputs) if mask >> j & 1 for i in inputs}
        out.append(_codes(spec, {k: before[k] if k in restored else v for k, v in after.items()}, params))
    return out


def _bitmaps(codes, k):
    idx = [[] for _ in range(k)]
    for i, c in enumerate(codes):
        idx[c].append(i)
    return [BitMap(x) for x in idx]


def metrics(rep, res, spec, max_values=50, top=30, max_ids=500):
    """Approval rate, swaps, fidelity and per-segment deltas of a what-if result under scope ``spec``; with the
    declaration's ``by_segment`` field only that field's values, all of them (e.g. one row per product).

    ``moved_ids`` lists the first ``max_ids`` applications whose decision would move, so the number drills into the
    application list; the cap keeps the id list small enough for a URL scope (``moved`` is always exact).
    """
    s = rep.scope(spec)
    values = res["outcome"]["values"]
    labels = [*values, "(прочее)"]
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
    by = res.get("by_segment")
    for f, vals in sorted(rep.seg.items()):
        if len(vals) > max_values or by and f != by:
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
            "by_segment": by, "segments": segs if by else segs[:top]}
