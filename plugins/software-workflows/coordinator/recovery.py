"""Generation-fenced recovery. Never replay an uncertain mutation or continue a turn."""
import asyncio
import base64
import json
import os
import signal
import time

from .client import CoordinatorError
from . import native, processes
from .store import uid


async def reconcile_listener(daemon, listener, advance=True):
    store = daemon.store
    daemon.ledger.require_owned(store.get(store.get(listener["actor"])["checkout"]))
    listener["recovery_state"] = "reconciling"
    store.save(listener)
    current = await asyncio.to_thread(processes.identity, listener["pid"])
    alive = current and current["birth"] == listener["birth"] and not current["zombie"]
    actor = store.get(listener["actor"])
    run = store.get(actor["run"])
    unsubmitted = not run.get("workflow") and not run.get("task") and not run.get("production_started") and not run.get("phases")
    await daemon.ensure_lease(actor["checkout"])
    if not listener.get("root_thread"):
        # A threadless bootstrap can be stopped without inventing a writer identity.
        if alive:
            await daemon.stop_listener(listener)
        return
    if not alive:
        await daemon.detach_listener(listener["id"])
        for reservation in daemon.custody.active(actor["checkout"]):
            if reservation["unit"] != actor["unit"]:
                continue
            for process in reservation.get("processes", []):
                try:
                    await asyncio.to_thread(processes.terminate, process, actor["session"])
                except CoordinatorError as exc:
                    reservation.update(state="quarantined", reason=exc.code)
                    store.save(reservation)
            store.unknown(actor["checkout"], "*", "listener died with potentially escaped effects", {"listener": listener["id"]})
        listener["state"] = "dead"
        store.save(listener)
        replacement = await daemon.spawn_listener(actor, listener["config"], listener.get("home"))
        replacement["root_thread"] = listener["root_thread"]
        replacement["replaces"] = listener["id"]
        replacement["hooks"] = listener.get("hooks", [])
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
        connection = await daemon.attach(listener)
    readmit = not alive or listener.get("admission_fingerprint", {}).get("adapter_digest") != native.adapter_digest()
    if not attached or readmit:
        response = await connection.call("thread/resume", {"threadId": listener["root_thread"],
                                          "approvalPolicy": "never", "sandbox": "danger-full-access"})
        native.policy_echo(response)
    if not alive:
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
    listed = await connection.call("hooks/list", {"cwds": [store.get(actor["checkout"])["root"]]})
    observed = {hook["key"]: hook for entry in listed["data"] for hook in entry["hooks"]}
    if len(listener.get("hooks", [])) != 2 or any(
        h["key"] not in observed or observed[h["key"]].get("currentHash") != h["currentHash"] or
        observed[h["key"]].get("trustStatus") != "trusted" or not observed[h["key"]].get("enabled")
        for h in listener.get("hooks", [])):
        raise CoordinatorError("RECOVERY_HOOK_TRUST", "Recovered listener no longer has its admitted hook definitions and precise trust")
    # The receive loop fsynced these notifications before scheduling processing.
    # Recover evidence that was received durably but not yet applied to an intent.
    incarnation = listener
    while True:
        for row in store.db.execute("SELECT data FROM events WHERE kind='native' AND subject=? ORDER BY seq", (incarnation["id"],)):
            notification = json.loads(row[0])
            params = notification.get("params", {})
            if notification.get("method") == "hook/completed":
                daemon.record_hook_evidence(listener, params, history=True)
            elif notification.get("method") == "item/completed" and params.get("item", {}).get("type") in ("fileChange", "commandExecution", "mcpToolCall"):
                operation = daemon.find_operation(listener, params["threadId"], params["turnId"], params["item"]["id"], history=True)
                if operation:
                    operation["completed_item"] = params["item"]
                    store.save(operation)
        if not incarnation.get("replaces"):
            break
        incarnation = store.get(incarnation["replaces"])
    # Discover every loaded descendant before reconciling its items. Parent events
    # may have been missed; thread/read supplies the native ancestry.
    await daemon.discover_descendants(listener)
    for child in list(store.all("actor", actor["unit"])):
        if child["kind"] == "lead":
            continue
        if unsubmitted and child["thread"] == listener["root_thread"]:
            # Native does not materialize history until the first user input.
            # This admitted, still-attached thread has never received one.
            if not attached:
                raise CoordinatorError("THREAD_UNMATERIALIZED", "The unused native thread was disconnected; create a new unit")
            continue
        if child["thread"] != listener["root_thread"] and not attached:
            # A new app-server connection must subscribe to each live child too.
            # Resuming history does not start a turn or repeat its input.
            child_response = await connection.call("thread/resume", {"threadId": child["thread"],
                "approvalPolicy": "never", "sandbox": "danger-full-access"})
            native.policy_echo(child_response)
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
                    elif not operation:
                        store.unknown(child["checkout"], "*", "history contains an unaccounted effect-capable item", {"item": item["id"], "listener": listener["id"]})
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
                await daemon.ledger.quarantine(operation, "crash gap: intent is not proof of execution or no effect")
    await daemon.ledger.reconcile(store.get(actor["checkout"]), ignore_pending=False, reason="recovery snapshot")
    listener = store.get(listener["id"])
    listener.update(recovery_state="reconciled", state="connected")
    store.save(listener)
    # Progression belongs to the daemon. Persisted prepared intents can submit;
    # uncertain inputs without a history match stay uncertain, never replayed.
    run = store.get(actor["run"])
    if advance and run.get("workflow"):
        await daemon.writer.advance(run["id"])
    if not alive:
        await daemon.messages.send("lead", actor["id"], "The listener died. A replacement cold-resumed this thread without continuing it. "
                                   "Effects were reconciled; inspect UNKNOWN and retained custody before further mutations.", "none")


