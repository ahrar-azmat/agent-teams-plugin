"""Short SQLite transactions and immutable, fsynced receipt objects.

Only the daemon event-loop thread accesses SQLite. Expensive work is done before
calling these methods. Historical events and UNKNOWN records are append-only.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid

from .client import CoordinatorError, validate_state
from . import telemetry


# Unreconciled operations: admission and custody look only at these, per checkout.
LIVE_OPERATION = "kind='operation' AND json_extract(data,'$.state') IN ('prepared','allowed','running','quarantined')"


def fingerprint(pairs):
    """Count and digest of (path, revision) heads: what an opaque operation was admitted against."""
    pairs = sorted(pairs)
    text = "".join(path + "\0" + revision + "\n" for path, revision in pairs)
    return {"count": len(pairs), "sha256": hashlib.sha256(text.encode()).hexdigest()}


# Partial indexes contain only live occupancy. Finished history advances the
# durable clock instead of being reread for every lead Bash fingerprint.
LEAD_COMPLETION_PENDING = "kind='lead_hook' AND json_extract(data,'$.execution.unadmitted_completion.state')='pending'"
LEAD_BATCH_PENDING = "kind='lead_batch' AND json_extract(data,'$.state')='pending'"
LEAD_BATCH_ITEM_PENDING = "kind='lead_batch_item' AND json_extract(data,'$.state')='pending'"
LEAD_BATCH_LEGACY = "kind='lead_batch' AND coalesce(json_extract(data,'$.schema'),1)=1"

def custody_window(prefix=""):
    """Every unreleased custody reservation is an active effect window.
    Quarantine preserves exclusion until process/terminal evidence allows release.
    Use the same predicate for occupancy indexes, queries and epoch transitions.
    """
    return prefix + "kind='custody' AND json_extract(" + prefix + "data,'$.state')!='released'"


WINDOW_ACTIVE = {
    "actor": "kind='actor' AND (json_extract(data,'$.active_turn') IS NOT NULL OR json_extract(data,'$.state')='running')",
    "operation": "kind='operation' AND json_extract(data,'$.state') IN ('prepared','allowed','running')",
    "custody": custody_window(),
    # A quarantined patch native may still be writing (ledger.quarantine executing=True).
    "executing": "kind='operation' AND json_extract(data,'$.executing') IS NOT NULL",
    "lead_edit": "kind='lead_hook' AND json_extract(data,'$.execution.phase') IN ('admitting','open','settling')",
    "lead_hook": "kind='lead_hook' AND ((json_extract(data,'$.tool')='Bash' AND json_extract(data,'$.state')='observed') OR "
                 "(json_extract(data,'$.tool') IN ('Write','Edit','MultiEdit','NotebookEdit') AND json_extract(data,'$.state')='received'))",
}


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        telemetry.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, root):
        self.status_epoch = 0
        self.root = validate_state(root, create=True)
        self._validated_blobs = {}
        self._blob_publish = threading.RLock()
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
        if current not in (1, 2, 3):
            raise CoordinatorError("SCHEMA_VERSION", "Unknown ledger migration version")
        if current == 1:
            with self.transaction():
                self.db.execute("""CREATE TABLE checkout_identities(
                    identity TEXT PRIMARY KEY, checkout TEXT REFERENCES records(id),
                    state TEXT NOT NULL, members TEXT NOT NULL)""")
                self.db.execute("INSERT INTO migrations VALUES(2,?)", (time.time(),))
        if current < 3:
            # Sidecars preserve every legacy record revision and receipt binding.
            # Do not use executescript here: it may commit an open transaction.
            with self.transaction():
                from .participants import capture
                for unit in list(self.all("unit")):
                    checkout = self.get(unit["checkout"])
                    capture(self, unit["id"], checkout, unit, [], 0)
                for action in ("UPDATE", "DELETE"):
                    self.db.execute("CREATE TRIGGER immutable_project_" + action.lower() +
                                    " BEFORE " + action + " ON records WHEN OLD.kind IN ('project_revision','project_root')"
                                    " BEGIN SELECT RAISE(ABORT,'immutable project revision'); END")
                self.db.execute("INSERT INTO migrations VALUES(3,?)", (time.time(),))
        # Also installs on an earlier P1 schema-3 database without rewriting any
        # historical records. A restart invalidates its old attribution windows.
        with self.transaction():
            self.db.execute("CREATE TABLE IF NOT EXISTS mutation_clock("
                            "id INTEGER PRIMARY KEY CHECK(id=1), effects INTEGER NOT NULL, lead_hooks INTEGER NOT NULL)")
            self.db.execute("INSERT OR IGNORE INTO mutation_clock VALUES(1,0,0)")
            for action in ("INSERT", "UPDATE", "DELETE"):
                refs = ("OLD", "NEW") if action == "UPDATE" else ("OLD",) if action == "DELETE" else ("NEW",)
                effects = " OR ".join(ref + ".kind IN ('operation','custody','custody_request') OR (" + ref +
                    ".kind='actor' AND coalesce(json_extract(" + ref + ".data,'$.kind'),'')!='lead')" for ref in refs)
                hooks = " OR ".join(ref + ".kind='lead_hook'" for ref in refs)
                for column, condition in (("effects", effects), ("lead_hooks", hooks)):
                    self.db.execute("CREATE TRIGGER IF NOT EXISTS mutation_" + column + "_" + action.lower() +
                        " AFTER " + action + " ON records WHEN " + condition + " BEGIN UPDATE mutation_clock SET " +
                        column + "=" + column + "+1 WHERE id=1; END")
            for kind, condition in WINDOW_ACTIVE.items():
                self.db.execute("CREATE INDEX IF NOT EXISTS window_" + kind +
                    " ON records(" + ("json_extract(data,'$.deadline')," if kind == "lead_hook" else "") +
                    "id) WHERE " + condition)
            # Consumer lookups must not deserialize every historical unit or
            # session before applying their checkout/session filter.
            for name, expression, condition in (
                ("lead_session_id", "json_extract(data,'$.session_id'),id", "kind='lead_session'"),
                ("participant_checkout", "json_extract(data,'$.checkout'),parent", "kind='participant'"),
                ("open_publication", "parent", "kind='publication' AND json_extract(data,'$.phase') NOT IN ('complete','abandoned')"),
                ("unfinished_unit", "id", "kind='unit' AND json_extract(data,'$.publication_state') IN ('prepared','partially_published')"),
                ("unfinished_unit_v2", "id", "kind='unit' AND json_extract(data,'$.publication_state') IN ('prepared','publishing','partially_published')"),
                ("message_unit", "json_extract(data,'$.unit'),id", "kind='message'"),
                ("message_recipient", "json_extract(data,'$.recipient'),id", "kind='message'"),
                ("mailbox_session", "json_extract(data,'$.session_id'),id", "kind='lead_mailbox'"),
                # A lead's inbox reads mail to lead actors and the sessions they belong to; PostToolBatch finds a
                # lead hook's record by the host's call id.
                ("lead_actor", "id", "kind='actor' AND json_extract(data,'$.kind')='lead'"),
                ("lead_session_actor", "json_extract(data,'$.actor'),id", "kind='lead_session'"),
                ("lead_hook_call", "json_extract(data,'$.tool_use_id'),id", "kind='lead_hook'"),
                ("lead_completion_pending", "id", LEAD_COMPLETION_PENDING),
                ("lead_batch_pending", "id", LEAD_BATCH_PENDING),
                ("lead_batch_item_pending", "coalesce(json_extract(data,'$.retry_after'),0),id", LEAD_BATCH_ITEM_PENDING),
                ("lead_batch_legacy", "id", LEAD_BATCH_LEGACY),
                ("observation_pending", "json_extract(data,'$.retry_key'),id", "kind='observation' AND json_extract(data,'$.state')='pending'"),
                ("observation_terminal", "id", "kind='observation' AND json_extract(data,'$.state')!='pending'"),
                ("claim_checkout", "json_extract(data,'$.checkout'),id", "kind='claim' AND json_extract(data,'$.state')='held'"),
                # Every native notification and hook finds its operation by call id; every admission
                # checks the checkout's unreconciled operations.
                ("writer_mcp_usage", "coalesce(json_extract(data,'$.completed'),json_extract(data,'$.created'))",
                 "kind='operation' AND json_extract(data,'$.completed_item.type')='mcpToolCall' AND "
                 "json_extract(data,'$.completed_item.status')='completed'"),
                ("operation_call", "json_extract(data,'$.native.call'),id", "kind='operation'"),
                ("live_operation", "json_extract(data,'$.checkout'),id", LIVE_OPERATION),
                # A patch's admission and receipt look for custody released since it began.
                ("custody_completed", "json_extract(data,'$.checkout'),json_extract(data,'$.completed')", "kind='custody'"),
                ("visible_run", "json_extract(data,'$.created') DESC,id DESC", "kind='run' AND "
                 "(json_extract(data,'$.workflow') IS NOT NULL OR json_extract(data,'$.project_revision') IS NOT NULL)"),
            ):
                self.db.execute("CREATE INDEX IF NOT EXISTS " + name + " ON records(" + expression + ") WHERE " + condition)
            self.db.execute("CREATE INDEX IF NOT EXISTS unknown_checkout ON unknowns(checkout)")
        # Exclusion epochs detect even a lead admission that starts and ends
        # during a scan. Heads detect byte transitions separately. The trigger
        # changes no epoch for ordinary native journal/progress saves.
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS path_versions(
          checkout TEXT NOT NULL, path TEXT NOT NULL, version INTEGER NOT NULL,
          PRIMARY KEY(checkout,path));
        CREATE TRIGGER IF NOT EXISTS path_exclusion_insert AFTER INSERT ON records
        WHEN NEW.kind='lead_hook' OR (NEW.kind='operation' AND json_extract(NEW.data,'$.kind') IN ('patch','lead-edit','restore'))
        BEGIN
          INSERT INTO path_versions(checkout,path,version)
          SELECT json_extract(NEW.data,'$.checkout'), value, 1 FROM json_each(NEW.data,'$.paths') WHERE value IS NOT NULL AND json_extract(NEW.data,'$.checkout') IS NOT NULL
          ON CONFLICT(checkout,path) DO UPDATE SET version=version+1;
        END;
        CREATE TRIGGER IF NOT EXISTS path_exclusion_update AFTER UPDATE ON records
        WHEN (NEW.kind='lead_hook' OR (NEW.kind='operation' AND json_extract(NEW.data,'$.kind') IN ('patch','lead-edit','restore')))
          AND (json_extract(NEW.data,'$.state','$.paths','$.executing','$.execution.phase','$.generation','$.checkout')
               IS NOT json_extract(OLD.data,'$.state','$.paths','$.executing','$.execution.phase','$.generation','$.checkout'))
        BEGIN
          INSERT INTO path_versions(checkout,path,version)
          SELECT checkout,path,1 FROM (
            SELECT json_extract(NEW.data,'$.checkout') AS checkout,value AS path FROM json_each(NEW.data,'$.paths')
            UNION SELECT json_extract(OLD.data,'$.checkout'),value FROM json_each(OLD.data,'$.paths')
          ) WHERE checkout IS NOT NULL AND path IS NOT NULL
          ON CONFLICT(checkout,path) DO UPDATE SET version=version+1;
        END;
        """)
        # A command can change bytes and restore them before settling. Heads
        # alone cannot detect an image sampled inside that completed window.
        # Persist acquisition/release epochs in the same transaction as custody.
        # The empty path is reserved for whole-checkout custody (not a filename).
        with self.transaction():
            for action in ("INSERT", "UPDATE", "DELETE"):
                refs = ("OLD", "NEW") if action == "UPDATE" else ("OLD",) if action == "DELETE" else ("NEW",)
                condition = " OR ".join(custody_window(ref + ".") for ref in refs)
                if action == "UPDATE":
                    condition = "(" + condition + ") AND (json_extract(NEW.data,'$.state','$.checkout','$.level','$.keys','$.paths','$.excludes') IS NOT " + \
                        "json_extract(OLD.data,'$.state','$.checkout','$.level','$.keys','$.paths','$.excludes'))"
                scopes = []
                for ref in refs:
                    scopes.append("SELECT json_extract(" + ref + ".data,'$.checkout') AS checkout, value AS path "
                        "FROM json_each(CASE WHEN json_extract(" + ref + ".data,'$.level')='paths' "
                        "THEN coalesce(json_extract(" + ref + ".data,'$.keys'),json_extract(" + ref + ".data,'$.paths'),'[]') "
                        "ELSE '[\"\"]' END) WHERE " + custody_window(ref + "."))
                name = "custody_epoch_" + action.lower()
                definition = ("CREATE TRIGGER " + name +
                    " AFTER " + action + " ON records WHEN " + condition + " BEGIN "
                    "INSERT INTO path_versions(checkout,path,version) SELECT checkout,path,1 FROM (" +
                    " UNION ".join(scopes) + ") WHERE checkout IS NOT NULL AND path IS NOT NULL "
                    "ON CONFLICT(checkout,path) DO UPDATE SET version=version+1; END")
                existing = self.db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
                if not existing or existing[0] != definition:
                    # Upgrade held-only triggers atomically; unchanged starts do no
                    # schema writes and no historical custody rows are rewritten.
                    if existing:
                        self.db.execute("DROP TRIGGER " + name)
                    self.db.execute(definition)
        generation = self.get("daemon", required=False)
        self.generation = (generation or {}).get("generation", 0) + 1
        self.put("meta", "daemon", {"generation": self.generation, "pid": os.getpid(),
                                    "sqlite": sqlite3.sqlite_version, "started": time.time()})

    @contextmanager
    def transaction(self):
        measured = bool(telemetry.CURRENT.get())
        if measured:
            with telemetry.span("sql_begin", False):
                self.db.execute("BEGIN IMMEDIATE")
        else:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            if measured:
                with telemetry.span("sql_commit", False):
                    self.db.execute("COMMIT")
            else:
                self.db.execute("COMMIT")
        except BaseException as exc:
            # COMMIT can fail with the transaction still active (notably BUSY).
            # Other SQLite errors may already have rolled it back. Never leave
            # the next owner inside this transaction, or hide the original error.
            if self.db.in_transaction:
                try:
                    self.db.execute("ROLLBACK")
                except BaseException as rollback:
                    exc.add_note("Rollback also failed: " + type(rollback).__name__ + ": " + str(rollback))
            raise

    @telemetry.measured("sql_work", False)
    def event(self, kind, subject, data):
        return self.db.execute("INSERT INTO events(time,kind,subject,data) VALUES(?,?,?,?)",
                               (time.time(), kind, subject, encoded(data))).lastrowid

    @telemetry.measured("sql_work", False)
    def put(self, kind, ident, data, parent=None):
        if kind in {"run", "unit", "actor", "listener", "checkout", "claim", "custody", "message", "claim_request",
                    "release_request", "custody_request", "participant", "publication", "publication_group", "project_revision", "resolution"}:
            self.status_epoch += 1
        self.db.execute("""INSERT INTO records(id,kind,parent,data) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=records.revision+1""",
                        (ident, kind, parent, encoded(data)))
        return dict(data, id=ident)

    def get(self, ident, required=True):
        if telemetry.CURRENT.get():
            with telemetry.span("database_fetch", False):
                row = self.db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
        if row is None:
            if required:
                raise CoordinatorError("NOT_FOUND", "Record does not exist", {"id": ident})
            return None
        if telemetry.CURRENT.get():
            with telemetry.span("json_materialization", False):
                return dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])
        return dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])

    def save(self, record):
        self.put(record["_kind"], record["id"], {k: v for k, v in record.items() if not k.startswith("_") and k != "id"})

    def all(self, kind, parent=None):
        query, args = "SELECT * FROM records WHERE kind=?", [kind]
        if parent is not None:
            query += " AND parent=?"
            args.append(parent)
        yield from self.selected(query + " ORDER BY id", args)

    def selected(self, query, args=()):
        """Decode only records selected by an internal, parameterized query."""
        if not telemetry.CURRENT.get():
            for row in self.db.execute(query, args):
                yield dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])
            return
        with telemetry.span("database_fetch", False):
            cursor = self.db.execute(query, args)
        while True:
            with telemetry.span("database_fetch", False):
                row = cursor.fetchone()
            if row is None:
                return
            with telemetry.span("json_materialization", False):
                record = dict(json.loads(row["data"]), id=row["id"], _kind=row["kind"], _revision=row["revision"])
            yield record  # no measurement context remains open across a yield

    def live_operations(self, checkout):
        return self.selected("SELECT * FROM records WHERE " + LIVE_OPERATION +
                             " AND json_extract(data,'$.checkout')=? ORDER BY id", (checkout,))

    def call_operations(self, unit, call):
        return self.selected("SELECT * FROM records WHERE kind='operation' AND "
                             "json_extract(data,'$.native.call')=? AND parent=? ORDER BY id", (call, unit))

    def heads_fingerprint(self, checkout):
        return fingerprint((row["path"], row["revision"]) for row in
                           self.db.execute("SELECT path,revision FROM heads WHERE checkout=?", (checkout,)))

    def held_custody(self, checkout=None, unit=None):
        """Unreleased custody, of one checkout, one unit or all. Released custody is history: every command leaves one."""
        if checkout is None and unit is None:
            return self.selected("SELECT * FROM records INDEXED BY window_custody WHERE " + WINDOW_ACTIVE["custody"] + " ORDER BY id")
        column, value = ("json_extract(data,'$.checkout')", checkout) if unit is None else ("parent", unit)
        return self.selected("SELECT * FROM records INDEXED BY window_custody WHERE " + WINDOW_ACTIVE["custody"] +
                             " AND " + column + "=? ORDER BY id", (value,))

    def released_custody(self, checkout, since):
        """Custody of a checkout released at or after `since`; one released without a time counts too."""
        query = ("SELECT * FROM records INDEXED BY custody_completed WHERE kind='custody' AND "
                 "json_extract(data,'$.checkout')=? AND json_extract(data,'$.state')='released' AND ")
        yield from self.selected(query + "json_extract(data,'$.completed')>=? ORDER BY id", (checkout, since))
        yield from self.selected(query + "json_extract(data,'$.completed') IS NULL ORDER BY id", (checkout,))

    def lead_sessions(self, session_id):
        return self.selected("SELECT * FROM records WHERE kind='lead_session' AND "
                             "json_extract(data,'$.session_id')=? ORDER BY id", (session_id,))

    def window_state(self, ident, mine, now):
        """Global quiet occupancy plus a constant-size durable mutation stamp.

        SQL triggers advance counters in the same transaction as every record
        transition. Subtracting this hook's revision excludes only its own saves.
        """
        for kind in ("lead_hook", "lead_edit", "actor", "operation", "custody", "executing"):
            query = "SELECT id FROM records INDEXED BY window_" + kind + " WHERE " + WINDOW_ACTIVE[kind]
            args = []
            if kind == "lead_hook":
                query += " AND json_extract(data,'$.deadline')>? AND id!=?"
                args = [now, ident]
            elif kind == "actor" and mine:
                query += " AND id NOT IN (" + ",".join("?" for _ in mine) + ")"
                args = sorted(mine)
            if self.db.execute(query + " LIMIT 1", args).fetchone():
                return None
        clock = self.db.execute("SELECT effects,lead_hooks FROM mutation_clock WHERE id=1").fetchone()
        own = self.db.execute("SELECT revision FROM records WHERE id=? AND kind='lead_hook'", (ident,)).fetchone()
        return [clock["effects"], clock["lead_hooks"] - (own["revision"] if own else 0)]

    @staticmethod
    def _blob_stamp(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @telemetry.measured("hashing_blob_io")
    def blob(self, content):
        """Hash first: reusing durable content creates and syncs nothing.

        A validated blob is reusable only while its file identity and change times
        match this generation's validation. Source streams are rewound for a new
        publication; non-seekable streams are buffered without creating a file.
        """
        if isinstance(content, bytes):
            content = io.BytesIO(content)
        if not content.seekable():
            content = io.BytesIO(content.read())
        position = content.tell()
        h = hashlib.sha256()
        io_count = telemetry.IO.get()
        measured = bool(telemetry.CURRENT.get())
        for chunk in iter(lambda: content.read(1 << 20), b""):
            if io_count is not None:
                io_count["bytes"] += len(chunk)
            if measured:
                with telemetry.span("hashing", False):
                    h.update(chunk)
            else:
                h.update(chunk)
        name = h.hexdigest()
        target = self.root / "blobs" / name
        # A second publisher must not reference a renamed blob before the first
        # publisher has synced its directory. Hashing stays outside this lock.
        with self._blob_publish:
            try:
                stamp = self._blob_stamp(target.stat())
            except FileNotFoundError:
                stamp = None
            if stamp is not None:
                if self._validated_blobs.get(name) != stamp:
                    self.read_blob(name)
                return name
            content.seek(position)
            fd, pending = tempfile.mkstemp(prefix="pending-", dir=target.parent)
            try:
                written = hashlib.sha256()
                with os.fdopen(fd, "wb") as stream:
                    for chunk in iter(lambda: content.read(1 << 20), b""):
                        with telemetry.span("hashing", False):
                            written.update(chunk)
                        stream.write(chunk)
                    if written.hexdigest() != name:
                        raise CoordinatorError("UNSTABLE_READ", "Source changed while publishing receipt bytes")
                    stream.flush()
                    telemetry.fsync(stream.fileno())
                os.replace(pending, target)
                fsync_dir(target.parent)
                self._validated_blobs[name] = self._blob_stamp(target.stat())
                return name
            except BaseException:
                if os.path.exists(pending):
                    os.unlink(pending)
                raise

    def read_blob(self, name):
        if not name or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise CoordinatorError("BLOB_ID", "Invalid receipt identifier")
        try:
            with (self.root / "blobs" / name).open("rb") as stream:
                before = self._blob_stamp(os.fstat(stream.fileno()))
                data = stream.read()
                after = self._blob_stamp(os.fstat(stream.fileno()))
        except OSError as exc:
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        with telemetry.span("hashing", False):
            observed = hashlib.sha256(data).hexdigest()
        if before != after or observed != name:
            self._validated_blobs.pop(name, None)
            raise CoordinatorError("BLOB_CORRUPT", "Receipt hash does not match", {"blob": name}, True)
        self._validated_blobs[name] = after
        return data

    @telemetry.measured("heads_fetch_materialization")
    def heads(self, checkout):
        """One consistent head inventory for a full observation."""
        return {row["path"]: dict(row) for row in self.db.execute(
            "SELECT r.* FROM revisions r JOIN heads h ON h.revision=r.id WHERE h.checkout=?", (checkout,))}

    def head(self, checkout, path):
        row = self.db.execute("SELECT r.* FROM revisions r JOIN heads h ON h.revision=r.id "
                              "WHERE h.checkout=? AND h.path=?", (checkout, path)).fetchone()
        return dict(row) if row else None

    def revision(self, checkout, path, blob, mode, attribution, operation=None, unit=None):
        from .admissions import check
        check()
        previous = self.head(checkout, path)
        ident = uid("rev")
        self.db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (ident, checkout, path, previous and previous["id"], operation, unit,
                         blob, mode, attribution, time.time()))
        self.db.execute("INSERT INTO heads VALUES(?,?,?) ON CONFLICT(checkout,path) "
                        "DO UPDATE SET revision=excluded.revision", (checkout, path, ident))
        return ident

    def unknown(self, checkout, path, reason, context, revision=None):
        self.status_epoch += 1
        from .admissions import check
        check()
        ident = uid("unknown")
        self.db.execute("INSERT INTO unknowns VALUES(?,?,?,?,?,?,?)",
                        (ident, checkout, path, revision, reason, encoded(context), time.time()))
        self.event("UNKNOWN", ident, {"checkout": checkout, "path": path, "reason": reason})
        return ident

    def unresolved(self, checkout, paths=None):
        rows = self.db.execute("SELECT u.*,s.restore,s.edit FROM unknowns u LEFT JOIN resolutions s "
                               "ON s.unknown_id=u.id WHERE u.checkout=? AND s.edit IS NULL", (checkout,))
        return [dict(row) for row in rows if paths is None or row["path"] in paths or row["path"] == "*"]
