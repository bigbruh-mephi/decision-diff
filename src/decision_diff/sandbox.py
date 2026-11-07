"""Per-execution sandbox for user procedures (x86_64 Linux).

A worker pool process (modules already imported) forks one child per execution. The child first closes every
inherited file descriptor except its result pipe (no database, broker or IPC handles reach user code), then
irreversibly confines itself — new session, optional uid drop (when the worker runs as root in its hardened container), rlimits,
no_new_privs, Landlock (read-only paths, one writable directory, no TCP, scoped signals/abstract sockets) and a
seccomp denylist — clears the environment (``/proc/self`` is not readable, so the initial environment block is unreachable too), runs the target and returns its result
as JSON through a pipe. The parent never unpickles anything from the child, enforces a wall-clock deadline and a
result size cap, and kills the whole process group on timeout.
"""

import ctypes
import ctypes.util
import errno
import os
import resource
import select
import signal
import sys
import time
import traceback

import orjson

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
NR_CREATE, NR_ADD, NR_RESTRICT = 444, 445, 446
PR_SET_NO_NEW_PRIVS = 38
M_ARENA_MAX = -8
FS_EXECUTE, FS_READ_FILE, FS_READ_DIR = 1 << 0, 1 << 2, 1 << 3
FS_READ = FS_READ_FILE | FS_READ_DIR
FS_ABI = {1: (1 << 13) - 1, 2: (1 << 14) - 1, 3: (1 << 15) - 1, 5: (1 << 16) - 1}
FILE_RIGHTS = (1 << 3) - 1 | (1 << 14) | (1 << 15)
DENY = ["socket", "connect", "bind", "listen", "accept", "accept4", "ptrace", "process_vm_readv",
        "process_vm_writev", "io_uring_setup", "io_uring_enter", "io_uring_register", "unshare", "setns", "mount",
        "umount2", "pivot_root", "chroot", "keyctl", "add_key", "request_key", "bpf", "perf_event_open",
        "userfaultfd", "personality", "kexec_load", "init_module", "finit_module", "memfd_secret", "mknod",
        "mknodat", "execve", "execveat"]


class SandboxError(RuntimeError):
    """User code failed, timed out, exceeded limits or returned an invalid result."""


class _Ruleset(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def landlock_abi():
    """Landlock ABI version of the running kernel, 0 if unavailable."""
    v = libc.syscall(NR_CREATE, None, 0, 1)
    return max(0, v)


def _landlock(read, write, abi):
    fs = FS_ABI[max(k for k in FS_ABI if k <= abi)]
    attr = _Ruleset(fs, 3 if abi >= 4 else 0, 3 if abi >= 6 else 0)
    size = ctypes.sizeof(_Ruleset) if abi >= 6 else (16 if abi >= 4 else 8)
    rfd = libc.syscall(NR_CREATE, ctypes.byref(attr), size, 0)
    if rfd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    try:
        for paths, allowed in ((read, FS_READ), (write, fs & ~FS_EXECUTE)):
            for p in paths:
                if not os.path.exists(p):
                    continue
                fd = os.open(p, os.O_PATH | os.O_CLOEXEC)
                try:
                    acc = allowed if os.path.isdir(p) else allowed & FILE_RIGHTS
                    if libc.syscall(NR_ADD, rfd, 1, ctypes.byref(_PathBeneath(acc & fs, fd)), 0) < 0:
                        raise OSError(ctypes.get_errno(), f"landlock_add_rule {p}")
                finally:
                    os.close(fd)
        if libc.syscall(NR_RESTRICT, rfd, 0) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(rfd)


def _libseccomp():
    try:
        ls = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2", use_errno=True)
    except OSError:
        return None
    ls.seccomp_init.restype = ctypes.c_void_p
    ls.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    ls.seccomp_load.argtypes = [ctypes.c_void_p]
    return ls


LIBSECCOMP = _libseccomp()


def _seccomp():
    ls = LIBSECCOMP
    if ls is None:
        raise OSError("libseccomp is not installed")
    ctx = ls.seccomp_init(0x7FFF0000)
    for name in DENY:
        nr = ls.seccomp_syscall_resolve_name(name.encode())
        if nr >= 0 and ls.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, nr, 0) != 0:
            raise OSError(f"seccomp_rule_add {name}")
    if ls.seccomp_load(ctx) != 0:
        raise OSError("seccomp_load")


