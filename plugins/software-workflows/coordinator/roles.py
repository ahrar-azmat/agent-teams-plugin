"""Hide lead authority in inherited MCP servers without removing other servers."""
from . import owned
import json
import re

from .client import CoordinatorError
from . import native
from . import push_guard
from .processes import MCP_SERVER_ENV
from .store import digest


LEAD_TOOLS = {"coordinator_claims", "coordinator_send", "coordinator_inbox", "coordinator_unit",
              "coordinator_seal", "coordinator_publish", "code_review", "abraham",
              "codex_resume_run", "codex_cancel_run", "codex_runs", "codex_run_log"}


def related(name, config=None, names=()):
    identity = (name + " " + str((config or {}).get("command", "")) + " " + str((config or {}).get("args", []))).lower()
    return any(label in identity for label in ("software-workflows", "codex-oracle")) or {"abraham", "architect_review"} <= set(names)


def overlay(config, source=None):
    # Codex's dotted -c parser splits keys before TOML parsing; quoted dotted
    # segments are not portable. Inline TOML tables quote every key safely and
    # merge only these leaves, preserving unrelated servers and secret values.
    # Every stdio server also names itself in its environment (MCP_SERVER_ENV): what makes a server that a writer's
    # thread starts after the writer's first command its service, not a command's survivor (mcp.survey).
    overrides = {}
    def policy(settings):
        value = {"disabled_tools": sorted(set(settings.get("disabled_tools") or []) | LEAD_TOOLS)}
        if settings.get("command"):
            value["env"] = {"SOFTWARE_WORKFLOWS_ROLE": "writer"}
        return value
    def mark(path, name, settings):
        if isinstance(settings, dict) and settings.get("command"):
            servers = overrides
            for key in path:
                servers = servers.setdefault(key, {})
            servers.setdefault(name, {}).setdefault("env", {})[MCP_SERVER_ENV] = name
    for name, settings in (config.get("mcp_servers") or {}).items():
        if related(name, settings):
            overrides.setdefault("mcp_servers", {})[name] = policy(settings)
        mark(("mcp_servers",), name, settings)
    for plugin, value in (config.get("plugins") or {}).items():
        if not isinstance(value, dict):
            continue
        for name, settings in (value.get("mcp_servers") or {}).items():
            if related(plugin + " " + name, settings):
                overrides.setdefault("plugins", {}).setdefault(plugin, {}).setdefault("mcp_servers", {})[name] = policy(settings)
            mark(("plugins", plugin, "mcp_servers"), name, settings)
    result = apply(overrides, push_guard.overlay(config, source))
    from .services import carry_guard
    carry_guard(result["shell_environment_policy"]["set"])
    return result


def unmarked(config):
    """The stdio MCP servers of an effective configuration whose environment lacks the name overlay() set: Codex did not
    honor it, so a thread that starts such a server after the writer's first command leaves it held as a command's
    survivor, as through 2.4.23."""
    servers = [(name, settings) for name, settings in (config.get("mcp_servers") or {}).items()]
    for plugin, value in (config.get("plugins") or {}).items():
        if isinstance(value, dict):
            servers += [(plugin + " " + name, settings) for name, settings in (value.get("mcp_servers") or {}).items()]
    return sorted(name for name, settings in servers if isinstance(settings, dict) and settings.get("command")
                  and MCP_SERVER_ENV not in (settings.get("env") or {}))


def servers(config):
    """The name of each MCP server of an effective configuration, a plugin's by its own name too (the name overlay()
    gives it): what an MCP call's native name is matched against for the servers it may reach (mcp.reached)."""
    names = set(config.get("mcp_servers") or {})
    for value in (config.get("plugins") or {}).values():
        if isinstance(value, dict):
            names |= set(value.get("mcp_servers") or {})
    return sorted(names)


def merge(config, restrictions):
    """Merge effective config dictionaries without TOML serialization or mutation."""
    from copy import deepcopy
    value = deepcopy(config)
    for key, item in restrictions.items():
        value[key] = merge(value[key], item) if isinstance(item, dict) and isinstance(value.get(key), dict) else deepcopy(item)
    return value


