import json
import random
from pathlib import Path

import pytest

from decision_diff.engine import chunk, finalize, formats, ingest, mart, procedures
from decision_diff.engine.compare import bucket_label
from decision_diff.engine.flatten import Paths, Trie, flatten

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "procedures"
TASK = ROOT / "agent-dump" / "inputs" / "task"
ORG = ROOT / "agent-dump" / "inputs" / "org"


def flat_pair(b, a, **profile):
    P, A = Paths(**profile), Trie()
    fb, _ = flatten(b, P, A)
    fa, _ = flatten(a, P, A)
    from decision_diff.engine.compare import compare

    return {(P.info(k[0])[1], A.akey(k[1])): v for k, v in compare(fb, fa).items()}, P


def test_keyed_array_permutation_has_no_false_differences():
    items = [{"id": i, "v": i * 10, "tags": [f"t{i}", f"u{i}"]} for i in range(20)]
    shuffled = random.Random(1).sample(items, len(items))
    res, _ = flat_pair({"x": items}, {"x": shuffled}, arrays={"x": ["id"], "x.tags": "@value"})
    assert {ct for ct, _ in res.values()} == {"SAME"}


def test_index_mode_and_four_change_types():
    b = {"s": {"value": [0.1, 0.2]}, "gone": 1, "same": "a", "chg": 1}
    a = {"s": {"value": [0.2, 0.1]}, "new": True, "same": "a", "chg": 2}
    res, _ = flat_pair(b, a, arrays={"s.value": "@index"})
    assert res["s.value", "0"][0] == "VALUE_CHANGED"
    assert res["gone", ""][0] == "ATTRIBUTE_LOST"
    assert res["new", ""][0] == "ATTRIBUTE_NEW"
    assert res["same", ""][0] == "SAME"


def test_contract_columns_and_scalar_array_key_is_empty():
    P, A = Paths(arrays={"company.rejectReasons": {"key": "@value", "element_name": "reason"},
                         "company.score": ["name"]}), Trie()
    out, _ = flatten({"company": {"rejectReasons": ["AGELOW@21"], "score": [{"name": "PD", "value": 0.4}],
                                  "limit": {"Limit": {"limit": 5}}}}, P, A)
    cols = {P.info(n)[1]: (P.info(n)[2:], A.akey(k)) for n, k in out}
    assert cols["company.rejectReasons.reason"] == (("company", "rejectReasons", "reason", ""), "AGELOW@21")
    assert cols["company.score.value"] == (("company", "score", "", "value"), "PD")
    assert cols["company.limit.Limit.limit"][0] == ("company", "limit", "Limit", "limit")


def test_duplicate_keys_are_reported_not_merged():
    P, A = Paths(arrays={"x": ["id"]}), Trie()
    out, issues = flatten({"x": [{"id": 1, "v": 1}, {"id": 1, "v": 2}]}, P, A)
    assert len(out) == 4 and any(i[0] == "duplicate_key" for i in issues)


def test_rename_and_key_style_with_collision():
    res, _ = flat_pair({"a": {"oldName": 1}}, {"a": {"new_name": 1}}, rename={"a.oldName": "new_name"})
    assert res["a.new_name", ""][0] == "SAME"
    P, A = Paths(key_style="snake"), Trie()
    out, issues = flatten({"modelName": 1, "model_name": 2, "cutOff": 3}, P, A)
    assert {P.info(n)[1] for n, _ in out} == {"model_name", "cut_off"}
    assert any(i[0] == "key_collision" for i in issues)


def test_depth_at_parser_limit_and_clear_error_beyond():
    def deep(n, leaf):
        return "[" * (n - 1) + "{\"v\":" + json.dumps(leaf) + "}" + "]" * (n - 1)

    b, a = formats.load_json(deep(1024, 1)), formats.load_json(deep(1024, 2))
    res, _ = flat_pair(b, a)
    assert [ct for ct, _ in res.values()] == ["VALUE_CHANGED"]
    with pytest.raises(formats.FormatError, match="1024"):
        formats.load_json(deep(1025, 1))


def test_xml_yaml_msgpack_depth_at_parser_limit_and_clear_error_beyond():
    def xml(n):
        return ("<a>" * n + "1" + "</a>" * n).encode()

    (_, tree), = formats.parse("x.xml", xml(2048))
    out, _ = flatten(tree, Paths(), Trie())
    assert len(out) == 1
    with pytest.raises(formats.FormatError, match="2048"):
        list(formats.parse("x.xml", xml(2049)))
    with pytest.raises(formats.FormatError, match="вложенность YAML"):
        list(formats.parse("x.yaml", b"[" * 5000 + b"]" * 5000))
    assert list(formats.parse("x.msgpack", b"\x81\xa1a" * 1024 + b"\x01"))
    with pytest.raises(formats.FormatError, match="1024"):
        list(formats.parse("x.msgpack", b"\x81\xa1a" * 1025 + b"\x01"))


