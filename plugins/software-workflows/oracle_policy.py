"""Publishing policy for the Oracle CLI, its shell and its MCP servers."""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time


class PolicyError(Exception):
    """A publishing restriction could not be established before inference."""


NAME = "sw-codex-no-push"
HELPERS = str(Path(__file__).resolve().parent / "hooks" / "codex-publish")
MARKER = "SOFTWARE_WORKFLOWS_PUSH_GUARD_PATH"
REFUSAL = "LEAD_PUBLISHES: the lead (Claude) pushes; Codex cannot publish commits."
INSTRUCTIONS = ("Publishing through Git, gh, the GitHub API or an MCP tool belongs to the lead (Claude). "
                "Leave changes uncommitted for the lead.")
PLUGIN_MCP_REASON = "Codex 0.163.0 cannot carry the push guard into plugin MCP servers"


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
    """Only the public guard value may be serialized into CLI overrides."""
    if value not in ("", PARAMETERS):
        raise PolicyError(f"Foreign GIT_CONFIG_PARAMETERS at {location}. Move those settings into a Git config file.")


def helper_path(path, previous=None):
    if path is None:
        return HELPERS
    entries = [entry for entry in path.split(os.pathsep) if entry != HELPERS and entry != previous]
    return os.pathsep.join([HELPERS, *entries])


def environment(source):
    """Apply the exact Git guard to a copy of the child environment."""
    require_parameters(source.get("GIT_CONFIG_PARAMETERS", ""), "launch environment")
    env = dict(source)
    if "GIT_ALLOW_PROTOCOL" in env and NAME not in env["GIT_ALLOW_PROTOCOL"].split(":"):
        raise PolicyError("Git protocol policy excludes the push-refusal helper")
    env["PATH"] = helper_path(env.get("PATH", os.defpath), env.get(MARKER))
    env["GIT_CONFIG_PARAMETERS"] = PARAMETERS
    env[MARKER] = HELPERS
    return env


def _helper_in(directory):
    # Git for Windows also runs extensionless shebang helpers. PATHEXT alone
    # does not describe that lookup.
    helper = "git-remote-" + NAME
    names = (helper + ".exe", helper) if os.name == "nt" else (helper,)
    for name in names:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def present(env):
    return (env.get("GIT_CONFIG_PARAMETERS") == PARAMETERS and env.get(MARKER) == HELPERS
            and env.get("PATH", "").split(os.pathsep)[0] == HELPERS
            and _helper_in(HELPERS) == os.path.join(HELPERS, "git-remote-" + NAME)
            and ("GIT_ALLOW_PROTOCOL" not in env or NAME in env["GIT_ALLOW_PROTOCOL"].split(":")))


def git_hook_notice(version):
    match = re.match(r"git version (\d+)\.(\d+)(?:\.(\d+))?", version)
    if match and tuple(map(int, match.group(1, 2))) >= (2, 54):
        return ""
    detail = (f"Git {match[0].split()[-1]} cannot run config hooks" if match
              else "Git config hook support could not be verified")
    return (f"{detail}. The Oracle guard needs Git 2.54 or newer to refuse pushes "
            "to a remote with an explicit pushurl.")


def trusted_git_paths(cwd):
    """Absolute Oracle PATH entries outside the reviewed tree, including symlink targets."""
    try:
        tree = Path(cwd).resolve()
        for parent in tree.parents:
            if (parent / ".git").exists():
                tree = parent
    except (OSError, RuntimeError):
        return {}
    paths = {}
    for entry in os.get_exec_path():
        path = Path(entry)
        if not path.is_absolute() or ".." in path.parts:
            continue
        try:
            executable = (path / ("git.exe" if os.name == "nt" else "git")).resolve()
            if any(part == tree or tree in part.parents for part in (path, path.resolve(), executable)):
                continue
            usable = executable.is_file() and os.access(executable, os.X_OK)
        except (OSError, RuntimeError):
            continue
        paths[os.path.normcase(os.path.normpath(entry))] = str(executable) if usable else None
    return paths


