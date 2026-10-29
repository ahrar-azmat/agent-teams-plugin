"""Short SQLite transactions and immutable, fsynced receipt objects.

Only the daemon event-loop thread accesses SQLite. Expensive work is done before
calling these methods. Historical events and UNKNOWN records are append-only.
"""
from collections import Counter
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
from . import native, telemetry


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
# The event loop reads these few records instead of every one of their kind (10-29, live: 3,783 listeners, 47.3 MB;
# 5,000 messages; 110 runs, 11.4 MB). Each writer hook finds its app-server by session among the live listeners (all
# decoded: 278-483 ms a hook); maintenance probes the connected ones and retries the failed ones each second (47-58 and
# 52 ms), delivers the writers' mail (19-21 ms), and keeps a checkout's compatibility lease while one of its runs is
# unfinished (17-65 ms).
LIVE_LISTENER = "kind='listener' AND json_extract(data,'$.state') IN ('connected','disconnected')"
FAILED_LISTENER = "kind='listener' AND json_extract(data,'$.recovery_state')='failed'"
WRITER_MAIL = ("kind='message' AND json_extract(data,'$.state') NOT IN ('entered_context','acknowledged') AND "
               "json_extract(data,'$.recipient')!='lead' AND substr(json_extract(data,'$.recipient'),1,13)!='lead_mailbox_'")
UNFINISHED_RUN = "kind='run' AND json_extract(data,'$.state') NOT IN ('completed','failed','interrupted','blocked','refused','cancelled')"
# Each start imports the lead's file edits left open (lead_calls.recover): it decoded every operation to find them
# (10-29, a copy of the user's ledger: 23,283 records, 1,772 MB, 10.7 s of the event loop for 341 lead edits).
LEAD_EDIT_OPERATION = "kind='operation' AND json_extract(data,'$.kind')='lead-edit'"

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

