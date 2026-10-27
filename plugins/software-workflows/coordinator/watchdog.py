"""Bounded, best-effort C-level diagnostics independent of the daemon's GIL.

The signal handler writes to a nonblocking pipe. Python's faulthandler does not
report short writes, so every dump is explicitly marked possibly truncated.
A stopped helper or slow filesystem can lose diagnostics, never block a writer.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
import faulthandler
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
# Threat: disk fill during weeks of recurring stalls. faulthandler bounds a
# dump to 100 threads x 100 frames; each frame has two <=500-character strings
# plus formatting (~11 MB worst case). Two 16 MiB segments bound disk use.
# One segment also bounds the helper's RAM queue if its filesystem stops.
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


async def wait_exit(process, seconds):
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
        self.dumps = 0
        self.closing = False
        self.previous_handler = None
        self.close_task = None
        self.closed_outcome = None
        self.forced = False

    def dumped(self, signum, frame):
        # Chained after the C dump; never perform I/O in this Python handler.
        if not self.closing:
            self.dumps += 1
            struct.pack_into("Q", self.memory, 16, self.dumps)

    def status(self):
        dropped = struct.unpack_from("Q", self.memory, 24)[0]
        return {"dumps": self.dumps, "dump_integrity": "possibly_truncated" if self.dumps else "no_dump",
                "dump_policy": "nonblocking; short writes cannot be proved absent",
                "logger_dropped_bytes": dropped, "logger_error": bool(struct.unpack_from("Q", self.memory, 32)[0]),
                "lookup_failures": struct.unpack_from("Q", self.memory, 40)[0],
                "lookup_error": LOOKUP_ERRORS.get(struct.unpack_from("Q", self.memory, 48)[0]),
                "lookup_retry_at": struct.unpack_from("d", self.memory, 56)[0]}

    def beat(self):
        if self.process:
            self.process.poll()  # waitpid(WNOHANG), including a stopped helper
        sample = {"wall": time.time(), "monotonic": time.monotonic(), "generation": self.generation}
        struct.pack_into("d", self.memory, 8, sample["wall"])
        struct.pack_into("d", self.memory, 0, sample["monotonic"])
        self.samples.append(sample)
        return sample

    async def start(self):
        self.beat()
        self.previous_handler = signal.signal(signal.SIGUSR2, self.dumped)
        try:
            faulthandler.register(signal.SIGUSR2, file=self.write_fd, all_threads=True, chain=True)
            # Python 3.15 on this macOS host can wake asyncio's child watcher
            # on SIGSTOP, then block its loop in waitpid(pid, 0). This helper
            # must never use that watcher: poll() always uses WNOHANG.
            self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                str(self.root), str(os.getpid()), str(self.generation), str(self.memory_file.fileno()), str(self.read_fd),
                ],
                pass_fds=(self.memory_file.fileno(), self.read_fd),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except BaseException:
            self.unregister()
            self.dispose()
            signal.signal(signal.SIGUSR2, self.previous_handler)
            raise
        # Retain a read descriptor: an exited helper must not turn the C
        # handler's best-effort write into SIGPIPE.

    def unregister(self):
        # Ignore any signal already sent by the helper before disposing either
        # descriptor. The original disposition is restored only after it exits.
        faulthandler.unregister(signal.SIGUSR2)
        signal.signal(signal.SIGUSR2, signal.SIG_IGN)

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
                if not await wait_exit(self.process, CLOSE_SECONDS):
                    # poll() still None proves this is our unreaped direct
                    # child. Its PID cannot be reused; a fallible ps lookup
                    # adds no ownership proof and must not strand this child.
                    if self.process.poll() is None:
                        self.process.kill()
                        self.forced = True
                    if not await wait_exit(self.process, CLOSE_SECONDS):
                        raise RuntimeError("watchdog did not exit after owned SIGKILL")
            self.closed_outcome = dict(self.status(), helper_forced=self.forced,
                                       shutdown_integrity="possibly_lost" if self.forced else "helper_exited")
            return self.closed_outcome
        finally:
            self.unregister()
            # Keep SIGUSR2 ignored and both descriptors owned while an
            # unreaped helper might still signal. A retry can still read status.
            if self.closed_outcome is not None:
                self.dispose()
                signal.signal(signal.SIGUSR2, self.previous_handler)


def monitor(root, parent, generation, memory_fd, pipe_fd):
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
    reported, dumps = None, 0
    retry_at, retry_delay, failures = 0.0, CLOSE_SECONDS, 0
    try:
        while os.getppid() == parent:
            before, wall = struct.unpack_from("dd", memory)
            if before < 0:
                break
            if before != struct.unpack_from("d", memory)[0]:
                continue
            count = struct.unpack_from("Q", memory, 16)[0]
            if count != dumps:
                event("watchdog_dump_possibly_truncated", dumps=count,
                      reason="nonblocking signal output; complete delivery cannot be proved")
                dumps = count
            age = time.monotonic() - before
            if age >= THRESHOLD and before != reported and time.monotonic() >= retry_at:
                try:
                    command = subprocess.check_output(["/bin/ps", "-p", str(parent), "-o", "command="],
                                                      text=True, timeout=CLOSE_SECONDS)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
                    failures += 1
                    code = 1 if isinstance(exc, subprocess.TimeoutExpired) else 2 if isinstance(exc, subprocess.CalledProcessError) else 3
                    retry_at = time.monotonic() + retry_delay
                    struct.pack_into("QQd", memory, 40, failures, code, time.time() + retry_delay)
                    event("watchdog_lookup_failed", error=type(exc).__name__, message=str(exc)[:512],
                          parent=parent, failures=failures, retry_seconds=retry_delay, observed_wall=time.time())
                    retry_delay = min(RETRY_MAX_SECONDS, retry_delay * 2)
                    # Continue draining the nonblocking pipe even while parent
                    # identity cannot be checked. Never signal an unproved PID.
                    command = None
                if command is None:
                    pass
                elif str(root) not in command or os.getppid() != parent:
                    event("watchdog_ownership_lost", parent=parent)
                    break
                else:
                    retry_delay, retry_at = CLOSE_SECONDS, 0.0
                    struct.pack_into("d", memory, 56, 0)
                    event("loop_stall", generation=generation, heartbeat_wall=wall,
                          stall_start_wall=wall + INTERVAL,
                          stall_start_utc=datetime.fromtimestamp(wall + INTERVAL, timezone.utc).isoformat(),
                          heartbeat_monotonic=before, observed_wall=time.time(), age_seconds=age,
                          threshold_seconds=THRESHOLD, parent=parent, dump_integrity="possibly_truncated")
                    if struct.unpack_from("d", memory)[0] < 0:
                        break
                    os.kill(parent, signal.SIGUSR2)
                    reported = before
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
    monitor(sys.argv[1], *(int(value) for value in sys.argv[2:]))
