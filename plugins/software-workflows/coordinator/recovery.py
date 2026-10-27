"""Generation-fenced recovery. Never replay an uncertain mutation or continue a turn."""
import asyncio
import base64
from contextlib import asynccontextmanager
import errno
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

from .locks import finish, finish_all, finish_thread
from .client import CoordinatorError
from . import native, processes, participants
from .store import uid

# A quiet app-server's graceful exit after SIGTERM is bounded by Codex 0.163 itself: its threads' shutdown at
# 10 s (shutdown_all_threads_bounded; a writer's MCP servers stop there) and each connection's RPC drain at 30 s
# (CONNECTION_RPC_DRAIN_TIMEOUT; the daemon's own connection is closed first), plus 5 s. One replaced for its hook
# program has nothing of its unit in flight, so past this it is killed rather than kept.
READMIT_DRAIN_S = 45
# A writer whose reconcile failed after recovery is reconciled again from maintenance: first after 5 s (the
# transient failures seen are an app-server start or one RPC, each 1-3 s), then doubling to 5 min, so one that keeps
# failing costs at most about a dozen app-server starts an hour. Its unit's work waits meanwhile.
RECONCILE_RETRY_FIRST_S, RECONCILE_RETRY_MAX_S = 5, 300
# 2026-10-23, real Codex 0.163.0, 27 synthetic histories totalling 1,997.85 MB:
# limits 1/2/4 completed start+resume+bootstrap in 77.0/45.5/33.3 s. At 4, full
# status p99/max were 15.8/27.1 ms; actual lead PreToolUse p99/max 145/156 ms;
# the slowest resume was 3.24 s (over 3x headroom against the native 10 s answer
# budget). Unbounded 2.4.5 reached 20.75 s resumes and seven history timeouts.
# Use the highest measured candidate, 4. This history-component fixture used a
# pre-provisioned ledger/lease; it does not measure admission or filesystem scans.
# Re-measure for other hosts/workloads; README and r246-r contain the raw evidence.
RECOVERY_CONCURRENCY = 4
_capacity = os.environ.get("SOFTWARE_WORKFLOWS_RECOVERY_CONCURRENCY")
if _capacity is not None:
    if not _capacity.isascii() or not _capacity.isdecimal() or int(_capacity) < 1:
        raise CoordinatorError("CONFIG", "SOFTWARE_WORKFLOWS_RECOVERY_CONCURRENCY must be a positive integer")
    RECOVERY_CONCURRENCY = int(_capacity)
RUNTIME_NOTE = ("Values the old app-server held in its tool runtime (code-mode variables and functions) are gone: "
                "only the thread's history carries over.")


def closed(exc):
    """A live app-server's connection is dead: refused or gone at connect (ConnectionRefusedError, FileNotFoundError),
    reset or broken under a write (a ConnectionError), closed under a read (NATIVE_DISCONNECTED, or EOFError in the
    handshake). Others (a schema this daemon cannot generate, a refusal native answered) are the daemon's or native's
    answer."""
    return (isinstance(exc, (ConnectionError, FileNotFoundError, EOFError))
            or getattr(exc, "code", None) == "NATIVE_DISCONNECTED")


def silent(exc):
    """A live app-server left a connection's handshake (TimeoutError) or a request (NATIVE_UNCERTAIN) unanswered.
    Silence alone never ends it: one that is only slow would lose its turn and its runtime values."""
    return isinstance(exc, TimeoutError) or getattr(exc, "code", None) == "NATIVE_UNCERTAIN"


def seconds(name, default):
    """A positive number of seconds from the environment variable `name`, else `default`. Anything else is refused
    as the daemon starts (CONFIG): a bound of zero would end every app-server, or refuse every call, at once."""
    value = os.environ.get(name)
    if not value:
        return default
    try:
        result = float(value)
    except ValueError:
        result = float("nan")
    if not 0 < result < float("inf"):
        raise CoordinatorError("CONFIG", name + " must be a positive number of seconds", {"value": value})
    return result


# A writer app-server that leaves thread/loaded/list unanswered this long is silent: it is reported (listener/silent,
# daemon/status "silent"), and a reconcile that needs its answers is refused LISTENER_SILENT and made again later. It
# is never ended for it. Native 0.163 runs that request on a task of its own (no serialization scope:
# app-server-protocol common.rs, app-server message_processor.rs), from its loaded-thread map under a read lock no turn
# holds (thread_manager.rs list_thread_ids): it never waits behind a turn or another request. Measured 10-21 on
# 0.163.0, 13 writers on one machine, 2,410 probes, scripted model: slowest 0.70 s, while every writer ran a heavy turn
# at once (a 20 s JavaScript busy loop in its code-mode cell, a shell loop, a 2,000,000-line command output, a patch);
# p99 45 ms then, 5 ms idle, 9 ms through a forced switch's recovery. Not measured through a context compaction or a
# real model's turn, which is why silence is only reported. An answer past 80 % of it is recorded (listener/answer_slow).
ANSWER_S = seconds("SOFTWARE_WORKFLOWS_ANSWER_S", 10)
# How often maintenance probes each connected writer app-server while the daemon serves: its process read (no
# request), then thread/loaded/list. Also how long a stop must last to count: an app-server stopped by a signal at two
# reads this far apart is ended and replaced through re-admission. Nothing in the coordinator or in Codex stops one,
# and each runs in a session of its own, so no job control reaches it: a stop is a person's or a tool's (a SIGSTOP,
# a debugger), and one still in place a whole probe period later is no pause.
ANSWER_EVERY_S = seconds("SOFTWARE_WORKFLOWS_ANSWER_EVERY_S", 30)
# How long one attempt of a call for a run, unit or checkout that startup recovery is still reconciling waits for it,
# then is refused WRITER_RECOVERING (retryable, with retry_after). Per attempt: the RPC envelope carries no caller
# deadline (a hook's does, and a hook waits until its own cutoff instead: writer_ready). Below the 30 s a coordinator
# Client waits by default, so the refusal reaches its caller rather than a lost answer. server.py _coordinator_call
# sends an unkeyed call again within its own 90 s; a keyed call keeps the refusal as its key's answer (no effect was
# made), so the CLI, which keys every call, prints it with retry_after (exit 3) for its caller to run it again.
RECOVERY_WAIT_S = seconds("SOFTWARE_WORKFLOWS_RECOVERY_WAIT_S", 20)
RECONCILE_RETRY_FIRST_S = seconds("SOFTWARE_WORKFLOWS_RECONCILE_RETRY_FIRST_S", RECONCILE_RETRY_FIRST_S)
RECONCILE_RETRY_MAX_S = seconds("SOFTWARE_WORKFLOWS_RECONCILE_RETRY_MAX_S", RECONCILE_RETRY_MAX_S)
if RECONCILE_RETRY_MAX_S < RECONCILE_RETRY_FIRST_S:
    raise CoordinatorError("CONFIG", "Reconcile retry maximum must be at least its initial delay")


async def may_execute(daemon, operation):
    """Whether native may still write for this admitted patch: allowed, with no sign its handler ended, and its
    app-server still running. A quarantine then keeps its paths busy (`executing`)."""
    from .daemon import handler_ended
    if operation["kind"] != "patch" or operation["state"] not in ("allowed", "running") or handler_ended(operation):
        return False
    listener = daemon.store.get(operation["native"].get("listener") or "", required=False)
    if not listener:
        return False
    current = await finish_thread(processes.identity, listener["pid"])
    return bool(current and current["birth"] == listener["birth"] and not current["zombie"])


async def reconcile_listener(daemon, listener, advance=True, *, demand=False):
    """Reconcile a run's writer app-server: reconnect, replace or re-admit it, and read back what native did
    meanwhile.
    - One caller at a time for each run (a recovery, a resume, a message's or a workflow's re-admission, a cancel, a
      transport gap, a startup incident's retry). Each takes the run's lock, then reconciles the incarnation
      current by then, so two callers never replace the same app-server.
    - A failure marks that incarnation failed, so its unit's work waits, and maintenance reconciles it again.
    - Every call is a stop's to wait for. It is counted in daemon.listener_reconciles before its first await,
      a caller still waiting for the run's lock included. Once a stop is closing, every call is refused
      DAEMON_DRAINING, so nothing the stop has already stopped is started again behind it (review turn 48)."""
    daemon.check_native_stop()
    if daemon.closing:
        raise CoordinatorError("DAEMON_DRAINING", "Coordinator is closing; reconcile on the serving daemon",
                               {"retryable": True, "run": listener["run"], "listener": listener["id"]}, True)
    token = uid("reconcile")
    invocation = {"run": listener["run"], "listener": listener["id"], "started": time.time()}
    daemon.listener_reconciles[token] = invocation
    try:
        # Cancellation cannot release the stop fence while this pass's native or filesystem work still runs.
        return await finish(_registered_reconcile(daemon, listener, advance, invocation, demand))
    finally:
        daemon.listener_reconciles.pop(token, None)


async def _registered_reconcile(daemon, listener, advance, invocation, demand):
    store = daemon.store
    async with daemon.reconcile_locks.hold(listener["run"]):
        listener = store.get(listener["id"])
        bound = store.get(listener["actor"]).get("listener")
        if listener.get("root_thread") and listener["state"] in ("dead", "stopped", "dormant") and bound and bound != listener["id"]:
            listener = store.get(bound)
        if listener["state"] == "dormant" and not demand:
            return
        invocation["listener"] = listener["id"]
        invocation["phase"] = "queued"
        async with capacity(daemon):
            invocation["phase"] = "active"
            try:
                notice = await reconcile_pass(daemon, listener)
                ensure_running(daemon)
            except Exception as exc:
                if demand:
                    raise
                reconcile_failed(daemon, listener, exc)
                if startup_obligation(store, listener["run"]):
                    quarantine_failed(daemon, listener, exc)
                raise
    # Outside the reconcile lock: advancement may need another reconcile. Unproven inputs are never resent.
    ensure_running(daemon)
    run = store.get(listener["run"])
    if advance and run.get("workflow"):
        await daemon.writer.advance(run["id"])
    if notice:
        await daemon.messages.notify(listener["actor"], notice)


@asynccontextmanager
async def capacity(daemon):
    """Queueing has no deadline, retry or quarantine of its own. The caller remains
    registered with the stop fence; recursive replacement reuses this one slot."""
    task = asyncio.current_task()
    if task in daemon.recovery_holders:
        yield
        return
    daemon.recovery_queued += 1
    try:
        await daemon.recovery_capacity.acquire()
    finally:
        daemon.recovery_queued -= 1
    daemon.recovery_active += 1
    daemon.recovery_holders.add(task)
    try:
        yield
    finally:
        daemon.recovery_active -= 1
        daemon.recovery_holders.remove(task)
        daemon.recovery_capacity.release()


async def replay_native(daemon, listener):
    """Apply journal evidence in short batches; a crash repeats only the unapplied
    suffix. SQLite and effect commits stay on the daemon's event-loop thread."""
    store, incarnation = daemon.store, listener
    while True:
        ensure_running(daemon)
        # A server request runs outside the serial notification worker. Its durable
        # obligation, rather than receipt/dequeue, permits the applied prefix to pass.
        # After a crash never execute it again: expose uncertainty and let a repeated
        # native request receive the existing DYNAMIC_UNCERTAIN response path.
        after = ""
        while True:
            obligations = list(store.selected("SELECT * FROM records WHERE kind='native_obligation' AND parent=? "
                                               "AND json_extract(data,'$.state') IN ('pending','failed') "
                                               "AND id>? ORDER BY id LIMIT 25", (incarnation["id"], after)))
            if not obligations:
                break
            for obligation in obligations:
                ensure_running(daemon)
                if (obligation["state"] == "failed" or obligation["state"] == "pending"
                        and obligation["generation"] != store.generation):
                    row = store.db.execute("SELECT data FROM events WHERE seq=?", (obligation["sequence"],)).fetchone()
                    if row:
                        request_uncertain(daemon, incarnation, json.loads(row["data"]), obligation)
                await asyncio.sleep(0)
            after = obligations[-1]["id"]
        key = "native_applied:" + incarnation["id"]
        sequence = (store.get(key, required=False) or {}).get("sequence", 0)
        while True:
            rows = store.db.execute("SELECT seq,data FROM events WHERE kind='native' AND subject=? AND seq>? "
                                    "ORDER BY seq LIMIT 25", (incarnation["id"], sequence)).fetchall()
            if not rows:
                break
            for row in rows:
                ensure_running(daemon)
                notification = json.loads(row["data"])
                params, method = notification.get("params", {}), notification.get("method")
                if "id" in notification:
                    obligation = store.get("native_obligation:" + str(row["seq"]), required=False)
                    # Older daemons had no obligation records. Their durable response
                    # already proves completion; migration must not add two writes and
                    # a transaction for every previously completed tool request.
                    completed = False
                    if method == "item/tool/call":
                        dynamic = store.get("dynamic:" + incarnation["id"] + ":" + params["threadId"]
                                            + ":" + params["callId"], required=False)
                        completed = bool(dynamic and dynamic.get("response") is not None)
                    if not obligation and not completed:
                        store.put("native_obligation", "native_obligation:" + str(row["seq"]),
                            {"sequence": row["seq"], "generation": store.generation, "state": "pending"}, incarnation["id"])
                        obligation = store.get("native_obligation:" + str(row["seq"]))
                        request_uncertain(daemon, incarnation, notification, obligation)
                elif method == "hook/completed":
                    daemon.record_hook_evidence(listener, params, history=True)
                elif method in ("item/started", "item/completed") and params.get("item", {}).get("type") in (
                        "fileChange", "commandExecution", "mcpToolCall"):
                    operation = daemon.find_operation(listener, params["threadId"], params["turnId"], params["item"]["id"], history=True)
                    field = "completed_item" if method == "item/completed" else "started_item"
                    if operation and (field == "completed_item" or not operation.get(field)):
                        operation[field] = params["item"]
                        store.save(operation)
                        if field == "completed_item":
                            daemon.ledger.end_execution(operation)
                sequence = row["seq"]
                await asyncio.sleep(0)
            applied = store.get(key, required=False) or {}
            ensure_running(daemon)
            progress = {"sequence": max(sequence, applied.get("sequence", 0))}
            if applied.get("failed", 0) > sequence:
                progress["failed"] = applied["failed"]
            if applied.get("pending", 0) > sequence:
                progress["pending"] = applied["pending"]
            store.put("native_applied", key, progress, incarnation["id"])
            await asyncio.sleep(0)
        if not incarnation.get("replaces"):
            break
        incarnation = store.get(incarnation["replaces"])


