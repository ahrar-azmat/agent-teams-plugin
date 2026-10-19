"""Full-answer review jobs bound before inference to immutable candidate objects."""
import hashlib
import re
import time

from .client import CoordinatorError
from .publish import Publisher, BINDING, binding
from .store import digest, uid
from . import gitio, routing


def verdict(answer):
    """Accept only one standalone, unquoted own verdict, at the answer's start."""
    lines, fenced = [], False
    for raw in answer.splitlines():
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and line and not line.startswith(">"):
            lines.append(line)
    if not lines:
        return "missing"
    entries = [line for line in lines if re.match(r"(?i)^(?:#{1,6}\s+)?(?:\*\*)?Verdict\b", line)]
    if len(entries) != 1 or entries[0] != lines[0]:
        return "ambiguous"
    match = re.fullmatch(r"(?i)(?:\*\*)?Verdict(?:\*\*)?\s*:\s*(?:\*\*)?(Ship it|Needs changes|Do not ship)(?:\*\*)?\.?", lines[0])
    if not match:
        return "ambiguous"
    return {"ship it": "pass", "needs changes": "needs_changes", "do not ship": "do_not_ship"}[match[1].lower()]


async def prepare(daemon, params):
    unit = daemon.store.get(params["unit"])
    routing.route(daemon.store, dict(unit=unit["id"], checkout=unit["checkout"]), ".",
                  params.get("target_cwd", params["cwd"]), directory=True)
    result = await Publisher(daemon).diff(params["unit"])
    ident = uid("review_job")
    daemon.store.put("review_job", ident, {"unit": params["unit"], "binding": result["binding"],
        "diff_digest": digest(result["diff"]), "state": "prepared", "created": time.time(),
        "dispatch_cwd": params["cwd"], "run": None}, params["unit"])
    return dict(result, job=ident)


def dispatched(daemon, params):
    job = daemon.store.get(params["job"])
    if job["state"] != "prepared":
        raise CoordinatorError("REVIEW_DISPATCHED", "Collect the existing review job; never replay dispatch")
    job.update(state="dispatched", run=params["run"])
    daemon.store.save(job)
    daemon.store.event("review/dispatched", job["id"], {"run": params["run"]})
    return {"job": job["id"], "run": job["run"]}


def complete(daemon, params):
    job = daemon.store.get(params["job"])
    if job["state"] in ("recorded", "refused"):
        return job
    # Repair old split-write transitions from the saved answer, never inference.
    result = job.get("completion") if job["state"] == "negative" and job.get("completion") else params["completion"]
    if result.get("run") != job["run"] or not isinstance(result.get("answer", ""), str):
        raise CoordinatorError("REVIEW_IDENTITY", "Completion does not identify the dispatched review run")
    answer = result.get("answer", "")
    parsed = verdict(answer)
    with daemon.store.transaction():
        job.update(completion=result, verdict=parsed, answer_digest=hashlib.sha256(answer.encode()).hexdigest(), state="negative")
        if result.get("completed") is True and result.get("status") == "ok" and result.get("thread") and parsed == "pass":
            evidence = dict(job["binding"], completed=True, verdict="pass", answer=answer,
                            answer_digest=job["answer_digest"], review_identity={"run": job["run"], "thread": result["thread"]})
            try:
                unit = daemon.store.get(job["unit"])
                seal = daemon.store.get(unit.get("seal"), required=False)
                if unit["state"] != "sealed" or not seal or any(evidence.get(key) != value for key, value in binding(seal).items()):
                    raise CoordinatorError("REVIEW_BINDING", "Saved completion is stale; reseal and obtain a review of the current candidate")
                if seal.get("binding_version") == 2:
                    diffs = ["Repository: " + item["root"] + "\n" + (gitio.git(item["root"],
                        ["diff", "--no-ext-diff", "--no-textconv", item["base"], item["candidate"]]).stdout.decode("utf-8", "strict")
                        if item["candidate"] else "") for item in seal["vector"]]
                    if digest("\n".join(diffs)) != job["diff_digest"]:
                        raise CoordinatorError("REVIEW_DIFF_CHANGED", "Complete candidate diff no longer matches the dispatched review")
                prior = next((r for r in daemon.store.all("review", job["unit"]) if r.get("valid") is not False
                    and all(r.get(k) == evidence.get(k) for k in (*job["binding"], "answer_digest", "review_identity", "completed", "verdict"))), None)
                recorded = {"review": prior["id"]} if prior else Publisher(daemon).review(job["unit"], evidence)
                job.update(state="recorded", review=recorded["review"])
            except CoordinatorError as exc:
                job.update(state="refused", refusal=exc.envelope())
        daemon.store.save(job)
        daemon.store.event("review/completed", job["id"], {"state": job["state"], "verdict": parsed, "answer_digest": job["answer_digest"]})
    return {k: job.get(k) for k in ("id", "run", "unit", "binding", "state", "verdict", "answer_digest", "review", "refusal")}


def repair(daemon):
    for job in daemon.store.all("review_job"):
        if job["state"] == "negative" and job.get("completion") and verdict(job["completion"].get("answer", "")) == "pass":
            complete(daemon, {"job": job["id"], "completion": job["completion"]})
