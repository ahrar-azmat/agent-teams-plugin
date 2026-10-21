"""Generation-fenced recovery. Never replay an uncertain mutation or continue a turn."""
import asyncio
import base64
import json
import os
from pathlib import Path
import signal
import time

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
RUNTIME_NOTE = ("Values the old app-server held in its tool runtime (code-mode variables and functions) are gone: "
                "only the thread's history carries over.")


async def may_execute(daemon, operation):
    """Whether native may still write for this admitted patch: allowed, with no sign its handler ended, and its
    app-server still running. A quarantine then keeps its paths busy (`executing`)."""
    from .daemon import handler_ended
    if operation["kind"] != "patch" or operation["state"] not in ("allowed", "running") or handler_ended(operation):
        return False
    listener = daemon.store.get(operation["native"].get("listener") or "", required=False)
    if not listener:
        return False
    current = await asyncio.to_thread(processes.identity, listener["pid"])
    return bool(current and current["birth"] == listener["birth"] and not current["zombie"])


async def reconcile_listener(daemon, listener, advance=True):
    """Reconcile a run's writer app-server: reconnect, replace or re-admit it, and read back what native did
    meanwhile. One caller at a time for each run (a recovery, a resume, a message's or a workflow's re-admission, a
    cancel, a transport gap): each takes the run's lock, then reconciles the incarnation current by then, so two
    callers never replace the same app-server. A failure marks that incarnation failed, so its unit's work waits,
    and maintenance reconciles it again."""
    store = daemon.store
    async with daemon.reconcile_locks.hold(listener["run"]):
        listener = store.get(listener["id"])
        bound = store.get(listener["actor"]).get("listener")
        if listener.get("root_thread") and listener["state"] in ("dead", "stopped") and bound and bound != listener["id"]:
            listener = store.get(bound)  # replaced meanwhile, by the caller this one waited for
        try:
            notice = await _reconcile_listener(daemon, listener)
        except BaseException as exc:
            reconcile_failed(daemon, listener, exc)
            raise
    # Progression belongs to the daemon. Persisted prepared intents can submit; uncertain inputs without a history
    # match stay uncertain, never replayed. Outside the run's lock: a submission can reconcile its writer again.
    run = store.get(listener["run"])
    if advance and run.get("workflow"):
        await daemon.writer.advance(run["id"])
    if notice:
        await daemon.messages.send("lead", listener["actor"], notice, "none")


def reconcile_failed(daemon, listener, exc):
    """Mark the run's current incarnation failed: hook(), deliveries and submissions refuse it, and maintenance
    reconciles it again once its retry is due. Startup recovery quarantines it instead."""
    store = daemon.store
    if not listener.get("root_thread"):
        return  # a threadless bootstrap is stopped again by the next recovery
    current = store.get(store.get(listener["actor"]).get("listener") or listener["id"])
    retries = current.get("recovery_retries", 0) + 1
    code = getattr(exc, "code", type(exc).__name__)
    current.update(recovery_state="failed", recovery_error=code, recovery_retries=retries,
                   retry_at=time.time() + min(RECONCILE_RETRY_MAX_S, RECONCILE_RETRY_FIRST_S * 2 ** (retries - 1)))
    store.save(current)
    store.event("listener/reconcile_failed", current["id"], {"code": code, "retries": retries, "retry_at": current["retry_at"]})


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


