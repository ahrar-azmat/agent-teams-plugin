"""Demand-driven, verified unload of idle native writer families."""
import asyncio
import math
import os
import time

from . import native, processes
from .client import CoordinatorError
from .locks import finish, KeyedLocks
from .store import BLOCKERS, WINDOW_ACTIVE


# 388 live completed -> started gaps (2026-10-19..26): median 792 s;
# 27% exceed 1800 s. This policy trades those cold resumes (~0.60 s measured)
# for reclaiming idle families (~546 MiB with every MCP server).
DEFAULT_IDLE_SECONDS = 1800.0

# Threat: concurrent history verification and family inspection retain native
# responses and compete for the event loop on a memory-constrained host. Four
# real writers at capacities 1/2/4 took 0.568/0.406/0.357 s; individual parks
# peaked at 0.140/0.167/0.254 s, and 50 ms loop samples at 52.2/51.8/51.2 ms.
# Two keeps most of the throughput gain with half of four's concurrent history
# readers. Excess candidates remain queued; capacity and queue depth are visible.
DEFAULT_RECLAIM_CONCURRENCY = 2
RETRY_FIRST_SECONDS = 5.0


def capacity_setting():
    source = next((key for key in ("SOFTWARE_WORKFLOWS_WRITER_RECLAIM_CONCURRENCY",
                                  "CLAUDE_PLUGIN_OPTION_WRITER_RECLAIM_CONCURRENCY") if key in os.environ), None)
    value = os.environ[source] if source else str(DEFAULT_RECLAIM_CONCURRENCY)
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise CoordinatorError("CONFIG", "writer_reclaim_concurrency must be a positive integer", {"source": source})
    return {"capacity": int(value), "source": source or "default"}


class CheckedConnection:
    """Fence demand around every native await before an unsubscribe is sent."""
    def __init__(self, connection, check):
        self.connection, self.check = connection, check

    def __getattr__(self, key):
        return getattr(self.connection, key)

    async def call(self, *args, **kwargs):
        self.check()
        result = await self.connection.call(*args, **kwargs)
        self.check()
        return result


def setting():
    source = next((key for key in ("SOFTWARE_WORKFLOWS_WRITER_PARK_IDLE_SECONDS",
                                  "CLAUDE_PLUGIN_OPTION_WRITER_PARK_IDLE_SECONDS") if key in os.environ), None)
    try:
        value = float(os.environ[source]) if source else DEFAULT_IDLE_SECONDS
        if not math.isfinite(value) or value < 0:
            raise ValueError()
        return {"seconds": value, "source": source or "default", "error": None}
    except ValueError:
        return {"seconds": 0, "source": source, "error": "WRITER_PARK_SETTING",
                "problem": "writer_park_idle_seconds must be a finite nonnegative number; idle-timer parking is disabled"}