def confine(read, write, uid=None, mem_mb=2048, cpu_s=300, nofile=128, nproc=64, fsize_mb=1024):
    """Irreversibly confine the calling single-threaded process; returns the Landlock ABI used.

    glibc malloc arenas are capped first: every arena of a multi-threaded library reserves 64 MB of address space that
    ``RLIMIT_AS`` counts (DuckDB's default 48 I/O threads took 3.5 GB before any query).
    """
    os.setsid()
    if uid is not None and os.getuid() == 0:
        os.setgroups([])
        os.setresgid(uid, uid, uid)
        os.setresuid(uid, uid, uid)
    libc.mallopt(M_ARENA_MAX, 2)
    for lim, val in ((resource.RLIMIT_AS, mem_mb << 20), (resource.RLIMIT_CPU, cpu_s),
                     (resource.RLIMIT_NOFILE, nofile), (resource.RLIMIT_NPROC, nproc),
                     (resource.RLIMIT_FSIZE, fsize_mb << 20), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(lim, (val, val))
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl")
    abi = landlock_abi()
    if abi:
        _landlock(read, write, abi)
    _seccomp()
    return abi


def _python_paths():
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.__file__)}
    paths |= {p for p in sys.path if p and os.path.isdir(p)}
    return sorted(paths) + ["/proc/self/cgroup", "/sys/devices/system/cpu", "/sys/fs/cgroup", "/etc/localtime", "/usr/share/zoneinfo",
                            "/dev/null", "/dev/urandom"]


def run(target, args=(), read=(), write=(), wall_s=300, mem_mb=4096, result_mb=64, uid=None, require=True):
    """Run ``target(*args)`` in a confined child and return its JSON-decoded result.

    ``read``/``write`` are the only paths the child may read / read and write (plus the Python installation).
    Raises :class:`SandboxError` with the child's traceback on failure, timeout or an oversized result.
    """
    if require and not (landlock_abi() and LIBSECCOMP):
        raise SandboxError("Landlock or libseccomp is not available; refusing to run user code")
    if threads() > 1:
        raise SandboxError("sandbox launcher process is multi-threaded; fork could deadlock the child")
    if uid is not None and os.getuid() == 0:
        for p in write:
            os.chown(p, uid, uid)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 0
        try:
            os.closerange(3, w)
            os.closerange(w + 1, resource.getrlimit(resource.RLIMIT_NOFILE)[0])
            confine(_python_paths() + list(read), list(write), uid=uid, mem_mb=mem_mb, cpu_s=wall_s + 5)
            os.environ.clear()
            payload = {"ok": True, "result": target(*args)}
        except BaseException as e:
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc(limit=-8)}
            code = 1
        try:
            data = orjson.dumps(payload, default=str)
        except TypeError as e:
            data, code = orjson.dumps({"ok": False, "error": f"result is not JSON-serializable: {e}"}), 1
        view = memoryview(data)
        while view:
            view = view[os.write(w, view):]
        os._exit(code)
    os.close(w)
    buf, deadline, limit = bytearray(), time.monotonic() + wall_s, result_mb << 20
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SandboxError(f"timeout after {wall_s} s")
            ready, _, _ = select.select([r], [], [], left)
            if not ready:
                continue
            chunk = os.read(r, 1 << 20)
            if not chunk:
                break
            buf += chunk
            if len(buf) > limit:
                raise SandboxError(f"result larger than {result_mb} MB")
    except SandboxError:
        _kill(pid)
        raise
    finally:
        os.close(r)
    _, status = os.waitpid(pid, 0)
    if not buf:
        sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
        raise SandboxError(f"sandbox died ({'signal ' + signal.Signals(sig).name if sig else 'no result'})")
    out = orjson.loads(buf)
    if not out["ok"]:
        raise SandboxError(out["error"] + ("\n" + out["traceback"] if out.get("traceback") else ""))
    return out["result"]


def threads():
    """Number of OS threads of this process (native threads included)."""
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) for line in f if line.startswith("Threads:"))


def _kill(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
