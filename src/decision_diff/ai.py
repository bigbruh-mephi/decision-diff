"""AI second opinion (research R10): evidence pack -> call 1 hypotheses -> system measures -> call 2 findings -> verify.

The model never sees application ids and never produces a number the system did not measure: derived attributes are
scalar SQL expressions over declared per-application variables, whitelisted on DuckDB's own AST and evaluated on a
read-only connection with external access disabled; findings carry numbers only as ``{ID.field}`` placeholders that
the verifier fills from the pack or the measurements, and a claim about how two sets relate needs a measurement that
confirms it (fail closed). Everything here is generic: paths and the decision field come from the run and its params.
"""

import fnmatch
import json
import re
import threading
import time

import duckdb
import httpx

from .report import RunReport, _glob
from .settings import settings

PACK_TOKENS = 20_000
MAX_SPECS = 16
SPEC_TIMEOUT_S = 10
LANG = "Russian"

SYSTEM = """You are an independent senior risk-strategy reviewer giving a second opinion on a change of a credit
decision strategy. Two versions (BEFORE = production, AFTER = candidate) were run on the same applications; the
system has already computed every count you see. Your value is what the deterministic report and the user's
checks do NOT say: meta-conclusions that follow from relations between attributes, stages and applicant data.

How to reason (silently, then fill `analysis`):
1. Read the change clusters K (apps grouped by the exact set of changed attributes) and the headline H.
2. For every cluster ask: is the change caused by the strategy (rules, cut-offs, models) or by the input data
   (attributes that should be identical in both runs)? Use D (numeric delta profiles: a constant offset or ratio on
   many apps is a data/scale defect, not real behaviour) and S (segment values that describe who is in a cluster,
   with precision/recall; explained=false means the segments do not explain the selection).
3. Look for inconsistencies a human sees from meaning, not statistics: rule names/descriptions vs applicant values
   in samples A, elements present only with one value (P), partial changes of the same BEFORE state (R: only part
   of identical apps changed - targeted rollout, hidden attribute, or a bug).
4. Compare with the check results C: do not restate a failing check; say what it misses or misattributes.
5. Every hypothesis that needs a number the pack does not contain must be stated as a derived attribute (vars +
   SQL expression) so the system measures it on all applications. Never estimate numbers yourself.

Hard rules: use only the provided items; cite item ids; prefer "insufficient data" to guessing; identifiers such as
rule or model names go in backticks; write user-facing text in {lang}. Answer with one JSON object only."""

CALL1 = """TASK (call 1 of 2): propose up to {max_h} hypotheses, each with one or more derived attributes the system
will evaluate over all {n_apps} applications.

Derived attribute = {{"name": snake_case, "why": text, "vars": {{alias: {{"path": PATH from schema M, "elem":
glob over ARRAY_KEY (e.g. "*|RULE_NAME") or null, "side": "before"|"after", "agg": "value"|"min"|"max"|"count"|
"list", "type": "num"|"text"}}}}, "where": optional SQL boolean population filter, "expr": SQL boolean}}.
Allowed in expressions: the aliases, built-ins flipped, changed, cluster ('K1'..), decision_before, decision_after,
comparisons, AND/OR/NOT, arithmetic, CASE, BETWEEN, IN, IS NULL, LIKE, abs, coalesce, least, greatest, round,
list_contains, len, lower, upper, starts_with, contains, regexp_matches. Values are strings unless type=num;
absent attributes are NULL. The system returns n_population, n_true, flip rates inside/outside, split by cluster.

JSON schema of your answer:
{schema}

EVIDENCE PACK (JSON; sections H headline, K clusters, D deltas, R partial changes/flips, S segment descriptors,
P presence, C checks, A samples, M schema):
{pack}"""

CALL2 = """TASK (call 2 of 2): the system evaluated your derived attributes. Measurements X (refused specs carry the
reason; you may not re-submit). Write the final opinion.
- Keep only hypotheses the measurements support; mark the others as refuted in `analysis`, never in findings.
- Numbers in title, text and check_next ONLY as placeholders {{ID.field}} pointing at a cited item (e.g.
  {{X3.n_true}}, {{K2.apps}}, {{R5.by_elem.NAME.0}}); never type digits except inside backticked identifiers.
- `evidence` lists item ids only ("X3", "K2"), never "X3.n_true". Shares as percent: {{X3.flip_rate_true:%}}.
  Attribute values (cut-off 0.55, product 751) only in backticks: `0.55`, `751`.
- Measurement fields: n_true = applications where expr is true; n_true_flipped = how many of THEM changed the
  decision (co-occurrence, not "caused by"); flip_rate_true vs flip_rate_rest = decision-change rate inside vs outside
  that set. Say "caused by" only from K.causes: the system's counterfactual - restoring that BEFORE factor alone (or
  the listed combination) reverts exactly that many flips of the cluster.
- claim_type "relation" (any statement that two sets coincide, overlap, explain or exclude each other) must cite
  the X that measures it and state `expects` (none|some|all) - the system checks it.
- At most {max_f} findings, most decision-relevant first; each with what to check next.

JSON schema of your answer:
{schema}

(The conversation continues from your call-1 answer; the evidence pack above is still valid.)
MEASUREMENTS X:
{derived}"""

