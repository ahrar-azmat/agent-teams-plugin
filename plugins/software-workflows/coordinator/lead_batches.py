"""Persist-first batch delivery with independent, compact completion items."""
import asyncio
import json
import os
import time

from .locks import finish, finish_thread
from .client import CoordinatorError
from .store import digest, encoded, LEAD_BATCH_ITEM_PENDING, LEAD_BATCH_LEGACY

# A failed delivery retries no faster than the hook's 3 s RPC timeout. This also
# bounds outbox polling and a failed bookkeeping write's in-memory cooldown;
# it is a retry interval, never an expiry or a limit on completion lifetime.
RETRY_SECONDS = 3
# How many pending items the observer settles at once; the rest wait, durable, for a later tick. Threat: event-loop
# starvation. Through 2.4.40 the observer started one task per due item every 0.25 s, and with 6,380 due items on
# 11-19 its row loop alone was 2,402 of 6,269 loop-stall samples in 6 h, while every lead edit waited behind it
# (LEAD_ADMISSION_BUSY). Size: since 2.4.41 an item either returns at once (its call already settled) or waits on
# its checkout's shared scan with every other (intents.settle_uncredited), so a waiting item is an idle task and
# more of them only make each scan's cohort larger; 64 keeps a tick's own work far under the 0.75 s stall threshold
# (VERIFY 2.4.41 measures the drain). It queues work, never drops it, and SOFTWARE_WORKFLOWS_BATCH_ATTEMPTS adjusts
# it. A submit's own items start at once and do not count against it.
try:
    ATTEMPTS = max(1, int(os.environ.get("SOFTWARE_WORKFLOWS_BATCH_ATTEMPTS", "64")))
except ValueError:
    ATTEMPTS = 64
# A failed release of deferrals is tried again this long after.
RELEASE_RETRY_SECONDS = 30
# Owed observations that may run even while live completions fill ATTEMPTS, so none waits for good.
OWED_RESERVE = 4
# The code defers a failed item by RETRY_SECONDS. A deferral beyond this was not set by it (on 11-19 the SmartPay
# lead deferred 22,923 stuck Bash items to 26 Nov by hand, to relieve 2.4.40's stall): each start releases it, so
# that item settles through the same path as every other.
FOREIGN_DEFERRAL_SECONDS = 3600


def runtime(daemon):
    if not hasattr(daemon, "lead_batches"):
        daemon.lead_batches = {"jobs": {}, "after": {}, "problems": {}, "scan": None, "scan_after": 0, "observer_jobs": set()}
    return daemon.lead_batches


def problem(daemon, ident, exc):
    """Even recording a bookkeeping failure may fail; never kill the observer."""
    state = runtime(daemon)
    code = getattr(exc, "code", type(exc).__name__)
    if state["problems"].get(ident) != code:
        state["problems"][ident] = code
        try:
            daemon.store.event("lead_batch/retry_failed", ident, {"code": code})
        except Exception:
            pass


async def read(daemon, ident):
    row = daemon.store.db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise CoordinatorError("NOT_FOUND", "Batch item does not exist", {"id": ident})
    data = await finish_thread(json.loads, row["data"])
    return dict(data, id=row["id"], _kind=row["kind"], _revision=row["revision"])


def identity(payload):
    data = payload.get("tool_input") or {}
    return {"session_id": payload.get("session_id"), "tool_use_id": payload.get("tool_use_id"),
            "tool_name": payload.get("tool_name"), "cwd": payload.get("cwd"),
            "path": data.get("notebook_path") if payload.get("tool_name") == "NotebookEdit" else data.get("file_path"),
            "input_digest": digest([payload.get("tool_name"), data])}


def prepared(calls, previous=None):
    from .intents import FILES
    if not isinstance(calls, list) or any(not isinstance(call, dict) or call.get("tool_name") not in FILES | {"Bash"}
                                         for call in calls):
        raise CoordinatorError("LEAD_HOOK_FIELDS", "A completion batch contains file or Bash calls")
    payloads = [dict(call, hook_event_name="PostToolBatch") for call in calls]
    ident = previous["id"] if previous else "lead_batch_" + digest(payloads)
    items = []
    for index, payload in enumerate(payloads):
        item = {"ordinal": index, "identity": identity(payload), "state": "pending", "payload": payload}
        old = previous["items"][index] if previous else {}
        if old.get("state") == "complete":
            item.pop("payload")
            item.update(state="complete", result=old["result"])
        elif old.get("error"):
            item["error"] = old["error"]
        items.append((ident + "_" + str(index), encoded(item)))
    remaining = sum(json.loads(data)["state"] == "pending" for _, data in items)
    header = {"schema": 2, "state": "pending" if remaining else "complete", "remaining": remaining,
              "count": len(items), "created": (previous or {}).get("created", time.time())}
    return ident, header, items


