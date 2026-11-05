"""Where the event loop was when it stalled (2.4.35).

The watchdog helper sees a stall from outside the worker and records only that it happened: since 2.4.9 nothing walks
the loop thread's frames from a signal handler (faulthandler's dump did, while the loop was inside SQLite, and crashed
the live worker on 10-27). So every start's 5-13 s stall, and the ~20 s stalls of 2.4.29 and 2.4.31, named no cause.
This sampler is a thread of the worker that reads the loop thread's stack the supported way, sys._current_frames(),
which holds the GIL: the frames it walks are not changed under it, as a signal handler's could be. It samples a stall
once the loop's heartbeat is THRESHOLD old and again at 4x and 16x that, one JSON line per sample in
<state>/loop-stall-stacks.log. Under an interpreter without the GIL it does not sample, and status says so.
"""
import json
import os
import sys
import threading
import time
import traceback

from .watchdog import INTERVAL, SEGMENT_BYTES, THRESHOLD

FILE = "loop-stall-stacks.log"
# Threat: disk fill from a stall every few seconds for weeks. A sample is one line of at most DEPTH frames (about 4 KB),
# and the file moves to FILE + ".1" past SEGMENT_BYTES (16 MiB): the two hold 32 MiB at most, as loop-stalls.log does.
DEPTH = 48
# One stall is sampled at these multiples of THRESHOLD (0.75, 3 and 12 s of heartbeat age): a 13 s stall shows each
# part of its work, and a stall of a second costs one line.
FACTORS = (1, 4, 16)
PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep


class Sampler:
    def __init__(self, root, generation, thread_ident):
        self.path = os.path.join(str(root), FILE)
        self.generation = generation
        self.thread_ident = thread_ident
        self.beat = time.monotonic()  # set by the loop at each heartbeat (Daemon.measure_loop_lag)
        self.stopping = threading.Event()
        self.samples = 0
        self.error = None
        self.thread = threading.Thread(target=self.run, name="loop-stall-stacks", daemon=True)

    def start(self):
        if not getattr(sys, "_is_gil_enabled", lambda: True)():
            self.error = "unsupported: this interpreter runs without the GIL"
        elif not hasattr(sys, "_current_frames"):
            self.error = "unsupported: no sys._current_frames"
        else:
            self.thread.start()
        return self

    def status(self):
        return {"stack_file": self.path, "stack_samples": self.samples, "stack_error": self.error,
                "stack_sampler_alive": self.thread.is_alive()}

    def close(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(1.0)

    def run(self):
        stalled, due = None, 0
        while not self.stopping.wait(INTERVAL):
            beat = self.beat
            age = time.monotonic() - beat
            if age < THRESHOLD:
                stalled = None
                continue
            if stalled != beat:
                stalled, due = beat, 0
            if due < len(FACTORS) and age >= THRESHOLD * FACTORS[due]:
                due += 1
                try:
                    self.sample(age, due)
                except Exception as exc:  # a diagnostic never ends the worker
                    self.error = type(exc).__name__ + ": " + str(exc)[:200]

    def sample(self, age, number):
        frame = sys._current_frames().get(self.thread_ident)
        try:
            frames = traceback.StackSummary.extract(traceback.walk_stack(frame), limit=DEPTH,
                                                    lookup_lines=False) if frame is not None else []
            stack = [[entry.filename[len(PLUGIN):] if entry.filename.startswith(PLUGIN) else entry.filename,
                      entry.lineno, entry.name] for entry in frames]
        finally:
            del frame
        stack.reverse()  # outermost first, as a traceback reads
        now = time.time()
        line = json.dumps({"event": "loop_stack", "generation": self.generation, "wall": now,
                           "stall_start_wall": now - age, "age_seconds": round(age, 3), "sample": number,
                           "stack": stack}) + "\n"
        try:
            if os.path.getsize(self.path) + len(line) > SEGMENT_BYTES:
                os.replace(self.path, self.path + ".1")
        except FileNotFoundError:
            pass
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
        self.samples += 1
