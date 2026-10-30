"""Continuous lossless payload compaction, with one native deletion gate.

All SQLite mutations remain on the daemon thread. Selection, JSON conversion,
compression, file verification and census decoding run in joined workers. Row,
byte and elapsed budgets are scheduling slices, never evidence-retention limits.
"""
import asyncio
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from . import archive, dependencies, tools
from .client import CoordinatorError
from .locks import finish_thread
from .memory import setting
from .store import BLOCKERS, encoded


# Threat: large JSON payloads and long writer transactions delay live coordination.
# VERIFY green-03: 1024 tiny park rows amortize a durable file; 17.8/21.0 MB
# oversized event/response work took 0.547 s off-loop, with 12.8 ms max commit.
# Keep 2.4.25's 64-row journal slice for deletes and 8 MiB for ordinary payloads;
# Review scale: commit p99/max 21.3/63.0 ms, transaction max 110.1 ms,
# ticker max 116.6 ms. The 50 ms yield target is checked between indivisible
# statements, not a hard latency ceiling; oversized work is measured and warned.
# Bigger rows run alone, never truncate or become permanently ineligible.
DEFAULT_ROWS = 1024
DEFAULT_BYTES = 8 << 20
DEFAULT_DELETE_ROWS = 64
DEFAULT_SECONDS = 0.050
# Threat: a long reader pins WAL. Green-03 allocated peak was 21,147,992 bytes;
# 32 MiB leaves >50% headroom for that size class, 128 MiB permits four such
# allowances before pausing. The supervised checkpoint keeps running when paused.
# This bounds compactor contribution, not unrelated foreground WAL growth.
DEFAULT_WAL_HIGH = 128 << 20
DEFAULT_WAL_LOW = 32 << 20
DELTAS = {"item/agentMessage/delta", "item/commandExecution/outputDelta", "item/reasoning/summaryTextDelta",
          "item/reasoning/textDelta", "item/reasoning/summaryPartAdded", "item/plan/delta", "item/fileChange/outputDelta"}


def number(name, default, integer=True):
    value = setting("RETENTION_" + name, default)
    try:
        parsed = int(value) if integer else float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise CoordinatorError("CONFIG", "Invalid retention setting", {"setting": name}) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise CoordinatorError("CONFIG", "Retention setting must be positive and finite", {"setting": name})
    return parsed


def archives(store, listeners):
    return [{key: record.get(key) for key in ("listener", "first", "last", "rows", "bytes", "sha256", "path", "state",
                                               "encoding", "event_kind", "logical_bytes")}
            for listener in listeners for record in store.all("archive", listener)
            if record["state"] in ("durable", "complete")]


def projection(row):
    data = json.loads(row["data"])
    params = data.get("params", {})
    item = params.get("item") or {}
    return {"seq": row["seq"], "kind": row["kind"], "subject": row["subject"], "time": row["time"],
            "bytes": len(row["data"].encode()), "method": data.get("method"),
            "thread": params.get("threadId") or (params.get("thread") or {}).get("id"),
            "turn": params.get("turnId"), "item": params.get("itemId") or item.get("id"),
            "reason": data.get("reason") if row["kind"] == "listener/park_abandoned" else None}


def select_events(root, after, through, rows, budget, subject=None, kind=None):
    db = archive.readonly(root)
    try:
        db.execute("BEGIN")
        where = "seq>? AND seq<=? AND kind IN ('native','listener/park_abandoned')"
        args = [after, through]
        if subject is not None:
            where += " AND subject=?"
            args.append(subject)
        if kind is not None:
            where += " AND kind=?"
            args.append(kind)
        index = " INDEXED BY event_subject_kind" if subject is not None else ""
        candidates = db.execute("SELECT seq,length(CAST(data AS BLOB)) AS bytes FROM events" + index + " WHERE " + where +
                                " ORDER BY seq LIMIT ?", (*args, rows))
        selected, size = [], 0
        for candidate in candidates:
            if selected and size + candidate["bytes"] > budget:
                break
            selected.append(dict(db.execute("SELECT * FROM events WHERE seq=?", (candidate["seq"],)).fetchone()))
            size += candidate["bytes"]
    finally:
        db.close()
    return selected, [projection(row) for row in selected]


