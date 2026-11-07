"""Finalize: chunk Parquet outputs -> one immutable, sorted DuckDB run file with facts and bitmaps.

Reads exactly the listed chunk directories (never a glob: orphan attempts may exist), assigns run-wide ``path_id``
(by sorted jsonpath) and ``app_ord`` (by sorted APP_ID), sorts the vertical datamart by
``(path_id, ARRAY_KEY, app_ord, SOURCE)`` so DuckDB zonemaps act as the index, and builds pivot facts with portable
roaring bitmaps of app ordinals (exact distinct-app counts; CNT is never summed across facts).
"""

import fnmatch
import hashlib
import os
import re
from pathlib import Path

import duckdb
import orjson
import pyarrow as pa
from pyroaring import BitMap

FACT_DIMS = ("path_id", "ARRAY_KEY", "ADJUSTED_VALUE", "DIFF_FLAG", "CHANGE_TYPE", "SOURCE")
MERGE_JACCARD = 0.6


def glob_re(patterns):
    """One regex for dotted-path globs: ``*`` matches one segment, ``**`` any number of segments."""
    return re.compile("|".join(fnmatch.translate(p.replace("**", "\0").replace("*", "[!.]*").replace("\0", "*"))
                               for p in patterns) or "(?!)")


def stage_roles(paths, stages):
    """``{key: role}`` of the first lineage stage whose globs match each path of ``{key: PATH}`` (else ``other``)."""
    rx = [(s["role"], glob_re(s["paths"])) for s in stages]
    return {k: next((r for r, g in rx if g.match(p)), "other") for k, p in paths.items()}


def _list(dirs, name):
    return "[" + ",".join("'" + str(Path(d) / f"{name}.parquet").replace("'", "''") + "'" for d in dirs) + "]"


def build(chunk_dirs, out_path, report_params=None, facts_max_values=200, threads=4, memory_limit="2GB"):
    """Build ``out_path`` (written to ``.tmp`` then renamed atomically). Returns run statistics.

    ``report_params`` are the segments procedure params plus the run's ``lineage`` params (when declared); they are
    stored in ``run_meta`` so checks can resolve ``"@lineage"`` params from the run itself.
    """
    out = Path(out_path)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    report_params = report_params or {}
    multi = report_params.get("multi_valued", {})
    con = duckdb.connect(str(tmp), config={"threads": threads, "async_threads": 0, "memory_limit": memory_limit})
    try:
        m, p, s, i = (_list(chunk_dirs, n) for n in ("mart", "paths", "segments", "issues"))
        con.execute(f"""
            CREATE TABLE attrs AS SELECT (row_number() OVER (ORDER BY JSONPATH) - 1)::UINTEGER AS path_id, *
            FROM (SELECT DISTINCT JSONPATH, PATH, PREFIX, CLASS, SUFFIX, KEY FROM read_parquet({p}));
            CREATE TABLE apps AS SELECT (row_number() OVER (ORDER BY APP_ID) - 1)::UINTEGER AS app_ord, APP_ID
            FROM (SELECT DISTINCT APP_ID FROM read_parquet({s}));
            CREATE TABLE mart AS SELECT a.app_ord, t.path_id, m.ARRAY_KEY, m.SOURCE, m.VALUE, m.TYPE, m.ADJUSTED_VALUE,
                m.DIFF_FLAG::UTINYINT AS DIFF_FLAG, m.CHANGE_TYPE, m.TYPE_CHANGED::UTINYINT AS TYPE_CHANGED
            FROM read_parquet({m}) m JOIN attrs t USING (JSONPATH) JOIN apps a USING (APP_ID)
            ORDER BY t.path_id, m.ARRAY_KEY, a.app_ord, m.SOURCE;
            CREATE TABLE segments AS SELECT a.app_ord, s.* FROM read_parquet({s}, union_by_name=true) s
                JOIN apps a USING (APP_ID) ORDER BY a.app_ord;
            CREATE TABLE issues AS SELECT a.app_ord, i.* FROM read_parquet({i}, union_by_name=true) i
                JOIN apps a USING (APP_ID);
            CREATE VIEW datamart AS SELECT a.APP_ID, m.SOURCE, t.PREFIX, t.CLASS, t.SUFFIX, m.ARRAY_KEY, t.KEY, t.PATH,
                m.VALUE, m.ADJUSTED_VALUE, m.DIFF_FLAG, m.CHANGE_TYPE
            FROM mart m JOIN attrs t USING (path_id) JOIN apps a USING (app_ord);
        """)
        dup = con.execute("""SELECT count(*) FROM (SELECT 1 FROM mart GROUP BY app_ord, path_id, ARRAY_KEY, SOURCE
                             HAVING count(*) > 1)""").fetchone()[0]
        if dup:
            raise ValueError(f"{dup} duplicate datamart keys (APP_ID, PATH, ARRAY_KEY, SOURCE): array key rules are wrong")
        _facts(con, facts_max_values)
        _segment_bitmaps(con, multi)
        _structure(con)
        _clusters(con, (report_params.get("lineage") or {}).get("stages") or [])
        _transitions(con)
        stats = dict(zip(("apps", "mart_rows", "comparison_rows", "changed_apps"), con.execute("""
            SELECT (SELECT count(*) FROM apps), (SELECT count(*) FROM mart),
                   (SELECT count(*) FROM (SELECT DISTINCT app_ord, path_id, ARRAY_KEY FROM mart)),
                   (SELECT count(DISTINCT app_ord) FROM mart WHERE DIFF_FLAG = 1)""").fetchone()))
        stats["facts"] = con.execute("SELECT count(*) FROM facts").fetchone()[0]
        con.execute("CREATE TABLE run_meta AS SELECT * FROM (VALUES ('duckdb_version', ?), ('report_params', ?)) "
                    "t(key, value)", [duckdb.__version__, orjson.dumps(report_params).decode()])
        con.execute("CHECKPOINT")
    finally:
        con.close()
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, out)
    return stats