def test_formats_land_in_the_same_tree():
    import msgpack
    import yaml

    doc = {"a": {"b": [{"k": "x", "v": "1"}, {"k": "y", "v": "2"}]}}
    as_json = list(formats.parse("I1.json", json.dumps(doc).encode()))
    as_yaml = list(formats.parse("I1.yaml", yaml.safe_dump(doc).encode()))
    as_mp = list(formats.parse("I1.msgpack", msgpack.packb(doc)))
    as_xml = list(formats.parse("I1.xml", b"<a><b><k>x</k><v>1</v></b><b><k>y</k><v>2</v></b></a>"))
    assert as_json == as_yaml == as_mp == as_xml == [("I1", doc)]
    rows = list(formats.parse("r.csv", b"APP;x\n1;a\n2;b\n", id_field="APP"))
    assert rows == [("1", {"APP": "1", "x": "a"}), ("2", {"APP": "2", "x": "b"})]
    with pytest.raises(formats.FormatError, match="pickle"):
        formats.detect("x.pkl", b"\x80\x04")


def test_xml_does_not_resolve_external_entities():
    xxe = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r><a>&x;</a></r>'
    (_, tree), = formats.parse("x.xml", xxe)
    assert "root:" not in json.dumps(tree)


def test_sample_is_reproducible_nested_and_sorted():
    items = [(f"I{i:03d}", None, None) for i in range(100)]
    small, big = ingest.sample(items, 10), ingest.sample(items, 40)
    assert small == ingest.sample(items, 10) and len(small) == 10 and small == sorted(small)
    assert set(small) < set(big) and small != items[:10]
    assert ingest.sample(items, None) == items and ingest.sample(items, 500) == items


def test_bucket_labels():
    br = (0, 0.3, 0.6, 1000)
    assert bucket_label(br, "0.42") == "(0.3 – 0.6]"
    assert bucket_label(br, "5000") == "(1K – +inf)"
    assert bucket_label(br, "REJECT") == "REJECT"


def _run(run_dir, tmp_path):
    n = procedures.load_file(PROC / "spr_normalize.py", "normalize")
    c = procedures.load_file(PROC / "spr_compare.py", "compare")
    s = procedures.load_file(PROC / "spr_segments.py", "segments")
    items = ingest.pairs(run_dir)
    dirs = []
    for k in range(0, len(items), 700):
        z = tmp_path / f"in{k}.zip"
        ingest.write_chunk_zip(z, items[k:k + 700])
        out = chunk.process_apps(ingest.read_chunk_zip(z.read_bytes()), n, c, s)
        dirs.append(chunk.write_chunk(tmp_path / f"c{k}", *out[:4]))
    stats = finalize.build(dirs, tmp_path / "run.duckdb", s["params"])
    return stats, mart.Mart(mart.open_run(tmp_path / "run.duckdb"))


def _expected(csv_path):
    import csv

    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    return (len(rows), sum(int(r["CHANGED"]) for r in rows), sum(int(r["DECISION_CHANGED"]) for r in rows))


@pytest.mark.parametrize("run_dir,reference", [
    (TASK / "data" / "RUN-20260827-0001", ORG / "ЭТАЛОН" / "expected_OPEN.csv"),
    (ORG / "HOLDOUT" / "RUN-20260827-0001-HOLDOUT", ORG / "ЭТАЛОН" / "expected_HOLDOUT.csv"),
])
def test_reference_numbers(run_dir, reference, tmp_path):
    if not run_dir.exists() or not reference.exists():
        pytest.skip("reference data is local-only and never committed")
    apps, changed, decision = _expected(reference)
    stats, M = _run(run_dir, tmp_path)
    dec = sum(1 for s in M.segments() if s["DECISION_CHANGED"])
    assert (stats["apps"], stats["changed_apps"], dec) == (apps, changed, decision)
    assert M.count(path="decisionResult.decision", diff_flag=1) == decision
    for f in sorted((TASK / "checks").glob("check_*.py")):
        chk = procedures.load_file(f, "check")
        assert procedures.validate_check_rows(chk["id"], chk["entrypoint"](M, chk["params"]))


def test_manifest_cannot_point_outside_the_run_folder(tmp_path):
    for bad in ("/etc", "../outside"):
        (tmp_path / "outside").mkdir(exist_ok=True)
        run = tmp_path / "run"
        (run / "RESULTS" / "AFTER").mkdir(parents=True, exist_ok=True)
        (run / "manifest.json").write_text(json.dumps({"srez": {"BEFORE": {"dir": bad}, "AFTER": {"dir": "RESULTS/AFTER"}}}))
        with pytest.raises(FileNotFoundError, match="внутри папки прогона"):
            ingest.slice_dirs(run)


def test_declaration_resolves_module_level_literal_params():
    code = 'DEFAULTS = {"min_share": 0.05}\n\ndef run(mart, params=None):\n    return []\n\n' \
           'CHECK = {"id": "c", "label": "c", "params": DEFAULTS, "entrypoint": run}\n'
    assert procedures.declaration(code)[1] == {"id": "c", "label": "c", "params": {"min_share": 0.05}}