async def recover(daemon):
    store = daemon.store
    store.event("recovery/start", "daemon", {"generation": store.generation})
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
    for listener in list(store.all("listener")):
        if listener["state"] == "dead" or listener["state"] == "stopped" and not listener.get("root_thread"):
            continue
        try:
            await reconcile_listener(daemon, listener)
        except Exception as exc:
            actor = store.get(listener["actor"])
            current_listener = store.get(store.get(actor["id"]).get("listener", listener["id"]))
            current_listener.update(state="quarantined", recovery_state="failed", recovery_error=getattr(exc, "code", type(exc).__name__))
            store.save(current_listener)
            store.unknown(actor["checkout"], "*", "listener recovery incomplete", {"listener": listener["id"], "code": getattr(exc, "code", type(exc).__name__)})
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
        if publication["phase"] not in ("complete", "abandoned"):
            try:
                unit = store.get(publication["unit"])
                async with daemon.ledger.lifecycle.hold(unit["checkout"]):
                    async with daemon.unit_locks.hold(unit["id"]):
                        await publisher.reconcile(publication)
            except CoordinatorError as exc:
                store.event("publication/recovery_refused", publication["id"], {"code": exc.code})
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
        process = await asyncio.create_subprocess_exec(*argv, cwd=daemon.store.get(actor["checkout"])["root"],
                                                       stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, start_new_session=True,
                                                       env=dict(os.environ, SOFTWARE_WORKFLOWS_OPERATION=operation["id"]), close_fds=True)
        birth = await asyncio.to_thread(processes.identity, process.pid)
        reservation["processes"] = [birth] if birth else []
        reservation["lead_root"] = birth or {"pid": process.pid, "birth": None, "exited_before_identity": True}
        daemon.store.save(reservation)
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
    operation = daemon.store.get(reservation["operation"])
    try:
        root = reservation.get("lead_root", {"pid": None, "birth": None})
        services = [daemon.leases.controller] if daemon.leases.controller else []
        survivors = await asyncio.to_thread(processes.inventory, root, operation["id"], reservation.get("processes", []),
                                           None, services, reservation.get("ambiguous_processes", []), reservation.get("process_baseline", []))
        reservation = daemon.store.get(reservation["id"])
        reservation["processes"] = survivors
        reservation["ambiguous_processes"] = []
        process_uncertainty = reservation.get("reason") in ("PROCESS_AMBIGUOUS", "PROCESS_UNREADABLE", "PROCESS_KERNEL_IDENTITY", "PROCESS_SCAN", "PROCESS_REUSED", "PROCESS_ENV")
        if process_uncertainty and not survivors:
            resolve_quarantine = True
        daemon.store.save(reservation)
        if reservation["state"] == "quarantined" and not resolve_quarantine:
            return
        if not survivors and resolve_quarantine and not operation.get("completed_item"):
            operation["completed_item"] = {"exitCode": None, "observedGone": True, "missingExitStatus": True}
            daemon.store.save(operation)
            daemon.store.unknown(operation["checkout"], "*", "lead reconciled command absence without exit status", {"operation": operation["id"]})
        if survivors or not operation.get("completed_item"):
            return
        await daemon.ledger.reconcile(daemon.store.get(operation["checkout"]), reason="lead command opaque effects")
        operation.update(state="reconciled", attribution="UNKNOWN-context-only")
        daemon.store.save(operation)
        await daemon.custody.relinquish(reservation)
    except CoordinatorError as exc:
        if exc.details.get("processes"):
            previous = {(p["pid"], p["birth"]): p for p in reservation.get("ambiguous_processes", [])}
            previous.update({(p["pid"], p["birth"]): p for p in exc.details["processes"]})
            reservation["ambiguous_processes"] = list(previous.values())
        reservation.update(state="quarantined", reason=exc.code)
        daemon.store.save(reservation)


