"""Exact lead file-tool intents, prepared before the host performs its edit.

Only literal replacement semantics are admitted. Fuzzy matching and notebook
serialization have no measured full-image adapter and receive a concrete refusal.
"""
import asyncio
import codecs
import time
from pathlib import Path

from .client import CoordinatorError
from .ledger import hidden, normalize, read_path
from .locks import KeyedLocks
from .observations import checkout_at
from .store import digest


FILES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# One lead Bash transition (Pre: reconcile, fingerprint, record; Post: fingerprint, reconcile) at a
# time per checkout, so no other lead command can start between a quiet check and its use.
BASH_TRANSITIONS = KeyedLocks()
# What was outside git's view when each lead Bash command began: {hook id: (deadline, entries)}.
HIDDEN = {}


def decode(raw):
    for bom, encoding in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(encoding), encoding, bom
    try:
        return raw.decode("utf-8"), "utf-8", b""
    except UnicodeError as exc:
        raise CoordinatorError("LEAD_ENCODING_UNSUPPORTED", "Exact lead editing requires UTF-8 or a BOM-identified UTF-16 file") from exc


def replace(text, edit):
    old, new = edit.get("old_string"), edit.get("new_string")
    if not isinstance(old, str) or not old or not isinstance(new, str) or old == new or type(edit.get("replace_all", False)) is not bool:
        raise CoordinatorError("LEAD_EDIT_INTENT", "Use nonempty literal old_string and a distinct new_string; a complete Write is available")
    count = text.count(old)
    if not count or not edit.get("replace_all", False) and count != 1:
        raise CoordinatorError("LEAD_EDIT_MATCH", "Exact replacement is missing or ambiguous; use unique literal text or full-file Write", {"occurrences": count})
    return text.replace(old, new) if edit.get("replace_all", False) else text.replace(old, new, 1)


def intended(tool, data, raw):
    text, encoding, bom = decode(raw)
    # The host normalizes text edits to the existing file's newline convention.
    crlf = "\r\n" in text and "\n" not in text.replace("\r\n", "")
    normalized = text.replace("\r\n", "\n") if crlf else text
    if tool == "Write":
        value = data.get("content")
        if not isinstance(value, str):
            raise CoordinatorError("LEAD_WRITE_INTENT", "Write content must be a string")
    elif tool == "Edit":
        value = replace(normalized, data)
    elif tool == "MultiEdit":
        edits = data.get("edits")
        if not isinstance(edits, list) or not edits:
            raise CoordinatorError("LEAD_MULTI_INTENT", "MultiEdit requires an ordered nonempty edits list")
        value = normalized
        for edit in edits:
            value = replace(value, edit)
    else:
        raise CoordinatorError("NOTEBOOK_ADAPTER_UNVERIFIED", "NotebookEdit serialization and native-generated cell IDs are not calibrated through this installed host. Use Write with the complete intended notebook JSON; no NotebookEdit receipt is granted.")
    if crlf:
        value = value.replace("\r\n", "\n").replace("\n", "\r\n")
    return bom + value.encode(encoding)