REPAIR = """The system rejected these findings (reason given; nothing else was wrong with them):
{rejected}
Rewrite ONLY these findings with the same JSON schema: every count or share as a placeholder {{ID.field}} or
{{ID.field:%}} of a cited item, attribute values in backticks, no other digits in title, text or check_next. Drop a
finding if its number is not in any item."""

DERIVED = {"type": "object", "required": ["name", "why", "vars", "expr"],
           "properties": {"name": {"type": "string"}, "why": {"type": "string"},
                          "vars": {"type": "object", "additionalProperties": {
                              "type": "object", "required": ["path", "side"],
                              "properties": {"path": {"type": "string"}, "elem": {"type": ["string", "null"]},
                                             "side": {"enum": ["before", "after"]},
                                             "agg": {"enum": ["value", "min", "max", "count", "list"]},
                                             "type": {"enum": ["num", "text"]}}}},
                          "where": {"type": ["string", "null"]}, "expr": {"type": "string"}}}
FINDING = {"type": "object", "required": ["title", "severity", "claim_type", "evidence", "text", "check_next"],
           "properties": {"title": {"type": "string", "maxLength": 120},
                          "severity": {"enum": ["high", "medium", "low"]},
                          "claim_type": {"enum": ["single_item", "relation"]},
                          "expects": {"enum": ["none", "some", "all"]},
                          "evidence": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                          "text": {"type": "string", "maxLength": 700},
                          "check_next": {"type": "string", "maxLength": 300}}}
SCHEMA1 = {"type": "object", "required": ["analysis", "hypotheses"],
           "properties": {"analysis": {"type": "string", "maxLength": 3000},
                          "hypotheses": {"type": "array", "maxItems": 8, "items": {
                              "type": "object", "required": ["statement", "evidence", "derived"],
                              "properties": {"statement": {"type": "string"},
                                             "evidence": {"type": "array", "items": {"type": "string"}},
                                             "derived": {"type": "array", "minItems": 1, "maxItems": 3,
                                                         "items": DERIVED}}}}}}
SCHEMA2 = {"type": "object", "required": ["analysis", "findings"],
           "properties": {"analysis": {"type": "string", "maxLength": 3000},
                          "findings": {"type": "array", "maxItems": 7, "items": FINDING}}}


class SpecError(ValueError):
    """A derived-attribute spec the system refuses to run; returned to the model as feedback in call 2."""


class LLMError(RuntimeError):
    """The endpoint failed or kept answering outside the contract."""


def _rows(con, sql, params=()):
    cur = con.execute(sql, list(params))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _dump(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str)


def tokens(o):
    """shortcut: chars/3 estimate (no tokenizer dependency), replace with the endpoint's tokenizer if budgets bite."""
    return len(_dump(o)) // 3


