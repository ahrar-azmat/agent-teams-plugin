"""Checkout scan producers and durable observation obligations.

Readers never own the checkout commit lock. A producer freezes its cohort before
submitting work to the executor; arrivals during that work belong to its next
scan. Results are immutable and reusable, but application guards are not shared.
Only ordinary, unattributed observations contribute to the same UNKNOWN reason.
"""
import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import MappingProxyType
import time

from .locks import finish_all
from . import telemetry
from .client import CoordinatorError
from .store import uid, digest


@dataclass(frozen=True)
class Snapshot:
    images: object
    unreadable: object
    listed: object
    heads: object
    versions: object
    cohort: tuple
    scan: str


class Versions:
    """Immutable exclusion epochs in the same normalized prefix space as claims.

    A parent mutation invalidates its descendants, and a child invalidates a
    parent read. Case aliases share a key. Siblings do not invalidate each other.
    """
    def __init__(self, checkout, rows):
        from .ledger import key
        self.checkout = checkout
        self.exact, self.subtree = {}, {}
        self.whole = 0
        for path, version in rows:
            if path == "":
                self.whole = version
                continue
            name = key(checkout, path)
            self.exact[name] = self.exact.get(name, 0) + version
            parts = name.split("/")
            for length in range(1, len(parts) + 1):
                parent = "/".join(parts[:length])
                self.subtree[parent] = self.subtree.get(parent, 0) + version

    def get(self, path):
        from .ledger import key
        name = key(self.checkout, path)
        parts = name.split("/")
        return (self.whole, self.subtree.get(name, 0),
                tuple(self.exact.get("/".join(parts[:n]), 0) for n in range(1, len(parts))))


def tokens(store, checkout, paths=None):
    heads = (dict(store.db.execute("SELECT path,revision FROM heads WHERE checkout=?", (checkout,)))
             if paths is None else {path: row[0] for path in paths if (row := store.db.execute(
                 "SELECT revision FROM heads WHERE checkout=? AND path=?", (checkout, path)).fetchone())})
    return (heads,
            Versions(store.get(checkout), store.db.execute(
                "SELECT path,version FROM path_versions WHERE checkout=?", (checkout,))))


def read(ledger, checkout, paths, known):
    from .ledger import full_snapshot, read_path, UNREADABLE
    unreadable, listed = {}, None
    if paths is None:
        listed = set()
        images = full_snapshot(ledger.store, checkout, known, unreadable, listed)
    else:
        images = {}
        for path in paths:
            try:
                images[path] = read_path(ledger.store, checkout, path)
            except CoordinatorError as exc:
                if exc.code not in UNREADABLE:
                    raise
                unreadable[path] = exc.code
    return images, unreadable, listed


