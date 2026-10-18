"""Durable two-turn Abraham workflow. MCP connections only observe this state.

Intent precedes every native submission. A lost reply never causes replay: the
client input ID must be found in paginated native history before progression.
"""
import asyncio
import json
import os
from pathlib import Path
import stat
import time

from .client import CoordinatorError
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


def rollout_policy(path, thread):
    """Read only policy fields from this native thread's own recorded rollout."""
    result = {"verified": False, "source": "native rollout turn_context", "turns": []}
    if not path or not thread or not Path(path).name.endswith("-" + thread + ".jsonl"):
        return dict(result, reason="native thread did not supply a matching rollout path")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return dict(result, reason="rollout is not an owned regular file")
            while True:
                line = stream.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 16 * 1024 * 1024:
                    return dict(result, reason="rollout record exceeds bounded reader")
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # An appending writer may have an unfinished last line.
                if record.get("type") != "turn_context":
                    continue
                payload = record.get("payload", {})
                sandbox = payload.get("sandbox_policy", {})
                result["turns"].append({"turn": payload.get("turn_id"), "model": payload.get("model"),
                    "effort": payload.get("effort"), "approval_policy": payload.get("approval_policy"),
                    "sandbox": sandbox.get("type") if isinstance(sandbox, dict) else sandbox})
    except OSError as exc:
        return dict(result, reason=type(exc).__name__)
    result["verified"] = bool(result["turns"]) and all(t["approval_policy"] == "never" and
        t["sandbox"] == "danger-full-access" for t in result["turns"])
    if not result["verified"]:
        result["reason"] = "no recorded turn contexts or unexpected native policy"
    return result


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
                        "unknown_baseline": [u["id"] for u in self.store.unresolved(run["checkout"])]}
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
        actor = self.store.get(run["root_actor"])
        await self.daemon.ensure_lease(run["checkout"])
        # Validate again after acquiring custody, before persisting a submitting
        # state. A lost RPC after that state remains uncertain, never replayed.
        image_paths([item["path"] for item in phase["input"] if item["type"] == "localImage"])
        phase.update(state="submitting", submitted=time.time())
        run.update(production_started=True, state="running")
        self.store.save(run)  # Durable before the native RPC, including its uncertain window.
        try:
            response = await self.daemon.connections[actor["listener"]].call("turn/start", {
                "threadId": actor["thread"], "input": phase["input"], "clientUserMessageId": phase["input_id"]})
        except BaseException as exc:
            run = self.store.get(runid)
            phase = run["phases"][run["phase"]]
            if phase["state"] == "submitting":
                phase.update(state="uncertain", error=getattr(exc, "code", type(exc).__name__))
                run["state"] = "submission_uncertain"
                self.store.save(run)
            self.store.event("workflow/submission_uncertain", runid, {"input_id": phase["input_id"]})
            return
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
            # History replay must not undo verified cancellation settlement.
            # Non-workflow turns have no Abraham phase-advance task to finish
            # a synthetic "settling" state on their behalf.
            if run.get("cancel_requested") and run["state"] == "cancelled":
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
        async with self.locks.hold(runid):
            run = self.store.get(runid)
            if not run.get("workflow") or run["state"] in TERMINAL or run.get("cancel_requested"):
                return
            phase = run.get("phases", {}).get(run.get("phase"), {})
            if phase.get("state") == "prepared":
                await self.submit(runid)
                return
            if phase.get("state") != "settling":
                return
            from .publish import Publisher
            try:
                await Publisher(self.daemon).quiescent(self.store.get(run["unit"]))
            except CoordinatorError as exc:
                run["settlement_refusal"] = exc.envelope()
                self.store.save(run)
                return
            await self.daemon.ledger.reconcile(self.store.get(run["checkout"]))
            run = self.store.get(runid)
            phase = run["phases"][run["phase"]]
            new_unknown = [u for u in self.store.unresolved(run["checkout"]) if u["id"] not in phase["unknown_baseline"]]
            success = phase.get("status") == "completed" and phase.get("input_observed") and bool(phase.get("answer", "").strip()) and not new_unknown
            phase.update(state="complete", completed=time.time(), success=success)
            phase["signed_result"] = self.signature(run, "ok" if success else "error") + "\n" + phase.get("answer", "")
            run.pop("settlement_refusal", None)
            run["state"] = "completed" if success else "blocked" if new_unknown else "failed"
            run["unknown_blockers"] = [u["id"] for u in new_unknown]
            root = self.store.get(run["root_actor"])
            run["rollout_readback"] = await asyncio.to_thread(rollout_policy, run.get("rollout"), root.get("thread"))
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
                   "admission": run.get("admission"), "refusal": run.get("refusal")}, sort_keys=True)
        text += "\n\n" + phase.get("answer", "") + "\n\n[UNIT ATTRIBUTION]\n"
        for row in self.store.db.execute("SELECT * FROM revisions WHERE unit=? ORDER BY created,id", (run["unit"],)):
            operation = self.store.get(row["operation"])
            author = self.store.get(operation["actor"])
            text += json.dumps({"path": row["path"], "label": row["attribution"], "revision": row["id"],
                       "operation": operation["id"], "actor": author["id"], "kind": author["kind"], "thread": author.get("thread")}) + "\n"
        for operation in self.store.all("operation", run["unit"]):
            if operation.get("custody"):
                text += json.dumps({"label": "custody", "operation": operation["id"], "actor": operation["actor"],
                           "native": operation["native"], "meaning": "possible effects; not exact authorship", "state": operation["state"]}) + "\n"
        for unknown in self.store.unresolved(run["checkout"]):
            text += json.dumps(dict(unknown, label="UNKNOWN", meaning="unresolved checkout effects; not credited to this unit")) + "\n"
        return {"run": runid, "unit": run["unit"], "state": run["state"], "phase": run.get("phase"),
                "text": text[cursor:cursor + limit], "cursor": cursor,
                "next_cursor": cursor + limit if cursor + limit < len(text) else None, "total_characters": len(text)}
