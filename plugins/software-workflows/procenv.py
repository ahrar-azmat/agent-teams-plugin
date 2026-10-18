#!/usr/bin/env python3
"""Exact process-ENVIRONMENT identification for the run marker (stdlib only).

Both the MCP server (server.py — sweep, cancel, collect) and the detached
no-server watchdog (/bin/sh, at the run deadline) must answer two questions
before they SIGKILL anything: which live processes carry
CODEX_ORACLE_RUN=<tag> in their ENVIRONMENT, and does THIS pid — not merely
in its argv? `ps -E` mixes argv and env in one string (an operator's grep or
a decoy matched a text scan, rounds 29-30), and the `-E` flag exists only in
BSD ps: procps-ng (Linux) has no such option (round 31), so a ps-based scan
was never a Linux capability at all. This module reads the real boundary:

  macOS  enumeration via `/bin/ps -ax -o pid=,stat=` (BSD, measured), then
         sysctl KERN_PROCARGS2 per pid → argc + argv[] + env[] with real
         boundaries (MEASURED 2026-09-02: env-marked child → True, argv decoy
         → False; other users' processes are unreadable → skipped)
  Linux  a /proc scan: /proc/<pid>/environ (NUL-separated; readable only for
         the caller's own processes) and /proc/<pid>/stat for the zombie state

It is the SINGLE implementation: server.py loads it by path and the watchdog
invokes the CLI, so the two can never disagree.

CLI (the watchdog has no server to ask):
    python3 procenv.py <pid> <tag>    exit 0 = marked (kill target), 1 = not
                                      marked, 2 = unverifiable (never a kill)
    python3 procenv.py --list <tag>   one marked pid per line; exit 0, or 2
                                      when enumeration itself failed (the
                                      watchdog reads that as UNKNOWN, never
                                      as quiescence)
"""
import os
import subprocess
import sys

RUN_MARKER_ENV = "CODEX_ORACLE_RUN"
# READ CAPS (round 36): a /proc read is bounded, and a file LARGER than its
# cap is REFUSED (uncertain), never silently truncated — a truncated environ
# could cut the marker's boundary and read as "no marker". Named threat: an
# unbounded read of a hostile /proc entry. Sizes: a Linux environment is
# bounded by RLIMIT_STACK/4 (2 MiB under the default 8 MiB stack), so 4 MiB
# is 2× the default ceiling; a status file is a few hundred bytes.
ENVIRON_MAX_BYTES = 4 << 20
STATUS_MAX_BYTES = 1 << 16
PS_BIN = "/bin/ps"  # deployment-verified absolute path on macOS (BSD ps)


def _needle(run_tag):
    return f"{RUN_MARKER_ENV}={run_tag}".encode()


def _darwin_env(pid):
    """argv-free environment of a macOS process via KERN_PROCARGS2, or None."""
    import ctypes
    libc = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[:size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:]
    i = rest.find(b"\0")  # exec path, then NUL padding, then argv[]
    while i + 1 < len(rest) and rest[i + 1] == 0:
        i += 1
    parts = rest[i + 1:].split(b"\0")
    return [p for p in parts[argc:] if p]