async def _reconcile_listener(daemon, listener):
    """reconcile_listener's work, under the run's lock. Returns the notice for the lead, if any."""
    store = daemon.store
    daemon.ledger.require_owned(store.get(store.get(listener["actor"])["checkout"]))
    listener["recovery_state"] = "reconciling"
    store.save(listener)
    current = await asyncio.to_thread(processes.identity, listener["pid"])
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
    for operation in list(store.all("operation", actor["unit"])):
        if operation.get("parts") and operation["state"] in ("prepared", "allowed", "running"):
            await daemon.ledger.quarantine(operation, "cross-checkout operation interrupted by native reconnect",
                                           executing=await may_execute(daemon, operation))
    unsubmitted = not run.get("workflow") and not run.get("task") and not run.get("production_started") and not run.get("phases")
    for checkoutid in participants.checkout_ids(store, actor["unit"]):
        await daemon.ensure_lease(checkoutid)
    unit = store.get(actor["unit"])
    roots = participants.roots(store, unit)
    cwd = run.get("thread_cwd", store.get(actor["checkout"])["root"])
    from .projects import verify_thread
    stale = daemon.hook_stale(listener)
    readmitted = stale and listener["state"] == "stopped"  # stopped below, by an earlier pass, for its hook program
    if not alive:
        await daemon.detach_listener(listener["id"])
        for reservation in daemon.custody.unit_active(actor["unit"]):
            if reservation["unit"] != actor["unit"]:
                continue
            for process in reservation.get("processes", []):
                try:
                    await asyncio.to_thread(processes.terminate, process, actor["session"])
                except CoordinatorError as exc:
                    reservation.update(state="quarantined", reason=exc.code)
                    store.save(reservation)
            store.unknown(reservation["checkout"], "*", "listener died with potentially escaped effects", {"listener": listener["id"]})
        # The receipts it wrote before it died apply to the calls it admitted; never its replacement's.
        await daemon.settle_native_log(listener["id"])
        await daemon.executor_exited(listener)
        listener = store.get(listener["id"])
        listener["state"] = "dead"
        store.save(listener)
        if listener.get("log"):
            Path(listener["log"]).unlink(missing_ok=True)
            daemon.log_offsets.pop(listener["id"], None)
        if stale:
            # Its stored config names the old release's hook program: admit the replacement with this one's.
            replacement = await daemon.admit_listener(actor, daemon.readmission_config(listener["config"]),
                                                      listener.get("home"), roots, cwd)
        else:
            replacement = await daemon.spawn_listener(actor, listener["config"], listener.get("home"))
            replacement["hooks"] = listener.get("hooks", [])
        replacement["root_thread"] = listener["root_thread"]
        replacement["replaces"] = listener["id"]
        # Reconciling until this pass ends, in the same save that makes it findable: hook(), deliveries and
        # submissions refuse it until its native history and its unit's operations are read back.
        replacement["recovery_state"] = "reconciling"
        replacement["initializing"] = "readmitted" if readmitted else "died"  # the notice its first full pass sends
        store.save(replacement)
        for child in list(store.all("actor", actor["unit"])):
            if child["kind"] == "lead":
                continue
            child["listener"] = replacement["id"]
            child["active_turn"] = None
            child["state"] = "recovering"
            store.save(child)
        listener = replacement
    connection = daemon.connections.get(listener["id"])
    attached = bool(connection and not connection.closed)
    if not connection or connection.closed:
        try:
            connection = await daemon.attach(listener)
        except (OSError, CoordinatorError):
            # A dying app-server can remove its socket after the initial birth
            # check but before attach. Recheck that exact process before choosing
            # live reconnect versus replacement; never advance on a failed bind.
            for _ in range(20):
                current = await asyncio.to_thread(processes.identity, listener["pid"])
                if not current or current["birth"] != listener["birth"] or current["zombie"]:
                    return await _reconcile_listener(daemon, store.get(listener["id"]))
                await asyncio.sleep(0.05)
            raise
    readmit = fresh or listener.get("admission_fingerprint", {}).get("adapter_digest") != native.adapter_digest()
    if not attached or readmit:
        response = await connection.call("thread/resume", {"threadId": listener["root_thread"],
                                          "approvalPolicy": "never", "sandbox": "danger-full-access",
                                          **({"cwd": cwd, "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {})})
        native.policy_echo(response)
        verify_thread(response, unit, cwd, roots)
    if fresh:
        # A replacement has not executed a turn. Its own infrastructure is a
        # fresh service baseline; prior command survivors retain their separate
        # birth-bound records, including those outside this listener's tree.
        listener = store.get(listener["id"])
        listener["services"] = await asyncio.to_thread(processes.inventory, listener, None)
        store.save(listener)
    if readmit:
        from .admission import Admission
        await Admission(store).writer_gate(daemon, listener["id"], response, fixture=bool(listener.get("home")))
        listener = store.get(listener["id"])
    listed = await connection.call("hooks/list", {"cwds": roots})
    native.coordinator_hooks(listed, roots, expected=listener.get("hooks", []))
    # The receive loop fsynced these notifications before scheduling processing.
    # Recover evidence that was received durably but not yet applied to an intent.
    incarnation = listener
    while True:
        for row in store.db.execute("SELECT data FROM events WHERE kind='native' AND subject=? ORDER BY seq", (incarnation["id"],)):
            notification = json.loads(row[0])
            params, method = notification.get("params", {}), notification.get("method")
            if method == "hook/completed":
                daemon.record_hook_evidence(listener, params, history=True)
            elif method in ("item/started", "item/completed") and params.get("item", {}).get("type") in ("fileChange", "commandExecution", "mcpToolCall"):
                operation = daemon.find_operation(listener, params["threadId"], params["turnId"], params["item"]["id"], history=True)
                key = "completed_item" if method == "item/completed" else "started_item"
                if operation and (key == "completed_item" or not operation.get(key)):
                    operation[key] = params["item"]
                    store.save(operation)
                    if key == "completed_item":
                        daemon.ledger.end_execution(operation)
        if not incarnation.get("replaces"):
            break
        incarnation = store.get(incarnation["replaces"])
    # Failed results it wrote while no daemon read its log, judged with every item replayed above
    # (its log is not followed while it reconciles).
    await daemon.settle_native_log(listener["id"])
    # Discover every loaded descendant before reconciling its items. Parent events
    # may have been missed; thread/read supplies the native ancestry.
    await daemon.discover_descendants(listener)
    for child in ancestry_order(list(store.all("actor", actor["unit"]))):
        if child["kind"] == "lead":
            continue
        if unsubmitted and child["thread"] == listener["root_thread"]:
            # Native does not materialize history until the first user input.
            # This admitted, still-attached thread has never received one.
            if not attached:
                raise CoordinatorError("THREAD_UNMATERIALIZED", "The unused native thread was disconnected; create a new unit")
            continue
        if child["thread"] != listener["root_thread"] and (not attached or fresh):
            # A new app-server connection must subscribe to each live child too, and a replacement has loaded only
            # the root. Resuming history does not start a turn or repeat its input.
            child_response = await connection.call("thread/resume", {"threadId": child["thread"],
                "approvalPolicy": "never", "sandbox": "danger-full-access",
                **({"cwd": child.get("cwd", cwd), "runtimeWorkspaceRoots": roots} if unit.get("project_revision") else {})})
            native.policy_echo(child_response)
            # Native 0.163.0 children inherit roots and ancestry, but their own
            # project_id is null. Preserve that observed association on resume.
            verify_thread(child_response, dict(unit, project=child.get("native_project")), child.get("cwd", cwd), roots)
        turns = []
        async for result in native.page(connection, "thread/turns/list", {"threadId": child["thread"], "itemsView": "full", "sortDirection": "asc"}):
            for turn in result["data"]:
                turns.append(turn)
                store.event("recovery/turn", child["id"], {"id": turn["id"], "status": turn["status"], "itemsView": turn.get("itemsView")})
                if turn["status"] == "inProgress":
                    child["active_turn"] = turn["id"]
                elif child.get("active_turn") == turn["id"]:
                    child["active_turn"] = None
                    child["state"] = turn["status"]
        async for result in native.page(connection, "thread/items/list", {"threadId": child["thread"], "sortDirection": "asc"}):
            for entry in result["data"]:
                item, turn = entry["item"], entry["turnId"]
                if entry.get("completedAtMs"):
                    daemon.writer.observe(child, "item/completed", {"threadId": child["thread"], "turnId": turn, "item": item})
                if item["type"] == "userMessage" and item.get("clientId"):
                    daemon.messages.entered(child["id"], item["clientId"], {"history": True, "turn": turn, "item": item["id"]})
                if item["type"] == "subAgentActivity":
                    await daemon.resolve_actor(listener, item["agentThreadId"])
                if item["type"] in ("fileChange", "commandExecution", "mcpToolCall"):
                    operation = daemon.find_operation(listener, child["thread"], turn, item["id"], history=True)
                    if operation and entry.get("completedAtMs"):
                        operation["completed_item"] = item
                        store.save(operation)
                        daemon.ledger.end_execution(operation)
                    elif not operation:
                        for checkoutid in participants.checkout_ids(store, child["unit"]):
                            store.unknown(checkoutid, "*", "history contains an unaccounted effect-capable item", {"item": item["id"], "listener": listener["id"]})
        metadata = await connection.call("thread/read", {"threadId": child["thread"], "includeTurns": False})
        if metadata["thread"].get("status", {}).get("type") == "idle":
            child["active_turn"] = None
            child["state"] = "idle"
        store.save(child)
        for turn in turns:
            if turn["status"] != "inProgress":
                daemon.writer.observe(child, "turn/completed", {"threadId": child["thread"], "turn": turn})
    for operation in list(store.all("operation", actor["unit"])):
        if operation["state"] in ("prepared", "allowed", "running"):
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
    await asyncio.gather(*(daemon.ledger.reconcile(store.get(key), ignore_pending=False, reason="recovery snapshot")
                           for key in participants.checkout_ids(store, actor["unit"])))
    listener = store.get(listener["id"])
    if alive and listener["state"] == "connected" and daemon.hook_stale(listener) and daemon.unit_quiet(actor["unit"]):
        # Admitted by another release, its hooks run that release's program, and hook() refuses every call it
        # makes (HOOK_PROGRAM_CHANGED). Its native turns are read now, and while it reconciles no hook, delivery or
        # submission starts work: with nothing of its unit in flight, it is stopped (no notification gap) and
        # replaced by an app-server admitted with this release's hooks, which resumes the same thread.
        await daemon.stop_listener(listener, drain_s=READMIT_DRAIN_S, kill=True)
        return await _reconcile_listener(daemon, store.get(listener["id"]))
    listener.update(recovery_state="reconciled", state="connected")
    for key in ("recovery_error", "recovery_retries", "retry_at"):
        listener.pop(key, None)
    initialized = listener.pop("initializing", None)
    store.save(listener)
    if initialized == "readmitted":
        return ("Re-admitted with this release's hooks: a replacement app-server resumed this thread without continuing "
                "it. Nothing of the unit was in flight. " + RUNTIME_NOTE)
    if initialized == "died":
        return ("The listener died. A replacement cold-resumed this thread without continuing it. Effects were "
                "reconciled; inspect UNKNOWN and retained custody before further mutations. " + RUNTIME_NOTE)
    return None


def recoverable_listener(listener):
    """Whether startup recovery reconciles it: not a threadless bootstrap that was stopped, and not dead."""
    return listener["state"] != "dead" and not (listener["state"] == "stopped" and not listener.get("root_thread"))


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
    store = daemon.store
    store.event("recovery/start", "daemon", {"generation": store.generation})
    from .intents import invalidate_windows
    invalidate_windows(daemon)
    await daemon.ledger.recover_ownership()
    for request in list(store.all("custody_request")):
        if request["state"] == "waiting":
            request.update(state="interrupted", recovered_generation=store.generation)
            store.save(request)
            store.event("custody/wait_interrupted", request["id"], {})
    for attempt in list(store.all("attempt")):
        attempt["generation"] += 1
        attempt["daemon_generation"] = store.generation
        store.save(attempt)
    defer_unbound(store)
    # All units regain coverage before any listener reconnects or input advances. A threadless bootstrap needs none: it
    # is stopped, never given work. A checkout whose lease is refused (a writer outside the coordinator took it while no
    # daemon ran) fails only its own writers' recovery: each reconcile takes its leases again before it reconnects, and
    # is quarantined with an UNKNOWN when refused. Every other checkout recovers.
    recoverable = [l for l in store.all("listener") if recoverable_listener(l)]
    for checkoutid in sorted({key for l in recoverable if l.get("root_thread") for key in participants.checkout_ids(store, l["unit"])}):
        try:
            await daemon.ensure_lease(checkoutid)
        except CoordinatorError as exc:
            store.event("recovery/lease_refused", checkoutid, {"code": exc.code, "message": str(exc)[:300]})
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
            current = await asyncio.to_thread(processes.identity, discovery["pid"])
            if current and current["birth"] == discovery["birth"]:
                os.kill(current["pid"], signal.SIGTERM)
            discovery["state"] = "interrupted"
            store.save(discovery)
    for listener in list(store.all("listener")):
        if not recoverable_listener(listener):
            continue
        try:
            await reconcile_listener(daemon, listener, advance=False)
        except Exception as exc:
            actor = store.get(listener["actor"])
            current_listener = store.get(store.get(actor["id"]).get("listener", listener["id"]))
            current_listener.update(state="quarantined", recovery_state="failed", recovery_error=getattr(exc, "code", type(exc).__name__))
            store.save(current_listener)
            for checkoutid in participants.checkout_ids(store, actor["unit"]):
                store.unknown(checkoutid, "*", "listener recovery incomplete", {"listener": listener["id"], "code": getattr(exc, "code", type(exc).__name__)})
    # Lead operations have no native history to fill the crash gap.
    for operation in list(store.all("operation")):
        if operation["kind"] in ("lead-edit", "restore", "lead-command") and operation["state"] in ("prepared", "allowed", "running"):
            await daemon.ledger.quarantine(operation, "lead operation interrupted before verified completion")
    for checkout in list(store.all("checkout")):
        if not checkout.get("ready") or checkout.get("quarantine") or checkout.get("identity") not in daemon.ledger.ownership:
            continue
        try:
            await daemon.ledger.reconcile(checkout, reason="daemon recovery filesystem snapshot")
        except Exception as exc:
            checkout["quarantine"] = getattr(exc, "code", type(exc).__name__)
            store.save(checkout)
            store.unknown(checkout["id"], "*", "recovery filesystem snapshot incomplete", {})
    from .publish import Publisher
    publisher = Publisher(daemon)
    # Histories/filesystem have been reconciled. An interrupted candidate is
    # never treated as reviewed, including legacy sealing units without attempts.
    for unit in list(store.all("unit")):
        attempt = store.get(unit.get("seal_attempt"), required=False)
        if unit["state"] == "sealing" or attempt and attempt["state"] == "started":
            publisher.interrupt_seal(unit["id"], "worker interrupted sealing")
    for publication in list(store.all("publication")):
        if not publication.get("group") and publication["phase"] not in ("complete", "abandoned"):
            try:
                unit = store.get(publication["unit"])
                async with daemon.ledger.lifecycle.hold(unit["checkout"]):
                    async with daemon.unit_locks.hold(unit["id"]):
                        await publisher.reconcile(publication)
            except CoordinatorError as exc:
                store.event("publication/recovery_refused", publication["id"], {"code": exc.code})
    from .group_publish import GroupPublisher
    for group in list(store.all("publication_group")):
        if group["phase"] not in ("complete", "abandoned", "superseded"):
            try:
                async with daemon.ledger.lifecycle.hold_many(participants.checkout_ids(store, group["unit"])):
                    async with daemon.unit_locks.hold(group["unit"]):
                        await GroupPublisher(daemon).reconcile(group)
            except CoordinatorError as exc:
                store.event("publication/recovery_refused", group["id"], exc.envelope())
    daemon.recovery_barrier = False
    for listener in store.all("listener"):
        if listener.get("recovery_state") == "reconciled" and store.get(listener["run"]).get("workflow"):
            await daemon.writer.advance(listener["run"])
    await daemon.messages.retry()
    store.event("recovery/complete", "daemon", {"generation": store.generation})


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
    try:
        cwd = params.get("cwd") or actor.get("cwd") or daemon.store.get(actor["checkout"])["root"]
        from .routing import route
        route(daemon.store, actor, ".", cwd, directory=True)
        process = await asyncio.create_subprocess_exec(*argv, cwd=cwd,
                                                       stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, start_new_session=True,
                                                       env=dict(os.environ, SOFTWARE_WORKFLOWS_OPERATION=operation["id"]), close_fds=True)
        birth = await asyncio.to_thread(processes.identity, process.pid)
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
        operation.update(completed_item={"exitCode": process.returncode}, stdout_digest=hashlib.sha256(stdout).hexdigest(),
                         stderr_digest=hashlib.sha256(stderr).hexdigest())
        daemon.store.save(operation)
        await settle_lead_command(daemon, reservation)
        return {"operation": operation["id"], "exit_code": process.returncode, "custody": daemon.store.get(reservation["id"])["state"],
                "stdout_digest": operation["stdout_digest"], "stderr_digest": operation["stderr_digest"]}
    except BaseException as exc:
        await daemon.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
        raise


async def settle_lead_command(daemon, reservation, resolve_quarantine=False):
    reservation = daemon.store.get(reservation.get("group", reservation["id"]))
    members = [daemon.store.get(key) for key in reservation.get("members", [reservation["id"]])]
    if all(r["state"] == "released" for r in members):
        return
    operation = daemon.store.get(reservation["operation"])
    try:
        root = reservation.get("lead_root", {"pid": None, "birth": None})
        services = [daemon.leases.controller] if daemon.leases.controller else []
        survivors = await asyncio.to_thread(processes.inventory, root, operation["id"], reservation.get("processes", []),
                                           None, services, reservation.get("ambiguous_processes", []), reservation.get("process_baseline", []))
        reservation = daemon.store.get(reservation["id"])
        for member in members:
            member["processes"] = survivors
            member["ambiguous_processes"] = []
            daemon.store.save(member)
        process_uncertainty = reservation.get("reason") in ("PROCESS_AMBIGUOUS", "PROCESS_UNREADABLE", "PROCESS_KERNEL_IDENTITY", "PROCESS_SCAN", "PROCESS_REUSED", "PROCESS_ENV")
        if process_uncertainty and not survivors:
            resolve_quarantine = True
        if reservation["state"] == "quarantined" and not resolve_quarantine:
            return
        if not survivors and resolve_quarantine and not operation.get("completed_item"):
            operation["completed_item"] = {"exitCode": None, "observedGone": True, "missingExitStatus": True}
            daemon.store.save(operation)
            for member in members:
                daemon.store.unknown(member["checkout"], "*", "lead reconciled command absence without exit status", {"operation": operation["id"]})
        if survivors or not operation.get("completed_item"):
            return
        await asyncio.gather(*(daemon.ledger.reconcile(daemon.store.get(member["checkout"]), reason="lead command opaque effects")
                               for member in members))
        operation.update(state="reconciled", attribution="UNKNOWN-context-only")
        daemon.store.save(operation)
        for member in members:
            await daemon.custody.relinquish(daemon.store.get(member["id"]))
    except CoordinatorError as exc:
        if exc.details.get("processes"):
            previous = {(p["pid"], p["birth"]): p for p in reservation.get("ambiguous_processes", [])}
            previous.update({(p["pid"], p["birth"]): p for p in exc.details["processes"]})
            reservation["ambiguous_processes"] = list(previous.values())
        for member in members:
            member.update(state="quarantined", reason=exc.code, ambiguous_processes=reservation.get("ambiguous_processes", []))
            daemon.store.save(member)


def resolve_restored_parents(store):
    for operation in list(store.all("operation")):
        if operation.get("parts") and operation["state"] == "quarantined" and all(
                store.get(key)["state"] == "resolved" for key in operation["parts"]):
            operation.update(state="resolved", restoration={key: store.get(key).get("restored_by") for key in operation["parts"]})
            store.save(operation)


def refuse_executing(store, checkout):
    """A restore would race a write native may still finish for a quarantined patch."""
    executing = [o["id"] for o in store.live_operations(checkout) if o.get("executing")]
    if executing:
        raise CoordinatorError("RESTORE_EXECUTING", "A quarantined patch may still be writing; its paths stay held "
                               "until its handler's end is reported or its app-server stops", {"operations": executing})


async def restore_unknown(daemon, params):
    store = daemon.store
    actor = store.get(params["actor"])
    if actor["kind"] != "lead":
        raise CoordinatorError("LEAD_ONLY", "Only a lead can restore a known revision")
    row = store.db.execute("SELECT * FROM unknowns WHERE id=?", (params["unknown"],)).fetchone()
    if not row or not participants.contains(store, actor["unit"], row["checkout"]):
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN must belong to a participant")
    if row and row["path"] == "*":
        return await restore_checkout(daemon, dict(actor, checkout=row["checkout"]), row, params.get("revisions", {}))
    known = store.db.execute("SELECT * FROM revisions WHERE id=?", (params["revision"],)).fetchone()
    if not row or not known or not participants.contains(store, actor["unit"], row["checkout"]) or row["path"] != known["path"] or known["checkout"] != row["checkout"]:
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN and known revision must name the same checkout path")
    if known["attribution"] not in ("baseline", "exact", "restore"):
        raise CoordinatorError("RESTORE_UNKNOWN", "An unexplained revision cannot be adopted")
    if any(a.get("active_turn") for a in store.all("actor") if participants.contains(store, a["unit"], row["checkout"])):
        raise CoordinatorError("RESTORE_ACTIVE", "Interrupt active writers before restoring UNKNOWN")
    if daemon.custody.active(row["checkout"]):
        raise CoordinatorError("RESTORE_CUSTODY", "Uncertain command survivors must be reconciled before restoration")
    refuse_executing(store, row["checkout"])
    await daemon.ledger.reconcile(store.get(row["checkout"]), [row["path"]], ignore_pending=False)
    change = {"mode": known["mode"]}
    if known["blob"]:
        change["bytes_base64"] = base64.b64encode(store.read_blob(known["blob"])).decode()
    result = await daemon.lead_edit({"actor": actor["id"], "changes": {str(__import__("pathlib").Path(store.get(row["checkout"])["root"]) / row["path"]): change}}, restore=True)
    with store.transaction():
        for unknown in store.unresolved(row["checkout"], [row["path"]]):
            if unknown["path"] != row["path"]:
                continue
            store.db.execute("INSERT INTO resolutions(unknown_id,restore) VALUES(?,?) ON CONFLICT(unknown_id) "
                             "DO UPDATE SET restore=excluded.restore WHERE resolutions.edit IS NULL", (unknown["id"], result["id"]))
        for operation in list(store.all("operation")):
            if not operation.get("parts") and operation["checkout"] == row["checkout"] and operation["state"] == "quarantined" and operation["paths"] and set(operation["paths"]) <= {row["path"]}:
                operation["state"] = "resolved"
                operation["restored_by"] = result["id"]
                store.save(operation)
        resolve_restored_parents(store)
        store.event("unknown/restored", row["id"], {"restore": result["id"], "known_revision": known["id"]})
    return {"restore": result["id"], "state": "restored_requires_new_edit", "unknown": row["id"]}


async def restore_checkout(daemon, actor, unknown, revisions):
    """A notification gap has checkout-wide scope, never a narrow adoption path."""
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
    await daemon.ledger.reconcile(checkout, ignore_pending=False)
    paths = {r[0] for r in store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
    if not paths or not isinstance(revisions, dict) or set(revisions) != paths:
        raise CoordinatorError("RESTORE_FULL_SCOPE", "A checkout-wide UNKNOWN requires an explicit known revision for every ledger path; "
                               "to keep the checkout as it is, narrow it instead (coordinator_unit action \"narrow\")",
                               {"paths": sorted(paths), "null_revision": "allowed only for a path absent from the recorded baseline tree"})
    baseline = await asyncio.to_thread(gitio.entries, checkout["root"], checkout["base"])
    changes = {}
    for path, revision in revisions.items():
        if revision is None:
            if path in baseline:
                raise CoordinatorError("RESTORE_BASELINE", "Cannot claim baseline absence for a tracked baseline path")
            changes[path] = {"mode": None}
            continue
        known = store.db.execute("SELECT * FROM revisions WHERE id=?", (revision,)).fetchone()
        if not known or known["checkout"] != checkout["id"] or known["path"] != path or known["attribution"] not in ("baseline", "exact", "restore"):
            raise CoordinatorError("RESTORE_KNOWN", "Every restored image must be a stored known revision")
        changes[path] = {"mode": known["mode"]}
        if known["blob"]:
            changes[path]["bytes_base64"] = base64.b64encode(store.read_blob(known["blob"])).decode()
    result = await daemon.lead_edit({"actor": actor["id"], "changes": changes, "cwd": checkout["root"]}, restore=True)
    with store.transaction():
        for item in store.unresolved(checkout["id"]):
            store.db.execute("INSERT INTO resolutions(unknown_id,restore) VALUES(?,?) ON CONFLICT(unknown_id) "
                             "DO UPDATE SET restore=excluded.restore WHERE resolutions.edit IS NULL", (item["id"], result["id"]))
        for operation in list(store.all("operation")):
            if not operation.get("parts") and operation["checkout"] == checkout["id"] and operation["state"] == "quarantined" and set(operation["paths"]) <= paths:
                operation.update(state="resolved", restored_by=result["id"])
                store.save(operation)
        resolve_restored_parents(store)
        store.event("unknown/checkout_restored", unknown["id"], {"restore": result["id"], "known_revisions": revisions})
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
        actual = await asyncio.to_thread(full_snapshot, store, checkout, known)
        quiet()  # a command whose custody was granted before the lock may have started meanwhile
        ident = uid("narrowing")
        with store.transaction():
            result = daemon.ledger.apply_observation(checkout, set(actual), actual,
                                                     "unexplained bytes, found narrowing a checkout-wide UNKNOWN", transaction=False)
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
    return {"narrowing": ident, "state": "narrowed", "resolved": resolved, "paths_scanned": result["paths_scanned"], "unknown": found}
