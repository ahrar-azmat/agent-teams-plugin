"""Detached singleton supervisor, async JSONL RPC, and private native listeners."""
import asyncio
import base64
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import selectors
import socket
import sqlite3
import stat
import subprocess
import sys
import time

from .client import CoordinatorError, validate_state, venv_fds, launch_environment
from .store import Store, uid, digest, encoded
from .ledger import Ledger, normalize, patch_scope, read_path
from .custody import Custody
from . import native, tools, processes, runtime, participants, routing
from .locks import KeyedLocks

VERSION = runtime.plugin_version()  # in daemon/status, so that a newer client can retire this release

# A hook has 15 s: three 3 s calls (dispatch, retry, Bash arm), the measured
# 3.5 s replacement start, and 1 s draining leave 1.5 s for polling/transport.
# This bounds waiting for RPC settlement; occupied mutations refuse immediately.
STOP_DRAIN_S = 1.0
READ_RPC = {"daemon/status", "checkout/query", "checkout/revisions", "project/status",
            "run/status", "run/list", "run/result", "run/events", "run/terminals",
            "claim/list", "unknown/list", "message/inbox", "unit/status", "unit/diff", "records/list"}
COMPLETION_RPC = {"operation/complete", "custody/reconcile", "run/interrupt", "run/terminate-terminal", "review/complete"}