def proc_env_has_marker(pid, run_tag, proc_root="/proc"):
    """True / False / None (unverifiable: no such process, denied, no channel)."""
    needle = _needle(run_tag)
    plat = sys.platform  # a variable, so a type checker does not prune the other OS's branch
    try:
        pid = int(pid)
        if plat == "darwin":
            env = _darwin_env(pid)
            return None if env is None else needle in env
        if os.path.isdir(proc_root):
            dfd = os.open(f"{proc_root}/{pid}", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                return needle in _read_at(dfd, "environ", ENVIRON_MAX_BYTES).split(b"\0")
            finally:
                os.close(dfd)
    except Exception:
        return None
    return None


def _read_at(dfd, name, limit):
    """Read /proc/<pid>/<name> RELATIVE to the pinned directory descriptor —
    the pid cannot be reused under us between the stat and the read (round
    34). Reads limit + 1 bytes and REFUSES a file larger than `limit` with
    an OSError (EFBIG) — the caller classifies it UNCERTAIN — instead of
    returning a silently truncated prefix (round 36)."""
    import errno
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0), dir_fd=dfd)
    try:
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(1 << 16, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise OSError(errno.EFBIG, f"{name} exceeds {limit} bytes — a truncated read is refused")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _status_field(blob, key):
    for line in blob.splitlines():
        if line.startswith(key):
            return line[len(key):].split()
    return None


def _status_uid(blob):
    """The REAL uid from /proc/<pid>/status — the directory's owner is root
    for a non-dumpable process of ours, so ownership is not identity."""
    f = _status_field(blob, b"Uid:")
    try:
        return int(f[0]) if f else None
    except (TypeError, ValueError):
        return None


def _status_zombie(blob):
    f = _status_field(blob, b"State:")
    return bool(f) and f[0] == b"Z"


SCAN_DEADLINE_S = 10.0   # the watchdog waits synchronously on --list
SCAN_MAX_ENTRIES = 65536
# TRANSIENT UNREADABILITY (round 36, measured on this host): a same-user
# process caught mid-exec or mid-exit can refuse its environment for a few
# milliseconds; one such pid made the whole scan UNKNOWN (fail closed — a
# cancel then refused to terminalize) in 1 of 3 runs. Unreadable/uncertain
# pids are therefore RE-EXAMINED a bounded number of times before the scan
# gives up on them; a pid that stays unreadable still makes the scan UNKNOWN.
SCAN_RETRIES = 3
SCAN_RETRY_PAUSE_S = 0.2


def scan_proc(run_tag, proc_root="/proc", me=None):
    """Linux enumeration over /proc. Every read is RELATIVE to a pinned
    /proc/<pid> descriptor; the owner comes from the pinned `status` file
    (real uid), never from directory ownership; a foreign user's process is
    never read. Only a DEFINITE disappearance (ENOENT/ESRCH) is a skip:
    permission-denied on our own live process is UNREADABLE and any other
    failure (EIO, EMFILE, …) is UNCERTAIN — both make the scan raise, because
    an empty list must PROVE quiescence (round 34). Bounded by a deadline
    and an entry cap (both raise)."""
    import time
    needle = _needle(run_tag)
    me = os.getpid() if me is None else me
    uid = os.geteuid() if hasattr(os, "geteuid") else None
    end = time.monotonic() + SCAN_DEADLINE_S
    try:
        names = os.listdir(proc_root)
    except OSError as exc:
        raise OSError(f"cannot list {proc_root}: {exc}") from exc
    if len(names) > SCAN_MAX_ENTRIES:
        raise OSError(f"{proc_root}: {len(names)} entries exceed the scan cap")
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)

    def examine(name):
        """One pid → "marked" | "clear" | "unreadable" | "uncertain"."""
        try:
            dfd = os.open(f"{proc_root}/{name}", dir_flags)
        except (FileNotFoundError, ProcessLookupError):
            return "clear"  # vanished: definite
        except OSError:
            return "uncertain"
        try:
            try:
                status = _read_at(dfd, "status", STATUS_MAX_BYTES)
            except (FileNotFoundError, ProcessLookupError):
                return "clear"
            except OSError:
                return "uncertain"
            puid = _status_uid(status)
            if puid is None:
                return "uncertain"
            if uid is not None and puid != uid:
                return "clear"  # foreign user: never ours, never read
            try:
                env = _read_at(dfd, "environ", ENVIRON_MAX_BYTES).split(b"\0")
            except (FileNotFoundError, ProcessLookupError):
                return "clear"
            except PermissionError:
                # ours, alive, denied (ptrace scope / non-dumpable)
                return "clear" if _status_zombie(status) else "unreadable"
            except OSError:
                return "uncertain"
            return "marked" if (needle in env and not _status_zombie(status)) else "clear"
        finally:
            os.close(dfd)

    out, pending = [], {}
    for name in names:
        if time.monotonic() > end:
            raise OSError("process scan exceeded its deadline")
        if not name.isdigit() or int(name) == me:
            continue
        verdict = examine(name)
        if verdict == "marked":
            out.append(int(name))
        elif verdict != "clear":
            pending[name] = verdict
    for _ in range(SCAN_RETRIES):
        if not pending:
            break
        time.sleep(SCAN_RETRY_PAUSE_S)
        if time.monotonic() > end:
            raise OSError("process scan exceeded its deadline")
        for name in list(pending):
            verdict = examine(name)
            if verdict == "marked":
                out.append(int(name))
                del pending[name]
            elif verdict == "clear":
                del pending[name]
            else:
                pending[name] = verdict
    if time.monotonic() > end:
        raise OSError("process scan exceeded its deadline")
    if pending:
        unreadable = sum(1 for v in pending.values() if v == "unreadable")
        uncertain = len(pending) - unreadable
        raise OSError(f"{unreadable} same-user process(es) unreadable and {uncertain} "
                      f"uncertain read(s) in {proc_root} after {SCAN_RETRIES} re-examinations "
                      "— enumeration is UNKNOWN, not empty")
    return out


