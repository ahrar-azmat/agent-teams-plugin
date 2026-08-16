#!/usr/bin/env python3
"""PreToolUse gate on Bash git push/commit. Reads the hook payload from
stdin; if the command is a git push/commit and the session transcript does
not show a Codex review COMPLETED FOR THE CURRENT TREE, returns
permissionDecision "ask" so the user explicitly approves the un-reviewed
operation — an additionalContext nudge cannot stop a tool call that is
already about to execute. Fail-open: a malformed payload or unreadable
transcript must never block the call.

Answer verification is a SIGNATURE, not shape-sniffing: server.py stamps
every answer header with `tool:<name> | status:<ok|timeout> | tree:<digest>`
(see `_answer_sig`). The gate opens only for a header carrying
`tool:code_review | status:ok` whose tree digest matches the workspace at
push time. Consequences, each pinned by tests/test_push_gate.py:
  - another tool's answer (codex_query etc.) carries its own tool name and
    never opens the gate, even if its text mentions code_review;
  - a TIMEOUT partial carries status:timeout and never opens the gate;
  - a review of an OLDER tree fails the digest compare — edits after the
    answer mean the review no longer vouches for this push (stale → ask);
  - reading this source file (or any file) into the transcript cannot forge
    a passing header for the current tree;
  - pre-signature answers (older server still running before /mcp
    Reconnect) fail verification, so the gate asks — the safe direction.

Command detection is deliberately over-broad (`git … push|commit` anywhere
in one shell segment): a false positive costs one extra permission prompt,
a false negative silently skips the gate.

TRUST MODEL — a guardrail against FORGETTING, not a security boundary.
This hook protects a cooperative agent from the accident of pushing
without a review, on a stale review, or on a failed/timed-out one. It
does NOT defend against a deliberately evasive agent: any Bash-text hook
is bypassable by construction (write a script and execute it, shell
functions, aliases), so a keyed signature would add ceremony, not
security — the header check is honesty-against-accident. Two disclosed
residuals: edits made WHILE the review runs are stamped into the
answer-time digest and are indistinguishable from reviewed state; and
whether the installed Claude Code enforces permissionDecision "ask" is
UNVERIFIED (anthropic/claude-code#81041 reports "ask" executing silently
on some versions) — the reason is therefore duplicated into
additionalContext, so the worst case degrades to the previous
context-nudge behavior, never below it.
"""
import hashlib
import json
import re
import subprocess
import sys

GATE_REASON = (
    "PUSH GATE: no completed Codex review found for the current tree. Run "
    "Codex `code_review` on the diff first — dispatched BLIND (send the "
    "DIFF, keep context/focus factual, put any belief of your own in "
    "caller_hypothesis for an explicit CONFIRMED/REFUTED/UNPROVEN verdict) "
    "— then push. Approve this prompt only to explicitly skip the review."
)

CODEX_PENDING_REASON = (
    "PUSH GATE: Codex's review was dispatched but has NOT RETURNED. Codex "
    "runs at max reasoning, so long calls are backgrounded and come back "
    "later as a task notification; that is normal, not a reason to proceed. "
    "Wait for the result and fold its findings in before pushing. "
    "Approve this prompt only to explicitly skip waiting for the review."
)

STALE_REASON = (
    "PUSH GATE: the completed Codex review is STALE — the workspace changed "
    "after Codex answered (tree digest mismatch), so that review no longer "
    "vouches for what is being pushed. Re-run `code_review` on the current "
    "diff. Approve this prompt only to explicitly push unreviewed changes."
)

# git followed by push/commit within one shell segment (no ; | & between) —
# catches `npm test && git push`, `git -C /repo push`, `FOO=1 git commit`.
GIT_PUSH_COMMIT_RE = re.compile(r"\bgit\b[^;|&\n]*\b(push|commit)\b")

