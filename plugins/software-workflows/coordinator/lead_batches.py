"""Persist-first batch delivery with independent, compact completion items."""
import asyncio
import json
import time

from .client import CoordinatorError
from .locks import finish
from .store import digest, encoded, LEAD_BATCH_ITEM_PENDING, LEAD_BATCH_LEGACY

# A failed delivery retries no faster than the hook's 3 s RPC timeout. This also
# bounds outbox polling and a failed bookkeeping write's in-memory cooldown;
# it is a retry interval, never an expiry or a limit on completion lifetime.
RETRY_SECONDS = 3


def runtime(daemon):
    if not hasattr(daemon, "lead_batches"):
        daemon.lead_batches = {"jobs": {}, "after": {}, "problems": {}, "scan": None, "scan_after": 0}
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
    data = await asyncio.to_thread(json.loads, row["data"])
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
            _, header, items = await asyncio.to_thread(prepared, [i["payload"] for i in old["items"]], old)
            commit(daemon, ident, header, items)


def owner(daemon, item):
    from .lead_calls import find
    who = item["identity"]
    record = find(daemon.store, who.get("session_id"), who.get("tool_use_id"))
    execution = (record or {}).get("execution") or {}
    end = (execution.get("end") or {}).get("source")
    if execution.get("unadmitted_completion") or end in (
            "PostToolUse", "PostToolUseFailure", "PostToolBatch", "transcript", "host exited"):
        return record["id"]
    return None


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
            if held:
                outcome = {"state": "owned", "owner": held}
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
                    data = await asyncio.to_thread(encoded, {k: v for k, v in item.items() if k != "id" and not k.startswith("_")})
                    daemon.store.db.execute("UPDATE records SET data=?,revision=revision+1 WHERE id=?", (data, ident))
                    return
        # Bookkeeping errors are retries, never mistaken for a call's refusal.
        acknowledge(daemon, item, batch, **outcome)
        if outcome["state"] != "owned":
            forget_marker(daemon.store, item["identity"])


def start(daemon, ident):
    state = runtime(daemon)
    existing = state["jobs"].get(ident)
    if existing:
        return existing
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
            "results": await asyncio.to_thread(decoded)}


async def submit(daemon, calls, settle=True):
    ident, header, items = await asyncio.to_thread(prepared, calls)
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
        for receipt, calls, error in await asyncio.to_thread(pending, daemon.store.root):
            try:
                if error:
                    raise CoordinatorError("OUTBOX_READ", error, operational=True)
                await submit(daemon, calls, settle=False)
                await asyncio.to_thread(ack_outbox, daemon.store.root, receipt)
            except Exception as exc:
                problem(daemon, receipt[0], exc)
    except Exception as exc:
        problem(daemon, "outbox", exc)


async def observe(daemon):
    """Only schedule isolated attempts; never await one item's filesystem work."""
    state = runtime(daemon)
    try:
        now = time.monotonic()
        if now >= state["scan_after"] and (state["scan"] is None or state["scan"].done()):
            state["scan_after"] = now + RETRY_SECONDS
            state["scan"] = daemon.background(scan(daemon))
        for row in daemon.store.db.execute("SELECT id FROM records INDEXED BY lead_batch_item_pending WHERE " +
                LEAD_BATCH_ITEM_PENDING + " AND coalesce(json_extract(data,'$.retry_after'),0)<=?", (time.time(),)):
            if daemon.closing:
                break
            if now >= state["after"].get(row["id"], 0):
                start(daemon, row["id"])
    except Exception as exc:
        problem(daemon, "scheduler", exc)
