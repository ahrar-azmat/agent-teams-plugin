"""Names-only plugin/read cache, shared by writer and advisor preflight."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .client import CoordinatorError

MEMORY = {}
FIELDS = ("id", "version", "localVersion", "installedAt", "source", "enabled")


def identity(connection, plugin, market):
    from . import native
    argv = getattr(connection, "codex_argv", None) or [native.native_codex()]
    binaries = []
    for arg in argv:
        path = Path(shutil.which(arg) or arg)
        if path.is_file():
            info = path.stat()
            binaries.append([str(path.resolve()), info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns])
    value = {"binary": binaries, "argv": argv, "plugin": {key: plugin.get(key) for key in FIELDS},
             "marketplace": market.get("path") or market.get("name"), "schema": 1}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid(value):
    return isinstance(value, list) and all(isinstance(name, str) and name for name in value)


async def names(connection, plugin, market):
    key = identity(connection, plugin, market)
    if key in MEMORY:
        return list(MEMORY[key])
    # Persist only with an explicit plugin-data location. A read-only or absent
    # directory never makes a successful metadata read fail, and no credentials,
    # server args, environment values or plugin bodies enter this cache.
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(data) / "codex-plugin-catalog" / (key + ".json") if data else None
    if path:
        try:
            value = json.loads(path.read_text())
            if value.get("identity") == key and valid(value.get("names")):
                MEMORY[key] = tuple(value["names"])
                return list(MEMORY[key])
        except (OSError, ValueError, AttributeError):
            pass
    params = {"pluginName": plugin["name"]}
    params["marketplacePath" if market.get("path") else "remoteMarketplaceName"] = market.get("path") or market["name"]
    detail = (await connection.call("plugin/read", params))["plugin"]
    value = detail.get("mcpServers", [])
    if not valid(value):
        raise CoordinatorError("CODEX_PLUGIN_INVENTORY", "Plugin MCP names are unavailable", {"plugin": plugin["id"]})
    value = sorted(set(value))
    MEMORY[key] = tuple(value)
    if path:
        pending = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, pending = tempfile.mkstemp(prefix="pending-", dir=path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump({"identity": key, "names": value}, stream, sort_keys=True)
            os.replace(pending, path)
        except OSError:
            pass
        finally:
            if pending:
                try:
                    os.unlink(pending)
                except OSError:
                    pass
    return value
