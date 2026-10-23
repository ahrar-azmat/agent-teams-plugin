"""Incremental rollout diagnostics and workflow policy readback. Native item cursors
provide authoritative input/turn evidence; this reader preserves the supplementary
damage, unmatched text and turn-context policy evidence those APIs do not expose.
An app-server's rollout is final only after it exits: read before, a later record may be missing.

Codex 0.163.0 writes a turn's records in order, each written and flushed to the OS before it goes on, so an exit,
even a kill, keeps them (rollout/src/recorder.rs). A turn starts with `task_started`; the input it takes is then
recorded, a user `response_item` followed by the `item_completed` UserMessage carrying the input's client id (in a
legacy history, the `user_message` event carrying it), before the model sees it; input steered into a running turn is
recorded the same way when the turn takes it, or at the turn's finish, and an abort drops it; a turn ends with
`task_complete`, or `turn_aborted` when interrupted, and one the process ended has neither (session/turn.rs
record_user_prompt_and_emit_turn_item, tasks/mod.rs on_task_finished and abort_all_tasks, rollout/src/policy.rs).

A record native failed to write is not there, though its turn went on: native logs the failure and keeps the record
in memory, to write it again at its next write (session/mod.rs persist_rollout_items, recorder.rs
write_pending_with_recovery). A write cut short leaves part of a record, and native ends that line when it opens the
file again (recorder.rs ensure_rollout_is_newline_terminated). The log line telling of a failure can be lost the same
way, as native's log writer drops what it cannot write. So a rollout's silence about an input proves nothing: only
its client id's record shows where it went, and what else the rollout and the log show (damage, a text, native_mark)
is told to the lead, never taken as proof.
"""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import re

from .store import digest, uid
from .locks import KeyedLocks


STARTED, ENDED = ("task_started", "turn_started"), {"task_complete": "completed", "turn_complete": "completed",
                                                    "turn_aborted": "interrupted"}
# A record carries the time native wrote it, to the millisecond (recorder.rs write_rollout_item); the coordinator
# reads its clock before it sends an input, so each of that input's records is later, but for this margin.
MARGIN_S = 1.0
# The targets of a writer's log (native.WRITER_LOG) that tell of its rollout's writing.
SESSION, WRITER = "codex_core::session", "codex_rollout"


def opened(path, thread):
    """The thread's own rollout as a binary stream, or (None, reason). A cold rollout (unmodified for 7 days) is
    replaced by its compressed sibling `<path>.zst` (rollout/src/compression.rs)."""
    if not path or not thread:
        return None, "native thread did not supply a rollout path"
    path = str(path)
    path = path[len("file://"):] if path.startswith("file://") else path
    if not Path(path).name.endswith("-" + thread + ".jsonl"):
        return None, "rollout path does not name this thread"
    for candidate, compressed in ((path, False), (path + ".zst", True)):
        try:
            descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, type(exc).__name__
        stream = os.fdopen(descriptor, "rb")
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            stream.close()
            return None, "rollout is not an owned regular file"
        if not compressed:
            return stream, None
        try:
            from compression import zstd  # Python 3.14+: the daemon's own interpreter
        except ImportError:
            stream.close()
            return None, "the rollout is compressed and this Python cannot read zstd"
        return zstd.ZstdFile(stream), None
    return None, "rollout file is missing"


def moment(text):
    """A time native wrote (RFC 3339, UTC) in seconds since the epoch, or None."""
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def user_texts(payload):
    return [c.get("text", "") for c in payload.get("content") or [] if isinstance(c, dict) and c.get("type") == "input_text"]


def file_identity(path):
    for name, compressed in ((str(path), False), (str(path) + ".zst", True)):
        try:
            info = Path(name).stat()
            return {"path": name, "file": [info.st_dev, info.st_ino], "size": info.st_size,
                    "modified": info.st_mtime_ns, "compressed": compressed}
        except FileNotFoundError:
            continue


def batch(stream):
    """One bounded diagnostic batch. A complete record, however large, is never truncated."""
    result, size = [], 0
    while len(result) < 128 and size < 1024 * 1024:
        offset = stream.tell()
        line = stream.readline()
        if not line:
            return result, True
        size += len(line)
        whole = line.endswith(b"\n")
        try:
            record = json.loads(line) if line.strip() and whole else None
        except ValueError:
            record = None
        result.append((offset, stream.tell(), whole, bool(line.strip()), record))
        if not whole:
            return result, True
    return result, False


