#!/usr/bin/env python3
"""PostToolUse and PostToolUseFailure on Bash: the git shim prints a denial on stderr, which the
command can redirect away (`2>/dev/null`, a `| tail` that cuts it). Each denial is also appended
to this session's notice file (push_gate.py `_write_notice`); after every Bash call this hook puts
into Claude's context each denial its output does not already show. A denial exits 1, and a Bash
call that fails gets PostToolUseFailure, with its output in `error`, never PostToolUse (measured
2026-10-18, Claude Code 2.1.302).

Bash calls run side by side (review of 2026-10-18). Readers and writers take one lock file that is
never removed (NOTICE_LOCK); a reader writes what it keeps to a new file and renames it over the
notice, so a reader that dies midway leaves the notice as it was. A denial from before this call
started stays for the call that made it, until no foreground Bash call can still be running (the
Bash tool's longest timeout); the next call then shows it as an earlier command's.

It also removes the passes the PreToolUse gate left for this call's acknowledged git (push_gate.py
`_issue_pass`): the shim removes the one it uses, but cannot inside Claude Code's sandbox."""
import json
import os
import re
import sys
import time

try:
    import fcntl
except ImportError:  # Windows: the shim refuses there and writes no notice
    fcntl = None

SLACK_S = 2.0  # the hook starts a moment after its call ended
NOTICE_LOCK = "notices.lock"  # push_gate.py NOTICE_LOCK; hooks/session_start.py never removes it
PASS_NONCE_RE = re.compile(r"CODEX_PUSH_ACK\s*=\s*['\"]?([0-9a-f]{16})")  # push_gate.py ACK_ENV


def _ack_dir():
    # push_gate.py `_ack_dir`
    return os.environ.get("CODEX_PUSH_ACK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-ack")


def _state_dir():
    # push_gate.py `_shim_state_dir`: beside the token store
    return os.path.join(os.path.dirname(_ack_dir()), "gitshim")


def _drop_passes(data):
    tool_input = data.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    for nonce in set(PASS_NONCE_RE.findall(command)):
        try:
            os.unlink(os.path.join(_ack_dir(), nonce + ".pass.json"))
        except OSError:
            pass


def _longest_call_s():
    # the Bash tool's ceiling on a foreground call: 600 s, or BASH_MAX_TIMEOUT_MS when raised
    try:
        ms = float(os.environ.get("BASH_MAX_TIMEOUT_MS") or 0)
    except ValueError:
        ms = 0.0
    return max(600.0, ms / 1000)


def main():
    data = json.loads(sys.stdin.read(64 << 20) or "{}")
    _drop_passes(data)
    session = str(data.get("session_id") or "")
    if fcntl is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
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
    lock = os.open(os.path.join(state, NOTICE_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
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
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            os.replace(tmp, notice)
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
