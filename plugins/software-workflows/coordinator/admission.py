"""Native capability admission, calibrated against the installed binary.

A probe succeeds only after its negative control was observed. Production policy
visibility is a separate mandatory capability, never a warning-through switch.
"""
from . import owned
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

from .locks import KeyedLocks, finish_all, finish_thread, retire
from .client import CoordinatorError
from . import native, gitio, processes, runtime, children
from .fixture import Provider, action, function, patch, message, scratch_directory
from .ledger import case_sensitive
from .store import digest, uid, encoded


PROBES = ("hooks", "evidence", "dynamic", "delivery", "tty", "receipts", "reconnect", "terminals", "ownership", "policy", "validation")
# Optional capabilities are diagnostic probe results until measured in the
# actual writer configuration; none is ever a refusal. inject: messages.deliver_notice injects a no-wake notice where it is "active" (at
# any time) or "idle" (while no turn runs), and holds it where the probe found it absent (None). A probe that raised or
# timed out (each of its steps is bounded) proves neither: its binding takes FALLBACK, recorded with the error.
CAPABILITIES = ("inject",)
FALLBACK = {"inject": None}


class ProbeListener:
    def __init__(self, directory, provider, config=None, experimental=True, hook=None, trusted=True, trace=False, log=None):
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home, self.repo = directory / "home", directory / "repo"
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.repo.mkdir(exist_ok=True)
        self.repo_initialized = False  # git init runs at the first spawn, off the event loop
        self.config = dict(provider.config)
        self.config.update({"approval_policy": "never", "sandbox_mode": "danger-full-access", "agents.enabled": True,
                            "features.multi_agent_v2": True, "web_search": "live"})
        self.config.update(config or {})
        self.events, self.trace_rules, self.traces = [], [], []
        self.experimental, self.hook, self.trusted, self.trace, self.log = experimental, hook, trusted, trace, log
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
        if not self.repo_initialized:
            await finish_thread(gitio.git, self.repo, ["init", "--quiet"])
            self.repo_initialized = True
        environment = native.environment(CODEX_HOME=str(self.home), LOG_FORMAT="json")
        if self.trace:
            environment["RUST_LOG"] = "codex_core::exec_policy=trace"
        if self.log:
            environment["RUST_LOG"] = self.log
        self.allocation = runtime.allocate("probe")
        self.socket = Path(self.allocation["path"])
        self.process = await owned.spawn(native.native_codex(), "app-server", "--listen", "unix://" + str(self.socket),
                           *native.override_args(self.config), cwd=self.repo, env=environment, stdin=asyncio.subprocess.DEVNULL,
                           stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True)
        self.birth = processes.identity(self.process.pid)["birth"]
        from .telemetry import detached_context
        self.stderr_task = asyncio.create_task(self.consume_stderr(), context=detached_context())
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
        # No native log text is copied. Only the requested trace's digest, and call identities, survive.
        async for line in self.process.stderr:
            if not self.connection:
                self.startup_diagnostics = (self.startup_diagnostics + [native.startup_diagnostic(line)])[-8:]
            try:
                entry = json.loads(line)
                fields = entry.get("fields", {})
                if fields.get("message") == "exec policy rules loaded":
                    self.trace_rules.append(native.policy_trace_digest(fields.get("rules")))
                elif entry.get("target") == "codex_otel.trace_safe" and fields.get("event.name") in ("codex.tool_call_received", "codex.tool_result", "codex.tool_result_ready"):
                    self.traces.append({key: fields.get(key) for key in
                                        ("event.name", "conversation.id", "call_id", "tool_name", "tool_source", "success")})
            except (ValueError, UnicodeError, AttributeError):
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
        # Direct callers and context-manager exits share the same protection.
        await retire(self._close)

    async def _close(self):
        try:
            if self.connection:
                await self.connection.close(abort=True)
                self.connection = None
        finally:
            # Do not retire the allocation until the child is actually reaped.
            # A cancelled retirement retries this sequence with the same owner.
            if self.process:
                await children.terminate(self.process, 10)
            if hasattr(self, "stderr_task"):
                from .locks import finish_all
                await finish_all(self.stderr_task, return_exceptions=True)
            if getattr(self, "allocation", None):
                runtime.cleanup(self.allocation)
                self.allocation = None


