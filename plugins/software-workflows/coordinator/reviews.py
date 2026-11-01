"""Full-answer review jobs bound before inference to immutable candidate objects."""
import hashlib
import re
import time

from .client import CoordinatorError
from .publish import Publisher, binding
from .locks import finish_thread
from .store import digest, uid
from . import gitio, routing


# A disconnected MCP call can be collected after an arbitrarily long sleep.
# Only lifecycle changes invalidate its bound answer, never elapsed time.
ACTIVE = "kind='review_job' AND json_extract(data,'$.state') IN ('prepared','dispatched')"


def remove_manifest(store, job):
    # Construct the owned filename instead of trusting a saved arbitrary path.
    (store.root / "review-packets" / (job["id"] + ".json")).unlink(missing_ok=True)


def abandon_stale(store, job):
    if job["state"] not in ("prepared", "dispatched"):
        return job
    unit = store.get(job["unit"])
    stale = unit.get("seal") != job["binding"]["seal"] or unit["state"] != "sealed"
    if not stale:
        from .transfers import validate_seal
        try:
            validate_seal(store, store.get(unit["seal"]))
        except CoordinatorError:
            stale = True
    if stale:
        job.update(state="abandoned", abandoned=time.time(), reason="seal superseded or publication rights changed")
        store.save(job)
        remove_manifest(store, job)
    return job


def jobs(daemon, params):
    # The partial indexes contain only recoverable jobs. Completed answers and
    # manifests are never decoded to collect another run in the same workspace.
    query = "SELECT * FROM records INDEXED BY active_review_cwd WHERE " + ACTIVE + " AND json_extract(data,'$.dispatch_cwd')=?"
    args = [params["cwd"]]
    if params.get("run"):
        query += " AND json_extract(data,'$.run')=?"
        args.append(params["run"])
    return {"data": [abandon_stale(daemon.store, job) for job in daemon.store.selected(query, args)]}


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
    for job in daemon.store.selected("SELECT * FROM records INDEXED BY active_review_unit WHERE " + ACTIVE + " AND parent=?", (params["unit"],)):
        abandon_stale(daemon.store, job)
    unit = daemon.store.get(params["unit"])
    routing.route(daemon.store, dict(unit=unit["id"], checkout=unit["checkout"]), ".",
                  params.get("target_cwd", params["cwd"]), directory=True)
    if unit["state"] != "sealed" or not unit.get("seal"):
        raise CoordinatorError("UNIT_NOT_SEALED", "Seal before asking for an object review")
    seal = daemon.store.get(unit["seal"])
    ident = uid("review_job")
    from .review_packet import build, context_budget
    budget = params.get("budget")
    if budget is None:
        from .native import native_codex
        budget = await finish_thread(lambda: context_budget(None, [native_codex()]))
        budget["derived_by"] = "daemon (legacy server supplied no budget)"
    else:
        budget = dict(budget, derived_by="server")
    packet = await finish_thread(build, daemon.store, seal, daemon.store.get(unit["checkout"]), ident, budget)
    try:
        daemon.store.put("review_job", ident, {"unit": params["unit"], "binding": binding(seal),
            **{k: v for k, v in packet.items() if k != "packet"}, "state": "prepared", "created": time.time(),
            "dispatch_cwd": params["cwd"], "run": None}, params["unit"])
    except BaseException:
        remove_manifest(daemon.store, {"id": ident})
        raise
    # 2.4.11–2.4.28 read diff and put it on argv. The legacy field is the same
    # bounded packet, never the full diff. Completion binds to the stored job.
    shown_binding = binding(seal) if params.get("budget") else {"seal": seal["seal"], "unit": unit["id"], "binding_digest": digest(binding(seal))}
    return {"binding": shown_binding, "job": ident, "packet": packet["packet"], "diff": packet["packet"], "packet_version": 1,
            "packet_bytes": packet["packet_bytes"], "manifest_digest": packet["manifest_digest"]}


def dispatched(daemon, params):
    job = abandon_stale(daemon.store, daemon.store.get(params["job"]))
    if job["state"] != "prepared":
        raise CoordinatorError("REVIEW_DISPATCHED", "Collect the existing review job; never replay dispatch")
    job.update(state="dispatched", run=params["run"])
    daemon.store.save(job)
    daemon.store.event("review/dispatched", job["id"], {"run": params["run"]})
    return {"job": job["id"], "run": job["run"]}