async def restore_unknown(daemon, params):
    store = daemon.store
    actor = store.get(params["actor"])
    if actor["kind"] != "lead":
        raise CoordinatorError("LEAD_ONLY", "Only a lead can restore a known revision")
    row = store.db.execute("SELECT * FROM unknowns WHERE id=?", (params["unknown"],)).fetchone()
    if row and row["path"] == "*":
        return await restore_checkout(daemon, actor, row, params.get("revisions", {}))
    known = store.db.execute("SELECT * FROM revisions WHERE id=?", (params["revision"],)).fetchone()
    if not row or not known or row["checkout"] != actor["checkout"] or row["path"] != known["path"] or known["checkout"] != row["checkout"]:
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN and known revision must name the same checkout path")
    if known["attribution"] not in ("baseline", "exact", "restore"):
        raise CoordinatorError("RESTORE_UNKNOWN", "An unexplained revision cannot be adopted")
    if any(a.get("active_turn") for a in store.all("actor") if a["checkout"] == actor["checkout"]):
        raise CoordinatorError("RESTORE_ACTIVE", "Interrupt active writers before restoring UNKNOWN")
    if daemon.custody.active(actor["checkout"]):
        raise CoordinatorError("RESTORE_CUSTODY", "Uncertain command survivors must be reconciled before restoration")
    await daemon.ledger.reconcile(store.get(actor["checkout"]), [row["path"]], ignore_pending=False)
    change = {"mode": known["mode"]}
    if known["blob"]:
        change["bytes_base64"] = base64.b64encode(store.read_blob(known["blob"])).decode()
    result = await daemon.lead_edit({"actor": actor["id"], "changes": {row["path"]: change}}, restore=True)
    with store.transaction():
        for unknown in store.unresolved(actor["checkout"], [row["path"]]):
            if unknown["path"] != row["path"]:
                continue
            store.db.execute("INSERT INTO resolutions(unknown_id,restore) VALUES(?,?) ON CONFLICT(unknown_id) "
                             "DO UPDATE SET restore=excluded.restore WHERE resolutions.edit IS NULL", (unknown["id"], result["id"]))
        for operation in list(store.all("operation")):
            if operation["checkout"] == actor["checkout"] and operation["state"] == "quarantined" and set(operation["paths"]) <= {row["path"]}:
                operation["state"] = "resolved"
                operation["restored_by"] = result["id"]
                store.save(operation)
        store.event("unknown/restored", row["id"], {"restore": result["id"], "known_revision": known["id"]})
    return {"restore": result["id"], "state": "restored_requires_new_edit", "unknown": row["id"]}


async def restore_checkout(daemon, actor, unknown, revisions):
    """A notification gap has checkout-wide scope, never a narrow adoption path."""
    from . import gitio
    store = daemon.store
    checkout = store.get(actor["checkout"])
    if unknown["checkout"] != checkout["id"]:
        raise CoordinatorError("RESTORE_SCOPE", "UNKNOWN belongs to another checkout")
    if any(a.get("active_turn") for a in store.all("actor") if a["checkout"] == checkout["id"]):
        raise CoordinatorError("RESTORE_ACTIVE", "Interrupt every writer before restoring a notification gap")
    if daemon.custody.active(checkout["id"]):
        raise CoordinatorError("RESTORE_CUSTODY", "Reconcile all terminals and descendants first")
    await daemon.ledger.reconcile(checkout, ignore_pending=False)
    paths = {r[0] for r in store.db.execute("SELECT path FROM heads WHERE checkout=?", (checkout["id"],))}
    if not paths or not isinstance(revisions, dict) or set(revisions) != paths:
        raise CoordinatorError("RESTORE_FULL_SCOPE", "A checkout-wide UNKNOWN requires an explicit known revision for every ledger path",
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
    result = await daemon.lead_edit({"actor": actor["id"], "changes": changes}, restore=True)
    with store.transaction():
        for item in store.unresolved(checkout["id"]):
            store.db.execute("INSERT INTO resolutions(unknown_id,restore) VALUES(?,?) ON CONFLICT(unknown_id) "
                             "DO UPDATE SET restore=excluded.restore WHERE resolutions.edit IS NULL", (item["id"], result["id"]))
        for operation in list(store.all("operation")):
            if operation["checkout"] == checkout["id"] and operation["state"] == "quarantined" and set(operation["paths"]) <= paths:
                operation.update(state="resolved", restored_by=result["id"])
                store.save(operation)
        store.event("unknown/checkout_restored", unknown["id"], {"restore": result["id"], "known_revisions": revisions})
    return {"restore": result["id"], "state": "restored_requires_new_edit", "unknown": unknown["id"],
            "required_new_edit_scope": sorted(paths)}