def _gone_or_zombie_darwin(pid, ps_bin=PS_BIN):
    """After a KERN_PROCARGS2 failure: did the process VANISH or become a
    zombie (both: skip) — or is it alive and genuinely unreadable (UNKNOWN)?
    MEASURED 2026-09-04: the only same-user pid that ever failed here was a
    `(sleep)` that had exited between the listing and the read."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except Exception:
        return False  # exists, not signallable by us: alive, unreadable
    try:
        done = subprocess.run([ps_bin, "-o", "stat=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    state = done.stdout.strip()
    if state:
        return state.startswith("Z")
    # EMPTY output: `ps` exits 1 both for a pid that is gone and when the
    # lookup itself failed (round 40: a failing ps read as "vanished" and the
    # scan returned [] — custody released over a live descendant). Only the
    # kernel decides — ask it again.
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except Exception:
        return False
    return False  # exists per the kernel, invisible to ps: alive and unreadable (UNKNOWN)


def scan_darwin(run_tag, ps_bin=PS_BIN, me=None):
    """macOS enumeration: BSD ps for the pid/state/uid list, KERN_PROCARGS2
    for the exact environment of each live, non-zombie pid. A SAME-USER pid
    whose environment cannot be read makes the scan UNKNOWN (raises);
    other users' processes are skipped. Bounded by a deadline."""
    import time
    me = os.getpid() if me is None else me
    uid = os.geteuid() if hasattr(os, "geteuid") else None
    end = time.monotonic() + SCAN_DEADLINE_S
    try:
        done = subprocess.run([ps_bin, "-ax", "-o", "pid=,stat=,uid="],
                              capture_output=True, text=True, timeout=SCAN_DEADLINE_S)
    except Exception as exc:
        raise OSError(f"{ps_bin} failed: {exc}") from exc
    if done.returncode != 0:
        raise OSError(f"{ps_bin} exited {done.returncode}: {done.stderr.strip()[:120]}")
    out, pending = [], []
    for line in done.stdout.splitlines():
        if time.monotonic() > end:
            raise OSError("process scan exceeded its deadline")
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, puid = int(parts[0]), int(parts[2])
        except ValueError:
            continue
        if pid == me or parts[1].startswith("Z"):
            continue
        if uid is not None and puid != uid:
            continue  # another user's process: never ours, never a kill target
        verdict = proc_env_has_marker(pid, run_tag)
        if verdict is True:
            out.append(pid)
        elif verdict is None and not _gone_or_zombie_darwin(pid, ps_bin=ps_bin):
            pending.append(pid)  # ours, alive, yet unreadable — never "not marked"
    for _ in range(SCAN_RETRIES):
        if not pending:
            break
        time.sleep(SCAN_RETRY_PAUSE_S)
        if time.monotonic() > end:
            raise OSError("process scan exceeded its deadline")
        still = []
        for pid in pending:
            verdict = proc_env_has_marker(pid, run_tag)
            if verdict is True:
                out.append(pid)
            elif verdict is None and not _gone_or_zombie_darwin(pid, ps_bin=ps_bin):
                still.append(pid)
        pending = still
    if pending:
        raise OSError(f"{len(pending)} same-user process(es) unreadable after "
                      f"{SCAN_RETRIES} re-examinations — enumeration is UNKNOWN, not empty")
    return out


def marked_pids(run_tag, proc_root="/proc", ps_bin=PS_BIN):
    """Live pids (same user; zombies and this process excluded) whose
    ENVIRONMENT carries the marker. Raises OSError when enumeration itself
    is impossible — callers must fail closed on that, never read it as
    "nobody left"."""
    if not run_tag:
        return []
    plat = sys.platform
    if plat == "darwin":
        return scan_darwin(run_tag, ps_bin=ps_bin)
    if os.path.isdir(proc_root):
        return scan_proc(run_tag, proc_root=proc_root)
    raise OSError("no process enumeration channel on this platform")


def main(argv):
    if len(argv) == 3 and argv[1] == "--list":
        try:
            pids = marked_pids(argv[2])
        except Exception:
            return 2
        for pid in pids:
            print(pid)
        return 0
    if len(argv) != 3:
        return 2
    try:
        verdict = proc_env_has_marker(int(argv[1]), argv[2])
    except Exception:
        return 2
    if verdict is True:
        return 0
    return 1 if verdict is False else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
