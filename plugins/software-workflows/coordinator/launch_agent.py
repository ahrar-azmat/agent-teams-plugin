"""Opt-in per-user owner restart. Generating a plist does not install it."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from . import runtime
from .client import CoordinatorError, daemon_python, validate_state, clean_environment


SETTINGS = ("WRITER_MCP_SERVERS", "WRITER_PARK_IDLE_SECONDS", "MEMORY_WARN_RATIO", "MEMORY_CRITICAL_RATIO",
            "MEMORY_HYSTERESIS", "OWNER_JOURNAL_BYTES")


def settings(root):
    """Persist only resolved non-secret policy, never a launcher's environment."""
    path = root / "launch-settings.json"
    allowed = {"SOFTWARE_WORKFLOWS_" + name for name in SETTINGS}
    saved = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(saved, dict) or any(k not in allowed or not isinstance(v, str) for k, v in saved.items()):
        raise CoordinatorError("LAUNCH_SETTINGS", "Retained launch settings are invalid")
    values = dict(saved, **{k: v for k, v in clean_environment(codex=False).items() if k in allowed})
    if values != saved or not path.exists():
        descriptor, pending = tempfile.mkstemp(prefix="launch-settings-", dir=root)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(values, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
            runtime.sync_directory(root)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    return values


def label_for(root):
    return "com.software-workflows.owner." + hashlib.sha256(os.fsencode(root)).hexdigest()[:12]


def specification(root, label=None, python=None):
    root = Path(root).absolute()
    label = label or label_for(root)
    directory = runtime.runtime_root()
    address = directory / ("owner-" + hashlib.sha256(os.fsencode(root)).hexdigest()[:12] + ".sock")
    runtime.check_length(address)
    return {"Label": label, "ProgramArguments": [python or daemon_python(), "-B", "-m", "coordinator",
            "daemon", "serve", "--state", str(root)],
            "WorkingDirectory": str(Path(__file__).resolve().parents[1]),
            "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "SOFTWARE_WORKFLOWS_RUNTIME": str(directory), "SOFTWARE_WORKFLOWS_LAUNCH_AGENT": "1", **settings(root)},
            "RunAtLoad": True, "ThrottleInterval": 5,
            "KeepAlive": {"PathState": {str(root / "autostart-inhibit"): False}},
            "Sockets": {"Custodian": {"SockFamily": "Unix", "SockType": "stream", "SockPassive": True,
                                      "SockPathName": str(address), "SockPathMode": 384}}}


def manage(root, action, consent=False):
    if sys.platform != "darwin":
        raise CoordinatorError("LAUNCH_AGENT_PLATFORM", "LaunchAgent supervision requires macOS")
    root = validate_state(root, create=True)
    label = label_for(root)
    path = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    if not consent:
        question = ("Install an owner LaunchAgent that restarts crash cleanup at login and after a crash" if action == "install"
                    else "Remove the owner LaunchAgent and return to cleanup on the next client start")
        if not sys.stdin.isatty() or input(question + "? Type yes: ").strip().lower() != "yes":
            raise CoordinatorError("CONSENT_REQUIRED", "LaunchAgent changes require explicit consent", {"path": str(path)})
    domain = "gui/" + str(os.getuid())
    if action == "install":
        if path.exists():
            raise CoordinatorError("LAUNCH_AGENT_EXISTS", "Owner LaunchAgent already exists", {"path": str(path)})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(plistlib.dumps(specification(root, label)))
        result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        if result.returncode:
            path.unlink()
            raise CoordinatorError("LAUNCH_AGENT_BOOTSTRAP", "launchctl refused the owner agent", {"exit": result.returncode}, True)
    elif action == "uninstall":
        if not path.exists() or plistlib.loads(path.read_bytes()).get("Label") != label:
            raise CoordinatorError("LAUNCH_AGENT_IDENTITY", "No matching installed owner plist")
        result = subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True)
        if result.returncode:
            raise CoordinatorError("LAUNCH_AGENT_BOOTOUT", "launchctl did not remove the owner agent", {"exit": result.returncode}, True)
        path.unlink()
    else:
        raise ValueError("Unknown LaunchAgent action")
    return {"label": label, "action": action, "path": str(path)}
