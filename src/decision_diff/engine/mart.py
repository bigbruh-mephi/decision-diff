"""MartAPI for checks over a finalized DuckDB run file (contract: ``checks/README.md`` of the task).

Contract filters on PATH/KEY/prefix resolve against the small cached ``attrs`` dictionary in Python, so DuckDB only
sees integer IN-lists on the sort key (zonemap pruning); values are always bound parameters, never concatenated.
"""

import duckdb

FILTERS = {"path", "path_prefix", "key", "array_key", "diff_flag", "change_type", "value_before", "value_after",
           "app_ids"}


def _as_list(v):
    return list(v) if isinstance(v, (list, tuple, set, frozenset)) else [v]


def open_run(path, threads=4, memory_limit="1GB"):
    """Read-only DuckDB connection to a run file with external access disabled."""
    return duckdb.connect(str(path), read_only=True,
                          config={"threads": threads, "memory_limit": memory_limit, "enable_external_access": False,
                                  "autoinstall_known_extensions": False, "autoload_known_extensions": False})


class Mart:
    """MartAPI: ``rows``, ``count``, ``app_ids``, ``segments``, ``total_apps`` over one run connection."""

    def __init__(self, con):
        self.con = con
        self.attrs = {r[0]: dict(zip(("PATH", "PREFIX", "CLASS", "SUFFIX", "KEY"), r[1:])) for r in con.execute(
            "SELECT path_id, PATH, PREFIX, CLASS, SUFFIX, KEY FROM attrs").fetchall()}
        self.app_id = [r[0] for r in con.execute("SELECT APP_ID FROM apps ORDER BY app_ord").fetchall()]
        self.app_ord = {a: i for i, a in enumerate(self.app_id)}
        self._segments = None

    def _where(self, f):
        bad = set(f) - FILTERS
        if bad:
            raise TypeError(f"unknown mart filters: {sorted(bad)}")
        conds, params, ids = [], [], None
        for name, test in (("path", lambda a, v: a["PATH"] in v), ("key", lambda a, v: a["KEY"] in v),
                           ("path_prefix", lambda a, v: any(a["PATH"].startswith(p) for p in v))):
            if f.get(name) is not None:
                v = set(_as_list(f[name])) if name != "path_prefix" else _as_list(f[name])
                hit = {i for i, a in self.attrs.items() if test(a, v)}
                ids = hit if ids is None else ids & hit
        if ids is not None:
            conds.append("path_id IN (SELECT unnest(?::UINTEGER[]))")
            params.append(sorted(ids))
        if f.get("app_ids") is not None:
            conds.append("app_ord IN (SELECT unnest(?::UINTEGER[]))")
            params.append(sorted(self.app_ord[a] for a in _as_list(f["app_ids"]) if a in self.app_ord))
        for name, col in (("array_key", "ARRAY_KEY"), ("change_type", "CHANGE_TYPE")):
            if f.get(name) is not None:
                conds.append(f"{col} IN (SELECT unnest(?::VARCHAR[]))")
                params.append([str(x) for x in _as_list(f[name])])
        if f.get("diff_flag") is not None:
            conds.append("DIFF_FLAG = ?")
            params.append(int(f["diff_flag"]))
        having = []
        for name, col in (("value_before", "VALUE_BEFORE"), ("value_after", "VALUE_AFTER")):
            if f.get(name) is not None:
                having.append(f"{col} = ?")
                params.append(str(f[name]))
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        return where, ("HAVING " + " AND ".join(having)) if having else "", params

    def _pivot(self, f):
        where, having, params = self._where(f)
        return (f"""SELECT app_ord, path_id, ARRAY_KEY,
                    coalesce(max(VALUE) FILTER (WHERE SOURCE = 'BEFORE'), '') AS VALUE_BEFORE,
                    coalesce(max(VALUE) FILTER (WHERE SOURCE = 'AFTER'), '') AS VALUE_AFTER,
                    coalesce(max(ADJUSTED_VALUE) FILTER (WHERE SOURCE = 'BEFORE'), '') AS ADJUSTED_BEFORE,
                    coalesce(max(ADJUSTED_VALUE) FILTER (WHERE SOURCE = 'AFTER'), '') AS ADJUSTED_AFTER,
                    max(DIFF_FLAG) AS DF, max(CHANGE_TYPE) AS CT
                FROM mart {where} GROUP BY app_ord, path_id, ARRAY_KEY {having}"""), params

    def rows(self, **f):
        sql, params = self._pivot(f)
        cur = self.con.cursor().execute(sql, params)
        while batch := cur.fetchmany(10_000):
            for app, pid, ak, vb, va, ab, aa, df, ct in batch:
                a = self.attrs[pid]
                yield {"APP_ID": self.app_id[app], "PREFIX": a["PREFIX"], "CLASS": a["CLASS"], "SUFFIX": a["SUFFIX"],
                       "ARRAY_KEY": ak, "KEY": a["KEY"], "PATH": a["PATH"], "VALUE_BEFORE": vb, "VALUE_AFTER": va,
                       "ADJUSTED_BEFORE": ab, "ADJUSTED_AFTER": aa, "DIFF_FLAG": int(df), "CHANGE_TYPE": ct}

    def count(self, **f):
        sql, params = self._pivot(f)
        return self.con.cursor().execute(f"SELECT count(*) FROM ({sql})", params).fetchone()[0]

    def app_ids(self, **f):
        sql, params = self._pivot(f)
        rows = self.con.cursor().execute(f"SELECT DISTINCT app_ord FROM ({sql}) ORDER BY app_ord", params).fetchall()
        return [self.app_id[r[0]] for r in rows]

    def segments(self):
        if self._segments is None:
            cur = self.con.cursor().execute("SELECT * EXCLUDE (app_ord) FROM segments ORDER BY app_ord")
            cols = [d[0] for d in cur.description]
            self._segments = [dict(zip(cols, r)) for r in cur.fetchall()]
            for s in self._segments:
                if "DECISION_CHANGED" in s and s["DECISION_CHANGED"] is not None:
                    s["DECISION_CHANGED"] = int(s["DECISION_CHANGED"])
        return iter(self._segments)

    def total_apps(self):
        return len(self.app_id)
