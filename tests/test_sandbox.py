import multiprocessing
import os
import socket
from concurrent.futures import ProcessPoolExecutor

import pytest

from decision_diff import sandbox

pytestmark = pytest.mark.skipif(not sandbox.landlock_abi(), reason="Landlock not available")


def run(*args, **kw):
    """Launch from a fresh single-threaded process, as the worker's launcher pool does."""
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as ex:
        return ex.submit(sandbox.run, *args, **kw).result()


def _read(path):
    return open(path).read()[:10]


def _connect():
    socket.create_connection(("1.1.1.1", 53), timeout=2)


def _env():
    return dict(os.environ)


def _spin():
    while True:
        pass


def _alloc():
    return len(bytearray(4 << 30))


def _value(x):
    return {"twice": x * 2}


def test_result_round_trip_as_json():
    assert run(_value, (21,)) == {"twice": 42}


def test_filesystem_outside_allowed_paths_is_denied(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("password")
    with pytest.raises(sandbox.SandboxError, match="PermissionError"):
        run(_read, (str(secret),))
    assert run(_read, (str(secret),), read=[str(secret)]) == "password"


def test_network_and_secrets_are_unavailable(monkeypatch):
    with pytest.raises(sandbox.SandboxError):
        run(_connect)
    monkeypatch.setenv("DD_PG_URL", "postgresql://secret")
    assert "DD_PG_URL" not in run(_env)


def test_timeout_and_memory_limits():
    with pytest.raises(sandbox.SandboxError, match="timeout"):
        run(_spin, wall_s=1)
    with pytest.raises(sandbox.SandboxError, match="MemoryError"):
        run(_alloc, mem_mb=512)


def _is_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def _environ_block():
    return open("/proc/self/environ", "rb").read()[:20].decode(errors="replace")


def _launcher_probe(path):
    with open(path, "w") as f:
        return sandbox.run(_is_open, (f.fileno(),)), _is_open(f.fileno())


def test_inherited_descriptors_and_environ_block_are_unreachable(tmp_path):
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as ex:
        in_child, in_launcher = ex.submit(_launcher_probe, str(tmp_path / "handle")).result()
    assert in_launcher and not in_child
    with pytest.raises(sandbox.SandboxError):
        run(_environ_block)
