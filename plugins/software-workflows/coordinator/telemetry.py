"""Monotonic settlement accounting. Measurements never authorize a transition.

Detailed counters are inclusive (SQL, hashing and fsync can run inside a snapshot).
The disjoint timeline assigns overlapping intervals to the most specific phase;
its residual and phase totals equal receipt-to-release wall time, not summed CPU.
"""
import asyncio
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
import os
import threading
import time

CURRENT = ContextVar("settlement_traces", default=())
HELD = ContextVar("observed_locks", default=())
OWNER = ContextVar("observation_owner", default=None)
IO = ContextVar("scan_io", default=None)


def detached_context(traces=()):
    """Shared/owned work has no caller deadline, lock ownership or identity.

    Callers explicitly pass settlement traces when that work contributes to
    them. The producer supplies its frozen cohort's traces separately.
    """
    from . import admissions
    context = copy_context()
    for variable, value in ((admissions.CURRENT, None), (CURRENT, traces),
                            (HELD, ()), (OWNER, None), (IO, None)):
        context.run(variable.set, value)
    return context


def identity():
    named = OWNER.get()
    if named:
        return dict(named)
    traces = CURRENT.get()
    return {"operation": traces[0].operation, "actor": traces[0].actor, "kind": "settlement"} if traces else {"kind": "coordinator"}


@contextmanager
def owner(named):
    token = OWNER.set(named)
    try:
        yield
    finally:
        OWNER.reset(token)


class Trace:
    def __init__(self, operation, actor, armed=True):
        self.operation, self.actor = operation, actor
        self.started = time.monotonic()
        self.armed = armed
        self.intervals, self.counters = [], {}
        self.marks = {"receipt": self.started}
        self.mutex = threading.Lock()
        self.closed = False

    def arm(self, when):
        if not self.armed:
            self.started = when
            self.marks = {"receipt": when}
            self.armed = True

    def add(self, name, start, end, timeline=True):
        with self.mutex:
            if self.closed or not self.armed or end <= self.started:
                return
            start = max(start, self.started)
            count, seconds = self.counters.get(name, (0, 0.0))
            self.counters[name] = (count + 1, seconds + end - start)
            if timeline:
                self.intervals.append((start, end, name))

    def result(self):
        end = self.marks.get("released", time.monotonic())
        with self.mutex:
            self.closed = True
            intervals, counters = list(self.intervals), dict(self.counters)
        # Nested specific phases take precedence over their containing spans.
        priority = {"journal_receipt": 1, "journal_queue": 1, "application": 0,
                    "actor_lock_wait": 2, "terminal_inventory": 2, "process_inspection": 2,
                    "checkout_lock_wait": 3, "checkout_lock_hold": 1,
                    "executor_queue": 4, "executor_run": 3, "executor_return": 4,
                    "snapshot_pass": 4, "scan_cohort_wait": 2, "observation_application": 2,
                    "notification_delivery": 3, "custody_release": 5}
        edges = [(self.started, 0, "unattributed"), (end, 0, "unattributed")]
        for a, b, name in intervals:
            a, b = max(a, self.started), min(b, end)
            if b > a:
                edges.extend(((a, 1, name), (b, -1, name)))
        active, account = Counter(), Counter()
        previous = self.started
        for when, delta, name in sorted(edges):
            phase = max((k for k, count in active.items() if count),
                        key=lambda k: (priority.get(k, 0), k), default="unattributed")
            account[phase] += when - previous
            active[name] += delta
            previous = when
        for name in (*priority, "git", "hashing", "fsync", "sql", "sql_commit", "sql_autocommit"):
            counters.setdefault(name, (0, 0.0))
        return {"operation": self.operation, "actor": self.actor, "clock": "monotonic",
                "receipt": self.started, "released": end, "seconds": end - self.started,
                "marks": self.marks, "phases": dict(account),
                "accounted_seconds": sum(account.values()),
                "inclusive": {k: {"count": n, "seconds": s} for k, (n, s) in counters.items()}}


@contextmanager
def using(traces):
    token = CURRENT.set(tuple(t for t in traces if t and not t.closed))
    try:
        yield
    finally:
        CURRENT.reset(token)


@contextmanager
def span(name, timeline=True):
    traces, locks = CURRENT.get(), HELD.get()
    old = [(lock, serial, lock.phase) for lock, serial in locks if lock.owner and lock.serial == serial]
    if not traces and not old:
        yield
        return
    start = time.monotonic()
    for lock, _, _ in old:
        lock.phase = name
    try:
        yield
    finally:
        end = time.monotonic()
        for trace in traces:
            trace.add(name, start, end, timeline)
        for lock, serial, phase in old:
            if lock.owner and lock.serial == serial:
                lock.phase = phase


def measured(name, timeline=False):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            if not CURRENT.get():
                return function(*args, **kwargs)
            with span(name, timeline):
                return function(*args, **kwargs)
        return call
    return decorate


async def to_thread(function, *args, **kwargs):
    from . import admissions
    from .locks import finish_thread
    if admissions.CURRENT.get() is not None:
        return await admissions.io(function, *args, **kwargs)
    if not CURRENT.get():
        return await finish_thread(function, *args, **kwargs)
    submitted = time.monotonic()
    traces = CURRENT.get()
    finished = []
    def work():
        start = time.monotonic()
        for trace in traces:
            trace.add("executor_queue", submitted, start)
        try:
            with span("executor_run"):
                return function(*args, **kwargs)
        finally:
            finished.append(time.monotonic())
    result = await finish_thread(work)
    if finished:
        for trace in traces:
            trace.add("executor_return", finished[0], time.monotonic())
    return result


def fsync(fd):
    if not CURRENT.get():
        return os.fsync(fd)
    with span("fsync", False):
        os.fsync(fd)


class ObservedLock(asyncio.Lock):
    def __init__(self, key, kind="checkout"):
        super().__init__()
        self.key, self.kind = key, kind
        self.waiting = {}
        self.owner, self.phase, self.serial = None, None, 0

    async def acquire(self):
        task = asyncio.current_task()
        from .admissions import mark
        record = {**identity(), "admission": mark(), "since": time.monotonic(),
                  "operations": [t.operation for t in CURRENT.get()]}
        self.waiting[task] = record
        try:
            with span(self.kind + "_lock_wait"):
                await super().acquire()
        finally:
            self.waiting.pop(task, None)
        self.owner = dict(record, since=time.monotonic())
        self.phase = "held"
        self.serial += 1
        HELD.set(HELD.get() + ((self, self.serial),))
        self.traces = CURRENT.get()
        return True

    def adopt(self):
        # A deadline acquisition used wait_for's child task; the real holder
        # supplies phase context and identity after that child returns.
        if not any(lock is self for lock, serial in HELD.get()):
            HELD.set(HELD.get() + ((self, self.serial),))

    def release(self):
        for trace in self.traces:
            trace.add(self.kind + "_lock_hold", self.owner["since"], time.monotonic())
        self.owner, self.phase = None, None
        # wait_for acquires in a child task. Its ContextVar token cannot be reset
        # by the caller releasing the lock; remove this lock from that context.
        HELD.set(tuple(pair for pair in HELD.get() if pair[0] is not self))
        super().release()

    def status(self):
        now = time.monotonic()
        def describe(record):
            return dict(record, age_seconds=now-record["since"]) if record else None
        return {"checkout": self.key, "holder": describe(self.owner), "phase": self.phase,
                "waiters": len(self.waiting),
                "oldest_waiter": describe(min(self.waiting.values(), key=lambda r: r["since"], default=None))}
