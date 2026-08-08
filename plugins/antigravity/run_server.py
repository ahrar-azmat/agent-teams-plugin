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
    # Match codex-oracle's floor so both launchers behave identically.
    if sys.version_info < (3, 11):
        _err(
            "bootstrap failed: python >= 3.11 required, launcher ran under "
            f"{sys.version.split()[0]}"
        )
        return 1

    # Fast path: this machine's maintained marketplace checkout already carries
    # a working venv — reuse its interpreter (deps only; the code that runs is
    # still THIS copy's server.py).
    mk = Path.home() / ".claude" / "plugins" / "marketplaces" / "agent-teams" / "plugins" / NAME
    if ROOT != mk:
        mk_python = _venv_python(mk / ".venv")
        if mk_python.exists():
            return _run(mk_python)

    venv = ROOT / ".venv"
    py = _venv_python(venv)
    if not py.exists():
        _err(f"bootstrapping venv at {venv} with {sys.executable}")
        for cmd in (
            [sys.executable, "-m", "venv", str(venv)],
            [str(py), "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")],
        ):
            proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
            if proc.returncode != 0:
                _err(f"bootstrap failed: {' '.join(cmd)} -> exit {proc.returncode}")
                return 1
    return _run(py)


if __name__ == "__main__":
    sys.exit(main())
