"""Birth-bound lifetime for direct external lead editors.

A missing or unreadable owner is never replaced by a heartbeat timeout. Kernel
lineage retains descendants after reparenting; ambiguous unseen orphans retain
the reservation. Explicit completion remains terminal caller evidence.
"""
import asyncio
import time

from . import processes, telemetry
from .client import CoordinatorError
from .store import uid


def capture(named):
    if not (isinstance(named, dict) and type(named.get("pid")) is int and named["pid"] > 1
            and isinstance(named.get("birth"), str) and named["birth"]):
        raise CoordinatorError("EXECUTOR_REQUIRED", "Direct operation/prepare requires executor {pid,birth}. "
                               "Prefer lead-edit when the daemon can install and verify the images itself.")
    owner = processes.identity(named["pid"])
    if not owner or owner["zombie"] or owner["birth"] != named["birth"]:
        raise CoordinatorError("EXECUTOR_GONE", "The named editor birth is not running")
    if not owner.get("kernel_id"):
        raise CoordinatorError("EXECUTOR_LINEAGE", "Direct preparation requires kernel lineage that survives reparenting. Use daemon-owned lead-edit on this host.")
    baseline = processes.baseline()
    return owner, [{"pid": p["pid"], "birth": p["birth"], "kernel_id": p.get("kernel_id"),
                    "parent_kernel_id": p.get("parent_kernel_id")} for p in baseline]


def inventory(record):
    retained = record.get("lineage", [record["owner"]])
    def ended():
        for known in retained:
            current = processes.identity(known["pid"])
            if current and not current["zombie"] and current["birth"] == known["birth"]:
                return False
        return True
    # Enumerate after verifying the last known ancestor's exit. A PID list
    # made before that exit can miss the child it forks immediately afterwards.
    before = ended()
    vanished = []
    snapshot = processes.baseline(vanished)
    if not before and ended():
        vanished = []
        snapshot = processes.baseline(vanished)
    records = retained + record["baseline"] + snapshot
    owner_id = record["owner"]["kernel_id"]
    domains = {p["kernel_id"] for p in retained if p.get("kernel_id")}
    # XNU kern_fork.c assigns ++nextuniqueid under proc_list_lock and preserves
    # it across exec (apple-oss-distributions/xnu blob 85b9ea2152a64846f0ebfebb1c423258871b7833).
    # An older original parent cannot descend from this editor. Unlike wall
    # time or PID order, this also proves new launchd children are outside.
    outside = {p["kernel_id"] for p in records if p.get("kernel_id") and p["kernel_id"] < owner_id}
    while True:
        inside = domains | {p["kernel_id"] for p in records if p.get("kernel_id") and p.get("parent_kernel_id") in domains}
        excluded = outside | {p["kernel_id"] for p in records if p.get("kernel_id") and p["kernel_id"] not in inside and
                             (p.get("parent_kernel_id") in outside or
                              0 < (p.get("parent_kernel_id") or 0) < owner_id)}
        if inside == domains and excluded == outside:
            break
        domains, outside = inside, excluded
    alive = [p for p in snapshot if p.get("kernel_id") in domains]
    # Baseline membership is not exclusion evidence: an editor may have spawned
    # an orphan through an intermediate that exited before prepare. Unknown
    # ancestry holds even when that orphan was already in the first snapshot.
    ambiguous = [p for p in snapshot if p.get("kernel_id") not in domains | outside]
    if not alive:
        ambiguous += vanished
    lineage = {(p["pid"], p["birth"]): p for p in retained}
    lineage.update({(p["pid"], p["birth"]): p for p in records if p.get("kernel_id") in domains})
    return alive, ambiguous, list(lineage.values())


def pending(store):
    return list(store.selected("SELECT * FROM records WHERE kind='executor' AND json_extract(data,'$.state') IN ('preparing','active')"))


def describe(store):
    return [{"id": r["id"], "operation": r.get("operation"), "phase": r["state"], "executor": r["owner"],
             "scope": r["scope"], "survivors": r.get("survivors", []), "ambiguous": r.get("ambiguous", []), "problem": r.get("problem")}
            for r in pending(store)]


async def begin(daemon, named, actor, scope):
    owner, baseline = await telemetry.to_thread(capture, named)
    ident = uid("executor")
    daemon.store.put("executor", ident, {"owner": owner, "baseline": baseline, "lineage": [owner],
        "generation": daemon.store.generation, "scope": scope, "actor": actor["id"], "unit": actor["unit"],
        "created": time.time(), "state": "preparing"}, actor["unit"])
    return daemon.store.get(ident)


def bind(store, owner, operation):
    with store.transaction():
        for part in [operation] + [store.get(k) for k in operation.get("parts", [])]:
            part.update(executor=owner["id"], executing=True)
            store.save(part)
        owner.update(state="active", operation=operation["id"])
        store.save(owner)


def finish(daemon, owner, reason):
    store = daemon.store
    owner = store.get(owner["id"])
    with store.transaction():
        for claim in store.selected("SELECT * FROM records WHERE kind='claim' AND json_extract(data,'$.executor')=?", (owner["id"],)):
            if claim["state"] == "held":
                claim.update(state="released", released=time.time())
                store.save(claim)
        operation = store.get(owner.get("operation"), required=False)
        if operation:
            daemon.ledger.end_execution(operation, transaction=False)
        owner.update(state="settled", completed=time.time(), reason=reason)
        for key in ("baseline", "lineage", "survivors", "ambiguous"):
            owner.pop(key, None)
        store.save(owner)
        store.event("executor/settled", owner["id"], {"operation": owner.get("operation"), "reason": reason})


