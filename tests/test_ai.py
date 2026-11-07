import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from decision_diff import ai
from decision_diff.engine import chunk, finalize, procedures
from decision_diff.report import RunReport

NORMALIZE = 'PROCEDURE = {"id": "n", "label": "n", "kind": "normalize", "params": {"arrays": {"rules": ["name"]}}}'
COMPARE = 'PROCEDURE = {"id": "c", "label": "c", "kind": "compare", "params": {"equivalent": []}}'
SEGMENTS = '''
def run(b, a, params):
    return {"DECISION_BEFORE": b["decision"], "DECISION_AFTER": a["decision"],
            "DECISION_CHANGED": int(b["decision"] != a["decision"]), "MODEL_BEFORE": b["model"]}
PROCEDURE = {"id": "s", "label": "s", "kind": "segments", "entrypoint": run,
             "params": {"decision_field": "DECISION", "pii_paths": ["applicant.email"]}}
'''
N = 40


def _doc(i, after):
    age, score = 18 + i % 10, 700 + i
    if after and i % 4 == 0:
        score -= 140
    hit = age < 21 and not (after and i % 5 == 0)
    email = f"{'User' if after and i % 4 == 0 else 'user'}{i}@mail.test"
    return {"applicant": {"age": age, "email": email}, "score": score, "model": f"M{i % 2}",
            "rules": [{"name": "AGE_MIN", "hit": hit, "text": "age below 21"}, {"name": "LIMIT", "hit": False}],
            "decision": "REJECT" if hit or score < 600 else "APPROVE"}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ai")
    n, c, s = (procedures.load(code, name, kind) for code, name, kind in
               ((NORMALIZE, "n.py", "normalize"), (COMPARE, "c.py", "compare"), (SEGMENTS, "s.py", "segments")))
    apps = [(f"APP-{i:04d}", _doc(i, False), _doc(i, True)) for i in range(N)]
    out = chunk.process_apps(apps, n, c, s)
    finalize.build([chunk.write_chunk(tmp / "c0", *out[:4])], tmp / "run.duckdb", s["params"])
    return str(tmp / "run.duckdb"), s["params"]


@pytest.fixture
def rep(run):
    r = RunReport(*run)
    ai.prepare(r)
    yield r
    r.con.close()


def _paths(rep):
    out = {}
    for pid, a in rep.attrs.items():
        out.setdefault(a["PATH"], []).append(pid)
    return out


def test_pack_is_generic_masked_and_within_budget(rep):
    mask = ai.Masker(rep.params["pii_paths"])
    pack = ai.build_pack(rep, [{"label": "chk", "rows": [{"ARGUMENT": "x", "CNT": 1, "TOTAL": 2, "RESULT": "FAIL",
                                                           "CONFIDENCE": "HIGH", "APP_IDS": ["APP-0001"]}]}], mask)
    dump = json.dumps(pack, default=str)
    assert set(pack) == set("HKDRSPCAM") and ai.tokens(pack) <= ai.PACK_TOKENS
    assert "APP-" not in dump and "mail.test" not in dump and mask.masked > 0
    assert pack["H"][0]["apps"] == N and pack["K"] and pack["A"]
    small = ai.fit(dict(pack), ai.tokens(pack) - 50)
    assert ai.tokens(small) <= ai.tokens(pack) - 50 and small["A"] == pack["A"] and "k" not in small["M"][0]
    with pytest.raises(ValueError, match="does not fit"):
        ai.fit(dict(pack), 50)


@pytest.mark.parametrize("expr", [
    "read_csv('/etc/passwd') IS NOT NULL",
    "(SELECT count(*) FROM mart) > 0",
    "getenv('HOME') = 'x'",
    "age IN (SELECT app_ord FROM apps)",
    "1) AS e; COPY (SELECT 1) TO 'leak.csv'; SELECT (1",
    "1) AS e; ATTACH 'x.db' AS x; SELECT (1",
    "1) AS e; PRAGMA database_list; SELECT (1",
    "pragma_version() IS NULL",
    "current_setting('enable_external_access') = 'false'",
    "1) AS e FROM read_text('/etc/passwd') WHERE (1",
    "1) AS e UNION SELECT (1",
    "list_contains(list_transform([age], y -> y), 1)",
    "app_ord > 1",
    "age > 1 -- comment",
])
def test_whitelist_refuses_attacks(rep, expr):
    spec = {"name": "a", "vars": {"age": {"path": "applicant.age", "side": "before", "type": "num"}}, "expr": expr}
    with pytest.raises(ai.SpecError):
        ai.compile_spec(rep.con, spec, _paths(rep))


def test_normal_aggregate_is_measured_exactly(rep):
    spec = {"name": "young_rule_stopped", "where": "age < 21 AND coalesce(n, 0) >= 1",
            "vars": {"age": {"path": "applicant.age", "side": "before", "type": "num"},
                     "n": {"path": "rules.hit", "elem": "AGE_MIN", "side": "after", "agg": "count"},
                     "hit": {"path": "rules.hit", "elem": "AGE_MIN", "side": "after", "agg": "max"}},
            "expr": "hit = 'false'"}
    x = ai.measure(rep.con, spec, _paths(rep))
    young = [i for i in range(N) if 18 + i % 10 < 21]
    assert (x["n_population"], x["n_true"]) == (len(young), sum(1 for i in young if i % 5 == 0))


