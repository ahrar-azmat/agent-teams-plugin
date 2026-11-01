"""Permanent failures are retried for declared repairs, never for version changes."""
from contextvars import ContextVar
import asyncio
import os
import time

from .client import CoordinatorError
from .store import digest, uid

# Stable ids and predicates over persisted, sanitized failed-attempt evidence.
# 2.4.30 carries no repair for the existing native thread/resume failures.
REPAIRS = ()
CURRENT = ContextVar("writer_repair_attempt", default=None)


def failure(store, run):
    record = store.get("writer_failure:" + run, required=False)
    return record if record and record["state"] == "pending" else None


def decision(store, listener):
    run = listener["run"]
    record = failure(store, run)
    demand = store.get("writer_start:" + run, required=False)
    startup = store.get(store.get(run).get("startup_recovery"), required=False)
    permanent = bool(record or listener.get("recovery_state") == "failed" and not listener.get("recovery_retryable", True)
                     or demand and demand.get("state") in ("failed", "pending", "starting") and not demand.get("retryable", True)
                     or startup and startup.get("state") == "pending" and not startup.get("retryable", True))
    result = {"permanent": permanent, "eligible": not permanent, "reason": None,
              "incident": record and record["incident"], "repair": None, "attempt": None}
    if not permanent:
        return result
    result["reason"] = "no_declared_repair" if record else "legacy_failure_without_matching_evidence"
    if record:
        for repair in REPAIRS:
            if not repair["applies"](record):
                continue
            ident = "writer_retry:" + digest([record["incident"], repair["id"]])
            claim = store.get(ident, required=False)
            result.update(repair=repair["id"], attempt=claim and claim["id"])
            if not claim or claim["state"] == "deferred":
                result.update(eligible=True, reason="declared_repair", key=ident)
                return result
            result["reason"] = "repair_attempt_consumed"
    return result


def record_failure(store, listener, info, retryable):
    prior = failure(store, listener["run"])
    if retryable and prior is None:
        return
    attempt = store.get("writer_attempt:" + listener["run"], required=False)
    evidence = (attempt or {}).get("evidence")
    record = prior or {"incident": uid("writer_incident"), "state": "pending", "created": time.time(),
                       "cause": info, "evidence": evidence, "run": listener["run"]}
    record.update(last_cause=info, last_evidence=evidence, last_failed=time.time())
    # Older recovery code assumes release-keyed retries. Commit its refusal
    # barrier before publishing an incident or repair-keyed claim.
    store.storage5()
    with store.transaction():
        store.durable()
        if prior is None:
            store.put("writer_failure", "writer_failure:" + listener["run"], record, listener["run"])
        else:
            store.save(record)


async def capture(daemon, actor, config, binary):
    from . import native, owned, children
    from .locks import finish_thread
    if not hasattr(daemon, "writer_evidence_cache"):
        daemon.writer_evidence_cache, daemon.writer_evidence_lock = {}, asyncio.Lock()
    def identity():
        try:
            info = os.stat(binary)
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        except OSError:
            return None
    async with daemon.writer_evidence_lock:
        physical = identity() if binary else None
        cached = daemon.writer_evidence_cache.get(physical)
        if physical is None:
            cached = {"binary_digest": None, "native_version": None}
        if cached is None:
            version, process = None, None
            try:
                process = await owned.spawn(binary, "--version", stdout=-1)
                # Optional evidence must not hang demand admission if an executable
                # stops answering --version. Timeout means unknown, not a repair.
                output, _ = await asyncio.wait_for(process.communicate(), 1.0)
                if process.returncode == 0:
                    version = output.decode().strip()
            except (asyncio.TimeoutError, OSError, CoordinatorError, UnicodeError):
                pass
            finally:
                if process is not None:
                    await children.terminate(process)
            try:
                binary_digest = await finish_thread(native.file_digest, binary)
            except OSError:
                binary_digest = None
            cached = {"binary_digest": binary_digest, "native_version": version}
            if identity() == physical:
                daemon.writer_evidence_cache[physical] = cached
            else:
                cached = {"binary_digest": None, "native_version": None}
    evidence = dict(cached,
                config_digest=digest(config), mode={"code_mode_only": True,
                    "model": config.get("model"), "provider": config.get("model_provider"),
                    "writer_profile_digest": digest(daemon.writer_profile)})
    daemon.store.put("writer_attempt", "writer_attempt:" + actor["run"],
                     {"evidence": evidence, "started": time.time()}, actor["run"])
    attempt = CURRENT.get()
    if attempt and attempt["active"]:
        attempt["evidence"] = evidence
    return evidence


def consume():
    attempt = CURRENT.get()
    if not attempt or not attempt["active"] or not attempt["scheduled"] or attempt.get("claim"):
        return
    daemon, listener = attempt["daemon"], attempt["listener"]
    choice = decision(daemon.store, listener)
    if not choice["permanent"]:
        return
    if not choice["eligible"]:
        raise CoordinatorError("RECOVERY_ATTEMPT_CLAIMED", "Permanent recovery has no unused matching repair", choice)
    daemon.memory.check()
    from .recovery import RECOVERY_CLASSIFICATION
    daemon.store.storage5()
    with daemon.store.transaction():
        daemon.store.durable()
        daemon.store.put("writer_retry", choice["key"], {"state": "claimed", "run": listener["run"],
            "listener": listener["id"], "incident": choice["incident"], "repair": choice["repair"],
            "generation": daemon.store.generation, "claimed": time.time(), "evidence": attempt.get("evidence"),
            "classification": {k: listener[k] for k in RECOVERY_CLASSIFICATION if k in listener}}, listener["run"])
    attempt["claim"] = choice["key"]


def deferred(exc):
    attempt = CURRENT.get()
    # Only an explicit owner acknowledgement that its GO gate never opened can
    # undo a claim. A lost reply, cancellation or missing success is ambiguous.
    if not attempt or not attempt.get("claim") or attempt.get("launched") or not (getattr(exc, "details", None) or {}).get("launch_not_started"):
        return
    store = attempt["daemon"].store
    claim = store.get(attempt["claim"])
    with store.transaction():
        store.durable()
        claim.update(state="deferred", deferred=time.time())
        store.save(claim)
    attempt.pop("claim", None)


def launched():
    attempt = CURRENT.get()
    if attempt and attempt["active"]:
        attempt["launched"] = True


def resolved(store, run):
    record = failure(store, run)
    if record:
        record.update(state="resolved", completed=time.time())
        store.save(record)
