"""Approval-rate change per segment value with a paired 95 % confidence interval.

BEFORE and AFTER score the SAME applications, so the change is a paired difference: delta = (swap_in - swap_out) / n
with the Bonett-Price adjusted Wald interval. FAIL when the interval excludes zero and |delta| >= `min_effect`
(direction "both" or "drop"). Segments with n < `min_n` are exploratory (CONFIDENCE LOW); a segment value covering
every application repeats the ALL row and is skipped.
"""

import math

DEFAULTS = {"field": "DECISION", "positive": "APPROVE", "segments": ["PRODUCTS", "PARTNER_TYPE", "ROUTING_METHOD"],
            "separator": ";", "min_effect": 0.01, "direction": "both", "z": 1.96, "min_n": 100,
            "low_confidence_cnt": 5, "app_ids_limit": 20}


def paired_ci(n, n_in, n_out, z):
    """Bonett & Price (2012) adjusted Wald CI for a difference of paired proportions."""
    p_in, p_out = (n_in + 1) / (n + 2), (n_out + 1) / (n + 2)
    d = p_in - p_out
    se = math.sqrt(max(p_in + p_out - d * d, 0.0) / (n + 2))
    return d - z * se, d + z * se


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    groups = {}
    for s in mart.segments():
        b = s.get(p["field"] + "_BEFORE") == p["positive"]
        a = s.get(p["field"] + "_AFTER") == p["positive"]
        keys = [("ALL", "")] + [(f, v) for f in p["segments"]
                                for v in str(s.get(f) or "").split(p["separator"]) if v]
        for k in keys:
            g = groups.setdefault(k, {"n": 0, "b": 0, "a": 0, "in": [], "out": []})
            g["n"] += 1
            g["b"] += b
            g["a"] += a
            if a and not b:
                g["in"].append(s["APP_ID"])
            if b and not a:
                g["out"].append(s["APP_ID"])
    rows = []
    total = groups.get(("ALL", ""), {"n": 0})["n"]
    for (f, v), g in sorted(groups.items()):
        if f != "ALL" and g["n"] == total:
            continue
        n, n_in, n_out = g["n"], len(g["in"]), len(g["out"])
        delta = (n_in - n_out) / n
        lo, hi = paired_ci(n, n_in, n_out, p["z"])
        significant = hi < 0 or (lo > 0 and p["direction"] == "both")
        swapped = sorted(g["in"] + g["out"])
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER",
                     "ARGUMENT": f"{f'{f}={v}' if f != 'ALL' else 'все заявки'}: доля одобрений {g['b'] / n:.3f} -> "
                                 f"{g['a'] / n:.3f}, 95% ДИ [{lo:+.3f}; {hi:+.3f}]",
                     "CNT": len(swapped), "TOTAL": n, "METRIC": round(delta, 4),
                     "APP_IDS": swapped[:p["app_ids_limit"]],
                     "RESULT": "FAIL" if significant and abs(delta) >= p["min_effect"] else "PASS",
                     "CONFIDENCE": "LOW" if n < p["min_n"] or len(swapped) < p["low_confidence_cnt"] else "HIGH"})
    return rows


CHECK = {"id": "approval_rate_ci", "label": "Доля одобрений по сегментам: значимое изменение", "params": DEFAULTS,
         "entrypoint": run}
