#!/usr/bin/env python3
"""E2E selftest for the progress heartbeat (the 2026-07-27 idle-abort fix).

Spawns server.py over stdio exactly as Claude Code does, calls codex_query
with a trivial prompt, and asserts that (1) progress notifications arrive
while codex runs and (2) the final answer comes back. Needs the codex CLI
installed and authenticated; costs one tiny codex call (~10s).

Run:  .venv/bin/python selftest_progress.py
"""

import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).parent


async def main() -> None:
    params = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "python"),
        args=[str(ROOT / "server.py")],
        # Tight heartbeat so even a ~10s codex answer yields several ticks.
        env={**os.environ, "CODEX_ORACLE_PROGRESS_INTERVAL": "2"},
    )
    progress_events: list[str] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        progress_events.append(message or "")
        print(f"  progress: {progress:.0f}/{total or 0:.0f} — {message}")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "codex_query",
                {"prompt": "Reply with exactly: SELFTEST-OK"},
                progress_callback=on_progress,
            )
            text = result.content[0].text  # type: ignore[union-attr]
            print("result head:", text[:120].replace("\n", " "))
            assert "SELFTEST-OK" in text, f"codex answer missing: {text[:300]}"
            assert progress_events, "NO progress notifications received — heartbeat broken"
            print(f"PASS: {len(progress_events)} progress notification(s) + clean result")


if __name__ == "__main__":
    asyncio.run(main())