async def diagnose(store, actor, path, thread, wanted):
    if not hasattr(store, "rollout_locks"):
        store.rollout_locks = KeyedLocks()
    async with store.rollout_locks.hold(actor["id"]):
        return await _diagnose(store, actor, path, thread, wanted)


async def _diagnose(store, actor, path, thread, wanted):
    """Incremental *diagnostics*, supplementary to native's authoritative item cursors.

    Store complete-record offsets and small, hashed user-text facts, never tool payloads
    or a second copy of native history. New input lookups reuse those facts. Unchanged
    compressed files are not opened or decompressed again. A changed compressed file
    is a replacement and needs a new bootstrap; plain files seek directly to the
    complete-record boundary, including retrying a formerly incomplete final line.
    """
    for _, _, needle, _ in wanted.values():
        if needle is not None and (not isinstance(needle, str) or not needle.startswith("C1_MESSAGE ")):
            raise ValueError("Unsupported rollout diagnostic needle; expected C1_MESSAGE <id>")
    key = "rollout_diagnostic:" + actor["id"]
    state = store.get(key, required=False) or {}
    identity = await asyncio.to_thread(file_identity, path) if path else None
    if identity is None:
        return {"readable": False, "reason": "rollout file is missing"}
    prior = state.get("identity", {})
    unchanged = state.get("version") == 2 and identity == prior
    same = (state.get("version") == 2 and identity.get("file") == prior.get("file") and identity.get("path") == prior.get("path")
            and identity["size"] >= prior.get("size", 0)
            and (not identity["compressed"] or unchanged)
            and (identity["size"] != prior.get("size") or unchanged))
    if not same:
        state = {"version": 2, "epoch": uid("diagnostic"), "offset": 0, "number": 0, "current": None, "damage": None}
    if not unchanged or not state.get("complete"):
        state.update(identity=identity, complete=False)
        store.put("rollout_diagnostic", key, {k: v for k, v in state.items() if not k.startswith("_") and k != "id"}, actor["id"])
        stream, reason = await asyncio.to_thread(opened, path, thread)
        if stream is None:
            return {"readable": False, "reason": reason}
        try:
            if state["offset"]:
                await asyncio.to_thread(stream.seek, state["offset"])
            finished = False
            while not finished:
                lines, finished = await asyncio.to_thread(batch, stream)
                for start, end, whole, nonempty, record in lines:
                    if not whole:
                        state.update(offset=start, damage={"reason": "ends with a record cut short", "mended": None})
                        break
                    state["offset"], state["number"] = end, state["number"] + 1
                    if not nonempty:
                        continue
                    if not isinstance(record, dict):
                        state["damage"] = {"reason": "holds a record that is not whole (record %d)" % state["number"],
                                           "mended": None}
                        continue
                    if state["damage"] and state["damage"]["mended"] is None:
                        state["damage"]["mended"] = moment(record.get("timestamp"))
                    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                    outer, kind = record.get("type"), payload.get("type")
                    turn = (payload.get("turn_id") or (payload.get("turn_attribution") or {}).get("turn_id")
                            or (payload.get("internal_chat_message_metadata_passthrough") or {}).get("turn_id"))
                    if outer == "turn_context":
                        sandbox = payload.get("sandbox_policy", {})
                        store.put("rollout_policy", key + ":" + state["epoch"] + ":" + str(state["number"]),
                                  {"epoch": state["epoch"], "number": state["number"], "turn": payload.get("turn_id"),
                                   "model": payload.get("model"), "effort": payload.get("effort"),
                                   "approval_policy": payload.get("approval_policy"),
                                   "sandbox": sandbox.get("type") if isinstance(sandbox, dict) else sandbox}, key)
                    elif outer == "event_msg" and kind in STARTED:
                        state["current"] = turn or state["current"]
                    elif outer == "event_msg" and kind in ("item_completed", "user_message"):
                        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                        if kind == "item_completed" and item.get("type") != "UserMessage":
                            continue
                        owner = (turn if kind == "item_completed" else None) or state["current"]
                        store.put("rollout_paired", key + ":" + state["epoch"] + ":" + digest(owner),
                                  {"number": state["number"]}, key)
                    elif outer == "response_item" and kind == "message" and payload.get("role") == "user":
                        said = user_texts(payload)
                        store.put("rollout_text", key + ":" + state["epoch"] + ":" + str(state["number"]),
                                  {"epoch": state["epoch"], "number": state["number"], "turn": turn or state["current"],
                                   "time": moment(record.get("timestamp")), "texts": [digest(t) for t in said],
                                   "messages": re.findall(r"C1_MESSAGE ([A-Za-z0-9_:-]+)", "\n".join(said))}, key)
                    await asyncio.sleep(0)
                # The parser state and the diagnostic facts are durable before moving
                # the offset. A crash earlier replays the same deterministic fact ids.
                store.put("rollout_diagnostic", key, {k: v for k, v in state.items() if not k.startswith("_") and k != "id"},
                          actor["id"])
                await asyncio.sleep(0)
        except OSError as exc:
            return {"readable": False, "reason": type(exc).__name__}
        finally:
            await asyncio.to_thread(stream.close)
        state.update(identity=identity, complete=True)
        store.put("rollout_diagnostic", key, {k: v for k, v in state.items() if not k.startswith("_") and k != "id"}, actor["id"])
    texts, latest, after = {}, {}, ""
    while True:
        rows = list(store.selected("SELECT * FROM records WHERE kind='rollout_text' AND parent=? AND id>? ORDER BY id LIMIT 128",
                                   (key, after)))
        if not rows:
            break
        for row in rows:
            if row["epoch"] != state["epoch"]:
                continue
            paired = store.get(key + ":" + state["epoch"] + ":" + digest(row["turn"]), required=False) or {}
            if paired.get("number", 0) >= row["number"]:
                continue
            for ident, (_, expected, needle, since) in wanted.items():
                matched = (expected and all(digest(t) in row["texts"] for t in expected)
                           or needle and needle.startswith("C1_MESSAGE ") and needle[11:] in row["messages"])
                if (matched and row["number"] > latest.get(ident, 0)
                        and (row["time"] is None or row["time"] >= (since or 0) - MARGIN_S)):
                    texts[ident] = (row["turn"], row["time"])
                    latest[ident] = row["number"]
        after = rows[-1]["id"]
        await asyncio.sleep(0)
    return {"readable": True, "damage": state["damage"], "texts": texts}