class Daemon:
    def __init__(self, root):
        self.store = Store(root)
        self.ledger = Ledger(self.store)
        self.custody = Custody(self.store, self.ledger)
        self.connections, self.queues, self.workers, self.tasks = {}, {}, {}, set()
        self.listener_requests = {}
        self.request_locks, self.actor_locks, self.unit_locks = KeyedLocks(), KeyedLocks(), KeyedLocks()
        self.lease_locks = KeyedLocks()
        self.ready = False
        self.recovery_barrier = True
        self.boot = uid("boot")
        self.draining = self.closing = False
        self.inflight, self.rpc_tasks = {}, set()
        self.stop = asyncio.Event()
        from .messages import Messages
        self.messages = Messages(self)
        from .writer import Writer
        self.writer = Writer(self)
        from .leases import Leases
        self.leases = Leases(self.store.root)
        from .runs import Runs
        self.runs = Runs(self)
        from .projects import Projects
        self.projects = Projects(self)

    async def ensure_lease(self, checkoutid):
        async with self.lease_locks.hold(checkoutid):
            checkout = self.store.get(checkoutid)
            evidence = await self.leases.call("acquire", checkout["root"])
            checkout["compatibility_lease"] = evidence
            self.store.save(checkout)
            return evidence

    async def release_lease(self, checkoutid, stopping=False):
        async with self.lease_locks.hold(checkoutid):
            await self._release_lease(checkoutid, stopping)

    async def _release_lease(self, checkoutid, stopping):
        checkout = self.store.get(checkoutid)
        if not checkout.get("compatibility_lease"):
            return
        runs = [r for r in self.store.all("run") if participants.contains(self.store, r["unit"], checkoutid) and r["mode"] == "native"]
        from .writer import TERMINAL
        if not stopping and any(r["state"] not in TERMINAL | {"idle"} for r in runs):
            return
        if any(a.get("active_turn") for a in self.store.all("actor") if participants.contains(self.store, a["unit"], checkoutid)) or self.custody.active(checkoutid):
            return
        for message in self.store.all("message"):
            if message["recipient"] == "lead" or message["recipient"].startswith("lead_mailbox_") or message["state"] in ("entered_context", "acknowledged"):
                continue
            recipient = self.store.get(message["recipient"])
            if participants.contains(self.store, recipient["unit"], checkoutid) and (message["delivery"] in ("submitting", "submitted", "uncertain")
                    or message["delivery"] == "pending" and message["wake"] != "none"):
                return  # A queued or uncertain native turn can start without another RPC.
        await self.leases.call("release", checkout["root"])
        checkout.pop("compatibility_lease", None)
        self.store.save(checkout)

    def background(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self.task_done)
        return task

    def task_done(self, task):
        if not task.cancelled() and task.exception():
            exception = task.exception()
            self.store.event("background/failure", None, {"code": getattr(exception, "code", type(exception).__name__)})

    def status(self, checkout=None):
        writers = []
        for run in self.store.all("run"):
            if checkout and not participants.contains(self.store, run["unit"], checkout):
                continue
            unit = self.store.get(run["unit"])
            writers.append({"run": run["id"], "unit": unit["id"], "task": run["task"], "state": run["state"],
                            "actors": [a for a in self.store.all("actor", unit["id"])],
                            "claims": [c for c in self.custody.claims(run["checkout"]) if c["unit"] == unit["id"]],
                            "custody": self.custody.unit_active(unit["id"]),
                            **participants.surface(self, unit),
                            "pending_requests": [r for kind in ("claim_request", "release_request", "custody_request")
                                                 for r in self.store.all(kind, unit["id"]) if r["state"] in ("pending", "pending_lead", "waiting")],
                            "messages": [m for m in self.store.all("message") if m.get("unit") == unit["id"] and m["state"] != "acknowledged"]})
        return {"ready": self.ready, "generation": self.store.generation, "pid": os.getpid(),
                "boot": self.boot, "capabilities": ["draining-stop"], "draining": self.draining,
                "birth": processes.identity(os.getpid())["birth"], "state_root": str(self.store.root),
                "endpoint": getattr(self, "endpoint", None),
                "locks": {"rpc": len(self.request_locks), "actor": len(self.actor_locks),
                          "message": len(self.messages.locks), "unit": len(self.unit_locks),
                          "checkout": len(self.ledger.lifecycle)},
                "sqlite": sqlite3.sqlite_version, "version": VERSION, "writers": writers}

    async def create_run(self, params, revision=None):
        if set(params) - {"checkout", "project", "cwd", "dispatch_cwd", "mode", "task", "paths", "model", "fixture", "workflow"} or params.get("mode", "native") not in ("lead", "native"):
            raise CoordinatorError("RUN_FIELDS", "run/start accepts checkout or project, cwd, dispatch_cwd, mode (lead or native), task, paths, model, fixture and workflow")
        if params.get("workflow") is not None:
            from .writer import descriptor
            params = dict(params, workflow=descriptor(params["workflow"]))
            if params.get("mode", "native") != "native":
                raise CoordinatorError("WORKFLOW_MODE", "Abraham uses native writers")
        if params.get("paths") is not None and (not isinstance(params["paths"], list) or any(not isinstance(p, (str, dict)) for p in params["paths"])):
            raise CoordinatorError("CLAIM_PATHS", "Claims must be a list of paths or omitted")
        params = dict(params)
        if params.get("project"):
            revision = revision or await self.projects.register(params)
            from .projects import containing
            cwd = params.get("cwd")
            candidates = [b for b in revision["bindings"] if cwd and containing(b["root"], cwd)]
            if not candidates:
                raise CoordinatorError("PROJECT_SCOPE", "Project run requires cwd inside a native root", {"roots": revision["roots"]})
            selected = max(candidates, key=lambda b: len(Path(b["root"]).parts))
            params.update(checkout=selected["checkout"], _revision=revision, _thread_cwd=selected["root"])
            ids = list(dict.fromkeys(b["checkout"] for b in revision["bindings"]))
        else:
            ids = [params["checkout"]]
        async with self.ledger.lifecycle.hold_many(ids):
            snapshots = {}
            for checkoutid in ids:
                checkout = self.store.get(checkoutid)
                try:
                    snapshots[checkoutid] = await self.admit_checkout(checkout, params)
                except CoordinatorError as exc:
                    raise CoordinatorError(exc.code, str(exc), dict(exc.details, root=checkout["root"]), exc.operational) from exc
            return await self.create_run_locked(params, snapshots)

    async def admit_checkout(self, checkout, params):
        self.ledger.require_owned(checkout)
        from .publish import Publisher
        for publication in list(self.store.all("publication")):
            if not publication.get("group") and self.store.get(publication["unit"])["checkout"] == checkout["id"] and publication["phase"] not in ("complete", "abandoned"):
                async with self.unit_locks.hold(publication["unit"]):
                    try:
                        await Publisher(self).reconcile(publication)
                    except CoordinatorError as exc:
                        # A recoverable journal is local to its unit. Its claims
                        # continue to exclude overlap without wedging the checkout.
                        self.store.event("publication/recovery_refused", publication["id"], exc.envelope())
        snapshot = await asyncio.to_thread(self.ledger._metadata, checkout["root"])
        if snapshot["identity"] != checkout["identity"] or snapshot["repository"] != checkout["repository"]:
            raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Checkout identity changed before unit creation")
        await self.ledger.reconcile(checkout)
        workflow = params.get("workflow")
        if workflow and not workflow["allow_dirty"]:
            from .ledger import overlaps
            claims = self.custody.claims(checkout["id"])
            def explained(path):
                return path != "*" and any(overlaps(normalize(checkout, path)["key"], normalize(checkout, c["path"])["key"]) for c in claims)
            unexplained = [u["path"] for u in self.store.unresolved(checkout["id"]) if not explained(u["path"])]
            from . import gitio
            index = await asyncio.to_thread(gitio.entries, checkout["root"])
            base_index = await asyncio.to_thread(gitio.entries, checkout["root"], snapshot["base"])
            unexplained += [p for p in set(index) | set(base_index) if index.get(p) != base_index.get(p) and not explained(p)]
            if unexplained:
                raise CoordinatorError("DIRTY_UNEXPLAINED", "Unexplained dirt requires allow_dirty=true; this grants no receipts", {"paths": sorted(set(unexplained))})
        return snapshot

    async def create_run_locked(self, params, snapshots):
        checkout = self.store.get(params["checkout"])
        snapshot = snapshots[checkout["id"]]
        workflow, revision = params.get("workflow"), params.get("_revision")
        thread_cwd = params.get("_thread_cwd", checkout["root"])
        project_fields = {"project": revision["project"], "project_revision": revision["id"], "generation": 1} if revision else {}
        ident, unitid, attemptid, actorid = uid("run"), uid("unit"), uid("attempt"), uid("actor")
        if workflow:
            ident = workflow["request_id"]
            if self.store.get(ident, required=False):
                raise CoordinatorError("RUN_EXISTS", "Collect the existing durable run; do not replay its dispatch", {"run": ident})
        lead = params.get("mode") == "lead"
        with self.store.transaction():
            self.store.put("unit", unitid, {"checkout": checkout["id"], "run": ident, "base": snapshot["base"],
                           "branch": snapshot["branch"], "state": "open", **project_fields}, checkout["id"])
            for position, (checkoutid, captured) in enumerate(snapshots.items()):
                bindings = [b for b in revision["bindings"] if b["checkout"] == checkoutid] if revision else []
                participants.capture(self.store, unitid, self.store.get(checkoutid), captured, bindings, position)
            self.store.put("run", ident, {"checkout": checkout["id"], "unit": unitid, "attempt": attemptid,
                           "root_actor": actorid, "task": params.get("task", ""), "state": "admitting", "mode": "lead" if lead else "native",
                           "created": time.time(), "workflow": workflow, "production_started": False,
                           "dispatch_cwd": params.get("dispatch_cwd") or (workflow["dispatch_cwd"] if workflow else checkout["root"]),
                           "target_cwd": params.get("cwd", checkout["root"]), "thread_cwd": thread_cwd,
                           "runtime_workspace_roots": revision["roots"] if revision else [checkout["root"]], **project_fields}, unitid)
            self.store.put("attempt", attemptid, {"run": ident, "unit": unitid, "generation": 1,
                           "daemon_generation": self.store.generation, "state": "starting"}, ident)
            self.store.put("actor", actorid, {"unit": unitid, "checkout": checkout["id"], "attempt": attemptid,
                           "run": ident, "kind": "lead" if lead else "root", "active_turn": None, "state": "idle", "cwd": thread_cwd}, unitid)
        if workflow and workflow.get("session_id"):
            from .lead import mailbox
            unit = self.store.get(unitid)
            unit["lead_mailbox"] = mailbox(self.store, workflow["session_id"], unitid)
            self.store.save(unit)
        actor = self.store.get(actorid)
        try:
            await self.custody.acquire(actor, params.get("paths") or [])
            if not lead:
                await self.launch_writer(actor, params)
            run = self.store.get(ident)
            if not workflow:
                run["state"] = "running" if not lead and params.get("task") else "idle"
            self.store.save(run)
            return {"run": ident, "unit": unitid, "attempt": attemptid, "actor": actorid,
                    "policy": self.store.get(actorid).get("policy"), **participants.surface(self, unitid)}
        except BaseException as exc:
            run = self.store.get(ident)
            if run.get("production_started"):
                self.store.event("workflow/dispatch_detached", ident, {"code": getattr(exc, "code", type(exc).__name__)})
                return {"run": ident, "unit": unitid, "actor": actorid, "state": run["state"], "reconcile_required": True}
            run.update(state="refused", refusal=getattr(exc, "code", type(exc).__name__))
            self.store.save(run)
            for listener in list(self.store.all("listener", ident)):
                await self.stop_listener(listener)
            for checkoutid in snapshots:
                for claim in self.custody.claims(checkoutid):
                    if claim["unit"] == unitid:
                        claim["state"] = "released"
                        self.store.save(claim)
                await self.release_lease(checkoutid)
            if isinstance(exc, CoordinatorError):
                raise CoordinatorError(exc.code, str(exc), dict(exc.details, safe_to_fallback=not bool(revision), run=ident), exc.operational) from exc
            raise CoordinatorError("NATIVE_START_" + type(exc).__name__, "Native startup failed before production execution; resources were released",
                                   {"safe_to_fallback": not bool(revision), "run": ident}, True) from exc

    def hook_config(self):
        command = shlex.join([sys.executable, "-B", str(Path(__file__).with_name("hooks.py")), "--state", str(self.store.root)])
        hook = {"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": 120, "async": False}]}
        return command, {"features.hooks": True, "hooks.PreToolUse": [hook], "hooks.PostToolUse": [hook]}

    async def spawn_listener(self, actor, config, home=None, incarnation=None):
        ident = incarnation or uid("listener")
        directory = self.store.root / "listeners" / ident
        directory.mkdir(mode=0o700, exist_ok=True)
        allocation = runtime.allocate("native")
        socket = Path(allocation["path"])
        binary = native.native_codex()
        environment = native.environment(SOFTWARE_WORKFLOWS_ROLE="writer")
        environment["LOG_FORMAT"] = "json"
        environment["RUST_LOG"] = "codex_core::exec_policy=trace"
        if home:
            environment["CODEX_HOME"] = str(home)
        process = await asyncio.create_subprocess_exec(binary, "app-server", "--listen", "unix://" + str(socket),
                                                       *native.override_args(config), cwd=actor.get("cwd", self.store.get(actor["checkout"])["root"]),
                                                       env=environment, stdin=asyncio.subprocess.DEVNULL,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                                                       start_new_session=True, close_fds=True)
        birth = await asyncio.to_thread(processes.identity, process.pid)
        if birth is None:
            code = await process.wait()
            runtime.cleanup(allocation)
            raise CoordinatorError("LISTENER_START", "Native listener exited before its process identity could be recorded", {"exit_code": code}, True)
        listener = self.store.put("listener", ident, {"actor": actor["id"], "run": actor["run"], "unit": actor["unit"],
                                  "socket": str(socket), "binary": binary, "binary_digest": await asyncio.to_thread(native.file_digest, binary),
                                  "endpoint_allocation": allocation,
                                  "pid": process.pid, "birth": birth["birth"], "incarnation": ident, "state": "starting",
                                  "kernel_id": birth.get("kernel_id"),
                                  "home": str(home) if home else None, "config": config,
                                  "hook_digest": native.file_digest(Path(__file__).with_name("hooks.py")),
                                  "generation": self.store.generation}, actor["run"])
        self.background(self.collect_policy_trace(ident, process.stderr))
        deadline = asyncio.get_running_loop().time() + 30
        while not socket.exists():
            if process.returncode is not None or asyncio.get_running_loop().time() >= deadline:
                raise CoordinatorError("LISTENER_START", "Native listener failed to start",
                                       {"exit_code": process.returncode, "path": str(socket),
                                        "bytes": len(os.fsencode(socket)), "measured_limit": runtime.capacity(),
                                        "diagnostic": self.store.get(ident).get("startup_diagnostic")}, True)
            await asyncio.sleep(0.02)
        try:
            await self.attach(self.store.get(ident))
        except BaseException:
            await self.stop_listener(self.store.get(ident))
            raise
        return self.store.get(ident)

    async def collect_policy_trace(self, listenerid, stream):
        async for line in stream:
            listener = self.store.get(listenerid)
            if listener["state"] == "starting":
                listener["startup_diagnostic"] = (listener.get("startup_diagnostic", []) + [native.startup_diagnostic(line)])[-8:]
                self.store.save(listener)
            try:
                fields = json.loads(line).get("fields", {})
                if fields.get("message") == "exec policy rules loaded":
                    listener = self.store.get(listenerid)
                    listener.setdefault("policy_traces", []).append(native.policy_trace_digest(fields.get("rules")))
                    listener.setdefault("policy_trace_observations", []).append(digest(fields.get("rules")))
                    self.store.save(listener)
            except (ValueError, UnicodeError):
                continue

    async def attach(self, listener):
        ident = listener["id"]
        await self.detach_listener(ident)
        queue = asyncio.Queue()
        self.queues[ident] = queue
        self.listener_requests[ident] = set()
        def inbound(message):
            # Ordered, durable evidence before scheduling expensive processing.
            self.store.event("native", ident, message)
            queue.put_nowait(message)
        from .admission import Admission
        schema, _ = await Admission(self.store).schema()
        try:
            connection = await native.Connection.connect(listener["socket"], inbound, native.Schema(schema))
            self.connections[ident] = connection
            self.workers[ident] = self.background(self.native_worker(listener, queue))
            initialized = await connection.initialize()
        except BaseException:
            await self.detach_listener(ident)
            raise
        listener.update(state="connected", user_agent=initialized.get("userAgent"), generation=self.store.generation)
        listener["endpoint"] = runtime.endpoint_info(listener["socket"], native=True)
        self.store.save(listener)
        return connection

    async def detach_listener(self, ident):
        """Close intake, drain journaled evidence, then remove the consumer.

        Native requests can be blocked on custody. Their durable pending records
        are recovery evidence; cancellation must not re-execute them.
        """
        connection = self.connections.get(ident)
        if connection:
            await connection.close()
        worker, queue = self.workers.get(ident), self.queues.get(ident)
        current = asyncio.current_task()
        if worker is current:
            # Run teardown after this notification's task_done, never self-await.
            self.background(self.detach_listener(ident))
            return
        if worker and not worker.done() and queue:
            await queue.join()
        requests = self.listener_requests.pop(ident, set())
        for request in requests:
            if request is not current:
                request.cancel()
        await asyncio.gather(*(r for r in requests if r is not current), return_exceptions=True)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.connections.pop(ident, None)
        self.workers.pop(ident, None)
        self.queues.pop(ident, None)

    async def launch_writer(self, actor, params):
        for checkoutid in participants.checkout_ids(self.store, actor["unit"]):
            await self.ensure_lease(checkoutid)
        roots = participants.roots(self.store, actor["unit"])
        unit = self.store.get(actor["unit"])
        cwd = self.store.get(actor["run"]).get("thread_cwd", self.store.get(actor["checkout"])["root"])
        from .admission import Admission
        admission = Admission(self.store)
        fixture = params.get("fixture")
        home, config = None, {}
        if fixture:
            from .fixture import runtime_configuration
            home, config = runtime_configuration(fixture, actor["run"])
        command, hooks = self.hook_config()
        config.update(hooks)
        config.update({"web_search": "live", "agents.enabled": True, "features.multi_agent_v2": True,
                       "features.multi_agent_v2_dynamic_tools": True, "features.unified_exec_tty": False,
                       "approval_policy": "never", "sandbox_mode": "danger-full-access"})
        config.update({"model_reasoning_effort": "max", "model_reasoning_summary": "detailed"})
        workflow = params.get("workflow")
        if workflow and workflow["compact_limit"]:
            config["model_auto_compact_token_limit"] = workflow["compact_limit"]
        listener = await self.spawn_listener(actor, config, home)
        connection = self.connections[listener["id"]]
        listed = await connection.call("hooks/list", {"cwds": roots})
        trust = {}
        for entry in listed["data"]:
            if entry.get("errors"):
                raise CoordinatorError("HOOK_CONFIG", "Native hooks/list reports errors")
            for hook in entry["hooks"]:
                if hook.get("source") == "sessionFlags" and hook.get("command") == command:
                    trust[hook["key"]] = {"trusted_hash": hook["currentHash"]}
        if len(trust) != 2:
            raise CoordinatorError("HOOK_DEFINITIONS", "Cannot identify exactly the coordinator's pre/post hook definitions")
        # Restart only the idle, threadless discovery listener. Unrelated hook state is
        # merged from the native config API, never read from CODEX_HOME files.
        effective = await connection.call("config/read", {"cwd": cwd, "includeLayers": False})
        from .roles import overlay, apply
        config = apply(config, overlay(effective.get("config", {})))
        prior_trust = effective.get("config", {}).get("hooks", {}).get("state", {})
        config.setdefault("hooks", {})["state"] = dict(prior_trust, **trust)
        await self.stop_listener(listener)
        listener = await self.spawn_listener(actor, config, home)
        connection = self.connections[listener["id"]]
        listed = await connection.call("hooks/list", {"cwds": roots})
        own = native.coordinator_hooks(listed, roots, command=command)
        if len(own) != 2 or any(h["trustStatus"] != "trusted" or not h["enabled"] for h in own):
            raise CoordinatorError("HOOK_TRUST", "Precise native hook trust was not honored")
        listener.update(hooks=own, config_digest=digest(effective))
        self.store.save(listener)
        actor["listener"] = listener["id"]
        self.store.save(actor)
        response = await connection.call("thread/start", {"cwd": cwd,
                                          **({"projectId": unit["project"], "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {}),
                                          "model": workflow["model"] if workflow else params.get("model", "gpt-6-astra"), "approvalPolicy": "never",
                                          "sandbox": "danger-full-access", "dynamicTools": tools.specifications(),
                                          "developerInstructions": "Coordinate through claim, declare, send, inbox, ack and status. "
                                          "Use apply_patch for attributable edits. Commands have opaque effects. "
                                          "Never publish or rewrite Git state: the lead records. "
                                          "Messages require ack from the named recipient. Child fallback ack format: C1_ACK <message-id>. "
                                          + "Workspace roots in order: " + json.dumps(roots) + ". Paths are absolute or relative to the actual tool cwd. "
                                          "Qualified coordinator paths use {root_id: checkout-id, path: repository-relative-path}."})
        from .projects import verify_thread
        verify_thread(response, unit, cwd, roots)
        actor.update(thread=response["thread"]["id"], session=response["thread"]["id"], policy=native.policy_echo(response))
        self.store.save(actor)
        listener = self.store.get(listener["id"])
        listener["root_thread"] = actor["thread"]
        listener["services"] = await asyncio.to_thread(processes.inventory, listener, None)
        self.store.save(listener)
        admitted = await admission.writer_gate(self, listener["id"], response, fixture=bool(fixture))
        run = self.store.get(actor["run"])
        run.update(admission=admitted, rollout=response["thread"].get("path"))
        self.store.save(run)
        if workflow:
            self.writer.prepare(actor["run"], "analysis")
            await self.writer.submit(actor["run"])
        elif params.get("task"):
            # Durable before submission: a lost reply may follow execution, so it must not release claims.
            run["production_started"] = True
            self.store.save(run)
            turn = await connection.call("turn/start", {"threadId": actor["thread"],
                                                       "input": [{"type": "text", "text": params["task"]}]})
            actor = self.store.get(actor["id"])
            actor["active_turn"] = turn["turn"]["id"]
            actor["state"] = "running"
            self.store.save(actor)

    async def stop_listener(self, listener):
        listener = self.store.get(listener["id"])
        if listener["state"] == "stopped" and listener.get("allocation_cleaned"):
            return
        listener["state"] = "stopping"
        self.store.save(listener)
        await self.detach_listener(listener["id"])
        current = await asyncio.to_thread(processes.identity, listener["pid"])
        if current and current["birth"] == listener["birth"]:
            os.kill(listener["pid"], signal.SIGTERM)
            for _ in range(100):
                current = await asyncio.to_thread(processes.identity, listener["pid"])
                if not current or current["zombie"]:
                    break
                await asyncio.sleep(0.05)
            else:
                raise CoordinatorError("LISTENER_DRAIN", "Native listener did not finish draining; retained ownership")
        if listener.get("endpoint_allocation"):
            if Path(listener["endpoint_allocation"]["directory"]).exists():
                runtime.cleanup(listener["endpoint_allocation"])
        listener = self.store.get(listener["id"])
        listener.update(state="stopped", allocation_cleaned=True)
        self.store.save(listener)

    async def resolve_actor(self, listener, thread, session=None):
        async with self.actor_locks.hold((listener["id"], thread)):
            return await self.resolve_actor_locked(listener, thread, session)

    async def discover_descendants(self, listener):
        connection = self.connections[listener["id"]]
        async for page in native.page(connection, "thread/loaded/list", {}):
            for entry in page["data"]:
                await self.resolve_actor(listener, entry if isinstance(entry, str) else entry["id"])
        for archived in (False, True):
            async for page in native.page(connection, "thread/list", {"ancestorThreadId": listener["root_thread"], "archived": archived}):
                for entry in page["data"]:
                    await self.resolve_actor(listener, entry["id"])

    async def resolve_actor_locked(self, listener, thread, session=None):
        for actor in self.store.all("actor", listener["unit"]):
            if actor.get("thread") == thread and actor.get("listener") == listener["id"]:
                return actor
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            try:
                response = await self.connections[listener["id"]].call("thread/read", {"threadId": thread, "includeTurns": False})
                break
            except CoordinatorError as exc:
                if exc.code != "NATIVE_RPC" or asyncio.get_running_loop().time() >= deadline:
                    raise
                # Native V2 announces a child before its thread/read materializes.
                # Keep the identity unresolved rather than guessing its parent.
                await asyncio.sleep(0.02)
        info = response["thread"]
        root = listener.get("root_thread") or self.store.get(listener["actor"]).get("thread")
        if info.get("sessionId") != root or not info.get("parentThreadId"):
            raise CoordinatorError("ACTOR_NAMESPACE", "Native thread cannot be bound to this listener's root")
        if session and session != root:
            raise CoordinatorError("ACTOR_SESSION", "Hook session does not match native root")
        parent = await self.resolve_actor(listener, info["parentThreadId"])
        root_actor = self.store.get(listener["actor"])
        unit = self.store.get(root_actor["unit"])
        if unit.get("project_revision") and info.get("projectId") not in (None, unit["project"]):
            raise CoordinatorError("PROJECT_THREAD_MISMATCH", "Native child belongs to another project")
        actorid = uid("actor")
        self.store.put("actor", actorid, {"unit": root_actor["unit"], "checkout": root_actor["checkout"],
                       "attempt": root_actor["attempt"], "run": root_actor["run"], "kind": "child",
                       "parent_actor": parent["id"], "listener": listener["id"], "thread": thread, "session": root,
                       "active_turn": None, "state": "unknown", "cwd": info.get("cwd", root_actor.get("cwd")),
                       "native_project": info.get("projectId")}, root_actor["unit"])
        return self.store.get(actorid)

    def find_operation(self, listener, thread, turn, call, history=False):
        incarnations = {listener["id"]}
        prior = listener
        while history and prior.get("replaces"):
            prior = self.store.get(prior["replaces"])
            incarnations.add(prior["id"])
        return next((op for op in self.store.all("operation", listener["unit"])
                     if op["native"].get("listener") in incarnations and
                     all(op["native"].get(key) == value for key, value in (("thread", thread), ("turn", turn), ("call", call)))), None)

    async def services_before_first_command(self, listener):
        listener = self.store.get(listener["id"])
        if listener.get("service_boundary"):
            return
        # Native can lazily start infrastructure (including its code-mode host)
        # after thread/start. Inventory it before the first opaque invocation,
        # while all command/MCP pre-hooks for this listener are serialized. Once
        # opaque execution is possible, a later survivor cannot become a service
        # merely because it outlives a command or has a familiar executable name.
        prior = any(op["kind"] in ("command", "mcp") and op.get("native", {}).get("listener") == listener["id"]
                    for op in self.store.all("operation", listener["unit"]))
        services = None if prior else await asyncio.to_thread(processes.inventory, listener, None)
        listener = self.store.get(listener["id"])
        if services is not None:
            listener["services"] = services
        listener["service_boundary"] = {"time": time.time(), "generation": self.store.generation,
                                        "captured": services is not None, "prior_opaque_operations": prior}
        self.store.save(listener)
        self.store.event("listener/service_boundary", listener["id"], listener["service_boundary"])

    async def hook(self, payload, hook_process=None):
        session, thread = payload.get("session_id"), payload.get("agent_id") or payload.get("session_id")
        listener = next((l for l in self.store.all("listener") if l.get("root_thread") == session and l["state"] == "connected"), None)
        if not listener:
            raise CoordinatorError("HOOK_IDENTITY", "No live native listener owns this hook session")
        if not listener.get("admission") or listener.get("recovery_state") in ("reconciling", "failed"):
            raise CoordinatorError("LISTENER_RECOVERING", "Listener admission and reconciliation must finish before a new tool invocation")
        if native.file_digest(Path(__file__).with_name("hooks.py")) != listener["hook_digest"]:
            raise CoordinatorError("HOOK_PROGRAM_CHANGED", "Hook program changed since admission")
        if hook_process:
            if not await asyncio.to_thread(processes.descendant, hook_process, listener):
                raise CoordinatorError("HOOK_PROCESS", "Hook client is not a birth-verified listener descendant")
            listener = self.store.get(listener["id"])
            helpers = []
            for helper in listener.get("helpers", []):
                current = processes.identity(helper["pid"])
                if current and not current["zombie"] and current["birth"] == helper["birth"]:
                    helpers.append(helper)
            listener["helpers"] = helpers + [dict(hook_process, role="coordinator-hook")]
            self.store.save(listener)
        actor = await self.resolve_actor(listener, thread, session)
        tool, event = payload.get("tool_name"), payload.get("hook_event_name")
        call, turn = payload.get("tool_use_id"), payload.get("turn_id")
        if not call or not turn:
            raise CoordinatorError("HOOK_IDENTITY", "Native hook lacks turn or tool-use identity")
        operation = self.find_operation(listener, thread, turn, call)
        if event == "PostToolUse":
            if operation:
                operation["post_payload"] = payload.get("tool_input")
                operation["post_received"] = True
                self.store.save(operation)
            return {}
        if event != "PreToolUse":
            return {}
        if operation:
            # A generation boundary cannot replay an allow decision for an uncertain mutation.
            if operation["generation"] != self.store.generation or operation["state"] not in ("prepared", "allowed"):
                raise CoordinatorError("HOOK_REPLAY", "Previous invocation must be reconciled before retry")
            return {}
        identity = {"listener": listener["id"], "thread": thread, "turn": turn, "call": call}
        command = payload.get("tool_input", {}).get("command", "")
        if tool == "apply_patch":
            run = self.store.get(actor["run"])
            if run.get("workflow") and run.get("phase") == "analysis":
                self.store.event("workflow/analysis_patch_denied", run["id"], {"actor": actor["id"], "native": identity})
                raise CoordinatorError("ANALYSIS_READ_ONLY", "Analysis cannot apply patches. Finish the implementation brief; the coordinator starts the writing turn after settlement.")
            cwd = payload.get("cwd") or actor.get("cwd") or self.store.get(actor["checkout"])["root"]
            if self.store.get(actor["unit"]).get("project_revision"):
                from .operations import prepare_patch
                # Completion notifications can still be settling a previous
                # command when the next native hook arrives. Prove the whole
                # command group settled before attempting any path claims.
                await self.settle_actor(actor)
                operation = await prepare_patch(self, actor, command, identity, cwd)
            else:
                scope = patch_scope(command)
                for entry in scope:
                    entry["path"] = routing.route(self.store, actor, entry["path"], cwd)["path"]
                    if "move" in entry:
                        entry["move"] = routing.route(self.store, actor, entry["move"], cwd)["path"]
                paths = [p for entry in scope for p in (entry["path"], entry.get("move")) if p]
                await self.custody.acquire(actor, [routing.qualified(actor["checkout"], p) for p in paths])
                operation = await self.ledger.prepare(actor, "patch", {"patch": command, "scope": scope}, paths, identity)
        elif tool in ("Bash", "exec_command") or str(tool).startswith("mcp"):
            # A hook's command omits workdir and tty. Never infer them. Full-checkout
            # reconciliation and process identity supply the conservative context.
            async with self.actor_locks.hold("services:" + listener["id"]):
                await self.services_before_first_command(listener)
                operation = await self.ledger.prepare(actor, "command" if tool in ("Bash", "exec_command") else "mcp",
                                                      {"tool_input": payload.get("tool_input"), "tool": tool}, [], identity)
            try:
                reservation = await self.custody.reserve(actor, command or "opaque MCP " + str(tool), operation["id"])
            except CoordinatorError as exc:
                operation = self.store.get(operation["id"])
                operation.update(state="quarantined" if operation.get("started_item") else "denied", decision="deny", refusal=exc.code)
                self.store.save(operation)
                raise
            reservation["operation"] = operation["id"]
            self.store.save(reservation)
            operation["custody"] = reservation["id"]
        else:
            # Collaboration and coordinator dynamic tools have their own contracts.
            cooperation = {"collaboration" + name for name in
                           ("spawn_agent", "send_message", "followup_task", "wait_agent", "list_agents", "interrupt_agent")}
            readers = {"web_search", "websearch", "web.run", "view_image", "update_plan", "request_user_input", "sleep"}
            if tool not in set(tools.CONTRACTS) | cooperation | readers:
                raise CoordinatorError("HOOK_TOOL_UNSUPPORTED", "No complete custody/effect contract for this native tool", {"tool": tool})
            self.store.event("hook/nonmutator", actor["id"], {"tool": tool, "native": identity})
            return {}
        operation["state"] = "allowed"
        operation["decision"] = "allow"
        self.store.save(operation)
        self.store.event("hook/decision", operation["id"], {"decision": "allow", "native": identity})
        # Native 0.163 treats permissionDecision=allow without updatedInput as a
        # hook failure. Empty successful output means continue, after our durable
        # allow decision; the matching completed lifecycle proves delivery.
        return {}

    async def native_worker(self, initial, queue):
        while True:
            message = await queue.get()
            listener = self.store.get(initial["id"])
            try:
                if "id" in message:
                    task = self.background(self.native_request(listener, message))
                    pending = self.listener_requests.setdefault(listener["id"], set())
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                else:
                    await self.notification(listener, message["method"], message.get("params", {}))
            except Exception as exc:
                self.store.event("native/reconcile_failure", listener["id"], {"code": getattr(exc, "code", type(exc).__name__)})
                for key in participants.checkout_ids(self.store, listener["unit"]):
                    self.store.unknown(key, "*", "native evidence processing failed",
                                       {"listener": listener["id"], "code": getattr(exc, "code", type(exc).__name__)})
            finally:
                queue.task_done()

    async def native_request(self, listener, message):
        connection = self.connections.get(listener["id"])
        if not connection:
            return
        method, params = message["method"], message.get("params", {})
        if method != "item/tool/call":
            await connection.send({"id": message["id"], "error": {"code": -32601,
                                    "message": "Unexpected server request under never/full access; coordinator refuses"}})
            for key in participants.checkout_ids(self.store, listener["unit"]):
                self.store.unknown(key, "*", "unexpected native server request", {"method": method})
            return
        ident = "dynamic:" + listener["id"] + ":" + params["threadId"] + ":" + params["callId"]
        existing = self.store.get(ident, required=False)
        if existing and existing.get("response"):
            await connection.send({"id": message["id"], "result": existing["response"]})
            return
        if existing:
            # A completed effect may precede its durable response. Never execute
            # the tool again merely because the native server repeats the request.
            response = tools.result({"error": {"code": "DYNAMIC_UNCERTAIN", "message":
                                    "Previous tool invocation has uncertain completion; inspect durable status before a new request."}}, False)
            existing.update(state="uncertain", response=response)
            self.store.save(existing)
            self.store.event("dynamic/replay_refused", ident, {"generation": self.store.generation})
            await connection.send({"id": message["id"], "result": response})
            return
        actor = await self.resolve_actor(listener, params["threadId"])
        self.store.put("dynamic", ident, {"actor": actor["id"], "native": params, "state": "pending"}, actor["unit"])
        try:
            response = tools.result(await tools.dispatch(self, actor, params["tool"], params["arguments"]))
        except CoordinatorError as exc:
            response = tools.result({"error": exc.envelope()}, False)
        self.store.put("dynamic", ident, {"actor": actor["id"], "state": "completed", "response": response})
        await connection.send({"id": message["id"], "result": response})

    async def notification(self, listener, method, params):
        if method == "coordinator/transportGap":
            if listener["state"] not in ("stopping", "stopped"):
                listener.update(state="disconnected", recovery_state="reconciling")
                self.store.save(listener)
                for key in participants.checkout_ids(self.store, listener["unit"]):
                    self.store.unknown(key, "*", "native notification gap", {"listener": listener["id"]})
                from .recovery import reconcile_listener
                self.background(reconcile_listener(self, listener))
            return
        thread = params.get("threadId")
        if not thread or method not in ("turn/started", "turn/completed", "hook/completed", "item/started", "item/completed", "item/commandExecution/terminalInteraction"):
            return
        actor = await self.resolve_actor(listener, thread)
        self.writer.observe(actor, method, params)
        turn = params.get("turnId") or params.get("turn", {}).get("id")
        if method == "turn/started":
            actor.update(active_turn=turn, state="running")
            self.store.save(actor)
        elif method == "turn/completed":
            actor.update(active_turn=None, state=params["turn"]["status"])
            self.store.save(actor)
            run = self.store.get(actor["run"])
            if actor["kind"] == "root" and not run.get("workflow"):
                run["state"] = params["turn"]["status"]
                self.store.save(run)
            for operation in list(self.store.all("operation", actor["unit"])):
                if (operation["actor"] == actor["id"] and operation["kind"] == "patch" and
                        operation["native"].get("turn") == turn and operation["state"] in ("prepared", "allowed", "running")):
                    await self.maybe_verify(operation)
                    if self.store.get(operation["id"])["state"] in ("prepared", "allowed", "running"):
                        await self.ledger.quarantine(self.store.get(operation["id"]), "turn ended without complete patch lifecycle evidence")
            await self.settle_actor(actor)
            await asyncio.gather(*(self.ledger.reconcile(self.store.get(key)) for key in participants.checkout_ids(self.store, actor["unit"])))
            if run.get("workflow"):
                self.background(self.writer.advance(run["id"]))
        elif method == "hook/completed":
            operation = self.record_hook_evidence(listener, params)
            if operation:
                await self.maybe_verify(operation)
        elif method in ("item/started", "item/completed"):
            item = params["item"]
            kind = item["type"]
            if kind not in {"userMessage", "hookPrompt", "agentMessage", "functionCallOutput", "plan", "reasoning",
                            "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall",
                            "subAgentActivity", "webSearch", "imageView", "sleep", "imageGeneration",
                            "enteredReviewMode", "exitedReviewMode", "contextCompaction"}:
                raise CoordinatorError("NATIVE_ITEM_SEMANTICS", "Unknown correctness-critical native item kind", {"kind": kind})
            if kind == "userMessage" and item.get("clientId"):
                self.messages.entered(actor["id"], item["clientId"], {"listener": listener["id"], "turn": turn, "item": item["id"]})
            elif kind == "subAgentActivity":
                await self.resolve_actor(listener, item["agentThreadId"])
            elif kind == "agentMessage" and actor["kind"] == "child":
                self.messages.child_ack(actor, item)
            elif kind in ("fileChange", "commandExecution", "mcpToolCall"):
                operation = self.find_operation(listener, thread, turn, item["id"])
                if not operation or not operation.get("hook_valid"):
                    for key in participants.checkout_ids(self.store, actor["unit"]):
                        self.store.unknown(key, "*", "UNMEDIATED effect-capable native item", {"listener": listener["id"], "thread": thread, "item": item["id"]})
                    if not operation:
                        # A failed-open hook still opens an opaque effect window.
                        # Record it even if a conflicting reservation already exists:
                        # the effect has happened and cannot be refused retroactively.
                        operationid = uid("op")
                        self.store.put("operation", operationid, {"actor": actor["id"], "unit": actor["unit"],
                            "checkout": actor["checkout"], "kind": "command" if kind == "commandExecution" else "mcp",
                            "state": "quarantined", "attribution": "UNKNOWN", "paths": [], "bases": {},
                            "native": {"listener": listener["id"], "thread": thread, "turn": turn, "call": item["id"]},
                            "generation": self.store.generation, "unmediated": True}, actor["unit"])
                        operation = self.store.get(operationid)
                    if not operation.get("custody"):
                        custodyid = uid("custody")
                        operation.update(custody=custodyid, unmediated=True)
                        self.store.save(operation)
                        members = []
                        with self.store.transaction():
                            for index, key in enumerate(participants.checkout_ids(self.store, actor["unit"])):
                                ident = uid("custody") if index else custodyid
                                members.append(ident)
                                self.store.put("custody", ident, {"checkout": key, "unit": actor["unit"],
                                    "actor": actor["id"], "operation": operation["id"], "command": "UNMEDIATED " + kind,
                                    "level": "exclusive", "excludes": [], "state": "held", "created": time.time(), "group": custodyid,
                                    "terminals": [], "processes": [], "generation": self.store.generation}, actor["unit"])
                            reservation = self.store.get(custodyid)
                            reservation["members"] = members
                            self.store.save(reservation)
                operation["started_item" if method == "item/started" else "completed_item"] = item
                if operation["state"] not in ("verified", "quarantined", "resolved"):
                    operation["state"] = "running"
                self.store.save(operation)
                if item.get("processId") and operation.get("custody"):
                    reservation = self.store.get(operation["custody"])
                    reservation["terminals"] = sorted(set(reservation["terminals"] + [item["processId"]]))
                    self.store.save(reservation)
                    self.background(self.observe_processes(actor, reservation))
                await self.maybe_verify(operation)
        elif method == "item/commandExecution/terminalInteraction" and params.get("stdin"):
            operation = self.find_operation(listener, thread, turn, params["itemId"])
            if operation and operation.get("custody"):
                self.store.event("command/stdin", operation["id"], {"processId": params["processId"], "stdin": params["stdin"]})
            else:
                for key in participants.checkout_ids(self.store, actor["unit"]):
                    self.store.unknown(key, "*", "UNMEDIATED nonempty write_stdin", {"listener": listener["id"]})

    def record_hook_evidence(self, listener, params, history=False):
        run = params["run"]
        call = run["id"].rsplit(":", 1)[-1]
        operation = self.find_operation(listener, params["threadId"], params.get("turnId"), call, history=history)
        own = (run.get("source") == "sessionFlags" and any(
               all(run.get(key) == hook.get(key) for key in
                   ("source", "sourcePath", "eventName", "displayOrder", "handlerType"))
               for hook in listener.get("hooks", [])))
        if not operation or not own:
            return None
        valid = run.get("status") == "completed" and not any(e.get("kind") in ("error", "warning") for e in run.get("entries", []))
        if run.get("eventName") == "preToolUse":
            operation["hook_valid"] = valid and operation.get("decision") == "allow"
        elif run.get("eventName") == "postToolUse":
            operation["post_hook"] = valid and operation.get("post_received", False)
        if not any(e["id"] == run["id"] for e in operation.get("hook_evidence", [])):
            operation.setdefault("hook_evidence", []).append(run)
        self.store.save(operation)
        return operation

    async def maybe_verify(self, operation):
        if operation["state"] in ("verified", "quarantined"):
            return
        if (operation["kind"] == "patch" and operation.get("completed_item") and
                (operation.get("post_hook") or operation["completed_item"].get("status") != "completed")):
            await self.ledger.complete_patch(operation, operation["completed_item"])
        elif operation["kind"] in ("command", "mcp") and operation.get("completed_item") and operation.get("post_hook"):
            await self.settle_actor(self.store.get(operation["actor"]))

    async def observe_processes(self, actor, reservation, resolve_quarantine=False, terminals=None, retry_helpers=True):
        listener = self.store.get(actor["listener"])
        try:
            operation = self.store.get(reservation["operation"])
            if terminals is None:
                response = await self.connections[listener["id"]].call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
                if response.get("nextCursor"):
                    raise CoordinatorError("TERMINAL_PAGINATION", "Native terminal inventory is incomplete")
                terminals = response["data"]
            # Bind every sibling terminal before inventory: a markerless native
            # child (including macOS system binaries) still has an exact osPid.
            for other in self.custody.unit_active(actor["unit"]):
                if other["actor"] != actor["id"] or not other.get("operation"):
                    continue
                intent = self.store.get(other["operation"])
                observed = {(p["pid"], p["birth"]): p for p in other.get("observed_processes", [])}
                for terminal in terminals:
                    if terminal.get("itemId") != intent["native"].get("call") and terminal.get("processId") not in other["terminals"]:
                        continue
                    root = await asyncio.to_thread(processes.terminal_root, listener, intent, terminal, list(observed.values()))
                    if root:
                        observed[(root["pid"], root["birth"])] = root
                other = self.store.get(other["id"])
                observed.update({(p["pid"], p["birth"]): p for p in other.get("observed_processes", [])})
                other["observed_processes"] = list(observed.values())
                self.store.save(other)
            reservation = self.store.get(reservation["id"])
            listener = self.store.get(listener["id"])
            separate = listener.get("services", []) + listener.get("helpers", [])
            # The descriptor channel attests this exact controller birth. A
            # replacement custodian is allowed; none of its future children
            # inherit that exemption.
            if self.leases.controller:
                separate.append(self.leases.controller)
            for service_listener in self.store.all("listener"):
                separate += service_listener.get("services", []) + service_listener.get("helpers", [])
            for other in self.custody.unit_active(actor["unit"]):
                if other["id"] != reservation["id"] and other.get("group", other["id"]) != reservation.get("group", reservation["id"]) and self.store.get(other["actor"]).get("listener") == listener["id"]:
                    separate += [p for p in other.get("observed_processes", []) if p.get("ownership") != "listener-domain"]
            found = await asyncio.to_thread(processes.inventory, listener, actor["session"],
                                            reservation.get("observed_processes", reservation.get("processes", [])),
                                            operation, separate, reservation.get("ambiguous_processes", []), reservation.get("process_baseline", []))
            reservation = self.store.get(reservation["id"])
            reservation["processes"] = found
            observed = {(p["pid"], p["birth"]): p for p in reservation.get("observed_processes", [])}
            observed.update({(p["pid"], p["birth"]): p for p in found})
            reservation["observed_processes"] = list(observed.values())
            reservation["ambiguous_processes"] = []
            listener = self.store.get(listener["id"])
            lineage = {(p["pid"], p["birth"]): p for p in listener.get("process_lineage", [])}
            lineage.update({(p["pid"], p["birth"]): p for p in found})
            listener["process_lineage"] = list(lineage.values())
            self.store.save(listener)
            process_uncertainty = reservation.get("reason") in ("PROCESS_AMBIGUOUS", "PROCESS_UNREADABLE", "PROCESS_KERNEL_IDENTITY", "PROCESS_SCAN", "PROCESS_REUSED", "PROCESS_ENV")
            if (resolve_quarantine or process_uncertainty) and not found and reservation["state"] == "quarantined":
                self.store.event("custody/process_reconciled", reservation["id"], {"previous_reason": reservation.get("reason"), "survivors": []})
                reservation["state"] = "held"
            self.store.save(reservation)
        except CoordinatorError as exc:
            if retry_helpers and exc.code == "PROCESS_AMBIGUOUS" and exc.details.get("processes"):
                # Inventory can race the post-hook's connection: the process
                # exists before its birth-verified RPC attests it as our helper.
                # Custody stays held. Neither disappearance nor elapsed time is
                # evidence; retry only after every ambiguous birth is attested.
                deadline = asyncio.get_running_loop().time() + 0.25
                while True:
                    helpers = self.store.get(listener["id"]).get("helpers", [])
                    if all(any(p["pid"] == h["pid"] and p["birth"] == h["birth"] for h in helpers)
                           for p in exc.details["processes"]):
                        return await self.observe_processes(actor, reservation, resolve_quarantine, terminals, False)
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                    await asyncio.sleep(0.01)
            reservation = self.store.get(reservation["id"])
            ambiguous = {(p["pid"], p["birth"]): p for p in reservation.get("ambiguous_processes", [])}
            ambiguous.update({(p["pid"], p["birth"]): p for p in exc.details.get("processes", [])})
            reservation["ambiguous_processes"] = list(ambiguous.values())
            if reservation["state"] != "quarantined":
                self.store.event("custody/process_uncertain", reservation["id"], {"code": exc.code})
            reservation.update(state="quarantined", reason=exc.code, process_error=exc.details, unmediated=True)
            self.store.save(reservation)

    async def settle_actor(self, actor, resolve_quarantine=False):
        async with self.actor_locks.hold("settle:" + actor["id"]):
            await self._settle_actor(actor, resolve_quarantine)

    async def _settle_actor(self, actor, resolve_quarantine):
        if not actor.get("listener"):
            return
        listener = self.store.get(actor["listener"])
        connection = self.connections.get(listener["id"])
        if not connection or connection.closed:
            return
        terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
        if terminals.get("nextCursor"):
            raise CoordinatorError("TERMINAL_PAGINATION", "Native terminal inventory is incomplete")
        settled_groups = set()
        for reservation in self.custody.unit_active(actor["unit"]):
            if reservation["actor"] != actor["id"]:
                continue
            group = reservation.get("group", reservation["id"])
            if group in settled_groups:
                continue
            settled_groups.add(group)
            reservation = self.store.get(group)
            members = reservation.get("members", [reservation["id"]])
            await self.observe_processes(actor, reservation, resolve_quarantine=resolve_quarantine, terminals=terminals["data"])
            reservation = self.store.get(reservation["id"])
            operation = self.store.get(reservation.get("operation"), required=False)
            if not operation:
                continue  # no inference that an interrupted hook intent had no effects
            own_terminals = [t for t in terminals["data"] if t.get("itemId") == operation["native"].get("call") or
                             t.get("processId") in reservation["terminals"]]
            if resolve_quarantine and not reservation["processes"] and not own_terminals and not operation.get("completed_item"):
                metadata = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
                if metadata["thread"].get("status", {}).get("type") in ("idle", "notLoaded") and not actor.get("active_turn"):
                    operation["completed_item"] = {"exitCode": None, "observedGone": True, "missingNativeCompletion": True}
                    self.store.save(operation)
                    for key in members:
                        self.store.unknown(self.store.get(key)["checkout"], "*", "lead reconciled absence of survivors without native completion", {"custody": key})
            if reservation["state"] == "quarantined" or reservation["processes"] or own_terminals or not operation.get("completed_item"):
                continue
            await asyncio.gather(*(self.ledger.reconcile(self.store.get(self.store.get(key)["checkout"]),
                                                        reason="opaque command effects") for key in members))
            operation["state"] = "reconciled"
            operation["attribution"] = "UNKNOWN-context-only"
            self.store.save(operation)
            for key in members:
                await self.custody.relinquish(self.store.get(key))

    async def lead_edit(self, params, restore=False):
        actor = self.store.get(params["actor"])
        if actor["kind"] != "lead":
            raise CoordinatorError("LEAD_ONLY", "A lead actor is required")
        changes = params["changes"]
        entries = changes.items() if isinstance(changes, dict) else ((c["path"], c["image"]) for c in changes)
        grouped = {}
        for path, image in entries:
            routed = routing.route(self.store, actor, path, params.get("cwd"))
            grouped.setdefault(routed["checkout"], {})[routed["path"]] = image
        expected = {key: await self.expected_images(self.store.get(key), values) for key, values in grouped.items()}
        await self.custody.acquire(actor, [routing.qualified(key, p) for key, values in expected.items() for p in values])
        kind = "restore" if restore else "lead-edit"
        if len(expected) == 1:
            checkoutid, images = next(iter(expected.items()))
            operation = await self.ledger.prepare(dict(actor, checkout=checkoutid), kind, {"expected": images}, list(images))
        else:
            from .operations import prepare
            operation = await prepare(self.ledger, actor, kind, {"expected": expected}, expected)
        try:
            await asyncio.gather(*(asyncio.to_thread(self.install_images, self.store.get(key), values) for key, values in expected.items()))
            if operation.get("parts"):
                from .operations import verify
                return await verify(self.ledger, operation, expected, "restore" if restore else "exact", {"executor": "coordinator"})
            return await self.ledger.verify(operation, images, "restore" if restore else "exact", {"executor": "coordinator"})
        except BaseException as exc:
            await self.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
            raise

    async def expected_images(self, checkout, changes):
        if not isinstance(changes, dict) or not changes:
            raise CoordinatorError("LEAD_INTENT", "Record at least one complete intended path image")
        expected = {}
        for path, change in changes.items():
            path = normalize(checkout, path)["path"]
            mode = change.get("mode")
            if mode not in (None, "100644", "100755"):
                raise CoordinatorError("FILE_MODE", "Only regular file modes are supported")
            content = base64.b64decode(change["bytes_base64"], validate=True) if mode else None
            expected[path] = {"blob": await asyncio.to_thread(self.store.blob, content) if content is not None else None, "mode": mode}
        return expected

    def install_images(self, checkout, expected):
        from .store import fsync_dir
        for name, image in expected.items():
            normalize(checkout, name)
            parts, descriptors = name.split("/"), []
            try:
                parent = os.open(checkout["root"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                descriptors.append(parent)
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o755, dir_fd=parent)
                        os.fsync(parent)
                    except FileExistsError:
                        pass
                    parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    descriptors.append(parent)
                if image["mode"] is None:
                    try:
                        os.unlink(parts[-1], dir_fd=parent)
                    except FileNotFoundError:
                        pass
                else:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
                    fd = os.open(parts[-1], flags, 0o600, dir_fd=parent)
                    with os.fdopen(fd, "wb") as output:
                        observed = os.fstat(output.fileno())
                        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                            raise CoordinatorError("UNSAFE_FILE", "Lead edit encountered a special file or hardlink before truncation")
                        output.truncate(0)
                        output.write(self.store.read_blob(image["blob"]))
                        output.flush()
                        os.fchmod(output.fileno(), 0o755 if image["mode"] == "100755" else 0o644)
                        os.fsync(output.fileno())
                os.fsync(parent)
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)

    def admit_rpc(self, method, params):
        if method == "daemon/stop" and "expect" in params and params["expect"] != {"pid": os.getpid(), "boot": self.boot}:
            raise CoordinatorError("DAEMON_REPLACED", "Stop belongs to a different daemon process",
                                   {"pid": os.getpid(), "boot": self.boot, "version": VERSION})
        completion = method in COMPLETION_RPC or (method in ("lead-hook/dispatch", "hook/dispatch")
            and params.get("payload", {}).get("hook_event_name") in ("PostToolUse", "PostToolUseFailure"))
        if method != "daemon/stop" and (self.closing or self.draining and method not in READ_RPC and not completion):
            raise CoordinatorError("DAEMON_DRAINING", "Coordinator is draining; retry on the serving daemon",
                                   {"retryable": True, "pid": os.getpid(), "boot": self.boot}, True)

    def stop_pending(self):
        from .writer import TERMINAL
        from .intents import FILE_HOOK_S
        now = time.time()
        hooks = []
        for record in self.store.all("lead_hook"):
            if record["state"] not in ("received", "prepared", "observed"):
                continue
            operation = self.store.get(record.get("operation"), required=False)
            deadline = record.get("deadline", (operation or {}).get("created", now) + FILE_HOOK_S)
            if now <= deadline:
                hooks.append(record)
        operations = [o for o in self.store.all("operation") if o["state"] in ("prepared", "allowed", "running")]
        lead_operations = [o for o in operations if o["kind"] in ("lead-edit", "restore", "lead-command")]
        methods = [method for task, method in self.inflight.items() if task is not asyncio.current_task()]
        counts = {
            "recovery": int(not self.ready),
            "workflows": sum(r["state"] not in TERMINAL | {"idle"} for r in self.store.all("run")
                             if r.get("workflow") or r["state"] == "admitting"),
            "writers": sum(bool(a.get("active_turn")) for a in self.store.all("actor")),
            "custody": sum(c["state"] != "released" for c in self.store.all("custody")),
            "deliveries": sum(m["recipient"] != "lead" and not m["recipient"].startswith("lead_mailbox_")
                and m["state"] not in ("entered_context", "acknowledged")
                and (m["delivery"] in ("submitting", "submitted", "uncertain") or
                     m["delivery"] == "pending" and m["wake"] != "none") for m in self.store.all("message")),
            "lead_hooks": len(hooks), "lead_operations": len(lead_operations),
            "other_operations": len(operations) - len(lead_operations),
            "publication_rpcs": sum(m in ("unit/publish", "unit/recover") for m in methods),
            "mutating_rpcs": sum(m not in READ_RPC and m != "daemon/stop" for m in methods),
        }
        return {key: value for key, value in counts.items() if value}

    async def drain(self, params):
        current = {"pid": os.getpid(), "boot": self.boot, "version": VERSION}
        if "expect" in params and params["expect"] != {"pid": current["pid"], "boot": self.boot}:
            raise CoordinatorError("DAEMON_REPLACED", "Stop belongs to a different daemon process", current)
        if self.draining:
            raise CoordinatorError("DAEMON_DRAINING", "Another stop is draining this daemon", {"retryable": True}, True)
        pending = self.stop_pending()
        if pending:
            raise CoordinatorError("NOT_QUIESCENT", "Settle pending work before stopping", {"pending": pending})
        self.draining = True
        try:
            deadline = time.monotonic() + STOP_DRAIN_S
            while True:
                pending = self.stop_pending()
                tasks = (self.rpc_tasks | set(self.inflight)) - {asyncio.current_task()}
                if not pending and not tasks:
                    break
                if time.monotonic() >= deadline:
                    raise CoordinatorError("NOT_QUIESCENT", "RPC drain did not settle; admission resumed",
                                           {"pending": dict(pending, inflight_rpcs=len(tasks)), "wait_seconds": STOP_DRAIN_S})
                await asyncio.sleep(0.01)
            # No pending completion remains. Fence even new observational RPCs
            # before detaching listeners or releasing leases/closing SQLite.
            self.closing = True
            for listener in list(self.store.all("listener")):
                if listener["state"] not in ("stopped", "dead"):
                    await self.stop_listener(listener)
            for checkout in list(self.store.all("checkout")):
                await self.release_lease(checkout["id"], stopping=True)
            self.stop.set()
            return {"stopping": True}
        finally:
            if not self.stop.is_set():
                self.draining = self.closing = False

    async def dispatch(self, method, params):
        self.admit_rpc(method, params)
        task = asyncio.current_task()
        owned = task not in self.inflight
        if owned:
            self.inflight[task] = method
        try:
            return await self.dispatch_admitted(method, params)
        finally:
            if owned:
                self.inflight.pop(task, None)

    async def dispatch_admitted(self, method, params):
        if method == "daemon/status":
            return self.status(params.get("checkout"))
        if method == "checkout/query":
            from .observations import query
            return query(self, params["cwd"], params.get("session_id", ""), params.get("git_common"))
        if method == "daemon/stop":
            return await self.drain(params)
        if not self.ready:
            raise CoordinatorError("RECOVERING", "Coordinator is reconciling durable state before admitting work")
        if method == "project/list":
            return await self.projects.listing(params)
        if method == "project/resolve":
            return await self.projects.resolve(params)
        if method == "project/register":
            return await self.projects.register(params)
        if method == "project/status":
            return self.projects.status(params)
        if method == "checkout/register":
            return await self.ledger.register(params["path"])
        if method == "checkout/reconcile":
            result = await self.ledger.reconcile(self.store.get(params["checkout"]), params.get("paths"))
            if params.get("resolve_quarantine") and params.get("paths") is None:
                checkout = self.store.get(params["checkout"])
                ownership = self.store.db.execute("SELECT state FROM checkout_identities WHERE identity=?", (checkout.get("identity"),)).fetchone()
                if not ownership or ownership["state"] != "ready" or checkout.get("identity") not in self.ledger.ownership:
                    raise CoordinatorError("CHECKOUT_OWNER", "Filesystem reconciliation cannot resolve an ownership collision")
                if checkout.get("ready"):
                    checkout.pop("quarantine", None)
                    self.store.save(checkout)
                    self.store.event("checkout/io_reconciled", checkout["id"], {})
            return result
        if method == "checkout/revisions":
            limit, cursor = params.get("limit", 100), params.get("cursor", "")
            if type(limit) is not int or limit < 1:
                raise CoordinatorError("PAGE_SIZE", "Page size must be positive")
            args = [params["checkout"], cursor]
            scope = " AND path=?" if "path" in params else ""
            if scope:
                args.append(params["path"])
            rows = list(self.store.db.execute("SELECT * FROM revisions WHERE checkout=? AND id>?" + scope + " ORDER BY id LIMIT ?", args + [limit + 1]))
            return {"data": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}
        if method == "run/start":
            return await self.create_run(params)
        if method.startswith("review/"):
            from . import reviews
            if method == "review/prepare":
                return await reviews.prepare(self, params)
            if method == "review/dispatch":
                return reviews.dispatched(self, params)
            if method == "review/complete":
                return reviews.complete(self, params)
            if method == "review/jobs":
                reviews.repair(self)
                return {"data": [j for j in self.store.all("review_job") if j["dispatch_cwd"] == params["cwd"] and
                                  (not params.get("run") or j["run"] == params["run"])]}
        if method == "lead/session":
            from .lead import attach
            return await attach(self, params["checkout"], params["session_id"], params.get("project"), params.get("cwd"), params.get("dispatch_cwd"), params.get("fixture"))
        if method == "lead/attach":
            unit = self.store.get(params["unit"])
            run = self.store.get(unit["run"])
            cwd = params.get("cwd") or run.get("target_cwd") or self.store.get(unit["checkout"])["root"]
            routed = routing.route(self.store, dict(unit=unit["id"], checkout=unit["checkout"]), ".", cwd, directory=True)
            ident = uid("actor")
            self.store.put("actor", ident, {"kind": "lead", "unit": unit["id"], "checkout": routed["checkout"],
                           "run": run["id"], "attempt": run["attempt"], "state": "idle", "active_turn": None,
                           "cwd": cwd}, unit["id"])
            return {"actor": ident, "unit": unit["id"]}
        if method == "run/status":
            return dict(self.store.get(params["run"]), **participants.surface(self, self.store.get(params["run"])["unit"])) if "run" in params else self.status(params.get("checkout"))
        if method == "run/list":
            return self.runs.listing(params["cwd"], params.get("limit", 50))
        if method == "run/result":
            self.runs.get(params["run"], params.get("cwd"))
            return self.writer.result(params["run"], params.get("cursor", 0), params.get("limit", 50000))
        if method == "run/events":
            return self.runs.events(self.runs.get(params["run"], params.get("cwd")), params.get("cursor", 0), params.get("limit", 100))
        if method in ("run/interrupt", "run/resume"):
            run = self.runs.get(params["run"], params.get("cwd"))
            if run.get("workflow"):
                return await (self.runs.cancel(run) if method == "run/interrupt" else self.runs.resume(run, text=params.get("text", ""), deadline=params.get("deadline"), images=params.get("images")))
            actor = self.store.get(run["root_actor"])
            connection = self.connections[actor["listener"]]
            if method == "run/interrupt":
                return await connection.call("turn/interrupt", {"threadId": actor["thread"], "turnId": actor["active_turn"]})
            return await self.runs.resume(run, text=params.get("text", ""), deadline=params.get("deadline"), images=params.get("images"))
        if method in ("run/terminals", "run/terminate-terminal"):
            actor = self.store.get(self.store.get(params["run"])["root_actor"])
            connection = self.connections[actor["listener"]]
            args = {"threadId": actor["thread"]}
            if method == "run/terminate-terminal":
                args["processId"] = params["process_id"]
                result = await connection.call("thread/backgroundTerminals/terminate", args)
                await self.settle_actor(actor)
                return result
            return await connection.call("thread/backgroundTerminals/list", args)
        if method == "hook/dispatch":
            from .hooks import deny
            try:
                return await self.hook(params["payload"], params.get("process"))
            except CoordinatorError as exc:
                self.store.event("hook/denied", None, {"code": exc.code, "details": exc.details})
                return deny(str(exc) + " " + encoded(exc.details))
        if method == "lead-hook/dispatch":
            from .intents import dispatch
            payload = params["payload"]
            async with self.actor_locks.hold("host:" + digest([payload.get("session_id"), payload.get("tool_use_id")])):
                return await dispatch(self, payload)
        if method == "lead-hook/armed":
            from .intents import arm
            return await arm(self, params)
        if method == "claim/acquire":
            self.ledger.require_owned(self.store.get(self.store.get(params["actor"])["checkout"]))
            return await self.custody.acquire(self.store.get(params["actor"]), params["paths"], params.get("cwd"))
        if method == "claim/list":
            ids = participants.checkout_ids(self.store, params["unit"]) if params.get("unit") else [params["checkout"]]
            return {"claims": [dict(c, root=self.store.get(key)["root"]) for key in ids for c in self.custody.claims(key)
                               if not params.get("unit") or c["unit"] == params["unit"]],
                    "custody": [c for key in ids for c in self.custody.active(key)
                                if not params.get("unit") or c["unit"] == params["unit"]]}
        if method == "claim/release-request":
            return self.custody.release_request(self.store.get(params["actor"]), params["paths"], params.get("cwd"))
        if method == "claim/handback":
            return await self.custody.release(self.store.get(params["unit"]), params.get("paths"), params.get("cwd"))
        if method == "command/declare":
            return self.custody.declare(self.store.get(params["actor"]), params["command"], params["reason"])
        if method == "custody/reconcile":
            reservation = self.store.get(params["custody"])
            actor = self.store.get(reservation["actor"])
            if actor["kind"] == "lead":
                from .recovery import settle_lead_command
                await settle_lead_command(self, reservation, resolve_quarantine=True)
            else:
                await self.settle_actor(actor, resolve_quarantine=True)
            return self.store.get(reservation["id"])
        if method == "lead-edit":
            return await self.lead_edit(params)
        if method == "operation/prepare":
            actor = self.store.get(params["actor"])
            if actor["kind"] != "lead" or params["kind"] != "lead-edit":
                raise CoordinatorError("NATIVE_BOUND", "Writer operations must originate in native hooks")
            groups = {}
            entries = params["changes"].items() if isinstance(params["changes"], dict) else ((c["path"], c["image"]) for c in params["changes"])
            for path, image in entries:
                routed = routing.route(self.store, actor, path, params.get("cwd"))
                groups.setdefault(routed["checkout"], {})[routed["path"]] = image
            expected = {key: await self.expected_images(self.store.get(key), values) for key, values in groups.items()}
            await self.custody.acquire(actor, [routing.qualified(key, p) for key, values in expected.items() for p in values])
            if len(expected) == 1:
                key, images = next(iter(expected.items()))
                return await self.ledger.prepare(dict(actor, checkout=key), "lead-edit", {"expected": images}, list(images))
            from .operations import prepare
            return await prepare(self.ledger, actor, "lead-edit", {"expected": expected}, expected)
        if method == "operation/complete":
            operation = self.store.get(params["operation"])
            if operation["kind"] != "lead-edit" or operation["state"] != "prepared":
                raise CoordinatorError("OPERATION_STATE", "Only a prepared lead edit can complete through RPC")
            try:
                if operation.get("parts"):
                    from .operations import verify
                    return await verify(self.ledger, operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
                return await self.ledger.verify(operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
            except BaseException as exc:
                await self.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
                raise
        if method == "lead-command":
            from .recovery import lead_command
            return await lead_command(self, params)
        if method == "unknown/list":
            return {"data": self.store.unresolved(params["checkout"], params.get("paths"))}
        if method == "unknown/restore":
            from .recovery import restore_unknown
            return await restore_unknown(self, params)
        if method == "lead/mailbox":
            from .lead import mailbox
            return {"mailbox": mailbox(self.store, params["session_id"], params["unit"])}
        if method == "message/send":
            return await self.messages.send(params.get("sender", "lead"), params["recipient"], params["text"], params["wake"])
        if method == "message/inbox":
            return self.messages.inbox(params.get("recipient", "lead"), params.get("cursor", ""), params.get("limit", 100), params.get("session_id", ""))
        if method == "message/wake":
            message = self.store.get(params["message"])
            if message["delivery"] != "pending" or params["wake"] not in ("queue", "start"):
                raise CoordinatorError("MESSAGE_WAKE", "Only a pending message may be explicitly woken")
            message["wake"] = params["wake"]
            self.store.save(message)
            self.background(self.messages.deliver(message["id"]))
            return {"message": message["id"], "wake": message["wake"]}
        if method == "message/retry":
            return await self.messages.retry_requested(params["message"])
        if method == "message/ack":
            if params.get("actor", "lead") != "lead" and not params.get("actor", "").startswith("lead_mailbox_"):
                raise CoordinatorError("NATIVE_BOUND", "Writer acknowledgements must originate from native actor metadata")
            return self.messages.ack(params.get("actor", "lead"), params["message"], params.get("session_id", ""))
        if method.startswith("unit/"):
            unit = self.store.get(params["unit"])
            async with self.ledger.lifecycle.hold_many(participants.checkout_ids(self.store, unit)):
                async with self.unit_locks.hold(unit["id"]):
                    for checkoutid in participants.checkout_ids(self.store, unit):
                        self.ledger.require_owned(self.store.get(checkoutid))
                    return await self.unit_dispatch(method, params)
        if method == "admission/check":
            from .admission import Admission
            return await Admission(self.store).check(params)
        if method == "records/list":
            # Paginated diagnostic surface, not an attribution or adoption API.
            records = list(self.store.all(params["kind"]))
            cursor, limit = params.get("cursor", ""), params.get("limit", 100)
            records = [r for r in records if r["id"] > cursor]
            return {"data": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None}
        raise CoordinatorError("METHOD", "Unknown coordinator method", {"method": method})

    async def unit_dispatch(self, method, params):
        from .publish import Publisher
        publisher = Publisher(self)
        if method == "unit/status":
            unit = self.store.get(params["unit"])
            return {"unit": unit, "run": self.runs.summary(self.store.get(unit["run"])),
                    "unknown": self.store.unresolved(unit["checkout"]),
                    "publications": list(self.store.all("publication", unit["id"])), **participants.surface(self, unit)}
        if method == "unit/recover":
            if self.store.get(params["unit"]).get("project_revision"):
                from .group_publish import GroupPublisher
                group = GroupPublisher(self)
                if params.get("action") == "abandon":
                    return await group.reopen(params["unit"], abandon=True)
                current = group.current(params["unit"])
                return await group.reconcile(current) if current else []
            if params.get("action") == "abandon":
                return await publisher.abandon(params["unit"])
            publications = [p for p in self.store.all("publication", params["unit"]) if p["phase"] not in ("abandoned", "complete")]
            return [await publisher.reconcile(p) for p in publications]
        if method == "unit/reopen":
            unit = self.store.get(params["unit"])
            if unit.get("project_revision"):
                from .group_publish import GroupPublisher
                return await GroupPublisher(self).reopen(unit["id"])
            if unit["state"] != "sealed" or any(p["phase"] != "abandoned" for p in self.store.all("publication", unit["id"])):
                raise CoordinatorError("UNIT_REOPEN", "Only a sealed unit without a publication journal can reopen")
            await publisher.quiescent(unit, allow_quarantined=True)
            unit.update(state="open", previous_seal=unit.pop("seal"))
            self.store.save(unit)
            self.store.event("unit/reopened", unit["id"], {"invalidated_seal": unit["previous_seal"]})
            return {"unit": unit["id"], "state": "open", "review_required": True}
        if method == "unit/seal":
            return await publisher.seal(params["unit"], params.get("validation", []), params.get("publication_order"))
        if method == "unit/review-record":
            return publisher.review(params["unit"], params["evidence"])
        if method == "unit/publish":
            return await publisher.publish(params["unit"], params["review"], params["metadata"])
        if method == "unit/diff":
            return await publisher.diff(params["unit"])
        raise CoordinatorError("METHOD", "Unknown unit method")

    async def rpc(self, reader, writer):
        task = asyncio.current_task()
        self.rpc_tasks.add(task)
        try:
            await self.rpc_request(reader, writer)
        finally:
            self.inflight.pop(task, None)
            self.rpc_tasks.discard(task)

    async def rpc_request(self, reader, writer):
        request = {}
        try:
            # Stream the line without StreamReader's implicit 64 KiB line cap.
            parts = []
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    raise CoordinatorError("RPC_TRUNCATED", "Incomplete request")
                parts.append(chunk)
                if b"\n" in chunk:
                    break
            raw = b"".join(parts)
            if raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
                raise CoordinatorError("RPC_FRAMING", "One JSONL request per connection is required")
            request = json.loads(raw)
            if set(request) - {"id", "method", "params", "key"}:
                raise CoordinatorError("RPC_FIELDS", "Unknown RPC envelope field")
            method, params, key = request["method"], request.get("params", {}), request.get("key")
            self.admit_rpc(method, params)
            # Includes requests queued on an idempotency lock, before dispatch.
            self.inflight[asyncio.current_task()] = method
            fingerprint = digest([method, params])
            if key is not None and (not isinstance(key, str) or not key):
                raise CoordinatorError("RPC_KEY", "An idempotency key must be a nonempty string")
            async with self.request_locks.hold(key):
                cached = self.store.db.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone() if key else None
                if cached:
                    if cached["fingerprint"] != fingerprint:
                        raise CoordinatorError("IDEMPOTENCY_CONFLICT", "Key was already used for different input")
                    if cached["state"] != "completed":
                        raise CoordinatorError("RPC_UNCERTAIN", "Prior request may have effects; reconcile its durable records")
                    answer = json.loads(cached["response"])
                else:
                    self.admit_rpc(method, params)
                    if key:
                        self.store.db.execute("INSERT INTO idempotency VALUES(?,?,?,NULL)", (key, fingerprint, "pending"))
                    try:
                        answer = {"result": await self.dispatch(method, params)}
                    except CoordinatorError as exc:
                        answer = {"error": exc.envelope()}
                    if key:
                        self.store.db.execute("UPDATE idempotency SET state='completed',response=? WHERE key=?", (encoded(answer), key))
            response = dict(answer, id=request.get("id"))
        except Exception as exc:
            error = exc if isinstance(exc, CoordinatorError) else CoordinatorError("OPERATIONAL_FAILURE", type(exc).__name__, operational=True)
            response = {"id": request.get("id"), "error": error.envelope()}
        try:
            writer.write((encoded(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def maintenance(self):
        while not self.stop.is_set():
            await asyncio.sleep(1)
            if not self.ready or self.draining:
                continue
            try:
                await self.leases.call("ping")
            except CoordinatorError as exc:
                self.store.event("lease/redundancy_unavailable", None, exc.envelope())
                continue
            for actor in list(self.store.all("actor")):
                if actor.get("listener") and any(c["actor"] == actor["id"] for c in self.custody.unit_active(actor["unit"])):
                    try:
                        await self.settle_actor(actor)
                    except (CoordinatorError, OSError):
                        pass  # custody remains held; no optimistic release
            from .recovery import settle_lead_command
            for reservation in list(self.store.all("custody")):
                if reservation.get("lead_root") and reservation["state"] != "released":
                    await settle_lead_command(self, reservation)
            await self.messages.retry()
            for run in list(self.store.all("run")):
                if run.get("cancel_requested") and run["state"] != "cancelled":
                    await self.runs.settle_cancel(run["id"])
                    continue
                if run.get("workflow") and run["state"] not in ("completed", "failed", "interrupted", "blocked", "refused", "cancelled") and run["workflow"].get("deadline", 0) > 0 and time.time() >= run["workflow"]["deadline"]:
                    self.background(self.runs.cancel(run))
                    continue
                if run.get("workflow") and run["state"] in ("settling", "transition_pending"):
                    try:
                        await self.writer.advance(run["id"])
                    except CoordinatorError as exc:
                        self.store.event("workflow/advance_refused", run["id"], exc.envelope())
            for checkout in list(self.store.all("checkout")):
                await self.release_lease(checkout["id"])
            for record in list(self.store.all("lead_hook")):
                if record["state"] == "prepared":
                    operation = self.store.get(record["operation"])
                    if time.time() - operation["created"] > 120:
                        record["state"] = "uncertain"
                        self.store.save(record)
                        await self.ledger.quarantine(operation, "host pre/post completion gap; late matching bytes are not proof")

    async def serve(self):
        from .recovery import recover
        allocation = runtime.allocate("rpc")
        socket = Path(allocation["path"])
        try:
            server = await asyncio.start_unix_server(self.rpc, path=socket)
        except OSError as exc:
            raise CoordinatorError("SOCKET_BIND", "Coordinator RPC socket failed to bind",
                                   {"path": str(socket), "bytes": len(os.fsencode(socket)),
                                    "measured_limit": runtime.capacity(), "errno": exc.errno}, True) from exc
        os.chmod(socket, 0o600)
        self.endpoint = runtime.endpoint_info(socket)
        runtime.atomic_json(self.store.root / "endpoint.json", {"state": str(self.store.root),
                            "generation": self.store.generation, "allocation": allocation})
        async with server:
            await recover(self)
            from .reviews import repair
            repair(self)
            from .registry import publish
            publish(self.store)
            self.ready = True
            self.background(self.maintenance())
            await self.stop.wait()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        runtime.cleanup(allocation)
        for descriptor in self.ledger.ownership.values():
            os.close(descriptor)
        self.store.db.close()


def worker(root):
    if sys.version_info < (3, 11):
        raise CoordinatorError("PYTHON_VERSION", "Daemon requires Python 3.11+")
    root = validate_state(root, create=True)
    os.umask(0o077)
    with open(root / "worker.lock", "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            asyncio.run(Daemon(root).serve())
        except Exception as exc:
            error = exc if isinstance(exc, CoordinatorError) else CoordinatorError("DAEMON_STARTUP", type(exc).__name__, operational=True)
            runtime.atomic_json(root / "startup-error.json", {"time": time.time(), "error": error.envelope()})
            return 3
    return 0


def supervise(root):
    root = validate_state(root, create=True)
    os.umask(0o077)
    inherited = venv_fds()
    with open(root / "supervisor.lock", "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        from .leases import Custodian, RecoveryEndpoint, receive, send
        records = json.loads(os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_LEASES", "[]"))
        recovery_fd = os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_FD", "")
        recovered = json.loads(os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_WORKER", "null"))
        custodian = Custodian(records)
        rescue = RecoveryEndpoint(root)
        while True:
            child = None
            if recovery_fd:
                parent_socket = socket.socket(fileno=int(recovery_fd))
                recovery_fd = ""
                for record in custodian.held.values():
                    custodian.compat.share_lease(record, recovered["pid"], os.getpid())
            else:
                parent_socket, child_socket = socket.socketpair()
                environment = launch_environment()
                environment["SOFTWARE_WORKFLOWS_CUSTODIAN_FD"] = str(child_socket.fileno())
                try:
                    child = subprocess.Popen([sys.executable, "-B", "-m", "coordinator", "daemon", "worker", "--state", str(root)],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             close_fds=True, pass_fds=inherited + (child_socket.fileno(),), env=environment,
                                             cwd=str(Path(__file__).resolve().parents[1]))
                except OSError as exc:
                    parent_socket.close()
                    child_socket.close()
                    runtime.atomic_json(root / "startup-error.json", {"time": time.time(), "error": {
                        "code": "WORKER_SPAWN", "message": type(exc).__name__, "leases_retained": bool(custodian.held)}})
                    if not custodian.held:
                        rescue.close()
                        return 3
                    time.sleep(1)
                    continue
                child_socket.close()
            parent_socket.settimeout(15)
            def alive():
                if child is not None:
                    return child.poll() is None
                current = processes.identity(recovered["pid"])
                return bool(current and current["birth"] == recovered["birth"] and not current["zombie"])
            with selectors.DefaultSelector() as selector:
                selector.register(parent_socket, selectors.EVENT_READ)
                selector.register(rescue.socket, selectors.EVENT_READ)
                while alive():
                    for key, _ in selector.select(0.25):
                        if key.fileobj is rescue.socket:
                            candidate, _ = rescue.socket.accept()
                            candidate.settimeout(20)
                            try:
                                expected = processes.identity(child.pid) if child is not None else recovered
                                custodian.reconnect(candidate, expected)
                            except Exception:
                                candidate.close()  # Every acquired reference remains held.
                                continue
                            try:
                                selector.unregister(parent_socket)
                            except KeyError:
                                pass
                            parent_socket.close()
                            parent_socket = candidate
                            selector.register(parent_socket, selectors.EVENT_READ)
                            continue
                        try:
                            request, _ = receive(parent_socket)
                            custodian.request(parent_socket, request)
                        except (OSError, EOFError, ValueError):
                            # A lost acknowledgment never releases an acquisition.
                            selector.unregister(key.fileobj)
                            break
                        except Exception as exc:
                            try:
                                send(parent_socket, {"ok": False, "holder": "Custodian retained exclusion: " + type(exc).__name__})
                            except OSError:
                                selector.unregister(key.fileobj)
            parent_socket.close()
            result = child.wait() if child is not None else 0
            recovered = None
            if result in (0, 3) and not custodian.held:
                rescue.close()
                return result
            # A replacement worker receives existing descriptors while the
            # custodian keeps its references throughout ledger reconciliation.
            time.sleep(1 if result == 3 else 0.25)
