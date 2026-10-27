"""Detached singleton supervisor, async JSONL RPC, and private native listeners."""
import asyncio
import base64
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import shlex
import sqlite3
import stat
import sys
import time

from .locks import KeyedLocks, finish_all
from .client import CoordinatorError, validate_state
from . import telemetry
from .store import Store, uid, digest, encoded, WINDOW_ACTIVE, BLOCKERS
from .ledger import Ledger, normalize, patch_scope, read_path
from .custody import Custody
from . import native, tools, processes, runtime, participants, routing

VERSION = runtime.plugin_version()  # in daemon/status, so that a newer client can retire this release

# A hook has 15 s: three 3 s calls (dispatch, retry, Bash arm), the measured
# 3.5 s replacement start, and 1 s draining leave 1.5 s for polling/transport.
# This bounds waiting for RPC settlement; occupied mutations refuse immediately.
STOP_DRAIN_S = 1.0
READ_RPC = {"daemon/status", "checkout/query", "checkout/revisions", "project/status", "services/status",
            "run/status", "run/list", "run/result", "run/events", "run/terminals",
            "claim/list", "unknown/list", "message/inbox", "unit/status", "unit/diff", "records/list"}
COMPLETION_RPC = {"operation/complete", "custody/reconcile", "run/interrupt", "run/terminate-terminal", "review/complete", "lead-hook/batch"}


NATIVE_LOG_POLL_S = 0.05  # a refused call's custody, waited on by the writer's next call, is released this much later


def returned_failure(operation):
    """Native wrote a failed result for the call and nothing it sent contradicts that: no Post hook (0.163 runs
    none after a failed handler) and no successful item."""
    return ((operation.get("returned") or {}).get("success") is False and not operation.get("post_received")
            and (operation.get("completed_item") or {}).get("status") != "completed")


def handler_ended(operation):
    """Native has shown the call's handler finished: its result, its completed item, or its Post hook. Each comes
    after the handler's last write. A dispatch that ended without a result (0.163 aborts it on cancellation) is not
    this: one write it began can still finish."""
    return bool(operation.get("returned") or operation.get("completed_item") or operation.get("post_received"))


def call_source(operation):
    """"direct" when native dispatched the call from the model's response, "code_mode" from a cell: from the end
    of its dispatch, else from the line native wrote on receiving it. None when neither was read."""
    return (operation.get("result_ready") or {}).get("source") or operation.get("native", {}).get("source")


# A 27-writer, 8,998-events/min fixture made 9,181 separate prefix commits.
# Batching reduced those writes to 2,134. Prefix replay is idempotent: schedule
# a flush after 100 ms, or flush immediately at 256 applied notifications, and
# commit all affected listeners together. Receipts and request
# obligations remain synchronous and durable before queueing.
NATIVE_APPLIED_FLUSH_S = 0.1
NATIVE_APPLIED_BATCH = 256


class NativeBacklog:
    """Coalesced wakeup over committed rows, with one live dispatch cursor.

    This cursor is not native_applied: dispatch, application and request-handler
    completion have different obligations. Only the current row is decoded in
    memory; the (subject, kind, seq) index holds any committed backlog.
    """
    def __init__(self, store, listener, cursor):
        self.store, self.listener = store, listener
        self.cursor = self.highwater = cursor
        self.ready = asyncio.Event()
        self._unfinished_tasks = 0
        self.received = self.dispatched = self.applied = self.applied_bytes = self.max_pending = 0
        self.active_bytes = 0

    def publish(self, sequence, count):
        self.highwater = sequence
        self._unfinished_tasks += count
        self.received += count
        self.max_pending = max(self.max_pending, self._unfinished_tasks)
        self.ready.set()

    async def get(self):
        while not self._unfinished_tasks:
            self.ready.clear()
            await self.ready.wait()
        row = self.store.db.execute("SELECT seq,data FROM events WHERE subject=? AND kind='native' "
                                    "AND seq>? AND seq<=? ORDER BY seq LIMIT 1",
                                    (self.listener, self.cursor, self.highwater)).fetchone()
        if row is None:
            raise CoordinatorError("NATIVE_BACKLOG", "Committed native backlog lost its row")
        self.cursor = row[0]
        self.dispatched += 1
        self.active_bytes = len(row[1].encode())
        return row[0], json.loads(row[1])

    def task_done(self):
        self._unfinished_tasks -= 1
        self.active_bytes = 0

    def status(self):
        return {"received": self.received, "dispatched": self.dispatched, "applied": self.applied,
                "applied_bytes": self.applied_bytes, "pending": self._unfinished_tasks,
                "max_pending": self.max_pending, "cursor": self.cursor, "highwater": self.highwater}