class Producer:
    def __init__(self, manager, checkout):
        self.manager, self.checkout = manager, checkout
        self.queue, self.task = [], None
        self.active = None
        self.last_seconds = 0.0
        self.scans, self.bytes = 0, 0
        self.skipped_bytes = 0
        self.rereads, self.reread_bytes = 0, 0

    async def request(self, obligation, paths):
        future = asyncio.get_running_loop().create_future()
        self.queue.append((obligation, paths, future, telemetry.CURRENT.get()))
        if self.task is None:
            self.task = asyncio.create_task(self.run(), context=telemetry.detached_context())
        # A cancelled caller does not cancel another caller's shared read. Its
        # unacknowledged durable obligation remains recoverable after a crash.
        return await asyncio.shield(future)

    async def run(self):
        try:
            while self.queue:
                cohort, self.queue = self.queue, []
                try:
                    records = tuple(entry[0] for entry in cohort)
                    traces = tuple({trace for entry in cohort for trace in entry[3]})
                    paths = None if any(entry[1] is None for entry in cohort) else set().union(*(entry[1] for entry in cohort))
                    requested = set().union(*(entry[1] or () for entry in cohort))
                    store, ledger = self.manager.store, self.manager.ledger
                    heads, versions = tokens(store, self.checkout["id"])
                    ident, began = uid("scan"), time.monotonic()
                    self.active = {"id": ident, "since": began, "phase": "executor queue",
                                   "obligations": [r["id"] for r in records], "caller": records[0]["caller"]}
                    counters = {"bytes": 0}
                    def work():
                        self.active["phase"] = "snapshot"
                        self.active["executor_started"] = time.monotonic()
                        token = telemetry.IO.set(counters)
                        try:
                            # Inventory and heads can both omit an absent path
                            # explicitly requested by a narrow cohort member.
                            return read(ledger, self.checkout, paths, set(heads) | requested)
                        finally:
                            telemetry.IO.reset(token)
                    with telemetry.using(traces):
                        images, unreadable, listed = await telemetry.to_thread(work)
                    # No mutable payload is shared between applications.
                    snapshot = Snapshot(MappingProxyType({p: MappingProxyType(v) for p, v in images.items()}),
                        MappingProxyType(unreadable), frozenset(listed) if listed is not None else None,
                        MappingProxyType(heads), versions, records, ident)
                    self.last_seconds = time.monotonic() - began
                    self.scans += 1
                    self.bytes += counters["bytes"]
                    self.skipped_bytes += counters.get("skipped_bytes", 0)
                    revalidation = any(r.get("revalidation") for r in records)
                    if revalidation:
                        self.rereads += 1
                        self.reread_bytes += counters["bytes"]
                    store.event("checkout/scan", ident, {"checkout": self.checkout["id"],
                        "obligations": self.active["obligations"], "started": began,
                        "seconds": self.last_seconds, "executor_delay": self.active["executor_started"] - began,
                        "bytes": counters["bytes"], "skipped_bytes": counters.get("skipped_bytes", 0),
                        "revalidation": revalidation, "paths": len(images) + len(unreadable),
                        "oldest_obligation_seconds": time.time() - min(r["created"] for r in records)})
                    for _, _, future, _ in cohort:
                        if not future.done():
                            future.set_result(snapshot)
                except BaseException as exc:
                    for _, _, future, _ in cohort:
                        if not future.done():
                            future.set_exception(exc)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                finally:
                    self.active = None
        finally:
            self.task = None
            stop = self.manager.ledger.terminating
            if self.queue and not (stop is not None and stop.is_set()):
                # The frozen cohort received its exception. Later callers still
                # own live futures and must get their own scan, even if this
                # producer was unexpectedly cancelled while serving.
                self.manager.store.event("checkout/producer_restarting", self.checkout["id"],
                                         {"queued": len(self.queue)})
                self.task = asyncio.create_task(self.run(), context=telemetry.detached_context())

    def status(self):
        now = time.monotonic()
        return {"checkout": self.checkout["id"], "active": self.active,
                "active_seconds": now - self.active["since"] if self.active else 0,
                "queued": len(self.queue), "scans": self.scans, "bytes": self.bytes,
                "hashed_bytes": self.bytes, "skipped_bytes": self.skipped_bytes,
                "rereads": self.rereads, "reread_bytes": self.reread_bytes,
                "last_seconds": self.last_seconds,
                "oldest_seconds": max((time.time() - r[0]["created"] for r in self.queue), default=0)}


