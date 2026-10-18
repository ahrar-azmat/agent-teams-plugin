#!/usr/bin/env python3
"""SessionStart: puts the push gate's git shim (hooks/gitshim) first on PATH for this session's
Bash tool, through the env file Claude Code sources before every Bash command (CLAUDE_ENV_FILE),
with the session's id and transcript, so commit and push are decided on their exact argv when git
runs them (push_gate.py --argv). The main session, its subagents and its agent-team teammates
share that file (measured on Claude Code 2.1.302). The shim proves itself live with a marker it
writes on its first run; this hook clears the marker, so every session start proves it again —
until then, and wherever the shim is absent, the PreToolUse text gate decides as before. It also
builds the shim's mirror of git's exec-path for the session's git, so a Bash command that cannot
write the state (a sandbox) still finds one.

Never blocks a session: without an env file (an older Claude Code), on Windows (the shim is not
measured there) or on any failure, it does nothing and the text gate stays in charge.

It also runs the quick setup checks (plugin_setup.py) when a session starts or resumes, and when
one fails it tells the person (systemMessage) and the model (additionalContext) to run
/software-workflows:setup; it says nothing when everything is ready."""
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import time

try:
    import fcntl
except ImportError:  # Windows: no shim, so no notices
    fcntl = None

NOTICE_LOCK = "notices.lock"  # push_gate.py and gate_notice.py NOTICE_LOCK: never removed
DAY_S = 86400


def _state_dir():
    # push_gate.py `_shim_state_dir`: beside the token store
    ack = os.environ.get("CODEX_PUSH_ACK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "push-ack")
    return os.path.join(os.path.dirname(ack), "gitshim")


def _drop_dead_mirrors(state, now):
    """Removes the mirrors of git's exec-path (push_gate.py `_git_exec_farm`) that nothing can use:
    the git binary, the exec-path or the shim it records is gone (a git upgraded, a plugin version
    removed), or the exec-path changed more than a day ago (a git updated in place: the shim stopped
    choosing it then). And a build that died a day ago."""
    try:
        entries = list(os.scandir(os.path.join(state, "exec")))
    except OSError:
        return
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            mark = os.path.join(entry.path, ".codex-gate-farm")
            if ".build-" in entry.name:
                dead = now - entry.stat(follow_symlinks=False).st_mtime > DAY_S
            elif not os.path.isfile(mark):
                continue
            else:
                with open(mark, encoding="utf-8", errors="surrogateescape") as fh:
                    recorded = fh.read().splitlines()[:3]
                dead = len(recorded) < 3 or not all(os.path.exists(p) for p in recorded)
                if not dead:
                    changed = os.stat(recorded[1]).st_mtime
                    dead = changed > os.stat(mark).st_mtime and now - changed > DAY_S
            if dead:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass


def _setup_notice(source):
    """The SessionStart output naming what is not ready, or None when everything is."""
    if source not in ("startup", "resume"):
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "plugin_setup.py")
    spec = importlib.util.spec_from_file_location("sw_plugin_setup", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    missing = [(c[0], c[2]) for c in setup.checks(quick=True) if not c[1]]
    if not missing:
        return None
    return {
        "systemMessage": "software-workflows: %s not ready. Run /software-workflows:setup"
                         % ", ".join(item for item, _ in missing),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "software-workflows setup is incomplete on this machine: "
                                 + "; ".join(f"{item}: {detail}" for item, detail in missing)
                                 + ". Tell the user once, briefly, that /software-workflows:setup fixes "
                                   "this (it asks before changing any setting)."}}


def main():
    data = json.loads(sys.stdin.read(1 << 20) or "{}")
    try:
        _install_shim(data)
    except Exception:
        pass
    notice = _setup_notice(str(data.get("source") or ""))
    if notice:
        print(json.dumps(notice))


def _install_shim(data):
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or os.name == "nt":
        return
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
    # missing marker again on its next run; a notice holds a denial for minutes at most). Under the
    # notices' lock, which is itself never removed: a writer appends only while holding it.
    now = time.time()
    lock = os.open(os.path.join(state, NOTICE_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        with os.scandir(state) as it:
            for entry in it:
                try:
                    if (entry.name != NOTICE_LOCK and entry.is_file(follow_symlinks=False)
                            and now - entry.stat().st_mtime > DAY_S):
                        os.unlink(entry.path)
                except OSError:
                    pass
    finally:
        os.close(lock)
    _drop_dead_mirrors(state, now)
    q = shlex.quote
    with open(env_file, "a", encoding="utf-8") as fh:
        fh.write(f'case ":$PATH:" in *:{q(shim)}:*) ;; *) export PATH={q(shim)}:"$PATH" ;; esac\n'
                 f"export CODEX_GATE_SESSION={q(session)}\n"
                 f"export CODEX_GATE_TRANSCRIPT={q(str(data.get('transcript_path') or ''))}\n"
                 f"export CODEX_GATE_STATE={q(state)}\n"
                 f"export CODEX_GATE_LIVE={q(live)}\n")
    try:
        _prebuild_mirror(shim, state)
    except Exception:
        pass  # the shim builds it on its first run instead


def _prebuild_mirror(shim, state):
    """The exec-path mirror (push_gate.py `_git_exec_farm`) for the git this session finds on
    PATH, built now, while this hook can write the state. A Bash command that cannot write it (a
    sandbox) still finds the mirror ready, so the shim refuses a git command only where the git
    that runs has none and none can be made (review of 2026-10-18, turn 9)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_gate.py")
    spec = importlib.util.spec_from_file_location("sw_push_gate_prebuild", path)
    if spec is None or spec.loader is None:
        return
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    search = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                             if not gate._same_dir(d, shim) and not gate._is_farm(d))
    real = shutil.which("git", path=search)
    if real:
        os.environ["CODEX_GATE_STATE"] = state
        gate._git_exec_farm(real, gate._git_builtins(real))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