NET = [(re.compile(p), r) for p, r in ((r"[\w.+-]+@[\w-]+\.[\w.]+", "<EMAIL>"), (r"\+?\d[\d ()-]{9,}\d", "<PHONE>"),
                                       (r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>"))]


class Masker:
    """Values of attributes matching params ``pii_paths`` globs become ``<masked>``; a regex net catches e-mails,
    phone numbers and ISO dates in every other string that enters the pack."""

    def __init__(self, globs):
        self.globs, self.masked = [_glob(g) for g in globs], 0

    def pii(self, name):
        return any(fnmatch.fnmatchcase(name, g) for g in self.globs)

    def __call__(self, path, v):
        if v is None:
            return None
        if self.pii(path):
            self.masked += 1
            return "<masked>"
        for p, r in NET:
            v, k = p.subn(r, str(v))
            self.masked += k
        return v


def prepare(rep):
    """Temp tables on the report connection: ``h`` (horizontal BEFORE/AFTER per key) and ``base0`` (per-app
    built-ins of the DSL: decision before/after, flipped, changed, cluster id ``K<cid>``)."""
    con = rep.con
    con.execute("SET memory_limit = '1500MB'")
    con.execute("""CREATE OR REPLACE TEMP TABLE h AS SELECT app_ord, path_id, ARRAY_KEY AS ak,
        split_part(ARRAY_KEY, '|', -1) AS tail, max(VALUE) FILTER (SOURCE = 'BEFORE') AS vb,
        max(VALUE) FILTER (SOURCE = 'AFTER') AS va, max(DIFF_FLAG) AS d, max(CHANGE_TYPE) AS ct FROM mart GROUP BY ALL""")
    n = len(rep.app_id)
    cols = {"cluster": ["unchanged"] * n, "decision_before": [None] * n, "decision_after": [None] * n}
    for c in rep.clusters:
        for i in c["apps"]:
            cols["cluster"][i] = f"K{c['cid']}"
    for name, side in (("decision_before", rep.dec_before), ("decision_after", rep.dec_after)):
        for v, bm in side.items():
            for i in bm:
                cols[name][i] = v
    con.execute("""CREATE OR REPLACE TEMP TABLE base0 AS SELECT * FROM (SELECT unnest(range(?::BIGINT))::UINTEGER AS app_ord,
        unnest(?::VARCHAR[]) AS cluster, unnest(?::VARCHAR[]) AS decision_before, unnest(?::VARCHAR[]) AS decision_after,
        unnest(?::BOOLEAN[]) AS flipped, unnest(?::BOOLEAN[]) AS changed)""",
                [n, cols["cluster"], cols["decision_before"], cols["decision_after"],
                 [i in rep.flipped for i in range(n)], [i in rep.changed for i in range(n)]])


def build_pack(rep, checks=(), mask=None, budget=PACK_TOKENS):
    """Evidence pack of generic miners (ids on every item, no application ids), cut to ``budget`` tokens.

    Call :func:`prepare` first. ``checks`` are check result rows ``{label, rows}`` of the run.
    """
    con, mask = rep.con, mask or Masker(())
    path = {pid: a["PATH"] for pid, a in rep.attrs.items()}
    n = len(rep.app_id)
    pack = {"H": [{"id": "H1", **rep.summary({})}]}

    K = []
    for c in rep.cluster_rows({}, top=20):
        if c["cid"] is None:
            K.append({"id": "K_other", **c})
            continue
        K.append({"id": f"K{c['cid']}", "apps": c["apps"], "decision_changed": c["decision_changed"],
                  "origin": c["origin"], "n_paths": len(c["paths"]), "paths": c["paths"][:12],
                  "transitions": [{**t, "before": mask(t["path"], t["before"]), "after": mask(t["path"], t["after"])}
                                  for t in c["transitions"]], "swap": c["swap"],
                  "causes": [{"label": x["label"], "apps": x["apps"]} for x in c.get("causes") or []]})
    pack["K"] = K

    D = _rows(con, """WITH y AS (SELECT path_id, app_ord, b, a, round(a - b, 6) dl,
                      CASE WHEN b <> 0 THEN round(a / b, 4) END rt FROM (SELECT path_id, app_ord,
                      try_cast(vb AS DOUBLE) b, try_cast(va AS DOUBLE) a FROM h WHERE ct = 'VALUE_CHANGED')
                      WHERE a IS NOT NULL AND b IS NOT NULL), m AS (SELECT path_id, mode(dl) md FROM y GROUP BY 1)
                      SELECT path_id, count(DISTINCT app_ord) apps, round(avg((dl > 0)::INT), 3) share_up,
                      median(dl) median_delta, any_value(md) mode_delta, round(avg((dl = md)::INT), 3) mode_share,
                      mode(rt) mode_ratio, min(b) min_before, max(b) max_before, min(a) min_after, max(a) max_after
                      FROM y JOIN m USING (path_id) GROUP BY path_id HAVING apps >= 5 ORDER BY apps DESC, path_id
                      LIMIT 20""")
    pack["D"] = [{"id": f"D{i + 1}", "path": path[r.pop("path_id")], **r, "constant_offset": r["mode_share"] >= 0.9}
                 for i, r in enumerate(r for r in D if not mask.pii(path[r["path_id"]]))]

    pack["R"] = _partial(con, path, mask)
    pack["S"] = _segments(rep, K, mask, n)
    pack["P"] = _presence(con, path, mask)
    seen = {}
    for chk in checks:
        for r in chk.get("rows") or []:
            k = (chk.get("label"), mask("", r.get("ARGUMENT")), r.get("CNT"), r.get("TOTAL"), r.get("RESULT"),
                 r.get("CONFIDENCE"))
            seen[k] = seen.get(k, 0) + 1
    pack["C"] = [{"id": f"C{i + 1}", "check": k[0], "argument": k[1], "cnt": k[2], "total": k[3], "result": k[4],
                  "confidence": k[5], "rows": v} for i, (k, v) in enumerate(seen.items())]
    pack["A"] = _samples(con, K, path, mask)
    pack["M"] = [{"id": f"M{i + 1}", "path": path[r.pop("path_id")], **r} for i, r in enumerate(_rows(con, """
        SELECT path_id, count(DISTINCT app_ord) FILTER (vb IS NOT NULL) apps_before,
        count(DISTINCT app_ord) FILTER (va IS NOT NULL) apps_after, approx_count_distinct(coalesce(vb, va)) k,
        round(avg((try_cast(coalesce(vb, va) AS DOUBLE) IS NOT NULL)::INT), 2) num,
        count(DISTINCT app_ord) FILTER (d = 1) apps_changed, count(DISTINCT ak) elems FROM h GROUP BY path_id
        ORDER BY path_id"""))]
    return fit(pack, budget)


def fit(pack, budget):
    """Cut the pack until it fits ``budget`` tokens: schema detail and tails first, samples A last (they carry the
    semantic contradictions)."""
    cuts = [("M", lambda m: [{"id": x["id"], "path": x["path"], "num": x["num"], "changed": x["apps_changed"]}
                             for x in m]),
            ("P", lambda p: p[:6]), ("S", lambda s: s[:6]), ("D", lambda d: d[:8]), ("R", lambda r: r[:5]),
            ("K", lambda k: [{**x, "transitions": x.get("transitions", [])[:1], "paths": x.get("paths", [])[:4]}
                             for x in k[:10]]),
            ("C", lambda c: c[:10]), ("M", lambda m: [x for x in m if x["changed"]][:60]),
            ("A", lambda a: a[:3]), ("A", lambda a: [{**x, "rows": x["rows"][:8]} for x in a[:2]]),
            ("A", lambda a: [])]
    for key, cut in cuts:
        if tokens(pack) <= budget:
            break
        pack[key] = cut(pack[key])
    if tokens(pack) > budget:
        raise ValueError(f"evidence pack does not fit {budget} tokens even after all cuts")
    return pack


def _partial(con, path, mask):
    """Partial changes (same BEFORE value, only part of the apps changed; by element) and partial flips (same
    transition, only part of the apps flipped the decision). Identical app sets are reported once."""
    out, seen, per_parent = [], set(), {}
    for r in _rows(con, """WITH s AS (SELECT path_id, vb, app_ord, max(d) d FROM h WHERE vb IS NOT NULL GROUP BY ALL),
                           lc AS (SELECT path_id FROM h GROUP BY 1 HAVING count(DISTINCT vb) <= 20)
                           SELECT path_id, vb, count(*) pop, sum(d)::INT ch,
                           md5(string_agg(CASE WHEN d = 1 THEN app_ord::VARCHAR END, ',' ORDER BY app_ord)) hs
                           FROM s WHERE path_id IN (SELECT path_id FROM lc) GROUP BY ALL
                           HAVING ch >= 5 AND ch <= 0.95 * pop ORDER BY ch DESC, path_id, vb LIMIT 60"""):
        p = path[r["path_id"]]
        par = p.rsplit(".", 1)[0]
        if r["hs"] in seen or mask.pii(p) or per_parent.get(par, 0) >= 2 or len(out) >= 10:
            continue
        seen.add(r["hs"])
        per_parent[par] = per_parent.get(par, 0) + 1
        to = _rows(con, """SELECT coalesce(va, '(absent)') va, count(DISTINCT app_ord) n FROM h WHERE path_id = ?
                           AND vb = ? AND d = 1 GROUP BY 1 ORDER BY 2 DESC LIMIT 4""", [r["path_id"], r["vb"]])
        els = _rows(con, """SELECT tail, count(DISTINCT app_ord) FILTER (d = 1) ch, count(DISTINCT app_ord) pop FROM h
                            WHERE path_id = ? AND vb = ? AND tail <> '' GROUP BY 1 ORDER BY ch DESC, tail LIMIT 6""",
                    [r["path_id"], r["vb"]])
        item = {"id": f"R{len(out) + 1}", "kind": "partial_change", "path": p, "before": mask(p, r["vb"]),
                "apps_with_before": r["pop"], "apps_changed": r["ch"], "apps_kept": r["pop"] - r["ch"],
                "changed_to": {mask(p, t["va"]): t["n"] for t in to}}
        if len(els) > 1:
            item["by_elem"] = {e["tail"]: [e["ch"], e["pop"]] for e in els}
        out.append(item)
    k = 0
    for r in _rows(con, """WITH s AS (SELECT DISTINCT path_id, vb, va, app_ord FROM h WHERE d = 1)
                           SELECT path_id, vb, va, count(*) apps, count(*) FILTER (flipped) flips,
                           md5(string_agg(app_ord::VARCHAR, ',' ORDER BY app_ord)) hs
                           FROM s JOIN base0 USING (app_ord) GROUP BY ALL
                           HAVING flips >= 5 AND flips <= 0.9 * apps AND apps >= 20
                           ORDER BY apps DESC, path_id LIMIT 100"""):
        p = path[r["path_id"]]
        if r["hs"] in seen or mask.pii(p) or k >= 6:
            continue
        seen.add(r["hs"])
        k += 1
        out.append({"id": f"R{len(out) + 1}", "kind": "partial_flip", "path": p, "before": mask(p, r["vb"]),
                    "after": mask(p, r["va"]), "apps": r["apps"], "apps_flipped": r["flips"]})
    return out


def _segments(rep, K, mask, n):
    """Best two single segment values describing each top cluster (WRAcc; precision, recall). Outcome fields
    (``*_AFTER``, the decision change flag) and fields with more than 20 values are not descriptors."""
    dec = rep.params.get("decision_field", "DECISION")
    out = []
    for c in [c for c in K if c["id"] != "K_other"][:8]:
        tgt = next(x["apps"] for x in rep.clusters if f"K{x['cid']}" == c["id"])
        p0 = len(tgt) / n
        cand = []
        for field, vals in rep.seg.items():
            if field.endswith("_AFTER") or field == f"{dec}_CHANGED" or len(vals) > 20 or mask.pii(field):
                continue
            for v, bm in vals.items():
                tp = len(bm & tgt)
                if tp:
                    cand.append((len(bm) / n * (tp / len(bm) - p0), field, v, len(bm), tp))
        for w, field, v, m, tp in sorted(cand, key=lambda x: (-x[0], x[1], x[2]))[:2]:
            if w <= 0:
                continue
            prec, rec = round(tp / m, 3), round(tp / len(tgt), 3)
            out.append({"id": f"S{len(out) + 1}", "describes": c["id"], "rule": f"segment {field} = '{mask(field, v)}'",
                        "apps_matching": m, "target_hit": tp, "target_apps": len(tgt), "precision": prec,
                        "recall": rec, "explained": prec >= 0.8 and rec >= 0.8})
    return out


def _presence(con, path, mask):
    """Array elements present on one side only, with low-cardinality leaf value histograms next to the histograms
    of sibling elements of the same parent (an element only ever reported with one value is an anomaly)."""
    rows = _rows(con, """SELECT path_id, tail, count(DISTINCT app_ord) FILTER (vb IS NOT NULL) b,
                         count(DISTINCT app_ord) FILTER (va IS NOT NULL) a, histogram(vb) FILTER (vb IS NOT NULL) hb,
                         histogram(va) FILTER (va IS NOT NULL) ha, count(DISTINCT coalesce(vb, va)) k
                         FROM h WHERE tail <> '' GROUP BY ALL""")
    el = {}
    for r in rows:
        el.setdefault((path[r["path_id"]].rsplit(".", 1)[0], r["tail"]), []).append(r)
    stable = {key for key, leaves in el.items() if max(x["b"] for x in leaves) == max(x["a"] for x in leaves)}
    out = []
    for (par, tail), leaves in el.items():
        if (par, tail) in stable:
            continue
        hist = {}
        for x in leaves:
            p = path[x["path_id"]]
            if x["k"] <= 5 and not mask.pii(p):
                sib = {}
                for key in stable:
                    if key[0] == par:
                        for s in el[key]:
                            if s["path_id"] == x["path_id"]:
                                for v, c in (s["ha"] or {}).items():
                                    sib[v] = sib.get(v, 0) + c
                hist[p.rsplit(".", 1)[-1]] = {"before": x["hb"] or {}, "after": x["ha"] or {}, "siblings_after": sib}
        out.append({"elem_of": par, "elem": tail, "apps_before": max(x["b"] for x in leaves),
                    "apps_after": max(x["a"] for x in leaves), "leaf_values": hist})
    out.sort(key=lambda r: (-abs(r["apps_before"] - r["apps_after"]), r["elem_of"], r["elem"]))
    return [{"id": f"P{i + 1}", **r} for i, r in enumerate(out[:12])]


def _samples(con, K, path, mask, k=5):
    """One pseudonymised application per top-``k`` cluster: changed rows (one per path) and unchanged sibling leaves
    of the changed array elements (names, descriptions, flags), all values through the masker."""
    out = []
    for c in [c for c in K if c["id"] != "K_other"][:k]:
        o = con.execute("SELECT min(app_ord) FROM base0 WHERE cluster = ?", [c["id"]]).fetchone()[0]
        rows = _rows(con, """WITH c AS (SELECT DISTINCT ak FROM h WHERE app_ord = ? AND d = 1 AND ak <> ''),
                             r AS (SELECT path_id, ak, vb, va, ct, d, row_number() OVER (PARTITION BY path_id, d
                             ORDER BY ak) rn FROM h WHERE app_ord = ? AND (d = 1 OR ak IN (SELECT ak FROM c)))
                             SELECT * FROM r WHERE rn = 1 ORDER BY d DESC, path_id, ak""", [o, o])
        rows = [r for r in rows if r["d"] == 1][:14] + [r for r in rows if r["d"] == 0][:8]
        out.append({"id": f"A{len(out) + 1}", "cluster": c["id"], "app": f"app#{len(out) + 1}",
                    "rows": [{"path": path[r["path_id"]], "elem_key": r["ak"] or None,
                              "before": mask(path[r["path_id"]], r["vb"]), "after": mask(path[r["path_id"]], r["va"]),
                              "ct": r["ct"]} for r in rows]})
    return out


CLASSES = {"COLUMN_REF", "CONSTANT", "COMPARISON", "CONJUNCTION", "OPERATOR", "FUNCTION", "CASE", "BETWEEN", "CAST"}
FUNCS = {"abs", "coalesce", "least", "greatest", "round", "list_contains", "list_value", "len", "length", "lower",
         "upper", "starts_with", "ends_with", "contains", "regexp_matches", "ifnull", "nullif", "+", "-", "*", "/",
         "%", "||", "~~", "!~~", "~~*", "!~~*"}
BUILTIN = {"flipped", "changed", "cluster", "decision_before", "decision_after"}
AGG = {"value": "any_value({v})", "min": "min({v})", "max": "max({v})", "count": "count({v})", "list": "list({v})"}
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def check_expr(con, expr, names):
    """Parse ``expr`` with DuckDB, walk the AST against the whitelist and return the canonical
    ``SELECT (<expr>) AS e`` the parser regenerates (no comments, no trailing text); raise SpecError otherwise."""
    if not isinstance(expr, str) or not expr.strip() or len(expr) > 2000:
        raise SpecError("expression must be a non-empty SQL expression string (<= 2000 chars)")
    raw = con.execute("SELECT json_serialize_sql(?)", [f"SELECT ({expr}) AS e"]).fetchone()[0]
    ast = json.loads(raw)
    if ast.get("error"):
        raise SpecError(f"parse error: {ast.get('error_message')}")
    stmts = ast["statements"]
    node = stmts[0]["node"]
    if len(stmts) != 1 or node.get("type") != "SELECT_NODE" or len(node.get("select_list", [])) != 1 \
            or node.get("from_table", {}).get("type") not in (None, "EMPTY") or node.get("modifiers") \
            or node.get("cte_map", {}).get("map") or any(node.get(k) for k in ("where_clause", "having", "qualify")) \
            or (node.get("groups") or {}).get("group_expressions"):
        raise SpecError("only a single scalar expression is allowed")

    def walk(x):
        if isinstance(x, list):
            for y in x:
                walk(y)
            return
        if not isinstance(x, dict):
            return
        cls = x.get("class")
        if cls is not None:
            if cls not in CLASSES:
                raise SpecError(f"construct {cls} is not allowed")
            if cls == "COLUMN_REF" and (len(x["column_names"]) != 1 or x["column_names"][0] not in names):
                raise SpecError(f"unknown name {'.'.join(x['column_names'])}; declare it in vars")
            if cls == "FUNCTION" and (x["function_name"].lower() not in FUNCS or x.get("schema") or x.get("catalog")
                                      or x.get("filter") or x.get("distinct")):
                raise SpecError(f"function {x['function_name']} is not allowed")
        for k, v in x.items():
            if k not in ("query_location", "query_location_length", "alias"):
                walk(v)
    walk(node["select_list"])
    return con.execute("SELECT json_deserialize_sql(?)", [raw]).fetchone()[0]


def compile_spec(con, spec, paths):
    """SQL + params evaluating one spec into per-app rows ``(cluster, flipped, pop, val)``. ``paths`` maps PATH to
    its path ids. A var named like a built-in (``decision_before``...) is ignored: the built-in already means it."""
    if not isinstance(spec, dict) or not IDENT.match(str(spec.get("name", ""))):
        raise SpecError("name must match [a-z][a-z0-9_]*")
    vs = spec.get("vars")
    if not isinstance(vs, dict) or not 1 <= len(vs) <= 8:
        raise SpecError("1..8 vars required")
    vs = {a: v for a, v in vs.items() if a not in BUILTIN}
    ctes, params = [], []
    for alias, v in vs.items():
        if not IDENT.match(alias) or not isinstance(v, dict):
            raise SpecError(f"bad var {alias}")
        if v.get("path") not in paths:
            raise SpecError(f"unknown path {v.get('path')} (see schema M)")
        agg, typ, elem = v.get("agg") or "value", v.get("type") or "text", v.get("elem")
        if v.get("side") not in ("before", "after") or agg not in AGG or typ not in ("num", "text") \
                or not isinstance(elem, (str, type(None))):
            raise SpecError(f"var {alias}: side before|after, agg {sorted(AGG)}, type num|text, elem glob or null")
        col = "vb" if v["side"] == "before" else "va"
        val = f"try_cast({col} AS DOUBLE)" if typ == "num" else col
        ctes.append(f"v_{alias} AS (SELECT app_ord, {AGG[agg].format(v=val)} AS {alias} FROM h WHERE path_id IN "
                    f"(SELECT unnest(?::UINTEGER[])){' AND ak GLOB ?' if elem else ''} AND {col} IS NOT NULL "
                    f"GROUP BY app_ord)")
        params += [paths[v["path"]]] + ([elem] if elem else [])
    names = set(vs) | BUILTIN
    expr = check_expr(con, spec.get("expr"), names)
    where = check_expr(con, spec["where"], names) if spec.get("where") else "SELECT true AS e"
    joins = " ".join(f"LEFT JOIN v_{a} USING (app_ord)" for a in vs)
    sql = (f"WITH {''.join(c + ', ' for c in ctes)}v__all AS (SELECT {''.join(a + ', ' for a in vs)}b.* "
           f"FROM base0 b {joins}) "
           f"SELECT cluster, flipped, coalesce(w.e, false) AS pop, coalesce(x.e, false) AS val FROM v__all, "
           f"LATERAL ({where}) w, LATERAL ({expr}) x")
    return sql, params


def measure(con, spec, paths, timeout_s=SPEC_TIMEOUT_S):
    """Evaluate one spec over every application (interrupted after ``timeout_s``); counts only, no app ids."""
    sql, params = compile_spec(con, spec, paths)
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        rows = con.execute(f"""WITH r AS ({sql}) SELECT cluster, count(*) FILTER (pop), count(*) FILTER (pop AND val),
            count(*) FILTER (pop AND val AND flipped), count(*) FILTER (pop AND flipped) FROM r GROUP BY cluster""",
                           params).fetchall()
    finally:
        timer.cancel()
    n_pop, n_true, n_tf, n_pf = (sum(r[i] for r in rows) for i in range(1, 5))
    return {"name": spec["name"], "expr": spec["expr"], "where": spec.get("where"), "n_population": n_pop,
            "n_true": n_true, "n_true_flipped": n_tf, "share_true": round(n_true / n_pop, 4) if n_pop else None,
            "flip_rate_true": round(n_tf / n_true, 4) if n_true else None,
            "flip_rate_rest": round((n_pf - n_tf) / (n_pop - n_true), 4) if n_pop - n_true else None,
            "by_cluster": {c: t for c, _, t, _, _ in sorted(rows, key=lambda r: -r[2])[:6] if t}}


def measure_all(con, specs, paths):
    out = []
    for i, spec in enumerate(specs[:MAX_SPECS]):
        t = time.perf_counter()
        try:
            res = {"id": f"X{i + 1}", **measure(con, spec, paths)}
        except (SpecError, duckdb.Error, TypeError, AttributeError) as e:
            res = {"id": f"X{i + 1}", "name": str((spec or {}).get("name") if isinstance(spec, dict) else None),
                   "refused": f"{type(e).__name__}: {e}"[:300]}
        res["eval_ms"] = round((time.perf_counter() - t) * 1000, 1)
        out.append(res)
    return out


PH = re.compile(r"\{([A-Za-z_]\w*)((?:\.[^\s{}.]+)*)\}")
TICK = re.compile(r"`[^`]*`")
IDS = re.compile(r"\b[A-Z]\d+\b")
DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
ATTR = re.compile(r"[\w.]+\s*=\s*[^\s,;:)]+")
NAME = re.compile(r"\b[A-Za-z_][\w@]*\d[\w@]*")


def _resolve(item, dotted):
    cur = item
    for part in [p for p in dotted.split(".") if p]:
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur if isinstance(cur, (int, float, str)) and not isinstance(cur, bool) else None


def _apps(item):
    for k in ("n_true", "apps", "apps_changed", "target_hit", "cnt"):
        if isinstance(item.get(k), int):
            return item[k]
    return None


def verify(findings, idx):
    """Fail-closed verification of call-2 findings against ``idx`` (pack items and measurements by id).

    Evidence written as a placeholder (``X7.n_true``) counts as its item id (``X7``). A finding is rejected when it
    cites an unknown id, when a number in its text is typed instead of a placeholder
    or a placeholder does not resolve to a cited item (``name=value`` pairs and names with digits such as
    ``RULE_2026`` are identifiers, not numbers), or when it is a ``relation`` claim without an evaluated
    measurement, without ``expects`` or contradicted by the measurement. Returns ``(findings, rejected)``.
    """
    kept, rejected = [], []
    for f in findings if isinstance(findings, list) else []:
        if not isinstance(f, dict):
            continue
        ev = [e if e in idx else e.split(".", 1)[0] for e in f.get("evidence") or [] if isinstance(e, str)]
        xs = [idx[e] for e in ev if e in idx and "n_true" in idx[e]]
        reason, numbers = None, 0
        if not ev or any(e not in idx for e in ev):
            reason = f"unknown evidence {[e for e in ev if e not in idx] or 'none'}"
        elif f.get("claim_type") == "relation":
            exp = f.get("expects")
            if not xs:
                reason = "relation without a measurement"
            elif exp not in ("none", "some", "all"):
                reason = "relation without expects"
            elif any((exp == "none" and x["n_true"] > 0) or (exp == "some" and x["n_true"] == 0) or
                     (exp == "all" and x["n_true"] != x["n_population"]) for x in xs):
                reason = "refuted by measurement " + ", ".join(f"{x['id']}.n_true={x['n_true']}" for x in xs)
        out = {}
        for key in ("title", "text", "check_next"):
            text, bad = str(f.get(key) or ""), []

            def sub(m, bad=bad, ev=ev):
                dotted, pct = m.group(2).removesuffix(":%"), m.group(2).endswith(":%")
                v = _resolve(idx.get(m.group(1)), dotted) if m.group(1) in ev else None
                if v is None or (pct and isinstance(v, str)):
                    bad.append(m.group(0))
                    return "[?]"
                return f"{v * 100:.1f} %".replace(".", ",") if pct else str(v)
            out[key] = PH.sub(sub, text)
            numbers += len(PH.findall(text)) - len(bad)
            bad += DIGITS.findall(NAME.sub("", ATTR.sub("", IDS.sub("", TICK.sub("", PH.sub("", text))))))
            if bad and not reason:
                reason = f"numbers not from the system in {key}: {bad[:5]}"
        if reason:
            rejected.append({"title": out.get("title") or str(f.get("title")), "reason": reason})
            continue
        x = xs[0] if xs else None
        kept.append({"title": out["title"], "severity": f.get("severity") if f.get("severity") in
                     ("high", "medium", "low") else "low", "text": out["text"],
                     "derived_attribute": f"{x['name']}: {x['expr']}" if x else None,
                     "evidence": [{"label": e + (f" {idx[e]['name']}" if "name" in idx[e] else ""),
                                   "apps": _apps(idx[e])} for e in ev],
                     "verified": bool(numbers or xs), "check_next": out["check_next"] or None})
    return kept, rejected


def _parse(text, schema):
    """First JSON object in ``text`` (code fences tolerated) with the schema's required keys."""
    s = text.strip()
    if "{" in s:
        if "}" not in s:
            raise ValueError("JSON is cut off before its closing brace")
        s = s[s.index("{"):s.rindex("}") + 1]
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON: {e}") from e
    miss = [k for k in schema["required"] if k not in obj] if isinstance(obj, dict) else schema["required"]
    if miss:
        raise ValueError(f"missing keys {miss}")
    return obj


class Client:
    """OpenAI-compatible chat client: structured output ``json_schema`` -> ``json_object`` -> plain prompt (the
    endpoint's 400/422 on ``response_format`` downgrades the mode), retries on transport errors, 429 and 5xx, one
    repair round when the answer is not the requested JSON. The schema is always in the prompt as well.
    ``max_tokens`` is explicit because reasoning models spend hidden reasoning tokens from the same budget; an answer
    cut at that limit (``finish_reason: length``) fails at once instead of a blind repeat."""

    MODES = ("json_schema", "json_object", "prompt")
    MAX_TOKENS = 16384

    def __init__(self, base_url, api_key, model, timeout_s=300, retries=2, backoff_s=1.0):
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"},
                                 timeout=httpx.Timeout(timeout_s, connect=10))
        self.model, self.retries, self.backoff_s, self.mode = model, retries, backoff_s, "json_schema"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def _send(self, body):
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post("/chat/completions", json=body)
                if r.status_code != 429 and r.status_code < 500:
                    return r
                err = f"HTTP {r.status_code}"
            except httpx.TransportError as e:
                err = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                time.sleep(self.backoff_s * 2 ** attempt)
        raise LLMError(f"endpoint unavailable after {self.retries + 1} attempts: {err}")

    def _complete(self, messages, schema, name, temperature):
        while True:
            body = {"model": self.model, "messages": messages, "temperature": temperature,
                    "max_tokens": self.MAX_TOKENS}
            if self.mode == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}
            elif self.mode == "json_object":
                body["response_format"] = {"type": "json_object"}
            r = self._send(body)
            if r.status_code in (400, 422) and self.mode != "prompt":
                self.mode = self.MODES[self.MODES.index(self.mode) + 1]
                continue
            if r.status_code >= 400:
                raise LLMError(f"endpoint answered HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            u = data.get("usage") or {}
            self.usage["prompt_tokens"] += u.get("prompt_tokens") or 0
            self.usage["completion_tokens"] += u.get("completion_tokens") or 0
            self.usage["calls"] += 1
            try:
                choice = data["choices"][0]
                text = choice["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError("endpoint answer has no choices[0].message.content") from e
            if choice.get("finish_reason") == "length":
                raise LLMError(f"answer cut at the output token limit {self.MAX_TOKENS} (usage so far {self.usage})")
            return text

    def chat(self, messages, schema, name, temperature=0.2):
        """Returns ``(parsed JSON object, messages with the assistant answer appended)``."""
        for attempt in range(2):
            text = self._complete(messages, schema, name, temperature)
            try:
                return _parse(text, schema), messages + [{"role": "assistant", "content": text}]
            except ValueError as e:
                if attempt:
                    raise LLMError(f"answer outside the JSON contract twice: {e}") from e
                messages = messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": f"Your answer is invalid ({e}). Reply with one "
                                                                   f"JSON object matching the schema, nothing else."}]


def opinion(run_file, params, checks=(), client=None):
    """Full second opinion for a finished run file; returns the stored result shape (state ``done``)."""
    params = params or {}
    client = client or Client(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
    t, t0 = {}, time.perf_counter()
    rep = RunReport(run_file, params)
    try:
        prepare(rep)
        mask = Masker(params.get("pii_paths") or [])
        pack = build_pack(rep, checks, mask)
        t["pack_s"] = round(time.perf_counter() - t0, 2)
        n = len(rep.app_id)
        msgs = [{"role": "system", "content": SYSTEM.replace("{lang}", LANG)},
                {"role": "user", "content": CALL1.format(max_h=8, n_apps=n, schema=_dump(SCHEMA1), pack=_dump(pack))}]
        t1 = time.perf_counter()
        a1, msgs = client.chat(msgs, SCHEMA1, "hypotheses", temperature=0.5)
        t["call1_s"] = round(time.perf_counter() - t1, 2)
        specs = [d for h in a1.get("hypotheses") or [] if isinstance(h, dict)
                 for d in (h.get("derived") or [])[:3]]
        paths = {}
        for pid, a in rep.attrs.items():
            paths.setdefault(a["PATH"], []).append(pid)
        t1 = time.perf_counter()
        xs = measure_all(rep.con, specs, paths)
        t["measure_s"] = round(time.perf_counter() - t1, 2)
        msgs.append({"role": "user", "content": CALL2.format(max_f=7, schema=_dump(SCHEMA2), derived=_dump(xs))})
        t1 = time.perf_counter()
        a2, msgs = client.chat(msgs, SCHEMA2, "findings")
        t["call2_s"] = round(time.perf_counter() - t1, 2)
        idx = {it["id"]: it for items in pack.values() for it in items} | {x["id"]: x for x in xs}
        findings, rejected = verify(a2.get("findings"), idx)
        a3 = None
        if any(r["reason"].startswith("numbers not from the system") for r in rejected):
            msgs.append({"role": "user", "content": REPAIR.format(rejected=_dump(rejected))})
            t1 = time.perf_counter()
            a3, _ = client.chat(msgs, SCHEMA2, "findings")
            t["call3_s"] = round(time.perf_counter() - t1, 2)
            fixed, still = verify(a3.get("findings"), idx)
            findings += [f for f in fixed if f["title"] not in {g["title"] for g in findings}]
            rejected = still + [r for r in rejected if r["title"] not in {f["title"] for f in fixed + still}]
    finally:
        rep.con.close()
    t["total_s"] = round(time.perf_counter() - t0, 2)
    return {"state": "done", "model": client.model, "findings": findings, "rejected": rejected, "measurements": xs,
            "usage": {**client.usage, "pack_tokens_est": tokens(pack), "mode": client.mode}, "timings": t,
            "privacy": {"pii_paths": list(params.get("pii_paths") or []), "masked_values": mask.masked,
                        "app_ids_sent": False},
            "audit": {"call1": a1, "call2": a2, "call3": a3}, "error": None}
