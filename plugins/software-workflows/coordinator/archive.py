"""Verified immutable gzip objects and a single hot/cold event reader.

Workers own their read-only connections. A page snapshots hot rows and manifests
together, then closes SQLite before opening files. Publication/deletion cannot
leave a hole between those two observations. Files are never a deletion permit;
only a committed, verified catalog entry and its exact coverage are.
"""
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from .client import CoordinatorError
from .locks import finish_thread
from .store import encoded, fsync_dir


ENCODING = "gzip-jsonl-raw-v2"
KINDS = ("native", "listener/park_abandoned")


def readonly(root):
    db = sqlite3.connect((Path(root) / "ledger.sqlite").as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def highwater(store):
    # AUTOINCREMENT's durable clock survives deleting the last hot event.
    row = store.db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
    return row[0] if row else 0


def file_digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def location(root, value):
    path = Path(value)
    base = Path(root) / "archive"
    if (not path.is_relative_to(base) or ".." in path.relative_to(base).parts
            or any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(base))):
        raise CoordinatorError("ARCHIVE_COVERAGE", "Archive path escapes its state directory")
    return path


def full_sync(stream):
    stream.flush()
    os.fsync(stream.fileno())
    if sys.platform == "darwin":
        import fcntl
        fcntl.fcntl(stream.fileno(), fcntl.F_FULLFSYNC)


def published_sync(root, path):
    directory = path.parent
    while True:
        fsync_dir(directory)
        if directory == Path(root):
            break
        directory = directory.parent
    with open(path, "rb") as published:
        full_sync(published)


