"""Python 3.9 observational gate API: never starts or registers anything.

Import query_checkout(cwd, session_id, timeout=0.25, state=None), or run
python3 -B -m coordinator.query CWD --session-id HOST_SESSION_ID.
"""
import argparse
import json
import time

from .client import Client, CoordinatorError


def query_checkout(cwd, session_id="", timeout=0.25, state=None, git_common=None):
    started = time.monotonic()
    params = {"cwd": str(cwd), "session_id": session_id}
    if git_common is not None:
        params["git_common"] = str(git_common)  # answered under "repository"; a release before 2.4.0 ignores it
    try:
        value = Client(state=state, timeout=timeout).call("checkout/query", params)
        if not isinstance(value, dict) or not {"available", "fresh", "status", "registered", "checkout", "units", "unknown", "own_unit"} <= set(value):
            raise CoordinatorError("RPC_RESPONSE", "Malformed checkout observation", operational=True)
    except (OSError, ValueError, KeyError, CoordinatorError) as exc:
        value = {"available": False, "fresh": False, "generation": None, "registered": None,
                 "status": "coordinator_down", "checkout": None, "units": [], "unknown": [], "own_unit": None,
                 "reason": getattr(exc, "code", type(exc).__name__)}
    for key, default in (("root", None), ("projects", []), ("publications", [])):
        value.setdefault(key, default)
    value["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cwd")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--state")
    parser.add_argument("--timeout", type=float, default=0.25)
    args = parser.parse_args()
    value = query_checkout(args.cwd, args.session_id, args.timeout, args.state)
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))
    return 0 if value["available"] and value["fresh"] and value["status"] != "identity_changed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
