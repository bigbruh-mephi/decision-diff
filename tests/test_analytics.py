import dataclasses
from pathlib import Path

import pytest

from decision_diff import pipeline
from decision_diff.engine import chunk, finalize, ingest, mart, procedures
from decision_diff.report import RunReport

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "procedures"
OPEN_RUN = ROOT / "agent-dump" / "inputs" / "task" / "data" / "RUN-20260827-0001"
LINEAGE = {"stages": [{"name": "input", "role": "input", "paths": ["inp.**"]},
                      {"name": "logic", "role": "logic", "paths": ["logic.**"]},
                      {"name": "outcome", "role": "outcome", "paths": ["out.**"]}],
           "decision_path": "out.decision"}
SEGMENTS = '''
def run(b, a, params):
    return {"DECISION_BEFORE": b["out"]["decision"], "DECISION_AFTER": a["out"]["decision"],
            "DECISION_CHANGED": int(b["out"]["decision"] != a["out"]["decision"])}
PROCEDURE = {"id": "s", "label": "s", "kind": "segments", "entrypoint": run,
             "params": {"decision_field": "DECISION", "positive_decision": "APPROVE"}}
'''
WHATIF = '''
import numpy as np

def evaluate(data, params):
    ok = data["pd"] >= data["cut"] + params["shift"]
    return np.where(np.bincount(data["unit_app"], weights=ok, minlength=data["n_apps"]) > 0, 0, 1).astype(np.uint8)

PROCEDURE = {"id": "t", "label": "t", "kind": "whatif", "unit_depth": 1,
             "inputs": {"pd": {"path": "logic.trials.pd", "type": "float"},
                        "cut": {"path": "logic.trials.cut", "type": "float"}},
             "outcome": {"field": "DECISION", "values": ["APPROVE", "REJECT"]},
             "params": {"slice": {"type": "enum", "values": ["AFTER", "BEFORE"], "default": "AFTER"},
                        "shift": {"type": "float", "min": -1, "max": 1, "default": 0.0}},
             "entrypoint": evaluate}
'''


def doc(score=600, band="A", cut=0.5, pd=0.6, decision="APPROVE", extra=None, first=False):
    d = {"inp": {"score": score}, "logic": {"band": band, "trials": [{"id": "p1", "pd": pd, "cut": cut}]},
         "out": {"decision": decision}}
    if extra:
        d["logic"]["extra"] = extra
    if first:
        d["aaa"] = 1
    return d


def synthetic(first=False):
    """6 input-driven flips, 4 logic flips + 1 with an optional attribute, 2 outcome-only flips, 3 unchanged."""
    after = [doc(score=500, pd=0.4, decision="REJECT")] * 6 + [doc(band="B", cut=0.7, decision="REJECT")] * 4 + \
            [doc(band="B", cut=0.7, decision="REJECT", extra=1)] + [doc(decision="REJECT")] * 2 + [doc()] * 3
    return [(f"A{i:02d}", doc(first=first), {**a, **({"aaa": 1} if first else {})}) for i, a in enumerate(after)]


def build(apps, out, report_params):
    n = procedures.load("PROCEDURE = {'id': 'n', 'label': 'n', 'kind': 'normalize', 'params': "
                        "{'arrays': {'logic.trials': ['id']}}}", kind="normalize")
    c = procedures.load("PROCEDURE = {'id': 'c', 'label': 'c', 'kind': 'compare'}", kind="compare")
    s = procedures.load(SEGMENTS, kind="segments")
    d = chunk.write_chunk(out.with_suffix(".chunk"), *chunk.process_apps(apps, n, c, s)[:4])
    finalize.build([d], out, report_params)
    return out


@pytest.fixture
def run(tmp_path):
    params = {"decision_field": "DECISION", "positive_decision": "APPROVE", "lineage": LINEAGE}
    return build(synthetic(), tmp_path / "run.duckdb", params), params


def test_clusters_by_cause_merge_optional_attributes_and_keep_signature_across_runs(run, tmp_path):
    path, params = run
    rep = RunReport(path, params)
    assert [len(c["apps"]) for c in rep.clusters] == [6, 5, 2]
    assert [rep.app_id[i] for i in rep.clusters[2]["apps"]] == ["A11", "A12"]
    rows = rep.cluster_rows({})
    assert [r["origin"] for r in rows] == ["input", "logic", "logic"]
    assert "out.decision" in rows[0]["paths"] and "logic.extra" in rows[1]["paths"]
    other = RunReport(build(synthetic(first=True), tmp_path / "other.duckdb", params), params)
    assert other.attrs != rep.attrs
    assert [c["signature"] for c in other.clusters] == [c["signature"] for c in rep.clusters]
    rep.con.close()
    other.con.close()