def publish_file(root, path, lines):
    """Finish compression, flush the device, publish, sync parents, flush again.

    Deterministic names/headers make an interrupted unpublished chunk replaceable
    by exactly the same input. An orphan or .partial never authorizes deletion.
    No archive file is garbage-collected by this release.
    """
    path = location(root, path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = location(root, path.with_suffix(path.suffix + ".partial"))
    stage = "compression"
    try:
        with open(pending, "wb") as raw:
            os.chmod(pending, 0o600)
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as output:
                for line in lines:
                    output.write(line)
            stage = "file_durability"
            full_sync(raw)
        stage = "publication"
        os.replace(pending, path)
        stage = "directory_durability"
        published_sync(root, path)
    except OSError as exc:
        raise CoordinatorError("ARCHIVE_DURABILITY", "Archive publication did not become durable",
                               {"stage": stage, "errno": exc.errno}, True) from exc
    return {"path": str(path), "sha256": file_digest(path), "bytes": path.stat().st_size}


def identity(row):
    data = json.loads(row["data"])
    return row["kind"], row["subject"], data.get("method", "") if row["kind"] == "native" else ""


def coverage_add(intervals, row):
    kind, subject, method = identity(row)
    if intervals and intervals[-1][:3] == [kind, subject, method] and intervals[-1][4] + 1 == row["seq"]:
        intervals[-1][4] = row["seq"]
    else:
        intervals.append([kind, subject, method, row["seq"], row["seq"]])


def verify(root, manifest, *, after=0, through=None, limit=0, kind=None, method=None):
    """Validate the entire promise, retaining only the requested page in memory.

    Legacy v1 files kept decoded data; v2 keeps the original SQLite TEXT. v1 is
    canonically re-encoded for its old writer's representation. Coverage is
    derived from actual rows, never from a manifest's first/last envelope.
    """
    path = location(root, manifest["path"])
    try:
        if file_digest(path) != manifest["sha256"]:
            raise CoordinatorError("ARCHIVE_DIGEST", "Archive checksum differs", {"archive": manifest.get("id")})
    except FileNotFoundError as exc:
        raise CoordinatorError("ARCHIVE_MISSING", "A promised archive is missing", {"archive": manifest.get("id")}) from exc
    encoding = manifest.get("encoding", "gzip-jsonl-v1")
    if encoding not in (ENCODING, "gzip-jsonl-v1"):
        raise CoordinatorError("ARCHIVE_ENCODING", "Unknown archive encoding", {"encoding": encoding})
    subject = manifest.get("subject", manifest.get("listener"))
    intervals, kept, completions, count, first, last, logical = [], [], [], 0, None, 0, 0
    content = hashlib.sha256()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                if encoding == "gzip-jsonl-v1":
                    row["data"] = encoded(row["data"])
                seq = row["seq"]
                if type(seq) is not int or seq <= last or row["subject"] != subject or row["kind"] not in KINDS:
                    raise CoordinatorError("ARCHIVE_COVERAGE", "Archive row scope or ordering differs")
                if manifest.get("event_kind", row["kind"]) != row["kind"]:
                    raise CoordinatorError("ARCHIVE_COVERAGE", "Archive event kind differs")
                if not isinstance(row["data"], str):
                    raise ValueError("raw data is not text")
                content.update((encoded(row) + "\n").encode())
                row_kind, _, row_method = identity(row)
                coverage_add(intervals, row)
                if row_kind == "native" and row_method == "item/completed":
                    params = json.loads(row["data"]).get("params", {})
                    if params.get("threadId") and params.get("turnId") and params.get("item", {}).get("id"):
                        completions.append((subject, params["threadId"], params["turnId"], params["item"]["id"], seq))
                count += 1
                first = seq if first is None else first
                last = seq
                logical += len(row["data"].encode())
                if (len(kept) < limit and seq > after and (through is None or seq <= through)
                        and (kind is None or kind == row_kind) and (method is None or method == row_method)):
                    kept.append(row)
    except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
        raise CoordinatorError("ARCHIVE_ENCODING", "Archive cannot be decoded", {"archive": manifest.get("id")}) from exc
    if (count != manifest["rows"] or first != manifest["first"] or last != manifest["last"]
            or "coverage" in manifest and manifest["coverage"] != intervals
            or "membership_sha256" in manifest and hashlib.sha256(encoded(intervals).encode()).hexdigest() != manifest["membership_sha256"]
            or "content_sha256" in manifest and content.hexdigest() != manifest["content_sha256"]
            or "logical_bytes" in manifest and logical != manifest["logical_bytes"]):
        raise CoordinatorError("ARCHIVE_COVERAGE", "Archive does not contain its exact promised rows",
                               {"archive": manifest.get("id")})
    return {"coverage": intervals, "data": kept, "logical_bytes": logical, "completions": completions}


def write_events(root, rows):
    intervals = []
    content = hashlib.sha256()
    for row in rows:
        coverage_add(intervals, row)
        content.update((encoded(row) + "\n").encode())
    subject, kind = rows[0]["subject"], rows[0]["kind"]
    if any(row["subject"] != subject or row["kind"] != kind for row in rows):
        raise CoordinatorError("ARCHIVE_COVERAGE", "A chunk must have one subject and event kind")
    key = content.hexdigest()
    manifest = {"id": "archive:" + key, "listener": subject, "subject": subject, "event_kind": kind,
                "methods": sorted({interval[2] for interval in intervals}), "encoding": ENCODING,
                "content_sha256": key,
                "first": rows[0]["seq"], "last": rows[-1]["seq"], "rows": len(rows),
                "membership_sha256": hashlib.sha256(encoded(intervals).encode()).hexdigest(),
                "logical_bytes": sum(len(row["data"].encode()) for row in rows)}
    path = Path(root) / "archive" / "events" / key[:2] / (key + ".jsonl.gz")
    if path.exists():
        location(root, path)
        manifest.update(path=str(path), sha256=file_digest(path), bytes=path.stat().st_size)
        # Re-adoption must finish publication after an interrupted replace.
        published_sync(root, path)
    else:
        manifest.update(publish_file(root, path, ((encoded(row) + "\n").encode() for row in rows)))
    verify(root, manifest)
    return manifest


def page_sync(root, subjects, after, limit, kind, method, through):
    subjects = sorted(set(subjects))
    if not subjects:
        return []
    slots = ",".join("?" for _ in subjects)
    where, args = "seq>? AND subject IN (" + slots + ")", [after, *subjects]
    if through is not None:
        where += " AND seq<=?"
        args.append(through)
    if kind is not None:
        where += " AND kind=?"
        args.append(kind)
    if method is not None:
        where += " AND json_extract(data,'$.method')=?"
        args.append(method)
    def next_manifest(db, end, cursor):
        # The immutable catalog is authoritative after indexing. Legacy manifests
        # remain readable before their first retention pass. Fetch one promise at
        # a time; a long-lived run cannot grow the reader's manifest cache.
        row = db.execute(
            "SELECT id,data,json_extract(data,'$.first') AS first FROM ("
            "SELECT id,manifest AS data FROM archive_catalog WHERE "
            "coalesce(json_extract(manifest,'$.subject'),json_extract(manifest,'$.listener')) IN (" + slots + ") "
            "UNION ALL SELECT r.id,r.data FROM records r WHERE kind='archive' AND parent IN (" + slots + ") "
            "AND json_extract(data,'$.state') IN ('durable','complete') "
            "AND NOT EXISTS(SELECT 1 FROM archive_catalog a WHERE a.id=r.id)) "
            "WHERE json_extract(data,'$.last')>? AND json_extract(data,'$.first')<=? "
            "AND (json_extract(data,'$.first')>? OR (json_extract(data,'$.first')=? AND id>?)) "
            "ORDER BY first,id LIMIT 1", (*subjects, *subjects, after, end, cursor[0], cursor[0], cursor[1])).fetchone()
        return dict(json.loads(row["data"]), id=row["id"]) if row else None

    db = readonly(root)
    try:
        db.execute("BEGIN")
        hot = [dict(row) for row in db.execute("SELECT * FROM events WHERE " + where + " ORDER BY seq LIMIT ?", (*args, limit))]
        end = highwater_db(db) if through is None else through
        if len(hot) == limit:
            end = min(end, hot[-1]["seq"])
        manifest = next_manifest(db, end, (0, ""))
    finally:
        db.close()
    merged = {row["seq"]: row for row in hot}
    while manifest:
        if len(merged) >= limit and manifest["first"] > sorted(merged)[limit - 1]:
            break
        if kind is None or manifest.get("event_kind", "native") == kind:
            result = verify(root, manifest, after=after, through=end, limit=limit, kind=kind, method=method)
            for row in result["data"]:
                previous = merged.get(row["seq"])
                if previous is not None and previous != row:
                    raise CoordinatorError("ARCHIVE_CONFLICT", "Hot and cold evidence disagree", {"sequence": row["seq"]})
                merged[row["seq"]] = row
            merged = {seq: merged[seq] for seq in sorted(merged)[:limit]}
        cursor = (manifest["first"], manifest["id"])
        # Promises are append-only. Any promise inserted behind this cursor can
        # only cover rows hot in our initial snapshot, already retained above up
        # to this page's boundary. No read transaction spans archive I/O.
        db = readonly(root)
        try:
            manifest = next_manifest(db, end, cursor)
        finally:
            db.close()
    return [merged[seq] for seq in sorted(merged)[:limit]]


def highwater_db(db):
    row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
    return row[0] if row else 0


async def page(store, subjects, after=0, limit=100, *, kind=None, method=None, through=None):
    return await finish_thread(page_sync, store.root, subjects, after, limit, kind, method, through)


async def events(store, subjects, after=0, *, kind=None, method=None, through=None):
    while True:
        rows = await page(store, subjects, after, 25, kind=kind, method=method, through=through)
        if not rows:
            return
        for row in rows:
            yield row
        after = rows[-1]["seq"]
        await asyncio.sleep(0)


async def exact(store, subject, sequence):
    rows = await page(store, [subject], sequence - 1, 1, through=sequence)
    if not rows or rows[0]["seq"] != sequence:
        raise CoordinatorError("ARCHIVE_COVERAGE", "Promised native evidence is unavailable", {"sequence": sequence})
    return rows[0]


def object_reference(root, value):
    raw = encoded(value).encode()
    key = hashlib.sha256(raw).hexdigest()
    path = Path(root) / "archive" / "dependencies" / key[:2] / (key + ".json.gz")
    return raw, {"path": str(path), "sha256": "0" * 64, "bytes": len(raw), "form": "dependency_object",
                 "content_sha256": key, "logical_bytes": len(raw), "encoding": "gzip-json-v1"}


def write_object(root, value):
    raw, ref = object_reference(root, value)
    path = location(root, ref["path"])
    if path.exists():
        ref.update(sha256=file_digest(path), bytes=path.stat().st_size)
        published_sync(root, path)
    else:
        ref.update(publish_file(root, path, [raw]))
    if read_object(root, ref) != value:
        raise CoordinatorError("DEPENDENCY_DIGEST", "Dependency object round trip differs")
    return ref


def read_object(root, ref):
    try:
        path = location(root, ref["path"])
        if ref.get("encoding") != "gzip-json-v1":
            raise CoordinatorError("DEPENDENCY_ENCODING", "Unknown dependency object encoding")
        if file_digest(path) != ref["sha256"]:
            raise CoordinatorError("DEPENDENCY_DIGEST", "Dependency object checksum differs")
        with gzip.open(path, "rb") as source:
            raw = source.read()
        if len(raw) != ref["logical_bytes"] or hashlib.sha256(raw).hexdigest() != ref["content_sha256"]:
            raise CoordinatorError("DEPENDENCY_DIGEST", "Dependency object content differs")
        return json.loads(raw)
    except FileNotFoundError as exc:
        raise CoordinatorError("DEPENDENCY_MISSING", "Dependency object is missing") from exc
    except (ValueError, KeyError, OSError, EOFError) as exc:
        raise CoordinatorError("DEPENDENCY_ENCODING", "Dependency object cannot be decoded") from exc
