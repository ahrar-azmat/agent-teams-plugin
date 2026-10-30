"""Explicit outage conversion and pre-Store crash continuation.

The raw SQLite functions do not construct Store or import process machinery.
The small admission guard is also imported by Python 3.9 lead hooks.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import heapq
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid

from .client import CoordinatorError


JOURNAL_LIMIT = 64 << 20
RESERVE = 15 << 30
TERMINAL = {"complete", "resumed", "refused", "failed_none", "aborted"}


def record(root):
    from .runtime import private_json
    try:
        return private_json(Path(root) / "maintenance.json")
    except FileNotFoundError:
        return {}


def sync(fd):
    os.fsync(fd)
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def save(root, value):
    """Intent and completion survive loss of the process and of host power."""
    root = Path(root)
    fd, name = tempfile.mkstemp(prefix=".maintenance-", dir=root)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            sync(stream.fileno())
        os.replace(name, root / "maintenance.json")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            sync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def exclusive(root, name, timeout=0):
    fd = os.open(Path(root) / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CoordinatorError("STATE_PERMISSIONS", "Unsafe maintenance lock", {"lock": name})
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoordinatorError("MAINTENANCE_BUSY", "Maintenance exclusion is owned", {"lock": name}, True)
                time.sleep(.05)
        yield
    finally:
        os.close(fd)


def admit_start(root):
    """An active job is determined by flock ownership, never a PID or age."""
    from .client import _held
    if _held(Path(root) / "maintenance.lock"):
        raise CoordinatorError("MAINTENANCE_ACTIVE", "An explicit outage maintenance job owns this state", operational=True)


def save_current(root, value):
    """Resume runs outside exclusion; never replace a newer attempt's record."""
    try:
        with exclusive(root, "maintenance.lock"):
            if record(root).get("attempt") == value["attempt"]:
                save(root, value)
    except CoordinatorError as exc:
        if exc.code != "MAINTENANCE_BUSY":
            raise


def file_space(path):
    try:
        info = Path(path).stat()
        return {"length_bytes": info.st_size, "allocated_bytes": info.st_blocks * 512}
    except FileNotFoundError:
        return {"length_bytes": 0, "allocated_bytes": 0}


def shape(db, path):
    values = {name: db.execute("PRAGMA " + name).fetchone()[0]
              for name in ("auto_vacuum", "page_count", "freelist_count", "page_size")}
    return dict(values, database=file_space(path), wal=file_space(str(path) + "-wal"),
                free_bytes=shutil.disk_usage(Path(path).parent).free, observed=time.time())


def settings(db):
    for key, value in (("journal_mode", "WAL"), ("synchronous", "FULL"), ("fullfsync", "ON"),
                       ("checkpoint_fullfsync", "ON"), ("journal_size_limit", str(JOURNAL_LIMIT)),
                       ("busy_timeout", "0"), ("wal_autocheckpoint", "0")):
        db.execute("PRAGMA " + key + "=" + value).fetchall()
    result = {key: db.execute("PRAGMA " + key).fetchone()[0] for key in
              ("journal_mode", "synchronous", "fullfsync", "checkpoint_fullfsync", "journal_size_limit", "wal_autocheckpoint")}
    if result != dict(journal_mode="wal", synchronous=2, fullfsync=1, checkpoint_fullfsync=1,
                      journal_size_limit=JOURNAL_LIMIT, wal_autocheckpoint=0):
        raise CoordinatorError("MAINTENANCE_DURABILITY", "SQLite durability readback differs", result, True)
    return result


def logical(db):
    """Stream all logical rows; include revision order used by provenance readers.

    No payload cap or Store construction. Include implicit rowids too: VACUUM
    may renumber them, and provenance and legacy recovery consume their order.
    """
    result = {}
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        columns = db.execute("PRAGMA table_info(" + quoted + ")").fetchall()
        keys = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]
        order = ",".join('"' + k.replace('"', '""') + '"' for k in keys) if keys else "rowid"
        if table == "revisions":
            order = "rowid"
        digest, count = hashlib.sha256(), 0
        for row in db.execute("SELECT rowid,* FROM " + quoted + " ORDER BY " + order):
            encoded = json.dumps(list(row), ensure_ascii=True, separators=(",", ":"),
                                 default=lambda b: {"blob_hex": b.hex()}).encode()
            digest.update(str(len(encoded)).encode() + b":" + encoded)
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    if "events" in tables:
        result["event_kinds"] = [list(r) for r in db.execute("SELECT kind,count(*),min(seq),max(seq) FROM events GROUP BY kind ORDER BY kind")]
        result["highwater"] = db.execute("SELECT coalesce(max(seq),0) FROM sqlite_sequence WHERE name='events'").fetchone()[0]
    return result


