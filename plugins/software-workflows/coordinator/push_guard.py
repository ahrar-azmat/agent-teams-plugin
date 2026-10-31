"""Codex-only push refusal and launch-policy checks (also imported by Python 3.9 hooks).

This is inherited Git configuration, not an execution or credential boundary.
Explicit pushurl, --no-verify, environment replacement and other transports must
remain visible in the capability report; none is evidence of a universal guard.
"""
import os
from pathlib import Path
import re
import shlex

NAME = "sw-codex-no-push"
HELPERS = str(Path(__file__).resolve().parents[1] / "hooks" / "codex-publish")
MARKER = "SOFTWARE_WORKFLOWS_PUSH_GUARD_PATH"
REFUSAL = "LEAD_PUBLISHES: the lead (Claude) pushes; Codex cannot publish commits."
INSTRUCTIONS = ("Publishing through Git, gh, the GitHub API or an MCP tool belongs to the lead (Claude). "
                "Leave changes uncommitted for the lead.")
PLUGIN_MCP_REASON = "Codex 0.163.0 cannot carry the push guard into plugin MCP servers"


def fail(message, **details):
    from .client import CoordinatorError
    raise CoordinatorError("CODEX_PUBLISH_ENV", "CODEX_PUBLISH_ENV: " + message, details)


def quote(value):
    # Git's sq_quote_buf, not shell expansion. Always quote even an empty value.
    return "'" + value.replace("'", "'\\''").replace("!", "'\\!'") + "'"


PAIRS = [("url." + NAME + "::.pushInsteadOf", ""),
         ("protocol." + NAME + ".allow", "always"),
         ("hook." + NAME + ".event", ""),
         ("hook." + NAME + ".event", "pre-push"),
         ("hook." + NAME + ".command", "printf '%s\\n' " + shlex.quote(REFUSAL) + " >&2; exit 1"),
         ("hook." + NAME + ".enabled", "true"),
         ("hook.pre-push.enabled", "true")]
PARAMETERS = " ".join(quote(k) + "=" + quote(v) for k, v in PAIRS)


def require_parameters(value, location):
    """Only our public constant may cross the config/argv/ledger boundary."""
    if value not in ("", PARAMETERS):
        fail("Foreign GIT_CONFIG_PARAMETERS at " + location + "; move those settings into a Git config file",
             location=location)


def parameters(value):
    """Validate Git's old and new single-quoted environment formats, without logging values."""
    if not isinstance(value, str) or "\0" in value:
        fail("Malformed inherited Git parameters")
    pos, result = 0, []

    def word():
        nonlocal pos
        if pos >= len(value) or value[pos] != "'":
            fail("Malformed inherited Git parameters")
        out = ""
        while True:
            pos += 1
            end = value.find("'", pos)
            if end < 0:
                fail("Malformed inherited Git parameters")
            out += value[pos:end]
            pos = end + 1
            if value[pos:pos + 2] not in ("\\'", "\\!"):
                return out
            out += value[pos + 1]
            pos += 2
            if pos >= len(value) or value[pos] != "'":
                fail("Malformed inherited Git parameters")

    while pos < len(value):
        if value[pos].isspace():
            pos += 1
            continue
        key = word()
        if pos < len(value) and value[pos] == "=":
            pos += 1
            val = word() if pos < len(value) and value[pos] == "'" else None
        else:
            key, sep, val = key.partition("=")
            val = val if sep else None
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*\..+", key) or (pos < len(value) and not value[pos].isspace()):
            fail("Malformed inherited Git parameters")
        result.append((key, val))
    return result


def detach(source):
    """Remove only our exact contribution, including one inherited from an older install."""
    env = dict(source)
    value = env.get("GIT_CONFIG_PARAMETERS", "")
    parameters(value)
    # We add exactly one suffix. An unrelated later -c/config contribution is
    # not discarded; a missing suffix with our marker is an explicit refusal.
    marked = env.pop(MARKER, None)
    if value == PARAMETERS or value.endswith(" " + PARAMETERS):
        value = value[:-len(PARAMETERS)]
        if value.endswith(" "):
            value = value[:-1]
        if value:
            env["GIT_CONFIG_PARAMETERS"] = value
        else:
            env.pop("GIT_CONFIG_PARAMETERS", None)
    elif marked:
        fail("Inherited Codex guard was replaced; cannot safely detach coordinator environment")
    if marked:
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != marked)
    return env


