"""Native executable, strict outbound contracts and RFC 6455 over private UDS."""
import asyncio
from contextlib import contextmanager
import base64
from collections import deque
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import struct
import subprocess
import time

from .client import CoordinatorError, clean_environment


def quiet_status(status):
    """None for a quiet thread, otherwise the native reason that prevents quiescence.

    Thread status is distinct from a saved turn's status. In Codex 0.163,
    systemError follows running/approval/input checks and is eligible for unload.
    An active thread is busy even when its activeFlags list is empty.
    """
    kind = status.get("type") if isinstance(status, dict) else None
    if kind in ("idle", "notLoaded", "systemError"):
        return None
    if kind == "active":
        return "native_active_turn"
    return "native_status_unknown:" + (str(kind) if kind is not None else "missing")


# Count wire JSON at the decode boundary, including late responses. These counters
# contain lengths/timings only; no native payloads or credentials are retained.
DECODE_METRICS = {"json_bytes": 0, "history_json_bytes": 0, "messages": 0,
                  "history_responses": 0, "max_json_bytes": 0, "max_decode_seconds": 0}
HISTORY_METHODS = {"thread/turns/list", "thread/items/list"}

# Notifications no reader of the coordinator needs, which the app-server then never sends on the connection
# (InitializeCapabilities.optOutNotificationMethods, codex 0.163.0, exact method names). turn/diff/updated repeats the
# turn's whole unified diff at every change: on the user's ledger it was 87% of the native bytes journaled in the 6.5 h
# to 10-29 07:32 (895 rows, 535 MiB; ~2 GiB a day) and 2.53 GiB of the 6.97 GiB journaled since 10-19. Application
# ignores it (Daemon.notification has no case for it), and run/events keeps each file's diff in its fileChange item.
OPTED_OUT = ["turn/diff/updated"]

# Threat: a live native peer stops reading, so flushing a close frame never ends.
# Round 12's 100 real 0.163.0 peer close round trips took at most 0.000431 s
# (local flush/close: 0.000218 s). One second gives over 2,300x headroom and
# room for the measured sub-second host stalls. This fixed, non-configurable
# transport grace is not an RPC or recovery deadline; expiry is journaled.
CLOSE_GRACE_S = 1.0

# Threat: a producer can outrun durable receipt indefinitely. Round 18's real
# 27-writer burst (starting load 21.65) measured 800 notifications / 244,416
# bytes per listener, at most 584 bytes each. 512 KiB holds over two such
# bursts; 64 entries fit a 40-event turn plus 24 more, and 64 KiB exceeds
# 64 * 584 bytes. These are buffering/grouping bounds, never message rejection
# limits: one larger message gets an exclusive handoff after earlier input.
# Collection never waits to fill a batch. Its 2 ms age/collection budget is
# also the daemon's shared scheduling slice; host/SQLite stalls can exceed it.
# Five initial real runs at mean loads 40.24-54.50 averaged 0.93-1.06 ms per
# batch (maximum 142.73 ms), with a peak of 11,013 queued bytes / 37 entries
# and 41 committed application entries per listener. Thus 2 ms allows roughly
# two average batches per shared slice; the count/byte bounds retain headroom
# for a whole measured turn. Final acceptance measurements are in README.md.
JOURNAL_QUEUE_BYTES = 512 * 1024
JOURNAL_BATCH_COUNT = 64
JOURNAL_BATCH_BYTES = 64 * 1024
JOURNAL_BATCH_AGE_S = 0.002