def commit(daemon, ident, header, items):
    with daemon.store.transaction():
        daemon.store.put("lead_batch", ident, header)
        for key, data in items:
            daemon.store.db.execute("INSERT INTO records(id,kind,parent,data) VALUES(?,?,?,?)",
                                   (key, "lead_batch_item", ident, data))


async def migrate(daemon, ident):
    async with daemon.actor_locks.hold("lead-batch:" + ident):
        old = await read(daemon, ident)
        if "items" in old:
            _, header, items = await finish_thread(prepared, [i["payload"] for i in old["items"]], old)
            commit(daemon, ident, header, items)


def owner(daemon, item):
    from .lead_calls import find
    who = item["identity"]
    record = find(daemon.store, who.get("session_id"), who.get("tool_use_id"))
    execution = (record or {}).get("execution") or {}
    end = (execution.get("end") or {}).get("source")
    if execution.get("unadmitted_completion") or end in (
            "PostToolUse", "PostToolUseFailure", "PostToolBatch", "transcript", "host exited", "host order"):
        return record["id"]
    return None


def recovered_before(daemon, item, batch):
    """A Bash item for a call recovery settled by its deadline (intents.invalidate_windows), delivered before that
    settlement: the command had ended before its batch was made, and the restart's snapshot of every checkout records
    what it left, so it needs no observation of its own. One delivered after observes (intents.settle_uncredited)."""
    if item["identity"].get("tool_name") != "Bash":
        return False
    from .intents import RECOVERED
    from .lead_calls import find
    record = find(daemon.store, item["identity"].get("session_id"), item["identity"].get("tool_use_id"))
    settlement = (record or {}).get("settlement") or {}
    created = (daemon.store.get(batch, required=False) or {}).get("created")
    if not (record and record["state"] == "reconciled" and settlement.get("source") == RECOVERED
            and isinstance(created, (int, float)) and created <= settlement.get("at", 0)):
        return False
    # Only with evidence that recovery observed every checkout the call reaches (recovery_snapshots): "wait" while
    # recovery runs; a checkout it skipped or failed leaves the item to observe (intents.settle_uncredited).
    reach = record.get("reach") or [record.get("checkout")]
    if all(key in getattr(daemon, "recovery_snapshots", ()) for key in reach):
        return True
    if not getattr(daemon, "ready", True) or any(("checkout", key) in (getattr(daemon, "recovering", None) or ()) for key in reach):
        return "wait"
    return False


def acknowledge(daemon, item, batch, **outcome):
    """Compact the item and decrement the small header in one transaction."""
    compact = {k: item[k] for k in ("id", "_kind", "_revision", "ordinal", "identity")}
    compact.update(outcome, completed=time.time())
    header = daemon.store.get(batch)
    with daemon.store.transaction():
        daemon.store.save(compact)
        header["remaining"] -= 1
        if not header["remaining"]:
            header.update(state="complete", completed=time.time())
        daemon.store.save(header)
    runtime(daemon)["problems"].pop(item["id"], None)


async def attempt(daemon, ident):
    from .lead_calls import forget_marker
    from .intents import FILES
    async with daemon.actor_locks.hold("lead-batch-item:" + ident):
        item = await read(daemon, ident)
        if item["state"] != "pending" or daemon.closing:
            return
        batch = daemon.store.db.execute("SELECT parent FROM records WHERE id=?", (ident,)).fetchone()[0]
        try:
            held = owner(daemon, item)
            recovered = None if held else recovered_before(daemon, item, batch)
            if held:
                outcome = {"state": "owned", "owner": held}
            elif recovered == "wait":
                return  # recovery has not observed its checkouts yet: the item waits for that (cooldown in start)
            elif recovered:
                outcome = {"state": "complete", "result": {"coordinated": True, "settled": True, "recovered": True}}
            else:
                result = await finish(daemon.dispatch("lead-hook/dispatch", {"payload": item["payload"]}))
                if (not isinstance(result, dict) or item["identity"]["tool_name"] in FILES and result.get("settled") is not True):
                    raise CoordinatorError("LEAD_BATCH_PENDING", "Completion has no terminal acknowledgment", operational=True)
                outcome = {"state": "complete", "result": result}
        except Exception as exc:
            error = (exc if isinstance(exc, CoordinatorError) else
                     CoordinatorError("OPERATIONAL_FAILURE", type(exc).__name__, operational=True)).envelope()
            if isinstance(exc, CoordinatorError) and not exc.operational:
                outcome = {"state": "refused", "error": error}
            else:
                held = owner(daemon, item)
                if held:
                    outcome = {"state": "owned", "owner": held, "error": error}
                else:
                    item.update(error=error, retry_after=time.time() + RETRY_SECONDS)
                    data = await finish_thread(encoded, {k: v for k, v in item.items() if k != "id" and not k.startswith("_")})
                    daemon.store.db.execute("UPDATE records SET data=?,revision=revision+1 WHERE id=?", (data, ident))
                    return
        # Bookkeeping errors are retries, never mistaken for a call's refusal.
        acknowledge(daemon, item, batch, **outcome)
        if outcome["state"] != "owned":
            forget_marker(daemon.store, item["identity"])