def _insert(con, table, names, rows):
    """Bulk insert ``rows`` whose last item is a list of app ordinals, adding CNT and a serialized bitmap."""
    cols = list(zip(*rows)) if rows else [()] * len(names)
    data = {n: list(c) for n, c in zip(names[:-2], cols[:-1])}
    data[names[-2]] = [len(x) for x in cols[-1]]
    data[names[-1]] = pa.array([BitMap(x).serialize() for x in cols[-1]], pa.binary())
    arrow = pa.table(data)  # noqa: F841
    con.execute(f"INSERT INTO {table} SELECT * FROM arrow")


def _facts(con, max_values):
    """Facts per (path, array key, adjusted value, diff flag, change type, source) with app bitmaps.

    An attribute with more than ``max_values`` distinct adjusted values (identifiers) collapses into one
    ``(N distinct)`` value per group so the pivot stays small; its detail stays reachable through the datamart.
    """
    rows = con.execute(f"""
        WITH card AS (SELECT path_id, count(DISTINCT ADJUSTED_VALUE) AS n FROM mart GROUP BY path_id)
        SELECT m.path_id, m.ARRAY_KEY,
               CASE WHEN c.n > {int(max_values)} THEN '(' || c.n || ' distinct)' ELSE m.ADJUSTED_VALUE END AS AV,
               m.DIFF_FLAG, m.CHANGE_TYPE, m.SOURCE, list(DISTINCT m.app_ord ORDER BY m.app_ord)
        FROM mart m JOIN card c USING (path_id)
        GROUP BY ALL""").fetchall()
    con.execute("""CREATE TABLE facts (fact_id UINTEGER, path_id UINTEGER, ARRAY_KEY VARCHAR, ADJUSTED_VALUE VARCHAR,
                   DIFF_FLAG UTINYINT, CHANGE_TYPE VARCHAR, SOURCE VARCHAR, CNT UINTEGER, APPS BLOB)""")
    _insert(con, "facts", ("fact_id", *FACT_DIMS, "CNT", "APPS"), [(n, *r) for n, r in enumerate(rows)])


def _segment_bitmaps(con, multi):
    cols = [c for (c,) in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'segments' "
        "AND column_name NOT IN ('app_ord', 'APP_ID')").fetchall()]
    con.execute("CREATE TABLE seg_bitmaps (FIELD VARCHAR, VALUE VARCHAR, CNT UINTEGER, APPS BLOB)")
    rows = []
    for c in cols:
        q = '"' + c.replace('"', '""') + '"'
        val = f"unnest(string_split(coalesce({q}, ''), ?))" if c in multi else f"coalesce({q}, 'NULL')"
        params = [multi[c]] if c in multi else []
        got = con.execute(f"SELECT v, list(DISTINCT app_ord ORDER BY app_ord) FROM "
                          f"(SELECT app_ord, {val} AS v FROM segments) WHERE v <> '' GROUP BY v", params).fetchall()
        rows += [(c, *r) for r in got]
    _insert(con, "seg_bitmaps", ("FIELD", "VALUE", "CNT", "APPS"), rows)


