#!/usr/bin/env python3
"""
Sync MCP servers from Claude to Antigravity.
Run this before starting Antigravity CLI to ensure all MCP servers are available.

Filters out known-broken servers (BROKEN_MCP_SERVERS) so the Antigravity CLI does
not waste cold-start time trying to connect to dead servers on every launch.
Also prunes them from any existing project .gemini/settings.json so stale
entries do not linger from earlier sync runs.
"""

import json
import sys
from pathlib import Path

# Must stay in sync with the same constant in server.py. Any server listed
# here is skipped when syncing Claude → Antigravity, and removed from any existing
# .gemini/settings.json we find.
BROKEN_MCP_SERVERS: frozenset[str] = frozenset({
    "MCP_DOCKER",    # docker mcp gateway — reported Failed to connect
    "portainer-dev", # host credentials unreachable
    "portainer-qa",  # host credentials unreachable
    "vercel",        # HTTP OAuth transport — not suitable for CLI passthrough
})


def get_claude_mcp_servers() -> dict:
    """Get healthy MCP servers from Claude's config.

    Filters out the antigravity server itself (recursion) and any server listed
    in BROKEN_MCP_SERVERS.
    """
    claude_config = Path.home() / ".claude.json"
    if not claude_config.exists():
        return {}

    with open(claude_config) as f:
        config = json.load(f)

    mcp_servers = config.get("mcpServers", {})
    return {
        k: v for k, v in mcp_servers.items()
        if k != "antigravity" and k not in BROKEN_MCP_SERVERS
    }


def sync_to_project(project_dir: Path) -> None:
    """Sync MCP servers to project's Antigravity settings.

    - Writes healthy servers from Claude's config into .gemini/settings.json
    - Removes any stale BROKEN_MCP_SERVERS entries that were written by
      earlier versions of this script
    """
    claude_mcps = get_claude_mcp_servers()

    gemini_dir = project_dir / ".gemini"
    settings_file = gemini_dir / "settings.json"

    if settings_file.exists():
        with open(settings_file) as f:
            settings = json.load(f)
    else:
        settings = {}

    existing = settings.get("mcpServers", {}) if isinstance(settings.get("mcpServers"), dict) else {}

    # Drop any existing entries that are now in the blocklist. This cleans
    # up leftovers from previous sync runs that did not filter.
    existing = {k: v for k, v in existing.items() if k not in BROKEN_MCP_SERVERS}

    # Upsert healthy servers from Claude config.
    for name, config in claude_mcps.items():
        command = config.get("command", "")
        args = config.get("args", [])
        env = config.get("env", {})

        if command:
            server_config: dict = {"command": command}
            if args:
                server_config["args"] = args
            if env:
                server_config["env"] = env
            existing[name] = server_config

    settings["mcpServers"] = existing

    # Only write if we have content or an existing file — avoid creating
    # empty .gemini directories in projects that never used them.
    if existing or settings_file.exists():
        gemini_dir.mkdir(exist_ok=True)
        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)


def main():
    # Get project directory from args or cwd
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1])
    else:
        project_dir = Path.cwd()

    if not project_dir.exists():
        print(f"Directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    sync_to_project(project_dir)


if __name__ == "__main__":
    main()
