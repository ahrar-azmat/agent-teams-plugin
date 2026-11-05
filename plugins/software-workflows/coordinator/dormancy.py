"""Exited writers recover without loading threads; explicit demand owns one start."""
import asyncio
from contextlib import nullcontext
import json
import time

from .client import CoordinatorError
from .locks import finish, finish_thread
from .store import digest, read_pass
from .item_projection import PROJECTION_VERSION, receipt_digests, receipt_result
from . import native, participants, processes


def completed_receipt(data):
    message = json.loads(data)
    params = message["params"]
    legacy = message.get("_coordinator_receipt_version") != 2
    item = params["item"]
    evidence = "legacy_serialization_unverified" if legacy and item.get("type") == "mcpToolCall" else "content_difference"
    return (params.get("threadId"), params["turnId"], item["id"],
            receipt_digests(item, receipt_result(data), legacy=legacy), len(data.encode()), evidence)


def unresolved(gap):
    return gap.get("state", "unresolved") == "unresolved"


def view(listener):
    return {**{key: listener.get(key) for key in ("state", "dormant_at", "dormant_reason", "history_gaps", "history_settled", "history_projection", "writer_profile",
                                                "recovery_state", "recovery_error", "recovery_retryable", "recovery_version")},
            "quarantined": listener.get("recovery_state") == "failed"}


