#!/usr/bin/env python3
"""Fixed Python 3.9 hook client. No actor/run/unit appears in its command line."""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coordinator.client import Client, CoordinatorError

# Codex ends a native hook this long after spawning it and then runs the call anyway (measured on
# 0.163: hook/completed "failed", "hook timed out after 120s", the command executed). hook_config
# gives Codex this timeout. The client answers CLIENT_MARGIN_S before it, counted from its own birth:
# under load an interpreter started its call over 5 s late (2026-10-19, seven writers), and a deny
# printed after Codex's timeout is no deny. The coordinator decides ANSWER_MARGIN_S before the
# client's deadline, so its answer still arrives.
NATIVE_HOOK_SECONDS = 120
CLIENT_MARGIN_S = 8
ANSWER_MARGIN_S = 4


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason or "Coordinator refused this operation"}}


def born(process):
    """Wall-clock birth of this process: Codex's timer starts when it spawns the hook."""
    try:
        if sys.platform == "darwin":
            seconds, micros = process["birth"].split(":")
            return int(seconds) + int(micros) / 1e6
        with open("/proc/uptime") as stream:
            uptime = float(stream.read().split()[0])
        return time.time() - uptime + int(process["birth"]) / os.sysconf("SC_CLK_TCK")
    except (KeyError, TypeError, ValueError, OSError):
        return time.time()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state")
    parser.add_argument("--check", action="store_true", help="Check Python/client compatibility without contacting a daemon")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"compatible": sys.version_info >= (3, 9), "protocol": "jsonl-v1"}))
        return 0
    try:
        payload = json.load(sys.stdin)
        from coordinator.processes import identity
        process = identity(os.getpid())
        deadline = born(process) + NATIVE_HOOK_SECONDS - CLIENT_MARGIN_S
        result = Client(args.state, timeout=NATIVE_HOOK_SECONDS, deadline=deadline).call(
            "hook/dispatch", {"payload": payload, "process": process, "deadline": deadline})
        print(json.dumps(result), flush=True)
    except Exception as exc:
        # Native hook timeout/crash is fail-open. An explicit valid denial is the
        # best response while this client is still alive; the event ledger still
        # detects a missing successful native lifecycle if the process dies.
        if isinstance(exc, (TimeoutError, socket.timeout)):
            reason = "Coordinator did not answer before the hook deadline (queued behind other calls), so nothing ran; retry this call"
        else:
            code = exc.code if isinstance(exc, CoordinatorError) else type(exc).__name__
            reason = "Coordinator unavailable or refused (" + code + "); retry after reconciliation"
        print(json.dumps(deny(reason)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