def merged_samples(root, db, selection=None):
    """Bounded hot and cold samples; the complete logical digest stays uncapped."""
    from . import archive
    began = time.monotonic()
    if selection is None:
        rank = lambda row: (hashlib.sha256(row[0].encode()).digest(), row[0])
        hot = heapq.nsmallest(256, db.execute("SELECT subject,min(seq),max(seq) FROM events "
                                            "WHERE subject IS NOT NULL GROUP BY subject"), key=rank)
        cold = heapq.nsmallest(256, db.execute(
            "SELECT id,manifest FROM archive_catalog UNION ALL SELECT r.id,r.data FROM records r "
            "WHERE kind='archive' AND json_extract(data,'$.state') IN ('durable','complete') "
            "AND NOT EXISTS(SELECT 1 FROM archive_catalog a WHERE a.id=r.id)"), key=rank)
        requests = {(subject, seq) for subject, first, last in hot for seq in (first, last)}
        for _, raw in cold:
            manifest = json.loads(raw)
            subject = manifest.get("subject", manifest.get("listener"))
            requests.update((subject, manifest[key]) for key in ("first", "last"))
        selection = {"hot_subjects": len(hot), "archive_manifests": len(cold),
                     "requests": [list(row) for row in sorted(requests)]}
    if not isinstance(selection, dict) or "requests" not in selection:
        raise CoordinatorError("MAINTENANCE_EVIDENCE", "Maintenance sample selection is missing", operational=True)
    result = {key: selection[key] for key in ("hot_subjects", "archive_manifests", "requests")}
    result["digests"] = []
    for subject, sequence in result["requests"]:
        rows = archive.page_sync(root, [subject], sequence - 1, 1, None, None, sequence)
        if not rows or rows[0]["seq"] != sequence:
            raise CoordinatorError("MAINTENANCE_EVIDENCE", "Merged evidence is missing", {"sequence": sequence}, True)
        result["digests"].append(hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
    result.update(calls=len(result["requests"]), seconds=time.monotonic() - began)
    return result


def capacity(path, temp):
    size = Path(path).stat().st_size
    volumes = {}
    # Reserve twice the original size on each possible volume. When shared,
    # count SQLite's documented total allowance once, not twice.
    for directory in (Path(path).parent, Path(temp)):
        device = directory.stat().st_dev
        volumes[str(device)] = {"path": str(directory), "free_bytes": shutil.disk_usage(directory).free,
                                "required_bytes": 2 * size + RESERVE}
    if any(v["free_bytes"] < v["required_bytes"] for v in volumes.values()):
        raise CoordinatorError("MAINTENANCE_SPACE", "Insufficient database/temporary volume capacity", volumes, True)
    return volumes


def checkpoint(db, timeout=30):
    began = time.monotonic()
    previous = db.execute("PRAGMA busy_timeout").fetchone()[0]
    attempts = 0
    try:
        db.execute("PRAGMA busy_timeout=250")
        while True:
            attempts += 1
            result = list(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
            details = {"result": result, "seconds": time.monotonic() - began, "attempts": attempts,
                       "retried": attempts > 1, "timeout_seconds": timeout}
            if result == [0, 0, 0]:
                return details
            if time.monotonic() - began >= timeout:
                raise CoordinatorError("MAINTENANCE_CHECKPOINT", "Checkpoint incomplete; preserve WAL and retry verification",
                                       details, True)
            time.sleep(.05)
    finally:
        db.execute("PRAGMA busy_timeout=" + str(previous))


def convert(path, *, baseline=None, allow_vacuum=False, progress=None):
    """Raw production conversion, also used on the page-for-page measurement copy.

    Caller owns exclusion. Only an explicit maintenance invocation passes
    allow_vacuum=True; crash continuation never does. No process machinery.
    """
    path = Path(path)
    progress = progress or (lambda phase, value: None)
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    try:
        result = {"sqlite": sqlite3.sqlite_version, "before": shape(db, path)}
        result["settings"] = settings(db)
        began = time.monotonic()
        baseline = logical(db) if baseline is None else baseline
        result["baseline_seconds"] = time.monotonic() - began
        progress("baseline", baseline)
        mode = db.execute("PRAGMA auto_vacuum").fetchone()[0]
        result["vacuum_seconds"], result["vacuum_cpu_seconds"] = 0.0, 0.0
        if mode != 2:
            if not allow_vacuum:
                raise CoordinatorError("MAINTENANCE_REQUIRED", "Explicit outage maintenance is required; startup never rebuilds")
            progress("vacuum_started", {})
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            began, cpu = time.monotonic(), time.process_time()
            db.execute("VACUUM").fetchall()
            result.update(vacuum_seconds=time.monotonic() - began, vacuum_cpu_seconds=time.process_time() - cpu)
        if db.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
            raise CoordinatorError("MAINTENANCE_MODE", "Incremental conversion did not persist", operational=True)
        result["converted"] = shape(db, path)
        progress("converted", result)
        result["checkpoint"] = checkpoint(db)
        progress("checkpointed", result)
        began = time.monotonic()
        integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
        if integrity != ["ok"] or logical(db) != baseline:
            raise CoordinatorError("MAINTENANCE_VERIFICATION", "Integrity or logical evidence changed", {"integrity": integrity}, True)
        result["verification_seconds"] = time.monotonic() - began
        result["integrity"] = integrity
        result["after"] = shape(db, path)
        return result
    finally:
        db.close()


@contextmanager
def temporary_store(root):
    """Scope SQLite's process-global directory before any daemon threads start."""
    with tempfile.TemporaryDirectory(prefix="maintenance-temp-", dir=root) as temp:
        previous = ""
        try:
            db = sqlite3.connect(":memory:")
            try:
                prior = db.execute("PRAGMA temp_store_directory").fetchone()
                previous = prior[0] if prior else ""
                db.execute("PRAGMA temp_store_directory='" + temp.replace("'", "''") + "'")
                if db.execute("PRAGMA temp_store_directory").fetchone() != (temp,):
                    raise CoordinatorError("MAINTENANCE_TEMP", "SQLite temporary directory readback differs", operational=True)
            finally:
                db.close()
            yield temp
        finally:
            db = sqlite3.connect(":memory:")
            try:
                db.execute("PRAGMA temp_store_directory='" + previous.replace("'", "''") + "'")
            finally:
                db.close()


def finish_job(root, value, allow_vacuum=False):
    path = root / "ledger.sqlite"
    began = time.monotonic()
    with temporary_store(root) as temp:
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
        try:
            value["capacity"] = capacity(path, temp)
            if "samples" not in value:
                value["samples"] = merged_samples(root, db)
                save(root, value)
        finally:
            db.close()
        def progress(phase, data):
            value.update(phase=phase, updated=time.time())
            if phase == "baseline":
                value["baseline"] = data
            else:
                value["result"] = data
            save(root, value)
        result = convert(path, baseline=value.get("baseline"), allow_vacuum=allow_vacuum, progress=progress)
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            verified = merged_samples(root, db, value["samples"])
            if verified["digests"] != value["samples"]["digests"]:
                raise CoordinatorError("MAINTENANCE_EVIDENCE", "Merged archive evidence changed", operational=True)
        finally:
            db.close()
        result["samples"] = {key: verified[key] for key in ("hot_subjects", "archive_manifests", "calls")}
        result["samples"].update(before_seconds=value["samples"]["seconds"], after_seconds=verified["seconds"])
        result["storage_seconds"] = time.monotonic() - began
        value.update(phase="complete", result=result, completed=time.time(), error=None)
        save(root, value)
    return value


def continue_start(root):
    """Called with worker.lock held, before Store, under the owning supervisor.

    Worker exclusion plus the job lock excludes every ledger writer. Do not
    reacquire upgrade/supervisor locks already held by our starting ancestors.
    """
    admit_start(root)
    value = record(root)
    if not value or value.get("phase") in TERMINAL:
        return
    with exclusive(root, "maintenance.lock"):
        db = sqlite3.connect((Path(root) / "ledger.sqlite").as_uri() + "?mode=rw", uri=True, isolation_level=None)
        try:
            mode = db.execute("PRAGMA auto_vacuum").fetchone()[0]
        finally:
            db.close()
        if value.get("phase") in ("intent", "baseline") and value.get("initial_mode") == mode:
            # The durable vacuum_started boundary precedes any conversion.
            # An unchanged attempt must not require evidence it never captured.
            value.update(phase="aborted", updated=time.time())
            save(root, value)
            return
        if mode != 2:
            value.update(phase="failed_none", error="Explicit outage maintenance required", updated=time.time())
            save(root, value)
            return
        if "baseline" not in value:
            raise CoordinatorError("MAINTENANCE_EVIDENCE", "Incomplete conversion lacks its durable baseline", operational=True)
        finish_job(Path(root), value)


def status(root):
    from .client import _held, validate_state
    root = validate_state(root)
    result = {"job": record(root), "active": _held(root / "maintenance.lock")}
    path = root / "ledger.sqlite"
    if path.exists():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            result["ledger"] = shape(db, path)
        finally:
            db.close()
    return result


def reclaim(root, outage_announced=False, lead_work_paused=False):
    if not outage_announced or not lead_work_paused:
        raise CoordinatorError("OUTAGE_REQUIRED", "Announce the outage and pause all lead work before invoking maintenance")
    from .client import Client, validate_state
    from .owner_journal import control_state
    root = validate_state(root)
    if not (root / "ledger.sqlite").is_file():
        raise CoordinatorError("MAINTENANCE_LEDGER", "No existing ledger to convert")
    client = Client(root, timeout=180)
    with exclusive(root, "maintenance.lock"):
        prior = record(root)
        # Retry a committed conversion with its original baseline; never replace
        # missing verification evidence with a new success baseline.
        pending = prior and prior.get("phase") not in TERMINAL
        value = prior if pending else {"attempt": uuid.uuid4().hex, "started": time.time(), "phase": "intent",
                    "outage_announced": True, "lead_work_paused": True,
                    "previously_inhibited": control_state(root)["inhibited"]}
        if not pending:
            db = sqlite3.connect((root / "ledger.sqlite").as_uri() + "?mode=ro", uri=True)
            try:
                value["initial_mode"] = db.execute("PRAGMA auto_vacuum").fetchone()[0]
            finally:
                db.close()
        save(root, value)
        try:
            with ExitStack() as locks:
                locks.enter_context(exclusive(root, "upgrade.lock", 30))
                try:
                    serving = client.call("daemon/status", {"brief": True})
                except (FileNotFoundError, ConnectionRefusedError):
                    serving = None
                if serving:
                    if not serving.get("boot") or "draining-stop" not in serving.get("capabilities", []):
                        raise CoordinatorError("MAINTENANCE_DRAIN", "Serving daemon cannot provide an identity-checked drain")
                    client.call("daemon/stop", {"expect": {"pid": serving["pid"], "boot": serving["boot"]}, "park": True})
                locks.enter_context(exclusive(root, "supervisor.lock", 180))
                locks.enter_context(exclusive(root, "worker.lock", 180))
                finish_job(root, value, allow_vacuum=True)
        except BaseException as exc:
            # Refusal before stopping leaves the serving daemon and its prior
            # inhibition unchanged. No conversion can precede the baseline;
            # later failures retain their original verification evidence.
            if getattr(exc, "code", None) in ("NOT_QUIESCENT", "MAINTENANCE_DRAIN"):
                value["phase"] = "refused"
            elif value.get("phase") == "intent" and "initial_mode" in value:
                value["phase"] = "aborted"
            value.update(error={"code": getattr(exc, "code", type(exc).__name__)}, updated=time.time())
            save(root, value)
            raise
    # Release every process/job lock before resume, whose early guard sees the
    # durable completion. A restart failure must not misreport conversion failure.
    try:
        ready = client.settled(client.resume(), 180)
        if not ready.get("ready"):
            raise CoordinatorError("DAEMON_START", "Resumed daemon did not become ready", operational=True)
        value.update(phase="resumed", readiness={"ready": ready.get("ready"), "pid": ready.get("pid")})
    except Exception as exc:
        value["readiness"] = {"ready": False, "error": getattr(exc, "code", type(exc).__name__)}
        save_current(root, value)
        raise CoordinatorError("MAINTENANCE_RESUME", "Ledger conversion completed; daemon resumption failed",
                               {"attempt": value["attempt"], "readiness": value["readiness"]}, True) from exc
    save_current(root, value)
    return value