def host_environment(source, cwd):
    """The CLI's own Git lookups happen before its child shell policy applies."""
    trusted = trusted_git_paths(cwd)
    entries = [part for part in source["PATH"].split(os.pathsep)
               if part != HELPERS and os.path.normcase(os.path.normpath(part)) in trusted
               and Path(part).is_absolute() and ".." not in Path(part).parts]
    search = os.pathsep.join(entries)
    # The installed bundle only supplies a remote helper. A source checkout
    # can also contain a git beside it. Keep that directory off the host CLI
    # path in that case; overlay() still pins the bundled helper in child tools.
    if any(os.path.lexists(os.path.join(HELPERS, name)) for name in _GIT_NAMES):
        return {**source, "PATH": search or sys.executable}
    return {**source, "PATH": helper_path(search if entries else None)}


_GIT_NAMES = ("git.com", "git.exe", "git.bat", "git.cmd", "git") if os.name == "nt" else ("git",)


def git_probe_environment(trusted):
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    return {**env, "PATH": os.pathsep.join(trusted), "LC_ALL": "C"}


def _child_git(path, trusted):
    """A relative prefix can shadow every later entry. Never inspect it on the host."""
    for entry in path.split(os.pathsep):
        directory = Path(entry)
        if not directory.is_absolute() or ".." in directory.parts:
            return None
        for name in _GIT_NAMES:
            candidate = directory / name
            try:
                executable = candidate.is_file() and os.access(candidate, os.X_OK)
            except OSError:
                return None
            if executable:
                if os.name == "nt" and name != "git.exe":
                    return None
                return trusted.get(os.path.normcase(os.path.normpath(entry)))
    return None


def _probe_git(git, option, env, deadline, stop, started, group_options, sweep_group):
    # A kernel pipe bounds output even from a broken launcher. Reap the group
    # before reading it: a descendant may have inherited the write end.
    started.set()
    if stop.is_set() or time.monotonic() >= deadline:
        return ""
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb") as writer:
        with _metadata_process([git, option], os.path.dirname(git), env, group_options, sweep_group,
                               stdout=writer) as process:
            writer.close()
            while not stop.is_set():
                if os.name == "nt":
                    exited = process.poll() is not None  # the job still owns the descendants
                elif hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
                    exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
                else:
                    return ""
                if exited:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                stop.wait(min(0.01, remaining))
        if stop.is_set() or process.returncode != 0:
            return ""
        data = reader.read(4097)
        return os.fsdecode(data).strip() if len(data) <= 4096 else ""


async def git_info(git, option, env, deadline, group_options, sweep_group):
    remaining = min(3, deadline - asyncio.get_running_loop().time())
    if remaining <= 0:
        return ""
    stop, started = threading.Event(), threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_probe_git, git, option, env, time.monotonic() + remaining,
                                                  stop, started, group_options, sweep_group))
    try:
        async with asyncio.timeout(remaining):
            return await asyncio.shield(worker)
    except (TimeoutError, OSError, subprocess.SubprocessError):
        return ""
    finally:
        stop.set()
        if started.is_set():
            await asyncio.gather(worker, return_exceptions=True)
        else:
            worker.cancel()  # still queued; a racing worker checks stop before spawn


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
        raise PolicyError("Shell environment policy cannot be proven")
    inherit = policy.get("inherit") or "all"
    if inherit not in ("all", "core", "none"):
        raise PolicyError("Unknown shell environment inheritance")
    # Only the two required variables matter; core does not contain Git parameters.
    env = dict(source) if inherit == "all" else {k: v for k, v in source.items() if k == "PATH" and inherit == "core"}
    exclude, include = policy.get("exclude") or [], policy.get("include_only") or []
    if policy.get("filters") is not None:
        filters = policy["filters"]
        if exclude or include or not isinstance(filters, dict) or any(v not in ("include", "exclude") for v in filters.values()):
            raise PolicyError("Unknown shell environment filters")
        exclude = [k for k, v in filters.items() if v == "exclude"]
        include = [k for k, v in filters.items() if v == "include"]
    if any(not isinstance(p, str) for p in [*exclude, *include]):
        raise PolicyError("Malformed shell environment filters")
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
        raise PolicyError("Plugin configuration did not preserve the frozen publishing policy")
    for ident, settings in actual.items():
        if ident != "_default" and settings.get("enabled", False) and ident not in expected:
            raise PolicyError(f"An unverified plugin became enabled ({ident})")
    for ident, settings in expected.items():
        received = actual.get(ident) or {}
        if received.get("enabled", False) != settings.get("enabled", False):
            raise PolicyError(f"Plugin enablement changed after publishing preflight ({ident})")
        for name, policy in (settings.get("mcp_servers") or {}).items():
            if (received.get("mcp_servers") or {}).get(name, {}).get("enabled", True) != policy.get("enabled", True):
                raise PolicyError(f"Plugin MCP enablement changed after publishing preflight ({ident}/{name})")


