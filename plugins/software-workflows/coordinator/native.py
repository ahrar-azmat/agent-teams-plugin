"""Native executable, strict outbound contracts and RFC 6455 over private UDS."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import time

from .client import CoordinatorError


def environment(**overrides):
    # These are the CALLER's tool-routing identities, not user configuration.
    # Inheriting them can route a nested CLI's tools through the caller and bypass
    # this private listener's hooks and lifecycle (measured on 0.163.0).
    result = {key: value for key, value in os.environ.items() if key not in
              {"CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "SOFTWARE_WORKFLOWS_OPERATION"}}
    result.update(overrides)
    return result


def startup_diagnostic(line):
    """Keep actionable error classes and fingerprints, never arbitrary log text."""
    lowered = line.lower()
    signatures = {"address_in_use": b"address already in use", "path_too_long": b"path too long",
                  "permission_denied": b"permission denied", "missing_path": b"no such file",
                  "invalid_argument": b"invalid argument", "invalid_config": b"error loading config",
                  "panic": b"panicked", "unsupported_option": b"unexpected argument"}
    return {"bytes": len(line), "sha256": hashlib.sha256(line).hexdigest(),
            "classes": [name for name, pattern in signatures.items() if pattern in lowered]}


def adapter_digest():
    from .store import digest
    return digest({p.name: file_digest(p) for p in Path(__file__).parent.glob("*.py")})


def policy_trace_digest(value):
    """Normalize only the two unordered maps in the measured 0.163 Policy trace.

    Rust HashMap debug order varies even between loads in one listener. Keep
    each rule and its ordered pattern/list byte-for-byte. An unfamiliar trace
    retains its raw digest (a conservative cache miss), never a guessed policy.
    """
    from .store import digest
    if not isinstance(value, str) or not value.startswith("Policy { ") or not value.endswith(" }"):
        return digest(value)

    def split(text):
        parts, stack, quoted, escaped, start = [], [], False, False, 0
        closing = {"}": "{", "]": "[", ")": "("}
        for index, char in enumerate(text):
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "{[(":
                stack.append(char)
            elif char in closing:
                if not stack or stack.pop() != closing[char]:
                    raise ValueError("unbalanced policy")
            elif char == "," and not stack:
                parts.append(text[start:index].strip())
                start = index + 1
        if quoted or stack:
            raise ValueError("incomplete policy")
        tail = text[start:].strip()
        return parts + ([tail] if tail else [])

    try:
        fields = [part.split(": ", 1) for part in split(value[9:-2])]
        if len(fields) != 3 or {key for key, _ in fields} != {"rules_by_program", "network_rules", "host_executables_by_name"}:
            return digest(value)
        result = dict(fields)
        for key in ("rules_by_program", "host_executables_by_name"):
            mapping = result[key]
            if not mapping.startswith("{") or not mapping.endswith("}"):
                return digest(value)
            entries = split(mapping[1:-1])
            if any(not entry.startswith('"') for entry in entries):
                return digest(value)
            result[key] = sorted(entries)
        return digest({"format": "codex-0.163-policy", "policy": result})
    except ValueError:
        return digest(value)


METHODS = {
    "initialize": "InitializeParams", "thread/start": "ThreadStartParams",
    "thread/resume": "ThreadResumeParams", "thread/read": "ThreadReadParams",
    "thread/list": "ThreadListParams",
    "thread/loaded/list": "ThreadLoadedListParams", "turn/start": "TurnStartParams",
    "turn/steer": "TurnSteerParams", "turn/interrupt": "TurnInterruptParams",
    "thread/turns/list": "ThreadTurnsListParams", "thread/items/list": "ThreadItemsListParams",
    "thread/queue/add": "ThreadQueueAddParams", "thread/queue/list": "ThreadQueueListParams",
    "thread/queue/start": "ThreadQueueStartParams",
    "thread/queue/delete": "ThreadQueueDeleteParams",
    "thread/backgroundTerminals/list": "ThreadBackgroundTerminalsListParams",
    "thread/backgroundTerminals/terminate": "ThreadBackgroundTerminalsTerminateParams",
    "experimentalFeature/list": "ExperimentalFeatureListParams", "config/read": "ConfigReadParams",
    "configRequirements/read": "ConfigRequirementsReadParams", "hooks/list": "HooksListParams",
    "model/list": "ModelListParams", "mcpServerStatus/list": "ListMcpServerStatusParams",
}
# Bootstrap contracts are deliberately narrower than the vendor protocol.
FIELDS = {
    "initialize": ({"clientInfo", "capabilities"}, {"clientInfo"}),
    "thread/start": ({"cwd", "model", "modelProvider", "approvalPolicy", "sandbox", "config",
                      "dynamicTools", "developerInstructions", "ephemeral", "historyMode"}, set()),
    "thread/resume": ({"threadId", "cwd", "model", "modelProvider", "approvalPolicy", "sandbox",
                       "config", "developerInstructions"}, {"threadId"}),
    "thread/read": ({"threadId", "includeTurns"}, {"threadId"}),
    "thread/list": ({"cursor", "limit", "ancestorThreadId", "archived", "sortDirection"}, set()),
    "turn/start": ({"threadId", "input", "clientUserMessageId", "approvalPolicy", "sandboxPolicy"}, {"threadId", "input"}),
    "turn/steer": ({"threadId", "expectedTurnId", "input", "clientUserMessageId"}, {"threadId", "expectedTurnId", "input"}),
    "turn/interrupt": ({"threadId", "turnId"}, {"threadId", "turnId"}),
    "thread/queue/add": ({"threadId", "input", "clientUserMessageId"}, {"threadId", "input", "clientUserMessageId"}),
    "thread/queue/list": ({"threadId", "cursor", "limit"}, {"threadId"}),
    "thread/queue/start": ({"threadId"}, {"threadId"}),
    "thread/queue/delete": ({"threadId", "queuedSubmissionId"}, {"threadId", "queuedSubmissionId"}),
    "thread/turns/list": ({"threadId", "cursor", "limit", "sortDirection", "itemsView"}, {"threadId"}),
    "thread/items/list": ({"threadId", "turnId", "cursor", "limit", "sortDirection"}, {"threadId"}),
    "thread/loaded/list": ({"cursor", "limit"}, set()),
    "thread/backgroundTerminals/list": ({"threadId"}, {"threadId"}),
    "thread/backgroundTerminals/terminate": ({"threadId", "processId"}, {"threadId", "processId"}),
    "experimentalFeature/list": ({"cursor", "limit"}, set()),
    "config/read": ({"cwd", "includeLayers"}, set()),
    "configRequirements/read": (set(), set()), "hooks/list": ({"cwds"}, set()),
    "model/list": ({"cursor", "limit", "includeHidden"}, set()),
    "mcpServerStatus/list": ({"cursor", "limit", "threadId", "serverName", "detail"}, set()),
}


def native_codex(value=None):
    value = value or os.environ.get("SOFTWARE_WORKFLOWS_CODEX") or shutil.which("codex")
    if not value:
        raise CoordinatorError("CODEX_MISSING", "Native Codex executable was not found")
    path = Path(value).resolve()
    if path.open("rb").read(2) != b"#!":
        return str(path)
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    if not arch or not system:
        raise CoordinatorError("CODEX_PLATFORM", "Cannot resolve native launcher for this platform")
    npm_arch = "arm64" if arch == "aarch64" else "x64"
    triple = arch + ("-apple-darwin" if system == "darwin" else "-unknown-linux-musl")
    package = path.parent.parent
    for candidate in (package / "node_modules" / "@openai" / ("codex-" + system + "-" + npm_arch) /
                      "vendor" / triple / "bin" / "codex", package / "vendor" / triple / "bin" / "codex"):
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.open("rb").read(2) != b"#!":
            return str(candidate)
    raise CoordinatorError("CODEX_LAUNCHER", "Refusing a launcher whose native child cannot be resolved")


def file_digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(toml(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
    raise TypeError("Unsupported TOML override")


def override_args(config):
    return [arg for key, value in config.items() for arg in ("-c", key + "=" + toml(value))]


def object_keys(value, allowed, required=(), at="params"):
    if not isinstance(value, dict):
        raise CoordinatorError("OUTBOUND_TYPE", at + " must be an object")
    if set(value) - set(allowed) or set(required) - set(value):
        raise CoordinatorError("OUTBOUND_FIELDS", "Unknown or missing outbound fields at " + at,
                               {"unknown": sorted(set(value) - set(allowed)), "missing": sorted(set(required) - set(value))})


def validate(method, params):
    if method not in FIELDS:
        raise CoordinatorError("OUTBOUND_METHOD", "Unsupported outbound method", {"method": method})
    allowed, required = FIELDS[method]
    object_keys(params, allowed, required)
    if "clientInfo" in params:
        object_keys(params["clientInfo"], {"name", "title", "version"}, {"name", "version"}, "clientInfo")
    if "capabilities" in params:
        object_keys(params["capabilities"], {"experimentalApi", "optOutNotificationMethods"}, at="capabilities")
    for item in params.get("input", []):
        kind = item.get("type")
        field = {"text": "text", "localImage": "path", "image": "url"}.get(kind)
        if field is None:
            raise CoordinatorError("OUTBOUND_INPUT", "Unsupported native input type")
        object_keys(item, {"type", field, "text_elements"} if kind == "text" else {"type", field}, {"type", field}, "input")
        if not isinstance(item[field], str) or not item[field]:
            raise CoordinatorError("OUTBOUND_INPUT", "Native input content is empty")
    for spec in params.get("dynamicTools", []):
        object_keys(spec, {"type", "name", "description", "inputSchema"},
                    {"type", "name", "description", "inputSchema"}, "dynamicTools")
        if spec["type"] != "function":
            raise CoordinatorError("OUTBOUND_TOOL", "Expected function declaration")


class Schema:
    """Validator for the JSON Schema subset emitted by the installed binary.

    Typed objects reject extras even when Serde's schema permits them. Explicit
    maps (config, arguments and JSON schemas) stay open.
    """
    def __init__(self, directory):
        self.files = {p.stem: p for p in Path(directory).rglob("*.json")}

    def validate(self, method, params):
        validate(method, params)
        if method == "configRequirements/read":
            return  # Native unit-parameter method has no generated Params type.
        name = METHODS[method]
        if name not in self.files:
            raise CoordinatorError("SCHEMA_MISSING", "Installed schema lacks " + name)
        schema = json.loads(self.files[name].read_text())
        self._check(params, schema, schema, "params")

    def _check(self, value, schema, root, at):
        if schema is True:
            return
        if schema is False:
            raise CoordinatorError("SCHEMA_VALUE", "Value disallowed at " + at)
        if "$ref" in schema:
            ref = root
            for part in schema["$ref"].removeprefix("#/").split("/"):
                ref = ref[part]
            return self._check(value, ref, root, at)
        for branch in schema.get("allOf", []):
            self._check(value, branch, root, at)
        choices = schema.get("oneOf", schema.get("anyOf"))
        if choices:
            for branch in choices:
                try:
                    self._check(value, branch, root, at)
                    break
                except CoordinatorError:
                    continue
            else:
                raise CoordinatorError("SCHEMA_UNION", "No schema variant matches at " + at)
        types = schema.get("type")
        if types:
            types = types if isinstance(types, list) else [types]
            matches = {"null": value is None, "object": isinstance(value, dict), "array": isinstance(value, list),
                       "string": isinstance(value, str), "integer": type(value) is int,
                       "number": type(value) in (int, float), "boolean": type(value) is bool}
            if not any(matches.get(t, False) for t in types):
                raise CoordinatorError("SCHEMA_TYPE", "Wrong type at " + at)
        if "enum" in schema and value not in schema["enum"]:
            raise CoordinatorError("SCHEMA_ENUM", "Unknown variant at " + at)
        if "const" in schema and value != schema["const"]:
            raise CoordinatorError("SCHEMA_CONST", "Wrong discriminator at " + at)
        if isinstance(value, dict):
            properties = schema.get("properties")
            if properties is not None:
                object_keys(value, properties, schema.get("required", []), at)
                for key, child in value.items():
                    self._check(child, properties[key], root, at + "." + key)
            elif isinstance(schema.get("additionalProperties"), dict):
                for key, child in value.items():
                    self._check(child, schema["additionalProperties"], root, at + "." + key)
        if isinstance(value, list) and "items" in schema:
            for child in value:
                self._check(child, schema["items"], root, at + "[]")
        if type(value) in (int, float) and "minimum" in schema and value < schema["minimum"]:
            raise CoordinatorError("SCHEMA_RANGE", "Out-of-range number at " + at)


class Connection:
    def __init__(self, reader, writer, on_event, schema=None):
        self.reader, self.writer, self.on_event, self.schema = reader, writer, on_event, schema
        self.pending, self.counter = {}, 0
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.receiver = asyncio.create_task(self.receive())

    @classmethod
    async def connect(cls, path, on_event, schema=None):
        from .runtime import endpoint_info
        endpoint = endpoint_info(path, native=True)
        reader, writer = await asyncio.open_unix_connection(endpoint["resolved"])
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = header.decode("ascii").split("\r\n")
        fields = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        actual_accept = next((line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("sec-websocket-accept:")), None)
        if (lines[0].split()[1] != "101" or fields.get("upgrade") != "websocket" or
                "upgrade" not in fields.get("connection", "").split(", ") or actual_accept != accept):
            writer.close()
            raise CoordinatorError("WEBSOCKET_HANDSHAKE", "Invalid native WebSocket handshake", operational=True)
        return cls(reader, writer, on_event, schema)

    async def frame(self, opcode, payload):
        mask, length = os.urandom(4), len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        async with self.send_lock:
            self.writer.write(header + mask + masked)
            await self.writer.drain()

    async def send(self, message):
        await self.frame(1, json.dumps(message, ensure_ascii=True).encode())

    async def call(self, method, params=None, timeout=60, unchecked=False):
        params = params or {}
        if not unchecked:
            (self.schema.validate if self.schema else validate)(method, params)
        self.counter += 1
        ident = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.send({"id": ident, "method": method, "params": params})
            response = await asyncio.wait_for(future, timeout)
            if "error" in response:
                error = response["error"]
                raise CoordinatorError("NATIVE_RPC", error.get("message", "Native refusal"),
                                       {"method": method, "native_code": error.get("code")})
            return response["result"]
        except asyncio.TimeoutError as exc:
            raise CoordinatorError("NATIVE_UNCERTAIN", "Native RPC result lost; reconcile before retry",
                                   {"method": method, "request": ident}, True) from exc
        finally:
            self.pending.pop(ident, None)

    async def initialize(self, experimental=True):
        result = await self.call("initialize", {"clientInfo": {"name": "software-workflows-coordinator", "version": "2.0.0"},
                                                "capabilities": {"experimentalApi": experimental}})
        await self.send({"method": "initialized"})
        return result

    async def receive(self):
        chunks, fragmented = [], False
        try:
            while True:
                first, second = await self.reader.readexactly(2)
                fin, opcode, length = bool(first & 128), first & 15, second & 127
                if first & 112 or second & 128:
                    raise ValueError("Unexpected extensions or masked server frame")
                if length == 126:
                    length = struct.unpack("!H", await self.reader.readexactly(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
                    if length >> 63:
                        raise ValueError("Invalid frame length")
                if opcode >= 8 and (not fin or length > 125):
                    raise ValueError("Invalid control frame")
                payload = await self.reader.readexactly(length)
                if opcode == 8:
                    await self.frame(8, payload)
                    break
                if opcode == 9:
                    await self.frame(10, payload)
                    continue
                if opcode == 10:
                    continue
                if opcode == 1 and not fragmented:
                    chunks, fragmented = [payload], True
                elif opcode == 0 and fragmented:
                    chunks.append(payload)
                else:
                    raise ValueError("Invalid native data frame")
                if not fin:
                    continue
                message = json.loads(b"".join(chunks).decode("utf-8"))
                chunks, fragmented = [], False
                if "id" in message and "method" not in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        future.set_result(message)
                else:
                    # Callback only journals/enqueues. Never await a hook/tool here.
                    self.on_event(message)
        except (OSError, ValueError, asyncio.IncompleteReadError, MemoryError) as exc:
            self.on_event({"method": "coordinator/transportGap", "params": {"error": type(exc).__name__}})
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CoordinatorError("NATIVE_DISCONNECTED", "Native connection closed", operational=True))

    async def close(self):
        if not self.closed:
            try:
                await self.frame(8, b"")
            except OSError:
                pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        finally:
            if self.receiver is not asyncio.current_task():
                self.receiver.cancel()
                await asyncio.gather(self.receiver, return_exceptions=True)


def policy_echo(response):
    if response.get("approvalPolicy") != "never" or response.get("sandbox", {}).get("type") != "dangerFullAccess":
        raise CoordinatorError("POLICY_MISMATCH", "Native runtime did not honor never / danger-full-access")
    return {"approvalPolicy": response["approvalPolicy"], "sandbox": response["sandbox"]}


async def page(connection, method, params):
    cursor = None
    seen = set()
    while True:
        result = await connection.call(method, dict(params, **({"cursor": cursor} if cursor else {})))
        yield result
        cursor = result.get("nextCursor")
        if not cursor:
            return
        if cursor in seen:
            raise CoordinatorError("HISTORY_CURSOR", "Native pagination repeated a cursor")
        seen.add(cursor)