class Parking:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.config = setting()
        self.capacity = capacity_setting()
        self.locks = KeyedLocks()
        self.timers, self.tasks, self.activity = {}, {}, {}
        self.queued, self.blockers = {}, {}
        self.joining = set()
        self.attempted = self.completed = 0
        self.abandoned, self.durations = {}, []

    def touch(self, listenerid):
        self.activity[listenerid] = time.monotonic()
        self.blockers.pop(listenerid, None)
        timer = self.timers.pop(listenerid, None)
        if timer:
            timer.cancel()
        self.schedule(listenerid)

    def schedule(self, listenerid, delay=None):
        if (delay is None and not self.config["seconds"] or self.daemon.closing
                or listenerid in self.timers or listenerid in self.tasks):
            return
        listener = self.store.get(listenerid, required=False)
        if (not listener or not listener.get("root_thread") or
                listener["state"] in ("dormant", "dead", "stopped") and not listener.get("park_stop")):
            return
        self.activity.setdefault(listenerid, time.monotonic())
        self.timers[listenerid] = asyncio.get_running_loop().call_later(
            self.config["seconds"] if delay is None else delay, self.due, listenerid)

    def due(self, listenerid):
        self.timers.pop(listenerid, None)
        if self.daemon.closing:
            return
        remaining = self.config["seconds"] - (time.monotonic() - self.activity.get(listenerid, time.monotonic()))
        if listenerid not in self.blockers and remaining > 0:
            self.schedule(listenerid, remaining)
            return
        self.request(listenerid)

    def request(self, listenerid):
        if listenerid in self.tasks or self.daemon.closing or self.daemon.draining or self.daemon.recovery_barrier:
            return
        listener = self.store.get(listenerid, required=False)
        if not listener or not self.kind(listener):
            self.activity.pop(listenerid, None)
            self.queued.pop(listenerid, None)
            return
        blocker = self.blockers.get(listenerid)
        if blocker and blocker["next"] > time.monotonic():
            # Local eligibility can arrive without native activity (custody,
            # delivery, cancellation or startup settlement). Rejudge cheap local
            # predicates; native observations still honor their retry deadline.
            if not blocker.get("local") or self.eligible(listener, retiring=self.kind(listener) == "retire"):
                return
            self.blockers.pop(listenerid, None)
        self.queued.setdefault(listenerid, None)
        self.pump()

    def kind(self, listener):
        from .recovery import permanently_failed, permanent_classification
        intent = self.intent(listener["id"])
        if intent and intent["state"] == "stopping":
            return "retire"
        if listener.get("park_stop"):
            return "park"
        if listener["state"] in ("stopped", "dead", "dormant"):
            return None
        if listener.get("recovery_state") == "failed":
            return "retire" if (permanently_failed(self.daemon, listener) and
                                permanent_classification(self.store, listener)[0]) else None
        return "park" if listener["state"] in ("connected", "parking", "stopping") else None

    def maintain(self):
        for listener in self.store.reclaim_listeners():
            run = self.store.get(listener["run"])
            if (listener.get("park_stop") or self.kind(listener) == "retire" or
                    run.get("workflow") or run.get("task") or run.get("production_started") or run.get("phases")):
                self.request(listener["id"])
        self.pump()

    def pump(self):
        if self.daemon.closing or self.daemon.draining or self.daemon.recovery_barrier:
            return
        while self.queued and len(self.tasks) + len(self.joining) < self.capacity["capacity"]:
            listenerid = next(iter(self.queued))
            self.queued.pop(listenerid)
            listener = self.store.get(listenerid, required=False)
            kind = self.kind(listener) if listener else None
            if not kind:
                continue
            task = self.daemon.background(self.retire(listenerid) if kind == "retire" else self.park(listenerid))
            self.tasks[listenerid] = task
            def done(unused, listenerid=listenerid):
                self.tasks.pop(listenerid, None)
                blocker = self.blockers.get(listenerid)
                if blocker:
                    self.schedule(listenerid, max(0, blocker["next"] - time.monotonic()))
                self.pump()
            task.add_done_callback(done)

    def refused(self, listenerid, reason, *, local=False, recorded=False, retiring=False):
        old = self.blockers.get(listenerid, {})
        ceiling = self.config["seconds"] or DEFAULT_IDLE_SECONDS
        delay = min(ceiling, old.get("delay", RETRY_FIRST_SECONDS / 2) * 2)
        self.blockers[listenerid] = {"reason": reason, "delay": delay, "next": time.monotonic() + delay,
                                     "next_attempt": time.time() + delay, "local": local}
        self.abandoned[reason] = self.abandoned.get(reason, 0) + 1
        if recorded:
            listener = self.store.get(listenerid)
            key = "retirement_refusals" if retiring else "park_refusals"
            if reason not in listener.get(key, []):
                listener.setdefault(key, []).append(reason)
                with self.store.transaction():
                    self.store.save(listener)
                    self.store.event("listener/retirement_refused" if retiring else "listener/park_abandoned",
                                     listenerid, {"reason": reason})

    def close(self):
        for timer in self.timers.values():
            timer.cancel()
        self.timers.clear()
        self.queued.clear()

    def actors(self, listener):
        return [a for a in self.store.all("actor", listener["unit"]) if a.get("listener") == listener["id"]]

    def eligible(self, listener, *, fenced=False, retiring=False):
        daemon, store = self.daemon, self.store
        run = store.get(listener["run"])
        if not listener.get("root_thread") or (not retiring and listener["state"] != ("parking" if fenced else "connected")):
            return "not_connected"
        if store.get(run["root_actor"]).get("listener") != listener["id"]:
            return "not_bound"
        if (not retiring and listener.get("recovery_state") != "reconciled" or daemon.recovery_barrier
                or ("run", run["id"]) in daemon.recovering or run["id"] in daemon.readmitting
                or any(v["run"] == run["id"] for v in daemon.listener_reconciles.values())):
            return "recovery"
        if any(a.get("active_turn") for a in self.actors(listener)):
            return "active_turn"
        if ((daemon.dormancy.demand(run["id"]) or {}).get("state") in ("pending", "starting")
                or run["id"] in daemon.dormancy.tasks or daemon.dormancy.entering.get(run["id"])
                or daemon.writer.locks.contended(run["id"])):
            return "demand_start"
        if daemon.runs.cancel_passes.get(run["id"]) or run["state"] == "cancelling":
            return "cancellation"
        if run["id"] in daemon.writer.resubmitting or any(
                p.get("state") in ("prepared", "submitting", "uncertain") for p in (run.get("phases") or {}).values()):
            return "input_or_workflow"
        if run.get("workflow") and run["state"] in ("transition_pending", "settling", "running"):
            return "workflow_owed"
        recipients = {a["id"] for a in self.actors(listener)}
        if any(store.db.execute("SELECT 1 FROM records INDEXED BY blocker_mail WHERE " + BLOCKERS["mail"] +
                               " AND json_extract(data,'$.recipient')=? LIMIT 1", (ident,)).fetchone() for ident in recipients):
            return "delivery_or_notice"
        if not daemon.unit_quiet(listener["unit"]):
            return "custody_operation_or_lead_hold"
        if not retiring and store.db.execute("SELECT 1 FROM records INDEXED BY blocker_operation WHERE " + BLOCKERS["operation"] +
                            " AND parent=? LIMIT 1", (listener["unit"],)).fetchone():
            return "operation"
        if not retiring and any(store.db.execute("SELECT 1 FROM records INDEXED BY blocker_" + kind + " WHERE " + BLOCKERS[kind] +
                                " AND parent=? LIMIT 1", (parent,)).fetchone()
               for kind, parent in (("dynamic", listener["unit"]), ("obligation", listener["id"]))):
            return "tool_request"
        if daemon.native_unapplied(listener["id"], flush=False):
            return "native_application"
        return None

    def stage(self, listenerid, step, **details):
        listener = self.store.get(listenerid)
        previous = listener.get("park", {})
        if previous.get("step") == step and all(previous.get(key) == value for key, value in details.items()):
            return
        listener["park"] = {**listener.get("park", {}), "step": step, "updated": time.time(), **details}
        with self.store.transaction():
            self.store.save(listener)
            self.store.event("listener/park_" + step, listenerid, details)

    async def inspect(self, listener, connection, check, *, loaded_only=False):
        loaded = set()
        async for page in native.page(connection, "thread/loaded/list", {}):
            check()
            if not isinstance(page.get("data"), list):
                raise CoordinatorError("NATIVE_OBSERVATION", "Loaded-thread inventory has no data list")
            for entry in page["data"]:
                ident = entry if isinstance(entry, str) else entry.get("id")
                if not isinstance(ident, str) or not ident:
                    raise CoordinatorError("NATIVE_OBSERVATION", "Loaded-thread inventory has no thread identity")
                loaded.add(ident)
        known = {a["thread"] for a in self.actors(listener)}
        threads = set(loaded) if loaded_only else loaded | known
        if not loaded_only:
            # Read discovery's inventories before its actor-creation pass can
            # write a fence. Unsupported/failed pagination is a standing blocker.
            for archived in (False, True):
                async for page in native.page(connection, "thread/list", {
                        "ancestorThreadId": listener["root_thread"], "archived": archived}):
                    check()
                    if not isinstance(page.get("data"), list):
                        raise CoordinatorError("NATIVE_OBSERVATION", "Descendant inventory has no data list")
                    for entry in page["data"]:
                        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                            raise CoordinatorError("NATIVE_OBSERVATION", "Descendant inventory has no thread identity")
                        threads.add(entry["id"])
        statuses = {}
        for thread in sorted(threads):
            response = await connection.call("thread/read", {"threadId": thread, "includeTurns": False})
            if not loaded_only and thread not in known:
                info = response.get("thread") or {}
                if info.get("sessionId") != listener["root_thread"] or not info.get("parentThreadId"):
                    raise CoordinatorError("ACTOR_NAMESPACE", "Native thread cannot be bound to this listener's root")
            status = (response.get("thread") or {}).get("status")
            reason = native.quiet_status(status)
            if reason:
                raise CoordinatorError("PARK_INELIGIBLE", reason)
            statuses[thread] = status["type"]
            # An already unloaded runtime has no terminal inventory. The saved
            # queue still has to be read, including on a partial-unload retry.
            methods = [("thread/queue/list", "native_queue")]
            if status["type"] != "notLoaded":
                methods.insert(0, ("thread/backgroundTerminals/list", "background_terminal"))
            for method, reason in methods:
                async for page in native.page(connection, method, {"threadId": thread}):
                    check()
                    if not isinstance(page.get("data"), list):
                        raise CoordinatorError("NATIVE_OBSERVATION", method + " has no data list")
                    if page["data"]:
                        raise CoordinatorError("PARK_INELIGIBLE", reason)
        check()
        return {"loaded": sorted(loaded), "statuses": statuses, "queues_empty": True, "terminals_empty": True}

    async def verify(self, listener, connection, pending, *, record=True, check=None):
        actors = self.actors(listener)
        receipts, costs = await self.daemon.dormancy.received(listener, actors, record=record, check=check)
        for actor in actors:
            if check:
                check()
            await self.daemon.dormancy.compare(actor, connection, receipts[actor["thread"]], costs[actor["thread"]],
                                               pending=pending, record=record)
        if check:
            check()
        return actors, costs

    async def park(self, listenerid):
        daemon, store = self.daemon, self.store
        listener = store.get(listenerid)
        began = time.monotonic()
        self.attempted += 1
        fenced, local = False, False
        dispatched = bool(listener.get("park_resume_required"))
        retrying_unload = dispatched
        generation = store.get(listener["run"]).get("cancel_generation", 0)

        def check():
            nonlocal local
            current = store.get(listenerid)
            if store.get(store.get(current["run"])["root_actor"]).get("listener") != listenerid:
                raise CoordinatorError("PARK_INELIGIBLE", "not_bound")
            if daemon.closing or daemon.draining:
                raise CoordinatorError("PARK_INELIGIBLE", "draining")
            if dispatched:
                return
            reason = self.eligible(current, fenced=fenced)
            if store.get(current["run"]).get("cancel_generation", 0) != generation:
                reason = "cancellation"
            local = bool(reason)
            if reason:
                raise CoordinatorError("PARK_INELIGIBLE", reason)

        try:
            async with daemon.writer.locks.hold(listener["run"]), self.locks.hold(listener["run"]), daemon.reconcile_locks.hold(listener["run"]):
                listener = store.get(listenerid)
                if listener.get("park_stop"):
                    await self.finish_park(listenerid, began)
                    return
                check()
                raw = daemon.connections.get(listenerid)
                if not raw or raw.closed:
                    raise CoordinatorError("PARK_INELIGIBLE", "connection_closed")
                connection = CheckedConnection(raw, check)
                # These observations cannot write coordinator records or events.
                # In particular, a standing post-fence blocker is rejudged here,
                # before recording another fence or history diagnostic.
                await self.inspect(listener, connection, check)
                await self.verify(listener, connection, True, record=False, check=check)
                family = (await daemon.leases.call("snapshot", payload={"listener": listenerid}))["family"]
                check()
                members = family["members"]
                if not dispatched:
                    listener = store.get(listenerid)
                    listener["state"] = "parking"
                    store.save(listener)
                    fenced = True
                    self.stage(listenerid, "fenced", idle_seconds=began - self.activity.get(listenerid, began))
                    check()
                    await daemon.discover_descendants(listener, connection=connection)
                    check()
                    await self.inspect(listener, connection, check)
                    self.stage(listenerid, "preverify")
                    await self.verify(listener, connection, True, check=check)
                    check()
                    self.stage(listenerid, "unloading", members=members)
                else:
                    # Retry the incomplete unload without another written fence
                    # or preverify. The read-only checks above still have to pass.
                    members = listener.get("park", {}).get("members", members)
                # Entering this stage does not yet authorize unsubscribe.
                await asyncio.sleep(0)
                check()
                for actor in self.actors(listener):
                    check()
                    if not dispatched:
                        listener = store.get(listenerid)
                        listener["park_resume_required"] = True
                        store.save(listener)
                        # A lost response can still mean native unsubscribed.
                        dispatched = True
                    await connection.call("thread/unsubscribe", {"threadId": actor["thread"]})
                threads = {a["thread"] for a in self.actors(listener)}
                deadline = time.monotonic() + 60
                while True:
                    loaded = set()
                    async for page in native.page(raw, "thread/loaded/list", {}):
                        loaded.update(entry if isinstance(entry, str) else entry["id"] for entry in page["data"])
                    if not threads.intersection(loaded):
                        break
                    if time.monotonic() >= deadline:
                        raise CoordinatorError("PARK_UNLOAD_PENDING", "Native unload is not yet confirmed", {"retryable": True}, True)
                    await asyncio.sleep(0.05)
                self.stage(listenerid, "postverify")
                actors, costs = await self.verify(listener, raw, False, record=not retrying_unload)
                gaps = [gap for cost in costs.values() for gap in cost["gaps"]]
                if gaps:
                    listener = store.get(listenerid)
                    listener["history_gaps"] = gaps
                    store.save(listener)
                    raise CoordinatorError("HISTORY_GAP", "Unloaded history differs from receipts", {"gaps": gaps})
                for actor in actors:
                    if actor["id"] in store.get(listenerid).get("park_observed", []):
                        continue
                    await daemon.history.observe(listener, actor, raw)
                    current = store.get(listenerid)
                    current.setdefault("park_observed", []).append(actor["id"])
                    store.save(current)
                # Persist the verified suffix before stopping can detach native.
                # A reaping retry needs no connection and does not repeat history.
                listener = store.get(listenerid)
                listener["park_stop"] = {"members": members, "actors": [a["id"] for a in actors],
                    "through": {a["id"]: costs[a["thread"]]["through"] for a in actors},
                    "idle_seconds": listener["park"]["idle_seconds"]}
                store.save(listener)
                self.stage(listenerid, "stopping")
                await self.finish_park(listenerid, began)
        except Exception as exc:
            reason = str(exc) if getattr(exc, "code", None) == "PARK_INELIGIBLE" else getattr(exc, "code", type(exc).__name__)
            if fenced:
                listener = store.get(listenerid)
                connection = daemon.connections.get(listenerid)
                if listener["state"] == "parking" and connection and not connection.closed:
                    listener["state"] = "connected"
                    store.save(listener)
            self.refused(listenerid, reason, local=local, recorded=fenced or dispatched)

    async def finish_park(self, listenerid, began):
        daemon, store = self.daemon, self.store
        listener = store.get(listenerid)
        proof = listener["park_stop"]
        await finish(daemon.stop_listener(listener))
        family = await daemon.family_receipt(store.get(listenerid))
        if family["state"] != "GONE":
            raise CoordinatorError("FAMILY_REAPING", "Park awaits the owner FAMILY_GONE receipt", operational=True)
        self.stage(listenerid, "family_exit", owner_sequence=family["owner_sequence"])
        with store.transaction():
            for actorid in proof["actors"]:
                cp = daemon.history.checkpoint(store.get(actorid))
                if cp:
                    cp["received_verified"] = proof["through"][actorid]
                    store.save(cp)
            listener = store.get(listenerid)
            listener.pop("park_stop", None)
            listener.pop("park_observed", None)
            listener.update(state="dormant", recovery_state="reconciled", dormant_at=time.time(), history_gaps=[],
                dormant_reason="parked after " + str(round(proof["idle_seconds"], 3)) + " s idle",
                dormant_proof={"exit": {"members": proof["members"], "observed": time.time()}, "read_only": True,
                               "descendants_enumerated": True, "actors": proof["actors"]})
            store.save(listener)
            store.event("listener/dormant", listenerid, {"why": listener["dormant_reason"], "seconds": time.monotonic() - began})
        previous = store.get(listener.get("replaces"), required=False)
        while previous:
            if processes.exit_evidence(previous):
                previous.update(state="dormant", dormant_proof=listener["dormant_proof"],
                                dormant_at=time.time(), dormant_reason="verified predecessor of parked writer")
                store.save(previous)
            previous = store.get(previous.get("replaces"), required=False)
        self.blockers.pop(listenerid, None)
        self.activity.pop(listenerid, None)
        self.completed += 1
        self.durations.append(time.monotonic() - began)
        from .writer import TERMINAL
        await daemon.broker.idle(listener["run"], ended=store.get(listener["run"])["state"] in TERMINAL, start_locked=True)
        daemon.retention.request(listenerid)

    def intent(self, listenerid):
        return self.store.get("writer_retirement:" + listenerid, required=False)

    async def retire(self, listenerid):
        from .recovery import permanently_failed, permanent_classification
        from .runtime import plugin_version
        from .locks import finish_thread
        daemon, store = self.daemon, self.store
        listener = store.get(listenerid)
        local = False

        def check():
            nonlocal local
            current = store.get(listenerid)
            if (not permanently_failed(daemon, current) or current.get("recovery_state") != "failed"
                    or not permanent_classification(store, current)[0]):
                raise CoordinatorError("PARK_INELIGIBLE", "recovery_retry_due")
            reason = self.eligible(current, retiring=True)
            if daemon.closing or daemon.draining:
                reason = "draining"
            if store.db.execute("SELECT 1 FROM records INDEXED BY quiet_executing WHERE " + WINDOW_ACTIVE["executing"] +
                                " AND json_extract(data,'$.native.listener')=? LIMIT 1", (listenerid,)).fetchone():
                reason = "executing_operation"
            local = bool(reason)
            if reason:
                raise CoordinatorError("PARK_INELIGIBLE", reason)

        try:
            async with daemon.writer.locks.hold(listener["run"]), self.locks.hold(listener["run"]), daemon.reconcile_locks.hold(listener["run"]):
                intent = self.intent(listenerid)
                if intent and intent["state"] == "stopping":
                    await self.complete_retirement(intent)
                    return
                check()
                current = await finish_thread(processes.identity, listener["pid"])
                check()
                if not current or current["birth"] != listener.get("birth") or current["zombie"]:
                    raise CoordinatorError("PARK_INELIGIBLE", "not_alive")
                raw = daemon.connections.get(listenerid)
                if not raw or raw.closed:
                    raise CoordinatorError("PARK_INELIGIBLE", "no_connection")
                facts = await self.inspect(listener, CheckedConnection(raw, check), check, loaded_only=True)
                check()
                family = (await daemon.leases.call("snapshot", payload={"listener": listenerid}))["family"]
                check()
                facts.update(unit_quiet=True, native_applied=True, executing_operations=False,
                             pid=listener["pid"], birth=listener["birth"])
                ident = "writer_retirement:" + listenerid
                store.put("writer_retirement", ident, {"run": listener["run"], "listener": listenerid, "state": "pending",
                    "generation": store.generation, "release": plugin_version(), "created": time.time(),
                    "members": family["members"], "proof": facts}, listener["run"])
                check()
                intent = store.get(ident)
                intent.update(state="stopping", authorized=time.time())
                store.save(intent)  # durable before any owner end request
                await self.complete_retirement(intent)
        except Exception as exc:
            reason = str(exc) if getattr(exc, "code", None) == "PARK_INELIGIBLE" else getattr(exc, "code", type(exc).__name__)
            self.refused(listenerid, reason, local=local, recorded=True, retiring=True)

    async def complete_retirement(self, intent):
        """Called with the reconcile lock held; never acquires writer/parking."""
        daemon, store = self.daemon, self.store
        listenerid = intent["listener"]
        await daemon.stop_listener(store.get(listenerid))
        listener = store.get(listenerid)
        if not listener.get("family_gone") or listener["state"] != "stopped":
            raise CoordinatorError("FAMILY_REAPING", "Retirement awaits durable family exit", {"retryable": True}, True)
        intent = store.get(intent["id"])
        intent.update(state="complete", completed=time.time(), family_gone=listener["family_gone"])
        with store.transaction():
            store.save(intent)
            store.event("listener/retired", listenerid, {"retirement": intent["id"], "release": intent["release"]})
        self.blockers.pop(listenerid, None)
        self.activity.pop(listenerid, None)

    async def join_retirements(self, runid):
        """Finish a previously authorized stop before any launch-capable call."""
        for intent in list(self.store.all("writer_retirement", runid)):
            if intent["state"] == "pending":
                # No signal was authorized. Keep the old proof as history only;
                # a new inspection must replace it before it can authorize end.
                intent.update(state="discarded", discarded=time.time())
                self.store.save(intent)
            elif intent["state"] == "stopping":
                if len(self.tasks) + len(self.joining) >= self.capacity["capacity"]:
                    self.request(intent["listener"])
                    raise CoordinatorError("FAMILY_REAPING", "Retirement is queued; retry to join its exit",
                                           {"retryable": True}, True)
                self.joining.add(intent["listener"])
                try:
                    await self.complete_retirement(intent)
                except Exception as exc:
                    raise CoordinatorError("FAMILY_REAPING", "Retirement has not yet proved family exit",
                                           {"retryable": True, "cause": getattr(exc, "code", type(exc).__name__)}, True) from exc
                finally:
                    self.joining.discard(intent["listener"])
                    self.pump()

    async def join_park(self, listenerid):
        """An already verified stop must finish before demand can replace it."""
        if not self.store.get(listenerid).get("park_stop"):
            return
        if len(self.tasks) + len(self.joining) >= self.capacity["capacity"]:
            self.request(listenerid)
            raise CoordinatorError("FAMILY_REAPING", "Verified park is queued; retry to join its exit",
                                   {"retryable": True}, True)
        self.joining.add(listenerid)
        try:
            await self.finish_park(listenerid, time.monotonic())
        except Exception as exc:
            raise CoordinatorError("FAMILY_REAPING", "Verified park has not yet proved family exit",
                                   {"retryable": True, "cause": getattr(exc, "code", type(exc).__name__)}, True) from exc
        finally:
            self.joining.discard(listenerid)
            self.pump()

    def status(self):
        values = sorted(self.durations)
        return {"setting": self.config, "parks_attempted": self.attempted, "parks_completed": self.completed,
                "parks_abandoned": self.abandoned, "persistence_pending": self.abandoned.get("PERSISTENCE_PENDING", 0),
                "park_p50": values[len(values) // 2] if values else None, "park_max": max(values, default=None),
                "reclaim": {**self.capacity, "active": len(self.tasks) + len(self.joining), "queued": len(self.queued)},
                "reclaim_blockers": {key: {k: v for k, v in value.items() if k in ("reason", "next_attempt", "delay")}
                                     for key, value in self.blockers.items()}}
