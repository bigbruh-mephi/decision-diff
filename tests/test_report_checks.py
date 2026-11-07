import dataclasses

from test_analytics import build, doc, synthetic

from decision_diff import pipeline, report
from decision_diff.engine import procedures

ALL_APPS = '''
DEFAULTS = {"app_ids_limit": 3}

def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    apps = sorted(mart.app_ids())
    return [{"LABEL": "all", "SOURCE": "AFTER", "ARGUMENT": "", "CNT": len(apps), "TOTAL": len(apps), "METRIC": 1.0,
             "APP_IDS": apps[:p["app_ids_limit"]], "RESULT": "FAIL", "CONFIDENCE": "HIGH"}]

CHECK = {"id": "all", "label": "all", "params": DEFAULTS, "entrypoint": run}
'''


def test_check_rows_keep_a_preview_and_drill_reaches_all_apps(tmp_path, monkeypatch):
    params = {"decision_field": "DECISION"}
    path = build(synthetic(), tmp_path / "run.duckdb", params)
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(pipeline.settings, sandbox="off"))
    sha = procedures.load(ALL_APPS)["sha"]
    res, = pipeline.run_checks(str(path), [{"sha": sha, "code": ALL_APPS, "name": "all.py", "params": {}}])
    assert res["error"] is None and res["rows"][0]["APP_IDS"] == ["A00", "A01", "A02"]
    rep = report.RunReport(path, params)
    assert len(rep.scope({"check": {"sha": sha, "row": 0}})) == res["rows"][0]["CNT"] == 16
    assert len(rep.scope({"check": {"sha": sha, "row": 1}})) == 0
    assert len(rep.scope({"check": {"sha": "../../etc", "row": 0}})) == 0
    stored = [{"check_id": "all", "check_sha": sha, "label": "all", "rows": res["rows"]}]
    assert [c["check_id"] for c in rep.app_checks("A15", stored)] == ["all"]
    rep.con.close()


def test_summary_reports_parse_issues_per_kind(tmp_path):
    params = {"decision_field": "DECISION"}
    apps = synthetic()
    dup = doc()
    dup["logic"]["trials"] = dup["logic"]["trials"] * 2
    apps[0] = (apps[0][0], apps[0][1], dup)
    rep = report.RunReport(build(apps, tmp_path / "run.duckdb", params), params)
    issues = rep.summary({})["issues"]
    assert [(i["kind"], i["apps"], i["examples"]) for i in issues] == [("duplicate_key", 1, ["A00"])]
    assert rep.summary({"apps": ["A01"]})["issues"] == []
    rep.con.close()
