import asyncio
import os

from decision_diff import catalog

CHECK = 'CHECK = {"id": "%s", "label": "L", "params": {}}\n'


class _Cur:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class FakeConn:
    """Just enough of the procedures table for ``catalog.sync_files``."""

    def __init__(self):
        self.procs, self.calls = {}, 0

    async def execute(self, sql, args=()):
        self.calls += 1
        sql = " ".join(sql.split())
        if sql.startswith("UPDATE procedures"):
            sha, kind, _name, path = args
            if path in self.procs:
                self.procs[path]["sha"] = sha
                return _Cur({"id": self.procs[path]["id"]})
            return _Cur(None)
        if sql.startswith("INSERT INTO procedures"):
            _, kind, _name, path, sha = args
            self.procs[path] = {"id": path, "sha": sha, "kind": kind}
            return _Cur({"id": path})
        if sql.startswith("DELETE FROM procedures"):
            self.procs = {k: v for k, v in self.procs.items() if k in args[0]}
        return _Cur(None)

    async def commit(self):
        pass


def test_bad_file_is_skipped_vanished_file_is_removed_and_resync_only_on_change(tmp_path):
    c = FakeConn()
    (tmp_path / "a.py").write_text(CHECK % "a")
    (tmp_path / "b.py").write_text(CHECK % "b")
    (tmp_path / "bad.py").write_text("x = 1\n")
    asyncio.run(catalog.sync_changed(c, tmp_path))
    assert set(c.procs) == {"a.py", "b.py"}
    calls = c.calls
    asyncio.run(catalog.sync_changed(c, tmp_path))
    assert c.calls == calls
    (tmp_path / "b.py").unlink()
    (tmp_path / "c.py").write_text(CHECK % "c")
    asyncio.run(catalog.sync_changed(c, tmp_path))
    assert set(c.procs) == {"a.py", "c.py"}
    old = c.procs["a.py"]["sha"]
    (tmp_path / "a.py").write_text(CHECK % "a2")
    os.utime(tmp_path / "a.py", ns=(1, 1))
    asyncio.run(catalog.sync_changed(c, tmp_path))
    assert c.procs["a.py"]["sha"] != old


class KindConn:
    """``procedures`` rows by id for ``catalog.snapshot``."""

    def __init__(self, kinds):
        self.kinds = kinds

    async def execute(self, sql, args=()):
        if "SELECT sha, kind FROM procedures" in sql:
            kind = self.kinds.get(args[0])
            return _Cur(kind and {"sha": "s-" + args[0], "kind": kind})
        return _Cur(None)


def test_config_draft_validation_skips_missing_stages_but_rejects_wrong_procedures():
    import pytest

    from decision_diff.engine.procedures import ProcedureError

    c = KindConn({"s1": "step", "c1": "check", "n1": "normalize"})
    snap = asyncio.run(catalog.snapshot(c, {"steps": ["s1"], "checks": ["c1"]}, {}, "u", partial=True))
    assert [x["sha"] for x in snap["steps"]] == ["s-s1"] and "normalize" not in snap
    with pytest.raises(ProcedureError, match="обязательный"):
        asyncio.run(catalog.snapshot(c, {"steps": ["s1"], "checks": []}, {}, "u"))
    with pytest.raises(ProcedureError, match="step"):
        asyncio.run(catalog.snapshot(c, {"steps": ["n1"], "checks": []}, {}, "u", partial=True))
