"""Probe-owned hook and cooperative CAS/window coordinator, never product code."""
import sys
sys.dont_write_bytecode = True

import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import time

from _common import append_json, states, write_json


def receipt(manifest, **record):
    append_json(manifest["log"], {"time_ns": time.monotonic_ns(), "pid": os.getpid(), **record})


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": "deny", "permissionDecisionReason": reason}}), flush=True)


def apply_cas(manifest, patch):
    repo = Path(manifest["repo"])
    # This fixture has a deliberately narrow domain: declared regular-file operations.
    declared = []
    for line in patch.splitlines():
        for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "):
            if line.startswith(prefix):
                declared.append(line[len(prefix):])
    if not declared or any(p not in manifest["paths"] or Path(p).is_absolute() or ".." in Path(p).parts for p in declared):
        receipt(manifest, event="cas", outcome="outside_manifest", declared=declared)
        return "outside_manifest"
    with open(manifest["lock"], "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        operations = Path(manifest["operations"])
        seen = json.loads(operations.read_text()) if operations.exists() else {}
        op = manifest["operation_id"]
        before = states(repo, manifest["paths"])
        if op in seen:
            receipt(manifest, event="cas", outcome="duplicate", operation_id=op, before=before)
            return "duplicate"
        if before != manifest["expected"]:
            receipt(manifest, event="cas", outcome="stale_preimage", operation_id=op,
                    expected=manifest["expected"], before=before)
            return "stale_preimage"
        argv = [manifest["native"], "--codex-run-as-apply-patch", patch]
        result = subprocess.run(argv, cwd=repo, text=True, capture_output=True, timeout=10)
        outcome = "applied" if result.returncode == 0 else "failed_or_partial"
        seen[op] = outcome
        write_json(operations, seen)
        receipt(manifest, event="cas", operation_id=op, outcome=outcome,
                before=before, after=states(repo, manifest["paths"]), argv=argv,
                exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                guarantee="Cooperative lock only; hash-check/write is not atomic against unrelated processes.")
        return outcome


def window_callback(manifest, payload):
    port = int(Path(manifest["window_port"]).read_text())
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall((json.dumps({"event": payload["hook_event_name"],
                                 "tool_use_id": payload.get("tool_use_id"),
                                 "tool_name": payload.get("tool_name")}) + "\n").encode())
        answer = conn.makefile("rb").readline()
        if answer != b"ok\n":
            raise RuntimeError("Window coordinator did not acknowledge")


def hook(path, mode, label):
    manifest = json.loads(Path(path).read_text())
    payload = json.load(sys.stdin)
    receipt(manifest, event="hook", label=label, mode=mode, payload=payload)
    if mode == "observe":
        print("{}")
    elif mode == "deny":
        deny("U0_DENIED")
    elif mode == "rewrite":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                                "updatedInput": {"command": manifest["replacement"]}}}))
    elif mode in ("apply-and-deny", "apply-and-crash"):
        outcome = apply_cas(manifest, payload["tool_input"]["command"])
        if mode == "apply-and-crash":
            os.kill(os.getpid(), signal.SIGKILL)
        deny("U0_COORDINATOR " + outcome + "; operation=" + manifest["operation_id"] + "; native apply suppressed")
    elif mode == "crash":
        os.kill(os.getpid(), signal.SIGKILL)
    elif mode == "exit1":
        return 1
    elif mode == "exit2-empty":
        return 2
    elif mode == "exit2-reason":
        print("U0_EXIT2_DENY", file=sys.stderr)
        return 2
    elif mode == "malformed":
        print('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"INVALID"}}')
    elif mode == "timeout":
        time.sleep(3)
        print("{}")
    elif mode == "window":
        window_callback(manifest, payload)
        print("{}")
    else:
        raise ValueError(mode)
    return 0


def serve(path):
    manifest = json.loads(Path(path).read_text())
    held = {}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            key = request.get("tool_use_id")
            if request["event"] == "PreToolUse":
                lock = open(manifest["lock"], "a+")
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held[key] = lock
                receipt(manifest, event="window_acquired", callback=request)
            else:
                lock = held.pop(key, None)
                receipt(manifest, event="window_released", had_lock=lock is not None, callback=request)
                if lock:
                    lock.close()
            self.wfile.write(b"ok\n")

    # Serial requests are sufficient for one fixture command at a time. Missing post
    # leaves the lock held until explicit fixture teardown, never a lease timeout.
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        Path(manifest["window_port"]).write_text(str(server.server_address[1]))
        server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1] == "hook":
        raise SystemExit(hook(*sys.argv[2:]))
    if sys.argv[1] == "serve":
        serve(sys.argv[2])
