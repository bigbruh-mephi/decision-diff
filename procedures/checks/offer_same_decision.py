"""Same decision, different product: the top offer changed while the decision stayed the same.

Generic over two segment field pairs (`<offer_field>_BEFORE/_AFTER`, `<decision_field>_BEFORE/_AFTER`); one row per
offer transition. FAIL when a transition covers more than `max_share` of the run.
"""

DEFAULTS = {"offer_field": "TOP_OFFER", "decision_field": "DECISION", "max_share": 0.01, "low_confidence_cnt": 5,
            "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    cells = {}
    for s in mart.segments():
        if s.get(p["decision_field"] + "_BEFORE") != s.get(p["decision_field"] + "_AFTER"):
            continue
        b, a = s.get(p["offer_field"] + "_BEFORE"), s.get(p["offer_field"] + "_AFTER")
        if b != a:
            cells.setdefault((str(b), str(a)), []).append(s["APP_ID"])
    rows = []
    for (b, a), apps in sorted(cells.items(), key=lambda kv: -len(kv[1])):
        share = len(apps) / total if total else 0.0
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": f"{b} -> {a}", "CNT": len(apps),
                     "TOTAL": total, "METRIC": round(share, 4), "APP_IDS": sorted(apps)[:p["app_ids_limit"]],
                     "RESULT": "FAIL" if share > p["max_share"] else "PASS",
                     "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH"})
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "", "CNT": 0, "TOTAL": total,
                     "METRIC": 0.0, "APP_IDS": [], "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "offer_same_decision", "label": "Решение то же, оффер другой", "params": DEFAULTS, "entrypoint": run}
