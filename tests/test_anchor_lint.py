#!/usr/bin/env python3
"""Tests for the anchoring lint shipped in the codex-oracle MCP server.

Run:  python3 tests/test_anchor_lint.py        (no dependencies — mcp is stubbed)

Why this exists
---------------
The lint is the mechanical half of the Independence Protocol: it detects when a
caller smuggled a conclusion into a NEUTRAL scoping field (`context`, `focus`,
`concerns`, `topic`, `prompt`) and warns them that any agreement they get back
is contaminated. Two properties must hold, and both can silently rot:

1. SENSITIVITY — it must fire on real conclusion language. A lint that misses
   is worse than no lint, because the absence of a warning reads as "you
   dispatched blind" when you did not.
2. SPECIFICITY — it must NOT fire on ordinary factual background. False
   positives train callers to ignore the banner, which destroys the signal.
"""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _stub_mcp() -> None:
    """Provide just enough of the `mcp` package to import the server."""
    for name in (
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "mcp.server.stdio",
        "mcp.types",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))

    def _passthrough_decorator(*a, **k):
        def deco(fn):
            return fn

        return deco

    class _FastMCP:
        """Stub for the codex-oracle server's FastMCP usage."""

        def __init__(self, *a, **k):
            pass

        tool = staticmethod(_passthrough_decorator)

    sys.modules["mcp.server.fastmcp"].FastMCP = _FastMCP
    sys.modules["mcp.server.fastmcp"].Context = object
    sys.modules["mcp.server.stdio"].stdio_server = None
    sys.modules["mcp.types"].Tool = object
    sys.modules["mcp.types"].TextContent = object


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Conclusion language — the lint MUST fire on every one of these.
MUST_FLAG = [
    "The root cause is a missing await in the retry loop.",
    "I fixed this by adding a lock around the counter.",
    "Please confirm that my approach is thread-safe.",
    "Does this look right?",
    "This is safe because the caller already holds the lock.",
    "I think the issue is in the session factory.",
    "Can you verify that my migration is reversible?",
    "Obviously the index is missing.",
    "I'm pretty sure this is a transaction boundary problem.",
    "Clearly the retry logic is at fault.",
    "It's just a typo in the constant name.",
    "Sanity-check the approach for me.",
    "The right fix is to move the flush before the commit.",
    "Make sure this is correct before we ship.",
    "The behaviour is as expected now.",
    "The bug is that the cursor is never closed.",
    "I believe the deadlock comes from the ordering.",
    "Is this correct?",
    "It should be safe to drop the column now.",
]

# Legitimate factual scoping — the lint MUST NOT fire on any of these.
# Every false positive here would train callers to ignore the banner.
MUST_PASS = [
    "This module handles payment allocation; it runs under RLS.",
    "security, concurrency",
    "Which async job queue best fits a Python/Postgres stack?",
    "The endpoint intermittently returns stale rows under concurrent load.",
    "Runs on Python 3.13; the test suite is pytest with asyncio mode auto.",
    "correctness, performance, error handling",
    "We need durable retries across restarts, ~5k jobs/day.",
    "The service is deployed behind Traefik with TLS terminated at the edge.",
    "Tenant isolation is enforced by row-level security policies.",
    "This diff touches the ledger writer and its two callers.",
    "I have read the upstream changelog and the migration guide.",
    "Bills are grouped by collector and tax year.",
    "The failing test is tests/test_ledger.py::test_reversal.",
    "Money is stored in integer cents throughout.",
    "",
]


def main() -> int:
    _stub_mcp()
    servers = {
        "codex-oracle": _load("plugins/codex-oracle/server.py", "cx_srv"),
    }

    failures = 0
    for plugin, mod in servers.items():
        for text in MUST_FLAG:
            if not mod._detect_anchoring({"context": text}):
                print(f"✗ [{plugin}] MISSED (should flag): {text!r}")
                failures += 1
        for text in MUST_PASS:
            hits = mod._detect_anchoring({"context": text})
            if hits:
                print(f"✗ [{plugin}] FALSE POSITIVE: {text!r} -> {hits}")
                failures += 1

        # The hypothesis channel must never be linted — it exists to carry a
        # conclusion safely, and flagging it would defeat its purpose.
        if mod._detect_anchoring({}) != []:
            print(f"✗ [{plugin}] empty field set should produce no hits")
            failures += 1

        # A hit must produce BOTH the prompt-side neutralizer and the
        # caller-facing banner; either alone leaves the loop half-closed.
        hits = mod._detect_anchoring({"context": MUST_FLAG[0]})
        if not mod._neutralizer(hits) or not mod._anchor_warning_banner(hits):
            print(f"✗ [{plugin}] hit did not produce neutralizer + banner")
            failures += 1
        if mod._neutralizer([]) or mod._anchor_warning_banner([]):
            print(f"✗ [{plugin}] clean dispatch must produce no banner at all")
            failures += 1

        # An empty hypothesis must add nothing; a real one must be fenced and
        # must demand an explicit verdict.
        if mod._hypothesis_block(""):
            print(f"✗ [{plugin}] empty hypothesis should render nothing")
            failures += 1
        block = mod._hypothesis_block("the lock is held by the caller")
        if "<caller_hypothesis>" not in block or "REFUTED" not in block:
            print(f"✗ [{plugin}] hypothesis block missing fence or verdict demand")
            failures += 1

        # A curly apostrophe must not walk past the lint. This is a
        # one-character bypass of the entire mechanism if normalisation breaks.
        for variant in ("I’ve fixed the ordering", "Iʼve fixed the ordering"):
            if not mod._detect_anchoring({"context": variant}):
                print(f"✗ [{plugin}] curly-apostrophe bypass: {variant!r}")
                failures += 1

        # Verdict accountability: a hypothesis with no verdict in the answer
        # must produce a loud notice; silence must never read as agreement.
        if not mod._verdict_missing_notice("my theory", "sounds plausible to me"):
            print(f"✗ [{plugin}] missing verdict did not raise a notice")
            failures += 1
        if mod._verdict_missing_notice("my theory", "Hypothesis verdict: REFUTED — see l.12"):
            print(f"✗ [{plugin}] verdict present but notice still raised")
            failures += 1
        if mod._verdict_missing_notice("", "no verdict anywhere"):
            print(f"✗ [{plugin}] notice raised without a hypothesis")
            failures += 1
        # The reserved length must actually cover the notice it pays for.
        if mod._VERDICT_NOTICE_LEN < len(mod._verdict_missing_notice("x", "")):
            print(f"✗ [{plugin}] _VERDICT_NOTICE_LEN under-reserves")
            failures += 1

    for name, mod in servers.items():
        hunt = mod._CAPABILITY_HUNT
        if "present is not supported" not in hunt or "swappable" not in hunt.lower():
            print(f"✗ [{name}] _CAPABILITY_HUNT lost its core directive")
            failures += 1

    # 12 = the per-server validations outside the two case lists: empty-field,
    # neutralizer+banner pair, clean-no-banner, empty-hypothesis,
    # real-hypothesis, 2 curly-apostrophe variants, verdict-missing,
    # verdict-present, notice-without-hypothesis, notice-length, and the
    # capability-hunt core directive. Update when adding one.
    checked = len(servers) * (len(MUST_FLAG) + len(MUST_PASS) + 12)
    if failures:
        print(f"\n{failures} FAILURE(S) across {checked} checks")
        return 1
    print(f"✓ all {checked} checks passed "
          f"({len(MUST_FLAG)} flag / {len(MUST_PASS)} pass cases, codex-oracle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