async def settle(daemon, ident):
    async with daemon.actor_locks.hold("executor:" + ident):
        owner = daemon.store.get(ident)
        if owner["state"] == "preparing" and owner["generation"] == daemon.store.generation:
            return  # A current RPC still owns preparation; never race its bind.
        if owner["state"] not in ("preparing", "active"):
            return
        # Explicit completion is durable end evidence, including a failed
        # verification. It must retire ownership even after a crash in cleanup.
        if owner.get("end"):
            await complete_locked(daemon, owner)
            return
        alive, ambiguous, lineage = await telemetry.to_thread(inventory, owner)
        owner = daemon.store.get(ident)
        if owner["state"] not in ("preparing", "active"):
            return
        owner.update(survivors=alive, ambiguous=ambiguous, lineage=lineage)
        daemon.store.save(owner)
        if alive or ambiguous:
            return
        if owner["state"] == "preparing":
            # Receipt may have committed before the crashed generation bound its
            # parent. Do not infer exact authorship from those intended bytes.
            for key, paths in owner["scope"].items():
                await daemon.ledger.reconcile(daemon.store.get(key), paths, ignore_pending=False,
                    preserve_leads=True, reason="external preparation interrupted before owner binding")
            finish(daemon, owner, "interrupted preparation; executor and descendants exited")
            return
        operation = daemon.store.get(owner["operation"])
        if operation["state"] == "prepared" and operation["generation"] == daemon.store.generation:
            try:
                if operation.get("parts"):
                    from .operations import verify
                    await verify(daemon.ledger, operation, operation["intended"]["expected"], "exact",
                                 {"executor": owner["owner"], "source": "birth-verified executor exit"})
                else:
                    await daemon.ledger.verify(operation, operation["intended"]["expected"], "exact",
                                               {"executor": owner["owner"], "source": "birth-verified executor exit"})
            except CoordinatorError as exc:
                if exc.code not in ("POSTIMAGE_MISMATCH", "REVISION_CHANGED", "OPAQUE_OVERLAP", "OPERATION_STATE"):
                    raise
                await daemon.ledger.settle_unapplied(operation, "external editor exited without a matching image", lambda current: True)
        elif operation["state"] not in ("verified", "denied", "resolved", "reconciled"):
            # A previous generation's intent cannot repair its crash-gap quarantine.
            await observe_ended(daemon, owner, operation)
        finish(daemon, owner, "executor and descendants exited")


async def observe_ended(daemon, owner, operation):
    """Observe the final scope of a quarantined, now-ended external execution.

    Quarantine is not a receipt. Commit this acknowledgement together with the
    final revisions, including all parts, before retiring the owner.
    """
    if owner.get("end_observed"):
        return
    def acknowledge():
        current = daemon.store.get(owner["id"])
        current["end_observed"] = {"generation": daemon.store.generation, "observed": time.time()}
        daemon.store.save(current)
    applications = [{"checkout": daemon.store.get(key), "paths": paths, "ignore_pending": False,
        "preserve_leads": True, "settling_operation": operation["id"]} for key, paths in owner["scope"].items()]
    await daemon.ledger.scans.observe(applications, "external editor ended after interrupted admission",
        action={"operation": operation["id"], "executor": owner["id"]}, on_observed=acknowledge)


async def complete_locked(daemon, owner):
    store = daemon.store
    operation = store.get(owner["operation"])
    if operation["state"] in ("verified", "denied", "resolved", "reconciled", "quarantined"):
        if owner["state"] != "settled":
            if operation["state"] == "quarantined":
                await observe_ended(daemon, owner, operation)
            finish(daemon, owner, "terminal operation acknowledged")
        return operation
    if operation["kind"] != "lead-edit" or operation["state"] != "prepared":
        raise CoordinatorError("OPERATION_STATE", "Only a prepared lead edit can complete through RPC")
    try:
        if operation.get("parts"):
            from .operations import verify
            result = await verify(daemon.ledger, operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
        else:
            result = await daemon.ledger.verify(operation, operation["intended"]["expected"], "exact", {"executor": "lead-editor"})
    except Exception as exc:
        await daemon.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
        finish(daemon, owner, "explicit completion failed verification")
        raise
    finish(daemon, owner, "explicit operation/complete")
    return result


async def complete(daemon, operation):
    ident = operation.get("executor")
    if not ident:
        return None  # Earlier records use the existing completion path.
    # Persist terminal evidence before waiting for either ownership or IO.
    owner = daemon.store.get(ident)
    if not owner.get("end"):
        owner["end"] = {"source": "operation/complete", "observed": time.time()}
        daemon.store.save(owner)
    async with daemon.actor_locks.hold("executor:" + ident):
        return await complete_locked(daemon, daemon.store.get(ident))


async def observe(daemon):
    jobs = {}
    while not daemon.stop.is_set():
        await asyncio.sleep(1)
        if not daemon.ready or daemon.draining:
            continue
        for owner in pending(daemon.store):
            ident = owner["id"]
            if (owner["state"] == "preparing" and owner["generation"] == daemon.store.generation) or ident in jobs:
                continue
            async def attempt(key=ident):
                task = asyncio.current_task()
                daemon.inflight[task] = "operation/complete"
                try:
                    await settle(daemon, key)
                except Exception as exc:
                    record = daemon.store.get(key)
                    code = getattr(exc, "code", type(exc).__name__)
                    if record.get("problem") != code:
                        record["problem"] = code
                        daemon.store.save(record)
                        daemon.store.event("executor/retry", key, {"code": code})
                finally:
                    daemon.inflight.pop(task, None)
                    jobs.pop(key, None)
            jobs[ident] = daemon.background(attempt())
