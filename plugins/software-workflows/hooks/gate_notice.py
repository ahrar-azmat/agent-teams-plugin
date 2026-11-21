#!/usr/bin/env python3
"""Deliver a pre-push refusal once if Bash hid its stderr.

PostToolUseFailure carries its output in error. Parallel Bash calls share the
session notice, so readers and writers lock the same stable inode and leave
earlier refusals for the call that produced them.
"""
import contextlib
import json
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:  # Windows has no pre-push review hook
    fcntl = None

SLACK_S = 2.0
SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _state_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-notices")


@contextlib.contextmanager
def _locked(state):
    lock = os.open(os.path.join(state, "notices.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


def write_notice(session, token, message):
    if not session or not SESSION.fullmatch(session) or fcntl is None:
        return
    state = _state_dir()
    os.makedirs(state, mode=0o700, exist_ok=True)
    with _locked(state):
        path = os.path.join(state, session + ".notice")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="ascii") as fh:
            fh.write(json.dumps({"ts": time.time(), "nonce": token, "text": message}) + "\n")


def _longest_call_s():
    # the Bash tool's ceiling on a foreground call: 600 s, or BASH_MAX_TIMEOUT_MS when raised
    try:
        ms = float(os.environ.get("BASH_MAX_TIMEOUT_MS") or 0)
    except ValueError:
        ms = 0.0
    return max(600.0, ms / 1000)


def main():
    data = json.loads(sys.stdin.read(64 << 20) or "{}")
    session = str(data.get("session_id") or "")
    if fcntl is None or not SESSION.fullmatch(session):
        return
    state = _state_dir()
    notice = os.path.join(state, session + ".notice")
    if not os.path.exists(notice):
        return  # no denial of this session waiting
    resp = data.get("tool_response")
    if isinstance(resp, dict):
        shown = "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr"))
    elif resp is not None:
        shown = str(resp)
    else:  # PostToolUseFailure
        shown = str(data.get("error") or "")
    now = time.time()
    since = now - float(data.get("duration_ms") or 0) / 1000 - SLACK_S  # this call's start
    stale = now - _longest_call_s() - SLACK_S  # no foreground call still running began before this
    with _locked(state):
        try:
            with open(notice, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            return
        mine, earlier, keep = [], [], []
        for line in lines:
            try:
                rec = json.loads(line)
                text, ts, nonce = str(rec["text"]), float(rec.get("ts") or 0), str(rec.get("nonce") or "")
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            # shown by its token when it has one (its header without the token cannot be acted on)
            if (nonce in shown) if nonce else (text.split("\n", 1)[0] in shown):
                continue
            if ts >= since:
                mine.append(text)
            elif ts < stale:
                earlier.append(text)
            else:
                keep.append(line + "\n")
        texts = mine + ["(an earlier Bash command's denial, which its output did not show)\n" + t for t in earlier]
        if texts:  # said before the notice lets go of them
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": str(data.get("hook_event_name") or "PostToolUse"),
                "additionalContext": "\n\n".join(texts)}}), flush=True)
        if not keep:
            os.unlink(notice)
        elif len(keep) < len(lines):
            tmp = f"{notice}.{os.getpid()}.tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.writelines(keep)
                os.replace(tmp, notice)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
