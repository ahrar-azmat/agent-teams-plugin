"""Shared 1.17.2 checkout exclusion protocol for exec and native custodians.

The supervisor owns one lease per filesystem checkout identity. Keep the legacy
bridges and mixed-version barrier in this single implementation.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import sys
import tempfile
import treedigest as _treedigest

LIVE_LOG_DIR = Path(os.environ.get("CODEX_ORACLE_LOG_DIR") or Path.home() / ".claude" / "logs" / "codex-oracle")
_IS_WINDOWS = os.name == "nt"

def _private(path: Path, mode: int) -> None:
    """Tighten permissions on a log/journal file or dir. These carry full
    prompts, model output and command output — other local users must not be
    able to read them (they were 0644 before). Never raises."""
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _proc_info_nt(pid: int) -> tuple[str, str]:
    """Windows: (state, start) from the process handle — creation time is the
    identity token, a zero-timeout wait is the liveness test (exit codes are
    ambiguous: STILL_ACTIVE is a legal exit code). Exact ctypes prototypes
    declared (HANDLE is pointer-sized; the int default would truncate).
    UNMEASURED on Windows; non-destructive by construction."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        h = k32.OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if not h:
            return "", ""
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            start = ""
            if k32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                start = f"win:{(ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime}"
            state = "R" if k32.WaitForSingleObject(h, 0) == 0x102 else "Z"  # WAIT_TIMEOUT = running
            return state, start
        finally:
            k32.CloseHandle(h)
    except Exception:
        return "", ""


