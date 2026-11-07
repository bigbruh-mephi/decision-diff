"""Rule hit-rate change: for every element of a keyed array with a boolean leaf, apps that hit BEFORE vs AFTER.

Covers what the stop-factor check does not: a rule that started firing (new rule or false -> true), as well as
removed / silenced rules. The element name is one component of ARRAY_KEY (`key_part`, default the last one).
FAIL when the hit share of the run moved by at least `min_delta`.
"""

DEFAULTS = {"paths": ["decisionResult.product_trials.rules.hit"], "key_part": -1, "true_values": ["true"],
            "min_delta": 0.005, "low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    hit_b, hit_a = {}, {}
    for r in mart.rows(path=p["paths"]):
        name = (r["ARRAY_KEY"] or "").split("|")[p["key_part"]]
        if r["VALUE_BEFORE"] in p["true_values"]:
            hit_b.setdefault(name, set()).add(r["APP_ID"])
        if r["VALUE_AFTER"] in p["true_values"]:
            hit_a.setdefault(name, set()).add(r["APP_ID"])
    rows = []
    for name in sorted(hit_b.keys() | hit_a.keys()):
        b, a = hit_b.get(name, set()), hit_a.get(name, set())
        moved = sorted(b ^ a)
        if not moved:
            continue
        delta = (len(a) - len(b)) / total if total else 0.0
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": f"{name}: {len(b)} -> {len(a)} заявок",
                     "CNT": len(moved), "TOTAL": total, "METRIC": round(delta, 4),
                     "APP_IDS": moved[:p["app_ids_limit"]],
                     "RESULT": "FAIL" if abs(delta) >= p["min_delta"] else "PASS",
                     "CONFIDENCE": "LOW" if len(moved) < p["low_confidence_cnt"] else "HIGH"})
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "", "CNT": 0, "TOTAL": total,
                     "METRIC": 0.0, "APP_IDS": [], "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "rule_hit_rate", "label": "Частота срабатывания правил изменилась", "params": DEFAULTS,
         "entrypoint": run}
