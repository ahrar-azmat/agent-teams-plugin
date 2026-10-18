#!/usr/bin/env python3
"""Fixed Python 3.9 hook client. No actor/run/unit appears in its command line."""
import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coordinator.client import Client, CoordinatorError


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason or "Coordinator refused this operation"}}


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
        result = Client(args.state, timeout=115).call("hook/dispatch", {"payload": payload, "process": identity(os.getpid())})
        print(json.dumps(result), flush=True)
    except Exception as exc:
        # Native hook timeout/crash is fail-open. An explicit valid denial is the
        # best response while this client is still alive; the event ledger still
        # detects a missing successful native lifecycle if the process dies.
        code = exc.code if isinstance(exc, CoordinatorError) else type(exc).__name__
        print(json.dumps(deny("Coordinator unavailable or refused (" + code + "); retry after reconciliation")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