def start(daemon, ident, observer=False):
    state = runtime(daemon)
    existing = state["jobs"].get(ident)
    if existing:
        return existing
    if observer:
        state["observer_jobs"].add(ident)
    async def guarded():
        try:
            await finish(attempt(daemon, ident))
        except Exception as exc:
            problem(daemon, ident, exc)
        finally:
            try:
                pending = daemon.store.db.execute("SELECT id FROM records WHERE id=? AND " +
                    LEAD_BATCH_ITEM_PENDING, (ident,)).fetchone()
            except Exception:
                pending = True
            if pending:
                state["after"][ident] = time.monotonic() + RETRY_SECONDS
            else:
                state["after"].pop(ident, None)
            state["jobs"].pop(ident, None)
            state["observer_jobs"].discard(ident)
    task = daemon.background(guarded())
    state["jobs"][ident] = task
    return task


async def answer(daemon, ident):
    header = daemon.store.get(ident)
    rows = [row["data"] for row in daemon.store.db.execute(
        "SELECT data FROM records WHERE kind='lead_batch_item' AND parent=? ORDER BY json_extract(data,'$.ordinal')", (ident,))]
    def decoded():
        results = []
        for raw in rows:
            item = json.loads(raw)
            result = dict(item["identity"])
            result.update({k: v for k, v in item.items() if k not in ("identity", "payload", "ordinal") and v is not None})
            if item["state"] == "pending":
                result["pending"] = True
            results.append(result)
        return results
    return {"batch": ident, "received": True, "settled": header["state"] == "complete",
            "results": await finish_thread(decoded)}


async def submit(daemon, calls, settle=True):
    from .admissions import record_end
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, dict):
                record_end(daemon, dict(call, hook_event_name="PostToolBatch"))
    ident, header, items = await finish_thread(prepared, calls)
    async with daemon.actor_locks.hold("lead-batch:" + ident):
        exists = daemon.store.db.execute("SELECT id FROM records WHERE id=?", (ident,)).fetchone()
        if not exists:
            commit(daemon, ident, header, items)
    await migrate(daemon, ident)
    if settle and daemon.ready:
        keys = [r[0] for r in daemon.store.db.execute(
            "SELECT id FROM records WHERE parent=? AND " + LEAD_BATCH_ITEM_PENDING + " ORDER BY json_extract(data,'$.ordinal')", (ident,))]
        for key in keys:
            if daemon.closing:
                break
            await asyncio.shield(start(daemon, key))
    return await answer(daemon, ident)


async def scan(daemon):
    from .lead_outbox import pending, acknowledge as ack_outbox
    try:
        # Includes completed round-5 batches: their historical input bodies
        # must be compacted too, once per daemon generation.
        ids = [r[0] for r in daemon.store.db.execute("SELECT id FROM records INDEXED BY lead_batch_legacy WHERE " + LEAD_BATCH_LEGACY)]
        for ident in ids:
            try:
                await migrate(daemon, ident)
            except Exception as exc:
                problem(daemon, ident, exc)
        for receipt, calls, error in await finish_thread(pending, daemon.store.root):
            try:
                if error:
                    raise CoordinatorError("OUTBOX_READ", error, operational=True)
                await submit(daemon, calls, settle=False)
                await finish_thread(ack_outbox, daemon.store.root, receipt)
            except Exception as exc:
                problem(daemon, receipt[0], exc)
    except Exception as exc:
        problem(daemon, "outbox", exc)


