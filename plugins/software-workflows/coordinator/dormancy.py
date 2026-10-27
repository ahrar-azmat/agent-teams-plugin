"""Exited writers recover without loading threads; explicit demand owns one start."""
import asyncio
import json
import time

from .client import CoordinatorError
from .locks import finish
from .store import digest
from .item_projection import receipt_digests
from . import native, participants, processes


def view(listener):
    return {key: listener.get(key) for key in ("state", "dormant_at", "dormant_reason", "history_gaps", "writer_profile")}


class Dormancy:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.tasks = {}
        self.cold = []

    def demand(self, runid):
        return self.store.get("writer_start:" + runid, required=False)

    def pending(self, runid):
        return (self.demand(runid) or {}).get("state") in ("pending", "starting", "failed")

    def require_quiet(self, listener):
        """A dormant state is not itself proof that its family is quiescent."""
        proof = listener.get("dormant_proof") or {}
        if (listener["state"] != "dormant" or not proof.get("exit") or not proof.get("read_only")
                or not proof.get("descendants_enumerated")):
            raise CoordinatorError("DORMANT_UNVERIFIED", "Dormant writer needs exit and saved-family evidence")
        if listener.get("history_gaps"):
            raise CoordinatorError("HISTORY_GAP", "Saved writer history differs from received evidence",
                                   {"gaps": listener["history_gaps"]})
        if self.pending(listener["run"]):
            raise CoordinatorError("WRITER_START_PENDING", "Dormant writer has a pending demand start", {"retryable": True}, True)
        actors = [a for a in self.store.all("actor", listener["unit"]) if a.get("listener") == listener["id"]]
        if any(a.get("active_turn") or a["id"] not in proof.get("actors", []) for a in actors):
            raise CoordinatorError("DORMANT_UNVERIFIED", "Dormant proof does not cover every bound actor")
        recipients = {a["id"] for a in actors}
        if any(m.get("recipient") in recipients and m["state"] not in ("entered_context", "acknowledged")
               and m.get("delivery") not in ("cancelled", "turn_ended") for m in self.store.all("message")):
            raise CoordinatorError("PENDING_DELIVERY", "Dormant writer still has mail or a notice to settle")

    def eligible(self, listener):
        from .recovery import startup_obligation
        from .writer import TERMINAL
        run = self.store.get(listener["run"])
        parking = (listener.get("park") or {}).get("step") in ("unloading", "postverify", "stopping", "family_exit")
        return ((run["state"] in TERMINAL or parking) and listener.get("root_thread") and not self.pending(run["id"])
                and bool(run.get("workflow") or run.get("task") or run.get("production_started") or run.get("phases"))
                and not startup_obligation(self.store, run["id"]) and (parking or not self.daemon.writer_owed(listener["unit"])))

    async def received(self, listener, actors):
        """One streamed journal pass per incarnation, indexed by completed item.

        Keep identities and digests, not native payloads. Each page releases its
        decoded items before yielding; concurrent readers retain the existing
        recovery capacity bound. Verified per-thread prefixes are skipped.
        """
        began = time.monotonic()
        end = self.store.db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
        after = {actor["thread"]: (self.daemon.history.checkpoint(actor) or {}).get("received_verified", 0)
                 for actor in actors}
        receipts = {thread: {} for thread in after}
        costs = {thread: {"after": value, "through": end, "items": 0, "pages": 0, "bytes": 0, "gaps": []}
                 for thread, value in after.items()}
        incarnation, passes, rows_seen, index_bytes = listener, 0, 0, 0
        while incarnation:
            cursor = self.store.db.execute("SELECT seq,data FROM events WHERE subject=? AND kind='native' "
                "AND seq>? AND seq<=? AND json_extract(data,'$.method')='item/completed' ORDER BY seq",
                (incarnation["id"], min(after.values(), default=end), end))
            passes += 1
            while rows := cursor.fetchmany(25):
                for row in rows:
                    rows_seen += 1
                    params = json.loads(row["data"])["params"]
                    thread = params.get("threadId")
                    if thread not in after or row["seq"] <= after[thread]:
                        continue
                    turn, ident = params["turnId"], params["item"]["id"]
                    value = (row["seq"], receipt_digests(params["item"]))
                    wanted = receipts[thread].setdefault(turn, {})
                    # The newest receipt wins if a reconnect repeated an item.
                    if ident not in wanted or wanted[ident][0] < row["seq"]:
                        wanted[ident] = value
                    costs[thread]["bytes"] += len(row["data"].encode())
                    index_bytes += len(thread) + len(turn) + len(ident) + 64 + 8
                await asyncio.sleep(0)
            incarnation = self.store.get(incarnation["replaces"]) if incarnation.get("replaces") else None
        self.store.event("history/received_indexed", listener["id"],
                         {"passes": passes, "rows": rows_seen, "threads": len(after), "through": end,
                          "identity_digest_bytes": index_bytes, "seconds": round(time.monotonic() - began, 6)})
        return receipts, costs

    async def compare(self, actor, connection, expected, cost, *, pending=False):
        """Compare the grouped receipt suffix with saved items, never a loaded thread."""
        began = time.monotonic()
        for turn, wanted in expected.items():
            seen = {}
            try:
                async for page in native.page(connection, "thread/items/list", {
                        "threadId": actor["thread"], "turnId": turn, "sortDirection": "asc", "limit": 25}):
                    cost["pages"] += 1
                    for entry in page["data"]:
                        item = entry["item"]
                        if item["id"] in wanted:
                            seen[item["id"]] = digest(item)
            except CoordinatorError as exc:
                if exc.code != "NATIVE_RPC":
                    raise
                turns = set()
                async for page in native.page(connection, "thread/turns/list", {
                        "threadId": actor["thread"], "itemsView": "notLoaded", "limit": 25}):
                    cost["pages"] += 1
                    turns.update(value["id"] for value in page["data"])
                if turn in turns:
                    raise
            for ident, (sequence, received) in wanted.items():
                cost["items"] += 1
                if seen.get(ident) in (received if isinstance(received, list) else [received]):
                    continue
                if pending:
                    raise CoordinatorError("PERSISTENCE_PENDING", "Received item is not yet saved in its native representation",
                                           {"thread": actor["thread"], "turn": turn, "item": ident, "retryable": True}, True)
                gap = {"code": "HISTORY_GAP", "thread": actor["thread"], "turn": turn, "item": ident,
                       "sequence": sequence, "received": received, "saved": seen.get(ident), "run": actor["run"]}
                key = "history_gap:" + actor["id"] + ":" + digest([turn, ident, received])
                if not self.store.get(key, required=False):
                    with self.store.transaction():
                        self.store.put("history_gap", key, gap, actor["run"])
                        self.store.event("history/gap", actor["run"], gap)
                cost["gaps"].append(key)
        cost["seconds"] = round(time.monotonic() - began, 6)
        self.store.event("history/received_compared", actor["id"], cost)
        return cost

    async def recover(self, listener):
        from .recovery import replay_native, reconcile_effects, retire_peer_requests
        daemon, store = self.daemon, self.store
        current = processes.identity(listener["pid"]) if listener.get("pid") else None
        if current and current["birth"] == listener.get("birth") and not current["zombie"]:
            raise CoordinatorError("WRITER_STILL_ALIVE", "Read-only dormancy requires a verified process exit")
        if listener.get("owner_sequence"):
            receipt = await daemon.leases.call("end", payload={"listener": listener["id"]})
            if receipt["family"]["state"] != "GONE":
                raise CoordinatorError("FAMILY_REAPING", "Dormancy requires FAMILY_GONE", operational=True)
        await replay_native(daemon, listener)
        retire_peer_requests(daemon, listener)
        await daemon.settle_native_log(listener["id"])
        root = store.get(listener["actor"])
        threads = []
        async with daemon.history.offline(root) as connection:
            # Discover saved descendants, without loading any thread.
            for archived in (False, True):
                async for page in native.page(connection, "thread/list", {
                        "ancestorThreadId": listener["root_thread"], "archived": archived}):
                    for entry in page["data"]:
                        await daemon.resolve_actor(listener, entry["id"], connection=connection)
            actors = [a for a in store.all("actor", listener["unit"]) if a["kind"] != "lead"]
            receipts, costs = await self.received(listener, actors)
            for actor in actors:
                daemon.history.readers[actor["id"]] = connection
                try:
                    comparison = await self.compare(actor, connection, receipts[actor["thread"]], costs[actor["thread"]])
                    observed = await daemon.history.observe(listener, actor, connection)
                    cp = daemon.history.checkpoint(actor)
                    if not comparison["gaps"]:
                        cp["received_verified"] = comparison["through"]
                        store.save(cp)
                    threads.append(dict(observed, received=comparison))
                finally:
                    if actor["id"] != root["id"]:
                        daemon.history.readers.pop(actor["id"], None)
        await reconcile_effects(daemon, listener, root)
        from .locks import finish_all
        await finish_all(*(daemon.ledger.reconcile(store.get(key), ignore_pending=False, preserve_leads=True,
                           reason="dormant recovery snapshot") for key in participants.checkout_ids(store, root["unit"])))
        daemon.check_native_stop()
        gaps = [g["id"] for g in store.all("history_gap", listener["run"])]
        history = {"completed": time.time(), "threads": threads, "read_only": True}
        with store.transaction():
            store.event("recovery/history", listener["id"], dict(history, run=listener["run"]))
            listener = store.get(listener["id"])
            listener.update(state="dormant", recovery_state="reconciled", dormant_at=time.time(),
                            dormant_reason="exited terminal writer recovered read-only", history_gaps=gaps,
                            dormant_proof={"exit": {"pid": listener.get("pid"), "birth": listener.get("birth"),
                                                   "observed": time.time()}, "read_only": True,
                                           "descendants_enumerated": True, "actors": [a["id"] for a in actors]})
            for key in ("recovery_error", "recovery_error_details", "recovery_retryable", "recovery_retries", "retry_at"):
                listener.pop(key, None)
            store.save(listener)
            store.event("listener/dormant", listener["id"], {"run": listener["run"], "when": listener["dormant_at"],
                        "why": listener["dormant_reason"], "history_gaps": gaps})

        # The streamed comparison also covered every predecessor's receipts.
        previous = store.get(listener.get("replaces"), required=False)
        while previous:
            if processes.exit_evidence(previous):
                previous.update(state="dormant", dormant_proof=listener["dormant_proof"],
                                dormant_at=time.time(), dormant_reason="verified predecessor of dormant writer")
                store.save(previous)
            previous = store.get(previous.get("replaces"), required=False)
        daemon.retention.request(listener["id"])

    async def start(self, runid, reason):
        """Persist intent before launch. All callers join the same task and RPC bound."""
        from .recovery import RECOVERY_WAIT_S
        daemon, store = self.daemon, self.store
        daemon.check_native_stop()
        run = store.get(runid)
        listener = store.get(store.get(run["root_actor"])["listener"])
        parking = daemon.parking.tasks.get(listener["id"])
        if parking is not None and listener["state"] == "parking":
            await asyncio.shield(parking)
            listener = store.get(store.get(run["root_actor"])["listener"])
        if listener["state"] != "dormant" and not listener.get("park_resume_required") and not self.pending(runid):
            return
        record = self.demand(runid)
        restoring = reason == "recovery" and self.pending(runid)
        generation = record.get("cancel_generation", run.get("cancel_generation", 0)) if restoring else run.get("cancel_generation", 0)
        resume_cancelled = (record.get("resume_cancelled", False) if restoring else
                            reason == "resume" and run["state"] == "cancelled")
        if (run.get("cancel_generation", 0) != generation or
                run.get("cancel_requested") and not resume_cancelled):
            if record and self.pending(runid):
                record.update(state="cancelled", cancelled=time.time())
                store.save(record)
                store.event("writer/demand_cancelled", runid, {"demand": record["id"]})
            raise CoordinatorError("RUN_CANCELLED", "A cancelled demand does not start a writer")
        if store.get(run["unit"])["state"] != "open":
            raise CoordinatorError("UNIT_FROZEN", "Reopen a sealed unit before resuming; published units cannot resume")
        record = record or {"id": "writer_start:" + runid, "state": "pending", "reasons": [], "attempts": 0}
        if reason not in record["reasons"]:
            record["reasons"].append(reason)
        task = self.tasks.get(runid)
        if task is None:
            record.update(state="pending", cancel_generation=generation, resume_cancelled=resume_cancelled)
        record.update(run=runid, requested=time.time())
        store.put("writer_start", record["id"], {k: v for k, v in record.items() if not k.startswith("_") and k != "id"}, runid)
        if task is None:
            store.event("writer/demand", runid, {"reason": reason, "demand": record["id"]})
            task = daemon.background(self._start(runid, listener, generation, resume_cancelled))
            self.tasks[runid] = task
            task.add_done_callback(lambda done: self.tasks.pop(runid, None))
        try:
            await asyncio.wait_for(asyncio.shield(task), RECOVERY_WAIT_S)
        except TimeoutError as exc:
            raise CoordinatorError("WRITER_START_PENDING", "Writer start is still owned; retry to join it",
                                   {"retryable": True, "run": runid}, True) from exc

    async def _start(self, runid, original, generation, resume_cancelled):
        async with self.daemon.parking.locks.hold(runid):
            return await self._start_locked(runid, original, generation, resume_cancelled)

    async def _start_locked(self, runid, original, generation, resume_cancelled):
        from .recovery import reconcile_listener, ensure_running
        daemon, store = self.daemon, self.store
        record = self.demand(runid)
        record.update(state="starting", attempts=record["attempts"] + 1, generation=store.generation)
        store.save(record)
        began = time.monotonic()
        bindings = [a for a in store.all("actor", original["unit"]) if a["kind"] != "lead"]
        try:
            ensure_running(daemon)
            run = store.get(runid)
            if run.get("cancel_generation", 0) != generation or (run.get("cancel_requested") and not resume_cancelled):
                raise CoordinatorError("RUN_CANCELLED", "The run was cancelled before its demand start")
            if store.get(run["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "The unit closed before its demand start")
            await reconcile_listener(daemon, original, advance=False, demand=True)
            run = store.get(runid)
            if run.get("cancel_generation", 0) != generation:
                raise CoordinatorError("RUN_CANCELLED", "The run was cancelled during its demand start")
            ensure_running(daemon)
        except Exception as exc:
            record = self.demand(runid)
            record.update(state="cancelled" if getattr(exc, "code", None) in ("RUN_CANCELLED", "UNIT_FROZEN") else "failed",
                          error=getattr(exc, "code", type(exc).__name__), failed=time.time(),
                          failures=record.get("failures", 0) + 1)
            store.save(record)
            store.event("writer/start_failed", runid, {"code": record["error"], "demand": record["id"], "retryable": record["state"] == "failed"})
            # Only this attempt's unsubmitted replacement can be retired here.
            current = store.get(store.get(run["root_actor"])["listener"])
            owned = (current["id"] != original["id"] and current.get("replaces") == original["id"]
                     and current.get("run") == runid and self.tasks.get(runid) is asyncio.current_task())
            if owned:
                await finish(daemon.stop_listener(current, kill=True))
            with store.transaction():
                for actor in bindings:
                    current_actor = store.get(actor["id"])
                    if current_actor.get("listener") != original["id"] and not (owned and current_actor.get("listener") == current["id"]):
                        continue  # A newer binding belongs to its own attempt.
                    current_actor.update(listener=original["id"], active_turn=None, state=actor["state"])
                    store.save(current_actor)
                old = store.get(original["id"])
                if not old.get("replaced_by") or owned and old["replaced_by"] == current["id"]:
                    # A failed park can leave the original server alive with
                    # unloaded threads. It still needs a demand resume; failure
                    # is not exit evidence and cannot make that server dormant.
                    old.update(state="dormant" if original["state"] == "dormant" else "connected",
                               recovery_state="reconciled")
                    old.pop("replaced_by", None)
                    store.save(old)
            if record["state"] == "cancelled":
                raise
            if record["error"] in ("RESOURCE_PAUSED", "RESOURCE_EXHAUSTED"):
                raise
            raise CoordinatorError("WRITER_START_FAILED", "Writer start failed; the dormant demand is retained for retry",
                                   {"retryable": True, "run": runid, "cause": record["error"]}, True) from exc
        record = self.demand(runid)
        seconds = time.monotonic() - began
        record.update(state="complete", completed=time.time(), seconds=seconds,
                      listener=store.get(run["root_actor"])["listener"])
        with store.transaction():
            store.save(record)
            store.event("writer/started", runid, {"demand": record["id"], "seconds": seconds, "listener": record["listener"]})
        self.cold.append(seconds)
        daemon.parking.touch(record["listener"])

    def status(self):
        listeners = [l for l in self.store.all("listener") if l.get("root_thread")
                     and self.store.get(l["actor"]).get("listener") == l["id"]]
        starts = list(self.store.all("writer_start"))
        values = sorted(self.cold)
        return {**self.daemon.parking.status(), "dormant": sum(l["state"] == "dormant" for l in listeners),
                "running": sum(l["state"] == "connected" for l in listeners),
                "history_gaps": self.store.db.execute("SELECT count(*) FROM records WHERE kind='history_gap'").fetchone()[0],
                "demand_starts": sum(r["attempts"] for r in starts),
                "start_failures": sum(r.get("failures", int(r["state"] == "failed")) for r in starts),
                "cold_start_p50": values[len(values) // 2] if values else None, "cold_start_max": max(values, default=None)}