def overlay(config, source):
    """Stdio MCP inherits a restricted env; explicitly carry the guard there too."""
    guarded_source = environment(source)
    policy = config.get("shell_environment_policy") or {}
    # Codex deep-merges these leaves with the user's set table. Never serialize
    # unrelated entries: they may be credentials and these overrides enter argv.
    values = {name: guarded_source[name] for name in ("GIT_CONFIG_PARAMETERS", MARKER)}
    configured = policy.get("set") or {}
    require_parameters(configured.get("GIT_CONFIG_PARAMETERS", ""), "shell_environment_policy.set")
    values["PATH"] = helper_path(shell_environment(config, guarded_source).get("PATH"), configured.get(MARKER))
    shell = {"set": values}
    if policy.get("include_only"):
        shell["include_only"] = list(dict.fromkeys([*policy["include_only"], *values]))
    overrides = {"shell_environment_policy": shell, "features": {"apps": False}}
    for path, settings in stdio_servers(config):
        if not isinstance(settings, dict) or not settings.get("command"):
            continue
        inherited = {}
        for item in settings.get("env_vars", []):
            name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
            if not name or isinstance(item, dict) and item.get("source") not in (None, "local"):
                raise PolicyError("Stdio MCP environment source cannot be proven for " + "/".join(path))
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


async def verify(config, source, cwd, deadline, group_options, sweep_group):
    require_parameters(source.get("GIT_CONFIG_PARAMETERS", ""), "launch environment")
    policy = config.get("shell_environment_policy") or {}
    require_parameters((policy.get("set") or {}).get("GIT_CONFIG_PARAMETERS", ""), "shell_environment_policy.set")
    if (config.get("features") or {}).get("apps") is not False:
        raise PolicyError("Connected ChatGPT apps must be disabled for Codex launches")
    shell = shell_environment(config, source)
    if not present(shell):
        raise PolicyError("Shell policy drops or replaces the Codex push guard; the lead (Claude) pushes")
    environments = [shell]
    for path, settings in stdio_servers(config):
        if isinstance(settings, dict) and settings.get("command") and settings.get("enabled", True):
            require_parameters((settings.get("env") or {}).get("GIT_CONFIG_PARAMETERS", ""), "/".join(path) + "/env")
            env = dict(settings.get("env") or {})
            for var in settings.get("env_vars", []):
                key = var if isinstance(var, str) else var.get("name")
                if key == "GIT_EXEC_PATH" and key in source:
                    env.setdefault(key, source[key])
            if not present(env):
                raise PolicyError("Stdio MCP environment omits the Codex push guard for " + "/".join(path))
            environments.append(env)
    # Git searches its own exec-path before PATH for remote helpers.
    trusted = trusted_git_paths(cwd)
    # Neither the checkout's cwd nor any of its proposed loader, shell or Git
    # environment settings may reach a host-side capability probe.
    host = git_probe_environment(trusted)
    inspected = {}
    helper = os.path.join(HELPERS, "git-remote-" + NAME)
    limits = []
    for env in environments:
        git = _child_git(env["PATH"], trusted)
        if not git:
            limits.append("The child's Git executable could not be verified from the Oracle's trusted PATH. "
                          "Push protection, including for an explicit pushurl, is unverified.")
            continue
        if git not in inspected:
            version = await git_info(git, "--version", host, deadline, group_options, sweep_group)
            default_path = await git_info(git, "--exec-path", host, deadline, group_options, sweep_group)
            inspected[git] = (git_hook_notice(version), default_path)
        limit, default_path = inspected[git]
        limits.append(limit)
        exec_path = env.get("GIT_EXEC_PATH", default_path)
        if not Path(exec_path).is_absolute():
            limits.append("Git's remote-helper search path could not be verified. "
                          "Push protection for ordinary remote URLs is unverified.")
            continue
        selected = _helper_in(exec_path)
        if selected and not os.path.samefile(selected, helper):
            raise PolicyError("Git would select a different push-refusal helper")
    return " ".join(dict.fromkeys(note for note in limits if note))


