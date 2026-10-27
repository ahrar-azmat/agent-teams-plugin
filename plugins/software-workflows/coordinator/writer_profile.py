"""Explicit, names-only writer MCP policy; enumeration never creates MCP clients."""
import json
import os
import sqlite3
import time

from .client import CoordinatorError


def setting():
    source = next((key for key in ("SOFTWARE_WORKFLOWS_WRITER_MCP_SERVERS",
                                  "CLAUDE_PLUGIN_OPTION_WRITER_MCP_SERVERS") if key in os.environ), None)
    value = (os.environ[source].strip() if source else "all") or "all"
    if value == "all":
        names = None
    else:
        try:
            names = json.loads(value)
        except ValueError:
            names = None
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            return {"mode": "invalid", "names": [], "source": source,
                    "changes_apply": "next daemon start", "error": "WRITER_MCP_PROFILE",
                    "problem": "writer_mcp_servers must be all or a JSON list of nonempty exact server names"}
        names = sorted(set(names))
    return {"mode": "all" if names is None else "list", "names": names or [],
            "source": source or "default", "changes_apply": "next daemon start"}


def require_valid(selected):
    if selected["mode"] == "invalid":
        raise CoordinatorError("WRITER_MCP_PROFILE", selected["problem"],
                               {"source": selected["source"], "retryable": True}, True)


def suggestion(store):
    """One indexed, time-bounded query at daemon start; never used as policy.

    Thirty days is a policy choice, not a cap or evidence of future tool needs.
    The measured week contained 88 writer calls on two servers.
    """
    began = time.monotonic()
    try:
        rows = store.db.execute("SELECT json_extract(o.data,'$.completed_item.server'),count(*) FROM records o "
        "INDEXED BY writer_mcp_usage JOIN records a ON a.id=json_extract(o.data,'$.actor') "
        "WHERE o.kind='operation' AND json_extract(o.data,'$.completed_item.type')='mcpToolCall' "
        "AND json_extract(o.data,'$.completed_item.status')='completed' "
        "AND coalesce(json_extract(o.data,'$.completed'),json_extract(o.data,'$.created'))>=? "
        "AND json_extract(a.data,'$.kind')!='lead' GROUP BY json_extract(o.data,'$.completed_item.server')",
            (time.time() - 30 * 86400,)).fetchall()
    except sqlite3.Error as exc:
        return {"days": 30, "policy_choice": True, "unavailable": type(exc).__name__,
                "seconds": round(time.monotonic() - began, 6)}
    return {"days": 30, "policy_choice": True, "counts": {name: count for name, count in rows if isinstance(name, str)},
            "seconds": round(time.monotonic() - began, 6)}


async def selection(root=None):
    # Exec preflight has no daemon. It takes one immutable selection for that run.
    return setting()


async def configure(connection, effective, cwd, selected):
    """Read config/plugin metadata only, then install table-safe launch overrides.

    mcpServerStatus/list eagerly creates clients in 0.163.0 and is forbidden here.
    An unenumerable plugin can be disabled as a whole; it cannot supply a selected
    name without metadata proving that name exists. No profile widens itself.
    """
    require_valid(selected)
    users = effective.get("mcp_servers") or {}
    if selected["mode"] == "all":
        # No plugin metadata (which may contact a marketplace) is needed to
        # preserve native configuration. Capture only names, once for status.
        selected.setdefault("inventory", {"servers": sorted(users),
                            "scope": "first native user-server configuration; plugin enumeration skipped"})
        return {}, {**selected, "enabled": sorted(name for name, config in users.items() if config.get("enabled", True)),
                    "disabled": [], "disabled_plugins": []}
    plugins, unknown = {}, set()
    declared = {key: value for key, value in (effective.get("plugins") or {}).items()
                if key != "_default" and isinstance(value, dict)}
    if declared or effective.get("features", {}).get("plugins", True):
        try:
            listed = await connection.call("plugin/installed", {"cwds": [cwd]})
            for market in listed.get("marketplaces", []):
                for plugin in market.get("plugins", []):
                    if not plugin.get("installed"):
                        continue
                    params = {"pluginName": plugin["name"]}
                    if market.get("path"):
                        params["marketplacePath"] = market["path"]
                    else:
                        params["remoteMarketplaceName"] = market["name"]
                    try:
                        detail = (await connection.call("plugin/read", params))["plugin"]
                        plugins[plugin["id"]] = detail.get("mcpServers", [])
                    except CoordinatorError:
                        unknown.add(plugin["id"])
        except CoordinatorError:
            unknown.update(declared)
    unknown.update(set(declared) - set(plugins))
    all_names = set(users) | {name for names in plugins.values() for name in names}
    catalog = set(selected.setdefault("plugin_catalog", sorted(set(plugins) | unknown)))
    selected["later_plugins"] = "Plugins installed later are disabled for writers until the coordinator restarts"
    chosen = set(selected["names"])
    if chosen - all_names:
        raise CoordinatorError("WRITER_MCP_UNKNOWN", "Writer MCP profile names are not in the native configuration",
                               {"servers": sorted(chosen - all_names), "unreadable_plugins": sorted(unknown)})
    restrictions = {"mcp_servers": {name: {"enabled": name in chosen} for name in users},
                    "plugins": {"_default": {"enabled": False}}}
    disabled_plugins = {}
    for ident in sorted(set(plugins) | unknown):
        names = plugins.get(ident, [])
        if ident in unknown or ident not in catalog:
            disabled_plugins[ident] = "metadata unavailable" if ident in unknown else "installed after the frozen catalog"
            restrictions["plugins"][ident] = {"enabled": False}
        else:
            enabled = bool(chosen.intersection(names)) or declared.get(ident, {}).get("enabled", True)
            restrictions["plugins"][ident] = {"enabled": enabled,
                "mcp_servers": {name: {"enabled": name in chosen} for name in names}}
            if not enabled:
                disabled_plugins[ident] = "disabled in native configuration"
    evidence = {**selected, "enabled": sorted(chosen), "disabled": sorted(all_names - chosen),
                "disabled_plugins": sorted(disabled_plugins), "disabled_plugin_reasons": disabled_plugins,
                "plugin_servers": plugins, "user_servers": sorted(users)}
    line = context(evidence)
    prior = [part for part in (effective.get("developer_instructions") or "").splitlines()
             if not part.startswith("Writer MCP profile excludes:")]
    restrictions["developer_instructions"] = "\n".join(prior + [line]).strip()
    return restrictions, evidence


