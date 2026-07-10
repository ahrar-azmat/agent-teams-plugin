#!/bin/bash
# Antigravity wrapper that auto-syncs MCP servers from Claude before starting

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync-mcp.py"

# Sync MCP servers silently
if [ -f "$SYNC_SCRIPT" ]; then
    python3 "$SYNC_SCRIPT" "$(pwd)" 2>/dev/null
fi

# Run the actual Antigravity CLI (agy) with all arguments
exec agy "$@"
