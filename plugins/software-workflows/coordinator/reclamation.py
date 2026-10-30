"""Paced free-page reclamation and a supervised, independently owned checkpoint.

PASSIVE never waits for the writer lock. Only the coordinated shutdown path
may truncate; idle time is neither a drain nor authorization to rebuild.
"""
import asyncio
import sqlite3
import threading
import time

from .client import CoordinatorError
from .locks import finish_thread
from .maintenance import JOURNAL_LIMIT, RESERVE, file_space, record, shape


HIGH = 16 << 20
LOW = 4 << 20
STEP_SECONDS = .1
LATENCY_SECONDS = .05
HEARTBEAT_SECONDS = 5
ALARM_SECONDS = 60
EXHAUSTION_SECONDS = 3600


class Checkpoint:
    """Every SQLite operation, including close, belongs to this thread."""
    def __init__(self, path):
        self.path = path
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.value = {"heartbeat": None, "progress": None, "error": None, "passes": 0, "contentions": 0}
        self.thread = threading.Thread(target=self.run, name="ledger-checkpoint", daemon=True)

    def snapshot(self):
        with self.lock:
            return dict(self.value)

    def run(self):
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=0)
            db.execute("PRAGMA busy_timeout=0")
            db.execute("PRAGMA checkpoint_fullfsync=ON")
            db.execute("PRAGMA journal_size_limit=" + str(JOURNAL_LIMIT))
            settings = {k: db.execute("PRAGMA " + k).fetchone()[0] for k in ("checkpoint_fullfsync", "journal_size_limit")}
            if settings != {"checkpoint_fullfsync": 1, "journal_size_limit": JOURNAL_LIMIT}:
                raise CoordinatorError("CHECKPOINT_DURABILITY", "Checkpoint settings did not persist")
            previous = None
            while not self.stop.is_set():
                began = time.monotonic()
                busy, log, done = db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                observation = shape(db, self.path)
                now = time.monotonic()
                valid = log >= 0 and done >= 0
                with self.lock:
                    changed = valid and (previous is None or done > previous[1] or log < previous[0])
                    if changed or valid and log <= done:
                        self.value["progress"] = now
                    self.value.update(heartbeat=now, observed=time.time(), log_frames=log, checkpointed_frames=done,
                                      pending_frames=max(0, log - done) if valid else None, busy=busy, settings=settings,
                                      seconds=now - began, max_seconds=max(self.value.get("max_seconds", 0), now - began),
                                      passes=self.value["passes"] + 1, contentions=self.value["contentions"] + int(bool(busy)),
                                      ledger=observation)
                if valid:
                    previous = (log, done)
                self.stop.wait(1)
        except BaseException as exc:
            with self.lock:
                self.value["error"] = getattr(exc, "code", type(exc).__name__)
        finally:
            if db is not None:
                db.close()