class Dormancy:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.tasks = {}
        self.entering = {}
        self.cold = []
        self.counts = None  # (the record writes it was read at, the counts): status()

    def demand(self, runid):
        return self.store.get("writer_start:" + runid, required=False)

    def pending(self, runid):
        return (self.demand(runid) or {}).get("state") in ("pending", "starting", "failed")

    @read_pass(readonly=True)
    def start_due(self, runid):
        """Whether a caller on its own schedule (a waking message, a workflow's advance, startup recovery) may start the
        run's writer now: no failed demand start, or one due by the policy of recovery.retry_due: its backoff has passed,
        and its cause can heal or a matching declared repair remains unused. A resume starts it regardless. Through 2.4.15 a failed
        start was tried again at each message delivery pass, each with a new app-server: on the user's ledger four runs'
        starts were tried 1,072 to 2,788 times, failing OWNER_UNREGISTERED or HOOK_DEFINITIONS every time."""
        from .recovery import gate_reopened
        record = self.demand(runid)
        if not record or record.get("state") != "failed":
            return True
        if record.get("retry_at", 0) > time.time() and not gate_reopened(self.daemon, record):
            return False
        return not self.start_held(runid)

    @read_pass(readonly=True)
    def start_held(self, runid):
        """The same incident/repair gate as startup, maintenance and transport recovery."""
        from .recovery import permanently_failed
        run = self.store.get(runid)
        listener = self.store.get(self.store.get(run["root_actor"])["listener"])
        return permanently_failed(self.daemon, listener)

    def gaps(self, runid):
        return list(self.store.all("history_gap", runid))

    def refresh(self, runid):
        gaps = self.gaps(runid)
        active = [g["id"] for g in gaps if unresolved(g)]
        settled = [g["id"] for g in gaps if g.get("state") == "settled"]
        # The run's incarnations only (a listener's parent is its run): decoding every listener record held the event
        # loop 0.36 s on the user's 4,624-listener ledger (11-02).
        for listener in list(self.store.all("listener", runid)):
            listener.update(history_gaps=active, history_settled=settled)
            self.store.save(listener)
        return active

    def needs_revalidation(self, listener):
        return any(g.get("projection_version", 0) < PROJECTION_VERSION for g in self.gaps(listener["run"]))

    def settle(self, unitid, actorid, ids, reason):
        actor = self.store.get(actorid, required=False)
        if not actor or actor.get("kind") != "lead" or actor.get("unit") != unitid:
            raise CoordinatorError("LEAD_REQUIRED", "History settlement requires a lead attached to this unit")
        if not isinstance(reason, str) or not reason.strip() or not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
            raise CoordinatorError("HISTORY_SETTLEMENT", "Supply explicit gap IDs and a nonblank reason")
        runid = self.store.get(unitid)["run"]
        gaps = [self.store.get(key) for key in dict.fromkeys(ids)]
        if any(g.get("_kind") != "history_gap" or g.get("run") != runid for g in gaps):
            raise CoordinatorError("HISTORY_SCOPE", "Every gap must belong to the selected unit")
        with self.store.transaction():
            for gap in gaps:
                if gap.get("state") in ("matched", "settled"):
                    continue
                observation = digest([gap["thread"], gap["turn"], gap["item"], gap["sequence"], gap["received"], gap.get("saved")])
                gap.update(state="settled", settlement={"reason": reason.strip(), "actor": actorid,
                           "time": time.time(), "observation": observation})
                self.store.save(gap)
                self.store.event("history/settled", gap["id"], {"unit": unitid, **gap["settlement"]})
            self.refresh(runid)
        return {"unit": unitid, "gaps": [self.store.get(g["id"]) for g in gaps]}

    def notice(self, actor, inputs):
        gaps = [g for g in self.gaps(actor["run"]) if g.get("state") != "matched"]
        if not gaps or any(i.get("type") == "text" and i.get("text", "").startswith("C1_HISTORY_GAPS\n") for i in inputs):
            return inputs
        lines = [g["id"] + " (" + g.get("state", "unresolved") + ", thread " + g["thread"] + ")" for g in gaps]
        warning = "C1_HISTORY_GAPS\nSaved history for this writer family has gaps. Do not assume missing history proves an action or its absence.\n" + "\n".join(lines)
        return [{"type": "text", "text": warning}] + inputs

    def require_quiet(self, listener, *, history=True):
        """A dormant state is not itself proof that its family is quiescent."""
        proof = listener.get("dormant_proof") or {}
        if (listener["state"] != "dormant" or not proof.get("exit") or history and
                (not proof.get("read_only") or not proof.get("descendants_enumerated"))):
            raise CoordinatorError("DORMANT_UNVERIFIED", "Dormant writer needs exit and saved-family evidence")
        gaps = [g["id"] for g in self.gaps(listener["run"]) if unresolved(g)]
        if history and gaps:
            raise CoordinatorError("HISTORY_GAP", "Saved writer history differs from received evidence",
                                   {"gaps": gaps})
        if history and self.pending(listener["run"]):
            raise CoordinatorError("WRITER_START_PENDING", "Dormant writer has a pending demand start", {"retryable": True}, True)
        actors = [a for a in self.store.all("actor", listener["unit"]) if a.get("listener") == listener["id"]]
        if history and any(a.get("active_turn") or a["id"] not in proof.get("actors", []) for a in actors):
            raise CoordinatorError("DORMANT_UNVERIFIED", "Dormant proof does not cover every bound actor")
        recipients = {a["id"] for a in actors}
        if history and any(self.daemon.delivery_owed(m) for m in self.store.messages("recipient", recipients)):
            raise CoordinatorError("PENDING_DELIVERY", "Dormant writer still has mail or a notice to settle")

    def eligible(self, listener):
        from .recovery import startup_obligation
        from .writer import TERMINAL
        run = self.store.get(listener["run"])
        parking = (listener.get("park") or {}).get("step") in ("unloading", "postverify", "stopping", "family_exit")
        return ((run["state"] in TERMINAL or parking) and listener.get("root_thread") and not self.pending(run["id"])
                and bool(run.get("workflow") or run.get("task") or run.get("production_started") or run.get("phases"))
                and not startup_obligation(self.store, run["id"]) and (parking or not self.daemon.writer_owed(listener["unit"])))

    async def received(self, listener, actors, *, record=True, check=None):
        """One streamed journal pass per incarnation, indexed by completed item.

        Keep identities and digests, not native payloads. Each page releases its
        decoded items before yielding; concurrent readers retain the existing
        recovery capacity bound. Verified per-thread prefixes are skipped.
        """
        began = time.monotonic()
        from . import archive
        end = archive.highwater(self.store)
        after = {actor["thread"]: (self.daemon.history.checkpoint(actor) or {}).get("received_verified", 0)
                 for actor in actors}
        # Revisit named observations even if an older incarnation advanced its
        # watermark. Settled observations are compared again, never a wildcard.
        for gap in self.gaps(listener["run"]):
            if gap["thread"] in after and gap.get("state") != "matched":
                after[gap["thread"]] = min(after[gap["thread"]], max(0, gap["sequence"] - 1))
        receipts = {thread: {} for thread in after}
        costs = {thread: {"after": value, "through": end, "items": 0, "pages": 0, "bytes": 0, "gaps": []}
                 for thread, value in after.items()}
        incarnation, passes, rows_seen, index_bytes = listener, 0, 0, 0
        while incarnation:
            passes += 1
            async for row in archive.events(self.store, [incarnation["id"]], min(after.values(), default=end),
                                             kind="native", method="item/completed", through=end):
                rows_seen += 1
                thread, turn, ident, digests, size, evidence = await finish_thread(completed_receipt, row["data"])
                if thread not in after or row["seq"] <= after[thread]:
                    continue
                value = (row["seq"], digests, evidence)
                wanted = receipts[thread].setdefault(turn, {})
                # The newest receipt wins if a reconnect repeated an item.
                if ident not in wanted or wanted[ident][0] < row["seq"]:
                    wanted[ident] = value
                costs[thread]["bytes"] += size
                index_bytes += len(thread) + len(turn) + len(ident) + 64 + 8
                if check:
                    check()
                await asyncio.sleep(0)
                if check:
                    check()
            incarnation = self.store.get(incarnation["replaces"]) if incarnation.get("replaces") else None
        if record:
            self.store.event("history/received_indexed", listener["id"],
                         {"passes": passes, "rows": rows_seen, "threads": len(after), "through": end,
                          "identity_digest_bytes": index_bytes, "seconds": round(time.monotonic() - began, 6)})
        return receipts, costs

    async def compare(self, actor, connection, expected, cost, *, pending=False, record=True):
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
            for ident, (sequence, received, evidence) in wanted.items():
                cost["items"] += 1
                previous = [g for g in self.gaps(actor["run"]) if g["thread"] == actor["thread"]
                            and g["turn"] == turn and g["item"] == ident]
                matches = seen.get(ident) in (received if isinstance(received, list) else [received])
                if matches and not previous:
                    continue
                # Version every observation we revisited, including a changed or
                # missing saved item. Commit this with its outcome so a crash cannot
                # mark the upgrade done before recording the new discrepancy.
                with self.store.transaction() if record else nullcontext():
                    if record:
                        for old in previous:
                            if old.get("projection_version") != PROJECTION_VERSION:
                                old["projection_version"] = PROJECTION_VERSION
                                self.store.save(old)
                    if matches:
                        if record:
                            for old in previous:
                                if old.get("state") != "matched":
                                    old.update(state="matched", matched_at=time.time())
                                    self.store.save(old)
                                    self.store.event("history/matched", old["id"], {"sequence": sequence, "saved": seen[ident]})
                        continue
                    # Same receipt and saved digest retains the lead's decision,
                    # even when a new projection adds another exact alternative.
                    settled = next((g for g in previous if g.get("state") == "settled" and
                                    g["sequence"] == sequence and g.get("saved") == seen.get(ident)), None)
                    if settled:
                        cost.setdefault("unverified", []).append(settled["id"])
                        continue
                    if pending:
                        raise CoordinatorError("PERSISTENCE_PENDING", "Received item is not yet saved in its native representation",
                                               {"thread": actor["thread"], "turn": turn, "item": ident, "retryable": True}, True)
                    gap = {"code": "HISTORY_GAP", "thread": actor["thread"], "turn": turn, "item": ident,
                           "sequence": sequence, "received": received, "saved": seen.get(ident), "run": actor["run"],
                           "state": "unresolved", "projection_version": PROJECTION_VERSION,
                           "evidence": evidence if seen.get(ident) is not None else "saved_item_missing"}
                    old = next((g for g in previous if unresolved(g) and g["sequence"] == sequence and g.get("saved") == seen.get(ident)), None)
                    key = old["id"] if old else "history_gap:" + actor["id"] + ":" + digest([turn, ident, sequence, received, seen.get(ident)])
                    if record and old:
                        old.update(evidence=gap["evidence"])
                        self.store.save(old)
                    if record and not self.store.get(key, required=False):
                        self.store.put("history_gap", key, gap, actor["run"])
                        self.store.event("history/gap", actor["run"], gap)
                    cost["gaps"].append(key)
        cost["seconds"] = round(time.monotonic() - began, 6)
        if record:
            self.store.event("history/received_compared", actor["id"], cost)
        return cost

    async def recover(self, listener):
        from .recovery import replay_native, reconcile_effects, retire_peer_requests
        daemon, store = self.daemon, self.store
        current = processes.identity(listener["pid"]) if listener.get("pid") else None
        if current and current["birth"] == listener.get("birth") and not current["zombie"]:
            raise CoordinatorError("WRITER_STILL_ALIVE", "Read-only dormancy requires a verified process exit")
        receipt = None
        if listener.get("owner_sequence"):
            receipt = await daemon.family_receipt(listener)
            if receipt["state"] != "GONE":
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
                    if not comparison["gaps"] and not comparison.get("unverified"):
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
        gaps = self.refresh(listener["run"])
        history = {"completed": time.time(), "threads": threads, "read_only": True}
        with store.transaction():
            store.event("recovery/history", listener["id"], dict(history, run=listener["run"]))
            listener = store.get(listener["id"])
            consumed = receipt is not None and listener.get("family_gone") is None
            if receipt is not None:
                listener["family_gone"] = receipt["exit_evidence"]
            listener.update(state="dormant", recovery_state="reconciled", dormant_at=time.time(),
                            dormant_reason="exited terminal writer recovered read-only", history_gaps=gaps,
                            history_projection=PROJECTION_VERSION,
                            dormant_proof={"exit": {"pid": listener.get("pid"), "birth": listener.get("birth"),
                                                   "observed": time.time()}, "read_only": True,
                                           "descendants_enumerated": True, "actors": [a["id"] for a in actors]})
            for key in ("recovery_error", "recovery_error_details", "recovery_retryable", "recovery_version", "recovery_retries", "retry_at"):
                listener.pop(key, None)
            store.save(listener)
            store.event("listener/dormant", listener["id"], {"run": listener["run"], "when": listener["dormant_at"],
                        "why": listener["dormant_reason"], "history_gaps": gaps})
        if consumed:  # durable with the dormant record now: the owner may drop its copy (2.4.12)
            await daemon.forget_families([listener["id"]])

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
        # Register before waiting for a park: until its first unsubscribe it
        # must yield to this demand. Cancellation removes only this caller.
        self.entering[runid] = self.entering.get(runid, 0) + 1
        try:
            return await self._requested_start(runid, reason)
        finally:
            self.entering[runid] -= 1
            if not self.entering[runid]:
                del self.entering[runid]

    async def _requested_start(self, runid, reason):
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
        if reason != "resume" and self.tasks.get(runid) is None and not self.start_due(runid):
            # Refused before any write: callers on a schedule ask again each pass (start_due).
            raise CoordinatorError("WRITER_START_FAILED", "The writer's last start failed; it is tried again when its retry is due",
                                   {"retryable": bool(record.get("retryable", True)), "run": runid, "cause": record.get("error"),
                                    "retry_at": record.get("retry_at")}, True)
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
            task = daemon.background(self._start(runid, listener, generation, resume_cancelled, reason == "resume"))
            self.tasks[runid] = task
            task.add_done_callback(lambda done: self.tasks.pop(runid, None))
        try:
            await asyncio.wait_for(asyncio.shield(task), RECOVERY_WAIT_S)
        except TimeoutError as exc:
            # waited_seconds: what the server's retry measures a further attempt by, as for WRITER_RECOVERING. A cold
            # start outlasts one wait: every resume of a completed write run met this (Sales_Profiler, 11-05, 5 of 5).
            raise CoordinatorError("WRITER_START_PENDING", "Writer start is still owned; retry to join it",
                                   {"retryable": True, "run": runid, "waited_seconds": RECOVERY_WAIT_S}, True) from exc

    async def _start(self, runid, original, generation, resume_cancelled, explicit=False):
        async with self.daemon.parking.locks.hold(runid):
            return await self._start_locked(runid, original, generation, resume_cancelled, explicit)

    async def _start_locked(self, runid, original, generation, resume_cancelled, explicit=False):
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
            await reconcile_listener(daemon, original, advance=False, demand=True, scheduled=not explicit)
            run = store.get(runid)
            if run.get("cancel_generation", 0) != generation:
                raise CoordinatorError("RUN_CANCELLED", "The run was cancelled during its demand start")
            ensure_running(daemon)
        except Exception as exc:
            from .recovery import failure_info, memory_gated, retryable_failure, retry_delay
            from .runtime import plugin_version
            record = self.demand(runid)
            code = getattr(exc, "code", type(exc).__name__)
            if code == "DAEMON_DRAINING":
                # A stop's own refusal fails nothing, as for a reconcile (recovery.reconcile_failed): the start stays
                # pending for the next daemon's recovery. Classified, it would hold the start until a resume.
                record.update(state="pending")
            else:
                record.update(state="cancelled" if code in ("RUN_CANCELLED", "UNIT_FROZEN") else "failed", error=code,
                              failed=time.time(), failures=record.get("failures", 0) + 1)
            if record["state"] == "failed":
                # The one retry policy (start_due), backing off over consecutive failures, a resource refusal too: one
                # the memory gate made is due again once the gate would admit it (recovery.gate_reopened).
                streak, info = record.get("failure_streak", 0) + 1, failure_info(exc)
                record.update(failure_streak=streak, retryable=retryable_failure(info), version=plugin_version(),
                              failure=info,
                              resource="memory" if memory_gated(info) else info.get("resource"),
                              retry_at=time.time() + retry_delay(streak))
                from .repairs import record_failure
                record_failure(store, original, info, record["retryable"])
            store.save(record)
            store.event("writer/start_failed", runid, {"code": code, "demand": record["id"], "retryable":
                                                       record["state"] == "pending" or record["state"] == "failed" and record["retryable"],
                                                       "retry_at": record.get("retry_at")})
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
            if record["state"] != "failed" or code in ("RESOURCE_PAUSED", "RESOURCE_EXHAUSTED"):
                raise
            raise CoordinatorError("WRITER_START_FAILED", "Writer start failed; the dormant demand is retained for retry",
                                   {"retryable": record["retryable"], "run": runid, "cause": record["error"]}, True) from exc
        record = self.demand(runid)
        seconds = time.monotonic() - began
        record.update(state="complete", completed=time.time(), seconds=seconds, failure_streak=0,
                      listener=store.get(run["root_actor"])["listener"])
        with store.transaction():
            store.save(record)
            store.event("writer/started", runid, {"demand": record["id"], "seconds": seconds, "listener": record["listener"]})
        self.cold.append(seconds)
        daemon.parking.touch(record["listener"])

    def status(self):
        """Brief daemon/status, which the Office asks for every 2 s. Each writer's current listener is found through
        its actor, and the counts are read again only after a listener, actor, writer start or history gap was
        written: through 2.4.14 every call decoded every listener record (10-29: 3,417, 40 MB; 0.18-0.41 s of the
        event loop per call, 13.5 ms this way)."""
        key = tuple(self.store.puts[kind] for kind in ("listener", "actor", "writer_start", "history_gap"))
        if self.counts is None or self.counts[0] != key:
            states = dict(self.store.db.execute(
                "SELECT json_extract(l.data,'$.state'), count(*) FROM records a JOIN records l "
                "ON l.id=json_extract(a.data,'$.listener') WHERE a.kind='actor' AND l.kind='listener' AND "
                "json_extract(l.data,'$.actor')=a.id AND coalesce(json_extract(l.data,'$.root_thread'),'')!='' GROUP BY 1"))
            starts = list(self.store.all("writer_start"))
            self.counts = (key, {
                "dormant": states.get("dormant", 0), "running": states.get("connected", 0),
                "history_gaps": self.store.db.execute("SELECT count(*) FROM records WHERE kind='history_gap' "
                    "AND coalesce(json_extract(data,'$.state'),'unresolved')='unresolved'").fetchone()[0],
                "history_settled": self.store.db.execute("SELECT count(*) FROM records WHERE kind='history_gap' "
                    "AND json_extract(data,'$.state')='settled'").fetchone()[0],
                "demand_starts": sum(r["attempts"] for r in starts),
                "start_failures": sum(r.get("failures", int(r["state"] == "failed")) for r in starts)})
        values = sorted(self.cold)
        return {**self.daemon.parking.status(), **self.counts[1],
                "cold_start_p50": values[len(values) // 2] if values else None, "cold_start_max": max(values, default=None)}
