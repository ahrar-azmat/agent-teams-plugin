#!/usr/bin/env python3
"""Claude host hooks, Python 3.9. Uninvolved calls only read routing hints."""
import sys
sys.dont_write_bytecode = True
import json
import os
import stat


TOOLS = {"abraham", "code_review", "codex_runs", "codex_run_log", "codex_resume_run", "codex_cancel_run",
         "coordinator_claims", "coordinator_send", "coordinator_inbox", "coordinator_unit", "coordinator_seal", "coordinator_publish"}
FILES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def registered(payload, state):
    """A stale hint can route a call, never authorize or deny an edit."""
    target = payload.get("cwd", "")
    if payload.get("tool_name") in FILES:
        data = payload.get("tool_input") or {}
        value = data.get("notebook_path") if payload["tool_name"] == "NotebookEdit" else data.get("file_path")
        if not isinstance(value, str) or not value:
            return False
        target = os.path.join(target, value)
    target = os.path.realpath(target)
    try:
        fd = os.open(os.path.join(state, "checkouts.json"), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return False
            registry = json.load(stream)
        if registry.get("version") != 1:
            return False
        for record in sorted(registry["roots"], key=lambda r: len(r["root"]), reverse=True):
            root = record["root"]
            if os.path.commonpath((root, target)) == root:
                return True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return False


def warning(session, state):
    import hashlib
    from pathlib import Path
    # Never create/repair the configured state just to write a warning marker.
    root = Path(state)
    try:
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("unusable state")
        root = root / "lead-warnings"
    except OSError:
        root = Path(os.path.realpath("/tmp")) / ("software-workflows-warnings-" + str(os.getuid()))
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        return False
    name = hashlib.sha256((state + "\0" + session).encode()).hexdigest()
    try:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def unavailable(session, state):
    try:
        first = warning(session or "unknown-session", state)
    except OSError:
        first = True
    return {"systemMessage": "Writer coordinator is unavailable. Lead edits are allowed; later reconciliation will expose unexplained changes as UNKNOWN."} if first else None


def main():
    payload = json.load(sys.stdin)
    event, tool, session = payload.get("hook_event_name"), payload.get("tool_name", ""), payload.get("session_id", "")
    short = tool.split("__")[-1]
    if event == "PreToolUse" and tool.startswith("mcp__") and any(n in tool for n in ("software-workflows", "codex-oracle")) and short in TOOLS:
        if not session:
            return None
        updated = dict(payload.get("tool_input") or {})
        updated["session_id"] = session
        return {"hookSpecificOutput": {"hookEventName": event, "updatedInput": updated}}
    if tool not in FILES | {"Bash"}:
        return None
    state = os.path.abspath(os.path.expanduser(os.environ.get("SOFTWARE_WORKFLOWS_STATE") or "~/.local/state/software-workflows"))
    if not registered(payload, state):
        return None
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    try:
        from coordinator.client import Client, CoordinatorError
    except Exception:
        return unavailable(session, state)
    try:
        data = {key: payload[key] for key in ("cwd", "session_id", "tool_use_id", "tool_name", "tool_input", "hook_event_name", "tool_response") if key in payload}
        if tool == "Bash":
            # How long the host lets this call run: the coordinator cannot read the host's environment.
            data["bash_limits"] = {k: os.environ[k] for k in ("BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS") if k in os.environ}
        result = Client(state, timeout=3).call("lead-hook/dispatch", {"payload": data})
        if not isinstance(result, dict):
            return unavailable(session, state)
        token = result.pop("arm", None) if tool == "Bash" and event == "PreToolUse" else None
        if token:
            # The answer came before the host runs the command: only that makes the coordinator's
            # record of what was there a state from before it, so tell it (a lost reply leaves UNKNOWN).
            try:
                armed = Client(state, timeout=3).call("lead-hook/armed", {"observed": result.get("observed"), "arm": token})
                result["armed"] = bool(isinstance(armed, dict) and armed.get("armed"))
            except Exception:
                result["armed"] = False
        if not result.get("coordinated", True):
            return None
        return {"hookSpecificOutput": {"hookEventName": event,
                "additionalContext": "Coordinator lead operation: " + json.dumps(result, ensure_ascii=True)}}
    except CoordinatorError as exc:
        # Only an authoritative claim conflict may deny. Infrastructure failures
        # and uncalibrated adapters allow the edit and retain UNKNOWN semantics.
        if event == "PreToolUse" and tool in FILES and exc.code == "CLAIM_CONFLICT" and not exc.operational and exc.details.get("conflicts"):
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                    "permissionDecisionReason": str(exc) + " " + json.dumps(exc.details)}}
        return unavailable(session, state)
    except Exception:
        return unavailable(session, state)


if __name__ == "__main__":
    try:
        result = main()
        if result:
            print(json.dumps(result, ensure_ascii=True))
    except Exception:
        # Payload/adapter failures never deny an edit or create state.
        pass