def test_lineage_param_substitution(run):
    path, _ = run
    con = mart.open_run(path)
    assert pipeline._with_lineage(con, {"stages": "@lineage", "k": 1}) == {"stages": LINEAGE["stages"], "k": 1}
    with pytest.raises(procedures.ProcedureError, match="lineage"):
        pipeline._with_lineage(con, {"missing": "@lineage"})
    con.close()


def test_whatif_prepare_evaluate_metrics(run, tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    from decision_diff import whatif

    path, params = run
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(pipeline.settings, sandbox="off"))
    decl = whatif.declaration(WHATIF)
    assert decl["params"]["shift"]["default"] == 0.0 and "entrypoint" not in decl
    meta = whatif.prepare(str(path), WHATIF, str(tmp_path / "cache"), {})
    rep = RunReport(path, params)
    assert (meta["slice"], meta["n_apps"], meta["n_units"]) == ("AFTER", 16, 16)
    res = whatif.evaluate(WHATIF, "t", meta, {"shift": -0.25})
    m = whatif.metrics(rep, res, {})
    assert m["fidelity"] == 0.875
    assert m["approval_rate"] == {"before": 1.0, "after": 0.1875, "whatif": 1.0} and m["moved"] == 13
    assert whatif.metrics(rep, res, {"apps": ["A00", "A13"]})["moved"] == 1
    assert len(m["moved_ids"]) == 13 and len(whatif.metrics(rep, res, {}, max_ids=2)["moved_ids"]) == 2
    for bad in ({"shift": 5}, {"shift": "x"}, {"nope": 1}):
        with pytest.raises(ValueError):
            whatif.evaluate(WHATIF, "t", meta, bad)
    with pytest.raises(ValueError):
        whatif.prepare(str(path), WHATIF, str(tmp_path / "cache"), {"slice": "../x"})
    rep.con.close()


def test_new_checks_validate_on_the_open_run(tmp_path):
    if not OPEN_RUN.exists():
        pytest.skip("task data is local-only")
    seg = procedures.load_file(PROC / "spr_segments.py", "segments")
    params = {**seg["params"], "lineage": procedures.load_file(PROC / "spr_lineage.py", "lineage")["params"]}
    n = procedures.load_file(PROC / "spr_normalize.py", "normalize")
    c = procedures.load_file(PROC / "spr_compare.py", "compare")
    z = tmp_path / "in.zip"
    ingest.write_chunk_zip(z, ingest.pairs(OPEN_RUN))
    d = chunk.write_chunk(tmp_path / "c0", *chunk.process_apps(ingest.read_chunk_zip(z.read_bytes()), n, c, seg)[:4])
    finalize.build([d], tmp_path / "run.duckdb", params)
    con = mart.open_run(tmp_path / "run.duckdb")
    m = mart.Mart(con)
    files = sorted((PROC / "checks").glob("*.py"))
    assert len(files) == 8
    for f in files:
        chk = procedures.load_file(f, "check")
        assert procedures.validate_check_rows(chk["id"], chk["entrypoint"](m, pipeline._with_lineage(con, chk["params"])))
    con.close()


def test_pivot_totals_are_distinct_apps(run):
    path, params = run
    rep = RunReport(path, params)
    res = rep.pivot(["PATH"], ["SOURCE"], {"DIFF_FLAG": ["1"]}, {})
    rows = {r[0]: r[1] for r in res["row_totals"]}
    cols = {c[0]: c[1] for c in res["col_totals"]}
    cells = {(c[0], c[1]): c[2] for c in res["cells"]}
    assert rows["out.decision"] == cells[("out.decision", "BEFORE")] == cells[("out.decision", "AFTER")] == 13
    assert rows["logic.extra"] == cells[("logic.extra", "AFTER")] == 1
    assert cols["BEFORE"] == 13 < sum(v for (_, src), v in cells.items() if src == "BEFORE")
    assert res["total"] == len(rep.changed) == 13
    rep.con.close()
