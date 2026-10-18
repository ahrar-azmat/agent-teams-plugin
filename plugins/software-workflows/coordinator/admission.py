"""Native capability admission, calibrated against the installed binary.

A probe succeeds only after its negative control was observed. Production policy
visibility is a separate mandatory capability, never a warning-through switch.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace

from .client import CoordinatorError
from . import native, gitio, processes, runtime
from .fixture import Provider, function, patch, message, scratch_directory
from .ledger import case_sensitive
from .store import digest, uid, encoded, fsync_dir
from .locks import KeyedLocks


PROBES = ("hooks", "evidence", "dynamic", "delivery", "tty", "reconnect", "terminals", "ownership", "policy", "validation")


class ProbeListener:
    def __init__(self, directory, provider, config=None, experimental=True, hook=None, trusted=True, trace=False):
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home, self.repo = directory / "home", directory / "repo"
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.repo.mkdir(exist_ok=True)
        gitio.git(self.repo, ["init", "--quiet"])
        self.config = dict(provider.config)
        self.config.update({"approval_policy": "never", "sandbox_mode": "danger-full-access", "agents.enabled": True,
                            "features.multi_agent_v2": True, "web_search": "live"})
        self.config.update(config or {})
        self.events, self.trace_rules = [], []
        self.experimental, self.hook, self.trusted, self.trace = experimental, hook, trusted, trace
        self.connection, self.process = None, None
        self.startup_diagnostics = []
        self.thread = None

    async def start(self):
        if self.hook:
            command = shlex.join([sys.executable, "-B", str(Path(__file__).with_name("fixture.py")), "hook",
                                  "--directory", str(self.directory), "--mode", self.hook])
            group = {"matcher": ".*", "hooks": [{"type": "command", "command": command,
                                                  "timeout": 1 if self.hook == "timeout" else 10, "async": False}]}
            self.config.update({"features.hooks": True, "hooks.PreToolUse": [group], "hooks.PermissionRequest": [group]})
        await self.spawn()
        if self.hook and self.trusted:
            listed = await self.connection.call("hooks/list", {"cwds": [str(self.repo)]})
            trust = {h["key"]: {"trusted_hash": h["currentHash"]} for e in listed["data"] for h in e["hooks"] if h.get("source") == "sessionFlags"}
            if len(trust) != 2:
                raise CoordinatorError("PROBE_HOOK_HASH", "Native probe hook hashes unavailable")
            await self.close()
            self.config["hooks.state"] = trust
            await self.spawn()
        return self

    async def spawn(self):
        environment = native.environment(CODEX_HOME=str(self.home), LOG_FORMAT="json")
        if self.trace:
            environment["RUST_LOG"] = "codex_core::exec_policy=trace"
        self.allocation = runtime.allocate("probe")
        self.socket = Path(self.allocation["path"])
        self.process = await asyncio.create_subprocess_exec(native.native_codex(), "app-server", "--listen", "unix://" + str(self.socket),
                           *native.override_args(self.config), cwd=self.repo, env=environment, stdin=asyncio.subprocess.DEVNULL,
                           stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True)
        self.birth = processes.identity(self.process.pid)["birth"]
        self.stderr_task = asyncio.create_task(self.consume_stderr())
        deadline = asyncio.get_running_loop().time() + 20
        while True:
            try:
                if not self.socket.exists():
                    raise FileNotFoundError(self.socket)
                self.connection = await native.Connection.connect(self.socket, self.events.append)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if self.process.returncode is not None or asyncio.get_running_loop().time() >= deadline:
                    raise CoordinatorError("PROBE_LISTENER", "Fixture listener failed to start",
                                           {"exit_code": self.process.returncode, "path": str(self.socket),
                                            "bytes": len(os.fsencode(self.socket)), "measured_limit": runtime.capacity(),
                                            "diagnostic": self.startup_diagnostics}, True)
                await asyncio.sleep(0.02)
        self.initialized = await self.connection.initialize(self.experimental)
        self.endpoint = runtime.endpoint_info(self.socket, native=True)
        runtime.atomic_json(self.directory / "endpoint.json", self.endpoint)

    async def consume_stderr(self):
        # No native log text is copied. Only the requested trace's digest survives.
        async for line in self.process.stderr:
            if not self.connection:
                self.startup_diagnostics = (self.startup_diagnostics + [native.startup_diagnostic(line)])[-8:]
            try:
                entry = json.loads(line)
                fields = entry.get("fields", {})
                if fields.get("message") == "exec policy rules loaded":
                    self.trace_rules.append(native.policy_trace_digest(fields.get("rules")))
            except (ValueError, UnicodeError):
                continue

    async def start_thread(self, **overrides):
        response = await self.connection.call("thread/start", {"cwd": str(self.repo), "model": "gpt-6-astra",
                                              "approvalPolicy": "never", "sandbox": "danger-full-access", **overrides})
        self.thread = response["thread"]["id"]
        self.policy = native.policy_echo(response)
        return response

    async def turn(self, text="C1 capability probe", wait=True):
        start = len(self.events)
        response = await self.connection.call("turn/start", {"threadId": self.thread, "input": [{"type": "text", "text": text}]})
        if wait:
            await self.wait(lambda m: m.get("method") == "turn/completed" and m["params"]["threadId"] == self.thread, start=start)
        return response

    async def wait(self, predicate, timeout=30, start=0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            found = next((event for event in self.events[start:] if predicate(event)), None)
            if found:
                return found
            await asyncio.sleep(0.01)
        raise CoordinatorError("PROBE_TIMEOUT", "Required native observation did not arrive")

    async def close(self):
        if self.connection:
            await self.connection.close()
            self.connection = None
        if self.process and self.process.returncode is None:
            current = processes.identity(self.process.pid)
            if current and current["birth"] == self.birth:
                self.process.send_signal(15)
                try:
                    await asyncio.wait_for(self.process.wait(), 10)
                except asyncio.TimeoutError:
                    current = processes.identity(self.process.pid)
                    if current and current["birth"] == self.birth:
                        self.process.kill()
                    await self.process.wait()
        if hasattr(self, "stderr_task"):
            await self.stderr_task
        if getattr(self, "allocation", None):
            runtime.cleanup(self.allocation)
            self.allocation = None


@asynccontextmanager
async def listening(directory, provider, **kwargs):
    listener = ProbeListener(directory, provider, **kwargs)
    try:
        await listener.start()
        yield listener
    finally:
        await listener.close()


def add_patch(name, content="capability"):
    return "*** Begin Patch\n*** Add File: " + name + "\n+" + content + "\n*** End Patch\n"


def steps_router(steps):
    def router(body, index):
        return steps[index] if index < len(steps) else message()
    return router


class Admission:
    def __init__(self, store):
        self.store = store
        if not hasattr(store, "admission_locks"):
            store.admission_locks = KeyedLocks()

    async def schema(self):
        binary = native.native_codex()
        sha = await asyncio.to_thread(native.file_digest, binary)
        directory = self.store.root / "schemas" / sha
        if not (directory / "complete.json").exists():
            directory.mkdir(mode=0o700, exist_ok=True)
            home = directory / "generation-home"
            home.mkdir(mode=0o700, exist_ok=True)
            process = await asyncio.create_subprocess_exec(binary, "app-server", "generate-json-schema", "--experimental", "--out", str(directory),
                        env=dict(os.environ, CODEX_HOME=str(home)), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            code = await process.wait()
            if code:
                raise CoordinatorError("SCHEMA_GENERATION", "Native schema generation failed", {"exit_code": code})
            hashes = {str(p.relative_to(directory)): native.file_digest(p) for p in directory.rglob("*.json")}
            (directory / "complete.json").write_text(encoded({"digest": digest(hashes), "exit_code": code}))
        return directory, json.loads((directory / "complete.json").read_text())["digest"]

    async def fingerprint(self, listener, schema_digest):
        connection = listener.connection
        effective = await connection.call("config/read", {"cwd": str(listener.repo), "includeLayers": True})
        requirements = await connection.call("configRequirements/read", {})
        hooks = await connection.call("hooks/list", {"cwds": [str(listener.repo)]})
        features = []
        async for response in native.page(connection, "experimentalFeature/list", {}):
            features.extend(response["data"])
        try:
            await connection.call("c1/method-inventory", {}, unchecked=True)
        except CoordinatorError as exc:
            inventory = re.findall(r"`([^`]+)`", str(exc))[1:]
        else:
            raise CoordinatorError("METHOD_INVENTORY", "Unknown method was unexpectedly accepted")
        missing = set(native.METHODS) - set(inventory)
        if missing:
            raise CoordinatorError("METHOD_INVENTORY", "Required native methods unavailable", {"missing": sorted(missing)})
        binary = native.native_codex()
        proc = await asyncio.create_subprocess_exec(binary, "--version", stdout=asyncio.subprocess.PIPE)
        version = (await proc.communicate())[0].decode().strip()
        config = effective.get("config", {})
        complete_policy = version == "codex-cli 0.163.0" and requirements.get("requirements") is None and bool(listener.trace_rules)
        return {"binary": binary, "binary_digest": native.file_digest(binary), "version": version,
                "user_agent": listener.initialized["userAgent"], "methods": sorted(set(inventory)),
                "schema_digest": schema_digest, "config_digest": digest(config), "requirements_digest": digest(requirements),
                "features": features, "hooks_digest": digest(hooks), "hook_program_digest": native.file_digest(Path(__file__).with_name("hooks.py")),
                "model": config.get("model"), "provider": config.get("model_provider"), "mcp_digest": digest(config.get("mcp_servers", {})),
                "runtime_modes": {"web_search": config.get("web_search"), "agents_enabled": (config.get("agents") or {}).get("enabled"),
                                  "effort": config.get("model_reasoning_effort"), "reasoning_summary": config.get("model_reasoning_summary"),
                                  "compact_limit": config.get("model_auto_compact_token_limit")},
                "exec_policy": {"complete": complete_policy, "trace_digests": sorted(set(listener.trace_rules)),
                                "managed_requirements_absent": requirements.get("requirements") is None,
                                "basis": "0.163.0 requirements null proves requirements_toml.is_empty including rules; trace is the resulting complete policy" if complete_policy else
                                         "post-managed-overlay effective execution policy is not exposed"},
                "git": gitio.text(listener.repo, ["--version"]), "sqlite": sqlite3.sqlite_version,
                "python": sys.version.split()[0], "os": platform.platform(),
                "case_sensitive": listener.case_sensitive if hasattr(listener, "case_sensitive") else case_sensitive(listener.repo),
                "socket": {key: listener.endpoint.get(key) for key in ("measured_limit", "uid", "mode")},
                "adapter_digest": native.adapter_digest()}

    async def writer_gate(self, daemon, listenerid, thread_response, fixture=False):
        listener = self.store.get(listenerid)
        checkout = self.store.get(self.store.get(listener["actor"])["checkout"])
        schema_directory, schema_digest = await self.schema()
        # A thread start loads rules without starting inference. Drain its trace
        # before collecting the full admission binding; no user files are read.
        for _ in range(100):
            listener = self.store.get(listenerid)
            if listener.get("policy_traces"):
                break
            await asyncio.sleep(0.01)
        adapter = SimpleNamespace(connection=daemon.connections[listenerid], repo=Path(checkout["root"]),
                                  initialized={"userAgent": listener["user_agent"]}, trace_rules=listener.get("policy_traces", []),
                                  endpoint=listener["endpoint"],
                                  case_sensitive=checkout["case_sensitive"])
        fingerprint = await self.fingerprint(adapter, schema_digest)
        hooks = await adapter.connection.call("hooks/list", {"cwds": [checkout["root"]]})
        own = [h for e in hooks["data"] for h in e["hooks"] if any(
               h.get("key") == expected.get("key") for expected in listener.get("hooks", []))]
        if len(own) != 2 or any(h.get("trustStatus") != "trusted" or h.get("enabled") is not True for h in own):
            raise CoordinatorError("HOOK_TRUST", "Every listener must prove effective hook trust, including on a cache hit")
        fingerprint["thread_policy"] = native.policy_echo(thread_response)
        fingerprint["model"] = thread_response.get("model")
        fingerprint["provider"] = thread_response.get("modelProvider")
        from .roles import verify
        catalog = await verify(adapter.connection, thread_response["thread"]["id"])
        fingerprint["writer_catalog_digest"] = catalog["digest"]
        features = {item["name"]: item["enabled"] for item in fingerprint["features"]}
        modes = fingerprint["runtime_modes"]
        workflow = self.store.get(self.store.get(listener["actor"])["run"]).get("workflow")
        if (modes["web_search"] != "live" or modes["agents_enabled"] is not True or modes["effort"] != "max"
                or modes["reasoning_summary"] != "detailed" or workflow and workflow["compact_limit"] is not None and modes["compact_limit"] != workflow["compact_limit"] or
                any(features.get(name) is not enabled for name, enabled in
                    (("hooks", True), ("multi_agent_v2", True), ("unified_exec_tty", False)))):
            raise CoordinatorError("ADMISSION_RUNTIME_MODE", "Effective native modes do not honor live web, V2 agents, hooks and disabled TTY",
                                   {"modes": modes, "features": {name: features.get(name) for name in ("hooks", "multi_agent_v2", "unified_exec_tty")}})
        if not fingerprint["exec_policy"]["complete"]:
            raise CoordinatorError("ADMISSION_EXEC_POLICY_UNOBSERVABLE", "Native effective execution policy is incomplete",
                                   {"exec_policy": fingerprint["exec_policy"]})
        binding = digest(fingerprint)
        async with self.store.admission_locks.hold(binding):
            cached = await self.calibrate_writer(binding, fingerprint, fixture)
        listener = self.store.get(listenerid)
        listener["admission"] = binding
        listener["admission_fingerprint"] = fingerprint
        listener["admission_observations"] = {"endpoint": listener["endpoint"], "listener": listenerid, "cache_hit": cached, "writer_catalog": catalog}
        self.store.save(listener)
        self.store.event("admission/writer", listenerid, {"fingerprint": binding, "fixture": fixture, "cache_hit": cached})
        return {"admitted": True, "fingerprint": binding, "cache_hit": cached, "effective_modes": modes}

    async def calibrate_writer(self, binding, fingerprint, fixture):
        cache = self.store.root / "calibrations" / ("writer-" + binding + ".json")
        try:
            cached = json.loads(cache.read_text())
        except (OSError, ValueError):
            cached = {}
        if not (cached.get("completed") is True and cached.get("fingerprint_digest") == binding and cached.get("fingerprint") == fingerprint):
            self.store.event("admission/calibrating", binding, {"state": "running"})
            # Isolated state under the OS temporary tree keeps every calibration
            # artifact under that same caller-owned root. Normal state continues
            # to use the OS temporary directory accepted by the fixture adapter.
            temporary_roots = {Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve()}
            parent = self.store.root / "calibrations" if any(p in self.store.root.parents for p in temporary_roots) else None
            directory = tempfile.mkdtemp(prefix="software-workflows-admission-", dir=parent)
            calibration = await self.check({"scratch": directory})
            if not calibration["completed"]:
                raise CoordinatorError("ADMISSION_INCOMPLETE", "Required runtime probes did not all complete")
            record = {"fingerprint": fingerprint, "fingerprint_digest": binding,
                      "evidence": calibration["evidence"], "completed": True, "fixture": fixture}
            descriptor, pending = tempfile.mkstemp(prefix="writer-pending-", dir=cache.parent)
            with os.fdopen(descriptor, "w") as output:
                output.write(encoded(record))
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, cache)
            fsync_dir(cache.parent)
            return False
        return True

    async def check(self, params):
        directory = scratch_directory(params["scratch"]) / uid("admission")
        directory.mkdir(mode=0o700)
        requested = params.get("probes") or list(PROBES)
        if set(requested) - set(PROBES) - {"exec_policy"}:
            raise CoordinatorError("ADMISSION_PROBE", "Unknown capability probe")
        schema_directory, schema_digest = await self.schema()
        results = []
        for name in requested:
            try:
                observation = await getattr(self, "probe_" + name)(directory / name)
                passed = observation.get("negative_observed") is True and observation.get("positive_observed") is True
                results.append({"probe": name, "passed": passed, "observations": observation})
            except Exception as exc:
                results.append({"probe": name, "passed": False, "error": getattr(exc, "code", type(exc).__name__),
                                "details": getattr(exc, "details", {}), "message": str(exc) if isinstance(exc, CoordinatorError) else type(exc).__name__})
            (directory / "results.json").write_text(encoded(results))
            self.store.event("admission/probe", name, results[-1])
        try:
            with Provider() as provider:
                async with listening(directory / "fingerprint", provider, trace=True) as listener:
                    await listener.start_thread()
                    fingerprint = await self.fingerprint(listener, schema_digest)
        except Exception as exc:
            fingerprint = {"exec_policy": {"complete": False}, "socket_capacity": runtime.capacity()}
            results.append({"probe": "fingerprint", "passed": False,
                            "error": getattr(exc, "code", type(exc).__name__), "details": getattr(exc, "details", {})})
        value = {"fingerprint": fingerprint, "fingerprint_digest": digest(fingerprint), "cases": results,
                 "evidence": str(directory), "completed": all(r["passed"] for r in results),
                 "production_admitted": False, "production_refusal": None if fingerprint["exec_policy"]["complete"] else "ADMISSION_EXEC_POLICY_UNOBSERVABLE"}
        (directory / "report.json").write_text(encoded(value))
        if value["completed"] and set(PROBES) <= set(requested):
            (self.store.root / "calibrations" / (value["fingerprint_digest"] + ".json")).write_text(encoded(value))
        if not value["completed"]:
            raise CoordinatorError("ADMISSION_FAILED", "One or more required red/green native observations failed", value)
        return value

    async def probe_hooks(self, directory):
        observations = {}
        for mode, trusted in (("observe", False), ("observe", True), ("deny", True), ("wait", True), ("crash", True), ("timeout", True)):
            label = mode + ("-trusted" if trusted else "-untrusted")
            with Provider(steps_router([patch(add_patch("patch.txt")), function("exec_command", {"cmd": "printf command > command.txt", "login": False}), message()])) as provider:
                async with listening(directory / label, provider, hook=mode, trusted=trusted) as listener:
                    await listener.start_thread()
                    start = time.monotonic()
                    await listener.turn()
                    elapsed = time.monotonic() - start
                    log = listener.directory / "hook-events.jsonl"
                    hooks = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                    failed_hooks = [e for e in listener.events if e.get("method") == "hook/completed" and e["params"]["run"].get("status") != "completed"]
                    observations[label] = {"patch": (listener.repo / "patch.txt").exists(), "command": (listener.repo / "command.txt").exists(),
                          "hook_calls": len(hooks), "permission_request_calls": sum(h["payload"]["hook_event_name"] == "PermissionRequest" for h in hooks),
                          "elapsed": elapsed, "failed_lifecycle": len(failed_hooks),
                          "unmediated": bool(failed_hooks and ((listener.repo / "patch.txt").exists() or (listener.repo / "command.txt").exists()))}
        good = observations["observe-trusted"]
        denial, wait = observations["deny-trusted"], observations["wait-trusted"]
        positive = good["hook_calls"] >= 2 and good["patch"] and good["command"] and not denial["patch"] and not denial["command"] and wait["elapsed"] >= 1
        positive = positive and observations["crash-trusted"]["unmediated"] and observations["timeout-trusted"]["unmediated"]
        return {"negative_observed": observations["observe-untrusted"]["hook_calls"] == 0,
                "positive_observed": bool(positive), "cases": observations,
                "permission_request": "measured count under never; not used as custody gate"}

    async def probe_policy(self, directory):
        with Provider() as provider:
            async with listening(directory / "bad", provider) as listener:
                response = await listener.connection.call("thread/start", {"cwd": str(listener.repo), "approvalPolicy": "on-request", "sandbox": "read-only"})
                try:
                    native.policy_echo(response)
                    negative = False
                except CoordinatorError:
                    negative = True
            async with listening(directory / "good", provider) as listener:
                started = await listener.start_thread()
                await listener.turn()
                resumed = await listener.connection.call("thread/resume", {"threadId": listener.thread, "approvalPolicy": "never", "sandbox": "danger-full-access"})
                return {"negative_observed": negative, "positive_observed": native.policy_echo(started) == native.policy_echo(resumed),
                        "start": native.policy_echo(started), "resume": native.policy_echo(resumed)}

    async def probe_validation(self, directory):
        params = {"cwd": str(directory), "approvalPolicy": "never", "sandbox": "danger-full-access", "c1UnknownField": True}
        with Provider() as provider:
            async with listening(directory, provider) as listener:
                params["cwd"] = str(listener.repo)
                response = await listener.connection.call("thread/start", params, unchecked=True)
                try:
                    native.validate("thread/start", params)
                    rejected = False
                except CoordinatorError:
                    rejected = True
                return {"negative_observed": bool(response.get("thread")), "positive_observed": rejected,
                        "native_accepts_extra": True, "coordinator_rejects_extra": rejected}

    async def probe_tty(self, directory):
        results = []
        for enabled in (True, False):
            with Provider(steps_router([function("exec_command", {"cmd": "printf tty", "tty": True, "login": False}), message()])) as provider:
                async with listening(directory / str(enabled), provider, config={"features.unified_exec_tty": enabled}) as listener:
                    await listener.start_thread()
                    await listener.turn()
                    outputs = json.dumps(provider.requests)
                    items = [e for e in listener.events if e.get("method") == "item/completed" and e["params"]["item"]["type"] == "commandExecution"]
                    results.append({"enabled": enabled, "commands": len(items), "refused": "TTY execution is disabled" in outputs})
        return {"negative_observed": results[0]["commands"] == 1, "positive_observed": results[1]["refused"] and results[1]["commands"] == 0, "cases": results}

    async def probe_reconnect(self, directory):
        with Provider() as provider:
            async with listening(directory, provider) as listener:
                await listener.start_thread()
                await listener.turn()
                thread = listener.thread
                await listener.connection.close()
                listener.connection = await native.Connection.connect(listener.socket, listener.events.append)
                try:
                    await listener.connection.call("thread/resume", {"threadId": thread})
                    negative = False
                except CoordinatorError:
                    negative = True
                await listener.connection.initialize()
                resumed = await listener.connection.call("thread/resume", {"threadId": thread, "approvalPolicy": "never", "sandbox": "danger-full-access"})
                return {"negative_observed": negative, "positive_observed": resumed["thread"]["id"] == thread,
                        "policy": native.policy_echo(resumed), "transport": "WebSocket over Unix socket"}

    async def probe_terminals(self, directory):
        with Provider(steps_router([function("exec_command", {"cmd": "sleep 20", "login": False, "yield_time_ms": 1000}), message()])) as provider:
            async with listening(directory, provider) as listener:
                await listener.start_thread()
                await listener.turn()
                try:
                    rejected = await listener.connection.call("thread/backgroundTerminals/terminate", {
                        "threadId": listener.thread, "processId": "c1-deliberately-invalid-terminal"})
                    negative = rejected.get("terminated") is False
                    bad_request = {"response": rejected}
                except CoordinatorError as exc:
                    negative = True
                    bad_request = {"error": exc.envelope()}
                response = await listener.connection.call("thread/backgroundTerminals/list", {"threadId": listener.thread})
                terminated = []
                for terminal in response["data"]:
                    terminated.append(await listener.connection.call("thread/backgroundTerminals/terminate", {"threadId": listener.thread, "processId": terminal["processId"]}))
                after = await listener.connection.call("thread/backgroundTerminals/list", {"threadId": listener.thread})
                return {"negative_observed": negative,
                        "positive_observed": bool(response["data"]) and not after["data"] and all(x.get("terminated") for x in terminated),
                        "bad_request": bad_request, "terminals": response["data"], "terminated": terminated}

    async def probe_ownership(self, directory):
        spawn = "import subprocess,time; subprocess.Popen(['/bin/sleep','4'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True); time.sleep(1)"
        command = shlex.join(["/bin/zsh", "-f", "-c", "/usr/bin/true; " + shlex.join([sys.executable, "-B", "-c", spawn]) + "; /usr/bin/true"])
        with Provider(steps_router([function("exec_command", {"cmd": command, "login": False, "yield_time_ms": 1000}), message()])) as provider:
            async with listening(directory, provider, config={"features.unified_exec_tty": False}) as listener:
                await listener.start_thread()
                owner = processes.identity(listener.process.pid)
                services = await asyncio.to_thread(processes.inventory, owner, None)
                unrelated = await asyncio.create_subprocess_exec("/bin/sleep", "10")
                unrelated_identity = processes.identity(unrelated.pid)
                seen, reparented, negative, marker_failures = {}, [], False, set()
                try:
                    await listener.turn(wait=False)
                    deadline = time.monotonic() + 7
                    found, operation = [], None
                    while time.monotonic() < deadline:
                        event = next((e for e in listener.events if e.get("method") == "item/started" and
                                      e["params"].get("item", {}).get("type") == "commandExecution"), None)
                        if not event:
                            await asyncio.sleep(0.02)
                            continue
                        operation = {"native": {"thread": listener.thread, "call": event["params"]["item"]["id"]}}
                        found = await asyncio.to_thread(processes.inventory, owner, listener.thread, list(seen.values()), operation, services)
                        for process in found:
                            seen[(process["pid"], process["birth"])] = process
                            try:
                                processes.markers(process["pid"])
                            except (CoordinatorError, OSError):
                                marker_failures.add(process["pid"])
                            if process["ppid"] == 1:
                                reparented.append(process)
                        owner["process_lineage"] = list(seen.values())
                        if seen and not found:
                            break
                        await asyncio.sleep(0.05)
                    if operation is None:
                        raise CoordinatorError("OWNERSHIP_COMMAND_MISSING", "Native command lifecycle was not observed")
                    try:
                        await asyncio.to_thread(processes.inventory, owner, listener.thread, (), operation, services, [unrelated_identity])
                    except CoordinatorError as exc:
                        negative = exc.code == "PROCESS_AMBIGUOUS" and unrelated.pid in exc.details.get("pids", [])
                    completed = [e["params"]["item"] for e in listener.events if e.get("method") == "item/completed" and
                                 e["params"].get("item", {}).get("type") == "commandExecution"]
                    return {"positive_observed": bool(seen and reparented and not found and completed and completed[0].get("exitCode") == 0),
                            "negative_observed": negative, "command": command, "completed": completed,
                            "identities": list(seen.values()), "reparented": reparented[:1],
                            "marker_unavailable_pids": sorted(marker_failures), "settled": not found}
                finally:
                    if unrelated.returncode is None:
                        current = processes.identity(unrelated.pid)
                        if current and current["birth"] == unrelated_identity["birth"]:
                            unrelated.terminate()
                    await unrelated.wait()

    async def probe_delivery(self, directory):
        with Provider(steps_router([function("exec_command", {"cmd": "sleep 1", "login": False, "yield_time_ms": 1000}), message()])) as provider:
            async with listening(directory, provider) as listener:
                await listener.start_thread()
                try:
                    await listener.connection.call("turn/steer", {"threadId": listener.thread, "expectedTurnId": "wrong", "input": [{"type": "text", "text": "red"}]})
                    negative = False
                except CoordinatorError:
                    negative = True
                turn = await listener.turn(wait=False)
                await listener.wait(lambda e: e.get("method") == "item/started" and e["params"]["item"]["type"] == "commandExecution")
                ident = uid("delivery")
                await listener.connection.call("turn/steer", {"threadId": listener.thread, "expectedTurnId": turn["turn"]["id"],
                            "input": [{"type": "text", "text": "steered"}], "clientUserMessageId": ident})
                echo = await listener.wait(lambda e: e.get("method") == "item/completed" and e["params"]["item"].get("clientId") == ident)
                await listener.wait(lambda e: e.get("method") == "turn/completed")
                queued = uid("delivery")
                await listener.connection.call("thread/queue/add", {"threadId": listener.thread, "input": [{"type": "text", "text": "queued"}], "clientUserMessageId": queued})
                queue_echo = await listener.wait(lambda e: e.get("method") == "item/completed" and e["params"]["item"].get("clientId") == queued)
                await listener.wait(lambda e: e.get("method") == "turn/completed" and e["params"]["turn"]["id"] != turn["turn"]["id"])
                return {"negative_observed": negative, "positive_observed": bool(echo and queue_echo), "steer_id": ident, "queue_id": queued}

    async def probe_dynamic(self, directory):
        specification = {"type": "function", "name": "c1_identity", "description": "Admission identity echo", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        with Provider() as provider:
            async with listening(directory / "bad", provider, experimental=False) as listener:
                try:
                    await listener.start_thread(dynamicTools=[specification])
                    negative = False
                except CoordinatorError:
                    negative = True
        with Provider(steps_router([function("c1_identity", {}), message()])) as provider:
            async with listening(directory / "good", provider) as listener:
                await listener.start_thread(dynamicTools=[specification])
                await listener.turn(wait=False)
                call = await listener.wait(lambda e: e.get("method") == "item/tool/call")
                await listener.connection.send({"id": call["id"], "result": {"contentItems": [{"type": "inputText", "text": "native-bound"}], "success": True}})
                await listener.wait(lambda e: e.get("method") == "turn/completed")
                identity = call["params"]
        inheritance = []
        for enabled in (False, True):
            def router(body, index):
                data = [x for x in body.get("input", []) if x.get("type") in ("message", "agent_message", "function_call", "function_call_output")]
                child = "C1_DYNAMIC_CHILD" in json.dumps(data) and not any(x.get("name") == "spawn_agent" for x in data)
                if child:
                    return function("c1_identity", {}) if not any(x.get("name") == "c1_identity" for x in data) else message("child done")
                return function("spawn_agent", {"task_name": "probe", "message": "C1_DYNAMIC_CHILD", "fork_turns": "none"}, "collaboration") if not any(x.get("name") == "spawn_agent" for x in data) else message()
            with Provider(router) as provider:
                async with listening(directory / ("child-" + str(enabled)), provider, config={"features.multi_agent_v2_dynamic_tools": enabled}) as listener:
                    await listener.start_thread(dynamicTools=[specification])
                    await listener.turn()
                    await asyncio.sleep(0.5)
                    calls = [e for e in listener.events if e.get("method") == "item/tool/call" and e["params"]["threadId"] != listener.thread]
                    for call in calls:
                        await listener.connection.send({"id": call["id"], "result": {"contentItems": [{"type": "inputText", "text": "child-native-bound"}], "success": True}})
                    if calls:
                        await listener.wait(lambda e: e.get("method") == "turn/completed" and e["params"]["threadId"] != listener.thread)
                    inheritance.append({"enabled": enabled, "child_calls": len(calls)})
        return {"negative_observed": negative, "positive_observed": bool(identity.get("threadId") and identity.get("turnId") and identity.get("callId")) and inheritance[0]["child_calls"] == 0 and inheritance[1]["child_calls"] > 0,
                "native_identity": identity, "inheritance": inheritance}

    async def probe_evidence(self, directory):
        observations = []
        for enabled in (False, True):
            def router(body, index):
                items = [i for i in body.get("input", []) if i.get("type") in ("message", "agent_message", "function_call", "function_call_output", "custom_tool_call")]
                serialized = json.dumps(items)
                child = "C1_PATCH_CHILD" in serialized and not any(i.get("name") == "spawn_agent" for i in items)
                calls = [i for i in items if i.get("type") in ("function_call", "custom_tool_call")]
                if child:
                    if not calls:
                        return patch(add_patch("child.txt"))
                    if len(calls) == 1:
                        return function("exec_command", {"cmd": "printf child", "login": False})
                    return message("child done")
                if not calls:
                    return patch(add_patch("root.txt"))
                if len(calls) == 1:
                    return function("exec_command", {"cmd": "printf root", "login": False})
                if len(calls) == 2:
                    return function("spawn_agent", {"task_name": "probe", "message": "C1_PATCH_CHILD", "fork_turns": "none"}, "collaboration")
                return message()
            with Provider(router) as provider:
                async with listening(directory / str(enabled), provider, config={"agents.enabled": enabled, "features.multi_agent_v2": enabled}) as listener:
                    await listener.start_thread()
                    await listener.turn()
                    if enabled:
                        await listener.wait(lambda e: e.get("method") == "turn/completed" and e["params"]["threadId"] != listener.thread)
                    changes = [e["params"] for e in listener.events if e.get("method") == "item/completed" and e["params"]["item"]["type"] == "fileChange"]
                    commands = [e["params"] for e in listener.events if e.get("method") == "item/started" and e["params"]["item"]["type"] == "commandExecution"]
                    observations.append({"agents_enabled": enabled, "root": listener.thread, "changes": changes, "commands": commands})
        good = observations[1]
        positive = len(good["changes"]) == 2 and len(good["commands"]) == 2
        positive = positive and all(os.path.isabs(c["path"]) and isinstance(c["diff"], str) for e in good["changes"] for c in e["item"]["changes"])
        positive = positive and all(e["item"].get("processId") for e in good["commands"])
        return {"negative_observed": len(observations[0]["changes"]) == 1, "positive_observed": bool(positive), "cases": observations}

    async def probe_exec_policy(self, directory):
        observations = []
        with Provider() as provider:
            listener = ProbeListener(directory, provider, trace=True)
            rules = listener.home / "rules"
            rules.mkdir()
            for changed in (False, True):
                listener.trace_rules = []
                (rules / "calibration.rules").write_text('prefix_rule(pattern=["c1-policy-' + ("changed" if changed else "base") + '"], decision="allow")\n')
                try:
                    await listener.start()
                    await listener.start_thread()
                    config = await listener.connection.call("config/read", {"includeLayers": False})
                    requirements = await listener.connection.call("configRequirements/read", {})
                    await asyncio.sleep(0.1)
                    observations.append({"trace": listener.trace_rules, "config": digest(config.get("config", {})),
                                         "managed_requirements_absent": requirements.get("requirements") is None})
                finally:
                    await listener.close()
        changed_trace = bool(observations[0]["trace"] and observations[1]["trace"] and observations[0]["trace"][-1] != observations[1]["trace"][-1])
        return {"negative_observed": observations[0]["config"] == observations[1]["config"], "positive_observed": changed_trace,
                "cases": observations, "complete_effective_policy": all(o["managed_requirements_absent"] for o in observations),
                "basis": "For supported native 0.163.0, null requirements proves absence of a managed rules overlay; non-null requirements remain a named admission refusal"}