def select_record(root, kind, after):
    db = archive.readonly(root)
    try:
        row = db.execute("SELECT r.* FROM retention_records q INDEXED BY retention_record_kind "
                         "JOIN records r ON r.id=q.id WHERE q.kind=? AND q.id>? ORDER BY q.id LIMIT 1",
                         (kind, after)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def select_members(root, sequences, budget):
    db = archive.readonly(root)
    rows, size, after = [], 0, sequences[0] - 1
    try:
        for seq in sequences:
            row = db.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
            if row:
                length = len(row["data"].encode())
                if rows and size + length > budget:
                    break
                rows.append(dict(row))
                size += length
            after = seq
        return [projection(row) for row in rows], after
    finally:
        db.close()


def convert_record(root, row):
    data = json.loads(row["data"])
    before = len(row["data"].encode())
    changed = False
    if row["kind"] == "dynamic":
        response = data.get("response")
        if response is not None and not response.get("form"):
            kept = tools.stored(response)
            # No receipt-range substitution without an immutable source proof.
            if encoded(tools.replayed(None, kept)) != encoded(response):
                raise CoordinatorError("DYNAMIC_REPLAY", "Compacted response differs from the known response")
            if len(encoded(kept)) < len(encoded(response)):
                data["response"] = kept
                changed = True
    elif "dependencies" in data and not data.get("dependency_ref") and data["dependencies"]:
        ref = dependencies.historical(root, data)
        if ref and len(encoded(ref)) < len(encoded(data["dependencies"])) + len(encoded(data.get("qualified_dependencies", []))):
            data.pop("dependencies")
            data.pop("qualified_dependencies", None)
            data["dependency_ref"] = ref
            changed = True
    result = encoded(data) if changed else None
    after = len(result.encode()) if result else before
    payload = data.get("response") if row["kind"] == "dynamic" else data.get("dependency_ref", data.get("dependencies", []))
    form = payload.get("form", "plain") if isinstance(payload, dict) else "plain"
    return result, before, after, {"listener": data.get("listener") or data.get("native", {}).get("listener"),
                                 "actor": data.get("actor"), "payload_bytes": len(encoded(payload)), "form": form}


def seed_page(root, kind, after):
    """One bounded, durable bootstrap cursor; never a repeating negative scan."""
    db = archive.readonly(root)
    try:
        if kind == "subjects":
            rows = []
            # Seek past an entire subject, rather than DISTINCT scanning its
            # potentially millions of index entries to find the next subject.
            for _ in range(DEFAULT_DELETE_ROWS):
                row = db.execute("SELECT subject FROM events INDEXED BY event_subject_kind WHERE subject>? "
                                 "AND kind IN ('native','listener/park_abandoned') ORDER BY subject LIMIT 1", (after,)).fetchone()
                if not row:
                    break
                after = row[0]
                rows.append(after)
            return rows, after
        rows = db.execute("SELECT id FROM records INDEXED BY record_kind WHERE kind=? AND id>? ORDER BY id LIMIT ?",
                          (kind, after, DEFAULT_DELETE_ROWS)).fetchall()
        return [row[0] for row in rows], rows[-1][0] if rows else after
    finally:
        db.close()


def event_census(root):
    db = archive.readonly(root)
    groups, completed = {}, None
    try:
        pending = db.execute("SELECT 1 FROM retention_subjects INDEXED BY retention_subject_pending WHERE epoch!=scanned LIMIT 1").fetchone()
        seeding = db.execute("SELECT 1 FROM retention_seed WHERE done=0 LIMIT 1").fetchone()
        for raw, stamp in db.execute("SELECT census,completed FROM retention_subjects"):
            if stamp is not None:
                completed = max(completed or stamp, stamp)
            for key, value in json.loads(raw).items():
                item = groups.setdefault(key, {"rows": 0, "bytes": 0, "eligible_bytes": 0, "pins": {}})
                for field in ("rows", "bytes", "eligible_bytes"):
                    item[field] += value[field]
                for reason, pin in value["pins"].items():
                    kept = item["pins"].setdefault(reason, {"rows": 0, "bytes": 0, "oldest": pin["oldest"]})
                    kept["rows"] += pin["rows"]
                    kept["bytes"] += pin["bytes"]
                    kept["oldest"] = min(kept["oldest"], pin["oldest"])
        return groups, not pending and not seeding, completed
    finally:
        db.close()


def record_census(root):
    db = archive.readonly(root)
    try:
        rows = db.execute("SELECT * FROM retention_inventory")
        expected = db.execute("SELECT count(*) FROM records WHERE kind IN ('dynamic','operation')").fetchone()[0]
        pending = db.execute("SELECT count(*) FROM retention_records").fetchone()[0]
        states, groups, scanned = {}, {}, 0
        for row in rows:
            scanned += 1
            listener = row["listener"]
            if listener not in states:
                value = db.execute("SELECT json_extract(data,'$.state') FROM records WHERE id=?", (listener,)).fetchone()
                states[listener] = value[0] if value else "unbound"
            key = row["kind"] + ":" + row["form"] + ":" + states[listener]
            value = groups.setdefault(key, {"rows": 0, "bytes": 0, "payload_bytes": 0})
            value["rows"] += 1
            value["bytes"] += row["bytes"]
            value["payload_bytes"] += row["payload_bytes"]
        return {"groups": groups, "scanned_records": scanned, "total_records": expected,
                "pending_revisions": pending, "complete": scanned == expected and pending == 0, "sampled": time.time()}
    finally:
        db.close()


def file_census(root):
    groups = {key: {"files": 0, "bytes": 0} for key in ("events", "dependencies", "partial", "unmanifested")}
    db = archive.readonly(root)
    try:
        for directory, _, names in os.walk(Path(root) / "archive"):
            for name in names:
                path = Path(directory) / name
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue  # publication raced this observational census
                kind = "partial" if name.endswith(".partial") else "dependencies" if "dependencies" in path.parts else "events"
                groups[kind]["files"] += 1
                groups[kind]["bytes"] += size
                if kind == "events" and not db.execute("SELECT 1 FROM archive_catalog WHERE id=?",
                                                       ("archive:" + name.removesuffix(".jsonl.gz"),)).fetchone():
                    # Legacy files have different names; their record is the promise.
                    if not db.execute("SELECT 1 FROM records WHERE kind='archive' AND json_extract(data,'$.path')=? LIMIT 1",
                                      (str(path),)).fetchone():
                        groups["unmanifested"]["files"] += 1
                        groups["unmanifested"]["bytes"] += size
        return {"groups": groups, "sampled": time.time()}
    finally:
        db.close()


def park_contributions(rows):
    summary = defaultdict(lambda: {"count": 0, "first": None, "last": None})
    if rows[0]["kind"] == "listener/park_abandoned":
        for row in rows:
            reason = json.loads(row["data"]).get("reason")
            value = summary[reason]
            value["count"] += 1
            value["first"] = row["time"] if value["first"] is None else min(value["first"], row["time"])
            value["last"] = row["time"] if value["last"] is None else max(value["last"], row["time"])
    return dict(summary)


def wal_observation(root):
    if sqlite3.sqlite_version_info < (3, 51, 0):
        return {"error": "WAL_OBSERVATION_UNSUPPORTED", "sqlite": sqlite3.sqlite_version}
    db = archive.readonly(root)
    try:
        result = db.execute("PRAGMA wal_checkpoint(NOOP)").fetchone()
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        db.close()
    path = Path(root) / "ledger.sqlite-wal"
    try:
        info = path.stat()
        length, allocated = info.st_size, info.st_blocks * 512
    except FileNotFoundError:
        length, allocated = 0, 0
    busy, log, checkpointed = result
    return {"busy": busy, "log_frames": log, "checkpointed_frames": checkpointed,
            "pending_bytes": max(0, log - checkpointed) * page_size if log >= 0 and checkpointed >= 0 else None,
            "allocated_bytes": allocated, "length_bytes": length,
            "page_size": page_size, "page_count": pages, "freelist_pages": free, "freelist_bytes": free * page_size}


class Retention:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.rows = number("ROWS", DEFAULT_ROWS)
        self.bytes = number("BYTES", DEFAULT_BYTES)
        self.delete_rows = number("DELETE_ROWS", DEFAULT_DELETE_ROWS)
        self.seconds = number("SECONDS", DEFAULT_SECONDS, False)
        self.wal_high = number("WAL_HIGH_BYTES", DEFAULT_WAL_HIGH)
        self.wal_low = number("WAL_LOW_BYTES", DEFAULT_WAL_LOW)
        if self.wal_low >= self.wal_high:
            raise CoordinatorError("CONFIG", "Retention WAL low water must be below high water")
        self.queue, self.task = {}, None
        self.wake = asyncio.Event()
        self.last, self.wal = None, {}
        self.paused = False
        self.started = time.monotonic()
        self.metrics = {"deleted_rows": 0, "deleted_bytes": 0, "converted_records": 0, "converted_bytes": 0,
                        "revision_conflicts": 0, "oversized_rows": 0, "oversized_max_bytes": 0,
                        "batch_max_seconds": 0.0, "commit_max_seconds": 0.0, "wal_peak_bytes": 0,
                        "wal_pending_peak_bytes": 0, "warnings": 0, "warning_budgets": {},
                        "delete_max_rows": 0, "delete_max_bytes": 0, "scanned_rows": 0,
                        "scanned_bytes": 0, "subject_passes": 0, "seed_pages": 0, "archive_attempts": 0}
        progress = self.store.get("retention_scan", required=False) or {}
        self.after, self.through, self.subject_after = 0, archive.highwater(self.store), ""
        self.record_after = {"dynamic": "", "operation": ""}
        self.record_kind = "dynamic"
        self.census = progress.get("census", {})
        self.cycle_complete = None
        self.last_error = None
        self.work = "startup"
        self.record_census = {}
        self.record_sampled = 0.0
        self.files = {}
        self.files_fingerprint = None
        self.inventory_dirty = True
        self.store.retention_wake = self.wake.set

    def request(self, listenerid=None):
        if self.daemon.closing:
            return
        if listenerid:
            self.store.db.execute("INSERT INTO retention_subjects(subject) VALUES(?) "
                                  "ON CONFLICT(subject) DO UPDATE SET epoch=epoch+1", (listenerid,))
        self.wake.set()
        if self.task is None:
            self.task = self.daemon.background(self.run())

    def warning(self, budget, value, limit):
        if value >= limit * .8:
            self.metrics["warnings"] += 1
            counts = self.metrics["warning_budgets"]
            counts[budget] = counts.get(budget, 0) + 1

    def recovered(self, work=None):
        self.store.db.execute("DELETE FROM records WHERE kind='retention_error' AND id=?", ("retention_error:" + (work or self.work),))

    def errors(self):
        count = self.store.db.execute("SELECT count(*) FROM records WHERE kind='retention_error'").fetchone()[0]
        row = self.store.db.execute("SELECT data FROM records WHERE kind='retention_error' ORDER BY id LIMIT 1").fetchone()
        return {"count": count, "example": json.loads(row[0]) if row else None}

    def context(self, subject):
        s, d = self.store, self.daemon
        listener = s.get(subject, required=False)
        if not listener or listener.get("_kind") != "listener":
            return {"pin": "LISTENER_MISSING", "state": "missing"}
        unit = listener.get("unit")
        context = {"listener": listener, "state": listener["state"], "pin": None}
        if listener["state"] == "quarantined" or listener.get("recovery_state") == "failed" or s.db.execute(
                "SELECT 1 FROM records INDEXED BY blocker_quarantine WHERE " + BLOCKERS["quarantine"] +
                " AND parent=? LIMIT 1", (unit,)).fetchone():
            context["pin"] = "QUARANTINE"
            return context
        if listener.get("history_gaps") or (listener.get("run") and d.dormancy.pending(listener["run"])):
            context["pin"] = "HISTORY"
            return context
        applied = s.get("native_applied:" + subject, required=False) or {}
        context["applied"] = applied.get("sequence", 0)
        context["application_failed"] = bool(applied.get("failed") or applied.get("pending"))
        queue = d.queues.get(subject)
        context["dispatched"] = min(queue.cursor, queue.highwater) if queue is not None else None
        context["applying"] = (d.native_applying.get(subject) or {}).get("sequence")
        context["obligations"] = {r[0] for r in s.db.execute(
            "SELECT json_extract(data,'$.sequence') FROM records INDEXED BY blocker_obligation WHERE " +
            BLOCKERS["obligation"] + " AND parent=?", (subject,))}
        context["execution"] = s.db.execute(
            "SELECT 1 FROM records INDEXED BY blocker_operation WHERE " + BLOCKERS["operation"] +
            " AND parent=? AND (json_extract(data,'$.native.listener')=? OR json_extract(data,'$.native.listener') IS NULL) LIMIT 1",
            (unit, subject)).fetchone() is not None
        verified, invalid = {}, False
        for actor in s.all("actor", unit):
            if actor.get("kind") == "lead" or not actor.get("thread"):
                continue
            cp = d.history.checkpoint(actor) or {}
            verified[actor["thread"]] = cp.get("received_verified", 0)
            invalid = invalid or bool(cp.get("invalid") or cp.get("pending"))
        context.update(verified=verified, history_invalid=invalid,
                       gone=listener.get("family_gone") is not None or bool(listener.get("dormant_proof", {}).get("exit")))
        return context

    def gate(self, row, context):
        """The only native deletion decision; called again after every worker await."""
        if context["pin"]:
            return context["pin"]
        if row["kind"] == "listener/park_abandoned":
            return None
        seq = row["seq"]
        if context["application_failed"] or seq > context["applied"]:
            return "APPLICATION"
        if context["dispatched"] is not None and seq > context["dispatched"]:
            return "DISPATCH"
        if seq == context["applying"]:
            return "APPLYING"
        if seq in context["obligations"]:
            return "OBLIGATION"
        if context["history_invalid"]:
            return "HISTORY"
        if row["method"] == "turn/diff/updated":
            return None
        if row["method"] in DELTAS:
            if not all(row.get(key) for key in ("thread", "turn", "item")):
                return "COMPLETION_IDENTITY"
            complete = self.store.db.execute(
                "SELECT sequence FROM native_completions WHERE subject=? AND thread=? AND turn=? AND item=?",
                (row["subject"], row["thread"], row["turn"], row["item"])).fetchone()
            return None if complete and complete[0] <= context["applied"] else "COMPLETION"
        if context["execution"]:
            return "EXECUTION"
        if context["state"] in ("stopped", "dead", "dormant") and not context["gone"]:
            return "FAMILY_EXIT"
        if row.get("thread"):
            verified = context["verified"].get(row["thread"], 0)
        elif context["verified"]:
            # Threadless account/startup notifications can concern every thread.
            verified = min(context["verified"].values())
        elif not context["listener"].get("root_thread") and context["gone"]:
            # No native thread ever existed: no history receipt is applicable.
            verified = context["applied"]
        else:
            return "RECEIPT_SCOPE"
        return None if seq <= verified else "RECEIPT"

    def completions(self, entries):
        changed = set()
        for entry in entries:
            if self.store.db.execute(
                    "INSERT INTO native_completions VALUES(?,?,?,?,?) ON CONFLICT(subject,thread,turn,item) "
                    "DO UPDATE SET sequence=excluded.sequence WHERE sequence<excluded.sequence", entry).rowcount:
                changed.add(entry[0])
        for subject in changed:
            self.store.db.execute("INSERT INTO retention_subjects(subject) VALUES(?) "
                                  "ON CONFLICT(subject) DO UPDATE SET epoch=epoch+1", (subject,))

    def install(self, manifest, checked):
        s = self.store
        ident = manifest["id"]
        existing = s.db.execute("SELECT manifest FROM archive_catalog WHERE id=?", (ident,)).fetchone()
        if existing:
            if json.loads(existing[0])["sha256"] != manifest["sha256"]:
                raise CoordinatorError("ARCHIVE_CONFLICT", "Committed archive identity changed")
            return
        if manifest.get("encoding") == archive.ENCODING or manifest.get("state") == "durable":
            s.storage4()
        s.durable()
        manifest = dict(manifest, event_kind=manifest.get("event_kind", "native"),
                        methods=sorted({interval[2] for interval in checked["coverage"]}))
        s.db.execute("INSERT INTO archive_catalog VALUES(?,?)", (ident, encoded(manifest)))
        number = s.db.execute("INSERT INTO archive_objects(id) VALUES(?)", (ident,)).lastrowid
        s.db.executemany("INSERT OR IGNORE INTO archive_members VALUES(?,?)",
                        ((seq, number) for _, _, _, first, last in checked["coverage"] for seq in range(first, last + 1)))
        self.completions(checked["completions"])
        totals = s.get("retention_totals", required=False) or {"rows": 0, "bytes": 0, "logical_bytes": 0}
        s.put("retention_totals", "retention_totals", {"rows": totals["rows"] + manifest["rows"],
              "bytes": totals["bytes"] + manifest["bytes"], "logical_bytes": totals["logical_bytes"] + checked["logical_bytes"]})

    async def resume(self):
        row = self.store.db.execute("SELECT r.id,r.data FROM retention_archives q INDEXED BY retention_archive_due "
                                    "JOIN records r ON r.id=q.id WHERE q.retry<=? ORDER BY q.retry,q.id LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return False
        self.work = row["id"]
        self.metrics["archive_attempts"] += 1
        record = dict(json.loads(row["data"]), id=row["id"])
        subject = record.get("subject", record.get("listener"))
        epoch = self.store.db.execute("SELECT epoch FROM retention_subjects WHERE subject=?", (subject,)).fetchone()
        indexed = self.store.db.execute("SELECT 1 FROM archive_catalog WHERE id=?", (row["id"],)).fetchone()
        outcome = "complete"  # Non-durable records have no pending deletion pass.
        if not indexed or record["state"] == "durable":
            checked = await finish_thread(archive.verify, self.store.root, record)
            with self.store.transaction():
                self.install(record, checked)
            if record["state"] == "durable":
                outcome = await self.delete(record)
        with self.store.transaction():
            current = self.store.db.execute("SELECT epoch FROM retention_subjects WHERE subject=?", (subject,)).fetchone()
            # An interrupted pass still owns work even when no pin evidence changed.
            if outcome == "complete" or outcome == "pinned" and current == epoch and not self.paused:
                self.store.db.execute("DELETE FROM retention_archives WHERE id=?", (row["id"],))
        self.recovered()
        return True

    async def write(self, rows):
        self.work = "events:" + rows[0]["subject"] + ":" + str(rows[0]["seq"])
        began = time.monotonic()
        manifest = await finish_thread(archive.write_events, self.store.root, rows)
        checked = await finish_thread(archive.verify, self.store.root, manifest)
        manifest.update(state="durable", created=time.time(), write_seconds=time.monotonic() - began)
        summary = await finish_thread(park_contributions, rows)
        began = time.monotonic()
        with self.store.transaction():
            if not self.store.get(manifest["id"], required=False):
                self.install(manifest, checked)
                self.store.put("archive", manifest["id"], {k: v for k, v in manifest.items() if k != "id"}, manifest["subject"])
                for reason, contribution in summary.items():
                    key = "park_summary:" + manifest["subject"] + ":" + hashlib.sha256(encoded(reason).encode()).hexdigest()
                    prior = self.store.get(key, required=False) or {"count": 0}
                    self.store.put("park_summary", key, {"reason": reason, "count": prior["count"] + contribution["count"],
                        "first": min(prior.get("first", contribution["first"]), contribution["first"]),
                        "last": max(prior.get("last", contribution["last"]), contribution["last"])}, manifest["subject"])
        self.metrics["commit_max_seconds"] = max(self.metrics["commit_max_seconds"], time.monotonic() - began)
        outcome = await self.delete(manifest)
        if outcome == "complete":
            self.store.db.execute("DELETE FROM retention_archives WHERE id=?", (manifest["id"],))
        self.recovered()

    async def delete(self, manifest):
        """Distinguish a full coverage pass from shutdown or WAL interruption."""
        # Restart revalidation is mandatory. Selection of this archive's covered
        # rows is byte-bounded; no SQLite cursor survives the file verification.
        checked = await finish_thread(archive.verify, self.store.root, manifest)
        sequences = [seq for _, _, _, first, last in checked["coverage"] for seq in range(first, last + 1)]
        offset = 0
        pinned = False
        while not self.daemon.closing:
            if offset == len(sequences):
                if not pinned:
                    record = self.store.get(manifest["id"])
                    record.update(state="complete", completed=time.time())
                    self.store.save(record)
                return "pinned" if pinned else "complete"
            selected = sequences[offset:offset + self.delete_rows]
            covered, through = await finish_thread(select_members, self.store.root, selected, self.bytes)
            if not await self.pressure():
                return "paused"
            context = self.context(manifest.get("subject", manifest.get("listener")))
            eligible = [r for r in covered if self.gate(r, context) is None]
            pinned = pinned or len(eligible) != len(covered)
            permitted = {r["seq"]: r for r in eligible}
            deleted = []
            began = time.monotonic()
            with self.store.transaction():
                # No await from the final gate through the short commit.
                if eligible:
                    self.store.storage4()
                after = through
                for row in covered:
                    if row["seq"] in permitted:
                        self.store.db.execute("DELETE FROM events WHERE seq=?", (row["seq"],))
                        deleted.append(row)
                    after = row["seq"]
                    if time.monotonic() - began >= self.seconds:
                        through = after
                        break
            while offset < len(sequences) and sequences[offset] <= through:
                offset += 1
            elapsed = time.monotonic() - began
            self.metrics["commit_max_seconds"] = max(self.metrics["commit_max_seconds"], elapsed)
            size = sum(r["bytes"] for r in deleted)
            self.metrics["deleted_rows"] += len(deleted)
            self.metrics["deleted_bytes"] += size
            self.metrics["delete_max_rows"] = max(self.metrics["delete_max_rows"], len(deleted))
            self.metrics["delete_max_bytes"] = max(self.metrics["delete_max_bytes"], size)
            self.warning("delete_rows", len(deleted), self.delete_rows)
            self.warning("delete_bytes", size, self.bytes)
            self.warning("commit_seconds", elapsed, self.seconds)
            await asyncio.sleep(0)
        return "interrupted"

    def count(self, counts, row, context, pin):
        key = row["kind"] + ":" + (row["method"] or "") + ":" + context["state"]
        item = counts.setdefault(key, {"rows": 0, "bytes": 0, "eligible_bytes": 0, "pins": {}})
        item["rows"] += 1
        item["bytes"] += row["bytes"]
        if pin:
            value = item["pins"].setdefault(pin, {"bytes": 0, "rows": 0, "oldest": row["time"]})
            value["bytes"] += row["bytes"]
            value["rows"] += 1
            value["oldest"] = min(value["oldest"], row["time"])
        else:
            item["eligible_bytes"] += row["bytes"]

    async def seed(self):
        row = self.store.db.execute("SELECT * FROM retention_seed WHERE done=0 ORDER BY kind LIMIT 1").fetchone()
        if not row:
            return False
        self.work = "seed:" + row["kind"]
        ids, after = await finish_thread(seed_page, self.store.root, row["kind"], row["after"])
        with self.store.transaction():
            for ident in ids:
                if row["kind"] == "subjects":
                    self.store.db.execute("INSERT OR IGNORE INTO retention_subjects(subject) VALUES(?)", (ident,))
                elif row["kind"] == "archive":
                    # One bounded legacy migration, not a repeated anti-join over
                    # completed manifests. New publications enqueue atomically.
                    self.store.db.execute("INSERT OR IGNORE INTO retention_archives(id) SELECT id FROM records WHERE id=? "
                        "AND (json_extract(data,'$.state')='durable' OR NOT EXISTS "
                        "(SELECT 1 FROM archive_catalog WHERE id=records.id))", (ident,))
                else:
                    self.store.db.execute("INSERT OR IGNORE INTO retention_records VALUES(?,?)", (ident, row["kind"]))
            self.store.db.execute("UPDATE retention_seed SET after=?,done=? WHERE kind=?", (after, int(not ids), row["kind"]))
        self.metrics["seed_pages"] += 1
        self.inventory_dirty = True
        return True

    async def scan(self, subject=None):
        s = self.store
        if subject is None:
            row = s.db.execute("SELECT * FROM retention_subjects INDEXED BY retention_subject_pending "
                               "WHERE epoch!=scanned AND subject>? ORDER BY subject LIMIT 1", (self.subject_after,)).fetchone()
            if not row:
                self.subject_after = ""
                row = s.db.execute("SELECT * FROM retention_subjects INDEXED BY retention_subject_pending "
                                   "WHERE epoch!=scanned ORDER BY subject LIMIT 1").fetchone()
        else:
            row = s.db.execute("SELECT * FROM retention_subjects WHERE subject=? AND epoch!=scanned", (subject,)).fetchone()
        if not row:
            return False
        subject = row["subject"]
        self.subject_after = subject
        self.work = "subject:" + subject
        self.cycle_complete = None
        if not row["phase"]:
            s.db.execute("UPDATE retention_subjects SET pass_epoch=epoch,through=?,phase='native',after=0,counts='{}' WHERE subject=?",
                         (archive.highwater(s), subject))
            row = s.db.execute("SELECT * FROM retention_subjects WHERE subject=?", (subject,)).fetchone()
        self.after, self.through = row["after"], row["through"]
        rows, projected = await finish_thread(select_events, s.root, row["after"], row["through"],
                                              self.rows, self.bytes, subject, row["phase"])
        if not rows:
            if row["phase"] == "native":
                s.db.execute("UPDATE retention_subjects SET phase='listener/park_abandoned',after=0 WHERE subject=?", (subject,))
            else:
                s.db.execute("UPDATE retention_subjects SET scanned=pass_epoch,phase='',census=counts,completed=? WHERE subject=?",
                             (time.time(), subject))
                self.metrics["subject_passes"] += 1
            self.inventory_dirty = True
            return True
        self.metrics["scanned_rows"] += len(rows)
        self.metrics["scanned_bytes"] += sum(r["bytes"] for r in projected)
        entries = [(r["subject"], r["thread"], r["turn"], r["item"], r["seq"]) for r in projected
                   if r["method"] == "item/completed" and all(r.get(k) for k in ("thread", "turn", "item"))]
        if entries:
            with self.store.transaction():
                self.completions(entries)
        context, selected, size = self.context(subject), [], 0
        began = time.monotonic()
        for raw, row in zip(rows, projected):
            pin = self.gate(row, context)
            if pin is None:
                # Durable chunks are resumed first; overlapping new files are not minted.
                covered = s.db.execute("SELECT 1 FROM archive_members WHERE seq=?", (row["seq"],)).fetchone()
                if not covered:
                    covered = s.db.execute("SELECT 1 FROM archive_coverage WHERE subject=? AND kind=? "
                    "AND method=? AND first<=? AND last>=? LIMIT 1",
                    (row["subject"], row["kind"], row["method"] or "", row["seq"], row["seq"])).fetchone()
                if not covered:
                    selected.append(raw)
                    size += row["bytes"]
            if time.monotonic() - began >= self.seconds:
                await asyncio.sleep(0)
                began = time.monotonic()
                context = self.context(subject)
        if selected:
            if not await self.pressure():
                return False
            if size > self.bytes:
                self.metrics["oversized_rows"] += 1
                self.metrics["oversized_max_bytes"] = max(self.metrics["oversized_max_bytes"], size)
            self.warning("selection_bytes", size, self.bytes)
            self.warning("selection_rows", len(selected), self.rows)
            await self.write(selected)
        # Count what remains, including pins that changed during file I/O. A
        # subject whose evidence changes during a pass retains its newer epoch.
        counts = json.loads(s.db.execute("SELECT counts FROM retention_subjects WHERE subject=?", (subject,)).fetchone()[0])
        context = self.context(subject)
        for row in projected:
            if s.db.execute("SELECT 1 FROM events WHERE seq=?", (row["seq"],)).fetchone():
                self.count(counts, row, context, self.gate(row, context))
        s.db.execute("UPDATE retention_subjects SET after=?,counts=? WHERE subject=?", (rows[-1]["seq"], encoded(counts), subject))
        self.inventory_dirty = True
        self.recovered("subject:" + subject)
        return True

    async def compact_record(self):
        kind = self.record_kind
        self.record_kind = "operation" if kind == "dynamic" else "dynamic"
        row = await finish_thread(select_record, self.store.root, kind, self.record_after[kind])
        if row is None:
            self.record_after[kind] = ""
            return False
        self.record_after[kind] = row["id"]
        self.work = row["id"]
        result, before, after, inventory = await finish_thread(convert_record, self.store.root, row)
        self.warning("record_bytes", before, self.bytes)
        if before > self.bytes:
            self.metrics["oversized_rows"] += 1
            self.metrics["oversized_max_bytes"] = max(before, self.metrics["oversized_max_bytes"])
        if not await self.pressure():
            return False
        began = time.monotonic()
        with self.store.transaction():
            current = self.store.db.execute("SELECT revision FROM records WHERE id=? AND kind=?", (row["id"], kind)).fetchone()
            if not current or current[0] != row["revision"]:
                self.metrics["revision_conflicts"] += 1
                if not current:
                    self.store.db.execute("DELETE FROM retention_records WHERE id=?", (row["id"],))
                return True
            revision = row["revision"]
            if result is not None:
                if kind == "operation":
                    self.store.storage4()
                if not self.store.compare_record(row["id"], kind, revision, result):
                    self.metrics["revision_conflicts"] += 1
                    return True
                revision += 1
                self.metrics["converted_records"] += 1
                self.metrics["converted_bytes"] += before - after
            self.store.db.execute("INSERT INTO retention_revisions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision",
                                  (row["id"], revision))
            listener = inventory["listener"]
            if not listener and inventory["actor"]:
                listener = (self.store.get(inventory["actor"], required=False) or {}).get("listener")
            self.store.db.execute("INSERT INTO retention_inventory VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                  "listener=excluded.listener,bytes=excluded.bytes,payload_bytes=excluded.payload_bytes,form=excluded.form",
                                  (row["id"], kind, listener, after, inventory["payload_bytes"], inventory["form"]))
            self.store.db.execute("DELETE FROM retention_records WHERE id=?", (row["id"],))
        self.metrics["commit_max_seconds"] = max(self.metrics["commit_max_seconds"], time.monotonic() - began)
        self.recovered()
        self.inventory_dirty = True
        return True

    async def pressure(self):
        return self.observe_pressure(await finish_thread(wal_observation, self.store.root))

    def observe_pressure(self, observation):
        self.wal = observation
        # A competing checkpoint can return unknown (-1) frame counts. It is
        # not evidence of an empty WAL; pause until a measurable pass returns.
        if self.wal.get("error") or self.wal["log_frames"] < 0 or self.wal["checkpointed_frames"] < 0:
            self.paused = True
            return False
        size = self.wal["pending_bytes"]
        self.warning("wal_bytes", size, self.wal_high)
        self.metrics["wal_peak_bytes"] = max(self.metrics["wal_peak_bytes"], self.wal["allocated_bytes"])
        self.metrics["wal_pending_peak_bytes"] = max(self.metrics["wal_pending_peak_bytes"], size)
        if size >= self.wal_high:
            self.paused = True
        elif size <= self.wal_low:
            self.paused = False
        return not self.paused

    async def run(self):
        try:
            while not self.daemon.closing:
                self.wake.clear()
                if self.daemon.draining:
                    await asyncio.sleep(1)
                    continue
                began = time.monotonic()
                retry = None
                try:
                    if not await self.pressure():
                        await asyncio.sleep(1)  # same retry cadence as daemon maintenance
                        continue
                    seeded = await self.seed()
                    resumed = await self.resume()
                    scanned = False
                    # Pending promises have their own indexed queue. Pinned
                    # promises leave it until their subject's evidence changes.
                    if not resumed:
                        scanned = await self.scan()
                    converted = await self.compact_record()
                    # An exhausted response scan must not insert a one-second
                    # idle wait between every pending operation conversion (or
                    # vice versa). Give both kinds a slice before deciding idle.
                    converted = await self.compact_record() or converted
                    active = seeded or resumed or scanned or converted
                    if active and self.store.reclamation_wake:
                        self.store.reclamation_wake()
                    if self.inventory_dirty and (not active or time.monotonic() - self.record_sampled >= 1):
                        self.record_census = await finish_thread(record_census, self.store.root)
                        self.census, complete, completed = await finish_thread(event_census, self.store.root)
                        self.cycle_complete = (completed or time.time()) if complete else None
                        self.record_sampled = time.monotonic()
                        self.inventory_dirty = False
                    totals = self.store.get("retention_totals", required=False) or {}
                    fingerprint = (totals.get("_revision"), self.metrics["converted_records"])
                    if not active and fingerprint != self.files_fingerprint:
                        self.files = await finish_thread(file_census, self.store.root)
                        self.files_fingerprint = fingerprint
                    self.last_error = self.errors()["example"]
                    self.last = {"time": time.time(), "after": self.after, "through": self.through}
                    if not active:
                        # Record cursors wrap without sleeping between queued
                        # revisions. No timed wake remains for unchanged pins.
                        if self.store.db.execute("SELECT 1 FROM retention_records LIMIT 1").fetchone():
                            active = True
                        due = self.store.db.execute("SELECT retry FROM retention_archives ORDER BY retry,id LIMIT 1").fetchone()
                        if due:
                            retry = max(.01, due[0] - time.time())
                except Exception as exc:
                    error = {"code": getattr(exc, "code", type(exc).__name__), "details": getattr(exc, "details", {}),
                             "time": time.time(), "work": self.work}
                    # A persistent broken archive must be visible, without emitting
                    # an unbounded repeated audit row on every one-second retry.
                    prior = self.store.get("retention_error:" + self.work, required=False)
                    if not prior or (prior["code"], prior["details"]) != (error["code"], error["details"]):
                        self.store.put("retention_error", "retention_error:" + self.work, error)
                        self.store.event("archive/deferred", None, error)
                    self.last_error = error
                    self.store.db.execute("UPDATE retention_archives SET retry=? WHERE id=?", (time.time() + 1, self.work))
                    active, retry = False, 1
                self.metrics["batch_max_seconds"] = max(self.metrics["batch_max_seconds"], time.monotonic() - began)
                if not active:
                    try:
                        if retry is None:
                            await self.wake.wait()
                        else:
                            await asyncio.wait_for(self.wake.wait(), retry)
                    except asyncio.TimeoutError:
                        pass
                await asyncio.sleep(0)
        finally:
            self.task = None

    def status(self):
        elapsed = max(.001, time.monotonic() - self.started)
        totals = self.store.get("retention_totals", required=False) or {"rows": 0, "bytes": 0, "logical_bytes": 0}
        eligible = sum(value["eligible_bytes"] for value in self.census.values())
        rate = (self.metrics["deleted_bytes"] + self.metrics["converted_bytes"]) / elapsed
        complete = self.cycle_complete is not None and self.record_census.get("complete", False)
        return {"ledger_bytes": (self.store.root / "ledger.sqlite").stat().st_size,
                "page_count": self.wal.get("page_count"), "freelist_pages": self.wal.get("freelist_pages"),
                "freelist_bytes": self.wal.get("freelist_bytes"), "archived_rows": totals["rows"], "archived_bytes": totals["bytes"],
                "archived_logical_bytes": totals["logical_bytes"], "hot": self.census, "scan_after": self.after, "scan_through": self.through,
                "scan_complete": self.cycle_complete is not None, "scan_completed": self.cycle_complete,
                "scan_age_seconds": time.time() - self.cycle_complete if self.cycle_complete else None,
                "record_hot": self.record_census, "archive_files": self.files,
                "eligible_bytes": eligible, "eligible_bytes_complete": complete,
                "drain_bytes_per_second": rate, "estimated_seconds": eligible / rate if rate and complete else None,
                "wal": dict(self.wal, paused=self.paused, high_bytes=self.wal_high, low_bytes=self.wal_low),
                "limits": {"rows": self.rows, "bytes": self.bytes, "delete_rows": self.delete_rows,
                           "seconds": self.seconds, "warning_fraction": .8, "oversized": "one complete row, joined worker"},
                "metrics": self.metrics, "last": self.last, "error": self.last_error, "errors": self.errors(),
                "queued": self.store.db.execute("SELECT count(*) FROM retention_subjects WHERE epoch!=scanned").fetchone()[0]}
