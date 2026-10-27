"""Claude file-call lifetime, independent of receipt/UNKNOWN state.

Terminal host evidence ends every new admitted call. Calls without a usable host birth or transcript retain the legacy limit.
An admission identity is bookkeeping, not a terminal-evidence channel. Every exclusion reads the same durable
lead_hook.execution record, including while admission or settlement waits.
The host-call lock serializes Pre, Posts and the observer; filesystem/host probes
never authorize a transition using a record saved before their await.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time

from . import admissions
from .client import CoordinatorError
from .custody import normalize, overlaps
from .ledger import read_path, UNREADABLE
from .locks import finish
from .observations import checkout_at, identity_changed
from .store import digest, LEAD_COMPLETION_PENDING, WINDOW_ACTIVE


PHASES = {"admitting", "open", "settling"}
TERMINAL_OPERATIONS = {"verified", "denied", "resolved", "reconciled"}
# 2.4.3/2.4.4's daemon maintenance quarantined prepared lead edits when
# now - operation.created > 120 (the hook-record deadline was 180 s).
# Retain the 120 s operation-gap availability limit for records without
# a usable host birth or transcript path, including new admission-tagged calls. It is not proof
# the host call ended. Each use is durable, counted and exposed in status.
LEGACY_FILE_CALL_SECONDS = 120
# Only for reconstructing admission time from a pre-2.4.5 hook record.
LEGACY_HOOK_RECORD_SECONDS = 180
LEGACY_LIMIT_REASON = "legacy file hook supplied neither usable host birth nor transcript path"


def has_host(execution):
    host = execution.get("host") or {}
    return (isinstance(host, dict) and type(host.get("pid")) is int and host["pid"] > 1
            and isinstance(host.get("birth"), str) and bool(host["birth"]))


def legacy_deadline(execution):
    path = (execution.get("transcript") or {}).get("path")
    if has_host(execution) or isinstance(path, str) and os.path.isabs(path):
        return None  # Missing/unreadable transcript bytes never remove its end-evidence channel.
    return execution["since"] + LEGACY_FILE_CALL_SECONDS


def legacy_status(store):
    # The aggregate subject uses event_subject_kind, rather than scanning every
    # event on each status request. The payload still identifies the individual call.
    row = store.db.execute("SELECT data FROM events WHERE subject='lead-hooks' AND kind='lead_hook/legacy_limit' "
                           "ORDER BY seq DESC LIMIT 1").fetchone()
    last = json.loads(row[0]) if row else None
    return {"seconds": LEGACY_FILE_CALL_SECONDS, "uses": last["use"] if last else 0,
            "reason": LEGACY_LIMIT_REASON, "last": last}


def terminal_state(operation):
    return "verified" if operation["state"] == "verified" else "no_effect" if operation.get("no_effect") else "failed"


def legacy_processed(store, record):
    """Completion evidence written by 5f82588, not quarantine by itself.

    Its failed/no_effect/verified/unrecorded saves follow completion; outside
    already ended coordination without a ledger observation.
    'uncertain' also came from the 120 s pass: only a Post verification failure
    quarantined that legacy hook with a bare exception code/class name. Inspect
    the first durable quarantine reason, so later quarantine cannot rewrite a
    maintenance/recovery gap into evidence that the host ended.
    """
    if record.get("execution"):
        return False
    if record["state"] in ("verified", "no_effect", "failed", "unrecorded", "outside"):
        return True
    if record["state"] != "uncertain" or not record.get("operation"):
        return False
    row = store.db.execute("SELECT reason FROM unknowns WHERE json_extract(context,'$.possible_operation')=? "
                           "ORDER BY rowid LIMIT 1", (record["operation"],)).fetchone()
    return bool(row and row["reason"].isascii() and row["reason"].isidentifier())


def lock_key(session, call):
    return "host:" + digest([session, call])


def active(store, checkout=None, paths=None, exclude=None):
    query = "SELECT * FROM records INDEXED BY window_lead_edit WHERE " + WINDOW_ACTIVE["lead_edit"]
    args = []
    if checkout is not None:
        query += " AND json_extract(data,'$.checkout')=?"
        args.append(checkout)
    for record in store.selected(query + " ORDER BY id", args):
        if record["id"] == exclude:
            continue
        if paths is not None and record.get("paths"):
            root = store.get(record["checkout"])
            if not any(overlaps(normalize(root, a)["key"], normalize(root, b)["key"])
                       for a in paths for b in record["paths"]):
                continue
        yield record


def describe(record):
    execution = record["execution"]
    deadline = legacy_deadline(execution)
    return {"id": record["id"], "operation": record.get("operation"), "unit": record.get("unit"),
            "session_id": record.get("session_id"), "tool_use_id": record.get("tool_use_id"),
            "tool": record["tool"], "checkout": record["checkout"], "paths": record.get("paths", []),
            "phase": execution["phase"], "since": execution["since"], "host": execution.get("host"),
            "waiting_for": "byte settlement" if execution.get("end") else
                "PostToolUse/Failure or PostToolBatch; legacy quarantine limit" if deadline is not None else
                "PostToolUse/Failure, PostToolBatch, transcript tool_result, or birth-verified host exit",
            **({"legacy_limit": {"at": deadline, "seconds": LEGACY_FILE_CALL_SECONDS, "reason": LEGACY_LIMIT_REASON}}
               if deadline is not None and not execution.get("end") else {}),
            **({"observation_problem": execution["problem"]} if execution.get("problem") else {}),
            **({"lease_problem": execution["lease_problem"]} if execution.get("lease_problem") else {})}


def blockers(store, checkout=None, paths=None, exclude=None):
    return [describe(r) for r in active(store, checkout, paths, exclude)]


def conflict(waiting, code="CLAIM_CONFLICT"):
    """The released 2.4.4 file hook enforces only a named claim conflict.

    Include the caller's own unit and admissions with no unit yet. Every guard
    of the lifetime hold uses this same wire contract, including after awaits.
    Restore/handback callers retain their explicit refusal codes.
    """
    return CoordinatorError(code, "A lead file call may still write or is settling its bytes",
        {"conflicts": [{"holder": r.get("unit") or r["id"], "kind": "lead-edit", "call": r["id"],
                        "checkout": r["checkout"], "paths": r.get("paths", [])} for r in waiting],
         "lead_edits": waiting})


def refuse(store, checkout, paths=None, exclude=None, code="CLAIM_CONFLICT"):
    waiting = blockers(store, checkout, paths, exclude)
    if waiting:
        raise conflict(waiting, code)


def find(store, session, call):
    records = list(store.selected("SELECT * FROM records WHERE kind='lead_hook' AND "
        "json_extract(data,'$.tool_use_id')=? AND json_extract(data,'$.session_id')=? ORDER BY id", (call, session)))
    if len(records) > 1:
        raise CoordinatorError("LEAD_HOOK_IDENTITY", "Host call identity is ambiguous", {"records": [r["id"] for r in records]})
    return records[0] if records else None


def host_snapshot(named):
    if (isinstance(named, dict) and type(named.get("pid")) is int and named["pid"] > 1
            and named.get("parent") == named["pid"] and isinstance(named.get("birth"), str) and named["birth"]):
        return {key: named[key] for key in ("pid", "birth", "parent")}
    return {"unavailable": (named or {}).get("unavailable", "Pre supplied no verified host birth") if isinstance(named, dict)
            else "Pre supplied no verified host birth"}


def transcript_result(record):
    """Read complete JSONL records, retaining a cursor only after a complete line.

    Start at zero, including on recovery of the old EOF-based cursor: a result can
    precede a delayed Pre's daemon RPC. A missing file is retried; a replacement or
    truncation is not termination. No age, line-count or total-byte cutoff exists.
    """
    execution = record["execution"]
    transcript = dict(execution.get("transcript") or {})
    path = transcript.get("path")
    if not isinstance(path, str) or not os.path.isabs(path):
        return None, transcript, "no transcript path"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return None, transcript, "transcript is not an owned regular file"
            identity = [info.st_dev, info.st_ino]
            if transcript.get("file") not in (None, identity):
                return None, transcript, "transcript file identity changed"
            transcript["file"] = identity
            cursor = transcript.get("cursor", 0)
            if info.st_size < cursor:
                return None, transcript, "transcript was truncated"
            stream.seek(cursor)
            # Bound this pass by the size observed on open, not an arbitrary
            # limit. A continuously growing transcript cannot starve other
            # calls; its new bytes remain available to the next pass.
            while stream.tell() < info.st_size:
                line = stream.readline(info.st_size - stream.tell())
                if not line or not line.endswith(b"\n"):
                    break
                transcript["cursor"] = stream.tell()
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeError):
                    continue  # damaged content proves nothing; later complete records can still end this call
                if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("sessionId") != record.get("session_id"):
                    continue
                message = entry.get("message")
                parts = message.get("content") if isinstance(message, dict) else None
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") == record.get("tool_use_id"):
                        return {"source": "transcript", "failed": part.get("is_error") is True}, transcript, None
    except OSError as exc:
        return None, transcript, "transcript: " + type(exc).__name__
    return None, transcript, None


def probe(record):
    evidence, transcript, problem = transcript_result(record)
    if evidence:
        return evidence, transcript, problem
    host = record["execution"].get("host") or {}
    if has_host(record["execution"]):
        from .processes import identity
        try:
            current = identity(host["pid"])
            if current is None or current["birth"] != host["birth"] or current["zombie"]:
                return {"source": "host exited", "failed": False}, transcript, None
        except CoordinatorError as exc:
            problem = "host identity: " + exc.code
    elif not problem:
        problem = host.get("unavailable", "host birth unavailable")
    return None, transcript, problem


def close(store, record, state, end=None):
    record = store.get(record["id"])
    record["state"] = state
    execution = record["execution"]
    execution.update(phase="settled", completed=time.time())
    if end and not execution.get("end"):
        execution["end"] = dict(end, observed=time.time())
    execution.pop("problem", None)
    execution.pop("retry_after", None)
    store.save(record)
    admissions.release_owned(store, record)


def forget_marker(store, record):
    """Only after the terminal transaction commits; the marker is a retry hint."""
    session, call = record.get("session_id"), record.get("tool_use_id")
    if session and call:
        marker = hashlib.sha256((session + "\0" + call).encode()).hexdigest()
        try:
            (store.root / "lead-hooks" / marker).unlink()
        except OSError:
            pass  # routing hint only; the durable terminal record is authoritative


async def reconcile_unadmitted(daemon, record):
    """A host completion without admission earns only a path observation.

    This retry is deliberately outside PHASES: no admission, lease, exclusion or
    receipt is reinstated. Commit the observation and acknowledgment together so
    a repeated Post, including after a restart, cannot scan later bytes again.
    """
    store = daemon.store
    record = store.get(record["id"])
    completion = record["execution"].get("unadmitted_completion") or {}
    if completion.get("state") != "pending":
        return

    def observed(outside=False):
        current = store.get(record["id"])
        if outside:
            current["state"] = "outside"
        pending = current["execution"]["unadmitted_completion"]
        pending.update(state="reconciled", completed=time.time())
        pending.pop("problem", None)
        pending.pop("retry_after", None)
        store.save(current)
        store.event("lead_hook/unadmitted_reconciled", current["id"], pending)

    try:
        checkout = store.get(record["checkout"])
        # Scope and the pending observation are already durable. An identity or
        # read failure must leave a retry, never a closed admission refusal.
        if identity_changed(checkout):
            raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Registered checkout identity changed")
        daemon.ledger.require_owned(checkout)
        outside = await daemon.ledger.classify(checkout, record["paths"])
        if set(record["paths"]) <= outside:
            with store.transaction():
                observed(outside=True)  # even an unreadable ignored file needs no byte observation
        else:
            # This completion has no hold of its own. Do not observe a newer
            # admitted call's bytes, or acknowledge a partial observation.
            await daemon.ledger.reconcile(checkout, record["paths"],
                reason="lead post hook without prepared intent", ignore_pending=False, on_observed=observed,
                owner={"kind": "lead-call", "call": record["id"]},
                guard=lambda: daemon.ledger.refuse_mutations(checkout["id"], record["paths"], code="RESTORE_EXECUTING"))
        forget_marker(store, record)
    except BaseException as exc:
        current = store.get(record["id"])
        pending = current["execution"]["unadmitted_completion"]
        if pending["state"] == "pending":
            from .lead_batches import RETRY_SECONDS
            pending["retry_after"] = time.time() + RETRY_SECONDS
            problem = "completion reconciliation: " + getattr(exc, "code", type(exc).__name__)
            if pending.get("problem") != problem:
                pending["problem"] = problem
                store.save(current)
                store.event("lead_hook/unadmitted_reconcile_failed", current["id"], {"reason": problem})
            store.save(current)
        raise


async def settle(daemon, record, evidence=None):
    """Called under the host-call lock. Keep every hold until the byte outcome is durable."""
    store = daemon.store
    record = store.get(record["id"])
    execution = record.get("execution") or {}
    if execution.get("phase") not in PHASES:
        source = (execution.get("end") or {}).get("source")
        event = (evidence or {}).get("source")
        # A batch is not proof a refused call ran. A Post is, even when an old
        # hook allowed a refusal it did not understand. Operational admissions
        # are fail-open, so their batch is sufficient to reconcile too.
        ran = event in ("PostToolUse", "PostToolUseFailure") or (
            event == "PostToolBatch" and source == "admission failed operationally")
        if source in ("admission refused", "admission failed operationally") and ran:
            if not execution.get("unadmitted_completion"):
                execution["unadmitted_completion"] = dict(evidence, state="pending", observed=time.time())
                store.save(record)
        # Also covers completion-only records. Once a Post proved a refused
        # call ran, its pending observation must be retried by a batch too.
        if execution.get("unadmitted_completion"):
            await reconcile_unadmitted(daemon, record)
        current = store.get(record["id"])
        return {"coordinated": current["state"] != "outside", "settled": True,
                "state": current["state"], "duplicate": True}
    if evidence and not execution.get("end"):
        execution.update(phase="settling", end=dict(evidence, observed=time.time()))
        execution.pop("problem", None)
        store.save(record)
        store.event("lead_hook/execution_ended", record["id"], execution["end"])
    end = execution.get("end")
    if not end:
        return {"coordinated": True, "settled": False, "waiting": describe(record)}
    operation = store.get(record.get("operation"), required=False)
    checkout = store.get(record["checkout"])
    try:
        if operation is None:
            await daemon.ledger.reconcile(checkout, record.get("paths") or None,
                                          reason="lead call ended without a prepared image", ignore_pending=False,
                                          owner={"kind": "lead-call", "call": record["id"]})
            state = "unrecorded"
        elif operation["state"] == "prepared" and not end.get("rewritten"):
            expected = operation["intended"]["expected"]
            images = {path: await admissions.io(read_path, store, checkout, path) for path in operation["paths"]}
            if not end.get("failed") and images == expected:
                try:
                    await daemon.ledger.verify(operation, expected, "exact", {"executor": "Claude file tool",
                        "session_id": record["session_id"], "tool_use_id": record["tool_use_id"], "post_event": end["source"]})
                except CoordinatorError as exc:
                    if exc.code not in ("POSTIMAGE_MISMATCH", "REVISION_CHANGED", "OPAQUE_OVERLAP", "OPERATION_STATE"):
                        raise
                    await daemon.ledger.quarantine(operation, "lead completion: " + exc.code)
            else:
                await daemon.ledger.settle_unapplied(operation, "Claude call ended: " + end["source"] +
                    "; changed or partial effects receive no receipt", lambda current: True)
            operation = store.get(operation["id"])
            state = terminal_state(operation)
        elif operation["state"] in TERMINAL_OPERATIONS:
            state = terminal_state(operation)
        else:
            # Recovery/changed inputs never repair authorship by coincidentally matching bytes.
            if operation["state"] != "quarantined":
                await daemon.ledger.quarantine(operation, "lead call completion input differs from its admission")
            await daemon.ledger.reconcile(checkout, record.get("paths") or None,
                                          reason="uncertain lead call ended", ignore_pending=False,
                                          owner={"kind": "lead-call", "call": record["id"]})
            state = "uncertain"
        with store.transaction():
            if operation is not None:
                daemon.ledger.end_execution(operation, transaction=False)  # also retires an unreleased turn-44 flag
            close(store, record, state)
        forget_marker(store, record)
        async with daemon.custody.condition:
            daemon.custody.condition.notify_all()
        return {"coordinated": True, "settled": True, "operation": record.get("operation"), "state": state}
    except BaseException as exc:
        from .lead_batches import RETRY_SECONDS
        current = store.get(record["id"])
        current["execution"]["problem"] = "settlement: " + getattr(exc, "code", type(exc).__name__)
        current["execution"]["retry_after"] = time.time() + RETRY_SECONDS
        store.save(current)
        raise


async def dispatch(daemon, payload):
    from .intents import FILES, intended
    store = daemon.store
    tool, event = payload.get("tool_name"), payload.get("hook_event_name")
    if tool not in FILES or event not in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch"):
        raise CoordinatorError("LEAD_HOOK_FIELDS", "Unsupported lead file hook event")
    session, call = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call, str) or not call:
        raise CoordinatorError("LEAD_HOOK_IDENTITY", "Host session_id and tool_use_id are required")
    record = find(store, session, call)
    data = payload.get("tool_input") or {}
    signature = digest([tool, data])
    if event != "PreToolUse" and record and record.get("execution"):
        response = payload.get("tool_response")
        evidence = {"source": event, "failed": event == "PostToolUseFailure" or
                    isinstance(response, dict) and response.get("isError") is True,
                    "rewritten": record["input_digest"] != signature}
        return await settle(daemon, record, evidence)
    if event != "PreToolUse" and record:
        # Old terminal hook saves may have no execution metadata. Their durable
        # observation/receipt is already enough; never read later bytes again.
        operation = store.get(record.get("operation"), required=False)
        if (legacy_processed(store, record) or
                operation and operation["state"] in TERMINAL_OPERATIONS or
                record["state"] == "refused" and event == "PostToolBatch"):
            return {"coordinated": record["state"] != "outside", "settled": True,
                    "state": record["state"], "duplicate": True}
    if record and event == "PreToolUse":
        if record["input_digest"] != signature:
            if record.get("execution", {}).get("phase") in PHASES:
                raise conflict([describe(record)])
            refuse(store, record["checkout"], record.get("paths"), record["id"])
            raise CoordinatorError("LEAD_HOOK_REWRITTEN", "A host call cannot be rebound to different input")
        if record["state"] == "outside":
            return {"coordinated": False, "settled": True, "outside": True}
        if record.get("execution", {}).get("phase") == "open":
            return {"coordinated": True, "operation": record.get("operation"), "duplicate": True}
        refuse(store, record["checkout"], record.get("paths"))
        raise CoordinatorError("LEAD_HOOK_UNCERTAIN", "This host call was already refused or ended; use a new tool use id")
    value = data.get("notebook_path") if tool == "NotebookEdit" else data.get("file_path")
    if not isinstance(value, str) or not value:
        return {"coordinated": False, "settled": True}
    target = str((Path(payload.get("cwd", "")) / value).resolve())
    checkout, changed = checkout_at(store, target)
    if checkout is None:
        return {"coordinated": False, "settled": True}
    if changed and event == "PreToolUse":
        raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Registered checkout identity changed")
    try:
        path = normalize(checkout, target)["path"]
    except (CoordinatorError, OSError) as exc:
        if event == "PreToolUse" or isinstance(exc, CoordinatorError) and exc.code not in UNREADABLE:
            raise
        # The lexical scope is known even if a parent is unreadable or the
        # endpoint is not an ordinary file. Let the durable observer read it.
        path = Path(target).relative_to(checkout["root"]).as_posix()
    ident = record["id"] if record else "lead_hook_" + digest([checkout["identity"], session, call])
    if event != "PreToolUse":
        # 2.4.4's refused() save omitted session/call fields, so find() could
        # not locate it. The checkout-derived identity still must never be
        # overwritten by a supposedly new completion-only record.
        record = record or store.get(ident, required=False)
        operation = store.get((record or {}).get("operation"), required=False)
        if record and (legacy_processed(store, record) or operation and operation["state"] in TERMINAL_OPERATIONS or
                       record["state"] == "refused" and event == "PostToolBatch"):
            return {"coordinated": record["state"] != "outside", "settled": True,
                    "state": record["state"], "duplicate": True}
        # Completion is not admission. Persist its exact path and retry before
        # any classification/read can fail; take no lease, unit, claim or hold.
        now = time.time()
        response = payload.get("tool_response")
        evidence = {"source": event, "observed": now, "failed": event == "PostToolUseFailure" or
                    isinstance(response, dict) and response.get("isError") is True}
        if record:
            record.setdefault("session_id", session)
            record.setdefault("tool_use_id", call)
            record.setdefault("paths", (operation or {}).get("paths") or [path])
            if operation:
                # An unfinished legacy admission keeps its operation, pre-image
                # and scope. This completion ends it through normal settlement.
                record["execution"] = {"phase": "open", "since": operation.get("created", now)}
            else:
                if record["state"] != "refused":
                    record["state"] = "unrecorded"
                record["execution"] = {"phase": "settled", "since": now, "completed": now, "end": evidence,
                                       "unadmitted_completion": dict(evidence, state="pending")}
            store.save(record)
        else:
            record = store.put("lead_hook", ident, {"tool": tool, "state": "unrecorded", "input_digest": signature,
                "generation": store.generation, "session_id": session, "tool_use_id": call, "checkout": checkout["id"],
                "paths": [path], "execution": {"phase": "settled", "since": now, "completed": now, "end": evidence,
                    "unadmitted_completion": dict(evidence, state="pending")}})
        return await settle(daemon, record, evidence)
    waiting = list(active(store, checkout["id"], [path]))
    if event == "PreToolUse" and waiting:
        # A denied host call must never become a fresh admission if its Pre is
        # replayed after the conflicting call ends.
        store.put("lead_hook", ident, {"tool": tool, "state": "refused", "input_digest": signature,
            "generation": store.generation, "session_id": session, "tool_use_id": call,
            "checkout": checkout["id"], "paths": [path],
            "execution": {"phase": "settled", "since": time.time(), "completed": time.time(),
                          "end": {"source": "admission refused", "observed": time.time()}}})
        raise conflict([describe(r) for r in waiting])
    record = store.put("lead_hook", ident, {"tool": tool, "state": "received", "input_digest": signature,
        "generation": store.generation, "session_id": session, "tool_use_id": call, "checkout": checkout["id"],
        "paths": [path], "execution": {"phase": "admitting", "since": time.time(),
            "host": host_snapshot(payload.get("host")), "transcript": dict(payload.get("transcript") or
                {"path": payload.get("transcript_path")}, cursor=0)}})
    record = store.get(ident)
    admissions.bind(record)
    store.save(record)
    try:
        admissions.check("recovery")
        while not daemon.ready:
            await asyncio.sleep(0.01)
            admissions.check()
        if daemon.recovering:
            from .recovery import recovered
            await admissions.wait(recovered(daemon, "lead-hook/dispatch", {"payload": payload}))
        admissions.check("classification")
        from .lead import attach
        from .routing import qualified
        from .participants import require_writable
        outside = await daemon.ledger.classify(checkout, [path])
        admissions.check("lease")
        if path in outside:
            close(store, record, "outside", {"source": "outside-ledger"})
            return {"coordinated": False, "settled": True, "outside": True}
        # A reservation is already durable before attaching, taking a lease or saving bytes can wait.
        await finish(daemon.ensure_lease(checkout["id"]))
        admissions.check("lead attachment")
        mapping = await attach(daemon, checkout["id"], session, cwd=payload.get("cwd"), hook=True)
        admissions.check("claims")
        actor = dict(store.get(mapping["actor"]), checkout=checkout["id"])
        record = store.get(ident)
        record.update(unit=actor["unit"], actor=actor["id"])
        store.save(record)
        require_writable(store, actor["unit"], checkout["id"])
        await daemon.custody.acquire(actor, [qualified(checkout["id"], path)], lead_call=ident)
        admissions.check("pre-image executor")
        def image_for_intent():
            # One executor owner captures the image, validates its blob and
            # constructs the intent. This does not mutate the ledger; prepare
            # still re-reads and validates under its two reservation phases.
            pre = read_path(store, checkout, path)
            raw = store.read_blob(pre["blob"]) if pre["blob"] else b""
            try:
                content = intended(tool, data, raw)
            except CoordinatorError as exc:
                return pre, None, exc
            return pre, {"blob": store.blob(content), "mode": pre["mode"] or "100644"}, None
        pre, image, intent_error = await admissions.io(image_for_intent)
        if intent_error:
            admissions.check()
            # An uncalibrated adapter still runs unrecorded, with the same lifetime exclusions.
            record = store.get(ident)
            record["execution"]["phase"] = "open"
            record["unverified"] = intent_error.envelope()
            store.save(record)
            return {"coordinated": True, "unverified": intent_error.code, "settled": False}
        admissions.check("checkout preparation")
        async def prepare():
            return await daemon.ledger.prepare(actor, "lead-edit", {"expected": {path: image}, "host_tool": tool,
                "input_digest": signature}, [path], native={"session_id": session, "tool_use_id": call, "lead_hook": ident,
                    "host": record["execution"]["host"], "transcript": record["execution"]["transcript"]}, outside=outside)
        try:
            operation = await prepare()
        except CoordinatorError as exc:
            if exc.code != "STALE_BASE":
                raise
            operation = await prepare()
        record = store.get(ident)
        record["operation"] = operation["id"]
        store.save(record)
        admissions.check("grant")
        if operation["pre"][path] != pre:
            await daemon.ledger.quarantine(operation, "pre-image changed during host intent preparation")
            raise CoordinatorError("LEAD_PREIMAGE_CHANGED", "Prepare the edit again against the current pre-image")
        record = store.get(ident)
        record["state"] = "prepared"
        record["execution"]["phase"] = "open"
        store.save(record)
        return {"coordinated": True, "operation": operation["id"], "settled": False}
    except BaseException as exc:
        # This dispatch failed before admission completed. The hook may allow
        # the tool on operational failure; never turn its later Post into an
        # exact receipt. A completed admission whose transport reply is lost
        # never enters here and retains its entire lifetime hold.
        if admissions.abandoned():
            # Its job may have waited in an executor beyond the answer deadline.
            # Keep exclusion until terminal evidence; stale work observes nothing.
            raise
        current = store.get(ident)
        if current["execution"]["phase"] == "admitting":
            refused = isinstance(exc, CoordinatorError) and not exc.operational
            reason = "admission refused" if refused else "admission failed operationally"
            operation = store.get(current.get("operation"), required=False)
            try:
                if operation and operation["state"] not in TERMINAL_OPERATIONS:
                    await finish(daemon.ledger.quarantine(operation, reason))
            finally:
                with store.transaction():
                    if operation:
                        daemon.ledger.end_execution(operation, transaction=False)
                    close(store, current, "refused" if refused else "unrecorded", {"source": reason})
                    store.event("lead_hook/admission_failed", ident,
                                {"code": getattr(exc, "code", type(exc).__name__), "refusal": refused})
        raise


async def expire_legacy(daemon, record):
    """The explicit legacy availability exception, under the same call lock as Posts.

    Quarantine/UNKNOWN commits before lifetime exclusion ends. A late Post only
    acknowledges this terminal record; it cannot turn the quarantine into a receipt.
    """
    store = daemon.store
    record = store.get(record["id"])
    execution = record["execution"]
    deadline = legacy_deadline(execution)
    if (execution["phase"] not in PHASES or execution.get("end") or deadline is None
            or time.time() < deadline):
        return
    operation = store.get(record.get("operation"), required=False)
    reason = LEGACY_LIMIT_REASON + "; no Post or batch within " + str(LEGACY_FILE_CALL_SECONDS) + " seconds"
    if operation:
        await daemon.ledger.quarantine(operation, reason)
    else:
        with store.transaction():
            for path in record.get("paths") or ["*"]:
                store.unknown(record["checkout"], path, reason, {"lead_hook": record["id"]})
    with store.transaction():
        details = {"use": legacy_status(store)["uses"] + 1, "seconds": LEGACY_FILE_CALL_SECONDS,
                   "reason": reason, "since": execution["since"], "observed": time.time(),
                   "lead_hook": record["id"], "operation": record.get("operation"),
                   "checkout": record["checkout"], "paths": record.get("paths", []),
                   "session_id": record.get("session_id"), "tool_use_id": record.get("tool_use_id")}
        current = store.get(record["id"])
        current["execution"]["legacy_limit"] = details
        store.save(current)
        if operation:
            daemon.ledger.end_execution(operation, transaction=False)
        close(store, current, "uncertain", {"source": "legacy availability limit", "call_ended": False})
        store.event("lead_hook/legacy_limit", "lead-hooks", details)
    forget_marker(store, record)
    async with daemon.custody.condition:
        daemon.custody.condition.notify_all()
    await daemon.release_lease(record["checkout"])


async def read_probe(record):
    """Read only a snapshot; neither cancellation nor interpreter exit waits for this thread."""
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def deliver(value, error):
        if not result.done():
            if error is None:
                result.set_result(value)
            else:
                result.set_exception(error)

    def read():
        value, error = None, None
        try:
            value = probe(record)
        except Exception as exc:
            error = exc
        try:
            loop.call_soon_threadsafe(deliver, value, error)
        except RuntimeError:
            pass  # the loop closed; a read-only result has no work to finish

    threading.Thread(target=read, name="lead-probe", daemon=True).start()
    return await result


def retry_call(daemon, ident, observation=None):
    """Probe and settlement have separate keyed jobs; neither blocks the observer."""
    from .lead_batches import RETRY_SECONDS
    if not hasattr(daemon, "lead_completion_retries"):
        daemon.lead_completion_retries = {}
        daemon.lead_completion_after = {}
        daemon.lead_completion_probes = {}
    try:
        saved = daemon.store.get(ident)
    except Exception:
        # A failed scheduling read must not escape into the shared observer.
        daemon.lead_completion_after[ident] = time.monotonic() + RETRY_SECONDS
        return
    execution = saved["execution"]
    pending = execution.get("unadmitted_completion") or {}
    if (daemon.closing or (pending or execution).get("retry_after", 0) > time.time()
            or time.monotonic() < daemon.lead_completion_after.get(ident, 0)):
        return
    if observation is None and execution.get("phase") in PHASES and not execution.get("end"):
        if ident in daemon.lead_completion_probes:
            return

        async def inspect():
            evidence, transcript, problem, error = None, None, None, None
            try:
                try:
                    evidence, transcript, problem = await read_probe(saved)
                except Exception as exc:
                    error = exc
                retry_call(daemon, ident, (saved, evidence, transcript, problem, error))
            finally:
                daemon.lead_completion_probes.pop(ident, None)

        daemon.lead_completion_probes[ident] = daemon.background(inspect())
        return
    if ident in daemon.lead_completion_retries:
        return

    async def retry():
        task = asyncio.current_task()
        try:
            evidence, transcript, problem, probe_error = (None, None, None, None)
            snapshot = saved
            if observation is not None:
                snapshot, evidence, transcript, problem, probe_error = observation
            # The same central fence as an arriving completion, before waiting on its lock.
            daemon.admit_rpc("lead-hook/dispatch", {"payload": {"hook_event_name": "PostToolBatch"}})
            daemon.inflight[task] = "lead-hook/dispatch"
            async with daemon.actor_locks.hold(lock_key(saved.get("session_id"), saved.get("tool_use_id"))):
                current = daemon.store.get(ident)
                if observation is not None and current["_revision"] != snapshot["_revision"]:
                    return  # discard obsolete evidence, cursor, problem and probe errors
                if probe_error is not None:
                    raise probe_error
                execution = current["execution"]
                pending = execution.get("unadmitted_completion") or {}
                if daemon.closing or (pending or execution).get("retry_after", 0) > time.time():
                    return
                if pending.get("state") == "pending":
                    await finish(reconcile_unadmitted(daemon, current))
                elif execution.get("phase") in PHASES:
                    if execution.get("lease_problem"):
                        try:
                            await finish(daemon.ensure_lease(current["checkout"]))
                        except CoordinatorError:
                            pass  # unavailable custody cannot suppress host end evidence
                        else:
                            execution.pop("lease_problem", None)
                            daemon.store.save(current)
                    if transcript is not None and transcript != execution.get("transcript"):
                        execution["transcript"] = transcript
                        daemon.store.save(current)
                    if evidence or execution.get("end"):
                        await finish(settle(daemon, current, evidence))
                    elif legacy_deadline(execution) is not None and time.time() >= legacy_deadline(execution):
                        await finish(expire_legacy(daemon, current))
                    elif problem != execution.get("problem"):
                        if problem:
                            execution["problem"] = problem
                        else:
                            execution.pop("problem", None)
                        daemon.store.save(current)
        except Exception as exc:
            # The call records its problem; even a failed problem save cannot
            # disable other calls. A failed database write also gets a cooldown.
            daemon.lead_completion_after[ident] = time.monotonic() + RETRY_SECONDS
            try:
                current = daemon.store.get(ident)
                execution = current.get("execution") or {}
                if execution.get("phase") in PHASES:
                    reason = "observer: " + getattr(exc, "code", type(exc).__name__)
                    changed = execution.get("problem") != reason
                    execution.update(problem=reason, retry_after=time.time() + RETRY_SECONDS)
                    daemon.store.save(current)
                    if changed:
                        daemon.store.event("lead_hook/observation_failed", ident, {"reason": reason})
            except Exception:
                pass  # preserve the hold and in-memory cooldown even if bookkeeping fails
        else:
            daemon.lead_completion_after.pop(ident, None)
        finally:
            daemon.inflight.pop(task, None)
            daemon.lead_completion_retries.pop(ident, None)

    daemon.lead_completion_retries[ident] = daemon.background(retry())


async def observe(daemon):
    """Schedule independently; never await a host probe, lease or settlement here."""
    while not daemon.stop.is_set():
        for saved in list(active(daemon.store)):
            if daemon.closing:
                break
            if saved["execution"].get("retry_after", 0) <= time.time():
                retry_call(daemon, saved["id"])
        # A completion without admission holds nothing. Retry its durable
        # path observation independently, including after daemon replacement.
        pending = list(daemon.store.selected("SELECT * FROM records INDEXED BY lead_completion_pending WHERE " +
                                             LEAD_COMPLETION_PENDING + " ORDER BY id"))
        for saved in pending:
            if daemon.closing:
                break
            if saved["execution"]["unadmitted_completion"].get("retry_after", 0) > time.time():
                continue
            retry_call(daemon, saved["id"])
        from .lead_batches import observe as observe_batches
        await observe_batches(daemon)
        await asyncio.sleep(0.25)


async def recover(daemon):
    """Import old incomplete admissions, including the reverted turn-44 executing records.

    A restart preserves exclusion but cannot re-establish exact attribution across
    an old daemon's gap. UNKNOWN remains until restoration, after the call ends.
    Legacy calls without an end-evidence channel retain the old quarantine limit.
    """
    store = daemon.store
    from .intents import FILES
    operations = {o["id"]: o for o in store.all("operation") if o["kind"] == "lead-edit"}
    for record in list(store.all("lead_hook")):
        if record.get("tool") not in FILES or record.get("execution"):
            continue
        operation = operations.get(record.get("operation"))
        if legacy_processed(store, record):
            if operation:
                daemon.ledger.end_execution(operation)
            forget_marker(store, record)
            continue  # preserve the completed hook verbatim, including its operation link
        if record["state"] not in ("received", "prepared", "uncertain") and not (operation or {}).get("executing"):
            continue
        native = (operation or {}).get("native") or {}
        record.update(paths=(operation or {}).get("paths", []),
                      session_id=record.get("session_id") or native.get("session_id"),
                      tool_use_id=record.get("tool_use_id") or native.get("tool_use_id"))
        since = (operation or {}).get("created")
        if since is None:
            since = record["deadline"] - LEGACY_HOOK_RECORD_SECONDS if record.get("deadline") else time.time()
        record["execution"] = {"phase": "open", "since": since,
            "host": native.get("host") or {"unavailable": "legacy admission has no host birth"},
            "transcript": dict(native.get("transcript") or {}, cursor=0)}
        with store.transaction():
            store.save(record)
            if operation and operation["state"] in TERMINAL_OPERATIONS:
                # The old receipt transaction won, then its hook save was lost.
                # That durable outcome is sufficient; do not read bytes again
                # or append another receipt, acquire a lease or import a hold.
                daemon.ledger.end_execution(operation, transaction=False)
                close(store, record, terminal_state(operation), {"source": "legacy terminal operation",
                                                               "operation_state": operation["state"]})
                store.event("lead_hook/recovered_terminal", record["id"], {"operation": operation["id"],
                                                                          "state": operation["state"]})
        if operation and operation["state"] in TERMINAL_OPERATIONS:
            forget_marker(store, record)
    bound = {r.get("operation") for r in active(store)}
    for operation in operations.values():
        native = operation.get("native") or {}
        if operation["id"] in bound or not operation.get("executing") or operation.get("executor"):
            # External editors have a durable birth-bound lifetime of their own.
            # Importing them as legacy hook calls invents a second, unobservable
            # host end and can retain exclusion after the real executor exits.
            continue
        if operation["state"] in TERMINAL_OPERATIONS:
            daemon.ledger.end_execution(operation)
            continue
        ident = "lead_hook_recovered_" + operation["id"]
        store.put("lead_hook", ident, {"tool": operation["intended"].get("host_tool", "Write"), "state": "uncertain",
            "input_digest": operation["intended"].get("input_digest"), "checkout": operation["checkout"],
            "unit": operation["unit"], "actor": operation["actor"], "operation": operation["id"], "paths": operation["paths"],
            "session_id": native.get("session_id"), "tool_use_id": native.get("tool_use_id"),
            "execution": {"phase": "open", "since": operation["created"], "host": native.get("host") or {},
                          "transcript": dict(native.get("transcript") or {}, cursor=0)}})
    for checkout in sorted({r["checkout"] for r in active(store)}):
        try:
            await daemon.ensure_lease(checkout)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            for record in list(active(store, checkout)):
                record["execution"]["lease_problem"] = code
                store.save(record)
            store.event("lead_hook/recovery_lease_refused", checkout, {"code": code})
    for record in list(active(store)):
        operation = store.get(record.get("operation"), required=False)
        try:
            if record["execution"].get("end"):
                await settle(daemon, record)
            elif operation and operation["state"] in ("prepared", "allowed", "running"):
                await daemon.ledger.quarantine(operation, "lead call interrupted by daemon replacement; awaiting host end")
                current = store.get(record["id"])
                current["state"] = "uncertain"
                store.save(current)
        except Exception as exc:
            # One unreadable target must not prevent this daemon (and therefore
            # its observer) from serving. Keep the end evidence and every hold;
            # observe() retries settlement through the same serialized path.
            current = store.get(record["id"])
            reason = "recovery settlement: " + getattr(exc, "code", type(exc).__name__)
            current["execution"]["problem"] = reason
            store.save(current)
            store.event("lead_hook/recovery_failed", current["id"], {"reason": reason})