def environment(source):
    """Pure, idempotent transformation. Never changes the lead's process environment."""
    # An exact guard detaches to empty. Any other nonempty value, including a
    # foreign prefix before our suffix, would otherwise be serialized by overlay.
    require_parameters(source.get("GIT_CONFIG_PARAMETERS", ""), "launch environment")
    env = detach(source)
    if "GIT_ALLOW_PROTOCOL" in env and NAME not in env["GIT_ALLOW_PROTOCOL"].split(":"):
        fail("Git protocol policy excludes the push-refusal helper")
    env["GIT_CONFIG_PARAMETERS"] = PARAMETERS
    path = env.get("PATH", os.defpath)
    env["PATH"] = path + (os.pathsep if path else "") + HELPERS if HELPERS not in path.split(os.pathsep) else path
    env[MARKER] = HELPERS
    return env


def present(env):
    return (env.get("GIT_CONFIG_PARAMETERS") == PARAMETERS and env.get(MARKER) == HELPERS
            and HELPERS in env.get("PATH", "").split(os.pathsep)
            and ("GIT_ALLOW_PROTOCOL" not in env or NAME in env["GIT_ALLOW_PROTOCOL"].split(":")))


def _matches(name, patterns):
    # Codex EnvironmentVariablePattern uses wildmatch's * and ? (case insensitive),
    # not Python fnmatch's additional bracket expressions.
    return any(re.fullmatch("".join(".*" if c == "*" else "." if c == "?" else re.escape(c)
                                  for c in pattern), name, re.IGNORECASE) for pattern in patterns)


def shell_environment(config, source):
    """Codex 0.163.0 policy ordering; unknown policy semantics refuse before inference."""
    policy = config.get("shell_environment_policy") or {}
    known = {"inherit", "ignore_default_excludes", "exclude", "include_only", "set", "filters", "experimental_use_profile"}
    if not isinstance(policy, dict) or set(policy) - known or policy.get("experimental_use_profile"):
        fail("Shell environment policy cannot be proven")
    inherit = policy.get("inherit") or "all"
    if inherit not in ("all", "core", "none"):
        fail("Unknown shell environment inheritance")
    # Only the two required variables matter; core does not contain Git parameters.
    env = dict(source) if inherit == "all" else {k: v for k, v in source.items() if k == "PATH" and inherit == "core"}
    exclude, include = policy.get("exclude") or [], policy.get("include_only") or []
    if policy.get("filters") is not None:
        filters = policy["filters"]
        if exclude or include or not isinstance(filters, dict) or any(v not in ("include", "exclude") for v in filters.values()):
            fail("Unknown shell environment filters")
        exclude = [k for k, v in filters.items() if v == "exclude"]
        include = [k for k, v in filters.items() if v == "include"]
    if any(not isinstance(p, str) for p in [*exclude, *include]):
        fail("Malformed shell environment filters")
    if policy.get("ignore_default_excludes") is False:
        exclude = [*exclude, "*KEY*", "*SECRET*", "*TOKEN*"]
    env = {k: v for k, v in env.items() if not _matches(k, exclude)}
    env.update(policy.get("set") or {})
    if include:
        env = {k: v for k, v in env.items() if _matches(k, include)}
    return env


def stdio_servers(config):
    for name, settings in (config.get("mcp_servers") or {}).items():
        yield ("mcp_servers", name), settings
    for plugin, settings in (config.get("plugins") or {}).items():
        if isinstance(settings, dict) and settings.get("enabled", True):
            for name, server in (settings.get("mcp_servers") or {}).items():
                yield ("plugins", plugin, "mcp_servers", name), server


