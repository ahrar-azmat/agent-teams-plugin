"""Hide lead authority in inherited MCP servers without removing other servers."""
from . import owned
import json
import re

from .client import CoordinatorError
from . import native
from .store import digest


LEAD_TOOLS = {"coordinator_claims", "coordinator_send", "coordinator_inbox", "coordinator_unit",
              "coordinator_seal", "coordinator_publish", "code_review", "abraham",
              "codex_resume_run", "codex_cancel_run", "codex_runs", "codex_run_log"}


def related(name, config=None, names=()):
    identity = (name + " " + str((config or {}).get("command", "")) + " " + str((config or {}).get("args", []))).lower()
    return any(label in identity for label in ("software-workflows", "codex-oracle")) or {"abraham", "architect_review"} <= set(names)


def overlay(config):
    # Codex's dotted -c parser splits keys before TOML parsing; quoted dotted
    # segments are not portable. Inline TOML tables quote every key safely and
    # merge only these leaves, preserving unrelated servers and secret values.
    overrides = {}
    def policy(settings):
        value = {"disabled_tools": sorted(set(settings.get("disabled_tools") or []) | LEAD_TOOLS)}
        if settings.get("command"):
            value["env"] = {"SOFTWARE_WORKFLOWS_ROLE": "writer"}
        return value
    for name, settings in (config.get("mcp_servers") or {}).items():
        if related(name, settings):
            overrides.setdefault("mcp_servers", {})[name] = policy(settings)
    for plugin, value in (config.get("plugins") or {}).items():
        if not isinstance(value, dict):
            continue
        for name, settings in (value.get("mcp_servers") or {}).items():
            if related(plugin + " " + name, settings):
                overrides.setdefault("plugins", {}).setdefault(plugin, {}).setdefault("mcp_servers", {})[name] = policy(settings)
    return overrides


def apply(config, restrictions):
    import tomllib
    # Coalesce existing explicit dotted overrides before adding a table overlay;
    # two -c assignments to the same table otherwise replace earlier leaves.
    def merge(target, patch):
        for key, item in patch.items():
            if isinstance(item, dict) and isinstance(target.get(key), dict):
                merge(target[key], item)
            else:
                target[key] = item
    # A readmission can contain a coalesced table plus newer dotted leaf
    # overrides. Each native -c assignment is independent; parsing them as one
    # TOML document incorrectly rejects that valid table/leaf combination.
    value = {}
    for key, item in config.items():
        merge(value, tomllib.loads(key + "=" + native.toml(item)))
    merge(value, restrictions)
    return value


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
    def __init__(self, process):
        self.process, self.counter = process, 0

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


async def exec_policy(argv0, cwd, config):
    """Inspect the actual exec binary/config and prove its thread catalog.

    No turns, schemas, hook probes, or model calls. No configuration or environment
    values are returned in evidence. All overrides are also applied to exec.
    """
    import asyncio
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connection(overrides):
        process = await owned.spawn(*argv0, "app-server", "--listen", "stdio://",
            *native.override_args(overrides), cwd=cwd, env=native.environment(SOFTWARE_WORKFLOWS_ROLE="writer"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=16 * 1024 * 1024, start_new_session=True)
        try:
            client = CatalogProcess(process)
            await client.initialize()
            yield client
        finally:
            from .children import terminate
            await terminate(process, group=True)

    from .writer_profile import configure, selection, verify as verify_profile, require_valid
    selected = await selection()
    require_valid(selected)
    async with connection(config) as client:
        effective = (await client.call("config/read", {"cwd": cwd, "includeLayers": False}))["config"]
        policy, profile = await configure(client, effective, cwd, selected)
        restrictions = apply(overlay(effective), policy)
    async with connection(apply(config, restrictions)) as client:
        verify_profile((await client.call("config/read", {"cwd": cwd, "includeLayers": False}))["config"], profile)
        thread = await client.call("thread/start", {"cwd": cwd, "model": config["model"],
                                   "approvalPolicy": "never", "sandbox": "danger-full-access", "ephemeral": True})
        evidence = await verify(client, thread["thread"]["id"])
        expected = set(profile["enabled"])
        visible = {c["server"] for c in evidence["catalogs"]} | set(evidence["unavailable"])
        if expected - visible:
            raise CoordinatorError("WRITER_CATALOG_INCOMPLETE", "Configured MCP servers missing from writer catalog", {"servers": sorted(expected - visible)})
        evidence["writer_profile"] = profile
        return restrictions, evidence