def apply(config, restrictions):
    import tomllib
    # Normalize explicit command-line overrides only. An effective config/read
    # dictionary contains nulls and secrets and must never be serialized here.
    # A readmission can contain a coalesced table plus newer dotted leaf
    # overrides. Each native -c assignment is independent; parsing them as one
    # TOML document incorrectly rejects that valid table/leaf combination.
    value = {}
    for key, item in config.items():
        value = merge(value, tomllib.loads(key + "=" + native.toml(item)))
    return merge(value, restrictions)


async def verify(connection, thread):
    import asyncio
    import time
    # Only a server that can carry the lead's tools decides this proof. Any other server that is
    # down or slow cannot expose them, and Codex runs the thread without it, as it does anywhere.
    # Requiring every configured server refused every writer while one unrelated server was down
    # (measured, 2.3.0: auth0).
    def lead_capable(server, names=()):
        return related(server["name"] + " " + str(server.get("pluginId") or ""), names=names)
    deadline = time.monotonic() + 60
    while True:
        servers = []
        async for page in native.page(connection, "mcpServerStatus/list", {"threadId": thread, "detail": "toolsAndAuthOnly"}):
            servers.extend(page["data"])
        pending = [s["name"] for s in servers if s.get("runtimeStatus") in ("starting", "notStarted") and lead_capable(s)]
        if not pending or time.monotonic() >= deadline:
            break
        await asyncio.sleep(.1)
    catalogs, unavailable = [], []
    for server in servers:
        if server["name"] == "codex_apps" or (server.get("pluginId") and server.get("runtimeStatus") != "disabled"):
            push_guard.fail("Connected apps or a plugin MCP server escaped the publishing policy", server=server["name"])
        names = {tool.get("name", name).split("__")[-1] for name, tool in server.get("tools", {}).items()}
        down = bool(server.get("toolsError")) or server.get("runtimeStatus") in ("starting", "notStarted", "failed")
        if lead_capable(server, names):
            if down:
                raise CoordinatorError("WRITER_CATALOG_UNVERIFIED", "Inherited plugin tool catalog is not available", {"server": server["name"]})
            forbidden = names & LEAD_TOOLS
            if forbidden:
                raise CoordinatorError("WRITER_LEAD_TOOLS", "Inherited writer catalog exposes lead authority", {"server": server["name"], "tools": sorted(forbidden)})
            catalogs.append({"server": server["name"], "tools": sorted(names), "error": False, "lead_capable": True})
        elif down:
            unavailable.append(server["name"])
        else:
            catalogs.append({"server": server["name"], "tools": sorted(names), "error": False, "lead_capable": False})
    # The digest binds what can carry authority, so another server's health never forces recalibration.
    return {"digest": digest([c for c in catalogs if c["lead_capable"]]), "catalogs": catalogs,
            "unavailable": sorted(unavailable)}


