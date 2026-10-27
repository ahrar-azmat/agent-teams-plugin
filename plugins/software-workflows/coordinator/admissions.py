"""Deadline-bounded file admission with durable, independently ended preparation.

The RPC never cancels a filesystem job. A detached, stop-fenced worker checks its
attempt after every await and before any observation or grant. Terminal evidence
is saved outside that worker's lock; only settlement releases its lifetime hold.
"""
import asyncio
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
import threading
import math
import time
import sys
import json
from collections import OrderedDict
import os
from pathlib import Path

from .client import CoordinatorError
from .store import digest

CURRENT = ContextVar("lead_admission", default=None)
STARTED = ContextVar("lead_hook_started", default=None)
# Threat: a file hook's 3 s client timeout can allow an unadmitted edit. The
# decision ends 2 s after hook process birth, leaving 0.5 s to the acceptance
# bound and another 0.5 s to that timeout. Round-16 calibrated pre-image work
# (1.15 s) needs more than the former 1 s receipt-relative budget; measured
# reply/output cost was 6–11 ms. Receipt delay consumes this budget too.
DECISION_SECONDS = 2.0


LAYOUT_CACHE = OrderedDict()
# Classification is an optimisation, never an admission limit. The measured
# matrix has two installed hook roots; 128 entries retain 64 such release pairs
# while bounding attacker-supplied script paths. Eviction merely reparses.
LAYOUT_CACHE_SIZE = 128


