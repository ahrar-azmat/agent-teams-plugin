"""Fsynced native archives precede bounded, restartable ledger deletion."""
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

from . import participants
from .locks import finish_thread
from .store import encoded, fsync_dir, BLOCKERS


# Reuse the native journal's measured 64-row cooperative batch, not a limit on
# retained evidence. A large individual native item remains one complete row.
BATCH_ROWS = 64


def archives(store, listeners):
    return [{key: record.get(key) for key in ("listener", "first", "last", "rows", "bytes", "sha256", "path", "state")}
            for listener in listeners for record in store.all("archive", listener)
            if record["state"] in ("durable", "complete")]


class Retention:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.queue, self.task = {}, None
        self.last = None

    def request(self, listenerid):
        if self.daemon.closing:
            return
        self.queue[listenerid] = None
        if self.task is None:
            self.task = self.daemon.background(self.run())

    def eligible(self, listener):
        store = self.store
        chain, current = [], listener
        while current:
            if current["state"] != "dormant" or not current.get("dormant_proof", {}).get("exit"):
                return None
            chain.append(current)
            current = store.get(current.get("replaces"), required=False)
        unit = listener["unit"]
        if listener.get("history_gaps") or self.daemon.dormancy.pending(listener["run"]):
            return None
        for checkout in participants.checkout_ids(store, unit):
            if store.db.execute("SELECT 1 FROM unknowns u LEFT JOIN resolutions s ON s.unknown_id=u.id "
                                "WHERE u.checkout=? AND s.edit IS NULL LIMIT 1", (checkout,)).fetchone():
                return None
        ids = {l["id"] for l in chain}
        if store.db.execute("SELECT 1 FROM records INDEXED BY blocker_quarantine WHERE " + BLOCKERS["quarantine"] +
                            " AND parent=? LIMIT 1", (unit,)).fetchone() or any(
                store.db.execute("SELECT 1 FROM records INDEXED BY blocker_operation WHERE " + BLOCKERS["operation"] +
                                 " AND parent=? AND json_extract(data,'$.native.listener')=? LIMIT 1", (unit, ident)).fetchone()
                for ident in ids):
            return None
        actors = [a for a in store.all("actor", unit) if a.get("kind") != "lead"]
        verified = min(((self.daemon.history.checkpoint(a) or {}).get("received_verified", 0) for a in actors), default=0)
        limits = {}
        self.daemon.flush_native_progress()
        for entry in chain:
            applied = store.get("native_applied:" + entry["id"], required=False) or {}
            if applied.get("failed") or applied.get("pending") or store.db.execute(
                    "SELECT 1 FROM records INDEXED BY blocker_obligation WHERE " + BLOCKERS["obligation"] +
                    " AND parent=? LIMIT 1", (entry["id"],)).fetchone():
                return None
            limits[entry["id"]] = min(verified, applied.get("sequence", 0))
        return limits

    async def run(self):
        try:
            while self.queue and not self.daemon.closing:
                listenerid = next(iter(self.queue))
                self.queue.pop(listenerid)
                listener = self.store.get(listenerid)
                limits = self.eligible(listener)
                if limits is None:
                    continue
                for ident, through in limits.items():
                    try:
                        await self.archive(listener, ident, through)
                    except Exception as exc:
                        self.last = {"listener": ident, "error": getattr(exc, "code", type(exc).__name__), "time": time.time()}
                        self.store.event("archive/deferred", ident, self.last)
        finally:
            self.task = None

    async def archive(self, owner, listenerid, through):
        store = self.store
        # Resume the exact durable range before considering a new one. Its
        # gzip can overlap undeleted rows after a crash, but never another file.
        pending = [a for a in store.all("archive", listenerid) if a["state"] == "durable"]
        if pending:
            for record in pending:
                await self.delete(owner, record)
            return
        bounds = store.db.execute("SELECT min(seq),max(seq),count(*) FROM events WHERE kind='native' AND subject=? AND seq<=?",
                                  (listenerid, through)).fetchone()
        first, last, count = bounds
        if not count:
            return
        if Path(listenerid).name != listenerid:
            raise ValueError("Invalid archive listener path")
        directory = store.root / "archive" / "native" / listenerid
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (str(first) + "-" + str(last) + ".jsonl.gz")
        temporary = path.with_suffix(path.suffix + ".partial")
        began = time.monotonic()
        rows_written, after = 0, first - 1
        # Gzip's header has no wall-clock or temporary filename: a replay of the
        # same range produces the same archive and digest.
        raw = open(temporary, "wb")
        os.chmod(temporary, 0o600)
        output = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
        try:
            while True:
                rows = store.db.execute("SELECT * FROM events WHERE kind='native' AND subject=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
                                        (listenerid, after, last, BATCH_ROWS)).fetchall()
                if not rows:
                    break
                data = ("".join(encoded({**dict(row), "data": json.loads(row["data"])}) + "\n" for row in rows)).encode()
                await finish_thread(output.write, data)
                rows_written += len(rows)
                after = rows[-1]["seq"]
                await asyncio.sleep(0)
            await finish_thread(output.close)
            await finish_thread(raw.flush)
            await finish_thread(os.fsync, raw.fileno())
        finally:
            output.close()
            raw.close()
        if rows_written != count:
            raise ValueError("Archive range changed before publication")
        await finish_thread(os.replace, temporary, path)
        await finish_thread(fsync_dir, directory)
        # Parent creation must also survive a crash.
        await finish_thread(fsync_dir, directory.parent)
        await finish_thread(fsync_dir, directory.parent.parent)
        await finish_thread(fsync_dir, store.root)
        checksum = await finish_thread(file_digest, path)
        ident = "archive:" + listenerid + ":" + str(first) + ":" + str(last)
        with store.transaction():
            store.put("archive", ident, {"listener": listenerid, "first": first, "last": last, "rows": count,
                "bytes": path.stat().st_size, "sha256": checksum, "path": str(path), "state": "durable",
                "created": time.time(), "write_seconds": time.monotonic() - began}, listenerid)
            store.event("native/archived", listenerid, {"archive": ident, "path": str(path), "first": first, "last": last})
        await self.delete(owner, store.get(ident))

    async def delete(self, owner, record):
        store = self.store
        path = Path(record["path"])
        if not path.is_relative_to(store.root / "archive" / "native") or await finish_thread(file_digest, path) != record["sha256"]:
            raise ValueError("Archive digest or location changed; no deletion permitted")
        began = time.monotonic()
        while not self.daemon.closing and self.eligible(store.get(owner["id"])) is not None:
            rows = store.db.execute("SELECT seq FROM events WHERE subject=? AND kind='native' AND seq>=? AND seq<=? ORDER BY seq LIMIT ?",
                                    (record["listener"], record["first"], record["last"], BATCH_ROWS)).fetchall()
            if not rows:
                record.update(state="complete", completed=time.time(), delete_seconds=time.monotonic() - began)
                store.save(record)
                self.last = {"listener": record["listener"], "rows": record["rows"], "bytes": record["bytes"], "time": time.time()}
                return
            with store.transaction():
                store.db.executemany("DELETE FROM events WHERE seq=?", [(row[0],) for row in rows])
            await asyncio.sleep(0)

    def status(self):
        row = self.store.db.execute("SELECT coalesce(sum(json_extract(data,'$.rows')),0),coalesce(sum(json_extract(data,'$.bytes')),0) "
                                    "FROM records WHERE kind='archive' AND json_extract(data,'$.state') IN ('durable','complete')").fetchone()
        path = self.store.root / "ledger.sqlite"
        return {"ledger_bytes": path.stat().st_size, "freelist_pages": self.store.db.execute("PRAGMA freelist_count").fetchone()[0],
                "archived_rows": row[0], "archived_bytes": row[1], "last": self.last, "queued": len(self.queue)}


def file_digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1 << 20):
            result.update(chunk)
    return result.hexdigest()
