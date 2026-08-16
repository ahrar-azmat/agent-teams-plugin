#!/usr/bin/env python3
"""PreToolUse nudge on advisory MCP calls — autostart the live view.

Background MCP tasks have no stdout stream, so the Shells panel's
Enter-to-view shows nothing for a backgrounded advisory call (host
limitation; see mcp-live). A hook process cannot own a Shells-panel row
either — only shells the AGENT launches get an openable buffer. So this
hook does the one thing hooks can do deterministically: every advisory
call injects an instruction telling the agent to start the mcp-live tail
as a background shell if it hasn't already. Result: the live view is up
by the time there is anything to watch, in every session that uses the
advisor, without the user asking.
"""
import json

CONTEXT = (
    "LIVE VIEW: an advisory MCP run is starting. If you have NOT already "
    "started the mcp-live background shell in this session, start it now "
    "(one per session) so the user can watch the run live via the Shells "
    "panel (Enter to view): Bash tool, run_in_background=true, command: "
    'tail -F "$HOME/.claude/logs/codex-oracle/stream.log" '
    "— description: \"mcp-live: live view of codex-oracle runs\". If it is "
    "already running, do nothing and do not mention it."
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": CONTEXT,
    }
}))
