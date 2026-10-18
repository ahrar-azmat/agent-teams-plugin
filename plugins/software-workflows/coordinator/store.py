"""Short SQLite transactions and immutable, fsynced receipt objects.

Only the daemon event-loop thread accesses SQLite. Expensive work is done before
calling these methods. Historical events and UNKNOWN records are append-only.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from .client import CoordinatorError, validate_state


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, root):
        self.root = validate_state(root, create=True)
        for name in ("blobs", "listeners", "indexes", "schemas", "calibrations"):
            directory = self.root / name
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
                raise CoordinatorError("STATE_PERMISSIONS", "Unsafe state subdirectory", {"name": name})
        version = sqlite3.sqlite_version_info
        if not (version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51, 0) or
                (3, 44, 6) <= version < (3, 45, 0)):
            raise CoordinatorError("SQLITE_WAL_VERSION", "A SQLite release with the WAL-reset fix is required")
        self.db = sqlite3.connect(self.root / "ledger.sqlite", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS records(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL,
          parent TEXT REFERENCES records(id), data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS record_kind ON records(kind, id);
        CREATE INDEX IF NOT EXISTS record_parent ON records(parent, kind);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, kind TEXT NOT NULL,
          subject TEXT, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS event_subject_kind ON events(subject,kind,seq);
        CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
          BEGIN SELECT RAISE(ABORT,'immutable event'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
          BEGIN SELECT RAISE(ABORT,'immutable event'); END;
        CREATE TABLE IF NOT EXISTS revisions(
          id TEXT PRIMARY KEY, checkout TEXT NOT NULL REFERENCES records(id), path TEXT NOT NULL,
          previous TEXT REFERENCES revisions(id), operation TEXT REFERENCES records(id),
          unit TEXT REFERENCES records(id), blob TEXT, mode TEXT, attribution TEXT NOT NULL,
          created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS heads(
          checkout TEXT NOT NULL REFERENCES records(id), path TEXT NOT NULL,
          revision TEXT NOT NULL REFERENCES revisions(id), PRIMARY KEY(checkout,path));
        CREATE TRIGGER IF NOT EXISTS immutable_revisions_update BEFORE UPDATE ON revisions
          BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_revisions_delete BEFORE DELETE ON revisions
          BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
        CREATE TABLE IF NOT EXISTS unknowns(
          id TEXT PRIMARY KEY, checkout TEXT NOT NULL REFERENCES records(id), path TEXT NOT NULL,
          revision TEXT REFERENCES revisions(id), reason TEXT NOT NULL, context TEXT NOT NULL,
          created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS resolutions(
          unknown_id TEXT PRIMARY KEY REFERENCES unknowns(id), restore TEXT NOT NULL REFERENCES records(id),
          edit TEXT REFERENCES records(id));
        CREATE TRIGGER IF NOT EXISTS immutable_unknowns_update BEFORE UPDATE ON unknowns
          BEGIN SELECT RAISE(ABORT,'immutable UNKNOWN'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_unknowns_delete BEFORE DELETE ON unknowns
          BEGIN SELECT RAISE(ABORT,'immutable UNKNOWN'); END;
        CREATE TABLE IF NOT EXISTS idempotency(
          key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, response TEXT);
        INSERT OR IGNORE INTO migrations VALUES(1,unixepoch());
        """)
        current = self.db.execute("SELECT max(version) FROM migrations").fetchone()[0]
        if current not in (1, 2):
            raise CoordinatorError("SCHEMA_VERSION", "Unknown ledger migration version")
        if current == 1:
            with self.transaction():
                self.db.execute("""CREATE TABLE checkout_identities(
                    identity TEXT PRIMARY KEY, checkout TEXT REFERENCES records(id),
                    state TEXT NOT NULL, members TEXT NOT NULL)""")
                self.db.execute("INSERT INTO migrations VALUES(2,?)", (time.time(),))
        generation = self.get("daemon", required=False)
        self.generation = (generation or {}).get("generation", 0) + 1
        self.put("meta", "daemon", {"generation": self.generation, "pid": os.getpid(),
                                    "sqlite": sqlite3.sqlite_version, "started": time.time()})

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def event(self, kind, subject, data):
        return self.db.execute("INSERT INTO events(time,kind,subject,data) VALUES(?,?,?,?)",
                               (time.time(), kind, subject, encoded(data))).lastrowid

    def put(self, kind, ident, data, parent=None):
        self.db.execute("""INSERT INTO records(id,kind,parent,data) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=records.revision+1""",
                        (ident, kind, parent, encoded(data)))
        return dict(data, id=ident)

    def get(self, ident, required=True):
        row = self.db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
        if row is None:
            if required:
                raise CoordinatorError("NOT_FOUND", "Record does not exist", {"id": ident})
            return None
        return dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])

    def save(self, record):
        self.put(record["_kind"], record["id"], {k: v for k, v in record.items() if not k.startswith("_") and k != "id"})

    def all(self, kind, parent=None):
        query, args = "SELECT * FROM records WHERE kind=?", [kind]
        if parent is not None:
            query += " AND parent=?"
            args.append(parent)
        for row in self.db.execute(query + " ORDER BY id", args):
            yield dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])

    def blob(self, content):
        """Persist before referencing; works with bytes or a binary stream."""
        h = hashlib.sha256()
        fd, name = tempfile.mkstemp(prefix="pending-", dir=self.root / "blobs")
        try:
            with os.fdopen(fd, "wb") as stream:
                chunks = (content,) if isinstance(content, bytes) else iter(lambda: content.read(1 << 20), b"")
                for chunk in chunks:
                    h.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            target = self.root / "blobs" / h.hexdigest()
            if target.exists():
                self.read_blob(h.hexdigest())
                os.unlink(name)
            else:
                os.replace(name, target)
                fsync_dir(target.parent)
            return h.hexdigest()
        except BaseException:
            if os.path.exists(name):
                os.unlink(name)
            raise

    def read_blob(self, name):
        if not name or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise CoordinatorError("BLOB_ID", "Invalid receipt identifier")
        try:
            data = (self.root / "blobs" / name).read_bytes()
        except OSError as exc:
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        if hashlib.sha256(data).hexdigest() != name:
            raise CoordinatorError("BLOB_CORRUPT", "Receipt hash does not match", {"blob": name}, True)
        return data

    def head(self, checkout, path):
        row = self.db.execute("SELECT r.* FROM revisions r JOIN heads h ON h.revision=r.id "
                              "WHERE h.checkout=? AND h.path=?", (checkout, path)).fetchone()
        return dict(row) if row else None

    def revision(self, checkout, path, blob, mode, attribution, operation=None, unit=None):
        previous = self.head(checkout, path)
        ident = uid("rev")
        self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (ident, checkout, path, previous and previous["id"], operation, unit,
                         blob, mode, attribution, time.time()))
        self.db.execute("INSERT INTO heads VALUES(?,?,?) ON CONFLICT(checkout,path) "
                        "DO UPDATE SET revision=excluded.revision", (checkout, path, ident))
        return ident

    def unknown(self, checkout, path, reason, context, revision=None):
        ident = uid("unknown")
        self.db.execute("INSERT INTO unknowns VALUES(?,?,?,?,?,?,?)",
                        (ident, checkout, path, revision, reason, encoded(context), time.time()))
        self.event("UNKNOWN", ident, {"checkout": checkout, "path": path, "reason": reason})
        return ident

    def unresolved(self, checkout, paths=None):
        rows = self.db.execute("SELECT u.*,s.restore,s.edit FROM unknowns u LEFT JOIN resolutions s "
                               "ON s.unknown_id=u.id WHERE u.checkout=? AND s.edit IS NULL", (checkout,))
        return [dict(row) for row in rows if paths is None or row["path"] in paths or row["path"] == "*"]
