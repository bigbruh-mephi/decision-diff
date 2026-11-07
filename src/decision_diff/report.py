"""Read model of a finished run for the report API: every count is an exact number of distinct applications.

A *scope* narrows the population: ``{"seg": {field: [values]}, "cluster": cid, "apps": [app ids]}`` -> one bitmap
(AND across fields and keys, OR within one field). All blocks accept a scope, so the analyst can zoom from the
population to a segment, a change cluster, an application and an attribute and back without losing context.
Domain words (which field is the decision, which value is an approval, which paths are input data) come from the
segments procedure params, never from this code.
"""

import fnmatch
import threading
from collections import OrderedDict

from pyroaring import BitMap

from .engine import mart

FACT_DIMS = ("PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "PATH", "ADJUSTED_VALUE", "DIFF_FLAG", "CHANGE_TYPE",
             "SOURCE")
MAX_CELLS = 50_000


def _glob(pattern):
    return pattern.replace("**", "\0").replace("*", "[!.]*").replace("\0", "*")


class RunReport:
    """Bitmaps and dictionaries of one immutable run file, loaded once and shared by all requests."""

    def __init__(self, path, params):
        self.con = mart.open_run(path, threads=2, memory_limit="512MB")
        self.params = params or {}
        q = self.con.cursor()
        self.attrs = {r[0]: dict(zip(("PATH", "PREFIX", "CLASS", "SUFFIX", "KEY", "JSONPATH"), r[1:])) for r in
                      q.execute("SELECT path_id, PATH, PREFIX, CLASS, SUFFIX, KEY, JSONPATH FROM attrs").fetchall()}
        self.app_id = [r[0] for r in q.execute("SELECT APP_ID FROM apps ORDER BY app_ord").fetchall()]
        self.app_ord = {a: i for i, a in enumerate(self.app_id)}
        self.all = BitMap(range(len(self.app_id)))
        self.facts = [(dict(zip(("path_id", "ARRAY_KEY", "ADJUSTED_VALUE", "DIFF_FLAG", "CHANGE_TYPE", "SOURCE"),
                                r[:6])), BitMap.deserialize(r[6])) for r in q.execute(
            "SELECT path_id, ARRAY_KEY, ADJUSTED_VALUE, DIFF_FLAG, CHANGE_TYPE, SOURCE, APPS FROM facts").fetchall()]
        self.seg = {}
        for field, value, apps in q.execute("SELECT FIELD, VALUE, APPS FROM seg_bitmaps").fetchall():
            self.seg.setdefault(field, {})[value] = BitMap.deserialize(apps)
        self.clusters = [{"cid": cid, "paths": list(paths), "apps": BitMap.deserialize(apps)} for cid, paths, apps in
                         q.execute("SELECT cid, paths, APPS FROM clusters ORDER BY cid").fetchall()]
        self.transitions = {}
        for pid, vb, va, apps in q.execute("SELECT path_id, VALUE_BEFORE, VALUE_AFTER, APPS FROM transitions").fetchall():
            self.transitions.setdefault(pid, []).append((vb, va, BitMap.deserialize(apps)))
        self.structure = [dict(zip(("path_id", "PATH", "PREFIX", "apps_new", "apps_lost", "apps_type_changed",
                                    "apps_before", "apps_after"), r)) for r in q.execute(
            "SELECT * FROM structure ORDER BY apps_new + apps_lost DESC").fetchall()]
        self.changed = BitMap.union(*(c["apps"] for c in self.clusters)) if self.clusters else BitMap()
        dec = self.params.get("decision_field", "DECISION")
        self.dec_before, self.dec_after = self.seg.get(f"{dec}_BEFORE", {}), self.seg.get(f"{dec}_AFTER", {})
        self.flipped = self.seg.get(f"{dec}_CHANGED", {}).get("1", BitMap())
        inputs = [_glob(p) for p in self.params.get("input_paths", [])]
        self.input_paths = {pid for pid, a in self.attrs.items() if any(fnmatch.fnmatchcase(a["PATH"], g)
                                                                        for g in inputs)}
        self.lock = threading.Lock()

    def scope(self, spec):
        """Bitmap of the applications in scope ``spec`` (see module docstring)."""
        bm = BitMap(self.all)
        for field, values in (spec.get("seg") or {}).items():
            vals = self.seg.get(field, {})
            bm &= BitMap.union(*(vals.get(str(v), BitMap()) for v in values)) if values else BitMap()
        if spec.get("cluster"):
            bm &= next((c["apps"] for c in self.clusters if c["cid"] == int(spec["cluster"])), BitMap())
        if spec.get("apps") is not None:
            bm &= BitMap(self.app_ord[a] for a in spec["apps"] if a in self.app_ord)
        return bm

    def summary(self, spec):
        """Verdict tiles and the decision swap matrix for the scope."""
        s = self.scope(spec)
        changed, flipped = s & self.changed, s & self.flipped
        positive = self.params.get("positive_decision")

        def rate(side):
            bm = side.get(positive, BitMap()) if positive else BitMap()
            return round(len(bm & s) / len(s), 4) if positive and len(s) else None

        swap = [{"before": vb, "after": va, "apps": n} for vb, b in sorted(self.dec_before.items())
                for va, a in sorted(self.dec_after.items()) if (n := len(b & a & s))]
        return {"apps": len(s), "changed": len(changed), "decision_changed": len(flipped),
                "changed_same_decision": len(changed - flipped), "unchanged": len(s - changed),
                "approval_before": rate(self.dec_before), "approval_after": rate(self.dec_after),
                "input_driven": len(BitMap.union(BitMap(), *(c["apps"] for c in self.clusters
                                                             if self._origin(c) == "input")) & s),
                "swap": swap}

    def _origin(self, c):
        """``input`` when the cluster's changes include declared input-data paths: the decision may follow the data,
        not the strategy (a false alarm for the analyst); ``logic`` otherwise."""
        return "input" if any(p in self.input_paths for p in c["paths"]) else "logic"

    def cluster_rows(self, spec, top=20):
        s = self.scope(spec)
        out = []
        for c in self.clusters:
            apps = c["apps"] & s
            if not apps:
                continue
            trans = sorted(((len(bm & apps), pid, vb, va) for pid in c["paths"]
                            for vb, va, bm in self.transitions.get(pid, []) if vb != "(other)"), reverse=True)[:3]
            out.append({"cid": c["cid"], "apps": len(apps), "decision_changed": len(apps & self.flipped),
                        "origin": self._origin(c), "paths": [self.attrs[p]["PATH"] for p in c["paths"]],
                        "input_paths": [self.attrs[p]["PATH"] for p in c["paths"] if p in self.input_paths],
                        "transitions": [{"path": self.attrs[p]["PATH"], "before": vb, "after": va, "apps": n}
                                        for n, p, vb, va in trans],
                        "swap": [{"before": vb, "after": va, "apps": n} for vb, b in self.dec_before.items()
                                 for va, a in self.dec_after.items() if vb != va and (n := len(b & a & apps))]})
        out.sort(key=lambda r: -r["apps"])
        if len(out) > top:
            rest = out[top:]
            out = out[:top] + [{"cid": None, "apps": sum(r["apps"] for r in rest), "clusters": len(rest),
                                "decision_changed": sum(r["decision_changed"] for r in rest), "origin": "mixed"}]
        return out

    def attributes(self, spec, limit=100):
        """Changed applications per PATH split by CHANGE_TYPE, with the share of decision flips among them."""
        s = self.scope(spec)
        per = {}
        for f, bm in self.facts:
            if f["DIFF_FLAG"]:
                d = per.setdefault(f["path_id"], {})
                d[f["CHANGE_TYPE"]] = d.get(f["CHANGE_TYPE"], BitMap()) | bm
        rows = []
        for pid, types in per.items():
            allb = BitMap.union(*types.values()) & s
            if allb:
                rows.append({"path_id": pid, **{k: self.attrs[pid][k] for k in ("PATH", "PREFIX", "KEY")},
                             "apps": len(allb), "decision_changed": len(allb & self.flipped),
                             "input": pid in self.input_paths,
                             "by_type": {k: len(v & s) for k, v in types.items() if v & s},
                             "transitions": [{"before": vb, "after": va, "apps": len(bm & s)}
                                             for vb, va, bm in self.transitions.get(pid, [])[:5] if bm & s]})
        rows.sort(key=lambda r: (-r["apps"], r["PATH"]))
        return rows[:limit]

    def segment_rows(self, spec, max_values=30):
        """Per segment field and value: applications, changed, decision changed (top values + other)."""
        s = self.scope(spec)
        out = []
        for field, vals in sorted(self.seg.items()):
            if len(vals) > max_values * 10:
                continue
            rows = sorted(({"value": v, "apps": len(bm & s), "changed": len(bm & s & self.changed),
                            "decision_changed": len(bm & s & self.flipped)} for v, bm in vals.items() if bm & s),
                          key=lambda r: -r["apps"])
            if len(rows) > max_values:
                rest = rows[max_values:]
                rows = rows[:max_values] + [{"value": f"(other {len(rest)})", "apps": sum(r["apps"] for r in rest),
                                             "changed": sum(r["changed"] for r in rest),
                                             "decision_changed": sum(r["decision_changed"] for r in rest)}]
            out.append({"field": field, "values": rows})
        return out

    def _fact_value(self, f, dim):
        if dim in ("PREFIX", "CLASS", "SUFFIX", "KEY", "PATH"):
            return self.attrs[f["path_id"]][dim]
        return f[dim]

    def pivot(self, rows, cols, filters, spec):
        """Exact distinct-app counts for every combination of ``rows + cols`` dimensions.

        Dimensions are fact fields (``FACT_DIMS``), segment fields or ``CLUSTER``; ``filters`` maps a dimension to
        allowed values. Returns ``{"dims", "cells": [[*keys, apps]], "truncated"}``.
        """
        dims = list(rows) + list(cols)
        fdims = [d for d in dims if d in FACT_DIMS]
        sdims = [d for d in dims if d not in FACT_DIMS]
        s = self.scope(spec)
        for d, vals in (filters or {}).items():
            if d not in FACT_DIMS:
                s &= self._split(d).get_any(vals)
        ffilt = {d: {str(v) for v in vals} for d, vals in (filters or {}).items() if d in FACT_DIMS}
        groups = {}
        for f, bm in self.facts:
            if all(str(self._fact_value(f, d)) in v for d, v in ffilt.items()):
                key = tuple(self._fact_value(f, d) for d in fdims)
                groups.setdefault(key, []).append(bm)
        cells = []
        for key, bms in groups.items():
            base = BitMap.union(*bms) & s
            if not base:
                continue
            for skey, sbm in self._split_all(sdims):
                n = len(base & sbm) if sdims else len(base)
                if n:
                    full = dict(zip(fdims, key)) | dict(zip(sdims, skey))
                    cells.append([*(full[d] for d in dims), n])
                    if len(cells) >= MAX_CELLS:
                        return {"dims": dims, "cells": cells, "truncated": True}
        return {"dims": dims, "cells": cells, "truncated": False}

    def _split(self, dim):
        if dim == "CLUSTER":
            return _Values({str(c["cid"]): c["apps"] for c in self.clusters} | {"none": self.all - self.changed})
        return _Values(self.seg.get(dim, {}))

    def _split_all(self, sdims):
        if not sdims:
            yield (), None
            return
        parts = [list(self._split(d).items()) for d in sdims]

        def rec(i, key, bm):
            if i == len(parts):
                yield tuple(key), bm
                return
            for v, b in parts[i]:
                nb = b if bm is None else bm & b
                if nb:
                    yield from rec(i + 1, key + [v], nb)

        yield from rec(0, [], None)

    def cell_apps(self, cell, filters, spec):
        """Bitmap of one pivot cell ``{dim: value}`` under ``filters`` and scope."""
        s = self.scope(spec)
        for d, vals in (filters or {}).items():
            if d not in FACT_DIMS:
                s &= self._split(d).get_any(vals)
        for d, v in cell.items():
            if d not in FACT_DIMS:
                s &= self._split(d).get_any([v])
        fcell = {d: str(v) for d, v in cell.items() if d in FACT_DIMS}
        ffilt = {d: {str(v) for v in vals} for d, vals in (filters or {}).items() if d in FACT_DIMS}
        bms = [bm for f, bm in self.facts if all(str(self._fact_value(f, d)) == v for d, v in fcell.items())
               and all(str(self._fact_value(f, d)) in v for d, v in ffilt.items())]
        return (BitMap.union(*bms) if bms else BitMap()) & s

    def app_list(self, bm, offset=0, limit=100):
        ids = list(bm)[offset:offset + limit]
        seg_fields = [f"{self.params.get('decision_field', 'DECISION')}_{x}" for x in ("BEFORE", "AFTER")]
        return {"total": len(bm), "apps": [{"app_id": self.app_id[i], "changed": i in self.changed,
                                            "decision_changed": i in self.flipped,
                                            "cluster": next((c["cid"] for c in self.clusters if i in c["apps"]), None),
                                            **{f: next((v for v, b in self.seg.get(f, {}).items() if i in b), None)
                                               for f in seg_fields}} for i in ids]}

    def app(self, app_id):
        """Horizontal BEFORE/AFTER view of one application (presentation only; storage stays vertical)."""
        i = self.app_ord.get(app_id)
        if i is None:
            return None
        with self.lock:
            rows = self.con.cursor().execute("""
                SELECT path_id, ARRAY_KEY,
                       max(VALUE) FILTER (WHERE SOURCE = 'BEFORE'), max(VALUE) FILTER (WHERE SOURCE = 'AFTER'),
                       max(ADJUSTED_VALUE) FILTER (WHERE SOURCE = 'BEFORE'),
                       max(ADJUSTED_VALUE) FILTER (WHERE SOURCE = 'AFTER'), max(CHANGE_TYPE), max(TYPE_CHANGED)
                FROM mart WHERE app_ord = ? GROUP BY path_id, ARRAY_KEY ORDER BY path_id, ARRAY_KEY""", [i]).fetchall()
            seg = self.con.cursor().execute("SELECT * EXCLUDE (app_ord) FROM segments WHERE app_ord = ?", [i])
            seg_row = dict(zip([d[0] for d in seg.description], seg.fetchone() or ()))
        return {"app_id": app_id, "segments": seg_row,
                "cluster": next((c["cid"] for c in self.clusters if i in c["apps"]), None),
                "rows": [{"path_id": pid, **{k: self.attrs[pid][k] for k in ("PATH", "PREFIX", "CLASS", "SUFFIX",
                                                                               "KEY")},
                          "ARRAY_KEY": ak, "VALUE_BEFORE": vb, "VALUE_AFTER": va, "ADJUSTED_BEFORE": ab,
                          "ADJUSTED_AFTER": aa, "CHANGE_TYPE": ct, "TYPE_CHANGED": bool(tc),
                          "input": pid in self.input_paths}
                         for pid, ak, vb, va, ab, aa, ct, tc in rows]}

    def transition_apps(self, path_id, before, after):
        return next((bm for vb, va, bm in self.transitions.get(path_id, []) if vb == before and va == after), BitMap())


class _Values(dict):
    def get_any(self, values):
        return BitMap.union(BitMap(), *(self.get(str(v), BitMap()) for v in values))


class ReportCache:
    """LRU of opened run reports (each holds one read-only DuckDB connection and its bitmaps)."""

    def __init__(self, size=16):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, run_id, path, params):
        with self.lock:
            r = self.items.pop(run_id, None)
            if r is None:
                r = RunReport(path, params)
            self.items[run_id] = r
            while len(self.items) > self.size:
                self.items.popitem(last=False)[1].con.close()
            return r