def request_uncertain(daemon, listener, message, obligation):
    """Keep interrupted ownership; generation change is not evidence of an effect.

    Only a matching reissued native request may resume preparation. Connection-
    local request IDs are never sent on a replacement connection. A durable
    response resolves the obligation without executing the tool again.
    """
    ensure_running(daemon)
    store, params = daemon.store, message.get("params", {})
    with store.transaction():
        completed, stage = False, "received"
        if message.get("method") == "item/tool/call":
            ident = "dynamic:" + listener["id"] + ":" + params["threadId"] + ":" + params["callId"]
            existing = store.get(ident, required=False)
            completed = bool(existing and existing.get("response") is not None)
            stage = (existing.get("stage") or "effect") if existing else "received"
            obligation["dynamic"] = ident
        obligation.update(state="completed" if completed else "pending", stage=stage,
                          replay="response" if completed else "matching_request" if stage in ("received", "preparing") else "effect_evidence")
        store.save(obligation)


def ensure_running(daemon):
    daemon.check_native_stop()
    if daemon.stop.is_set():
        raise native.Retired()


def reconcile_failed(daemon, listener, exc):
    """Mark the run's current incarnation failed: hook(), deliveries and submissions refuse it, and maintenance
    reconciles it again once its retry is due.
    - The backoff starts at RECONCILE_RETRY_FIRST_S and doubles up to RECONCILE_RETRY_MAX_S. It is carried across
      replacements, so a replacement does not restart it.
    - The failure is recorded as a code with structured details, never native log text, and classified as
      retryable or not (retryable_failure).
    - A stop's own refusal (DAEMON_DRAINING) marks nothing."""
    daemon.check_native_stop()
    if isinstance(exc, native.Retired) or not listener.get("root_thread") or getattr(exc, "code", None) == "DAEMON_DRAINING":
        return
    store = daemon.store
    current = store.get(store.get(listener["actor"]).get("listener") or listener["id"])
    retries = current.get("recovery_retries", 0) + 1
    info = failure_info(exc)
    retryable, delay = retryable_failure(info), retry_delay(retries)
    current.update(recovery_state="failed", recovery_error=info["code"], recovery_error_details=info,
                   recovery_retryable=retryable, recovery_retries=retries, retry_at=time.time() + delay)
    store.save(current)
    store.event("listener/reconcile_failed", current["id"], dict(info, retryable=retryable, retries=retries,
                retry_at=current["retry_at"], delay_seconds=delay, max_seconds=RECONCILE_RETRY_MAX_S))


def failure_info(exc):
    """Persist codes and structured diagnostics, never native log text or configuration."""
    details = getattr(exc, "details", {}) or {}
    info = {"code": getattr(exc, "code", type(exc).__name__)}
    for key in ("method", "native_code", "exit_code", "errno"):
        if key in details:
            info[key] = details[key]
    if isinstance(exc, OSError):
        info["errno"] = exc.errno
    if info["code"] == "ADMISSION_FAILED":
        info["cases"] = []
        for case in details.get("cases", []):
            if case.get("passed"):
                continue
            error = case.get("error") or "PROBE_FAILED"
            # Admission's original case has a code; an adopted incident may already have our structured form.
            failure = (CoordinatorError(error.get("code") or "PROBE_FAILED", "", error) if isinstance(error, dict)
                       else CoordinatorError(error, "", case.get("details")))
            info["cases"].append({"probe": case.get("probe"), "error": failure_info(failure)})
    return info


def retryable_failure(info):
    """Temporary loss of evidence or capacity can heal; correctness refusals need intervention."""
    code = info["code"]
    if code == "ADMISSION_FAILED":
        cases = info.get("cases") or []
        return bool(cases) and all(retryable_failure(case["error"]) for case in cases)
    if code in {"NATIVE_INTAKE_STOPPED", "NATIVE_JOURNAL_FAILED", "WRITER_HISTORY_READ_FAILED", "WRITER_START_FAILED", "WRITER_START_PENDING", "NATIVE_UNCERTAIN", "NATIVE_DISCONNECTED", "LISTENER_SILENT", "TimeoutError", "HISTORY_CHANGED", "HISTORY_APPLY_PENDING",
                "ConnectionError", "ConnectionResetError", "ConnectionRefusedError", "BrokenPipeError", "EOFError",
                "EXCLUSIVE_WRITER", "CUSTODIAN_LOST", "LEASE_UNCERTAIN", "PROBE_TIMEOUT",
                "RESTORE_CUSTODY", "RESTORE_EXECUTING", "UNSTABLE_SNAPSHOT", "RECOVERY_BUSY", "REPLACEMENT_UNBOUND"}:
        return True
    if code in ("LISTENER_START", "PROBE_LISTENER"):
        # No exit yet is a socket-start timeout; a signal can reflect resource pressure.
        # A completed nonzero exit may instead prove malformed config: do not retry it blindly.
        return info.get("exit_code") is None or info["exit_code"] < 0
    return info.get("errno") in {errno.EAGAIN, errno.ENOMEM, errno.EMFILE, errno.ENFILE, errno.ENOSPC,
                                 errno.ECONNRESET, errno.ECONNREFUSED, errno.EPIPE, errno.ETIMEDOUT}


def retry_delay(retries):
    # Saturate the exponent too: an indefinitely failing writer never builds an enormous integer.
    ceiling = math.ceil(math.log2(RECONCILE_RETRY_MAX_S) - math.log2(RECONCILE_RETRY_FIRST_S))
    step = min(max(0, retries - 1), ceiling)
    return RECONCILE_RETRY_MAX_S if step == ceiling else math.ldexp(RECONCILE_RETRY_FIRST_S, step)


def startup_obligation(store, runid):
    """The incident belongs to the run, not the app-server incarnation it may replace."""
    run = store.get(runid)
    incident = store.get(run.get("startup_recovery"), required=False)
    return incident if incident and incident["state"] == "pending" else None


def retry_due(daemon, listener):
    incident = startup_obligation(daemon.store, listener["run"])
    return (listener.get("recovery_state") == "failed" and listener.get("retry_at", 0) <= time.time()
            and (incident["retryable"] if incident else listener["state"] != "quarantined"))


def startup_counts(store):
    incidents = list(store.selected("SELECT * FROM records WHERE kind='startup_recovery' "
                                    "AND json_extract(data,'$.state')='pending'"))
    retryable = sum(bool(i["retryable"]) for i in incidents)
    return {"pending": len(incidents), "retryable": retryable, "permanent": len(incidents) - retryable}


def listener_lineage(store, listener):
    found = set()
    while listener and listener["id"] not in found:
        found.add(listener["id"])
        listener = store.get(listener.get("replaces"), required=False)
    return found


def repair_startup_records(daemon):
    """Adopt 2.4.4 quarantine obligations and repair only predecessors proved replaced by durable lineage."""
    store = daemon.store
    for incident in list(store.all("startup_recovery")):
        if (incident.get("state") != "pending" or incident.get("retryable")
                or incident.get("error", {}).get("code") not in ("NATIVE_JOURNAL_FAILED", "NATIVE_INTAKE_STOPPED")):
            continue
        # Reclassify only this known local failure, retaining every UNKNOWN and
        # its incident ownership until normal history/filesystem reconciliation.
        with store.transaction():
            incident["retryable"] = True
            store.save(incident)
            listener = store.get(incident.get("listener"), required=False)
            if listener and listener.get("recovery_error") in ("NATIVE_JOURNAL_FAILED", "NATIVE_INTAKE_STOPPED"):
                listener.update(recovery_retryable=True, retry_at=0)
                store.save(listener)
            store.event("recovery/local_stop_reclassified", incident["id"], {"code": incident["error"]["code"]})
    for successor in list(store.all("listener")):
        previous = store.get(successor.get("replaces"), required=False)
        if not previous or previous["state"] != "dead" or previous.get("recovery_state") != "reconciling":
            continue
        bound = store.get(store.get(previous["actor"]).get("listener"), required=False)
        if (bound and successor["run"] == previous["run"] and successor["actor"] == previous["actor"]
                and successor["id"] in listener_lineage(store, bound) and bound["id"] != previous["id"]):
            with store.transaction():
                previous.update(recovery_state="replaced", replaced_by=successor["id"])
                store.save(previous)
                store.event("listener/replaced", previous["id"], {"successor": successor["id"], "repaired": True})
    for run in list(store.all("run")):
        actor = store.get(run.get("root_actor"), required=False)
        listener = store.get((actor or {}).get("listener"), required=False)
        if (not listener or not listener.get("root_thread") or startup_obligation(store, run["id"])
                or run.get("startup_recovery") or listener.get("recovery_state") != "failed"):
            continue
        lineage = listener_lineage(store, listener)
        unknowns = [row["id"] for row in store.db.execute(
            "SELECT id,context FROM unknowns WHERE reason='listener recovery incomplete' AND path='*'")
            if json.loads(row["context"]).get("listener") in lineage]
        if listener["state"] != "quarantined" and not unknowns:
            continue
        exc = CoordinatorError(listener.get("recovery_error") or "RECOVERY_INCOMPLETE", "",
                               listener.get("recovery_error_details"))
        quarantine_failed(daemon, listener, exc, unknowns=unknowns, reconstructed=True)


async def resolve_startup(daemon, listener, history):
    """Read history first, then checkout bytes; resolve only this incident's immutable UNKNOWN rows."""
    from .ledger import full_snapshot
    store = daemon.store
    incident = startup_obligation(store, listener["run"])
    if not incident:
        return None
    if daemon.hook_stale(listener):
        raise CoordinatorError("RECOVERY_BUSY", "Startup recovery still owes replacement with this release's hooks", {}, True)
    lineage = listener_lineage(store, listener)
    wanted = set(incident["unknowns"])
    for checkoutid in participants.checkout_ids(store, listener["unit"]):
        checkout = store.get(checkoutid)
        def quiet():
            daemon.ledger.require_owned(checkout)
            if daemon.custody.active(checkoutid):
                raise CoordinatorError("RESTORE_CUSTODY", "Startup recovery waits for checkout custody to settle", {}, True)
            refuse_executing(store, checkoutid)
        async with daemon.ledger.lock(checkoutid):
            quiet()
            began = time.time()
            selected = [u["id"] for u in store.unresolved(checkoutid)
                        if u["id"] in wanted and u["path"] == "*" and u["reason"] == "listener recovery incomplete"
                        and u["created"] < began and json.loads(u["context"]).get("listener") in lineage]
            known = {r[0] for r in store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkoutid,))}
            unreadable, listed = {}, set()
            actual = await finish_thread(full_snapshot, store, checkout, known, unreadable, listed)
            quiet()
            outside = await daemon.ledger.outside(checkout, actual, unreadable, listed)
            quiet()
            ident = uid("recovery_resolution")
            with store.transaction():
                result = daemon.ledger.apply_observation(checkout, set(actual), actual,
                    "unexplained bytes after startup history reconciliation", transaction=False,
                    outside=outside, unreadable=unreadable)
                # A lead resolution made while the read yielded is retained verbatim.
                unresolved = {u["id"] for u in store.unresolved(checkoutid)}
                selected = [key for key in selected if key in unresolved]
                evidence = {"run": listener["run"], "listener": listener["id"], "incident": incident["id"],
                            "failed_listener": incident["failed_listener"], "history": history, "checkout": checkoutid,
                            "scan_started": began, "created": time.time(), "unknowns": selected,
                            "paths_scanned": result["paths_scanned"], "found": result["unknowns"], "unreadable": unreadable}
                store.put("recovery_resolution", ident, evidence, incident["id"])
                for unknown in selected:
                    store.db.execute("INSERT INTO resolutions(unknown_id,restore,edit) VALUES(?,?,?) ON CONFLICT(unknown_id) "
                                     "DO UPDATE SET restore=excluded.restore,edit=excluded.edit WHERE resolutions.edit IS NULL",
                                     (unknown, ident, ident))
                incident = store.get(incident["id"])
                incident["resolutions"].append(ident)
                store.save(incident)
                store.event("unknown/recovery_resolved", ident, evidence)
    remaining = [key for key in wanted if store.db.execute(
        "SELECT 1 FROM unknowns u LEFT JOIN resolutions r ON r.unknown_id=u.id WHERE u.id=? AND r.edit IS NULL",
        (key,)).fetchone()]
    if remaining:
        raise CoordinatorError("RECOVERY_EVIDENCE", "Startup UNKNOWNs do not match the reconciled listener's participants and lineage",
                               {"unknowns": remaining})
    incident.update(state="complete", completed=time.time(), listener=listener["id"], history=history)
    return incident  # committed with the current listener's successful outcome, with no intervening await


