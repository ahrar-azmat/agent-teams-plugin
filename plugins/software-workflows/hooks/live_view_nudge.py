#!/usr/bin/env python3
"""The raw live view of Codex runs: one log file, which a person can follow in their own terminal.

Until 2.4.5 a PreToolUse hook here told Claude to start this tail as a background shell on every Codex call.
That cost context on each call, and the Codex Office now shows the same runs, so hooks/office_autostart.py
starts the office instead and nothing asks Claude for a tail. coordinator_lead.py still reads these names: a
lead's Bash running exactly this tail cannot touch a checkout, so it is not coordinated (no record, no scan,
no quiet window held).
"""

LIVE_VIEW_LOG = "$HOME/.claude/logs/codex-oracle/stream.log"
LIVE_VIEW_COMMAND = 'tail -F "' + LIVE_VIEW_LOG + '"'
