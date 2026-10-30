"""Scoped durable run observation and explicit control; no exec watchdog IDs."""
import asyncio
from contextlib import nullcontext
import json
import os
import time
from pathlib import Path

from .locks import KeyedLocks, finish_thread
from .client import CoordinatorError
from .store import WINDOW_ACTIVE, uid, digest
from .writer import TERMINAL
from . import processes, participants, rollout, native


class Runs:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.cancel_locks = KeyedLocks()
        self.cancel_passes = {}  # run -> cancel passes still interrupting and terminating: no cancelled meanwhile
        self.cancel_reconciled = {}  # run -> monotonic time of its cancel's last reconcile: one each 2 s at most
        self.settle_locks = KeyedLocks()  # run -> one settlement of its unit's inputs of unknown outcome at a time
        self.input_reads = {}  # run -> monotonic time of maintenance's last settlement of its deliveries: one each 2 s

    def visible(self, run, cwd):
        from .observations import checkout_at
        checkout, changed = checkout_at(self.store, cwd)
        if checkout and not changed and participants.contains(self.store, run["unit"], checkout["id"]):
            return True
        workspace = os.path.realpath(cwd)
        return any(path and os.path.commonpath((workspace, os.path.realpath(path))) == workspace
                   for path in (run.get("dispatch_cwd"), run.get("target_cwd")))

    def get(self, runid, cwd=None):
        run = self.store.get(runid)
        if run.get("_kind") != "run" or cwd is not None and not self.visible(run, cwd):
            raise CoordinatorError("RUN_SCOPE", "Run is not visible from this dispatch or target folder")
        return run

    def reported(self, run):
        """The run's state as its readers see it, with the unit's writer turns running: a finished or idle run whose
        writer runs a turn (a message's, or a sub-agent's) is running that turn. Derived from the actors' turns, never
        stored, so a turn's end read later (a reconcile's history) leaves nothing behind. A requested cancel's states
        stand (cancelling, cancelled)."""
        turns = []
        for actor in self.store.all("actor", run["unit"]):
            if actor.get("active_turn") and actor["kind"] != "lead":
                turns.append(dict({"actor": actor["id"], "kind": actor["kind"], "thread": actor.get("thread"),
                                   "turn": actor["active_turn"]}, **self.turn_source(run, actor)))
        state = run["state"]
        if turns and not run.get("cancel_requested") and state in TERMINAL | {"idle"}:
            state = "running"
        return state, turns

    def turn_source(self, run, actor):
        """What started a root turn: the phase whose input it took, or the message whose delivery started it."""
        turn = actor["active_turn"]
        if actor["kind"] != "root":
            return {}
        phase = next((name for name, p in (run.get("phases") or {}).items() if p.get("turn") == turn), None)
        if phase:
            return {"phase": phase}
        # Through the unit's mail (message_unit, then record_parent): never every delivery in the ledger.
        row = self.store.db.execute("SELECT json_extract(d.data,'$.message') FROM records m JOIN records d ON d.parent=m.id AND "
                                    "d.kind='delivery' WHERE m.kind='message' AND json_extract(m.data,'$.unit')=? AND "
                                    "json_extract(d.data,'$.result.turn.id')=? LIMIT 1", (run["unit"], turn)).fetchone()
        return {"message": row[0]} if row else {}

    def summary(self, run, listed=False):
        unit = self.store.get(run["unit"])
        surface = participants.surface(self.daemon, unit)
        state, turns = self.reported(run)
        ident = (self.store.get(run.get("root_actor") or "", required=False) or {}).get("listener") or ""
        if listed:
            listener = self.listed_listener(ident)
        else:
            from .dormancy import view
            listener = view(self.store.get(ident, required=False) or {})
        own = next((p["unknown_count"] for p in surface["participants"] if p["checkout"] == run["checkout"]), None)
        return {"run": run["id"], "revision": run.get("_revision", 0) + surface["participant_revision"], "backend": "native", "unit": unit["id"], "unit_state": unit["state"],
                "phase": run.get("phase"), "state": state, "listener": listener, "turns": turns, "created": run.get("created", 0),
                "dispatch_cwd": run.get("dispatch_cwd"), "target_cwd": run.get("target_cwd"),
                "claims": [c for p in surface["participants"] if p["checkout"] == run["checkout"] for c in p["claims"]],
                "custody": [{k: v for k, v in c.items() if k != "process_baseline"}
                            for c in self.daemon.custody.unit_active(run["unit"]) if c["unit"] == unit["id"]],
                "unread_messages": self.store.db.execute("SELECT count(*) FROM records WHERE kind='message' AND "
                    "json_extract(data,'$.unit')=? AND json_extract(data,'$.state')!='acknowledged'", (unit["id"],)).fetchone()[0],
                "unknown_count": own if own is not None else self.store.unresolved_count(run["checkout"]),
                **surface,
                "actors": [{k: a.get(k) for k in ("id", "kind", "thread", "active_turn", "state")} for a in self.store.all("actor", unit["id"])]}

    def listed_listener(self, ident):
        """A listed run's listener: dormancy.view without the writer profile, which run/events and full daemon/status
        keep, read in SQL rather than decoded whole. A dormant listener's record is ~100 KB; the profiles were 130 of
        the 420 KiB of one run/list answer (10-29, 91 runs)."""
        row = self.store.db.execute("SELECT json_extract(data,'$.state'), json_extract(data,'$.dormant_at'), "
                                    "json_extract(data,'$.dormant_reason'), json_extract(data,'$.history_gaps') "
                                    "FROM records WHERE id=?", (ident,)).fetchone()
        state, dormant_at, reason, gaps = row if row else (None, None, None, None)
        return {"state": state, "dormant_at": dormant_at, "dormant_reason": reason,
                "history_gaps": json.loads(gaps) if gaps is not None else None}

    async def listing(self, cwd, limit=50):
        """run/list, which the Codex Office asks for every 2 s and gives up on after 2 s (office.py NATIVE_TIMEOUT_S).
        The runs are chosen from a few fields read in SQL, newest first through visible_run, reading no further than the
        last one listed. The index is named: without statistics SQLite chose record_kind and sorted every run first.
        Then they are summarised one at a time, each as of its own turn of the event loop: through 2.4.14 one listing
        held the loop for all of its 7-30 s on the user's coordinator (10-29, 91 runs), while hooks, native intake and
        every other reply waited."""
        from .observations import checkout_at
        checkout, changed = checkout_at(self.store, cwd)
        workspace = os.path.realpath(cwd)
        chosen = []
        for ident, unit, dispatched, target in self.store.db.execute(
                "SELECT id, json_extract(data,'$.unit'), json_extract(data,'$.dispatch_cwd'), json_extract(data,'$.target_cwd') "
                "FROM records INDEXED BY visible_run WHERE kind='run' AND "
                "(json_extract(data,'$.workflow') IS NOT NULL OR json_extract(data,'$.project_revision') IS NOT NULL) "
                "ORDER BY json_extract(data,'$.created') DESC,id DESC"):
            if (checkout and not changed and participants.contains(self.store, unit, checkout["id"]) or
                    any(path and os.path.commonpath((workspace, os.path.realpath(path))) == workspace
                        for path in (dispatched, target))):
                chosen.append(ident)
                if len(chosen) >= max(1, min(limit, 100)):
                    break
        data = []
        for ident in reversed(chosen):
            await asyncio.sleep(0)
            run = self.store.get(ident, required=False)
            if run is not None:
                data.append(self.summary(run, listed=True))
        return {"data": data, "generation": self.store.generation}

    async def events(self, run, cursor=0, limit=100):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 400:
            raise CoordinatorError("EVENT_CURSOR", "Use a nonnegative sequence cursor and limit 1..400")
        subjects = {run["id"], run["unit"], run["attempt"]}
        # Ids only, through indexes: decoding every record of these kinds made each poll cost the
        # whole ledger. Actors, operations, custody and claims are stored under their unit.
        for kind in ("actor", "operation", "custody", "claim"):
            subjects.update(row["id"] for row in self.store.db.execute("SELECT id FROM records WHERE parent=? AND kind=?", (run["unit"], kind)))
        subjects.update(row["id"] for row in self.store.db.execute(
            "SELECT id FROM records WHERE kind='message' AND json_extract(data,'$.unit')=?", (run["unit"],)))
        subjects.update(r["id"] for r in self.store.all("listener", run["id"]))
        from . import archive
        from .locks import finish_thread
        rows = await archive.page(self.store, subjects, cursor, limit + 1)
        data = await finish_thread(lambda: [dict(row, data=json.loads(row["data"])) for row in rows[:limit]])
        listener = self.store.get((self.store.get(run.get("root_actor") or "", required=False) or {}).get("listener") or "", required=False) or {}
        from .dormancy import view
        from .retention import archives
        return {"run": run["id"], "listener": view(listener), "data": data, "cursor": data[-1]["seq"] if data else cursor,
                "archives": archives(self.store, [r["id"] for r in self.store.all("listener", run["id"])]),
                "more": len(rows) > limit, "state": self.reported(self.store.get(run["id"]))[0]}

    def cancel_current(self, runid, generation):
        """Whether the cancel of this generation still stands: no resume has cleared it and no newer cancel replaced
        it. A pass or a settlement of a cancel that no longer stands interrupts, deletes and terminates nothing more."""
        run = self.store.get(runid)
        return bool(run.get("cancel_requested")) and run.get("cancel_generation", 0) == generation

    def superseded(self, runid, generation):
        self.store.event("run/cancel_superseded", runid, {"generation": generation})
        return False

    @staticmethod
    def expired(run):
        """A workflow still running past its deadline: maintenance cancels it."""
        return bool(run.get("workflow") and not run.get("cancel_requested")
                    and run["state"] not in ("completed", "failed", "interrupted", "blocked", "refused", "cancelled")
                    and run["workflow"].get("deadline", 0) > 0 and time.time() >= run["workflow"]["deadline"])

    async def cancel(self, run, expired=False):
        run = self.store.get(run["id"])  # maintenance's deadline cancel runs later than its read
        if expired and (self.daemon.draining or not self.expired(run)):
            # Maintenance read the run before the awaits of the runs ahead of it: resumed with a new deadline, finished
            # or cancelled since, its old deadline no longer holds. While a stop drains, the next daemon cancels it: its
            # settlement could start a writer's replacement after the stop listed the app-servers it ends (a run waiting
            # for the lead holds no stop). Nothing awaits between this check and the save below.
            return self.summary(run)
        # Each cancel is a new generation: a resume that began before it does not clear it (resume compares them), and
        # each step of this cancel's pass and of its settlement first checks that it still stands (cancel_current).
        generation = run.get("cancel_generation", 0) + 1
        run.update(cancel_requested=True, cancel_generation=generation, state="cancelling")
        self.store.save(run)
        self.store.event("run/cancel_requested", run["id"], {"generation": generation})
        failures = []
        # While a pass runs, no settlement reports the run cancelled: a resume could then clear the cancel and start a
        # turn before the pass's next interrupt.
        self.cancel_passes[run["id"]] = self.cancel_passes.get(run["id"], 0) + 1
        try:
            stands = await self.cancel_pass(run, generation, failures)
        finally:
            self.cancel_passes[run["id"]] -= 1
            if not self.cancel_passes[run["id"]]:
                del self.cancel_passes[run["id"]]
        deadline = time.monotonic() + 30
        while stands and time.monotonic() < deadline:
            if await self.settle_cancel(run["id"]):
                break
            await asyncio.sleep(0.1)
        return dict(self.summary(self.store.get(run["id"])), cancellation_failures=failures,
                    superseded=not self.cancel_current(run["id"], generation),
                    meaning="cancelled requires terminal events and verified process settlement; cancelling retains custody")

    async def cancel_pass(self, run, generation, failures):
        """The cancel's pass over its unit: queued work, each actor's turn, terminals and processes. Each interrupt,
        deletion and termination first checks, after the awaits before it, that the cancel still stands. Returns
        whether it stood at the end."""
        runid = run["id"]
        root = self.store.get(run["root_actor"])
        if root.get("listener") in self.daemon.connections:
            # Not while its app-server is being replaced (no connection): settle_cancel's reconcile, which waits for
            # the replacement, discovers them on the incarnation that is current then.
            await self.daemon.discover_descendants(self.store.get(root["listener"]))
        for message in list(self.store.all("message")):
            if (message.get("unit") != run["unit"] or message["state"] in ("entered_context", "acknowledged")
                    or self.daemon.lead_mail(message)):
                continue  # a lead reads its mail through its inbox: it is no work of the run
            async with self.daemon.messages.locks.hold(message["id"]):
                message = self.store.get(message["id"])
                if message["delivery"] == "pending" and self.cancel_current(runid, generation):
                    message["delivery"] = "cancelled"
                    self.store.save(message)
        for actor in list(self.store.all("actor", run["unit"])):
            if not actor.get("listener"):
                continue
            connection = self.daemon.connections.get(actor["listener"])
            if not connection and (self.store.get(actor["listener"]).get("recovery_error") == "ENDED" or self.store.get(actor["listener"])["state"] == "dormant"):
                continue  # ended to settle an input (end_writer): nothing runs there; its queue is read on its replacement
            try:
                from .native import page
                connection = self.daemon.listener_connection(self.store.get(actor["listener"]))
                async for queued in page(connection, "thread/queue/list", {"threadId": actor["thread"]}):
                    for entry in queued["data"]:
                        if not self.cancel_current(runid, generation):
                            return self.superseded(runid, generation)
                        deleted = await connection.call("thread/queue/delete", {"threadId": actor["thread"], "queuedSubmissionId": entry["id"]})
                        if not deleted.get("deleted"):
                            # Native started it first: its dispatch and a deletion hold one guard (ext/queue/src/service.rs).
                            # Its turn is interrupted below, and its own records settle the message.
                            continue
                        for message in list(self.store.all("message")):
                            if message.get("unit") == run["unit"] and entry["clientUserMessageId"] in (message["native_id"], message["native_id"] + ":relay"):
                                message["delivery"] = "cancelled"
                                self.store.save(message)
                                self.store.event("message/cancelled", message["id"], {"queue_id": entry["id"]})
                # Read after the queue's awaits: native serializes a thread's requests, so a turn/start in flight for
                # it (a submission, a delivery) may have started its turn meanwhile, and a resume its own.
                actor = self.store.get(actor["id"])
                if not self.cancel_current(runid, generation):
                    return self.superseded(runid, generation)
                if actor.get("active_turn") and actor.get("interrupted_turn") != actor["active_turn"]:
                    await connection.call("turn/interrupt", {"threadId": actor["thread"], "turnId": actor["active_turn"]})
                    self.interrupted(actor["id"], actor["active_turn"])
                terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
                if terminals.get("nextCursor"):
                    raise CoordinatorError("TERMINAL_PAGINATION", "Cancellation terminal inventory is incomplete")
                for terminal in terminals["data"]:
                    if not self.cancel_current(runid, generation):
                        return self.superseded(runid, generation)
                    await connection.call("thread/backgroundTerminals/terminate", {"threadId": actor["thread"], "processId": terminal["processId"]})
                for reservation in self.daemon.custody.unit_active(run["unit"]):
                    if reservation["actor"] == actor["id"]:
                        for process in reservation.get("processes", []):
                            if not self.cancel_current(runid, generation):
                                return self.superseded(runid, generation)
                            await self.daemon.leases.call("end-member", payload={"listener": actor["listener"], "member": process})
            except (CoordinatorError, OSError) as exc:
                failures.append({"actor": actor["id"], "code": getattr(exc, "code", type(exc).__name__)})
        return self.cancel_current(runid, generation) or self.superseded(runid, generation)

    async def interrupt_turn(self, actor, turn, generation):
        """Interrupt a turn of a run whose cancel of this generation still stands. One that started after the cancel's
        pass (its turn/start was in flight then, or a queued message started it) is found by its turn/started, or by
        the cancel's settlement; its calls are refused meanwhile (RUN_CANCELLED)."""
        if not self.cancel_current(actor["run"], generation):
            return  # a resume cleared the cancel since then, or a newer cancel interrupts it
        if self.store.get(actor["id"]).get("interrupted_turn") == turn:
            return  # aborted already: its end is read with its notification or the next history read
        connection = self.daemon.connections.get(actor.get("listener"))
        try:
            if not connection or connection.closed:
                raise CoordinatorError("LISTENER_RECOVERING", "The writer's app-server is not connected")
            await connection.call("turn/interrupt", {"threadId": actor["thread"], "turnId": turn})
            self.interrupted(actor["id"], turn)
            self.store.event("run/turn_interrupted", actor["run"], {"actor": actor["id"], "turn": turn})
        except (CoordinatorError, OSError) as exc:
            self.store.event("run/turn_interrupt_refused", actor["run"], {"actor": actor["id"], "turn": turn,
                                                                          "code": getattr(exc, "code", type(exc).__name__)})

    def interrupted(self, actorid, turn):
        """Native answers turn/interrupt once the turn has aborted, so it is not interrupted again: a second interrupt
        of a turn that had aborted went unanswered until its 60 s timeout (codex 0.163.0, measured), holding the
        cancel's settlement meanwhile. Its end is read with its notification or the next history read."""
        actor = self.store.get(actorid)
        if actor.get("active_turn") == turn:
            actor["interrupted_turn"] = turn
            self.store.save(actor)

    async def settle_cancel(self, runid):
        async with self.cancel_locks.hold(runid):
            done = await self._settle_cancel(runid)
            if done:
                self.cancel_reconciled.pop(runid, None)
            return done

    async def _settle_cancel(self, runid):
        """Report the run cancelled once nothing of it runs or can still start. Returns True when that is done, or
        when the cancel no longer stands (a resume cleared it, or a newer cancel settles itself)."""
        run = self.store.get(runid)
        if not run.get("cancel_requested"):
            return True
        if run["state"] == "cancelled":
            # Settled by the caller this one waited for (the cancel's own loop, maintenance): reported once. A newer
            # cancel is a new generation, which sets cancelling first.
            return True
        generation = run.get("cancel_generation", 0)
        # A pass that reads native (reconciles, an input's outcome) at most every 2 s, by a monotonic clock: a wall
        # clock set back would hold the next one, and a cancel that waits for it.
        due = time.monotonic() - self.cancel_reconciled.get(runid, float("-inf")) > 2
        if due:
            self.cancel_reconciled[runid] = time.monotonic()
            from .recovery import reconcile_listener, retry_due
            for ident in {a["listener"] for a in self.store.all("actor", run["unit"]) if a.get("listener")}:
                listener = self.store.get(ident)
                if listener.get("recovery_error") == "ENDED" or listener["state"] == "dormant":
                    continue  # ended to settle an input (end_writer): nothing runs there, and a cancel needs no replacement
                if listener.get("recovery_state") == "failed" and not retry_due(self.daemon, listener):
                    continue  # the one retry policy: not before its backoff, and one that cannot heal waits for a resume
                try:
                    await reconcile_listener(self.daemon, listener, advance=False, scheduled=True)
                except (CoordinatorError, OSError) as exc:
                    self.store.event("run/cancel_reconcile_refused", runid, {"code": getattr(exc, "code", type(exc).__name__)})
            # Every turn still active is interrupted again: one the cancel's pass could not see, and one whose
            # interrupt was lost with its app-server. Only through a live connection: one without is interrupted on a
            # pass after it reconnects, by this generation, as read then. Through 2.4.15 each pass recorded its refusal
            # (10-29: 3,734 events in 5.6 h for two writers whose readmission could not pass).
            for actor in list(self.store.all("actor", run["unit"])):
                connection = self.daemon.connections.get(actor.get("listener"))
                if actor.get("active_turn") and connection and not connection.closed:
                    await self.interrupt_turn(actor, actor["active_turn"], generation)
        for actor in list(self.store.all("actor", run["unit"])):
            await self.daemon.settle_actor(actor)
        if not self.cancel_current(runid, generation):
            return True
        if any(a.get("active_turn") for a in self.store.all("actor", run["unit"])) or any(c["unit"] == run["unit"] for c in self.daemon.custody.unit_active(run["unit"])):
            return False
        if self.cancel_passes.get(runid):
            return False  # a cancel's pass is still interrupting and terminating: cancelled only once it ends
        # An input whose RPC is in flight in this daemon is waited for: its result, or its loss, tells what it started.
        # An input whose native outcome is unknown (a lost turn/start, a delivery not in context yet) may still start a
        # turn: each is settled, on a pass that reads native, by reads in its thread's order or, where those cannot
        # tell, by ending the app-server it went to and reading what that saved (settle_inputs). Until then the run
        # stays cancelling.
        if self.in_flight(self.store.get(runid)):
            return False
        if self.unsettled(self.store.get(runid), "cancel"):
            if not due or not await self.settle_inputs(runid, "cancel", generation=generation):
                return False
        if not await self.threads_idle(run):
            return False  # a turn runs that no record shows: the next reconcile finds it, and it is interrupted
        run = self.store.get(runid)  # read after the awaits above: what a notification saved meanwhile is kept
        if not run.get("cancel_requested") or run.get("cancel_generation", 0) != generation or run["state"] == "cancelled":
            return True  # a resume or a newer cancel since then: this cancel saves nothing
        run["state"] = "cancelled"
        self.store.save(run)
        self.store.event("run/cancelled", runid, {"generation": generation, "terminal_and_processes_settled": True})
        await self.daemon.release_lease(run["checkout"])
        return True

    def unsettled(self, run, mode, holding=None):
        """The unit's native inputs whose outcome is not known, for a settlement in `mode` ("cancel", "resume", or
        "background": maintenance's, of deliveries). The run's phase input whose turn/start result was lost
        (submitting, uncertain) or, answered, whose turn was never seen to end (submitted); unless for a resume, each
        writer delivery not in context whose result was lost (submitting, uncertain) or, answered and not seen to
        enter (submitted), one for a cancel, and for maintenance once no turn of the unit runs. One whose RPC is in
        flight in this daemon is not: its start is in writer.starting, and a delivery holds its message's lock
        (`holding`: the caller's own)."""
        found = []
        actors = list(self.store.all("actor", run["unit"]))
        root = next((a for a in actors if a["id"] == run.get("root_actor")), {})
        if mode != "background":
            name = run.get("phase")
            phase = (run.get("phases") or {}).get(name) or {}
            if (phase.get("status") is None and phase.get("input_id") and phase["input_id"] not in self.daemon.writer.starting
                    and (phase.get("state") in ("submitting", "uncertain") or phase.get("state") == "submitted" and phase.get("turn"))
                    and not (phase.get("turn") and root.get("active_turn") == phase["turn"])):
                found.append({"key": ("phase", name), "client": phase["input_id"], "method": "turn/start",
                              "state": phase["state"], "listener": phase.get("start_listener"), "turn": phase.get("turn"),
                              "texts": [i["text"] for i in phase.get("input", []) if i.get("type") == "text"], "needle": None,
                              "sent": phase.get("submitted"), "since": phase.get("submitted")})
        if mode == "resume":
            return found
        running = any(a.get("active_turn") for a in actors)
        for message in self.store.selected("SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.unit')=? AND "
                                           "json_extract(data,'$.delivery') IN ('submitting','uncertain','submitted') AND "
                                           "json_extract(data,'$.state') NOT IN ('entered_context','acknowledged') ORDER BY id",
                                           (run["unit"],)):
            if self.daemon.lead_mail(message) or not message.get("attempts"):
                continue
            if (message.get("relay") or {}).get("state") == "entered_context":
                continue  # in the root's context: the child's own acknowledgement settles the rest
            if message["id"] != holding and message["id"] in self.daemon.messages.locks.entries:
                continue  # its delivery is under way
            attempt = self.store.get(message["attempts"][-1], required=False) or {}
            if message["delivery"] == "submitted" and mode == "background" and running:
                continue  # native has it: its echo enters it, or it is read once no turn runs
            # Every attempt sends the same client id and text: a record of an earlier one is this message's too.
            first = self.store.get(message["attempts"][0], required=False) or {}
            found.append({"key": ("message", message["id"]), "client": attempt.get("client_id") or message["native_id"],
                          "method": attempt.get("method"), "state": message["delivery"], "listener": attempt.get("listener"),
                          "turn": ((attempt.get("result") or {}).get("turn") or {}).get("id"),
                          "texts": [], "needle": "C1_MESSAGE " + message["id"],
                          "sent": attempt.get("created"), "since": first.get("created")})
        return found

    def in_flight(self, run):
        """A native input of the unit whose RPC is in flight in this daemon: a phase's turn/start (writer.starting), or
        a delivery not in context under its message's lock."""
        return (any(p.get("input_id") in self.daemon.writer.starting for p in (run.get("phases") or {}).values())
                or any(m["id"] in self.daemon.messages.locks.entries and not self.daemon.lead_mail(m)
                       for m in self.store.selected(
                           "SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.unit')=? AND "
                           "json_extract(data,'$.delivery') IN ('submitting','uncertain','submitted') AND "
                           "json_extract(data,'$.state') NOT IN ('entered_context','acknowledged')", (run["unit"],))))

    async def settle_inputs(self, runid, mode, generation=None, holding=None):
        """Settle each of the unit's inputs whose native outcome is not known (unsettled) as its notifications would
        have, from native's own records of that input: it entered a turn (that turn, and its end when it has ended),
        or it waits in native's queue. A record's absence proves nothing, so an input that nothing can start any more
        and nothing shows is unproven: only the lead sends it again (a resume, message/retry). Reads first (locate);
        where they cannot tell, the app-server the input went to is ended, so that nothing can start it, and what it
        saved read (end_writer), with no bound of time in the decision. Returns whether none is left."""
        async with self.settle_locks.hold(runid):
            unqueued = set()  # queued inputs native's queue did not hold when read, on an app-server ended since
            for attempt in (0, 1):
                run = self.store.get(runid)
                if mode == "cancel" and not self.cancel_current(runid, generation):
                    return True
                inputs = self.unsettled(run, mode, holding)
                if not inputs:
                    return True
                ambiguous = {}  # listener to end -> whether it was connected when read
                for item, outcome in await self.locate(run, inputs, mode, unqueued):
                    if outcome[0] == "ambiguous":
                        ambiguous[outcome[2]] = self.connected(outcome[2])
                        if outcome[3]:
                            unqueued.add(item["key"])
                    elif outcome[0] == "wait":
                        self.running(runid, outcome[2])
                    elif item["key"][0] == "phase":
                        self.apply_phase(runid, item, outcome)
                    else:
                        await self.apply_message(runid, item, outcome, mode, generation, holding)
                if not ambiguous or attempt or not await self.end_writer(runid, ambiguous, mode, holding):
                    break
            return not self.unsettled(self.store.get(runid), mode, holding)

    async def settle_delivery(self, message, actor):
        """Settle a delivery whose native outcome is not known, from deliver (maintenance's retries, under the
        message's lock), with the unit's other such deliveries (settle_inputs). Native is read for a run once each
        2 s at most, by a monotonic clock. A cancel settles its run's own (_settle_cancel)."""
        run = self.store.get(actor["run"])
        if run.get("cancel_requested") or self.daemon.draining or self.daemon.recovery_barrier:
            return
        if not self.inputs_due(run["id"]):
            return
        if await self.settle_inputs(run["id"], "background", holding=message["id"]):
            self.input_reads.pop(run["id"], None)

    def inputs_due(self, runid):
        """Whether maintenance's settlement may read native for this run's inputs now: once each 2 s at most, by a
        monotonic clock (a delivery's, settle_delivery; a workflow phase's, writer.advance)."""
        if time.monotonic() - self.input_reads.get(runid, float("-inf")) < 2:
            return False
        self.input_reads[runid] = time.monotonic()
        return True

    async def locate(self, run, inputs, mode, unqueued=()):
        """Where each input went, as [(input, outcome)]: ("entered", turn, status, source), status that turn's end or
        None while it runs; ("queued", entry); ("unproven", why) when nothing can start it any more and no record of it
        shows where it went (unproven_reason); ("wait", why, running turn); or
        ("ambiguous", why, listener, unqueued) when only ending that app-server can tell, `unqueued` whether native's
        queue, read there in the thread's order, did not hold the input. Each is read from where it went: the
        app-server it was sent to, if it has exited (by birth identity), from what that saved (its rollout, final
        then); if it runs, by reads in its thread's own order on its connection. `unqueued` holds the queued inputs
        whose queue read was on an app-server ended since."""
        root = self.store.get(run["root_actor"])
        groups, outcomes = {}, []
        for item in inputs:
            groups.setdefault(item["listener"] or root.get("listener"), []).append(item)
        for ident, items in groups.items():
            if not await self.app_server_runs(ident):
                outcomes += await self.locate_saved(run, root, ident, items, unqueued)
            elif ident == root.get("listener") and self.connected(ident):
                outcomes += await self.locate_live(run, root, self.daemon.connections[ident], items, mode)
            else:
                outcomes += [(i, ("ambiguous", "its app-server runs without a connection to read it in order", ident, False))
                             for i in items]
        return outcomes

    def connected(self, ident):
        connection = self.daemon.connections.get(ident)
        return bool(connection and not connection.closed and (self.store.get(ident, required=False) or {}).get("state") == "connected")

    async def ordered_reads(self, connection, thread, queue, turns=True, inputs=(), actor=None):
        """Keep ordered queue/status fences around paginated observation. Native pages
        are not ordered thread requests. A tail that changes during the scan counts as
        active/uncertain, never as evidence that an input is absent."""
        from .native import page, quiet_status
        try:
            entries = {}
            if queue:
                async for result in page(connection, "thread/queue/list", {"threadId": thread}):
                    entries.update({e.get("clientUserMessageId"): e["id"] for e in result["data"]})
            status = await connection.call("thread/read", {"threadId": thread, "includeTurns": False})
            reason = quiet_status(status["thread"].get("status"))
            if reason and reason != "native_active_turn":
                raise CoordinatorError("NATIVE_STATUS_UNKNOWN", reason)
            active = reason is not None
            actors = list(self.store.selected("SELECT * FROM records WHERE kind='actor' AND json_extract(data,'$.thread')=?", (thread,)))
            actor = actor or next((a for a in actors if self.daemon.connections.get(a.get("listener")) is connection), None)
            if actor is None:
                raise CoordinatorError("HISTORY_BINDING", "Input history has no current actor binding")
            history = {}
            if turns or not active:
                await self.daemon.history.observe(self.store.get(actor["listener"]), actor, connection)
                read = await connection.call("thread/read", {"threadId": thread, "includeTurns": False})
                reason = quiet_status(read["thread"].get("status"))
                if reason and reason != "native_active_turn":
                    raise CoordinatorError("NATIVE_STATUS_UNKNOWN", reason)
                active = active or reason is not None
                tail = await connection.call("thread/turns/list", {"threadId": thread, "itemsView": "notLoaded",
                                                                  "sortDirection": "desc", "limit": 1})
                latest = tail["data"][0]["id"] if tail["data"] else None
                cp = self.daemon.history.checkpoint(actor)
                active = active or latest != cp.get("boundary")
                for item in inputs:
                    found = self.daemon.history.input(actor, item["client"])
                    known = self.daemon.history.turn(actor, item["turn"]) if item.get("turn") else None
                    for fact in (found, known):
                        if fact:
                            history.setdefault(fact["turn"], {"id": fact["turn"], "status": fact["status"], "items": []})
                    if found:
                        history[found["turn"]]["items"].append({"type": "userMessage", "clientId": item["client"]})
                if latest:
                    fact = self.daemon.history.turn(actor, latest)
                    if fact:
                        history.setdefault(latest, {"id": latest, "status": fact["status"], "items": []})
        except (CoordinatorError, OSError) as exc:
            return None, getattr(exc, "code", type(exc).__name__)
        active = active or any(t["status"] == "inProgress" for t in history.values())
        return {"queue": entries, "active": active, "turns": list(history.values()),
                "running": next((t["id"] for t in history.values() if t["status"] == "inProgress"), None)}, None

    @staticmethod
    def in_history(turns, client):
        return next((t for t in turns if any(i.get("type") == "userMessage" and i.get("clientId") == client
                                             for i in t.get("items") or [])), None)

    async def locate_live(self, run, root, connection, items, mode):
        """Inputs sent to the running, connected app-server: read in the thread's order. A turn/start or a queue/add
        is told only by its record: native answers turn/start once core holds the turn, but reports the thread active
        and records the input once that turn's task runs, so an idle thread without it may still be about to start
        it (tasks/mod.rs start_task, tasks/regular.rs); and a queued input leaves the queue once its turn has started,
        before it is recorded (ext/queue/src/service.rs). Nothing can start an input any more once the turn native
        started for it has ended, nor a steer once no turn runs: the turn it entered records it when it takes it or at
        its finish, and an abort drops it (tasks/mod.rs on_task_finished, abort_all_tasks). Without its record, such an
        input is unproven: history is native's reading of the rollout, which lacks what native failed to write."""
        reads, error = await self.ordered_reads(connection, root["thread"], any(i["method"] == "thread/queue/add" for i in items),
                                                turns=mode != "background", inputs=items)
        if reads is None:
            return [(i, ("ambiguous", "its thread could not be read: " + error, root["listener"], False)) for i in items]
        outcomes = []
        for item in items:
            turn = self.in_history(reads["turns"], item["client"])
            if turn:
                outcome = ("entered", turn["id"], None if turn["status"] == "inProgress" else turn["status"], "history")
            elif item["method"] == "thread/queue/add" and item["client"] in reads["queue"]:
                outcome = ("queued", reads["queue"][item["client"]])
            elif reads["active"]:
                outcome = ("wait", "a turn runs that may take it", reads["running"])
            elif item["turn"] and any(t["id"] == item["turn"] for t in reads["turns"]):
                outcome = ("unproven", "the turn native started for it has ended, and its thread's history holds no record "
                                       "of it, " + self.unproven_reason(run, root, root["listener"], item))
            elif item["method"] == "turn/steer":
                outcome = ("unproven", "no turn runs, and its thread's history holds no record of it (a steer enters its "
                                       "turn or is dropped with it), " + self.unproven_reason(run, root, root["listener"], item))
            elif item["state"] == "submitted" and mode == "background":
                # Native answered it: its turn is about to start, or it is about to leave the queue for one. A cancel
                # or a resume tells by ending the app-server; maintenance waits for it to show.
                outcome = ("wait", "native answered it, and its turn has not shown yet", None)
            else:
                # Read after it in the thread's order, native's queue does not hold a queued one: once this app-server
                # has ended, what it saved tells whether a turn took it (locate_saved).
                outcome = ("ambiguous", "the thread is idle and holds no record of it", root["listener"],
                           item["method"] == "thread/queue/add")
            outcomes.append((item, outcome))
        return outcomes

    async def locate_saved(self, run, root, ident, items, unqueued=()):
        """Read an exited writer's saved history through native without resuming it.

        A replacement that already runs retains the old queue/status ordering rules.
        Otherwise an owned, temporary reader queries the unloaded thread; its durable
        queue cannot dispatch. Raw rollout facts supplement absence with diagnostics,
        never replace native cursors or authorize an automatic resend.
        """
        replacement = root.get("listener") if root.get("listener") != ident else None
        if replacement and not (self.connected(replacement) and await self.app_server_runs(replacement)):
            replacement = None
        try:
            if replacement:
                reads, error = await self.ordered_reads(self.daemon.connections[replacement], root["thread"], True, inputs=items)
            else:
                from .recovery import replay_native
                await replay_native(self.daemon, self.store.get(root["listener"]))
                async with self.daemon.history.offline(root) as connection:
                    reads, error = await self.ordered_reads(connection, root["thread"], True, inputs=items, actor=root)
        except (CoordinatorError, OSError) as exc:
            reads, error = None, getattr(exc, "code", type(exc).__name__)
        if reads is None:
            return [(i, ("wait", "saved native history could not be read: " + error, None)) for i in items]
        wanted = {i["key"]: (i["client"], i["texts"], i["needle"], i.get("since")) for i in items
                  if not self.in_history(reads["turns"], i["client"])}
        cp = self.daemon.history.checkpoint(root) or {}
        path = cp.get("identity", {}).get("path") or run.get("rollout")
        saved = await rollout.diagnose(self.store, root, path, root.get("thread"), wanted) if wanted else {}
        outcomes = []
        for item in items:
            turn = self.in_history(reads["turns"], item["client"])
            if turn:
                status = turn["status"]
                if status == "inProgress":
                    status = None if replacement else "interrupted"
                outcomes.append((item, ("entered", turn["id"], status, "history")))
            elif item["method"] == "thread/queue/add" and item["client"] in reads["queue"]:
                outcomes.append((item, ("queued", reads["queue"][item["client"]])))
            elif replacement and reads["active"]:
                outcomes.append((item, ("wait", "a turn runs on the replacement that may have taken it", reads["running"])))
            elif replacement and item["method"] == "thread/queue/add" and item["key"] not in unqueued:
                outcomes.append((item, ("ambiguous", "native's queue no longer holds it, and the replacement may be starting it",
                                        replacement, True)))
            else:
                diagnostic = "" if saved.get("readable") else " (rollout diagnostics unavailable: " + saved.get("reason", "unknown") + ")"
                outcomes.append((item, ("unproven", "its app-server exited, and neither saved native history nor its queue holds it"
                                        + diagnostic + ", " + self.unproven_reason(run, root, ident, item, saved))))
        return outcomes

    def unproven_reason(self, run, root, ident, item, saved=None):
        """What the lead is told of an input nothing can start any more and no record of which shows where it went: no
        proof either way, as native goes on from memory when it fails to write a record, and its log can lose the line
        telling of the failure, so its rollout's silence (or history's) says nothing. What the app-server `ident` and
        its rollout show is named: a failure of its session's or its rollout writer's that native logged from the
        send on, or that its log shows nothing of native's session after the send (rollout.native_mark), for a queued
        input the same of each of its thread's app-servers; damage to the rollout after the input was sent; and a user
        message holding its text without its client id's record (rollout.diagnose)."""
        sent, told = item.get("sent"), []
        hosts = [ident]
        if item["method"] == "thread/queue/add":
            hosts += [l["id"] for l in self.store.all("listener", run["id"]) if l.get("actor") == root["id"] and l["id"] != ident]
        for host in hosts:
            listener = self.store.get(host, required=False) or {}
            if not listener.get("log"):
                if host == ident:
                    told.append("its app-server kept no log of its rollout's writing (started before 2.4.1)")
                continue
            # What it wrote that this daemon has not read yet (it is read to its end before a stop deletes it).
            for operation in self.daemon.read_native_log(host):
                self.daemon.background(self.daemon.settle_logged(host, self.store.get(operation)))
            marks = self.daemon.native_log(self.store.get(host))
            failed = marks.get("failed")
            if sent is not None and failed and failed["time"] >= sent - rollout.MARGIN_S:
                told.append("native logged a failure of its session or rollout writing after it was sent (" + failed["line"] + ")")
            elif sent is not None and host == ident and (marks.get("written") or {}).get("time", float("-inf")) <= sent:
                told.append("the log of its app-server shows nothing of native's session after it was sent")
        if sent is None:
            told.append("when it was sent is not recorded")
        damage = (saved or {}).get("damage")
        if damage and (damage["mended"] or float("inf")) >= (item.get("since") or sent or 0) - rollout.MARGIN_S:
            told.append("that rollout " + damage["reason"])
        if item["key"] in (saved or {}).get("texts", {}):
            turn, when = saved["texts"][item["key"]]
            told.append("a user message of turn " + str(turn) + " holds its text without its client id's record"
                        + (", at a time that cannot be read" if when is None else ""))
        return ("which proves nothing, as native goes on from memory when it fails to write a record and its log can "
                "lose the line telling of it" + (": " + "; ".join(told) if told else ""))

    def apply_phase(self, runid, item, outcome):
        """Record a phase input's outcome as its notifications would have."""
        run = self.store.get(runid)
        phase = (run.get("phases") or {}).get(item["key"][1]) or {}
        if phase.get("status") is not None or phase.get("input_id") != item["client"]:
            return  # settled meanwhile: its notifications, a reconcile's history
        actor = self.store.get(run["root_actor"])
        if outcome[0] == "entered":
            turn, status = outcome[1], outcome[2]
            self.daemon.writer.observe(actor, "item/completed", {"threadId": actor["thread"], "turnId": turn,
                                       "item": {"type": "userMessage", "clientId": item["client"], "id": ""}})
            if status is None:
                # Interrupted while it runs by a cancel, and settled as any turn is.
                actor = self.store.get(actor["id"])
                actor.update(active_turn=turn, state="running")
                self.store.save(actor)
                run = self.store.get(runid)
                if run["state"] == "submission_uncertain":
                    run["state"] = "running"
                    self.store.save(run)
            else:
                self.daemon.writer.observe(actor, "turn/completed", {"threadId": actor["thread"], "turn": {"id": turn, "status": status}})
            self.store.event("workflow/submission_found", runid, {"input_id": item["client"], "turn": turn, "status": status,
                                                                  "source": outcome[3]})
        elif outcome[0] == "unproven":
            # Nothing can tell whether it ran, so nothing sends it again of itself (writer.advance): the lead's
            # explicit resume sends it, ahead of its own text, as the lead's decision (resume); a cancel ends the run
            # without it. Nothing can start it any more, so it holds nothing of the unit in flight (unit_quiet).
            phase.update(state="unproven", unproven=outcome[1])
            if not run.get("cancel_requested"):
                run["state"] = "submission_uncertain"
            self.store.save(run)
            self.store.event("workflow/submission_unproven", runid, {"input_id": item["client"], "proof": outcome[1]})

    def running(self, runid, turn):
        """A turn runs on the root thread that native may not have recorded its input in yet: it may be an unsettled
        input's. It is the root actor's active turn, so a cancel interrupts it and a resume waits for it; the input's
        outcome is read once it ends."""
        actor = self.store.get(self.store.get(runid)["root_actor"])
        if turn and not actor.get("active_turn"):
            actor.update(active_turn=turn, state="running")
            self.store.save(actor)

    async def apply_message(self, runid, item, outcome, mode, generation, holding):
        """Record a delivery's outcome. A delivery under way meanwhile (its lock held) is left to it."""
        ident = item["key"][1]
        if outcome[0] == "wait" or ident != holding and ident in self.daemon.messages.locks.entries:
            return
        async with nullcontext() if ident == holding else self.daemon.messages.locks.hold(ident):
            message = self.store.get(ident)
            if message["state"] in ("entered_context", "acknowledged") or message["delivery"] != item["state"]:
                return  # settled meanwhile
            if mode == "cancel" and not self.cancel_current(runid, generation):
                return
            root = self.store.get(self.store.get(runid)["root_actor"])
            if outcome[0] == "entered":
                self.daemon.messages.entered(root["id"], item["client"], {"settled": outcome[3], "turn": outcome[1]})
                message = self.store.get(ident)
                message["delivery"] = "submitted"
                self.store.save(message)
            elif outcome[0] == "queued" and mode == "cancel":
                connection = self.daemon.connections.get(root["listener"])
                try:
                    params = {"threadId": root["thread"], "queuedSubmissionId": outcome[1]}
                    if connection and not connection.closed:
                        deleted = await connection.call("thread/queue/delete", params)
                    elif not await self.app_server_runs(root["listener"]):
                        async with self.daemon.history.offline(root) as reader:
                            deleted = await reader.call("thread/queue/delete", params)
                    else:
                        raise CoordinatorError("LISTENER_RECOVERING", "The writer's app-server is not connected")
                except (CoordinatorError, OSError) as exc:
                    self.store.event("message/cancel_refused", ident, {"queue_id": outcome[1], "code": getattr(exc, "code", type(exc).__name__)})
                    return
                if not deleted.get("deleted"):
                    return  # native started it first (one guard holds its dispatch and a deletion): its turn's records settle it
                message = self.store.get(ident)
                message["delivery"] = "cancelled"
                self.store.save(message)
                self.store.event("message/cancelled", ident, {"queue_id": outcome[1]})
            elif outcome[0] == "queued":
                message["delivery"] = "submitted"
                self.daemon.messages.transition(message, "submitted", {"native_queue": True, "queue_id": outcome[1]})
            elif outcome[0] == "unproven" and self.daemon.messages.notice(message):
                # A notice (a legacy one 2.4.3 steered) starts no turn and is owed at least once: delivered again as
                # notices are (deliver_notice), into history only, where C1_NOTICE <id> tells its copies apart.
                message["delivery"] = "pending"
                self.store.save(message)
                self.store.event("message/delivery_unproven", ident, {"attempt": message["attempts"][-1], "proof": outcome[1],
                                                                      "possible_duplicate_native_delivery": True})
            elif outcome[0] == "unproven":
                # Nothing can tell whether its turn ran, so it is never delivered again of itself (deliver): only the
                # lead's explicit retry (message/retry) sends it, which may deliver it twice. A cancel ends the run
                # without it: nothing can start it any more.
                message.update(delivery="unproven", unproven=outcome[1] + ". message/retry sends it again, which may deliver it twice")
                self.store.save(message)
                self.store.event("message/delivery_unproven", ident, {"attempt": message["attempts"][-1], "proof": outcome[1]})

    def quiet_but_inputs(self, run, mode, holding=None):
        """Nothing of the run's unit in flight but its inputs of unknown outcome, so ending its app-server cuts no
        work: no turn, custody, admission or live operation, no input start or delivery under way in this daemon, and
        no resume, advance, re-admission or cancel of the run under way but the caller's own (each holds the run's
        writer lock or its cancel lock)."""
        unit = run["unit"]
        if (any(a.get("active_turn") for a in self.store.all("actor", unit)) or self.daemon.custody.unit_active(unit)
                or self.daemon.admitting_units.get(unit)
                or any(self.store.db.execute("SELECT 1 FROM records INDEXED BY quiet_" + kind + " WHERE " + WINDOW_ACTIVE[kind] +
                                             " AND parent=? LIMIT 1", (unit,)).fetchone() for kind in ("operation", "executing"))
                or any(p.get("input_id") in self.daemon.writer.starting for p in (run.get("phases") or {}).values())):
            return False
        if mode != "resume" and run["id"] in self.daemon.writer.locks.entries or mode != "cancel" and run["id"] in self.cancel_locks.entries:
            return False
        if self.cancel_passes.get(run["id"]):
            return False
        return not any(m["id"] != holding and m["id"] in self.daemon.messages.locks.entries for m in self.store.selected(
            "SELECT * FROM records WHERE kind='message' AND json_extract(data,'$.unit')=?", (unit,)))

    async def end_writer(self, runid, ambiguous, mode, holding=None):
        """End the app-servers these inputs went to, so that what each saved is final, once nothing of the unit is in
        flight but its inputs of unknown outcome (quiet_but_inputs) and native reports each of its threads idle. Each
        is ended as one replaced for its hook program is (stop_listener: its birth identity checked, SIGTERM, Codex's
        own drain, then a kill), after a startup interrupt (an empty turnId: Op::Interrupt, answered once submitted)
        ordered after every input sent to its thread before it, so a task one of them started aborts rather than runs
        through the drain (app-server turn_processor.rs turn_interrupt, core tasks/mod.rs abort_all_tasks). Its record
        is then failed with ENDED and no retry wait: maintenance replaces it while work is owed to its writer
        (writer_owed), and a resume or a reconcile replaces it as one that died. `ambiguous` maps each to whether it
        was connected when its inputs were read. Returns whether each has exited."""
        from .recovery import READMIT_DRAIN_S
        if self.daemon.draining or self.daemon.recovery_barrier:
            return False
        async with self.daemon.reconcile_locks.hold(runid):
            for ident, was_connected in ambiguous.items():
                if not ident or not await self.app_server_runs(ident):
                    continue
                if self.connected(ident) != was_connected:
                    return False  # reconciled since it was read (reattached, or replaced): read again first
                run = self.store.get(runid)
                root = self.store.get(run["root_actor"])
                if not self.quiet_but_inputs(run, mode, holding) or not await self.threads_idle(run):
                    return False
                connection = self.daemon.connections.get(ident)
                if connection and not connection.closed:
                    try:
                        await connection.call("turn/interrupt", {"threadId": root["thread"], "turnId": ""})
                    except (CoordinatorError, OSError) as exc:
                        self.store.event("run/startup_interrupt_refused", runid, {"listener": ident, "code": getattr(exc, "code", type(exc).__name__)})
                # Read after the awaits above. Nothing awaits from here to stop_listener's first save (stopping): a
                # delivery, a submission or a hook after it finds the app-server not connected, and is refused.
                if not self.quiet_but_inputs(self.store.get(runid), mode, holding):
                    return False
                listener = self.store.get(ident)
                listener["ended"] = "to settle inputs whose native outcome reads could not tell"
                self.store.save(listener)
                try:
                    await self.daemon.stop_listener(listener, drain_s=READMIT_DRAIN_S, kill=True)
                except (CoordinatorError, OSError) as exc:
                    self.store.event("listener/end_refused", ident, {"run": runid, "code": getattr(exc, "code", type(exc).__name__)})
                    return False
                listener = self.store.get(ident)
                if self.store.get(listener["actor"]).get("listener") == ident:
                    listener.update(recovery_state="failed", recovery_error="ENDED", retry_at=0)
                    self.store.save(listener)
                self.store.event("listener/ended", ident, {"run": runid, "reason": listener["ended"]})
        return True

    async def threads_idle(self, run):
        """Whether native reports no turn running on the unit's threads, each read in its thread's own order: a turn
        whose start ended after a reconcile's history pages (those run beside a thread's ordered requests) runs though
        no record shows it. A thread that cannot be read counts as running unless native does not hold it loaded, and
        one whose app-server runs without a connection counts as running."""
        for actor in list(self.store.all("actor", run["unit"])):
            if not actor.get("thread") or not actor.get("listener"):
                continue
            connection = self.daemon.connections.get(actor["listener"])
            if not connection or connection.closed:
                if await self.app_server_runs(actor["listener"]):
                    return False
                continue
            try:
                read = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
            except (CoordinatorError, OSError) as exc:
                self.store.event("run/cancel_status_unread", run["id"], {"actor": actor["id"], "code": getattr(exc, "code", type(exc).__name__)})
                if await self.thread_loaded(connection, actor["thread"]) is not False:
                    return False
                continue  # only a loaded thread runs a turn
            reason = native.quiet_status(read["thread"].get("status"))
            if reason and reason != "native_active_turn":
                raise CoordinatorError("NATIVE_STATUS_UNKNOWN", reason, {"actor": actor["id"]})
            if reason:
                return False
        return True

    async def app_server_runs(self, listenerid):
        """Whether the app-server of this listener record still runs (its process, by birth identity), or may."""
        listener = self.store.get(listenerid, required=False) or {}
        if not listener.get("pid"):
            return False
        try:
            current = await finish_thread(processes.identity, listener["pid"])
        except CoordinatorError:
            return True  # its process cannot be read
        return bool(current and current["birth"] == listener.get("birth") and not current.get("zombie"))

    async def thread_loaded(self, connection, thread):
        """Whether native holds the thread loaded (thread/loaded/list), or None when it cannot say."""
        from .native import page
        try:
            async for result in page(connection, "thread/loaded/list", {}):
                if any((entry if isinstance(entry, str) else entry.get("id")) == thread for entry in result["data"]):
                    return True
        except (CoordinatorError, OSError):
            return None
        return False

    @staticmethod
    def resendable(run):
        """Whether a resume sends the run's current phase input again, ahead of its own text: one whose outcome
        nothing can tell (unproven), which only the lead's resume sends."""
        return (run.get("phases") or {}).get(run.get("phase"), {}).get("state") == "unproven"

    @staticmethod
    def awaiting_lead(run):
        """A run that waits only for the lead's decision on its unproven input: nothing of it can run until a resume
        sends that input again or a cancel ends the run, so, like a blocked run, it holds no stop and no compatibility
        lease (the resume's submission takes the lease again)."""
        return run["state"] == "submission_uncertain" and Runs.resendable(run)

    async def resume(self, run, text, deadline=None, images=None):
        from .writer import image_paths, IMAGES_NUDGE
        images = image_paths(images)
        if images and not text:
            text = IMAGES_NUDGE
        if not text:
            return self.daemon.writer.result(run["id"]) if run.get("workflow") else self.summary(run)
        if deadline is None and not run.get("workflow"):
            deadline = time.time() + 3600
        if type(deadline) not in (int, float) or not time.time() < deadline <= time.time() + 12600:
            raise CoordinatorError("RUN_DEADLINE", "A resumed turn requires a fresh deadline within the supported runtime budget")
        generation = self.store.get(run["id"]).get("cancel_generation", 0)
        async with self.daemon.writer.locks.hold(run["id"]):
            from .recovery import reconcile_listener
            if self.store.get(run["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Reopen a sealed unit before resuming; published units cannot resume")
            await self.daemon.dormancy.start(run["id"], "resume")
            # A prior input whose native outcome is not known is settled first: by reads, or by ending the app-server
            # it went to and reading what that saved (settle_inputs). Never resumed beside it, nor left unresumable.
            # A replacement reads what a rollout could not tell.
            for replaced in (False, True):
                if not self.unsettled(self.store.get(run["id"]), "resume") or await self.settle_inputs(run["id"], "resume"):
                    break
                if any(a.get("active_turn") for a in self.store.all("actor", run["unit"])):
                    raise CoordinatorError("RUN_ACTIVE", "Collect this run; an active or uncertain turn cannot be resumed")
                if replaced:
                    raise CoordinatorError("SUBMISSION_UNCERTAIN", "The prior input's native outcome cannot be told while "
                                           "work of its unit is in flight; resume again once it is quiet")
                await reconcile_listener(self.daemon, self.store.get(self.store.get(run["root_actor"])["listener"]), advance=False)
            root = self.store.get(run["root_actor"])
            # Reconcile without advancing the workflow while this control lock is held.
            await reconcile_listener(self.daemon, self.store.get(root["listener"]), advance=False)
            run = self.store.get(run["id"])
            eligible = TERMINAL | ({"idle"} if not run.get("workflow") else set())
            # A run whose phase input nothing can tell waits for the lead (unproven): this resume sends it, ahead of
            # its own text.
            if (run["state"] not in eligible and not self.resendable(run)) or any(a.get("active_turn") for a in self.store.all("actor", run["unit"])):
                raise CoordinatorError("RUN_ACTIVE", "Collect this run; an active or uncertain turn cannot be resumed")
            if self.store.get(run["unit"])["state"] != "open":
                raise CoordinatorError("UNIT_FROZEN", "Reopen a sealed unit before resuming; published units cannot resume")
            if self.store.unresolved(run["checkout"]):
                raise CoordinatorError("RECOVERY_UNKNOWN", "Resolve UNKNOWN before resuming")
            from .publish import Publisher
            await Publisher(self.daemon).quiescent(self.store.get(run["unit"]))
            # Read after the awaits above. A cancel requested since this resume began (run/interrupt does not wait for
            # this lock) is the newer request and stands. Nothing awaits between this read and the save.
            run = self.store.get(run["id"])
            if run.get("cancel_generation", 0) != generation:
                raise CoordinatorError("RUN_CANCELLED", "The run was cancelled while this resume waited; the cancel stands. "
                                       "Resume again to continue it")
            if (run["state"] not in eligible and not self.resendable(run)) or any(a.get("active_turn") for a in self.store.all("actor", run["unit"])):
                raise CoordinatorError("RUN_ACTIVE", "Collect this run; an active or uncertain turn cannot be resumed")
            old = run.get("phases", {}).get(run.get("phase"), {})
            resent = "resumed_unproven" if old.get("state") == "unproven" else None
            if old.get("state") in ("submitting", "submitted", "uncertain") and old.get("status") is None:
                raise CoordinatorError("SUBMISSION_UNCERTAIN", "Reconcile the prior native input identity before resuming")
            missing = []
            if run["state"] != "completed":
                for item in old.get("input", []):
                    if item.get("type") != "localImage" or item["path"] in images:
                        continue
                    if Path(item["path"]).is_file():
                        images.append(item["path"])
                    else:
                        missing.append(item["path"])
            run.setdefault("previous_phases", []).append(dict(old, phase=run.get("phase")))
            inputs = [{"type": "text", "text": "Continue the same task under the original instructions.\n" + text}]
            if resent:
                # No record shows it in the thread, so the continuation carries it, ahead of the new text. It may have
                # run, its records lost: sending it again is this resume's decision, recorded as such.
                inputs = [i for i in old.get("input", []) if i.get("type") == "text"] + inputs
            inputs += [{"type": "localImage", "path": p} for p in images]
            run.setdefault("phase", "continuation")
            record = {"state": "prepared", "input_id": uid("input"), "input": inputs, "input_digest": digest(inputs),
                      "messages": {}, "created": time.time(), "unknown_baseline": []}
            if resent:
                record["resent"] = old["input_id"]
            run.setdefault("phases", {})[run["phase"]] = record
            run.update(state="transition_pending", cancel_requested=False)
            if run.get("workflow"):
                run["workflow"]["deadline"] = deadline
            with self.store.transaction():
                self.store.save(run)
                if resent:
                    self.store.event("run/input_resent", run["id"], dict({"phase": run["phase"], "input_id": record["input_id"],
                                                                          "resent": record["resent"], "reason": resent},
                                                                         **({"proof": old["unproven"]} if old.get("unproven") else {})))
            await self.daemon.writer.submit(run["id"])
            return dict(self.summary(self.store.get(run["id"])), missing_historical_images=missing)