async def plugin_inventory(connection, config, cwd):
    """Read names only, before any thread can start plugin MCP processes."""
    if config.get("features", {}).get("plugins") is False:
        return {}, set()
    from .client import CoordinatorError
    from .plugin_catalog import names
    declared = {key for key, value in (config.get("plugins") or {}).items()
                if key != "_default" and isinstance(value, dict)}
    plugins, unknown = {}, set()
    try:
        listed = await connection.call("plugin/installed", {"cwds": [cwd]})
        for market in listed.get("marketplaces", []):
            for plugin in market.get("plugins", []):
                if not plugin.get("installed"):
                    continue
                try:
                    plugins[plugin["id"]] = await names(connection, plugin, market)
                except CoordinatorError:
                    unknown.add(plugin["id"])
    except CoordinatorError:
        # Without an inventory, an enabled default could start an unseen server.
        if (config.get("plugins") or {}).get("_default", {}).get("enabled", True):
            fail("Installed plugin inventory is unavailable")
    unknown.update(declared - set(plugins))
    return plugins, unknown


def plugin_restrictions(config, plugins, unknown):
    """Disable plugin MCP transports while preserving each known plugin's skills.

    Freeze the inventory with a disabled default so a newly installed plugin
    cannot acquire a server after this threadless proof. Skills-only plugins keep
    their enablement. Unreadable plugins are disabled until metadata is available.
    """
    if config.get("features", {}).get("plugins") is False:
        return {}
    policy = config.get("plugins") or {}
    default = policy.get("_default", {}).get("enabled", True)
    restrictions = {"_default": {"enabled": False}}
    for ident in sorted(set(plugins) | set(unknown)):
        settings = policy.get(ident) or {}
        enabled = settings.get("enabled", default)
        if ident in unknown:
            restrictions[ident] = {"enabled": False}
        else:
            restrictions[ident] = {"enabled": enabled,
                                   "mcp_servers": {name: {"enabled": False} for name in plugins[ident]}}
    return {"plugins": restrictions}


def plugin_evidence(restrictions, plugins, unknown):
    policy = restrictions.get("plugins") or {}
    return {"apps_disabled": True,
            "publish_mcp_disabled": sorted(ident + "/" + name for ident, names in plugins.items()
                if policy.get(ident, {}).get("enabled", False) for name in names),
            "publish_mcp_reason": PLUGIN_MCP_REASON,
            "publish_plugins_unavailable": sorted(unknown)}


def plugin_context(evidence):
    disabled = evidence.get("publish_mcp_disabled") or []
    unknown = evidence.get("publish_plugins_unavailable") or []
    parts = []
    if evidence.get("apps_disabled"):
        parts.append("Connected ChatGPT apps (codex_apps) are disabled; native live web search stays available.")
    if disabled:
        parts.append("Plugin MCP servers disabled: " + ", ".join(disabled) + ". " + PLUGIN_MCP_REASON + "; their plugin skills stay enabled.")
    if unknown:
        parts.append("Plugins disabled because MCP metadata is unavailable: " + ", ".join(unknown) + ".")
    return " ".join(parts)


def verify_plugin_policy(config, expected):
    if config.get("features", {}).get("plugins") is False:
        return
    actual = config.get("plugins") or {}
    if not isinstance(expected, dict) or actual.get("_default", {}).get("enabled") is not False:
        fail("Plugin configuration did not preserve the frozen publishing policy")
    for ident, settings in actual.items():
        if ident != "_default" and settings.get("enabled", False) and ident not in expected:
            fail("An unverified plugin became enabled", plugin=ident)
    for ident, settings in expected.items():
        received = actual.get(ident) or {}
        if received.get("enabled", False) != settings.get("enabled", False):
            fail("Plugin enablement changed after publishing preflight", plugin=ident)
        for name, policy in (settings.get("mcp_servers") or {}).items():
            if (received.get("mcp_servers") or {}).get(name, {}).get("enabled", True) != policy.get("enabled", True):
                fail("Plugin MCP enablement changed after publishing preflight", plugin=ident, servers=[name])