class Daemon:
    def __init__(self, root):
        self.store = Store(root)
        self.ledger = Ledger(self.store)
        self.custody = Custody(self.store, self.ledger)
        self.connections, self.queues, self.workers, self.tasks = {}, {}, {}, set()
        self.native_applying = {}
        self.native_stop_error = None
        self.retiring_listeners = set()
        self.lead_commands = set()  # live calls that still own their subprocess exit status
        self.custody_settlements = KeyedLocks()  # group -> one inventory/observation/release owner
        self.services = {}
        self.listener_requests = {}
        self.native_progress, self.native_progress_count = {}, 0
        self.native_progress_flush = None
        self.settlements = {}
        self.loop_lag = {"last_seconds": 0.0, "max_seconds": 0.0, "samples": 0}
        self.birth = processes.identity(os.getpid())["birth"]
        self.writer_status = {}
        from .dormancy import Dormancy
        self.dormancy = Dormancy(self)
        from .parking import Parking
        from .retention import Retention
        self.parking = Parking(self)
        from . import memory
        self.memory = memory.Memory(self)
        memory.CURRENT = self.memory
        self.retention = Retention(self)
        from .services import Services
        self.broker = Services(self)
        from .writer_profile import setting, suggestion
        self.writer_profile = setting()
        self.writer_profile["suggestion"] = suggestion(self.store)
        # Listener id -> the task following that listener's own log, and how far it was read.
        self.log_tails, self.log_offsets = {}, {}
        self.request_locks, self.actor_locks, self.unit_locks = KeyedLocks(), KeyedLocks(), KeyedLocks()
        self.lease_locks = KeyedLocks()
        self.reconcile_locks = KeyedLocks()  # run -> one reconcile of its writer app-server at a time
        self.admitting = {}  # checkout -> effect-capable Pre hooks admitting, before their operation is recorded
        self.admitting_units = {}  # unit -> its writers' hooks being decided (hook, recovery.writer_ready)
        self.readmitting = set()  # runs whose writer app-server is being re-admitted or reconciled again
        self.gap_reconciles = set()  # transport-gap reconciles under way: a drain waits for them, as for readmitting
        self.listener_reconciles = {}  # invocation -> run/listener/start; includes lock waiters and nested calls
        from .recovery import RECOVERY_CONCURRENCY
        self.recovery_capacity = asyncio.Semaphore(RECOVERY_CONCURRENCY)
        self.recovery_holders = set()
        self.recovery_limit = RECOVERY_CONCURRENCY
        self.recovery_active = self.recovery_queued = 0
        self.ready = False
        self.recovery_barrier = True
        # (kind, id) -> asyncio.Event: a run, unit or checkout startup recovery is still reconciling while calls are
        # admitted (recovery.recover_writers); the event is set, and the entry removed, once it is done.
        self.recovering = {}
        self.answer_probes = {}  # listener -> (when, task) of maintenance's last probe (recovery.answer_watch)
        self.silent = {}  # listener -> since when, and why, its app-server has not answered (reported, never ended)
        self.ended = {}  # reason -> how many writer app-servers this daemon ended (recovery.end_wedged)
        # Startup recovery's writer ends, and the last recovery's measured length, for retry_after
        self.recovery_began, self.recovery_ends, self.recovery_measured = None, [], None
        self.child_watcher = None  # what children.start measured as this daemon started
        self.upgrade_fd = None  # upgrade.lock, passed by a supervisor restarting this worker, held until it serves
        self.boot = uid("boot")
        self.draining = self.closing = False
        self.inflight, self.rpc_tasks = {}, set()
        self.rpc_writers = {}
        self.stop = asyncio.Event()
        self.ledger.terminating = self.stop
        self.ledger.retiring = self.listener_retiring
        self.native_intake_closed = False
        self.native_slice_until = 0.0
        self.native_tickets = deque()
        self.native_waiters = {}
        self.native_granted = set()
        self.native_service = None
        from .messages import Messages
        self.messages = Messages(self)
        self.ledger.messages = self.messages  # a release outside the ledger tells the leads (Ledger.notify_released)
        from .writer import Writer
        self.writer = Writer(self)
        from .history import History
        self.history = History(self)
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
            from .admissions import check
            check("lease acquired")
            checkout["compatibility_lease"] = evidence
            self.store.save(checkout)
            return evidence

    async def release_lease(self, checkoutid, stopping=False):
        async with self.lease_locks.hold(checkoutid):
            await self._release_lease(checkoutid, stopping)

    async def _release_lease(self, checkoutid, stopping):
        checkout = self.store.get(checkoutid)
        from .lead_calls import active
        if next(active(self.store, checkoutid), None) is not None:
            return  # A stop cannot end the user's Claude host or waive its file-call exclusion.
        if not checkout.get("compatibility_lease"):
            return
        runs = [r for r in self.store.all("run") if participants.contains(self.store, r["unit"], checkoutid) and r["mode"] == "native"]
        from .writer import TERMINAL
        if not stopping and any(r["state"] not in TERMINAL | {"idle"} and not self.runs.awaiting_lead(r) for r in runs):
            return
        if any(a.get("active_turn") for a in self.store.all("actor") if participants.contains(self.store, a["unit"], checkoutid)) or self.custody.active(checkoutid):
            return
        if self.admitting.get(checkoutid) or not stopping and any(o["state"] in ("prepared", "allowed", "running") or o.get("executing")
                                                                  for o in self.store.live_operations(checkoutid)):
            # Native may still write for it (a project's parts included): legacy writers stay excluded until it
            # ends. A stop releases after every app-server has exited.
            return
        for message in self.store.all("message"):
            if self.delivery_owed(message) and participants.contains(self.store, self.store.get(message["recipient"])["unit"], checkoutid):
                return  # A queued or uncertain native turn can start without another RPC.
        await self.leases.call("release", checkout["root"])
        checkout.pop("compatibility_lease", None)
        self.store.save(checkout)

    async def admission_lease(self, actor):
        """Hold every checkout of the call's unit against lease release (under the lock release takes), and find
        its compatibility lease held: refused (EXCLUSIVE_WRITER) while a writer outside the coordinator holds it."""
        keys = []
        try:
            for key in participants.checkout_ids(self.store, actor["unit"]):
                async with self.lease_locks.hold(key):
                    self.admitting[key] = self.admitting.get(key, 0) + 1
                    keys.append(key)
                    checkout = self.store.get(key)
                    if not checkout.get("compatibility_lease"):
                        checkout["compatibility_lease"] = await self.leases.call("acquire", checkout["root"])
                        self.store.save(checkout)
        except BaseException:
            for key in keys:
                self.admitting[key] -= 1
            raise
        return keys

    def background(self, awaitable):
        # A long-lived observer is not part of the admission that started it.
        # Owned preparation/finish tasks retain their context explicitly.
        context = telemetry.detached_context()
        task = asyncio.create_task(awaitable, context=context)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self.task_done)
        return task

    def start_service(self, name, factory):
        """Supervise process-lifetime duties, including unexpected cancellation."""
        if self.stop.is_set():
            return
        state = self.services.setdefault(name, {"restarts": 0})
        state.update(state="running", started=time.monotonic())
        task = self.background(factory())
        def ended(completed):
            if self.stop.is_set():
                state.update(state="stopped", reason="daemon stopping")
                return
            error = None if completed.cancelled() else completed.exception()
            reason = "cancelled" if completed.cancelled() else type(error).__name__ if error else "returned"
            state["restarts"] += 1
            # A broken service must not form a restart/commit busy loop. Retry
            # indefinitely, 1..30 s, resetting after 30 s of healthy service.
            failures = 0 if time.monotonic() - state["started"] >= 30 else state.get("failures", 0)
            delay = min(30, 2 ** min(failures, 5))
            state.update(state="restarting", reason=reason, failures=failures + 1, retry_after=delay)
            self.store.event("service/restarting", name, dict(state))
            asyncio.get_running_loop().call_later(delay, self.start_service, name, factory)
        task.add_done_callback(ended)
        return task

    def task_done(self, task):
        if not task.cancelled() and task.exception():
            exception = task.exception()
            self.store.event("background/failure", None, {"code": getattr(exception, "code", type(exception).__name__)})

    def watch_listener_task(self, task, listener, role):
        def ended(completed):
            if self.stop.is_set() or self.listener_retiring(listener):
                return
            error = None if completed.cancelled() else completed.exception()
            if error is not None and native.local_resource_failure(error):
                connection = self.connections.get(listener)
                if connection is not None:
                    connection.intake_failed(error, role)
                    return
            current = self.store.get(listener, required=False)
            if not current or current["state"] in ("stopped", "dead") or role == "log" and not current.get("log"):
                return
            # Replaying a lost queue item must precede later applications. Stop
            # visibly and let startup replay the durable suffix, rather than
            # restarting a consumer past an interrupted journal application.
            self.store.event("service/failed", listener, {"service": role,
                "reason": "cancelled" if completed.cancelled() else type(error).__name__ if error else "returned",
                "action": "daemon stopping; journal replay required"})
            self.closing, self.ready = True, False
            self.stop.set()
        task.add_done_callback(ended)
        return task

    def status(self, checkout=None, brief=False):
        """The daemon's identity and readiness; unless brief, every run's writers too. A client checking
        readiness asks for brief: the full answer decodes each run's claims and custody."""
        from .recovery import recovering_counts, startup_counts
        from .lead_calls import legacy_status
        cached = self.writer_status.get(checkout)
        epoch = self.store.status_epoch
        writers = cached[1] if not brief and cached and cached[0] == epoch else []
        rebuild = not brief and not (cached and cached[0] == epoch)
        messages = list(self.store.all("message")) if rebuild else ()
        for run in self.store.all("run") if rebuild else ():
            if checkout and not participants.contains(self.store, run["unit"], checkout):
                continue
            unit = self.store.get(run["unit"])
            # What its app-server's admission proved (admission.CAPABILITIES): how a notice reaches the writer.
            listener = self.store.get((self.store.get(run.get("root_actor") or "", required=False) or {}).get("listener") or "",
                                      required=False) or {}
            writers.append({"run": run["id"], "unit": unit["id"], "task": run["task"], "state": self.runs.reported(run)[0],
                            "capabilities": listener.get("capabilities"),
                            "listener": {k: listener.get(k) for k in ("state", "dormant_at", "dormant_reason", "history_gaps", "writer_profile")},
                            **({"capability_fallback": listener["capability_fallback"]} if listener.get("capability_fallback") else {}),
                            "actors": [a for a in self.store.all("actor", unit["id"])],
                            "claims": self.custody.claims(run["checkout"], unit["id"]),
                            "custody": self.custody.unit_active(unit["id"]),
                            **participants.surface(self, unit),
                            "pending_requests": [r for kind in ("claim_request", "release_request", "custody_request")
                                                 for r in self.store.all(kind, unit["id"]) if r["state"] in ("pending", "pending_lead", "waiting")],
                            "messages": [m for m in messages if m.get("unit") == unit["id"] and m["state"] != "acknowledged"]})
        if rebuild:
            self.writer_status[checkout] = (epoch, writers)
        return {"ready": self.ready, "generation": self.store.generation, "pid": os.getpid(),
                "boot": self.boot, "capabilities": ["draining-stop"], "draining": self.draining,
                "birth": self.birth, "state_root": str(self.store.root),
                "endpoint": getattr(self, "endpoint", None),
                "event_loop_lag": dict(self.loop_lag),
                "services": {name: dict(state) for name, state in self.services.items()},
                "project_services": self.broker.status(),
                "checkout_scans": self.ledger.scans.status(),
                "checkout_locks": [lock.status() for lock in self.ledger.locks.values() if lock.locked() or lock.waiting],
                "locks": {"rpc": len(self.request_locks), "actor": len(self.actor_locks),
                          "message": len(self.messages.locks), "unit": len(self.unit_locks),
                          "checkout": len(self.ledger.lifecycle)},
                # What children.start measured as this daemon started, and the runs, units and checkouts startup
                # recovery is still reconciling. The writer app-servers it ended (recovery.end_wedged), by reason, and
                # those now silent (reported, never ended): the first sign of either is here, not a blocked lead.
                "memory": self.memory.status(), "child_watcher": self.child_watcher, "recovering": recovering_counts(self),
                "listener_reconciles": list(self.listener_reconciles.values()), "startup_recovery": startup_counts(self.store),
                "recovery_capacity": {"limit": self.recovery_limit, "active": self.recovery_active, "queued": self.recovery_queued},
                "history_capacity": {"limit": self.history.incremental_limit, "active": self.history.incremental_active,
                                     "queued": self.history.incremental_queued},
                "native_decode": dict(native.DECODE_METRICS), "history_observers": self.history.active,
                "native_intake": self.intake_status(),
                "lead_edits": self.pending_lead_edits(),
                "lead_edit_legacy_limit": legacy_status(self.store),
                "ended": dict(self.ended), "silent": len(self.silent), "dormancy": self.dormancy.status(), "writer_mcp_profile": self.writer_profile,
                "retention": self.retention.status(),
                "sqlite": sqlite3.sqlite_version, "version": VERSION, **({} if brief else {"writers": writers})}

    def intake_status(self):
        # Counters only: no SQL, payloads, or per-request profile-file writes.
        # Summarize before JSON encoding so frequent status polls do not export
        # one large metrics object per listener on the measured service path.
        result = {"journal": {}, "application": {}, "rpc": {}}
        for ident, connection in self.connections.items():
            queue = self.queues.get(ident)
            sources = {"journal": connection.intake.status() if connection.intake else {},
                       "rpc": connection.rpc_metrics,
                       "application": queue.status() if isinstance(queue, NativeBacklog) else {}}
            for section, values in sources.items():
                for key, value in values.items():
                    if key in ("cursor", "highwater"):
                        continue
                    current = result[section].get(key, 0)
                    result[section][key] = (max(current, value) if key.startswith("max_") or key == "oldest_seconds"
                                            else current + value)
        return result

    async def create_run(self, params, revision=None):
        if set(params) - {"checkout", "project", "cwd", "dispatch_cwd", "mode", "task", "paths", "model", "fixture", "workflow"} or params.get("mode", "native") not in ("lead", "native"):
            raise CoordinatorError("RUN_FIELDS", "run/start accepts checkout or project, cwd, dispatch_cwd, mode (lead or native), task, paths, model, fixture and workflow")
        if params.get("mode", "native") == "native":
            # Refuse before allocating a run or its claims. The launch boundary
            # checks again in case pressure changes during preparation.
            self.memory.check()
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
        from . import admissions
        file_admission = admissions.CURRENT.get() is not None and params.get("mode") == "lead"
        if file_admission:
            # Creating the session mapping for its first file call is metadata
            # work. Its path reservation and pre-image verification provide the
            # admission fence; it must not join whole-checkout observation or
            # publication recovery queues. Those retain their own obligations.
            admissions.check("lead session metadata")
            snapshot = await telemetry.to_thread(self.ledger._metadata, checkout["root"])
            admissions.check()
            if snapshot["identity"] != checkout["identity"] or snapshot["repository"] != checkout["repository"]:
                raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Checkout identity changed before lead attachment")
            return snapshot
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
        snapshot = await telemetry.to_thread(self.ledger._metadata, checkout["root"])
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
            index = await telemetry.to_thread(gitio.entries, checkout["root"])
            base_index = await telemetry.to_thread(gitio.entries, checkout["root"], snapshot["base"])
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
        from .admissions import check
        check("unit metadata commit")
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
            self.check_native_stop()
            run = self.store.get(ident)
            if run.get("production_started"):
                self.store.event("workflow/dispatch_detached", ident, {"code": getattr(exc, "code", type(exc).__name__)})
                return {"run": ident, "unit": unitid, "actor": actorid, "state": run["state"], "reconcile_required": True}
            async def rollback(reason):
                run.update(state="refused", refusal=reason)
                self.store.save(run)
                for listener in list(self.store.all("listener", ident)):
                    await self.stop_listener(listener)
                for checkoutid in snapshots:
                    for claim in self.custody.claims(checkoutid):
                        if claim["unit"] == unitid:
                            claim["state"] = "released"
                            self.store.save(claim)
                    await self.release_lease(checkoutid)
            from .locks import finish
            await finish(rollback(getattr(exc, "code", type(exc).__name__)))
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, CoordinatorError):
                raise CoordinatorError(exc.code, str(exc), dict(exc.details, safe_to_fallback=not bool(revision), run=ident), exc.operational) from exc
            raise CoordinatorError("NATIVE_START_" + type(exc).__name__, "Native startup failed before production execution; resources were released",
                                   {"safe_to_fallback": not bool(revision), "run": ident}, True) from exc

    def hook_config(self):
        command = shlex.join([sys.executable, "-B", str(Path(__file__).with_name("hooks.py")), "--state", str(self.store.root)])
        from .hooks import NATIVE_HOOK_SECONDS
        hook = {"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": NATIVE_HOOK_SECONDS, "async": False}]}
        return command, {"features.hooks": True, "hooks.PreToolUse": [hook], "hooks.PostToolUse": [hook]}

    def hook_stale(self, listener):
        """Whether a listener runs another release's hook program: admitted by an older daemon, its config names
        that release's hooks.py, and hook() refuses every call it makes (HOOK_PROGRAM_CHANGED)."""
        command, _ = self.hook_config()
        table = (listener.get("config") or {}).get("hooks") or {}  # admission stores the config as TOML tables
        configured = {h.get("command") for event in ("PreToolUse", "PostToolUse") for entry in table.get(event) or []
                      for h in entry.get("hooks", [])}
        return configured != {command} or listener.get("hook_digest") != native.file_digest(Path(__file__).with_name("hooks.py"))

    def unit_quiet(self, unitid):
        """No turn, custody, admission, live operation, workflow input or native delivery of the unit in flight: its
        app-server can be replaced without cutting work."""
        run = self.store.get(self.store.get(unitid)["run"])
        from .lead_calls import active
        # Another unit's call being admitted in the same checkout runs on its own app-server: it is no reason to keep
        # this one (counting it left a resume waiting on an unrelated admission).
        return not (any(a.get("active_turn") for a in self.store.all("actor", unitid)) or self.custody.unit_active(unitid)
                    or self.admitting_units.get(unitid)
                    or any(r.get("unit") == unitid for r in active(self.store))
                    or any(self.store.db.execute("SELECT 1 FROM records INDEXED BY quiet_" + kind + " WHERE " + WINDOW_ACTIVE[kind] +
                                                 " AND parent=? LIMIT 1", (unitid,)).fetchone() for kind in ("operation", "executing"))
                    or any(p.get("state") in ("submitting", "uncertain") for p in (run.get("phases") or {}).values())
                    or self.store.db.execute("SELECT 1 FROM records INDEXED BY blocker_delivery WHERE " + BLOCKERS["delivery"] +
                                             " AND json_extract(data,'$.unit')=? LIMIT 1", (unitid,)).fetchone())

    def lead_mail(self, message):
        """Mail a lead reads through its inbox (`lead`, a session mailbox, or an actor of kind lead): no native turn
        starts for it."""
        return (message["recipient"] == "lead" or message["recipient"].startswith("lead_mailbox_")
                or (self.store.get(message["recipient"], required=False) or {}).get("kind") == "lead")

    def delivery_owed(self, message):
        """A native turn can still start for this message, or a native delivery of it is still to be settled: one in
        flight (submitting, submitted, uncertain) or pending with a wake. Never a lead's mail (lead_mail)."""
        if message["state"] in ("entered_context", "acknowledged") or self.lead_mail(message):
            return False
        return message["delivery"] in ("submitting", "submitted", "uncertain") or message["delivery"] == "pending" and message["wake"] != "none"

    def writer_owed(self, unitid):
        """Work that only the unit's writer can start or settle: its run is unfinished, the unit is not quiet (a turn,
        custody, an admission, a live operation, a workflow input or a native delivery in flight), or a message to one
        of its writers is owed a delivery (delivery_owed). A writer whose reconcile failed is retried only for it;
        otherwise its next resume reconciles it."""
        from .writer import TERMINAL
        if self.store.get(self.store.get(unitid)["run"])["state"] not in TERMINAL or not self.unit_quiet(unitid):
            return True
        return any(self.delivery_owed(m) for m in self.store.selected(
            "SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.unit')=? AND "
            "json_extract(data,'$.delivery') IN ('pending','submitting','submitted','uncertain') AND "
            "json_extract(data,'$.state') NOT IN ('entered_context','acknowledged')", (unitid,)))

    def readmit(self, runid):
        """In the background, reconcile a run's writer app-server again. That covers three cases:
        - one another release admitted, once its unit is quiet (it is replaced);
        - one whose reconcile failed, once its retry is due;
        - a finished run's writer that startup recovery still owes (its incident, recovery.startup_obligation).
        What waits for it (a message, a prepared input) stays pending until then; maintenance delivers or submits it
        after. None starts while the daemon drains. A drain waits for those under way (self.readmitting, and every
        reconcile in self.listener_reconciles) before it stops listeners."""
        if runid in self.readmitting or self.draining:
            return
        self.readmitting.add(runid)
        async def replace():
            try:
                advance = False
                async with self.writer.locks.hold(runid):
                    if self.draining:
                        return
                    from .recovery import reconcile_listener, retry_due, startup_obligation
                    run = self.store.get(runid)
                    listener = self.store.get(self.store.get(run["root_actor"])["listener"])
                    incident = startup_obligation(self.store, runid)
                    if retry_due(self, listener) or listener["state"] == "connected" and listener.get("recovery_state") != "failed" and \
                            self.hook_stale(listener) and self.unit_quiet(run["unit"]):
                        if incident:
                            self.store.event("listener/startup_retry", listener["id"],
                                {"run": runid, "incident": incident["id"], "retry_at": listener.get("retry_at"),
                                 "retries": listener.get("recovery_retries", 0)})
                        await reconcile_listener(self, listener, advance=False)
                        advance = bool(incident and run.get("workflow") and not startup_obligation(self.store, runid))
                # Writer.advance takes this same non-reentrant writer lock. Match successful startup after releasing it.
                if advance and not self.draining:
                    await self.writer.advance(runid)
            except (CoordinatorError, OSError) as exc:
                self.store.event("listener/readmit_refused", runid, {"code": getattr(exc, "code", type(exc).__name__)})
            finally:
                self.readmitting.discard(runid)
        self.background(replace())

    def readmission_config(self, config):
        """A listener's stored config with this release's coordinator hooks in place of those it was admitted with.
        Its trust entries for them stay keyed by position: admit_listener replaces their hashes."""
        _, hooks = self.hook_config()
        return dict(config, thread_unload_delay_secs=0, features=dict(config.get("features") or {}, hooks=True),
                    hooks=dict(config.get("hooks") or {}, PreToolUse=hooks["hooks.PreToolUse"], PostToolUse=hooks["hooks.PostToolUse"]))

    async def admit_listener(self, actor, config, home, roots, cwd):
        """A writer's app-server with exactly the coordinator's hooks trusted: a first, threadless one reports their
        hashes, and the one kept starts with that trust."""
        command, _ = self.hook_config()
        profile_base_config = config
        service_environment = await self.broker.acquire(actor["run"])
        spawned = []
        try:
            listener = await self.spawn_listener(actor, config, home)
            spawned.append(listener["id"])
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
            from .writer_profile import configure, verify
            selected = self.writer_profile
            policy, profile = await configure(connection, effective.get("config", {}), cwd, selected)
            config = apply(config, overlay(effective.get("config", {})))
            config = apply(config, policy)
            prior_trust = effective.get("config", {}).get("hooks", {}).get("state", {})
            config.setdefault("hooks", {})["state"] = dict(prior_trust, **trust)
            await self.stop_listener(listener)
            listener = await self.spawn_listener(actor, config, home, service_environment=service_environment)
            spawned.append(listener["id"])
            connection = self.connections[listener["id"]]
            effective_writer = (await connection.call("config/read", {"cwd": cwd, "includeLayers": False}))["config"]
            verify(effective_writer, profile)
            self.broker.verify_environment(effective_writer, service_environment)
            listed = await connection.call("hooks/list", {"cwds": roots})
            own = native.coordinator_hooks(listed, roots, command=command)
            if len(own) != 2 or any(h["trustStatus"] != "trusted" or not h["enabled"] for h in own):
                raise CoordinatorError("HOOK_TRUST", "Precise native hook trust was not honored")
            listener.update(hooks=own, config_digest=digest(effective), writer_profile=profile, profile_base_config=profile_base_config)
            self.store.save(listener)
            return listener
        except BaseException:
            # These listeners are still threadless; finish every owned retirement.
            async def cleanup():
                for ident in spawned:
                    try:
                        await self.stop_listener(self.store.get(ident), kill=True)
                    except (CoordinatorError, OSError) as exc:
                        self.store.event("listener/stop_refused", ident, {"code": getattr(exc, "code", type(exc).__name__)})
            from .locks import finish
            await finish(cleanup())
            raise

    async def offline_listener(self, actor, original):
        from .writer_profile import configure, verify
        from .roles import apply, overlay
        config = self.readmission_config(original.get("profile_base_config", original["config"]))
        reader = await self.spawn_listener(actor, config, original.get("home"))
        try:
            connection = self.connections[reader["id"]]
            cwd = actor.get("cwd") or self.store.get(actor["checkout"])["root"]
            effective = (await connection.call("config/read", {"cwd": cwd, "includeLayers": False}))["config"]
            policy, profile = await configure(connection, effective, cwd, self.writer_profile)
            selected = apply(apply(config, overlay(effective)), policy)
            await self.stop_listener(reader, kill=True)
            reader = await self.spawn_listener(actor, selected, original.get("home"))
            connection = self.connections[reader["id"]]
            verify((await connection.call("config/read", {"cwd": cwd, "includeLayers": False}))["config"], profile)
            reader.update(writer_profile=profile, purpose="offline_history")
            self.store.save(reader)
            return reader
        except BaseException:
            from .locks import finish
            await finish(self.stop_listener(reader, kill=True))
            raise

    async def spawn_listener(self, actor, config, home=None, incarnation=None, service_environment=None):
        from .writer_profile import require_valid
        require_valid(self.writer_profile)
        ident = incarnation or uid("listener")
        directory = self.store.root / "listeners" / ident
        directory.mkdir(mode=0o700, exist_ok=True)
        allocation = runtime.allocate("native")
        socket = Path(allocation["path"])
        binary = native.native_codex()
        environment = native.environment(SOFTWARE_WORKFLOWS_ROLE="writer")
        environment["LOG_FORMAT"] = "json"
        environment["RUST_LOG"] = native.WRITER_LOG
        if home:
            environment["CODEX_HOME"] = str(home)
        # Native's log goes to a file the listener itself holds open, not to a pipe from this daemon: what it
        # writes while the daemon restarts is read afterwards, from the listener's durable offset.
        log = directory / "native.log"
        descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            offset = os.fstat(descriptor).st_size
            from .owned import spawn
            process = await spawn(binary, "app-server", "--listen", "unix://" + str(socket),
                                                           kind="listener", listener=ident,
                                                           *native.override_args(dict(config, **({"shell_environment_policy.set": service_environment} if service_environment else {}))),
                                                           cwd=actor.get("cwd", self.store.get(actor["checkout"])["root"]),
                                                           env=environment, stdin=asyncio.subprocess.DEVNULL,
                                                           stdout=asyncio.subprocess.DEVNULL, stderr=descriptor,
                                                           start_new_session=True, close_fds=True)
        finally:
            os.close(descriptor)
        try:
            birth = await telemetry.to_thread(processes.identity, process.pid)
            if birth is None:
                code = await process.wait()
                raise CoordinatorError("LISTENER_START", "Native listener exited before its process identity could be recorded", {"exit_code": code}, True)
            self.store.put("listener", ident, {"actor": actor["id"], "run": actor["run"], "unit": actor["unit"],
                           "socket": str(socket), "binary": binary, "binary_digest": await telemetry.to_thread(native.file_digest, binary),
                           "endpoint_allocation": allocation,
                           "pid": process.pid, "birth": birth["birth"], "incarnation": ident, "state": "starting",
                           "kernel_id": birth.get("kernel_id"), "boot_session": birth.get("boot_session"),
                           "owner_sequence": process.family["owner_sequence"],
                           "home": str(home) if home else None, "config": config,
                           "hook_digest": native.file_digest(Path(__file__).with_name("hooks.py")),
                           "log": str(log), "generation": self.store.generation}, actor["run"])
            # Only read_native_log writes this record: a listener record saved from a stale copy cannot undo it.
            self.store.put("native_log", ident + ":log", {"offset": offset}, ident)
            deadline = asyncio.get_running_loop().time() + 30
            while not socket.exists():
                if await process.poll() is not None or asyncio.get_running_loop().time() >= deadline:
                    self.read_native_log(ident)
                    raise CoordinatorError("LISTENER_START", "Native listener failed to start",
                                           {"exit_code": process.returncode, "path": str(socket),
                                            "bytes": len(os.fsencode(socket)), "measured_limit": runtime.capacity(),
                                            "diagnostic": self.native_log(self.store.get(ident)).get("startup_diagnostic")}, True)
                await asyncio.sleep(0.02)
        except BaseException:
            async def cleanup():
                # It never served, so nothing was admitted to it: end it and what it started (its own process group, while
                # this daemon has not reaped it), so no failed start outlives its error.
                await process.end()
                try:
                    runtime.cleanup(allocation)
                    cleaned = True
                except (CoordinatorError, OSError) as exc:  # recorded; the start's own error is the one raised
                    cleaned = False
                    self.store.event("listener/cleanup_refused", ident, {"code": getattr(exc, "code", type(exc).__name__)})
                Path(log).unlink(missing_ok=True)
                record = self.store.get(ident, required=False)
                if record:
                    record.update(state="stopped", allocation_cleaned=cleaned)
                    self.store.save(record)
            from .locks import retire
            await retire(cleanup)
            raise
        try:
            await self.attach(self.store.get(ident))
        except BaseException:
            from .locks import finish
            await finish(self.stop_listener(self.store.get(ident), kill=True))  # nothing runs there
            raise
        return self.store.get(ident)

    def read_native_log(self, listenerid):
        """Apply the listener's log past its offset: startup diagnostics, policy traces, the latest line of native's
        session and of a failed rollout write (rollout.native_mark: `written`, `failed`), and for each admitted call
        the result native writes once its handler returned (`codex.tool_result`, success or failure) and the mark
        it writes once its dispatch ended (`codex.tool_result_ready`, aborted dispatches included), on the call's
        record whatever its state. Synchronous, and what it applies commits with the offset that passes those
        lines, so a line is applied once. Lines with nothing to apply advance only the offset in memory: after a
        restart they are read again, to no effect. Returns the operations given a receipt."""
        listener = self.store.get(listenerid)
        if not listener.get("log"):
            return []  # started before 2.4.1, logging to a pipe
        offset = max(self.store.get(listenerid + ":log")["offset"], self.log_offsets.get(listenerid, 0))
        try:
            with open(listener["log"], "rb") as stream:
                if os.fstat(stream.fileno()).st_size <= offset:
                    return []
                stream.seek(offset)
                data = stream.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n") + 1  # a line still being written waits for its newline
        if not end:
            return []
        from .rollout import native_mark, fold
        lines, policies, receipts, writing = data[:end].splitlines(), [], [], {}
        for line in lines:
            try:
                entry = json.loads(line)
            except (ValueError, UnicodeError):
                entry = None
            # What it tells of native's rollout writing (runs.unproven_reason), kept with the offset as receipts are.
            fold(writing, native_mark(entry, time.time()))
            fields = entry.get("fields") if isinstance(entry, dict) else None
            if not isinstance(fields, dict):
                continue
            if fields.get("message") == "exec policy rules loaded":
                policies.append(fields.get("rules"))
            elif (entry.get("target") == "codex_otel.trace_safe" and (fields.get("event.name") in ("codex.tool_result_ready", "codex.tool_call_received") or
                    fields.get("event.name") == "codex.tool_result" and fields.get("success") in ("true", "false"))):
                receipts.append(fields)
        given = []
        if listener["state"] == "starting" or policies or receipts or writing:
            with self.store.transaction():
                record = self.store.get(listenerid + ":log")
                for kind, mark in writing.items():
                    fold(record, (kind, mark["time"], mark.get("line")))
                if listener["state"] == "starting":
                    record["startup_diagnostic"] = (record.get("startup_diagnostic", []) + [native.startup_diagnostic(line) for line in lines])[-8:]
                for rules in policies:
                    record.setdefault("policy_traces", []).append(native.policy_trace_digest(rules))
                    record.setdefault("policy_trace_observations", []).append(digest(rules))
                marks, received = record.get("abandoned", {}), record.get("received", {})
                for fields in receipts:
                    thread, call = fields.get("conversation.id"), fields.get("call_id")
                    if fields["event.name"] == "codex.tool_call_received":
                        if isinstance(thread, str) and isinstance(call, str):
                            # Native writes it before the call's Pre hook starts, which reads it (received_source).
                            received[thread + "\n" + call] = [fields.get("tool_source"), time.time()]
                        continue
                    key = "returned" if fields["event.name"] == "codex.tool_result" else "result_ready"
                    operation = self.receipt_operation(listener, thread, call, key)
                    if not operation:
                        if key == "result_ready" and isinstance(thread, str) and isinstance(call, str):
                            # The dispatch ended before any record of the call existed: native gave up on it while
                            # its Pre hook still queued, and that hook must not allow it (abandoned_early).
                            marks[thread + "\n" + call] = time.time()
                        continue
                    if key == "returned":
                        # Native writes the result after the handler's last write: no write can follow it.
                        operation["returned"] = {"success": fields["success"] == "true", "tool": fields.get("tool_name"), "seq": fields.get("tool_result_seq")}
                        self.store.save(operation)
                        self.ledger.end_execution(operation, transaction=False)
                        if operation["returned"]["success"]:
                            continue
                        self.store.event("native/returned", operation["id"], operation["returned"])
                    else:
                        # The dispatch is over, its Post hook included: settlement can stop waiting for the call.
                        # `source` is "direct" for a call of the model's response, "code_mode" for one a cell made.
                        operation["result_ready"] = {"tool": fields.get("tool_name"), "source": fields.get("tool_source"),
                                                     "turn": fields.get("turn_id")}
                        self.store.save(operation)
                        if not operation.get("returned"):
                            self.store.event("native/ended_without_result", operation["id"], operation["result_ready"])
                    given.append(operation["id"])
                from .hooks import NATIVE_HOOK_SECONDS
                # An abandoned mark serves only a Pre hook native began before it wrote the mark, and Codex ends
                # every hook within its timeout of the hook's start. A received line serves the Pre hook native
                # starts after it; one read later finds none, and its call's source stays unknown.
                horizon = time.time() - NATIVE_HOOK_SECONDS
                marks = {mark: seen for mark, seen in marks.items() if seen >= horizon}
                received = {mark: seen for mark, seen in received.items() if seen[1] >= horizon}
                for name, kept in (("abandoned", marks), ("received", received)):
                    if kept:
                        record[name] = kept
                    else:
                        record.pop(name, None)
                record["offset"] = offset + end
                self.store.save(record)
        self.log_offsets[listenerid] = offset + end
        return given

    def native_log(self, listener):
        """What a listener's log has shown: its offset, policy traces and startup diagnostics. A listener
        started before 2.4.1 keeps the traces its pipe delivered on its own record."""
        return self.store.get(listener["id"] + ":log", required=False) or listener

    def receipt_operation(self, listener, thread, call, key):
        """The admitted call a receipt names, in any state, not yet given that receipt. Only this listener
        incarnation's: a replacement's receipts never apply to a call its predecessor admitted."""
        if not isinstance(thread, str) or not isinstance(call, str):
            return None
        found = [op for op in self.store.call_operations(listener["unit"], call)
                 if op["native"].get("listener") == listener["id"] and op["native"].get("thread") == thread]
        if len(found) != 1 or found[0].get(key):
            return None
        return found[0]

    def abandoned_early(self, listener, thread, call):
        """Whether native ended this call's dispatch before any record of it existed (read_native_log keeps that
        mark, durably): it gave up on a call whose Pre hook had not prepared it yet, a hook queued behind others."""
        record = self.store.get(listener["id"] + ":log", required=False) or {}
        return isinstance(thread, str) and isinstance(call, str) and thread + "\n" + call in record.get("abandoned", {})

    def received_source(self, listener, thread, call):
        """How native received this call, from the line it writes before the call's Pre hook starts
        (`codex.tool_call_received`): "direct" from the model's response, "code_mode" from a cell. None when no
        such line was read (a listener without a log)."""
        record = self.store.get(listener["id"] + ":log", required=False) or {}
        if not isinstance(thread, str) or not isinstance(call, str):
            return None
        return (record.get("received", {}).get(thread + "\n" + call) or [None])[0]

    async def settle_native_log(self, listenerid):
        """Read the listener's log to its end, then settle each unsettled call it gave a receipt: also one whose
        settlement a restart interrupted after its receipt was recorded."""
        self.read_native_log(listenerid)
        for operation in list(self.store.selected("SELECT * FROM records INDEXED BY window_operation WHERE " + WINDOW_ACTIVE["operation"] +
                                                  " AND (json_extract(data,'$.returned') IS NOT NULL OR json_extract(data,'$.result_ready') IS NOT NULL)"
                                                  " AND json_extract(data,'$.native.listener')=? ORDER BY id", (listenerid,))):
            await self.settle_returned(operation)

    async def settle_returned(self, operation):
        """Native wrote a failed result for this call (Codex 0.163 `codex.tool_result`, success=false), direct or
        nested in a code-mode cell: its handler returned, refusing it (`tty` on a TTY-disabled runtime, an unusable
        `workdir`, a patch whose context is not in the file) or failing. That proves the call ended, not that it
        changed nothing. A patch is settled as no effect only with every endpoint at its pre-image; other bytes
        quarantine it. A command's checkouts are reconciled, as after any command, once no process or terminal of it
        remains; its custody is released then.

        A dispatch that ended without a result (`codex.tool_result_ready` alone) never finished its handler. One
        still waiting for its Pre hook never runs: native gave up on it (an interrupted turn, a cell that closed) and
        it is withdrawn. An admitted patch was aborted: 0.163 drops the handler, and a write it had begun still
        finishes on its own thread. It is quarantined, holding its paths while its app-server runs (`executing`).
        An admitted command was aborted too, whether before or while its handler ran: once no process or terminal of
        it remains, its checkouts are reconciled and its custody released (_settle_actor).

        A patch whose dispatch ended after its handler did, with no Post hook of ours received, never completes its
        lifecycle and is quarantined. With its Post hook received, native sent the item and the hook's outcome before
        it wrote the mark, over the app-server connection, which the log is not ordered against: they settle it
        when they arrive (maybe_verify). Its handler wrote all it will, so it only waits: for those, for its turn's
        end when the call was direct (the turn ends after its direct calls' dispatches, and native sends in order),
        or for its app-server's exit, whichever comes first (a Post hook native cancelled sends no outcome)."""
        operation = self.store.get(operation["id"])
        ident = operation.get("native", {}).get("listener")
        if self.listener_retiring(ident) and self.native_unapplied(ident):
            return  # replay the listener's hook/item evidence before judging its log
        aborted = operation.get("result_ready") and not operation.get("returned")
        if operation["state"] == "prepared" and aborted:
            await self.withdraw(operation["id"], operation.get("custody"), "NATIVE_ABANDONED")
        elif operation["state"] not in ("allowed", "running"):
            return
        elif returned_failure(operation):
            if operation["kind"] == "patch":
                await self.ledger.settle_unapplied(operation, "NATIVE_CALL_FAILED", returned_failure)
            else:
                await self.settle_actor(self.store.get(operation["actor"]))
        elif operation["kind"] == "patch" and not handler_ended(operation):
            await self.ledger.quarantine(operation, "native ended the call without a result", executing=True)
        elif operation["kind"] == "patch" and operation.get("result_ready") and not operation.get("post_received"):
            # Native ran no Post hook of ours for it: its lifecycle can never complete.
            await self.ledger.quarantine(operation, "native ended the call without its Post hook")
        elif operation["kind"] == "command" and aborted:
            await self.settle_actor(self.store.get(operation["actor"]))

    def listener_retiring(self, ident):
        connection = self.connections.get(ident)
        return ident in self.retiring_listeners or bool(connection and connection.retiring)

    def native_unapplied(self, ident):
        self.flush_native_progress()
        applied = self.store.get("native_applied:" + ident, required=False) or {}
        return self.store.db.execute("SELECT 1 FROM events WHERE kind='native' AND subject=? AND seq>? LIMIT 1",
                                     (ident, applied.get("sequence", 0))).fetchone() is not None

    async def executor_exited(self, listener, unfinished=None):
        """Its app-server has exited: nothing it admitted can still write. Quarantines stop holding the paths they
        kept for a write that might still finish. With `unfinished`, a patch it never reported ending is
        quarantined for that reason, as a crash gap is."""
        # An app-server's exit alone says nothing about the descendants it left.
        if listener.get("owner_sequence"):
            result = await self.leases.call("end", payload={"listener": listener["id"], "mode": "drain"})
            if result["family"]["state"] != "GONE":
                raise CoordinatorError("FAMILY_REAPING", "Recorded descendants still hold execution custody", operational=True)
            listener["owner_sequence"] = result["family"]["owner_sequence"]
            listener["family_gone"] = result["family"]["exit_evidence"]
            self.store.save(listener)
        else:
            self.legacy_exited(listener)
        query = "SELECT * FROM records INDEXED BY window_{} WHERE {} AND json_extract(data,'$.native.listener')=? ORDER BY id"
        # A stopped process is not evidence that its queued completion was absent.
        # Detach normally replays it; an interrupted retirement leaves this
        # judgement to the next reconcile, after the same durable suffix.
        if unfinished and not self.native_unapplied(listener["id"]):
            for operation in list(self.store.selected(query.format("operation", WINDOW_ACTIVE["operation"]), (listener["id"],))):
                if operation["kind"] == "patch" and operation["state"] in ("allowed", "running"):
                    await self.ledger.quarantine(operation, unfinished)
        for operation in list(self.store.selected(query.format("executing", WINDOW_ACTIVE["executing"]), (listener["id"],))):
            self.ledger.end_execution(operation)

    def legacy_exited(self, listener):
        # Migration of already-exited writers is observation, never permission
        # to signal. Retained custody identities must independently have ended.
        members = [listener]
        for reservation in self.custody.unit_active(listener["unit"]):
            members.extend(reservation.get("processes", []))
        if any(not processes.exit_evidence(member) for member in members):
            raise CoordinatorError("OWNER_UNREGISTERED", "Unregistered surviving processes retain custody; operator reconciliation required",
                                   {"listener": listener["id"], "retryable": True}, True)

    async def native_turn(self):
        """FIFO cooperative service: n live tickets ahead means at most n+1 grants.

        A grant always permits a step, even if another step overran 2 ms before
        its owner resumed. No release or lock spans arbitrary application awaits.
        Cheap consecutive steps share a slice only when nobody is owed service.
        """
        loop = asyncio.get_running_loop()
        if not self.native_tickets and not self.native_granted and self.native_service is None and loop.time() < self.native_slice_until:
            return
        owner = asyncio.current_task()
        future = self.native_waiters.get(owner)
        if future is None:
            future = loop.create_future()
            self.native_waiters[owner] = future
            self.native_tickets.append((owner, future))
        if self.native_service is None:
            self.native_service = loop.call_soon(self.native_grant)
        try:
            await future
        finally:
            self.native_waiters.pop(owner, None)
            self.native_granted.discard(owner)

    def native_grant(self):
        self.native_service = None
        while self.native_tickets:
            owner, future = self.native_tickets.popleft()
            if future.done():
                continue
            loop = asyncio.get_running_loop()
            self.native_slice_until = loop.time() + native.JOURNAL_BATCH_AGE_S
            self.native_granted.add(owner)
            future.set_result(None)
            if self.native_tickets:
                self.native_service = loop.call_soon(self.native_grant)
            break

    def check_native_stop(self):
        if self.native_stop_error is not None:
            raise self.native_stop_error

    def check_native_resource(self, listener, error, stage):
        if native.local_resource_failure(error):
            connection = self.connections.get(listener)
            if connection is not None:
                connection.intake_failed(error, stage)
        self.check_native_stop()

    async def tail_native_log(self, listenerid):
        """Follow a live listener's log. A failure to read it is recorded, never fatal: a result read late only
        delays a release."""
        failure = None
        while True:
            listener = self.store.get(listenerid, required=False)
            if not listener or not listener.get("log") or listener["state"] in ("stopped", "dead"):
                if self.log_tails.get(listenerid) is asyncio.current_task():
                    del self.log_tails[listenerid]
                return
            if listener.get("recovery_state") == "reconciling":
                # Recovery reads the log itself, once the journaled items it replays are applied.
                await asyncio.sleep(NATIVE_LOG_POLL_S)
                continue
            try:
                offset = max(self.store.get(listenerid + ":log")["offset"], self.log_offsets.get(listenerid, 0))
                try:
                    grew = os.stat(listener["log"]).st_size > offset
                except FileNotFoundError:
                    grew = False
                if not grew:
                    await asyncio.sleep(NATIVE_LOG_POLL_S)
                    continue
                await self.native_turn()
                for ident in self.read_native_log(listenerid):
                    self.background(self.settle_returned(self.store.get(ident)))
                failure = None
            except Exception as exc:
                self.check_native_resource(listenerid, exc, "native log")
                code = getattr(exc, "code", type(exc).__name__)
                if code != failure:
                    self.store.event("native/log_failure", listenerid, {"code": code})
                failure = code
                await asyncio.sleep(1)
            await asyncio.sleep(NATIVE_LOG_POLL_S)

    async def attach(self, listener):
        if self.native_intake_closed:
            raise CoordinatorError("DAEMON_STOPPING", "Native intake has ended", operational=True)
        ident = listener["id"]
        if listener.get("owner_sequence"):
            grant = await self.leases.call("adopt", payload={"listener": ident})
        else:
            # Upgrade bridge: only a persisted, thread-bound predecessor may be
            # imported. The supervisor verifies its birth and journals ownership
            # before granting it; threadless living bootstraps still refuse.
            saved = self.store.get(ident)
            proof = {key: saved[key] for key in ("id", "pid", "birth", "kernel_id", "boot_session",
                                                "root_thread", "process_lineage") if key in saved}
            grant = await self.leases.call("adopt-legacy", payload={"record": proof})
        listener["owner_sequence"] = grant["family"]["owner_sequence"]
        self.store.save(listener)
        await self.detach_listener(ident)
        self.retiring_listeners.discard(ident)
        self.flush_native_progress()
        # New notifications cannot prove that an older incarnation's received
        # journal suffix was applied. Hold its high-water mark before opening
        # intake; replay_native clears it only after covering that entire suffix.
        key = "native_applied:" + ident
        applied = self.store.get(key, required=False) or {}
        received = self.store.db.execute("SELECT max(seq) FROM events WHERE kind='native' AND subject=?",
                                         (ident,)).fetchone()[0]
        if received is not None and received > applied.get("sequence", 0):
            progress = {"sequence": applied.get("sequence", 0),
                        "pending": max(received, applied.get("pending", 0))}
            if applied.get("failed"):
                progress["failed"] = applied["failed"]
            self.store.put("native_applied", key, progress, ident)
        tail = self.log_tails.get(ident)
        if not tail or tail.done():
            self.log_tails[ident] = self.watch_listener_task(self.background(self.tail_native_log(ident)), ident, "log")
        queue = NativeBacklog(self.store, ident, received or 0)
        self.queues[ident] = queue
        self.listener_requests[ident] = set()
        def inbound(message):
            if message.get("method") == "coordinator/closeEscalated":
                # The close timer ends before the connection is joined and
                # SQLite closes, including after the forced-stop intake fence.
                self.store.event("native/close_escalated", ident, message["params"])
                return
            inbound_batch([(0, message, 0, time.monotonic())])

        def inbound_batch(entries):
            if self.native_intake_closed:
                # No successful journal barrier may cover discarded input.
                raise native.Retired()
            traces = []
            began = time.monotonic()
            # Receipt and ownership must commit together. Replay can run while the
            # notification worker is behind; a live request is still owned by this
            # generation even before its handler has been scheduled.
            with self.store.transaction():
                for _, message, _, _ in entries:
                    trace = self.completion_trace(listener, message)
                    with telemetry.using([trace]):
                        sequence = self.store.event("native", ident, message)
                        if "id" in message:
                            self.store.put("native_obligation", "native_obligation:" + str(sequence),
                                {"sequence": sequence, "generation": self.store.generation, "state": "pending"}, ident)
                    if trace:
                        traces.append((trace, sequence))
            ended = time.monotonic()
            for trace, traced_sequence in traces:
                trace.add("journal_receipt", began, ended)
                trace.marks["journaled"] = ended
                trace.marks["sequence"] = traced_sequence
            # This publication is strictly after COMMIT; failed batches leave
            # neither an application wakeup nor a claimed receipt high-water.
            queue.publish(sequence, len(entries))
            self.parking.touch(ident)

        def intake_failure(details):
            self.native_stop_error = connection.intake_error
            self.closing, self.ready = True, False
            self.stop.set()
            for current in self.connections.values():
                if current.intake is not None:
                    current.intake.wake()
                for waiter in current.response_waits:
                    waiter.changed()
            try:
                if self.store.db.in_transaction:
                    raise RuntimeError("Journal rollback did not end its transaction")
                self.store.event("native/journal_failure", ident, details)
            except Exception as diagnostic:
                print(json.dumps({"event": "native/journal_failure", "listener": ident,
                                  "details": details, "diagnostic_error": type(diagnostic).__name__}),
                      file=sys.stderr, flush=True)
            finally:
                # Even an unavailable database must not strand the owner/waiters
                # or prevent the visible stop and next-generation replay.
                self.closing, self.ready = True, False
                self.stop.set()
        from .admission import Admission
        schema, _ = await Admission(self.store).schema()
        try:
            connection = await native.Connection.connect(listener["socket"], inbound, native.Schema(schema),
                on_batch=inbound_batch, turn=self.native_turn, on_failure=intake_failure, check_stop=self.check_native_stop)
            if self.native_intake_closed:
                await connection.close(abort=True)
                raise CoordinatorError("DAEMON_STOPPING", "Native intake has ended", operational=True)
            self.connections[ident] = connection
            self.workers[ident] = self.watch_listener_task(self.background(self.native_worker(listener, queue)), ident, "native application")
            initialized = await connection.initialize()
        except BaseException:
            await self.detach_listener(ident)
            raise
        listener.update(state="connected", user_agent=initialized.get("userAgent"), generation=self.store.generation)
        listener["endpoint"] = runtime.endpoint_info(listener["socket"], native=True)
        self.store.save(listener)
        return connection

    async def detach_listener(self, ident):
        """Retire intake and consumers without abandoning their owned cleanup."""
        if self.workers.get(ident) is asyncio.current_task():
            # Run teardown after this notification's task_done, never self-await.
            self.background(self.detach_listener(ident))
            return
        from .locks import retire
        await retire(self._detach_listener, ident, asyncio.current_task())

    async def _detach_listener(self, ident, owner):
        from .locks import cancel_and_join
        began = time.monotonic()
        self.retiring_listeners.add(ident)
        connection = self.connections.get(ident)
        worker = self.workers.get(ident)
        if not self.stop.is_set():
            if connection:
                await connection.close()
            # Local close can cancel a consumer's pending native RPC. Queued
            # evidence remains in the journal; attach/replay covers that suffix.
            # queue.join() cannot finish after that consumer has ended.
        closed = time.monotonic()
        requests = self.listener_requests.get(ident, set())
        consumers = {task for task in requests if task is not owner}
        if worker and worker is not owner:
            consumers.add(worker)
        await cancel_and_join(consumers)
        if connection and self.stop.is_set():
            await connection.close(abort=True)
        self.listener_requests.pop(ident, None)
        self.connections.pop(ident, None)
        self.workers.pop(ident, None)
        self.queues.pop(ident, None)
        consumed = replayed = time.monotonic()
        if not self.stop.is_set():
            # The replay cursor only proves evidence was stored. Receipt reads
            # can still have been cancelled after that cursor advanced.
            if self.native_unapplied(ident):
                from .recovery import replay_native
                await replay_native(self, self.store.get(ident))
            replayed = time.monotonic()
            query = ("SELECT * FROM records INDEXED BY window_operation WHERE " + WINDOW_ACTIVE["operation"]
                     + " AND json_extract(data,'$.native.listener')=? ORDER BY id")
            for operation in list(self.store.selected(query, (ident,))):
                if operation["kind"] == "patch" and not operation.get("parent_operation"):
                    await self.maybe_verify(operation)
        ended = time.monotonic()
        self.store.event("listener/detached", ident, {
            "started": began, "ended": ended, "seconds": ended - began, "draining": self.draining,
            "close_seconds": closed - began, "consumers_seconds": consumed - closed,
            "replay_seconds": replayed - consumed, "receipts_seconds": ended - replayed})

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
        _, hooks = self.hook_config()
        config.update(hooks)
        config.update({"web_search": "live", "agents.enabled": True, "features.multi_agent_v2": True,
                       "features.multi_agent_v2_dynamic_tools": True, "features.unified_exec_tty": False,
                       "approval_policy": "never", "sandbox_mode": "danger-full-access"})
        config.update({"model_reasoning_effort": "max", "model_reasoning_summary": "detailed"})
        config["thread_unload_delay_secs"] = 0
        workflow = params.get("workflow")
        if workflow and workflow["compact_limit"]:
            config["model_auto_compact_token_limit"] = workflow["compact_limit"]
        listener = await self.admit_listener(actor, config, home, roots, cwd)
        connection = self.connections[listener["id"]]
        actor["listener"] = listener["id"]
        self.store.save(actor)
        from .writer_profile import context
        profile_line = context(listener["writer_profile"]) if listener["writer_profile"]["mode"] == "list" else ""
        response = await connection.call("thread/start", {"cwd": cwd,
                                          "config": await self.broker.thread_config(actor),
                                          **({"projectId": unit["project"], "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {}),
                                          "model": workflow["model"] if workflow else params.get("model", "gpt-6-astra"), "approvalPolicy": "never",
                                          "sandbox": "danger-full-access", "dynamicTools": tools.specifications(),
                                          "developerInstructions": "Coordinate through claim, declare, send, inbox, ack and status. "
                                          "Use apply_patch for attributable edits. Commands have opaque effects. "
                                          "Never publish or rewrite Git state: the lead records. "
                                          "Messages require ack from the named recipient. Child fallback ack format: C1_ACK <message-id>. "
                                          + "Workspace roots in order: " + json.dumps(roots) + ". Paths are absolute or relative to the actual tool cwd. "
                                          "Qualified coordinator paths use {root_id: checkout-id, path: repository-relative-path}." +
                                          ("\n" + profile_line if profile_line else "") +
                                          ("\n" + self.broker.instructions(actor) if self.broker.instructions(actor) else "")})
        from .projects import verify_thread
        verify_thread(response, unit, cwd, roots)
        actor.update(thread=response["thread"]["id"], session=response["thread"]["id"], policy=native.policy_echo(response))
        if profile_line:
            actor["writer_profile_context"] = profile_line
        self.store.save(actor)
        listener = self.store.get(listener["id"])
        listener["root_thread"] = actor["thread"]
        listener["services"] = await telemetry.to_thread(processes.inventory, listener, None)
        self.store.save(listener)
        admitted = await admission.writer_gate(self, listener["id"], response, fixture=bool(fixture))
        listener = self.store.get(listener["id"])
        listener["recovery_state"] = "reconciled"
        self.store.save(listener)
        self.parking.touch(listener["id"])
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

    async def listener_exited(self, listener, seconds):
        deadline = time.monotonic() + seconds
        while True:
            current = await telemetry.to_thread(processes.identity, listener["pid"])
            if not current or current["birth"] != listener["birth"] or current["zombie"]:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    async def stop_listener(self, listener, drain_s=5, kill=False):
        from .locks import retire
        await retire(self._stop_listener, listener, drain_s, kill)

    async def _stop_listener(self, listener, drain_s=5, kill=False):
        """SIGTERM its app-server, then SIGCONT (one stopped by a signal acts on neither until it is continued), and
        wait `drain_s` for Codex's graceful exit. One still draining then is kept (LISTENER_DRAIN), or killed: with
        `kill`, for an app-server with nothing of its unit in flight, and always for one found stopped or behind a dead
        connection (recovery.end_wedged). SIGKILL ends a stopped process too."""
        listener = self.store.get(listener["id"])
        if listener["state"] == "dormant" or listener["state"] == "stopped" and listener.get("allocation_cleaned"):
            return
        began_stop = time.monotonic()
        listener["state"] = "stopping"
        self.store.save(listener)
        await self.detach_listener(listener["id"])
        detached = time.monotonic()
        kill = kill or bool(listener.get("wedged"))
        if listener.get("owner_sequence"):
            result = await self.leases.call("end", payload={"listener": listener["id"], "mode": "drain"})
            if result["family"]["state"] != "GONE":
                raise CoordinatorError("LISTENER_DRAIN", "Recorded family remains alive; custody retained")
            listener["owner_sequence"] = result["family"]["owner_sequence"]
            self.store.save(listener)
        else:
            self.legacy_exited(listener)
        exited_at = time.monotonic()
        if listener.get("endpoint_allocation"):
            if Path(listener["endpoint_allocation"]["directory"]).exists():
                runtime.cleanup(listener["endpoint_allocation"])
        await self.settle_native_log(listener["id"])  # what it wrote before it exited
        await self.executor_exited(listener, "app-server stopped before the patch ended")
        listener = self.store.get(listener["id"])
        listener.update(state="stopped", allocation_cleaned=True)
        self.store.save(listener)
        if listener.get("log"):
            # Applied to its operations; the log also holds policy text, which C1 does not keep.
            Path(listener["log"]).unlink(missing_ok=True)
            self.log_offsets.pop(listener["id"], None)
        ended = time.monotonic()
        self.store.event("listener/stopped", listener["id"], {
            "started": began_stop, "ended": ended, "seconds": ended - began_stop, "draining": self.draining,
            "detach_seconds": detached - began_stop, "exit_seconds": exited_at - detached,
            "settle_seconds": ended - exited_at})

    async def resolve_actor(self, listener, thread, session=None, connection=None):
        async with self.actor_locks.hold((listener["id"], thread)):
            return await self.resolve_actor_locked(listener, thread, session, connection)

    def listener_connection(self, listener):
        self.check_native_stop()
        connection = self.connections.get(listener["id"])
        if connection is not None:
            connection.check_intake()
        if connection is None or connection.closed:
            raise CoordinatorError("LISTENER_UNAVAILABLE", "This listener has no live connection",
                                   {"listener": listener["id"], "state": listener["state"], "retryable": True}, True)
        return connection

    async def discover_descendants(self, listener):
        connection = self.listener_connection(listener)
        async for page in native.page(connection, "thread/loaded/list", {}):
            for entry in page["data"]:
                await self.resolve_actor(listener, entry if isinstance(entry, str) else entry["id"])
        for archived in (False, True):
            async for page in native.page(connection, "thread/list", {"ancestorThreadId": listener["root_thread"], "archived": archived}):
                for entry in page["data"]:
                    await self.resolve_actor(listener, entry["id"])

    async def resolve_actor_locked(self, listener, thread, session=None, connection=None):
        for actor in self.store.all("actor", listener["unit"]):
            if actor.get("thread") == thread and actor.get("listener") == listener["id"]:
                return actor
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            try:
                response = await (connection or self.listener_connection(listener)).call("thread/read", {"threadId": thread, "includeTurns": False})
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
        parent = await self.resolve_actor(listener, info["parentThreadId"], connection=connection)
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
        return next((op for op in self.store.call_operations(listener["unit"], call)
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
        services = None if prior else await telemetry.to_thread(processes.inventory, listener, None)
        listener = self.store.get(listener["id"])
        if services is not None:
            listener["services"] = services
        listener["service_boundary"] = {"time": time.time(), "generation": self.store.generation,
                                        "captured": services is not None, "prior_opaque_operations": prior}
        self.store.save(listener)
        self.store.event("listener/service_boundary", listener["id"], listener["service_boundary"])

    async def hook(self, payload, hook_process=None, deadline=None):
        from .hooks import ANSWER_MARGIN_S
        from .recovery import writer_ready
        # A writer still being reconciled (startup recovery, a reconnect, a re-admission) is waited for while the hook
        # client still listens; LISTENER_RECOVERING after that, and HOOK_IDENTITY as before.
        listener = await writer_ready(self, payload.get("session_id"),
                                      (deadline if isinstance(deadline, (int, float)) else time.time() + 115) - ANSWER_MARGIN_S)
        # Until it is decided, the call is its unit's call in flight (unit_quiet), counted with no await after
        # writer_ready's own count, so no re-admission replaces the app-server it came from under it. (2.4.3 counted an
        # effect-capable call only, and only after the descendant check, its actor, the native log and its lease.)
        unit = listener["unit"]
        self.admitting_units[unit] = self.admitting_units.get(unit, 0) + 1
        try:
            return await self.admitted_hook(payload, hook_process, deadline, listener)
        finally:
            self.admitting_units[unit] -= 1
            if not self.admitting_units[unit]:
                del self.admitting_units[unit]

    async def admitted_hook(self, payload, hook_process, deadline, listener):
        session, thread = payload.get("session_id"), payload.get("agent_id") or payload.get("session_id")
        if native.file_digest(Path(__file__).with_name("hooks.py")) != listener["hook_digest"]:
            raise CoordinatorError("HOOK_PROGRAM_CHANGED", "Hook program changed since admission")
        if hook_process:
            if not await telemetry.to_thread(processes.descendant, hook_process, listener):
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
                self.ledger.end_execution(operation)  # native runs the Post hook after the handler returned
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
        from .hooks import ANSWER_MARGIN_S
        # Decide while the hook client still listens. An older client sends no deadline and
        # counts 115 s from its call.
        cutoff = (deadline if isinstance(deadline, (int, float)) else time.time() + 115) - ANSWER_MARGIN_S
        # Native writes a call's result before it hands the failure to the model or the cell: a retry of a
        # refused call finds that call settled, not still holding its paths.
        await self.settle_native_log(listener["id"])
        if self.abandoned_early(listener, thread, call):
            raise CoordinatorError("NATIVE_ABANDONED", "Native stopped waiting for this call; it does not run")
        identity["source"] = self.received_source(listener, thread, call)
        # Until its operation is recorded, an effect-capable call keeps its checkouts' compatibility lease, taken
        # again if a run's end released it meanwhile (a cell's call can still wait here after its turn).
        effect = tool == "apply_patch" or tool in ("Bash", "exec_command") or str(tool).startswith("mcp")
        if effect and self.store.get(actor["run"]).get("cancel_requested"):
            # A turn that started after its run's cancel (its turn/start in flight then, a queued message) is
            # interrupted once known; until it ends, none of its effect-capable calls runs.
            raise CoordinatorError("RUN_CANCELLED", "This run was cancelled: its calls do not run")
        guarded = await self.admission_lease(actor) if effect else []
        try:
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
                    await self.settle_before_claim(actor, listener, cutoff)
                    operation = await prepare_patch(self, actor, command, identity, cwd, cutoff)
                else:
                    scope = patch_scope(command)
                    for entry in scope:
                        entry["path"] = routing.route(self.store, actor, entry["path"], cwd)["path"]
                        if "move" in entry:
                            entry["move"] = routing.route(self.store, actor, entry["move"], cwd)["path"]
                    paths = [p for entry in scope for p in (entry["path"], entry.get("move")) if p]
                    if self.custody.unit_active(actor["unit"]):
                        # As for a project's patch: a command of this unit whose call already returned can still hold
                        # its custody until it is settled, and would refuse its own unit's next patch (CLAIM_CONFLICT).
                        await self.settle_before_claim(actor, listener, cutoff)
                    # Outside the ledger (ledger.unwatched) a patch is written, never recorded: no claim, no receipt.
                    claimed = await self.custody.acquire(actor, [routing.qualified(actor["checkout"], p) for p in paths])
                    outside = {p["path"] for p in claimed.get("outside", ())}
                    operation = await self.ledger.prepare(actor, "patch", {"patch": command, "scope": scope}, paths, identity, cutoff,
                                                          outside=outside)
            elif tool in ("Bash", "exec_command") or str(tool).startswith("mcp"):
                # A hook's command omits workdir and tty. Never infer them. Full-checkout
                # reconciliation and process identity supply the conservative context.
                async with self.actor_locks.hold("services:" + listener["id"], cutoff):
                    await self.services_before_first_command(listener)
                    operation = await self.ledger.prepare(actor, "command" if tool in ("Bash", "exec_command") else "mcp",
                                                          {"tool_input": payload.get("tool_input"), "tool": tool}, [], identity, cutoff)
                try:
                    reservation = await self.custody.reserve(actor, command or "opaque MCP " + str(tool), operation["id"],
                                                             timeout=max(0.0, cutoff - time.time()))
                except CoordinatorError as exc:
                    operation = self.store.get(operation["id"])
                    if operation["state"] == "prepared":  # not withdrawn meanwhile
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
                # Codex's clock namespace (0.163: sleep, curr_time) only reads or waits on time.
                readers = {"web_search", "websearch", "web.run", "view_image", "update_plan", "request_user_input", "sleep",
                           "clocksleep", "clockcurr_time"}
                if tool not in set(tools.CONTRACTS) | cooperation | readers:
                    raise CoordinatorError("HOOK_TOOL_UNSUPPORTED", "No complete custody/effect contract for this native tool", {"tool": tool})
                self.store.event("hook/nonmutator", actor["id"], {"tool": tool, "native": identity})
                return {}
            # Native can stop waiting for this hook (an interrupted turn, a cell that closed): it then marks the call's
            # dispatch ended and never runs it, and the call is withdrawn, also when that mark came before the call
            # had a record.
            await self.settle_native_log(listener["id"])
            if self.store.get(operation["id"])["state"] != "prepared" or self.abandoned_early(listener, thread, call):
                await self.withdraw(operation["id"], operation.get("custody"), "NATIVE_ABANDONED")  # custody granted since
                raise CoordinatorError("NATIVE_ABANDONED", "Native stopped waiting for this call; it does not run")
            if time.time() >= cutoff:
                # The hook client has stopped waiting and denies at its deadline: an allow now would
                # hold custody for a call native never runs.
                from .locks import busy
                await self.withdraw(operation["id"], operation.get("custody"), "COORDINATOR_BUSY")
                raise busy()
            current = self.store.get(listener["id"])
            if current["state"] != "connected" or current.get("recovery_state") in ("reconciling", "failed"):
                # Its app-server began to be reconciled, stopped or replaced after this hook came in: an allow could
                # belong to an incarnation that is going away, and native may never run the call.
                await self.withdraw(operation["id"], operation.get("custody"), "LISTENER_RECOVERING")
                raise CoordinatorError("LISTENER_RECOVERING", "The writer's app-server is being reconciled; call again")
            if self.store.get(actor["run"]).get("cancel_requested"):
                # Its run was cancelled while this call was being admitted.
                await self.withdraw(operation["id"], operation.get("custody"), "RUN_CANCELLED")
                raise CoordinatorError("RUN_CANCELLED", "This run was cancelled: its calls do not run")
            if operation["kind"] == "command":
                self.settlements.setdefault(operation["id"], telemetry.Trace(operation["id"], operation["actor"], armed=False))
            operation["state"] = "allowed"
            operation["decision"] = "allow"
            self.store.save(operation)
            self.store.event("hook/decision", operation["id"], {"decision": "allow", "native": identity})
            # Native 0.163 treats permissionDecision=allow without updatedInput as a
            # hook failure. Empty successful output means continue, after our durable
            # allow decision; the matching completed lifecycle proves delivery.
            return {}
        finally:
            for key in guarded:
                self.admitting[key] -= 1

    async def withdraw(self, operationid, custodyid, refusal):
        """An admitted call that native never runs: refuse it, with the parts it prepared in other
        checkouts, and give back the custody it holds."""
        # One transaction: a denial committed apart from its custody release would leave a denied
        # call holding custody, which no later pass releases.
        async with self.custody.condition:
            operation = self.store.get(operationid)
            root = self.store.get(custodyid) if custodyid else None
            with self.store.transaction():
                if operation["state"] in ("prepared", "allowed"):
                    for record in [operation] + [self.store.get(key) for key in operation.get("parts", [])]:
                        if record["state"] in ("prepared", "allowed"):
                            record.update(state="denied", decision="deny", no_effect=True, refusal=record.get("refusal") or refusal)
                            self.store.save(record)
                    self.store.event("operation/withdrawn", operationid, {"refusal": refusal})
                for key in (root.get("members", [root["id"]]) if root else []):
                    member = self.store.get(key)
                    if member["state"] != "released":
                        self.custody.release_held(member)
            self.custody.condition.notify_all()

    def flush_native_progress(self):
        if not self.native_progress:
            return
        # No awaits: a listener failure or a replay cannot interleave with the
        # validation and commit. A crash before this commit safely repeats a suffix.
        with self.store.transaction():
            for ident, sequence in self.native_progress.items():
                applied = self.store.get("native_applied:" + ident, required=False) or {}
                if (not applied.get("failed") and not applied.get("pending")
                        and sequence > applied.get("sequence", 0)):
                    self.store.put("native_applied", "native_applied:" + ident, {"sequence": sequence}, ident)
        self.native_progress.clear()
        self.native_progress_count = 0

    def applied_native(self, ident, sequence):
        self.native_progress[ident] = max(sequence, self.native_progress.get(ident, 0))
        self.native_progress_count += 1
        if self.native_progress_count >= NATIVE_APPLIED_BATCH:
            self.flush_native_progress()
        if self.native_progress_flush is None or self.native_progress_flush.done():
            async def flush():
                await asyncio.sleep(NATIVE_APPLIED_FLUSH_S)
                self.flush_native_progress()
            self.native_progress_flush = self.background(flush())

    def completion_trace(self, listener, message):
        received = time.monotonic()
        params = message.get("params", {})
        item = params.get("item", {})
        if message.get("method") != "item/completed" or item.get("type") != "commandExecution":
            return None
        operation = self.find_operation(listener, params.get("threadId"), params.get("turnId"), item.get("id"))
        if not operation or operation["state"] in ("denied", "reconciled", "verified"):
            return None
        if operation["id"] not in self.settlements:
            self.settlements[operation["id"]] = telemetry.Trace(operation["id"], operation["actor"], armed=False)
        trace = self.settlements[operation["id"]]
        trace.arm(received)
        return trace

    def start_loop_supervisor(self):
        """Restart even an unexpected exit of the supervisory coroutine."""
        if self.stop.is_set():
            return
        from .watchdog import CLOSE_SECONDS, RETRY_MAX_SECONDS
        began = time.monotonic()
        task = self.background(self.supervise_loop_lag())
        def ended(task):
            if self.stop.is_set():
                return
            failure = "cancelled" if task.cancelled() else str(task.exception() or "returned")[:512]
            delay = getattr(self, "loop_supervisor_delay", CLOSE_SECONDS)
            if time.monotonic() - began >= RETRY_MAX_SECONDS:
                delay = CLOSE_SECONDS
            self.loop_supervisor_delay = min(RETRY_MAX_SECONDS, delay * 2)
            reason = "supervisor task ended while service remained open: " + failure
            self.loop_lag["supervisor"] = {"state": "restarting", "reason": reason,
                                           "since": time.time(), "retry_at": time.time() + delay}
            self.store.event("watchdog/supervisor_task_exited", None, {"reason": reason, "retry_seconds": delay})
            asyncio.get_running_loop().call_later(delay, self.start_loop_supervisor)
        task.add_done_callback(ended)

    async def supervise_loop_lag(self):
        """Own diagnostics until stop is irreversible, including failed drains."""
        from .watchdog import CLOSE_SECONDS, RETRY_MAX_SECONDS
        from .locks import finish
        delay = CLOSE_SECONDS

        def health(state, reason):
            self.loop_lag["supervisor"] = {"state": state, "reason": reason, "since": time.time()}

        async def reap():
            watchdog = getattr(self, "diagnostic_watchdog", None)
            if watchdog is not None:
                self.loop_lag.update(await watchdog.close())
                self.diagnostic_watchdog = None
                self.loop_lag["watchdog_pid"] = None

        try:
            while not self.stop.is_set():
                began = time.monotonic()
                try:
                    watchdog = getattr(self, "diagnostic_watchdog", None)
                    if watchdog is not None and watchdog.closing:
                        health("restarting", "reaping previous watchdog helper")
                        await reap()
                    health("running", "heartbeat and helper supervision")
                    await self.measure_loop_lag()
                    reason = "heartbeat supervisor returned while service remained open"
                except asyncio.CancelledError:
                    if self.stop.is_set():
                        break
                    asyncio.current_task().uncancel()
                    reason = "heartbeat supervisor cancelled while service remained open"
                except Exception as exc:
                    reason = type(exc).__name__ + ": " + str(exc)[:512]
                if self.stop.is_set():
                    break
                if time.monotonic() - began >= RETRY_MAX_SECONDS:
                    delay = CLOSE_SECONDS
                watchdog = getattr(self, "diagnostic_watchdog", None)
                health("restarting", reason)
                self.loop_lag.update(diagnostic_error=reason, diagnostic_last_error=reason,
                    watchdog_retry_at=time.time() + delay,
                    watchdog_unreaped=watchdog.process.pid if watchdog and watchdog.process else None)
                self.store.event("watchdog/supervisor_exited", None, {
                    "reason": reason, "retry_seconds": delay,
                    "unreaped_helper": self.loop_lag["watchdog_unreaped"]})
                await asyncio.sleep(delay)
                delay = min(RETRY_MAX_SECONDS, delay * 2)
        finally:
            async def retire():
                nonlocal delay
                # An unreaped child can still own a pending signal. Never
                # restore/reuse the handler or abandon ownership on reap failure.
                while getattr(self, "diagnostic_watchdog", None) is not None:
                    try:
                        await reap()
                    except Exception as exc:
                        reason = type(exc).__name__ + ": " + str(exc)[:512]
                        health("restarting", "termination waiting for helper reap: " + reason)
                        self.store.event("watchdog/reap_failed", None, {"reason": reason, "retry_seconds": delay})
                        await asyncio.sleep(delay)
                        delay = min(RETRY_MAX_SECONDS, delay * 2)
                health("stopped" if self.stop.is_set() else "restarting",
                       "daemon irreversibly terminating" if self.stop.is_set() else "supervisor task ending")
            await finish(retire())

    async def measure_loop_lag(self):
        from .watchdog import Watchdog, THRESHOLD, INTERVAL, SEGMENT_BYTES, CLOSE_SECONDS, RETRY_MAX_SECONDS
        watchdog = getattr(self, "diagnostic_watchdog", None)
        retry_at, delay, started = 0.0, CLOSE_SECONDS, 0.0
        reported_dumps = reported_failures = 0
        restarts = self.loop_lag.get("watchdog_restarts", -1) + 1
        self.loop_lag.update(traceback_file=str(self.store.root / "loop-stalls.log"), threshold_seconds=THRESHOLD,
                             diagnostic_bytes_limit=2 * SEGMENT_BYTES)
        async def retire():
            nonlocal watchdog
            outcome = await watchdog.close()
            self.loop_lag.update(outcome)
            if outcome["helper_forced"]:
                self.store.event("watchdog/output_possibly_lost", None, outcome)
            watchdog = self.diagnostic_watchdog = None
        def retry(error):
            nonlocal retry_at, delay
            retry_at = time.monotonic() + delay
            self.loop_lag.update(diagnostic_error=error, diagnostic_last_error=error,
                                 watchdog_pid=None, watchdog_retry_at=time.time() + delay)
            delay = min(RETRY_MAX_SECONDS, delay * 2)
        try:
            while not self.stop.is_set():
                if watchdog is None and time.monotonic() >= retry_at:
                    try:
                        candidate = Watchdog(self.store.root, self.store.generation)
                        await candidate.start()
                    except Exception as exc:
                        error = type(exc).__name__ + ": " + str(exc)[:512]
                        retry(error)
                        self.store.event("watchdog/start_failed", None, {"error": error, "retry_at": self.loop_lag["watchdog_retry_at"]})
                    else:
                        watchdog, started = candidate, time.monotonic()
                        self.diagnostic_watchdog = watchdog
                        reported_dumps = reported_failures = 0
                        self.loop_lag.update(watchdog_pid=watchdog.process.pid, watchdog_restarts=restarts,
                                             watchdog_retry_at=None, diagnostic_error=None, watchdog_unreaped=None)
                        if restarts:
                            self.store.event("watchdog/restarted", None, {"pid": watchdog.process.pid, "restarts": restarts})
                        restarts += 1
                sample = watchdog.beat() if watchdog else {"wall": time.time(), "monotonic": time.monotonic(),
                                                           "generation": self.store.generation}
                await asyncio.sleep(INTERVAL)
                lag = max(0.0, time.monotonic() - sample["monotonic"] - INTERVAL)
                self.loop_lag.update(last_seconds=lag, max_seconds=max(lag, self.loop_lag["max_seconds"]),
                                     samples=self.loop_lag["samples"] + 1, heartbeat=sample)
                if watchdog is None:
                    continue
                status = watchdog.status()
                self.loop_lag.update(status)
                if status["lookup_failures"] != reported_failures:
                    self.store.event("watchdog/lookup_failed", None, status)
                    reported_failures = status["lookup_failures"]
                if watchdog.dumps != reported_dumps:
                    self.store.event("watchdog/dump_possibly_truncated", None, watchdog.status())
                    reported_dumps = watchdog.dumps
                if hasattr(watchdog.process, "end") and processes.exit_evidence(watchdog.process.family["root"]):
                    # The supervisor is this helper's parent. Its handle has no
                    # child watcher to refresh returncode in this worker. Ask
                    # for the real outcome after kernel exit evidence, leaving
                    # a healthy idle helper free of periodic owner RPCs.
                    await watchdog.process.poll()
                if watchdog.process.returncode is not None:
                    error = "watchdog exited: " + str(watchdog.process.returncode)
                    self.store.event("watchdog/exited", None, {"pid": watchdog.process.pid,
                                     "returncode": watchdog.process.returncode, "output_integrity": "possibly_lost"})
                    await retire()  # Reaped before restoring SIGUSR2 or creating another pipe.
                    retry(error)
                elif time.monotonic() - started >= RETRY_MAX_SECONDS:
                    delay = CLOSE_SECONDS
        finally:
            async def close_watchdog():
                if watchdog is not None:
                    await retire()
            # serve() can cancel this task after closing has already begun.
            # It must still wait for the owned helper and retire its signal FD.
            from .locks import finish
            await finish(close_watchdog())

    async def native_worker(self, initial, queue):
        while True:
            sequence, message = await queue.get()
            listener = self.store.get(initial["id"])
            self.native_applying[listener["id"]] = {"sequence": sequence, "method": message.get("method")}
            try:
                await self.native_turn()
                self.check_native_stop()
                if "id" in message:
                    # The worker cannot await a tool that may itself need notifications.
                    # Intake already durably recorded ownership with the journal row.
                    # Never overwrite a request's ownership/state during dequeue.
                    obligation = self.store.get("native_obligation:" + str(sequence))
                    task = self.background(self.apply_native_request(listener, message, obligation))
                    pending = self.listener_requests.setdefault(listener["id"], set())
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                else:
                    trace = self.completion_trace(listener, message)
                    if trace:
                        trace.add("journal_queue", trace.marks.get("journaled", trace.started), time.monotonic())
                        trace.marks["application"] = time.monotonic()
                    with telemetry.using([trace]), telemetry.span("application"):
                        await self.notification(listener, message["method"], message.get("params", {}))
                self.check_native_stop()
                key = "native_applied:" + listener["id"]
                applied = self.store.get(key, required=False) or {}
                # Failures freeze the contiguous applied prefix. Recovery replays the
                # unapplied evidence before it can establish a newer history anchor.
                if not applied.get("failed") and not applied.get("pending"):
                    self.applied_native(listener["id"], sequence)
                    params = message.get("params", {})
                    if params.get("threadId") and message.get("method") in ("item/completed", "turn/completed", "thread/reverted"):
                        self.history.schedule(listener, params["threadId"])
                if isinstance(queue, NativeBacklog):
                    queue.applied += 1
                    queue.applied_bytes += queue.active_bytes
            except Exception as exc:
                self.check_native_resource(listener["id"], exc, "native application")
                code = getattr(exc, "code", type(exc).__name__)
                key = "native_applied:" + listener["id"]
                applied = self.store.get(key, required=False) or {}
                self.native_progress.pop(listener["id"], None)
                progress = {"sequence": applied.get("sequence", 0), "failed": sequence}
                if applied.get("pending"):
                    progress["pending"] = applied["pending"]
                self.store.put("native_applied", key, progress, listener["id"])
                self.store.event("native/reconcile_failure", listener["id"], {"code": code})
                from .ledger import UNREADABLE
                details = getattr(exc, "details", None) or {}
                keys = participants.checkout_ids(self.store, listener["unit"])
                # A path the failure names (a symlink, a special file, bytes changing while read) is that path's UNKNOWN:
                # checkout-wide only when nothing scopes it (2.4.0 recorded 11 '*' for one symlink in Sales_Profiler).
                # Only an UNREADABLE code's path: a PATH_ENCODING detail can hold a name sqlite cannot store.
                path = details.get("path") if code in UNREADABLE and isinstance(details.get("path"), str) else None
                where = details.get("checkout")  # the ledger names the checkout it read (ledger.read_path, normalize)
                if path and where in keys:
                    keys = [where]
                elif path and (where is not None or len(keys) != 1):
                    path = None  # no participant of the unit named: a path in one of several checkouts scopes none of them
                for key in keys:
                    self.store.unknown(key, path or "*", "native evidence processing failed",
                                       {"listener": listener["id"], "code": code})
            finally:
                self.native_applying.pop(listener["id"], None)
                queue.task_done()

    async def apply_native_request(self, listener, message, obligation):
        try:
            await self.native_request(listener, message)
            self.check_native_stop()
        except asyncio.CancelledError:
            # Receipt is durable, application is not. The next generation
            # handles the pending request exactly as after a crash.
            raise
        except BaseException as exc:
            self.check_native_resource(listener["id"], exc, "native request")
            obligation.update(state="failed")
            self.store.save(obligation)
            raise
        else:
            obligation.update(state="completed")
            self.store.save(obligation)

    async def native_request(self, listener, message):
        params = message.get("params", {})
        if message["method"] == "item/tool/call":
            ident = "dynamic:" + listener["id"] + ":" + params["threadId"] + ":" + params["callId"]
            async with self.request_locks.hold(ident):
                await self._native_request(listener, message)
            return
        await self._native_request(listener, message)

    async def _native_request(self, listener, message):
        self.check_native_stop()
        connection = self.connections.get(listener["id"])
        if not connection:
            raise native.Retired()
        connection.check_intake()
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
            self.complete_dynamic_obligations(ident)
            await connection.send({"id": message["id"], "result": existing["response"]})
            return
        if existing and existing.get("stage") != "preparing":
            # A completed effect may precede its durable response. Never execute
            # the tool again merely because the native server repeats the request.
            response = tools.result({"error": {"code": "DYNAMIC_UNCERTAIN", "message":
                                    "Previous tool invocation has uncertain completion; inspect durable status before a new request."}}, False)
            existing.update(state="uncertain", response=response)
            self.store.save(existing)
            self.complete_dynamic_obligations(ident)
            self.store.event("dynamic/replay_refused", ident, {"generation": self.store.generation})
            await connection.send({"id": message["id"], "result": response})
            return
        actor = await self.resolve_actor(listener, params["threadId"])
        self.check_native_stop()
        self.store.put("dynamic", ident, {"actor": actor["id"], "native": params, "state": "pending",
                                         "stage": "preparing", "generation": self.store.generation}, actor["unit"])
        def effect():
            self.check_native_stop()
            invocation = self.store.get(ident)
            invocation.update(stage="effect")
            self.store.save(invocation)
        try:
            response = tools.result(await tools.dispatch(self, actor, params["tool"], params["arguments"], effect=effect))
        except CoordinatorError as exc:
            response = tools.result({"error": exc.envelope()}, False)
        self.check_native_stop()
        self.store.put("dynamic", ident, {"actor": actor["id"], "state": "completed", "response": response})
        self.complete_dynamic_obligations(ident)
        await connection.send({"id": message["id"], "result": response})

    def complete_dynamic_obligations(self, ident):
        for obligation in list(self.store.selected("SELECT * FROM records WHERE kind='native_obligation' "
                "AND json_extract(data,'$.dynamic')=? AND json_extract(data,'$.state')='pending'", (ident,))):
            obligation.update(state="completed", replay="response")
            self.store.save(obligation)

    async def notification(self, listener, method, params):
        if method == "mcpServer/startupStatus":
            snapshot = await self.leases.call("snapshot", payload={"listener": listener["id"]})
            listener["owner_sequence"] = snapshot["family"]["owner_sequence"]
            self.store.save(listener)
            return
        if method == "thread/reverted":
            actor = await self.resolve_actor(listener, params["threadId"])
            self.history.invalidate(actor, "thread_reverted")
            return
        if method == "coordinator/transportGap":
            if listener["state"] not in ("stopping", "stopped"):
                listener.update(state="disconnected", recovery_state="reconciling")
                self.store.save(listener)
                for key in participants.checkout_ids(self.store, listener["unit"]):
                    self.store.unknown(key, "*", "native notification gap", {"listener": listener["id"]})
                if self.draining:
                    # A replacement started now could outlive the stop, which lists the app-servers it ends. Left failed,
                    # as a reconcile that could not run: the next daemon's recovery reconciles it, or, if this stop is
                    # refused, this one's maintenance (readmit).
                    listener.update(recovery_state="failed", recovery_error="DAEMON_DRAINING", retry_at=time.time())
                    self.store.save(listener)
                    return
                from .recovery import reconcile_listener
                task = self.background(reconcile_listener(self, listener))
                self.gap_reconciles.add(task)
                task.add_done_callback(self.gap_reconciles.discard)
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
            run = self.store.get(actor["run"])
            if run.get("cancel_requested"):
                # Started after its run's cancel (its turn/start was in flight, or a queued message started it).
                self.background(self.runs.interrupt_turn(actor, turn, run.get("cancel_generation", 0)))
        elif method == "turn/completed":
            actor.update(active_turn=None, state=params["turn"]["status"])
            self.store.save(actor)
            run = self.store.get(actor["run"])
            if actor["kind"] == "root" and not run.get("workflow") and not run.get("cancel_requested"):
                # A requested cancel owns the run's state: cancelling until it settles, then cancelled.
                run["state"] = params["turn"]["status"]
                self.store.save(run)
            # Native writes a direct call's receipts, and those of each nested call its cell awaited, before the turn
            # can end, on the thread that ran the call: they are applied before turn end is judged.
            await self.settle_native_log(listener["id"])
            ended_early = params["turn"]["status"] != "completed" or not listener.get("log")
            for operation in list(self.store.live_operations(actor["checkout"])):
                if (operation["actor"] != actor["id"] or operation["kind"] != "patch" or operation["native"].get("turn") != turn
                        or operation["state"] not in ("allowed", "running")):
                    # Native can start the thread's next turn before this end is read, and that turn's calls reach
                    # their hooks first: only this turn's are judged. One still prepared is its Pre hook's to decide.
                    continue
                await self.maybe_verify(operation)
                operation = self.store.get(operation["id"])
                if operation["state"] not in ("allowed", "running"):
                    continue
                if not handler_ended(operation):
                    if ended_early:
                        # An interrupted or failed turn, or an app-server that writes no receipts: no receipt will
                        # tell when a write it began finishes, so its paths stay held while its app-server runs.
                        await self.ledger.quarantine(operation, "turn ended without complete patch lifecycle evidence", executing=True)
                    # Otherwise its dispatch is still running (a cell can outlive its turn): its own receipts settle it.
                elif not listener.get("log") or call_source(operation) == "direct":
                    # A direct call's dispatch, its Post hook included, ended before its turn could, and native sends
                    # a thread's notifications in order: what it sent for the call came before this turn's end.
                    await self.ledger.quarantine(operation, "turn ended without complete patch lifecycle evidence")
                # Otherwise a cell's call, whose Post hook can end after its turn, also an interrupted one (the turn's
                # status does not say whether its cells were terminated): its outcome, or its app-server's exit,
                # settles it. Its handler is done, so nothing more is written for it.
            await self.settle_actor(actor)
            await finish_all(*(self.ledger.reconcile(self.store.get(key), reason="turn-end observation",
                owner={"kind": "turn", "actor": actor["id"], "turn": turn}) for key in participants.checkout_ids(self.store, actor["unit"])))
            if run.get("workflow"):
                self.background(self.writer.advance(run["id"]))
        elif method == "hook/completed":
            operation = self.record_hook_evidence(listener, params)
            if operation:
                await self.maybe_verify(operation)
            run = params["run"]
            if run.get("eventName") == "preToolUse" and run.get("status") == "blocked":
                # Native refused the call at a Pre hook, ours or another: it never runs (measured on
                # 0.163: no item follows a blocked Pre), so nothing admitted for it may stay held.
                blocked = self.find_operation(listener, thread, turn, run["id"].rsplit(":", 1)[-1])
                if blocked and blocked["state"] in ("prepared", "allowed") and not blocked.get("started_item"):
                    await self.withdraw(blocked["id"], blocked.get("custody"), "NATIVE_BLOCKED")
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
                if operation and operation.get("decision") == "allow" and operation["state"] in ("denied", "reconciled"):
                    # Admitted, then settled on native's own end of it: no effect, or a reconciled command. Its
                    # items travel apart from a failed result and can arrive after the settlement: they complete
                    # its record, admitted, never unmediated, and never reopen it. A patch settled as no effect
                    # that native then reports applied gets a separate conflict
                    # obligation; its old terminal outcome is not rewritten.
                    key = "started_item" if method == "item/started" else "completed_item"
                    if key == "completed_item" or not operation.get(key):
                        operation[key] = item
                        self.store.save(operation)
                    self.store.event("native/late_item", operation["id"], {"method": method, "status": item.get("status")})
                    if (kind == "fileChange" and operation["state"] == "denied" and method == "item/completed"
                            and item.get("status") == "completed"):
                        with self.store.transaction():
                            for record in [operation] + [self.store.get(part) for part in operation.get("parts", [])]:
                                record.pop("no_effect", None)
                                self.store.save(record)
                        self.ledger.conflicting_evidence(operation, "native reported a patch applied after reporting it failed", item)
                    return
                # A denied call (refused or withdrawn) that native runs anyway is unmediated.
                if not operation or not operation.get("hook_valid") or operation["state"] == "denied":
                    if operation and operation["state"] == "denied":
                        operation.update(state="quarantined", attribution="UNKNOWN")
                    if not (operation and operation.get("unmediated")):  # once per item, not per notification
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
                    operation["unmediated"] = True
                    if not operation.get("custody") or self.store.get(operation["custody"])["state"] == "released":
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
                if method == "item/completed":
                    self.ledger.end_execution(operation)  # native completes the item after the handler's writes
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
        operation = self.store.get(operation["id"])
        if operation["state"] not in ("prepared", "allowed", "running"):
            return
        if (operation["kind"] == "patch" and operation.get("completed_item")
                and operation["completed_item"].get("status") in ("failed", "declined")):
            # Native ended the patch without success and runs no Post hook for it.
            await self.ledger.settle_unapplied(operation, "NATIVE_PATCH_" + operation["completed_item"]["status"].upper(),
                                               lambda o: (o.get("completed_item") or {}).get("status") in ("failed", "declined"))
        elif (operation["kind"] == "patch" and operation.get("completed_item") and
                (operation.get("post_hook") or operation["completed_item"].get("status") != "completed")):
            try:
                await self.ledger.complete_patch(operation, operation["completed_item"])
            except Exception:
                if self.store.get(operation["id"])["state"] != "quarantined":
                    raise
                # Recorded: its quarantine made each of its paths UNKNOWN, and the notification goes on (2.4.0-2.4.3
                # failed it, recording a checkout-wide UNKNOWN too and skipping the rest of a turn's end).
        elif (operation["kind"] == "patch" and operation.get("completed_item")
                and any(run.get("eventName") == "postToolUse" for run in operation.get("hook_evidence", []))):
            # Native reports its Post hook ended without success: its lifecycle can never complete.
            await self.ledger.quarantine(operation, "PATCH_EVIDENCE: its Post hook did not complete")
        elif operation["kind"] in ("command", "mcp") and operation.get("completed_item") and operation.get("post_hook"):
            await self.settle_actor(self.store.get(operation["actor"]))

    async def observe_processes(self, actor, reservation, resolve_quarantine=False, terminals=None, retry_helpers=True):
        listener = self.store.get(actor["listener"])
        try:
            operation = self.store.get(reservation["operation"])
            if terminals is None:
                response = await self.listener_connection(listener).call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
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
                    root = await telemetry.to_thread(processes.terminal_root, listener, intent, terminal, list(observed.values()))
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
            found = await telemetry.to_thread(processes.inventory, listener, actor["session"],
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
        """Release the custody of each of the actor's commands that has ended. Returns the custody groups still held
        only for native's end of the call: no process or terminal of it remains, and its result, Post hook or
        completed item is still to be read."""
        traces = [trace for trace in self.settlements.values() if trace.actor == actor["id"]]
        with telemetry.using(traces):
            began = time.monotonic()
            async with self.actor_locks.hold("settle:" + actor["id"]):
                for trace in traces:
                    trace.add("actor_lock_wait", began, time.monotonic())
                return await self._settle_actor(actor, resolve_quarantine)

    async def settle_before_claim(self, actor, listener, cutoff):
        """Settle the unit's commands before its patch claims paths. A command with no process or terminal left holds
        its custody until native's end of it is read: its result in the log, its Post hook, its completed item. Each
        can arrive after the next call's hook, and the unit's own patch was refused for it (CLAIM_CONFLICT, 10-21).
        Wait for them, until the hook's cutoff. A command still running is not waited for: its custody conflicts, as
        before."""
        while True:
            await self.settle_native_log(listener["id"])
            awaiting = []
            for holder in {r["actor"] for r in self.custody.unit_active(actor["unit"])}:
                try:
                    awaiting += await self.settle_actor(self.store.get(holder))
                except (CoordinatorError, OSError):
                    if holder == actor["id"]:
                        raise
                    # Another of the unit's actors (a sub-agent) not settled now: its custody stays held, and
                    # conflicts as before.
            if not awaiting or time.time() >= cutoff:
                return
            async with self.custody.condition:
                try:
                    await asyncio.wait_for(self.custody.condition.wait(), max(0.0, min(0.25, cutoff - time.time())))
                except asyncio.TimeoutError:
                    pass

    async def _settle_actor(self, actor, resolve_quarantine):
        self.check_native_stop()
        if not actor.get("listener"):
            return []
        listener = self.store.get(actor["listener"])
        connection = self.connections.get(listener["id"])
        if connection:
            connection.check_intake()
        if not connection or connection.closed:
            return []
        with telemetry.span("terminal_inventory"):
            terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
        if terminals.get("nextCursor"):
            raise CoordinatorError("TERMINAL_PAGINATION", "Native terminal inventory is incomplete")
        settled_groups, awaiting = set(), []
        for reservation in self.custody.unit_active(actor["unit"]):
            if reservation["actor"] != actor["id"]:
                continue
            group = reservation.get("group", reservation["id"])
            if group in settled_groups:
                continue
            settled_groups.add(group)
            reservation = self.store.get(group)
            members = reservation.get("members", [reservation["id"]])
            with telemetry.span("process_inspection"):
                await self.observe_processes(actor, reservation, resolve_quarantine=resolve_quarantine, terminals=terminals["data"])
            reservation = self.store.get(reservation["id"])
            operation = self.store.get(reservation.get("operation"), required=False)
            if not operation:
                continue  # no inference that an interrupted hook intent had no effects
            own_terminals = [t for t in terminals["data"] if t.get("itemId") == operation["native"].get("call") or
                             t.get("processId") in reservation["terminals"]]
            # Native's own end of the call: its completed item, or a failed result it wrote for it, which
            # 0.163 follows with no item when its handler refused the call. Either can leave a process behind.
            # So can the end of a local command's dispatch that native aborted (no result): on macOS 0.163
            # spawns the process synchronously inside the handler, which it drops only at an await, so any
            # process the call started already exists and is held by lineage. An MCP server can still be
            # running a call native stopped waiting for.
            ended = (operation.get("completed_item") or returned_failure(operation) or
                     operation["kind"] == "command" and operation.get("result_ready") and not operation.get("returned"))
            if resolve_quarantine and not reservation["processes"] and not own_terminals and not ended:
                metadata = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
                if metadata["thread"].get("status", {}).get("type") in ("idle", "notLoaded") and not actor.get("active_turn"):
                    operation["completed_item"] = {"exitCode": None, "observedGone": True, "missingNativeCompletion": True}
                    self.store.save(operation)
                    ended = True
                    for key in members:
                        self.store.unknown(self.store.get(key)["checkout"], "*", "lead reconciled absence of survivors without native completion", {"custody": key})
            if reservation["state"] == "quarantined" or reservation["processes"] or own_terminals:
                continue
            if not ended:
                # Nothing of it runs, and native's end of it is still to be read: its result, its Post hook or its
                # completed item. A dispatch that ended without a result shows nothing more.
                if handler_ended(operation) or not operation.get("result_ready"):
                    awaiting.append(reservation["id"])
                continue
            trace = self.settlements.get(operation["id"])
            if trace:
                trace.marks.setdefault("eligible", time.monotonic())
            def release(operation_id=operation["id"], member_ids=members, trace=trace):
                # Runs in the observation transaction: bytes, acknowledgements
                # and custody release cannot be separated by a daemon crash.
                with telemetry.span("custody_release"):
                    current = self.store.get(operation_id)
                    current.update(state="reconciled", attribution="UNKNOWN-context-only")
                    self.store.save(current)
                    for key in member_ids:
                        member = self.store.get(key)
                        if member["state"] != "released":
                            self.custody.release_held(member)
                    if trace:
                        trace.marks["released"] = time.monotonic()
            applications = [{"caller": "_settle_actor", "checkout": self.store.get(self.store.get(key)["checkout"]), "paths": None} for key in members]
            await self.ledger.scans.observe(applications, "opaque command effects",
                action={"kind": "custody-release", "operation": operation["id"], "members": members}, on_observed=release)
            async with self.custody.condition:
                self.custody.condition.notify_all()
            trace = self.settlements.pop(operation["id"], None)
            if trace and trace.armed:
                self.store.event("operation/settlement", operation["id"], trace.result())
        return awaiting

    async def lead_edit(self, params, restore=False, mark=None):
        # `mark`: a restore's, carried by the claims it creates (Custody.acquire, recovery.release_taken).
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
        await self.custody.acquire(actor, [routing.qualified(key, p) for key, values in expected.items() for p in values], mark=mark)
        kind = "restore" if restore else "lead-edit"
        if len(expected) == 1:
            checkoutid, images = next(iter(expected.items()))
            operation = await self.ledger.prepare(dict(actor, checkout=checkoutid), kind, {"expected": images}, list(images))
        else:
            from .operations import prepare
            operation = await prepare(self.ledger, actor, kind, {"expected": expected}, expected)
        try:
            await finish_all(*(telemetry.to_thread(self.install_images, self.store.get(key), values) for key, values in expected.items()))
            if operation.get("parts"):
                from .operations import verify
                return await verify(self.ledger, operation, expected, "restore" if restore else "exact", {"executor": "coordinator"})
            return await self.ledger.verify(operation, images, "restore" if restore else "exact", {"executor": "coordinator"})
        except BaseException as exc:
            from .locks import finish
            await finish(self.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__)))
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
            expected[path] = {"blob": await telemetry.to_thread(self.store.blob, content) if content is not None else None, "mode": mode}
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
            and params.get("payload", {}).get("hook_event_name") in ("PostToolUse", "PostToolUseFailure", "PostToolBatch"))
        if method != "daemon/stop" and (self.closing or self.draining and method not in READ_RPC and not completion):
            raise CoordinatorError("DAEMON_DRAINING", "Coordinator is draining; retry on the serving daemon",
                                   {"retryable": True, "pid": os.getpid(), "boot": self.boot}, True)

    def awaiting_outcome(self, operation):
        """A patch whose handler and dispatch both ended, waiting only for the outcome native sent before its
        dispatch's end, or never sends (a Post hook it cancelled): nothing more is written for it, and stopping its
        app-server settles it (stop_listener), so a stop need not wait for it."""
        if operation.get("parent_operation"):
            operation = self.store.get(operation["parent_operation"])
        return (operation["kind"] == "patch" and operation["state"] in ("allowed", "running")
                and bool(operation.get("result_ready")) and handler_ended(operation))

    def settling_on_outcomes(self, run):
        """A workflow whose turn ended and whose settlement waits only on patches awaiting their outcome: the stop
        settles those, and the next daemon judges the workflow again. Workflows do not advance while draining."""
        if run["state"] != "settling":
            return False
        waiting = list(self.store.selected("SELECT * FROM records WHERE kind='operation' AND parent=? AND json_extract(data,'$.state') "
                                           "NOT IN ('verified','reconciled','resolved','denied')", (run["unit"],)))
        return bool(waiting) and all(self.awaiting_outcome(o) for o in waiting)

    def stop_pending(self, caller_commands=()):
        from .writer import TERMINAL
        now = time.time()
        hooks = []
        for record in self.store.all("lead_hook"):
            if record.get("stop_control") and record.get("shell_command_digest") in caller_commands:
                continue  # only the connected caller's pure stop command, never its other shell work
            if record.get("execution"):
                if record["execution"]["phase"] in ("admitting", "open", "settling"):
                    hooks.append(record)
                continue
            if record["state"] not in ("received", "prepared", "observed"):
                continue
            # Recovery imports unfinished file calls. A legacy record it cannot
            # interpret remains conservative; only Bash has a host time limit.
            if record.get("tool") != "Bash" or now <= record.get("deadline", float("inf")):
                hooks.append(record)
        operations = [o for o in self.store.selected("SELECT * FROM records INDEXED BY window_operation WHERE " + WINDOW_ACTIVE["operation"])
                      if not self.awaiting_outcome(o)]
        lead_operations = [o for o in operations if o["kind"] in ("lead-edit", "restore", "lead-command")]
        methods = [method for task, method in self.inflight.items() if task is not asyncio.current_task()]
        from .executors import pending as pending_executors
        counts = {
            # Includes the dequeued item until its finally calls task_done().
            # Requests leave that queue before their separate handler finishes.
            "native_applications": sum(queue._unfinished_tasks for queue in self.queues.values()),
            # Includes a decoded capacity waiter and the staged uncommitted batch.
            "native_receipts": sum(connection.intake.received - connection.intake.committed
                                   for connection in self.connections.values() if connection.intake is not None),
            "native_requests": sum(not task.done() for tasks in self.listener_requests.values() for task in tasks),
            "recovery": int(not self.ready) + len(self.recovering),  # writers, units, checkouts startup recovery is still reconciling
            "history": self.history.active + len(self.history.scheduled),
            "observations": len(self.ledger.scans.live) + sum(p.task is not None for p in self.ledger.scans.producers.values()),
            "preimage_readers": sum(len(readers) for readers in getattr(self, "admission_reads", {}).values()),
            # One waiting only for the lead's decision on an unproven input runs nothing (Runs.awaiting_lead).
            "workflows": sum(r["state"] not in TERMINAL | {"idle"} and not self.settling_on_outcomes(r) and not self.runs.awaiting_lead(r)
                             for r in self.store.all("run") if r.get("workflow") or r["state"] == "admitting"),
            "writers": sum(bool(a.get("active_turn")) for a in self.store.all("actor")),
            "custody": self.store.db.execute("SELECT count(*) FROM records INDEXED BY window_custody WHERE " + WINDOW_ACTIVE["custody"]).fetchone()[0],
            # A lead reads its mail through its inbox, so no native turn starts for it: unread lead mail is no
            # delivery in flight, and a stop never waits for it.
            "deliveries": sum(self.delivery_owed(m) for m in self.store.all("message")),
            "lead_hooks": len(hooks), "lead_operations": len(lead_operations),
            "external_editors": len(pending_executors(self.store)),
            "other_operations": len(operations) - len(lead_operations),
            "publication_rpcs": sum(m in ("unit/publish", "unit/recover") for m in methods),
            "mutating_rpcs": sum(m not in READ_RPC and m != "daemon/stop" for m in methods),
        }
        return {key: value for key, value in counts.items() if value}

    def pending_lead_edits(self):
        from .lead_calls import blockers
        from .executors import describe
        return blockers(self.store) + describe(self.store)

    async def drain(self, params):
        current = {"pid": os.getpid(), "boot": self.boot, "version": VERSION}
        if "expect" in params and params["expect"] != {"pid": current["pid"], "boot": self.boot}:
            raise CoordinatorError("DAEMON_REPLACED", "Stop belongs to a different daemon process", current)
        if self.draining:
            raise CoordinatorError("DAEMON_DRAINING", "Another stop is draining this daemon", {"retryable": True}, True)
        caller_commands = params.get("_caller_commands", ())
        pending = self.stop_pending(caller_commands)
        if pending:
            raise CoordinatorError("NOT_QUIESCENT", "Settle pending work before stopping",
                                   {"pending": pending, "listener_reconciles": list(self.listener_reconciles.values()),
                                    "native_applications": dict(self.native_applying),
                                    "lead_edits": self.pending_lead_edits()})
        self.draining = True
        try:
            deadline = time.monotonic() + STOP_DRAIN_S
            while True:
                pending = self.stop_pending(caller_commands)
                tasks = (self.rpc_tasks | set(self.inflight)) - {asyncio.current_task()}
                # An advance that began before the drain sees it at its next step and ends without a turn. A
                # re-admission or a transport gap's reconcile under way ends first: what it starts is a listener, which
                # the stop below then stops.
                if (not pending and not tasks and not self.writer.advancing and not self.readmitting
                        and not self.gap_reconciles and not self.listener_reconciles):
                    break
                if time.monotonic() >= deadline:
                    raise CoordinatorError("NOT_QUIESCENT", "RPC drain did not settle; admission resumed",
                                           {"pending": dict(pending, inflight_rpcs=len(tasks), workflow_advances=self.writer.advancing,
                                                            readmissions=len(self.readmitting), gap_reconciles=len(self.gap_reconciles),
                                                            listener_reconciles=len(self.listener_reconciles)),
                                            "listener_reconciles": list(self.listener_reconciles.values()),
                                            "lead_edits": self.pending_lead_edits(),
                                            "wait_seconds": STOP_DRAIN_S})
                await asyncio.sleep(0.01)
            # No pending completion remains. Fence even new observational RPCs
            # before detaching listeners or releasing leases/closing SQLite.
            self.closing = True
            for listener in list(self.store.all("listener")):
                if listener["state"] not in ("stopped", "dead", "dormant"):
                    await self.stop_listener(listener)
            for checkout in list(self.store.all("checkout")):
                await self.release_lease(checkout["id"], stopping=True)
            await self.leases.call("owner/stop", payload={"inhibit": bool(params.get("park"))})
            self.stop.set()
            return {"stopping": True, "inhibited": bool(params.get("park"))}
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
            from .intents import FILES
            timed_admission = method == "lead-hook/dispatch" and params.get("payload", {}).get("tool_name") in FILES
            if method not in READ_RPC | COMPLETION_RPC | {"daemon/stop"} and not timed_admission:
                await self.leases.call("ping")  # detached leases cannot authorize new production before bridge refresh
            return await self.dispatch_admitted(method, params)
        finally:
            if owned:
                self.inflight.pop(task, None)

    async def dispatch_admitted(self, method, params):
        self.check_native_stop()
        if method == "daemon/status":
            result = self.status(params.get("checkout"), bool(params.get("brief")))
            result["supervision"] = await self.leases.call("owner/status")
            return result
        if method == "checkout/query":
            from .observations import query
            return query(self, params["cwd"], params.get("session_id", ""), params.get("git_common"))
        if method == "daemon/stop":
            return await self.drain(params)
        if method == "lead-hook/batch":
            from .lead_batches import submit
            from .locks import finish
            return await finish(submit(self, params.get("calls")))
        if method == "lead-hook/dispatch":
            from .intents import FILES
            if params["payload"].get("tool_name") in FILES:
                from . import admissions
                return await admissions.dispatch(self, params["payload"])
        if not self.ready:
            raise CoordinatorError("RECOVERING", "Coordinator is reconciling durable state before admitting work", operational=True)
        if self.recovering:
            from .recovery import recovered
            await recovered(self, method, params)  # a call for a writer, unit or checkout still reconciling waits
        if method == "project/list":
            return await self.projects.listing(params)
        if method == "project/resolve":
            return await self.projects.resolve(params)
        if method == "project/register":
            return await self.projects.register(params)
        if method == "project/status":
            return self.projects.status(params)
        if method == "services/declare":
            return self.broker.declare(self.store.get(params["unit"]), params["spec"])
        if method == "services/status":
            return self.broker.status()
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
            if "run" not in params:
                return self.status(params.get("checkout"))
            run = self.store.get(params["run"])
            # A finished run whose writer runs a message's turn reads running, with that turn (Runs.reported).
            reported = dict(zip(("state", "turns"), self.runs.reported(run))) if run.get("_kind") == "run" else {}
            return dict(run, **reported, **participants.surface(self, run["unit"]))
        if method == "run/list":
            return self.runs.listing(params["cwd"], params.get("limit", 50))
        if method == "run/result":
            self.runs.get(params["run"], params.get("cwd"))
            return self.writer.result(params["run"], params.get("cursor", 0), params.get("limit", 50000))
        if method == "run/events":
            return self.runs.events(self.runs.get(params["run"], params.get("cwd")), params.get("cursor", 0), params.get("limit", 100))
        if method in ("run/interrupt", "run/resume"):
            run = self.runs.get(params["run"], params.get("cwd"))
            if method == "run/interrupt":
                # A plain run is cancelled as a workflow is: its turns, its sub-agents' turns and its queued work, and
                # its calls are refused until a resume. A turn interrupt alone left a start in flight to run its turn.
                if run.get("mode") == "lead":
                    raise CoordinatorError("RUN_LEAD_MODE", "A lead-mode run has no writer turns to cancel")
                return await self.runs.cancel(run)
            # A resume reconciles the writer's app-server first, replacing one that is gone or not connected.
            return await self.runs.resume(run, text=params.get("text", ""), deadline=params.get("deadline"), images=params.get("images"))
        if method in ("run/terminals", "run/terminate-terminal"):
            actor = self.store.get(self.store.get(params["run"])["root_actor"])
            listener = self.store.get(actor["listener"])
            self.check_native_stop()
            if listener["state"] == "dormant":
                if method == "run/terminate-terminal":
                    raise CoordinatorError("NO_SUCH_TERMINAL", "A dormant writer has no running terminal")
                return {"data": [], "nextCursor": None}
            connection = self.connections.get(actor["listener"])
            if not connection or connection.closed:
                raise CoordinatorError("LISTENER_UNAVAILABLE", "Terminal inventory needs a connected writer", {"retryable": True}, True)
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
                return await self.hook(params["payload"], params.get("process"), params.get("deadline"))
            except CoordinatorError as exc:
                self.store.event("hook/denied", None, {"code": exc.code, "details": exc.details})
                return deny(str(exc) + " " + encoded(exc.details))
        if method == "lead-hook/dispatch":
            from .intents import dispatch
            from .locks import finish
            payload = params["payload"]
            async with self.actor_locks.hold("host:" + digest([payload.get("session_id"), payload.get("tool_use_id")])):
                return await finish(dispatch(self, payload))
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
            from . import executors
            owner = await executors.begin(self, params.get("executor"), actor, {key: list(values) for key, values in groups.items()})
            try:
                expected = {key: await self.expected_images(self.store.get(key), values) for key, values in groups.items()}
                await self.custody.acquire(actor, [routing.qualified(key, p) for key, values in expected.items() for p in values],
                                           executor=owner["id"])
                if len(expected) == 1:
                    key, images = next(iter(expected.items()))
                    operation = await self.ledger.prepare(dict(actor, checkout=key), "lead-edit", {"expected": images}, list(images),
                                                          native={"executor": owner["id"]})
                else:
                    from .operations import prepare
                    operation = await prepare(self.ledger, actor, "lead-edit", {"expected": expected}, expected,
                                              identity={"executor": owner["id"]})
                executors.bind(self.store, owner, operation)
                return self.store.get(operation["id"])
            except BaseException:
                executors.finish(self, owner, "preparation refused before execution")
                raise
        if method == "operation/complete":
            operation = self.store.get(params["operation"])
            if operation.get("executor"):
                from .executors import complete
                return await complete(self, operation)
            if operation["kind"] != "lead-edit" or operation["state"] != "prepared":
                raise CoordinatorError("OPERATION_STATE", "Only a prepared lead edit can complete through RPC")
            try:
                if operation.get("parts"):
                    from .operations import verify
                    result = await verify(self.ledger, operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
                else:
                    result = await self.ledger.verify(operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
                if operation.get("executor"):
                    from . import executors
                    executors.finish(self, self.store.get(operation["executor"]), "explicit operation/complete")
                return result
            except BaseException as exc:
                from .locks import finish
                await finish(self.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__)))
                raise
        if method == "lead-command":
            from .recovery import lead_command
            return await lead_command(self, params)
        if method == "unknown/list":
            return {"data": self.store.unresolved(params["checkout"], params.get("paths"))}
        if method == "unknown/restore":
            from .recovery import restore_unknown
            return await restore_unknown(self, params)
        if method == "unknown/narrow":
            from .recovery import narrow_unknown
            return await narrow_unknown(self, params)
        if method == "lead/mailbox":
            from .lead import mailbox
            return {"mailbox": mailbox(self.store, params["session_id"], params["unit"])}
        if method == "message/send":
            return await self.messages.send(params.get("sender", "lead"), params["recipient"], params["text"], params["wake"],
                                            params.get("cwd", ""))
        if method == "message/inbox":
            return self.messages.inbox(params.get("recipient", "lead"), params.get("cursor", ""), params.get("limit", 100),
                                       params.get("session_id", ""), params.get("cwd", ""))
        if method == "message/wake":
            message = self.store.get(params["message"])
            if message["delivery"] == "unproven":
                # Its turn may have run (runs.apply_message): sending it again is the lead's explicit retry.
                raise CoordinatorError("MESSAGE_WAKE", "This delivery may have run: message/retry sends it again, which may "
                                       "deliver it twice", {"delivery": "unproven", "unproven": message.get("unproven")})
            if message["delivery"] != "pending" or params["wake"] not in ("queue", "start"):
                raise CoordinatorError("MESSAGE_WAKE", "Only a pending message may be explicitly woken")
            message["wake"] = params["wake"]
            self.store.save(message)
            self.background(self.messages.deliver(message["id"]))
            return {"message": message["id"], "wake": message["wake"]}
        if method == "message/retry":
            return await self.messages.retry_requested(params["message"])
        if method == "message/ack":
            # A lead acknowledges as `lead`, its mailbox, its session or a lead actor; a writer only through its own
            # native call (tools.py), never by naming its actor here.
            actor = params.get("actor", "lead")
            if (actor != "lead" and not actor.startswith("lead_mailbox_")
                    and (self.store.get(actor, required=False) or {}).get("kind") != "lead"):
                raise CoordinatorError("NATIVE_BOUND", "Writer acknowledgements must originate from native actor metadata")
            return self.messages.ack(actor, params["message"], params.get("session_id", ""), params.get("cwd", ""))
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
        from .admissions import STARTED
        token = STARTED.set(None)
        task = asyncio.current_task()
        self.rpc_tasks.add(task)
        self.rpc_writers[task] = writer
        try:
            # Accept callbacks already queued when the listener closes must not
            # start work after the irreversible serve fence.
            if not self.stop.is_set():
                await self.rpc_request(reader, writer)
        finally:
            # Cancellation can arrive during framing, dispatch or reply drain.
            # Never leave a transport waiting for a client to read or send EOF.
            writer.transport.abort()
            self.inflight.pop(task, None)
            self.rpc_writers.pop(task, None)
            self.rpc_tasks.discard(task)
            STARTED.reset(token)

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
            # Admission/control RPCs must not queue behind filesystem workers.
            # Batch-item codec work remains off-loop in lead_batches.
            request = json.loads(raw)
            if set(request) - {"id", "method", "params", "key"}:
                raise CoordinatorError("RPC_FIELDS", "Unknown RPC envelope field")
            method, params, key = request["method"], request.get("params", {}), request.get("key")
            if method == "daemon/stop":
                commands = []
                try:
                    peer = writer.get_extra_info("socket")
                    current = processes.identity(peer.getsockopt(0, 2))
                    seen = set()
                    while current and current["pid"] not in seen and current["pid"] > 1:
                        seen.add(current["pid"])
                        argv = processes.arguments(current["pid"])
                        for index, arg in enumerate(argv[:-1]):
                            if arg in (b"-c", b"-lc"):
                                commands.append(digest(argv[index + 1].decode()))
                        current = processes.identity(current["ppid"])
                except (CoordinatorError, OSError, UnicodeError):
                    commands = []
                params = dict(params, _caller_commands=commands)
            payload = params.get("payload", {}) if method == "lead-hook/dispatch" else {}
            if payload.get("hook_event_name") == "PreToolUse":
                from .admissions import STARTED, peer_started
                STARTED.set(peer_started(writer))
            self.admit_rpc(method, params)
            # Includes requests queued on an idempotency lock, before dispatch.
            self.inflight[asyncio.current_task()] = method
            fingerprint = digest([method, params]) if key else None
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
                        if (answer.get("error") or {}).get("code") in ("WRITER_START_FAILED", "WRITER_START_PENDING", "RESOURCE_PAUSED", "RESOURCE_EXHAUSTED"):
                            # No input was submitted by this refusal. Its separate durable
                            # start operation remains joinable, including with the same key.
                            self.store.db.execute("DELETE FROM idempotency WHERE key=?", (key,))
                        else:
                            self.store.db.execute("UPDATE idempotency SET state='completed',response=? WHERE key=?", (encoded(answer), key))
            response = dict(answer, id=request.get("id"))
        except native.IntakeStopped as exc:
            # This is an operational stop, not a completed business outcome.
            # Leave a keyed request pending: a retry must reconcile its durable
            # effects, never replay it merely because this envelope was sent.
            error = CoordinatorError(exc.code, "Native intake stopped locally; retry on the serving daemon and reconcile pending work",
                                     dict(exc.details, retryable=True), operational=True)
            response = {"id": request.get("id"), "error": error.envelope()}
        except Exception as exc:
            error = exc if isinstance(exc, CoordinatorError) else CoordinatorError("OPERATIONAL_FAILURE", type(exc).__name__, operational=True)
            response = {"id": request.get("id"), "error": error.envelope()}
        try:
            writer.write((encoded(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            from .locks import wait_closed
            await wait_closed(writer)

    async def compact_history(self):
        """Before 2.4.1 an opaque operation copied every head into its record, and released custody kept the
        process table its command was checked against: every scan decoded both again. Neither is read once
        the record is finished (publish skips opaque bases), so keep a fingerprint of the bases and drop the table."""
        from .store import fingerprint
        def operation(record):
            if (record["kind"] in ("command", "mcp", "lead-command") and record.get("bases")
                    and record["state"] in ("reconciled", "denied", "resolved")):
                record.update(bases={}, heads=fingerprint(record["bases"].items()))
                return True
            return False
        def custody(record):
            return record["state"] == "released" and record.pop("process_baseline", None) is not None
        compacted = {}
        for kind, where, compact in (
                ("operation", "json_extract(data,'$.kind') IN ('command','mcp','lead-command') AND length(data)>65536", operation),
                ("custody", "json_extract(data,'$.state')='released' AND json_extract(data,'$.process_baseline') IS NOT NULL", custody)):
            candidates = [row["id"] for row in self.store.db.execute("SELECT id FROM records WHERE kind=? AND " + where, (kind,))]
            for start in range(0, len(candidates), 20):
                if self.stop.is_set() or self.draining:
                    return
                with self.store.transaction():
                    for ident in candidates[start:start + 20]:
                        record = self.store.get(ident)
                        if compact(record):
                            self.store.save(record)
                            compacted[kind] = compacted.get(kind, 0) + 1
                await asyncio.sleep(0)
        if compacted:
            self.store.event("ledger/compacted", None, compacted)

    async def maintenance(self):
        while not self.stop.is_set():
            await asyncio.sleep(1)
            if not self.ready or self.draining:
                continue
            try:
                try:
                    await self.leases.call("ping")
                except CoordinatorError as exc:
                    self.store.event("lease/redundancy_unavailable", None, exc.envelope())
                    continue
                # What startup recovery is still reconciling stays its own until it is done (recovery.recover_writers):
                # its writers' custody and retries, its runs' cancels and inputs, its checkouts' leases.
                recovering = self.recovering
                self.broker.maintain()
                held = list(self.store.held_custody())
                for actor in list(self.store.all("actor")):
                    if (actor.get("listener") and ("run", actor.get("run")) not in recovering
                            and any(c["actor"] == actor["id"] for c in held)):
                        try:
                            await self.settle_actor(actor)
                        except (CoordinatorError, OSError):
                            pass  # custody remains held; no optimistic release
                from .recovery import settle_lead_command, watch_answers, retry_due, startup_obligation
                for reservation in held:
                    if (reservation.get("lead_root") and ("checkout", reservation.get("checkout")) not in recovering
                            and self.store.get(reservation["id"])["state"] != "released"):
                        await settle_lead_command(self, reservation)
                watch_answers(self)
                # A writer whose reconcile failed holds its unit's work until it is reconciled again. Once its retry is
                # due, it is retried here in either case:
                # - a startup incident, which stays owed even for a finished run (only a retryable one; one that
                #   cannot pass stays quarantined for the lead);
                # - while work is owed to it (writer_owed), or when it was ended stopped or behind a dead connection
                #   (recovery.end_wedged: its replacement is due now).
                # Otherwise its next resume reconciles it. Its backoff is no work under way, so it holds no stop; an
                # attempt it starts enters reconcile_listener's stop fence like any other.
                for listener in list(self.store.selected("SELECT * FROM records WHERE kind='listener' AND "
                                                         "json_extract(data,'$.recovery_state')='failed'")):
                    if (retry_due(self, listener)
                            and ("run", listener["run"]) not in recovering
                            and self.store.get(listener["actor"]).get("listener") == listener["id"]
                            and (startup_obligation(self.store, listener["run"]) or listener.get("wedged")
                                 or self.writer_owed(listener["unit"]))):
                        self.readmit(listener["run"])
                await self.messages.retry()
                await self.ledger.scans.retry()
                # Only runs this loop can act on: every finished run is history. A plain run's prepared continuation
                # (transition_pending) is submitted again too: its writer was being replaced when run/resume prepared it.
                for run in list(self.store.selected("SELECT * FROM records WHERE kind='run' AND ((json_extract(data,'$.cancel_requested') "
                        "AND json_extract(data,'$.state')!='cancelled') OR json_extract(data,'$.state') IN ('transition_pending','submission_uncertain') OR "
                        "(json_extract(data,'$.workflow') IS NOT NULL AND "
                        "json_extract(data,'$.state') NOT IN ('completed','failed','interrupted','blocked','refused','cancelled'))) ORDER BY id")):
                    if ("run", run["id"]) in recovering:
                        continue
                    if run.get("cancel_requested") and run["state"] != "cancelled":
                        await self.runs.settle_cancel(run["id"])
                        continue
                    if self.runs.expired(run):
                        # This list was read before the awaits of the runs ahead of it, and a resume renews a deadline:
                        # the cancel checks it again on the run as it is when it begins.
                        self.background(self.runs.cancel(run, expired=True))
                        continue
                    # A workflow whose turn/start result was lost (submission_uncertain) is advanced too: the advance settles
                    # that input, reading native at most each 2 s (writer.py); one nothing shows waits for the lead's resume.
                    if run.get("workflow") and run["state"] in ("settling", "transition_pending", "submission_uncertain"):
                        try:
                            await self.writer.advance(run["id"])
                        except CoordinatorError as exc:
                            self.store.event("workflow/advance_refused", run["id"], exc.envelope())
                    elif not run.get("workflow") and run["state"] == "transition_pending":
                        self.writer.resubmit(run["id"])
                    elif not run.get("workflow") and run["state"] == "submission_uncertain":
                        try:
                            await self.writer.settle_uncertain(run["id"])
                        except CoordinatorError as exc:
                            self.store.event("run/input_settlement_refused", run["id"], exc.envelope())
                for checkout in list(self.store.all("checkout")):
                    if ("checkout", checkout["id"]) not in recovering:
                        await self.release_lease(checkout["id"])
            except native.Retired:
                if self.stop.is_set() or asyncio.current_task().cancelling():
                    raise
                self.store.event("maintenance/retry", None, {"reason": "native connection retired"})

    async def serve(self):
        from .locks import finish
        from .recovery import recover, recover_writers
        allocation = server = None
        from .stop_control import StopControl
        control = getattr(self, "stop_control", None)
        local_control = control is None
        if local_control:
            control = StopControl().__enter__()
        loop = asyncio.get_running_loop()
        def request_stop():
            self.stop.set()
            self.closing = True
            self.ready = False
        control.bind(loop, request_stop)
        try:
            if control.requested:
                return
            # Probe the actual owner, not an unrelated worker asyncio watcher.
            from .owned import verify_watcher
            self.child_watcher = await verify_watcher()
            self.store.event("daemon/child_watcher", None, self.child_watcher)
            if not self.child_watcher["ok"]:
                raise CoordinatorError("CHILD_WATCHER_UNSAFE", "Native child exit watching failed its startup probe",
                                       self.child_watcher, True)
            if self.upgrade_fd is not None:
                asyncio.get_running_loop().call_later(UPGRADE_HOLD_S, self.release_upgrade)
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
            # Calls are admitted once the ledger is consistent (recover); each writer's own reconcile then runs in the
            # background, and a call for one still reconciling waits for it (recovery.recovered, writer_ready).
            plan = await recover(self)
            from .reviews import repair
            repair(self)
            from .registry import publish
            publish(self.store)
            self.ready = True
            self.release_upgrade()
            self.background(recover_writers(self, plan))
            self.background(self.broker.recover())
            self.start_loop_supervisor()
            self.start_service("maintenance", self.maintenance)
            from .executors import observe as observe_executors
            self.start_service("executors", lambda: observe_executors(self))
            from .lead_calls import observe
            self.start_service("lead_calls", lambda: observe(self))
            self.background(self.compact_history())
            for listener in list(self.store.all("listener")):
                if listener["state"] == "dormant":
                    self.retention.request(listener["id"])
                elif listener["state"] == "connected":
                    self.parking.schedule(listener["id"])
            await self.stop.wait()
        finally:
            # Unlike a tentative drain, leaving serve is irreversible. Fence
            # supervisor callbacks before cancelling any background work, also
            # when asyncio.run cancels us for SIGINT or startup raises.
            self.stop.set()
            self.closing = True
            self.ready = False
            try:
                await finish(self.close_serving(allocation, server))
            finally:
                if control.requested:
                    try:
                        await finish(self.leases.call("owner/stop", payload={"inhibit": bool(os.environ.get("SOFTWARE_WORKFLOWS_LAUNCH_AGENT"))}))
                    except (CoordinatorError, OSError):
                        pass  # the explicit process exit code also fences supervisor restart
                control.loop = control.callback = None
                if local_control:
                    control.__exit__(None, None, None)

    async def close_serving(self, allocation, server):
        """Join resource users before releasing anything, despite cancellation."""
        from .locks import cancel_and_join, joined
        self.parking.close()
        self.broker.close()
        if server is not None:
            # Python 3.12+ wait_closed also waits for idle connections.
            server.close()
        started = time.monotonic()
        handlers = set(self.rpc_tasks)
        methods = [self.inflight[task] for task in handlers if task in self.inflight]
        for task in handlers - self.inflight.keys():
            self.rpc_writers[task].transport.abort()
            task.cancel()
        pending = set()
        if handlers:
            # The one-second grace bounds clients, not running filesystem IO.
            # Cancellation may shorten the grace but cannot skip the joins.
            try:
                _, pending = await asyncio.wait(handlers, timeout=STOP_DRAIN_S)
            except asyncio.CancelledError:
                pending = {task for task in handlers if not task.done()}
        # Stream transports support abort on every accepted Python (3.11+).
        # Server.abort_clients/close_clients were only added in Python 3.13.
        for writer in list(self.rpc_writers.values()):
            writer.transport.abort()
        await cancel_and_join(handlers, interval=STOP_DRAIN_S)
        if server is not None:
            while True:
                try:
                    await server.wait_closed()
                    break
                except asyncio.CancelledError:
                    continue
        self.store.event("daemon/rpc_shutdown", None, {
            "handlers": len(handlers), "cancelled": len(pending),
            "methods": methods, "grace_seconds": STOP_DRAIN_S,
            "seconds": time.monotonic() - started})
        # Fence new evidence, but keep connections open while their consumers
        # finish cancellation and compensation. Aborting them first turns an
        # intentional stop into NATIVE_DISCONNECTED evidence failures.
        self.native_intake_closed = True
        # Cancellation cleanup can hand work to another tracked owner. Drain
        # each resulting generation before closing its shared resources too.
        while self.tasks:
            tasks = list(self.tasks)
            await cancel_and_join(tasks, interval=STOP_DRAIN_S)
        while True:
            try:
                await self.ledger.scans.close()
                break
            except asyncio.CancelledError:
                continue
        # Consumers can no longer call or journal. Retire and join the
        # untracked receivers before SQLite; native app-server processes stay.
        connections = list(self.connections.values())
        await joined(asyncio.gather(*(connection.close(abort=True) for connection in connections),
                                    return_exceptions=True), propagate_cancel=False)
        self.connections.clear()
        if self.native_service is not None:
            self.native_service.cancel()
            self.native_service = None
        for _, future in self.native_tickets:
            future.cancel()
        self.native_tickets.clear()
        self.native_waiters.clear()
        self.native_granted.clear()
        self.store.event("daemon/native_intake_closed", None, {"receivers": len(connections)})
        if hasattr(self, "admission_executor"):
            self.admission_executor.shutdown(wait=True, cancel_futures=True)
        # No unconditional finally: an unsuccessful join must never release the
        # database or descriptors still owned by its unfinished users.
        if allocation is not None:
            runtime.cleanup(allocation)
        self.release_upgrade()
        for descriptor in self.ledger.ownership.values():
            os.close(descriptor)
        self.ledger.ownership.clear()
        self.store.db.close()

    def release_upgrade(self):
        """Release upgrade.lock, held since the supervisor restarted this worker."""
        if self.upgrade_fd is not None:
            os.close(self.upgrade_fd)
            self.upgrade_fd = None


# A worker its supervisor restarted holds upgrade.lock until it serves, as a client that starts a daemon holds it
# until the daemon is ready (Client.settled), and no longer than such a client holds it: the caller's deadline,
# Client.ensure's 30 s timeout. Measured 10-21: a restarted worker serves 0.64 s after it starts with 13 writers in
# its ledger, 1.2 s with the child watcher's calibration (children.start); past the bound it lets go without serving.
UPGRADE_HOLD_S = 30


def upgrade_claim(root):
    """<state>/upgrade.lock, taken as Client.upgrading takes it, for a worker its supervisor restarts: its
    descriptor, or None while another process holds it for a second (a client starting, replacing or stopping the
    daemon). A process that only tests the lock (Client.current, the lead hooks) holds it for microseconds."""
    from .client import UPGRADE_LOCK
    descriptor = os.open(str(root / UPGRADE_LOCK), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    # A test of the lock (client._held) holds it 13 µs at the median, 56 µs at p99.9 and 341 µs at most (measured
    # 10-21, 20,000 tests): 1 s is about 2,900 times the longest. A start, replacement or stop holds it for the whole
    # daemon start or drain it makes, at least the 0.64 s a worker takes to serve.
    deadline = time.monotonic() + 1
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                return None
            time.sleep(0.05)


def worker(root):
    from .stop_control import StopControl
    with StopControl() as control:
        return _worker(root, control)


def _worker(root, control):
    if sys.version_info < (3, 11):
        raise CoordinatorError("PYTHON_VERSION", "Daemon requires Python 3.11+")
    root = validate_state(root, create=True)
    os.umask(0o077)
    upgrade = os.environ.pop("SOFTWARE_WORKFLOWS_UPGRADE_FD", "")
    with open(root / "worker.lock", "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            daemon = Daemon(root)
            daemon.stop_control = control
            if upgrade:
                daemon.upgrade_fd = int(upgrade)
                os.set_inheritable(daemon.upgrade_fd, False)  # never into an app-server or a command
            asyncio.run(daemon.serve())
        except Exception as exc:
            error = exc if isinstance(exc, CoordinatorError) else CoordinatorError(
                "DAEMON_STARTUP", type(exc).__name__ + ": " + str(exc)[:1024], operational=True)
            runtime.atomic_json(root / "startup-error.json", {"time": time.time(), "error": error.envelope()})
            return 4 if control.requested else 3
    return 4 if control.requested else 0


def supervise(root):
    from .owners import supervise as own
    return own(root)
