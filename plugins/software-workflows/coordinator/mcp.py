"""The MCP servers a writer's threads start are its app-server's infrastructure, never a command's (2.4.24).

Codex starts a thread's MCP servers (a sub-agent's, a resumed one's) as the app-server's direct children: with the
thread, or, a server whose tools it has cached, as it dispatches the thread's first call that needs it, before that
call's hook. It reports each start with mcpServer/startupStatus/updated: "starting", then how it ended ("ready",
"failed"). A writer's services are inventoried when its root thread starts and before its first command
(Daemon.services_before_first_command); a process that appears after that is held, by kernel lineage, as a command's
survivor (processes.inventory). Through 2.4.23 such an MCP server was so held by every later command of its unit for as
long as it ran: on 10-29 one writer's MCP_DOCKER server held 71 custodies for over an hour, and its unit could not
settle. Now the coordinator names each stdio server in its launch environment (roles.overlay). A report of how a start
ended, and each reconcile, survey the app-server: its named servers, with what each started for no call, become the
listener's services (survey). Before each MCP call is admitted, the servers it may reach are bounded: what they start
once it is admitted can be the call's, and stays held (dispatching)."""
import re
import time

from . import processes, telemetry
from .client import CoordinatorError

# A survey that could not read every process is tried again before the listener's custody inventories, first after
# RETRY_S, then doubling to RETRY_MAX_S: the writers' reconcile backoff (recovery.py). A process that stays unreadable
# costs one survey per interval, not one per inventory.
RETRY_S, RETRY_MAX_S = 5, 300

# Codex 0.163 keeps a tool's namespace whole up to 113 bytes and cuts a longer one (codex-mcp src/tools.rs,
# fit_callable_parts_with_hash): its 128-byte name limit less a `_<12 hex>` hash and the `__` delimiter.
NAMESPACE_MAX = 128 - 13 - 2


def key(entry):
    return entry["pid"], entry["birth"]


def merged(existing, inventoried):
    """An inventory's services (processes.inventory) with what a survey or a call's admission recorded of each process
    already: the server it is or was started by, and a server's bound (dispatching)."""
    recorded = {key(p): p for p in existing}
    return [dict(p, **{k: recorded[key(p)][k] for k in ("service", "server", "until") if k in recorded.get(key(p), ())})
            for p in inventoried]


def letters(name):
    return re.sub(r"[^A-Za-z0-9]", "", name)


def reached(tool, names):
    """The configured MCP servers (`names`) a call of this native tool may reach, or None: any. None it may reach is
    left out, or what the call starts would be taken (review turn 126). Codex 0.163 hooks a call by its tool's
    namespace and name (core src/tools/handlers/mcp.rs, ensure_mcp_prefix(join_tool_name)): the namespace is the
    server's name with each character outside [A-Za-z0-9_] made `_`, `mcp__` before it unless it begins so, and a hash
    after it when two names would collide (codex-mcp src/tools.rs); the hook joins it to the tool with `__` once its
    trailing `_` are trimmed, and puts `mcp__` before unless it begins so. So servers `foo` and `foo_` both hook
    `mcp__foo__spawn`: only letters and digits tell a name, and a call may reach each server whose own, after `mcp` or
    not, begin the call's. A namespace Codex cuts (NAMESPACE_MAX) may begin as another server's does: while a
    configured name is long enough to be cut, with `mcp__` and a collision's hash, a call may reach any server. The
    user's ledger, 10-30: the hook name of each of 120 completed MCP calls began mcp__<its item's server>__."""
    if any(len(name.encode()) + len("mcp__") + 13 > NAMESPACE_MAX for name in names or ()):
        return None
    called = letters(str(tool))
    hits = {name for name in names or () if called.startswith("mcp" + letters(name)) or called.startswith(letters(name))}
    return hits or None


