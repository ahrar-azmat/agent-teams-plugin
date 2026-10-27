"""Demand-driven, verified unload of idle native writer families."""
import asyncio
import math
import os
import time

from . import native
from .client import CoordinatorError
from .locks import finish, KeyedLocks
from .store import BLOCKERS


# 388 live completed -> started gaps (2026-10-19..26): median 792 s;
# 27% exceed 1800 s. This policy trades those cold resumes (~0.60 s measured)
# for reclaiming idle families (~546 MiB with every MCP server).
DEFAULT_IDLE_SECONDS = 1800.0


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
                "problem": "writer_park_idle_seconds must be a finite nonnegative number; parking is disabled"}


class Parking:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.config = setting()
        self.locks = KeyedLocks()
        self.timers, self.tasks, self.activity = {}, {}, {}
        self.attempted = self.completed = 0
        self.abandoned, self.durations = {}, []

    def touch(self, listenerid):
        self.activity[listenerid] = time.monotonic()
        self.schedule(listenerid)

    def schedule(self, listenerid, delay=None):
        if not self.config["seconds"] or self.daemon.closing or listenerid in self.timers or listenerid in self.tasks:
            return
        listener = self.store.get(listenerid, required=False)
        if not listener or not listener.get("root_thread") or listener["state"] != "connected":
            return
        self.activity.setdefault(listenerid, time.monotonic())
        self.timers[listenerid] = asyncio.get_running_loop().call_later(
            self.config["seconds"] if delay is None else delay, self.due, listenerid)

    def due(self, listenerid):
        self.timers.pop(listenerid, None)
        if self.daemon.closing:
            return
        remaining = self.config["seconds"] - (time.monotonic() - self.activity.get(listenerid, time.monotonic()))
        if remaining > 0:
            self.schedule(listenerid, remaining)
            return
        self.request(listenerid)

    def request(self, listenerid):
        if listenerid in self.tasks or self.daemon.closing:
            return
        task = self.daemon.background(self.park(listenerid))
        self.tasks[listenerid] = task
        def done(unused):
            self.tasks.pop(listenerid, None)
            # Back off after an abandoned park. This is a pending action, not an
            # idle poll; no timer remains once a family becomes dormant.
            self.schedule(listenerid, max(1.0, self.config["seconds"]))
        task.add_done_callback(done)

    def close(self):
        for timer in self.timers.values():
            timer.cancel()
        self.timers.clear()

    def actors(self, listener):
        return [a for a in self.store.all("actor", listener["unit"]) if a.get("listener") == listener["id"]]

    def eligible(self, listener, *, fenced=False):
        daemon, store = self.daemon, self.store
        run = store.get(listener["run"])
        if listener["state"] != ("parking" if fenced else "connected") or not listener.get("root_thread"):
            return "not_connected"
        if listener.get("recovery_state") != "reconciled" or daemon.recovery_barrier:
            return "recovery"
        if any(a.get("active_turn") for a in self.actors(listener)):
            return "active_turn"
        if daemon.dormancy.pending(run["id"]):
            return "demand_start"
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
        if store.db.execute("SELECT 1 FROM records INDEXED BY blocker_operation WHERE " + BLOCKERS["operation"] +
                            " AND parent=? LIMIT 1", (listener["unit"],)).fetchone():
            return "operation"
        if any(store.db.execute("SELECT 1 FROM records INDEXED BY blocker_" + kind + " WHERE " + BLOCKERS[kind] +
                                " AND parent=? LIMIT 1", (parent,)).fetchone()
               for kind, parent in (("dynamic", listener["unit"]), ("obligation", listener["id"]))):
            return "tool_request"
        if daemon.native_unapplied(listener["id"]):
            return "native_application"
        return None

    def stage(self, listenerid, step, **details):
        listener = self.store.get(listenerid)
        listener["park"] = {**listener.get("park", {}), "step": step, "updated": time.time(), **details}
        with self.store.transaction():
            self.store.save(listener)
            self.store.event("listener/park_" + step, listenerid, details)

    async def verify(self, listener, connection, pending):
        actors = self.actors(listener)
        receipts, costs = await self.daemon.dormancy.received(listener, actors)
        for actor in actors:
            await self.daemon.dormancy.compare(actor, connection, receipts[actor["thread"]], costs[actor["thread"]], pending=pending)
        return actors, costs

    async def park(self, listenerid):
        daemon, store = self.daemon, self.store
        listener = store.get(listenerid)
        began = time.monotonic()
        self.attempted += 1
        try:
            # Same order as resume: input serialization, family start/park,
            # reconcile. No scheduler mutex spans these native awaits.
            async with daemon.writer.locks.hold(listener["run"]), self.locks.hold(listener["run"]), daemon.reconcile_locks.hold(listener["run"]):
                listener = store.get(listenerid)
                reason = self.eligible(listener)
                if reason:
                    raise CoordinatorError("PARK_INELIGIBLE", reason)
                connection = daemon.connections.get(listenerid)
                if not connection or connection.closed:
                    raise CoordinatorError("PARK_INELIGIBLE", "connection_closed")
                listener["state"] = "parking"
                store.save(listener)
                self.stage(listenerid, "fenced", idle_seconds=began - self.activity.get(listenerid, began))
                await daemon.discover_descendants(listener)
                for actor in self.actors(listener):
                    response = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
                    if response["thread"].get("status", {}).get("type") not in ("idle", "notLoaded"):
                        raise CoordinatorError("PARK_INELIGIBLE", "native_active_turn")
                    terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
                    if terminals["data"] or terminals.get("nextCursor"):
                        raise CoordinatorError("PARK_INELIGIBLE", "background_terminal")
                    queued = await connection.call("thread/queue/list", {"threadId": actor["thread"]})
                    if queued.get("data") or queued.get("items"):
                        raise CoordinatorError("PARK_INELIGIBLE", "native_queue")
                reason = self.eligible(store.get(listenerid), fenced=True)
                if reason:
                    raise CoordinatorError("PARK_INELIGIBLE", reason)
                self.stage(listenerid, "preverify")
                await self.verify(listener, connection, True)
                family = (await daemon.leases.call("snapshot", payload={"listener": listenerid}))["family"]
                members = family["members"]
                self.stage(listenerid, "unloading", members=members)
                listener = store.get(listenerid)
                listener["park_resume_required"] = True
                store.save(listener)
                for actor in self.actors(listener):
                    await connection.call("thread/unsubscribe", {"threadId": actor["thread"]})
                # thread/closed is receipt evidence; loaded/list confirms that
                # the unload completed, even if the notification applied later.
                threads = {a["thread"] for a in self.actors(listener)}
                deadline = time.monotonic() + 60
                while True:
                    loaded = set()
                    async for page in native.page(connection, "thread/loaded/list", {}):
                        loaded.update(entry if isinstance(entry, str) else entry["id"] for entry in page["data"])
                    if not threads.intersection(loaded):
                        break
                    if time.monotonic() >= deadline:
                        raise CoordinatorError("PARK_UNLOAD_PENDING", "Native unload is not yet confirmed", {"retryable": True}, True)
                    await asyncio.sleep(0.05)
                self.stage(listenerid, "postverify")
                actors, costs = await self.verify(listener, connection, False)
                gaps = [gap for cost in costs.values() for gap in cost["gaps"]]
                if gaps:
                    listener = store.get(listenerid)
                    listener["history_gaps"] = gaps
                    store.save(listener)
                    raise CoordinatorError("HISTORY_GAP", "Unloaded history differs from receipts", {"gaps": gaps})
                # The live path may not yet have needed a history checkpoint.
                # Establish it from unloaded history before authorizing retention.
                for actor in actors:
                    await daemon.history.observe(listener, actor, connection)
                self.stage(listenerid, "stopping")
                await finish(daemon.stop_listener(store.get(listenerid)))
                receipt = await daemon.leases.call("owner/status")
                family = next(f for f in receipt["families"] if f["listener"] == listenerid)
                if family["state"] != "GONE":
                    raise CoordinatorError("FAMILY_REAPING", "Park awaits the owner FAMILY_GONE receipt", operational=True)
                self.stage(listenerid, "family_exit", owner_sequence=family["owner_sequence"])
                with store.transaction():
                    for actor in actors:
                        cp = daemon.history.checkpoint(actor)
                        if cp:
                            cp["received_verified"] = costs[actor["thread"]]["through"]
                            store.save(cp)
                    listener = store.get(listenerid)
                    listener.update(state="dormant", recovery_state="reconciled", dormant_at=time.time(), history_gaps=[],
                        dormant_reason="parked after " + str(round(listener["park"]["idle_seconds"], 3)) + " s idle",
                        dormant_proof={"exit": {"members": members, "observed": time.time()}, "read_only": True,
                                       "descendants_enumerated": True, "actors": [a["id"] for a in actors]})
                    store.save(listener)
                    store.event("listener/dormant", listenerid, {"why": listener["dormant_reason"], "seconds": time.monotonic() - began})
                from . import processes
                previous = store.get(listener.get("replaces"), required=False)
                while previous:
                    if processes.exit_evidence(previous):
                        previous.update(state="dormant", dormant_proof=listener["dormant_proof"],
                                        dormant_at=time.time(), dormant_reason="verified predecessor of parked writer")
                        store.save(previous)
                    previous = store.get(previous.get("replaces"), required=False)
                self.completed += 1
                self.durations.append(time.monotonic() - began)
                from .writer import TERMINAL
                await daemon.broker.idle(listener["run"], ended=store.get(listener["run"])["state"] in TERMINAL, start_locked=True)
                daemon.retention.request(listenerid)
        except Exception as exc:
            reason = str(exc) if getattr(exc, "code", None) == "PARK_INELIGIBLE" else getattr(exc, "code", type(exc).__name__)
            self.abandoned[reason] = self.abandoned.get(reason, 0) + 1
            listener = store.get(listenerid)
            # park_resume_required survives an abandoned unload. Every input
            # path joins the persisted demand before using that live server.
            # Never pretend an exited/half-reaped family is connected.
            connection = daemon.connections.get(listenerid)
            if listener["state"] == "parking" and connection and not connection.closed:
                listener["state"] = "connected"
                store.save(listener)
            self.stage(listenerid, "abandoned", reason=reason)

    def status(self):
        values = sorted(self.durations)
        return {"setting": self.config, "parks_attempted": self.attempted, "parks_completed": self.completed,
                "parks_abandoned": self.abandoned, "persistence_pending": self.abandoned.get("PERSISTENCE_PENDING", 0),
                "park_p50": values[len(values) // 2] if values else None, "park_max": max(values, default=None)}
