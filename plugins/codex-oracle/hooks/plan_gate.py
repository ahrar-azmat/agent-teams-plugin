#!/usr/bin/env python3
"""PreToolUse gate on EnterPlanMode — cross-platform (was a printf one-liner
that only a POSIX shell could run; hooks now use exec form + python)."""
import json

CONTEXT = (
    "PLANNING GATE: Before entering plan mode, ensure you have cross-model "
    "advisory context. If you have NOT yet consulted Codex architect_review "
    "in this session for the current task, call it NOW (it works in plan "
    "mode since plan mode supports all tools except Write/Edit). DISPATCH "
    "BLIND: give it the REQUIREMENT and CONSTRAINTS and ask it to solve it "
    "— do NOT paste the design you already picked and ask it to bless "
    "it, which returns agreement rather than architecture. Put any preferred "
    "design in the caller_hypothesis parameter so it is refuted instead of "
    "confirmed. Expect live web research with source URLs for prior art and "
    "version claims. If the advisory has already run, proceed into plan mode."
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": CONTEXT,
    }
}))
