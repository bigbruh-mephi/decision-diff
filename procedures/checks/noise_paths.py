"""Noise candidates: attributes that differ in almost every application with almost all-distinct values.

Typical for request ids and timestamps missing from the technical exclusions; such a path puts every application
into "changed" and destroys clustering. FAIL = suggest adding the path to the exclusions (never applied silently).
"""

DEFAULTS = {"min_diff_share": 0.99, "min_distinct_share": 0.99, "min_apps": 20, "low_confidence_cnt": 5,
            "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    stats = {}
    for r in mart.rows(diff_flag=1):
        s = stats.setdefault(r["PATH"], {"apps": set(), "values": set()})
        s["apps"].add(r["APP_ID"])
        s["values"].add(r["VALUE_AFTER"])
    rows = []
    for path, s in sorted(stats.items()):
        n = len(s["apps"])
        if n < p["min_apps"] or n < p["min_diff_share"] * total or len(s["values"]) < p["min_distinct_share"] * n:
            continue
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": path, "CNT": n, "TOTAL": total,
                     "METRIC": round(n / total, 4), "APP_IDS": sorted(s["apps"])[:p["app_ids_limit"]],
                     "RESULT": "FAIL", "CONFIDENCE": "LOW" if n < p["low_confidence_cnt"] else "HIGH"})
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "", "CNT": 0, "TOTAL": total,
                     "METRIC": 0.0, "APP_IDS": [], "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "noise_paths", "label": "Кандидаты в технические исключения", "params": DEFAULTS, "entrypoint": run}
