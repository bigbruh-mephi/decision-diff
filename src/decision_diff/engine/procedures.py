"""User procedures: Python code from the workspace or a placed ``.py`` file, same contract shape as the given checks.

A module exports ``PROCEDURE`` (or ``CHECK`` for checks) = ``{"id", "label", "kind", "params", "entrypoint"}``.
Kinds and entrypoints:

- ``normalize``: ``params`` is the flatten profile (``arrays``, ``exclude``, ``buckets``, ``rename``, ``key_style``,
  ``xml_array_tags``, ``id_field``); optional ``entrypoint(doc, params) -> doc`` pre-transforms each document.
- ``compare``: ``params`` has ``equivalent`` rules; no entrypoint needed.
- ``segments``: ``entrypoint(before_doc, after_doc, params) -> dict`` (one row of the segments layer per app);
  ``params["multi_valued"]`` maps a field to its separator for report filters.
- ``facts``: ``entrypoint(mart, params) -> list[dict]`` extra report facts.
- ``check``: the given check contract, ``entrypoint(mart, params) -> list[dict]`` result rows.
"""

import hashlib
import types
from pathlib import Path

KINDS = ("normalize", "compare", "segments", "facts", "check")
CHECK_FIELDS = ("LABEL", "SOURCE", "ARGUMENT", "CNT", "TOTAL", "METRIC", "APP_IDS", "RESULT", "CONFIDENCE")


class ProcedureError(ValueError):
    """Procedure code that does not satisfy the contract; shown to the user with the procedure id."""


def load(code, name="<workspace>", kind=None):
    """Execute procedure ``code`` and return its contract dict plus ``sha`` (hash of the code) and ``code``."""
    module = types.ModuleType(Path(name).stem)
    module.__file__ = name
    try:
        exec(compile(code, name, "exec"), module.__dict__)
    except Exception as e:
        raise ProcedureError(f"{name}: {type(e).__name__}: {e}") from e
    spec = getattr(module, "PROCEDURE", None) or getattr(module, "CHECK", None)
    if not isinstance(spec, dict):
        raise ProcedureError(f"{name}: module must export PROCEDURE or CHECK dict")
    spec = {"kind": "check" if hasattr(module, "CHECK") and not hasattr(module, "PROCEDURE") else None, **spec}
    if kind and spec["kind"] != kind:
        raise ProcedureError(f"{name}: expected kind {kind!r}, got {spec['kind']!r}")
    if spec["kind"] not in KINDS:
        raise ProcedureError(f"{name}: kind must be one of {KINDS}")
    missing = [k for k in ("id", "label") if not spec.get(k)]
    if missing:
        raise ProcedureError(f"{name}: missing {missing}")
    if spec["kind"] in ("segments", "facts", "check") and not callable(spec.get("entrypoint")):
        raise ProcedureError(f"{name}: {spec['kind']} procedure needs a callable entrypoint")
    spec.setdefault("params", {})
    spec["code"], spec["sha"] = code, hashlib.sha256(code.encode()).hexdigest()[:16]
    return spec


def load_file(path, kind=None):
    """Load a procedure from a placed ``.py`` file."""
    p = Path(path)
    return load(p.read_text(encoding="utf-8"), str(p), kind)


def validate_check_rows(check_id, rows):
    """Validate check output at the trust boundary; returns normalized rows or raises :class:`ProcedureError`."""
    if not isinstance(rows, list):
        raise ProcedureError(f"{check_id}: check must return a list of rows, got {type(rows).__name__}")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ProcedureError(f"{check_id}: row {i} is not a dict")
        miss = [f for f in CHECK_FIELDS if f not in r]
        if miss:
            raise ProcedureError(f"{check_id}: row {i} misses {miss}")
        if r["RESULT"] not in ("PASS", "FAIL") or r["CONFIDENCE"] not in ("HIGH", "LOW"):
            raise ProcedureError(f"{check_id}: row {i} has RESULT/CONFIDENCE outside PASS|FAIL / HIGH|LOW")
        if r["SOURCE"] not in ("BEFORE", "AFTER"):
            raise ProcedureError(f"{check_id}: row {i} SOURCE must be BEFORE or AFTER")
        out.append({**{f: r[f] for f in CHECK_FIELDS}, "APP_IDS": [str(a) for a in r["APP_IDS"] or []]})
    return out
