"""Exact lead file-tool intents, prepared before the host performs its edit.

Only literal replacement semantics are admitted. Fuzzy matching and notebook
serialization have no measured full-image adapter and receive a concrete refusal.
"""
import asyncio
import codecs
import hashlib
import os
import time

from .locks import KeyedLocks, finish_all, finish_thread
from .client import CoordinatorError
from .ledger import hidden, normalize
from .observations import checkout_at
from .store import digest, uid


FILES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# One lead Bash transition (Pre: reconcile, fingerprint, record; Post: fingerprint, reconcile) at a
# time globally, so no other lead command can start between a quiet check and its use.
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


def exec_window(root):
    """Live occupancy and retained evidence of a completed exec writer.

    The lock file survives release and names the run's child. Its digest must
    remain in the window after that child exits; a live-only check loses it.
    """
    import re
    from .compat import _pid_alive, _write_lock_path
    tree = None
    if isinstance(root, dict):
        # The lock is keyed by the tree as it is now, the identity an exec writer derives for it
        # (compat._tree_identity): a deleted checkout has no tree to hold, a re-created one its own lock.
        root = root["root"]
        try:
            work = os.stat(root)
        except (FileNotFoundError, NotADirectoryError):
            return False, None
        except OSError:
            return True, None
        tree = f"{work.st_dev}-{work.st_ino}"
    try:
        with _write_lock_path(root, tree).open("rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())
    except FileNotFoundError:
        return False, None
    except OSError:
        return True, None
    def identity(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    if identity(before) != identity(after):
        return True, None
    stamp = [identity(after), hashlib.sha256(raw).hexdigest()]
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("coordinator "):
        return False, stamp  # native writers are covered by the durable clock and occupancy
    match = re.search(r"\bchild=(\d+) cstart=(\S+)", text)
    busy = bool(match) and _pid_alive(int(match.group(1)), "" if match.group(2) == "-" else match.group(2))
    return busy, stamp


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


def quiet(daemon, checkout, mine, ident):
    """A global, generation-fenced stamp, without rereading finished history.

    `mine` are this session's lead actors. Only this hook's own record updates
    are subtracted from the durable clock; its operations still invalidate it.
    """
    if SETTLING or not getattr(daemon, "ready", True) or getattr(daemon, "recovering", None) or foreign_observation(daemon, ident):
        # An observation not this call's own may record what a credited command writes as UNKNOWN: an uncredited
        # completion's (settle_uncredited), recovery's snapshot, a failed observation's repair (scans.Manager.repairs).
        return None
    clock = daemon.store.window_state(ident, mine, time.time())
    if clock is None:
        return None
    marks = []
    for registered in daemon.store.all("checkout"):
        busy, stamp = exec_window(registered)
        if busy:
            return None
        marks.append([registered["id"], stamp])
    # Observations started by anyone but this call: one that starts after the window and commits before its Post
    # leaves no pending row and moves no durable clock, yet may have recorded the command's writes as UNKNOWN.
    scans = daemon.ledger.scans
    epoch = scans.started - scans.started_by.get(ident, 0)
    return digest([daemon.store.generation, clock, marks, epoch])


def foreign_observation(daemon, ident):
    """Whether an observation other than this call's own credited one is pending or running (partial index)."""
    for (operation,) in daemon.store.db.execute(
            "SELECT json_extract(data,'$.owner.operation') FROM records INDEXED BY observation_pending "
            "WHERE kind='observation' AND json_extract(data,'$.state')='pending'"):
        if operation != ident:
            return True
    return False


async def window(daemon, checkout, mine, ident):
    """A fingerprint of what besides this lead's command could change files here, or None while
    something could: another lead Bash command still running (in any session), a writer's turn, a
    pending patch or edit, command custody (a command or its held survivors), or an exec write
    holding the tree. Completed operations, released custody, writer turns and
    other lead calls advance durable counters; completed exec writers leave a
    changed lock-file stamp. None disappears when it leaves the active set."""
    # The one await comes first: everything after it reads the store without yielding.
    if any(busy for busy, _ in await finish_all(*(finish_thread(exec_window, c)
                                                    for c in daemon.store.all("checkout")))):
        return None
    return quiet(daemon, checkout, mine, ident)


async def arm(daemon, params):
    """The hook got the answer to a lead Bash call's Pre before the host ran the command: only then
    is what Pre recorded a state from before the command, so only an armed window names the lead.
    Losing the initial Pre reply means no confirmation and no attribution. Losing
    this confirmation's reply may leave a persisted arm; a same-token retry
    acknowledges that decision without reopening or extending its window."""
    ident, token = params.get("observed"), params.get("arm")
    record = daemon.store.get(ident, required=False) if isinstance(ident, str) and ident.startswith("lead_hook_") else None
    if (not record or record.get("tool") != "Bash" or not token
            or record.get("generation") != daemon.store.generation or record.get("arm") != token
            or record.get("window_invalidated_generation")):
        return {"armed": False}
    if record.get("armed"):
        return {"armed": True}
    if (record.get("state") != "observed" or time.time() > record.get("arm_deadline", float("inf"))
            or record.get("window") is None
            or quiet(daemon, None, set(record.get("session_actors") or []), ident) != record["window"]):
        return {"armed": False}
    record["armed"] = True
    daemon.store.save(record)
    return {"armed": True}


def invalidate_windows(daemon):
    """Fence every outstanding Bash call before recovery can admit hooks. A call past its deadline whose completion
    never arrived is settled here: nothing can credit it any more, and the next reconcile of its checkouts (recovery's
    snapshot, which runs in the background once the daemon serves, or any later one) records the bytes it left as
    UNKNOWN; what it changed and changed back before then is not recovered, as it was not by 2.4.40, whose follow-up
    for it never ran. A completion that arrives later still observes (settle_uncredited), and one whose observation
    was owed when the daemon stopped stays owed (retry_owed). Through 2.4.40 these calls stayed observed for good,
    and this read decoded every lead_hook record (73,000 on the live ledger, 29,476 of them observed Bash calls,
    11-19); it now reads the observed ones through their index."""
    from .store import WINDOW_ACTIVE
    now = time.time()
    with daemon.store.transaction():
        for record in list(daemon.store.selected("SELECT * FROM records INDEXED BY window_lead_hook WHERE " +
                                                 WINDOW_ACTIVE["lead_hook"] + " ORDER BY id")):
            if record.get("tool") != "Bash" or record.get("state") != "observed":
                continue
            record.setdefault("armed_before_recovery", bool(record.get("armed")))
            record.update(armed=False, arm=None, window=None, arrival=None,
                          window_invalidated_generation=daemon.store.generation)
            if record.get("deadline", 0) < now and not record.get("completion"):
                record.update(state="reconciled", attribution="UNKNOWN-context-only",
                              settlement={"source": RECOVERED, "scan": "recovery snapshot", "at": now})
            daemon.store.save(record)
    # The calls remain observed until Post/deadline: an old command may still
    # write, but cannot attribute bytes using a lost pre-restart hidden listing.
    HIDDEN.clear()


async def dispatch(daemon, payload):
    tool, event = payload.get("tool_name"), payload.get("hook_event_name")
    if tool in FILES:
        from .lead_calls import dispatch as file_call
        return await file_call(daemon, payload)
    if tool != "Bash" or event not in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch"):
        raise CoordinatorError("LEAD_HOOK_FIELDS", "Unsupported lead hook event")
    data = payload.get("tool_input") or {}
    target = payload.get("cwd", "")
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
    batch = event == "PostToolBatch"
    if batch:
        # The host resolved the call and no Post hook settled it: another hook or a permission refused it, the user
        # rejected it, or its Post was lost (Claude Code sends neither Post event for a refused call). Its record is
        # settled now instead of holding every lead's quiet window and a stop until its deadline: nothing shows that a
        # command ran, so its window credits it with nothing, and an edit is decided by its bytes while its claim still
        # holds the path. It is found by the host's call id when the session's folder moved since its Pre.
        if record is None:
            record = next(daemon.store.selected("SELECT * FROM records WHERE kind='lead_hook' AND json_extract(data,"
                                                "'$.tool_use_id')=? AND json_extract(data,'$.session_id')=? ORDER BY id",
                                                (call, session)), None)
            if record:
                ident, checkout = record["id"], daemon.store.get(record["checkout"])
        # A call with no record (its Pre failed before recording it, its Post was lost) is observed like any other
        # uncredited completion: the batch may be the only evidence that the command ran.
        if record and (record["state"] not in ("observed", "received", "prepared")
                       and (record.get("settlement") or {}).get("source") != RECOVERED):
            return {"coordinated": True, "settled": True, "state": record["state"]}
        event = "PostToolUse"
    from .lead import reach as reached
    reach = [daemon.store.get(key) for key in sorted(reached(daemon.store, checkout["id"]))]
    if record and record["input_digest"] != signature:
        raise CoordinatorError("LEAD_HOOK_REWRITTEN", "Host input changed between hook events; prepared intent cannot be reused")
    fresh = event == "PreToolUse" and record is None
    if fresh:
        # Registered before anything that can wait (attaching a new session, the checkout's other
        # transitions, the ledger): a hook that gives up waiting (its client's 3 s) lets the command
        # go ahead, so a call the daemon has received counts as possibly writing from here.
        deadline = bash_deadline(data, payload.get("bash_limits"))
        from .lead import session_actors
        fields = {"tool": tool, "state": "observed", "input_digest": signature,
                  "generation": daemon.store.generation, "session_id": session, "tool_use_id": call,
                  "checkout": checkout["id"], "reach": [c["id"] for c in reach], "window": None, "deadline": deadline, "arm_deadline": time.time() + 3,
                  "session_actors": session_actors(daemon.store, checkout["id"], session)}
        if tool == "Bash":
            fields["shell_command_digest"] = digest(data.get("command", ""))
            from .client import stop_control_command
            fields["stop_control"] = stop_control_command(data.get("command"))
            # Quiet as the call arrives? The window taken at the end of Pre must equal this, so a call
            # that started and ended in between (another lead's, a writer's turn) leaves none.
            fields["arrival"] = quiet(daemon, checkout, set(fields["session_actors"]), ident)
        daemon.store.put("lead_hook", ident, fields)
        record = None
        if fields.get("arrival") is not None:
            # What exists outside git's view, listed next, before anything here can wait: the command may
            # already be running, and what it makes visible from there must not pass for its own. What
            # becomes visible before the reconcile below is settled there as not its doing.
            for key in [key for key, (until, _) in HIDDEN.items() if until < time.time()]:
                del HIDDEN[key]
            listings = await finish_all(*(finish_thread(hidden, c) for c in reach))
            HIDDEN[ident] = (deadline, dict(zip([c["id"] for c in reach], listings)))
    if event != "PreToolUse" and not creditable(daemon, daemon.store.get(ident, required=False), batch):
        # Decided before attaching the session: settling it needs no actor, and a new session's attach creates its
        # run and unit, which waits behind every other.
        return await settle_uncredited(daemon, ident, reach, batch, checkout, payload, signature)
    from .lead import attach
    mapping = await attach(daemon, checkout["id"], session, cwd=payload.get("cwd"), hook=True)
    actor = daemon.store.get(mapping["actor"])
    if event == "PreToolUse":
        current = daemon.store.get(ident, required=False)
        if not fresh or current is None or current.get("arrival") is None:
            # A call that arrives while anything else could change files can never be credited: its window must equal
            # its arrival, and `quiet` gave it none. Only bookkeeping serves it, so it neither waits for the global lock
            # nor scans. Through 2.4.40 every such call listed hidden files, queued for that lock and scanned every
            # checkout it reaches; with ~25 sessions running Bash the queue outgrew it from 17 Nov 21:47 IST, and no
            # later completion settled (VERIFY 2.4.41). A repeated Pre only answers again.
            if fresh and current is not None:
                receipt_units = await units_of(daemon, actor, session, reach)
                current = daemon.store.get(ident)
                current.update(unit=actor["unit"], actor=actor["id"],
                               session_actors=sorted(set(current.get("session_actors") or []) | {actor["id"]}),
                               receipt_units=receipt_units, published_participants=published(daemon, receipt_units),
                               window=None, arm=None)
                daemon.store.save(current)
            return pre_answer(daemon, ident, current, fresh)
    async with BASH_TRANSITIONS.hold("global"):
        if fresh:
            receipt_units = await units_of(daemon, actor, session, reach)
            # What changed before this command is not its doing: settle that first, so only changes
            # made inside the window can become the lead's.
            await daemon.ledger.reconcile_many(reach, reason="before lead Bash: " + ident, target=checkout["id"],
                                               owner={"kind": "operation", "operation": ident})
            record = daemon.store.get(ident)
            mine = set(record.get("session_actors") or []) | {actor["id"]}
            now = await window(daemon, checkout, mine, ident)
            held = now is not None and now == record.get("arrival")
            # The window counts only once the hook has the answer before the command runs (`arm`).
            record = daemon.store.get(ident)
            record.update(unit=actor["unit"], actor=actor["id"], session_actors=sorted(mine),
                          receipt_units=receipt_units, published_participants=published(daemon, receipt_units),
                          window=now if held else None, arm=uid("arm") if held else None)
            daemon.store.save(record)
            return pre_answer(daemon, ident, record, fresh)
        record = daemon.store.get(ident, required=False)
        mine = set((record or {}).get("session_actors") or []) | {actor["id"]}
        # A window that changed while this waited (its deadline passed, another completion settled it, anything
        # started) credits nothing: that completion is settled the durable way, after the lock (settle_uncredited).
        if creditable(daemon, record, batch) and record["window"] == await window(daemon, checkout, mine, ident):
            # A command that ran while nothing else could change files here is the lead's: what it
            # changed outside other units' claims is recorded as the lead's, not UNKNOWN. Otherwise
            # every `sed -i`, formatter or lockfile update by the lead left content that no commit
            # or native resume could take (measured, 2.3.0). Another unit's claimed paths stay UNKNOWN.
            attribute = {"attribution": "lead-command", "operation": ident, "unit": actor["unit"],
                         "checkouts": {c["id"]: {
                             "unit": record.get("receipt_units", {}).get(c["id"], actor["unit"]),
                             "hidden": (HIDDEN.get(ident, (None, {}))[1] or {}).get(c["id"]),
                             "excluded": [normalize(c, claim["path"])["key"] for claim in daemon.custody.claims(c["id"])
                                 if claim["unit"] != record.get("receipt_units", {}).get(c["id"], actor["unit"])]}
                             for c in reach if c["id"] in record.get("receipt_units", {})
                             and c["id"] in (HIDDEN.get(ident, (None, {}))[1] or {})},
                         # checked again after the scan: anything that started meanwhile voids it
                         "still_quiet": lambda: quiet(daemon, checkout, mine, ident) == record["window"]}
            try:
                result = await daemon.ledger.reconcile_many(reach, reason="possible lead Bash effects: " + ident,
                                                            target=checkout["id"], attribute=attribute)
            except Exception as exc:
                # Owed the durable way from here: the credit is lost, the observation is not (retry_owed).
                current = daemon.store.get(ident, required=False)
                if current and current["state"] == "observed":
                    current.update(completion={"source": "PostToolBatch" if batch else "Post", "at": time.time()},
                                   observing={"attempt": 1, "at": time.time(), "retry_at": time.time() + OWED_RETRY_SECONDS,
                                              "failure": getattr(exc, "code", type(exc).__name__)})
                    daemon.store.save(current)
                raise
            HIDDEN.pop(ident, None)
            daemon.ledger.scans.started_by.pop(ident, None)
            current = daemon.store.get(ident, required=False)
            if current:
                current["state"] = "reconciled"
                daemon.store.save(current)
            return dict(result, attribution="lead-command" if "attributed" in result else "UNKNOWN-context-only", executed_again=False)
    return await settle_uncredited(daemon, ident, reach, batch, checkout, payload, signature)


def creditable(daemon, record, batch):
    """Whether this Bash completion can still credit what its command changed to the lead: a delivered Post (a
    PostToolBatch is no evidence that the command ran) for a call still observed, armed by this daemon generation
    with a window, before the call's deadline. The window itself is compared under the global lock."""
    return bool(not batch and record and record.get("state") == "observed" and record.get("window") is not None
                and record.get("armed") and record.get("generation") == daemon.store.generation
                and time.time() <= record.get("deadline", 0))


async def units_of(daemon, actor, session, reach):
    """The unit that receives this session's records in each reached checkout."""
    from .lead import attach
    from .participants import contains
    units = {}
    for participant in reach:
        if contains(daemon.store, actor["unit"], participant["id"]):
            units[participant["id"]] = actor["unit"]
        else:
            # Reach includes older revisions too. Keep their receipts in a unit that owns that
            # checkout, without repinning the current unit or widening its publication membership.
            sibling = await attach(daemon, participant["id"], session, cwd=participant["root"], hook=True)
            units[participant["id"]] = sibling["unit"]
    return units


def published(daemon, receipt_units):
    from .participants import published_participant
    return [found for key, unitid in receipt_units.items() if (found := published_participant(daemon.store, unitid, key))]


def pre_answer(daemon, ident, record, fresh):
    answer = {"coordinated": True, "observed": ident, "executed_again": False}
    from .lead_calls import blockers
    waiting = blockers(daemon.store)
    if waiting:
        answer["quiet_window_blocked_by"] = waiting
    if record and record.get("published_participants"):
        answer["published_participants"] = record["published_participants"]
    if fresh and record and record.get("arm"):
        answer["arm"] = record["arm"]
    return answer


# The completions whose shared observation is in flight, by call: `quiet` gives no window while any is.
SETTLING = {}
SETTLED_REASON = "possible lead Bash effects"
RECOVERED = "recovery: deadline passed"
# A live Post waits this long for its observation, then answers `settling` while the observation goes on; the call is
# settled when it commits. It leaves room in the hook's 3 s RPC (coordinator_lead.CALL_SECONDS) for the lease ping and
# the host lock before it, and for the event loop's own stalls and queue: with 2.5 s, 48 of 120 Posts on an
# 8,000-file checkout passed 3 s, and with 1.5 s, 20 of 120 on 300 files under 30 concurrent sessions (VERIFY 2.4.41).
POST_WAIT_SECONDS = 1.0
# A failed observation is owed again after this long, doubling to OWED_RETRY_MAX_SECONDS.
OWED_RETRY_SECONDS, OWED_RETRY_MAX_SECONDS = 5, 300


async def settle_uncredited(daemon, ident, reach, batch, checkout, payload, signature):
    """Settle a completion that cannot credit anything, without the global lock. What its command changed is recorded
    UNKNOWN-context-only by an observation of every checkout it reaches that begins after the completion arrived, and
    the call is marked reconciled only once that observation commits. The completion is written on the call first
    (`completion`), so the observation is owed durably: a failure, a stop or a reply the hook took as final leaves it
    for retry_owed. Concurrent completions share each scan (scans.Producer) and one commit (scans.compatible), so a
    thousand of them cost one scan per checkout, not one each. While any runs no window holds (`quiet`), and each
    attempt moves the durable clock before it scans, so a window armed earlier cannot pass still_quiet either: its
    scan never takes a credited command's changes. A live Post waits up to POST_WAIT_SECONDS for the commit, and a
    PostToolBatch item for the attempt in flight; either may then answer `settling` while retry_owed, which owns the
    call's observation, goes on. A completion for a call another completion settled scans nothing; one for a call
    recovery settled by its deadline observes, since the command may have run after it (a late permission)."""
    current = daemon.store.get(ident, required=False)
    answer = {"coordinated": True, "observed": ident, "attribution": "UNKNOWN-context-only", "executed_again": False}
    if current and current["state"] == "reconciled" and (current.get("settlement") or {}).get("source") != RECOVERED:
        return dict(answer, settled=True, duplicate=True)
    completion = {"source": "PostToolBatch" if batch else "Post", "at": time.time()}
    if current is None:
        # A completion whose Pre never arrived: recorded whole first, so a repeated completion finds it and joins.
        current = daemon.store.put("lead_hook", ident, {
            "tool": "Bash", "state": "observed", "input_digest": signature, "generation": daemon.store.generation,
            "session_id": payload.get("session_id"), "tool_use_id": payload.get("tool_use_id"), "checkout": checkout["id"],
            "reach": [c["id"] for c in reach], "window": None, "deadline": time.time(), "arm_deadline": time.time(),
            "session_actors": [], "completion": completion})
    elif not current.get("completion") or current["state"] == "reconciled":
        current.update(state="observed", completion=completion)
        daemon.store.save(current)
    task = owe(daemon, ident, reach, current)
    try:
        if task is not None:
            # A PostToolBatch item waits for the attempt in flight; a live Post up to POST_WAIT_SECONDS.
            await (asyncio.shield(task) if batch else asyncio.wait_for(asyncio.shield(task), POST_WAIT_SECONDS))
    except Exception:
        pass  # a failed or slower observation stays owed on the call (retry_owed); this delivery is done
    settled = daemon.store.get(ident)["state"] == "reconciled"
    return dict(answer, settled=settled, **({} if settled else {"settling": True}))


def owe(daemon, ident, reach, record):
    """The call's observation in flight, started now unless one already runs; None while a failed one backs off."""
    task = SETTLING.get(ident)
    if task is not None and not task.done():
        return task
    if (record.get("observing") or {}).get("retry_at", 0) > time.time():
        return None
    task = SETTLING[ident] = daemon.background(observe_uncredited(daemon, ident, reach, record.get("completion") or {}))
    return task


async def observe_uncredited(daemon, ident, reach, completion):
    try:
        current = daemon.store.get(ident, required=False)
        if current is None or current["state"] != "observed":
            return
        # Moves the durable clock (store.window_state) before anything is scanned, with no await between.
        attempt = (current.get("observing") or {}).get("attempt", 0) + 1
        current["observing"] = {"attempt": attempt, "at": time.time()}
        daemon.store.save(current)
        try:
            await finish_all(*(daemon.ledger.reconcile(checkout, reason=SETTLED_REASON) for checkout in reach))
        except Exception as exc:
            # Owed again after a backoff (retry_owed): a busy checkout's git view can keep moving (UNSTABLE_SNAPSHOT,
            # 706 of 3,000 completions in an 8,000-file run with live writes, VERIFY 2.4.41).
            code = getattr(exc, "code", type(exc).__name__)
            current = daemon.store.get(ident, required=False)
            if current and current["state"] == "observed":
                delay = min(OWED_RETRY_SECONDS * 2 ** (attempt - 1), OWED_RETRY_MAX_SECONDS)
                current["observing"] = {"attempt": attempt, "at": time.time(), "retry_at": time.time() + delay, "failure": code}
                daemon.store.save(current)
                daemon.store.event("lead_bash/observation_failed", ident, {"code": code, "attempt": attempt, "retry_seconds": delay})
            return
        current = daemon.store.get(ident, required=False)
        if current and current["state"] == "observed":
            current.update(state="reconciled", attribution="UNKNOWN-context-only",
                           settlement=dict(completion, scan="observed", at=time.time()))
            current.pop("observing", None)
            daemon.store.save(current)
        HIDDEN.pop(ident, None)
        daemon.ledger.scans.started_by.pop(ident, None)
    finally:
        if SETTLING.get(ident) is asyncio.current_task():
            del SETTLING[ident]


OWED_CURSOR = {"after": ""}
# The owed observations retry_owed started that are still running, as tasks (a replacement for the same call is
# another task, so a finished one's callback never drops it): its reserve is concurrent places, not per pass.
OWED_ACTIVE = set()


def retry_owed(daemon, limit):
    """Start at most `limit` of the observations owed to completions that arrived but did not settle (a failed
    attempt once its retry is due, one a stop interrupted), resuming where the last pass stopped. They are the
    observed Bash calls that carry a completion, read by index."""
    from .lead import reach as reached
    from .store import WINDOW_ACTIVE
    now, started = time.time(), 0
    # A call's own-observation count matters only while it can still be credited (quiet): forget the rest.
    started_by = daemon.ledger.scans.started_by
    for operation in list(started_by):
        record = daemon.store.get(operation, required=False)
        if not record or record.get("state") != "observed" or record.get("deadline", 0) < now:
            del started_by[operation]
    if limit <= 0:
        return
    rows = list(daemon.store.selected("SELECT * FROM records INDEXED BY window_lead_hook WHERE " + WINDOW_ACTIVE["lead_hook"] +
                                      " AND json_extract(data,'$.completion') IS NOT NULL AND id>? ORDER BY id LIMIT ?",
                                      (OWED_CURSOR["after"], limit * 4)))
    # The next pass resumes after the last row read here, or from the start once a pass reads to the end.
    OWED_CURSOR["after"] = rows[-1]["id"] if len(rows) == limit * 4 else ""
    for index, record in enumerate(rows):
        if started >= limit:
            OWED_CURSOR["after"] = rows[index - 1]["id"]
            break
        if (record.get("tool") != "Bash" or record.get("state") != "observed" or record["id"] in SETTLING
                or (record.get("observing") or {}).get("retry_at", 0) > now):
            continue
        checkout = daemon.store.get(record["checkout"], required=False)
        if checkout is None:
            continue
        task = owe(daemon, record["id"], [daemon.store.get(key) for key in sorted(reached(daemon.store, checkout["id"]))], record)
        if task:
            OWED_ACTIVE.add(task)
            task.add_done_callback(OWED_ACTIVE.discard)
            started += 1
