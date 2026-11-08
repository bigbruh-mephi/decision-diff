"""Swap set: applications whose decision moved between slices, one row per (BEFORE -> AFTER) pair.

Generic over any segment field pair `<field>_BEFORE` / `<field>_AFTER`; FAIL when a swap cell holds more than
`max_share` of the run (swap-in and swap-out are reported separately, as in credit-risk swap-set analysis).
"""

DEFAULTS = {"field": "DECISION", "max_share": 0.05, "low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    cells = {}
    for s in mart.segments():
        b, a = s.get(p["field"] + "_BEFORE"), s.get(p["field"] + "_AFTER")
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


CHECK = {"id": "decision_swap", "label": "Смена решения (swap-set)",
         "description": "Сколько заявок перешло из одного решения в другое (например, одобрено -> отказ).", "params": DEFAULTS, "entrypoint": run}