# Repository-redirection forms: the digest binds the review to the hook's
# cwd, so a push aimed at ANOTHER repository must never be auto-opened by a
# matching cwd digest — those always ask.
GIT_OTHER_REPO_RE = re.compile(r"\bgit\b[^;|&\n]*\s(?:-C|--git-dir)\b|GIT_DIR=")

# The server-stamped answer signature (order pinned by server.py
# `_answer_sig`); only an ok-status code_review answer can open the gate.
ANSWER_SIG_RE = re.compile(
    r"\[Codex model: [^\]\n]*\| tool:code_review \| status:ok"
    r" \| tree:([0-9a-fA-F]{4,64}|nogit|unknown)\]"
)

_HEX12_RE = re.compile(r"[0-9a-f]{12}")


def _workspace_digest(cwd):
    """12-hex digest of the workspace state (HEAD + tracked diff + status).

    Behavioral twin of server.py `_workspace_digest` (hooks cannot import
    the server module — it needs the mcp package); parity is pinned by
    tests/test_push_gate.py.
    """
    try:
        parts = []
        for args in (("rev-parse", "HEAD"), ("diff", "HEAD"), ("status", "--porcelain")):
            proc = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0:
                # A digest over PARTIAL state is worse than no digest —
                # every command must succeed or the digest is void.
                return "nogit" if args[0] == "rev-parse" else "unknown"
            parts.append(proc.stdout)
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:12]
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _unwrap(text):
    """Undo known MCP result wrappers: {'result': '...'} or ['...']."""
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            obj = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(obj, dict) and isinstance(obj.get("result"), str):
            return obj["result"]
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, str) and "[Codex model:" in item:
                    return item
    return text


def _texts(block):
    """Every text payload inside a tool_result content field."""
    content = block.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                yield part
            elif isinstance(part, dict) and part.get("type") == "text":
                yield str(part.get("text") or "")


def _review_state(transcript_path):
    """Return (dispatched, answered_digests) from transcript evidence.

    dispatched: an assistant tool_use whose name is an MCP code_review tool
    (structural — file contents in the transcript cannot fabricate it).
    answered_digests: tree digests from verified answer signatures, found in
    tool_result payloads (foreground, possibly JSON-wrapped) or
    queue-operation entries (backgrounded task notifications).
    """
    dispatched = False
    answered_digests = set()
    for line in open(transcript_path, encoding="utf-8", errors="ignore"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        etype = entry.get("type")
        if etype == "queue-operation":
            for match in ANSWER_SIG_RE.finditer(str(entry.get("content") or "")):
                answered_digests.add(match.group(1).lower())
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if etype == "assistant" and block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                if name.startswith("mcp__") and name.endswith("__code_review"):
                    dispatched = True
            elif block.get("type") == "tool_result":
                for text in _texts(block):
                    for match in ANSWER_SIG_RE.finditer(_unwrap(text)):
                        answered_digests.add(match.group(1).lower())
    return dispatched, answered_digests


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not GIT_PUSH_COMMIT_RE.search(command):
        return 0
    transcript = data.get("transcript_path") or ""
    reason = GATE_REASON
    if transcript and not GIT_OTHER_REPO_RE.search(command):
        try:
            dispatched, answered_digests = _review_state(transcript)
            current = _workspace_digest(data.get("cwd") or ".")
            # Opening requires BOTH legs: the structural dispatch record
            # (a forged tool_result alone is not evidence a review ran) AND
            # a real digest match — if either side of the digest could not
            # be computed (nogit/unknown), the binding is meaningless and
            # the gate asks instead of trusting a vacuous match.
            if (
                dispatched
                and _HEX12_RE.fullmatch(current)
                and current in answered_digests
            ):
                return 0  # completed review vouches for exactly this tree
            if answered_digests:
                reason = STALE_REASON
            elif dispatched:
                reason = CODEX_PENDING_REASON
        except OSError:
            pass
    elif transcript:
        reason = (
            GATE_REASON
            + " (This push redirects to another repository via -C/--git-dir/"
            "GIT_DIR — the gate cannot bind a review to it, so it always asks.)"
        )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
            "additionalContext": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
