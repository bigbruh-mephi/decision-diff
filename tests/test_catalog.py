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
