"""Structure change scenarios: applications grouped by which attributes appeared or disappeared, one row per cluster.

Fingerprint = set of (PATH, CHANGE_TYPE) of changes of `change_types` (default: appeared / disappeared; values
always change a lot, so value changes are left to the overview) outside the outcome-role stages of the lineage
(outcomes are consequences and would split one cause by its effect); applications without such changes get no row. Exact groups are then merged greedily
(biggest first) when Jaccard similarity >= `merge_jaccard` (1.0 disables), which absorbs optional attributes.
FAIL = the scenario moves decisions; PASS = changed without decision impact.
"""

import re

GLYPH = {"VALUE_CHANGED": "~", "ATTRIBUTE_NEW": "+", "ATTRIBUTE_LOST": "-"}
DEFAULTS = {"stages": "@lineage", "decision_path": "@lineage", "change_types": ["ATTRIBUTE_NEW", "ATTRIBUTE_LOST"],
            "merge_jaccard": 0.6, "top_n": 20,
            "low_confidence_cnt": 5, "app_ids_limit": 20}


def _glob(g):
    body = r"\.".join("(?:.*)" if s == "**" else re.escape(s).replace(r"\*", "[^.]*") for s in g.split("."))
    return re.compile("^" + body.replace(r"\.(?:.*)", r"(?:\..*)?") + "$")


def clusters(sigs, threshold):
    """Greedy leader merge of distinct signatures; returns a list of (leader signature, apps)."""
    groups = {}
    for app, s in sigs.items():
        groups.setdefault(frozenset(s), []).append(app)
    out = []
    for s in sorted(groups, key=lambda k: (-len(groups[k]), sorted(k))):
        hit = next((c for c in out if s and c[0] and len(s & c[0]) / len(s | c[0]) >= threshold), None)
        if hit:
            hit[1].extend(groups[s])
        else:
            out.append((s, list(groups[s])))
    return out


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    compiled = [(s["role"], [_glob(g) for g in s["paths"]]) for s in p["stages"]]
    cache, sigs = {}, {}
    for r in mart.rows(diff_flag=1):
        if r["CHANGE_TYPE"] not in p["change_types"]:
            continue
        path = r["PATH"]
        if path not in cache:
            role = next((role for role, gs in compiled if any(g.match(path) for g in gs)), "logic")
            cache[path] = role == "outcome"
        sig = sigs.setdefault(r["APP_ID"], set())
        if not cache[path]:
            sig.add((path, r["CHANGE_TYPE"]))
    flipped = set(mart.app_ids(path=p["decision_path"], diff_flag=1))
    total = mart.total_apps()
    rows = []
    found = clusters(sigs, p["merge_jaccard"])
    for k, (sig, apps) in enumerate(found):
        if k == p["top_n"]:
            apps = [a for _, aa in found[k:] for a in aa]
            name = f"прочие (кластеров: {len(found) - k})"
        elif not sig:
            continue
        else:
            keys = sorted(f"{path.rsplit('.', 1)[-1]}{GLYPH.get(ct, '')}" for path, ct in sig)
            name = ", ".join(keys[:4]) + (f" (+{len(keys) - 4})" if len(keys) > 4 else "")
        fl = flipped.intersection(apps)
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": f"{name}; смена решения {len(fl)}",
                     "CNT": len(apps), "TOTAL": total, "METRIC": round(len(fl) / len(apps), 4),
                     "APP_IDS": sorted(apps)[:p["app_ids_limit"]], "RESULT": "FAIL" if fl else "PASS",
                     "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH"})
        if k == p["top_n"]:
            break
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "", "CNT": 0, "TOTAL": total,
                     "METRIC": 0.0, "APP_IDS": [], "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "change_clusters", "label": "Атрибуты появились или исчезли (сценарии)",
         "description": "Группирует заявки по одинаковому набору изменений, чтобы видеть сценарии, а не тысячи отдельных отличий.", "params": DEFAULTS, "entrypoint": run}