def ancestry_order(actors):
    """Parent before child (stable within a depth): native resumes a sub-agent's thread only once its parent's is
    loaded ("resume the parent first")."""
    by_id = {actor["id"]: actor for actor in actors}
    def depth(actor):
        seen = {actor["id"]}
        while by_id.get(actor.get("parent_actor")) and actor["parent_actor"] not in seen:
            actor = by_id[actor["parent_actor"]]
            seen.add(actor["id"])
        return len(seen)
    return sorted(actors, key=depth)


async def stopped(listener, current):
    """Why a live app-server is to be ended for a stop, if it is: stopped by a signal at two reads of its process
    ANSWER_EVERY_S apart, as the same process (`current` is the first). It runs nothing while stopped, and a stop
    that lasts is nobody's pause. None otherwise."""
    if not current.get("stopped"):
        return None
    await asyncio.sleep(ANSWER_EVERY_S)
    again = await finish_thread(processes.identity, listener["pid"])
    if not again or again["birth"] != listener["birth"] or again["zombie"] or not again.get("stopped"):
        return None
    return "stopped by a signal, and still " + format(ANSWER_EVERY_S, "g") + " s later"


def silence(daemon, listener, why):
    """Report a live writer app-server that does not answer: once each time it falls silent (listener/silent), and in
    daemon/status ("silent") until it answers again (spoke). Never ended for it."""
    if listener["id"] not in daemon.silent:
        daemon.silent[listener["id"]] = {"since": time.time(), "why": why}
        daemon.store.event("listener/silent", listener["id"], {"why": why, "pid": listener["pid"]})


def spoke(daemon, listener, took):
    """A writer app-server reported silent answered again."""
    found = daemon.silent.pop(listener["id"], None)
    if found:
        daemon.store.event("listener/answered_again", listener["id"], {"seconds": round(took, 3),
                                                                       "silent_seconds": round(time.time() - found["since"], 3)})


async def answers(daemon, listener):
    """Before a pass works with an attached app-server: it answers thread/loaded/list within ANSWER_S, or it is
    reported silent and the pass is refused LISTENER_SILENT (retryable), the app-server left as it is. One not attached
    is probed by connecting to it (_reconcile_listener)."""
    connection = daemon.connections.get(listener["id"])
    if not connection or connection.closed:
        return
    try:
        took = await answer(daemon, listener, connection)
    except (OSError, CoordinatorError):
        return  # its connection broke: the pass connects again
    if took is None:
        why = "thread/loaded/list unanswered for " + format(ANSWER_S, "g") + " s"
        silence(daemon, listener, why)
        raise CoordinatorError("LISTENER_SILENT", "The writer's app-server does not answer; it is left running and "
                               "reconciled again later", {"listener": listener["id"], "why": why}, True)
    spoke(daemon, listener, took)


async def answer(daemon, listener, connection):
    """The seconds a writer app-server took to answer thread/loaded/list, or None when it left it unanswered for
    ANSWER_S. An answer later than 80 % of ANSWER_S records listener/answer_slow: healthy app-servers near the bound
    show before one is reported silent."""
    began = time.monotonic()
    try:
        await connection.call("thread/loaded/list", {}, timeout=ANSWER_S)
    except CoordinatorError as exc:
        if exc.code == "NATIVE_UNCERTAIN":
            return None
        raise
    took = time.monotonic() - began
    if took > 0.8 * ANSWER_S:
        daemon.store.event("listener/answer_slow", listener["id"], {"seconds": round(took, 3), "bound": ANSWER_S})
    return took


async def end_wedged(daemon, listener, why, reason):
    """End a live app-server nothing can be done with: stopped by a signal (`reason` "stopped"), or alive behind a
    connection that is refused, gone or closed on it ("closed"). SIGTERM, then SIGCONT (a stopped one acts on it
    then), and SIGKILL once READMIT_DRAIN_S passes, its birth identity checked before each signal (stop_listener).
    The pass that follows finds it gone and replaces it through re-admission, as one that died, and tells its writer
    why (the "ended" notice): its writer stays usable. Counted in daemon/status ("ended", by reason)."""
    store = daemon.store
    listener = store.get(listener["id"])
    listener.update(wedged=reason, ended=why)
    store.save(listener)
    daemon.ended[reason] = daemon.ended.get(reason, 0) + 1
    daemon.silent.pop(listener["id"], None)
    store.event("listener/wedged", listener["id"], {"why": why, "reason": reason, "pid": listener["pid"]})
    await daemon.stop_listener(listener, drain_s=READMIT_DRAIN_S, kill=True)