async def policy(store, actor, path, thread):
    """Workflow policy readback shares the diagnostic offset, including after restart."""
    result = {"verified": False, "source": "native rollout turn_context", "turns": []}
    observed = await diagnose(store, actor, path, thread, {})
    if not observed.get("readable"):
        return dict(result, reason=observed.get("reason", "rollout diagnostics unavailable"))
    key = "rollout_diagnostic:" + actor["id"]
    state = store.get(key)
    after = ""
    facts = []
    while True:
        rows = list(store.selected("SELECT * FROM records WHERE kind='rollout_policy' AND parent=? AND id>? ORDER BY id LIMIT 128",
                                   (key, after)))
        if not rows:
            break
        facts.extend(row for row in rows if row["epoch"] == state["epoch"])
        after = rows[-1]["id"]
        await asyncio.sleep(0)
    result["turns"] = [{name: row.get(name) for name in ("turn", "model", "effort", "approval_policy", "sandbox")}
                       for row in sorted(facts, key=lambda row: row["number"])]
    result["verified"] = bool(result["turns"]) and all(t["approval_policy"] == "never" and
        t["sandbox"] == "danger-full-access" for t in result["turns"])
    if not result["verified"]:
        result["reason"] = "no recorded turn contexts or unexpected native policy"
    return result


def native_mark(entry, read_at):
    """What one line of a writer's native log (native.WRITER_LOG) tells of its rollout's writing, or None: ("failed",
    time, summary) for a warning or an error of native's session or rollout writer, a failed append among them
    (measured on 0.163.0: "failed to record rollout items", "rollout writer failed for ..."), and for a line that is
    not native's JSON (`entry` None), at `read_at`, the latest it can have been written; ("written", time, None) for
    any other line of the session's, as each operation its handlers dispatch, and its shutdown, write one."""
    if not isinstance(entry, dict):
        return "failed", read_at, "a line that is not native's JSON log"
    target = entry.get("target") if isinstance(entry.get("target"), str) else ""
    if not target.startswith((SESSION, WRITER)):
        return None
    when = moment(entry.get("timestamp"))
    if entry.get("level") in ("WARN", "ERROR"):
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        return "failed", read_at if when is None else when, "%s %s: %s" % (target, entry["level"], str(fields.get("message"))[:200])
    if target.startswith(SESSION) and when is not None:
        return "written", when, None
    return None


def fold(marks, mark):
    """Keep the latest of each kind of native_mark in `marks`: {"failed": {"time", "line"}, "written": {"time"}}."""
    if mark:
        kind, when, line = mark
        if when > (marks.get(kind) or {}).get("time", float("-inf")):
            marks[kind] = {"time": when, "line": line} if line else {"time": when}
