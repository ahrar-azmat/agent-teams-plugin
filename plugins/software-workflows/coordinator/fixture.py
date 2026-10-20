"""Loopback Responses SSE provider for product capability admission.

This is not a test runner. It supplies deterministic model responses so admission
can measure native runtime semantics without credentials or external inference.
The CLI accepts a plan on stdin and keeps serving for direct operational runs.
"""
import argparse
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coordinator.client import CoordinatorError


def function(name, arguments, namespace=None):
    result = {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": "fx-" + uuid.uuid4().hex}
    if namespace:
        result["namespace"] = namespace
    return result


def patch(text):
    return {"type": "custom_tool_call", "name": "apply_patch", "input": text, "call_id": "fx-" + uuid.uuid4().hex}


def message(text="done"):
    return {"type": "message", "role": "assistant", "id": "fx-msg-" + uuid.uuid4().hex,
            "content": [{"type": "output_text", "text": text}]}


def action(step):
    if "delay" in step:
        time.sleep(step["delay"])
    if "parallel" in step:
        # One response carrying several tool calls, as a model's parallel calls arrive.
        return [action(s) for s in step["parallel"]]
    if "patch" in step:
        return patch(step["patch"])
    if "exec" in step:
        # A code-mode cell: its JavaScript calls nested tools, as models whose catalog tool_mode is
        # code_mode_only do (0.163: gpt-6-astra).
        return {"type": "custom_tool_call", "name": "exec", "input": step["exec"], "call_id": "fx-" + uuid.uuid4().hex}
    if "command" in step:
        return function("exec_command", {"cmd": step["command"], "login": False, **step.get("arguments", {})})
    if "tool" in step:
        return function(step["tool"], step.get("arguments", {}), step.get("namespace"))
    return message(step.get("text", "done"))


class Provider:
    def __init__(self, router=None, plan=None):
        self.router, self.plan = router, plan or {"default": []}
        self.requests, self.errors, self.routes, self.counts = [], [], {}, {}
        self.lock = threading.Lock()
        provider = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[],"models":[]}')

            def do_POST(self):
                try:
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    body = json.loads(raw)
                    with provider.lock:
                        index = len(provider.requests)
                        provider.requests.append(body)
                    answer = provider.router(body, index) if provider.router else provider.from_plan(body)
                    if answer is None:
                        answer = message()
                    items = answer if isinstance(answer, list) else [answer]
                    rid = "fx-r" + str(index)
                    events = [{"type": "response.created", "response": {"id": rid}}]
                    events.extend({"type": "response.output_item.done", "item": item} for item in items)
                    events.append({"type": "response.completed", "response": {"id": rid, "usage": {
                        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                        "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}})
                    data = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # native stream preemption is expected during delivery probes
                except Exception as exc:
                    provider.errors.append(type(exc).__name__)
                    self.send_error(500, "fixture request failed")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.config = {"model": "gpt-6-astra", "model_provider": "c1_fixture",
                       "model_providers.c1_fixture": {"name": "C1 loopback capability provider",
                            "base_url": "http://127.0.0.1:" + str(self.server.server_port) + "/v1",
                            "wire_api": "responses", "requires_openai_auth": False,
                            "supports_websockets": False, "request_max_retries": 0, "stream_max_retries": 0},
                       "features.plugins": False, "features.shell_snapshot": False}

    def from_plan(self, body):
        base_key = body.get("prompt_cache_key") or str(body.get("metadata", {}))
        messages = json.dumps([i for i in body.get("input", []) if i.get("type") in ("message", "agent_message")])
        route = next((name for name in self.plan.get("routes", {}) if name in messages), "default")
        # Native children may share the root's prompt cache key. A distinct task
        # route keeps their scripted Responses streams independent.
        key = str(base_key) + ":" + route
        with self.lock:
            self.routes[key] = route
            index = self.counts.get(key, 0)
            self.counts[key] = index + 1
            steps = self.plan.get("routes", {}).get(route, self.plan.get("default", []))
        if index >= len(steps):
            # A queued coordinator message can be acknowledged deterministically.
            import re
            ids = re.findall(r"C1_MESSAGE (msg_[0-9a-f]{32})", json.dumps(body.get("input", [])))
            already = {i.get("arguments", "") for i in body.get("input", []) if i.get("name") == "ack"}
            for ident in reversed(ids):
                if not any(ident in arguments for arguments in already):
                    return function("ack", {"message": ident})
            return message()
        return action(steps[index])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def scratch_directory(value):
    directory = Path(value).resolve()
    temporary_roots = {Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve()}
    if not any(root in directory.parents for root in temporary_roots) or directory in temporary_roots:
        raise CoordinatorError("FIXTURE_DIRECTORY", "Fixtures require a dedicated directory under the OS temporary directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise CoordinatorError("FIXTURE_PERMISSIONS", "Fixture directory must be owned and private")
    return directory


def runtime_configuration(directory, run):
    directory = scratch_directory(directory)
    record = json.loads((directory / "fixture.json").read_text())
    from .processes import identity
    process = identity(record["pid"])
    if not process or process["birth"] != record["birth"]:
        raise CoordinatorError("FIXTURE_DEAD", "Fixture provider is not the recorded live process")
    config = record["config"]
    provider = config.get("model_providers.c1_fixture", {})
    if not provider.get("base_url", "").startswith("http://127.0.0.1:") or provider.get("requires_openai_auth") is not False or config.get("features.plugins") is not False:
        raise CoordinatorError("FIXTURE_CONFIG", "Fixture must use unauthenticated loopback with plugin sync disabled")
    home = directory / ("home-" + run)
    if record.get("native_home"):
        requested = Path(record["native_home"])
        home = scratch_directory(requested)
        if requested != home or directory not in home.parents:
            raise CoordinatorError("FIXTURE_HOME", "Shared native home must be a physical private directory inside this fixture")
    home.mkdir(mode=0o700, exist_ok=True)
    return home, config


def serve(directory, plan, native_home=None):
    directory = scratch_directory(directory)
    from .processes import identity
    with Provider(plan=plan) as provider:
        manifest = {"pid": os.getpid(), "birth": identity(os.getpid())["birth"], "config": provider.config}
        if native_home:
            home = scratch_directory(native_home)
            if Path(native_home) != home or directory not in home.parents:
                raise CoordinatorError("FIXTURE_HOME", "Shared native home must be inside this fixture")
            manifest["native_home"] = str(home)
        (directory / "fixture.json").write_text(json.dumps(manifest))
        os.chmod(directory / "fixture.json", 0o600)
        print(json.dumps({"fixture": str(directory), "pid": os.getpid(), "port": provider.server.server_port}), flush=True)
        stopped = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stopped.set())
        stopped.wait()
        (directory / "observations.json").write_text(json.dumps({"requests": len(provider.requests), "errors": provider.errors, "routes": provider.routes}))
    return 0


def calibration_hook(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--mode", required=True, choices=["observe", "deny", "wait", "crash", "timeout"])
    args = parser.parse_args(argv)
    directory = scratch_directory(args.directory)
    payload = json.load(sys.stdin)
    with (directory / "hook-events.jsonl").open("a") as stream:
        stream.write(json.dumps({"payload": payload, "time": time.time()}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if args.mode == "crash":
        os._exit(1)
    if args.mode == "timeout":
        time.sleep(5)
    if args.mode == "wait":
        time.sleep(0.5)
    if args.mode == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "C1 calibration denial"}}))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        raise SystemExit(calibration_hook(sys.argv[2:]))
    raise SystemExit("Use python -m coordinator fixture serve")
