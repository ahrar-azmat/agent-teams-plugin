"""Durable two-turn Abraham workflow. MCP connections only observe this state.

Intent precedes every native submission. A lost reply never causes replay: the
client input ID must be found in native history or the rollout before
progression (runs.settle_inputs). An input nothing shows is unproven, and only
the lead's resume sends it again.
"""
import asyncio
import json
from pathlib import Path
import time

from .client import CoordinatorError
from . import participants, rollout
from .locks import KeyedLocks
from .store import uid, digest


TERMINAL = {"completed", "failed", "interrupted", "blocked", "refused", "cancelled"}
IMAGES_NUDGE = "Images are attached to this turn. Look at each one, then continue from where you left off with them in mind."


def image_paths(images):
    values = [images] if isinstance(images, str) else list(images or [])
    missing = [str(p) for p in values if not isinstance(p, str) or not Path(p).is_file()]
    if missing:
        raise CoordinatorError("RESUME_IMAGES", "Not delivered: image paths must exist", {"missing": missing})
    return list(dict.fromkeys(str(Path(p).resolve()) for p in values))


def descriptor(value):
    fields = {"kind", "request_id", "dispatch_cwd", "analysis", "implementation", "images",
              "model", "effort", "compact_limit", "allow_dirty", "deadline", "session_id"}
    if not isinstance(value, dict) or set(value) != fields or value.get("kind") != "abraham":
        raise CoordinatorError("WORKFLOW_FIELDS", "Expected the versioned Abraham workflow descriptor")
    if value["effort"] != "max" or not isinstance(value["model"], str) or not value["model"]:
        raise CoordinatorError("WORKFLOW_POLICY", "Abraham requires an explicit model and max effort")
    for name in ("analysis", "implementation", "dispatch_cwd", "request_id"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise CoordinatorError("WORKFLOW_INPUT", "Missing workflow " + name)
    import re
    if not re.fullmatch(r"run_[0-9a-f]{32}", value["request_id"]):
        raise CoordinatorError("WORKFLOW_ID", "Expected a stable run UUID")
    if value["implementation"].count("<C2_ANALYSIS_BRIEF>") != 1:
        raise CoordinatorError("WORKFLOW_BRIEF", "Implementation must consume exactly one persisted analysis brief")
    if type(value["allow_dirty"]) is not bool or type(value["deadline"]) not in (float, int):
        raise CoordinatorError("WORKFLOW_POLICY", "Invalid dirty admission or deadline")
    if value["compact_limit"] is not None and (type(value["compact_limit"]) is not int or value["compact_limit"] < 1):
        raise CoordinatorError("WORKFLOW_POLICY", "Compaction limit must be a positive deployed-model threshold")
    if not isinstance(value["images"], list) or any(not isinstance(p, str) or not Path(p).is_absolute() or not Path(p).is_file() for p in value["images"]):
        raise CoordinatorError("WORKFLOW_IMAGES", "Every image must be an existing absolute local path")
    return dict(value)


class Writer:
    def __init__(self, daemon):
        self.daemon, self.store = daemon, daemon.store
        self.locks = KeyedLocks()
        self.advancing = 0  # advances under way: a drain waits for them
        self.resubmitting = set()  # plain runs whose prepared continuation is being submitted again
        self.starting = set()  # input ids whose turn/start is in flight in this daemon

    def prepare(self, runid, phase):
        run = self.store.get(runid)
        workflow = run["workflow"]
        text = workflow[phase]
        if phase == "implementation":
            analysis = run["phases"]["analysis"]
            if analysis.get("success") is not True or analysis.get("status") != "completed" or not analysis.get("answer", "").strip() or not analysis.get("signed_result"):
                raise CoordinatorError("ANALYSIS_GATE", "A completed, successful, nonempty analysis result is required")
            text = text.replace("<C2_ANALYSIS_BRIEF>", analysis["signed_result"])
        inputs = [{"type": "text", "text": text}] + [{"type": "localImage", "path": p} for p in workflow["images"]]
        phase_record = {"state": "prepared", "input_id": uid("input"), "input": inputs,
                        "input_digest": digest(inputs), "created": time.time(), "messages": {},
                        "unknown_baseline": [u["id"] for u in [u for key in participants.checkout_ids(self.store, run["unit"]) for u in self.store.unresolved(key)]]}
        run.setdefault("phases", {})[phase] = phase_record
        run.update(phase=phase, state="transition_pending")
        with self.store.transaction():
            self.store.save(run)
            self.store.event("workflow/prepared", runid, {"phase": phase, "input_id": phase_record["input_id"]})

    async def submit(self, runid):
        run = self.store.get(runid)
        phase = run["phases"][run["phase"]]
        if phase["state"] != "prepared":
            raise CoordinatorError("SUBMISSION_UNCERTAIN", "Reconcile the previous input identity before continuing")
        input_id = phase["input_id"]
        actor = self.store.get(run["root_actor"])
        for key in participants.checkout_ids(self.store, run["unit"]):
            await self.daemon.ensure_lease(key)
        listener = self.store.get(actor["listener"])
        if self.daemon.hook_stale(listener) and not self.unbound(listener):
            # Admitted by another release, its calls would be refused: replace it first, once its unit is quiet. The
            # input stays prepared until then, and maintenance submits it again (advance, or resubmit for a plain run).
            if not self.daemon.unit_quiet(run["unit"]):
                return
            from .recovery import reconcile_listener
            await reconcile_listener(self.daemon, listener, advance=False)
        # Everything is read again after the awaits above (a lease, a replacement): a cancel, a drain, another input
        # or another incarnation since then starts nothing, and the input stays prepared until native bindings are
        # verified again, or the next daemon. Nothing awaits between this check and the submitting state's save.
        run = self.store.get(runid)
        phase = run["phases"][run["phase"]]
        actor = self.store.get(run["root_actor"])
        listener = self.store.get(actor["listener"])
        connection = self.daemon.connections.get(listener["id"])
        if (run.get("cancel_requested") or phase["state"] != "prepared" or phase["input_id"] != input_id
                or self.unbound(listener) or self.daemon.hook_stale(listener)):
            return
        # Validate again after acquiring custody, before persisting a submitting
        # state. A lost RPC after that state remains uncertain, never replayed.
        image_paths([item["path"] for item in phase["input"] if item["type"] == "localImage"])
        phase.update(state="submitting", submitted=time.time(), start_listener=listener["id"])
        run.update(production_started=True, state="running")
        self.store.save(run)  # Durable before the native RPC, including its uncertain window.
        self.starting.add(input_id)
        try:
            response = await connection.call("turn/start", {
                "threadId": actor["thread"], "input": phase["input"], "clientUserMessageId": phase["input_id"]})
        except BaseException as exc:
            run = self.store.get(runid)
            phase = run["phases"][run["phase"]]
            if phase["state"] == "submitting":
                phase.update(state="uncertain", error=getattr(exc, "code", type(exc).__name__))
                if not run.get("cancel_requested"):
                    run["state"] = "submission_uncertain"
                self.store.save(run)
            self.store.event("workflow/submission_uncertain", runid, {"input_id": phase["input_id"]})
            return
        finally:
            self.starting.discard(input_id)
        run = self.store.get(runid)
        phase = run["phases"][run["phase"]]
        phase["turn"] = response["turn"]["id"]
        if phase["state"] == "submitting":
            phase["state"] = "submitted"
        self.store.save(run)
        actor = self.store.get(actor["id"])
        if phase.get("status") is None:
            actor.update(active_turn=phase["turn"], state="running")
            self.store.save(actor)

    def unbound(self, listener):
        """Not a verified binding a turn can start on: recovery or a drain under way, or an app-server not
        connected, still reconciling, failed, or without a live connection."""
        connection = self.daemon.connections.get(listener["id"])
        return (self.daemon.recovery_barrier or self.daemon.draining or listener["state"] != "connected" or
                listener.get("recovery_state") in ("reconciling", "failed") or not connection or connection.closed)

    def resubmit(self, runid):
        """In the background, submit again a plain run's continuation that run/resume prepared but could not submit
        (transition_pending): its writer was being replaced, reconciled or reconnected then."""
        if runid in self.resubmitting or self.daemon.recovery_barrier or self.daemon.draining:
            return
        self.resubmitting.add(runid)
        async def again():
            self.advancing += 1  # a drain waits for it, and it stops at its next step
            try:
                async with self.locks.hold(runid):
                    run = self.store.get(runid)
                    if (self.daemon.draining or run.get("workflow") or run["state"] != "transition_pending" or run.get("cancel_requested")
                            or (run.get("phases") or {}).get(run.get("phase"), {}).get("state") != "prepared"):
                        return
                    await self.submit(runid)
            except (CoordinatorError, OSError) as exc:
                self.store.event("run/resubmit_refused", runid, {"code": getattr(exc, "code", type(exc).__name__)})
            finally:
                self.advancing -= 1
                self.resubmitting.discard(runid)
        self.daemon.background(again())

    def observe(self, actor, method, params):
        run = self.store.get(actor["run"])
        if not run.get("phases") or actor["kind"] != "root":
            return
        turn = params.get("turnId") or params.get("turn", {}).get("id")
        item = params.get("item", {})
        phase = next((p for p in run.get("phases", {}).values() if p.get("turn") == turn or
                      item.get("clientId") and item["clientId"] == p["input_id"]), None)
        if phase is None and method == "turn/started":
            candidate = run.get("phases", {}).get(run.get("phase"), {})
            if candidate.get("state") in ("submitting", "submitted", "uncertain") and not candidate.get("turn"):
                phase = candidate
        if phase is None:
            return
        phase["turn"] = turn
        if item.get("type") == "userMessage" and item.get("clientId") == phase["input_id"]:
            phase["input_observed"] = True
            if phase["state"] in ("uncertain", "submitting"):
                phase["state"] = "submitted"
        if method == "item/completed" and item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
            phase["messages"][item["id"]] = item.get("text", "")
        if method == "turn/completed":
            if phase.get("state") == "complete":
                return
            phase.update(status=params["turn"]["status"], error=params["turn"].get("error"), state="settling")
            phase["answer"] = "\n\n".join(phase["messages"].values()).strip()
            # A requested cancel owns the run's state (cancelling until it settles, then cancelled), and history
            # replay must not undo it. Non-workflow turns have no Abraham phase-advance task to finish a synthetic
            # "settling" state on their behalf.
            if run.get("cancel_requested"):
                pass
            elif not run.get("workflow"):
                run["state"] = params["turn"]["status"]
            else:
                run["state"] = "settling"
        self.store.save(run)

    def signature(self, run, status):
        unit = self.store.get(run["unit"])
        return "[Codex model: " + run["workflow"]["model"] + " | reasoning: max | tool:abraham | status:" + status + " | tree:" + unit["base"][:12] + "]"

    async def advance(self, runid):
        # A drain may stop a workflow settling on patches awaiting their outcome. It waits for the advances under way,
        # and each stops at its next step: no phase completes and no turn starts while the daemon drains.
        if self.daemon.recovery_barrier or self.daemon.draining:
            return
        self.advancing += 1
        try:
            await self.advance_locked(runid)
        finally:
            self.advancing -= 1

    async def advance_locked(self, runid):
        async with self.locks.hold(runid):
            run = self.store.get(runid)
            if not run.get("workflow") or run["state"] in TERMINAL or run.get("cancel_requested"):
                return
            phase = run.get("phases", {}).get(run.get("phase"), {})
            if (phase.get("state") in ("submitting", "uncertain") and phase.get("status") is None
                    and phase.get("input_id") not in self.starting):
                # Its turn/start's result was lost: settled as a resume settles it, by reads in its thread's order or by
                # what its app-server saved once ended (runs.settle_inputs), reading native at most each 2 s.
                if not self.daemon.runs.inputs_due(runid):
                    return
                await self.daemon.runs.settle_inputs(runid, "resume")
                run = self.store.get(runid)
                if run["state"] in TERMINAL or run.get("cancel_requested"):
                    return
                phase = run["phases"][run["phase"]]
            if phase.get("state") == "prepared":
                await self.submit(runid)
                return
            if phase.get("state") != "settling":
                return
            from .publish import Publisher
            try:
                await Publisher(self.daemon).quiescent(self.store.get(run["unit"]))
            except CoordinatorError as exc:
                run = self.store.get(runid)  # read after the await: a cancel since then is not overwritten
                run["settlement_refusal"] = exc.envelope()
                self.store.save(run)
                return
            await asyncio.gather(*(self.daemon.ledger.reconcile(self.store.get(key)) for key in participants.checkout_ids(self.store, run["unit"])))
            if self.daemon.draining:
                return
            root = self.store.get(run["root_actor"])
            readback = await rollout.policy(self.store, root, run.get("rollout"), root.get("thread"))
            if self.daemon.draining:
                return  # the phase stays settling; the next daemon judges it again
            # Read after every await above. A cancel requested meanwhile (run/interrupt does not wait for this lock)
            # stands, as one before this advance does: the phase stays settling and no phase starts. Nothing awaits
            # between this read and the save.
            run = self.store.get(runid)
            phase = run["phases"][run["phase"]]
            if run["state"] in TERMINAL or run.get("cancel_requested") or phase.get("state") != "settling":
                return
            new_unknown = [u for u in [u for key in participants.checkout_ids(self.store, run["unit"]) for u in self.store.unresolved(key)] if u["id"] not in phase["unknown_baseline"]]
            success = phase.get("status") == "completed" and phase.get("input_observed") and bool(phase.get("answer", "").strip()) and not new_unknown
            phase.update(state="complete", completed=time.time(), success=success)
            phase["signed_result"] = self.signature(run, "ok" if success else "error") + "\n" + phase.get("answer", "")
            run.pop("settlement_refusal", None)
            run["state"] = "completed" if success else "blocked" if new_unknown else "failed"
            run["unknown_blockers"] = [u["id"] for u in new_unknown]
            run["rollout_readback"] = readback
            self.store.save(run)
            self.store.event("workflow/phase_completed", runid, {"phase": run["phase"], "success": success, "unknown": run["unknown_blockers"]})
            if success and run["phase"] == "analysis":
                self.prepare(runid, "implementation")
                await self.submit(runid)

    def result(self, runid, cursor=0, limit=50000):
        run = self.store.get(runid)
        if not run.get("workflow"):
            raise CoordinatorError("WORKFLOW_REQUIRED", "This run has no Abraham workflow")
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 100000:
            raise CoordinatorError("RESULT_CURSOR", "Use a nonnegative cursor and a limit from 1 to 100000")
        phase = run.get("phases", {}).get(run.get("phase"), {})
        text = self.signature(run, "ok" if run["state"] == "completed" and run.get("phase") == "implementation" else "running" if run["state"] not in TERMINAL else "error")
        actor = self.store.get(run["root_actor"])
        text += "\n" + json.dumps({"backend": "native", "run": runid, "unit": run["unit"], "phase": run.get("phase"),
                   "state": run["state"], "thread": actor.get("thread"), "policy": actor.get("policy"),
                   "settlement": run.get("settlement_refusal"), "rollout": run.get("rollout"),
                   "rollout_readback": run.get("rollout_readback"),
                   "admission": run.get("admission"), "refusal": run.get("refusal"),
                   # Why this phase's input may have run though nothing shows it: a resume sends it again, a cancel ends it.
                   "input_unproven": phase.get("unproven")}, sort_keys=True)
        text += "\n\n" + phase.get("answer", "") + "\n\n[UNIT ATTRIBUTION]\n"
        for row in self.store.db.execute("SELECT * FROM revisions WHERE unit=? ORDER BY created,id", (run["unit"],)):
            operation = self.store.get(row["operation"])
            author = self.store.get(operation["actor"])
            text += json.dumps({"path": row["path"], "checkout": row["checkout"], "root": self.store.get(row["checkout"])["root"], "label": row["attribution"], "revision": row["id"],
                       "operation": operation["id"], "actor": author["id"], "kind": author["kind"], "thread": author.get("thread")}) + "\n"
        for operation in self.store.all("operation", run["unit"]):
            if operation.get("custody"):
                text += json.dumps({"label": "custody", "operation": operation["id"], "actor": operation["actor"],
                           "native": operation["native"], "meaning": "possible effects; not exact authorship", "state": operation["state"]}) + "\n"
        for unknown in [u for key in participants.checkout_ids(self.store, run["unit"]) for u in self.store.unresolved(key)]:
            text += json.dumps(dict(unknown, label="UNKNOWN", meaning="unresolved checkout effects; not credited to this unit")) + "\n"
        return {"run": runid, "unit": run["unit"], "state": run["state"], "phase": run.get("phase"),
                "text": text[cursor:cursor + limit], "cursor": cursor,
                "next_cursor": cursor + limit if cursor + limit < len(text) else None, "total_characters": len(text)}
