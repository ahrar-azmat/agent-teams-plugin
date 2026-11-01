"""Event-driven, paced blob conversion; SQLite remains on the daemon thread."""
import errno
import os
import threading
import time

from .blob_storage import HEADER, SUFFIX, stamp, valid_name
from .locks import finish_thread


class Paused(Exception):
    pass


class Conversion:
    def __init__(self, daemon):
        self.daemon, self.storage = daemon, daemon.store.blobs
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.dirty, self.inventory, self.seen, self.failed = set(), {}, {}, {}
        self.backlog = set()
        self.storage.changed = self.changed
        self.metrics = {"logical_bytes": 0, "raw_logical_bytes": 0, "encoded_logical_bytes": 0,
                        "raw_allocated_bytes": 0, "encoded_allocated_bytes": 0, "overlap_bytes": 0,
                        "backlog": 0, "converted": 0, "converted_bytes": 0, "failures": 0,
                        "seconds": 0.0, "bytes_per_second": 0.0, "last_error": None,
                        "pause_reason": "starting", "block_bytes": self.storage.block,
                        "scans": 0, "container_header_reads": 0, "failed_blobs": 0,
                        "kept_raw": 0, "state": "starting", "next_retry_at": None}

    def status(self):
        with self.storage.guard:
            return dict(self.metrics, **self.storage.metrics, reserved_bytes=self.storage.reserved,
                        background_reserve_bytes=self.storage.background_reserve,
                        allocation_unit=self.storage.allocation_unit)

    def changed(self, name):
        with self.storage.guard:
            self.dirty.add(name)
        self.wake.set()

    def reason(self):
        if self.stop.is_set() or self.daemon.closing:
            return "stopped"
        if self.daemon.draining:
            return "draining"
        if self.daemon.retention.paused:
            return "wal_pressure"
        if self.daemon.recovery_queued or self.daemon.recovery_active:
            return "writer_recovery"
        if self.daemon.memory.level != "normal":
            return "memory_pressure"
        return None

    def checkpoint(self):
        reason = self.reason()
        self.metrics["pause_reason"] = reason
        if reason:
            raise Paused()
        if self.stop.wait(0.0001):
            raise Paused()

    def refresh(self, names):
        def counts(name):
            raw, encoded = self.inventory.get((name, "")), self.inventory.get((name, SUFFIX))
            return {"logical_bytes": (encoded or raw or (0, 0, 0))[1],
                    "raw_logical_bytes": raw[1] if raw else 0,
                    "encoded_logical_bytes": encoded[1] if encoded else 0,
                    "raw_allocated_bytes": raw[2] if raw else 0,
                    "encoded_allocated_bytes": encoded[2] if encoded else 0,
                    "overlap_bytes": raw[2] if raw and encoded else 0}
        for name in names:
            before = counts(name)
            refreshed = {}
            for suffix in ("", SUFFIX):
                path, key = self.storage.root / (name + suffix), (name, suffix)
                try:
                    info = path.stat()
                except FileNotFoundError:
                    refreshed[key] = None
                    continue
                physical = stamp(info)
                previous = self.inventory.get(key)
                if previous and previous[0] == physical:
                    refreshed[key] = previous
                    continue
                length = info.st_size
                if suffix:
                    with path.open("rb") as source:
                        header = source.read(HEADER.size)
                        self.metrics["container_header_reads"] += 1
                        length = HEADER.unpack(header)[3] if len(header) == HEADER.size else 0
                refreshed[key] = (physical, length, info.st_blocks * 512)
            # Commit one name's census together. A failed header read must not
            # leave an uncounted partial update that a retry counts twice.
            for key, value in refreshed.items():
                if value is None:
                    self.inventory.pop(key, None)
                else:
                    self.inventory[key] = value
            for key, value in counts(name).items():
                self.metrics[key] += value - before[key]
            raw = self.inventory.get((name, ""))
            if raw and self.seen.get(name) != raw[0]:
                self.backlog.add(name)
            else:
                self.backlog.discard(name)
        self.metrics.update(backlog=len(self.backlog), failed_blobs=len(self.failed))

    def failure(self, name, exc, phase, physical=None):
        prior = self.failed.get(name, {})
        count = prior.get("count", 0) + 1
        code = getattr(exc, "code", None) or errno.errorcode.get(getattr(exc, "errno", None), type(exc).__name__)
        self.failed[name] = {"stamp": physical, "count": count, "code": code, "phase": phase,
                             "retry_at": time.monotonic() + min(300, 5 * 2 ** min(count - 1, 6))}
        self.metrics["failures"] += 1
        self.metrics["last_error"] = {"code": code, "blob": name, "count": count, "phase": phase}

    def run(self):
        pending, discover = set(), True
        while not self.stop.is_set():
            try:
                self.wake.clear()
                now = time.monotonic()
                if discover and self.failed.get(None, {}).get("retry_at", 0) <= now:
                    try:
                        with os.scandir(self.storage.root) as entries:
                            for entry in entries:
                                name = entry.name[:-len(SUFFIX)] if entry.name.endswith(SUFFIX) else entry.name
                                if valid_name(name):
                                    pending.add(name)
                        self.metrics["scans"] += 1
                        self.failed.pop(None, None)
                        discover = False
                    except Exception as exc:
                        self.failure(None, exc, "discovery")
                with self.storage.guard:
                    pending.update(self.dirty)
                    self.dirty.clear()
                pending.update(n for n, value in self.failed.items()
                               if n is not None and value["retry_at"] <= now)
                for name in list(pending):
                    if self.failed.get(name, {}).get("retry_at", 0) > time.monotonic():
                        pending.discard(name)
                        continue
                    physical, phase = None, "refresh"
                    try:
                        self.checkpoint()
                        self.metrics["state"] = "running"
                        self.refresh({name})
                        phase = "stat"
                        try:
                            physical = stamp((self.storage.root / name).stat())
                        except FileNotFoundError:
                            physical = None
                        if physical is not None and self.seen.get(name) != physical:
                            began = time.monotonic()
                            phase = "conversion"
                            converted = self.storage.convert(name, self.checkpoint)
                            self.seen[name] = physical
                            self.metrics["seconds"] += time.monotonic() - began
                            if converted:
                                self.metrics["converted"] += 1
                                self.metrics["converted_bytes"] += physical[2]
                            else:
                                self.metrics["kept_raw"] += 1
                            self.metrics["bytes_per_second"] = self.metrics["converted_bytes"] / max(self.metrics["seconds"], .001)
                            phase = "refresh"
                            self.refresh({name})
                        self.failed.pop(name, None)
                    except Paused:
                        break
                    except Exception as exc:
                        self.failure(name, exc, phase, physical)
                    pending.discard(name)
                self.metrics["failed_blobs"] = sum(name is not None for name in self.failed)
                delay = min((max(0, f["retry_at"] - time.monotonic()) for f in self.failed.values()), default=None)
                self.metrics["next_retry_at"] = time.time() + delay if delay is not None else None
                if pending:
                    # A due retry still obeys pressure. Its expired deadline
                    # must not turn a paused worker into a zero-wait loop.
                    delay = 1
                    self.metrics["state"] = "paused" if self.reason() else "running"
                else:
                    self.metrics["state"] = "retrying" if self.failed else "idle"
                    self.metrics["pause_reason"] = "failed_backoff" if self.failed else "caught_up"
                # Only publication, retry deadlines, pressure or shutdown wake
                # this worker. No caught-up directory polling or header reads.
                if self.stop.is_set():
                    break
                self.wake.wait(delay)
            except Exception as exc:
                # An inventory/service failure never retires the worker. Preserve
                # pending names and repeat discovery after bounded backoff.
                self.failure(None, exc, "inventory")
                discover = True
                delay = max(0, self.failed[None]["retry_at"] - time.monotonic())
                self.metrics.update(state="retrying", pause_reason="failed_backoff",
                                    next_retry_at=time.time() + delay)
                self.stop.wait(delay)
        self.metrics.update(state="stopped", pause_reason="stopped", next_retry_at=None)

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop.clear()
            self.thread = threading.Thread(target=self.run, name="blob-conversion")
            self.thread.start()

    async def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread is not None:
            await finish_thread(self.thread.join)