def exec_writing(root):
    """An exec write holds the tree's lock through its codex child, named in the lock's payload."""
    import re
    from .compat import _pid_alive, _write_lock_path
    try:
        text = _write_lock_path(root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if text.startswith("coordinator "):
        return False  # the coordinator's own lease: its native writers are the actors and custody above
    match = re.search(r"\bchild=(\d+) cstart=(\S+)", text)
    return bool(match) and _pid_alive(int(match.group(1)), "" if match.group(2) == "-" else match.group(2))


def bash_deadline(data, limits):
    """When Claude Code has ended this Bash call in the foreground: the call's own timeout, else the
    host's default, capped by the host's ceiling, which is the larger of its maximum and its default
    (120 000 and 600 000 ms unless BASH_DEFAULT_TIMEOUT_MS or BASH_MAX_TIMEOUT_MS, read by the hook
    from the host's environment, say otherwise), plus 60 s for the hooks themselves. Past it, a
    call whose Post never came no longer keeps other lead commands from being quiet. A command the
    host moves to the background has its Post already; what it writes later is an outside program's."""
    def ms(value, default):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return number if number > 0 else default
    limits = limits if isinstance(limits, dict) else {}
    default = ms(limits.get("BASH_DEFAULT_TIMEOUT_MS"), 120000)
    ceiling = max(ms(limits.get("BASH_MAX_TIMEOUT_MS"), 600000), default)
    return time.time() + min(ms(data.get("timeout"), default), ceiling) / 1000 + 60


# A lead file edit's own deadline: the daemon's 120 s pre/post gap for prepared edits, plus 60 s.
FILE_HOOK_S = 180


def others_running(daemon, checkout, ident):
    """Another lead call in this checkout that the daemon has received and that may still write
    outside any prepared operation, within its deadline: a Bash call not yet completed, or a file
    edit not yet prepared (a prepared one is a pending operation, which reconciliation skips).
    Synchronous: callers check it with nothing awaited before they act."""
    now = time.time()
    return any(other["id"] != ident and other.get("checkout") == checkout["id"] and now < other.get("deadline", 0)
               and (other.get("tool") == "Bash" and other.get("state") == "observed"
                    or other.get("tool") in FILES and other.get("state") == "received")
               for other in daemon.store.all("lead_hook"))


def quiet(daemon, checkout, actor, ident):
    """The synchronous part of `window`: the fingerprint, or None."""
    if others_running(daemon, checkout, ident):
        return None
    marks = []
    for other in daemon.store.all("actor"):
        if other["checkout"] != checkout["id"] or other["id"] == actor["id"]:
            continue
        if other.get("active_turn") or other.get("state") == "running":
            return None
        marks.append([other["id"], other["_revision"]])
    if daemon.custody.active(checkout["id"]):
        return None
    if any(o["checkout"] == checkout["id"] and o["state"] in ("prepared", "allowed", "running") for o in daemon.store.all("operation")):
        return None
    return digest(sorted(marks))


async def window(daemon, checkout, actor, ident):
    """A fingerprint of what besides this lead's command could change files here, or None while
    something could: another lead Bash command still running (in any session), a writer's turn, a
    pending patch or edit, command custody (a command or its held survivors), or an exec write
    holding the tree. A turn that starts and ends inside the window still rewrites its actor's
    record, so the fingerprint changes; a lead command that does so reconciles its own effects
    as UNKNOWN before this one ends, since its window was not quiet."""
    # The one await comes first: everything after it reads the store without yielding.
    if await asyncio.to_thread(exec_writing, checkout["root"]):
        return None
    return quiet(daemon, checkout, actor, ident)


async def dispatch(daemon, payload):
    tool, event = payload.get("tool_name"), payload.get("hook_event_name")
    if tool not in FILES | {"Bash"} or event not in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        raise CoordinatorError("LEAD_HOOK_FIELDS", "Unsupported lead hook event")
    data = payload.get("tool_input") or {}
    target = payload.get("cwd", "")
    if tool in FILES:
        value = data.get("notebook_path") if tool == "NotebookEdit" else data.get("file_path")
        if not isinstance(value, str) or not value:
            return {"coordinated": False}
        target = str((Path(target) / value).resolve())
    checkout, changed = checkout_at(daemon.store, target)
    if checkout is None:
        return {"coordinated": False}
    if changed:
        raise CoordinatorError("CHECKOUT_IDENTITY_CHANGED", "Registered checkout identity changed; reconcile before editing")
    session, call = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call, str) or not call:
        raise CoordinatorError("LEAD_HOOK_IDENTITY", "Host session_id and tool_use_id are required")
    ident = "lead_hook_" + digest([checkout["identity"], session, call])
    data = payload.get("tool_input") or {}
    signature = digest([tool, data])
    record = daemon.store.get(ident, required=False)
    if record and record["input_digest"] != signature:
        raise CoordinatorError("LEAD_HOOK_REWRITTEN", "Host input changed between hook events; prepared intent cannot be reused")
    fresh = event == "PreToolUse" and record is None
    if fresh:
        # Registered before anything that can wait (attaching a new session, the checkout's other
        # transitions, the ledger): a hook that gives up waiting (its client's 3 s) lets the command
        # or the edit go ahead, so a call the daemon has received counts as possibly writing from here.
        deadline = bash_deadline(data, payload.get("bash_limits")) if tool == "Bash" else time.time() + FILE_HOOK_S
        daemon.store.put("lead_hook", ident, {"tool": tool, "state": "observed" if tool == "Bash" else "received",
                         "input_digest": signature, "checkout": checkout["id"], "window": None, "deadline": deadline})
        record = None
        if tool == "Bash":
            # What exists outside git's view, listed next, before anything here can wait: the command may
            # already be running, and what it makes visible from there must not pass for its own. What
            # becomes visible before the reconcile below is settled there as not its doing.
            for key in [key for key, (until, _) in HIDDEN.items() if until < time.time()]:
                del HIDDEN[key]
            HIDDEN[ident] = (deadline, await asyncio.to_thread(hidden, checkout))
    from .lead import attach
    mapping = await attach(daemon, checkout["id"], session)
    actor = daemon.store.get(mapping["actor"])
    if tool == "Bash":
        async with BASH_TRANSITIONS.hold(checkout["id"]):
            if fresh:
                # What changed before this command is not its doing: settle that first, so only changes
                # made inside the window can become the lead's.
                await daemon.ledger.reconcile(checkout, reason="before lead Bash: " + ident)
                record = daemon.store.get(ident)
                record.update(unit=actor["unit"], actor=actor["id"], window=await window(daemon, checkout, actor, ident))
                daemon.store.save(record)
            if event != "PreToolUse" and record and record["state"] == "reconciled":
                # A second completion for the same call (Post and Failure, or a retried hook) names nobody:
                # its window closed with the first.
                result = await daemon.ledger.reconcile(checkout, reason="possible lead Bash effects: " + ident)
                return dict(result, attribution="UNKNOWN-context-only", executed_again=False, duplicate=True)
            if event != "PreToolUse":
                # A command that ran while nothing else could change files here is the lead's: what it
                # changed outside other units' claims is recorded as the lead's, not UNKNOWN. Otherwise
                # every `sed -i`, formatter or lockfile update by the lead left content that no commit
                # or native resume could take (measured, 2.3.0). Another unit's claimed paths stay UNKNOWN.
                attribute = None
                if record and record.get("window") is not None and record["window"] == await window(daemon, checkout, actor, ident):
                    attribute = {"attribution": "lead-command", "operation": ident, "unit": actor["unit"],
                                 "hidden": HIDDEN.get(ident, (None, None))[1],
                                 "excluded": [normalize(checkout, c["path"])["key"] for c in daemon.custody.claims(checkout["id"])
                                              if c["unit"] != actor["unit"]],
                                 # checked again after the scan: anything that started meanwhile voids it
                                 "still_quiet": lambda: quiet(daemon, checkout, actor, ident) == record["window"]}
                result = await daemon.ledger.reconcile(checkout, reason="possible lead Bash effects: " + ident, attribute=attribute)
                HIDDEN.pop(ident, None)
                if record:
                    record["state"] = "reconciled"
                    daemon.store.save(record)
                return dict(result, attribution="lead-command" if "attributed" in result else "UNKNOWN-context-only", executed_again=False)
            return {"coordinated": True, "observed": ident, "executed_again": False}
    path = normalize(checkout, target)["path"]
    if event == "PreToolUse":
        def refused():  # denied: it writes nothing
            daemon.store.put("lead_hook", ident, {"tool": tool, "state": "refused", "input_digest": signature,
                             "checkout": checkout["id"]})

        def refuse(conflicts):
            refused()
            raise CoordinatorError("CLAIM_CONFLICT", "Scope held by " + conflicts[0]["holder"] +
                                   "; send a message to that holder or the lead, then request lead-authorized handback",
                                   {"conflicts": conflicts})
        if record and record["state"] == "refused":
            # a replayed denial is decided again, never turned into an allowing warning
            conflicts = daemon.custody.conflicts(checkout["id"], actor["unit"], [normalize(checkout, path)])
            if conflicts:
                refuse(conflicts)
            record = None
            daemon.store.put("lead_hook", ident, {"tool": tool, "state": "received", "input_digest": signature,
                             "checkout": checkout["id"], "window": None, "deadline": time.time() + FILE_HOOK_S})
        if record:
            if record["state"] not in ("prepared", "verified") or daemon.store.get(record["operation"])["state"] not in ("prepared", "verified"):
                raise CoordinatorError("LEAD_HOOK_UNCERTAIN", "Prior intent is uncertain; do not replay this tool use")
            return {"coordinated": True, "operation": record["operation"], "duplicate": True}
        # Another unit's claim decides first: the hook allows an edit any later refusal is about
        # (an uncalibrated adapter, an encoding), so that refusal must never hide the claim.
        conflicts = daemon.custody.conflicts(checkout["id"], actor["unit"], [normalize(checkout, path)])
        if conflicts:
            refuse(conflicts)
        if tool == "NotebookEdit":
            intended(tool, data, b"")  # Concrete refusal before taking a claim.
        pre = await asyncio.to_thread(read_path, daemon.store, checkout, path)
        raw = daemon.store.read_blob(pre["blob"]) if pre["blob"] else b""
        content = intended(tool, data, raw)
        image = {"blob": daemon.store.blob(content), "mode": pre["mode"] or "100644"}
        try:
            await daemon.custody.acquire(actor, [path])
        except CoordinatorError as exc:
            if exc.code == "CLAIM_CONFLICT" and exc.details.get("conflicts"):
                refused()  # lost the claim to a concurrent edit: the hook denies it too
            raise
        operation = await daemon.ledger.prepare(actor, "lead-edit", {"expected": {path: image}, "host_tool": tool, "input_digest": signature}, [path],
                                                native={"session_id": session, "tool_use_id": call})
        if operation["pre"][path] != pre:
            await daemon.ledger.quarantine(operation, "pre-image changed during host intent preparation")
            raise CoordinatorError("LEAD_PREIMAGE_CHANGED", "Prepare the edit again against the current pre-image")
        daemon.store.put("lead_hook", ident, {"unit": actor["unit"], "actor": actor["id"], "tool": tool,
                         "state": "prepared", "input_digest": signature, "operation": operation["id"]}, actor["unit"])
        return {"coordinated": True, "operation": operation["id"]}
    if not record or record["state"] in ("received", "refused", "unrecorded"):
        # No prepared intent: its Pre was refused (an uncalibrated adapter, an encoding) or never
        # finished, and the host went ahead. It counts as possibly writing until this reconcile
        # has recorded its bytes; a failed reconcile leaves it counting until its deadline.
        await daemon.ledger.reconcile(checkout, [path], reason="lead post hook without prepared intent")
        if record and record["state"] != "unrecorded":
            record["state"] = "unrecorded"
            daemon.store.save(record)
        raise CoordinatorError("LEAD_PRE_MISSING", "No matching prepared host intent; matching bytes cannot acquire authorship")
    operation = daemon.store.get(record["operation"])
    if record["state"] == "verified":
        return {"operation": operation["id"], "duplicate": True, "state": "verified"}
    if record["state"] != "prepared" or operation["state"] != "prepared":
        raise CoordinatorError("LEAD_HOOK_UNCERTAIN", "Missing/failed completion cannot be repaired by matching bytes")
    response = payload.get("tool_response")
    failed = event == "PostToolUseFailure" or isinstance(response, dict) and response.get("isError")
    if failed:
        record["state"] = "failed"
        daemon.store.save(record)
        await daemon.ledger.quarantine(operation, "host file tool failed; partial effects receive no receipt")
        return {"operation": operation["id"], "state": "uncertain"}
    try:
        result = await daemon.ledger.verify(operation, operation["intended"]["expected"], "exact",
                                            {"executor": "Claude file tool", "session_id": session, "tool_use_id": call, "post_event": event})
        record["state"] = "verified"
        daemon.store.save(record)
        return {"operation": result["id"], "state": "verified"}
    except BaseException as exc:
        record["state"] = "uncertain"
        daemon.store.save(record)
        await daemon.ledger.quarantine(operation, getattr(exc, "code", type(exc).__name__))
        raise
