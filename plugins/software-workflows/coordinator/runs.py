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
        for kind in ("actor", "operation", "custody", "claim", "message"):
            subjects.update(r["id"] for r in self.store.all(kind) if r.get("unit") == run["unit"])
        subjects.update(r["id"] for r in self.store.all("listener", run["id"]))
        placeholders = ",".join("?" for _ in subjects)
        rows = list(self.store.db.execute("SELECT * FROM events WHERE seq>? AND subject IN (" + placeholders + ") ORDER BY seq LIMIT ?",
                                         [cursor, *sorted(subjects), limit + 1]))
        data = [dict(row, data=json.loads(row["data"])) for row in rows[:limit]]
        return {"run": run["id"], "data": data, "cursor": data[-1]["seq"] if data else cursor,
                "more": len(rows) > limit, "state": self.store.get(run["id"])["state"]}

    async def cancel(self, run):
        run.update(cancel_requested=True, state="cancelling")
        self.store.save(run)
        self.store.event("run/cancel_requested", run["id"], {})
        root = self.store.get(run["root_actor"])
        if root.get("listener"):
            await self.daemon.discover_descendants(self.store.get(root["listener"]))
        failures = []
        for message in list(self.store.all("message")):
            if message.get("unit") != run["unit"] or message["state"] in ("entered_context", "acknowledged"):
                continue
            async with self.daemon.messages.locks.hold(message["id"]):
                message = self.store.get(message["id"])
                if message["delivery"] == "pending":
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
                        await connection.call("thread/queue/delete", {"threadId": actor["thread"], "queuedSubmissionId": entry["id"]})
                        for message in list(self.store.all("message")):
                            if message.get("unit") == run["unit"] and entry["clientUserMessageId"] in (message["native_id"], message["native_id"] + ":relay"):
                                message["delivery"] = "cancelled"
                                self.store.save(message)
                                self.store.event("message/cancelled", message["id"], {"queue_id": entry["id"]})
                if actor.get("active_turn"):
                    await connection.call("turn/interrupt", {"threadId": actor["thread"], "turnId": actor["active_turn"]})
                terminals = await connection.call("thread/backgroundTerminals/list", {"threadId": actor["thread"]})
                if terminals.get("nextCursor"):
                    raise CoordinatorError("TERMINAL_PAGINATION", "Cancellation terminal inventory is incomplete")
                for terminal in terminals["data"]:
                    await connection.call("thread/backgroundTerminals/terminate", {"threadId": actor["thread"], "processId": terminal["processId"]})
                for reservation in self.daemon.custody.unit_active(run["unit"]):
                    if reservation["actor"] == actor["id"]:
                        for process in reservation.get("processes", []):
                            await asyncio.to_thread(processes.terminate, process, actor["session"])
            except (CoordinatorError, OSError, AttributeError) as exc:
                failures.append({"actor": actor["id"], "code": getattr(exc, "code", type(exc).__name__)})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if await self.settle_cancel(run["id"]):
                break
            await asyncio.sleep(0.1)
        return dict(self.summary(self.store.get(run["id"])), cancellation_failures=failures,
                    meaning="cancelled requires terminal events and verified process settlement; cancelling retains custody")

    async def settle_cancel(self, runid):
        async with self.cancel_locks.hold(runid):
            return await self._settle_cancel(runid)

    async def _settle_cancel(self, runid):
        run = self.store.get(runid)
        if time.time() - run.get("cancel_reconciled", 0) > 2:
            run["cancel_reconciled"] = time.time()
            self.store.save(run)
            from .recovery import reconcile_listener
            for ident in {a["listener"] for a in self.store.all("actor", run["unit"]) if a.get("listener")}:
                try:
                    await reconcile_listener(self.daemon, self.store.get(ident), advance=False)
                except (CoordinatorError, OSError) as exc:
                    self.store.event("run/cancel_reconcile_refused", runid, {"code": getattr(exc, "code", type(exc).__name__)})
        for actor in list(self.store.all("actor", run["unit"])):
            await self.daemon.settle_actor(actor)
        if any(a.get("active_turn") for a in self.store.all("actor", run["unit"])) or any(c["unit"] == run["unit"] for c in self.daemon.custody.unit_active(run["unit"])):
            return False
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
        run["state"] = "cancelled"
        self.store.save(run)
        self.store.event("run/cancelled", runid, {"terminal_and_processes_settled": True})
        await self.daemon.release_lease(run["checkout"])
        return True

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
