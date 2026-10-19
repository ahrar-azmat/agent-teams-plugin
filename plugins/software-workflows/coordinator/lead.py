"""Lead session mapping. Only host metadata or an explicit session ID may bind it."""
import os

from .client import CoordinatorError
from .store import digest
from . import participants


async def attach(daemon, checkoutid, session_id, project=None, cwd=None, dispatch_cwd=None, fixture=None, hook=False):
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 1024:
        raise CoordinatorError("CLAUDE_SESSION_REQUIRED", "Supply the actual Claude session_id from host hook metadata or explicitly")
    checkout = daemon.store.get(checkoutid)
    # Existing membership is pinned even if the native project later changes.
    matches = [m for m in daemon.store.lead_sessions(session_id) if participants.contains(daemon.store, m["unit"], checkoutid)
               and (not project or m.get("project") == project)
               and daemon.store.get(m["unit"])["state"] != "published"]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1 and not hook:
        raise CoordinatorError("PROJECT_AMBIGUOUS", "This session owns multiple units here; select a project or unit",
                               {"candidates": [{"id": m.get("project"), "unit": m["unit"]} for m in matches]})
    if hook:
        project = daemon.projects.hook_project(checkoutid, dispatch_cwd or cwd)
    elif not project and any(checkoutid in {b["checkout"] for b in daemon.store.get(p["revision"])["bindings"]}
                           for p in daemon.store.all("project")):
        fixture = fixture or os.environ.get("SOFTWARE_WORKFLOWS_FIXTURE")
        selected = await daemon.projects.resolve({"cwd": cwd or checkout["root"],
                                                   "dispatch_cwd": dispatch_cwd or cwd, "fixture": fixture, "registered": True})
        project = selected["project"] and selected["project"]["id"]
    ident = "lead_session_" + digest([project or checkout["identity"], session_id])
    if hook and project:
        existing = daemon.store.get(ident, required=False)
        if existing and not participants.contains(daemon.store, existing["unit"], checkoutid):
            # A refreshed project must not retarget an existing session unit.
            project = None
            ident = "lead_session_" + digest([checkout["identity"], session_id])
    async with daemon.actor_locks.hold(ident):
        existing = daemon.store.get(ident, required=False)
        if existing and daemon.store.get(existing["unit"])["state"] != "published":
            if not participants.contains(daemon.store, existing["unit"], checkoutid):
                raise CoordinatorError("UNIT_SCOPE", "Existing lead unit is pinned to its original participants")
            return existing
        if existing and not participants.contains(daemon.store, existing["unit"], checkoutid):
            raise CoordinatorError("UNIT_SCOPE", "Existing lead session is pinned to its original project revision")
        params = {"checkout": checkoutid, "mode": "lead", "task": "Claude lead session", "cwd": cwd or checkout["root"]}
        if project:
            params.update(project=project, dispatch_cwd=dispatch_cwd, fixture=fixture)
        registered = daemon.store.get("project_" + digest(project), required=False) if project else None
        revision = daemon.store.get(registered["revision"]) if registered else None
        if hook and revision:
            from .projects import containing
            if not any(containing(b["root"], params["cwd"]) for b in revision["bindings"]):
                params["cwd"] = next(b["root"] for b in revision["bindings"] if b["checkout"] == checkoutid)
        run = await daemon.create_run(params, revision=revision)
        box = mailbox(daemon.store, session_id, run["unit"])
        unit = daemon.store.get(run["unit"])
        unit["lead_mailbox"] = box
        daemon.store.save(unit)
        return daemon.store.put("lead_session", ident, {"checkout": checkoutid, "identity": checkout["identity"],
            "session_id": session_id, "project": project, "mailbox": box, "actor": run["actor"], "unit": run["unit"], "run": run["run"],
            "previous_unit": existing and existing["unit"]}, checkoutid)


def own_unit(store, checkout, session_id):
    if not session_id:
        return None
    matches = [m for m in store.lead_sessions(session_id) if participants.contains(store, m["unit"], checkout["id"])]
    return matches[-1]["unit"] if matches else None


def session_actors(store, checkoutid, session_id):
    return [m["actor"] for m in store.lead_sessions(session_id) if participants.contains(store, m["unit"], checkoutid)]


def mailbox(store, session_id, unitid):
    if not session_id:
        raise CoordinatorError("CLAUDE_SESSION_REQUIRED", "Lead mailbox requires the actual host session_id and unit")
    key = "lead_mailbox_" + digest([session_id, unitid])
    if not store.get(key, required=False):
        store.put("lead_mailbox", key, {"session_id": session_id, "unit": unitid}, unitid)
    return key
