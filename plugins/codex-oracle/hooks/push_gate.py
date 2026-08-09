#!/usr/bin/env python3
"""PreToolUse gate on Bash git push/commit — cross-platform port of the
former sh+jq+grep one-liner (exec form + python; also drops the jq
dependency on POSIX). Reads the hook payload from stdin; if the command is
a git push/commit and the session transcript does not show BOTH advisory
reviews, injects the push-gate context. Fail-open: a malformed payload or
unreadable transcript must never block the tool call outright."""
import json
import re
import sys
from pathlib import Path

GATE_CONTEXT = (
    "PUSH GATE: You are about to git push/commit without a complete "
    "multi-model review. You MUST run BOTH on the diff first, dispatched in "
    "PARALLEL (same message): Codex `code_review` (PRIMARY, authoritative) "
    "and Antigravity `antigravity_review_pr` (SECONDARY, corroborating).\n"
    "DISPATCH BLIND: send the DIFF and let them find the defects. Do NOT "
    'write "I fixed X by doing Y, confirm that is right" — that buys '
    "agreement, not review, and two models handed the same claim agree for "
    "reasons unrelated to the code. Keep context and focus factual; put any "
    "belief of your own in the caller_hypothesis parameter for an explicit "
    "CONFIRMED/REFUTED/UNPROVEN verdict.\n"
    "If the user explicitly said skip review, proceed. Otherwise STOP."
)

CODEX_PENDING_CONTEXT = (
    "PUSH GATE: Codex's review has NOT RETURNED yet — the transcript shows "
    "the call but no Codex result. Codex is the PRIMARY advisor and runs at "
    "max reasoning, so long calls are moved to the background and come back "
    "later as a task notification; that is normal, NOT a reason to proceed. "
    "Antigravity answering first is not the review being done — it is the "
    "SECONDARY advisor, corroboration only. Do NOT push/commit on it alone: "
    "WAIT for Codex (Monitor the task / wait for the notification) and do "
    "other work meanwhile, then fold its findings in. Push only once Codex "
    "has actually answered — or the user explicitly says skip review."
)

# A real Codex ANSWER always carries this header (see server.py `_run_codex`);
# the tool NAME appearing in the transcript only proves the call was made,
# which is still true when the call was backgrounded and never came back.
CODEX_RESULT_MARKERS = ("[Codex model:", "Codex TIMEOUT", "Codex health check FAILED")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    # ^-anchored per line, matching the original grep -E '^[[:space:]]*git[[:space:]]+(push|commit)'
    if not re.search(r"^\s*git\s+(push|commit)\b", command, re.MULTILINE):
        return 0
    transcript = data.get("transcript_path") or ""
    context = GATE_CONTEXT
    if transcript:
        try:
            text = Path(transcript).read_text(encoding="utf-8", errors="ignore")
            both_dispatched = "code_review" in text and "antigravity_review_pr" in text
            codex_answered = any(m in text for m in CODEX_RESULT_MARKERS)
            if both_dispatched and codex_answered:
                return 0  # complete review: primary answered, secondary too
            if both_dispatched:
                # The tools were called but the PRIMARY never came back — the
                # exact case where a fast Antigravity answer looks like a
                # finished review. Say so precisely instead of generically.
                context = CODEX_PENDING_CONTEXT
        except OSError:
            pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