def _structure(con):
    """Structure changes per path: apps where the attribute appeared, disappeared or changed JSON type."""
    con.execute("""
        CREATE TABLE structure AS
        SELECT t.path_id, t.PATH, t.PREFIX,
               count(DISTINCT CASE WHEN m.CHANGE_TYPE = 'ATTRIBUTE_NEW' THEN m.app_ord END) AS apps_new,
               count(DISTINCT CASE WHEN m.CHANGE_TYPE = 'ATTRIBUTE_LOST' THEN m.app_ord END) AS apps_lost,
               count(DISTINCT CASE WHEN m.TYPE_CHANGED = 1 THEN m.app_ord END) AS apps_type_changed,
               count(DISTINCT CASE WHEN m.SOURCE = 'BEFORE' THEN m.app_ord END) AS apps_before,
               count(DISTINCT CASE WHEN m.SOURCE = 'AFTER' THEN m.app_ord END) AS apps_after
        FROM mart m JOIN attrs t USING (path_id)
        GROUP BY ALL
        HAVING apps_new + apps_lost + apps_type_changed > 0""")


def _clusters(con, stages):
    """Change clusters by cause fingerprint: the set of changed (PATH, CHANGE_TYPE) outside outcome-role stages.

    Outcomes are consequences and would split one cause by its effect, so the lineage's outcome stages are left out
    of the fingerprint (no lineage: every path counts). Distinct fingerprints are merged greedily, largest first, into
    the first leader with Jaccard similarity >= ``MERGE_JACCARD``; apps whose changes are all outcomes form their own
    cluster. ``paths`` is the union of the members' changed paths; ``signature`` hashes PATH strings of the leader
    fingerprint, so the same cause has the same signature in every run (``path_id`` is per run).
    """
    paths = dict(con.execute("SELECT path_id, PATH FROM attrs").fetchall())
    outcome = sorted(k for k, r in stage_roles(paths, stages).items() if r == "outcome")
    rows = con.execute("""
        WITH ch AS (SELECT DISTINCT app_ord, path_id, CHANGE_TYPE FROM mart WHERE DIFF_FLAG = 1),
        a AS (SELECT app_ord, list(path_id) AS pids,
                     list_sort(coalesce(list(path_id || ':' || CHANGE_TYPE)
                                        FILTER (WHERE NOT list_contains(?::UINTEGER[], path_id)), [])) AS sig
              FROM ch GROUP BY app_ord)
        SELECT sig, list(app_ord ORDER BY app_ord), list_sort(list_distinct(flatten(list(pids)))) FROM a
        GROUP BY sig ORDER BY count(*) DESC, sig""", [outcome]).fetchall()
    leaders = []
    for sig, apps, pids in rows:
        s = frozenset(sig)
        hit = next((c for c in leaders if s and c[0] and len(s & c[0]) / len(s | c[0]) >= MERGE_JACCARD), None)
        if hit:
            hit[1].extend(apps)
            hit[2].update(pids)
        else:
            leaders.append((s, list(apps), set(pids)))
    out = []
    for s, apps, pids in leaders:
        items = sorted(f"{paths[int(i)]}:{t}" for i, t in (x.split(":", 1) for x in s))
        out.append((sorted(pids), hashlib.sha256("\n".join(items).encode()).hexdigest()[:16], sorted(apps)))
    out.sort(key=lambda r: (-len(r[2]), r[1]))
    con.execute("CREATE TABLE clusters (cid UINTEGER, paths UINTEGER[], signature VARCHAR, CNT UINTEGER, APPS BLOB)")
    if out:
        _insert(con, "clusters", ("cid", "paths", "signature", "CNT", "APPS"), [(i + 1, *r) for i, r in enumerate(out)])


def _transitions(con, top=20):
    """Value transitions per path (BEFORE -> AFTER over changed keys), top ``top`` per path plus one ``(other)``."""
    rows = con.execute(f"""
        WITH k AS (
            SELECT app_ord, path_id,
                   coalesce(max(VALUE) FILTER (WHERE SOURCE = 'BEFORE'), '∅') AS vb,
                   coalesce(max(VALUE) FILTER (WHERE SOURCE = 'AFTER'), '∅') AS va
            FROM mart WHERE DIFF_FLAG = 1 GROUP BY app_ord, path_id, ARRAY_KEY),
        t AS (SELECT path_id, vb, va, list(DISTINCT app_ord ORDER BY app_ord) AS apps,
                     row_number() OVER (PARTITION BY path_id ORDER BY count(DISTINCT app_ord) DESC, vb, va) AS rk
              FROM k GROUP BY path_id, vb, va)
        SELECT path_id, vb, va, apps FROM t WHERE rk <= {int(top)}
        UNION ALL
        SELECT path_id, '(other)', '(other)', list(DISTINCT a ORDER BY a)
        FROM (SELECT path_id, unnest(apps) AS a FROM t WHERE rk > {int(top)}) GROUP BY path_id""").fetchall()
    con.execute("CREATE TABLE transitions (path_id UINTEGER, VALUE_BEFORE VARCHAR, VALUE_AFTER VARCHAR, CNT UINTEGER, "
                "APPS BLOB)")
    if rows:
        _insert(con, "transitions", ("path_id", "VALUE_BEFORE", "VALUE_AFTER", "CNT", "APPS"), rows)