async def reconcile_pass(daemon, listener):
    """_reconcile_listener, made once more when its app-server's connection closed under it, or a request went
    unanswered: the second pass replaces an app-server that has gone, or is found stopped (it is ended), as one that
    died, and connects again to one that lives (a connection refused there ends it). One that lives and is silent is
    reported, and the failure stands: it is reconciled again later, never ended for it. One replaced during the first
    pass is not ended again: that failure is its replacement's, retried as any other."""
    store = daemon.store
    try:
        return await _reconcile_listener(daemon, listener)
    except (OSError, CoordinatorError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        # Its connection closed shows as NATIVE_DISCONNECTED when the receive loop read the end first, and as the
        # write's own error (a ConnectionError: BrokenPipeError, ConnectionResetError) when a request was written first.
        if not (closed(exc) or silent(exc)):
            raise
        current = store.get(listener["id"])
        if not current.get("root_thread") or store.get(current["actor"]).get("listener") != current["id"]:
            raise
        identity = await finish_thread(processes.identity, current["pid"])
        if identity and identity["birth"] == current["birth"] and not identity["zombie"]:
            why = await stopped(current, identity)
            if why:
                await end_wedged(daemon, current, why + ", after " + code, "stopped")
            elif silent(exc):
                silence(daemon, current, code + " for " + str((getattr(exc, "details", None) or {}).get("method", "its connection")))
                raise
        store.event("listener/reconcile_again", current["id"], {"code": code})
        return await _reconcile_listener(daemon, store.get(current["id"]))


async def _reconcile_listener(daemon, listener):
    """reconcile_listener's work, under the run's lock. Returns the notice for the lead, if any."""
    store = daemon.store
    daemon.ledger.require_owned(store.get(store.get(listener["actor"])["checkout"]))
    listener["recovery_state"] = "reconciling"
    store.save(listener)
    current = await finish_thread(processes.identity, listener["pid"])
    alive = current and current["birth"] == listener["birth"] and not current["zombie"]
    # A replacement is fresh until a pass over it ends: one whose first pass failed part-way may not have resumed its
    # threads or passed its writer gate, so its next pass does all of that again (a resume of a loaded thread is
    # delegated to that thread), and that pass sends the notice the first one owed.
    fresh = not alive or bool(listener.get("initializing"))
    actor = store.get(listener["actor"])
    run = store.get(actor["run"])
    # The receipts its log holds settle the calls they name before any crash quarantine: a refused patch is no
    # effect at its pre-images, in every checkout it spans.
    await daemon.settle_native_log(listener["id"])
    if not listener.get("root_thread"):
        # A threadless bootstrap can be stopped without inventing a writer identity. It was never given work: it needs
        # no lease, its unit's operations are its writer's to reconcile, and a record whose process is gone is retired.
        await daemon.stop_listener(listener)
        return
    if alive:
        why = await stopped(listener, current)
        if why:
            await end_wedged(daemon, listener, why, "stopped")
            return await _reconcile_listener(daemon, store.get(listener["id"]))
        await answers(daemon, listener)  # attached and silent: reported, and this pass refused (LISTENER_SILENT)
    for operation in list(store.all("operation", actor["unit"])):
        if operation.get("parts") and operation["state"] in ("prepared", "allowed", "running"):
            if daemon.ledger.current_lead(operation):
                # A writer reconnect does not interrupt this daemon's lead edit
                # or restore. Quarantining its parent would also quarantine and
                # observe every part before the snapshot can preserve them.
                continue
            if (operation["kind"] == "patch" and operation.get("completed_item")
                    and operation.get("hook_valid") and operation.get("post_hook")):
                try:
                    await daemon.ledger.complete_patch(operation, operation["completed_item"])
                except CoordinatorError:
                    pass  # the receipt owner durably records insufficient evidence
                continue
            await daemon.ledger.quarantine(operation, "cross-checkout operation interrupted by native reconnect",
                                           executing=await may_execute(daemon, operation))
    unsubmitted = not run.get("workflow") and not run.get("task") and not run.get("production_started") and not run.get("phases")
    for checkoutid in participants.checkout_ids(store, actor["unit"]):
        await daemon.ensure_lease(checkoutid)
    unit = store.get(actor["unit"])
    roots = participants.roots(store, unit)
    cwd = run.get("thread_cwd", store.get(actor["checkout"])["root"])
    from .projects import verify_thread
    was_dormant = listener["state"] == "dormant" or listener.get("initializing") == "dormant"
    stale = daemon.hook_stale(listener)
    readmitted = stale and listener["state"] == "stopped"  # stopped below, by an earlier pass, for its hook program
    if not alive:
        await daemon.detach_listener(listener["id"])
        ended = ("listener ended by the coordinator (" + listener["ended"] + "), with potentially escaped effects"
                 if listener.get("ended") else "listener died with potentially escaped effects")
        for reservation in daemon.custody.unit_active(actor["unit"]):
            if reservation["unit"] != actor["unit"]:
                continue
            for process in reservation.get("processes", []):
                try:
                    await finish_thread(processes.terminate, process, actor["session"])
                except CoordinatorError as exc:
                    reservation.update(state="quarantined", reason=exc.code)
                    store.save(reservation)
            store.unknown(reservation["checkout"], "*", ended, {"listener": listener["id"]})
        # The receipts it wrote before it died apply to the calls it admitted; never its replacement's.
        await daemon.settle_native_log(listener["id"])
        await daemon.executor_exited(listener)
        listener = store.get(listener["id"])
        if daemon.dormancy.eligible(listener):
            try:
                await daemon.dormancy.recover(listener)
            except (CoordinatorError, OSError) as exc:
                raise CoordinatorError("WRITER_HISTORY_READ_FAILED", "Read-only recovery failed; the exited writer is retained for retry",
                                       {"cause": getattr(exc, "code", type(exc).__name__), "retryable": True}, True) from exc
            return None
        listener["state"] = "dead"
        store.save(listener)
        if listener.get("log"):
            Path(listener["log"]).unlink(missing_ok=True)
            daemon.log_offsets.pop(listener["id"], None)
        # A replacement re-reads the native catalog and the current writer profile,
        # including same-release cold starts. Keep the pre-profile overrides so a
        # later "all" can re-enable a server our earlier profile disabled.
        replacement = await daemon.admit_listener(actor,
            daemon.readmission_config(listener.get("profile_base_config", listener["config"])),
            listener.get("home"), roots, cwd)
        replacement["root_thread"] = listener["root_thread"]
        replacement["replaces"] = listener["id"]
        # Reconciling until this pass ends, in the same save that makes it findable: hook(), deliveries and
        # submissions refuse it until its native history and its unit's operations are read back.
        replacement["recovery_state"] = "reconciling"
        # The notice its first full pass sends: why the coordinator ended it, when it did (end_wedged, runs.end_writer).
        replacement["initializing"] = "dormant" if was_dormant else "ended" if listener.get("ended") else "readmitted" if readmitted else "died"
        # A replacement attempt does not restart the run's backoff.
        for key in ("recovery_retries", "retry_at", "recovery_error", "recovery_error_details", "recovery_retryable"):
            if key in listener:
                replacement[key] = listener[key]
        with store.transaction():
            store.save(replacement)
            listener.update(state="dead", recovery_state="replaced", replaced_by=replacement["id"])
            store.save(listener)
            for child in list(store.all("actor", actor["unit"])):
                if child["kind"] == "lead":
                    continue
                child["listener"] = replacement["id"]
                child["active_turn"] = None
                child["state"] = "recovering"
                store.save(child)
            store.event("listener/replaced", listener["id"], {"successor": replacement["id"], "run": listener["run"]})
        listener = replacement
    connection = daemon.connections.get(listener["id"])
    attached = bool(connection and not connection.closed)
    if not connection or connection.closed:
        try:
            connection = await daemon.attach(listener)
        except (OSError, CoordinatorError, EOFError) as exc:  # EOFError: the handshake's end read (IncompleteReadError)
            # A dying app-server can remove its socket after the initial birth
            # check but before attach. Recheck that exact process before choosing
            # live reconnect versus replacement; never advance on a failed bind.
            for _ in range(20):
                current = await finish_thread(processes.identity, listener["pid"])
                if not current or current["birth"] != listener["birth"] or current["zombie"]:
                    return await _reconcile_listener(daemon, store.get(listener["id"]))
                await asyncio.sleep(0.05)
            code = getattr(exc, "code", type(exc).__name__)
            why = await stopped(listener, current) if silent(exc) else None
            if why or closed(exc):
                # It lives, and nothing can be done with it: stopped, or its socket refused, gone or closed on it. Ended,
                # it is replaced by the pass that follows, as one that died is.
                await end_wedged(daemon, listener, why or "its socket refused or closed the connection while it ran: " + code,
                                 "stopped" if why else "closed")
                return await _reconcile_listener(daemon, store.get(listener["id"]))
            if silent(exc):
                silence(daemon, listener, "no answer on its socket (" + code + ")")
            raise
    readmit = fresh or listener.get("admission_fingerprint", {}).get("adapter_digest") != await finish_thread(native.adapter_digest)
    resuming = not attached or readmit or bool(listener.get("recovery_error")) or bool(startup_obligation(store, listener["run"]))
    if resuming:
        # Full history is paged below, so returning it here is redundant. 2026-10-22, 26 writers/4 checkouts:
        # slowest root resume 0.117 s before, 0.094 s with excludeTurns (0 timeouts; sampled load up to 18.7).
        # This fixture leaves ample headroom in the existing 60 s RPC timeout; it does not justify a new cap.
        response = await connection.call("thread/resume", {"threadId": listener["root_thread"],
                                          "approvalPolicy": "never", "sandbox": "danger-full-access", "excludeTurns": True,
                                          **({"cwd": cwd, "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {})})
        native.policy_echo(response)
        verify_thread(response, unit, cwd, roots)
    if alive:
        # A message of ours in the native queue of an app-server another release admitted starts on that release's
        # hooks the moment its thread is idle, with no request of ours (messages.withdraw_stale): taken back as soon as
        # it is connected, and once more before it is judged for replacement below.
        await daemon.messages.withdraw_stale(listener)
    if fresh:
        # A replacement has not executed a turn. Its own infrastructure is a
        # fresh service baseline; prior command survivors retain their separate
        # birth-bound records, including those outside this listener's tree.
        listener = store.get(listener["id"])
        listener["services"] = await finish_thread(processes.inventory, listener, None)
        store.save(listener)
    if readmit and alive and stale:
        # It runs another release's hook program: hook() refuses every call it makes (HOOK_PROGRAM_CHANGED), so no
        # admission would let it run anything, and it is replaced once its unit is quiet (below, or by
        # maintenance's re-admission). Its gate would calibrate a binding nothing ever uses (2.4.3's switch: a
        # calibration of about 25 s for each).
        store.event("admission/writer_skipped", listener["id"], {"reason": "HOOK_PROGRAM_CHANGED"})
    elif readmit:
        from .admission import Admission
        await Admission(store).writer_gate(daemon, listener["id"], response, fixture=bool(listener.get("home")))
        listener = store.get(listener["id"])
    listed = await connection.call("hooks/list", {"cwds": roots})
    native.coordinator_hooks(listed, roots, expected=listener.get("hooks", []))
    # Connection.call's fixed receipt barrier committed all notifications read
    # before this response, independently of application and newer intake.
    # Recover that durable evidence before judging an intent's missing effects.
    await replay_native(daemon, listener)
    # Failed results it wrote while no daemon read its log, judged with every item replayed above
    # (its log is not followed while it reconciles).
    await daemon.settle_native_log(listener["id"])
    # Discover every loaded descendant before reconciling its items. Parent events
    # may have been missed; thread/read supplies the native ancestry.
    await daemon.discover_descendants(listener)
    history_threads = []
    seen_threads = set()
    while True:
        work = [a for a in ancestry_order(list(store.all("actor", actor["unit"])))
                if a["kind"] != "lead" and a["thread"] not in seen_threads]
        if not work:
            break
        child = work[0]
        seen_threads.add(child["thread"])
        if child["kind"] == "lead":
            continue
        if unsubmitted and child["thread"] == listener["root_thread"]:
            # Native does not materialize history until the first user input.
            # This admitted, still-attached thread has never received one.
            if not attached:
                raise CoordinatorError("THREAD_UNMATERIALIZED", "The unused native thread was disconnected; create a new unit")
            history_threads.append({"actor": child["id"], "thread": child["thread"], "unsubmitted": True})
            continue
        if child["thread"] != listener["root_thread"] and (resuming or fresh):
            # A new app-server connection must subscribe to each live child too, and a replacement has loaded only
            # the root. Resuming history does not start a turn or repeat its input.
            child_response = await connection.call("thread/resume", {"threadId": child["thread"],
                "approvalPolicy": "never", "sandbox": "danger-full-access", "excludeTurns": True,
                **({"cwd": child.get("cwd", cwd), "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {})})
            native.policy_echo(child_response)
            # Native 0.163.0 children inherit roots and ancestry, but their own
            # project_id is null. Preserve that observed association on resume.
            verify_thread(child_response, dict(unit, project=child.get("native_project")), child.get("cwd", cwd), roots)
        recorded = child.get("active_turn")
        observed = await daemon.history.observe(listener, child, connection)
        history_threads.append(observed)
        child = store.get(child["id"])
        last = observed.get("last_turn")
        run = store.get(child["run"])
        if (child["kind"] == "root" and last and last["status"] != "inProgress" and not child.get("active_turn")
                and (recorded or run["state"] == "running") and not run.get("phases") and not run.get("workflow")
                and not run.get("cancel_requested")):
            run["state"] = last["status"]
            store.save(run)
    await reconcile_effects(daemon, listener, actor)
    history = {"completed": time.time(), "threads": history_threads}
    history["event"] = store.event("recovery/history", listener["id"], dict(history, run=listener["run"]))
    # A failing checkout must not leave sibling scans running after this invocation leaves the stop fence.
    results = await finish_all(*(daemon.ledger.reconcile(store.get(key), ignore_pending=False, preserve_leads=True,
                                                          reason="recovery snapshot")
                                    for key in participants.checkout_ids(store, actor["unit"])), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if alive:
        await daemon.messages.withdraw_stale(listener)  # a unit held only by a message queued there reads quiet below
    listener = store.get(listener["id"])
    if alive and not connection.closed and daemon.hook_stale(listener) and daemon.unit_quiet(actor["unit"]):
        # Admitted by another release, its hooks run that release's program, and hook() refuses every call it
        # makes (HOOK_PROGRAM_CHANGED). Its native turns are read now, and while it reconciles no hook, delivery or
        # submission starts work: with nothing of its unit in flight, it is stopped (no notification gap) and
        # replaced by an app-server admitted with this release's hooks, which resumes the same thread.
        await daemon.stop_listener(listener, drain_s=READMIT_DRAIN_S, kill=True)
        return await _reconcile_listener(daemon, store.get(listener["id"]))
    incident = await resolve_startup(daemon, listener, history)
    listener = store.get(listener["id"])
    listener.update(recovery_state="reconciled", state="connected")
    for key in ("recovery_error", "recovery_error_details", "recovery_retryable", "recovery_retries", "retry_at"):
        listener.pop(key, None)
    initialized = listener.pop("initializing", None)
    with store.transaction():
        store.save(listener)
        if incident:
            store.save(incident)
            store.event("listener/startup_recovered", listener["id"],
                        {"run": listener["run"], "incident": incident["id"], "resolutions": incident["resolutions"],
                         "seconds": round(incident["completed"] - incident["created"], 3)})
    if initialized == "dormant":
        gaps = list(store.all("history_gap", listener["run"]))
        return ("The writer started from dormancy on the same thread. " + RUNTIME_NOTE +
                (" HISTORY GAP: " + str(len(gaps)) + " received completed items were absent or different in saved history; inspect codex_run_log." if gaps else ""))
    if initialized == "readmitted":
        return ("Re-admitted with this release's hooks: a replacement app-server resumed this thread without continuing "
                "it. Nothing of the unit was in flight. " + RUNTIME_NOTE)
    if initialized == "died":
        return ("The listener died. A replacement cold-resumed this thread without continuing it. Effects were "
                "reconciled; inspect UNKNOWN and retained custody before further mutations. " + RUNTIME_NOTE)
    if initialized == "ended":
        why = (store.get(listener.get("replaces") or "", required=False) or {}).get("ended") or "by the coordinator"
        unknown = any(store.unresolved(key) for key in participants.checkout_ids(store, actor["unit"]))
        return ("The coordinator ended this writer's app-server (" + why + "); a replacement resumed this thread "
                "without continuing it." + (" Effects were reconciled; inspect UNKNOWN and retained custody before "
                "further mutations." if unknown else "") + " " + RUNTIME_NOTE)
    return None


async def reconcile_effects(daemon, listener, actor):
    store = daemon.store
    for operation in list(store.all("operation", actor["unit"])):
        if operation["state"] in ("prepared", "allowed", "running"):
            if daemon.ledger.current_lead(operation):
                # A lead attached to this writer's unit can still be editing.
                # Its own completion settles it; only earlier generations have
                # a crash gap. Recovery's snapshot preserves the same hold.
                continue
            if operation.get("parent_operation"):
                continue
            if operation["kind"] == "patch" and operation.get("hook_valid") and operation.get("post_hook") and operation.get("completed_item"):
                try:
                    await daemon.ledger.complete_patch(operation, operation["completed_item"])
                except CoordinatorError:
                    pass  # complete_patch durably quarantines insufficient evidence
            elif operation["kind"] in ("command", "mcp"):
                if not operation.get("custody"):
                    await daemon.ledger.quarantine(operation, "crash gap before a durable custody decision")
                else:
                    await daemon.settle_actor(store.get(operation["actor"]))
            else:
                await daemon.ledger.quarantine(operation, "crash gap: intent is not proof of execution or no effect",
                                               executing=await may_execute(daemon, operation))


def recoverable_listener(listener):
    """Whether startup recovery reconciles it: not a threadless bootstrap that was stopped, and not dead."""
    return listener["state"] not in ("dead", "dormant") and not (listener["state"] == "stopped" and not listener.get("root_thread"))


def defer_unbound(store):
    """A writer still bound to a dead app-server (its daemon stopped before the replacement bound) is reconciled once
    this daemon serves, by maintenance's retry, one writer at a time: not by startup recovery, where its leases and
    failures would hold every other writer's recovery."""
    for listener in list(store.all("listener")):
        if (listener["state"] == "dead" and listener.get("root_thread") and listener.get("recovery_state") != "failed"
                and store.get(listener["actor"]).get("listener") == listener["id"]):
            listener.update(recovery_state="failed", recovery_error="REPLACEMENT_UNBOUND", retry_at=0)
            store.save(listener)
            store.event("listener/reconcile_failed", listener["id"], {"code": "REPLACEMENT_UNBOUND", "retries": 0, "retry_at": 0})


async def recover(daemon):
    """Startup recovery's first part, before the daemon admits a call: the ledger made consistent with a crash at any
    point, without a request to an app-server. Returns the plan recover_writers carries out in the background once
    calls are admitted; until each scope it names (a run, a unit, a checkout) is done it is in daemon.recovering,
    and a call for it waits (recovered, writer_ready)."""
    store = daemon.store
    began = time.monotonic()
    store.event("recovery/start", "daemon", {"generation": store.generation})
    from .intents import invalidate_windows
    invalidate_windows(daemon)
    await daemon.ledger.recover_ownership()
    from .lead_calls import recover as recover_lead_calls
    await recover_lead_calls(daemon)
    for request in list(store.all("custody_request")):
        if request["state"] == "waiting":
            request.update(state="interrupted", recovered_generation=store.generation)
            store.save(request)
            store.event("custody/wait_interrupted", request["id"], {})
    for attempt in list(store.all("attempt")):
        attempt["generation"] += 1
        attempt["daemon_generation"] = store.generation
        store.save(attempt)
    repair_startup_records(daemon)
    defer_unbound(store)
    # All units regain coverage before any listener reconnects or input advances. A threadless bootstrap needs none: it
    # is stopped, never given work. A checkout whose lease is refused (a writer outside the coordinator took it while no
    # daemon ran) fails only its own writers' recovery: each reconcile takes its leases again before it reconnects, and
    # is quarantined with an UNKNOWN when refused. Every other checkout recovers.
    permanent = {i["run"] for i in store.all("startup_recovery") if i["state"] == "pending" and not i["retryable"]}
    recoverable = [l for l in store.all("listener") if l["run"] not in permanent and
                   (recoverable_listener(l) or l.get("root_thread") and
                    store.get(l["actor"]).get("listener") == l["id"] and
                    (l["state"] == "dead" and daemon.dormancy.eligible(l) or daemon.dormancy.pending(l["run"])))]
    for checkoutid in sorted({key for l in recoverable if l.get("root_thread") for key in participants.checkout_ids(store, l["unit"])}):
        try:
            await daemon.ensure_lease(checkoutid)
        except CoordinatorError as exc:
            store.event("recovery/lease_refused", checkoutid, {"code": exc.code, "message": str(exc)[:300]})
    # Reconciling from here, so hook(), deliveries and submissions refuse each until its own pass ends: that state,
    # not the daemon-wide barrier, holds them once calls are admitted.
    for listener in recoverable:
        listener["recovery_state"] = "reconciling"
        store.save(listener)
    # A multi-checkout invocation commits receipts only at its original atomic
    # completion boundary. History cannot retrospectively complete one whose
    # transaction did not commit, even if every post-image now matches.
    # Fence it before reconnecting: queued native events may arrive immediately.
    # The receipts each listener's log holds come first: a patch native refused before the crash is settled at
    # its pre-images, and one it may still be writing keeps its paths (`executing`).
    for listener in recoverable:
        await daemon.settle_native_log(listener["id"])
    for operation in list(store.all("operation")):
        if operation.get("parts") and operation["state"] in ("prepared", "allowed", "running"):
            await daemon.ledger.quarantine(operation, "cross-checkout operation interrupted before atomic receipt completion",
                                           executing=await may_execute(daemon, operation))
    for discovery in list(store.all("project_discovery")):
        if discovery["state"] == "running":
            current = await finish_thread(processes.identity, discovery["pid"])
            if current and current["birth"] == discovery["birth"]:
                os.kill(current["pid"], signal.SIGTERM)
            discovery["state"] = "interrupted"
            store.save(discovery)
    # Lead operations have no native history to fill the crash gap: fenced before any lead call is admitted.
    for operation in list(store.all("operation")):
        if operation["kind"] in ("lead-edit", "restore", "lead-command") and operation["state"] in ("prepared", "allowed", "running"):
            await daemon.ledger.quarantine(operation, "lead operation interrupted before verified completion")
    from .publish import Publisher
    publisher = Publisher(daemon)
    # An interrupted candidate is never treated as reviewed, including legacy sealing units without attempts.
    for unit in list(store.all("unit")):
        attempt = store.get(unit.get("seal_attempt"), required=False)
        if unit["state"] == "sealing" or attempt and attempt["state"] == "started":
            publisher.interrupt_seal(unit["id"], "worker interrupted sealing")
    plan = recovery_plan(daemon, recoverable)
    # For retry_after: this recovery's writer ends, and how long the last one that reconciled writers took.
    last = store.db.execute("SELECT json_extract(data,'$.seconds') FROM events WHERE subject='daemon' AND kind='recovery/complete' "
                            "AND json_extract(data,'$.runs') > 0 AND json_extract(data,'$.seconds') IS NOT NULL "
                            "ORDER BY seq DESC LIMIT 1").fetchone()
    daemon.recovering.update(plan["events"])
    daemon.recovery_began, daemon.recovery_ends, daemon.recovery_measured = time.monotonic(), [], last[0] if last else None
    daemon.recovery_barrier = False
    store.event("recovery/serving", "daemon", {"generation": store.generation, "seconds": round(time.monotonic() - began, 3),
                                                "runs": len(plan["runs"]), "units": len(plan["units"]), "checkouts": len(plan["checkouts"])})
    return plan


def recovery_plan(daemon, recoverable):
    """What recover_writers reconciles, scope by scope: each run's writer app-servers (the incarnation each record
    leads to, once: a stopped one already replaced leads to its replacement), each checkout's filesystem snapshot
    after every run in it, each unit's publications after its runs and its checkouts' snapshots."""
    store = daemon.store
    runs = {}
    for listener in recoverable:
        bound = store.get(listener["actor"]).get("listener")
        target = bound if (listener.get("root_thread") and listener["state"] in ("dead", "stopped", "dormant") and bound
                           and bound != listener["id"]) else listener["id"]
        targets = runs.setdefault(listener["run"], [])
        if target not in targets:
            targets.append(target)
    checkouts = {checkout["id"]: [] for checkout in store.all("checkout")}
    units = {}
    for runid in runs:
        unit = store.get(runid)["unit"]
        units.setdefault(unit, {"runs": [], "publications": [], "groups": []})["runs"].append(runid)
        for key in participants.checkout_ids(store, unit):
            checkouts.setdefault(key, []).append(runid)
    for publication in store.all("publication"):
        if not publication.get("group") and publication["phase"] not in ("complete", "abandoned"):
            units.setdefault(publication["unit"], {"runs": [], "publications": [], "groups": []})["publications"].append(publication["id"])
    for group in store.all("publication_group"):
        if group["phase"] not in ("complete", "abandoned", "superseded"):
            units.setdefault(group["unit"], {"runs": [], "publications": [], "groups": []})["groups"].append(group["id"])
    events = {("run", key): asyncio.Event() for key in runs}
    events.update({("checkout", key): asyncio.Event() for key in checkouts})
    events.update({("unit", key): asyncio.Event() for key in units})
    return {"runs": runs, "checkouts": checkouts, "units": units, "events": events}


def quarantine_failed(daemon, listener, exc, unknowns=None, reconstructed=False):
    """A writer whose startup reconcile failed is quarantined, with an UNKNOWN in each of its unit's checkouts,
    since its history was not read back.
    - One incident per run (startup_recovery) holds those UNKNOWNs and the failure's classification, and it
      outlives replacements.
    - A retryable incident is reconciled again by maintenance once its retry is due, and a resume reconciles it
      at once. resolve_startup then resolves exactly its UNKNOWNs, after the history read and a read of each
      checkout.
    - One that cannot pass (a completed nonzero exit, a refused policy) stays quarantined for the lead."""
    daemon.check_native_stop()
    if isinstance(exc, native.Retired) or not listener.get("root_thread") or getattr(exc, "code", None) == "DAEMON_DRAINING":
        return
    store = daemon.store
    current = store.get(store.get(listener["actor"]).get("listener") or listener["id"])
    run = store.get(current["run"])
    info = failure_info(exc)
    retryable = retryable_failure(info)
    incident = startup_obligation(store, run["id"])
    with store.transaction():
        if not incident:
            ident = uid("startup_recovery")
            selected = list(unknowns or [])
            if not selected:
                selected = [store.unknown(key, "*", "listener recovery incomplete",
                    {"listener": current["id"], "run": run["id"], "incident": ident, "code": info["code"]})
                    for key in participants.checkout_ids(store, current["unit"])]
            store.put("startup_recovery", ident, {"run": run["id"], "failed_listener": current["id"],
                "generation": store.generation, "created": time.time(), "state": "pending",
                "unknowns": selected, "resolutions": [], "reconstructed": reconstructed}, run["id"])
            run["startup_recovery"] = ident
            store.save(run)
            incident = store.get(ident)
        incident.update(error=info, retryable=retryable, listener=current["id"], last_failed=time.time())
        store.save(incident)
        current.update(state="quarantined", recovery_state="failed", recovery_error=info["code"],
                       recovery_error_details=info, recovery_retryable=retryable)
        current.setdefault("retry_at", time.time() + RECONCILE_RETRY_FIRST_S)
        store.save(current)
        store.event("listener/startup_quarantined", current["id"],
                    {"incident": incident["id"], "unknowns": incident["unknowns"], "error": info, "retryable": retryable})


async def recover_writers(daemon, plan):
    """Startup recovery's second part, in the background while calls are admitted. One task for each run: its writer
    app-servers reconciled in turn (a pass resumes a parent's thread before its sub-agents'), then its workflow
    advanced. Runs reconcile in parallel: what two of them share is serialized where it is shared (a checkout's
    ledger lock, lease lock, a calibration's binding). Each checkout's filesystem snapshot follows every run in it,
    and each unit's publications its runs and its checkouts' snapshots, as in a sequential recovery."""
    store = daemon.store
    await daemon.ledger.scans.recover()
    ensure_running(daemon)
    events = plan["events"]
    stopped = None
    began = time.monotonic()

    def done(scope):
        nonlocal stopped
        outcome = sys.exception()
        if isinstance(outcome, native.IntakeStopped):
            stopped = outcome
        events[scope].set()
        if stopped is None and daemon.native_stop_error is None and not daemon.stop.is_set():
            daemon.recovering.pop(scope, None)

    async def dependencies(scopes):
        await asyncio.gather(*(events[scope].wait() for scope in scopes))
        ensure_running(daemon)
        if stopped is not None:
            raise stopped
        # A scope ending releases its dependents even if its connection retired
        # or its task failed. Each dependent reconciles its own durable evidence.
        # Only a daemon stop forbids starting that work.

    def bound(ident):
        """The incarnation the run's writer is bound to now (a pass may have replaced the one it began with)."""
        return store.get(store.get(store.get(ident)["actor"]).get("listener") or ident, required=False) or {}

    async def writer(runid, listeners):
        started = time.monotonic()
        try:
            for ident in listeners:
                ensure_running(daemon)
                listener = store.get(ident)
                try:
                    if daemon.dormancy.pending(runid):
                        await daemon.dormancy.start(runid, "recovery")
                    else:
                        await reconcile_listener(daemon, listener, advance=False)
                except Exception as exc:
                    if getattr(exc, "code", "").startswith("WRITER_") or getattr(exc, "code", None) in ("UNIT_FROZEN", "RUN_CANCELLED"):
                        store.event("writer/recovery_deferred", runid, {"code": exc.code})
                    else:
                        quarantine_failed(daemon, listener, exc)
            ensure_running(daemon)
            if store.get(runid).get("workflow") and bound(listeners[-1]).get("recovery_state") == "reconciled":
                try:
                    await daemon.writer.advance(runid)
                except CoordinatorError as exc:
                    store.event("workflow/advance_refused", runid, exc.envelope())
        finally:
            try:
                daemon.recovery_ends.append(time.monotonic() - daemon.recovery_began)
                current = bound(listeners[-1])
                store.event("recovery/writer", runid, {"seconds": round(time.monotonic() - started, 3),
                                                       "since_serving": round(time.monotonic() - began, 3),
                                                       "listener": current.get("id"), "state": current.get("state"),
                                                       "recovery_state": current.get("recovery_state"),
                                                       "code": current.get("recovery_error")})
            finally:
                done(("run", runid))

    async def snapshot(checkoutid, runs):
        try:
            await dependencies([("run", key) for key in runs])
            checkout = store.get(checkoutid)
            if checkout.get("ready") and not checkout.get("quarantine") and checkout.get("identity") in daemon.ledger.ownership:
                try:
                    await daemon.ledger.reconcile(checkout, reason="daemon recovery filesystem snapshot")
                except Exception as exc:
                    checkout = store.get(checkoutid)
                    checkout["quarantine"] = getattr(exc, "code", type(exc).__name__)
                    store.save(checkout)
                    store.unknown(checkoutid, "*", "recovery filesystem snapshot incomplete", {})
        finally:
            done(("checkout", checkoutid))

    async def publications(unitid, scope):
        from .publish import Publisher
        from .group_publish import GroupPublisher
        try:
            await dependencies([("run", key) for key in scope["runs"]] +
                               [("checkout", key) for key in participants.checkout_ids(store, unitid)
                                if ("checkout", key) in events])
            publisher = Publisher(daemon)
            for ident in scope["publications"]:
                ensure_running(daemon)
                publication = store.get(ident)
                if publication["phase"] in ("complete", "abandoned"):
                    continue
                try:
                    unit = store.get(publication["unit"])
                    async with daemon.ledger.lifecycle.hold(unit["checkout"]):
                        async with daemon.unit_locks.hold(unit["id"]):
                            await publisher.reconcile(store.get(ident))
                except CoordinatorError as exc:
                    store.event("publication/recovery_refused", ident, {"code": exc.code})
            for ident in scope["groups"]:
                ensure_running(daemon)
                group = store.get(ident)
                if group["phase"] in ("complete", "abandoned", "superseded"):
                    continue
                try:
                    async with daemon.ledger.lifecycle.hold_many(participants.checkout_ids(store, group["unit"])):
                        async with daemon.unit_locks.hold(group["unit"]):
                            await GroupPublisher(daemon).reconcile(store.get(ident))
                except CoordinatorError as exc:
                    store.event("publication/recovery_refused", ident, exc.envelope())
        finally:
            done(("unit", unitid))

    tasks = [writer(runid, listeners) for runid, listeners in plan["runs"].items()]
    tasks += [snapshot(key, runs) for key, runs in plan["checkouts"].items()]
    tasks += [publications(key, scope) for key, scope in plan["units"].items()]
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        ensure_running(daemon)
        if stopped is not None:
            raise stopped
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            # A defect, never an expected refusal (each is recorded where it happens): seen, and the rest goes on.
            store.event("recovery/task_failed", "daemon", {"code": getattr(result, "code", type(result).__name__),
                                                           "message": str(result)[:300]})
    ensure_running(daemon)
    await daemon.messages.retry()
    ensure_running(daemon)
    store.event("recovery/complete", "daemon", {"generation": store.generation, "seconds": round(time.monotonic() - began, 3),
                                                "runs": len(plan["runs"])})


# Calls that act on a writer, change a unit's lifecycle, or resolve a checkout's UNKNOWN: each waits for the scope
# it names while startup recovery reconciles it (recovered). A run's cancel does not wait: it is recorded at once,
# and its settlement waits for the writer as it does for any reconcile.
RUN_CALLS = ("run/resume", "run/terminate-terminal")
UNIT_CALLS = ("unit/recover", "unit/reopen", "unit/seal", "unit/review-record", "unit/publish")
UNKNOWN_CALLS = ("unknown/restore", "unknown/narrow")


def recovering_counts(daemon):
    """What startup recovery is still reconciling, by scope (daemon/status, WRITER_RECOVERING)."""
    kinds = [kind for kind, _ in daemon.recovering]
    return {"writers": kinds.count("run"), "units": kinds.count("unit"), "checkouts": kinds.count("checkout")}


def retry_after(daemon):
    """The seconds after which a call refused WRITER_RECOVERING is worth making again, from the writer reconciles this
    recovery has measured (recovery/writer). Writers end one after another (calibrations of one binding are made
    once, each after the other), so the next end is due one mean interval between those ends after the last one,
    and at once after the first. Before any has ended, the last recovery that reconciled writers took
    recovery/complete's seconds (a forced switch re-admits the same writers); without one, none is due sooner than
    the time this one has run so far. At least 1 s."""
    ends, elapsed = daemon.recovery_ends, time.monotonic() - daemon.recovery_began
    if len(ends) >= 2:
        estimate = ends[-1] + (ends[-1] - ends[0]) / (len(ends) - 1) - elapsed
    elif ends:
        estimate = 0
    elif daemon.recovery_measured and daemon.recovery_measured > elapsed:
        estimate = daemon.recovery_measured - elapsed
    else:
        estimate = elapsed
    return max(1, math.ceil(estimate))


async def recovered(daemon, method, params):
    """Before a call is dispatched: when it acts on a run, unit or checkout that startup recovery is still
    reconciling, wait for that, at most RECOVERY_WAIT_S; then refuse it WRITER_RECOVERING (retryable), with when to
    call again (retry_after) and what recovery is still reconciling."""
    if not daemon.recovering:
        return
    scope = None
    if method in RUN_CALLS:
        scope = ("run", params.get("run"))
    elif method in UNIT_CALLS:
        scope = ("unit", params.get("unit"))
    elif method in UNKNOWN_CALLS and isinstance(params.get("unknown"), str):
        row = daemon.store.db.execute("SELECT checkout FROM unknowns WHERE id=?", (params["unknown"],)).fetchone()
        scope = ("checkout", row["checkout"]) if row else None
    if not scope or not isinstance(scope[1], str) or scope not in daemon.recovering:
        return
    began = time.monotonic()
    try:
        await asyncio.wait_for(daemon.recovering[scope].wait(), RECOVERY_WAIT_S)
    except TimeoutError:
        retry, counts = retry_after(daemon), recovering_counts(daemon)
        daemon.store.event("recovery/call_refused", scope[1], {"method": method, "scope": scope[0], "waited": RECOVERY_WAIT_S,
                                                               "retry_after": retry, "recovering": counts})
        raise CoordinatorError("WRITER_RECOVERING", "Startup recovery is still reconciling this " + scope[0] + " (" +
                               str(counts["writers"]) + " writers to go); call again in about " + str(retry) + " s",
                               {"scope": scope[0], "id": scope[1], "waited_seconds": RECOVERY_WAIT_S, "retry_after": retry,
                                "recovering": counts, "retryable": True}, True) from None
    daemon.store.event("recovery/call_waited", scope[1], {"method": method, "scope": scope[0],
                                                          "seconds": round(time.monotonic() - began, 3)})


async def writer_ready(daemon, session, cutoff):
    """The writer app-server that owns a hook's session, once it may admit a call: admitted, neither reconciling nor
    failed. One being reconciled (startup recovery, a reconnect, a re-admission) is waited for until `cutoff`
    (time.time()), when the hook client still listens; refused LISTENER_RECOVERING then. When the reconcile replaced
    it, the replacement is returned: the hook's process is checked against that one after, and a hook of the one it
    replaced is no descendant of it."""
    store = daemon.store
    waiting = ("connected", "disconnected")  # disconnected: a transport gap's reconcile reconnects it

    def owner():
        found = [l for l in store.all("listener") if l.get("root_thread") == session and l["state"] in waiting]
        if not found:
            raise CoordinatorError("HOOK_IDENTITY", "No live native listener owns this hook session")
        return next((l for l in found if l["state"] == "connected"), found[0])

    listener, began = owner(), time.monotonic()
    unit, counted = listener["unit"], False
    try:
        while (listener["state"] != "connected" or not listener.get("admission")
               or listener.get("recovery_state") in ("reconciling", "failed")):
            if not counted:
                # A call waiting here is its unit's call in flight: the unit is not quiet (unit_quiet), so the reconcile
                # it waits for does not replace its app-server for its hook program under it, nor end it.
                daemon.admitting_units[unit] = daemon.admitting_units.get(unit, 0) + 1
                counted = True
            if time.time() >= cutoff or listener.get("recovery_state") == "failed" and listener.get("retry_at", 0) > cutoff:
                raise CoordinatorError("LISTENER_RECOVERING", "Listener admission and reconciliation must finish before a new tool invocation",
                                       {"waited_seconds": round(time.monotonic() - began, 3)})
            await asyncio.sleep(0.05)
            current = store.get(listener["id"])
            listener = current if current["state"] in waiting else owner()
        return listener
    finally:
        if counted:
            daemon.admitting_units[unit] -= 1
            if not daemon.admitting_units[unit]:
                del daemon.admitting_units[unit]


def watch_answers(daemon):
    """From maintenance: probe each connected writer app-server (answer_watch), each at most once every
    ANSWER_EVERY_S, in the background. Not one startup recovery or another reconcile is reading, nor while the
    daemon drains."""
    now = time.monotonic()
    probes = daemon.answer_probes
    connected = set()
    for listener in daemon.store.selected("SELECT * FROM records WHERE kind='listener' AND json_extract(data,'$.state')='connected'"):
        connected.add(listener["id"])
        if (not listener.get("root_thread") or listener.get("recovery_state") == "reconciling" or not silent_failed(listener)
                and listener.get("recovery_state") == "failed" or ("run", listener["run"]) in daemon.recovering):
            continue
        last = probes.get(listener["id"])
        if last and (not last[1].done() or now - last[0] < ANSWER_EVERY_S):
            continue
        probes[listener["id"]] = (now, daemon.background(answer_watch(daemon, listener)))
    for ident in [key for key, (_, task) in probes.items() if key not in connected and task.done()]:
        del probes[ident]
    for ident in list(daemon.silent):
        found = daemon.store.get(ident, required=False)
        if not found or found["state"] in ("stopping", "stopped", "dead"):
            del daemon.silent[ident]  # replaced or stopped: no longer to report (a quarantined one still is)


def silent_failed(listener):
    """A connected writer whose reconcile was refused only because its app-server was silent (answers): probed on,
    so that it is reconciled as soon as it answers again."""
    return listener.get("recovery_state") == "failed" and listener.get("recovery_error") == "LISTENER_SILENT"


async def answer_watch(daemon, listener):
    """Probe a connected writer app-server with nothing that can wait behind its turn: its process, read without a
    request, then thread/loaded/list, which native answers on a task of its own (ANSWER_S). Stopped by a signal at two
    reads ANSWER_EVERY_S apart, it is ended under its run's reconcile lock, marked failed with its retry due, and
    re-admitted: its replacement resumes the thread, as one found so at recovery. Silent, it is reported (silence)
    and left running: never ended for that; one whose reconcile was refused for it is reconciled again once it answers.
    One that has exited is its transport gap's to reconcile."""
    store = daemon.store
    connection = daemon.connections.get(listener["id"])
    if not connection or connection.closed or daemon.draining:
        return
    current = await finish_thread(processes.identity, listener["pid"])
    if not current or current["birth"] != listener["birth"] or current["zombie"]:
        return
    why = await stopped(listener, current)
    if not why:
        try:
            took = await answer(daemon, listener, connection)
        except (OSError, CoordinatorError):
            return  # closed or refused: a transport gap or the next call decides
        if took is None:
            silence(daemon, listener, "thread/loaded/list unanswered for " + format(ANSWER_S, "g") + " s")
        else:
            spoke(daemon, listener, took)
            current = store.get(listener["id"])
            if silent_failed(current):
                # Its reconcile was refused while it was silent (LISTENER_SILENT): due now that it answers, not at its
                # next resume nor after its backoff.
                current.update(retry_at=0)
                store.save(current)
                daemon.readmit(listener["run"])
        return
    async with daemon.reconcile_locks.hold(listener["run"]):
        current = store.get(listener["id"])
        if (daemon.draining or current["state"] != "connected" or current.get("recovery_state") == "reconciling"
                or current.get("recovery_state") == "failed" and not silent_failed(current)
                or store.get(current["actor"]).get("listener") != current["id"]):
            return  # reconciled, replaced or stopped meanwhile: that decides
        identity = await finish_thread(processes.identity, current["pid"])
        if not identity or identity["birth"] != current["birth"] or identity["zombie"] or not identity.get("stopped"):
            return  # it exited (its transport gap reconciles it), or it was continued
        # Failed with its retry due first, so hook(), deliveries and submissions refuse it from here.
        current.update(recovery_state="failed", recovery_error="LISTENER_STOPPED", retry_at=0)
        store.save(current)
        store.event("listener/reconcile_failed", current["id"], {"code": "LISTENER_STOPPED", "retries": current.get("recovery_retries", 0), "retry_at": 0})
        await end_wedged(daemon, current, why, "stopped")
    daemon.readmit(listener["run"])


async def lead_command(daemon, params):
    actor = daemon.store.get(params["actor"])
    if actor["kind"] != "lead":
        raise CoordinatorError("LEAD_ONLY", "Lead command requires a lead actor")
    argv = params["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise CoordinatorError("COMMAND_ARGV", "Lead commands require an explicit argument array")
    import shlex
    operation = await daemon.ledger.prepare(actor, "lead-command", {"argv": argv}, [])
    try:
        reservation = await daemon.custody.reserve(actor, shlex.join(argv), operation["id"])
    except CoordinatorError as exc:
        operation.update(state="denied", refusal=exc.code)
        daemon.store.save(operation)
        raise
    daemon.lead_commands.add(operation["id"])
    try:
        cwd = params.get("cwd") or actor.get("cwd") or daemon.store.get(actor["checkout"])["root"]
        from .routing import route
        route(daemon.store, actor, ".", cwd, directory=True)
        process = await asyncio.create_subprocess_exec(*argv, cwd=cwd,
                                                       stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, start_new_session=True,
                                                       env=dict(os.environ, SOFTWARE_WORKFLOWS_OPERATION=operation["id"]), close_fds=True)
        birth = await finish_thread(processes.identity, process.pid)
        with daemon.store.transaction():
            for key in reservation.get("members", [reservation["id"]]):
                member = daemon.store.get(key)
                member["processes"] = [birth] if birth else []
                member["lead_root"] = birth or {"pid": process.pid, "birth": None, "exited_before_identity": True}
                daemon.store.save(member)
        reservation = daemon.store.get(reservation["id"])
        stdout, stderr = await process.communicate()
        # Record output digests, not arbitrary secrets printed by a command.
        import hashlib
        operation = daemon.store.get(operation["id"])
        operation.update(completed_item={"exitCode": process.returncode}, stdout_digest=hashlib.sha256(stdout).hexdigest(),
                         stderr_digest=hashlib.sha256(stderr).hexdigest())
        daemon.store.save(operation)
        await settle_lead_command(daemon, reservation)
        return {"operation": operation["id"], "exit_code": process.returncode, "custody": daemon.store.get(reservation["id"])["state"],
                "stdout_digest": operation["stdout_digest"], "stderr_digest": operation["stderr_digest"]}
    except BaseException as exc:
        if (isinstance(exc, asyncio.CancelledError) and (daemon.stop.is_set() or isinstance(exc, native.Retired))
                and (exc.__cause__ is None or isinstance(exc.__cause__, native.Retired))):
            # The prepared operation and custody precede process launch and
            # survive this waiter. Startup fences and reconciles them before
            # admitting work. Do not start a new full-checkout scan while
            # stopping, or mistake local retirement for contrary evidence.
            raise
        await finish(daemon.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        daemon.lead_commands.discard(operation["id"])


async def settle_lead_command(daemon, reservation, resolve_quarantine=False):
    store = daemon.store
    group = store.get(reservation["id"]).get("group", reservation["id"])
    async with daemon.custody_settlements.hold(group):
        while True:
            reservation = store.get(group)
            members = [store.get(key) for key in reservation.get("members", [group])]
            if all(member["state"] == "released" for member in members):
                return
            operation = store.get(reservation["operation"])
            # communicate() and process binding can publish while inventory runs.
            # An empty inventory predating either publication proves no absence.
            versions = {record["id"]: record["_revision"] for record in [operation, *members]}
            def current():
                return all(store.get(key)["_revision"] == revision for key, revision in versions.items())
            try:
                root = reservation.get("lead_root", {"pid": None, "birth": None})
                services = [daemon.leases.controller] if daemon.leases.controller else []
                survivors = await finish_thread(processes.inventory, root, operation["id"], reservation.get("processes", []),
                                               None, services, reservation.get("ambiguous_processes", []), reservation.get("process_baseline", []))
                if not current():
                    store.event("custody/inventory_discarded", group, {"reason": "ownership changed during inventory"})
                    continue
                with store.transaction():
                    for member in members:
                        member.update(processes=survivors, ambiguous_processes=[])
                        store.save(member)
                versions = {key: store.get(key)["_revision"] for key in versions}
                process_uncertainty = reservation.get("reason") in ("PROCESS_AMBIGUOUS", "PROCESS_UNREADABLE", "PROCESS_KERNEL_IDENTITY", "PROCESS_SCAN", "PROCESS_REUSED", "PROCESS_ENV")
                if process_uncertainty and not survivors:
                    resolve_quarantine = True
                if reservation["state"] == "quarantined" and not resolve_quarantine:
                    return
                # A live call owns its exit status. A restart has no such owner;
                # explicit crash-gap settlement still records the missing receipt.
                if not operation.get("completed_item"):
                    if operation["id"] in daemon.lead_commands or survivors or not resolve_quarantine:
                        return
                    with store.transaction():
                        operation["completed_item"] = {"exitCode": None, "observedGone": True, "missingExitStatus": True}
                        store.save(operation)
                        for member in members:
                            store.unknown(member["checkout"], "*", "lead reconciled command absence without exit status", {"operation": operation["id"]})
                    continue  # release requires an inventory begun after this evidence
                if survivors:
                    return
                await finish_all(*(daemon.ledger.reconcile(store.get(member["checkout"]), reason="lead command opaque effects")
                                   for member in members))
                async with daemon.custody.condition:
                    if not current():
                        store.event("custody/inventory_discarded", group, {"reason": "ownership changed before release"})
                        continue
                    # All members and the operation settle together, without an
                    # await between validation and the last release.
                    with store.transaction():
                        operation = store.get(operation["id"])
                        operation.update(state="reconciled", attribution="UNKNOWN-context-only")
                        store.save(operation)
                        for member in members:
                            daemon.custody.release_held(store.get(member["id"]))
                    daemon.custody.condition.notify_all()
                return
            except CoordinatorError as exc:
                if not current():
                    store.event("custody/inventory_discarded", group, {"reason": "ownership changed before failure", "code": exc.code})
                    continue
                previous = {(p["pid"], p["birth"]): p for p in reservation.get("ambiguous_processes", [])}
                previous.update({(p["pid"], p["birth"]): p for p in exc.details.get("processes", [])})
                with store.transaction():
                    for member in members:
                        member = store.get(member["id"])
                        member.update(state="quarantined", reason=exc.code, ambiguous_processes=list(previous.values()))
                        store.save(member)
                return


def resolve_restored_parents(store):
    for operation in list(store.all("operation")):
        if operation.get("parts") and operation["state"] == "quarantined" and all(
                store.get(key)["state"] == "resolved" for key in operation["parts"]):
            operation.update(state="resolved", restoration={key: store.get(key).get("restored_by") for key in operation["parts"]})
            store.save(operation)


def refuse_executing(store, checkout, paths=None):
    """A restore must not race a call whose host can still write."""
    from .lead_calls import refuse
    refuse(store, checkout, paths, code="RESTORE_EXECUTING")
    from .custody import normalize, overlaps
    root = store.get(checkout)
    executing = []
    for operation in store.live_operations(checkout):
        pending = (operation["kind"] in ("patch", "lead-edit", "restore")
                and operation["state"] in ("prepared", "allowed", "running"))
        if (operation.get("executing") or pending) and (paths is None or not operation["paths"] or any(
                overlaps(normalize(root, a)["key"], normalize(root, b)["key"])
                for a in paths for b in operation["paths"])):
            executing.append(operation["id"])
    if executing:
        raise CoordinatorError("RESTORE_EXECUTING", "An admitted or quarantined write may still be executing or settling",
                               {"operations": executing})


def baseline_of(head, index):
    """A path's baseline, from the branch commit's and the index's entries there (gitio.entries_at): the mode and
    object of the ordinary file both hold, absence ({"mode": None}) where both lack it, or None where they differ or
    hold something else (a folder, a symlink, a submodule, a conflict)."""
    if head != index:
        return None
    if not head:
        return {"mode": None, "oid": None}
    if len(head) != 1 or head[0]["mode"] not in ("100644", "100755"):
        return None
    return {"mode": head[0]["mode"], "oid": head[0]["oid"]}


def at_baseline(store, checkout, image, base):
    """Whether these bytes (a read_path image) are that baseline."""
    from . import gitio
    if base is None or image["mode"] != base["mode"]:
        return False
    return base["mode"] is None or gitio.blob_oid(store.read_blob(image["blob"]), checkout["object_format"]) == base["oid"]


def known_revision(store, checkout, path):
    row = store.db.execute("SELECT id FROM revisions WHERE checkout=? AND path=? AND attribution IN ('baseline','exact','restore') "
                           "ORDER BY created DESC LIMIT 1", (checkout["id"], path)).fetchone()
    return row and row[0]


def committed_blobs(root, oids):
    from . import gitio
    with gitio.blobs(root) as read:
        return {oid: read(oid) for oid in sorted(oids)}


async def release_taken(daemon, unitid, checkoutid, paths, mark):
    """A restore writes known bytes, no unit's authorship: the claims it took at its paths are let go once it has
    resolved what it found, so the new edit that follows can be any unit's. It lets go only of the claims its own edit
    created that still carry its `mark` (Custody.acquire). A claim another call acquired as well, or one taken anew
    after a handback, has a later owner and stays. (2.4.3 kept them: a writer's edit of a path its lead had restored
    from its own session's unit was refused CLAIM_CONFLICT until the lead handed it back.)"""
    released = []
    async with daemon.custody.condition:
        with daemon.store.transaction():
            for claim in daemon.custody.claims(checkoutid, unitid):
                if claim["path"] in paths and claim.get("restore") == mark:
                    claim["state"] = "released"
                    daemon.store.save(claim)
                    released.append(claim["id"])
            if released:
                daemon.store.event("claim/released", unitid, {"claims": released, "reason": "a restore keeps no claim"})
        daemon.custody.condition.notify_all()
    return released


async def restore_unknown(daemon, params):
    """Write the UNKNOWN's path again: a known revision of it, "baseline" (what the branch commit and the index both hold
    there: an ordinary file's bytes and mode, or absence where both lack it), or null in `revisions` (absence, where both
    lack it). Read again afterwards and found at its baseline, the path's UNKNOWNs are resolved by the restore (resolution
    "baseline"), which adds the path to no unit's scope (ledger.mutation_scope); any other known revision is still to be
    followed by a new exact edit. A path outside the ledger (ledger.unwatched) has nothing to restore: its UNKNOWNs are
    resolved as outside it, and nothing is written. (2.4.3 took only a stored known revision, so a path that never had one,
    such as test output read while it was UNKNOWN, could not be restored.)"""
    from . import gitio
    from .ledger import read_path
    store = daemon.store
    actor = store.get(params["actor"])
    if actor["kind"] != "lead":
        raise CoordinatorError("LEAD_ONLY", "Only a lead can restore a known revision")
    row = store.db.execute("SELECT * FROM unknowns WHERE id=?", (params["unknown"],)).fetchone()
    if not row or not participants.contains(store, actor["unit"], row["checkout"]):
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN must belong to a participant")
    if row and row["path"] == "*":
        return await restore_checkout(daemon, dict(actor, checkout=row["checkout"]), row, params.get("revisions", {}))
    path, revision, revisions = row["path"], params.get("revision") or None, params.get("revisions") or {}
    if revisions:
        if not isinstance(revisions, dict) or set(revisions) != {path} or revision and revisions[path] != revision:
            raise CoordinatorError("RESTORE_SCOPE", "revisions names only the UNKNOWN's path: a known revision of it, \"baseline\", "
                                   "or null to remove it", {"path": path})
        revision = revisions[path]
    elif not revision:  # an omitted revision never means a removal
        raise CoordinatorError("RESTORE_SCOPE", "Name the known revision to restore, \"baseline\" for what the branch commit and "
                               "the index hold, or pass revisions {path: null} to remove a path both lack", {"path": path})
    checkout = store.get(row["checkout"])
    refuse_executing(store, checkout["id"], [path])
    if await daemon.ledger.unwatched(checkout, [path]):
        async with daemon.ledger.lock(checkout["id"]):
            refuse_executing(store, checkout["id"], [path])
            with store.transaction():
                released = daemon.ledger.release(checkout, [path], "restore of a path outside the ledger")
        await daemon.ledger.notify_released()
        return {"state": "resolved", "resolution": "outside-ledger", "unknown": row["id"], "release": released}
    known = None
    if revision != "baseline" and revision is not None:
        known = store.db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
        if not known or path != known["path"] or known["checkout"] != row["checkout"]:
            raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN and known revision must name the same checkout path")
        if known["attribution"] not in ("baseline", "exact", "restore"):
            raise CoordinatorError("RESTORE_UNKNOWN", "An unexplained revision cannot be adopted")
    if any(a.get("active_turn") for a in store.all("actor") if participants.contains(store, a["unit"], row["checkout"])):
        raise CoordinatorError("RESTORE_ACTIVE", "Interrupt active writers before restoring UNKNOWN")
    if daemon.custody.active(row["checkout"]):
        raise CoordinatorError("RESTORE_CUSTODY", "Uncertain command survivors must be reconciled before restoration")
    refuse_executing(store, row["checkout"], [path])
    # The branch commit and the index, not the tree recorded at registration: a path a later publication added is
    # missing from that tree but committed on the branch.
    base = baseline_of(*await finish_thread(gitio.entries_at, checkout["root"], path))
    if revision == "baseline":
        if base is None:
            raise CoordinatorError("RESTORE_BASELINE", "The branch commit and the index differ at this path, or hold no ordinary "
                                   "file there, so it has no baseline; restore a known revision of it",
                                   {"path": path, "latest_known_revision": known_revision(store, checkout, path)})
        mode = base["mode"]
        data = (await finish_thread(committed_blobs, checkout["root"], [base["oid"]]))[base["oid"]] if mode else None
    elif revision is None:
        if base is None or base["mode"] is not None:
            raise CoordinatorError("RESTORE_BASELINE", "The branch commit or the index holds this path, so removing it is no "
                                   "baseline; restore \"baseline\" or a known revision of it",
                                   {"path": path, "latest_known_revision": known_revision(store, checkout, path)})
        mode, data = None, None
    else:
        data = store.read_blob(known["blob"]) if known["blob"] else None
        mode = known["mode"] if data is not None else None
    # The read records what is there now: a restore replaces bytes it has seen.
    await daemon.ledger.reconcile(checkout, [path], ignore_pending=False,
                                  owner={"kind": "restore", "unknown": row["id"], "phase": "before"},
                                  guard=lambda: refuse_executing(store, checkout["id"], [path]))
    change = {"mode": mode}
    if data is not None:
        change["bytes_base64"] = base64.b64encode(data).decode()
    # It resolves what it found before its edit, by id: an UNKNOWN or a quarantined operation recorded later is an
    # effect it never saw.
    found = [unknown["id"] for unknown in store.unresolved(row["checkout"], [path]) if unknown["path"] == path]
    replaced = [o["id"] for o in store.all("operation") if not o.get("parts") and o["checkout"] == row["checkout"]
                and o["state"] == "quarantined" and o["paths"] and set(o["paths"]) <= {path}]
    mark = uid("restore")  # carried by the claims its edit creates (release_taken)
    result = await daemon.lead_edit({"actor": actor["id"], "changes": {str(Path(checkout["root"]) / path): change}},
                                    restore=True, mark=mark)
    try:
        # Its claim is held until the resolution commits: another unit's claim of the path is refused meanwhile.
        # Resolved only as found now, against what the branch commit and the index hold now: the path read again,
        # holding the restore's own revision, which is still its head when the resolution commits.
        observed = store.head(checkout["id"], path)
        image = await finish_thread(read_path, store, checkout, path)
        now = baseline_of(*await finish_thread(gitio.entries_at, checkout["root"], path))
        resolved = (observed is not None and observed["operation"] == result["id"]
                    and image == {"blob": observed["blob"], "mode": observed["mode"]}
                    and await finish_thread(at_baseline, store, checkout, image, now))
        with store.transaction():
            resolved = resolved and (store.head(checkout["id"], path) or {}).get("id") == observed["id"]
            for unknown in found:
                store.db.execute("INSERT INTO resolutions(unknown_id,restore,edit) VALUES(?,?,?) ON CONFLICT(unknown_id) "
                                 "DO UPDATE SET restore=excluded.restore,edit=excluded.edit WHERE resolutions.edit IS NULL",
                                 (unknown, result["id"], result["id"] if resolved else None))
            operation = store.get(result["id"])
            # Whose bytes it wrote (publish.Publisher.provenance): the known revision, "baseline", or None for absence.
            operation["restored_from"] = {path: known["id"] if known else revision}
            if resolved:
                operation["resolution"] = "baseline"  # ledger.mutation_scope: the restore adds the path to no unit's scope
            store.save(operation)
            for key in replaced:
                operation = store.get(key)
                if operation["state"] == "quarantined":
                    operation["state"] = "resolved"
                    operation["restored_by"] = result["id"]
                    store.save(operation)
            resolve_restored_parents(store)
            store.event("unknown/restored", row["id"], {"restore": result["id"], "requested": revision,
                                                        "known_revision": known and known["id"], "resolution": "baseline" if resolved else None})
    finally:
        await finish(release_taken(daemon, actor["unit"], checkout["id"], {path}, mark))
    if resolved:
        return {"restore": result["id"], "state": "resolved", "resolution": "baseline", "unknown": row["id"]}
    return {"restore": result["id"], "state": "restored_requires_new_edit", "unknown": row["id"]}


async def restore_checkout(daemon, actor, unknown, revisions):
    """A notification gap has checkout-wide scope, never a narrow adoption path: every path the ledger holds (paths
    outside it, ledger.unwatched, are neither named nor written) is restored to a known revision, "baseline" or null
    (absence, where the branch commit and the index lack it). Found at its baseline throughout afterwards, every path
    read again by a full reconcile, the checkout is as the branch commit holds it, and each UNKNOWN there is resolved
    (resolution "baseline"); otherwise a new exact edit of the whole restored scope must follow."""
    from . import gitio
    store = daemon.store
    checkout = store.get(actor["checkout"])
    if unknown["checkout"] != checkout["id"]:
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN belongs to another checkout")
    if any(a.get("active_turn") for a in store.all("actor") if participants.contains(store, a["unit"], checkout["id"])):
        raise CoordinatorError("RESTORE_ACTIVE", "Interrupt every writer before restoring a notification gap")
    if daemon.custody.active(checkout["id"]):
        raise CoordinatorError("RESTORE_CUSTODY", "Reconcile all terminals and descendants first")
    refuse_executing(store, checkout["id"])
    await daemon.ledger.reconcile(checkout, ignore_pending=False,
                                  owner={"kind": "restore", "unknown": unknown["id"], "phase": "before"},
                                  guard=lambda: refuse_executing(store, checkout["id"]))
    held = {r[0] for r in store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
    paths = held - await daemon.ledger.unwatched(checkout, held)
    if not paths or not isinstance(revisions, dict) or set(revisions) != paths:
        raise CoordinatorError("RESTORE_FULL_SCOPE", "A checkout-wide UNKNOWN requires an explicit known revision, \"baseline\" or "
                               "null for every ledger path; to keep the checkout as it is, narrow it instead (coordinator_unit "
                               "action \"narrow\")", {"paths": sorted(paths), "baseline": "what the branch commit and the index both "
                               "hold", "null_revision": "allowed only for a path the branch commit and the index lack"})
    async def baselines():
        tree, index = await finish_all(finish_thread(gitio.entries, checkout["root"], "HEAD"),
                                           finish_thread(gitio.entries, checkout["root"]))
        return {path: baseline_of(tree.get(path, []), index.get(path, [])) for path in paths}
    base, changes = await baselines(), {}
    for path, revision in revisions.items():
        if revision == "baseline" and base[path] is None:
            raise CoordinatorError("RESTORE_BASELINE", "The branch commit and the index differ at this path, or hold no ordinary "
                                   "file there, so it has no baseline", {"path": path})
        if revision is None and (base[path] is None or base[path]["mode"] is not None):
            raise CoordinatorError("RESTORE_BASELINE", "Cannot claim baseline absence for a path the branch commit or the index holds",
                                   {"path": path})
        if revision is None or revision == "baseline":
            changes[path] = {"mode": base[path]["mode"]}
            continue
        known = store.db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
        if not known or known["checkout"] != checkout["id"] or known["path"] != path or known["attribution"] not in ("baseline", "exact", "restore"):
            raise CoordinatorError("RESTORE_KNOWN", "Every restored image must be a stored known revision")
        changes[path] = {"mode": known["mode"]}
        if known["blob"]:
            changes[path]["bytes_base64"] = base64.b64encode(store.read_blob(known["blob"])).decode()
    wanted = {base[path]["oid"] for path, revision in revisions.items() if revision == "baseline" and base[path]["mode"]}
    data = await finish_thread(committed_blobs, checkout["root"], wanted)
    for path, revision in revisions.items():
        if revision == "baseline" and base[path]["mode"]:
            changes[path]["bytes_base64"] = base64.b64encode(data[base[path]["oid"]]).decode()
    # As a path's restore: it resolves what it found before its edit, holds its claims until that commits, and is
    # resolved only while every head is still the one its read found.
    before = [item["id"] for item in store.unresolved(checkout["id"])]
    replaced = [o["id"] for o in store.all("operation") if not o.get("parts") and o["checkout"] == checkout["id"]
                and o["state"] == "quarantined" and set(o["paths"]) <= paths]
    mark = uid("restore")
    result = await daemon.lead_edit({"actor": actor["id"], "changes": changes, "cwd": checkout["root"]}, restore=True, mark=mark)
    try:
        # Resolution needs the whole checkout. Refuse a lead admitted during
        # the restore's awaits instead of silently resolving a partial scan.
        after = await daemon.ledger.reconcile(checkout, ignore_pending=False, reason="after a checkout-wide restore",
                                              owner={"kind": "restore", "operation": result["id"], "phase": "after"},
                                              guard=lambda: refuse_executing(store, checkout["id"]))
        now = await baselines()
        images = {path: store.head(checkout["id"], path) or {"id": None, "blob": None, "mode": None} for path in paths}
        resolved = not after["unknowns"] and await finish_thread(
            lambda: all(at_baseline(store, checkout, images[path], now[path]) for path in sorted(paths)))
        with store.transaction():
            resolved = resolved and all((store.head(checkout["id"], path) or {}).get("id") == images[path]["id"] for path in paths)
            for item in before:
                store.db.execute("INSERT INTO resolutions(unknown_id,restore,edit) VALUES(?,?,?) ON CONFLICT(unknown_id) "
                                 "DO UPDATE SET restore=excluded.restore,edit=excluded.edit WHERE resolutions.edit IS NULL",
                                 (item, result["id"], result["id"] if resolved else None))
            operation = store.get(result["id"])
            operation["restored_from"] = dict(revisions)  # whose bytes it wrote (publish.Publisher.provenance)
            if resolved:
                operation["resolution"] = "baseline"
            store.save(operation)
            for key in replaced:
                operation = store.get(key)
                if operation["state"] == "quarantined":
                    operation.update(state="resolved", restored_by=result["id"])
                    store.save(operation)
            resolve_restored_parents(store)
            store.event("unknown/checkout_restored", unknown["id"], {"restore": result["id"], "known_revisions": revisions,
                                                                     "resolution": "baseline" if resolved else None})
    finally:
        await finish(release_taken(daemon, actor["unit"], checkout["id"], paths, mark))
    if resolved:
        return {"restore": result["id"], "state": "resolved", "resolution": "baseline", "unknown": unknown["id"]}
    return {"restore": result["id"], "state": "restored_requires_new_edit", "unknown": unknown["id"],
            "required_new_edit_scope": sorted(paths)}


async def narrow_unknown(daemon, params):
    """A checkout-wide UNKNOWN says the coordinator lost track of the checkout for a while, so an effect it
    could not attribute may be anywhere in it. Reading every path again shows what that left: each path whose
    bytes differ from the revision the ledger knows becomes its own UNKNOWN, as in any reconcile, and every
    checkout-wide UNKNOWN recorded before the read is resolved by it. Nothing is written and no bytes are
    adopted, so no writer needs to be idle: a patch or edit still in flight keeps its own lifecycle, and seal
    and publish read their paths again before they freeze them. Not seen: a change the gap made and undid
    before the read."""
    from .ledger import full_snapshot
    store = daemon.store
    actor = store.get(params["actor"])
    if actor["kind"] != "lead":
        raise CoordinatorError("LEAD_ONLY", "Only a lead can narrow a checkout-wide UNKNOWN")
    row = store.db.execute("SELECT * FROM unknowns WHERE id=?", (params.get("unknown"),)).fetchone()
    if not row or not participants.contains(store, actor["unit"], row["checkout"]):
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN must belong to a participant")
    if row["path"] != "*":
        raise CoordinatorError("NARROW_SCOPE", "Only a checkout-wide UNKNOWN is narrowed; restore a path's UNKNOWN to a known revision")
    checkout = store.get(row["checkout"])

    def quiet():
        # A command's effects are opaque until it is reconciled: read while one runs, they would be UNKNOWN.
        if daemon.custody.active(checkout["id"]):
            raise CoordinatorError("RESTORE_CUSTODY", "A command holds custody in this checkout; narrow once it is reconciled", {}, True)
        refuse_executing(store, checkout["id"])

    async with daemon.ledger.lock(checkout["id"]):  # no operation is admitted here while the checkout is read
        quiet()
        began = time.time()
        known = {r[0] for r in store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
        # Read tolerantly: a path in git's view that is no ordinary file (a symlink) is its own UNKNOWN, not a refusal
        # of the narrowing; and a path outside the ledger (ledger.outside) is left out, its old head let go.
        unreadable, listed = {}, set()
        actual = await finish_thread(full_snapshot, store, checkout, known, unreadable, listed)
        quiet()  # a command whose custody was granted before the lock may have started meanwhile
        outside = await daemon.ledger.outside(checkout, actual, unreadable, listed)
        ident = uid("narrowing")
        with store.transaction():
            result = daemon.ledger.apply_observation(checkout, set(actual), actual,
                                                     "unexplained bytes, found narrowing a checkout-wide UNKNOWN", transaction=False,
                                                     outside=outside, unreadable=unreadable)
            resolved = [u["id"] for u in store.unresolved(checkout["id"]) if u["path"] == "*" and u["created"] < began]
            found = [{"id": r["id"], "path": r["path"]} for unknown in result["unknowns"]
                     for r in store.db.execute("SELECT id, path FROM unknowns WHERE id=?", (unknown,))]
            store.put("narrowing", ident, {"checkout": checkout["id"], "actor": actor["id"], "unknowns": resolved,
                                           "paths_scanned": result["paths_scanned"], "found": found, "created": time.time()}, checkout["id"])
            for unknown in resolved:
                store.db.execute("INSERT INTO resolutions(unknown_id,restore,edit) VALUES(?,?,?) ON CONFLICT(unknown_id) "
                                 "DO UPDATE SET restore=excluded.restore,edit=excluded.edit WHERE resolutions.edit IS NULL",
                                 (unknown, ident, ident))
            store.event("unknown/narrowed", ident, {"checkout": checkout["id"], "unknowns": resolved,
                                                    "paths_scanned": result["paths_scanned"], "found": found})
    await daemon.ledger.notify_released()  # the leads of the units whose UNKNOWNs outside the ledger were resolved
    return {"narrowing": ident, "state": "narrowed", "resolved": resolved, "paths_scanned": result["paths_scanned"], "unknown": found}
