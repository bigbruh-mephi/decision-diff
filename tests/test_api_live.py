"""End-to-end API test against a running stack: ``DD_LIVE_URL=http://localhost:8088 uv run pytest tests/test_api_live.py``.

Needs a dataset folder visible in the new-run form (``DD_LIVE_DATASET``, default the first one) and a login
(``DD_LIVE_LOGIN`` / ``DD_LIVE_PASSWORD``); asserts internal consistency.
"""

import os
import time

import httpx
import pytest

BASE = os.environ.get("DD_LIVE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="DD_LIVE_URL is not set")


@pytest.fixture(scope="module")
def client():
    c = httpx.Client(base_url=BASE, timeout=60, headers={"Sec-Fetch-Site": "same-origin"})
    r = c.post("/api/auth/login", json={"login": os.environ.get("DD_LIVE_LOGIN", "analyst"),
                                        "password": os.environ.get("DD_LIVE_PASSWORD", "analyst-pass-123")})
    assert r.status_code == 200, r.text
    c.headers["Cookie"] = r.headers["set-cookie"].split(";", 1)[0]
    return c


@pytest.fixture(scope="module")
def run_id(client):
    datasets = client.get("/api/datasets").json()
    name = os.environ.get("DD_LIVE_DATASET") or datasets[0]["path"]
    by = {}
    for p in client.get("/api/procedures").json():
        by.setdefault(p["kind"], []).append(p)
    sel = {k: by[k][0]["id"] for k in ("normalize", "compare", "segments")}
    sel["lineage"] = by["lineage"][0]["id"] if by.get("lineage") else None
    sel["checks"] = [p["id"] for p in by.get("check", []) if p["origin"] == "file"]
    cfg = client.post("/api/configs", json={"name": "live test", "dataset": name, "procedures": sel}).json()
    rid = client.post("/api/runs", json={"config_id": cfg["id"]}).json()["id"]
    deadline = time.time() + 600
    while (st := client.get(f"/api/runs/{rid}").json())["state"] not in ("done", "failed", "cancelled"):
        assert time.time() < deadline, "run did not finish in 10 minutes"
        time.sleep(0.5)
    assert st["state"] == "done", st.get("error")
    yield rid
    client.delete(f"/api/configs/{cfg['id']}")


def test_report_is_consistent(client, run_id):
    s = client.get(f"/api/runs/{run_id}/summary").json()
    assert 0 < s["changed"] <= s["apps"] and s["decision_changed"] + s["changed_same_decision"] == s["changed"]
    assert sum(x["apps"] for x in s["swap"]) == s["apps"]
    etag = client.get(f"/api/runs/{run_id}/summary").headers["etag"]
    assert client.get(f"/api/runs/{run_id}/summary", headers={"If-None-Match": etag}).status_code == 304
    pv = client.post(f"/api/runs/{run_id}/pivot", json={"rows": ["PATH"], "cols": ["CHANGE_TYPE"],
                                                         "filters": {"DIFF_FLAG": ["1"]}}).json()
    assert pv["cells"]
    path, change = pv["cells"][0][0], pv["cells"][0][1]
    apps = client.post(f"/api/runs/{run_id}/apps", json={"cell": {"PATH": path, "CHANGE_TYPE": change},
                                                         "filters": {"DIFF_FLAG": ["1"]}}).json()
    assert apps["total"] > 0
    one = client.get(f"/api/runs/{run_id}/apps/{apps['apps'][0]['app_id']}").json()
    assert any(r["PATH"] == path and r["CHANGE_TYPE"] != "SAME" for r in one["rows"])
    assert all(c["error"] is None for c in client.get(f"/api/runs/{run_id}/checks").json())
    assert "<html" in client.get(f"/api/runs/{run_id}/export.html").text.lower()


def test_cell_runs_in_sandbox_without_secrets(client, run_id):
    cell = client.post("/api/cells", json={"run_id": run_id, "code": "import os\nprint(mart.total_apps())\n"
                                                                      "sorted(os.environ)"}).json()
    while (res := client.get(f"/api/cells/{cell['id']}").json())["state"] in ("queued", "running"):
        time.sleep(0.05)
    assert res["state"] == "done" and int(res["result"]["stdout"]) > 0
    assert not any("DD_" in str(v) for v in [res["result"]["value"]])


def test_share_link_is_read_only_and_revocable(client, run_id):
    token = client.post(f"/api/runs/{run_id}/share").json()["url"].rsplit("/", 1)[1]
    viewer = httpx.Client(base_url=BASE, headers={"Sec-Fetch-Site": "same-origin"})
    viewer.headers["Cookie"] = viewer.get(f"/s/{token}").headers["set-cookie"].split(";", 1)[0]
    assert viewer.get(f"/api/runs/{run_id}/summary").status_code == 200
    assert viewer.post("/api/cells", json={"code": "1"}).status_code == 403
    assert client.delete(f"/api/runs/{run_id}/share").status_code == 204
    assert viewer.get(f"/api/runs/{run_id}/summary").status_code == 401


def test_config_delete_is_owner_only(client, run_id):
    cfg = client.get("/api/configs").json()[0]
    mine = client.post("/api/configs", json={"name": "delete me", "dataset": cfg["dataset"],
                                             "procedures": cfg["procedure_ids"]}).json()
    token = client.post(f"/api/runs/{run_id}/share").json()["url"].rsplit("/", 1)[1]
    viewer = httpx.Client(base_url=BASE, headers={"Sec-Fetch-Site": "same-origin"})
    viewer.headers["Cookie"] = viewer.get(f"/s/{token}").headers["set-cookie"].split(";", 1)[0]
    assert viewer.delete(f"/api/configs/{mine['id']}").status_code == 403
    assert client.delete(f"/api/configs/{mine['id']}").status_code == 200
    assert mine["id"] not in {c["id"] for c in client.get("/api/configs").json()}
    client.delete(f"/api/runs/{run_id}/share")


def test_pivot_templates_crud_and_validation(client):
    layout = {"rows": ["PATH"], "cols": ["SOURCE"], "filters": {"DIFF_FLAG": ["1"]}, "mode": "row"}
    t = client.post("/api/pivot-templates", json={"name": "live tpl", "layout": layout, "shared": True}).json()
    assert t["layout"] == layout and t["mine"]
    assert client.put(f"/api/pivot-templates/{t['id']}", json={"name": "renamed", "layout": layout}).json()["name"] == "renamed"
    assert client.post("/api/pivot-templates", json={"name": "x", "layout": {"rows": ["A"], "cols": ["A"]}}).status_code == 422
    assert client.delete(f"/api/pivot-templates/{t['id']}").status_code == 204
    assert client.delete(f"/api/pivot-templates/{t['id']}").status_code == 404
