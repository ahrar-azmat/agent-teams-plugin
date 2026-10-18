"""Lead session mapping. Only host metadata or an explicit session ID may bind it."""
from .client import CoordinatorError
from .store import digest


async def attach(daemon, checkoutid, session_id):
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 1024:
        raise CoordinatorError("CLAUDE_SESSION_REQUIRED", "Supply the actual Claude session_id from host hook metadata or explicitly")
    checkout = daemon.store.get(checkoutid)
    ident = "lead_session_" + digest([checkout["identity"], session_id])
    async with daemon.actor_locks.hold(ident):
        existing = daemon.store.get(ident, required=False)
        if existing and daemon.store.get(existing["unit"])["state"] != "published":
            return existing
        run = await daemon.create_run({"checkout": checkoutid, "mode": "lead", "task": "Claude lead session"})
        return daemon.store.put("lead_session", ident, {"checkout": checkoutid, "identity": checkout["identity"],
            "session_id": session_id, "actor": run["actor"], "unit": run["unit"], "run": run["run"],
            "previous_unit": existing and existing["unit"]}, checkoutid)


def own_unit(store, checkout, session_id):
    if not session_id:
        return None
    mapping = store.get("lead_session_" + digest([checkout["identity"], session_id]), required=False)
    return mapping["unit"] if mapping else None
