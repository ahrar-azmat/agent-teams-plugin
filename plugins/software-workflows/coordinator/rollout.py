"""What a writer's app-server saved in its thread's rollout: the turns it started, how each ended, and which turn
each input entered. It is final only once that app-server has exited: read before, a later record is missing.

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
from datetime import datetime
import json
import os
from pathlib import Path
import stat


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


def scan(path, thread, wanted):
    """Read the thread's rollout. `wanted` maps a key to (client_id, texts, needle, since). Returns {"readable": False,
    "reason": ...}, or {"readable": True, "damage": None or {"reason", "mended"}, "turns": {turn: "completed" |
    "interrupted" | None}, "found": {key: turn}, "texts": {key: (turn, time or None)}}. An input is found only by its
    client id's record. What else could be it is told, never taken as proof: a user message holding its text (every
    text of `texts` an input_text, or `needle` within one) that no other input's UserMessage record followed in its
    turn, written at or after `since`, when the input was first sent, or at a time that cannot be read (an older one
    is another input's); and damage, a record without its newline (the writer ended mid-record) or not JSON (a write
    cut short whose line native ended later). `mended` is when native wrote the first whole record after the last
    damage (None when none follows): records are written in order, so damage mended before an input was sent cannot
    have cut its records short."""
    stream, reason = opened(path, thread)
    if stream is None:
        return {"readable": False, "reason": reason}
    by_client = {client: key for key, (client, _, _, _) in wanted.items() if client}
    turns, found, current, damage = {}, {}, None, None
    candidates = {}  # key -> (turn, time, record number) of the latest user message with its content
    unpaired = {}  # turn -> [(key, record number)] matched by its latest user message, before that message's client id
    number = 0
    try:
        with stream:
            for line in stream:
                number += 1
                if not line.endswith(b"\n"):
                    damage = {"reason": "ends with a record cut short", "mended": None}
                    break
                if not line.strip():
                    continue  # no record (native's own reader skips it too: recorder.rs load_rollout_items)
                try:
                    record = json.loads(line)
                except ValueError:
                    record = None
                if not isinstance(record, dict):
                    damage = {"reason": "holds a record that is not whole (record %d)" % number, "mended": None}
                    continue
                if damage and damage["mended"] is None:
                    damage["mended"] = moment(record.get("timestamp"))
                payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                outer, kind = record.get("type"), payload.get("type")
                turn = (payload.get("turn_id") or (payload.get("turn_attribution") or {}).get("turn_id")
                        or (payload.get("internal_chat_message_metadata_passthrough") or {}).get("turn_id"))
                if outer == "event_msg" and kind in STARTED:
                    current = turn or current
                    turns.setdefault(current, None)
                elif outer == "event_msg" and kind in ENDED:
                    turns[turn or current] = ENDED[kind]
                elif outer == "event_msg" and kind in ("item_completed", "user_message"):
                    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                    if kind == "item_completed" and item.get("type") != "UserMessage":
                        continue
                    client = item.get("client_id") if kind == "item_completed" else payload.get("client_id")
                    owner = (turn if kind == "item_completed" else None) or current
                    for key, matched in unpaired.pop(owner, ()):
                        # This record ends the user message that held the key's content: another input's, or its own.
                        if client != wanted[key][0] and candidates.get(key, (None, None, None))[2] == matched:
                            del candidates[key]
                    if client in by_client and by_client[client] not in found:
                        found[by_client[client]] = owner
                elif outer == "response_item" and kind == "message" and payload.get("role") == "user":
                    owner, said = turn or current, user_texts(payload)
                    for key, (_, expected, needle, _) in wanted.items():
                        if key not in found and ((expected and all(t in said for t in expected))
                                                 or (needle and any(needle in t for t in said))):
                            candidates[key] = (owner, moment(record.get("timestamp")), number)
                            unpaired.setdefault(owner, []).append((key, number))
    except OSError as exc:
        return {"readable": False, "reason": type(exc).__name__}
    texts = {key: (owner, when) for key, (owner, when, _) in candidates.items()
             if key not in found and (when is None or when >= (wanted[key][3] or 0) - MARGIN_S)}
    return {"readable": True, "damage": damage, "turns": turns, "found": found, "texts": texts}


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