def complete(daemon, params):
    job = abandon_stale(daemon.store, daemon.store.get(params["job"]))
    if job["state"] in ("recorded", "refused", "abandoned"):
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
                from .transfers import validate_seal
                validate_seal(daemon.store, seal)
                if job.get("packet_version") == 1:
                    if digest(job["manifest"]) != job["manifest_digest"]:
                        raise CoordinatorError("REVIEW_DIFF_CHANGED", "Saved object manifest no longer matches the dispatched review")
                elif seal.get("binding_version") == 2:
                    diffs = ["Repository: " + item["root"] + "\n" + (gitio.git(item["root"],
                        ["diff", "--no-ext-diff", "--no-textconv", item["base"], item["candidate"]]).stdout.decode("utf-8", "strict")
                        if item["candidate"] else "") for item in seal["vector"]]
                    if digest("\n".join(diffs)) != job["diff_digest"]:
                        raise CoordinatorError("REVIEW_DIFF_CHANGED", "Complete candidate diff no longer matches the dispatched review")
                prior = next((r for r in daemon.store.selected("SELECT * FROM records INDEXED BY bound_review_answer WHERE kind='review' AND parent=? "
                    "AND json_extract(data,'$.review_identity.run')=? AND json_extract(data,'$.review_identity.thread')=? "
                    "AND json_extract(data,'$.answer_digest')=?", (job["unit"], job["run"], result["thread"], job["answer_digest"])) if r.get("valid") is not False
                    and all(r.get(k) == evidence.get(k) for k in (*job["binding"], "answer_digest", "review_identity", "completed", "verdict"))), None)
                recorded = {"review": prior["id"]} if prior else Publisher(daemon).review(job["unit"], evidence)
                job.update(state="recorded", review=recorded["review"])
            except CoordinatorError as exc:
                job.update(state="refused", refusal=exc.envelope())
        daemon.store.save(job)
        daemon.store.event("review/completed", job["id"], {"state": job["state"], "verdict": parsed, "answer_digest": job["answer_digest"]})
    remove_manifest(daemon.store, job)
    return {k: job.get(k) for k in ("id", "run", "unit", "binding", "state", "verdict", "answer_digest", "review", "refusal")}


def repair(daemon):
    # Startup only, before RPC readiness. Orphans can arise between file creation
    # and the job transaction; no preparation can race this sweep. Terminal
    # cleanup normally happens once at transition, with this crash recovery.
    from .transfers import initialize
    initialize(daemon.store)
    daemon.store.db.execute("CREATE INDEX IF NOT EXISTS active_review_unit ON records(parent,id) WHERE " + ACTIVE)
    daemon.store.db.execute("CREATE INDEX IF NOT EXISTS active_review_cwd ON records(json_extract(data,'$.dispatch_cwd'),json_extract(data,'$.run'),id) WHERE " + ACTIVE)
    daemon.store.db.execute("CREATE INDEX IF NOT EXISTS bound_review_answer ON records(parent,json_extract(data,'$.review_identity.run'),"
                           "json_extract(data,'$.review_identity.thread'),json_extract(data,'$.answer_digest')) WHERE kind='review'")
    for job in daemon.store.selected("SELECT * FROM records WHERE kind='review_job' AND json_extract(data,'$.state')='negative'"):
        if job.get("completion") and verdict(job["completion"].get("answer", "")) == "pass":
            complete(daemon, {"job": job["id"], "completion": job["completion"]})
    for job in daemon.store.selected("SELECT * FROM records INDEXED BY active_review_unit WHERE " + ACTIVE):
        abandon_stale(daemon.store, job)
    for path in (daemon.store.root / "review-packets").glob("review_job_*.json"):
        row = daemon.store.db.execute("SELECT json_extract(data,'$.state') FROM records WHERE id=? AND kind='review_job'", (path.stem,)).fetchone()
        if row is None or row[0] not in ("prepared", "dispatched"):
            path.unlink(missing_ok=True)
