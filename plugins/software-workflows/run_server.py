#!/usr/bin/env python3
"""Cross-platform MCP server launcher with venv bootstrap (Windows + POSIX).

Replaces run-server.sh, which was POSIX-only twice over: Windows cannot exec
a .sh file at all, and a Windows venv keeps its interpreter at
.venv/Scripts/python.exe, not .venv/bin/python.

Claude Code materializes plugins into a versioned CACHE copy
(~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/) and resolves
${CLAUDE_PLUGIN_ROOT} there — but .venv is gitignored, so it never exists in
a fresh copy. This launcher makes any copy self-sufficient.

stdout is the MCP JSON-RPC channel — ALL bootstrap output goes to stderr.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = ROOT.name
IS_WINDOWS = os.name == "nt"


def _venv_python(venv: Path) -> Path:
    if IS_WINDOWS:
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _requirements_hash() -> str:
    import hashlib

    try:
        req = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    except OSError:
        return ""
    # The interpreter is part of the venv's identity: a venv built by an
    # older/other python is stale even when requirements did not change.
    return f"{req}:py{sys.version_info.major}.{sys.version_info.minor}:{sys.platform}"


def _venv_current(venv: Path, expected: str) -> bool:
    """A venv is usable only if it exists AND was installed from the same
    requirements.txt — otherwise a dependency change in the plugin would
    silently keep running against the old environment forever."""
    if not _venv_python(venv).exists():
        return False
    try:
        return (venv / ".requirements.sha256").read_text().strip() == expected
    except OSError:
        return False


def _err(msg: str) -> None:
    print(f"[{NAME}] {msg}", file=sys.stderr, flush=True)


def _run(python: Path) -> int:
    argv = [str(python), str(ROOT / "server.py")]
    if IS_WINDOWS:
        # No true exec() on Windows — run the server as a child. stdio handles
        # are inherited, so the JSON-RPC channel passes straight through, and
        # the server exits on stdin EOF when the host closes the pipe.
        return subprocess.run(argv).returncode
    os.execv(str(python), argv)
    return 0  # unreachable


def main() -> int:
    # server.py needs 3.11+ (tomllib); the venv inherits this interpreter.
    if sys.version_info < (3, 11):
        _err(
            "bootstrap failed: python >= 3.11 required, launcher ran under "
            f"{sys.version.split()[0]}"
        )
        return 1

    expected = _requirements_hash()

    # Fast path: this machine's maintained marketplace checkout already carries
    # a working venv — reuse its interpreter (deps only; the code that runs is
    # still THIS copy's server.py). Only when its installed dependencies match
    # THIS copy's requirements.txt: a stale sibling venv must not mask a
    # dependency change shipped with the plugin.
    mk = Path.home() / ".claude" / "plugins" / "marketplaces" / "agent-teams" / "plugins" / NAME
    if ROOT != mk and _venv_current(mk / ".venv", expected):
        return _run(_venv_python(mk / ".venv"))

    venv = ROOT / ".venv"
    py = _venv_python(venv)
    if not _venv_current(venv, expected):
        _err(f"(re)installing venv deps at {venv} with {sys.executable}")
        cmds = []
        if not py.exists():
            cmds.append([sys.executable, "-m", "venv", str(venv)])
        # --upgrade so a re-install actually moves within the declared
        # constraint range instead of keeping whatever satisfied it years ago.
        cmds.append([str(py), "-m", "pip", "install", "--quiet", "--upgrade",
                     "-r", str(ROOT / "requirements.txt")])
        for cmd in cmds:
            proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
            if proc.returncode != 0:
                _err(f"bootstrap failed: {' '.join(cmd)} -> exit {proc.returncode}")
                return 1
        try:
            marker = venv / ".requirements.sha256"
            tmp = marker.with_suffix(".tmp")
            tmp.write_text(expected)
            tmp.replace(marker)  # atomic: never a torn marker
        except OSError as exc:
            _err(f"could not record requirements hash: {exc}")
    return _run(py)


if __name__ == "__main__":
    sys.exit(main())
