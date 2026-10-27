"""Operational CLI. Exit 0: completed; 2: refusal; 3: operational failure."""
import argparse
import json
import sys
import uuid

from .client import Client, CoordinatorError, state_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", help="External private state directory")
    sub = parser.add_subparsers(dest="command", required=True)
    daemon = sub.add_parser("daemon", help="Start/discover/status/stop the independent daemon")
    daemon.add_argument("action", choices=["start", "status", "stop", "serve", "worker", "install-agent", "uninstall-agent"])
    daemon.add_argument("--state", dest="daemon_state")
    daemon.add_argument("--park", action="store_true", help="Inhibit automatic starts after the drain")
    daemon.add_argument("--consent", action="store_true", help="Explicit consent for the requested LaunchAgent change")
    rpc = sub.add_parser("rpc", help="Call a documented C2 RPC method with JSON params")
    rpc.add_argument("method")
    rpc.add_argument("params", nargs="?", default="{}", help="JSON object, or '-' for stdin")
    rpc.add_argument("--key", help="Stable idempotency key; reuse after reconciling a lost response")
    rpc.add_argument("--timeout", type=float, default=180)
    admission = sub.add_parser("admission", help="Run native capability admission")
    admission.add_argument("--scratch", required=True)
    admission.add_argument("--probe", action="append", default=[])
    fixture = sub.add_parser("fixture", help="Local product capability fixture; no model/network spend")
    fixture.add_argument("action", choices=["serve"])
    fixture.add_argument("--directory", required=True)
    fixture.add_argument("--native-home", help="Shared disposable native home inside the fixture directory")
    fixture.add_argument("--plan", default="-", help="JSON fixture plan from stdin or literal JSON")
    args = parser.parse_args(argv)
    state = getattr(args, "daemon_state", None) or args.state
    try:
        if args.command == "daemon" and args.action in ("serve", "worker"):
            from .daemon import supervise, worker
            try:
                return (supervise if args.action == "serve" else worker)(state_root(state))
            except CoordinatorError as exc:
                # A detached start prints to nowhere: the starting client reads why from here, instead
                # of timing out as DAEMON_START (a socket path over macOS's 104 bytes, measured 2.3.0).
                # Never into a folder that failed, or might fail, the state checks themselves.
                import time
                from . import runtime
                from .client import validate_state
                try:
                    if not exc.code.startswith("STATE_"):
                        runtime.atomic_json(validate_state(state_root(state)) / "startup-error.json",
                                            {"time": time.time(), "error": exc.envelope()})
                except Exception:
                    pass
                raise
        if args.command == "fixture":
            from .fixture import serve
            plan = json.load(sys.stdin) if args.plan == "-" else json.loads(args.plan)
            return serve(args.directory, plan, args.native_home)
        client = Client(state)
        if args.command == "daemon":
            if args.action in ("install-agent", "uninstall-agent"):
                from .launch_agent import manage
                value = manage(state_root(state), args.action.removesuffix("-agent"), consent=args.consent)
            elif args.action == "start":
                value = client.resume()
            elif args.action == "status":
                value = client.call("daemon/status")
            else:
                value = client.stop(park=args.park)
        elif args.command == "admission":
            client.ensure()
            client.timeout = 1800
            value = client.call("admission/check", {"scratch": args.scratch, "probes": args.probe}, key=uuid.uuid4().hex)
        else:
            client.ensure()
            client.timeout = args.timeout
            params = json.load(sys.stdin) if args.params == "-" else json.loads(args.params)
            value = client.call(args.method, params, key=args.key or uuid.uuid4().hex)
        print(json.dumps({"ok": True, "result": value}, ensure_ascii=True, sort_keys=True))
        return 0
    except CoordinatorError as exc:
        print(json.dumps({"ok": False, "error": exc.envelope()}, ensure_ascii=True))
        return 3 if exc.operational else 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": {"kind": "failure", "code": type(exc).__name__, "message": "CLI operation failed"}}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
