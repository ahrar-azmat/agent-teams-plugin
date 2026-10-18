"""Local Responses SSE fixture; never proxies requests or records HTTP headers."""
import sys
sys.dont_write_bytecode = True

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import uuid

from _common import require


def patch_call(patch):
    return {"type": "custom_tool_call", "name": "apply_patch", "input": patch, "call_id": "u0-" + uuid.uuid4().hex}


def function_call(name, arguments):
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": "u0-" + uuid.uuid4().hex}


def shell_call(command, **kwargs):
    return function_call("exec_command", {"cmd": command, "login": False, **kwargs})


def mcp_call(name, arguments):
    # The installed code-mode host exposes MCP through the exec bridge. The
    # canonical mcp__ name is the inner hook name, not a direct wire function.
    return {"type": "custom_tool_call", "name": "exec",
            "input": "text(await tools." + name + "(" + json.dumps(arguments) + "));",
            "call_id": "u0-" + uuid.uuid4().hex}


def continue_session(request, chars="", yield_time_ms=1000):
    inputs = request.get("input", [])
    outputs = [i for i in inputs if i.get("type") in ("function_call_output", "custom_tool_call_output")]
    matches = re.findall(r"[Ss]ession (?:ID|id)[: ]+(\d+)", json.dumps(outputs[-1:]))
    require(matches, "Yielding exec did not expose a session ID")
    return function_call("write_stdin", {"session_id": int(matches[-1]), "chars": chars, "yield_time_ms": yield_time_ms})


class ResponsesFixture:
    def __init__(self, scratch, steps=(), home=None):
        self.home = Path(home) if home else Path(scratch) / ("home-" + uuid.uuid4().hex[:10])
        self.home.mkdir(parents=True, exist_ok=True)
        self.steps, self.requests, self.errors = list(steps), [], []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    body = json.loads(raw)
                    index = len(fixture.requests)
                    fixture.requests.append(body)
                    item = fixture.steps[index] if index < len(fixture.steps) else None
                    if callable(item):
                        item = item(body)
                    if item is None:
                        item = {"type": "message", "role": "assistant", "id": "u0-done",
                                "content": [{"type": "output_text", "text": "done"}]}
                    items = item if isinstance(item, list) else [item]
                    response_id = f"u0-r{index}"
                    events = [{"type": "response.created", "response": {"id": response_id}}]
                    events += [{"type": "response.output_item.done", "item": i} for i in items]
                    events += [{"type": "response.completed", "response": {"id": response_id, "usage": {
                        "input_tokens": 0, "input_tokens_details": None, "output_tokens": 0,
                        "output_tokens_details": None, "total_tokens": 0}}}]
                    data = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as exc:
                    fixture.errors.append(str(exc))
                    self.send_error(500, "U0 fixture error")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.config = {"model": "gpt-5.6-sol", "model_provider": "u0_fixture",
                       "model_providers.u0_fixture": {"name": "U0 deterministic transport",
                       "base_url": f"http://127.0.0.1:{self.server.server_port}/v1", "wire_api": "responses",
                       "requires_openai_auth": False, "supports_websockets": False,
                       "request_max_retries": 0, "stream_max_retries": 0},
                       "features.shell_snapshot": False}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
