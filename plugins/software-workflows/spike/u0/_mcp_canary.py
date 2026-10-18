"""Newline-delimited stdio MCP fixture, restricted to manifest-owned targets."""
import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import time

from _common import append_json


def serve(path):
    manifest = json.loads(Path(path).read_text())
    def log(event, **fields):
        append_json(manifest["mcp_log"], {"event": event, "time_ns": time.monotonic_ns(), "pid": os.getpid(), **fields})

    for line in sys.stdin:
        request = json.loads(line)
        method, ident = request.get("method"), request.get("id")
        if ident is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "u0-canary", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": "write_marker", "description": "Write only a U0 synthetic fixture marker.",
                       "inputSchema": {"type": "object", "properties": {
                           "fixture_id": {"type": "string"}, "data": {"type": "string"},
                           "mode": {"type": "string", "enum": ["success", "delayed", "error"]}},
                           "required": ["fixture_id", "data", "mode"], "additionalProperties": False}}]}
        elif method == "tools/call":
            params = request["params"]
            args = params.get("arguments", {})
            log("tool_entry", arguments=args)
            if (params.get("name") != "write_marker" or args.get("fixture_id") != manifest["fixture_id"]
                    or args.get("mode") not in ("success", "delayed", "error") or len(args.get("data", "")) > 100):
                result = {"content": [{"type": "text", "text": "invalid fixture arguments"}], "isError": True}
            else:
                if args["mode"] == "delayed":
                    time.sleep(0.5)
                Path(manifest["mcp_target"]).write_text(args["data"])
                log("mutation", target=manifest["mcp_target"])
                result = {"content": [{"type": "text", "text": "U0 marker written"}], "isError": args["mode"] == "error"}
            log("tool_return", is_error=result["isError"])
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "unknown method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)


if __name__ == "__main__":
    serve(sys.argv[1])
