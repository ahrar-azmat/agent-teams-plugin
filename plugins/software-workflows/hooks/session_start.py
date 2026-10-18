#!/usr/bin/env python3
"""SessionStart: puts the push gate's git shim (hooks/gitshim) first on PATH for this session's
Bash tool, through the env file Claude Code sources before every Bash command (CLAUDE_ENV_FILE),
with the session's id and transcript, so commit and push are decided on their exact argv when git
runs them (push_gate.py --argv). The main session, its subagents and its agent-team teammates
share that file (measured on Claude Code 2.1.302). The shim proves itself live with a marker it
writes on its first run; this hook clears the marker, so every session start proves it again —
until then, and wherever the shim is absent, the PreToolUse text gate decides as before.

Never blocks a session: without an env file (an older Claude Code), on Windows (the shim is not
measured there) or on any failure, it does nothing and the text gate stays in charge."""
import json
import os
import re
import shlex
import sys
import time


def _state_dir():
    # push_gate.py `_shim_state_dir`: beside the token store
    ack = os.environ.get("CODEX_PUSH_ACK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-ack")
    return os.path.join(os.path.dirname(ack), "gitshim")


def main():
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or os.name == "nt":
        return
    data = json.loads(sys.stdin.read(1 << 20) or "{}")
    session = str(data.get("session_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        return
    shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gitshim")
    state = _state_dir()
    os.makedirs(state, mode=0o700, exist_ok=True)
    live = os.path.join(state, session + ".live")
    try:
        os.unlink(live)
    except FileNotFoundError:
        pass
    # other sessions' markers and notices a day old: removing one costs nothing (the shim writes a
    # missing marker again on its next run; a notice is only read right after its own Bash call)
    now = time.time()
    with os.scandir(state) as it:
        for entry in it:
            try:
                if entry.is_file(follow_symlinks=False) and now - entry.stat().st_mtime > 86400:
                    os.unlink(entry.path)
            except OSError:
                pass
    q = shlex.quote
    with open(env_file, "a", encoding="utf-8") as fh:
        fh.write(f'case ":$PATH:" in *:{q(shim)}:*) ;; *) export PATH={q(shim)}:"$PATH" ;; esac\n'
                 f"export CODEX_GATE_SESSION={q(session)}\n"
                 f"export CODEX_GATE_TRANSCRIPT={q(str(data.get('transcript_path') or ''))}\n"
                 f"export CODEX_GATE_STATE={q(state)}\n"
                 f"export CODEX_GATE_LIVE={q(live)}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
