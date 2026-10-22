#!/usr/bin/env python3
"""Claude host hooks, Python 3.9. Uninvolved calls only read routing hints."""
import sys
sys.dont_write_bytecode = True
import json
import os
import stat
import time


TOOLS = {"abraham", "code_review", "codex_runs", "codex_run_log", "codex_resume_run", "codex_cancel_run",
         "coordinator_claims", "coordinator_send", "coordinator_inbox", "coordinator_unit", "coordinator_seal", "coordinator_publish"}
FILES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
CALL_SECONDS = 3
# Stay below hooks.json's 15 s including parsing/output. Reserve a full call
# for the retry, and another for Bash's arm; waiting never consumes either.
# PostToolBatch has the same 15 s: it starts a daemon call only while a whole
# one fits before HOOK_SECONDS, so a hung daemon ends it by 14 s (four calls at
# most), and a batch whose calls all settled at their Posts calls nothing.
HOOK_SECONDS = 14
# A lead call the coordinator may hold something of until its Post or the batch settles it leaves a marker here
# (marker): PostToolBatch asks the daemon about those calls only. A marker its session never settled (one ended
# mid-call, or a daemon down at both) is dropped by a later Pre once a day old, a sweep at most hourly: one dropped
# early only leaves its record to its own deadline, as every lead call was left before 2.4.4.
MARKERS = "lead-hooks"
MARKER_AGE_S, SWEEP_S = 24 * 3600, 3600


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


def live_view(tool_input):
    """The live-view tail live_view_nudge.py asks a lead to start: it reads one log file and writes only its own
    output, so it cannot touch a checkout. It is no coordinated call: no record, no scan, no window held."""
    command = (tool_input or {}).get("command")
    if not isinstance(command, str):
        return False
    try:
        import shlex
        from live_view_nudge import LIVE_VIEW_LOG
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except (ImportError, ValueError):
        return False
    tail = LIVE_VIEW_LOG[len("$HOME"):]
    return (len(words) == 3 and words[0] == "tail" and words[1] in ("-F", "-f")
            and words[2] in (LIVE_VIEW_LOG, "${HOME}" + tail, "~" + tail, os.path.expanduser("~") + tail))


def marker(state, payload):
    """The marker of a lead call (MARKERS), named by its session and the host's call id; None without them, or when
    the state is not this user's private directory (the hook never creates or repairs it)."""
    import hashlib
    session, call = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call, str) or not call:
        return None
    try:
        info = os.lstat(state)
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        return None
    return os.path.join(state, MARKERS, hashlib.sha256((session + "\0" + call).encode()).hexdigest())


def mark(path):
    directory = os.path.dirname(path)
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("unusable marker directory")
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600))
    stamp, now = os.path.join(directory, ".swept"), time.time()
    try:
        if now - os.lstat(stamp).st_mtime < SWEEP_S:
            return
    except FileNotFoundError:
        pass
    os.close(os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600))
    os.utime(stamp)
    for name in os.listdir(directory):
        try:
            if not name.startswith(".") and now - os.lstat(os.path.join(directory, name)).st_mtime > MARKER_AGE_S:
                os.unlink(os.path.join(directory, name))
        except OSError:
            pass