async def dispatching(daemon, ident, tool):
    """Before an MCP call is admitted, under the listener's services lock (Daemon.admitted_hook): each of the
    app-server's MCP servers the call may reach (reached), and each of its children whose environment cannot be read,
    is bounded now unless it was: a tool's work starts only once its call is admitted, so what a server started by its
    bound it started for no call, and what it starts after can be a call's (processes.started_services). A child whose
    identity cannot be read fails the call: its bound would be unknown (review turn 125). A server no survey took yet
    keeps its bound beside the services (mcp_bounds) until one does."""
    listener = daemon.store.get(ident)
    if listener.get("mcp_names") is None:
        return  # admitted through 2.4.23: its servers carry no name, and no survey takes any
    at = time.time()
    bounded = {key(p) for p in [*listener.get("services", []), *listener.get("mcp_bounds", [])] if "until" in p}
    found, present = await telemetry.to_thread(processes.dispatch_bounds, listener,
                                               reached(tool, listener["mcp_names"]), bounded)
    listener = daemon.store.get(ident)
    services = {key(p): p for p in listener.get("services", [])}
    bounds = [b for b in listener.get("mcp_bounds", []) if key(b) in present]  # one whose process is gone needs none
    for entry, server in found:
        if key(entry) in services:
            services[key(entry)].setdefault("until", at)
        elif all(key(b) != key(entry) for b in bounds):
            bounds.append(dict({k: entry[k] for k in ("pid", "birth", "kernel_id") if k in entry}, server=server, until=at))
    if bounds:
        listener["mcp_bounds"] = bounds
    else:
        listener.pop("mcp_bounds", None)
    daemon.store.save(listener)  # a save that changes nothing writes nothing (Store.put)


def status(daemon, listener, params):
    """Apply one mcpServer/startupStatus/updated: a report of how a start ended surveys the listener (survey), in the
    background, so the native intake never waits on the listener's services lock; "starting" changes nothing."""
    if params.get("status") != "starting":
        daemon.background(survey(daemon, listener["id"], {"name": params.get("name"), "thread": params.get("threadId"),
                                                          "status": params.get("status")}))


async def retry(daemon, listener):
    """Before a custody inventory of the listener: once due, survey again after a survey that could not read every
    process."""
    pending = listener.get("mcp_pending")
    if pending and time.time() >= pending.get("retry_at", 0):
        await survey(daemon, listener["id"])


async def survey(daemon, ident, report=None):
    """The listener's services from its app-server's MCP servers as they are now (processes.started_services): each
    named server, with what it started by its bound (dispatching), joins them, so no custody holds it; an MCP service
    that has exited leaves them. Every server's whole tree is read each time, so what one survey could not read a later
    one takes, under the same bound; a survey that could not read every process is tried again (retry) until one reads
    all (mcp_pending). Under the listener's services lock, as each inventory of its services and each MCP call's
    admission, so none of them saves over another's (review turn 125). A process that cannot be read never raises."""
    async with daemon.actor_locks.hold("services:" + ident):
        current = daemon.store.get(ident)
        if current.get("mcp_names") is None:
            return  # admitted through 2.4.23: Codex starts its servers with no name, and none is taken
        pending = current.get("mcp_pending") or {}
        bounds = {key(p): p["until"] for p in [*current.get("services", []), *current.get("mcp_bounds", [])]
                  if "until" in p}
        kept = [p for p in current.get("services", []) if p.get("service") == "mcp"] + list(current.get("mcp_bounds", []))

        def run():
            exited = set()
            for entry in kept:
                try:
                    if processes.exit_evidence(entry):
                        exited.add(key(entry))
                except CoordinatorError:
                    pass  # an unreadable process has not shown that it exited
            found, complete = processes.started_services(current, bounds)
            return found, complete, exited

        try:
            found, complete, exited = await telemetry.to_thread(run)
            code = None if complete else "PROCESS_UNREADABLE"
        except Exception as exc:
            found, exited, code = [], set(), getattr(exc, "code", type(exc).__name__)
        listener = daemon.store.get(ident)
        services = [p for p in listener.get("services", []) if key(p) not in exited]
        index = {key(p): p for p in services}
        added = []
        for entry in found:
            if key(entry) in index:
                index[key(entry)].update(service="mcp", server=entry["server"])  # an inventory took it as a service
            else:
                services.append(dict(entry, service="mcp"))
                added.append(services[-1])
        listener["services"] = services
        taken = {key(p) for p in services}
        left = [b for b in listener.get("mcp_bounds", []) if key(b) not in exited and key(b) not in taken]
        if left:
            listener["mcp_bounds"] = left
        else:
            listener.pop("mcp_bounds", None)
        attempts = pending.get("attempts", 0) + 1
        if code:
            listener["mcp_pending"] = {"attempts": attempts, "code": code,
                                       "retry_at": time.time() + min(RETRY_S * 2 ** min(attempts - 1, 16), RETRY_MAX_S)}
        else:
            listener.pop("mcp_pending", None)
        daemon.store.save(listener)  # a save that changes nothing writes nothing (Store.put)
        if added:
            daemon.store.event("listener/services_started", ident, {
                "report": report, "processes": [{"pid": p["pid"], "birth": p["birth"], "server": p["server"]} for p in added]})
        if code:
            daemon.store.event("listener/services_unread", ident, {"code": code, "attempts": attempts, "report": report})
