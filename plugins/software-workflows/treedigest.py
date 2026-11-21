#!/usr/bin/env python3
"""Hard deadline and process-group containment for the Oracle's dispatch snapshot and preflight."""
import os
import select
import signal
import subprocess
import threading
import time

GRACE_S = 3.0
PS_BIN = "/bin/ps"


def _start_writer(proc, payload):
    """Feed `payload` to the child's stdin from a thread and close it — no
    temporary file (round 37: a machine without a writable temporary
    directory crashed the hook before the deny could be printed) and no
    pipe the caller could block on. A dead child ends the writer (EPIPE)."""
    pipe = proc.stdin

    def run():
        try:
            pipe.write(payload)
            pipe.flush()
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t = threading.Thread(target=run, name="codex-oracle-stdin", daemon=True)
    t.start()
    return t


def _leader_exited(proc):
    """Has the child EXITED, without reaping it? waitid(WNOWAIT) where the
    platform has it (macOS and Linux, measured); None when it cannot be
    known without reaping (the caller then relies on EOF and the deadline)."""
    waitid = getattr(os, "waitid", None)
    if waitid is None or not hasattr(os, "WNOWAIT") or not hasattr(os, "P_PID"):
        return None
    try:
        return waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except OSError:
        return None


def group_live_members(pgid, ps_bin=PS_BIN):
    """Pids of LIVE (non-zombie) members of process group `pgid`, from a
    process listing (`ps -A -o pid=,pgid=,stat=` — BSD and procps agree on
    these options); None when the listing itself failed (unknown)."""
    if os.name == "nt":
        return None
    try:
        done = subprocess.run([ps_bin, "-A", "-o", "pid=,pgid=,stat="],
                              capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    live = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, pg = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        # procps' Zl: a leader that exited while another thread runs
        if pg == pgid and (not parts[2].startswith("Z") or "l" in parts[2]):
            live.append(pid)
    return live


def _kill_group(proc):
    """SIGKILL the process GROUP led by `proc` — by the recorded group id even
    when the leader already EXITED (round 32: an exited leader left its group
    alive) — then reap the leader. The leader must be UNREAPED (round 36):
    `proc.returncode` is Popen's own reap record, and once it is set the pid,
    hence the group id, may already belong to a stranger, so the group is
    NOT signalled (only the dead leader is reaped). Returns True only when
    the group was signalled or PROVEN empty: ESRCH proves it; EPERM (a
    zombie-led group answers EPERM on macOS — measured — but so would a live
    member we may not signal) is checked against a process listing, and a
    live member left behind makes this False (round 37: containment failure
    is the caller's to report, never "gone"). Windows has no groups: the
    leader alone is killed (documented, UNMEASURED)."""
    swept = False
    if os.name != "nt" and proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            swept = True
        except ProcessLookupError:
            swept = True  # ESRCH: nothing left in a group that is still ours
        except PermissionError:
            # EPERM = no member could be signalled: a zombie-led group (fine)
            # or a live member we may not signal — INCLUDING the leader
            # (round 37: excluding it read an unsignalable live leader as
            # swept). Only an empty live listing proves the group gone.
            live = group_live_members(proc.pid)
            swept = live == []
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    if proc.returncode is None:
        swept = False  # the leader itself could not be terminated or reaped
    return swept


def _read_bounded_thread(proc, end, cap):
    """Windows reader (no select on pipes): a thread reads chunks, the caller
    waits with timeouts; on the deadline or the cap the child is killed so
    the blocked read ends. Returns (chunks, why) with why "" | "timeout" |
    "cap". UNMEASURED on Windows."""
    chunks, why = [], ""
    state = {"done": False, "cap": False, "total": 0}
    pipe = proc.stdout

    def run():
        try:
            while True:
                chunk = pipe.read1(1 << 16) if hasattr(pipe, "read1") else pipe.read(1 << 16)
                if not chunk:
                    break
                state["total"] += len(chunk)
                if state["total"] > cap:
                    state["cap"] = True
                    break
                chunks.append(chunk)
        except Exception:
            pass
        finally:
            state["done"] = True

    t = threading.Thread(target=run, name="codex-oracle-stdout", daemon=True)
    t.start()
    while not state["done"]:
        remaining = end - time.monotonic()
        if remaining <= 0:
            why = "timeout"
            break
        t.join(min(0.25, remaining))
    if state["cap"]:
        why = "cap"
    if why:
        try:
            proc.kill()
        except Exception:
            pass
        t.join(5)
    return chunks, why


def run_contained(argv, timeout_s, cap=1 << 20, payload=None, grace_s=GRACE_S, *, env=None, cwd=None):
    """Run `argv` in its OWN session and return (returncode, stdout bytes,
    why) with why = "" | "timeout" | "cap" | "spawn: …" | "read: …" |
    "sweep: …". stdout is streamed under select() until EOF, the deadline, or
    the LEADER's EXIT (waitid WNOWAIT — a helper that inherited the pipe
    cannot hold the call open, round 37); `payload` (bytes) is fed to stdin
    by a writer thread. The group is swept on EVERY completion by a group id
    that is still reserved (the leader is reaped LAST), the pipe is drained
    after the sweep, and a member the sweep could not remove is reported as
    "sweep: …" — never silently. Windows: no sessions or groups — the
    leader alone is bounded by a reader thread (UNMEASURED)."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=(os.name != "nt"), env=env, cwd=cwd)
    except Exception as exc:
        return -1, b"", f"spawn: {exc}"
    writer = _start_writer(proc, payload) if payload is not None else None
    pipe = proc.stdout
    assert pipe is not None
    end = time.monotonic() + timeout_s
    if os.name == "nt":
        chunks, why = _read_bounded_thread(proc, end, cap)
        _kill_group(proc)
        return (proc.returncode if proc.returncode is not None else -1), b"".join(chunks), why
    chunks, total, why = [], 0, ""
    leader_done = False
    fd = pipe.fileno()
    try:
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                why = "timeout"
                break
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if not ready:
                if _leader_exited(proc):
                    leader_done = True  # a helper may still hold the pipe: sweep, then drain
                    break
                continue
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                why = "cap"
                break
            chunks.append(chunk)
    except Exception as exc:
        why = why or f"read: {exc}"
    finally:
        if not why and not leader_done:
            _settle(proc, grace_s)
        swept = _kill_group(proc)  # the leader is still OURS here: unreaped until this reaps it
        if not swept:
            # round 38: a breach already recorded must not HIDE a failed
            # containment — both are reported
            why = (why + "; " if why else "") + "sweep: a live member of the child's process group could not be signalled"
        if leader_done and not why:
            # the helpers holding the pipe are gone: take what they left
            try:
                os.set_blocking(fd, False)
                while total <= cap:
                    try:
                        chunk = os.read(fd, min(1 << 16, cap + 1 - total))  # never past the cap + 1 sentinel
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > cap:
                        why = "cap"  # the sentinel byte arrived: a loud cap violation, not a silent overrun
                        break
                    chunks.append(chunk)
            except Exception:
                pass
        try:
            pipe.close()
        except Exception:
            pass
        if writer is not None:
            writer.join(1.0)
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, b"".join(chunks), why


def _settle(proc, grace_s):
    """Wait up to `grace_s` for the leader to EXIT without reaping it —
    `waitid(WNOWAIT)` where the platform has it (macOS and Linux, measured)
    — so a leader that closed its stdout and is finishing is not killed
    mid-exit (its real exit status still decides) while its pid, hence the
    group id, stays reserved. Without waitid a short fixed pause is all that
    is possible; a leader still exiting then reads as killed (-9), which only
    makes the caller MORE conservative."""
    end = time.monotonic() + max(0.0, grace_s)
    while True:
        exited = _leader_exited(proc)
        if exited is None:
            time.sleep(min(0.2, max(0.0, grace_s)))
            return
        if exited or time.monotonic() >= end:
            return
        time.sleep(0.02)
