#!/usr/bin/env python3
"""VERBATIM 1.17.1 write-lock protocol (agent-teams-plugin commit 9f3d0c3),
vendored as a cross-version interop fixture: these functions are what a
still-running pre-1.17.2 server executes against the shared lock directory.

Differences from the shipped source, and nothing else:
- LIVE_LOG_DIR / MAX_RUNTIME_SECONDS are module globals a test points at its
  isolated directory (the shipped code read the server's own globals);
- the _private() chmod helper is dropped (irrelevant to lock semantics).

Used by test_write_mode.test_legacy_interop_both_orders_and_aliases to prove
the 1.17.2 bridge excludes an OLD writer in both acquisition orders.
"""
import hashlib
import os
import re
import time
from pathlib import Path

LIVE_LOG_DIR = Path(".")
MAX_RUNTIME_SECONDS = 3600


def _write_lock_path(cwd: str) -> Path:
    return (
        LIVE_LOG_DIR / "write-locks"
        / f"{hashlib.sha1(cwd.encode('utf-8', 'replace')).hexdigest()[:16]}.lock"
    )


def _acquire_write_lock(cwd: str, run_hint: str) -> tuple[bool, str]:
    """One-writer-per-tree MUTUAL EXCLUSION, across server processes.

    (1.17.1 semantics, verbatim: O_EXCL content lockfile, pid-liveness
    stale-break, age fallback.)
    """
    path = _write_lock_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"write-lock dir unusable ({e}) — refusing to write unlocked"
    payload = f"{run_hint} pid={os.getpid()} cwd={cwd} t={int(time.time())}\n"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            return True, ""
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                holder = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue  # holder released between EXISTS and stat — retry
            holder_dead = False
            m = re.search(r"\bpid=(\d+)\b", holder)
            if m and os.name != "nt":
                try:
                    os.kill(int(m.group(1)), 0)
                except ProcessLookupError:
                    holder_dead = True
                except (PermissionError, OSError):
                    pass  # exists (or unknowable) — treat as alive
            if holder_dead or age > MAX_RUNTIME_SECONDS:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            return False, holder or "unknown holder"
        except OSError as e:
            return False, f"write-lock unusable ({e}) — refusing to write unlocked"
    return False, "lock contention"


def _release_write_lock(cwd: str) -> None:
    try:
        _write_lock_path(cwd).unlink()
    except OSError:
        pass