def unmark(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def answered(exc):
    """Whether a failed hook call reached a daemon that decided it (a refusal): it holds nothing more of it for the
    batch to settle. A connection failure or a failure the daemon reports leaves that open."""
    from coordinator.client import CoordinatorError
    return isinstance(exc, CoordinatorError) and not exc.operational


def batch(payload, state, deadline):
    """PostToolBatch: every call of the batch has resolved. A lead call no Post hook settled (another hook, a
    permission or the user refused it, or its Post was lost: the host sends neither Post event for a refused call)
    is settled now, one call at a time while the hook's time lasts; any left over keep their deadlines. Only a call
    whose marker is left is asked about: one its Post settled, or one never coordinated, costs a stat."""
    found = []
    for call in payload.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("tool_name") not in FILES | {"Bash"}:
            continue
        data = {key: call[key] for key in ("tool_name", "tool_input", "tool_use_id") if key in call}
        data.update(hook_event_name="PostToolBatch", session_id=payload.get("session_id", ""), cwd=payload.get("cwd", ""))
        path = marker(state, data)
        if path and os.path.lexists(path):
            found.append((data, path))
    if not found:
        return None
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    try:
        from coordinator.client import Client
        client = Client(state, timeout=CALL_SECONDS)
    except Exception:
        return None
    for data, path in found:
        if time.monotonic() > deadline - CALL_SECONDS:
            break
        try:
            client.call("lead-hook/dispatch", {"payload": data})
        except Exception as exc:
            if not answered(exc):
                continue  # its deadline still ends what it holds
        unmark(path)
    return None


def replacement_call(client, method, params, deadline, reserve=0):
    from coordinator.client import CoordinatorError, _held
    try:
        return client.call(method, params)
    except (OSError, CoordinatorError) as exc:
        if isinstance(exc, CoordinatorError) and exc.code not in ("DAEMON_DRAINING", "RPC_UNCERTAIN", "RECOVERING"):
            raise
        lock = client.state / "upgrade.lock"
        if not _held(lock):
            raise  # No upgrade owner: today's unavailable path, without waiting.
        until = deadline - CALL_SECONDS - reserve
        while _held(lock) and time.monotonic() < until:
            time.sleep(min(0.05, max(0, until - time.monotonic())))
        if _held(lock) or time.monotonic() > until:
            raise
        # Same host identity, one retry. It may acknowledge an already prepared
        # intent, but cannot turn a lost/restarted Bash window into an armed one.
        return client.call(method, params)


def main():
    deadline = time.monotonic() + HOOK_SECONDS
    payload = json.load(sys.stdin)
    event, tool, session = payload.get("hook_event_name"), payload.get("tool_name", ""), payload.get("session_id", "")
    short = tool.split("__")[-1]
    if event == "PreToolUse" and tool.startswith("mcp__") and any(n in tool for n in ("software-workflows", "codex-oracle")) and short in TOOLS:
        if not session:
            return None
        updated = dict(payload.get("tool_input") or {})
        updated["session_id"] = session
        if isinstance(payload.get("cwd"), str) and os.path.isabs(payload["cwd"]):
            updated["dispatch_cwd"] = payload["cwd"]
        return {"hookSpecificOutput": {"hookEventName": event, "updatedInput": updated}}
    state = os.path.abspath(os.path.expanduser(os.environ.get("SOFTWARE_WORKFLOWS_STATE") or "~/.local/state/software-workflows"))
    if event == "PostToolBatch":
        return batch(payload, state, deadline)
    if tool not in FILES | {"Bash"} or tool == "Bash" and live_view(payload.get("tool_input")):
        return None
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
        client = Client(state, timeout=CALL_SECONDS)
        bash_pre = tool == "Bash" and event == "PreToolUse"
        pending = marker(state, payload)
        if pending and event == "PreToolUse":
            try:
                mark(pending)  # before the call: the daemon may hold something of it even when its answer is lost
            except OSError:
                pending = None
        try:
            result = replacement_call(client, "lead-hook/dispatch", {"payload": data}, deadline,
                                      reserve=CALL_SECONDS if bash_pre else 0)
        except Exception as exc:
            if answered(exc) or event == "PreToolUse" and isinstance(exc, (FileNotFoundError, ConnectionRefusedError)):
                unmark(pending)  # decided, or a Pre that never reached a daemon: nothing of it is held
            raise
        if not isinstance(result, dict):
            return unavailable(session, state)
        if event != "PreToolUse" or not result.get("coordinated", True):
            unmark(pending)  # its Post settled it, or nothing of it is held
        token = result.pop("arm", None) if tool == "Bash" and event == "PreToolUse" else None
        if token:
            # The answer came before the host runs the command: only that makes the coordinator's
            # record a state from before it. A lost initial Pre reply cannot arm;
            # a lost confirmation reply may already have persisted the arm.
            try:
                armed = replacement_call(client, "lead-hook/armed", {"observed": result.get("observed"), "arm": token}, deadline)
                result["armed"] = armed["armed"] if isinstance(armed, dict) and type(armed.get("armed")) is bool else "uncertain"
            except Exception:
                result["armed"] = "uncertain"
        elif tool == "Bash" and event == "PreToolUse":
            result["armed"] = False
        if not result.get("coordinated", True):
            return None
        note = ""
        if bash_pre and result.get("published_participants"):
            note = (" Changes in these participants, already published or being published, are UNKNOWN; new edits"
                    " require a new unit: " + json.dumps(result["published_participants"]))
        return {"hookSpecificOutput": {"hookEventName": event,
                "additionalContext": "Coordinator lead operation: " + json.dumps(result, ensure_ascii=True) + note}}
    except CoordinatorError as exc:
        # Only authoritative refusals may deny: a claim conflict, and a repository whose part of the
        # unit is already published (an edit there could only become UNKNOWN). Infrastructure failures
        # and uncalibrated adapters allow the edit and retain UNKNOWN semantics.
        published = exc.code == "PREFIX_PUBLISHED" and not exc.operational
        if event == "PreToolUse" and tool in FILES and (published or exc.code == "CLAIM_CONFLICT" and not exc.operational
                                                       and exc.details.get("conflicts")):
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                    "permissionDecisionReason": str(exc) + " " + json.dumps(exc.details)}}
        if published:  # a Bash call may only read: it runs, and what it changes there is UNKNOWN
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "Coordinator refused to record this call: " + str(exc) + " " + json.dumps(exc.details)
                    + ". Whatever it changes in that repository is UNKNOWN."}}
        if answered(exc):  # the daemon decided it (OPERATION_BUSY, LEAD_HOOK_UNCERTAIN): a refusal, not unavailability
            return {"systemMessage": "Coordinator refused to record this call (" + exc.code + "): " + str(exc) +
                    ". What it changes is recorded UNKNOWN."}
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