class Reclamation:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.wake = asyncio.Event()
        self.store.reclamation_wake = self.wake.set
        self.worker = None
        self.cached = {}
        self.job = record(self.store.root)
        self.fallback = self.store.db.execute("PRAGMA wal_autocheckpoint").fetchone()[0] or 1000
        self.autocheckpoint = self.fallback
        self.batch = 256
        self.fast_steps = 0
        self.active = False
        self.conditions = {}
        self.metrics = {"steps": 0, "pages_removed": 0, "transaction_max_seconds": 0.0, "restarts": 0,
                        "physical_reductions_bytes": 0, "overruns": 0}
        self.started = time.monotonic()
        self.last_step = None
        self.last_progress = self.started
        self.pressure_since = None
        self.previous_space = None
        self.disk_sample = None
        self.exhaustion = None

    def transition(self, name, active, details=None):
        if self.conditions.get(name, False) != active:
            self.conditions[name] = active
            self.store.event("reclamation/condition", None, {"condition": name, "active": active, "details": details or {}})

    def automatic(self, enabled):
        desired = self.fallback if enabled else 0
        if self.autocheckpoint != desired:
            self.store.db.execute("PRAGMA wal_autocheckpoint=" + str(desired))
            self.autocheckpoint = self.store.db.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
            if self.autocheckpoint != desired:
                raise CoordinatorError("CHECKPOINT_FALLBACK", "Writer autocheckpoint setting differs", operational=True)

    def observe(self, value):
        now = time.monotonic()
        ledger = value.get("ledger")
        if not ledger:
            return
        old = self.cached.get("ledger", {})
        self.cached = value
        if ledger.get("freelist_count") != old.get("freelist_count"):
            self.wake.set()
        allocated = sum(ledger[key]["allocated_bytes"] for key in ("database", "wal"))
        if self.previous_space is not None:
            self.metrics["physical_reductions_bytes"] += max(0, self.previous_space - allocated)
        self.previous_space = allocated
        if self.disk_sample is None:
            self.disk_sample = (now, ledger["free_bytes"])
        elif now - self.disk_sample[0] >= 60:
            elapsed, consumed = now - self.disk_sample[0], self.disk_sample[1] - ledger["free_bytes"]
            self.exhaustion = max(0, ledger["free_bytes"] - RESERVE) / (consumed / elapsed) if consumed > 0 else None
            self.disk_sample = (now, ledger["free_bytes"])
        # Feed the same hysteresis state used by retention, including while its
        # archive queue is idle. Checkpointing itself never obeys this pause.
        retention = self.daemon.retention
        pending = None if value["pending_frames"] is None else value["pending_frames"] * ledger["page_size"]
        retention.observe_pressure({"pending_bytes": pending, "allocated_bytes": ledger["wal"]["allocated_bytes"],
                                    "length_bytes": ledger["wal"]["length_bytes"], "log_frames": value["log_frames"],
                                    "checkpointed_frames": value["checkpointed_frames"], "busy": value["busy"],
                                    "page_size": ledger["page_size"], "page_count": ledger["page_count"],
                                    "freelist_pages": ledger["freelist_count"], "freelist_bytes": ledger["freelist_count"] * ledger["page_size"]})
        if retention.paused:
            self.pressure_since = self.pressure_since or now
        else:
            self.pressure_since = None
            self.wake.set() if self.active else None
        self.transition("wal_pressure", self.pressure_since is not None and now - self.pressure_since >= ALARM_SECONDS)
        self.transition("reclamation_stalled", self.active and now - self.last_progress >= ALARM_SECONDS)
        self.transition("projected_exhaustion", ledger["free_bytes"] < RESERVE or
                        self.exhaustion is not None and self.exhaustion < EXHAUSTION_SECONDS,
                        {"seconds": self.exhaustion, "free_bytes": ledger["free_bytes"]})

    async def run(self):
        delay, retry_at, healthy_since = 1, 0, None
        reclaim = self.daemon.background(self.reclaim())
        try:
            while not self.daemon.closing:
                if reclaim.done():
                    if not reclaim.cancelled():
                        reclaim.result()
                    raise CoordinatorError("RECLAMATION_STOPPED", "Reclamation task ended unexpectedly", operational=True)
                now = time.monotonic()
                if self.worker is None and now >= retry_at:
                    self.worker = Checkpoint(self.store.root / "ledger.sqlite")
                    self.worker.thread.start()
                value = self.worker.snapshot() if self.worker else {}
                alive = self.worker is not None and self.worker.thread.is_alive()
                heartbeat = value.get("heartbeat")
                healthy = alive and heartbeat is not None and now - heartbeat <= HEARTBEAT_SECONDS and not value.get("error")
                self.automatic(not healthy)
                if value.get("ledger") and value.get("observed") != self.cached.get("observed"):
                    self.observe(value)
                if healthy:
                    healthy_since = healthy_since or now
                    if now - healthy_since >= HEARTBEAT_SECONDS:
                        self.transition("checkpoint_failed", False)
                        delay = 1
                else:
                    healthy_since = None
                    if value.get("error") or heartbeat is not None or now - self.started >= HEARTBEAT_SECONDS:
                        self.transition("checkpoint_failed", True, {"error": value.get("error"), "alive": alive})
                if self.worker and not alive:
                    self.worker.thread.join()
                    self.worker = None
                    self.metrics["restarts"] += 1
                    retry_at, delay = now + delay, min(30, delay * 2)
                await asyncio.sleep(.5)
        finally:
            self.automatic(True)
            reclaim.cancel()
            await asyncio.gather(reclaim, return_exceptions=True)
            if self.worker:
                self.worker.stop.set()
                await finish_thread(self.worker.thread.join)
                self.worker = None

    def step(self, pages):
        """One ordinary FULL commit; lost reclamation never loses evidence."""
        if not isinstance(pages, int) or pages < 1:
            raise CoordinatorError("RECLAMATION_PAGES", "Incremental step must request at least one page")
        before = self.store.db.execute("PRAGMA freelist_count").fetchone()[0]
        began = time.monotonic()
        with self.store.transaction():
            self.store.db.execute("PRAGMA incremental_vacuum(" + str(pages) + ")").fetchall()
        elapsed = time.monotonic() - began
        after = self.store.db.execute("PRAGMA freelist_count").fetchone()[0]
        self.last_step = time.monotonic()
        self.metrics.update(steps=self.metrics["steps"] + 1, pages_removed=self.metrics["pages_removed"] + max(0, before - after),
                            transaction_seconds=elapsed, transaction_max_seconds=max(self.metrics["transaction_max_seconds"], elapsed))
        if after < before:
            self.last_progress = self.last_step
        if elapsed > LATENCY_SECONDS:
            self.metrics["overruns"] += 1
            self.batch = max(1, min(self.batch - 1, int(self.batch * LATENCY_SECONDS / elapsed)))
            self.fast_steps = 0
        elif elapsed < LATENCY_SECONDS / 2:
            self.fast_steps += 1
            if self.fast_steps >= 8:
                self.batch = min(256, self.batch * 2)
                self.fast_steps = 0
        else:
            self.fast_steps = 0
        return after

    async def reclaim(self):
        while not self.daemon.closing:
            self.wake.clear()
            if self.daemon.draining:
                await asyncio.sleep(1)
                continue
            mode = self.store.db.execute("PRAGMA auto_vacuum").fetchone()[0]
            size = self.store.db.execute("PRAGMA page_size").fetchone()[0]
            free = self.store.db.execute("PRAGMA freelist_count").fetchone()[0] * size
            if mode != 2 or free < LOW:
                self.active = False
            elif free > HIGH and not self.active:
                self.active, self.last_progress = True, time.monotonic()
            if self.active and not self.daemon.retention.paused:
                # Space transactions from the *end* of the previous commit.
                if self.last_step is not None:
                    await asyncio.sleep(max(0, STEP_SECONDS - (time.monotonic() - self.last_step)))
                if self.daemon.draining or self.daemon.closing or self.daemon.retention.paused:
                    continue
                try:
                    self.step(self.batch)
                    self.transition("reclamation_failed", False)
                except Exception as exc:
                    self.fast_steps = 0
                    self.transition("reclamation_failed", True, {"error": getattr(exc, "code", type(exc).__name__)})
                    await asyncio.sleep(1)
                await asyncio.sleep(0)
            else:
                await self.wake.wait()

    def status(self):
        now = time.monotonic()
        value = self.worker.snapshot() if self.worker else dict(self.cached)
        heartbeat, progress = value.pop("heartbeat", None), value.pop("progress", None)
        ledger = value.get("ledger", {})
        return dict(value, heartbeat_age_seconds=None if heartbeat is None else now - heartbeat,
                    progress_age_seconds=None if progress is None else now - progress,
                    healthy=bool(self.worker and self.worker.thread.is_alive() and heartbeat is not None and
                                 now - heartbeat <= HEARTBEAT_SECONDS and not value.get("error")),
                    freshness_seconds=None if not value.get("observed") else time.time() - value["observed"],
                    conversion_pending=ledger.get("auto_vacuum") != 2, maintenance_phase=self.job.get("phase"),
                    freelist_bytes=ledger.get("freelist_count", 0) * ledger.get("page_size", 0),
                    batch_pages=self.batch, active=self.active, writer_autocheckpoint=self.autocheckpoint,
                    fallback_autocheckpoint=self.fallback, metrics=dict(self.metrics), alarms=dict(self.conditions),
                    projected_exhaustion_seconds=self.exhaustion, archive_storage=self.daemon.retention.files,
                    limits={"high_bytes": HIGH, "low_bytes": LOW, "step_seconds": STEP_SECONDS,
                            "transaction_target_seconds": LATENCY_SECONDS, "heartbeat_seconds": HEARTBEAT_SECONDS,
                            "stalled_seconds": ALARM_SECONDS, "exhaustion_seconds": EXHAUSTION_SECONDS,
                            "journal_size_limit": JOURNAL_LIMIT, "reserve_bytes": RESERVE})

    async def drained_checkpoint(self):
        """After the final shutdown write and after every background user joined."""
        path = self.store.root / "ledger.sqlite"
        def run():
            db = sqlite3.connect(path, isolation_level=None, timeout=0)
            try:
                db.execute("PRAGMA checkpoint_fullfsync=ON")
                db.execute("PRAGMA journal_size_limit=" + str(JOURNAL_LIMIT))
                return list(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
            finally:
                db.close()
        self.cached["drained_checkpoint"] = await finish_thread(run)
        self.cached["wal_after_drain"] = file_space(str(path) + "-wal")