def layout_identity(paths):
    return tuple((str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                 for path in paths for info in [path.stat()])


def hook_layout(script):
    """Confirm the script is the entry point declared by its plugin."""
    path = Path(script).resolve()
    if path.name != "coordinator_lead.py" or path.parent.name != "hooks":
        return False
    plugin = path.parent.parent
    files = tuple(p.resolve() for p in (plugin / ".claude-plugin" / "plugin.json", plugin / "hooks" / "hooks.json",
                                       path, plugin / "coordinator" / "client.py"))
    try:
        before = layout_identity(files)
    except OSError:
        LAYOUT_CACHE.pop(str(path), None)
        return False
    cached = LAYOUT_CACHE.get(str(path))
    if cached and cached[0] == before:
        LAYOUT_CACHE.move_to_end(str(path))
        return cached[1]
    manifest = json.loads(files[0].read_text())
    hooks = json.loads(files[1].read_text())
    result = (manifest.get("name") == "software-workflows" and path.is_file() and
            (plugin / "coordinator" / "client.py").is_file() and any(
                hook.get("command") == "python3" and
                hook.get("args") == ["${CLAUDE_PLUGIN_ROOT}/hooks/coordinator_lead.py"]
                for group in hooks.get("hooks", {}).get("PreToolUse", [])
                for hook in group.get("hooks", [])))
    try:
        unchanged = before == layout_identity(files) and all(os.path.isfile(p) for p in files)
    except OSError:
        unchanged = False
    if unchanged:
        LAYOUT_CACHE[str(path)] = (before, result)
        LAYOUT_CACHE.move_to_end(str(path))
        while len(LAYOUT_CACHE) > LAYOUT_CACHE_SIZE:
            LAYOUT_CACHE.popitem(last=False)
    else:
        LAYOUT_CACHE.pop(str(path), None)
    return result if unchanged else False


def peer_started(writer):
    """Kernel birth of an installed, short-lived hook, without spawning ps.

    Only interpreter + script (optionally -B) is a supported hook invocation.
    Wrappers, -c/-m programs, stdin and script data arguments use request time.
    This is a tighter deadline, never authorization or evidence of completion.
    Return the fallback reason too: a lookup failure must not be silent.
    """
    if sys.platform != "darwin":
        return {"reason": "peer lookup unavailable on this platform"}
    try:
        from .processes import identity, arguments
        peer = writer.get_extra_info("socket")
        info = identity(peer.getsockopt(0, 2))  # SOL_LOCAL / LOCAL_PEERPID
        argv = arguments(info["pid"])
        interpreter = Path(argv[0].decode()).name if argv else ""
        script_args = argv[2:] if len(argv) > 1 and argv[1] == b"-B" else argv[1:]
        python = interpreter == "Python" or interpreter == "python3" or (
            interpreter.startswith("python3.") and interpreter[8:].isdigit())
        if not python or len(script_args) != 1 or not script_args[0].startswith(b"/"):
            return {"reason": "peer is not an interpreter running the hook as its only script argument"}
        path = Path(script_args[0].decode()).resolve()
        if not hook_layout(str(path)):
            return {"reason": "peer script is not a declared software-workflows lead-hook entry point"}
        seconds, micros = info["birth"].split(":")
        began = int(seconds) + int(micros) / 1_000_000
        return {"started": began, "script": str(path)}
    except (OSError, AttributeError, TypeError, ValueError, CoordinatorError):
        return {"reason": "peer lookup or hook-layout validation failed", "error": sys.exc_info()[0].__name__}


def identity(payload):
    return "lead_admission_" + digest([payload.get("session_id"), payload.get("tool_use_id")])


def runtime(daemon):
    if not hasattr(daemon, "admissions"):
        daemon.admissions = {}
    if not hasattr(daemon, "admission_phases"):
        daemon.admission_phases = {}
    return daemon.admissions


def progress(daemon, attempt):
    return getattr(daemon, "admission_phases", {}).get(
        attempt["id"], attempt.get("phase", "preparation"))


def save_attempt(daemon, attempt):
    # Identity, deadline, state and end evidence remain durable. Progress labels
    # are live diagnostics; persist the final label with the state transition,
    # not an independent FULL-sync transaction at every preparation checkpoint.
    attempt["phase"] = progress(daemon, attempt)
    daemon.store.save(attempt)


def busy(daemon, attempt):
    from .lead_calls import find
    call = find(daemon.store, attempt["session_id"], attempt["tool_use_id"])
    paths = (call or {}).get("paths") or attempt["paths"]
    checkout = (call or {}).get("checkout")
    lock = daemon.ledger.locks.get(checkout)
    status = lock.status() if lock and lock.locked() else {}
    blocker = status.get("holder") or {"operation": (call or {}).get("operation"),
        "actor": (call or {}).get("actor"), "kind": "lead preparation", "admission": attempt["id"]}
    phase = status.get("phase") or progress(daemon, attempt)
    # Queue age and its measured service time, not a fixed retry promise.
    producer = daemon.ledger.scans.producers.get(checkout)
    scan = producer.status() if producer else {}
    service = scan.get("last_seconds", 0)
    retry = max(.05, service * (1 + bool(scan.get("queued"))) - scan.get("active_seconds", 0),
                status.get("oldest_waiter", {}).get("age_seconds", 0) if status.get("oldest_waiter") else 0)
    if progress(daemon, attempt) == "recovery":
        from .recovery import retry_after, recovering_counts
        blocker = {"kind": "coordinator startup recovery", "generation": daemon.store.generation,
                   "recovering": recovering_counts(daemon)}
        phase = "startup recovery"
        retry = retry_after(daemon) if daemon.recovery_began is not None else max(
            .05, time.time() - daemon.store.get("daemon")["started"])
    holder = blocker.get("operation") or blocker.get("actor") or blocker.get("observation") or blocker.get("admission")
    if blocker["kind"] == "coordinator startup recovery":
        holder = blocker["kind"]
    return CoordinatorError("CLAIM_CONFLICT",
        "LEAD_ADMISSION_BUSY: this edit was not admitted; nothing ran. "
        "Wait for " + str(holder or blocker.get("kind", "checkout preparation")) + " (" + phase +
        ") to finish, then retry the edit with a new tool-use id. Estimated retry in " + format(retry, ".2f") + " seconds.",
        {"reason": "LEAD_ADMISSION_BUSY", "paths": paths, "retry_after_seconds": retry,
         "retry_estimate": True, "attempt": attempt["id"],
         "conflicts": [{"holder": holder or attempt["id"], "kind": blocker.get("kind", "lead-admission"),
                        "phase": phase, "checkout": checkout, "paths": paths, "blocker": blocker}]})


def deadline():
    context = CURRENT.get()
    if context is None:
        return None
    check()
    return context[0].store.get(context[1])["deadline"]


def timeout_error():
    context = CURRENT.get()
    if context:
        return busy(context[0], context[0].store.get(context[1]))
    from .locks import busy as native_busy
    return native_busy()


def cancel_reads(daemon, ident):
    for future, cancelled in getattr(daemon, "admission_reads", {}).get(ident, []):
        cancelled.set()
        future.cancel()  # True only while still queued; never releases running IO.


async def io(function, *args, **kwargs):
    from .locks import finish_thread, joined
    context = CURRENT.get()
    if context is None:
        return await finish_thread(function, *args, **kwargs)
    check("pre-image IO")
    daemon, ident = context
    cutoff = deadline()
    if not hasattr(daemon, "admission_executor"):
        # A separate pool keeps cheap pre-images out of the scan executor queue.
        # Its standard-library capacity is not an admission limit: the whole
        # decision deadline bounds waiting, with the legacy refusal envelope.
        daemon.admission_executor = ThreadPoolExecutor(thread_name_prefix="lead-preimages")
        daemon.admission_reads = {}
    cancelled = threading.Event()
    def work():
        if cancelled.is_set() or time.time() >= cutoff:
            return None
        return function(*args, **kwargs)
    from . import telemetry
    future = daemon.admission_executor.submit(
        telemetry.detached_context(traces=telemetry.CURRENT.get()).run, work)
    entry = (future, cancelled)
    daemon.admission_reads.setdefault(ident, []).append(entry)
    waiting = asyncio.wrap_future(future)
    try:
        done, _ = await asyncio.wait({waiting}, timeout=max(0, cutoff - time.time()))
        if not done:
            cancelled.set()
            if not future.cancel():
                # The stop fence owns a running reader until it really exits.
                # Its stale result is discarded; no ledger effects occur here.
                try:
                    await joined(waiting)
                except Exception:
                    pass
            raise timeout_error()
        if waiting.cancelled():
            raise timeout_error()
        result = waiting.result()
        check()
        return result
    except asyncio.CancelledError as cancellation:
        cancelled.set()
        future.cancel()
        # Cancellation of the waiter cannot retire a running executor owner.
        try:
            await joined(waiting, propagate_cancel=False)
        except Exception as exc:
            raise cancellation from exc
        except asyncio.CancelledError:
            pass
        raise
    finally:
        daemon.admission_reads[ident].remove(entry)
        if not daemon.admission_reads[ident]:
            del daemon.admission_reads[ident]


async def wait(awaitable):
    cutoff = deadline()
    if cutoff is None:
        return await awaitable
    try:
        async with asyncio.timeout(max(0, cutoff - time.time())):
            return await awaitable
    except TimeoutError:
        raise timeout_error() from None


def check(phase=None):
    context = CURRENT.get()
    if context is None:
        return
    daemon, ident = context
    attempt = daemon.store.get(ident)
    if (attempt["generation"] != daemon.store.generation or attempt["state"] != "preparing"
            or time.time() >= attempt["deadline"]):
        raise busy(daemon, attempt)
    if phase:
        runtime(daemon)
        daemon.admission_phases[ident] = phase


def abandoned():
    context = CURRENT.get()
    if context is None:
        return False
    daemon, ident = context
    attempt = daemon.store.get(ident)
    return (attempt["generation"] != daemon.store.generation or attempt["state"] != "preparing"
            or time.time() >= attempt["deadline"])


def bind(record):
    context = CURRENT.get()
    if context:
        record["execution"]["admission"] = context[1]


def mark():
    context = CURRENT.get()
    return context[1] if context else None


def release_owned(store, record):
    ident = record.get("execution", {}).get("admission")
    attempt = store.get(ident, required=False) if ident else None
    if attempt and not attempt.get("granted"):
        for claim in store.selected("SELECT * FROM records WHERE kind='claim' AND json_extract(data,'$.admission')=?", (ident,)):
            if claim["state"] == "held":
                claim.update(state="released", released=time.time())
                store.save(claim)


def record_end(daemon, payload):
    """Record terminal evidence even while preparation owns the host-call lock."""
    from .lead_calls import find
    attempt = daemon.store.get(identity(payload), required=False)
    if not attempt:
        return None
    event = payload.get("hook_event_name")
    if event not in ("PostToolUse", "PostToolUseFailure", "PostToolBatch"):
        return None
    record = find(daemon.store, payload.get("session_id"), payload.get("tool_use_id"))
    response = payload.get("tool_response")
    evidence = {"source": event, "observed": time.time(),
                "failed": event == "PostToolUseFailure" or isinstance(response, dict) and response.get("isError") is True,
                "rewritten": bool(record and record["input_digest"] != digest([payload.get("tool_name"), payload.get("tool_input") or {}]))}
    with daemon.store.transaction():
        if not attempt.get("end"):
            attempt.update(end=evidence, ended=time.time())
            if attempt["state"] == "preparing":
                attempt["state"] = "ended"
            save_attempt(daemon, attempt)
        if record and record.get("execution", {}).get("phase") in ("admitting", "open", "settling"):
            execution = record["execution"]
            if not execution.get("end"):
                execution.update(phase="settling", end=evidence)
                daemon.store.save(record)
                daemon.store.event("lead_hook/execution_ended", record["id"], evidence)
    cancel_reads(daemon, attempt["id"])
    return attempt


async def dispatch(daemon, payload):
    from .intents import dispatch as apply
    from .lead_calls import lock_key
    key = lock_key(payload.get("session_id"), payload.get("tool_use_id"))
    ident = identity(payload)
    jobs = runtime(daemon)
    if payload.get("hook_event_name") != "PreToolUse":
        attempt = record_end(daemon, payload)
        job = jobs.get(ident)
        if attempt and job and not job.done():
            async def complete():
                await asyncio.shield(job)
                async with daemon.actor_locks.hold(key):
                    return await apply(daemon, payload)
            daemon.background(complete())
            return {"coordinated": True, "settled": False, "completion_recorded": True}
        async with daemon.actor_locks.hold(key):
            return await apply(daemon, payload)
    existing = daemon.store.get(ident, required=False)
    if existing:
        if existing.get("granted"):
            # The duplicate Pre branch reads the durable call without awaiting.
            # Its settled/rewritten states refuse; an open grant retains exclusion.
            return await apply(daemon, payload)
        if existing["state"] != "preparing" or ident not in jobs:
            raise busy(daemon, existing)
        attempt = existing
    else:
        now = time.time()
        supplied = payload.get("admission_deadline")
        peer = STARTED.get() or {"reason": "no socket peer context"}
        began = peer.get("started", now)
        candidates = [(began + DECISION_SECONDS, "peer birth" if "started" in peer else "receipt")]
        if type(supplied) in (float, int) and math.isfinite(supplied):
            candidates.append((supplied, "supplied"))
        deadline, source = min(candidates, key=lambda item: item[0])
        data = payload.get("tool_input") or {}
        attempt = daemon.store.put("lead_admission", ident, {
            "generation": daemon.store.generation, "deadline": deadline, "created": now, "hook_started": began,
            "deadline_source": source, "deadline_peer": peer,
            "state": "preparing", "phase": "host-call lock", "session_id": payload.get("session_id"),
            "tool_use_id": payload.get("tool_use_id"), "paths": [data.get("notebook_path") or data.get("file_path")],
            "input_digest": digest([payload.get("tool_name"), data])})
        async def prepare():
            token = CURRENT.set((daemon, ident))
            task = asyncio.current_task()
            daemon.inflight[task] = "lead-hook/dispatch"
            try:
                # Queueing behind family retirement is part of admission too.
                # The outer decision wait still refuses on time; this task may
                # never produce a grant after ownership takes its deadline.
                check("ownership")
                await daemon.leases.call("ping")
                check("ownership")
                async with daemon.actor_locks.hold(key):
                    result = await apply(daemon, payload)
                    check()
                    current = daemon.store.get(ident)
                    current.update(state="granted", granted=True, decided=time.time())
                    save_attempt(daemon, current)
                    return result, None
            except Exception as exc:
                current = daemon.store.get(ident)
                if current["state"] == "preparing":
                    current.update(state="expired" if time.time() >= current["deadline"] else "refused", decided=time.time())
                    save_attempt(daemon, current)
                return None, exc
            finally:
                CURRENT.reset(token)
                daemon.inflight.pop(task, None)
                daemon.admission_phases.pop(ident, None)
        jobs[ident] = daemon.background(prepare())
        jobs[ident].add_done_callback(lambda task: jobs.pop(ident, None) if jobs.get(ident) is task else None)
    job = jobs[ident]
    done, _ = await asyncio.wait({job}, timeout=max(0, attempt["deadline"] - time.time()))
    # A completed preparation can wake this waiter after the admission deadline.
    # Keep its durable ownership for settlement, but do not send a late allow.
    if done and time.time() < attempt["deadline"]:
        result, error = job.result()
        if error:
            current = daemon.store.get(ident)
            if time.time() >= current["deadline"] or current["state"] in ("expired", "ended"):
                raise busy(daemon, current)
            raise error
        return result
    current = daemon.store.get(ident)
    # No await between observing the state, abandonment, and the busy reply.
    if current["state"] == "preparing":
        current.update(state="expired", decided=time.time())
        save_attempt(daemon, current)
    cancel_reads(daemon, ident)
    raise busy(daemon, current)
