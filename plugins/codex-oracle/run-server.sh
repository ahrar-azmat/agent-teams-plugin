#!/bin/sh
# MCP server launcher with venv bootstrap.
#
# Claude Code materializes plugins into a versioned CACHE copy
# (~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/) and resolves
# ${CLAUDE_PLUGIN_ROOT} there — but .venv is gitignored, so it never exists
# in a fresh copy and the old ".venv/bin/python" command ENOENT'd on every
# version bump (observed for 1.0.1 / 1.1.0 / 1.2.1). This launcher makes any
# copy self-sufficient.
#
# stdout is the MCP JSON-RPC channel — ALL bootstrap output goes to stderr.
set -u
NAME="codex-oracle"
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Fast path: this machine's maintained marketplace checkout already carries
# a working venv — reuse its interpreter (deps only; the code that runs is
# still THIS copy's server.py).
MK="$HOME/.claude/plugins/marketplaces/agent-teams/plugins/$NAME"
if [ "$ROOT" != "$MK" ] && [ -x "$MK/.venv/bin/python" ]; then
    exec "$MK/.venv/bin/python" "$ROOT/server.py"
fi

VENV="$ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    PY=""
    for c in python3.14 python3.13 python3.12 python3.11 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PY="$c"; break
        fi
    done
    if [ -z "$PY" ]; then
        echo "[$NAME] bootstrap failed: no python >= 3.11 on PATH (3.11+ needed for tomllib)" >&2
        exit 1
    fi
    echo "[$NAME] bootstrapping venv at $VENV with $PY" >&2
    "$PY" -m venv "$VENV" 1>&2 || exit 1
    "$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt" 1>&2 || exit 1
fi
exec "$VENV/bin/python" "$ROOT/server.py"