def local_resource_failure(error):
    """Unavailable local storage/capacity is not contrary native evidence.

    SQLite IOERR does not reliably retain the underlying ENOSPC on APFS. It
    therefore stops this local consumer as an unavailable ledger; corruption
    and constraint failures keep their existing evidence/reconciliation path.
    Follow wrappers without losing an original OS resource error.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, MemoryError):
            return True
        if isinstance(error, OSError) and error.errno in (
                errno.ENOSPC, errno.EDQUOT, errno.EFBIG, errno.EMFILE, errno.ENFILE, errno.ENOMEM):
            return True
        if isinstance(error, sqlite3.Error) and getattr(error, "sqlite_errorcode", 0) & 255 in (
                sqlite3.SQLITE_FULL, sqlite3.SQLITE_NOMEM, sqlite3.SQLITE_IOERR):
            return True
        error = error.__cause__ or error.__context__
    return False


def environment(**overrides):
    # These are the CALLER's tool-routing identities, not user configuration.
    # Inheriting them can route a nested CLI's tools through the caller and bypass
    # this private listener's hooks and lifecycle (measured on 0.163.0).
    result = {key: value for key, value in os.environ.items() if key not in
              {"CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "SOFTWARE_WORKFLOWS_OPERATION"}}
    result.update(overrides)
    return clean_environment(result)


# A writer's log filter. The policy trace is admission's view of the effective execution policy. Codex 0.163
# writes `codex.tool_result` to the trace-safe target once a call's handler has returned, direct or nested in a
# code-mode cell, with success=false when it returned a failure; that target carries identifiers, names and
# lengths, never arguments or output. Native's session logs each failed rollout append and goes on from its
# in-memory history (core session/mod.rs persist_rollout_items, an error), as its rollout writer logs each failed
# write (rollout/src/recorder.rs, errors and warnings), and the session's handlers log each operation they dispatch
# (a span opened and closed: session/handlers.rs submission_dispatch) and its shutdown: what an app-server saved
# proves an input absent only with this log (rollout.native_mark, runs.locate_saved).
WRITER_LOG = ("codex_core::exec_policy=trace,codex_otel.trace_safe=info,codex_core::session=warn,"
              "codex_core::session::handlers=info,codex_rollout::recorder=warn")


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
    "project/list": "ProjectListParams", "project/read": "ProjectReadParams",
    "initialize": "InitializeParams", "thread/start": "ThreadStartParams",
    "thread/resume": "ThreadResumeParams", "thread/read": "ThreadReadParams",
    "thread/list": "ThreadListParams", "thread/unsubscribe": "ThreadUnsubscribeParams",
    "thread/loaded/list": "ThreadLoadedListParams", "turn/start": "TurnStartParams",
    "turn/steer": "TurnSteerParams", "turn/interrupt": "TurnInterruptParams",
    "thread/turns/list": "ThreadTurnsListParams", "thread/items/list": "ThreadItemsListParams",
    "thread/queue/add": "ThreadQueueAddParams", "thread/queue/list": "ThreadQueueListParams",
    "thread/queue/start": "ThreadQueueStartParams",
    "thread/queue/delete": "ThreadQueueDeleteParams", "thread/inject_items": "ThreadInjectItemsParams",
    "thread/backgroundTerminals/list": "ThreadBackgroundTerminalsListParams",
    "thread/backgroundTerminals/terminate": "ThreadBackgroundTerminalsTerminateParams",
    "experimentalFeature/list": "ExperimentalFeatureListParams", "config/read": "ConfigReadParams",
    "plugin/installed": "PluginInstalledParams", "plugin/read": "PluginReadParams",
    "configRequirements/read": "ConfigRequirementsReadParams", "hooks/list": "HooksListParams",
    "model/list": "ModelListParams", "mcpServerStatus/list": "ListMcpServerStatusParams",
}
PROJECT_METHODS = frozenset({"project/list", "project/read"})
BASELINE_METHODS = frozenset(METHODS) - PROJECT_METHODS
# Bootstrap contracts are deliberately narrower than the vendor protocol.
FIELDS = {
    "project/list": ({"cursor", "limit", "sortKey", "sortDirection"}, set()),
    "project/read": ({"projectId"}, {"projectId"}),
    "initialize": ({"clientInfo", "capabilities"}, {"clientInfo"}),
    "thread/start": ({"cwd", "model", "modelProvider", "approvalPolicy", "sandbox", "config",
                      "dynamicTools", "developerInstructions", "ephemeral", "historyMode", "projectId", "runtimeWorkspaceRoots"}, set()),
    "thread/resume": ({"threadId", "cwd", "model", "modelProvider", "approvalPolicy", "sandbox",
                       "config", "developerInstructions", "runtimeWorkspaceRoots", "excludeTurns"}, {"threadId"}),
    "thread/read": ({"threadId", "includeTurns"}, {"threadId"}),
    "thread/list": ({"cursor", "limit", "ancestorThreadId", "archived", "sortDirection"}, set()),
    "thread/unsubscribe": ({"threadId"}, {"threadId"}),
    "turn/start": ({"threadId", "input", "clientUserMessageId", "approvalPolicy", "sandboxPolicy"}, {"threadId", "input"}),
    "turn/steer": ({"threadId", "expectedTurnId", "input", "clientUserMessageId"}, {"threadId", "expectedTurnId", "input"}),
    "turn/interrupt": ({"threadId", "turnId"}, {"threadId", "turnId"}),
    "thread/queue/add": ({"threadId", "input", "clientUserMessageId"}, {"threadId", "input", "clientUserMessageId"}),
    "thread/queue/list": ({"threadId", "cursor", "limit"}, {"threadId"}),
    "thread/queue/start": ({"threadId"}, {"threadId"}),
    "thread/queue/delete": ({"threadId", "queuedSubmissionId"}, {"threadId", "queuedSubmissionId"}),
    # History only: an idle thread records the items at once and starts no turn; a running turn takes them at its next
    # sampling request without preempting a running code-mode cell, which a steer's user input does (measured on
    # 0.163.0, r244/w-lead/logs/inject1.log). Admission's inject probe decides whether a listener may use it.
    "thread/inject_items": ({"threadId", "items"}, {"threadId", "items"}),
    "thread/turns/list": ({"threadId", "cursor", "limit", "sortDirection", "itemsView"}, {"threadId"}),
    "thread/items/list": ({"threadId", "turnId", "cursor", "limit", "sortDirection"}, {"threadId"}),
    "thread/loaded/list": ({"cursor", "limit"}, set()),
    "thread/backgroundTerminals/list": ({"threadId"}, {"threadId"}),
    "thread/backgroundTerminals/terminate": ({"threadId", "processId"}, {"threadId", "processId"}),
    "experimentalFeature/list": ({"cursor", "limit"}, set()),
    "config/read": ({"cwd", "includeLayers"}, set()),
    "plugin/installed": ({"cwds"}, set()),
    "plugin/read": ({"pluginName", "marketplacePath", "remoteMarketplaceName"}, {"pluginName"}),
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
    if method == "project/list":
        if params.get("sortDirection") is not None and params.get("sortKey") is None:
            raise CoordinatorError("OUTBOUND_FIELDS", "project/list sortDirection requires sortKey")
        if params.get("sortKey") not in (None, "position", "recencyAt") or params.get("sortDirection") not in (None, "asc", "desc"):
            raise CoordinatorError("OUTBOUND_FIELDS", "Invalid native project ordering")
    if "runtimeWorkspaceRoots" in params:
        roots = params["runtimeWorkspaceRoots"]
        if not isinstance(roots, list) or not roots or any(not isinstance(p, str) or not os.path.isabs(p) for p in roots):
            raise CoordinatorError("OUTBOUND_FIELDS", "Runtime workspace roots must be nonempty absolute paths")
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
    if method == "thread/inject_items":
        # Raw Responses items reach the model as written: only user text messages are sent.
        if not isinstance(params["items"], list) or not params["items"]:
            raise CoordinatorError("OUTBOUND_INPUT", "Injected items must be a nonempty list")
        for item in params["items"]:
            object_keys(item, {"type", "role", "content"}, {"type", "role", "content"}, "items")
            if item["type"] != "message" or item["role"] != "user" or not isinstance(item["content"], list) or not item["content"]:
                raise CoordinatorError("OUTBOUND_INPUT", "Only user message items are injected")
            for part in item["content"]:
                object_keys(part, {"type", "text"}, {"type", "text"}, "items.content")
                if part["type"] != "input_text" or not isinstance(part["text"], str) or not part["text"]:
                    raise CoordinatorError("OUTBOUND_INPUT", "Injected message content must be nonempty input_text")
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


class Retired(asyncio.CancelledError):
    """This call lost its local connection, not its task's lifetime.

    Evidence consumers leave their durable obligation unapplied. Service loops
    retry the item on a rebound connection; actual Task.cancel still propagates.
    """


class IntakeStopped(Retired):
    """A local intake owner failed; durable consumers must leave work replayable."""
    code = "NATIVE_INTAKE_STOPPED"

    def __init__(self, cause, stage):
        super().__init__(self.code)
        self.cause = cause
        self.details = {"stage": stage, "code": getattr(cause, "code", type(cause).__name__)}


class ResponseWait:
    """One alarm on the connection's active-reader clock, never a polling timer."""
    def __init__(self, connection, future, timeout):
        self.connection, self.future, self.timeout = connection, future, timeout
        self.loop = asyncio.get_running_loop()
        self.started, self.active_started = self.loop.time(), connection.reader_active()
        self.wake = asyncio.Event()
        self.alarm = None

    def elapsed(self):
        wall = self.loop.time() - self.started
        active = self.connection.reader_active() - self.active_started
        return {"wall_seconds": wall, "active_seconds": active, "paused_seconds": max(0, wall - active)}

    def changed(self, unused=None):
        if self.alarm is not None:
            self.alarm.cancel()
            self.alarm = None
        self.wake.set()

    async def result(self):
        self.connection.response_waits.add(self)
        self.future.add_done_callback(self.changed)
        try:
            while True:
                self.connection.check_intake()
                if self.future.done():
                    return self.future.result()
                remaining = None if self.timeout is None else self.timeout - self.elapsed()["active_seconds"]
                if remaining is not None and remaining <= 0:
                    raise asyncio.TimeoutError()
                self.wake.clear()
                if remaining is not None and not self.connection.reader_pause_depth:
                    self.alarm = self.loop.call_later(remaining, self.changed)
                await self.wake.wait()
        finally:
            self.changed()
            self.future.remove_done_callback(self.changed)
            self.connection.response_waits.discard(self)
            measured = self.elapsed()
            self.connection.last_response_wait = measured
            for name, seconds in measured.items():
                key = "wait_" + name
                self.connection.rpc_metrics[key] = self.connection.rpc_metrics.get(key, 0) + seconds


