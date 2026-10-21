"""Scoped durable run observation and explicit control; no exec watchdog IDs."""
import asyncio
import json
import os
import time
from pathlib import Path

from .client import CoordinatorError
from .store import uid, digest
from .writer import TERMINAL
from . import processes, participants
from .locks import KeyedLocks


class Runs:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.cancel_locks = KeyedLocks()
        self.cancel_passes = {}  # run -> cancel passes still interrupting and terminating: no cancelled meanwhile
        self.cancel_reconciled = {}  # run -> monotonic time of its cancel's last reconcile: one each 2 s at most
        self.start_absent = {}  # run -> (input, monotonic time) of the first read of a lost start that found no turn

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

    def summary(self, run):
        unit = self.store.get(run["unit"])
        surface = participants.surface(self.daemon, unit)
        return {"run": run["id"], "revision": run.get("_revision", 0) + surface["participant_revision"], "backend": "native", "unit": unit["id"], "unit_state": unit["state"],
                "phase": run.get("phase"), "state": run["state"], "created": run.get("created", 0),
                "dispatch_cwd": run.get("dispatch_cwd"), "target_cwd": run.get("target_cwd"),
                "claims": [c for p in surface["participants"] if p["checkout"] == run["checkout"] for c in p["claims"]],
                "custody": [{k: v for k, v in c.items() if k != "process_baseline"}
                            for c in self.daemon.custody.unit_active(run["unit"]) if c["unit"] == unit["id"]],
                "unread_messages": self.store.db.execute("SELECT count(*) FROM records WHERE kind='message' AND "
                    "json_extract(data,'$.unit')=? AND json_extract(data,'$.state')!='acknowledged'", (unit["id"],)).fetchone()[0],
                "unknown_count": len(self.store.unresolved(run["checkout"])),
                **surface,
                "actors": [{k: a.get(k) for k in ("id", "kind", "thread", "active_turn", "state")} for a in self.store.all("actor", unit["id"])]}

    def listing(self, cwd, limit=50):
        from .observations import checkout_at
        checkout, changed = checkout_at(self.store, cwd)
        workspace = os.path.realpath(cwd)
        runs = []
        for run in self.store.selected("SELECT * FROM records WHERE kind='run' AND "
                "(json_extract(data,'$.workflow') IS NOT NULL OR json_extract(data,'$.project_revision') IS NOT NULL) "
                "ORDER BY json_extract(data,'$.created') DESC,id DESC"):
            if (checkout and not changed and participants.contains(self.store, run["unit"], checkout["id"]) or
                    any(path and os.path.commonpath((workspace, os.path.realpath(path))) == workspace
                        for path in (run.get("dispatch_cwd"), run.get("target_cwd")))):
                runs.append(run)
                if len(runs) >= max(1, min(limit, 100)):
                    break
        return {"data": [self.summary(r) for r in reversed(runs)],
                "generation": self.store.generation}

    def events(self, run, cursor=0, limit=100):
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
        placeholders = ",".join("?" for _ in subjects)
        rows = list(self.store.db.execute("SELECT * FROM events WHERE seq>? AND subject IN (" + placeholders + ") ORDER BY seq LIMIT ?",
                                         [cursor, *sorted(subjects), limit + 1]))
        data = [dict(row, data=json.loads(row["data"])) for row in rows[:limit]]
        return {"run": run["id"], "data": data, "cursor": data[-1]["seq"] if data else cursor,
                "more": len(rows) > limit, "state": self.store.get(run["id"])["state"]}

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
        if expired and not self.expired(run):
            # Maintenance read the run before the awaits of the runs ahead of it: resumed with a new deadline, finished
            # or cancelled since, its old deadline no longer holds. Nothing awaits between this check and the save below.
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
            try:
                from .native import page
                async for queued in page(connection, "thread/queue/list", {"threadId": actor["thread"]}):
                    for entry in queued["data"]:
                        if not self.cancel_current(runid, generation):
                            return self.superseded(runid, generation)
                        await connection.call("thread/queue/delete", {"threadId": actor["thread"], "queuedSubmissionId": entry["id"]})
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
                            await asyncio.to_thread(processes.terminate, process, actor["session"])
            except (CoordinatorError, OSError, AttributeError) as exc:
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
                self.start_absent.pop(runid, None)
            return done

    async def _settle_cancel(self, runid):
        """Report the run cancelled once nothing of it runs or can still start. Returns True when that is done, or
        when the cancel no longer stands (a resume cleared it, or a newer cancel settles itself)."""
        run = self.store.get(runid)
        if not run.get("cancel_requested"):
            return True
        generation = run.get("cancel_generation", 0)
        # A pass that reads native (reconciles, a lost start's outcome) at most every 2 s, by a monotonic clock: a wall
        # clock set back would hold the next one, and a cancel that waits for it.
        due = time.monotonic() - self.cancel_reconciled.get(runid, float("-inf")) > 2
        if due:
            self.cancel_reconciled[runid] = time.monotonic()
            from .recovery import reconcile_listener
            for ident in {a["listener"] for a in self.store.all("actor", run["unit"]) if a.get("listener")}:
                listener = self.store.get(ident)
                if listener.get("recovery_state") == "failed" and listener.get("retry_at", 0) > time.time():
                    continue  # its retry is not due yet: a start that keeps failing is not tried every 2 s
                try:
                    await reconcile_listener(self.daemon, listener, advance=False)
                except (CoordinatorError, OSError) as exc:
                    self.store.event("run/cancel_reconcile_refused", runid, {"code": getattr(exc, "code", type(exc).__name__)})
            # Every turn still active is interrupted again: one the cancel's pass could not see, and one whose
            # interrupt was lost with its app-server.
            for actor in list(self.store.all("actor", run["unit"])):
                if actor.get("active_turn"):
                    await self.interrupt_turn(actor, actor["active_turn"], generation)
        for actor in list(self.store.all("actor", run["unit"])):
            await self.daemon.settle_actor(actor)
        if not self.cancel_current(runid, generation):
            return True
        if any(a.get("active_turn") for a in self.store.all("actor", run["unit"])) or any(c["unit"] == run["unit"] for c in self.daemon.custody.unit_active(run["unit"])):
            return False
        if self.cancel_passes.get(runid):
            return False  # a cancel's pass is still interrupting and terminating: cancelled only once it ends
        run = self.store.get(runid)
        phase = (run.get("phases") or {}).get(run.get("phase")) or {}
        if phase.get("state") in ("submitting", "uncertain") and not phase.get("turn"):
            # Its turn/start's outcome is unknown: native may have started its turn. One in flight here is waited for;
            # otherwise a read ordered after it tells (start_outcome), on a pass that reads native. Until one does,
            # the run stays cancelling.
            input_id = phase["input_id"]
            if input_id in self.daemon.writer.starting or not due:
                return False
            first = self.start_absent.get(runid)
            first = first[1] if first and first[0] == input_id else None
            if first is not None and time.monotonic() - first < 2:
                return False
            turn = await self.start_outcome(run, phase)
            if turn is None or not self.cancel_current(runid, generation):
                self.start_absent.pop(runid, None)
                return False
            run = self.store.get(runid)
            phase = run["phases"][run["phase"]]
            if phase.get("turn") or phase["state"] not in ("submitting", "uncertain"):
                return False  # its turn was seen meanwhile: the next pass settles it
            if turn:
                self.start_absent.pop(runid, None)
                self.found_start(run, turn)
                return False  # interrupted while it runs, then settled as any turn is
            if first is None:
                # turn/start answers once core holds its turn, but native reports the thread active only once that
                # turn's task reports it started: a read between the two finds no turn on an idle thread. A second
                # read, sent at least 2 s after this one, settles it.
                self.start_absent[runid] = (input_id, time.monotonic())
                return False
            self.start_absent.pop(runid, None)
            phase.update(state="not_started", status="not_started")
            self.store.save(run)
            self.store.event("workflow/submission_not_started", runid, {"input_id": phase["input_id"]})
        # A steer the turn never took cannot enter it once no turn is active: say so, rather than wait
        # for an echo that cannot come (a pause after a pending Send now never settled, measured 2.3.0).
        # A later echo still enters it; only an explicit retry sends it again.
        for message in list(self.store.all("message")):
            if (message.get("unit") == run["unit"] and message["state"] not in ("entered_context", "acknowledged")
                    and message["delivery"] == "submitted"):
                async with self.daemon.messages.locks.hold(message["id"]):
                    message = self.store.get(message["id"])
                    if message["state"] not in ("entered_context", "acknowledged") and message["delivery"] == "submitted":
                        message["delivery"] = "turn_ended"
                        self.store.save(message)
                        self.store.event("message/turn_ended", message["id"], {"run": runid})
        if any(m.get("unit") == run["unit"] and m["state"] not in ("entered_context", "acknowledged")
               and m["delivery"] in ("submitting", "submitted", "uncertain") for m in self.store.all("message")):
            return False
        if not await self.threads_idle(run):
            return False  # a turn runs that no record shows: the next reconcile finds it, and it is interrupted
        run = self.store.get(runid)  # read after the awaits above: what a notification saved meanwhile is kept
        if not run.get("cancel_requested") or run.get("cancel_generation", 0) != generation:
            return True  # a resume or a newer cancel since then: this cancel saves nothing
        run["state"] = "cancelled"
        self.store.save(run)
        self.store.event("run/cancelled", runid, {"terminal_and_processes_settled": True})
        await self.daemon.release_lease(run["checkout"])
        return True

    async def start_outcome(self, run, phase):
        """The turn that a phase's input entered when its turn/start result was lost, False when none did, or None
        while that cannot be told. Native runs a thread's requests one at a time in arrival order, across connections,
        and drops a closed connection's queued ones; turn/start starts or steers a turn, never queues, and answers once
        core holds the turn it starts. So the thread/read of the status, sent after the loss, runs once that start has
        ended or was dropped, and the read of the turns that follows (native saves the live thread first) holds the
        input of a turn that ended. One read cannot tell, as native builds its turns before it reads the status. Nor can
        an idle status alone: native reports a thread active once the turn's own task reports it started, after the
        start answers, so False is settled by a second read (_settle_cancel). (codex 0.163.0: request_serialization.rs,
        thread_processor.rs, thread_status.rs, session/turn_input.rs, tasks/mod.rs, tasks/regular.rs)"""
        actor = self.store.get(run["root_actor"])
        sent = phase.get("start_listener")
        if sent and sent != actor.get("listener") and (self.store.get(sent, required=False) or {}).get("state") in ("connected", "starting"):
            return None  # its start went to an app-server that still runs: no read of another one is ordered after it
        connection = self.daemon.connections.get(actor.get("listener"))
        if not connection or connection.closed:
            return None
        try:
            status = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": False})
            read = await connection.call("thread/read", {"threadId": actor["thread"], "includeTurns": True})
        except (CoordinatorError, OSError) as exc:
            self.store.event("run/cancel_start_unread", run["id"], {"code": getattr(exc, "code", type(exc).__name__)})
            return None
        turns = read["thread"].get("turns") or []
        for turn in turns:
            if any(item.get("type") == "userMessage" and item.get("clientId") == phase["input_id"] for item in turn.get("items") or []):
                return turn
        if any((r["thread"].get("status") or {}).get("type") == "active" for r in (status, read)):
            # A turn runs, and native may not have saved its input yet: it may be this input's. It is the actor's
            # active turn, so the cancel interrupts it, and the outcome is read again once the thread is idle.
            running = next((t for t in reversed(turns) if t["status"] == "inProgress"), None)
            actor = self.store.get(actor["id"])
            if running and not actor.get("active_turn"):
                actor.update(active_turn=running["id"], state="running")
                self.store.save(actor)
            return None
        return False

    def found_start(self, run, turn):
        """Record the turn a lost start's input entered, as its notifications would have: the phase's turn and its
        input seen, then its end, or the actor's active turn while it runs (the cancel's next pass interrupts it)."""
        actor = self.store.get(run["root_actor"])
        input_id = run["phases"][run["phase"]]["input_id"]
        item = next(i for i in turn["items"] if i.get("type") == "userMessage" and i.get("clientId") == input_id)
        self.daemon.writer.observe(actor, "item/completed", {"threadId": actor["thread"], "turnId": turn["id"], "item": item})
        if turn["status"] == "inProgress":
            actor = self.store.get(actor["id"])
            actor.update(active_turn=turn["id"], state="running")
            self.store.save(actor)
        else:
            self.daemon.writer.observe(actor, "turn/completed", {"threadId": actor["thread"], "turn": turn})
        self.store.event("workflow/submission_found", run["id"], {"input_id": input_id, "turn": turn["id"], "status": turn["status"]})

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
            if (read["thread"].get("status") or {}).get("type") == "active":
                return False
        return True

    async def app_server_runs(self, listenerid):
        """Whether the app-server of this listener record still runs (its process, by birth identity), or may."""
        listener = self.store.get(listenerid, required=False) or {}
        if not listener.get("pid"):
            return False
        try:
            current = await asyncio.to_thread(processes.identity, listener["pid"])
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
            root = self.store.get(run["root_actor"])
            from .recovery import reconcile_listener
            # Reconcile without advancing the workflow while this control lock is held.
            await reconcile_listener(self.daemon, self.store.get(root["listener"]), advance=False)
            run = self.store.get(run["id"])
            eligible = TERMINAL | ({"idle"} if not run.get("workflow") else set())
            if run["state"] not in eligible or any(a.get("active_turn") for a in self.store.all("actor", run["unit"])):
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
            if run["state"] not in eligible or any(a.get("active_turn") for a in self.store.all("actor", run["unit"])):
                raise CoordinatorError("RUN_ACTIVE", "Collect this run; an active or uncertain turn cannot be resumed")
            old = run.get("phases", {}).get(run.get("phase"), {})
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
            inputs += [{"type": "localImage", "path": p} for p in images]
            run.setdefault("phase", "continuation")
            run.setdefault("phases", {})[run["phase"]] = {"state": "prepared", "input_id": uid("input"), "input": inputs,
                "input_digest": digest(inputs), "messages": {}, "created": time.time(), "unknown_baseline": []}
            run.update(state="transition_pending", cancel_requested=False)
            if run.get("workflow"):
                run["workflow"]["deadline"] = deadline
            self.store.save(run)
            await self.daemon.writer.submit(run["id"])
            return dict(self.summary(self.store.get(run["id"])), missing_historical_images=missing)