def test_verifier_drops_wrong_numbers_and_false_relations():
    idx = {"K1": {"id": "K1", "apps": 5}, "X1": {"id": "X1", "name": "overlap", "expr": "a AND b", "n_true": 0,
                                                 "n_population": 10}}
    base = {"severity": "high", "claim_type": "single_item", "check_next": "look at `K1`"}
    findings = [
        {**base, "title": "ok", "evidence": ["K1"], "text": "{K1.apps} apps changed in `RULE_21`"},
        {**base, "title": "typed", "evidence": ["K1"], "text": "17 apps changed"},
        {**base, "title": "wrong field", "evidence": ["K1"], "text": "{K1.flips} apps flipped"},
        {**base, "title": "uncited", "evidence": ["X1"], "text": "{K1.apps} apps"},
        {**base, "title": "ghost", "evidence": ["K9"], "text": "x"},
        {**base, "title": "false relation", "claim_type": "relation", "expects": "all", "evidence": ["K1", "X1"],
         "text": "the cluster apps are exactly the overlap apps"},
        {**base, "title": "unmeasured relation", "claim_type": "relation", "expects": "some", "evidence": ["K1"],
         "text": "the cluster overlaps the model switch"},
        {**base, "title": "true relation", "claim_type": "relation", "expects": "none", "evidence": ["K1", "X1"],
         "text": "no app is in both sets ({X1.n_true})"},
    ]
    kept, rejected = ai.verify(findings, idx)
    assert [f["title"] for f in kept] == ["ok", "true relation"]
    assert kept[0]["text"] == "5 apps changed in `RULE_21`" and kept[0]["verified"]
    assert kept[1]["derived_attribute"] == "overlap: a AND b"
    assert {r["title"] for r in rejected} == {"typed", "wrong field", "uncited", "ghost", "false relation",
                                              "unmeasured relation"}


class FakeLLM:
    """Local OpenAI-compatible endpoint answering from a script of ``(status, content)`` pairs."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status, content = outer.script.pop(0)
                body = json.dumps({"choices": [{"message": {"content": content}}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def client(self):
        return ai.Client(self.url, "test-key", "fake-model", timeout_s=10, backoff_s=0)


def test_client_falls_back_retries_and_repairs():
    fake = FakeLLM([(400, ""), (500, ""), (200, "not json"), (200, '```json\n{"analysis": "a", "findings": []}\n```')])
    try:
        got, msgs = fake.client().chat([{"role": "user", "content": "hi"}], ai.SCHEMA2, "findings")
    finally:
        fake.srv.shutdown()
    assert got == {"analysis": "a", "findings": []} and msgs[-1]["role"] == "assistant"
    assert "json_schema" == fake.requests[0]["response_format"]["type"]
    assert [r["response_format"]["type"] for r in fake.requests[1:]] == ["json_object"] * 3


def test_opinion_end_to_end_against_fake_endpoint(run):
    spec = {"name": "shifted", "why": "w", "expr": "sa - sb = -140",
            "vars": {"sb": {"path": "score", "side": "before", "type": "num"},
                     "sa": {"path": "score", "side": "after", "type": "num"}}}
    call1 = {"analysis": "a", "hypotheses": [{"statement": "s", "evidence": ["D1"], "derived": [
        spec, {**spec, "name": "attack", "expr": "read_text('/etc/passwd') IS NULL"}]}]}
    call2 = {"analysis": "a", "findings": [
        {"title": "input shift", "severity": "high", "claim_type": "relation", "expects": "some",
         "evidence": ["X1"], "text": "score dropped by a constant in {X1.n_true} apps", "check_next": "the source"},
        {"title": "made up", "severity": "low", "claim_type": "single_item", "evidence": ["H1"],
         "text": "about 12 apps", "check_next": ""}]}
    fake = FakeLLM([(200, json.dumps(call1)), (200, json.dumps(call2))])
    try:
        res = ai.opinion(*run, client=fake.client())
    finally:
        fake.srv.shutdown()
    assert {"state", "model", "findings", "usage", "timings", "error"} <= set(res) and res["state"] == "done"
    assert res["measurements"][0]["n_true"] == N // 4 and "refused" in res["measurements"][1]
    (f,) = res["findings"]
    assert f["text"] == f"score dropped by a constant in {N // 4} apps" and f["verified"]
    assert set(f) == {"title", "severity", "text", "derived_attribute", "evidence", "verified", "check_next"}
    assert [r["title"] for r in res["rejected"]] == ["made up"]
    sent = json.dumps(fake.requests)
    assert "APP-" not in sent and "mail.test" not in sent and res["privacy"]["masked_values"] > 0
