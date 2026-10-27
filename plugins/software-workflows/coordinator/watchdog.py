"""Bounded, best-effort loop-stall diagnostics independent of the daemon's GIL.

The helper records each stall it sees. Since 2.4.9 the worker writes no
traceback for the owner's diagnostic signal: faulthandler's signal-handler dump
walked every thread's frames while the loop was inside SQLite, and on 10-27 that
walk crashed the live worker (SIGSEGV in _PyObject_IsFreed). The signal is only
counted. A stopped helper or slow filesystem can lose diagnostics, never block a
writer.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import mmap
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

THRESHOLD = .75
INTERVAL = .1
# Threat: disk fill during weeks of recurring stalls. The segment was sized for
# 2.4.8's worst-case faulthandler dump (~11 MB); since 2.4.9 a stall writes only
# its event lines (under 1 KB). Two 16 MiB segments bound disk use. One segment
# also bounds the helper's RAM queue if its filesystem stops.
SEGMENT_BYTES = 16 * 1024 * 1024
# Ten polling intervals allow ordinary pipe draining and logger shutdown.
# A stopped helper then gets SIGKILL using its unreaped direct-child ownership,
# followed by one second to reap. Shutdown needs no auxiliary lookup process.
CLOSE_SECONDS = 10 * INTERVAL
# Threat: repeated lookup/spawn failures consuming process slots and CPU. Begin
# at the lookup's one-second timeout; double to 30 seconds (at most two attempts
# per minute under sustained failure). Never stop retrying; expose the deadline.
RETRY_MAX_SECONDS = 30
MEMORY_BYTES = 64
LOOKUP_ERRORS = {1: "TimeoutExpired", 2: "CalledProcessError", 3: "OSError"}


def absorb(signum, frame):
    """SIGUSR2 while no helper is registered. The owner can still deliver a diagnostic request that a retired helper
    queued, and SIG_DFL would end the worker. A caught signal, unlike SIG_IGN, is back at SIG_DFL in every process the
    worker starts: exec resets caught signals and keeps ignored ones."""


def resting(previous):
    """SIGUSR2's disposition while no helper is registered: the previous handler, or absorb in place of SIG_DFL and
    SIG_IGN."""
    return absorb if previous in (None, signal.SIG_DFL, signal.SIG_IGN) else previous


async def wait_exit(process, seconds):
    if hasattr(process, "end"):
        try:
            await asyncio.wait_for(process.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False
    deadline = time.monotonic() + seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(INTERVAL / 2)
    return True


class Watchdog:
    def __init__(self, root, generation):
        self.root, self.generation = root, generation
        self.path = root / "loop-stalls.log"
        self.samples = deque(maxlen=100)
        self.memory_file = tempfile.TemporaryFile(dir=root)
        self.memory_file.truncate(MEMORY_BYTES)
        self.memory = mmap.mmap(self.memory_file.fileno(), MEMORY_BYTES)
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.write_fd, False)
        self.process = None
        self.signals = 0
        self.closing = False
        self.previous_handler = None
        self.close_task = None
        self.closed_outcome = None
        self.forced = False

    def signalled(self, signum, frame):
        # Counts the owner's diagnostic signal; never perform I/O in this Python handler.
        if not self.closing:
            self.signals += 1
            struct.pack_into("Q", self.memory, 16, self.signals)

    def status(self):
        dropped = struct.unpack_from("Q", self.memory, 24)[0]
        return {"diagnostic_signals": self.signals, "dumps": 0, "dump_integrity": "no_dump",
                "dump_policy": "none: the diagnostic signal is counted, never dumped in-process",
                "logger_dropped_bytes": dropped, "logger_error": bool(struct.unpack_from("Q", self.memory, 32)[0]),
                "lookup_failures": struct.unpack_from("Q", self.memory, 40)[0],
                "lookup_error": LOOKUP_ERRORS.get(struct.unpack_from("Q", self.memory, 48)[0]),
                "lookup_retry_at": struct.unpack_from("d", self.memory, 56)[0]}

    def beat(self):
        if self.process and not hasattr(self.process, "end"):
            self.process.poll()  # waitpid(WNOHANG), including a stopped helper
        sample = {"wall": time.time(), "monotonic": time.monotonic(), "generation": self.generation}
        struct.pack_into("d", self.memory, 8, sample["wall"])
        struct.pack_into("d", self.memory, 0, sample["monotonic"])
        self.samples.append(sample)
        return sample

    async def start(self):
        self.beat()
        self.previous_handler = signal.signal(signal.SIGUSR2, self.signalled)
        try:
            # Python 3.15 on this macOS host can wake asyncio's child watcher
            # on SIGSTOP, then block its loop in waitpid(pid, 0). This helper
            # must never use that watcher: poll() always uses WNOHANG.
            from .owned import spawn
            from .processes import identity
            self.process = await spawn(sys.executable, "-B", str(Path(__file__).resolve()),
                str(self.root), str(os.getpid()), str(self.generation), str(self.memory_file.fileno()), str(self.read_fd),
                json.dumps(identity(os.getpid())),
                kind="watchdog", fd_arguments={6: self.memory_file.fileno(), 7: self.read_fd},
                pass_fds=(self.memory_file.fileno(), self.read_fd),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            self.unregister()
            self.dispose()
            signal.signal(signal.SIGUSR2, resting(self.previous_handler))
            raise
        # Nothing writes to the pipe since 2.4.9; the helper's select on it
        # paces its polling, and this read descriptor is retained with it.

    def unregister(self):
        # Absorb any signal already sent by the helper before disposing either
        # descriptor. The original disposition is restored only after it exits,
        # and never as SIG_DFL or SIG_IGN (resting).
        signal.signal(signal.SIGUSR2, absorb)

    def dispose(self):
        for fd in (self.read_fd, self.write_fd):
            if fd is not None:
                os.close(fd)
        self.read_fd = self.write_fd = None
        self.memory.close()
        self.memory_file.close()

    async def close(self):
        from .locks import finish
        from .telemetry import detached_context
        if self.closed_outcome is not None:
            return self.closed_outcome
        if self.close_task is None or (self.close_task.done() and
                (self.close_task.cancelled() or self.close_task.exception() is not None)):
            # A bounded reap attempt can fail or itself be cancelled. Retain the registration's
            # descriptors and direct-child ownership until another attempt
            # proves exit; no replacement helper may reuse them meanwhile.
            self.close_task = asyncio.create_task(self._close(), context=detached_context())

        async def completed():
            return await asyncio.shield(self.close_task)

        # Shutdown can race supervision cancellation. Ownership lasts until
        # this single close task has reaped the helper, even if its caller ends.
        return await finish(completed())

    async def _close(self):
        self.closing = True
        struct.pack_into("d", self.memory, 0, -1)
        self.unregister()
        try:
            if self.process:
                if hasattr(self.process, "end"):
                    if not await wait_exit(self.process, CLOSE_SECONDS):
                        self.forced = True
                    await self.process.end()
                else:
                    await self.close_direct()
            self.closed_outcome = dict(self.status(), helper_forced=self.forced,
                                       shutdown_integrity="possibly_lost" if self.forced else "helper_exited")
            return self.closed_outcome
        finally:
            self.unregister()
            if self.closed_outcome is not None:
                self.dispose()
                signal.signal(signal.SIGUSR2, resting(self.previous_handler))

    async def close_direct(self):
        if self.process:
                if not await wait_exit(self.process, CLOSE_SECONDS):
                    # poll() still None proves this is our unreaped direct
                    # child. Its PID cannot be reused; a fallible ps lookup
                    # adds no ownership proof and must not strand this child.
                    if self.process.poll() is None:
                        self.process.kill()
                        self.forced = True
                    if not await wait_exit(self.process, CLOSE_SECONDS):
                        raise RuntimeError("watchdog did not exit after owned SIGKILL")


def monitor(root, parent, generation, memory_fd, pipe_fd, parent_identity=None):
    from coordinator import processes
    from coordinator.client import CoordinatorError
    parent_identity = parent_identity or processes.identity(parent)
    if not parent_identity:
        return
    memory = mmap.mmap(memory_fd, MEMORY_BYTES)
    os.close(memory_fd)
    os.set_blocking(pipe_fd, False)
    path = Path(root) / "loop-stalls.log"
    previous = Path(root) / "loop-stalls.log.1"
    queue, condition = deque(), threading.Condition()
    queued, dropped, done = 0, 0, False

    def enqueue(data):
        nonlocal queued, dropped
        with condition:
            if queued + len(data) > SEGMENT_BYTES:
                dropped += len(data)
                struct.pack_into("Q", memory, 24, dropped)
                return  # Drop new output, preserving the earlier diagnostic.
            queue.append(data)
            queued += len(data)
            condition.notify()

    def event(kind, **values):
        enqueue((json.dumps({"event": kind, **values}, separators=(",", ":")) + "\n").encode())

    def logger():
        nonlocal queued
        output = None
        try:
            for segment in (path, previous):
                if segment.exists() and segment.stat().st_size > SEGMENT_BYTES:
                    with segment.open("rb") as source:
                        source.seek(-SEGMENT_BYTES, os.SEEK_END)
                        tail = source.read()
                    with segment.open("wb") as target:
                        target.write(tail)
            size = path.stat().st_size if path.exists() else 0
            reported_drop = 0
            while True:
                with condition:
                    while not queue and not done:
                        condition.wait()
                    if not queue:
                        return
                    data = queue.popleft()
                    queued -= len(data)
                if dropped != reported_drop:
                    data = (json.dumps({"event": "watchdog_output_lost", "bytes": dropped}) + "\n").encode() + data
                    reported_drop = dropped
                if output is None:
                    output = path.open("ab")
                offset = 0
                while offset < len(data):
                    if size >= SEGMENT_BYTES:
                        output.close()
                        os.replace(path, previous)
                        output = path.open("wb")
                        size = 0
                    chunk = data[offset:offset + SEGMENT_BYTES - size]
                    output.write(chunk)
                    size += len(chunk)
                    offset += len(chunk)
                output.flush()
        except OSError:
            struct.pack_into("Q", memory, 32, 1)
        finally:
            if output:
                output.close()

    writer = threading.Thread(target=logger, name="watchdog-file-output", daemon=True)
    writer.start()
    reported, signals = None, 0
    retry_at, retry_delay, failures = 0.0, CLOSE_SECONDS, 0
    try:
        # The owner is now our kernel parent; the observed worker is a sibling.
        # Its pinned identity, not a parent PID or executable name, authorizes
        # this diagnostic signal. The negative heartbeat ends normal cleanup.
        while True:
            before, wall = struct.unpack_from("dd", memory)
            if before < 0:
                break
            if before != struct.unpack_from("d", memory)[0]:
                continue
            count = struct.unpack_from("Q", memory, 16)[0]
            if count != signals:
                event("watchdog_diagnostic_signal", signals=count)
                signals = count
            age = time.monotonic() - before
            if age >= THRESHOLD and before != reported and time.monotonic() >= retry_at:
                try:
                    alive = not processes.exit_evidence(parent_identity)
                except (CoordinatorError, OSError) as exc:
                    failures += 1
                    code = 3
                    retry_at = time.monotonic() + retry_delay
                    struct.pack_into("QQd", memory, 40, failures, code, time.time() + retry_delay)
                    event("watchdog_lookup_failed", error=type(exc).__name__, message=str(exc)[:512],
                          parent=parent, failures=failures, retry_seconds=retry_delay, observed_wall=time.time())
                    retry_delay = min(RETRY_MAX_SECONDS, retry_delay * 2)
                    # Continue draining the nonblocking pipe even while parent
                    # identity cannot be checked. Never signal an unproved PID.
                    alive = None
                if alive is None:
                    pass
                elif not alive:
                    event("watchdog_ownership_lost", parent=parent)
                    break
                else:
                    retry_delay, retry_at = CLOSE_SECONDS, 0.0
                    struct.pack_into("d", memory, 56, 0)
                    event("loop_stall", generation=generation, heartbeat_wall=wall,
                          stall_start_wall=wall + INTERVAL,
                          stall_start_utc=datetime.fromtimestamp(wall + INTERVAL, timezone.utc).isoformat(),
                          heartbeat_monotonic=before, observed_wall=time.time(), age_seconds=age,
                          threshold_seconds=THRESHOLD, parent=parent, dump_integrity="no_dump")
                    if struct.unpack_from("d", memory)[0] < 0:
                        break
                    try:
                        from coordinator.leases import control
                        result = control(root, "owner/diagnostic", {"worker": parent_identity}, timeout=CLOSE_SECONDS)
                        if result.get("sent"):
                            reported = before
                    except (CoordinatorError, OSError, EOFError):
                        # An owner restart or an exit race is not evidence that
                        # the worker ended; retry without signalling it ourselves.
                        retry_at = time.monotonic() + retry_delay
                        retry_delay = min(RETRY_MAX_SECONDS, retry_delay * 2)
            readable, _, _ = select.select([pipe_fd], [], [], INTERVAL)
            if readable:
                data = os.read(pipe_fd, 65536)
                if data:
                    enqueue(data)
    finally:
        with condition:
            done = True
            condition.notify()
        writer.join(CLOSE_SECONDS / 2)
        # A file write may be stuck in the OS. The daemon thread is disposable;
        # do not close its mmap underneath it before process exit.
        if not writer.is_alive():
            memory.close()
        os.close(pipe_fd)


if __name__ == "__main__":
    monitor(sys.argv[1], *(int(value) for value in sys.argv[2:6]),
            json.loads(sys.argv[6]) if len(sys.argv) > 6 else None)
