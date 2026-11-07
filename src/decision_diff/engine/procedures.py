"""User procedures: Python code from the workspace or a placed ``.py`` file, same contract shape as the given checks.

A module exports ``PROCEDURE`` (or ``CHECK`` for checks) = ``{"id", "label", "kind", "params", "entrypoint"}``.
Kinds and entrypoints:

- ``normalize``: ``params`` is the flatten profile (``arrays``, ``exclude``, ``buckets``, ``rename``, ``key_style``,
  ``xml_array_tags``, ``id_field``); optional ``entrypoint(doc, params) -> doc`` pre-transforms each document.
- ``compare``: ``params`` has ``equivalent`` rules; no entrypoint needed.
- ``segments``: ``entrypoint(before_doc, after_doc, params) -> dict`` (one row of the segments layer per app);
  ``params["multi_valued"]`` maps a field to its separator for report filters.
- ``facts``: ``entrypoint(mart, params) -> list[dict]`` extra report facts.
- ``check``: the given check contract, ``entrypoint(mart, params) -> list[dict]`` result rows. A param whose value
  is ``"@lineage"`` receives the run's lineage param of the same name.
- ``lineage``: params only, ``{"stages": [{"name", "role", "paths"}], "decision_path"}``; ordered stages, first
  matching glob wins; roles ``input | reference | logic | outcome``.
- ``whatif``: ``entrypoint(data, params) -> array[n_apps]`` of outcome indices over arrays the app prepares from the
  declared ``unit_depth``, ``inputs`` and ``outcome``; ``params`` declares the user controls (see ``whatif.py``).
"""

import contextlib
import hashlib
import types
from pathlib import Path

KINDS = ("normalize", "compare", "segments", "facts", "check", "lineage", "whatif")
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
        raise ProcedureError(f"{name}: модуль должен объявить словарь PROCEDURE или CHECK")
    spec = {"kind": "check" if hasattr(module, "CHECK") and not hasattr(module, "PROCEDURE") else None, **spec}
    if kind and spec["kind"] != kind:
        raise ProcedureError(f"{name}: нужна процедура вида {kind!r}, а объявлена {spec['kind']!r}")
    if spec["kind"] not in KINDS:
        raise ProcedureError(f"{name}: вид процедуры (kind) должен быть одним из: {', '.join(KINDS)}")
    missing = [k for k in ("id", "label") if not spec.get(k)]
    if missing:
        raise ProcedureError(f"{name}: не заполнены поля {', '.join(missing)}")
    if spec["kind"] in ("segments", "facts", "check", "whatif") and not callable(spec.get("entrypoint")):
        raise ProcedureError(f"{name}: процедуре вида {spec['kind']!r} нужна функция entrypoint")
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
        raise ProcedureError(f"{check_id}: проверка должна вернуть список строк, а вернула {type(rows).__name__}")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ProcedureError(f"{check_id}: строка {i} результата не является словарем")
        miss = [f for f in CHECK_FIELDS if f not in r]
        if miss:
            raise ProcedureError(f"{check_id}: в строке {i} результата нет полей {', '.join(miss)}")
        if r["RESULT"] not in ("PASS", "FAIL") or r["CONFIDENCE"] not in ("HIGH", "LOW"):
            raise ProcedureError(f"{check_id}: в строке {i} RESULT должен быть PASS или FAIL, CONFIDENCE - HIGH или LOW")
        if r["SOURCE"] not in ("BEFORE", "AFTER"):
            raise ProcedureError(f"{check_id}: в строке {i} SOURCE должен быть BEFORE или AFTER")
        out.append({**{f: r[f] for f in CHECK_FIELDS}, "APP_IDS": [str(a) for a in r["APP_IDS"] or []]})
    return out


def declaration(code, name="<workspace>"):
    """Literal entries of the ``PROCEDURE`` (or ``CHECK``) dict assignment, read WITHOUT executing the code.

    Returns ``(variable name, {key: literal})``; an entry naming a module-level literal (``"params": DEFAULTS``)
    takes that literal, other non-literal entries (``entrypoint``) are left out. A what-if declaration
    (``unit_depth``, ``inputs``, ``outcome``, ``params``) is read this way by trusted code.
    Raises :class:`ProcedureError` on syntax errors or a missing contract.
    """
    import ast

    try:
        tree = ast.parse(code, name)
    except SyntaxError as e:
        raise ProcedureError(f"{name}, строка {e.lineno}: синтаксическая ошибка ({e.msg})") from e
    found, consts = {}, {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if node.targets[0].id in ("PROCEDURE", "CHECK") and isinstance(node.value, ast.Dict):
            meta = {}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant):
                    if isinstance(v, ast.Name) and v.id in consts:
                        meta[k.value] = consts[v.id]
                    with contextlib.suppress(ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                        meta[k.value] = ast.literal_eval(v)
            found[node.targets[0].id] = meta
        else:
            with contextlib.suppress(ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                consts[node.targets[0].id] = ast.literal_eval(node.value)
    var = "PROCEDURE" if "PROCEDURE" in found else "CHECK" if "CHECK" in found else None
    if var is None:
        raise ProcedureError(f"{name}: модуль должен присвоить PROCEDURE или CHECK словарь-литерал {{...}}")
    return var, found[var]


def inspect_source(code, name="<workspace>"):
    """Contract metadata from source WITHOUT executing it (for the API and file discovery).

    Reads the literal ``id``/``label``/``kind`` of the ``PROCEDURE`` or ``CHECK`` assignment; raises
    :class:`ProcedureError` on syntax errors or a missing contract. Execution happens only in the sandbox.
    """
    var, meta = declaration(code, name)
    kind = meta.get("kind") or ("check" if var == "CHECK" else None)
    if kind not in KINDS:
        raise ProcedureError(f"{name}: вид процедуры (kind) должен быть одним из: {', '.join(KINDS)}")
    if not isinstance(meta.get("id"), str) or not isinstance(meta.get("label"), str) or not meta["id"] \
            or not meta["label"]:
        raise ProcedureError(f"{name}: id и label должны быть непустыми строками-литералами")
    return {"kind": kind, "id": meta["id"], "label": meta["label"],
            "sha": hashlib.sha256(code.encode()).hexdigest()[:16]}