def context(profile):
    excluded = profile.get("disabled", []) + ["plugin:" + name + " (" + profile.get("disabled_plugin_reasons", {}).get(name, "metadata unavailable") + ")"
               for name in profile.get("disabled_plugins", [])]
    return ("Writer MCP profile excludes: " + (", ".join(excluded) or "none") +
            ". To enable a server, change writer_mcp_servers and restart the coordinator. "
            "Plugins installed later stay disabled until that restart.")


async def deliver_context(daemon, actor, preparing_input=False):
    """Old threads keep their initial context on native resume. Publish once.

    A fresh writer includes the line in thread/start's developer instructions.
    A migrated thread gets a durable notice before input instead. Its stable
    message identity reuses delivery evidence across readmissions and retries.
    """
    from .store import digest
    store = daemon.store
    profile = store.get(actor["listener"]).get("writer_profile", {})
    if profile.get("mode") != "list":
        return
    line = context(profile)
    if actor.get("writer_profile_context") == line:
        return
    ident = "writer_profile:" + actor["id"] + ":" + digest(line)
    message = await daemon.messages.send("coordinator", actor["id"], line, "none", notice=True, ident=ident)
    if message["delivery"] == "cancelled" and not store.get(actor["run"]).get("cancel_requested"):
        message["delivery"] = "pending"
        store.save(message)
    await daemon.messages.deliver(ident)
    if store.get(ident)["state"] not in ("entered_context", "acknowledged"):
        if preparing_input:
            return  # writer/messages durably prefix it to this authorized input
        raise CoordinatorError("WRITER_MCP_CONTEXT", "The writer profile notice is still pending; retry before submitting input",
                               {"message": ident, "retryable": True}, True)
    actor = store.get(actor["id"])
    actor["writer_profile_context"] = line
    store.save(actor)


def verify(effective, profile):
    """Refuse a native configuration layer that did not honor the launch policy."""
    require_valid(profile)
    if profile["mode"] == "all":
        return
    users = effective.get("mcp_servers") or {}
    bad = [name for name, config in users.items() if bool(config.get("enabled", True)) != (name in profile["enabled"])]
    bad.extend(name for name in profile.get("user_servers", []) if name in profile["enabled"] and name not in users)
    for ident, names in profile.get("plugin_servers", {}).items():
        policy = (effective.get("plugins") or {}).get(ident, {})
        for name in names:
            enabled = policy.get("enabled", True) and (policy.get("mcp_servers") or {}).get(name, {}).get("enabled", True)
            if bool(enabled) != (name in profile["enabled"]):
                bad.append(name)
    for ident, policy in (effective.get("plugins") or {}).items():
        if ident == "_default" or not isinstance(policy, dict) or not policy.get("enabled", True):
            continue
        for name, config in (policy.get("mcp_servers") or {}).items():
            if bool(config.get("enabled", True)) != (name in profile["enabled"]):
                bad.append(name)
        if ident in profile.get("disabled_plugins", []):
            bad.append("plugin:" + ident)
    if bad:
        raise CoordinatorError("WRITER_MCP_UNENFORCEABLE", "Native configuration refused the writer MCP profile", {"servers": sorted(bad)})
    if context(profile) not in (effective.get("developer_instructions") or ""):
        raise CoordinatorError("WRITER_MCP_CONTEXT", "Native configuration omitted the writer MCP profile context")