async def release_deferrals(daemon):
    """Release deferrals the code did not set (FOREIGN_DEFERRAL_SECONDS), in chunks, each compare-and-set on the
    bytes read so a concurrent attempt's own write is kept. Once per daemon start."""
    released, after = 0, ""
    while not daemon.closing:
        rows = daemon.store.db.execute(
            "SELECT id,data FROM records INDEXED BY lead_batch_item_pending WHERE " + LEAD_BATCH_ITEM_PENDING +
            " AND coalesce(json_extract(data,'$.retry_after'),0)>? AND id>? ORDER BY id LIMIT 500",
            (time.time() + FOREIGN_DEFERRAL_SECONDS, after)).fetchall()
        if not rows:
            break
        after = rows[-1]["id"]
        def rewrite():
            out = []
            for row in rows:
                data = json.loads(row["data"])
                data.pop("retry_after", None)
                out.append((encoded(data), row["id"], row["data"]))
            return out
        changes = await finish_thread(rewrite)
        with daemon.store.transaction():
            for data, ident, old in changes:
                released += daemon.store.db.execute("UPDATE records SET data=?,revision=revision+1 WHERE id=? AND data=?",
                                                    (data, ident, old)).rowcount
        await asyncio.sleep(0)
    if released:
        daemon.store.event("lead_batch/deferrals_released", None, {"items": released})


async def release_once(daemon):
    """release_deferrals until it completes once in this daemon; a failure is retried RELEASE_RETRY_SECONDS later."""
    state = runtime(daemon)
    try:
        await release_deferrals(daemon)
        state["released"] = True
    except Exception as exc:
        problem(daemon, "release", exc)
        state["release_after"] = time.monotonic() + RELEASE_RETRY_SECONDS


async def observe(daemon):
    """Only schedule isolated attempts; never await one item's filesystem work. At most ATTEMPTS run at once, and a
    tick reads only as many due rows as it can start (plus those it must skip: running, or cooling after a failure).
    Items failed before and due again get half of a tick's free places and fresh items the other half, each in its
    own index order, so neither kind can keep the other waiting."""
    state = runtime(daemon)
    try:
        now = time.monotonic()
        if now >= state["scan_after"] and (state["scan"] is None or state["scan"].done()):
            state["scan_after"] = now + RETRY_SECONDS
            state["scan"] = daemon.background(scan(daemon))
        release = state.get("release")
        if not state.get("released") and (release is None or release.done()) and now >= state.get("release_after", 0):
            state["release"] = daemon.background(release_once(daemon))
        if now >= state.get("owed_after", 0):
            state["owed_after"] = now + RETRY_SECONDS
            from .intents import retry_owed, SETTLING, OWED_ACTIVE
            # Owed observations share the observer's bound, with OWED_RESERVE concurrent places that live traffic
            # cannot take.
            retry_owed(daemon, max(OWED_RESERVE - len(OWED_ACTIVE), ATTEMPTS - len(SETTLING)))
        # A submit's own items run beside these and never take their places: retried items always get some.
        free = ATTEMPTS - len(state["observer_jobs"])
        if free <= 0:
            return
        cooling = [key for key, until in state["after"].items() if until > now]
        skip = len(state["jobs"]) + len(cooling)
        retried = list(daemon.store.db.execute(
            "SELECT id FROM records INDEXED BY lead_batch_item_pending WHERE " + LEAD_BATCH_ITEM_PENDING +
            " AND coalesce(json_extract(data,'$.retry_after'),0)>0 AND coalesce(json_extract(data,'$.retry_after'),0)<=? "
            "ORDER BY coalesce(json_extract(data,'$.retry_after'),0), id LIMIT ?", (time.time(), free + skip)))
        fresh = list(daemon.store.db.execute(
            "SELECT id FROM records INDEXED BY lead_batch_item_pending WHERE " + LEAD_BATCH_ITEM_PENDING +
            " AND coalesce(json_extract(data,'$.retry_after'),0)=0 ORDER BY coalesce(json_extract(data,'$.retry_after'),0), id LIMIT ?",
            (free + skip,)))
        def startable(rows):
            return [row["id"] for row in rows if row["id"] not in state["jobs"] and now >= state["after"].get(row["id"], 0)]
        retried, fresh = startable(retried), startable(fresh)
        # Ticks alternate which kind gets the odd place, so a single free place serves both in turn.
        prefer = state["prefer_retried"] = not state.get("prefer_retried", False)
        taken = retried[:(free + 1) // 2 if prefer else free // 2]
        chosen = taken + fresh[:free - len(taken)]
        chosen += retried[len(taken):len(taken) + free - len(chosen)]
        for ident in chosen:
            if daemon.closing:
                break
            start(daemon, ident, observer=True)
    except Exception as exc:
        problem(daemon, "scheduler", exc)
