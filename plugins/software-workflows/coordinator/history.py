"""Durably applied native history, shared by recovery and input reconciliation.

Lock order is run reconciliation, a capacity lane, then this observer's actor lock.
A trusted observer releases its lane and actor lock before escalating to bootstrap.
The observer never reconciles a listener, delivers mail or advances a workflow while
holding its lock. Item effects precede item anchors; turn effects precede turn cursors.
Unfinished items retain durable obligations while the applied tail covers successors.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import os
import time

from .locks import KeyedLocks, finish, finish_thread
from . import native, participants, processes
from .client import CoordinatorError
from .store import digest, uid, WINDOW_ACTIVE


PAGE_SIZE = 25
VERSION = 1
# 2026-10-23, 27 real 0.163.0 writers, ~21,160 events/min (57.5% text
# deltas): short-lane limits 1/2 gave lead Pre p99 643/583 ms and writer Pre
# p99 723/512 ms, with no warnings. Use 2, the highest measured candidate,
# leaving at least 1.7x headroom below the 1 s target. Separately, with four 151 MB
# bootstraps active, delivery/settlement/catch-up took 9/9/55 ms at 2 versus
# 2.8 s when they shared bootstrap slots. Full scans still use recovery capacity.
# These fixture results size this host's default, not a universal latency bound.
HISTORY_CONCURRENCY = 2
_capacity = os.environ.get("SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY")
if _capacity is not None:
    if not _capacity.isascii() or not _capacity.isdecimal() or int(_capacity) < 1:
        raise CoordinatorError("CONFIG", "SOFTWARE_WORKFLOWS_HISTORY_CONCURRENCY must be a positive integer")
    HISTORY_CONCURRENCY = int(_capacity)

EFFECT_ITEMS = {"fileChange", "commandExecution", "mcpToolCall"}


def identity(thread):
    """Native reversion replaces its rollout path. Also detect replacement/truncation.

    Only stat the native-provided path; visible history always comes from native APIs.
    Compression changes the physical file too, conservatively losing the old anchor.
    """
    path = thread.get("path")
    result = {"thread": thread["id"], "mode": thread.get("historyMode"), "path": path}
    if path:
        for name in (path, path + ".zst"):
            try:
                info = Path(name).stat()
                result.update(file=[info.st_dev, info.st_ino], size=info.st_size)
                break
            except FileNotFoundError:
                continue
    return result


def same_identity(before, after):
    return (all(before.get(k) == after.get(k) for k in ("thread", "mode", "path", "file"))
            and after.get("size", 0) >= before.get("size", 0))


def rejected_anchor(exc):
    """A refused cursor-bearing read loses trust, independent of native's wording.

    Bootstrap retries once without that stored position. Cursor-free refusals and
    transport uncertainty still raise; neither can establish a valid history prefix.
    """
    return (getattr(exc, "code", None) == "NATIVE_RPC"
            and exc.details.get("method") in ("thread/turns/list", "thread/items/list")
            and exc.details.get("history_cursor") is True)


class BootstrapRequired(Exception):
    """Internal handoff: release the short lane before waiting for bootstrap capacity."""


class History:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.locks = KeyedLocks()
        self.reader_locks = KeyedLocks()
        self.scheduled, self.dirty = {}, set()
        self.readers = {}
        self.active = 0
        self.incremental_limit = HISTORY_CONCURRENCY
        self.incremental_slots = asyncio.Semaphore(HISTORY_CONCURRENCY)
        self.incremental_holders = set()
        self.incremental_active = self.incremental_queued = 0

    @staticmethod
    def key(actor):
        return "native_history:" + actor["id"]

    @staticmethod
    def turn_key(actor, turn):
        return "native_turn:" + actor["id"] + ":" + turn

    def checkpoint(self, actor):
        return self.store.get(self.key(actor), required=False)

    def invalidate(self, actor, reason, details=None):
        old = self.checkpoint(actor)
        if old:
            old["invalid"] = reason
            old["invalid_details"] = details or {}
            self.store.save(old)
        self.store.event("history/invalidated", actor["id"], {"reason": reason})

    def turn(self, actor, turn):
        value = self.store.get(self.turn_key(actor, turn), required=False)
        cp = self.checkpoint(actor)
        return value if value and cp and value.get("epoch") == cp.get("epoch") and not cp.get("invalid") else None

    def input(self, actor, client):
        value = self.store.get("native_input:" + actor["id"] + ":" + digest(client), required=False)
        cp = self.checkpoint(actor)
        if value and cp and not cp.get("invalid") and value.get("epoch") == cp.get("epoch"):
            return self.turn(actor, value["turn"])

    def obligations(self, actor, turn=None):
        """Native does not persist ItemStarted. Admission/journal evidence pins a
        turn even when its running call is entirely absent from the item listing.
        Query live operations only; completed operation history can be very large.
        """
        from .daemon import returned_failure
        query = ("SELECT * FROM records INDEXED BY window_operation WHERE " + WINDOW_ACTIVE["operation"]
                 + " AND json_extract(data,'$.native.thread')=? AND parent=? ORDER BY id")
        return [operation for operation in self.store.selected(query, (actor["thread"], actor["unit"]))
                if operation.get("native", {}).get("turn") and not operation.get("parent_operation")
                and (turn is None or operation["native"]["turn"] == turn)
                and not operation.get("completed_item") and not returned_failure(operation)]

    async def closure_evidence(self, operations):
        """A handler/end-of-dispatch receipt or an executor exit warrants a full
        read of this turn if its applied tail did not find the expected completion.
        A finished turn alone never proves that its code-mode cells have finished.
        """
        result, executors = {}, {}
        for operation in operations:
            listenerid = operation["native"].get("listener")
            if listenerid not in executors:
                listener = self.store.get(listenerid, required=False) or {}
                current = await finish_thread(processes.identity, listener["pid"]) if listener.get("pid") else None
                executors[listenerid] = bool(current and current["birth"] == listener.get("birth") and not current["zombie"])
            evidence = {key: operation.get(key) for key in ("returned", "result_ready", "post_received")}
            evidence["executor_ended"] = not executors[listenerid]
            if any(evidence.values()):
                result[operation["id"]] = digest(evidence)
        return result

    def valid(self, listener, actor, connection, epoch):
        self.daemon.check_native_stop()
        connection.check_intake()
        current = self.store.get(actor["id"])
        cp = self.checkpoint(actor)
        applied = self.store.get("native_applied:" + listener["id"], required=False) or {}
        if applied.get("failed") or applied.get("pending"):
            raise CoordinatorError("HISTORY_APPLY_PENDING", "Native notification application must be reconciled first", operational=True)
        if (current.get("thread") != actor["thread"] or current.get("listener") != listener["id"]
                or (self.daemon.connections.get(listener["id"]) is not connection
                    and self.readers.get(actor["id"]) is not connection)
                or connection.closed or not cp or cp.get("epoch") != epoch or cp.get("invalid")):
            raise CoordinatorError("HISTORY_CHANGED", "History binding changed during observation", operational=True)

    @asynccontextmanager
    async def incremental_capacity(self):
        task = asyncio.current_task()
        if task in self.incremental_holders or task in self.daemon.recovery_holders:
            yield
            return
        self.incremental_queued += 1
        try:
            await self.incremental_slots.acquire()
        finally:
            self.incremental_queued -= 1
        self.incremental_holders.add(task)
        self.incremental_active += 1
        try:
            yield
        finally:
            self.incremental_active -= 1
            self.incremental_holders.remove(task)
            self.incremental_slots.release()

    async def observe(self, listener, actor, connection=None):
        self.daemon.check_native_stop()
        connection = connection or self.daemon.listener_connection(listener)
        connection.check_intake()
        from .recovery import capacity
        cp = self.checkpoint(actor) or {}
        bootstrap = not cp.get("observed") or bool(cp.get("invalid"))
        self.active += 1
        try:
            while True:
                lane = capacity(self.daemon) if bootstrap else self.incremental_capacity()
                async with lane, self.locks.hold(actor["id"]):
                    try:
                        return await self._observe(listener, actor, connection, bootstrap=bootstrap)
                    except BootstrapRequired:
                        # Never wait for the full lane while holding the actor lock
                        # or a short-lane slot: a full recovery may need this actor.
                        bootstrap = True
        finally:
            self.active -= 1

    @asynccontextmanager
    async def offline(self, actor):
        """Read saved history without resuming a thread or dispatching its queue.

        The temporary listener has no root_thread and never replaces the writer's
        binding. Normal listener ownership/stop accounting also covers this process
        if the daemon dies. Only reader startup takes short-observation capacity;
        an untrusted history scan takes bootstrap capacity after startup releases it.
        """
        original = self.store.get(actor["listener"])
        self.active += 1
        reader = None
        try:
            async with self.reader_locks.hold(actor["id"]):
                async with self.incremental_capacity():
                    reader = await self.daemon.offline_listener(actor, original)
                connection = self.daemon.connections[reader["id"]]
                self.readers[actor["id"]] = connection
                try:
                    yield connection
                finally:
                    self.readers.pop(actor["id"], None)
                    # The native reader never loaded the thread. Ending it cannot
                    # interrupt that thread or start one of its durable submissions.
                    await finish(self.daemon.stop_listener(reader, kill=True))
        finally:
            self.active -= 1

    async def _observe(self, listener, actor, connection, bootstrap=False):
        began = time.monotonic()
        refusal = {}
        for attempt in range(2):
            metadata = (await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False}))["thread"]
            if metadata.get("historyMode") != "paginated":
                raise CoordinatorError("NATIVE_HISTORY_UNSUPPORTED", "Incremental recovery requires native paginated history",
                                       {"mode": metadata.get("historyMode"), "thread": actor["thread"]})
            observed_identity = await finish_thread(identity, metadata)
            self.daemon.check_native_stop()
            connection.check_intake()
            cp = self.checkpoint(actor)
            reason = None
            if not cp:
                reason = "missing"
            elif (cp.get("version") != VERSION or not isinstance(cp.get("pending"), dict)
                    or not isinstance(cp.get("epoch"), str)
                    or cp.get("cursor") is not None and not isinstance(cp["cursor"], str)
                    or bool(cp.get("cursor")) != bool(cp.get("boundary"))
                    or not isinstance(cp.get("identity"), dict)
                    or any(not isinstance(v, dict) or not isinstance(v.get("boundary"), str)
                           or v.get("cursor") is not None and not isinstance(v["cursor"], str)
                           for v in cp.get("pending", {}).values())):
                reason = "format"
            elif cp.get("invalid"):
                reason = cp["invalid"]
                refusal = cp.get("invalid_details", {})
            elif not same_identity(cp.get("identity", {}), observed_identity):
                reason = "native_identity"
            if reason:
                if not bootstrap:
                    raise BootstrapRequired()
                cp = self.store.put("native_history", self.key(actor),
                                    {"version": VERSION, "epoch": uid("history"), "identity": observed_identity,
                                     "cursor": None, "boundary": None, "pending": {}}, actor["id"])
                self.store.event("history/fallback", actor["id"], {"reason": reason, **refusal})
            cp = {k: v for k, v in cp.items() if not k.startswith("_") and k != "id"}
            try:
                result = await self.scan(listener, actor, connection, cp, observed_identity)
                result["seconds"] = round(time.monotonic() - began, 6)
                self.store.event("history/observed", actor["id"], result)
                return result
            except CoordinatorError as exc:
                if attempt or not (rejected_anchor(exc) or exc.code == "HISTORY_ANCHOR"):
                    raise
                if rejected_anchor(exc):
                    refusal = {"native_code": exc.details.get("native_code"), "message": str(exc),
                               "method": exc.details["method"]}
                self.invalidate(actor, "rejected_anchor" if rejected_anchor(exc) else "boundary_mismatch", refusal)
                if not bootstrap:
                    raise BootstrapRequired() from exc
        raise AssertionError("history fallback did not finish")

    async def scan(self, listener, actor, connection, cp, observed_identity):
        epoch = cp["epoch"]
        summary = {"actor": actor["id"], "thread": actor["thread"], "turns": 0, "items": 0,
                   "pages": 0, "items_complete": True, "incremental": bool(cp.get("cursor"))}
        # Snapshot the candidate *before* scanning. A turn created after this request cannot
        # move the checkpoint beyond effects this scan has actually applied.
        tail = await connection.call("thread/turns/list", {"threadId": actor["thread"], "itemsView": "notLoaded",
                                                            "sortDirection": "desc", "limit": 1})
        candidate = tail["data"][0]["id"] if tail["data"] else None
        covered, pending = set(), dict(cp["pending"])

        # An admission can arrive after a finished turn was scanned or its cursor
        # saved. Rebuild pins from durable operations, not just native item listings.
        # Older observer records lack positions: locate the turn once with paged
        # metadata, retaining its existing applied item tail.
        for operation in self.obligations(actor):
            turnid = operation["native"]["turn"]
            prior = self.turn(actor, turnid) or {}
            pending.setdefault(turnid, prior.get("position") or {"cursor": None, "boundary": ""})

        async def visit(turn, cursor, boundary):
            self.valid(listener, actor, connection, epoch)
            position = {"cursor": cursor, "boundary": boundary}
            result = await self.items(listener, actor, connection, turn, epoch, summary, position)
            covered.add(turn["id"])
            if result["mutable"] or turn["status"] == "inProgress":
                pending[turn["id"]] = position
            else:
                pending.pop(turn["id"], None)
            summary["turns"] += 1
            summary["last_turn"] = {"id": turn["id"], "status": turn["status"]}
            await asyncio.sleep(0)

        # Cells may outlive their turn. These obligations remain even after the tail
        # advances to a newer turn; their item prefix never passes an unfinished item.
        for turnid, position in list(pending.items()):
            found = False
            params = {"threadId": actor["thread"], "itemsView": "notLoaded", "sortDirection": "asc",
                      "limit": PAGE_SIZE, **({"cursor": position["cursor"]} if position.get("cursor") else {})}
            first = True
            async for page in native.page(connection, "thread/turns/list", params):
                summary["pages"] += 1
                if first and position.get("boundary") and (not page["data"] or page["data"][0]["id"] != position["boundary"]):
                    raise CoordinatorError("HISTORY_ANCHOR", "Mutable turn boundary is no longer visible")
                first = False
                for turn in page["data"]:
                    if turn["id"] == turnid:
                        await visit(turn, page.get("backwardsCursor"), page["data"][0]["id"])
                        found = True
                        break
                if found:
                    break
            if not found:
                raise CoordinatorError("HISTORY_ANCHOR", "Mutable turn is no longer visible")

        params = {"threadId": actor["thread"], "itemsView": "notLoaded", "sortDirection": "asc", "limit": PAGE_SIZE}
        if cp.get("cursor"):
            params["cursor"] = cp["cursor"]
        first = True
        async for page in native.page(connection, "thread/turns/list", params):
            summary["pages"] += 1
            if first and cp.get("boundary") and (not page["data"] or page["data"][0]["id"] != cp["boundary"]):
                raise CoordinatorError("HISTORY_ANCHOR", "Inclusive turn boundary does not match the checkpoint")
            first = False
            for turn in page["data"]:
                if turn["id"] not in covered:
                    await visit(turn, page.get("backwardsCursor"), page["data"][0]["id"])
                summary["last_turn"] = {"id": turn["id"], "status": turn["status"]}
            # Native backwardsCursor includes the FIRST row, never the page's last row.
            # Keeping it is safe at a crash mid-bootstrap; per-turn prefixes avoid rereading payloads.
            if page["data"]:
                self.valid(listener, actor, connection, epoch)
                self.store.put("native_history", self.key(actor), dict(cp, pending=pending,
                               cursor=page.get("backwardsCursor"), boundary=page["data"][0]["id"],
                               identity=observed_identity), actor["id"])
        after = (await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False}))["thread"]
        after_identity = await finish_thread(identity, after)
        if not same_identity(observed_identity, after_identity):
            self.invalidate(actor, "native_identity")
            raise CoordinatorError("HISTORY_ANCHOR", "Native history changed during observation")
        self.valid(listener, actor, connection, epoch)
        # The stat above can yield to newer notifications. Take the final ordered
        # status fence after it, and do not await between that fence and this save.
        after = (await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False}))["thread"]
        self.valid(listener, actor, connection, epoch)
        if after.get("path") != after_identity.get("path") or after.get("historyMode") != after_identity.get("mode"):
            self.invalidate(actor, "native_identity")
            raise CoordinatorError("HISTORY_ANCHOR", "Native history changed at the ordered status fence")
        current = self.store.get(actor["id"])
        last = summary.get("last_turn", {})
        status_reason = native.quiet_status(after.get("status"))
        if self.readers.get(actor["id"]) is not connection and status_reason and status_reason != "native_active_turn":
            raise CoordinatorError("NATIVE_STATUS_UNKNOWN", status_reason, {"actor": actor["id"]})
        if self.readers.get(actor["id"]) is connection:
            # A saved inProgress turn is evidence of interruption, not a live turn
            # on this unloaded reader. Cancellation must not wait for it forever.
            current.update(active_turn=None, state="interrupted" if last.get("status") == "inProgress"
                           else last.get("status", "idle"))
        elif status_reason is None:
            current.update(active_turn=None, state=last.get("status", "idle"))
        elif last.get("status") == "inProgress" and (not current.get("active_turn") or current["active_turn"] in covered):
            current.update(active_turn=last["id"], state="running")
        elif current.get("active_turn") in covered and last.get("status") != "inProgress":
            current.update(active_turn=None, state=last.get("status", "idle"))
        self.store.save(current)
        if candidate and candidate not in covered:
            raise CoordinatorError("HISTORY_ANCHOR", "Tail candidate was not observed")
        # Use the native descending one-turn cursor only after its candidate is covered.
        final = dict(cp, identity=after_identity, pending=pending, cursor=tail.get("backwardsCursor"),
                     boundary=candidate, observed=time.time())
        self.store.put("native_history", self.key(actor), final, actor["id"])
        return summary

    async def items(self, listener, actor, connection, turn, epoch, summary, turn_position):
        key = self.turn_key(actor, turn["id"])
        prior = self.store.get(key, required=False) or {}
        if prior.get("epoch") != epoch:
            prior = {}
        anchor = prior.get("anchor")
        tail = prior.get("tail", anchor)
        if not isinstance(prior.get("pending_items", {}), dict):
            raise CoordinatorError("HISTORY_ANCHOR", "Stored unfinished items are malformed")
        pending = dict(prior.get("pending_items", {}))
        closure_checked = dict(prior.get("closure_checked", {}))
        if (any(value is not None and (not isinstance(value, str) or not value) for value in (anchor, tail))
                or any(not isinstance(ident, str) or not isinstance(position, dict)
                       or position.get("after") is not None and not isinstance(position["after"], str)
                       for ident, position in pending.items())):
            raise CoordinatorError("HISTORY_ANCHOR", "Stored item progress is malformed")
        base = {"threadId": actor["thread"], "turnId": turn["id"], "sortDirection": "asc"}

        def save(status="inProgress"):
            result = {"epoch": epoch, "turn": turn["id"], "status": status, "anchor": anchor,
                      "tail": tail, "pending_items": pending, "position": turn_position,
                      "closure_checked": closure_checked,
                      "mutable": bool(pending or self.obligations(actor, turn["id"]))}
            self.store.put("native_turn", key, result, actor["id"])
            return result

        # A tail may pass an unfinished item only with this durable obligation.
        # Re-read exactly that item using its native predecessor anchor, rather than
        # fetching every already-applied successor each time the cell is still open.
        for ident, position in list(pending.items()):
            params = dict(base, limit=1)
            if position.get("after"):
                params["cursor"] = {"type": "item", "itemId": position["after"]}
            page = await connection.call("thread/items/list", params)
            summary["pages"] += 1
            if not page["data"] or page["data"][0]["item"]["id"] != ident:
                raise CoordinatorError("HISTORY_ANCHOR", "Unfinished item is no longer at its recorded position")
            entry = page["data"][0]
            self.valid(listener, actor, connection, epoch)
            await self.apply_item(listener, actor, entry, epoch, connection)
            if entry.get("completedAtMs") is not None:
                pending.pop(ident)
            summary["items"] += 1
            await asyncio.sleep(0)
        if not pending:
            anchor = tail
        params = dict(base, limit=PAGE_SIZE)
        if tail:
            params["cursor"] = {"type": "item", "itemId": tail}
        async for page in native.page(connection, "thread/items/list", params):
            summary["pages"] += 1
            for entry in page["data"]:
                self.valid(listener, actor, connection, epoch)
                await self.apply_item(listener, actor, entry, epoch, connection)
                ident = entry["item"]["id"]
                # Zero is a recorded completion timestamp. Save the unfinished
                # obligation together with the tail, after all item effects.
                if entry.get("completedAtMs") is None:
                    pending[ident] = {"after": tail}
                tail = ident
                if not pending:
                    anchor = tail
                summary["items"] += 1
                await asyncio.sleep(0)
            self.valid(listener, actor, connection, epoch)
            save()
        # Missing from the tail is not missing from the turn: completion may update
        # an earlier item. Re-read only this turn when closure evidence changes.
        # Save that evidence after the read and its effects. An ended server with
        # an unfindable call then costs an empty tail, not repeated full scans.
        operations = self.obligations(actor, turn["id"])
        evidence = await self.closure_evidence(operations)
        due = {key: value for key, value in evidence.items() if closure_checked.get(key) != value}
        if due:
            self.store.event("history/closure", actor["id"], {"turn": turn["id"], "operations": sorted(due)})
            summary["closure_reads"] = summary.get("closure_reads", 0) + 1
            previous = None
            async for page in native.page(connection, "thread/items/list", dict(base, limit=PAGE_SIZE)):
                summary["pages"] += 1
                for entry in page["data"]:
                    self.valid(listener, actor, connection, epoch)
                    await self.apply_item(listener, actor, entry, epoch, connection)
                    if entry.get("completedAtMs") is not None:
                        pending.pop(entry["item"]["id"], None)
                    else:
                        pending[entry["item"]["id"]] = {"after": previous}
                    previous = entry["item"]["id"]
                    summary["items"] += 1
                    await asyncio.sleep(0)
            self.valid(listener, actor, connection, epoch)
            closure_checked.update(due)
        remaining = {operation["id"] for operation in self.obligations(actor, turn["id"])}
        closure_checked = {key: value for key, value in closure_checked.items() if key in remaining}
        # Item-before-turn is required for phase answers. Re-fetch actor/run after awaits.
        current = self.store.get(actor["id"])
        if turn["status"] != "inProgress":
            self.daemon.writer.observe(current, "turn/completed", {"threadId": actor["thread"], "turn": turn})
        return save(turn["status"])

    async def apply_item(self, listener, actor, entry, epoch, connection):
        item, turn = entry["item"], entry["turnId"]
        complete = entry.get("completedAtMs") is not None
        if complete:
            self.daemon.writer.observe(actor, "item/completed", {"threadId": actor["thread"], "turnId": turn, "item": item})
        if item["type"] == "userMessage" and item.get("clientId"):
            self.daemon.messages.entered(actor["id"], item["clientId"], {"history": True, "turn": turn, "item": item["id"]})
            self.store.put("native_input", "native_input:" + actor["id"] + ":" + digest(item["clientId"]),
                           {"epoch": epoch, "turn": turn, "item": item["id"]}, actor["id"])
        if item["type"] == "subAgentActivity":
            await self.daemon.resolve_actor(listener, item["agentThreadId"], connection=connection)
        if item["type"] in EFFECT_ITEMS:
            operation = self.daemon.find_operation(listener, actor["thread"], turn, item["id"], history=True)
            if operation and complete:
                operation["completed_item"] = item
                self.store.save(operation)
                self.daemon.ledger.end_execution(operation)
            elif not operation:
                marker = "history_unknown:" + actor["id"] + ":" + digest([turn, item["id"]])
                if not self.store.get(marker, required=False):
                    with self.store.transaction():
                        for checkout in participants.checkout_ids(self.store, actor["unit"]):
                            self.store.unknown(checkout, "*", "history contains an unaccounted effect-capable item",
                                               {"item": item["id"], "listener": listener["id"]})
                        self.store.put("history_unknown", marker, {"item": item["id"]}, actor["id"])

    def schedule(self, listener, thread):
        """Coalesce applied notifications; never make the native worker wait for history."""
        key = (listener["id"], thread)
        self.dirty.add(key)
        if key in self.scheduled or self.daemon.draining:
            return

        async def catch_up():
            try:
                while key in self.dirty and not self.daemon.draining:
                    self.dirty.discard(key)
                    await asyncio.sleep(0.05)
                    current = self.store.get(listener["id"])
                    applied = self.store.get("native_applied:" + listener["id"], required=False) or {}
                    if (current.get("recovery_state") in ("reconciling", "failed")
                            or applied.get("failed") or applied.get("pending")):
                        return
                    connection = self.daemon.connections.get(listener["id"])
                    if not connection or connection.closed:
                        return
                    actor = await self.daemon.resolve_actor(current, thread)
                    await self.observe(current, actor, connection)
            finally:
                self.scheduled.pop(key, None)
        self.scheduled[key] = self.daemon.background(catch_up())