def _proc_info(pid: int) -> tuple[str, str]:
    """(state, start) of a process: on POSIX via one `ps -o stat=,lstart=`
    call — state is the first letter of `stat` ("Z" = zombie: dead in every
    sense that matters, yet kill(0) and lstart still answer for it), start is
    the space-normalised `lstart` token; on Windows via the process handle.
    ("", "") when UNKNOWN (ps denied/absent, handle refused) — callers must
    treat unknown as alive for exclusion and as unkillable for destruction."""
    if not pid or pid <= 0:
        return "", ""
    if os.name == "nt":
        return _proc_info_nt(pid)
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["ps", "-o", "stat=,lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
        if out:
            return out[0][:1].upper(), "_".join(out[1:])
    return "", ""


def _proc_start(pid: int) -> str:
    """Immutable process identity: its start time (POSIX `ps -o lstart=`),
    space-normalised to one token. Empty when unknown (Windows, ps failure)
    — callers then fall back to kill(0) + command-line checks. Together
    with the pid this is what makes liveness and kill decisions PID-reuse
    safe (review of 1.17.0: signal 0 is a permission check, not identity)."""
    return _proc_info(pid)[1]


def _kill0(pid: int) -> bool | None:
    """True/False = exists/gone; None = exists but identity unknowable.
    NEVER os.kill(pid, 0) on Windows: Python routes non-control signals to
    TerminateProcess, so a "liveness probe" would kill the process (review
    round 2). There we query the handle instead (unmeasured on Windows, but
    non-destructive by construction)."""
    if os.name == "nt":
        stat, _ = _proc_info_nt(pid)
        return None if not stat else stat != "Z"
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return False


def _pid_alive(pid: int, start: str = "", strict: bool = False) -> bool:
    """Is this codex process still running? kill(0), then the recorded start
    time when we have one (a reused pid fails it), else the command line.
    ``strict``: only a VERIFIED identity match counts — unknown is False.
    Used where a wrong "alive" would AUTHORISE something (detaching an
    unbounded child on the strength of its watchdog)."""
    if not pid or pid <= 0:
        return False
    k = _kill0(pid)
    if k is False:
        return False
    if k is None:
        return not strict  # unknowable: alive for exclusion, never for authorisation
    stat, cur = _proc_info(pid)
    if not stat and not cur:
        return not strict
    if stat == "Z":
        return False  # a zombie is not running
    if strict:
        return bool(start) and bool(cur) and cur == start
    if start and cur:
        return cur == start
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout
            return "codex" in out.lower()
    return True


def _server_alive(pid: int, start: str = "") -> bool:
    """Is the codex-oracle server process that owns a run still alive? Our
    own pid is alive by definition; a recorded start time settles identity;
    otherwise the command line must still look like this server (macOS
    `ps` reports the python framework binary for a venv python, so the
    command-line test alone misjudged a live holder — measured)."""
    if not pid or pid <= 0:
        return False
    if pid == os.getpid():
        # Our own pid — but a recorded start that is not OURS means the
        # record belongs to a previous incarnation of this pid.
        return (not start) or (not _SERVER_START) or start == _SERVER_START
    k = _kill0(pid)
    if k is False:
        return False
    if k is None:
        return True
    stat, cur = _proc_info(pid)
    if not stat and not cur:
        return True  # UNKNOWN evidence (ps denied/absent) never reads as dead
    if stat == "Z":
        return False
    if start and cur:
        return cur == start
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            if not out.strip():
                return True  # no evidence either way
            return "server.py" in out or "codex-oracle" in out or "run_server" in out
    return True


_SERVER_START = _proc_start(os.getpid())

def _tree_identity(cwd: str) -> str:
    """Filesystem identity of the WORKTREE containing ``cwd``: the git
    toplevel's (st_dev, st_ino). Case variants, symlink aliases and any
    subdirectory of one checkout all map to one lock (review round 5:
    realpath() is not an identity on case-insensitive APFS, and a
    subdirectory got its own lock). Falls back to the realpath hash."""
    root = cwd
    with contextlib.suppress(Exception):
        top = subprocess.run(
            _treedigest.git_argv(cwd, ("rev-parse", "--show-toplevel")), env=_treedigest.git_env(),
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if top:
            root = top
    try:
        st = os.stat(root)
        return f"{st.st_dev}-{st.st_ino}"
    except OSError:
        return hashlib.sha1(os.path.realpath(root).encode("utf-8", "replace")).hexdigest()[:16]


def _write_lock_path(cwd: str, identity=None) -> Path:
    # The coordinator already has a verified canonical worktree identity.
    # Ordinary callers still resolve arbitrary aliases through Git.
    return LIVE_LOG_DIR / "write-locks" / f"tree-{identity or _tree_identity(cwd)}.lock"


def _legacy_write_lock_holder(cwd: str) -> str:
    """A pre-1.17.2 server holds no kernel lock — its lock is a content FILE
    keyed by the sha1 of the RAW cwd string, so a symlink / case / subdir
    alias of one checkout lives under a DIFFERENT file name (round 6: the
    exact-path probe missed those). Scan every legacy-format file in the
    shared directory and match by the TREE its recorded cwd resolves to; a
    live holder on our tree refuses us. A live holder whose cwd cannot be
    parsed fails CLOSED (counts as ours)."""
    my_tree = _tree_identity(cwd)
    tree_cache: dict[str, str] = {}
    try:
        entries = list((LIVE_LOG_DIR / "write-locks").iterdir())
    except OSError:
        return ""
    for entry in entries:
        name = entry.name
        if not name.endswith(".lock") or name.startswith("tree-"):
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        live = ""
        for key, skey, alive in (("child", "cstart", _pid_alive), ("pid", "pstart", _server_alive)):
            mp = re.search(rf"\b{key}=(\d+)\b", text)
            ms = re.search(rf"\b{skey}=(\S+)", text)
            if mp and alive(int(mp.group(1)), ms.group(1) if ms and ms.group(1) != "-" else ""):
                live = text
                break
        if not live:
            continue
        mc = re.search(r"\bcwd=(.+?) t=\d+", text) or re.search(r"\bcwd=(\S+)", text)
        if mc:
            rec_cwd = mc.group(1)
            tree = tree_cache.get(rec_cwd)
            if tree is None:
                tree = tree_cache[rec_cwd] = _tree_identity(rec_cwd)
            if tree != my_tree:
                continue  # a live legacy writer, but on a DIFFERENT tree
        return live  # our tree (or unprovably not ours — fail closed)
    return ""


# ---- kernel-held locks -----------------------------------------------------
# Mutual exclusion across processes is an ADVISORY OS LOCK held on an open
# descriptor (flock on POSIX, msvcrt.locking on Windows): exclusive while the
# holder — or a child that inherited the descriptor — lives, and released by
# the kernel on death. No pid, age, nonce or rename heuristics: three review
# rounds showed every file-content protocol had a race (a live owner age-
# expired; two recoverers of one stale file both acquiring; a fresh lock
# renamed away). Lock files are never unlinked — an unlink races a fresh
# opener onto a different inode and silently gives two holders.
_HELD: dict[str, int] = {}  # lock path → descriptor this process holds
PLUGIN_LOCK_PROTOCOL = "1.17.2"  # stamped into the server registry (write barrier)


def _try_lock_fd(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _acquire_os_lock(path: Path, payload: str) -> tuple[bool, str]:
    """Take the lock at ``path`` for this process. Returns (acquired, holder
    description when refused). The payload is diagnostics only — who holds
    it — never a liveness input."""
    try:
        from .client import private_parents
        private_parents(path.parent)
        _private(path.parent, 0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        return False, f"lock dir unusable ({e})"
    if not _try_lock_fd(fd):
        holder = ""
        with contextlib.suppress(OSError):
            holder = os.read(fd, 4096).decode("utf-8", errors="replace").strip()
        os.close(fd)
        return False, holder or "held by another process"
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    _HELD[str(path)] = fd
    return True, ""


def _release_os_lock(path: Path) -> None:
    """Close our descriptor. The kernel drops the lock when the LAST
    descriptor on the open file description closes — so a child that
    inherited it (a write run's codex) keeps the tree locked until it exits.
    No explicit unlock: that would strip the child's protection."""
    fd = _HELD.pop(str(path), None)
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)


def _held_lock_fd(path: Path) -> int | None:
    return _HELD.get(str(path))


def _rewrite_held_payload(path: Path, payload: str) -> bool:
    fd = _HELD.get(str(path))
    if fd is None:
        return False
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
        return True
    return False


_PLANTED_LEGACY: dict[str, list[Path]] = {}  # tree lock path → legacy bridge files we planted


def _server_registry_dir() -> Path:
    return LIVE_LOG_DIR / "servers"


def _register_server() -> None:
    """Record this (1.17.2+) server process so the mixed-version write
    barrier can tell registered new-code servers from pre-1.17.2 ones.
    Best-effort: a missed registration only OVER-blocks writes (fail closed).
    Called at real-server startup (__main__)."""
    try:
        d = _server_registry_dir()
        from .client import private_parents
        private_parents(d)
        _private(d, 0o700)
        p = d / f"{os.getpid()}.json"
        p.write_text(json.dumps({"pid": os.getpid(), "start": _SERVER_START,
                                 "version": PLUGIN_LOCK_PROTOCOL}), encoding="utf-8")
        _private(p, 0o600)
        for e in d.glob("*.json"):  # opportunistic prune of dead entries
            with contextlib.suppress(Exception):
                rec = json.loads(e.read_text(encoding="utf-8"))
                pid = int(rec.get("pid") or 0)
                if pid != os.getpid() and not _pid_alive(pid, str(rec.get("start") or "")):
                    e.unlink()
    except Exception:
        pass


def _registered_server_pids() -> dict[int, str]:
    out: dict[int, str] = {}
    with contextlib.suppress(OSError):
        for e in _server_registry_dir().glob("*.json"):
            with contextlib.suppress(Exception):
                rec = json.loads(e.read_text(encoding="utf-8"))
                out[int(rec.get("pid") or 0)] = str(rec.get("start") or "")
    return out


def _ps_snapshot() -> str:
    """`pid command` lines for this user's processes (tests inject their own).
    A nonzero ps exit is an ERROR, never an empty-but-successful snapshot
    (round 8: discarding returncode turned a failed ps into "no processes")."""
    done = subprocess.run(
        ["ps", "-U", str(os.getuid()), "-axo", "pid=,command="],
        capture_output=True, text=True, timeout=10,
    )
    if done.returncode != 0:
        raise OSError(f"ps exited {done.returncode}: {done.stderr.strip()[:120]}")
    return done.stdout


def _proc_comm(pid: int) -> str:
    """The process's EXECUTABLE (ps comm — a single field, so a spaced path
    cannot be mis-split). "" = the process is gone. Raises on a ps failure so
    the caller fails closed. Injectable for tests."""
    done = subprocess.run(
        ["ps", "-o", "comm=", "-p", str(pid)],
        capture_output=True, text=True, timeout=5,
    )
    if done.returncode != 0:
        # ps exits nonzero BOTH for a dead pid and for its own failures
        # (round 12): "gone" needs corroboration — kill(0) ESRCH. Anything
        # else is an unverifiable candidate and must raise so the barrier
        # fails closed.
        if _kill0(pid) is False:
            return ""  # genuinely gone
        raise OSError(
            f"ps -o comm= -p {pid} exited {done.returncode}"
            f" ({done.stderr.strip()[:120] or 'no stderr'}) while kill(0) says the "
            f"process exists")
    return done.stdout.strip()


def _scratch_namespace(cwd):
    """Explicit log isolation is allowed only with a common private temp root.

    Ordinary checkouts always retain the global old-server barrier. A disposable
    namespace must never consult or create the user's normal log registry.
    """
    if not cwd or not os.environ.get("CODEX_ORACLE_LOG_DIR"):
        return False
    try:
        common = Path(os.path.commonpath([Path(cwd).resolve(), LIVE_LOG_DIR.resolve()]))
        roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
        return any(root in common.parents for root in roots) and common.stat().st_uid == os.getuid() and not common.stat().st_mode & 0o077
    except (OSError, ValueError):
        return False


def _same_log_namespace(pid):
    """Read only the namespace field; never return or persist environment data."""
    import procenv
    before = _proc_start(pid)
    try:
        if sys.platform == "darwin":
            values = procenv._darwin_env(pid)
        else:
            fd = os.open("/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY)
            try:
                values = procenv._read_at(fd, "environ", procenv.ENVIRON_MAX_BYTES).split(b"\0")
            finally:
                os.close(fd)
        if values is None or not before or _proc_start(pid) != before:
            return None
        prefix = b"CODEX_ORACLE_LOG_DIR="
        configured = next((os.fsdecode(v[len(prefix):]) for v in values if v.startswith(prefix)), None)
        return configured is not None and os.path.realpath(configured) == os.path.realpath(LIVE_LOG_DIR)
    except OSError:
        return None


def _pre_1172_server_running(cwd=None) -> str:
    """Description of a live codex-oracle server process that predates the
    registry (pre-1.17.2), or ''. Round 7: old code cannot be patched inside
    running processes, and NO enumeration of path aliases can make its
    content locks meet our inode locks — so while such a process exists,
    write dispatch refuses outright. This is the mixed-version guarantee;
    the legacy scan and planted bridge files remain as defense in depth.
    POSIX only (write mode already fails closed on Windows)."""
    if _IS_WINDOWS:
        return ""
    try:
        out = _ps_snapshot()
    except Exception as e:
        return f"cannot enumerate processes (ps failed: {e})"
    registered = _registered_server_pids()
    rows: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_s, cmd = line.split(None, 1)
            rows.append((int(pid_s), cmd))
        except ValueError:
            continue
    if os.getpid() not in {p for p, _ in rows}:
        # A truthful snapshot of this user's processes must contain THIS
        # process; one that does not is empty, partial, or from the wrong
        # user — never proof that no old server exists (round 8).
        return "process snapshot did not include this process (partial/invalid) — cannot rule out an old server"
    for pid, cmd in rows:
        if pid == os.getpid():
            continue
        # CANDIDATE = loose substring match (round 11: token positions
        # guessed from a space-joined `ps command` line mis-split a spaced
        # install path into a false NEGATIVE — the superset match cannot
        # miss). What separates a real oracle server from a `codex exec`
        # child whose PROMPT mentions these strings is the EXECUTABLE:
        # servers run under a python; codex children run under codex/node.
        # `ps comm` is a single field, immune to spaces.
        low = cmd.lower()
        if "run_server.py" not in low and "server.py" not in low:
            continue
        if not any(name in low for name in ("codex-oracle", "software-workflows", "agent-teams")):
            continue
        try:
            comm = _proc_comm(pid)
        except Exception as e:
            return f"cannot verify candidate process {pid} (ps failed: {e})"
        if not comm:
            continue  # gone between snapshot and probe
        exe = comm.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not exe.startswith("python"):
            continue  # codex/node/etc — an oracle CHILD, not a server
        if _scratch_namespace(cwd) and _same_log_namespace(pid) is False:
            continue
        start = registered.get(pid)
        if start is not None:
            cur = _proc_start(pid)
            if cur and cur == start:
                continue  # verified registered 1.17.2+ server
            # Unknown or mismatched identity: the registry entry may belong
            # to a dead server whose pid was reused — NOT an exemption
            # (round 8: unknowable identity must fail closed here).
        if _kill0(pid) is False:
            continue  # dead — a stale snapshot line
        # RAW liveness only: _pid_alive's no-start fallback requires the
        # command to LOOK like a codex process, but here the snapshot line
        # is the identity evidence (measured: a live process read as dead).
        return f"unregistered (pre-1.17.2) codex-oracle server pid {pid}: {cmd[:120]}"
    return ""


def _legacy_lock_paths_for(cwd: str) -> list[Path]:
    """The legacy-format lock files a pre-1.17.2 server would consult for
    this tree: one per plausible alias of the checkout (raw cwd, realpath,
    git toplevel, its realpath), deduplicated by file name. An old server on
    an alias OUTSIDE this set is not excluded — that hole exists between two
    old servers as well and closes when every server is on 1.17.2."""
    aliases = [cwd, os.path.realpath(cwd)]
    with contextlib.suppress(Exception):
        top = subprocess.run(
            _treedigest.git_argv(cwd, ("rev-parse", "--show-toplevel")), env=_treedigest.git_env(),
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if top:
            aliases += [top, os.path.realpath(top)]
    out: list[Path] = []
    seen: set[str] = set()
    for alias in aliases:
        path = (LIVE_LOG_DIR / "write-locks"
                / f"{hashlib.sha1(alias.encode('utf-8', 'replace')).hexdigest()[:16]}.lock")
        if path.name in seen:
            continue
        seen.add(path.name)
        out.append(path)
    return out


def _plant_legacy_locks(cwd: str, run_hint: str) -> tuple[bool, str, list[Path]]:
    """Occupy the LEGACY lock namespace for this tree while we hold the inode
    lock, so a still-running pre-1.17.2 server refuses in BOTH acquisition
    orders (round 6: it otherwise takes its content lock after we already
    hold the — to it invisible — inode lock). Legacy files are content-based
    by the OLD protocol's own definition: creating and unlinking them here
    follows that protocol; the never-unlink law protects flock-held files,
    not these. Returns (ok, live_holder_when_refused, planted_so_far)."""
    planted: list[Path] = []
    payload = (f"{run_hint} pid={os.getpid()} pstart={_SERVER_START or '-'} "
               f"planted-by={os.getpid()} v=1.17.2-bridge cwd={cwd} "
               f"t={int(time.time())}\n")
    for path in _legacy_lock_paths_for(cwd):
        occupied = False
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(payload)
                planted.append(path)
                occupied = True
                break
            except FileExistsError:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue  # released between EXISTS and read — retry once
                m = re.search(r"\bpid=(\d+)\b", text)
                if (m and int(m.group(1)) != os.getpid()
                        and _pid_alive(int(m.group(1)))):
                    return False, text or "unknown legacy holder", planted
                with contextlib.suppress(OSError):
                    path.unlink()  # stale by the old protocol's own rules
                continue
            except OSError as e:
                # Round 7: an unplantable alias is a REFUSAL, not a skip — a
                # partially occupied namespace is fail-open.
                return False, f"cannot plant legacy bridge {path.name} ({e})", planted
        if not occupied:
            return False, f"could not occupy legacy bridge {path.name} (contention)", planted
    return True, "", planted


def _acquire_write_lock(cwd: str, run_hint: str) -> tuple[bool, str]:
    """One-writer-per-tree MUTUAL EXCLUSION, across server processes AND
    across plugin versions, held by the kernel for the holder's lifetime and
    inherited by the codex child. Stable order for every new writer:
    (1) refuse while any pre-1.17.2 content lock on this TREE names a live
    holder; (2) take the authoritative inode lock; (3) occupy the legacy
    namespace so an old writer starting LATER refuses too (round 6).
    Unusable lock dir FAILS CLOSED — write dispatch without mutual exclusion
    is not an acceptable fallback. Returns (acquired, holder_when_refused)."""
    blocker = _pre_1172_server_running(cwd)
    if blocker:
        return False, (
            f"mixed-version write barrier: {blocker} — old and new write "
            "locks cannot exclude each other through unanticipated path "
            "aliases (round 7); reconnect/reload that session onto 1.17.2 "
            "first, then retry"
        )
    legacy = _legacy_write_lock_holder(cwd)
    if legacy:
        return False, f"legacy (pre-1.17.2) lock live: {legacy}"
    payload = (f"{run_hint} pid={os.getpid()} pstart={_SERVER_START or '-'} "
               f"cwd={cwd} t={int(time.time())}\n")
    path = _write_lock_path(cwd)
    ok, holder = _acquire_os_lock(path, payload)
    if not ok:
        if holder.startswith("lock dir unusable"):
            return False, f"write-lock {holder} — refusing to write unlocked"
        return False, holder
    planted_ok, live_holder, planted = _plant_legacy_locks(cwd, run_hint)
    if not planted_ok:
        for p in planted:
            with contextlib.suppress(OSError):
                p.unlink()
        _release_os_lock(path)
        return False, f"legacy (pre-1.17.2) lock live: {live_holder}"
    _PLANTED_LEGACY[str(path)] = planted
    return True, ""


def _note_write_child(cwd: str, pid: int) -> bool:
    """Record the spawned codex child in the held lock's payload (who holds
    the tree — diagnostics). False only if this process does not hold the
    lock, which a write run must treat as fatal."""
    path = _write_lock_path(cwd)
    fd = _HELD.get(str(path))
    if fd is None:
        return False
    with contextlib.suppress(OSError):
        os.lseek(fd, 0, os.SEEK_SET)
        text = os.read(fd, 4096).decode("utf-8", errors="replace").rstrip("\n")
        # REPLACE the child identity (round 19): abraham publishes phase 1's
        # child and then phase 2's under the same held lock — skipping when
        # `child=` exists left the bridge naming a finished phase-1 pid.
        text = re.sub(r"\s*child=\d+ cstart=\S+", "", text)
        _rewrite_held_payload(path, f"{text} child={pid} cstart={_proc_start(pid) or '-'}\n")
    # Re-point our planted LEGACY files at the child: if this server crashes,
    # the child (holding the inherited inode lock) keeps the tree locked, and
    # the legacy files must keep naming a LIVE pid so an old server's
    # liveness probe still refuses instead of breaking a "stale" file.
    # ALL-OR-NOTHING and durable (round 7): one un-repointed file is an
    # unprotected crash window, so any failure returns False and the caller
    # kills the child (which has not executed yet — see the execution
    # barrier in _exec_codex_once).
    cstart = _proc_start(pid) or "-"
    for p in _PLANTED_LEGACY.get(str(path), []):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            if f"planted-by={os.getpid()}" not in txt:
                return False  # the bridge file is not ours any more
            tail = txt[txt.index("cwd="):] if "cwd=" in txt else txt
            data = (f"write-child pid={pid} pstart={cstart} child={pid} "
                    f"cstart={cstart} planted-by={os.getpid()} "
                    f"v=1.17.2-bridge {tail}")
            wfd = os.open(p, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(wfd, data.encode("utf-8"))
                os.fsync(wfd)
            finally:
                os.close(wfd)
        except (OSError, ValueError):
            return False
    return True


_LOCK_CUSTODY: set[str] = set()  # write-lock paths held past their run (survivors)


def _release_write_lock(cwd: str) -> None:
    path = _write_lock_path(cwd)
    if str(path) in _LOCK_CUSTODY:
        # CUSTODIAN (round 15): a run ended with group survivors — the
        # descriptor stays held by this server so no second writer can start
        # on a tree that may still be being written. codex_cancel_run
        # releases custody once group death is verified and the run is
        # terminalized.
        return
    for p in _PLANTED_LEGACY.pop(str(path), []):
        with contextlib.suppress(OSError):
            if f"planted-by={os.getpid()}" in p.read_text(encoding="utf-8",
                                                          errors="replace"):
                p.unlink()  # ours; content-based namespace (see _plant_legacy_locks)
    _release_os_lock(path)



def lease_record(cwd):
    """Metadata for duplicating an existing flock, never acquiring another one."""
    path = _write_lock_path(cwd)
    fd = _HELD[str(path)]
    info = os.fstat(fd)
    bridges = _PLANTED_LEGACY.get(str(path), [])
    return {"cwd": cwd, "path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "bridges": [str(p) for p in bridges]}


def adopt_lease(record, fd):
    path = _write_lock_path(record["cwd"])
    info = os.fstat(fd)
    if str(path) != record["path"] or (info.st_dev, info.st_ino) != (record["device"], record["inode"]) or not os.path.samestat(info, path.stat()):
        raise ValueError("Lease descriptor identity differs from its registered inode")
    previous = _HELD.get(str(path))
    _HELD[str(path)] = fd
    _PLANTED_LEGACY[str(path)] = [Path(p) for p in record["bridges"]]
    if previous is not None and previous != fd:
        os.close(previous)


def share_lease(record, worker_pid, custodian_pid):
    """Durably name both controllers before production is acknowledged."""
    from .runtime import sync_directory
    token = record["token"]
    data = (f"coordinator pid={worker_pid} pstart={_proc_start(worker_pid) or '-'} "
            f"child={custodian_pid} cstart={_proc_start(custodian_pid) or '-'} "
            f"lease={token} v=1.17.2-bridge cwd={record['cwd']} t={int(time.time())}\n")
    for value in record["bridges"]:
        path = Path(value)
        try:
            old = path.read_text()
        except FileNotFoundError:
            # A lost release receipt can leave only the worker's duplicate.
            # Re-establish the old-server bridge before restoring admission.
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                old = path.read_text()
            else:
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                sync_directory(path.parent)
                continue
        if "lease=" + token not in old and f"planted-by={custodian_pid}" not in old:
            raise ValueError("Legacy bridge ownership changed")
        # Atomic replacement prevents an old reader from seeing truncated data.
        import tempfile
        fd, temporary = tempfile.mkstemp(prefix="bridge-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if not _rewrite_held_payload(Path(record["path"]), data):
        raise OSError("Lease diagnostic could not be persisted")


def release_lease(record, bridges=True):
    """Close only this reference. Shared flocks must never use LOCK_UN."""
    if bridges:
        from .runtime import sync_directory
        for value in record["bridges"]:
            path = Path(value)
            try:
                if "lease=" + record["token"] not in path.read_text():
                    raise ValueError("Legacy bridge ownership changed; exclusion retained")
                path.unlink()
                sync_directory(path.parent)
            except FileNotFoundError:
                pass
    _PLANTED_LEGACY.pop(record["path"], None)
    _release_os_lock(Path(record["path"]))
