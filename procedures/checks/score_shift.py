"""Distribution shift of numeric attributes (scores): PSI on BEFORE-decile bins and two-sample KS.

Units are datamart elements (application x array key). PSI FAILs at >= `fail_psi` (rule of thumb: < 0.1 stable,
0.1-0.25 moderate, > 0.25 major) only if it also exceeds its sampling critical value chi2(0.95, B-1) * (1/n + 1/m)
(Yurdakul 2018); KS FAILs at >= `fail_ks` only above the asymptotic 5 % critical value.
"""

import bisect
import math

DEFAULTS = {"paths": ["characteristics.score_okb", "decisionResult.product_trials.scoring.pd.value",
                      "decisionResult.product_trials.scoring.scoreModels.value", "decisionResult.scoring.value"],
            "bins": 10, "fail_psi": 0.1, "fail_ks": 0.05, "eps": 1e-4, "low_confidence_cnt": 5, "app_ids_limit": 20}


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _chi2_95(k):
    """Wilson-Hilferty approximation of the 95 % chi-square quantile with k degrees of freedom."""
    return k * (1 - 2 / (9 * k) + 1.6449 * math.sqrt(2 / (9 * k))) ** 3


def psi(before, after, bins, eps):
    """PSI with bin edges at BEFORE quantiles; returns (psi, number of bins)."""
    s = sorted(before)
    edges = sorted({s[int(len(s) * i / bins)] for i in range(1, bins)})
    def shares(xs):
        c = [0] * (len(edges) + 1)
        for x in xs:
            c[bisect.bisect_right(edges, x)] += 1
        return [max(v / len(xs), eps) for v in c]
    b, a = shares(before), shares(after)
    return sum((y - x) * math.log(y / x) for x, y in zip(b, a)), len(b)


def ks(before, after):
    """Two-sample Kolmogorov-Smirnov statistic sup |F_before - F_after|."""
    b, a = sorted(before), sorted(after)
    return max(abs(bisect.bisect_right(b, x) / len(b) - bisect.bisect_right(a, x) / len(a)) for x in set(b) | set(a))


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    rows = []
    for path in p["paths"]:
        before, after, changed = [], [], set()
        for r in mart.rows(path=path):
            xb, xa = _num(r["VALUE_BEFORE"]), _num(r["VALUE_AFTER"])
            if xb is not None:
                before.append(xb)
            if xa is not None:
                after.append(xa)
            if r["DIFF_FLAG"]:
                changed.add(r["APP_ID"])
        if len(before) < 2 or len(after) < 2:
            continue
        n, m = len(before), len(after)
        value, nb = psi(before, after, p["bins"], p["eps"])
        crit_psi = _chi2_95(max(nb - 1, 1)) * (1 / n + 1 / m)
        d = ks(before, after)
        crit_ks = 1.358 * math.sqrt((n + m) / (n * m))
        band = "стабильно" if value < 0.1 else "умеренный сдвиг" if value < 0.25 else "сильный сдвиг"
        common = {"LABEL": CHECK["label"], "SOURCE": "AFTER", "CNT": len(changed), "TOTAL": n,
                  "APP_IDS": sorted(changed)[:p["app_ids_limit"]],
                  "CONFIDENCE": "LOW" if len(changed) < p["low_confidence_cnt"] else "HIGH"}
        rows.append({**common, "ARGUMENT": f"{path} PSI ({band}, порог {crit_psi:.4f})", "METRIC": round(value, 4),
                     "RESULT": "FAIL" if value >= p["fail_psi"] and value > crit_psi else "PASS"})
        rows.append({**common, "ARGUMENT": f"{path} KS (порог {crit_ks:.4f})", "METRIC": round(d, 4),
                     "RESULT": "FAIL" if d >= p["fail_ks"] and d > crit_ks else "PASS"})
    return rows


CHECK = {"id": "score_shift", "label": "Сдвиг распределения скоров (PSI, KS)", "params": DEFAULTS, "entrypoint": run}