def overlay(config, source=None):
    """Stdio MCP inherits a restricted env; explicitly carry the guard there too."""
    if source is None:
        from .client import clean_environment
        source = clean_environment()
    guarded_source = environment(source)
    policy = config.get("shell_environment_policy") or {}
    # Codex deep-merges these leaves with the user's set table. Never serialize
    # unrelated entries: they may be credentials and overrides enter argv/ledger.
    values = {name: guarded_source[name] for name in ("GIT_CONFIG_PARAMETERS", MARKER)}
    configured = policy.get("set") or {}
    require_parameters(configured.get("GIT_CONFIG_PARAMETERS", ""), "shell_environment_policy.set")
    if "PATH" in configured:
        path = configured["PATH"]
        values["PATH"] = path if HELPERS in path.split(os.pathsep) else path + (os.pathsep if path else "") + HELPERS
    shell = {"set": values}
    if policy.get("include_only"):
        shell["include_only"] = list(dict.fromkeys([*policy["include_only"], *values]))
    overrides = {"shell_environment_policy": shell, "features": {"apps": False}}
    for path, settings in stdio_servers(config):
        # A writer profile can enable a server that discovery reports disabled.
        # Prepare its leaves too, before that profile is applied.
        if not isinstance(settings, dict) or not settings.get("command"):
            continue
        inherited = {}
        for item in settings.get("env_vars", []):
            name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
            if not name or isinstance(item, dict) and item.get("source") not in (None, "local"):
                fail("Stdio MCP environment source cannot be proven", server="/".join(path))
            if name in source:
                inherited[name] = source[name]
        inherited.setdefault("PATH", source.get("PATH", os.defpath))
        # Stdio MCP's default inherited list excludes Git parameters. Only the
        # guard constant is safe to include in overrides and listener records.
        inherited.setdefault("GIT_CONFIG_PARAMETERS", guarded_source["GIT_CONFIG_PARAMETERS"])
        own = settings.get("env") or {}
        require_parameters(own.get("GIT_CONFIG_PARAMETERS", ""), "/".join(path) + "/env")
        if "GIT_CONFIG_PARAMETERS" in own and MARKER not in own:
            inherited.pop(MARKER, None)
        for name in ("PATH", "GIT_CONFIG_PARAMETERS", MARKER, "GIT_ALLOW_PROTOCOL"):
            if name in own:
                inherited[name] = own[name]
        if MARKER not in inherited and inherited.get("GIT_CONFIG_PARAMETERS") == guarded_source["GIT_CONFIG_PARAMETERS"]:
            inherited[MARKER] = guarded_source[MARKER]
        guarded = environment(inherited)
        target = overrides
        for key in path:
            target = target.setdefault(key, {})
        target["env"] = {k: guarded[k] for k in ("GIT_CONFIG_PARAMETERS", "PATH", MARKER)}
    return overrides


def verify(config, source=None, *, configuration=True):
    if source is None:
        from .client import clean_environment
        source = clean_environment()
    require_parameters(source.get("GIT_CONFIG_PARAMETERS", ""), "launch environment")
    policy = config.get("shell_environment_policy") or {}
    require_parameters((policy.get("set") or {}).get("GIT_CONFIG_PARAMETERS", ""), "shell_environment_policy.set")
    if configuration and (config.get("features") or {}).get("apps") is not False:
        fail("Connected ChatGPT apps must be disabled for Codex launches")
    if not present(shell_environment(config, source)):
        fail("Shell policy drops or replaces the Codex push guard; the lead (Claude) pushes")
    missing = []
    for path, settings in stdio_servers(config):
        if isinstance(settings, dict) and settings.get("command") and settings.get("enabled", True):
            require_parameters((settings.get("env") or {}).get("GIT_CONFIG_PARAMETERS", ""), "/".join(path) + "/env")
            if not present(settings.get("env") or {}):
                missing.append("/".join(path))
    if missing:
        fail("Stdio MCP environment omits the Codex push guard", servers=missing)
    helper = Path(HELPERS) / ("git-remote-" + NAME)
    if not helper.is_file() or not os.access(helper, os.X_OK):
        fail("Codex push-refusal helper is missing or not executable")


def service_variables(names):
    if any(name in ("PATH", MARKER) or name.startswith("GIT_CONFIG") for name in names):
        fail("Service environment cannot replace push-guard variables")
