#!/usr/bin/env python3
"""PostToolUse on Bash: the git shim prints a denial on stderr, which the command can redirect
away (`2>/dev/null`, a `| tail` that cuts it). Each denial is also written to this session's notice
file (push_gate.py `_write_notice`); this hook reads and removes that file after every Bash call
and puts into Claude's context every denial whose token the tool result does not already show."""
import json
import os
import re
import sys
import time


def _state_dir():
    # push_gate.py `_shim_state_dir`: beside the token store
    ack = os.environ.get("CODEX_PUSH_ACK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-ack")
    return os.path.join(os.path.dirname(ack), "gitshim")


def main():
    data = json.loads(sys.stdin.read(64 << 20) or "{}")
    session = str(data.get("session_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        return
    notice = os.path.join(_state_dir(), session + ".notice")
    claim = f"{notice}.{os.getpid()}"
    try:
        os.rename(notice, claim)  # one reader per notice, even with Bash calls running side by side
    except OSError:
        return
    try:
        with open(claim, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(claim)
    resp = data.get("tool_response")
    shown = ("\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr")) if isinstance(resp, dict)
             else str(resp or ""))
    # only denials from THIS call (it ran for duration_ms; 2 s of slack), never one left over
    since = time.time() - float(data.get("duration_ms") or 0) / 1000 - 2
    texts = [r["text"] for r in records
             if float(r.get("ts") or 0) >= since
             and not (r.get("nonce") and r["nonce"] in shown) and r["text"].split("\n", 1)[0] not in shown]
    if texts:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": "\n\n".join(texts)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
