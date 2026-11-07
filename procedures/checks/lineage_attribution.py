"""Where does each change start? First differing stage along the user-declared lineage, per application.

Stages are ordered globs with a role (input / reference / logic / outcome). Applications whose first change is an
input are tested against the BEFORE population ("donor test"): for every changed low-cardinality logic value
(cut-off name, band, flag) the BEFORE applications with the same input value must show it as their most common
value (without an exact match: it must occur at the nearest input value below or above). Passed = the change is explained by input data under the old logic (false alarm for the strategy).
FAIL rows: input-explained with decision flips, input + unexplained logic change (mixed), and outcome-only changes
(decision moved with nothing upstream changed). Logic / reference rows PASS: the change is the strategy's own.
"""

import bisect
import re
from collections import Counter

ROLE = {"logic": "логика", "outcome": "результат", "reference": "справочник"}
DEFAULTS = {"stages": "@lineage", "decision_path": "@lineage", "max_levels": 50, "donors": 25,
            "low_confidence_cnt": 5, "app_ids_limit": 20}


def _glob(g):
    body = r"\.".join("(?:.*)" if s == "**" else re.escape(s).replace(r"\*", "[^.]*") for s in g.split("."))
    return re.compile("^" + body.replace(r"\.(?:.*)", r"(?:\..*)?") + "$")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _donor_values(idx, x0, value_of, cap):
    """BEFORE values of donors whose input equals ``x0``; without one, of donors at the nearest input on each side."""
    i = j = bisect.bisect_left(idx, (x0, ""))
    exact = []
    while j < len(idx) and idx[j][0] == x0:
        if len(exact) < cap and (v := value_of(idx[j][1])) is not None:
            exact.append(v)
        j += 1
    if exact:
        return exact, True
    side = []
    for k, step in ((i - 1, -1), (j, 1)):
        x, n = None, 0
        while 0 <= k < len(idx) and (x is None or idx[k][0] == x) and n < cap:
            if (v := value_of(idx[k][1])) is not None:
                x, n = idx[k][0], n + 1
                side.append(v)
            k += step
    return side, False


def _donor_test(mart, input_led, stage_of, p):
    """Split input-led apps into explained / mixed by comparing changed logic values with BEFORE donors."""
    tests, keys = {}, {}
    for app, rs in input_led.items():
        inp = sorted((path, _num(va)) for path, _, va, _ in rs
                     if stage_of(path)[2] == "input" and _num(va) is not None)
        keys[app] = inp[0] if inp else None
        tests[app] = [(path, ak, va) for path, ak, va, ct in rs
                      if stage_of(path)[2] == "logic" and ct == "VALUE_CHANGED"]
    logic_paths = sorted({t[0] for ts in tests.values() for t in ts})
    before, levels = {}, {}
    for r in mart.rows(path=logic_paths) if logic_paths else ():
        before[(r["PATH"], r["ARRAY_KEY"], r["APP_ID"])] = r["VALUE_BEFORE"]
        levels.setdefault(r["PATH"], set()).add(r["VALUE_BEFORE"])
    index = {}
    for path in sorted({k[0] for k in keys.values() if k}):
        index[path] = sorted((x, r["APP_ID"]) for r in mart.rows(path=path)
                             if (x := _num(r["VALUE_BEFORE"])) is not None)
    explained, mixed = set(), set()
    for app, key in keys.items():
        ok = True
        for path, akey, value in tests[app]:
            if key is None or len(levels.get(path, ())) > p["max_levels"]:
                continue
            vals, exact = _donor_values(index[key[0]], key[1],
                                        lambda d, path=path, akey=akey: before.get((path, akey, d)), p["donors"])
            if vals and (Counter(vals).most_common(1)[0][0] != value if exact else value not in vals):
                ok = False
                break
        (explained if ok else mixed).add(app)
    return explained, mixed


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    compiled = [(i, s["name"], s["role"], [_glob(g) for g in s["paths"]]) for i, s in enumerate(p["stages"])]
    cache = {}

    def stage_of(path):
        if path not in cache:
            cache[path] = next(((i, n, r) for i, n, r, gs in compiled if any(g.match(path) for g in gs)),
                               (len(compiled), "прочее", "logic"))
        return cache[path]

    changed = {}
    for r in mart.rows(diff_flag=1):
        changed.setdefault(r["APP_ID"], []).append((r["PATH"], r["ARRAY_KEY"], r["VALUE_AFTER"], r["CHANGE_TYPE"]))
    flipped = set(mart.app_ids(path=p["decision_path"], diff_flag=1))
    groups, input_led = {}, {}
    for app, rs in changed.items():
        first = min(stage_of(r[0]) for r in rs)
        if first[2] == "input":
            input_led[app] = rs
        else:
            groups.setdefault((first[0], f"{first[1]} ({ROLE.get(first[2], first[2])})", first[2]), set()).add(app)
    explained, mixed = _donor_test(mart, input_led, stage_of, p)
    if explained:
        groups[(-2, "вход: объясняется входными данными при логике BEFORE", "input")] = explained
    if mixed:
        groups[(-1, "вход и логика: изменение логики не объясняется входом", "mixed")] = mixed
    rows = []
    for (_, label, role), apps in sorted(groups.items()):
        fl = apps & flipped
        fail = role == "outcome" or role == "mixed" or (role == "input" and fl)
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": f"{label}; смен решения {len(fl)}",
                     "CNT": len(apps), "TOTAL": len(changed), "METRIC": round(len(fl) / len(apps), 4),
                     "APP_IDS": (sorted(fl) + sorted(apps - fl))[:p["app_ids_limit"]],
                     "RESULT": "FAIL" if fail else "PASS",
                     "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH"})
    return rows


CHECK = {"id": "lineage_attribution", "label": "Источник изменения по цепочке решения", "params": DEFAULTS,
         "entrypoint": run}