class Journal:
    """One connection's independent durable owner; no application dependencies.

    Bytes include the staged transaction until COMMIT succeeds. The reader can
    additionally hold one decoded message while waiting for capacity; WebSocket
    assembly and transport buffers are separate. An oversized message is handed
    off alone, without truncation, rejection, or an impossible capacity wait.
    """
    def __init__(self, connection, commit, turn):
        self.connection, self.commit, self.turn = connection, commit, turn
        self.queue, self.staged = deque(), []
        self.ready, self.capacity, self.progress = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.received = self.committed = self.bytes = self.waiting_bytes = 0
        self.reader_done = self.done = False
        self.error = None
        self.metrics = dict.fromkeys(("received_bytes", "committed_bytes", "max_queue_bytes", "max_queue_count",
            "max_waiting_bytes", "batches", "max_batch_count", "max_batch_bytes", "batch_seconds",
            "max_batch_seconds", "max_commit_age_seconds", "barrier_waits", "barrier_seconds",
            "max_barrier_seconds", "capacity_waits", "oversized_messages"), 0)
        from .telemetry import detached_context
        self.owner = asyncio.create_task(self.run(), context=detached_context())

    def status(self):
        oldest = self.staged[0][3] if self.staged else self.queue[0][3] if self.queue else None
        return dict(self.metrics, received=self.received, committed=self.committed,
                    pending=self.received - self.committed, queue_bytes=self.bytes,
                    waiting_bytes=self.waiting_bytes,
                    oldest_seconds=max(0, time.monotonic() - oldest) if oldest is not None else 0,
                    failed=self.error is not None)

    async def put(self, message, size):
        self.received += 1
        ordinal, arrived = self.received, time.monotonic()
        self.metrics["received_bytes"] += size
        self.waiting_bytes = size
        self.metrics["max_waiting_bytes"] = max(self.metrics["max_waiting_bytes"], size)
        queued = False
        if self.bytes and self.bytes + size > JOURNAL_QUEUE_BYTES:
            self.metrics["capacity_waits"] += 1
        try:
            while self.bytes and self.bytes + size > JOURNAL_QUEUE_BYTES:
                self.check_open()
                self.capacity.clear()
                with self.connection.pause_reader("capacity"):
                    await self.capacity.wait()
            self.check_open()
            if size > JOURNAL_QUEUE_BYTES:
                self.metrics["oversized_messages"] += 1
            self.bytes += size
            self.queue.append((ordinal, message, size, arrived))
            queued = True
            self.metrics["max_queue_bytes"] = max(self.metrics["max_queue_bytes"], self.bytes)
            self.metrics["max_queue_count"] = max(self.metrics["max_queue_count"], self.received - self.committed)
            self.ready.set()
            if size > JOURNAL_QUEUE_BYTES:
                # Do not start assembling a second message during this handoff.
                with self.connection.pause_reader("oversized handoff"):
                    await self.through(ordinal)
        except asyncio.CancelledError:
            if not queued and self.connection.retiring and not self.connection.close_aborted and self.connection.intake_error is None:
                # Ordinary retirement owns the already-decoded capacity waiter
                # too. Hand off that one extra message before joining the owner;
                # no more frames can be read. Forced stop keeps crash semantics.
                self.bytes += size
                self.queue.append((ordinal, message, size, arrived))
                self.metrics["max_queue_bytes"] = max(self.metrics["max_queue_bytes"], self.bytes)
                self.metrics["max_queue_count"] = max(self.metrics["max_queue_count"], self.received - self.committed)
                self.ready.set()
            raise
        finally:
            self.waiting_bytes = 0

    def check_open(self):
        self.connection.check_intake()
        if self.error is not None:
            raise self.error
        if self.connection.retiring:
            raise Retired()
        if self.done:
            raise CoordinatorError("NATIVE_JOURNAL_CLOSED", "Native journal owner ended", operational=True)

    async def through(self, ordinal):
        self.connection.check_intake()
        if self.committed >= ordinal:
            return
        began = time.monotonic()
        self.metrics["barrier_waits"] += 1
        try:
            while self.committed < ordinal:
                self.check_open()
                self.progress.clear()
                await self.progress.wait()
            self.connection.check_intake()
        finally:
            seconds = time.monotonic() - began
            self.metrics["barrier_seconds"] += seconds
            self.metrics["max_barrier_seconds"] = max(self.metrics["max_barrier_seconds"], seconds)

    def wake(self):
        self.ready.set()
        self.capacity.set()
        self.progress.set()

    async def run(self):
        try:
            while self.queue or not self.reader_done:
                if not self.queue:
                    self.ready.clear()
                    await self.ready.wait()
                    continue
                if self.turn:
                    await self.turn()
                self.connection.check_intake()
                began, size = time.monotonic(), 0
                self.staged = []
                while self.queue and len(self.staged) < JOURNAL_BATCH_COUNT:
                    entry = self.queue[0]
                    if self.staged and (size + entry[2] > JOURNAL_BATCH_BYTES or
                                        time.monotonic() - began >= JOURNAL_BATCH_AGE_S):
                        break
                    self.staged.append(self.queue.popleft())
                    size += entry[2]
                # Synchronous callback: receipt + obligations in one transaction.
                # No sequence or capacity is published before its successful return.
                self.commit(self.staged)
                ended = time.monotonic()
                self.committed = self.staged[-1][0]
                self.bytes -= size
                self.metrics["committed_bytes"] += size
                self.metrics["batches"] += 1
                self.metrics["max_batch_count"] = max(self.metrics["max_batch_count"], len(self.staged))
                self.metrics["max_batch_bytes"] = max(self.metrics["max_batch_bytes"], size)
                self.metrics["batch_seconds"] += ended - began
                self.metrics["max_batch_seconds"] = max(self.metrics["max_batch_seconds"], ended - began)
                self.metrics["max_commit_age_seconds"] = max(self.metrics["max_commit_age_seconds"], ended - self.staged[0][3])
                self.staged = []
                self.capacity.set()
                self.progress.set()
        except asyncio.CancelledError as exc:
            if not self.connection.retiring and not isinstance(exc, Retired):
                self.connection.intake_failed(exc, "journal cancelled")
            self.error = self.connection.stopped_error() or Retired()
        except Exception as exc:
            self.connection.intake_failed(exc, "journal")
        finally:
            self.done = True
            self.wake()