# Eligibility is queried between cooperative retention batches. Index only the
# blockers, with their scope in the index: historic tool output may be gigabytes
# and must never be decoded to answer an existence question. SQLite maintains
# these predicates in the same transaction as each record mutation.
BLOCKERS = {
    "operation": "kind='operation' AND (json_extract(data,'$.state') NOT IN ('verified','reconciled','resolved','denied') OR json_extract(data,'$.executing') IS NOT NULL)",
    "quarantine": "kind='operation' AND json_extract(data,'$.state')='quarantined'",
    "dynamic": "kind='dynamic' AND json_extract(data,'$.state')='pending'",
    "obligation": "kind='native_obligation' AND json_extract(data,'$.state') IN ('pending','failed')",
    "mail": "kind='message' AND json_extract(data,'$.state') NOT IN ('entered_context','acknowledged') AND coalesce(json_extract(data,'$.delivery'),'') NOT IN ('cancelled','turn_ended')",
    "delivery": "kind='message' AND json_extract(data,'$.state') NOT IN ('entered_context','acknowledged') AND json_extract(data,'$.delivery') IN ('submitting','submitted','uncertain')",
}


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identities(*groups):
    """The (pid, birth) of each process record in these lists; a list may be None."""
    return frozenset((entry["pid"], entry["birth"]) for group in groups for entry in group or ())


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        telemetry.fsync(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, root):
        self.status_epoch = 0
        # Writes per record kind in this process: a projection read from records of some kinds is computed again only
        # once one of them was written (Dormancy.status). A write rolled back still counts: one more computation.
        self.puts = Counter()
        # parent -> writes of the records under it since a backoff began to watch it (a key here): a workflow phase's
        # settlement refused is tried again once a record of its unit or its run changes (Writer.settle_due).
        self.watched = {}
        self._listener_processes = None  # listener id -> its services' and helpers' identities (listener_processes)
        self._listener_written = False  # a listener was written in the open transaction
        self.root = validate_state(root, create=True)
        self._validated_blobs = {}
        self._path_blobs = {}  # advisory source stamps; never persisted across a restart
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
        -- Store.all(kind, parent), the migrations' included: a parent's records of one kind in id order. With no
        -- statistics SQLite chose record_kind for it, whose order needs no sort, and read every record of the kind to
        -- test its parent (10-29, live: 1.0-1.7 s cold for a unit's operations, custody or dynamic calls; 6-40 ms).
        CREATE INDEX IF NOT EXISTS record_children ON records(parent, kind, id);
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
                self.db.execute("CREATE INDEX IF NOT EXISTS quiet_" + kind + " ON records(parent,id) WHERE " + condition)
            for kind, condition in BLOCKERS.items():
                scope = "json_extract(data,'$.recipient')" if kind == "mail" else "json_extract(data,'$.unit')" if kind == "delivery" else "parent"
                self.db.execute("CREATE INDEX IF NOT EXISTS blocker_" + kind + " ON records(" + scope +
                    (",json_extract(data,'$.native.listener')" if kind == "operation" else "") + ",id) WHERE " + condition)
            # Consumer lookups must not deserialize every historical unit or
            # session before applying their checkout/session filter.
            for name, expression, condition in (
                ("lead_session_id", "json_extract(data,'$.session_id'),id", "kind='lead_session'"),
                ("participant_checkout", "json_extract(data,'$.checkout'),parent", "kind='participant'"),
                ("open_publication", "parent", "kind='publication' AND json_extract(data,'$.phase') NOT IN ('complete','abandoned')"),
                ("unfinished_unit", "id", "kind='unit' AND json_extract(data,'$.publication_state') IN ('prepared','partially_published')"),
                ("unfinished_unit_v2", "id", "kind='unit' AND json_extract(data,'$.publication_state') IN ('prepared','publishing','partially_published')"),
                ("message_unit", "json_extract(data,'$.unit'),id", "kind='message'"),
                # run/list counts each unit's unread mail: through message_unit alone it read every message of the
                # unit (10-29: 374 ms of one 0.82 s listing, 91 units).
                ("message_unread", "json_extract(data,'$.unit'),id", "kind='message' AND json_extract(data,'$.state')!='acknowledged'"),
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
                ("live_listener", "json_extract(data,'$.root_thread'),id", LIVE_LISTENER),
                ("failed_listener", "id", FAILED_LISTENER),
                ("writer_mail", "id", WRITER_MAIL),
                ("unfinished_run", "id", UNFINISHED_RUN),
                ("lead_edit_operation", "id", LEAD_EDIT_OPERATION),
            ):
                self.db.execute("CREATE INDEX IF NOT EXISTS " + name + " ON records(" + expression + ") WHERE " + condition)
            self.db.execute("CREATE INDEX IF NOT EXISTS unknown_checkout ON unknowns(checkout)")
            # participants.surface sums the revisions of a unit's claims and custody for pollers, and every command
            # leaves a custody record. `revision` follows `data` in a row: without this covering index each sum read
            # all of those records whole (10-29: 17,792 custody records; 0.73-1.85 s for the 91 units of one run/list,
            # 41.5 ms with it; built in 4.8 s on a copy of the 15 GiB ledger). Partial, because each save changes the
            # revision, so an index over all records is updated at every save (on the copy, 2,000 custody saves wrote
            # 1.3 more WAL pages each with it), and 80% of the copy's 15.4 M saves were of other kinds (listeners alone
            # 10.3 M).
            self.db.execute("CREATE INDEX IF NOT EXISTS claim_custody_revision ON records(parent,kind,revision) "
                            "WHERE kind IN ('claim','custody')")
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
        # Native evidence may leave SQLite only after a durable archive record.
        # Every other event, and every event update, remains immutable. Retention
        # validates the file digest before each resumed deletion sequence.
        archive_delete = """CREATE TRIGGER immutable_events_delete BEFORE DELETE ON events
        WHEN OLD.kind!='native' OR NOT EXISTS (
          SELECT 1 FROM records WHERE kind='archive' AND parent=OLD.subject
          AND json_extract(data,'$.state') IN ('durable','complete')
          AND OLD.seq BETWEEN json_extract(data,'$.first') AND json_extract(data,'$.last'))
        BEGIN SELECT RAISE(ABORT,'immutable event without durable archive'); END"""
        existing = self.db.execute("SELECT sql FROM sqlite_master WHERE name='immutable_events_delete'").fetchone()
        if not existing or existing[0] != archive_delete:
            with self.transaction():
                self.db.execute("DROP TRIGGER IF EXISTS immutable_events_delete")
                self.db.execute(archive_delete)
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
        self._listener_written = False
        try:
            yield
            if measured:
                with telemetry.span("sql_commit", False):
                    self.db.execute("COMMIT")
            else:
                self.db.execute("COMMIT")
        except BaseException as exc:
            if self._listener_written:
                self._listener_processes = None  # what this transaction's listeners kept is read again
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
        # A save of the bytes a record already holds writes nothing: no WAL frames, no new revision, and the caches
        # kept until a write (status_epoch, puts, watched) stay. Through 2.4.21 each was written (10-29, the user's
        # ledger: 318 of 363 listener saves a minute and 96 of 103 run saves changed nothing).
        written = self.db.execute("""INSERT INTO records(id,kind,parent,data) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=records.revision+1
            WHERE records.data IS NOT excluded.data RETURNING parent""", (ident, kind, parent, encoded(data))).fetchall()
        if written:
            if written[0][0] in self.watched:
                self.watched[written[0][0]] += 1
            if kind in {"run", "unit", "actor", "listener", "checkout", "claim", "custody", "message", "claim_request",
                        "release_request", "custody_request", "participant", "publication", "publication_group", "project_revision", "resolution"}:
                self.status_epoch += 1
            self.puts[kind] += 1
        if kind == "listener":
            self._listener_written = self._listener_written or self.db.in_transaction
            if self._listener_processes is not None:
                self._listener_processes[ident] = identities(data.get("services"), data.get("helpers"))
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
        if parent is None:
            yield from self.selected("SELECT * FROM records WHERE kind=? ORDER BY id", (kind,))
        else:
            yield from self.selected("SELECT * FROM records INDEXED BY record_children WHERE parent=? AND kind=? ORDER BY id",
                                     (parent, kind))

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

    def listener_processes(self):
        """The identities, {pid, birth}, of every listener's services and helpers. Kept per listener as each listener
        is written; read from SQLite without decoding the records on the first call, and on the first call after a
        transaction that wrote a listener rolled back. Each process inventory of a command reads them: built from every
        listener record decoded, that cost 142 ms on the event loop (10-29: 3,686 records, 45 MB; this read, 45 ms)."""
        if self._listener_processes is None:
            self._listener_processes = {
                row[0]: identities(json.loads(row[1] or "[]"), json.loads(row[2] or "[]"))
                for row in self.db.execute("SELECT id, json_extract(data,'$.services'), json_extract(data,'$.helpers') "
                                           "FROM records WHERE kind='listener'")}
        return [{"pid": pid, "birth": birth} for pid, birth in frozenset().union(*self._listener_processes.values())]

    def live_operations(self, checkout):
        return self.selected("SELECT * FROM records WHERE " + LIVE_OPERATION +
                             " AND json_extract(data,'$.checkout')=? ORDER BY id", (checkout,))

    def call_operations(self, unit, call):
        return self.selected("SELECT * FROM records WHERE kind='operation' AND "
                             "json_extract(data,'$.native.call')=? AND parent=? ORDER BY id", (call, unit))

    def operations_in_flight(self, unit):
        """A unit's operations prepared, allowed or running, in id order: what a reconcile settles. Every operation of
        the unit decoded cost 0.2-3.0 s for a unit of 3,077 (10-29, a copy of the user's ledger)."""
        return self.selected("SELECT * FROM records INDEXED BY quiet_operation WHERE " + WINDOW_ACTIVE["operation"] +
                             " AND parent=? ORDER BY id", (unit,))

    def session_listeners(self, session):
        """The connected and disconnected listeners of a writer session (their root thread), in id order."""
        return self.selected("SELECT * FROM records INDEXED BY live_listener WHERE " + LIVE_LISTENER +
                             " AND json_extract(data,'$.root_thread') IS ? ORDER BY id", (session,))

    def connected_listeners(self):
        return self.selected("SELECT * FROM records INDEXED BY live_listener WHERE " + LIVE_LISTENER +
                             " AND json_extract(data,'$.state')='connected' ORDER BY id")

    def failed_listeners(self):
        return self.selected("SELECT * FROM records INDEXED BY failed_listener WHERE " + FAILED_LISTENER + " ORDER BY id")

    def unfinished_runs(self):
        return self.selected("SELECT * FROM records INDEXED BY unfinished_run WHERE " + UNFINISHED_RUN + " ORDER BY id")

    def writer_mail(self):
        """Messages not yet in their recipient's context, in id order, but for mail to `lead` or a lead's mailbox."""
        return self.selected("SELECT * FROM records INDEXED BY writer_mail WHERE " + WRITER_MAIL + " ORDER BY id")

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
                    self.verify_blob(name)
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
            if native.local_resource_failure(exc):
                raise  # descriptors, memory, storage: stops native intake (review turn 106), not a missing receipt
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        with telemetry.span("hashing", False):
            observed = hashlib.sha256(data).hexdigest()
        if before != after or observed != name:
            self._validated_blobs.pop(name, None)
            raise CoordinatorError("BLOB_CORRUPT", "Receipt hash does not match", {"blob": name}, True)
        self._validated_blobs[name] = after
        return data

    def verify_blob(self, name):
        """Check a receipt's bytes against its name and record its stamp as verified, as read_blob does, hashing it
        in 1 MiB chunks: memory one chunk, not the receipt (through 2.4.16 the first use of a receipt in a daemon,
        a publication's reuse included, read it whole)."""
        if not name or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise CoordinatorError("BLOB_ID", "Invalid receipt identifier")
        observed = hashlib.sha256()
        try:
            with (self.root / "blobs" / name).open("rb") as stream:
                before = self._blob_stamp(os.fstat(stream.fileno()))
                with telemetry.span("hashing", False):
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        observed.update(chunk)
                after = self._blob_stamp(os.fstat(stream.fileno()))
        except OSError as exc:
            if native.local_resource_failure(exc):
                raise  # descriptors, memory, storage: stops native intake (review turn 106), not a missing receipt
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        if before != after or observed.hexdigest() != name:
            self._validated_blobs.pop(name, None)
            raise CoordinatorError("BLOB_CORRUPT", "Receipt hash does not match", {"blob": name}, True)
        self._validated_blobs[name] = after

    def read_blob_range(self, name, offset, length):
        """Bytes [offset, offset + length) of a receipt, and its size, in memory bounded by the range (a read_file
        page): a receipt not verified since its file last changed (its stamp) is verified first (verify_blob), then
        only the range is read. Through 2.4.16 every page read the whole receipt, on the loop."""
        if not name or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            raise CoordinatorError("BLOB_ID", "Invalid receipt identifier")
        try:
            stamp = self._blob_stamp((self.root / "blobs" / name).stat())
        except OSError as exc:
            if native.local_resource_failure(exc):
                raise  # descriptors, memory, storage: stops native intake (review turn 106), not a missing receipt
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        if self._validated_blobs.get(name) != stamp:
            self.verify_blob(name)
        try:
            with (self.root / "blobs" / name).open("rb") as stream:
                before = self._blob_stamp(os.fstat(stream.fileno()))
                data = b""
                if offset < before[2]:  # at or past the end reads nothing: seek refuses an offset from 2**63 on
                    stream.seek(offset)
                    data = stream.read(length)
                after = os.fstat(stream.fileno())
        except OSError as exc:
            if native.local_resource_failure(exc):
                raise  # descriptors, memory, storage: stops native intake (review turn 106), not a missing receipt
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc
        if before != self._validated_blobs.get(name) or self._blob_stamp(after) != before:
            self._validated_blobs.pop(name, None)
            raise CoordinatorError("BLOB_CORRUPT", "Receipt changed while read", {"blob": name}, True)
        return data, after.st_size

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

    def unresolved_count(self, checkout):
        """len(unresolved(checkout)), without reading each UNKNOWN's context."""
        return self.db.execute("SELECT count(*) FROM unknowns u LEFT JOIN resolutions s ON s.unknown_id=u.id "
                               "WHERE u.checkout=? AND s.edit IS NULL", (checkout,)).fetchone()[0]