def merge(config, restrictions):
    """Merge effective config dictionaries without TOML serialization or mutation."""
    from copy import deepcopy
    value = deepcopy(config)
    for key, item in restrictions.items():
        value[key] = merge(value[key], item) if isinstance(item, dict) and isinstance(value.get(key), dict) else deepcopy(item)
    return value


def without_user_config(response):
    """Conservative parity check for exec --ignore-user-config (no app-server flag).

    config/read layers are highest precedence first. Project trust and managed
    configuration can depend on the omitted user layer; refuse that ambiguity
    rather than call a differently configured preflight proof.
    """
    layers = response.get("layers")
    if not isinstance(layers, list):
        raise PolicyError("Exec config layer inventory unavailable")
    result = {}
    for layer in reversed(layers):
        kind = layer.get("name", {}).get("type")
        if kind == "user":
            continue
        if kind not in ("sessionFlags", "system", "project"):
            raise PolicyError(f"Cannot establish --ignore-user-config parity for configuration layer {kind}")
        if kind == "project" and layer.get("config"):
            raise PolicyError("Cannot establish project trust parity with --ignore-user-config")
        if not layer.get("disabledReason"):
            result = merge(result, layer.get("config") or {})
    for key, origin in response.get("origins", {}).items():
        if key.startswith(("shell_environment_policy", "mcp_servers", "plugins")) and origin.get("name", {}).get("type") == "packagedDefaults":
            raise PolicyError("Packaged launch policy is not exposed in the exec layer inventory")
    return result


def toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(toml(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + "=" + toml(v) for k, v in value.items()) + "}"
    raise TypeError("Unsupported TOML override")


def override_args(config):
    return [arg for key, value in config.items() for arg in ("-c", key + "=" + toml(value))]


class MetadataError(PolicyError):
    """Safe diagnostics. Neither subprocess output nor RPC error text is user-facing."""

    def __init__(self, stage, category):
        self.stage = stage
        self.category = category
        self.status = None
        self.stderr_category = "other"

    def __str__(self):
        status = self.status if self.status is not None else "unavailable"
        return (f"Codex metadata {self.stage} failed (exit status {status}, {self.category})"
                f" [stderr category: {self.stderr_category}]")


def _stderr_category(stream, stopped):
    """Inspect at most 16 KiB across both ends. Only return a fixed label."""
    head = bytearray()
    tail = bytearray()
    with contextlib.ExitStack() as stack:
        stack.enter_context(stream)
        selector = None
        if os.name != "nt":
            selector = stack.enter_context(selectors.DefaultSelector())
            selector.register(stream, selectors.EVENT_READ)
        final_bytes = 0
        while True:
            # Closing the Windows job closes inherited pipe handles. On POSIX
            # even an unrelated holder of a duplicated fd must not delay return.
            finished = stopped.is_set()
            if selector is not None and not selector.select(0 if finished else 0.1):
                if finished:
                    break
                continue
            chunk = os.read(stream.fileno(), 8192)
            if not chunk:
                break
            head.extend(chunk[:8192 - len(head)])
            tail.extend(chunk)
            del tail[:-8192]
            if finished:
                final_bytes += len(chunk)
                if final_bytes >= 1 << 20:
                    break
    text = (head + b"\n" + tail).lower()
    for category, phrases in (
        ("read-only filesystem", (b"read-only file system", b"read-only filesystem", b"erofs")),
        ("permission denied", (b"permission denied", b"operation not permitted", b"access is denied", b"eacces")),
        ("not logged in", (b"not logged in", b"not authenticated", b"login required", b"please log in")),
    ):
        if any(phrase in text for phrase in phrases):
            return category
    return "other"


class MetadataClient:
    """Sequential, threadless stdio requests. Never start a Codex thread or an MCP server."""

    def __init__(self, process):
        self.process = process
        self.request_id = 0
        self.stage = "initialize"

    async def call(self, method, params):
        self.stage = method
        self.request_id += 1
        request = {"id": self.request_id, "method": method, "params": params}
        return await asyncio.to_thread(self._exchange, request)

    def _exchange(self, request):
        # The Popen leader is reaped only by _metadata, after the group sweep.
        # Closing a timed-out transport also unblocks this one pipe reader.
        stage = request["method"]
        try:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise MetadataError(stage, "closed input") from None
        if "id" not in request:
            return
        limit = 16 * 1024 * 1024
        while True:
            line = self.process.stdout.readline(limit + 1)
            if not line:
                raise MetadataError(stage, "closed output")
            if len(line) > limit:
                raise MetadataError(stage, "response too large")
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                raise MetadataError(stage, "invalid JSON") from None
            if not isinstance(message, dict):
                raise MetadataError(stage, "invalid response")
            if "id" in message and "method" in message:
                raise MetadataError(stage, "unexpected server request")
            if message.get("id") == request["id"]:
                if "error" in message:
                    raise MetadataError(stage, "RPC error")
                if not isinstance(message.get("result"), dict):
                    raise MetadataError(stage, "invalid response")
                return message["result"]

    async def config(self, cwd, ignore_user_config):
        response = await self.call("config/read", {"cwd": cwd, "includeLayers": ignore_user_config})
        if not ignore_user_config:
            return response["config"]
        requirements = (await self.call("configRequirements/read", {})).get("requirements") or {}
        if requirements.get("shell_environment_policy"):
            raise PolicyError("Cannot verify managed shell policy with --ignore-user-config")
        return without_user_config(response)


@contextlib.contextmanager
def _windows_metadata_job():
    """A job handle retains every descendant even after the CLI launcher exits."""
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    extended_limit_information = 9  # JobObjectExtendedLimitInformation
    if not api.SetInformationJobObject(job, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        api.CloseHandle(job)
        raise ctypes.WinError(error)

    def attach(process):
        if not api.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        process.stdin.write(b"+")
        process.stdin.flush()

    try:
        yield attach
    finally:
        try:
            if not api.TerminateJobObject(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            api.CloseHandle(job)


@contextlib.contextmanager
def _metadata_process(argv, cwd, env, group_options, sweep_group, *, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL):
    with contextlib.ExitStack() as family:
        attach = None
        if os.name == "nt":
            attach = family.enter_context(_windows_metadata_job())
            # The pipe handshake closes the spawn/assignment race. Until the
            # parent has assigned this interpreter to the job it cannot fork.
            bootstrap = ("import os, subprocess, sys\n"
                         "if os.read(0, 1) == b'+':\n"
                         " sys.exit(subprocess.call(sys.argv[1:], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr))")
            argv = [sys.executable, "-IBS", "-c", bootstrap, *argv]
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=stdout, stderr=stderr, **group_options)
        try:
            if attach:
                attach(process)
            yield process
        finally:
            # An unreaped POSIX leader reserves its group ID. On Windows the
            # job retains its members independently of parent PIDs.
            try:
                if attach:
                    family.close()
                    process.kill()  # also covers a bootstrap whose assignment failed
                    process.wait(timeout=5)
                elif not sweep_group(process):
                    raise OSError("metadata process group could not be reaped")
            finally:
                process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()


@contextlib.asynccontextmanager
async def _metadata(argv, cwd, env, config, group_options, sweep_group, deadline):
    process = None
    stage = "launch"
    failure = None
    # Drain concurrently so a full stderr pipe cannot stall an RPC. The
    # dedicated reader never competes with RPC workers in the default executor.
    read_fd, write_fd = os.pipe()
    category = ["other"]
    stopped = threading.Event()
    def drain():
        with contextlib.suppress(OSError):
            category[0] = _stderr_category(os.fdopen(read_fd, "rb"), stopped)
    reader = threading.Thread(target=drain, name="oracle-metadata-stderr")
    try:
        reader.start()
    except RuntimeError:
        os.close(read_fd)
        os.close(write_fd)
        raise MetadataError("launch", "could not capture diagnostics") from None
    try:
        args = [*argv, "app-server", "--listen", "stdio://", *override_args(config)]
        with _metadata_process(args, cwd, env, group_options, sweep_group, stderr=write_fd) as process:
            client = MetadataClient(process)
            try:
                async with asyncio.timeout_at(deadline):
                    await client.call("initialize", {"clientInfo": {"name": "software-workflows-oracle", "version": "2.5.0"},
                                                     "capabilities": {"experimentalApi": False}})
                    client.stage = "initialized"
                    await asyncio.to_thread(client._exchange, {"method": "initialized"})
                    yield client
            finally:
                stage = client.stage
    except MetadataError as exc:
        failure = exc
        raise
    except TimeoutError:
        failure = MetadataError(stage, "deadline exceeded")
        raise failure from None
    except subprocess.TimeoutExpired:
        failure = MetadataError(stage, "process cleanup timed out")
        raise failure from None
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        failure = MetadataError(stage, "could not start process" if process is None else "invalid metadata or transport")
        raise failure from None
    finally:
        os.close(write_fd)
        stopped.set()
        await asyncio.to_thread(reader.join)
        if failure is not None:
            failure.status = process.returncode if process is not None else None
            failure.stderr_category = category[0]


_PLUGIN_NAMES = {}
_IDENTITY_FIELDS = ("id", "remotePluginId", "version", "localVersion", "installedAt", "source", "enabled")


def _declaration_bytes(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("linked declaration")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("non-file declaration")
        data = stream.read((1 << 20) + 1)
    if len(data) > 1 << 20:
        raise ValueError("large declaration")
    return data


def _local_declarations(env, plugin, market):
    """Fingerprint both copies for the standard layout. Rediscover other layouts by RPC."""
    parts = (market.get("name"), plugin.get("name"), plugin.get("localVersion"))
    if not all(isinstance(part, str) and part not in ("", ".", "..") and
               re.fullmatch(r"[\w.@+-]+", part) for part in parts):
        return None
    home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
    source = Path(plugin["source"].get("path") or "")
    installed = home.joinpath("plugins", "cache", *parts)
    hashes = []
    # A manifest can point at arbitrary MCP configuration files. Only cache
    # the conventional .mcp.json layout when the manifest uses descriptive fields.
    descriptive = {"name", "version", "description", "author", "license", "homepage",
                   "repository", "keywords", "interface", "skills"}
    try:
        for root in (source, installed):
            if not root.is_absolute():
                return None
            manifest = _declaration_bytes(root / ".codex-plugin" / "plugin.json")
            info = json.loads(manifest) if manifest is not None else None
            if not isinstance(info, dict) or set(info) - descriptive:
                return None
            if (root / ".claude-plugin" / "plugin.json").exists():
                return None
            config = _declaration_bytes(root / ".mcp.json")
            if config is not None:
                declarations = json.loads(config)
                if (not isinstance(declarations, dict) or set(declarations) != {"mcpServers"}
                        or not isinstance(declarations["mcpServers"], dict)):
                    return None
            hashes.append((hashlib.sha256(manifest).hexdigest(),
                           hashlib.sha256(config).hexdigest() if config is not None else None))
    except (OSError, ValueError, RecursionError):
        return None
    return hashes


def _plugin_identity(argv, env, plugin, market):
    source = plugin.get("source") or {}
    if not isinstance(source, dict):
        return None
    if source.get("type") == "local":
        declarations = _local_declarations(env, plugin, market)
        if declarations is None:
            return None
    elif source.get("type") == "remote":
        declarations = None
    else:
        return None
    binaries = []
    for arg in argv:
        absolute = arg if os.path.isabs(arg) else shutil.which(arg, path=env.get("PATH"))
        if not absolute or not os.path.isabs(absolute):
            continue
        path = Path(absolute)
        if path.is_file():
            info = path.stat()
            binaries.append([str(path.resolve()), info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns])
    if not binaries:
        return None
    identity = {"binary": binaries, "argv": argv, "plugin": {key: plugin.get(key) for key in _IDENTITY_FIELDS},
                "marketplace": market.get("path") or market.get("name"), "declarations": declarations, "schema": 2}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _valid_names(value):
    return isinstance(value, list) and all(isinstance(name, str) and name for name in value)


async def _plugin_names(client, market, plugin, argv, env):
    key = await asyncio.to_thread(_plugin_identity, argv, env, plugin, market)
    if key is not None and key in _PLUGIN_NAMES:
        return _PLUGIN_NAMES[key]
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(data) / "codex-plugin-catalog" / (key + ".json") if data and key else None
    if path:
        with contextlib.suppress(OSError, ValueError, AttributeError):
            cached = json.loads(path.read_text())
            if cached.get("identity") == key and _valid_names(cached.get("names")):
                _PLUGIN_NAMES[key] = tuple(cached["names"])
                return _PLUGIN_NAMES[key]
    params = {"pluginName": plugin["name"]}
    params["marketplacePath" if market.get("path") else "remoteMarketplaceName"] = market.get("path") or market["name"]
    detail = (await client.call("plugin/read", params)).get("plugin")
    names = detail.get("mcpServers") if isinstance(detail, dict) else None
    if not _valid_names(names):
        return None
    names = tuple(sorted(set(names)))
    if key is None:
        return names
    if plugin["source"]["type"] == "local":
        if key != await asyncio.to_thread(_plugin_identity, argv, env, plugin, market):
            return None  # the package changed while its names were being read
    _PLUGIN_NAMES[key] = names
    if path:
        pending = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix="pending-", delete=False) as stream:
                pending = stream.name
                json.dump({"identity": key, "names": _PLUGIN_NAMES[key]}, stream)
            os.replace(pending, path)
        except OSError:
            pass
        finally:
            if pending:
                with contextlib.suppress(OSError):
                    os.unlink(pending)
    return _PLUGIN_NAMES[key]


async def _plugin_inventory(client, config, cwd, argv, env):
    if config.get("features", {}).get("plugins") is False:
        return {}, set()
    policy = config.get("plugins") or {}
    declared = {name for name in policy if name != "_default"}
    plugins, unknown = {}, set()
    try:
        listed = await client.call("plugin/installed", {"cwds": [cwd]})
    except MetadataError as error:
        if error.category != "RPC error" or policy.get("_default", {}).get("enabled", True):
            raise
        return {}, declared
    for market in listed["marketplaces"]:
        for plugin in market["plugins"]:
            if not plugin["installed"]:
                continue
            ident = plugin["id"]
            if not policy.get(ident, {}).get("enabled", policy.get("_default", {}).get("enabled", True)):
                plugins[ident] = ()
                continue
            try:
                names = await _plugin_names(client, market, plugin, argv, env)
            except MetadataError as error:
                if error.category != "RPC error":
                    raise
                names = None
            if names is None:
                unknown.add(ident)
            else:
                plugins[ident] = names
    return plugins, unknown | (declared - plugins.keys())


async def preflight(argv, cwd, env, config, ignore_user_config, group_options, sweep_group, timeout, launch_env=None):
    """Discover policy, read it back with our overrides, and pass those same leaves to exec."""
    base = {**config, "features": {"apps": False}}
    deadline = asyncio.get_running_loop().time() + timeout
    if launch_env is None:
        launch_env = host_environment(env, cwd)
    async with _metadata(argv, cwd, launch_env, base, group_options, sweep_group, deadline) as client:
        effective = await client.config(cwd, ignore_user_config)
        plugins, unknown = await _plugin_inventory(client, effective, cwd, argv, launch_env)
        restrictions = merge(overlay(effective, env), plugin_restrictions(effective, plugins, unknown))
    async with _metadata(argv, cwd, launch_env, merge(base, restrictions), group_options, sweep_group, deadline) as client:
        checked = await client.config(cwd, ignore_user_config)
        verify_plugin_policy(checked, restrictions.get("plugins"))
    limit = await verify(checked, env, cwd, deadline, group_options, sweep_group)
    disabled = sorted(f"{ident}/{name}" for ident, names in plugins.items()
                      if restrictions.get("plugins", {}).get(ident, {}).get("enabled") for name in names)
    context = plugin_context({"apps_disabled": True, "publish_mcp_disabled": disabled,
                              "publish_plugins_unavailable": sorted(unknown)})
    return restrictions, " ".join(part for part in (context, limit) if part), limit