class Connection:
    def __init__(self, reader, writer, on_event, schema=None, *, on_batch=None, turn=None, on_failure=None, check_stop=None):
        self.reader, self.writer, self.on_event, self.schema = reader, writer, on_event, schema
        self.pending, self.counter = {}, 0
        self.request_methods = {}  # retained through a timeout until its late response or disconnect
        self.response_ordinals, self.request_started = {}, {}
        self.metrics = dict.fromkeys(DECODE_METRICS, 0)
        self.rpc_metrics = {"responses": 0, "response_seconds": 0.0, "max_response_seconds": 0.0}
        self.turn, self.on_failure = turn, on_failure
        self.check_stop = check_stop
        self.intake_error = None
        self.reader_pause_depth = 0
        self.reader_paused_at = None
        self.reader_paused_seconds = 0.0
        self.reader_pause_counts = {}
        self.response_waits = set()
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.retiring = False  # intentional local retirement, not a transport failure
        self.close_timer = None
        self.close_started = None
        self.close_lock = asyncio.Lock()
        self.close_complete = self.close_aborted = self.close_escalated = False
        self.intake = Journal(self, on_batch, turn) if on_batch is not None else None
        from .telemetry import detached_context
        self.receiver = asyncio.create_task(self.receive(), context=detached_context())

    def check_intake(self):
        if self.intake_error is not None:
            raise self.intake_error
        if self.check_stop is not None:
            self.check_stop()

    def stopped_error(self):
        try:
            self.check_intake()
        except IntakeStopped as exc:
            return exc
        return None

    def reader_active(self):
        now = asyncio.get_running_loop().time()
        paused = self.reader_paused_seconds
        if self.reader_pause_depth:
            paused += now - self.reader_paused_at
        return now - paused

    def reader_status(self):
        now = asyncio.get_running_loop().time()
        return {"paused": bool(self.reader_pause_depth), "paused_seconds": now - self.reader_active(),
                "pause_counts": dict(self.reader_pause_counts), "response_waits": len(self.response_waits)}

    @contextmanager
    def pause_reader(self, reason):
        if not self.reader_pause_depth:
            self.reader_paused_at = asyncio.get_running_loop().time()
            for waiter in self.response_waits:
                waiter.changed()
        self.reader_pause_depth += 1
        self.reader_pause_counts[reason] = self.reader_pause_counts.get(reason, 0) + 1
        try:
            yield
        finally:
            self.reader_pause_depth -= 1
            if not self.reader_pause_depth:
                self.reader_paused_seconds += asyncio.get_running_loop().time() - self.reader_paused_at
                self.reader_paused_at = None
                for waiter in self.response_waits:
                    waiter.changed()

    @classmethod
    async def connect(cls, path, on_event, schema=None, **kwargs):
        from .runtime import endpoint_info
        endpoint = endpoint_info(path, native=True)
        reader, writer = await asyncio.open_unix_connection(endpoint["resolved"])
        try:
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
            return cls(reader, writer, on_event, schema, **kwargs)
        except BaseException:
            # Handshake ownership has not reached a Connection yet.
            writer.transport.abort()
            from .locks import retire, wait_closed
            try:
                await retire(wait_closed, writer)
            except OSError:
                pass
            raise

    async def frame(self, opcode, payload):
        self.check_intake()
        if self.retiring and opcode != 8:
            raise Retired()  # local retirement leaves evidence replayable
        mask, length = os.urandom(4), len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        try:
            async with self.send_lock:
                self.check_intake()
                if self.retiring:
                    raise Retired()
                self.writer.write(header + mask + masked)
                await self.writer.drain()
                self.check_intake()
        except OSError:
            self.check_intake()
            if self.retiring:
                raise Retired() from None
            raise

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
        self.request_methods[ident] = method
        self.request_started[ident] = time.monotonic()
        try:
            await self.send({"id": ident, "method": method, "params": params})
            try:
                response = await ResponseWait(self, future, timeout).result()
            except asyncio.TimeoutError as exc:
                raise CoordinatorError("NATIVE_UNCERTAIN", "Native RPC result lost; reconcile before retry",
                                       {"method": method, "request": ident}, True) from exc
            # The reader has answered the wire request. Preserve the former
            # receive-loop durability contract separately from its timeout:
            # only notifications preceding THIS response must have committed.
            if self.intake is not None:
                await self.intake.through(self.response_ordinals.pop(ident, 0))
            self.check_intake()
            if "error" in response:
                error = response["error"]
                details = {"method": method, "native_code": error.get("code")}
                if method in ("thread/turns/list", "thread/items/list") and params.get("cursor") is not None:
                    details["history_cursor"] = True
                raise CoordinatorError("NATIVE_RPC", error.get("message", "Native refusal"), details)
            return response["result"]
        finally:
            self.pending.pop(ident, None)
            self.response_ordinals.pop(ident, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # a local-stop check can precede consuming its result

    async def initialize(self, experimental=True):
        result = await self.call("initialize", {"clientInfo": {"name": "software-workflows-coordinator", "version": "2.0.0"},
                                                "capabilities": {"experimentalApi": experimental,
                                                                 "optOutNotificationMethods": list(OPTED_OUT)}})
        await self.send({"method": "initialized"})
        return result

    async def receive(self):
        chunks, fragmented = [], False
        read_until = 0.0
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
                size = sum(map(len, chunks))
                for metrics in (self.metrics, DECODE_METRICS):
                    metrics["json_bytes"] += size
                    metrics["messages"] += 1
                    metrics["max_json_bytes"] = max(metrics["max_json_bytes"], size)
                began = time.perf_counter()
                message = json.loads(b"".join(chunks).decode("utf-8"))
                decode_seconds = time.perf_counter() - began
                chunks, fragmented = [], False
                method = self.request_methods.pop(message.get("id"), None) if "method" not in message else None
                for metrics in (self.metrics, DECODE_METRICS):
                    metrics["max_decode_seconds"] = max(metrics["max_decode_seconds"], decode_seconds)
                    if method in HISTORY_METHODS:
                        metrics["history_json_bytes"] += size
                        metrics["history_responses"] += 1
                if "id" in message and "method" not in message:
                    started = self.request_started.pop(message["id"], None)
                    if started is not None:
                        seconds = time.monotonic() - started
                        self.rpc_metrics["responses"] += 1
                        self.rpc_metrics["response_seconds"] += seconds
                        self.rpc_metrics["max_response_seconds"] = max(self.rpc_metrics["max_response_seconds"], seconds)
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if self.intake is not None:
                            self.response_ordinals[message["id"]] = self.intake.received
                        future.set_result(message)
                elif self.intake is not None:
                    await self.intake.put(message, size)
                else:
                    # Callback only journals/enqueues. Never await a hook/tool here.
                    if before_event := getattr(self, "before_event", None):
                        await before_event()
                    self.on_event(message)
                if self.turn is not None:
                    if time.monotonic() >= read_until:
                        with self.pause_reader("scheduler"):
                            await self.turn()
                        # One FIFO grant serves a bounded read slice, like the
                        # journal's bounded batch. Gating each cheap message
                        # separately strands receipt throughput behind every
                        # application's cooperative step.
                        read_until = time.monotonic() + JOURNAL_BATCH_AGE_S
                else:
                    await asyncio.sleep(0)
        except (OSError, ValueError, asyncio.IncompleteReadError, MemoryError) as exc:
            if local_resource_failure(exc):
                self.intake_failed(exc, "receiver capacity")
                return
            if not self.closed and not self.retiring and self.stopped_error() is None:
                message = {"method": "coordinator/transportGap", "params": {"error": type(exc).__name__}}
                if self.intake is not None:
                    # Same ordered owner, behind every accepted notification.
                    try:
                        await self.intake.put(message, len(json.dumps(message).encode()))
                    except (Exception, Retired) as failure:
                        if not self.retiring and self.intake.error is None:
                            self.intake_failed(failure, "transport gap receipt")
                else:
                    self.on_event(message)
        except Retired:
            pass  # the forced-stop receipt fence, like intentional local close
        except asyncio.CancelledError:
            if not self.retiring:
                self.intake_failed(asyncio.CancelledError(), "receiver cancelled")
        except Exception as exc:
            self.intake_failed(exc, "receiver")
        finally:
            self.closed = True
            self.request_methods.clear()
            self.request_started.clear()
            if self.intake is not None:
                self.intake.reader_done = True
                self.intake.wake()
            for future in self.pending.values():
                if not future.done():
                    if error := self.stopped_error():
                        future.set_exception(error)
                    elif self.retiring or self.intake is not None and isinstance(self.intake.error, Retired):
                        future.set_exception(Retired())  # retry this item, not cancellation of its owner
                    else:
                        future.set_exception(CoordinatorError("NATIVE_DISCONNECTED", "Native connection closed", operational=True))

    def intake_failed(self, exc, stage):
        """An internal owner failure is not an ordinary transport gap."""
        if self.intake_error is not None:
            return
        error = IntakeStopped(exc, stage)
        self.intake_error = error
        if self.intake is not None:
            self.intake.error = error
        try:
            # Publish daemon stop before any consumer can resume. Diagnostic
            # persistence is best effort; the in-memory outcome is authoritative.
            if self.on_failure is not None:
                self.on_failure(dict(error.details, **(self.intake.status() if self.intake is not None else {})))
            else:
                asyncio.get_running_loop().call_exception_handler({"message": "Native intake failed", "exception": exc})
        finally:
            if self.intake is not None:
                self.intake.wake()
            self.writer.transport.abort()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            for waiter in self.response_waits:
                waiter.changed()

    async def close(self, *, abort=False):
        from .locks import retire
        self.retiring = True
        if self.intake is not None:
            self.intake.wake()
        if abort:
            self.abort()
        await retire(self._close, abort=abort)

    def abort(self, *, expired=False):
        """Escalation is synchronous and independent of any task joining this close."""
        self.retiring = True
        if self.intake is not None:
            self.intake.wake()
        self.closed = self.close_aborted = True
        self.writer.transport.abort()
        if self.close_timer is not None:
            self.close_timer.cancel()
            self.close_timer = None
        if expired and not self.close_escalated and not self.close_complete:
            self.close_escalated = True
            self.on_event({"method": "coordinator/closeEscalated", "params": {
                "connection": str(self.writer.get_extra_info("peername")),
                "grace_seconds": CLOSE_GRACE_S, "waited_seconds": time.monotonic() - self.close_started}})

    async def _close(self, *, abort=False):
        # One completion state even when detach, stop and a caller's finally
        # all retire this connection. An explicit abort can still interrupt the
        # lock holder's graceful wait synchronously through abort().
        async with self.close_lock:
            if self.close_complete:
                return
            await self._close_once(abort=abort)

    async def _close_once(self, *, abort=False):
        from .locks import cancel_and_join, wait_closed
        try:
            if not self.closed:
                # frame() writes a complete frame before awaiting drain. Appending
                # this empty masked close frame is atomic on the same loop.
                self.writer.write(bytes((0x88, 0x80)) + os.urandom(4))
            self.writer.close()
            if not abort and not self.close_aborted and not self.writer._protocol._get_close_waiter(self.writer).done():
                from functools import partial
                if self.close_started is None:
                    self.close_started = time.monotonic()
                if self.close_timer is None:
                    self.close_timer = asyncio.get_running_loop().call_later(
                        max(0, CLOSE_GRACE_S - (time.monotonic() - self.close_started)),
                        partial(self.abort, expired=True))
            await wait_closed(self.writer)
        except OSError:
            pass  # an already-failed transport repeats its error on every completion wait
        finally:
            if self.receiver is not asyncio.current_task():
                await cancel_and_join([self.receiver])
            if self.intake is not None and self.intake.owner is not asyncio.current_task():
                if abort:
                    # Forced stop retains crash semantics for uncommitted input.
                    await cancel_and_join([self.intake.owner])
                else:
                    from .locks import joined
                    # Ordinary retirement commits accepted input before replay.
                    await joined(self.intake.owner, propagate_cancel=False)
            if self.close_timer is not None:
                self.close_timer.cancel()
                self.close_timer = None
        self.close_complete = True


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
        await asyncio.sleep(0)


def coordinator_hooks(listed, roots, command=None, expected=None):
    """Deduplicate definitions while requiring trusted pre/post coverage per root."""
    expected = {h["key"]: h for h in expected or []}
    unique, covered = {}, set()
    for entry in listed["data"]:
        own = [h for h in entry["hooks"] if
               (h.get("command") == command and h.get("source") == "sessionFlags" if command else h.get("key") in expected)]
        keys = set()
        for hook in own:
            key = hook["key"]
            if key in unique and unique[key] != hook:
                raise CoordinatorError("HOOK_DEFINITIONS", "A hook identity has inconsistent definitions across roots")
            if expected and hook.get("currentHash") != expected[key].get("currentHash"):
                raise CoordinatorError("HOOK_TRUST", "Recovered hook content changed")
            if hook.get("trustStatus") != "trusted" or hook.get("enabled") is not True:
                raise CoordinatorError("HOOK_TRUST", "Coordinator hook is not enabled and trusted")
            unique[key] = hook
            keys.add(key)
        if entry.get("errors") or len(keys) != 2:
            raise CoordinatorError("HOOK_TRUST", "Every root must have both coordinator hooks", {"root": entry.get("cwd")})
        covered.add(entry.get("cwd"))
    if set(roots) - covered or len(unique) != 2:
        raise CoordinatorError("HOOK_TRUST", "Hook inventory does not cover every runtime root", {"roots": roots})
    return list(unique.values())