class Manager:
    def __init__(self, ledger):
        self.ledger, self.store = ledger, ledger.store
        self.producers = {}
        self.live = {}
        self.retry_needed = False

    def status(self):
        return [p.status() for p in self.producers.values()]

    def producer(self, checkout):
        if checkout["id"] not in self.producers:
            self.producers[checkout["id"]] = Producer(self, checkout)
        return self.producers[checkout["id"]]

    async def observe(self, applications, reason, action=None, on_observed=None, _replay=None, owner=None):
        record = {"generation": self.store.generation,
            "state": "pending", "created": time.time(), "causal_point": time.monotonic(), "incarnation": uid("application"),
            "caller": dict(telemetry.identity(), entry=applications[0].get("caller", "observe")), "reason": reason, "action": action,
            "scope": {c["checkout"]["id"]: sorted(c["paths"]) if c["paths"] is not None else None for c in applications},
            "policy": [{"checkout": c["checkout"]["id"], "ignore_pending": c.get("ignore_pending", True),
                        "preserve_leads": c.get("preserve_leads", False), "guard": bool(c.get("guard")),
                        "attribute": bool(c.get("attribute")), "settling_operation": c.get("settling_operation")} for c in applications]}
        record["owner"] = owner or action or {"kind": "observation", "caller": record["caller"], "scope": record["scope"]}
        record["retry_key"] = digest({key: record[key] for key in ("generation", "owner", "reason", "action", "scope", "policy")})
        previous = _replay
        if previous is None:
            previous = next((r for r in self.store.selected(
                "SELECT * FROM records INDEXED BY observation_pending WHERE kind='observation' "
                "AND json_extract(data,'$.state')='pending' AND json_extract(data,'$.retry_key')=?",
                (record["retry_key"],)) if r["id"] not in self.live), None)
        ident = previous["id"] if previous else uid("observation")
        if previous:
            record["created"] = previous["created"]
            if _replay:
                record["retry_key"] = previous["retry_key"]
        record = self.store.put("observation", ident, record)
        self.live[ident] = {"applications": applications, "callback": on_observed, "traces": telemetry.CURRENT.get(),
                            "incarnation": record["incarnation"]}
        try:
            return await self._observe(record, applications, reason, action, on_observed)
        except BaseException as exc:
            current = self.store.get(ident)
            if current["state"] == "pending":
                current.update(retry_after=time.time() + 5, failure=getattr(exc, "code", type(exc).__name__))
                self.store.save(current)
                self.retry_needed = True
            raise
        finally:
            self.live.pop(ident, None)
            # The acknowledgement/audit is in the same transaction as the
            # revisions and release. Only active futures still need this row.
            self.store.db.execute("DELETE FROM records WHERE id=? AND kind='observation' "
                                  "AND json_extract(data,'$.state')='committed'", (ident,))

    async def _observe(self, record, applications, reason, action, on_observed):
        """Observe first, then validate and commit all participants together.

        Python callbacks are live guards, never recovery authorization. A crash
        before acknowledgement replays a conservative observation; native/lead
        recovery re-establishes the completion action from its durable owner.
        """
        store, ledger, ident = self.store, self.ledger, record["id"]
        began = time.monotonic()
        with telemetry.owner({"kind": "observation", "operation": (action or {}).get("operation"), "observation": ident,
                              "actor": telemetry.identity().get("actor"), "reason": reason}):
            for application in applications:
                if application.get("guard"):
                    application["guard"]()
            with telemetry.span("scan_cohort_wait"):
                snapshots = await finish_all(*(self.producer(c["checkout"]).request(record, c["paths"]) for c in applications))
            # Private overlays are created only for paths invalidated during IO.
            observations = []
            for application, snapshot in zip(applications, snapshots):
                scope = application["paths"]
                observations.append({
                    "actual": {p: image for p, image in snapshot.images.items() if scope is None or p in scope},
                    "unreadable": {p: code for p, code in snapshot.unreadable.items() if scope is None or p in scope},
                    # A listed inventory authorizes whole-checkout outside
                    # classification. A narrow application never receives one.
                    "listed": snapshot.listed if scope is None else None,
                    "heads": snapshot.heads, "versions": snapshot.versions})
            while True:
                current = store.get(ident)
                if current["state"] == "committed":
                    results = current["results"]
                    break
                outsides = [await ledger.outside(c["checkout"], o["actual"], o["unreadable"], o["listed"])
                            for c, o in zip(applications, observations)]
                changed = []
                lock_waits = []
                async with AsyncExitStack() as stack:
                    for checkout in sorted({c["checkout"]["id"] for c in applications}):
                        waiting = time.monotonic()
                        await stack.enter_async_context(ledger.lock(checkout))
                        lock_waits.append((waiting, time.monotonic()))
                    acquired = time.monotonic()
                    # Another member of this frozen ordinary cohort may already
                    # have committed our acknowledgement and completion action.
                    current = store.get(ident)
                    if current["state"] == "committed":
                        results = current["results"]
                        break
                    for c, o in zip(applications, observations):
                        if c.get("guard"):
                            c["guard"]()
                        heads, versions = tokens(store, c["checkout"]["id"])
                        paths = set(o["actual"]) | set(o["unreadable"])
                        if c["paths"] is not None:
                            paths.update(c["paths"])
                        if c["paths"] is None:
                            paths |= set(heads)
                        dirty = {p for p in paths if versions.get(p) != o["versions"].get(p)}
                        for path in paths - dirty:
                            if heads.get(path) != o["heads"].get(path):
                                head = store.head(c["checkout"]["id"], path)
                                image = {"blob": head["blob"], "mode": head["mode"]} if head else {"blob": None, "mode": None}
                                if path not in o["actual"] or o["actual"][path] != image:
                                    dirty.add(path)
                        # Opaque custody may have created a path absent from
                        # both the old inventory and heads. Re-enumerate the
                        # whole requested scope, not just already known paths.
                        whole = c["paths"] is None and versions.whole != o["versions"].whole
                        changed.append((dirty, heads, versions, whole))
                    if not any(paths or whole for paths, _, _, whole in changed):
                        committed = time.monotonic()
                        results = {}
                        merged = {ident: record}
                        if len(applications) == 1 and compatible(record):
                            for candidate in snapshots[0].cohort:
                                live = self.live.get(candidate["id"])
                                if (live and live["incarnation"] == candidate["incarnation"]
                                        and len(live["applications"]) == 1 and compatible(candidate)
                                        and candidate["scope"] == record["scope"]
                                        and store.get(candidate["id"])["state"] == "pending"
                                        and store.get(candidate["id"])["incarnation"] == candidate["incarnation"]):
                                    merged[candidate["id"]] = candidate
                        traces = tuple({t for key in merged for t in self.live[key]["traces"]})
                        # A turn-end caller may apply a settlement's compatible
                        # cohort. Attribute its shared commit lock to those
                        # settlements too, without counting the holder twice.
                        inherited = set(telemetry.CURRENT.get())
                        for trace in traces:
                            if trace not in inherited:
                                for waiting, held in lock_waits:
                                    trace.add("checkout_lock_wait", waiting, held)
                        with telemetry.using(traces), telemetry.span("observation_application"), store.transaction():
                            for c, o, outside, snapshot in zip(applications, observations, outsides, snapshots):
                                proof = c.get("attribute")
                                contributing = list(merged.values())
                                results[c["checkout"]["id"]] = ledger.apply_observation(c["checkout"],
                                    set(o["actual"]) | set(o["unreadable"]) if c["paths"] is None else c["paths"], o["actual"], reason,
                                    ignore_pending=c.get("ignore_pending", True), attribute=proof, transaction=False, outside=outside,
                                    unreadable=o["unreadable"], preserve_leads=c.get("preserve_leads", False),
                                    settling_operation=c.get("settling_operation"),
                                    observations={"obligations": [r["id"] for r in contributing], "reasons": sorted({r["reason"] for r in contributing})})
                            for member in merged:
                                callback = self.live[member]["callback"]
                                if callback:
                                    callback()
                                current = store.get(member)
                                current.update(state="committed", completed=time.time(), scans=[s.scan for s in snapshots], results=results)
                                store.save(current)
                            store.event("observation/committed", ident, {"seconds": time.monotonic() - began,
                                "commit_seconds": time.monotonic() - committed, "scans": [s.scan for s in snapshots],
                                "obligations": list(merged), "scope": record["scope"], "action": action,
                                "members": [{key: member.get(key) for key in ("id", "incarnation", "causal_point", "owner", "caller", "reason", "action", "scope")}
                                            for member in merged.values()]})
                        finished = time.monotonic()
                        for trace in traces:
                            if "released" in trace.marks:
                                trace.marks["released"] = finished  # visible after COMMIT
                            if trace not in inherited:
                                trace.add("checkout_lock_hold", acquired, finished)
                        break
                # Re-read invalidated scope outside every commit lock. Token
                # capture precedes these reads, so a second transition repeats
                # validation rather than accepting another transient image.
                for index, (c, o, (paths, heads, versions, whole)) in enumerate(zip(applications, observations, changed)):
                    producer = self.producer(c["checkout"])
                    if whole:
                        # An opaque transition invalidates every waiting full
                        # observer. Rejoin the producer instead of launching
                        # one whole-checkout executor read per observer.
                        snapshot = await producer.request(dict(record, revalidation=True), None)
                        snapshots[index] = snapshot
                        o.update(actual=dict(snapshot.images), unreadable=dict(snapshot.unreadable),
                                 listed=snapshot.listed)
                        heads, versions = snapshot.heads, snapshot.versions
                        store.event("observation/revalidate", ident, {"checkout": c["checkout"]["id"],
                            "paths": None, "whole_custody": True, "shared_scan": snapshot.scan,
                            "bytes": 0})  # Physical bytes counted once in checkout/scan.
                    elif paths:
                        counters = {"bytes": 0}
                        def reread():
                            token = telemetry.IO.set(counters)
                            try:
                                return read(ledger, c["checkout"], paths,
                                            set(heads) | set(o["actual"]) | set(o["unreadable"]))
                            finally:
                                telemetry.IO.reset(token)
                        images, unreadable, _ = await telemetry.to_thread(reread)
                        for path in paths:
                            o["actual"].pop(path, None)
                            o["unreadable"].pop(path, None)
                        o["actual"].update(images)
                        o["unreadable"].update(unreadable)
                        producer.rereads += 1
                        producer.reread_bytes += counters["bytes"]
                        producer.skipped_bytes += counters.get("skipped_bytes", 0)
                        store.event("observation/revalidate", ident, {"checkout": c["checkout"]["id"],
                            "paths": sorted(paths), "whole_custody": False, "bytes": counters["bytes"]})
                    o["heads"], o["versions"] = heads, versions
        with telemetry.span("notification_delivery"):
            await ledger.notify_released()
        await asyncio.sleep(0)
        return results

    async def close(self):
        # A forced daemon shutdown still owns submitted readers until they end.
        # Closing SQLite while a reader is publishing blobs would break that
        # ownership boundary. No new callers are admitted during shutdown.
        await asyncio.gather(*(asyncio.shield(p.task) for p in self.producers.values() if p.task),
                             return_exceptions=True)

    async def retry(self):
        # Healthy maintenance performs no observation-history query. A failure
        # leaves a durable row; repair retries are separate from owner actions.
        if self.retry_needed:
            await self.recover(current=True)

    def repairs(self, pending):
        """Transfer failed applications to one repair per participant set.

        This is a durable handoff, never an acknowledgement of the old scan.
        Completion actions still belong to their operation/turn/lead owners.
        A held checkout can therefore defer repair indefinitely without adding
        one pending observation per failed turn. The audit retains every owner.
        """
        groups = {}
        for record in pending:
            if record["id"] not in self.live:
                groups.setdefault(tuple(sorted(record["scope"])), []).append(record)
        with self.store.transaction():
            for checkouts, records in groups.items():
                retained = next((r for r in records if r.get("owner", {}).get("kind") == "observation-repair"), None)
                if retained and len(records) == 1:
                    continue
                scope = {}
                for checkout in checkouts:
                    scopes = [r["scope"][checkout] for r in records]
                    scope[checkout] = None if None in scopes else sorted(set().union(*map(set, scopes)))
                owner = {"kind": "observation-repair", "checkouts": list(checkouts)}
                ident = retained["id"] if retained else uid("observation")
                record = {"generation": self.store.generation, "state": "pending", "owner": owner,
                    "created": min(r["created"] for r in records), "causal_point": time.monotonic(),
                    "incarnation": uid("application"), "scope": scope, "action": None,
                    "reason": "unfinished observation repair", "caller": {"kind": "observation-repair"},
                    # Later failures join the next read without postponing an
                    # already eligible repair under continuing arrivals.
                    "retry_after": min(r.get("retry_after", 0) for r in records),
                    "retry_key": digest({"generation": self.store.generation, "owner": owner})}
                self.store.put("observation", ident, record)
                self.store.event("observation/repair_handoff", ident, {"scope": scope,
                    "sources": [{key: r.get(key) for key in ("id", "incarnation", "owner", "reason", "action", "scope", "failure")}
                                for r in records], "acknowledged": False})
                for old in records:
                    if old["id"] != ident:
                        self.store.db.execute("DELETE FROM records WHERE id=?", (old["id"],))
        return list(self.store.selected("SELECT * FROM records INDEXED BY observation_pending "
            "WHERE kind='observation' AND json_extract(data,'$.state')='pending'"))

    async def recover(self, current=False):
        # A crash can leave an acknowledged row before its live future retires.
        # Its atomic audit remains; neither it nor old round-2 terminal rows
        # should be materialized by startup. Both queries use partial indexes.
        # During maintenance, a merged member may still be consuming its
        # committed acknowledgement. Only its own observe() finally retires
        # that row. Startup has no live consumers of crash leftovers.
        if not current and not self.live:
            self.store.db.execute("DELETE FROM records WHERE kind='observation' "
                                  "AND json_extract(data,'$.state')!='pending'")
        pending = list(self.store.selected("SELECT * FROM records INDEXED BY observation_pending "
            "WHERE kind='observation' AND json_extract(data,'$.state')='pending'"))
        if current:
            pending = self.repairs(pending)
        self.retry_needed = False
        async def replay(record):
            if record["id"] in self.live:
                return
            if record["generation"] == self.store.generation and not current:
                self.retry_needed = True
                return
            if current and record.get("retry_after", 0) > time.time():
                self.retry_needed = True
                return
            applications = [{"checkout": self.store.get(key), "paths": paths, "ignore_pending": False,
                             "preserve_leads": True} for key, paths in record["scope"].items()]
            if current:
                for application in applications:
                    key, paths = application["checkout"]["id"], application["paths"]
                    def quiet(key=key, paths=paths):
                        self.ledger.refuse_mutations(key, paths)
                    application["guard"] = quiet
            try:
                await self.observe(applications, "interrupted observation: " + record["id"],
                                   _replay=record, owner=record.get("owner"))
            except Exception as exc:
                self.retry_needed = True
                if current:
                    return  # A live guard's refusal is not a checkout quarantine.
                # Keep the obligation pending. Failure is visible and cannot
                # strand every writer's startup barrier before it is launched.
                with self.store.transaction():
                    for key in record["scope"]:
                        checkout = self.store.get(key)
                        checkout["quarantine"] = getattr(exc, "code", type(exc).__name__)
                        self.store.save(checkout)
                        self.store.unknown(key, "*", "interrupted observation could not be recovered",
                                           {"observation": record["id"], "code": checkout["quarantine"]})
                    self.store.event("observation/recovery_failed", record["id"], {"code": getattr(exc, "code", type(exc).__name__)})
        await finish_all(*(replay(record) for record in pending))


def compatible(record):
    return (record["reason"] in ("opaque command effects", "turn-end observation")
            and all(p["ignore_pending"] and not p["attribute"] and not p["guard"]
                    and not p["preserve_leads"] and not p["settling_operation"] for p in record["policy"]))