class CatalogProcess:
    """Basic stdio app-server RPC, independent of native admission facilities."""
    def __init__(self, process, codex_argv=None):
        self.process, self.counter = process, 0
        self.codex_argv = codex_argv

    async def call(self, method, params=None, timeout=60):
        import asyncio
        self.counter += 1
        ident = self.counter
        self.process.stdin.write((json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n").encode())
        await self.process.stdin.drain()
        async def response():
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise CoordinatorError("WRITER_CATALOG_UNAVAILABLE", "Catalog preflight app-server exited")
                value = json.loads(line)
                if value.get("id") == ident and "method" not in value:
                    if "error" in value:
                        raise CoordinatorError("WRITER_CATALOG_UNAVAILABLE", "Catalog preflight RPC failed", {"method": method})
                    return value["result"]
                if "id" in value and "method" in value:
                    raise CoordinatorError("WRITER_CATALOG_UNAVAILABLE", "Unexpected approval/tool request before inference")
        return await asyncio.wait_for(response(), timeout)

    async def initialize(self):
        await self.call("initialize", {"clientInfo": {"name": "software-workflows-catalog", "version": "2.0.0"},
                                       "capabilities": {"experimentalApi": False}})
        self.process.stdin.write(b'{"method":"initialized"}\n')
        await self.process.stdin.drain()


def without_user_config(response):
    """Conservative parity check for exec --ignore-user-config (no app-server flag).

    config/read layers are highest precedence first. Project trust and managed
    configuration can depend on the omitted user layer; refuse that ambiguity
    rather than call a differently configured preflight proof.
    """
    layers = response.get("layers")
    if not isinstance(layers, list):
        push_guard.fail("Exec config layer inventory unavailable")
    result = {}
    for layer in reversed(layers):
        kind = layer.get("name", {}).get("type")
        if kind == "user":
            continue
        if kind not in ("sessionFlags", "system", "project"):
            push_guard.fail("Cannot establish --ignore-user-config parity for this configuration layer", layer=kind)
        if kind == "project" and layer.get("config"):
            push_guard.fail("Cannot establish project trust parity with --ignore-user-config")
        if not layer.get("disabledReason"):
            result = merge(result, layer.get("config") or {})
    for key, origin in response.get("origins", {}).items():
        if key.startswith(("shell_environment_policy", "mcp_servers", "plugins")) and origin.get("name", {}).get("type") == "packagedDefaults":
            push_guard.fail("Packaged launch policy is not exposed in the exec layer inventory")
    return result


async def exec_policy(argv0, cwd, config, *, writer=True, ignore_user_config=False, environment=None):
    """Inspect the actual exec binary/config and prove its thread catalog.

    No turns, schemas, hook probes, or model calls. No configuration or environment
    values are returned in evidence. All overrides are also applied to exec.
    """
    import asyncio
    from contextlib import asynccontextmanager
    launch_env = environment if environment is not None else native.environment(SOFTWARE_WORKFLOWS_ROLE="writer")

    @asynccontextmanager
    async def connection(overrides):
        process = await owned.spawn(*argv0, "app-server", "--listen", "stdio://",
            *native.override_args(apply(overrides, {"features": {"apps": False}})), cwd=cwd, env=launch_env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=16 * 1024 * 1024, start_new_session=True)
        try:
            client = CatalogProcess(process, argv0)
            await client.initialize()
            yield client
        finally:
            from .children import terminate
            await terminate(process, group=True)

    from .writer_profile import configure, selection, verify as verify_profile, require_valid
    selected = await selection() if writer else None
    if writer:
        require_valid(selected)
    async def effective_config(client):
        response = await client.call("config/read", {"cwd": cwd, "includeLayers": ignore_user_config})
        if ignore_user_config:
            requirements = (await client.call("configRequirements/read")).get("requirements") or {}
            if requirements.get("shell_environment_policy"):
                push_guard.fail("Cannot establish ignored-user parity for managed shell policy")
            return without_user_config(response)
        return response["config"]
    async with connection(config) as client:
        effective = await effective_config(client)
        if writer:
            policy, profile = await configure(client, effective, cwd, selected)
            restrictions = apply(overlay(effective, launch_env), policy)
        else:
            plugins, unknown = await push_guard.plugin_inventory(client, effective, cwd)
            restrictions = apply(push_guard.overlay(effective, launch_env),
                                 push_guard.plugin_restrictions(effective, plugins, unknown))
            profile = push_guard.plugin_evidence(restrictions, plugins, unknown)
    async with connection(apply(config, restrictions)) as client:
        effective = await effective_config(client)
        push_guard.verify(effective, launch_env)
        push_guard.verify_plugin_policy(effective, restrictions.get("plugins"))
        if not writer:
            return restrictions, {"publish_guard": True, "ignore_user_config": ignore_user_config, **profile}
        verify_profile(effective, profile)
        thread = await client.call("thread/start", {"cwd": cwd, "model": config["model"],
                                   "approvalPolicy": "never", "sandbox": "danger-full-access", "ephemeral": True})
        evidence = await verify(client, thread["thread"]["id"])
        expected = set(profile["enabled"])
        visible = {c["server"] for c in evidence["catalogs"]} | set(evidence["unavailable"])
        if expected - visible:
            raise CoordinatorError("WRITER_CATALOG_INCOMPLETE", "Configured MCP servers missing from writer catalog", {"servers": sorted(expected - visible)})
        evidence["writer_profile"] = profile
        return restrictions, evidence