@asynccontextmanager
async def serving(router=None):
    """A loopback model provider for one probe (fixture.Provider), shut down off the event loop: socketserver's
    shutdown waits for its serve loop's next poll, up to 0.5 s, and every writer's hooks and reconciles share this
    loop (measured: 62 stalls of 0.3 to 0.85 s through one 13-writer recovery's four calibrations)."""
    provider = Provider(router)
    provider.__enter__()
    try:
        yield provider
    finally:
        await finish_thread(provider.__exit__, None, None, None)


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
        sha = await finish_thread(native.file_digest, binary)
        directory = self.store.root / "schemas" / sha
        # One generation at a time for each binary: writers reconciled in parallel all attach, and two generators
        # writing the same files would interleave them.
        async with self.store.admission_locks.hold("schema:" + sha):
            if not (directory / "complete.json").exists():
                directory.mkdir(mode=0o700, exist_ok=True)
                home = directory / "generation-home"
                home.mkdir(mode=0o700, exist_ok=True)
                process = await owned.spawn(binary, "app-server", "generate-json-schema", "--experimental", "--out", str(directory),
                            env=dict(os.environ, CODEX_HOME=str(home)), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    code = await process.wait()
                finally:
                    await children.terminate(process)
                if code:
                    raise CoordinatorError("SCHEMA_GENERATION", "Native schema generation failed", {"exit_code": code})
                hashes = {str(p.relative_to(directory)): native.file_digest(p) for p in directory.rglob("*.json")}
                (directory / "complete.json").write_text(encoded({"digest": digest(hashes), "exit_code": code}))
        return directory, json.loads((directory / "complete.json").read_text())["digest"]

    async def fingerprint(self, listener, schema_digest):
        connection = listener.connection
        roots = getattr(listener, "roots", [str(listener.repo)])
        configurations = await asyncio.gather(*(connection.call("config/read", {"cwd": root, "includeLayers": True}) for root in roots))
        effective = configurations[roots.index(str(listener.repo))]
        requirements = await connection.call("configRequirements/read", {})
        hooks = await connection.call("hooks/list", {"cwds": roots})
        features = []
        async for response in native.page(connection, "experimentalFeature/list", {}):
            features.extend(response["data"])
        try:
            await connection.call("c1/method-inventory", {}, unchecked=True)
        except CoordinatorError as exc:
            inventory = re.findall(r"`([^`]+)`", str(exc))[1:]
        else:
            raise CoordinatorError("METHOD_INVENTORY", "Unknown method was unexpectedly accepted")
        required = native.BASELINE_METHODS | (native.PROJECT_METHODS if getattr(listener, "project_revision", None) else set())
        missing = required - set(inventory)
        if missing:
            raise CoordinatorError("METHOD_INVENTORY", "Required native methods unavailable", {"missing": sorted(missing)})
        binary = native.native_codex()
        proc = await owned.spawn(binary, "--version", stdout=asyncio.subprocess.PIPE)
        try:
            version = (await proc.communicate())[0].decode().strip()
        finally:
            await children.terminate(proc)
        # Off the event loop: the binary is 250 MB (0.1 s to hash, measured), and every writer gate reads these.
        binary_digest, git_version, adapter = await finish_all(finish_thread(native.file_digest, binary),
                                                                   finish_thread(gitio.text, listener.repo, ["--version"]),
                                                                   finish_thread(native.adapter_digest))
        config = effective.get("config", {})
        complete_policy = version == "codex-cli 0.163.0" and requirements.get("requirements") is None and bool(listener.trace_rules)
        result = {"binary": binary, "binary_digest": binary_digest, "version": version,
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
                "git": git_version, "sqlite": sqlite3.sqlite_version,
                "python": sys.version.split()[0], "os": platform.platform(),
                "case_sensitive": listener.case_sensitive if hasattr(listener, "case_sensitive") else case_sensitive(listener.repo),
                "socket": {key: listener.endpoint.get(key) for key in ("measured_limit", "uid", "mode")},
                "adapter_digest": adapter}
        if getattr(listener, "project_revision", None):
            result["project"] = {"revision": listener.project_revision, "roots": roots, "cwd": str(listener.repo),
                                 "configuration_digests": [digest(c) for c in configurations]}
        return result

    async def writer_gate(self, daemon, listenerid, thread_response, fixture=False):
        listener = self.store.get(listenerid)
        checkout = self.store.get(self.store.get(listener["actor"])["checkout"])
        schema_directory, schema_digest = await self.schema()
        # A thread start loads rules without starting inference. Drain its trace
        # before collecting the full admission binding; no user files are read.
        for _ in range(100):
            daemon.read_native_log(listenerid)
            listener = self.store.get(listenerid)
            if daemon.native_log(listener).get("policy_traces"):
                break
            await asyncio.sleep(0.01)
        from . import participants
        actor = self.store.get(listener["actor"])
        unit = self.store.get(actor["unit"])
        roots = participants.roots(self.store, unit)
        cwd = self.store.get(actor["run"]).get("thread_cwd", checkout["root"])
        adapter = SimpleNamespace(connection=daemon.connections[listenerid], repo=Path(cwd),
                                  roots=roots, project_revision=unit.get("project_revision"),
                                  initialized={"userAgent": listener["user_agent"]}, trace_rules=daemon.native_log(listener).get("policy_traces", []),
                                  endpoint=listener["endpoint"],
                                  case_sensitive=checkout["case_sensitive"])
        fingerprint = await self.fingerprint(adapter, schema_digest)
        hooks = await adapter.connection.call("hooks/list", {"cwds": roots})
        native.coordinator_hooks(hooks, roots, expected=listener.get("hooks", []))
        from .projects import verify_thread
        verify_thread(thread_response, unit, cwd, roots)
        fingerprint["thread_policy"] = native.policy_echo(thread_response)
        fingerprint["model"] = thread_response.get("model")
        fingerprint["provider"] = thread_response.get("modelProvider")
        from .roles import verify
        catalog = await verify(adapter.connection, thread_response["thread"]["id"])
        fingerprint["writer_catalog_digest"] = digest([catalog["digest"], listener.get("writer_profile")])
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
            cached, capabilities = await self.calibrate_writer(binding, fingerprint, fixture)
        listener = self.store.get(listenerid)
        listener["admission"] = binding
        listener["admission_fingerprint"] = fingerprint
        listener["capabilities"] = {name: value.get("capability") for name, value in capabilities.items()}
        listener["capability_fallback"] = {name: value["error"] for name, value in capabilities.items() if value.get("fallback")}
        listener["admission_observations"] = {"endpoint": listener["endpoint"], "listener": listenerid, "cache_hit": cached, "writer_catalog": catalog}
        self.store.save(listener)
        self.store.event("admission/writer", listenerid, {"writer_profile": listener.get("writer_profile"), "fingerprint": binding, "fixture": fixture, "cache_hit": cached,
                                                          "capabilities": listener["capabilities"]})
        return {"admitted": True, "fingerprint": binding, "cache_hit": cached, "effective_modes": modes}

    async def calibrate_writer(self, binding, fingerprint, fixture):
        """Run reference controls without borrowing their configuration identity.

        Each behavioral control deliberately changes its disposable provider,
        hooks or policy. A later fingerprint-only listener is not the listener
        that measured those controls. Keep the evidence, but do not cache it as
        a writer capability. The actual writer policy is checked by writer_gate.
        """
        temporary_roots = {Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve()}
        parent = self.store.root / "calibrations" if any(p in self.store.root.parents for p in temporary_roots) else None
        directory = tempfile.mkdtemp(prefix="software-workflows-admission-", dir=parent)
        self.store.event("admission/calibrating", binding, {"state": "running", "fixture": fixture})
        calibration = await self.check({"scratch": directory, "probes": list(PROBES) + (["projects"] if fingerprint.get("project") else [])})
        if not calibration["completed"]:
            raise CoordinatorError("ADMISSION_INCOMPLETE", "Required runtime probes did not all complete")
        return False, {name: {"capability": None, "fallback": True,
            "error": "CAPABILITY_CONFIGURATION_UNMEASURED",
            "evidence": calibration["evidence"]} for name in CAPABILITIES}

    async def capabilities(self, scratch, names):
        """Probe the optional capabilities `names` (CAPABILITIES). Each is its probe's "capability" (None where that
        was not proven) with its observations, or, where the probe raised or timed out, its FALLBACK with the error;
        with the seconds it added to this admission."""
        directory = scratch_directory(scratch) / uid("capabilities")
        directory.mkdir(mode=0o700)
        found = {}
        for name in names:
            started = time.monotonic()
            try:
                observations = await getattr(self, "probe_" + name)(directory / name)
                found[name] = {"capability": observations.get("capability"), "observations": observations}
            except Exception as exc:
                found[name] = {"capability": FALLBACK[name], "fallback": True, "error": getattr(exc, "code", type(exc).__name__),
                               "message": str(exc) if isinstance(exc, CoordinatorError) else type(exc).__name__}
            found[name]["seconds"] = round(time.monotonic() - started, 2)
            self.store.event("admission/capability", name, {k: v for k, v in found[name].items() if k != "observations"})
        return found

    async def check(self, params):
        directory = scratch_directory(params["scratch"]) / uid("admission")
        directory.mkdir(mode=0o700)
        requested = params.get("probes") or list(PROBES)
        if set(requested) - set(PROBES) - set(CAPABILITIES) - {"exec_policy", "projects"}:
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
            async with serving() as provider:
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
        # report.json is evidence only. The fingerprint-only listener did not
        # measure the preceding controls, so it cannot name a capability cache.
        if not value["completed"]:
            raise CoordinatorError("ADMISSION_FAILED", "One or more required red/green native observations failed", value)
        return value

    async def probe_projects(self, directory):
        from .project_admission import probe
        return await probe(directory)

    async def probe_hooks(self, directory):
        observations = {}
        for mode, trusted in (("observe", False), ("observe", True), ("deny", True), ("wait", True), ("crash", True), ("timeout", True)):
            label = mode + ("-trusted" if trusted else "-untrusted")
            async with serving(steps_router([patch(add_patch("patch.txt")), function("exec_command", {"cmd": "printf command > command.txt", "login": False}), message()])) as provider:
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
        async with serving() as provider:
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
        async with serving() as provider:
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
            async with serving(steps_router([function("exec_command", {"cmd": "printf tty", "tty": True, "login": False}), message()])) as provider:
                async with listening(directory / str(enabled), provider, config={"features.unified_exec_tty": enabled}) as listener:
                    await listener.start_thread()
                    await listener.turn()
                    outputs = json.dumps(provider.requests)
                    items = [e for e in listener.events if e.get("method") == "item/completed" and e["params"]["item"]["type"] == "commandExecution"]
                    results.append({"enabled": enabled, "commands": len(items), "refused": "TTY execution is disabled" in outputs})
        return {"negative_observed": results[0]["commands"] == 1, "positive_observed": results[1]["refused"] and results[1]["commands"] == 0, "cases": results}

    async def probe_receipts(self, directory):
        """The result native writes once a call's handler has returned (0.163 `codex.tool_result`), which the daemon
        settles refused calls by, and the mark it writes after it once the call's dispatch ended
        (`codex.tool_result_ready`), which tells a call native gave up on: for calls nested in a code-mode cell, as
        writers on gpt-6-astra (tool_mode code_mode_only) make every call, and for a direct one. Each is refused
        inside its handler: `tty` on the TTY-disabled runtime, a patch to a file that does not exist. Red: the log
        filter 2.4.0 started writers with."""
        cell = ('await tools.exec_command({cmd: "printf tty", tty: true}).catch(e => text("refused " + String(e))); '
                'await tools.apply_patch("*** Begin Patch\\n*** Update File: absent.txt\\n@@\\n-absent\\n+present\\n*** End Patch\\n")'
                '.catch(e => text("refused " + String(e)));')
        results = []
        for log in ("codex_core::exec_policy=trace", native.WRITER_LOG):
            steps = [action({"exec": cell}), function("exec_command", {"cmd": "printf tty", "tty": True, "login": False}), message()]
            async with serving(steps_router(steps)) as provider:
                async with listening(directory / str(len(results)), provider, config={"features.unified_exec_tty": False}, log=log) as listener:
                    await listener.start_thread()
                    await listener.turn()
                    final = provider.requests[-1]["input"]
            # Native's own account of each refusal: the outputs the model received.
            cell_output = json.dumps([i for i in final if i.get("type") == "custom_tool_call_output"])
            direct_output = json.dumps([i for i in final if i.get("type") == "function_call_output"])
            received = {t["call_id"]: t for t in listener.traces if t["event.name"] == "codex.tool_call_received"}
            failed = {t["call_id"] for t in listener.traces if t["event.name"] == "codex.tool_result"
                      and t["success"] == "false" and t["conversation.id"] == listener.thread}
            nested = {call for call, t in received.items() if t["tool_source"] == "code_mode"}
            direct = {call for call, t in received.items() if t["tool_source"] == "direct" and t["tool_name"] == "exec_command"}
            position = {(t["event.name"], t["call_id"]): index for index, t in enumerate(listener.traces)
                        if t["conversation.id"] == listener.thread}
            ended = {call for call in failed if position.get(("codex.tool_result_ready", call), -1) > position[("codex.tool_result", call)]}
            results.append({"log": log, "refused": cell_output.count("refused ") == 2 and "TTY execution is disabled" in direct_output,
                            "results": sum(t["event.name"] == "codex.tool_result" for t in listener.traces),
                            "ready": sum(t["event.name"] == "codex.tool_result_ready" for t in listener.traces),
                            "nested": len(nested), "direct": len(direct),
                            "failed_nested": len(nested & failed), "failed_direct": len(direct & failed),
                            "ended_after_result": len((nested | direct) & ended)})
        red, green = results
        return {"negative_observed": red["refused"] and red["results"] == 0 and red["ready"] == 0,
                "positive_observed": green["refused"] and green["nested"] == 2 and green["failed_nested"] == 2
                                     and green["direct"] == 1 and green["failed_direct"] == 1 and green["ended_after_result"] == 3,
                "cases": results}

    async def probe_reconnect(self, directory):
        async with serving() as provider:
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
        async with serving(steps_router([function("exec_command", {"cmd": "sleep 20", "login": False, "yield_time_ms": 1000}), message()])) as provider:
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
        async with serving(steps_router([function("exec_command", {"cmd": command, "login": False, "yield_time_ms": 1000}), message()])) as provider:
            async with listening(directory, provider, config={"features.unified_exec_tty": False}) as listener:
                await listener.start_thread()
                owner = processes.identity(listener.process.pid)
                services = await finish_thread(processes.inventory, owner, None)
                unrelated = await owned.spawn("/bin/sleep", "10")
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
                        found = await finish_thread(processes.inventory, owner, listener.thread, list(seen.values()), operation, services)
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
                        await finish_thread(processes.inventory, owner, listener.thread, (), operation, services, [unrelated_identity])
                    except CoordinatorError as exc:
                        negative = exc.code == "PROCESS_AMBIGUOUS" and unrelated.pid in exc.details.get("pids", [])
                    completed = [e["params"]["item"] for e in listener.events if e.get("method") == "item/completed" and
                                 e["params"].get("item", {}).get("type") == "commandExecution"]
                    return {"positive_observed": bool(seen and reparented and not found and completed and completed[0].get("exitCode") == 0),
                            "negative_observed": negative, "command": command, "completed": completed,
                            "identities": list(seen.values()), "reparented": reparented[:1],
                            "marker_unavailable_pids": sorted(marker_failures), "settled": not found}
                finally:
                    await children.terminate(unrelated)

    async def probe_delivery(self, directory):
        async with serving(steps_router([function("exec_command", {"cmd": "sleep 1", "login": False, "yield_time_ms": 1000}), message()])) as provider:
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

    async def probe_inject(self, directory):
        """thread/inject_items in a writer's mode (code_mode_only, a Pre hook). Into a turn while its code-mode cell
        runs: the model's next request carries the note and shows the cell completed, and no other turn starts. Into
        an idle thread: no turn starts, and the next turn's first request carries it. Red: turn/steer of a note while
        the cell runs preempts it (core input_queue.rs watch_user_input), so the request after it shows the cell still
        running: the detector sees a preemption. The capability is "active" where both held, "idle" where only the idle
        thread did, else None (messages.deliver_notice)."""
        cell = ('await tools.exec_command({cmd: "/bin/sleep 3", yield_time_ms: 20000})'
                '.catch(e => text("refused " + String(e).slice(0, 120))); text("C1-CELL-COMPLETED");')
        start = "C1 inject probe: run the cell."
        def router(body, index):
            items = body.get("input") or []
            return action({"exec": cell}) if items and items[-1].get("type") == "message" and start in json.dumps(items[-1]) else message()
        def note(text):
            return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
        def after_cell(requests, text):
            # The request after the cell's call: whether its output shows the cell completed, and whether it carries the note.
            for body in requests:
                outputs = [i for i in body.get("input") or [] if i.get("type") == "custom_tool_call_output"]
                if outputs:
                    return {"completed": "C1-CELL-COMPLETED" in json.dumps(outputs), "carried": text in json.dumps(body["input"])}
            return {"completed": None, "carried": None}
        cases = {}
        for case in ("steer", "inject"):
            text = "C1 inject probe note: " + case
            async with serving(router) as provider:
                async with listening(directory / case, provider, hook="observe") as listener:
                    await listener.start_thread()
                    turn = await listener.turn(start, wait=False)
                    await listener.wait(lambda e: e.get("method") == "item/started" and e["params"]["item"]["type"] == "commandExecution")
                    await asyncio.sleep(0.5)
                    if case == "steer":
                        await listener.connection.call("turn/steer", {"threadId": listener.thread, "expectedTurnId": turn["turn"]["id"],
                                                                      "input": [{"type": "text", "text": text}]})
                    else:
                        await listener.connection.call("thread/inject_items", {"threadId": listener.thread, "items": [note(text)]})
                    await listener.wait(lambda e: e.get("method") == "turn/completed", timeout=60)
                    cases[case] = dict(after_cell(provider.requests, text), turns=sum(e.get("method") == "turn/started" for e in listener.events))
                    if case == "inject":
                        text, mark = "C1 inject probe note: idle", len(listener.events)
                        await listener.connection.call("thread/inject_items", {"threadId": listener.thread, "items": [note(text)]})
                        # Native starts a turn for pending work at once (tasks/mod.rs maybe_start_turn_for_pending_work):
                        # none in 1.5 s is none (measured on 0.163.0: none in 6 s).
                        await asyncio.sleep(1.5)
                        started, count = sum(e.get("method") == "turn/started" for e in listener.events[mark:]), len(provider.requests)
                        await listener.turn("C1 inject probe: next.")
                        cases["idle"] = {"turns": started, "carried": len(provider.requests) > count and text in json.dumps(provider.requests[count]["input"])}
        negative = cases["steer"]["completed"] is False and cases["steer"]["carried"] is True
        idle = cases["idle"]["turns"] == 0 and cases["idle"]["carried"] is True
        active = cases["inject"]["completed"] is True and cases["inject"]["carried"] is True and cases["inject"]["turns"] == 1
        capability = ("active" if active else "idle") if negative and idle else None
        return {"negative_observed": negative, "positive_observed": capability == "active", "capability": capability, "cases": cases}

    async def probe_dynamic(self, directory):
        specification = {"type": "function", "name": "c1_identity", "description": "Admission identity echo", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        async with serving() as provider:
            async with listening(directory / "bad", provider, experimental=False) as listener:
                try:
                    await listener.start_thread(dynamicTools=[specification])
                    negative = False
                except CoordinatorError:
                    negative = True
        async with serving(steps_router([function("c1_identity", {}), message()])) as provider:
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
            async with serving(router) as provider:
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
            async with serving(router) as provider:
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
        async with serving() as provider:
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
