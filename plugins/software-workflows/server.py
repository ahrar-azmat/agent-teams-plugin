"""
Codex Oracle MCP Server
========================
Exposes OpenAI Codex CLI as MCP tools for Claude Code.
Codex runs in headless mode (codex exec) with maximum reasoning power.

Auto-detects the model from ~/.codex/config.toml; reasoning effort is
PINNED to "max" (the desktop-app slider rewrites the config file and had
silently downgraded the oracle to xhigh). All tools use deep analysis with
extended timeouts.

Roles:
- Senior Architect: architecture & design review
- Code Reviewer: critical code analysis
- Research Analyst: deep research with LIVE web access
- General Oracle: freeform queries to Codex

All outputs are treated as authoritative second opinions that should be
critically verified — not blindly followed.

Two properties are enforced by this server rather than left to the caller
(see the INDEPENDENCE / WEB RESEARCH section below):

1. INDEPENDENCE. The value of a second opinion is that it was formed
   independently. A caller who states their own diagnosis and asks Codex to
   react to it has not bought a second opinion — they have bought an echo.
   Every prompt this server builds instructs Codex to reach its own
   conclusion from primary evidence BEFORE weighing anything the caller
   asserted, treats caller framing as a claim under test, and flags callers
   whose "context" smuggled in a conclusion.

2. LIVE WEB RESEARCH. Codex defaults to ``web_search = "cached"`` (an
   OpenAI-maintained snapshot index). Every invocation here forces
   ``web_search = "live"`` so version numbers, APIs, CVEs and best-practice
   claims are checked against the real web instead of recalled from
   training data.
"""

import asyncio
import contextlib
import functools
import itertools
import hashlib
import json
import math
import os
import random
import threading
import re
import secrets
import shutil
import signal
import stat as stat_mod
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any, TextIO

from mcp.server.fastmcp import Context, FastMCP

# Conversation-facing output cap. MCP results STAY IN CONTEXT for the rest
# of the caller's session (measured 2026-08-08: advisory MCP results = 26%
# of a session's usage in the Claude Code usage panel). The FULL answer is
# always persisted to the per-run .result.txt + live log, so the cap only
# bounds what enters the caller's context; the truncation notice keeps the
# live-log pointer so the rest is one Read away. ~60K chars ≈ 15K tokens ≈
# 2× a typical long advisory answer. Env-adjustable, never a code edit.
def _env_int(name: str, default: int, minimum: int) -> int:
    """Parse an int env override, clamped to a sane minimum. A malformed or
    too-small value must NOT crash the server at import (a non-int used to
    raise ValueError before the module finished loading) — fall back instead."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


MAX_OUTPUT_CHARS = _env_int("CODEX_ORACLE_MAX_OUTPUT_CHARS", 60000, 2000)

# ---------------------------------------------------------------------------
# Subprocess I/O limits
# ---------------------------------------------------------------------------
# asyncio.create_subprocess_exec() creates StreamReader instances for
# stdout/stderr with a default buffer limit of 64 KiB (2^16). The codex
# CLI can produce lines exceeding this (JSON events, long responses,
# session headers), causing LimitOverrunError: "Separator is not found,
# and chunk exceed the limit". Set a 4 MiB limit as a safety net.
SUBPROCESS_BUFFER_LIMIT = 4 * 1024 * 1024  # 4 MiB

# Read chunk size for consuming subprocess output. Using read(chunk_size)
# instead of readline() avoids LimitOverrunError entirely — read() never
# searches for a separator so it cannot overflow regardless of line length.
READ_CHUNK_SIZE = 65_536  # 64 KiB per read

def _env_seconds(name: str, default: float, minimum: float) -> float:
    """Env-tunable seconds knob, validated: unparsable, non-finite, or
    below-minimum values fall back to the default — a NaN/inf here would
    disarm the heartbeat cutoff entirely, and a zero/negative interval
    would busy-spin the loop."""
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < minimum:
        return default
    return value


# Progress heartbeat interval. Claude Code aborts any MCP tool call that
# produces no response AND no progress notification for 30 minutes
# ("idle timeout 1800s") — long max-effort runs and laptop-sleep gaps both
# crossed it (2026-07-27: a review was killed at 5929s idle after a ~94-min
# lid-close; the 60-min MAX_RUNTIME budget was unreachable through MCP
# without progress). Heartbeats reset the client's idle timer and surface
# liveness (elapsed time + output bytes). Env knob exists for the selftest.
PROGRESS_INTERVAL_SECONDS = _env_seconds(
    "CODEX_ORACLE_PROGRESS_INTERVAL", 10.0, 1.0
)

# STOP heartbeating BEFORE the client backgrounds the call — two measured
# incidents, one per side of the boundary:
#   2026-08-09 (macOS): Claude Code moves an MCP call to a background task at
#   ~120s and DEREGISTERS that request's progress token. Our heartbeat kept
#   sending on it every 10s; each came back as "Connection error: Received a
#   progress notification for an unknown token", and after enough of them the
#   client KILLED THE SERVER ("SIGINT failed, sending SIGTERM to MCP server
#   process"), taking every SIBLING in-flight run down with it.
#   2026-08-21 (Windows): the first fix stopped at 150s — the WRONG SIDE of
#   the ~120s boundary, guaranteeing 2-3 dead-token sends per backgrounded
#   call. macOS tolerated those few; on Windows the client's reaction WEDGED
#   the stdio channel instead: completed results were never delivered (the
#   background task read "running" forever) and NEW tool calls never reached
#   dispatch — one bug wearing two costumes.
# Heartbeats only ever existed to stop the client's 30-min idle-abort while it
# WAITS on the call; once the call is backgrounded the client no longer waits.
# So stop while the token is still ALIVE: default 100s — with the 10s
# interval the last send lands at ≤100s, a ≥20s margin under the ~120s
# deregistration, and no send ever targets a dead token. The live log keeps
# streaming regardless, so nothing observable is lost. Set 0 to disable
# heartbeats entirely (the first tick exits before any send).
PROGRESS_MAX_SECONDS = _env_seconds(
    "CODEX_ORACLE_PROGRESS_MAX_SECONDS", 100.0, 0.0
)

# Combined-geometry invariant: a send can begin at ≤MAX and take up to one
# INTERVAL (its wait_for bound), and the whole envelope must clear the
# client's ~120s token deregistration. An unsafe env combination is CLAMPED,
# not honored — these knobs tune cadence, never the safety boundary
# (round-2 review, 2026-08-21: MAX=1000 would have restored dead-token
# sends wholesale).
_SAFE_PROGRESS_ENVELOPE = 115.0
if PROGRESS_MAX_SECONDS + PROGRESS_INTERVAL_SECONDS > _SAFE_PROGRESS_ENVELOPE:
    PROGRESS_MAX_SECONDS = max(
        0.0, _SAFE_PROGRESS_ENVELOPE - PROGRESS_INTERVAL_SECONDS
    )

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
# Startup probe: if codex produces ZERO output within this window, the
# process is stuck before it even reached the model (broken stdin, auth, etc.)
STARTUP_PROBE_SECONDS = 90

# Wall-clock timeout: maximum total runtime for a single codex invocation.
# RUN BUDGET — a MANAGED limit (Limits Doctrine), not a scoping cap.
# Named threat: a run that never finishes (model loop, hung tool, stalled
# transport) burning provider credits and holding the worktree write lock,
# its claims and the caller's wait forever. Sized from MEASURED workloads
# (SmartPay trains, 2026-09-02..04): healthy runs finish in 4–30 min; three
# legitimate analysis-heavy runs were SIGKILLed at 62–66 min while still
# working (exit -9, the old 60-min literal). Default 3 h = ≥2.7× the longest
# observed legitimate need, and under the chained ceilings: the plugin's MCP
# call timeout (.mcp.json, 4 h) and the host's own idle abort (~4 h
# observed) — both of which a DETACHED run survives anyway. Adjustable
# without a deploy: CODEX_ORACLE_MAX_RUNTIME_S (seconds). Out-of-band values
# (< 300 s would kill every real run; > 12600 s would outlive the plugin's
# 4 h MCP call timeout with no margin) are
# REJECTED loudly — default + a startup warning on stderr — never clamped.
# Observable: the live log warns at 80 % of the budget, `codex_runs` prints
# the effective budget, and a deadline kill journals status "timeout" with
# the budget and the knob in its message.
MAX_RUNTIME_DEFAULT_S = 3 * 3600
MAX_RUNTIME_MIN_S = 300
# CHAINED CEILING (round 32): the plugin's MCP call timeout (.mcp.json) is a
# HARD 4 h per-call wall clock the client enforces; the band's maximum sits
# 30 min under it so no allowed budget can outlive the call.
MAX_RUNTIME_MAX_S = 12600
# ONE budget per MCP REQUEST (round 32): retries and abraham's two phases
# share it — an attempt that cannot get this much of it is refused.
MIN_ATTEMPT_SECONDS = 120


def _max_runtime_from_env() -> tuple[int, str]:
    """(budget seconds, source): source is "default", "env", or a rejection
    note naming the bad value — a rejection KEEPS the default (loud, never
    clamped)."""
    raw = os.environ.get("CODEX_ORACLE_MAX_RUNTIME_S")
    if raw is None or raw.strip() == "":
        return MAX_RUNTIME_DEFAULT_S, "default"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return MAX_RUNTIME_DEFAULT_S, f"rejected CODEX_ORACLE_MAX_RUNTIME_S={raw!r} (not a number)"
    if not math.isfinite(value) or not (MAX_RUNTIME_MIN_S <= value <= MAX_RUNTIME_MAX_S):
        return MAX_RUNTIME_DEFAULT_S, (
            f"rejected CODEX_ORACLE_MAX_RUNTIME_S={raw!r} "
            f"(allowed {MAX_RUNTIME_MIN_S}..{MAX_RUNTIME_MAX_S} s)")
    return int(value), "env"


MAX_RUNTIME_SECONDS, MAX_RUNTIME_SOURCE = _max_runtime_from_env()
if MAX_RUNTIME_SOURCE.startswith("rejected"):
    sys.stderr.write(f"[codex-oracle] {MAX_RUNTIME_SOURCE}; using the default "
                     f"{MAX_RUNTIME_DEFAULT_S}s run budget\n")

# ---------------------------------------------------------------------------
# Write mode (abraham): auto-compaction
# ---------------------------------------------------------------------------
# Implementation runs are LONG. codex only auto-compacts its own history at
# 90% of the context window by default (0.147.0-generation source:
# resolved_context_window * 9 / 10 in openai_models.rs; the registry carries
# no per-model override for the gpt-5.6 / gpt-6 families), which leaves the tail of a
# long write run degraded. Write runs therefore pass an explicit
# -c model_auto_compact_token_limit at AUTOCOMPACT_PCT of the window.
#
# Two measured traps shape this code:
# 1. How a configured limit combines with the 90% default depends on the
#    binary. On 0.163.0, under the default model_auto_compact_token_limit_scope
#    `total`, it is CLAMPED: min(config, window * 9 / 10)
#    (protocol/src/openai_models.rs:529-540, fed by
#    models-manager/src/model_info.rs:29-31), so it can only lower the
#    threshold; under `body_after_prefix` it is used as-is
#    (core/src/session/context_window.rs:68-79). On the 0.147-generation
#    binaries it won outright (config.model_auto_compact_token_limit.or_else
#    (model default), no min()), so a limit above the real window NEVER
#    fired, silently disabling compaction. The flag is therefore only passed
#    when the window is KNOWN.
# 2. The window must come from the deployed binary's OWN registry
#    (models_cache.json under CODEX_HOME — the same base its 90% default
#    derives from), never from recalled docs: gpt-5.6-sol is 272_000 there
#    (measured 2026-08-14) while API-era docs suggest 400_000. A guessed 400k
#    base would have put "65%" at 95.6% of the real window — LATER than the
#    default it replaced. Same shape again for gpt-6-astra (measured
#    2026-09-05, codex-cli 0.153.4 registry): 272_000 in models_cache.json
#    while the API model page says 1,050,000 — and 272K is also the input
#    size above which the API bills the long-context premium, so 65% of the
#    registry window keeps write runs under both ceilings.
# Precedence: CODEX_ORACLE_CONTEXT_WINDOW env (explicit operator override)
# > models_cache.json exact-slug lookup > omit the flag (vendor default
# governs). The chosen branch is recorded in the live-log header.
AUTOCOMPACT_PCT = min(85, _env_int("CODEX_ORACLE_AUTOCOMPACT_PCT", 65, 30))


def _model_context_window(model: str) -> tuple[int, str]:
    """(window_tokens, source) per the DEPLOYED binary's own registry cache;
    (0, reason) when unknown. Never guesses a number."""
    override = _env_int("CODEX_ORACLE_CONTEXT_WINDOW", 0, 0)
    if override:
        return override, "env CODEX_ORACLE_CONTEXT_WINDOW"
    cache = (
        Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        / "models_cache.json"
    )
    try:
        if cache.stat().st_size > 20 * 1024 * 1024:
            return 0, "models_cache.json implausibly large — refused"
        data = json.loads(cache.read_text(encoding="utf-8"))
        for entry in data.get("models") or []:
            if isinstance(entry, dict) and entry.get("slug") == model:
                # Mirrors ModelInfo::resolved_context_window(): prefer
                # context_window, fall back to max_context_window.
                window = int(
                    entry.get("context_window")
                    or entry.get("max_context_window")
                    or 0
                )
                if window > 0:
                    return window, "models_cache.json"
                break
        return 0, f"model {model!r} not in models_cache.json"
    except (OSError, ValueError, TypeError):
        return 0, "models_cache.json unreadable"


def _auto_compact_limit(model: str) -> tuple[int | None, str]:
    """Explicit auto-compaction threshold for write runs, or (None, why) when
    the flag must be omitted so the vendor's own 90% default governs."""
    window, source = _model_context_window(model)
    if not window:
        return None, f"window unknown ({source}); vendor default (90%) governs"
    return (window * AUTOCOMPACT_PCT) // 100, (
        f"{AUTOCOMPACT_PCT}% of {window:,} ({source})"
    )


mcp = FastMCP(
    "codex-oracle",
    instructions=(
        "Codex Oracle is the SOLE cross-model advisor: OpenAI's latest Codex "
        "model at MAXIMUM reasoning effort, with repository access and LIVE web "
        "search. It is the deepest and most rigorous advisor available — use it "
        "for review, architecture, research, web research, and synthesis. Its "
        "verdict is the one that governs.\n\n"
        "NEVER CONCLUDE OR ACT WITHOUT CODEX. Codex runs at max effort and can "
        "take many minutes; long calls are moved to the background and return "
        "later as a task notification. That is NORMAL — WAIT for it. Do not "
        "ship, commit, or declare a decision while Codex's answer is pending. "
        "If Codex has not answered yet, the review is NOT complete — "
        "block on its result (Monitor / wait for the notification) and keep "
        "working on something else meanwhile.\n\n"
        "WHEN YOU AND CODEX DISAGREE, Codex carries — unless you can DISPROVE "
        "it by measuring the deployed system. (Measurement beats the model: "
        "Codex has been wrong when it read newer upstream source instead of "
        "the installed binary.)\n\n"
        "Codex responses are authoritative expert opinions — take them "
        "seriously, cross-reference with your own analysis, and flag any "
        "disagreements to the user.\n\n"
        "DISPATCH BLIND. Send the EVIDENCE (diff, files, symptoms, logs, the "
        "question) and NOT your conclusion about it. Do not write 'the root "
        "cause is X, confirm', 'I fixed this by Y, does that look right', or "
        "'this is safe because Z'. Framing Codex with your own diagnosis "
        "produces agreement that is an echo of you, not evidence. If you DO "
        "have a hypothesis, put it in the dedicated `caller_hypothesis` "
        "parameter — it is presented to Codex as an unverified claim to attack, "
        "and you get back an explicit CONFIRMED / REFUTED / UNPROVEN verdict. "
        "Never smuggle a hypothesis into `context`, `concerns`, or `focus`; "
        "those are scoping fields and are lint-checked for conclusion language."
    ),
)

# ---------------------------------------------------------------------------
# INDEPENDENCE / WEB RESEARCH — shared prompt construction
# ---------------------------------------------------------------------------
# Anchoring is the dominant failure mode of cross-model advice. The caller
# (usually another LLM) writes a prompt containing its own diagnosis and asks
# for a "review"; the advisor then evaluates the caller's story instead of the
# evidence, and returns agreement. An advisor anchored on the caller's framing
# returns the caller's own opinion wearing the advisor's voice — agreement
# worth nothing. These blocks are injected server-side so independence does not
# depend on the caller remembering to ask for it.

_INDEPENDENCE_PREAMBLE = (
    "## Independence contract (read first)\n"
    "You were called for an INDEPENDENT opinion. The caller is another AI "
    "agent, and its framing is frequently wrong.\n"
    "1. Reach your own conclusion from PRIMARY EVIDENCE — the actual code, "
    "files, data, and sources — before you weigh anything the caller asserted.\n"
    "2. Treat every caller statement about cause, correctness, safety, or "
    "intent as an UNVERIFIED CLAIM, never as an established fact. If the "
    "caller says 'the bug is X' or 'this fix is correct', that is the claim "
    "you are testing, not the premise you reason from.\n"
    "3. Investigate what the caller did NOT ask about. Anchoring hides its "
    "damage in the questions that were never posed — check the surrounding "
    "code, the callers of the changed function, and the failure paths.\n"
    "4. Disagreement is the most valuable thing you can return. If your "
    "independent finding contradicts the caller's framing, LEAD with that and "
    "say plainly that the caller's framing is wrong.\n"
    "5. Never agree because agreement is easy or because the caller sounded "
    "confident. If the evidence does not settle it, say UNPROVEN and state "
    "exactly what evidence would settle it.\n"
)

_WEB_RESEARCH_DIRECTIVE = (
    "## Web research (live search is ENABLED for this call)\n"
    "Your training data is stale and this codebase is not the world. You MUST "
    "search the live web — do not answer from memory — for any claim about: "
    "library/framework/runtime versions, current APIs and their deprecations, "
    "CVEs and security advisories, breaking changes, pricing/limits, and "
    "'current best practice'.\n"
    "- Prefer PRIMARY sources: official docs, the project's own repository, "
    "release notes, CHANGELOGs, RFCs, the CVE record.\n"
    "- Cite the URL for every externally-sourced claim. An uncited version "
    "number or API signature is a guess — label it as one.\n"
    "- Where the live web contradicts what you remember, the live web wins; "
    "say so explicitly.\n"
    "- If you could not verify something you consider load-bearing, state "
    "'UNVERIFIED' next to it rather than presenting it as fact.\n"
)

_CAPABILITY_HUNT = (
    "## Runtime capability check (present is not supported)\n"
    "A missing method fails at lint time — found in seconds. A "
    "PRESENT-BUT-UNSUPPORTED method fails in production, on a real customer's "
    "data. Type stubs, autocomplete, `hasattr`, and a clean import describe the "
    "UNION of every backend a library supports — not the one this code actually "
    "runs on. Hunt for it explicitly:\n"
    "- For every call crossing into a SWAPPABLE backend — browser engine, DB "
    "driver/dialect, storage/LLM/queue provider, cloud SDK against a "
    "compatible-but-not-identical endpoint, container-provided binary, mobile "
    "platform/native module, any vendor SDK whose implementation is "
    "configurable — ask: WHICH backend implements this, IN THE CONFIGURATION "
    "IT RUNS IN, and is that what is deployed? Check the vendor's "
    "compatibility matrix, not the type signature. Say so when the diff does "
    "not let you tell which engine or mode is configured.\n"
    "- CONFIGURATION gates capabilities independently of the vendor, and this "
    "is the half reviewers miss: headful/headless, pooled/direct connection, "
    "sync/async driver, persistent/ephemeral context, edge/regional runtime, "
    "free/paid tier, emulator vs real device. A real case: swapping to the "
    "'correct' browser engine still left `page.pdf()` dead, because it is "
    "headless-only and the browser launches headful — right engine, wrong "
    "mode. A capability comment that records a version but not the MODE is a "
    "union claim, not evidence.\n"
    "- Engine/driver/provider/version/MODE SWAPS are where this bug is born. "
    "If the diff changes one, treat every call into that surface as suspect — "
    "AND re-audit every error-string classifier that parses that vendor's "
    "messages: a swap can keep the match while inverting its meaning.\n"
    "- When a wrapper refuses, ask whether the UNDERLYING protocol refuses too "
    "— the gate is often a check in the wrapper's own driver, not a limit of "
    "the engine. Flag it as a hypothesis needing a probe, never as a fact.\n"
    "- A probe that has never returned a red is not a probe: capability checks "
    "must be calibrated against a known-bad configuration before their greens "
    "mean anything.\n"
    "- A `try`/`except` (or `catch`) around such a call that degrades to a "
    "no-op, a default, or a skipped write is the worst form: it converts a "
    "loud failure into silent data loss. Flag it unless the handler "
    "DISTINGUISHES the capability miss from a genuine failure and RECORDS "
    "which occurred.\n"
    "- Same defect in other clothes: a parameter accepted then ignored, "
    "clamped, or silently downgraded; a config key that parses but no longer "
    "exists in the installed version; an instruction with no mechanism behind "
    "it. Accepted-but-ignored is worse than rejected.\n"
    "- A check that passes on the library's DEFAULT backend proves nothing "
    "about the deployed one. Flag a capability nobody has seen work on the "
    "real engine, in the real mode; what settles it is one real run there "
    "and its observed result, not more test cases.\n"
)

# Conclusion language in a scoping field means the caller anchored the advisor.
# Detected and reported LOUDLY back to the caller — never silently stripped,
# never blocked: silent mutation of a caller's prompt is its own defect, and a
# hard block would break legitimate round-2 adversarial dispatch.
# Apostrophe variants normalised to ASCII before matching (curly quotes are
# what an LLM usually emits, and they silently defeat every "I've"-style rule).
_APOSTROPHES = {ord(c): "'" for c in "\u2019\u02bc\uff07\u2018\u00b4"}

_ANCHOR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:the |a )?root cause is\b", "asserts the root cause"),
    (r"\bthe (?:bug|issue|problem|defect) is\b", "asserts the defect"),
    (r"\bI (?:think|believe|suspect|reckon)\b", "states the caller's belief"),
    (r"\bI(?:'ve| have)? (?:fixed|solved|resolved|corrected)\b", "asserts a fix"),
    (r"\bI (?:fixed|changed|refactored) (?:this|it|the)\b", "asserts a fix"),
    (r"\b(?:please )?confirm (?:that|this|my|the)\b", "requests confirmation"),
    (r"\b(?:can you )?verify (?:that )?my\b", "requests confirmation"),
    (r"\bdoes (?:this|that) look (?:right|good|correct|ok)\b", "requests approval"),
    (r"\bis (?:this|that) (?:correct|right|safe|fine|ok)\b", "requests approval"),
    (r"\bmake sure (?:this|that|it) (?:is|looks)\b", "requests approval"),
    (r"\bshould be (?:safe|fine|correct|ok)\b", "pre-judges the answer"),
    (r"\bobviously\b", "pre-judges the answer"),
    (r"\bas expected\b", "pre-judges the answer"),
    (r"\bthe (?:correct|right|best) (?:approach|fix|solution) is\b", "asserts the answer"),
    (r"\bthis is (?:safe|correct|fine) because\b", "asserts the answer"),
    (r"\b(?:I'm|I am) (?:pretty |fairly |quite )?(?:sure|certain)\b", "states the caller's belief"),
    (r"\bclearly (?:the|a|an|this|it)\b", "pre-judges the answer"),
    (r"\bjust (?:a|an) (?:typo|oversight|nit)\b", "pre-judges severity"),
    (r"\bnothing (?:else )?(?:to worry about|wrong)\b", "pre-judges the answer"),
    (r"\bsanity[- ]check\b", "requests approval"),
    (r"\bmy (?:diagnosis|read|take|theory|conclusion) is\b", "states the caller's belief"),
    (r"\broot cause\s*[::]", "asserts the root cause"),
    (r"\bthe (?:bug|issue|problem|defect)\s*[::]", "asserts the defect"),
    (r"\blooks (?:fine|correct|right|good) to me\b", "pre-judges the answer"),
)


def _detect_anchoring(fields: dict[str, str]) -> list[str]:
    """Return human-readable anchoring hits found in caller scoping fields.

    ``fields`` maps parameter name -> caller-supplied text. Only the NEUTRAL
    scoping parameters are linted; ``caller_hypothesis`` is exempt by design,
    since that parameter exists precisely to carry a conclusion safely.
    """
    hits: list[str] = []
    for param, text in fields.items():
        if not text:
            continue
        # Normalise the apostrophe variants a model actually emits. Without
        # this, "I’ve fixed" (curly quote) walks straight past the
        # "I've fixed" pattern — a one-character bypass of the whole lint.
        probe = text.translate(_APOSTROPHES)
        for pattern, why in _ANCHOR_PATTERNS:
            m = re.search(pattern, probe, re.IGNORECASE)
            if m:
                hits.append(f"`{param}`: \"{m.group(0)}\" ({why})")
                break  # one hit per field is enough to make the point
    return hits


def _neutralizer(hits: list[str]) -> str:
    """Extra counter-anchoring text injected when the caller anchored."""
    if not hits:
        return ""
    return (
        "\n## ⚠️ Caller anchoring detected\n"
        "The caller's scoping text contains conclusion language: "
        + "; ".join(hits)
        + ".\n"
        "Discount it. Those statements are the caller's guesses, and this "
        "server flags them because callers routinely state a wrong diagnosis "
        "as fact. Derive your findings from the primary evidence alone, then "
        "state explicitly whether the caller's implied conclusion survives.\n"
    )


def _hypothesis_block(hypothesis: str) -> str:
    """Render the caller's hypothesis as a claim under test, never as fact."""
    if not hypothesis:
        return ""
    return (
        "\n## Caller's hypothesis — UNVERIFIED CLAIM UNDER TEST\n"
        "The following is what the caller BELIEVES. It is not evidence, it is "
        "not background, and it may be entirely wrong. Do not adopt its "
        "vocabulary or its framing. Form your own findings FIRST, then "
        "adversarially test this claim — actively try to REFUTE it:\n"
        f"<caller_hypothesis>\n{hypothesis}\n</caller_hypothesis>\n"
        "Required in your output — a dedicated line:\n"
        "**Hypothesis verdict**: CONFIRMED / REFUTED / UNPROVEN — the specific "
        "evidence (file:line, source URL, observed behaviour) that decided it. "
        "If CONFIRMED, name the evidence that would have refuted it and say "
        "why it is absent. 'It sounds plausible' is not a verdict.\n"
    )


def _anchor_warning_banner(hits: list[str]) -> str:
    """Loud, visible notice prepended to the RESULT the caller receives."""
    if not hits:
        return ""
    return (
        "⚠️ ANCHORING WARNING — read before trusting this answer\n"
        "Your scoping fields contained conclusion language: "
        + "; ".join(hits)
        + ".\n"
        "Codex was instructed to discount it and reason from primary evidence, "
        "but you biased the sample. If this answer AGREES with you, treat that "
        "agreement as WEAK evidence — it may be an echo of your own framing, "
        "not independent corroboration. Disagreement below is still strong "
        "evidence.\n"
        "Fix: send only the evidence and the question; put any diagnosis in the "
        "`caller_hypothesis` parameter, which is presented as a claim to refute "
        "and returns an explicit CONFIRMED/REFUTED/UNPROVEN verdict.\n"
        + "─" * 72
        + "\n\n"
    )

# Reserve for the "[TRUNCATED: ...]" line that is appended AFTER slicing, so a
# single oversized result cannot push the final payload past MAX_OUTPUT_CHARS.
_TRUNC_NOTICE_ALLOWANCE = 128

_VERDICT_TOKENS = ("CONFIRMED", "REFUTED", "UNPROVEN")


def _verdict_missing_notice(hypothesis: str, result: str) -> str:
    """Loud notice when a hypothesis was sent but no verdict came back.

    The CONFIRMED/REFUTED/UNPROVEN requirement is a PROMPT instruction; this
    server cannot force the model to honour it. When it is ignored, say so —
    a caller who reads silence as confirmation has been misled by the very
    mechanism meant to protect them.
    """
    if not hypothesis:
        return ""
    upper = result.upper()
    if any(token in upper for token in _VERDICT_TOKENS):
        return ""
    return (
        "\u26a0\ufe0f NO HYPOTHESIS VERDICT RETURNED\n"
        "You supplied `caller_hypothesis`, but the answer below contains no "
        "CONFIRMED / REFUTED / UNPROVEN verdict. That verdict is a prompt "
        "requirement this server cannot enforce on the model. Do NOT read the "
        "silence as confirmation — your hypothesis was not adjudicated. "
        "Re-ask with the hypothesis alone if you need it settled.\n"
        + "\u2500" * 72
        + "\n\n"
    )


# Fixed-length (no interpolation), so its cost can be reserved up front.
_VERDICT_NOTICE_LEN = len(_verdict_missing_notice("x", ""))


# ---------------------------------------------------------------------------
# Config reader — proper TOML parsing with mtime-based caching
# ---------------------------------------------------------------------------

_config_cache: dict[str, str] | None = None
_config_mtime: float = 0.0


def _read_codex_config() -> dict[str, str]:
    """Parse key settings from ~/.codex/config.toml.

    Uses tomllib for correct TOML parsing (the old hand-rolled parser
    stopped at the first [section] and missed keys in some layouts).
    Results are cached and only re-read when the file's mtime changes.
    """
    global _config_cache, _config_mtime

    config_path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if not config_path.exists():
        _config_cache = {}
        return _config_cache

    try:
        mtime = (str(config_path), config_path.stat().st_mtime_ns)
    except OSError:
        _config_cache = {}
        return _config_cache

    if _config_cache is not None and mtime == _config_mtime:
        return _config_cache

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        # Extract top-level scalar values (model, reasoning effort, etc.)
        _config_cache = {k: str(v) for k, v in raw.items() if isinstance(v, (str, int, float, bool))}
    except (tomllib.TOMLDecodeError, OSError):
        _config_cache = {}

    _config_mtime = mtime
    return _config_cache


def _get_codex_model() -> str:
    """Auto-detect the latest model from Codex config."""
    # Fallback = the vendor's own bundled default (codex-cli 0.153.4 made
    # GPT-6 Astra the bundled default, 2026-09-04); the pin normally comes
    # from ~/.codex/config.toml, re-read on every mtime change.
    return _read_codex_config().get("model", "gpt-6-astra")


def _get_reasoning_effort() -> str:
    """Reasoning effort — PINNED to max, never read from config.

    The Codex desktop app slider rewrites model_reasoning_effort in
    ~/.codex/config.toml (observed drift: max -> xhigh), so trusting the
    file silently downgrades the oracle. "ultra" stays deliberately
    avoided: it delegates to auto-picked subagents we can't control.
    """
    return "max"


def _get_cwd() -> str:
    """Get the working directory — prefer CLAUDE_CWD if set."""
    return os.environ.get("CLAUDE_CWD", os.getcwd())


def _workspace_digest(cwd: str) -> str:
    """12-hex CONTENT digest of the worktree — ONE implementation,
    treedigest.py (loaded by path, shared with hooks/push_gate.py), computed
    here in a child under a HARD deadline (round 31: no in-process deadline
    can interrupt a blocking read); it reads bytes itself (no `git diff` /
    `git status`, round 34: those run configured helper commands) and
    ignores HEAD. Stamped into answer headers by ``_answer_sig`` and
    recomputed by the push-gate hook at push time: a mismatch means the
    CONTENT changed after the answer. "unknown" on any budget breach or failure.
    """
    try:
        return _treedigest.digest_hard(cwd)
    except Exception:
        return "unknown"

def _answer_sig(tool_name: str, status: str, tree: str | None = None) -> str:
    """Machine-verifiable answer signature, appended inside the result header.

    hooks/push_gate.py opens the push gate only for a header carrying
    ``tool:code_review | status:ok`` and a ``tree:`` digest matching the
    workspace at push time — so a TIMEOUT partial, another tool's answer, or
    a review of an older tree can never satisfy the gate.
    """
    if not tool_name:
        return ""
    # ``tree`` = the digest taken at DISPATCH (journaled with the run): the
    # answer vouches for the tree the model actually read, not for whatever
    # the workspace looks like when the answer is delivered (review of
    # 1.17.0: adoption stamped the collection-time tree).
    return f" | tool:{tool_name} | status:{status} | tree:{tree or _workspace_digest(_get_cwd())}"


# The write argv (user rulings 2026-09-05 and 2026-10-18): codex's --yolo
# alias, verbatim from `codex exec --help` on 0.163.0 ("Skip all confirmation
# prompts and execute commands without sandboxing"). Every abraham write runs
# this way since 2.0.0 ("let us remove the option wherein they can keep full
# access off"): the sealed workspace-write mode, its write-capability probe and
# the probe's cache are gone. The user config is KEPT (as in infra mode) so the
# writer has the MCP tools a live investigation has. What the run really got
# is read back from its rollout afterwards (_effective_modes).
FULL_ACCESS_WRITE_ARGS = ("--dangerously-bypass-approvals-and-sandbox",)


def _sealed_env_refusal() -> str:
    """A refusal when CODEX_ORACLE_WRITE_FULL_ACCESS asks for the removed
    sealed mode; empty when it is unset, empty or true. A false value is
    refused, never silently ignored."""
    raw = os.environ.get("CODEX_ORACLE_WRITE_FULL_ACCESS", "").strip()
    if not raw or raw.lower() in ("1", "true", "yes", "on"):
        return ""
    return (f"CODEX_ORACLE_WRITE_FULL_ACCESS={raw!r} asks for the sealed write "
            "mode, which software-workflows 2.0.0 removed: every abraham write "
            "runs with full access. Unset the variable (or set it to 1) in the "
            "environment Claude Code starts the server with.")


# ---------------------------------------------------------------------------
# Infra-shaped question detection (auto `infra` for research)
# ---------------------------------------------------------------------------
# `infra=True` is NOT a free upgrade: it runs codex with --sandbox
# danger-full-access AND the caller's own MCP servers (which hold live
# credentials), with read-only enforced by PROMPT ONLY. Research is also the
# one mode that ingests UNTRUSTED external content (live web), so defaulting it
# on would put web-borne prompt injection in front of a credentialed shell.
#
# Live WEB research does NOT need it — `web_search=live` is forced on every
# call regardless of mode (measured: ordinary sandboxed runs perform real web
# searches). What infra actually buys is reaching THIS PROJECT'S live systems:
# SSH, the live DB, container/deploy logs.
#
# So auto-enable only for questions that are actually about live systems,
# using TWO signals — a liveness word AND an infra noun. One signal alone is
# too loose: "compare Postgres vs MySQL indexing" is a general question and
# must stay sandboxed; "why is our production database slow" is not.
# An explicit infra= from the caller ALWAYS wins, in both directions, and the
# decision is reported in the result so the mode is never switched silently.
_INFRA_LIVENESS = (
    "live", "production", "prod", "our ", "currently", "right now",
    "deployed", "running", "actual", "in-flight", "real-time", "realtime",
)
_INFRA_NOUNS = (
    "server", "database", " db", "postgres", "redis", "log", "container",
    "docker", "kubernetes", "k8s", "cluster", "deploy", "dokploy", "ssh",
    "endpoint", "instance", "traefik", "nginx", "celery", "temporal",
    "migration", "outage", "downtime",
)


def _looks_infra_shaped(text: str) -> tuple[bool, str]:
    """True when a topic is about THIS project's LIVE systems.

    Returns ``(enable, reason)``; the reason is surfaced to the caller so an
    auto-enabled full-access run is always visible, never silent.
    """
    t = f" {text.lower()} "
    live = next((w for w in _INFRA_LIVENESS if w in t), "")
    noun = next((w for w in _INFRA_NOUNS if w in t), "")
    if live and noun:
        return True, f"matched liveness {live.strip()!r} + infra noun {noun.strip()!r}"
    return False, ""


# ---------------------------------------------------------------------------
# Write mode (abraham): git safety
# ---------------------------------------------------------------------------
# An autonomous write run gets exactly one undo mechanism: git. So a write
# run (1) refuses to start outside a work tree, (2) snapshots the dirty state
# BEFORE dispatch so its report separates "changed by this run" from "already
# dirty", and (3) verifies afterwards that HEAD did not move (the prompt
# forbids commits; the report calls out a violation instead of trusting it).


def _git(args: list[str], cwd: str) -> tuple[int, str]:
    """Run a short git metadata query. Sync on purpose: these are <50ms
    reads at dispatch/return time, not part of the streamed run."""
    try:
        p = subprocess.run(
            ["git", *_treedigest.GIT_SAFE_CONFIG, *args], cwd=cwd, env=_treedigest.git_env(),
            capture_output=True, text=True, timeout=15,
        )
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


_GIT_STATE_REASON = ""  # why the last _git_state refused (for the messages that follow it)


def _tree_snapshot(cwd: str) -> dict:
    """ONE walk of the tree (treedigest.inspect) → {"ok", "lines", "head",
    "paths", "index", "reason"}: _git_state's status lines and HEAD, plus a
    digest of each path's content, tracked and untracked (ignored files are
    not part of a change), a submodule's covering what is inside it, and each
    tracked path's staged (index) identity — so a further edit to a file that
    was dirty already, or a staged blob swapped under an unchanged status,
    both show, which status lines cannot (review of 2026-10-18). ok is False
    when any of it could not be read, and `reason` says why."""
    try:
        r = _treedigest.inspect(cwd, want_status=True, inner_paths=True)
    except Exception as exc:  # the module never raises; belt and braces
        r = {"repo": False, "lines": None, "head": "", "paths": None, "index": None,
             "reason": f"{type(exc).__name__}: {exc}"}
    if not (r["repo"] and r["lines"] is not None and r.get("paths") is not None
            and r.get("index") is not None):
        return {"ok": False, "lines": set(), "head": "", "paths": {}, "index": {},
                "reason": r["reason"] or "unknown"}
    return {"ok": True, "lines": set(r["lines"]), "head": r["head"],
            "paths": r["paths"], "index": r["index"], "reason": ""}


def _lines_by_path(lines: set[str]) -> dict[str, str]:
    """Status lines by path. Two byte paths that display alike (bytes that are
    not UTF-8) keep both lines, in a stable order (review of 2026-10-18)."""
    out: dict[str, str] = {}
    for ln in sorted(lines):
        out[ln[3:]] = f"{out[ln[3:]]}; {ln}" if ln[3:] in out else ln
    return out


def _tree_changes(before: dict, after: dict) -> list[tuple[str, str]]:
    """(path, line) for each path two _tree_snapshot results disagree on, by
    content or by status: the line is the path's status line now, `   path`
    when it is clean now, `(gone) path` when it is no longer there; marked
    `(dirty before)` when it already differed from HEAD before."""
    bl, al = _lines_by_path(before["lines"]), _lines_by_path(after["lines"])
    bp, ap = before["paths"], after["paths"]
    bi, ai = before.get("index") or {}, after.get("index") or {}
    out = []
    for path in sorted(set(bp) | set(ap) | set(bl) | set(al) | set(bi) | set(ai)):
        if bp.get(path) == ap.get(path) and bl.get(path) == al.get(path) and bi.get(path) == ai.get(path):
            continue
        line = al.get(path) or (f"   {path}" if path in ap else f"(gone) {path}")
        out.append((path, line + (" (dirty before)" if path in bl else "")))
    return out


def _git_state(cwd: str) -> tuple[bool, set[str], str]:
    """(is_work_tree, porcelain-shaped dirty lines, HEAD sha — '' on a repo
    with no commits yet, which is still a valid write target).

    Round 36: computed by treedigest (_tree_snapshot) — the index and HEAD
    listings plus this process's OWN byte reads — so no `git status` runs
    here: status refreshes the index and runs a configured clean filter for
    any file whose stat data changed (measured on 2.55.0), a helper command
    the writer could have configured into the very tree it wrote.
    Vocabulary = porcelain's, plus ` ~` for a path whose bytes differ from
    the index under a conversion attribute (treated as dirty; the report
    carries the legend). ANY failure — budget, unreadable listing, an
    unresolvable submodule — is NOT a clean tree: (False, ∅, '') refuses the
    write target (fail closed, round 34), and the reason is kept for the
    refusal message."""
    global _GIT_STATE_REASON
    snap = _tree_snapshot(cwd)
    _GIT_STATE_REASON = snap["reason"]
    return snap["ok"], snap["lines"], snap["head"]


_WRITE_REPORT_MAX_CHARS = 3500  # the report is a summary; the DIFF is the review surface


def _write_changes_report(before: dict, cwd: str) -> str:
    """Attribution block appended to every write-run result, from the
    _tree_snapshot taken before the run and one taken now. By content: a
    further change to a file that was dirty already is named, and a dirty
    file the run left alone is not (review of 2026-10-18; porcelain-line set
    math could not tell them apart). What another writer changed in the same
    tree meanwhile is in it too, and the header says so.
    """
    after = _tree_snapshot(cwd)
    if not after["ok"]:
        return (
            "\n\n[CHANGED FILES: git state unreadable after the run "
            f"({after['reason']}) — inspect the tree manually]"
        )
    head_before, head_after = before["head"], after["head"]
    changed = _tree_changes(before, after)
    left_alone = {ln[3:] for ln in before["lines"]} - {p for p, _ in changed}
    parts = ["\n\n[CHANGED FILES — in this tree while this write run ran "
             "(another writer's changes meanwhile count too)]"]
    if changed:
        parts += [f"  {ln}" for _, ln in changed]
    else:
        parts.append("  (no working-tree changes during this run)")
    if left_alone:
        parts.append(f"  dirty before dispatch and left as they were: {len(left_alone)} path(s)")
    if head_after != head_before:
        parts.append(
            f"  ⚠ HEAD MOVED {head_before[:9] or '(none)'} → "
            f"{head_after[:9] or '(none)'} — the run violated the no-commit "
            "contract; audit `git log` before trusting the tree"
        )
    else:
        parts.append(
            f"  HEAD unchanged ({head_before[:9] or 'no commits yet'}) — "
            "nothing was committed"
        )
    if any(len(ln) > 1 and ln[1] == "~" for ln in (before["lines"] | after["lines"])):
        parts.append(
            "  legend: ` ~` = bytes differ from the index but a conversion "
            "attribute (filter/eol/ident/encoding) applies, so the comparison "
            "is not authoritative without that helper — treated as dirty"
        )
    report = "\n".join(parts)
    if len(report) > _WRITE_REPORT_MAX_CHARS:
        report = (
            report[:_WRITE_REPORT_MAX_CHARS]
            + "\n  … report truncated — run `git status` for the full picture"
        )
    return report


def _active_write_run(cwd: str, exclude_run: str = "") -> str:
    """Run tag of a still-alive WRITE run in this workspace, or ''.

    One writer per tree: two autonomous write runs interleaving edits in the
    same checkout produce an unreviewable diff. Liveness = journaled start
    without an end record + live log written <60s ago (the same test the
    resume guard applies before touching a thread).
    """
    for rec in _journal_runs().values():
        if not rec.get("write") or rec.get("cwd") != cwd or rec.get("has_end"):
            continue
        if exclude_run and rec.get("run") == exclude_run:
            continue
        log_path = str(rec.get("log") or "")
        with contextlib.suppress(OSError):
            if log_path and Path(log_path).is_file():
                if time.time() - Path(log_path).stat().st_mtime < 60:
                    return str(rec.get("run", "?"))
    return ""


# One implementation is shared with the independent native-writer custodian.
from coordinator.compat import (
    _tree_identity,
    _write_lock_path,
    _legacy_write_lock_holder,
    _try_lock_fd,
    _acquire_os_lock,
    _release_os_lock,
    _held_lock_fd,
    _rewrite_held_payload,
    _server_registry_dir,
    _register_server,
    _registered_server_pids,
    _ps_snapshot,
    _proc_comm,
    _pre_1172_server_running,
    _legacy_lock_paths_for,
    _plant_legacy_locks,
    _acquire_write_lock,
    _note_write_child,
    _release_write_lock,
    _HELD,
    _PLANTED_LEGACY,
    _LOCK_CUSTODY,
    PLUGIN_LOCK_PROTOCOL
)


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------
# Every run streams its full event feed — reasoning summaries, web-search
# queries, commands, MCP tool calls, errors, token usage — to a per-run log:
#
#     tail -f ~/.claude/logs/codex-oracle/latest.log
#
# Mechanism: ``codex exec --json`` (JSONL ThreadEvents on stdout) plus
# ``-c model_reasoning_summary=detailed`` so the thinking is actually
# emitted (the default banner showed "reasoning summaries: none" — nothing
# to watch). Both verified live on codex-cli 0.144.1 headless exec mode:
# events arrive incrementally and --output-last-message still works.

LIVE_LOG_DIR = Path(os.environ.get("CODEX_ORACLE_LOG_DIR") or Path.home() / ".claude" / "logs" / "codex-oracle")
LIVE_LOG_RETENTION_DAYS = 7
# Merged viewer feed: every run ALSO appends tagged lines to stream.log so
# `tail -F stream.log` shows ALL concurrent runs (across sessions/processes —
# O_APPEND interleaves at line granularity). Truncated at run-start past this
# cap; it is a convenience view DUPLICATING the per-run files (the archive),
# so truncation loses nothing durable. 128 MiB ≈ months of heavy use at the
# observed ~60 KB per max-effort review run.
STREAM_LOG_MAX_BYTES = 128 * 1024 * 1024
_live_log_seq = itertools.count(1)


def _private(path: Path, mode: int) -> None:
    """Tighten permissions on a log/journal file or dir. These carry full
    prompts, model output and command output — other local users must not be
    able to read them (they were 0644 before). Never raises."""
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _prune_live_logs() -> None:
    """Drop run logs and run spool dirs older than the retention window.
    A spool dir's age is its NEWEST file (a detached run keeps writing).
    Never raises."""
    cutoff = time.time() - LIVE_LOG_RETENTION_DAYS * 86_400
    with contextlib.suppress(OSError):
        for p in LIVE_LOG_DIR.glob("*.log"):
            if p.is_symlink():
                continue
            with contextlib.suppress(OSError):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
    with contextlib.suppress(OSError):
        for d in _run_dir_root().glob("*"):
            if not d.is_dir() or d.name in _MANAGED_DIRS:
                # claims/ and cancel/ are coordination state: unlinking a
                # LOCKED file leaves the lock on the old inode and lets a
                # second process lock a new one under the same name (round 4).
                continue
            with contextlib.suppress(OSError):
                newest = max(
                    (f.stat().st_mtime for f in d.iterdir()),
                    default=d.stat().st_mtime,
                )
                if newest < cutoff:
                    shutil.rmtree(d, ignore_errors=True)


def _open_live_log(label: str) -> tuple[Path | None, TextIO | None, TextIO | None, str]:
    """Open the per-run live log + the merged stream, repoint ``latest.log``.

    Returns ``(path, run_fh, stream_fh, tag)``. The tag (``codex5·21746``)
    prefixes this run's lines in ``stream.log`` so concurrent runs stay
    tellable-apart in the merged view. Observability must never break the
    run itself: any OS failure degrades to ``None`` handles.
    """
    seq = next(_live_log_seq)
    # pid + counter alone recurs after PID reuse (review of 1.17.0); the
    # token keeps journal folds and spool dirs from merging two runs.
    tag = f"{label}{seq}·{os.getpid()}·{secrets.token_hex(2)}"
    path: Path | None = None
    fh: TextIO | None = None
    stream_fh: TextIO | None = None
    try:
        LIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _private(LIVE_LOG_DIR, 0o700)
        _prune_live_logs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = LIVE_LOG_DIR / f"{stamp}-p{os.getpid()}-{seq}-{label}.log"
        fh = path.open("w", encoding="utf-8")
        _private(path, 0o600)
        latest = LIVE_LOG_DIR / "latest.log"
        with contextlib.suppress(OSError):
            latest.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            latest.symlink_to(path.name)
    except OSError:
        path, fh = None, None
    with contextlib.suppress(OSError):
        stream_path = LIVE_LOG_DIR / "stream.log"
        truncated = False
        with contextlib.suppress(OSError):
            if stream_path.exists() and stream_path.stat().st_size > STREAM_LOG_MAX_BYTES:
                # O_APPEND writers elsewhere keep working: their next write
                # lands at the new (small) EOF. Per-run files keep everything.
                stream_path.write_text("")
                truncated = True
        stream_fh = stream_path.open("a", encoding="utf-8")
        _private(stream_path, 0o600)
        if truncated:
            stream_fh.write(
                f"# stream.log truncated past {STREAM_LOG_MAX_BYTES:,} bytes "
                f"— full history stays in the per-run files\n"
            )
            stream_fh.flush()
    return path, fh, stream_fh, tag


def _live_write(fh: TextIO | None, t0: float, text: str, tag: str = "") -> None:
    """Append one stamped block to a live log, flushed for tail -f.

    With ``tag`` the stamp carries the run id — the merged-stream format.
    The whole block is one buffered write so concurrent runs interleave at
    block granularity in stream.log.
    """
    if fh is None:
        return
    with contextlib.suppress(Exception):
        stamp = (
            f"[{time.monotonic() - t0:7.1f}s {tag}] " if tag
            else f"[{time.monotonic() - t0:7.1f}s] "
        )
        pad = " " * len(stamp)
        lines = text.splitlines() or [""]
        block = stamp + lines[0] + "\n" + "".join(pad + ln + "\n" for ln in lines[1:])
        fh.write(block)
        fh.flush()


def _process_exec_event(ev: dict[str, Any], state: dict[str, str]) -> str | None:
    """Digest one ``codex exec --json`` ThreadEvent.

    Returns the human line(s) for the live log (None = nothing to show) and
    updates ``state`` in place: ``activity`` (for the progress heartbeat),
    ``last_message`` (final-answer fallback), ``last_error``, ``usage``.

    Field names follow codex-rs ``exec/src/exec_events.rs`` (verified against
    the installed 0.144.1 binary): items are tagged unions with flat fields —
    reasoning/agent_message carry ``text``, command_execution ``command``/
    ``exit_code``, web_search ``query``, mcp_tool_call ``server``/``tool``.
    Unknown shapes degrade to a raw JSON snippet, never a crash.
    """
    et = str(ev.get("type", ""))
    item = ev.get("item") or {}
    it = str(item.get("type") or item.get("item_type") or "")

    if et == "thread.started":
        state["activity"] = "session started"
        # The rollout/session id — the handle `codex exec resume` continues.
        state["thread_id"] = str(ev.get("thread_id") or "")
        return f"thread started (id: {ev.get('thread_id', '?')})"
    if et == "turn.started":
        state["activity"] = "thinking"
        return "turn started"
    if et == "turn.completed":
        state["turn_completed"] = True
        usage = ev.get("usage") or {}
        if usage:
            # On a resume codex reports the THREAD's cumulative usage here
            # (measured 0.163.0); the per-turn figures live in the rollout.
            state["usage"] = (
                ("thread total: " if state.get("resumed") else "")
                + f"tokens in={usage.get('input_tokens', '?')} "
                f"(cached {usage.get('cached_input_tokens', 0)}) "
                f"out={usage.get('output_tokens', '?')}"
            )
        return f"turn completed — {state.get('usage', 'no usage reported')}"
    if et == "turn.failed":
        err = (ev.get("error") or {}).get("message") or json.dumps(ev)[:400]
        state["last_error"] = err
        state["activity"] = "turn FAILED"
        return f"TURN FAILED: {err}"
    if et == "error":
        msg = str(ev.get("message") or json.dumps(ev)[:400])
        state["last_error"] = msg
        state["activity"] = "stream error"
        return f"STREAM ERROR: {msg}"

    if et in ("item.started", "item.updated", "item.completed"):
        done = et == "item.completed"
        if it == "reasoning":
            text = str(item.get("text") or "")
            if not (done and text):
                return None
            first = text.splitlines()[0].strip("* ") if text.splitlines() else ""
            state["activity"] = f"thinking: {first[:100]}"
            return "reasoning:\n" + "\n".join(f"  {ln}" for ln in text.splitlines())
        if it == "agent_message":
            text = str(item.get("text") or "")
            if not (done and text):
                return None
            state["last_message"] = text
            state["activity"] = f"answer: {text[:100]}"
            return f"assistant: {text}"
        if it == "web_search":
            query = str(item.get("query") or "")
            if done and query:
                state["activity"] = f"web search: {query[:100]}"
                return f"web_search: {query}"
            if et == "item.started":
                state["activity"] = "web search…"
                return "web search started"
            return None
        if it == "command_execution":
            command = str(item.get("command") or "")
            if et == "item.started":
                state["activity"] = f"exec: {command[:100]}"
                return f"$ {command}"
            if done:
                status = item.get("status", "?")
                code = item.get("exit_code")
                out = str(item.get("aggregated_output") or "").strip()
                line = f"$ {command} → {status}" + (f" (exit {code})" if code is not None else "")
                if out:
                    tail = out[-500:]
                    line += "\n" + "\n".join(f"  | {ln}" for ln in tail.splitlines())
                if str(status) == "failed":
                    # a failed TOOL command is the model's business, not a
                    # terminal error of the run (round 46: it masked the real
                    # reason a detached run ended and would have made a
                    # completed detached run read status:error)
                    state["last_command_failure"] = f"command failed: {command} → exit {code}"
                return line
            return None
        if it == "mcp_tool_call":
            name = f"{item.get('server', '?')}.{item.get('tool', '?')}"
            if et == "item.started":
                state["activity"] = f"mcp: {name}"
                return f"mcp call: {name}"
            if done:
                return f"mcp call: {name} → {item.get('status', '?')}"
            return None
        if it == "file_change":
            if done:
                return f"file change: {item.get('status', '?')}"
            return None
        if it == "error":
            msg = str(item.get("message") or "")
            if msg:
                # DIAGNOSTIC, not terminal (round 50): codex emits non-fatal
                # WARNINGS as completed `error` ITEMS while the turn keeps
                # running, so writing them to `last_error` made a run that
                # finished with a real answer collect as an error, and let a
                # warning mask a later stderr-only disconnect from retry
                # classification. Only `turn.failed` and a stream-level
                # `error` EVENT are terminal.
                state["last_item_error"] = msg
                state["activity"] = "error"
                return f"ERROR: {msg}"
            return None
        if it == "todo_list":
            return None  # progress plumbing, too chatty for the log
        if done:
            return f"{it or 'item'}: {json.dumps(item)[:300]}"
        return None

    return f"event: {json.dumps(ev)[:300]}"


# ---------------------------------------------------------------------------
# Advisor context (curated, secret-free — NOT CLAUDE.md, NOT memory)
# ---------------------------------------------------------------------------
# SECURITY: a project's CLAUDE.md and its memory files contain LIVE SECRETS
# (verified: this workspace's CLAUDE.md carries SSH/DB passwords in its first
# 3 KB and says "never copy into any repo"; project-typed memories carry
# credentials too). The advisor is an EXTERNAL provider (OpenAI), so we
# NEVER inject those. We inject ONLY a file the maintainer curates to be
# external-safe: ADVISOR_CONTEXT.md. Absent file = nothing sent (default
# CLOSED). Chosen by the user 2026-08-09.
#
# NOTE (measured 0.144.1, twice): codex reads CLAUDE.md as a project doc
# NATIVELY and neither `project_doc_max_bytes=0` nor
# `project_doc_fallback_filenames=[...]` suppresses it — so codex→OpenAI
# CLAUDE.md exposure is PRE-EXISTING and outside this wrapper's control. We
# neither add to it nor pin it. (A source read of a NEWER upstream commit
# suggests it is only a configurable fallback there; that is not what the
# deployed binary does — verify the backend you run, not the source you read.)
ADVISOR_CONTEXT_FILE = "ADVISOR_CONTEXT.md"
ADVISOR_CONTEXT_MAX_CHARS = _env_int("CODEX_ORACLE_ADVISOR_CONTEXT_MAX_CHARS", 32000, 0)


def _advisor_context_bases(cwd: Path, home: Path) -> list[Path]:
    """Directories to search for ADVISOR_CONTEXT.md, nearest first.

    Bounded to $HOME: inside it we walk cwd -> $HOME; OUTSIDE it (e.g. cwd is
    /tmp/project) we search ONLY cwd — never a shared parent like /tmp or /
    where another user could plant a file."""
    if cwd == home or home in cwd.parents:
        bases, b = [], cwd
        while True:
            bases.append(b)
            if b == home:
                break
            b = b.parent
        return bases
    return [cwd]


def _advisor_context(max_chars: int = ADVISOR_CONTEXT_MAX_CHARS) -> str:
    """The curated ADVISOR_CONTEXT.md (cwd, then up to $HOME), bounded, or "".

    Explicitly NOT CLAUDE.md/AGENTS.md/memory — only the maintainer's vetted,
    secret-free advisor file. Nearest wins. Best-effort; never raises.

    SECURITY: the file must be a REGULAR file, NOT a symlink (a repo could
    plant ADVISOR_CONTEXT.md -> ~/.ssh/id_rsa or -> CLAUDE.md and exfiltrate
    it to the provider), must resolve inside the directory it was found in,
    and is read with a BOUNDED read so a huge file can't exhaust memory."""
    if max_chars <= 0:
        return ""
    with contextlib.suppress(OSError):
        cwd = Path(os.environ.get("CLAUDE_CWD", os.getcwd())).resolve()
        home = Path.home().resolve()
        for base in _advisor_context_bases(cwd, home):
            fp = base / ADVISOR_CONTEXT_FILE
            try:
                if fp.is_symlink() or not fp.is_file():
                    continue
                if not fp.resolve().is_relative_to(base.resolve()):
                    continue  # a parent-dir symlink escaped the tree
                with fp.open("r", encoding="utf-8", errors="replace") as fh:
                    raw = fh.read(max_chars + 1)  # bounded read
            except OSError:
                continue
            text = raw.strip()
            if text:
                body = text[:max_chars]
                if len(raw) > max_chars:
                    body += "\n[...advisor context truncated...]"
                return (
                    "[PROJECT CONTEXT — the maintainer's curated, external-safe "
                    f"notes on how this codebase works ({fp}):\n\n{body}]"
                )
    return ""


# ---------------------------------------------------------------------------
# Run journal + resume/retry
# ---------------------------------------------------------------------------
# Sessions are the recovery substrate: a failed or orphaned run can be
# continued via `codex exec … resume <thread_id> <nudge>` — same thread id.
# --ephemeral is therefore NOT passed anymore.
#
# ⚠ Do NOT rely on the rollout having the original input. An earlier note
# here said "measured 2026-08-08: a SIGKILL'd run resumed with full context"
# — that measurement was taken under the LEAKY kill (proc.kill() reaped only
# the node shim; the surviving grandchild FINISHED the turn and flushed it).
# Re-measured 2026-08-09 under the true process-group kill: a turn killed
# early persists NOTHING of its input — the resumed thread starts with only
# session plumbing, and the model fabricates. codex_resume_run therefore
# restates the journaled original prompt in every continuation.
#
# The journal (runs.jsonl, one JSON record per line: start/session/end) plus a
# per-run .result.txt survive MCP-server restarts, so `codex_resume_run` can
# recover a run after a plugin reload killed the call mid-flight — returning
# the stored answer for free when the run actually finished.
#
# AMNESIA GUARD (measured): `codex exec resume` with an unknown/empty id
# SILENTLY starts a fresh context-less thread and still exits 0 — every
# resume must verify the resumed thread.started id equals the expected one.

RUNS_JOURNAL = LIVE_LOG_DIR / "runs.jsonl"
RUNS_JOURNAL_MAX_BYTES = 5 * 1024 * 1024  # ~300 runs with full prompts; one
# older generation kept as runs.jsonl.1 — recovery data, not an archive.
MAX_TRANSIENT_RETRIES = 2
# Provider LOAD-SHEDDING is a different failure from a dropped stream, and it
# needs a different response. codex maps HTTP 503 {"error":{"code":
# "server_is_overloaded"|"slow_down"}} — and the equivalent response.failed
# SSE error — to CodexErr::ServerOverloaded, whose rendered text is
# "Selected model is at capacity. Please try a different model.", and its
# is_retryable() is FALSE (codex-rs/protocol/src/error.rs, identical at
# rust-v0.147.0 and rust-v0.151.0): the CLI fails the turn on the spot with
# no internal retry. This wrapper is therefore the ONLY retry layer there is.
# 2026-08-31: four max-effort reviews died exactly this way (89s..1163s in,
# attempts=1 each) because the classifier below matched the VARIANT NAME
# ("overloaded") and never the rendered message. A shed is also not fixed by
# an instant retry: wait, then resume the SAME thread (context intact) — and
# never switch models; the model/effort pin is the point of this oracle.
# Schedule: base·2^i capped — 30s, 60s, 120s, 240s (≈7.5 min of waiting over
# 4 retries). Derivation: rides out the short sheds (seconds to minutes) that
# would otherwise throw away an hour-long max-effort run; a shed that outlives
# the budget (2026-08-31's ran 12:16→~15:07 IST, ≈3 h; a 15:45 probe answered
# in 6.7 s) ends in the explicit codex_resume_run path,
# which continues the same thread later at no re-ask cost. Env-adjustable:
# CODEX_ORACLE_OVERLOAD_BACKOFF=0 retries immediately, _RETRIES=0 disables.
OVERLOAD_MAX_RETRIES = min(12, _env_int("CODEX_ORACLE_OVERLOAD_RETRIES", 4, 0))
OVERLOAD_BACKOFF_BASE_SECONDS = _env_seconds(
    "CODEX_ORACLE_OVERLOAD_BACKOFF", 30.0, 0.0
)
OVERLOAD_BACKOFF_CAP_SECONDS = 300.0  # the ceiling AFTER jitter
# Per-class budgets (overload above; disconnect = MAX_TRANSIENT_RETRIES) and
# one explicit total ceiling: a mixed failure sequence can never exceed it.
MAX_TOTAL_RETRIES = OVERLOAD_MAX_RETRIES + MAX_TRANSIENT_RETRIES

# ---- RUN SURVIVABILITY across MCP server restarts (1.17.0) ------------------
# A backgrounded oracle call is a CHILD of this MCP server. Claude Code's
# `/mcp` reconnect, a plugin reload, or a session exit sends SIGINT then
# SIGTERM ~100 ms apart (its own log, 2026-08-31) and the caller sees
# "Connection closed"; the old cleanup then SIGKILLed the codex tree — a
# 25-minute max-effort review gone, re-dispatched from scratch. Two measured
# facts decide the design: (1) an orphaned `codex exec --json` whose stdout
# PIPE reader vanished panics with "failed printing to stdout: Broken pipe"
# — so the child's stdio must be FILES that this server TAILS; (2) the codex
# thread/rollout and the run journal are on disk — so a run that outlives
# its server can be COLLECTED by the next one. Hence: a per-run spool dir,
# a detached /bin/sh watchdog that enforces MAX_RUNTIME with no server
# alive, a shutdown flag set by the signal handlers so the cancel-cleanup
# DETACHES instead of killing, and adoption in codex_resume_run. A caller
# cancel with no shutdown signal still kills (that is "stop spending");
# write runs are never detached (the one-writer lock's liveness is THIS
# server's pid). Knobs: CODEX_ORACLE_TAIL_POLL (spool poll interval),
# CODEX_ORACLE_CODEX_BIN (pin the codex executable — e.g. the ChatGPT.app
# bundled one, or a fake for tests).
TAIL_POLL_SECONDS = _env_seconds("CODEX_ORACLE_TAIL_POLL", 0.25, 0.05)
SPOOL_COLLISION_MAX = 100        # suffixes tried before a private temp dir
CAPTURE_MAX_BYTES = 512 * 1024   # in-memory tail of a run's stdout/stderr
REPLAY_MAX_BYTES = 1024 * 1024   # how much of a spool status/adoption re-reads
_SHUTDOWN = threading.Event()


_MANAGED_DIRS = ("claims", "cancel")


def _run_dir_root() -> Path:
    return LIVE_LOG_DIR / "runs"


def _run_spool_dir(run_tag: str) -> Path:
    """Per-run private dir for the child's stdout/stderr spool + answer file.
    Created EXCLUSIVELY — a recurring tag must never append into an older
    run's spool. Falls back to a temp dir rather than break the run."""
    base = _run_dir_root() / run_tag.replace("·", "-")
    d = base
    try:
        _run_dir_root().mkdir(parents=True, exist_ok=True)
        _private(_run_dir_root(), 0o700)
        for i in range(SPOOL_COLLISION_MAX):
            try:
                d.mkdir(exist_ok=False)
                _private(d, 0o700)
                return d
            except FileExistsError:
                d = base.with_name(f"{base.name}-{i + 1}")
    except OSError:
        pass
    # Exclusive creation never succeeded: never hand back a dir we did not
    # create (it may be another run's spool) — a private temp dir instead.
    return Path(tempfile.mkdtemp(prefix="codex-oracle-run-"))


_WATCHDOG_SH = (
    # Deadline enforcer with NO server alive. Round-21 corrections:
    # polling is not continuity — pids/pgids are reusable between 5s
    # samples — so every group signal is anchored to the LEADER's IDENTITY
    # (start token, captured before this watchdog spawned): killpg fires
    # only while `ps -o lstart=` still matches; once it does not, kills are
    # marker-verified pids only, forever. The marker scan is procenv.py
    # `--list` (exact ENVIRONMENT reads — round 31: `ps -E` is BSD-only, so
    # a ps text scan was never a Linux capability); a failed scan emits U:
    # unknown keeps the loop alive and is logged, never read as quiescence.
    'pid=$1; pgid=$2; deadline=$3; tag=$4; start=$5; flog=$6; '
    'maxu=${7:-60}; PS=${8:-/bin/ps}; PY=${9:-}; PE=${10:-}; s=; '
    # TICK BOUND (round 34): the wall clock (`date +%s`) can be rolled
    # back under a detached enforcer; ticks (one per 5 s poll) cannot —
    # the deadline fires on whichever comes first.
    'maxt=${11:-0}; t=0; [ "$maxt" -gt 0 ] || maxt=$(( (deadline - $(date +%s)) / 5 + 2 )); '
    # Absolute ps (deployment-verified /bin/ps on macOS and Linux) so a PATH
    # accident cannot blind the enforcer (round 23); the bound `maxu` on
    # CONSECUTIVE unknown scans is the termination policy below.
    'lead() { [ -n "$start" ] && '
    '[ "$("$PS" -o lstart= -p "$pid" 2>/dev/null | '
    "awk '{$1=$1; gsub(/ /,\"_\"); print}')\" = \"$start\" ]; }; "
    'marked() { [ -n "$PY" ] && [ -n "$PE" ] || { echo U; return; }; '
    'o=$("$PY" "$PE" --list "$tag" 2>/dev/null) || { echo U; return; }; printf "%s\\n" "$o"; }; '
    # verified(): EXACT env check through procenv.py (round 30) — the text
    # scan above only nominates; a kill needs the marker in the process
    # ENVIRONMENT. No interpreter / no verifier = unverifiable = no kill.
    'verified() { [ -n "$PY" ] && [ -n "$PE" ] && "$PY" "$PE" "$1" "$tag" >/dev/null 2>&1; }; '
    # alive(): an unknown scan (U) reads alive, but is COUNTED — a watchdog
    # that cannot see for maxu consecutive ticks exits degraded (exit 2).
    'alive() { if lead; then u=0; return 0; fi; mm=$(marked); '
    'if [ "$mm" = U ]; then u=$((u+1)); '
    'if [ "$u" -ge "$maxu" ]; then [ -n "$flog" ] && '
    'echo "$(date +%s) run=$tag degraded ps-unavailable exit=2" >> "$flog" 2>/dev/null; '
    'exit 2; fi; return 0; fi; u=0; '
    '[ -n "$mm" ] && return 0; return 1; }; '
    "trap 'kill $s 2>/dev/null; exit 0' TERM INT; "
    'while alive; do t=$((t+1)); '
    'if [ "$(date +%s)" -ge "$deadline" ] || [ "$t" -ge "$maxt" ]; then '
    'if lead; then kill -9 -- "-$pgid" 2>/dev/null; fi; '
    # ENV-VERIFIED KILLS (round 30): the ps text scan nominates candidates
    # (argv+env in one string), procenv.py confirms the marker is in the
    # ENVIRONMENT before any signal; an unverified nominee is logged as
    # `unverified-marked` (degraded custody, bounded by the 5-pass limit
    # below) and never signalled — an argv decoy or an operator's grep is
    # never killed by a watchdog either.
    # Deadline sweep (round 22): UNKNOWN evidence never counts toward
    # completion — a failed ps keeps the sweep alive (logged, throttled)
    # until it can see again; only VERIFIED kill passes are bounded (5),
    # after which unkillable survivors are logged and the watchdog exits.
    'n=0; u=0; while :; do '
    'ms=$(marked); [ -z "$ms" ] && break; '
    'if [ "$ms" = U ]; then u=$((u+1)); '
    '[ "$u" -le 3 ] && [ -n "$flog" ] && '
    'echo "$(date +%s) run=$tag ps-unknown" >> "$flog" 2>/dev/null; '
    # BOUNDED (round 23): after maxu CONSECUTIVE unknown scans the
    # enforcer cannot see and must not pretend to enforce — it records a
    # machine-readable DEGRADED state and exits 2; a dead watchdog makes
    # the run ORPHANED (never adoptable, still stoppable via the server's
    # own marker-verified cancel), the fail-closed direction.
    'if [ "$u" -ge "$maxu" ]; then [ -n "$flog" ] && '
    'echo "$(date +%s) run=$tag degraded ps-unavailable exit=2" >> "$flog" 2>/dev/null; '
    'exit 2; fi; '
    'sleep 5; continue; fi; u=0; '
    'if [ "$n" -ge 5 ]; then [ -n "$flog" ] && '
    'echo "$(date +%s) run=$tag unkillable-marked $ms" >> "$flog" 2>/dev/null; break; fi; '
    'for p in $ms; do if verified "$p"; then kill -9 "$p" 2>/dev/null; '
    'elif [ -n "$flog" ]; then echo "$(date +%s) run=$tag unverified-marked $p" >> "$flog" 2>/dev/null; fi; done; '
    'sleep 1; n=$((n+1)); done; exit 0; fi; '
    'sleep 5 & s=$!; wait $s; done'
)


def _venv_lock() -> tuple[int, ...]:
    """run_server.py's shared lock on this server's venv, as pass_fds for a detached watchdog: the
    watchdog runs procenv.py with this venv's python after the server may be gone, and the
    launcher deletes an old venv only when no process holds that lock. () when this server was
    not started that way (the descriptor must be the lock on this very venv's .in-use)."""
    fd = os.environ.pop("CODEX_ORACLE_VENV_LOCK", "")
    try:
        held, ours = os.fstat(int(fd)), os.stat(Path(sys.prefix) / ".in-use")
    except (ValueError, OSError):
        return ()
    return (int(fd),) if os.path.samestat(held, ours) else ()


_VENV_LOCK_FDS = _venv_lock()


def _spawn_watchdog(pid: int, pgid: int, deadline_ts: float,
                    run_tag: str = "", pid_start: str = "",
                    max_unknown: int = 60, ps_bin: str = "/bin/ps",
                    python_bin: str = "", procenv: str = "",
                    max_ticks: int = 0) -> subprocess.Popen | None:
    """POSIX: a tiny detached /bin/sh that SIGKILLs the codex process group
    at the run deadline, or exits within 5 s of codex ending on its own. The
    MAX_RUNTIME bound therefore holds with NO server alive — a detached run
    has none. Windows keeps the in-server timeout only (returns None)."""
    if os.name == "nt":
        return None
    try:
        wd = subprocess.Popen(
            ["/bin/sh", "-c", _WATCHDOG_SH, "codex-oracle-watchdog",
             str(pid), str(pgid), str(int(deadline_ts)), run_tag, pid_start,
             str(LIVE_LOG_DIR / "watchdog-failures.log"), str(max_unknown), ps_bin,
             python_bin or sys.executable, procenv or str(PROCENV_PATH), str(max_ticks)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            pass_fds=_VENV_LOCK_FDS,
        )
        return wd
    except OSError:
        return None


def _proc_info_nt(pid: int) -> tuple[str, str]:
    """Windows: (state, start) from the process handle — creation time is the
    identity token, a zero-timeout wait is the liveness test (exit codes are
    ambiguous: STILL_ACTIVE is a legal exit code). Exact ctypes prototypes
    declared (HANDLE is pointer-sized; the int default would truncate).
    UNMEASURED on Windows; non-destructive by construction."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        h = k32.OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if not h:
            return "", ""
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            start = ""
            if k32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                start = f"win:{(ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime}"
            state = "R" if k32.WaitForSingleObject(h, 0) == 0x102 else "Z"  # WAIT_TIMEOUT = running
            return state, start
        finally:
            k32.CloseHandle(h)
    except Exception:
        return "", ""


def _proc_info(pid: int) -> tuple[str, str]:
    """(state, start) of a process: on POSIX via one `ps -o stat=,lstart=`
    call — state is the first letter of `stat` ("Z" = zombie: dead in every
    sense that matters, yet kill(0) and lstart still answer for it), start is
    the space-normalised `lstart` token; on Windows via the process handle.
    ("", "") when UNKNOWN (ps denied/absent, handle refused) — callers must
    treat unknown as alive for exclusion and as unkillable for destruction."""
    if not pid or pid <= 0:
        return "", ""
    if os.name == "nt":
        return _proc_info_nt(pid)
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["ps", "-o", "stat=,lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
        if out:
            return out[0][:1].upper(), "_".join(out[1:])
    return "", ""


def _proc_start(pid: int) -> str:
    """Immutable process identity: its start time (POSIX `ps -o lstart=`),
    space-normalised to one token. Empty when unknown (Windows, ps failure)
    — callers then fall back to kill(0) + command-line checks. Together
    with the pid this is what makes liveness and kill decisions PID-reuse
    safe (review of 1.17.0: signal 0 is a permission check, not identity)."""
    return _proc_info(pid)[1]


_SERVER_START = _proc_start(os.getpid())
_IS_WINDOWS = os.name == "nt"  # module flag so the refusal branches are testable


def _kill0(pid: int) -> bool | None:
    """True/False = exists/gone; None = exists but identity unknowable.
    NEVER os.kill(pid, 0) on Windows: Python routes non-control signals to
    TerminateProcess, so a "liveness probe" would kill the process (review
    round 2). There we query the handle instead (unmeasured on Windows, but
    non-destructive by construction)."""
    if os.name == "nt":
        stat, _ = _proc_info_nt(pid)
        return None if not stat else stat != "Z"
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return False


def _pid_alive(pid: int, start: str = "", strict: bool = False) -> bool:
    """Is this codex process still running? kill(0), then the recorded start
    time when we have one (a reused pid fails it), else the command line.
    ``strict``: only a VERIFIED identity match counts — unknown is False.
    Used where a wrong "alive" would AUTHORISE something (detaching an
    unbounded child on the strength of its watchdog)."""
    if not pid or pid <= 0:
        return False
    k = _kill0(pid)
    if k is False:
        return False
    if k is None:
        return not strict  # unknowable: alive for exclusion, never for authorisation
    stat, cur = _proc_info(pid)
    if not stat and not cur:
        return not strict
    if stat == "Z":
        return False  # a zombie is not running
    if strict:
        return bool(start) and bool(cur) and cur == start
    if start and cur:
        return cur == start
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout
            return "codex" in out.lower()
    return True


def _kill_pgid(pgid: int, pid: int, start: str = "") -> bool:
    """SIGKILL a run's process group (falls back to the pid). DESTRUCTIVE:
    refuses unless a recorded identity exists AND is verified to match — a
    record without a start token (legacy, or a platform that could not
    produce one) is never killed by pid alone (review round 3). Never raises."""
    if not start:
        return False
    cur = _proc_start(pid)
    if not cur or cur != start:
        return False
    try:
        if os.name != "nt" and pgid:
            os.killpg(pgid, signal.SIGKILL)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        if os.name == "nt":
            done = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                  capture_output=True, timeout=10)
            return done.returncode == 0
        os.kill(pid, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        return False


# One JSONL event is normally small (codex truncates tool output per event —
# `truncation_policy` 10k tokens on this model catalog, ≈40 KB), but an
# unterminated record must not grow the line buffer without bound (round 3).
# 32 MiB = ~800× that norm: a ceiling for a runaway, never a working limit.
JSONL_RECORD_MAX_BYTES = 32 * 1024 * 1024


def _feed_jsonl(linebuf: bytearray, chunk: bytes, state: dict[str, Any], emit) -> None:
    """Split a `codex exec --json` byte stream into events and digest them
    (shared by the live tail and by adoption replay). An oversized record is
    dropped LOUDLY and the stream re-synchronises at the next newline."""
    linebuf.extend(chunk)
    if state.get("_skip_to_nl"):
        nl = linebuf.find(b"\n")
        if nl < 0:
            linebuf.clear()
            return
        del linebuf[:nl + 1]
        state["_skip_to_nl"] = False
    if len(linebuf) > JSONL_RECORD_MAX_BYTES and b"\n" not in linebuf:
        emit(f"⚠ oversized event dropped ({len(linebuf):,} bytes without a newline; "
             f"cap {JSONL_RECORD_MAX_BYTES:,}) — stream re-syncs at the next record")
        linebuf.clear()
        state["_skip_to_nl"] = True
        return
    while True:
        nl = linebuf.find(b"\n")
        if nl < 0:
            return
        if nl > JSONL_RECORD_MAX_BYTES:
            emit(f"⚠ oversized event dropped ({nl:,} bytes; cap {JSONL_RECORD_MAX_BYTES:,})")
            del linebuf[:nl + 1]
            continue
        raw = bytes(linebuf[:nl]).strip()
        del linebuf[:nl + 1]
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            line = _process_exec_event(ev, state)
        except ValueError:
            line = raw.decode("utf-8", errors="replace")
        if line:
            emit(line)


def _run_in_workspace(run_cwd: str, cwd: str) -> bool:
    """A run belongs to a workspace when its tree is the workspace itself or
    any directory below it; outside paths stay refused (security fence)."""
    if not run_cwd:
        return False
    if run_cwd == cwd:
        return True
    try:
        base, target = Path(cwd).resolve(), Path(run_cwd).resolve()
    except OSError:
        return False
    return base in target.parents


def _run_belongs(rec: dict[str, Any], cwd: str) -> bool:
    """A run belongs to a workspace through its target tree or through the
    folder it was dispatched from (abraham may target any repository)."""
    return (_run_in_workspace(str(rec.get("cwd") or ""), cwd)
            or _run_in_workspace(str(rec.get("dispatch_cwd") or ""), cwd))


SHUTDOWN_HARD_EXIT_SECONDS = 3.0


def _install_shutdown_handlers(hard_exit: bool = False) -> None:
    """First SIGINT/SIGTERM/SIGHUP: raise the shutdown flag, ignore further
    signals so the ~100 ms-later SIGTERM cannot tear the cleanup mid-way,
    then take the normal KeyboardInterrupt exit (anyio cancels the in-flight
    tool tasks; their cleanup sees the flag and detaches instead of killing).
    The cleanup is bounded (journal + log close, no waits on the child).

    ``hard_exit`` (the real server only — never under tests): the process
    MUST still die once the follow-up SIGTERM is ignored. The stdio reader
    thread blocks on a pipe the client may keep open (measured: the server
    lingered >10 s after SIGINT+SIGTERM), so a daemon thread ``os._exit``s
    after SHUTDOWN_HARD_EXIT_SECONDS, and __main__ exits the moment
    ``mcp.run`` unwinds. Detachment does not depend on the cleanup finishing
    at all: the spawn record carries the owning server's pid, and a run
    whose server is dead IS detached (see _is_detached)."""
    sigs = [getattr(signal, n, None) for n in ("SIGINT", "SIGTERM", "SIGHUP")]
    sigs = [s for s in sigs if s is not None]

    def _handler(signum, frame):
        _SHUTDOWN.set()
        for s in sigs:
            with contextlib.suppress(Exception):
                signal.signal(s, signal.SIG_IGN)
        if hard_exit:
            def _backstop() -> None:
                time.sleep(SHUTDOWN_HARD_EXIT_SECONDS)
                os._exit(0)
            threading.Thread(target=_backstop, name="shutdown-backstop",
                             daemon=True).start()
        raise KeyboardInterrupt

    for s in sigs:
        with contextlib.suppress(Exception):
            signal.signal(s, _handler)


def _server_alive(pid: int, start: str = "") -> bool:
    """Is the codex-oracle server process that owns a run still alive? Our
    own pid is alive by definition; a recorded start time settles identity;
    otherwise the command line must still look like this server (macOS
    `ps` reports the python framework binary for a venv python, so the
    command-line test alone misjudged a live holder — measured)."""
    if not pid or pid <= 0:
        return False
    if pid == os.getpid():
        # Our own pid — but a recorded start that is not OURS means the
        # record belongs to a previous incarnation of this pid.
        return (not start) or (not _SERVER_START) or start == _SERVER_START
    k = _kill0(pid)
    if k is False:
        return False
    if k is None:
        return True
    stat, cur = _proc_info(pid)
    if not stat and not cur:
        return True  # UNKNOWN evidence (ps denied/absent) never reads as dead
    if stat == "Z":
        return False
    if start and cur:
        return cur == start
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            if not out.strip():
                return True  # no evidence either way
            return "server.py" in out or "codex-oracle" in out or "run_server" in out
    return True


def _pgid_alive(pgid: int) -> bool:
    """Does the process GROUP still have a RUNNING member? killpg(0) alone is
    not enough — a signalled-but-unreaped ZOMBIE still answers it (measured;
    the same trap _pid_alive closed in round 2), so success is corroborated
    by ps: at least one member whose stat is not Z. Unknown evidence stays
    alive for exclusion."""
    if os.name == "nt" or not pgid:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        pass  # MEASURED: killpg(0) on a zombie-only group raises EPERM on
        # macOS — non-ESRCH outcomes all defer to the ps corroboration.
    try:
        done = subprocess.run(["ps", "-eo", "pgid=,stat="],
                              capture_output=True, text=True, timeout=5)
        if done.returncode != 0:
            return True  # unknown evidence is alive for exclusion
        for line in done.stdout.splitlines():
            parts = line.split(None, 1)
            if (len(parts) == 2 and parts[0].strip() == str(pgid)
                    and not parts[1].strip().startswith("Z")):
                return True
        return False
    except Exception:
        return True


PROCENV_PATH = Path(__file__).resolve().with_name("procenv.py")


def _load_sibling(name: str, path: Path):
    """Import a sibling module BY PATH — server.py itself is loaded by path in
    tests and by the plugin runtime, so a package-relative import is not
    available; the file next to this one is the only stable reference."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_procenv = _load_sibling("codex_oracle_procenv", PROCENV_PATH)
TREEDIGEST_PATH = Path(__file__).resolve().with_name("treedigest.py")
_treedigest = _load_sibling("codex_oracle_treedigest", TREEDIGEST_PATH)
RUN_MARKER_ENV = _procenv.RUN_MARKER_ENV  # ONE definition, shared with the watchdog CLI


def _proc_env_has_marker(pid: int, run_tag: str) -> bool | None:
    """EXACT env verification: does pid's ENVIRONMENT (not argv) contain
    CODEX_ORACLE_RUN=<tag>? `ps -E` mixes argv and env in one string, so a
    process whose ARGV merely mentions the marker (an operator's grep, a
    decoy) classified as marked and was SIGKILLed (round 29). The single
    implementation lives in procenv.py (macOS KERN_PROCARGS2 — MEASURED
    both directions; Linux /proc/<pid>/environ) so the no-server watchdog
    runs the very same check from /bin/sh (round 30). None = unverifiable
    (counts as a survivor for DETECTION, is never a KILL target)."""
    return _procenv.proc_env_has_marker(pid, run_tag)


def _marked_survivors(run_tag: str) -> list[int]:
    """Live pids still carrying this run's spawn-marker env var. Codex
    0.151.0's shell tools call setsid() (upstream source, round 16) — they
    leave our process group, so pgid checks cannot see them; the inherited
    ENVIRONMENT survives both setsid and fd-closing, and procenv.py reads it
    EXACTLY — macOS: KERN_PROCARGS2 per pid; Linux: a /proc scan (`ps -E` is
    BSD-only and never existed on procps-ng — round 31). Same-user scope;
    zombies excluded; RAISES when enumeration fails so callers fail closed.

    TRUST MODEL (round 17, explicit): the marker is COOPERATIVE containment.
    It reaches everything codex 0.151.0 itself leaves behind — same-group
    leaks and its setsid() shell tools, which inherit the environment. A
    descendant that deliberately SCRUBS its environment (env={} +
    close_fds + new session) escapes every userspace channel at once; no
    in-process mechanism can hold it (that would take kernel custody:
    launchd or cgroup supervision). For read runs that residual is bounded
    by codex's own OS sandbox, which descendants inherit; infra and abraham
    runs have no sandbox, so there it is not bounded. It is named here so
    it can never be mistaken for covered."""
    if os.name == "nt" or not run_tag:
        return []
    return list(_procenv.marked_pids(run_tag))  # OSError propagates: fail closed


def _kill_marked(run_tag: str, pids: list[int]) -> None:
    """SIGKILL marker-identified processes after a FRESH marker re-scan
    (round 17): a pid from an older snapshot whose process exited (and was
    reused) is never signalled. This NARROWS the reuse window to the
    scan→signal gap — it does not close it (round 18): pid-based signalling
    has no atomic identity on POSIX; only a supervisor holding process
    handles could close it.
    """
    if not pids:
        return
    try:
        fresh = set(_marked_survivors(run_tag))
    except Exception:
        return  # cannot revalidate → kill nothing (the caller re-scans and fails closed)
    for pid in pids:
        # KILL only on POSITIVE env verification at signal time (round 29):
        # an unverifiable candidate stays a detected survivor but is never
        # a kill target — misidentified kills are worse than over-refusal.
        if pid in fresh and _proc_env_has_marker(pid, run_tag) is True:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


def _writer_alive(pid: int, start: str, pgid: int, spool: Path) -> bool:
    """Is the run's WRITER still running? The recorded pid may be a LAUNCHER
    (legacy shim-spawned records — round 12): when it is dead but its process
    group still has members AND the spool is being written, the real writer
    lives. A stale spool (>120s) with only group members left reads dead —
    pgid recycling must not park a collector forever."""
    if _pid_alive(pid, start):
        return True
    if not _pgid_alive(pgid):
        return False
    with contextlib.suppress(OSError):
        return time.time() - spool.stat().st_mtime < 120
    return False


def _is_detached(rec: dict[str, Any]) -> bool:
    """A run is DETACHED when it has no end record and either its cleanup
    journaled the detach, or the server that spawned it is gone — the
    latter needs no cooperation from a shutdown that may have been torn."""
    if rec.get("has_end") or not rec.get("has_spawn"):
        return False
    if rec.get("write"):
        # A write child that outlived a crashed server is ORPHANED, never
        # adoptable: the one-writer contract has no owner to enforce it.
        return False
    if not rec.get("watchdog_pid"):
        # No deadline enforcer was ever spawned (Windows, spawn failure):
        # a survivor is unbounded — ORPHANED, to be stopped, not adopted.
        return False
    if _pid_alive(int(rec.get("pid") or 0), str(rec.get("pid_start") or "")) and not _pid_alive(
            int(rec.get("watchdog_pid") or 0), str(rec.get("watchdog_start") or ""), strict=True):
        # The child lives but its enforcer is gone or is not the recorded
        # process: unbounded — ORPHANED (round 3: a bare nonzero pid was
        # accepted as an enforcer).
        return False
    if rec.get("has_detached"):
        return True
    owner = int(rec.get("server_pid") or 0)
    # No self-pid shortcut (round 11): _server_alive itself verifies the
    # start token even for our own pid, so an OLD record whose owner pid was
    # REUSED by this very server still reads detached.
    return bool(owner) and not _server_alive(owner, str(rec.get("server_start") or ""))


def _is_orphaned_write(rec: dict[str, Any]) -> bool:
    if rec.get("has_end") or not rec.get("write") or not rec.get("has_spawn"):
        return False
    owner = int(rec.get("server_pid") or 0)
    return (
        _pid_alive(int(rec.get("pid") or 0), str(rec.get("pid_start") or ""))
        and bool(owner)
        and not _server_alive(owner, str(rec.get("server_start") or ""))
    )
RESUME_NUDGE = (
    "The previous process was interrupted before your answer arrived. "
    "Continue from where you left off and provide the complete final answer."
)

IMAGES_NUDGE = (
    "Images are attached to this turn. Look at each one, then continue from "
    "where you left off with them in mind."
)


_journal_lock = threading.Lock()


def _journal(rec: dict[str, Any]) -> bool:
    """Append one record to runs.jsonl, flushed so a kill cannot lose it.
    Returns False when the append could not be made durable — callers that
    PUBLISH state others act on (the spawn record) must fail closed on it.
    Serialized: journal writes also come from worker threads, and two
    unlocked rotations could clobber runs.jsonl.1."""
    try:
        with _journal_lock:
            LIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
            _private(LIVE_LOG_DIR, 0o700)
            with contextlib.suppress(OSError):
                if RUNS_JOURNAL.exists() and RUNS_JOURNAL.stat().st_size > RUNS_JOURNAL_MAX_BYTES:
                    RUNS_JOURNAL.replace(RUNS_JOURNAL.with_suffix(".jsonl.1"))
            with RUNS_JOURNAL.open("a", encoding="utf-8") as fh:
                _private(RUNS_JOURNAL, 0o600)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True
    except Exception:
        return False


def _journal_runs() -> dict[str, dict[str, Any]]:
    """Fold journal records into per-run views, oldest→newest insertion order."""
    runs: dict[str, dict[str, Any]] = {}
    for path in (RUNS_JOURNAL.with_suffix(".jsonl.1"), RUNS_JOURNAL):
        if not path.exists():
            continue
        with contextlib.suppress(Exception):
            for line in path.read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(Exception):
                    rec = json.loads(line)
                    run = runs.setdefault(rec["run"], {})
                    if run.get("has_end") and rec.get("phase") != "end" and "status" in rec:
                        # A terminal status is IMMUTABLE to non-terminal
                        # records (round 9: a late `detached` append from a
                        # slow shutdown overwrote `cancelled` in the fold).
                        rec = {k: v for k, v in rec.items() if k != "status"}
                    run.update({k: v for k, v in rec.items() if k != "phase"})
                    run[f"has_{rec.get('phase', '?')}"] = True
                    # `ts` is the LAST record's; keep the first and last
                    # explicitly so elapsed time can be computed.
                    run.setdefault("first_ts", rec.get("ts"))
                    run["last_ts"] = rec.get("ts")
    return runs


# Two transient classes, two responses. OVERLOAD = the provider is shedding
# load (capacity, 429, 503): an instant retry lands on the same shed, so wait
# with backoff, then resume. DISCONNECT = an infrastructure blip (dropped
# stream, reset, 5xx other than 503): resume right away. Neither includes
# auth (needs a human), usage/quota policy denials, or argument errors —
# retrying cannot fix those. The first entry is the rendered text of
# CodexErr::ServerOverloaded: after a codex update, check it against the
# installed release's source (`grep -n "at capacity" codex-rs/protocol/src/error.rs`
# in the export scripts/codex_src.py keeps at ~/Documents/codex-installed).
_OVERLOAD_SIGNALS = (
    "at capacity",                     # ServerOverloaded (terminal in codex)
    "server_is_overloaded",            # the 503 error codes behind it
    "slow_down",
    "experiencing high demand",        # InternalServerError
    " 503",                            # RetryLimit "last status: 503 …"
    "service unavailable",
    "overloaded",
    "temporarily unavailable",
    " 429",
    "rate limit",                      # RateLimitExceeded "rate limit exceeded: …"
    "too many requests",
    "retry later",
)
_DISCONNECT_SIGNALS = (
    "stream disconnected",             # Stream
    "connection failed",               # ConnectionFailed "Connection failed: …"
    "error while reading the server response",  # ResponseStreamFailed
    "exceeded retry limit",            # RetryLimit (non-503 statuses)
    "agent loop died",                 # InternalAgentDied
    "request timed out",               # RequestTimeout
    "timeout waiting for child process",  # Timeout
    "stream closed",
    "connection reset",
    "connection closed",
    "connection refused",
    "connection error",
    "network error",
    "timed out",
    "timeout",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "error 500",
    "error 502",
    "error 504",
)


def _transient_class(text: str) -> str | None:
    """'overload' | 'disconnect' | None — see the signal tables above."""
    t = text.lower()
    if any(s in t for s in _OVERLOAD_SIGNALS):
        return "overload"
    if any(s in t for s in _DISCONNECT_SIGNALS):
        return "disconnect"
    return None


def _is_transient_error(text: str) -> bool:
    """Failures worth an automatic resume/retry: infrastructure, not semantics."""
    return _transient_class(text) is not None


def _retry_fits(request_started: float | None, wait: float) -> tuple[bool, float]:
    """Can a `wait` plus one more attempt fit in the request's remaining
    budget? (fits, seconds left). The attempt floor scales with the budget
    exactly as the pre-spawn refusal does."""
    if request_started is None:
        return True, float(MAX_RUNTIME_SECONDS)
    left = MAX_RUNTIME_SECONDS - (time.monotonic() - request_started)
    floor = min(MIN_ATTEMPT_SECONDS, 0.5 * MAX_RUNTIME_SECONDS)
    return (left - wait) >= floor, left


def _overload_backoff_seconds(retry_index: int) -> float:
    """Wait before overload retry #retry_index (0-based): base·2^i, capped."""
    if OVERLOAD_BACKOFF_BASE_SECONDS <= 0:
        return 0.0
    return min(
        OVERLOAD_BACKOFF_CAP_SECONDS,
        OVERLOAD_BACKOFF_BASE_SECONDS * (2 ** retry_index),
    )


def _build_exec_argv(
    model: str,
    reasoning: str,
    infra: bool,
    web_search: bool,
    output_file: Path,
    prompt: str | None = None,
    resume_tid: str | None = None,
    images: list[str] | None = None,
    write: bool = False,
    auto_compact_limit: int | None = None,
    role_config: dict | None = None,
) -> list[str]:
    """codex exec argv for a fresh run or a resume of an existing thread.

    Grammar per codex-rs exec/src/cli.rs @8e4b104 (verified live 0.144.1):
    ``--sandbox`` and ``-c`` overrides are PARENT options and must precede the
    ``resume`` subcommand token; ``--json``/``--model``/``--output-last-message``/
    ``--skip-git-repo-check`` are marked global. NO --ephemeral: persisting the
    rollout is what makes interrupted runs resumable.

    MCP isolation (default mode): ``--ignore-user-config``, NOT
    ``-c mcp_servers={}``. MEASURED 2026-08-08 on 0.144.1: ``-c mcp_servers={}``
    is a NO-OP — ``codex mcp list -c mcp_servers={}`` still shows every
    configured server, and default-mode runs emitted rmcp worker/auth-failure
    lines, i.e. codex was starting all ~12 user MCP servers on every
    supposedly-MCP-free call. ``--ignore-user-config`` drops
    ``$CODEX_HOME/config.toml`` entirely (auth still uses CODEX_HOME), so no
    user MCP servers start; the ``-c`` overrides and ``--model`` below are
    passed explicitly so they survive it, and CLAUDE.md project-doc discovery
    still works (all verified live). Infra mode deliberately KEEPS the user
    config so codex has its own MCP tools (auth0, temporal, ...) for live
    investigation.

    Images attach via ``-i`` whenever the caller passes them (vision verified
    live on 0.144.1), on a resume as well: 0.163.0 chains the parent's ``-i``
    list into the resumed turn's input (exec/src/lib.rs:937-941), which is how
    a follow-up turn in a continued advisory session sees a new screenshot. A
    recovery resume re-attaches surviving interrupted-turn images and adds
    the caller's new images. ``--image`` is VARIADIC
    (``<FILE>...``) — measured live: if the ``-i`` list sits immediately
    before the positional prompt, clap swallows the prompt as another image
    path and codex reports "No prompt provided via stdin". So images go RIGHT
    AFTER ``exec``, where the following flag terminates the value list and the
    prompt stays a clean trailing positional.

    ``write=True`` is the implementation phase, always with full access
    (FULL_ACCESS_WRITE_ARGS) since 2.0.0. ``auto_compact_limit`` emits
    ``model_auto_compact_token_limit`` — the caller passes it ONLY when the
    model's window is known (see the auto-compaction notes above
    AUTOCOMPACT_PCT: on 0.163.0 the value can only lower the vendor's 90%
    threshold, on older binaries it replaced it outright).
    """
    argv = [*_codex_argv0(), "exec"]
    for img in images or []:
        argv += ["-i", img]
    if write:
        # The implementation process (user rulings 2026-09-05 and 2026-10-18):
        # codex's own --dangerously-bypass-approvals-and-sandbox — no sandbox,
        # no approval prompts, network on, the user's own configuration and
        # MCP servers, the caller's full privileges — so the writer runs the
        # project's real gates against the real system (a sealed writer never
        # touched PostgreSQL, Temporal or a browser, and every real-system
        # defect surfaced one round later in the lead's rig). The git
        # contract, the write lock and the changed-files report still apply.
        argv += [*FULL_ACCESS_WRITE_ARGS]
    elif infra:
        argv += ["--sandbox", "danger-full-access"]
    else:
        # Isolate: no user MCP servers, no user config bleed-through.
        argv += ["--sandbox", "read-only", "--ignore-user-config"]
    argv += [
        "--json",
        "--model", model,
        "--skip-git-repo-check",
        "--color", "never",
        "--output-last-message", str(output_file),
        "-c", "approval_policy=never",
        "-c", f"model_reasoning_effort={reasoning}",
        "-c", "model_reasoning_summary=detailed",
        # NO CLAUDE.md project-doc pin: that file carries LIVE SECRETS and the
        # advisor is an external provider. (Measured twice on 0.144.1: codex
        # reads CLAUDE.md natively regardless — neither project_doc_max_bytes=0
        # nor a fallback-list override suppresses it. That exposure is
        # pre-existing and outside this wrapper; we simply never add to it.
        # We inject only the curated ADVISOR_CONTEXT.md ourselves.)
        "-c", f"web_search={'live' if web_search else 'disabled'}",
    ]
    if auto_compact_limit is not None:
        argv += ["-c", f"model_auto_compact_token_limit={auto_compact_limit}"]
    if role_config:
        from coordinator.native import override_args
        argv += override_args(role_config)
    if resume_tid is not None:
        # The caller's nudge rides in ``prompt`` — same slot as a fresh run.
        # (A separate resume_prompt param existed here that no caller ever
        # passed, so every resume silently sent the generic nudge and DROPPED
        # the caller's added instructions. One prompt slot, no shadow.)
        argv += ["resume", resume_tid, prompt or RESUME_NUDGE]
    else:
        # Images already attached right after `exec` (see docstring — the
        # variadic -i must not sit next to the positional prompt).
        argv.append(prompt if prompt is not None else "")
    return argv



_NATIVE_CODEX_CACHE: dict[tuple[str, float], str] = {}


def _codex_target(platform_key: str | None = None,
                  machine: str | None = None) -> tuple[str, str]:
    """(platform package name, target triple), mirroring the npm shim's
    PLATFORM_PACKAGE_BY_TARGET (read from the installed 0.151.0 codex.js).
    ("", "") = unsupported platform — no native resolution. Parameters
    default to THIS machine and exist so every mapping is testable from any
    platform (round 14: the Windows branch was untestable and kept a
    wildcard glob)."""
    import platform as _platform
    plat = platform_key if platform_key is not None else (
        "win32" if os.name == "nt" else sys.platform)
    mach = (machine if machine is not None else _platform.machine()).lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "aarch64", "aarch64": "aarch64"}.get(mach, "")
    if not arch:
        return "", ""
    npm_arch = "arm64" if arch == "aarch64" else "x64"
    if plat == "darwin":
        return f"codex-darwin-{npm_arch}", f"{arch}-apple-darwin"
    if plat.startswith("linux"):
        return f"codex-linux-{npm_arch}", f"{arch}-unknown-linux-musl"
    if plat == "win32":
        return f"codex-win32-{npm_arch}", f"{arch}-pc-windows-msvc"
    return "", ""


def _is_launcher_script(argv0: str) -> bool:
    """True when argv0 (resolved via the CHILD env's PATH for a bare name —
    the spawn resolves there, not in the parent's; round 13) is a #! script —
    a LAUNCHER whose real writer would be a child our descriptors and pid
    records never reach. Unreadable = True (fail closed for write gating)."""
    path = argv0 if os.sep in argv0 else (
        shutil.which(argv0, path=_codex_env().get("PATH")) or "")
    if not path:
        return False  # the spawn will fail loudly on its own
    if not os.path.isabs(path):
        path = os.path.abspath(path)  # inspect the same file the spawn runs
    try:
        with open(os.path.realpath(path), "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return True


def _native_codex_from_launcher(path: str) -> str:
    """The vendored NATIVE codex binary behind an npm launcher script, or ''
    when `path` is already a real executable or nothing better is found.

    WHY (round 12, MEASURED on codex-cli 0.151.0): the npm `codex` is a node
    script that spawns the native binary with stdio:"inherit" — Node passes
    only fds 0-2 to the child, so the pass_fds lock/claim descriptors NEVER
    reach the process that actually writes, and every recorded pid is the
    LAUNCHER's: if node dies, the kernel releases the locks while the native
    child keeps writing. Launching the native binary directly makes the
    recorded pid, the inherited descriptors, the watchdog and the kill
    target all refer to the real writer. Layout measured in the installed
    package: <pkg>/node_modules/@openai/codex-<plat>/vendor/<triple>/bin/codex
    (fallback <pkg>/vendor/<triple>/bin/codex, the shim's own fallback)."""
    try:
        real = os.path.realpath(path)
        st_mtime = os.stat(real).st_mtime
    except OSError:
        return ""
    key = (real, st_mtime)
    if key in _NATIVE_CODEX_CACHE:
        return _NATIVE_CODEX_CACHE[key]
    result = ""
    try:
        with open(real, "rb") as fh:
            is_script = fh.read(2) == b"#!"
    except OSError:
        is_script = False
    if is_script:
        pkg = Path(real).parent.parent  # <pkg>/bin/codex.js → <pkg>
        exe = "codex.exe" if os.name == "nt" else "codex"
        pkg_name, triple = _codex_target()
        candidates = []
        if pkg_name:
            # EXACT paths for THIS machine's target only — a glob picked the
            # lexicographically first package, which on a multi-architecture
            # install can be the wrong binary (round 13). Unknown platform =
            # no resolution = write runs refuse behind the launcher.
            candidates = [
                pkg / "node_modules" / "@openai" / pkg_name / "vendor" / triple / "bin" / exe,
                pkg / "vendor" / triple / "bin" / exe,  # the shim's own fallback
            ]
        for cand in candidates:
            with contextlib.suppress(OSError):
                if cand.is_file() and os.access(cand, os.X_OK):
                    with open(cand, "rb") as fh:
                        if fh.read(2) != b"#!":
                            result = str(cand)
                            break
    _NATIVE_CODEX_CACHE[key] = result
    return result


def _codex_argv0() -> list[str]:
    """Executable prefix for codex — usually one element, ["node", codex.js] as
    a fallback.

    POSIX: the bare name resolves via PATH. Windows: npm installs only
    .cmd/.ps1 shims, and CreateProcess cannot resolve a bare "codex" to a
    .cmd (WinError 2, measured on codex-cli 0.147.0) — so resolve the shim
    via shutil.which (PATHEXT-aware) and prefer the vendored native
    codex.exe next to it, which avoids routing untrusted prompt text through
    cmd.exe. Fallbacks: node + codex.js, then the full-path .cmd shim
    (spawnable when given a full path — measured).
    """
    override = os.environ.get("CODEX_ORACLE_CODEX_BIN", "").strip()
    if override:
        # ONE canonical absolute path before inspection or spawn (round 14:
        # a relative override was inspected against the server cwd but
        # spawned under the run's workdir — two different files). A bare
        # name resolves in the child env's PATH, like everything else. The
        # launcher resolution still applies; a native pin passes through.
        if os.sep in override or (os.altsep and os.altsep in override):
            override = os.path.abspath(override)
        else:
            resolved = shutil.which(override, path=_codex_env().get("PATH"))
            # ABSOLUTE always (round 15): which() through a RELATIVE PATH
            # entry returns a relative path, which the spawn would resolve
            # under the run's workdir — a different file.
            override = os.path.abspath(resolved) if resolved else override
        return [_native_codex_from_launcher(override) or override]
    if os.name != "nt":
        # Resolve against the CHILD env's PATH (round 13: the spawn env
        # prepends /opt/homebrew/bin — a parent PATH without it made the
        # probe see nothing while the child still found the JS launcher),
        # and return the ABSOLUTE path so probe and spawn cannot diverge.
        shim = shutil.which("codex", path=_codex_env().get("PATH"))
        if shim:
            shim = os.path.abspath(shim)  # a relative PATH entry yields a relative which()
            return [_native_codex_from_launcher(shim) or shim]
        return ["codex"]
    shim = shutil.which("codex")
    if not shim:
        return ["codex"]  # spawn fails; the caller reports the install hint
    shim = os.path.abspath(shim)  # a relative PATH entry yields a relative which()
    pkg = Path(shim).parent / "node_modules" / "@openai" / "codex"
    pkg_name, triple = _codex_target()
    if pkg_name:
        # EXACT target only (round 14): the win32 wildcard glob picked the
        # first architecture alphabetically on a multi-arch install.
        exe = pkg / "node_modules" / "@openai" / pkg_name / "vendor" / triple / "bin" / "codex.exe"
        if exe.is_file():
            return [str(exe)]
    js = pkg / "bin" / "codex.js"
    node = shutil.which("node")
    if js.is_file() and node:
        return [os.path.abspath(node), str(js)]
    return [shim]


def _codex_env() -> dict[str, str]:
    """Subprocess env. On macOS ensure Homebrew's bin dir is on PATH; on
    other platforms the parent PATH passes through untouched (the previous
    unconditional '/opt/homebrew/bin:' prefix corrupted the first PATH entry
    on Windows, where the separator is ';')."""
    from coordinator.native import environment
    env = environment()
    if sys.platform == "darwin":
        env["PATH"] = "/opt/homebrew/bin" + os.pathsep + env.get("PATH", "")
    return env


def _inherit_lock_kwargs(state: dict[str, Any], workdir: str) -> dict[str, Any]:
    """Descriptors the codex child must keep alive: the tree's write lock for
    write runs, and the RUN CLAIM for a continuation (codex_resume_run) — so
    a detached continuation still excludes a second resume of the same thread
    after its collector is gone (review round 4). POSIX only."""
    if os.name == "nt":
        return {}
    fds = []
    if state.get("write") or state.get("custody_cwd"):
        # Lock OWNERSHIP, not write mode (round 18): abraham's phase-1 READ
        # run executes under the caller-held tree lock — its child must
        # inherit the descriptor exactly like a write child, so a detached
        # phase 1 keeps the tree locked after this server (and abraham's
        # finally) are gone.
        fd = _held_lock_fd(_write_lock_path(
            str(state.get("custody_cwd") or "") or workdir or _get_cwd()))
        if fd is not None:
            fds.append(fd)
    key = str(state.get("claim_key") or "")
    if key:
        fd = _held_lock_fd(_run_claim_path(key))
        if fd is not None:
            fds.append(fd)
    return {"pass_fds": tuple(fds)} if fds else {}


def _new_group_kwargs() -> dict[str, Any]:
    """Spawn kwargs that put the child in its own process group, so the whole
    tree can be reaped later (see _kill_tree)."""
    if os.name != "nt":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


def _kill_tree(proc) -> None:
    """Kill the subprocess AND everything it spawned.

    `codex` on POSIX is a node SHIM that execs the vendored native binary as a
    GRANDCHILD. proc.kill() reaps only the shim: the real codex survives with
    ppid=1, keeps burning tokens, and — measured 2026-08-09 — keeps holding
    the thread-store writer lock, so a later resume dies with
    "thread <id> already has an active writer". Orphans up to EIGHT DAYS old
    were found this way. Because we spawn with start_new_session=True, the
    child leads its own process GROUP, so one killpg takes the whole tree.
    """
    if os.name != "nt":
        try:
            pgid = os.getpgid(proc.pid)
            # Refuse to killpg our OWN group: if a refactor ever drops the
            # start_new_session spawn kwarg, the child shares this server's
            # group and killpg would take down the MCP server and every
            # sibling run with it. Fall through to the single-process kill.
            if pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGKILL)
                return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # group already gone, or we can't signal it — fall through
    else:
        # Windows: no process groups to signal; taskkill /T walks the tree.
        # Only a rc=0 taskkill actually killed anything — access-denied and
        # not-found exit nonzero WITHOUT raising, and returning then left
        # the root alive (round-3 review, 2026-08-21). Fall through to
        # proc.kill() on any other outcome.
        with contextlib.suppress(Exception):
            done = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10)
            if done.returncode == 0:
                return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


async def _exec_codex_once(
    cmd: list[str],
    output_file: Path,
    state: dict[str, str],
    emit,
    ctx: Context | None,
    model: str,
    extra_env: dict[str, str] | None = None,
    workdir: str = "",
    request_started: float | None = None,
) -> tuple[str, bool, str, str, int, str | None, bool]:
    """One codex exec attempt (spawn → stream → reap).

    Returns ``(final_message, clean_extraction, stdout_text, stderr_text,
    returncode, hung_reason, timed_out)``. The final message is read from
    ``output_file`` (deleted here), falling back to the last parsed
    agent_message. Live-log handles stay OPEN — the orchestrator owns them
    across retry attempts.
    """
    # FILE-BACKED STDIO (see RUN SURVIVABILITY): the child never holds a
    # pipe to this process, so it survives us; we TAIL its spool files.
    stdout_path = output_file.with_name(output_file.stem + ".stdout.jsonl")
    stderr_path = output_file.with_name(output_file.stem + ".stderr.log")
    try:
        out_fh = open(stdout_path, "ab")
    except OSError as e:
        return (
            "", False, "",
            f"cannot open run spool files under {output_file.parent}: {e}",
            1, None, False,
        )
    try:
        err_fh = open(stderr_path, "ab")
    except OSError as e:
        out_fh.close()
        return (
            "", False, "",
            f"cannot open run spool files under {output_file.parent}: {e}",
            1, None, False,
        )
    _private(stdout_path, 0o600)
    _private(stderr_path, 0o600)
    # REQUEST-LEVEL BUDGET (round 32): MAX_RUNTIME_SECONDS is ONE budget per
    # MCP request — retries and abraham's two phases share it (each attempt
    # used to receive a fresh budget, so a request could outlive the client's
    # hard per-call timeout). An attempt that cannot get MIN_ATTEMPT_SECONDS
    # of what is left is refused as a timeout instead of being spawned.
    def _left() -> float:
        # the REQUEST's remaining budget read at the MOMENT OF USE (round 33:
        # a duration computed before spawn/setup added that time back)
        if request_started is None:
            return float(MAX_RUNTIME_SECONDS)
        return max(0.0, MAX_RUNTIME_SECONDS - (time.monotonic() - request_started))

    request_elapsed = (0.0 if request_started is None
                       else max(0.0, time.monotonic() - request_started))
    attempt_budget = _left()
    # the floor never exceeds half the configured budget (a 3 s test budget
    # must still spawn its first attempt; a real budget is ≥ 300 s)
    if attempt_budget < min(MIN_ATTEMPT_SECONDS, 0.5 * MAX_RUNTIME_SECONDS):
        emit(f"■ request budget exhausted before this attempt "
             f"({int(request_elapsed)}s of {MAX_RUNTIME_SECONDS}s used, "
             f"CODEX_ORACLE_MAX_RUNTIME_S) — not spawning")
        out_fh.close()
        err_fh.close()
        return "", False, "", "", -1, "request budget exhausted before the attempt", True
    # EXECUTION BARRIER for write runs (round 7, POSIX): the child is spawned
    # reading a pipe and execs codex only after this server releases it — so
    # codex cannot run before its pid is journaled and every legacy bridge
    # file durably names it. If this server dies first, the pipe closes, the
    # read fails, and the child exits 97: fail closed, never an unpublished
    # writer racing a bridge that still names a dead server.
    barrier = bool(state.get("write") or state.get("custody_cwd")) and os.name != "nt"
    if barrier:
        cmd = ["/bin/sh", "-c", 'read _ok || exit 97; exec "$0" "$@" </dev/null',
               cmd[0], *cmd[1:]]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=(asyncio.subprocess.PIPE if barrier else asyncio.subprocess.DEVNULL),
            stdout=out_fh,
            stderr=err_fh,
            cwd=workdir or _get_cwd(),
            env={**_codex_env(), **(extra_env or {}),
                 # Round 16: descendants that setsid() out of our group are
                 # findable/killable only by this inherited marker.
                 RUN_MARKER_ENV: str(state.get("run_tag") or "")},
            # Own process group so _kill_tree reaps the node shim AND the
            # vendored codex grandchild together (POSIX: setsid + killpg;
            # Windows: its own group, reaped via taskkill /T).
            **_new_group_kwargs(),
            # A write run's codex INHERITS the tree's lock descriptor: the
            # kernel keeps the tree locked while the child lives, even if
            # this server dies (POSIX; Windows region locks do not inherit).
            **_inherit_lock_kwargs(state, workdir),
        )
    except FileNotFoundError:
        return (
            "", False, "",
            "codex binary not found in PATH. Install with: npm i -g @openai/codex",
            127, None, False,
        )
    except OSError as e:
        # PermissionError, ENOEXEC, descriptor exhaustion, ... (round 9): a
        # spawn failure must become a durable terminal error, never an
        # exception that unwinds past the end path with the claims held.
        return (
            "", False, "",
            f"could not spawn codex ({type(e).__name__}: {e}) — nothing was executed",
            1, None, False,
        )
    finally:
        out_fh.close()
        err_fh.close()

    pgid = proc.pid
    if os.name != "nt":
        with contextlib.suppress(OSError):
            pgid = os.getpgid(proc.pid)
    spawn_ts = time.time()
    attempt_budget = _left()  # re-read AFTER the spawn (round 33)
    deadline_ts = spawn_ts + attempt_budget
    leader_start = _proc_start(proc.pid)  # captured BEFORE the watchdog exists:
    # it anchors every group kill to THIS leader's identity (round 21).
    # TICK BOUND FROM THE PARENT (round 36): the enforcer's own wall clock
    # can be rolled back, so the tick count is derived HERE from the
    # monotonic attempt budget (one tick per 5 s poll, +2 slack) and passed
    # in; the child never derives it from `date`.
    max_ticks = int(attempt_budget // 5) + 2
    watchdog = _spawn_watchdog(proc.pid, pgid, deadline_ts,
                               str(state.get("run_tag") or ""), leader_start,
                               max_ticks=max_ticks)
    watchdog_start = _proc_start(watchdog.pid) if watchdog is not None else ""
    if watchdog is not None and not watchdog_start:
        # An enforcer whose identity cannot be recorded cannot be verified
        # later: treat as absent (kill on shutdown, never detach).
        with contextlib.suppress(Exception):
            watchdog.terminate()
        watchdog = None
    watchdog_pid = watchdog.pid if watchdog is not None else None
    state["spawn"] = {
        "pid": proc.pid, "pgid": pgid, "watchdog_pid": watchdog_pid,
        "server_pid": os.getpid(), "server_start": _SERVER_START,
        "pid_start": leader_start, "watchdog_start": watchdog_start,
        "stdout": str(stdout_path), "stderr": str(stderr_path),
        "output_file": str(output_file),
        "spawn_ts": spawn_ts, "deadline_ts": deadline_ts, "max_ticks": max_ticks,
    }
    state.pop("detached", None)
    if (state.get("write") or state.get("custody_cwd")) and not _note_write_child(
            str(state.get("custody_cwd") or "") or workdir or _get_cwd(), proc.pid):
        # Either the tree's write lock is no longer ours, or a legacy bridge
        # file could not be durably repointed at the child (round 7): both
        # leave a window for a second writer, so nothing may execute.
        _kill_tree(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        return (
            "", False, "",
            "write publication failed (lock no longer ours, or a legacy "
            "bridge file could not be repointed) — refusing to write; "
            "nothing was executed",
            1, None, False,
        )
    if watchdog is None:
        emit("⚠ no runtime watchdog (Windows, or spawn failed): this run will "
             "be KILLED, not detached, if the server shuts down")
    # PUBLISH the pid (the emit journals the spawn record) BEFORE checking
    # for a cancel intent: a canceller that reads the journal after this
    # point sees the pid and takes the kill path; one that wrote its intent
    # before this point is caught by the check below. No window either way
    # (review round 4).
    emit(f"▶ codex pid {proc.pid} (pgid {pgid}) spool={output_file.parent}")
    if state.pop("publish_failed", False):
        # Nobody can see this child (cancel, status, adoption all read the
        # journal): an unrecorded run must not run.
        _kill_tree(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        return ("", False, "", "could not record the spawn in the run journal — refusing to run unrecorded",
                1, None, False)
    if state.get("run_tag") and _cancel_requested(str(state["run_tag"])):
        _kill_tree(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        return ("", False, "", "cancelled at spawn (codex_cancel_run)", -9, None, False)
    if barrier:
        # Publication complete (spawn journaled, bridge repointed, no cancel):
        # release the execution barrier so the child execs codex.
        try:
            proc.stdin.write(b"go\n")
            await proc.stdin.drain()
        except Exception:
            _kill_tree(proc)
            with contextlib.suppress(Exception):
                await proc.wait()
            return ("", False, "",
                    "could not release the write execution barrier — nothing was executed",
                    1, None, False)
        with contextlib.suppress(Exception):
            proc.stdin.close()

    startup_seen = asyncio.Event()
    exited = asyncio.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    captured = {"stdout": 0, "stderr": 0}

    def _keep(buffer: list[bytes], chunk: bytes, key: str) -> None:
        """In-memory capture is a bounded TAIL (the spool on disk is the full
        record): oldest chunks are dropped past CAPTURE_MAX_BYTES."""
        buffer.append(chunk)
        captured[key] += len(chunk)
        while captured[key] > CAPTURE_MAX_BYTES and len(buffer) > 1:
            captured[key] -= len(buffer.pop(0))
    hung_reason: str | None = None
    timed_out = False
    stdout_linebuf = bytearray()

    def _feed_stdout(chunk: bytes) -> None:
        _feed_jsonl(stdout_linebuf, chunk, state, emit)

    def _feed_stderr(chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="replace")
        for ln in text.splitlines():
            if ln.strip():
                emit(f"! {ln}")

    async def _reaper() -> None:
        with contextlib.suppress(Exception):
            await proc.wait()
        exited.set()

    def _read_more(path: Path, pos: int) -> bytes:
        with contextlib.suppress(OSError):
            with open(path, "rb") as fh:
                fh.seek(pos)
                return fh.read(READ_CHUNK_SIZE)
        return b""

    async def _tail(path: Path, buffer: list[bytes], on_chunk) -> None:
        """Follow a spool file until the child has exited AND the file is
        drained. Fixed-size reads: a line of any length cannot overrun a
        buffer. The live view must never break the run: feeder errors are
        swallowed."""
        pos = 0
        while True:
            if state.get("fatal") and proc.returncode is None:
                # A publication failure (session journal, thread claim) makes
                # the run untrackable/unexcludable: fail closed, do not let
                # the child run on (round 6).
                state["last_error"] = str(state.get("last_error") or state["fatal"])
                _kill_tree(proc)
            chunk = _read_more(path, pos)
            if chunk:
                pos += len(chunk)
                if not startup_seen.is_set():
                    startup_seen.set()
                _keep(buffer, chunk, "stdout" if buffer is stdout_chunks else "stderr")
                with contextlib.suppress(Exception):
                    on_chunk(chunk)
                continue
            if exited.is_set():
                # Final drain: bytes written between our last read and exit.
                while True:
                    chunk = _read_more(path, pos)
                    if not chunk:
                        return
                    pos += len(chunk)
                    _keep(buffer, chunk, "stdout" if buffer is stdout_chunks else "stderr")
                    with contextlib.suppress(Exception):
                        on_chunk(chunk)
            await asyncio.sleep(TAIL_POLL_SECONDS)

    async def _startup_probe() -> None:
        """Wait up to STARTUP_PROBE_SECONDS for first output. Kill on silence."""
        nonlocal hung_reason
        try:
            await asyncio.wait_for(startup_seen.wait(), timeout=STARTUP_PROBE_SECONDS)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                hung_reason = (
                    f"no output within {STARTUP_PROBE_SECONDS}s of launch — "
                    "process never started producing events"
                )
                _kill_tree(proc)

    reaper_task = asyncio.create_task(_reaper())
    stdout_task = asyncio.create_task(_tail(stdout_path, stdout_chunks, _feed_stdout))
    stderr_task = asyncio.create_task(_tail(stderr_path, stderr_chunks, _feed_stderr))
    probe_task = asyncio.create_task(_startup_probe())
    run_t0 = time.monotonic()
    request_t0 = run_t0 if request_started is None else request_started
    heartbeat_task = (
        asyncio.create_task(
            _heartbeat_loop(ctx, request_t0, run_t0, state, model, emit)
        )
        if ctx is not None else None
    )

    async def _budget_sentinel() -> None:
        # OBSERVABLE BEFORE SATURATION (Limits Doctrine): the live log warns
        # at 80 % of the run budget so the first signal is never the kill.
        await asyncio.sleep(0.8 * attempt_budget)
        if proc.returncode is None:
            emit(f"⚠ run at {int(time.monotonic() - run_t0)}s = 80% of its "
                 f"{attempt_budget:.0f}s attempt budget (request budget "
                 f"{MAX_RUNTIME_SECONDS}s, CODEX_ORACLE_MAX_RUNTIME_S, "
                 f"{MAX_RUNTIME_SOURCE}); the watchdog kills it at the deadline")

    budget_task = asyncio.create_task(_budget_sentinel())

    try:
        # Wall-clock timeout prevents zombie processes that start but never
        # finish (observed: research calls stuck for 6+ hours at xhigh).
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task),
            timeout=_left(),
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
    except asyncio.CancelledError:
        if (_SHUTDOWN.is_set() and not state.get("write")
                and watchdog is not None and watchdog.poll() is None):
            # poll() verifies the enforcer is ALIVE (round 21: a crashed
            # watchdog left a non-null handle, detaching an unbounded child).
            # DETACH: the server is going down, not the caller's interest.
            # Only with a watchdog alive to enforce the deadline; otherwise
            # a detached run would have no bound at all (review of 1.17.0).
            # codex keeps running on its spool files; the watchdog keeps the
            # deadline; codex_resume_run collects the answer later.
            state["detached"] = True
        else:
            _kill_tree(proc)
            with contextlib.suppress(Exception):
                await proc.wait()
        raise
    finally:
        detached = bool(state.get("detached"))
        # Always ensure the process is reaped (unless deliberately detached)
        # and the watcher tasks are cancelled.
        if not detached:
            if proc.returncode is None:
                _kill_tree(proc)
            with contextlib.suppress(Exception):
                await proc.wait()
            if os.name != "nt" and pgid:
                # MARKER QUIESCENCE (rounds 14+16+21): codex can leave
                # spawned processes running after its own exit — sweep by
                # MARKER-verified pids only. The leader was just REAPED, so
                # its pid (and, once the group empties, its pgid) is free
                # for reuse: a post-reap numeric killpg could SIGKILL an
                # unrelated group (POSIX reuse) — removed. `_pgid_alive`
                # stays as detection evidence: a group that still reads
                # alive after the marker sweep is either an unmarked leak or
                # a reused id, and both directions land in the nonterminal
                # survivor path (fail closed, operator resolves).
                try:
                    marked = _marked_survivors(str(state.get("run_tag") or ""))
                except Exception:
                    marked = [-1]  # unverifiable → fail closed below
                _kill_marked(str(state.get("run_tag") or ""), [p for p in marked if p > 0])
                sweep_end = time.monotonic() + 3.0
                while time.monotonic() < sweep_end:
                    still_marked: list[int] = []
                    if marked:
                        try:
                            still_marked = _marked_survivors(str(state.get("run_tag") or ""))
                        except Exception:
                            still_marked = [-1]
                    if not _pgid_alive(pgid) and not still_marked:
                        marked = []
                        break
                    marked = still_marked or marked
                    await asyncio.sleep(0.1)
                if _pgid_alive(pgid) or marked:
                    state["group_survivors"] = pgid
                    state["marked_survivors"] = [p for p in marked if p > 0]
            if watchdog is not None:
                # Reap it too — a signalled-but-unwaited child is a zombie
                # that still answers kill(0) (measured in the detach suite).
                with contextlib.suppress(Exception):
                    watchdog.terminate()
                    watchdog.wait(timeout=3)
        for _task in (probe_task, heartbeat_task, reaper_task, budget_task):
            if _task is not None:
                _task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await _task

    stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()

    final_message = ""
    clean_extraction = False
    with contextlib.suppress(OSError):
        if output_file.exists() and output_file.stat().st_size > 0:
            final_message = output_file.read_text(encoding="utf-8", errors="replace").strip()
            clean_extraction = bool(final_message)
    # The spool (stdout/stderr/answer) is KEPT as evidence and for adoption;
    # _prune_live_logs retires it with the run logs.

    if not final_message and not timed_out:
        # stdout is a JSONL event stream — the parsed last agent message is
        # the meaningful fallback; raw stdout only as a last resort, and
        # NEVER for a process killed by a signal (codex_cancel_run / the
        # watchdog): a JSONL fragment is not an answer, and the caller must
        # see the kill, not a status:error header over event soup.
        raw_ok = (
            (proc.returncode or 0) == 0
            and stdout_text
            and not stdout_text.lstrip().startswith("{")  # JSONL is never an answer
        )
        final_message = state["last_message"] or (stdout_text if raw_ok else "")

    return (
        final_message, clean_extraction, stdout_text, stderr_text,
        proc.returncode or 0, hung_reason, timed_out,
    )


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------

async def _heartbeat_loop(
    ctx: Any,
    request_t0: float,
    run_t0: float,
    state: dict[str, Any],
    model: str,
    emit: Any,
) -> None:
    """Emit MCP progress while codex runs — module-level for testability.

    The STOP deadline is measured from REQUEST start (``request_t0``), not
    this run's start: one MCP request can span a capability probe, two
    abraham phases, and retries, and the client's progress token dies ~120s
    after the REQUEST began — a fresh per-run clock resurrected dead-token
    sends in later phases (round-2 review, 2026-08-21). Display elapsed
    stays run-relative for the operator. A failed notification must never
    affect the run itself.
    """
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
        now = time.monotonic()
        if now - request_t0 > PROGRESS_MAX_SECONDS:
            # Approaching the client's ~120s backgrounding threshold for
            # the REQUEST: stop while the progress token is still alive
            # (sending on a deregistered token killed the server on macOS
            # 2026-08-09 and wedged stdio on Windows 2026-08-21). The live
            # log continues to carry every event.
            emit(
                f"⏱ progress notifications stopped "
                f"{int(now - request_t0)}s into the request "
                f"(before the client backgrounds it; live log continues)"
            )
            return
        run_elapsed = now - run_t0
        with contextlib.suppress(Exception):
            # Bounds THIS task's await so a blocking transport cannot
            # wedge it (Windows, 2026-08-21). This is not a transport
            # flush guarantee — staying under the request deadline with
            # margin is what keeps sends off dead tokens.
            await asyncio.wait_for(
                ctx.report_progress(
                    min(run_elapsed, MAX_RUNTIME_SECONDS),
                    MAX_RUNTIME_SECONDS,
                    f"codex {model} · {int(run_elapsed)}s · "
                    f"{state['activity'][:140]}",
                ),
                timeout=PROGRESS_INTERVAL_SECONDS,
            )


async def _wait_for_capacity(
    seconds: float,
    ctx: Any,
    request_t0: float,
    run_t0: float,
    state: dict[str, Any],
    model: str,
    emit: Any,
    run_tag: str = "",
) -> bool:
    """Sit out a provider capacity shed WITHOUT going dark.

    The spinner keeps saying what the run is doing, and while the request's
    progress token is still alive heartbeats keep flowing — _heartbeat_loop
    owns that geometry (same envelope as a running attempt); this only
    brackets the sleep with it. Cancellation propagates to the caller, which
    journals it exactly like a cancel during an attempt.

    The sleep is sliced so a codex_cancel_run intent is honoured within ~1s
    instead of after up to the full backoff (round 6), and a liveness line
    lands in the live log every 30s so status probes (the resume guard's
    idle test, _active_write_run) keep seeing a run that is merely waiting.
    Returns True when a cancel intent arrived during the wait.
    """
    if seconds <= 0:
        return False
    state["activity"] = (
        f"provider at capacity — waiting {int(seconds)}s before resuming"
    )
    # Past PROGRESS_MAX_SECONDS the client has backgrounded the request and
    # its token is gone; starting the loop then would only log a spurious
    # "notifications stopped" line per wait. The live log carries the wait.
    token_alive = (time.monotonic() - request_t0) <= PROGRESS_MAX_SECONDS
    hb = (
        asyncio.create_task(
            _heartbeat_loop(ctx, request_t0, run_t0, state, model, emit)
        )
        if ctx is not None and token_alive else None
    )
    try:
        remaining = float(seconds)
        waited = 0.0
        next_note = 30.0
        while remaining > 0:
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            waited += step
            remaining -= step
            if run_tag and _cancel_requested(run_tag):
                emit("■ cancel intent found during the capacity wait — "
                     "honouring it before the next attempt")
                return True
            if remaining > 0 and waited >= next_note:
                next_note += 30.0
                emit(f"⏳ capacity wait: {int(remaining)}s remaining "
                     f"(a cancel intent is honoured within ~1s)")
    finally:
        if hb is not None:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
    return False


IMPLEMENTATION_INSTRUCTIONS = (
            "IMPLEMENTATION MODE — FULL ACCESS: this process runs WITHOUT a "
            "sandbox, with the caller's own privileges, network, live web "
            "search and MCP servers (--dangerously-bypass-approvals-and-"
            "sandbox). Read anything you need, anywhere, and reach the "
            "project's infrastructure the way its docs describe. Use that to "
            "VERIFY your work against the real system before you report: run "
            "what you changed for real (the app, command, endpoint or "
            "migration on a fresh database) and the project's own existing "
            "checks (builds, linters, whatever suites it already has) exactly "
            "as the project runs them, and report their real exit codes and "
            "output — never a claim of green without the run. Do not write "
            "test cases or test harnesses unless the user explicitly asked "
            "for them: verification is the real run and its results.\n"
            "BOUNDARIES: write inside this repository (the working directory "
            "and the repositories nested in it); a change another repository "
            "needs is its own run, so name it in your report instead of "
            "making it. Never modify a dotfile, a credential store or system "
            "configuration; no destructive infrastructure actions (dropping "
            "or truncating shared databases, deleting deployments or data) "
            "unless the task explicitly orders them; treat any secret you "
            "encounter as never-to-be-copied into files or output.\n"
            "GIT CONTRACT (non-negotiable): leave ALL changes as uncommitted "
            "working-tree edits for the caller to review. NEVER run git "
            "commit, push, checkout, switch, restore, reset, stash, clean, "
            "rebase, merge, or any branch/tag operation; never touch .git "
            "internals; no bulk deletes. Violations are detected after the "
            "run and reported to the caller.\n\n"
        )

INFRA_INSTRUCTIONS = (
            "INFRA INVESTIGATION MODE — you have full shell and network access "
            "plus your configured MCP tools. How to reach this project's "
            "infrastructure (servers, databases, clusters, dashboards, logs) is "
            "project-specific:\n"
            "1. FIRST follow any access instructions given in the task below.\n"
            "2. Otherwise DISCOVER them: read CLAUDE.md / AGENTS.md / README.md "
            "in the working directory AND each parent directory up to $HOME "
            "(workspace roots often document credentials that per-repo files "
            "deliberately omit).\n"
            "3. Also check standard local access: ~/.ssh/config, ~/.kube/config "
            "+ kubectl contexts, docker contexts, cloud CLI profiles (aws/gcloud), "
            "and .env files near the code.\n"
            "Only conclude access is unavailable after steps 2-3.\n"
            "STRICTLY READ-ONLY: observe and query only. Never write, restart, "
            "delete, deploy, scale, or change any config, data, or service.\n\n"
        )


async def _run_codex(
    prompt: str,
    infra: bool = False,
    write: bool = False,
    ctx: Context | None = None,
    reserve: int = 0,
    web_search: bool = True,
    resume_tid: str | None = None,
    parent_run: str = "",
    images: list[str] | None = None,
    tool_name: str = "",
    workdir: str = "",
    request_started: float | None = None,
    claim_key: str = "",
    custody_cwd: str = "",
    session_out: dict[str, Any] | None = None,
    review_job: str = "",
) -> str:
    """Run codex exec headlessly with clean final-message extraction.

    ``session_out``: filled with this run's live state dict (``run_tag``,
    ``thread_id``) so an advisory caller can report the session it ran in.

    ``custody_cwd``: the tree whose WRITE LOCK the caller holds around this
    run (abraham holds it across BOTH phases — round 17: a phase-1 READ run
    with survivors must put that lock into custody too, or the finally
    releases a tree an infra-mode survivor may still write).

    ``workdir`` overrides the run's working tree (abraham targeting another
    git repo — below the server's cwd, as in a multi-repo project root that
    is not itself a work tree, or anywhere else). Empty = the server cwd.

    Uses codex-cli 0.118.0+ features (live-view additions verified 0.144.1):
    - ``--json`` — JSONL ThreadEvents on stdout, streamed to the per-run
      live log (``~/.claude/logs/codex-oracle/latest.log``) together with
      ``-c model_reasoning_summary=detailed`` so the operator can watch the
      model's reasoning, web searches, commands and errors in real time.
    - ``--output-last-message FILE`` — clean final-message extraction.
    - ``-c approval_policy=never`` — prevents blocking on approval prompts.
    - ``-c mcp_servers={}`` — disables codex's built-in MCP servers
      (default mode only; ``infra=True`` keeps them and lifts the sandbox).
    - ``-c web_search=live`` — LIVE web search (see below).
    - ``--color never`` — strips ANSI sequences.
    - ``stdin=DEVNULL`` — prevents reading the parent's MCP JSON-RPC stdin.

    Web search (2026-08-07, verified against codex-cli 0.144.1 + the current
    config reference at learn.chatgpt.com/docs/config-file/config-reference):
    the top-level ``web_search`` key takes ``disabled`` | ``cached`` |
    ``indexed`` | ``live``, and DEFAULTS TO ``cached`` — an OpenAI-maintained
    snapshot index, not the live web. Every tool here asks for current
    versions/APIs/CVEs, so ``live`` is forced on every invocation, including
    under ``--sandbox read-only``: web search is a native Responses tool and
    does not go through the shell sandbox (empirically confirmed — a read-only
    run retrieved a same-day GitHub release tag). Do NOT switch to
    ``tools.web_search`` (legacy boolean; superseded by the table form) or
    ``features.web_search_request`` (rejected as deprecated by 0.144.1);
    ``tools.web_search_request`` is not a valid key at all.

    Key fix (2026-04-09): subprocess stdout/stderr are consumed with
    ``read(READ_CHUNK_SIZE)`` instead of ``readline()``. The old
    ``readline()`` called ``StreamReader.readuntil(b'\\n')`` which raises
    ``LimitOverrunError`` ("Separator is not found, and chunk exceed the
    limit") when codex outputs any single line > 64 KiB (the default
    asyncio StreamReader buffer). ``read()`` never searches for a separator
    so it cannot overflow. Additionally, ``limit=SUBPROCESS_BUFFER_LIMIT``
    (4 MiB) is passed to ``create_subprocess_exec()`` as a safety net.
    """
    model = _get_codex_model()
    reasoning = _get_reasoning_effort()
    if request_started is None:
        # the REQUEST clock starts at ENTRY (round 33): digest, prompt and
        # journal preparation below are part of the request, not free time
        request_started = time.monotonic()

    # Coerce a lone string to a list — iterating a str yields characters,
    # which would produce a baffling per-character "not found".
    if isinstance(images, str):
        images = [images]
    if images:
        missing = [p for p in images if not Path(p).is_file()]
        if missing:
            # Refuse loudly BEFORE any spend — a silently dropped image
            # produces a confident review of the wrong thing.
            return (
                "[Error: image file(s) not found — nothing was sent to "
                f"codex: {', '.join(missing)}]"
            )
        # Resolve to absolute paths: a relative path that begins with '-'
        # (e.g. "-x.png") would be parsed by clap as a flag, not a value.
        images = [str(Path(p).resolve()) for p in images]

    # WRITE PRECONDITION: git is the only undo an autonomous write run has.
    # Refuse outside a work tree, and snapshot the dirty state so the final
    # report can attribute changes honestly (see _write_changes_report).
    eff_cwd = workdir or _get_cwd()
    write_before: dict = {}
    mode_since = 0
    extra_env: dict[str, str] = {}
    holds_tree_lock = bool(write or custody_cwd)
    if holds_tree_lock:
        # An exclusive fallback is still a writer. Inherited local instances
        # of this plugin must register only the advisory tool catalog.
        extra_env["SOFTWARE_WORKFLOWS_ROLE"] = "writer"
    if holds_tree_lock and os.name != "nt" and _is_launcher_script(_codex_argv0()[0]):
        # Round 12 (write) + round 19 (any lock-holding run): behind a
        # launcher script the inherited tree lock and the recorded pid
        # belong to the LAUNCHER, not the real codex — the one-writer
        # guarantee would be fiction. Plain read runs proceed (their claims
        # degrade, adoption stays spool-driven); lock holders refuse.
        return (
            "[lock-holding run refused: the codex on PATH is a launcher "
            "SCRIPT and no vendored native binary could be resolved behind "
            "it — the tree lock would never reach the real codex process. "
            "Pin the native binary via CODEX_ORACLE_CODEX_BIN or reinstall "
            "@openai/codex.]"
        )
    role_config = {}
    catalog = None
    if holds_tree_lock:
        try:
            from coordinator.roles import exec_policy
            role_config, catalog = await exec_policy(_codex_argv0(), eff_cwd, {
                "model": model, "model_reasoning_effort": reasoning, "approval_policy": "never",
                "sandbox_mode": "danger-full-access", "web_search": "live" if web_search else "disabled"})
        except Exception as exc:
            return "[Exec writer refused before inference: " + str(getattr(exc, "code", type(exc).__name__)) + "; restricted MCP catalog could not be verified.]"
    if write:
        write_before = _tree_snapshot(eff_cwd)
        if not write_before["ok"]:
            return (
                f"[write run refused: {eff_cwd} is not inside a git work "
                f"tree, or its state could not be read ({write_before['reason']}). "
                "Autonomous writes without version control have no "
                "undo. Run from a git checkout (or `git init` first), or use "
                "the read-only tools instead.]"
            )
        # where this run's own turns begin in the thread's rollout, for the
        # effective-mode read-back after the run
        prior = _rollout_path(resume_tid) if resume_tid else None
        if prior is not None:
            with contextlib.suppress(OSError):
                mode_since = prior.stat().st_size

    # Inject the curated external-safe ADVISOR_CONTEXT.md (never CLAUDE.md or
    # memory — those carry live secrets). Fresh threads only: a resumed thread
    # received it on turn one and re-sending would bloat the rollout.
    if resume_tid is None:
        advisor_ctx = _advisor_context()
        if advisor_ctx:
            prompt = f"{advisor_ctx}\n\n{prompt}"

    if write:
        # The implementation phase, always full access (user rulings
        # 2026-09-05 and 2026-10-18): no sandbox, so the writer runs the
        # project's REAL gates — that is the point — under the git contract.
        prompt = IMPLEMENTATION_INSTRUCTIONS + prompt
    elif infra:
        # Infra mode: full sandbox (network + shell) so Codex can SSH to the
        # server, query the live DB, read container/Dokploy logs, etc., and
        # the MCP servers from ~/.codex/config.toml (auth0, temporal, ...)
        # stay enabled. Read-only DISCIPLINE is enforced via prompt only —
        # callers opt in per call and must trust the investigation prompt.
        prompt = INFRA_INSTRUCTIONS + prompt

    t0 = time.monotonic()
    # The digest of the tree the model will READ — the run's own tree, which
    # abraham may target below the workspace (architect review: hashing the
    # session cwd let a subtree run vouch for the wrong worktree).
    tree_at_dispatch = _workspace_digest(eff_cwd)
    mode_str = ("write+infra" if write and infra else
                "write" if write else
                "infra" if infra else "read-only")
    ac_limit: int | None = None
    ac_why = ""
    if write:
        ac_limit, ac_why = _auto_compact_limit(model)
    # Which auto-compact branch ran is RECORDED, never silent: an omitted
    # flag is a deliberate fallback to the vendor's 90% default, and the
    # header must let a later reader tell the two apart.
    ac_note = (
        f" autocompact={ac_limit if ac_limit is not None else 'vendor-default(90%)'}"
        f" [{ac_why}]"
    ) if write else ""
    live_path, live_fh, stream_fh, run_tag = _open_live_log(
        "abraham" if write else ("infra" if infra else "codex"))
    state: dict[str, Any] = {"activity": "launching codex", "last_message": "",
                             "last_error": "", "last_item_error": "",
                             "usage": "", "thread_id": "",
                             "write": "1" if write else "", "run_tag": run_tag,
                             "parent_run": parent_run or "", "claim_key": claim_key,
                             "custody_cwd": custody_cwd}
    if session_out is not None:
        session_out["state"] = state
    if review_job:
        try:
            await _coordinator_call("review/dispatch", {"job": review_job, "run": run_tag}, key=review_job + ":dispatch")
        except Exception as exc:
            for stream in (live_fh, stream_fh):
                if stream:
                    stream.close()
            return f"[Bound review was not dispatched: {getattr(exc, 'code', type(exc).__name__)}. Collect review job {review_job} before retrying.]"

    journaled_tid = ""
    journaled_spawn_pid = 0

    def _emit(text: str) -> None:
        """One event → per-run log, tagged merged stream, session journal.

        The session id is journaled the INSTANT it streams in (not after the
        attempt returns) — a run cancelled mid-flight must still leave a
        resumable handle behind.
        """
        nonlocal journaled_tid, journaled_spawn_pid
        _live_write(live_fh, t0, text)
        _live_write(stream_fh, t0, text, run_tag)
        tid = state.get("thread_id") or ""
        if tid and tid != journaled_tid:
            # Durability first: journaled_tid records what IS on disk, never
            # what we hoped to write (round 6). Retries are bounded and
            # IMMEDIATE (round 7): waiting for later events let a quiet
            # child run on indefinitely after one failed append.
            ok_sess = False
            for sess_try in range(3):
                if sess_try:
                    time.sleep(0.05)
                if _journal({"run": run_tag, "phase": "session", "ts": time.time(),
                             "thread_id": tid}):
                    ok_sess = True
                    break
            if ok_sess:
                journaled_tid = tid
                if not state.get("claim_key"):
                    # A FRESH run claims its thread the moment the id exists
                    # and holds the claim across every retry and backoff
                    # (round 6: an idle backoff window let a concurrent
                    # resume double-execute the thread). Continuations
                    # arrive already holding it (claim_key).
                    got_claim, claim_holder = _acquire_run_claim(_thread_claim_key(tid))
                    if got_claim:
                        state["own_claim"] = _thread_claim_key(tid)
                        state["claim_key"] = _thread_claim_key(tid)  # later attempts' children inherit it
                    else:
                        state["fatal"] = (
                            f"could not claim thread {tid} ({claim_holder}) "
                            "— a concurrent continuation cannot be excluded")
            else:
                state["fatal"] = (
                    "could not journal the codex session id after 3 "
                    "attempts — the thread would be unresumable and "
                    "uncancellable")
        # The spawn record (pid/pgid/spool paths/deadline) is what lets a
        # LATER server adopt or cancel this process — journaled the instant
        # the child exists, once per attempt.
        sp = state.get("spawn")
        if isinstance(sp, dict) and sp.get("pid") and sp.get("pid") != journaled_spawn_pid:
            journaled_spawn_pid = int(sp["pid"])
            if not _journal({"run": run_tag, "phase": "spawn", "ts": time.time(), **sp}):
                state["publish_failed"] = True  # the spawn is not on record: fail closed

    if live_fh is not None:
        with contextlib.suppress(Exception):
            live_fh.write(
                f"# codex-oracle live view — {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
                f"# model={model} effort={reasoning} "
                f"mode={mode_str}{ac_note} "
                f"web_search={'live' if web_search else 'disabled'} cwd={eff_cwd}\n"
                f"# prompt ({len(prompt)} chars): {prompt[:400]!r}\n\n"
            )
            live_fh.flush()
    _live_write(
        stream_fh, t0,
        f"▶ start model={model} effort={reasoning} "
        f"mode={mode_str} "
        f"prompt {len(prompt)} chars: {prompt[:120]!r}",
        run_tag,
    )

    # THE OWNER IS THE TERMINAL WRITER while attached (round 8): hold the
    # run-terminal claim for the run's lifetime — and hold it BEFORE the
    # start record becomes visible (round 10: publish-then-claim let a
    # canceller find the apparently free claim of a just-published run,
    # terminalize "never spawned", and clear the marker while the owner went
    # on to spawn anyway). A canceller that cannot take the claim defers; one
    # that CAN take it knows no task remains responsible.
    got_term, term_holder = _acquire_run_claim(_run_terminal_claim_key(run_tag))
    if not got_term:
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()
        return (
            f"[dispatch refused: the fresh run tag {run_tag} is already "
            f"claimed ({term_holder}) — state directory anomaly; retry.]"
        )
    if not _journal({
        "run": run_tag, "phase": "start", "ts": time.time(), "engine": "codex",
        "tool": tool_name,
        "model": model, "reasoning": reasoning, "infra": infra, "write": write,
        "full_access": bool(write),
        "web_search": web_search, "parent_run": parent_run,
        "images": list(images or []), "cwd": eff_cwd,
        "writer_mcp_catalog": catalog,
        # the session's folder, which may lie outside the target tree: the run
        # is listed and resumable from both (_run_belongs)
        "dispatch_cwd": _get_cwd(),
        "prompt": prompt, "log": str(live_path or ""),
        "tree": tree_at_dispatch,
        "custody_cwd": custody_cwd,  # lock ownership, for cancel-time custody release (round 18)
        # A continuation's start carries its thread id (round 6): a crash
        # before the child's thread.started must still leave the claim and
        # any later adoption keyed by the THREAD, not the run.
        "thread_id": resume_tid or "",
    }):
        _release_run_claim(_run_terminal_claim_key(run_tag))
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()
        return (
            "[dispatch refused: the run journal could not be written "
            f"({RUNS_JOURNAL}) — an unrecorded run cannot be watched, "
            "cancelled, resumed, or adopted (round 6: fail-closed covers "
            "every publication phase, not just the spawn record). Fix the "
            "state directory and retry.]"
        )

    attempt = 0
    retry_classes: list[str] = []  # journaled: what each retry recovered from
    overload_retries = 0
    disconnect_retries = 0
    waited_total = 0.0

    def _cancelled(where: str) -> None:
        """Bookkeeping for a cancel — mid-attempt or during a capacity wait.
        Server SHUTDOWN (detached child): journal `detached` — no `end`, so
        the run stays a resume candidate and codex_resume_run ADOPTS the
        still-running process. Caller cancel: journal `end cancelled` (the
        thread id is already journaled, so it stays resumable). Either way
        say so in the live log and release the log handles."""
        if state.get("detached"):
            cc = str(state.get("custody_cwd") or "")
            if cc:
                # A detached lock-held read (abraham phase 1) keeps the tree
                # locked through the CHILD's inherited descriptor; putting
                # the lock into CUSTODY makes the caller's finally a no-op,
                # so our release cannot unplant the legacy bridges or close
                # the last local handle while the child still runs
                # (round 18). The dead server's custody entry dies with it;
                # the child's flock dies with the child; stale bridge files
                # self-heal on the next acquire.
                _LOCK_CUSTODY.add(str(_write_lock_path(cc)))
            sp = state.get("spawn") or {}
            remaining = int(max(0.0, float(sp.get("deadline_ts") or 0) - time.time()))
            # Publish the detached transition WHILE still holding the claims
            # (round 9): released first, a collector could append a terminal
            # end in the window and this late record would land after it.
            _journal({"run": run_tag, "phase": "detached", "ts": time.time(),
                      "status": "detached", "pid": sp.get("pid"),
                      "pgid": sp.get("pgid"), "attempts": attempt + 1,
                      "retry_classes": list(retry_classes),
                      "capacity_wait_s": int(waited_total)})
            _release_run_claim(_run_terminal_claim_key(run_tag))
            own_claim = str(state.pop("own_claim", "") or "")
            if own_claim:
                # Our descriptor only — a child that INHERITED it keeps the
                # thread claimed; releasing ours lets the adopting server
                # take the claim and collect (mirrors codex_resume_run's
                # finally, which also releases on detach).
                _release_run_claim(own_claim)
            with contextlib.suppress(Exception):
                _emit(
                    f"■ server shutting down — codex keeps running DETACHED "
                    f"(pid {sp.get('pid')}, hard deadline in {remaining}s). "
                    f"Collect it from the next connection: "
                    f"codex_resume_run(run=\"{run_tag}\")"
                )
        elif state.get("group_survivors"):
            # Round 14: the kill left group members alive — no terminal
            # record over a possibly-writing group. The marker keeps the run
            # CANCELLING and stoppable; a later codex_cancel_run verifies
            # group death before terminalizing. Lock-holding runs put the
            # tree's flock into CUSTODY (rounds 15+17) so no new writer can
            # start — write phase or lock-held read phase alike.
            if write or custody_cwd:
                _LOCK_CUSTODY.add(str(_write_lock_path(custody_cwd or eff_cwd)))
            _request_cancel(run_tag)
            _journal({"run": run_tag, "phase": "cancel_requested",
                      "ts": time.time(), "by_pid": os.getpid(),
                      "survivors_pgid": state["group_survivors"]})
            own_claim = str(state.pop("own_claim", "") or "")
            if own_claim:
                _release_run_claim(own_claim)
            _release_run_claim(_run_terminal_claim_key(run_tag))
            with contextlib.suppress(Exception):
                _emit(
                    f"✖ cancel: process group {state['group_survivors']} still "
                    f"has live members — NOT terminalizing; the run stays "
                    f"stoppable (codex_cancel_run retries once the group dies)"
                )
        else:
            # SHUTDOWN IS NOT A DECISION (round 10): a server going down
            # during a capacity wait (no live child to detach) is
            # `interrupted` — journaling it `cancelled` made the new
            # bare-resume guard refuse the automatic recovery an ordinary
            # restart deserves. `cancelled` is reserved for a caller's
            # explicit stop.
            shutdown = _SHUTDOWN.is_set() and not _cancel_requested(run_tag)
            status_c = "interrupted" if shutdown else "cancelled"
            if _journal({"run": run_tag, "phase": "end", "ts": time.time(),
                         "status": status_c, "attempts": attempt + 1,
                         "retry_classes": list(retry_classes),
                         "capacity_wait_s": int(waited_total)}):
                if not shutdown:
                    _clear_cancel(run_tag)  # durable terminal record retires the intent
            own_claim = str(state.pop("own_claim", "") or "")
            if own_claim:
                _release_run_claim(own_claim)
            _release_run_claim(_run_terminal_claim_key(run_tag))
            with contextlib.suppress(Exception):
                _emit(
                    ("■ run interrupted by server shutdown"
                     if shutdown else "■ run cancelled by caller")
                    + (f" {where}" if where else "")
                    + " (resume later: codex_resume_run)"
                )
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()

    # One request-time anchor for EVERY attempt: retries must not reset the
    # heartbeat deadline (the client's progress token is request-scoped).
    final_message = ""
    clean_extraction = False
    stdout_text = stderr_text = ""
    returncode = 0
    hung_reason: str | None = None
    timed_out = False

    while True:
        try:
            # Per-run spool: answer file + the child's stdout/stderr live
            # here (file-backed so the run survives this server; see RUN
            # SURVIVABILITY). Inside the containment (round 11): an OSError
            # from the spool mkdir escaped _run_codex with the terminal
            # claim held — the run was unkillable until server restart.
            output_file = _run_spool_dir(run_tag) / f"attempt{attempt}.txt"
            cmd = _build_exec_argv(
                model, reasoning, infra, web_search, output_file,
                prompt=prompt, resume_tid=resume_tid, images=images, role_config=role_config,
                write=write, auto_compact_limit=ac_limit,
            )
        except Exception as e:
            stderr_text = f"run machinery failure: {type(e).__name__}: {e}"
            state["last_error"] = stderr_text
            returncode = 1
            final_message = ""
            clean_extraction = False
            with contextlib.suppress(Exception):
                _emit(f"✖ {stderr_text}")
            break
        expected_tid = resume_tid
        state["resumed"] = resume_tid is not None
        state["last_error"] = ""
        state["last_message"] = ""  # attempt N's commentary is not attempt N+1's answer
        state.pop("turn_completed", None)  # completion evidence is ATTEMPT-scoped
        state["usage"] = ""             # (round 16: attempt 1's turn.completed
        #                                 signed attempt 2's partial as ok)
        if _cancel_requested(run_tag):
            # Cancel intent lodged before this attempt spawned: honour it
            # here — the requester journaled an intent, not a result.
            returncode, final_message = -9, ""
            state["last_error"] = "cancelled before spawn (codex_cancel_run)"
            _emit("■ cancel requested before spawn — honoured, nothing executed")
            break
        try:
            (final_message, clean_extraction, stdout_text, stderr_text,
             returncode, hung_reason, timed_out) = await _exec_codex_once(
                cmd, output_file, state, _emit, ctx, model,
                extra_env=extra_env, workdir=eff_cwd,
                request_started=request_started)
        except asyncio.CancelledError:
            _cancelled("")
            raise
        except Exception as e:
            # Run-machinery failure (unexpected spawn error, tail bug, fd
            # exhaustion): terminalize DURABLY instead of unwinding — an
            # escaped exception left the terminal claim held until server
            # restart, with the run unkillable and uncancellable (round 9).
            stderr_text = f"run machinery failure: {type(e).__name__}: {e}"
            state["last_error"] = stderr_text
            returncode = 1
            final_message = ""
            clean_extraction = False
            with contextlib.suppress(Exception):
                _emit(f"✖ {stderr_text}")
            break

        if state.get("fatal"):
            stderr_text = str(state["fatal"])
            state["last_error"] = str(state.get("last_error") or stderr_text)
            returncode = returncode or 1
            final_message = ""
            clean_extraction = False
            _emit(f"✖ failed closed: {stderr_text}")
            break

        # AMNESIA GUARD (measured): resume with an unknown id silently starts
        # a fresh context-less thread and exits 0 — that is NOT a resume.
        if expected_tid and state.get("thread_id") and state["thread_id"] != expected_tid:
            stderr_text = (
                f"resume continuity lost: expected thread {expected_tid}, got "
                f"{state['thread_id']} — codex started a fresh context-less "
                f"thread instead of resuming. Not retrying; re-dispatch the "
                f"original question instead."
            )
            returncode = returncode or 1
            final_message = ""
            _emit(f"✖ {stderr_text}")
            break

        if state.get("group_survivors"):
            # Round 20: NEVER spawn attempt N+1 over attempt N's live
            # descendants — an infra-mode survivor retains danger-full-access
            # and would overlap the retry's commands. The post-loop survivor
            # path takes over (nonterminal, marker, custody).
            break
        failed = returncode != 0 and hung_reason is None and not timed_out
        # NO AUTOMATIC RETRY FOR WRITE RUNS (cross-model review, CRITICAL):
        # replaying "implement X" after half of X was written is not
        # idempotent — a fresh retry can double-apply edits the failed
        # attempt already made. Recovery for write runs is the EXPLICIT
        # codex_resume_run path, where the caller first reconciles the
        # changed-files report against the tree.
        # Classify the TERMINAL error only (the `error`/`turn.failed` event;
        # stderr as the sole fallback) — never model or tool output, whose
        # prose can say "at capacity" while the actual failure is a quota
        # denial (review of 1.16.2, MEDIUM).
        terminal = state["last_error"] or stderr_text
        klass = _transient_class(terminal) if failed and not write else None
        class_budget = (
            OVERLOAD_MAX_RETRIES if klass == "overload" else MAX_TRANSIENT_RETRIES
        )
        class_used = overload_retries if klass == "overload" else disconnect_retries
        if (
            klass is not None
            and class_used < class_budget
            and attempt < MAX_TOTAL_RETRIES
        ):
            wait = 0.0
            if klass == "overload":
                # ±20% jitter so runs shed together do not return together;
                # clamped so the cap is the cap.
                wait = min(
                    OVERLOAD_BACKOFF_CAP_SECONDS,
                    _overload_backoff_seconds(overload_retries)
                    * random.uniform(0.8, 1.2),
                )
            # ABSOLUTE REQUEST DEADLINE (round 33): the wait plus a retry must
            # fit in what the request has LEFT — never a fresh clock. Retry
            # state is mutated only for a retry that actually happens (round
            # 34: a refused wait was journaled as an attempt and a full wait).
            fits, left = _retry_fits(request_started, wait)
            if not fits:
                _emit(f"■ request budget cannot cover a {int(wait)}s wait plus a retry "
                      f"({int(left)}s of {MAX_RUNTIME_SECONDS}s left) — giving up as a timeout")
                timed_out = True
                break
            resume_tid = state.get("thread_id") or None
            what = (
                "provider capacity shed" if klass == "overload"
                else "transient failure"
            )
            where = (
                f"resuming thread {resume_tid}" if resume_tid
                else "retrying fresh (failed before the session started)"
            )
            _emit(
                f"⟲ {what} — {where} ({klass} retry {class_used + 1}/{class_budget}, "
                f"total attempt {attempt + 2}"
                + (f", after a {int(wait)}s wait)" if wait else ")")
            )
            if wait:
                wait_t0 = time.monotonic()
                try:
                    cancel_seen = await _wait_for_capacity(
                        wait, ctx, request_started, t0, state, model, _emit,
                        run_tag=run_tag,
                    )
                except asyncio.CancelledError:
                    # a CANCELLED wait is journaled as the time actually spent
                    waited_total += min(wait, time.monotonic() - wait_t0)
                    _cancelled("during the capacity wait")
                    raise
                if cancel_seen:
                    # A codex_cancel_run INTENT arrived during the wait (round
                    # 36: the boolean was returned and ignored, so the run
                    # spawned again over a lodged cancel): journal the time
                    # actually waited and TERMINALIZE as cancelled — no spawn.
                    waited_total += min(wait, time.monotonic() - wait_t0)
                    returncode, final_message = -9, ""
                    state["last_error"] = "cancelled during the capacity wait (codex_cancel_run)"
                    _emit("■ cancel intent honoured during the capacity wait — no further attempt")
                    break
                waited_total += wait  # a completed wait IS the planned wait
            # RETRY TELEMETRY (round 36): counted only now, immediately before
            # the spawn the next iteration performs — a refused or cancelled
            # wait never spawned, so it never counts as an attempt.
            attempt += 1
            retry_classes.append(klass)
            if klass == "overload":
                overload_retries += 1
            else:
                disconnect_retries += 1
            continue
        break

    with contextlib.suppress(Exception):
        _emit(
            f"■ run finished: exit={returncode} timed_out={timed_out} "
            f"attempts={attempt + 1} {state.get('usage') or ''}".rstrip()
        )
    for _fh in (live_fh, stream_fh):
        if _fh is not None:
            with contextlib.suppress(Exception):
                _fh.close()

    log_note = f"\n\n[live log: {live_path}]" if live_path else ""
    if state.get("group_survivors") and not state.get("detached"):
        # Round 15: survivors make the run NONTERMINAL — a terminal record
        # plus a released write lock made the tree eligible for another
        # writer while descendants might still be modifying it. The cancel
        # MARKER keeps the run CANCELLING/stoppable; codex_cancel_run
        # verifies group death (round 13) and then terminalizes. For write
        # runs the tree's flock goes into CUSTODY: this server keeps the
        # descriptor, so new write dispatches and write resumes refuse until
        # the group is resolved.
        gp = state["group_survivors"]
        _journal({"run": run_tag, "phase": "survivors", "ts": time.time(),
                  "pgid": gp})
        _request_cancel(run_tag)
        custody_note = ""
        if write or custody_cwd:
            # Custody follows LOCK OWNERSHIP, not write mode (round 17).
            _LOCK_CUSTODY.add(str(_write_lock_path(custody_cwd or eff_cwd)))
            custody_note = (
                " The tree's write lock stays HELD by this server as "
                "custodian — write dispatches and write resumes on this "
                "tree refuse until then.")
        with contextlib.suppress(Exception):
            _emit(f"✖ process group {gp} still has live members after the "
                  f"SIGKILL sweep — NOT terminalizing (run stays stoppable)")
        own_claim = str(state.pop("own_claim", "") or "")
        if own_claim:
            _release_run_claim(own_claim)
        _release_run_claim(_run_terminal_claim_key(run_tag))
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()
        survivors_report = (
            _write_changes_report(write_before, eff_cwd)
            if write else ""
        )
        return (
            f"[Codex run {run_tag}: the codex leader exited but its process "
            f"group ({gp}) still reads alive after the marker sweep — "
            f"survivors may still be modifying files. NOT terminalized: "
            f"codex_cancel_run(run=\"{run_tag}\") kills MARKER-VERIFIED "
            f"survivors (list them: python3 procenv.py --list {run_tag}) "
            f"and records the terminal state once they are gone. Never signal "
            f"the bare group id — it may already belong to another process."
            f"{custody_note}]"
            + (f"\n\n{survivors_report}" if survivors_report else "")
            + log_note
        )
    if (returncode == 0 and not timed_out and hung_reason is None
            and not (final_message
                     and (clean_extraction or state.get("turn_completed")))):
        # OK IS EARNED (round 15, matching the adoption rule): exit 0
        # without a completed final answer is an error — otherwise the
        # rendered "answer" is the log-note metadata, which abraham's gate
        # would treat as a brief.
        state["last_error"] = state.get("last_error") or (
            "codex exited 0 without a completed final answer "
            "(no output-last-message / turn.completed)")
        returncode = 1
    status = (
        "ok" if (returncode == 0 and not timed_out and hung_reason is None)
        else ("timeout" if timed_out else ("hung" if hung_reason else "error"))
    )
    result_file = ""
    if status == "ok" and final_message and live_path is not None:
        with contextlib.suppress(Exception):
            rf = live_path.with_suffix(".result.txt")
            rf.write_text(final_message, encoding="utf-8")
            _private(rf, 0o600)
            result_file = str(rf)
    if status == "error" and returncode < 0:
        # Killed by signal: a verified or intended cancellation (codex_cancel_run)
        # is the terminal status, not "error".
        prior = _journal_runs().get(run_tag, {})
        if (prior.get("status") == "cancelled" and prior.get("cancelled_by")) or _cancel_requested(run_tag):
            status = "cancelled"
    if session_out is not None:
        session_out["completion"] = {"run": run_tag, "thread": state.get("thread_id"), "status": status,
            "completed": status == "ok" and bool(final_message) and bool(clean_extraction or state.get("turn_completed")),
            "answer": final_message or "", "returncode": returncode}
    # What a write run REALLY ran with, read back from its rollout: a flag
    # and an exit code are union claims (the old sealed sandbox once
    # downgraded itself to read-only and still exited 0), the turn_context
    # codex recorded for each turn is the measurement.
    modes = _effective_modes(str(state.get("thread_id") or ""), mode_since) if write else []
    if _journal({
        "run": run_tag, "phase": "end", "ts": time.time(), "status": status,
        "returncode": returncode, "attempts": attempt + 1,
        "retry_classes": retry_classes, "capacity_wait_s": int(waited_total),
        "error": (state["last_error"] or stderr_text)[:500] if status != "ok" else "",
        "result_file": result_file,
        **({"effective_modes": modes} if write else {}),
    }):
        # Only a DURABLE terminal record retires a cancel intent (round 6) —
        # clearing on a failed append would leave the run live-looking with
        # its cancel silently dropped.
        _clear_cancel(run_tag)
    else:
        log_note += (
            "\n[⚠ the run's terminal journal record could not be written — "
            "status displays may show the run live, and any pending cancel "
            "intent is retained]"
        )
    own_claim = str(state.pop("own_claim", "") or "")
    if own_claim:
        _release_run_claim(own_claim)
    _release_run_claim(_run_terminal_claim_key(run_tag))

    # Every write-run outcome — success, timeout, hang, error — reports what
    # changed on disk: a timed-out run may still have written files, and the
    # caller's next step is reviewing exactly that.
    write_report = (
        _write_changes_report(write_before, eff_cwd) + _modes_line(modes)
        if write else ""
    )

    if timed_out:
        if final_message:
            return (
                f"[Codex model: {model} | reasoning: {reasoning}"
                f"{_answer_sig(tool_name, 'timeout', tree_at_dispatch)}]\n"
                f"[TIMEOUT — the {MAX_RUNTIME_SECONDS}s REQUEST budget ran out "
                f"(shared by every attempt and phase of this call; "
                f"CODEX_ORACLE_MAX_RUNTIME_S, {MAX_RUNTIME_SOURCE}) — partial "
                f"output recovered; codex_resume_run continues this thread]"
                f"{log_note}\n\n"
                f"{final_message}{write_report}"
            )
        return (
            f"[Codex TIMEOUT: the {MAX_RUNTIME_SECONDS}s REQUEST budget ran out "
            f"(shared by every attempt and phase of this call; "
            f"CODEX_ORACLE_MAX_RUNTIME_S, {MAX_RUNTIME_SOURCE})]\n"
            f"If the work was legitimately long, raise the budget in the server's "
            f"environment and codex_resume_run this thread (its reasoning is kept); "
            f"if it was stuck, narrow the task."
            f"{log_note}{write_report}"
        )

    if hung_reason is not None:
        return (
            f"[Codex health check FAILED: {hung_reason}]\n\n"
            f"This usually means: (1) the codex CLI is waiting on stdin it will never receive, "
            f"(2) an expired auth token in ~/.codex/config.toml MCP servers, "
            f"(3) an approval prompt blocked by a missing TTY.\n\n"
            f"Partial stdout ({len(stdout_text)} chars):\n{stdout_text[:2000] or '(none)'}\n\n"
            f"Partial stderr ({len(stderr_text)} chars):\n{stderr_text[:2000] or '(none)'}"
            f"{log_note}{write_report}"
        )

    noise_patterns = (
        "Loaded cached credentials",
        "[INFO]",
        "Reading additional input from stdin",
        "OpenAI Codex v",
        "workdir:",
        "provider:",
        "approval:",
        "sandbox:",
        "reasoning effort:",
        "reasoning summaries:",
        "session id:",
        "tokens used",
        "--------",
    )

    def _stderr_diagnostics() -> str:
        """The noisy session stream, only when clean extraction failed on a
        non-zero exit — i.e. when the diagnostic is needed."""
        if clean_extraction or not stderr_text:
            return ""
        lines = [
            line for line in stderr_text.splitlines()
            if line.strip() and not any(pat in line for pat in noise_patterns)
        ]
        return ("\n\n[stderr]\n" + "\n".join(lines[-40:])) if lines else ""

    if returncode != 0:
        # ONE structured renderer for EVERY non-zero exit (review of 1.16.2,
        # HIGH): a failed run that had already emitted assistant text used to
        # return that text as if it were the answer, skipping the capacity
        # note and the resume hand-off. The failure is the message; whatever
        # the model said before it is PARTIAL OUTPUT — labelled and bounded.
        terminal = state["last_error"] or stderr_text
        detail = terminal
        if returncode < 0 and not state["last_error"]:
            detail = (
                f"codex process killed by signal {-returncode} "
                f"(codex_cancel_run, or the runtime watchdog at the "
                f"{MAX_RUNTIME_SECONDS}s run budget, CODEX_ORACLE_MAX_RUNTIME_S)"
                + (f"\n{stderr_text[-1500:]}" if stderr_text else "")
            )
        retry_note = f" after {attempt + 1} attempts" if attempt else ""
        overload_failures = retry_classes.count("overload") + (
            1 if _transient_class(terminal) == "overload" else 0
        )
        capacity_note = (
            f"\n[provider capacity: {model} answered 'at capacity' on "
            f"{overload_failures} attempt(s); waited {int(waited_total)}s between "
            f"same-thread resumes; the model/effort pin was NOT changed — the shed "
            f"outlived the in-request budget, resume the thread once capacity "
            f"returns]"
            if overload_failures else ""
        )
        partial = ""
        if final_message:
            snippet = final_message
            if len(snippet) > 4000:
                snippet = (
                    snippet[:4000]
                    + "\n… [partial output truncated; the full text is in the live log]"
                )
            partial = (
                "\n\n[partial output before the failure — NOT the answer]\n" + snippet
            )
        result = (
            f"[Codex error (exit {returncode}){retry_note}"
            f"{_answer_sig(tool_name, 'error', tree_at_dispatch)}]\n{detail}"
            f"{capacity_note}\n"
            f"[recoverable: call codex_resume_run to continue this run "
            f"(run id: {run_tag})]{partial}{write_report}"
            f"{_stderr_diagnostics()}{log_note}"
        )
    else:
        # status:ok is EARNED by exit 0 (the push gate reads the signature).
        header = (
            f"[Codex model: {model} | reasoning: {reasoning}"
            f"{_answer_sig(tool_name, 'ok', tree_at_dispatch)}]"
        )
        result = f"{header}\n\n{final_message}"
        if attempt:
            recovered_from = (
                f"a provider capacity shed (waited {int(waited_total)}s)"
                if "overload" in retry_classes else "a transient failure"
            )
            result += (
                f"\n\n[note: recovered automatically after {recovered_from} — "
                f"{attempt + 1} attempts, context preserved via codex session resume]"
            )
        result += write_report
        result += log_note

    # Truncate to avoid exceeding Claude Code's MCP result limit. ``reserve``
    # is the length of text the CALLER will prepend (the anchoring banner) —
    # taken out of the budget rather than added on top of it, so the warning
    # survives truncation without pushing the total past the cap.
    budget = MAX_OUTPUT_CHARS - reserve - _TRUNC_NOTICE_ALLOWANCE
    if len(result) > budget:
        truncated = result[:budget]
        last_nl = truncated.rfind("\n")
        if last_nl > budget * 0.8:
            truncated = truncated[:last_nl]
        # The live-log/result-file pointer lives at the END of the result —
        # re-attach it so the caller can always reach the full answer.
        result = (
            f"{truncated}\n\n"
            f"[TRUNCATED: output was {len(result):,} chars, capped at "
            f"{budget:,} — the FULL answer is in the run's .result.txt next "
            f"to the live log]{log_note}"
        )

    return result


# ---------------------------------------------------------------------------
# Advisory sessions (2.0): a follow-up review continues its own codex thread
# ---------------------------------------------------------------------------
# A follow-up review is the next TURN of the earlier review's codex thread
# (`codex exec resume <UUID> "<prompt>"`, the one native resume form: a name
# lookup is cwd-scoped and a miss silently starts a new thread), so the
# reviewer starts from its own earlier findings and reads of the code, not
# from a restated summary. Codex itself replays the history, compacts it at
# 90% of the window and refuses a second concurrent resume (measured on
# 0.163.0). The plugin picks the thread, holds the thread claim so two calls
# never race for one thread, and reports the session from its rollout.

SESSION_DEFAULTS = {"code_review": "continue", "architect_review": "continue",
                    "research": "new", "codex_query": "new"}
_SESSION_LINE_RESERVE = 600  # the session line and note, out of the result budget
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FOLLOW_UP = {
    "code_review": (
        "FOLLOW-UP REVIEW in this same session: your earlier turns above "
        "reviewed earlier versions of this work. Start from them: say which of "
        "your earlier findings are now fixed, which still stand, and what is "
        "new. Never present an earlier finding as new."),
    "architect_review": (
        "FOLLOW-UP DESIGN REVIEW in this same session: your earlier turns above "
        "reviewed earlier versions of this design. Start from them: say which "
        "of your earlier concerns are resolved, which still stand, and what is "
        "new. Never present an earlier concern as new."),
}
_FOLLOW_UP_OTHER = (
    "FOLLOW-UP in this same session: build on your earlier answers above "
    "instead of restating them, and say where this question changes them.")


def _rollout_path(tid: str) -> Path | None:
    """The thread's LIVE rollout, ``$CODEX_HOME/sessions/YYYY/MM/DD/
    rollout-*-<tid>.jsonl``, or None: an archived thread lives in
    archived_sessions/ and refuses ``exec resume`` (exit 1, measured 0.163.0).
    The day directory is the thread's local creation date, which a UUIDv7
    thread id carries in its first 48 bits; that one is tried first."""
    if not _UUID_RE.fullmatch(tid):
        return None
    base = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"
    name = f"rollout-*-{tid}.jsonl"
    with contextlib.suppress(ValueError, OverflowError, OSError):
        day = time.strftime("%Y/%m/%d", time.localtime(int(tid[:8] + tid[9:13], 16) / 1000))
        hits = sorted((base / day).glob(name))
        if hits:
            return hits[-1]
    hits = sorted(base.glob(f"*/*/*/{name}"))
    return hits[-1] if hits else None


def _session_stats(path: Path | None, since: int) -> dict[str, int]:
    """A thread's turns, compactions and token use, read from its rollout:
    exec JSON shows no compaction on the remote path, and on a resume its
    turn.completed carries the THREAD total. Records from byte ``since`` on
    belong to this call. ``context`` is the last request's total_tokens,
    codex's own tokens_in_context_window."""
    st: dict[str, int] = {k: 0 for k in ("turns", "compactions", "new_compactions", "in",
                                          "cached", "out", "context", "window")}
    try:
        data = path.read_bytes() if path is not None else b""
    except OSError:
        data = b""
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    end = 0
    for line in data.split(b"\n"):
        start, end = end, end + len(line) + 1
        if b"task_started" not in line and b"compacted" not in line and b"token_count" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        payload = rec.get("payload") if isinstance(rec, dict) else None
        if not isinstance(payload, dict):
            continue
        if rec.get("type") == "compacted":
            st["compactions"] += 1
            if start >= since:
                st["new_compactions"] += 1
        elif rec.get("type") != "event_msg":
            continue
        elif payload.get("type") == "task_started":
            st["turns"] += 1
        elif payload.get("type") == "token_count" and isinstance(payload.get("info"), dict):
            info = payload["info"]
            if start < since:
                before = info
            else:
                after = info
            last = int((info.get("last_token_usage") or {}).get("total_tokens") or 0)
            if last:  # the record right after a compaction reports 0
                st["context"], st["window"] = last, int(info.get("model_context_window") or 0)
    if after:
        t0 = before.get("total_token_usage") or {}
        t1 = after.get("total_token_usage") or {}
        for key, field in (("in", "input_tokens"), ("cached", "cached_input_tokens"),
                           ("out", "output_tokens")):
            st[key] = max(0, int(t1.get(field) or 0) - int(t0.get(field) or 0))
    return st


def _effective_modes(tid: str, since: int) -> list[str]:
    """The sandbox and approval policy codex recorded for each turn of a
    thread from byte ``since`` on ("danger-full-access/never"), distinct, in
    order — read from the rollout's turn_context records."""
    path = _rollout_path(tid)
    try:
        data = path.read_bytes() if path is not None else b""
    except OSError:
        data = b""
    modes: list[str] = []
    end = 0
    for line in data.split(b"\n"):
        start, end = end, end + len(line) + 1
        if start < since or b"turn_context" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        payload = rec.get("payload") if isinstance(rec, dict) else None
        if not isinstance(payload, dict) or rec.get("type") != "turn_context":
            continue
        sandbox = payload.get("sandbox_policy")
        mode = (f"{sandbox.get('type') if isinstance(sandbox, dict) else sandbox}"
                f"/{payload.get('approval_policy')}")
        if mode not in modes:
            modes.append(mode)
    return modes


def _modes_line(modes: list[str]) -> str:
    """The write report's line on what the run really ran with."""
    if not modes:
        return ("\n  effective mode: UNVERIFIED — the run's rollout recorded no turn "
                "context (it may have failed before its first turn)")
    if modes == ["danger-full-access/never"]:
        return ("\n  effective mode: full access (sandbox danger-full-access, approvals "
                "never), as codex recorded it for every turn")
    return (f"\n  ⚠ effective mode: codex recorded {', '.join(modes)} — NOT plain full "
            "access; commands or writes may have been refused (read the run log)")


def _session_thread(tool: str, session: str) -> tuple[str, str, str, str]:
    """(thread_id, run, note, refusal) for an advisory call's ``session``.

    "new" is a fresh thread. "continue" is the thread of this workspace's
    newest completed ``tool`` run, or a fresh thread (with a note when that
    thread can no longer be resumed). A run id, a codex thread id, or a
    unique prefix of 8+ characters of one names that thread among this
    workspace's completed read-only runs; what it cannot honour is a
    REFUSAL, never a silent fresh start."""
    want = (session or "new").strip()
    if want.lower() == "new":
        return "", "", "", ""
    cwd = _get_cwd()
    done = [r for r in _journal_runs().values()
            if r.get("has_start") and r.get("has_end") and r.get("status") == "ok"
            and not r.get("write") and _UUID_RE.fullmatch(str(r.get("thread_id") or ""))
            and _run_in_workspace(str(r.get("cwd") or ""), cwd)]
    if want.lower() == "continue":
        mine = [r for r in done if r.get("tool") == tool and r.get("cwd") == cwd]
        if not mine:
            return "", "", "", ""
        rec = max(mine, key=lambda r: float(r.get("last_ts") or 0))
        tid = str(rec["thread_id"])
        if _rollout_path(tid) is None:
            return "", "", (f"the last {tool} session ({tid}) is archived or "
                            "deleted in codex, so this call started a new one"), ""
        return tid, str(rec.get("run") or ""), "", ""
    key = want.lower()
    hits = [r for r in done if r.get("run") == want
            or (len(key) >= 8 and str(r["thread_id"]).startswith(key))]
    tids = {str(r["thread_id"]) for r in hits}
    if len(tids) != 1:
        return "", "", "", (
            f"session {want!r} matches {len(tids)} completed read-only sessions "
            "in this workspace. Pass a run id or a full codex thread id from an "
            "earlier result's session line, or session=\"continue\" or \"new\".")
    rec = max(hits, key=lambda r: float(r.get("last_ts") or 0))
    tid = tids.pop()
    if _rollout_path(tid) is None:
        return "", "", "", (f"session {tid} is archived or deleted in codex "
                            f"(`codex unarchive {tid}` restores an archived one).")
    return tid, str(rec.get("run") or ""), "", ""


async def _advise(prompt: str, tool_name: str, session: str, *, infra: bool,
                  ctx: Any, web_search: bool, reserve: int, images: list[str],
                  result_out: dict | None = None, review_job: str = "") -> str:
    """Run an advisory tool in the codex session ``session`` names, then put
    the session line right under the signed header (the push gate and
    abraham's gate read only the FIRST line)."""
    tid, prev_run, note, refusal = _session_thread(tool_name, session)
    if refusal:
        return f"[session refused, nothing was sent to codex: {refusal}]"
    claim = _thread_claim_key(tid) if tid else ""
    if claim:
        got, holder = _acquire_run_claim(claim)
        if not got:
            if (session or "").strip().lower() != "continue":
                return (f"[session refused, nothing was sent to codex: session "
                        f"{tid} is in use by another call ({holder}). Wait for "
                        f"it, or pass session=\"new\".]")
            note = (f"the last {tool_name} session ({tid}) is in use by another "
                    "call, so this call started a new one")
            tid, prev_run, claim = "", "", ""
    since = 0
    if tid:
        path = _rollout_path(tid)
        if path is not None:
            with contextlib.suppress(OSError):
                since = path.stat().st_size
        prompt = f"{_FOLLOW_UP.get(tool_name, _FOLLOW_UP_OTHER)}\n\n{prompt}"
    out: dict[str, Any] = {}
    try:
        result = await _run_codex(
            prompt, infra=infra, ctx=ctx, web_search=web_search,
            reserve=reserve + _SESSION_LINE_RESERVE, images=images,
            tool_name=tool_name, resume_tid=tid or None, parent_run=prev_run,
            claim_key=claim, session_out=out, review_job=review_job)
    finally:
        if result_out is not None:
            result_out.update(out)
        if claim:
            _release_run_claim(claim)
    note_line = f"\n[session note: {note}]" if note else ""
    ran = str((out.get("state") or {}).get("thread_id") or "")
    if not ran:
        return result + note_line
    st = _session_stats(_rollout_path(ran), since if ran == tid else 0)
    bits = [f"session {ran}",
            "continued" if ran == tid else ("new" if not tid else f"a NEW thread, not {tid}"),
            f"turn {st['turns']}"]
    if st["in"] or st["out"]:
        bits.append(f"this call: in {st['in']:,} ({st['cached']:,} cached), out {st['out']:,}")
    if st["window"]:
        bits.append(f"context {st['context']:,} of {st['window']:,}")
    if st["compactions"]:
        bits.append(f"compacted {st['compactions']}×" + (
            f" ({st['new_compactions']} in this call)" if st["new_compactions"] else ""))
    nxt = (f'the next {tool_name} continues it; session="new" starts fresh'
           if SESSION_DEFAULTS.get(tool_name) == "continue"
           else f'session="{ran}" continues it')
    first, sep, rest = result.partition("\n")
    return f"{first}\n[{' · '.join(bits)} — {nxt}]{note_line}{sep}{rest}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _lead_tool():
    """Writer instances retain advisors, but cannot advertise lead authority."""
    def register(function):
        if os.environ.get("SOFTWARE_WORKFLOWS_ROLE") == "writer":
            return function
        @functools.wraps(function)
        async def observed(*args, **kwargs):
            return await _coordinator_notice(await function(*args, **kwargs))
        return mcp.tool()(observed)
    return register


@mcp.tool()
async def architect_review(
    description: str,
    files: str = "",
    concerns: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
    images: list[str] = [],
    session: str = "continue",
    ctx: Context = None,
) -> str:
    """
    Senior architect review of a design, approach, or implementation.
    Runs Codex at maximum reasoning depth with LIVE web search. Use for
    architecture decisions, system design, API contracts, data modeling,
    or structural patterns.

    DISPATCH BLIND: `description` should state WHAT is being built and the
    constraints — not which design you already favour. Put any preferred
    design or predicted verdict in `caller_hypothesis` so Codex attacks it
    instead of absorbing it.

    Args:
        description: What to review — the problem, the constraints, the
            design under consideration. State the requirement, not your
            preferred answer.
        files: Comma-separated file paths for Codex to examine
        concerns: Specific trade-off axes to evaluate (e.g. "write
            throughput vs read consistency"). A neutral scoping field —
            lint-checked for conclusion language; do NOT put a verdict here.
        caller_hypothesis: OPTIONAL. Your own design preference or predicted
            verdict, stated plainly. Presented to Codex as an unverified
            claim to REFUTE, and answered with an explicit
            CONFIRMED/REFUTED/UNPROVEN verdict. Use this instead of leaking
            your view into `description`/`concerns`.
        web_search: Live web search, ON by default so version/API/CVE claims
            are checked against the real web instead of recalled. Set False
            to keep the call fully offline — use it when the material is
            sensitive enough that outbound search queries are themselves a
            disclosure risk. Turning it off means version and API claims come
            from stale training data; treat them as unverified.
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation. Slower startup;
            use only when live state matters to the review.
        session: Which codex session this review runs in. "continue"
            (default): the next turn of this workspace's latest completed
            architect_review session, so the reviewer starts from its own
            earlier concerns and reads of the code; the result's second line
            names the session, its turn count, this call's tokens and the
            context size. "new": a fresh session, for an unrelated design or
            a deliberately independent second opinion. A codex thread id
            from an earlier session line (or a run id) continues that
            session. A session another call is using falls back to a fresh
            one, with a note.
    """
    hits = _detect_anchoring({"description": description, "concerns": concerns})

    prompt_parts = [
        "You are a principal software architect with 20+ years of experience.",
        "Perform a deep, critical architecture review. Think step by step.",
        "Be direct and opinionated. Do not hedge or be diplomatic.",
        "Flag every risk, anti-pattern, and scalability concern you find.",
        "Suggest concrete alternatives with trade-off analysis.",
        "",
        _INDEPENDENCE_PREAMBLE,
        "Design review specifics: evaluate from FIRST PRINCIPLES. Do not "
        "treat the existing architecture, the caller's chosen pattern, or the "
        "framing of the request as a constraint unless it is a stated hard "
        "requirement. 'This is how the codebase already does it' is not a "
        "justification — if a materially better design exists, say so.",
        "",
        _CAPABILITY_HUNT,
        "If the design swaps or abstracts over an engine/driver/provider, the "
        "capability surface of EVERY candidate backend is part of the design, "
        "not an implementation detail — an abstraction that assumes the union "
        "of all backends breaks on whichever one lacks a method.",
        "",
        _WEB_RESEARCH_DIRECTIVE,
        "",
        f"## Review request\n{description}",
    ]
    if files:
        prompt_parts.append(f"\n## Files to examine (read these files)\n{files}")
    if concerns:
        prompt_parts.append(f"\n## Trade-off axes to evaluate\n{concerns}")

    prompt_parts.append(_hypothesis_block(caller_hypothesis))
    prompt_parts.append(_neutralizer(hits))

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Get straight to findings.\n\n"
        "1. **Verdict**: APPROVE / CONCERNS / REJECT\n"
        "2. **Executive summary**: 2-3 sentences\n"
        "3. **Where I disagree with the caller's framing**: state it here or "
        "write 'none — framing held up' (do not omit this section)\n"
        "4. **Critical findings**: Severity-ranked list\n"
        "5. **Recommendations**: Concrete, actionable changes\n"
        "6. **Risks**: What happens if this approach ships as-is\n"
        "7. **Alternative approaches**: If CONCERNS/REJECT, suggest alternatives\n"
        "8. **Sources**: URLs for every externally-sourced claim, or "
        "'no external claims made'\n\n"
        "Skip sections 4-7 if they have zero findings. Sections 3 and 8 are "
        "mandatory. Do not repeat yourself."
    )

    banner = _anchor_warning_banner(hits)
    reserve = len(banner) + (_VERDICT_NOTICE_LEN if caller_hypothesis else 0)
    result = await _advise(
        "\n".join(prompt_parts), "architect_review", session, infra=infra,
        ctx=ctx, web_search=web_search, reserve=reserve,
        images=list(images or []),
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@_lead_tool()
async def code_review(
    code_or_diff: str = "",
    context: str = "",
    focus: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
    images: list[str] = [],
    session: str = "continue",
    sealed_unit: str = "",
    session_id: str = "",
    cwd: str = "",
    ctx: Context = None,
) -> str:
    """
    Deep critical code review from Codex at maximum reasoning power, with
    LIVE web search. Use for independent review of code changes, diffs, or
    implementations. Codex will read files in the working directory if given
    paths.

    DISPATCH BLIND: send the diff and let Codex find the problems. Do NOT
    write "I fixed the race by adding a lock, does that look right" — that
    buys agreement, not review. If you have a belief about what the code
    does or whether it is correct, put it in `caller_hypothesis`.

    Args:
        code_or_diff: Code snippet, diff, or file paths to review
        context: Factual background only — what the feature does, which
            invariants the codebase guarantees, how to run it. A neutral
            scoping field, lint-checked for conclusion language. Do NOT put
            "the bug is X" or "this fix is correct" here.
        focus: Areas to weight (security, performance, correctness, ...).
            Also lint-checked — it scopes attention, it does not state answers.
        caller_hypothesis: OPTIONAL. What you believe about this code — your
            diagnosis, your claim that a fix is correct, your reason for
            thinking it is safe. Presented to Codex as an unverified claim to
            REFUTE, answered with an explicit CONFIRMED/REFUTED/UNPROVEN
            verdict backed by file:line evidence.
        web_search: Live web search, ON by default so version/API/CVE claims
            are checked against the real web instead of recalled. Set False
            to keep the call fully offline — use it when the material is
            sensitive enough that outbound search queries are themselves a
            disclosure risk. Turning it off means version and API claims come
            from stale training data; treat them as unverified.
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation. Slower startup;
            use only when live state matters to the review.
        session: Which codex session this review runs in. "continue"
            (default): the next turn of this workspace's latest completed
            code_review session, so the reviewer starts from its own earlier
            findings and reads of the code; the result's second line names
            the session, its turn count, this call's tokens and the context
            size. "new": a fresh session, for an unrelated change or a
            deliberately independent second opinion. A codex thread id from
            an earlier session line (or a run id) continues that session. A
            session another call is using falls back to a fresh one, with a
            note.
    """
    job = None
    if sealed_unit:
        try:
            status = await _coordinator_call("unit/status", {"unit": sealed_unit})
            target = str(Path(cwd or _get_cwd()).resolve())
            if str(Path(status["run"]["target_cwd"]).resolve()) != target:
                return await _coordinator_notice("[Bound review refused: supply the sealed unit's checkout as cwd.]")
            job = await _coordinator_call("review/prepare", {"unit": sealed_unit, "cwd": _get_cwd()})
            code_or_diff = "IMMUTABLE CANDIDATE BINDING\n" + json.dumps(job["binding"], sort_keys=True) + "\n\nOBJECT DIFF\n" + job["diff"]
            context += "\nReview the bound Git objects and complete object diff. Working-tree contents can belong to other writers. Every finding and verdict must refer to this candidate."
        except Exception as exc:
            return await _coordinator_notice("[Bound review refused before inference: " + str(getattr(exc, "code", type(exc).__name__)) + "]")
    hits = _detect_anchoring({"context": context, "focus": focus})

    prompt_parts = [
        "You are an elite code reviewer who has prevented production outages.",
        "Perform a deep, line-by-line review. Think step by step.",
        "Look for: bugs, security vulnerabilities (OWASP Top 10),",
        "performance issues (N+1 queries, memory leaks), race conditions,",
        "edge cases (empty, null, overflow), error handling gaps,",
        "and violations of clean code principles.",
        "Do NOT compliment the code. Find real problems.",
        "Do NOT ask for new test cases or a test harness: the user's rule is "
        "no tests unless they explicitly ask for them. Where a claim needs "
        "evidence, name the real run that would show it and the result to "
        "expect.",
        "",
        _INDEPENDENCE_PREAMBLE,
        "Code review specifics: read the code before you read the caller's "
        "description of it, and trust the code where they conflict. A diff "
        "that 'fixes' something is a claim — verify the bug existed, verify "
        "the fix closes it, and verify it did not open another. Check the "
        "paths the caller did not mention: error branches, concurrent "
        "callers, empty/null inputs, and the callers of every changed "
        "signature.",
        "",
        _WEB_RESEARCH_DIRECTIVE,
        "Additionally, for code: verify library/API usage against current "
        "upstream docs rather than memory — signatures, deprecations, and "
        "security guidance move. Check whether any dependency touched here "
        "has a known CVE.",
        "",
        _CAPABILITY_HUNT,
        "",
        f"## Code to review\n```\n{code_or_diff}\n```",
    ]
    if context:
        prompt_parts.append(f"\n## Background (factual context, not findings)\n{context}")
    if focus:
        prompt_parts.append(f"\n## Focus areas\n{focus}")

    prompt_parts.append(_hypothesis_block(caller_hypothesis))
    prompt_parts.append(_neutralizer(hits))

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Get straight to findings.\n\n"
        "**Verdict**: Ship it / Needs changes / Do not ship\n"
        "**Findings** (highest severity first, one per line):\n"
        "  [CRITICAL/HIGH/MEDIUM/LOW] file:line — issue → fix\n"
        "  (include code snippets only for CRITICAL/HIGH fixes)\n"
        "**Where I disagree with the caller's framing**: mandatory — state "
        "it, or write 'none — framing held up'\n"
        "**Sources**: URLs for every externally-sourced claim (API docs, "
        "CVEs, version claims), or 'no external claims made'\n\n"
        "Skip the findings section only if there are genuinely zero findings. "
        "Do not repeat yourself."
    )

    banner = _anchor_warning_banner(hits)
    reserve = len(banner) + (_VERDICT_NOTICE_LEN if caller_hypothesis else 0)
    completion = {}
    result = await _advise(
        "\n".join(prompt_parts), "code_review", session, infra=infra,
        ctx=ctx, web_search=web_search, reserve=reserve,
        images=list(images or []), result_out=completion, review_job=job["job"] if job else "",
    )
    evidence = ""
    if job:
        try:
            finished = await _coordinator_call("review/complete", {"job": job["job"],
                "completion": completion.get("completion", {"run": (completion.get("state") or {}).get("run_tag"),
                    "status": "failed", "completed": False, "answer": ""})})
            evidence = "\n[Bound review]\n" + json.dumps(finished, sort_keys=True)
        except Exception as exc:
            evidence = f"\n[Review job {job['job']} requires result collection: {getattr(exc, 'code', type(exc).__name__)}. Passing evidence was not confirmed.]"
    return await _coordinator_notice(banner + _verdict_missing_notice(caller_hypothesis, result) + result + evidence)


@mcp.tool()
async def research(
    topic: str,
    constraints: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool | None = None,
    images: list[str] = [],
    session: str = "new",
    ctx: Context = None,
) -> str:
    """
    Deep technical research using Codex's LIVE web search and full reasoning.
    Always runs at maximum depth. Use for up-to-date information, library
    comparisons, best practices, or technical investigation.

    DISPATCH BLIND: ask the open question ("which X should we use for Y, and
    why") rather than seeking support for an answer you already picked. A
    leading question returns a supporting brief, not research. Put your
    current pick in `caller_hypothesis` and it gets stress-tested instead.

    Args:
        topic: What to research — be specific. Phrase it as an open question.
        constraints: Hard constraints that bound the answer — versions,
            frameworks, platforms, licence limits, date ranges. Neutral
            scoping field, lint-checked for conclusion language.
        caller_hypothesis: OPTIONAL. The answer you currently expect or the
            option you are leaning toward. Presented as an unverified claim
            to REFUTE, with a required CONFIRMED/REFUTED/UNPROVEN verdict.
        web_search: Live web search, ON by default so version/API/CVE claims
            are checked against the real web instead of recalled. Set False
            to keep the call fully offline — use it when the material is
            sensitive enough that outbound search queries are themselves a
            disclosure risk. Turning it off means version and API claims come
            from stale training data; treat them as unverified.
        infra: Live-infrastructure access (SSH, live DB, Dokploy, logs, your
            MCP servers) for read-only investigation. Default None = AUTO:
            enabled only for questions about THIS project's live systems
            (a liveness word AND an infra noun — "why is our production
            database slow" yes; "compare Postgres vs MySQL indexing" no).
            True/False force it. Live WEB research never needs this — web
            search is live in every mode. When you already know the access
            path (host, DB, container), STATE IT IN THE TOPIC so codex uses
            it directly; its own discovery is the fallback, not the plan.
        images: Local image file paths (UI screenshots, diagrams, error
            dialogs) to attach — codex views them natively, so visual
            references beat prose descriptions. Missing paths are rejected
            loudly before any spend.
        session: "new" (default): a fresh codex session. "continue": the next
            turn of this workspace's latest completed research session, for a
            follow-up that should build on the earlier answer. A codex thread
            id from an earlier result's session line (or a run id) continues
            that session.
    """
    # infra: None = AUTO (on only for live-systems questions), True/False =
    # explicit and always honoured. Never switch mode silently — an
    # auto-enabled full-access run says so in the result.
    infra_notice = ""
    if infra is None:
        infra, why = _looks_infra_shaped(f"{topic} {constraints}")
        if infra:
            infra_notice = (
                f"[infra mode AUTO-ENABLED — {why}. Codex ran with live "
                f"infrastructure access (shell/network + your MCP servers), "
                f"read-only by instruction. Pass infra=false to force it off.]\n\n"
            )
    hits = _detect_anchoring({"topic": topic, "constraints": constraints})

    prompt_parts = [
        "You are a senior technical researcher with LIVE web access.",
        "Search the web extensively for current, accurate information.",
        "Think deeply. Cross-reference multiple INDEPENDENT sources.",
        "Cite URLs for every claim. Distinguish facts from opinions.",
        "Flag anything uncertain or conflicting.",
        "",
        _INDEPENDENCE_PREAMBLE,
        "Research specifics: run the search you would run if the caller had "
        "expressed no preference at all. Actively search for evidence AGAINST "
        "the option the question seems to favour — known failure reports, "
        "migration-away posts, open issues, benchmark rebuttals. A research "
        "answer that found no downsides for its recommendation is an "
        "incomplete search, not a clean result.",
        "",
        _WEB_RESEARCH_DIRECTIVE,
        "Research-grade sourcing: a blog post is weaker than the project's "
        "own docs; a docs page is weaker than the release notes or source. "
        "Where sources conflict, say so and say which you trust and why. "
        "Give the retrieval date for anything version- or price-sensitive.",
        "",
        f"## Research topic\n{topic}",
    ]
    if constraints:
        prompt_parts.append(f"\n## Hard constraints\n{constraints}")

    prompt_parts.append(_hypothesis_block(caller_hypothesis))
    prompt_parts.append(_neutralizer(hits))

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Lead with the answer.\n\n"
        "**Recommendation**: Your pick + reasoning (HIGH/MEDIUM/LOW confidence)\n"
        "**Key findings**: Bulleted, one line each — with source URLs\n"
        "**Trade-offs**: Brief pros/cons per approach\n"
        "**Evidence against my own recommendation**: mandatory — the "
        "strongest counter-case you found, or state that you searched for one "
        "and what you searched\n"
        "**Sources**: every URL used, with retrieval date for version/price "
        "claims\n\n"
        "Do not repeat yourself."
    )

    banner = _anchor_warning_banner(hits)
    reserve = (len(banner) + len(infra_notice)
               + (_VERDICT_NOTICE_LEN if caller_hypothesis else 0))
    result = await _advise(
        "\n".join(prompt_parts), "research", session, infra=bool(infra),
        ctx=ctx, web_search=web_search, reserve=reserve,
        images=list(images or []),
    )
    return (banner + infra_notice
            + _verdict_missing_notice(caller_hypothesis, result) + result)


@mcp.tool()
async def codex_query(
    prompt: str,
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
    images: list[str] = [],
    session: str = "new",
    ctx: Context = None,
) -> str:
    """
    Freeform deep query to Codex at maximum reasoning power, with LIVE web
    search. Use for anything — explanations, comparisons, debugging
    hypotheses, or any task where a second AI perspective is valuable.
    Codex can read files in the current working directory.

    DISPATCH BLIND: ask the question, don't pitch the answer. "Why does X
    fail under Y?" gets you analysis; "X fails because of Z, right?" gets you
    agreement with Z. Put your theory in `caller_hypothesis` to have it
    attacked rather than confirmed.

    Args:
        prompt: The question or task for Codex. Phrase it neutrally — this
            field is lint-checked for conclusion language.
        caller_hypothesis: OPTIONAL. Your current theory or expected answer.
            Presented as an unverified claim to REFUTE, with a required
            CONFIRMED/REFUTED/UNPROVEN verdict.
        web_search: Live web search, ON by default so version/API/CVE claims
            are checked against the real web instead of recalled. Set False
            to keep the call fully offline — use it when the material is
            sensitive enough that outbound search queries are themselves a
            disclosure risk. Turning it off means version and API claims come
            from stale training data; treat them as unverified.
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation.
        images: Local image file paths (UI screenshots, diagrams, error
            dialogs) to attach — codex views them natively, so visual
            references beat prose descriptions. Missing paths are rejected
            loudly before any spend.
        session: "new" (default): a fresh codex session. "continue": the next
            turn of this workspace's latest completed codex_query session,
            for a follow-up question that should build on the earlier
            answer. A codex thread id from an earlier result's session line
            (or a run id) continues that session.
    """
    hits = _detect_anchoring({"prompt": prompt})

    preamble = (
        "Think deeply and step by step. Be thorough and precise. "
        "If you need to read files, do so. If you need to search the web, do so. "
        "Provide your analysis with evidence and reasoning. "
        "Keep your response concise — under 1500 words. No filler or preamble.\n\n"
        + _INDEPENDENCE_PREAMBLE
        + "\n"
        + _WEB_RESEARCH_DIRECTIVE
        + "\n## Question\n"
    )
    body = (
        prompt
        + _hypothesis_block(caller_hypothesis)
        + _neutralizer(hits)
        + "\n\nEnd your answer with a **Sources** line: every URL used, or "
        "'no external claims made'. If your conclusion contradicts what the "
        "question presupposed, say so explicitly rather than answering "
        "around it."
    )
    banner = _anchor_warning_banner(hits)
    reserve = len(banner) + (_VERDICT_NOTICE_LEN if caller_hypothesis else 0)
    result = await _advise(
        preamble + body, "codex_query", session, infra=infra,
        ctx=ctx, web_search=web_search, reserve=reserve,
        images=list(images or []),
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


def _analysis_note(before: dict, after: dict, phase2: bool) -> str:
    """What abraham's ANALYSIS phase — full access, read-only only by
    instruction — left changed in the tree, on every way out of it, by
    content (architect review and code review of 2026-10-18): "" when
    nothing changed and phase 2 follows."""
    if not before["ok"] or not after["ok"]:
        side, why = ("before", before["reason"]) if not before["ok"] else ("after", after["reason"])
        return (
            "⚠ whether the ANALYSIS phase changed the tree is UNKNOWN: the tree "
            f"could not be read {side} it ({why}) — review the whole diff.\n\n"
        )
    changed = [ln for _, ln in _tree_changes(before, after)]
    if before["head"] != after["head"]:
        changed.append(f"HEAD MOVED {before['head'][:9] or '(none)'} → {after['head'][:9] or '(none)'}")
    if not changed:
        return "" if phase2 else "The tree is as it was before the run.\n\n"
    where = ("the changed-files report below counts them as there before phase 2"
             if phase2 else "phase 2 never ran, and they are on disk now")
    return (
        "⚠ the tree changed during the ANALYSIS phase, which was told not to "
        f"modify files (or another writer changed it meanwhile); {where} — "
        "review them in the diff:\n" + "\n".join(f"  {ln}" for ln in changed[:20])
        + (f"\n  … +{len(changed) - 20} more" if len(changed) > 20 else "")
        + "\n\n"
    )


async def _coordinator_call(method: str, params: dict | None = None, *, start: bool = False,
                            key: str | None = None, timeout: float = 90) -> Any:
    from coordinator.client import Client
    client = Client(timeout=timeout)
    if start:
        await asyncio.to_thread(client.ensure, python=sys.executable, inherited_fds=_VENV_LOCK_FDS)
        deadline = time.monotonic() + 90
        while not (await asyncio.to_thread(client.call, "daemon/status"))["ready"]:
            if time.monotonic() >= deadline:
                from coordinator.client import CoordinatorError
                raise CoordinatorError("COORDINATOR_RECOVERING", "Coordinator recovery is still in progress")
            await asyncio.sleep(0.2)
    return await asyncio.to_thread(client.call, method, params, key)


async def _coordinator_notice(text: str) -> str:
    notice = "[Coordinator: unread lead messages; use coordinator_inbox to read and explicitly acknowledge.]"
    if notice in text:
        return text
    try:
        inbox = await _coordinator_call("message/inbox", {"recipient": "lead", "limit": 1}, timeout=2)
        if inbox["unread"]:
            return text.rstrip() + "\n" + notice
    except Exception:
        pass
    return text


async def _native_runs(limit: int = 50) -> dict:
    from coordinator.client import state_root
    if not state_root().exists():
        # No coordinator state: no native run ever existed here, so the exec runs are all of them.
        return {"available": True, "data": [], "reason": None}
    try:
        try:
            result = await _coordinator_call("run/list", {"cwd": _get_cwd(), "limit": limit}, timeout=3)
        except (FileNotFoundError, ConnectionRefusedError):
            # Native runs outlive the daemon in its ledger (a reboot, a crash): start it to list them.
            result = await _coordinator_call("run/list", {"cwd": _get_cwd(), "limit": limit}, start=True, timeout=3)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("Malformed native discovery")
        return {"available": True, "data": result["data"], "reason": None}
    except Exception as exc:
        return {"available": False, "data": [], "reason": str(getattr(exc, "code", type(exc).__name__))}


def _native_unavailable(discovery: dict) -> str:
    return "[Native coordinator unavailable: " + discovery["reason"] + ". Native runs could not be inspected; explicit exec IDs remain usable.]"


async def _native_run_action(method: str, run: str, **params) -> str:
    try:
        value = await _coordinator_call(method, dict(params, run=run, cwd=_get_cwd()), timeout=90)
        text = value.get("text") if isinstance(value, dict) else None
        if text is None:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        elif value.get("next_cursor") is not None:
            text += f"\n[Continue: codex_run_log(run='{run}', cursor={value['next_cursor']}, result=True).]"
        return await _coordinator_notice(text)
    except Exception as exc:
        value = exc.envelope() if hasattr(exc, "envelope") else {"code": type(exc).__name__, "message": "Coordinator unavailable; durable state was not replayed"}
        if method == "run/resume" and (params.get("text") or params.get("images")):
            value["delivery"] = "Continuation not delivered or not confirmed; reconcile the saved input identity before retrying."
        return await _coordinator_notice(json.dumps(value))


async def _lead_request(method: str, params: dict, cwd: str = "", session_id: str = "",
                        own: bool = False, unit: str = "") -> str:
    """Typed MCP tools share setup and error notices; no arbitrary RPC is exposed."""
    from coordinator.client import CoordinatorError
    try:
        if os.environ.get("SOFTWARE_WORKFLOWS_ROLE") == "writer":
            raise CoordinatorError("LEAD_ONLY", "Lead controls are unavailable to writer instances")
        checkout = await _coordinator_call("checkout/register", {"path": cwd or _get_cwd()}, start=True)
        params = dict(params)
        mapping = None
        if session_id:
            mapping = await _coordinator_call("lead/session", {"checkout": checkout["id"], "session_id": session_id})
        if own:
            if mapping is None:
                raise CoordinatorError("CLAUDE_SESSION_REQUIRED", "This action needs the actual Claude session_id; the host hook supplies it")
            params["actor"] = mapping["actor"]
        if method.startswith("checkout/") or method in ("claim/list", "unknown/list", "run/status"):
            params["checkout"] = checkout["id"]
        if method.startswith("unit/") or method == "claim/handback":
            unit = unit or (mapping or {}).get("unit", "")
            if not unit:
                raise CoordinatorError("UNIT_REQUIRED", "Supply a unit ID or the actual Claude session_id")
            status = await _coordinator_call("unit/status", {"unit": unit})
            if status["unit"]["checkout"] != checkout["id"]:
                raise CoordinatorError("UNIT_SCOPE", "Unit belongs to another checkout; supply its cwd explicitly")
            params["unit"] = unit
        value = await _coordinator_call(method, params, timeout=180)
        return await _coordinator_notice(json.dumps(value, ensure_ascii=False, indent=2))
    except Exception as exc:
        value = exc.envelope() if hasattr(exc, "envelope") else {"code": type(exc).__name__, "message": "Coordinator operation did not complete; inspect durable status"}
        return await _coordinator_notice(json.dumps(value, ensure_ascii=False))


@_lead_tool()
async def coordinator_claims(action: str = "list", paths: list[str] | None = None, unit: str = "",
                             cwd: str = "", session_id: str = "") -> str:
    """List claims/custody; acquire paths for this Claude session; request release;
    or authorize handback of an idle unit. Actions: list, acquire, release, handback.
    A conflicting claim names its holder. Reading does not acknowledge messages.
    """
    methods = {"list": "claim/list", "acquire": "claim/acquire", "release": "claim/release-request", "handback": "claim/handback"}
    if action not in methods:
        return await _coordinator_notice("[Unknown claims action: use list, acquire, release or handback.]")
    if action in ("acquire", "release") and not paths:
        return await _coordinator_notice("[Supply a nonempty paths list for acquire or release. Handback omission selects all eligible claims; [] selects none.]")
    return await _lead_request(methods[action], {"paths": paths} if action != "list" else {}, cwd, session_id,
                               own=action in ("acquire", "release"), unit=unit)


@_lead_tool()
async def coordinator_send(recipient: str, text: str, wake: str = "none", cwd: str = "", session_id: str = "") -> str:
    """Send to a run, unit, actor or lead. none leaves idle recipients idle;
    queue stores a native queued turn; start explicitly wakes an idle recipient.
    Persisted, submitted, entered_context and acknowledged are distinct states.
    """
    return await _lead_request("message/send", {"recipient": recipient, "text": text, "wake": wake}, cwd, session_id)


@_lead_tool()
async def coordinator_inbox(acknowledge: list[str] = [], cursor: str = "", limit: int = 100,
                            cwd: str = "", session_id: str = "") -> str:
    """Read lead messages with a cursor. Only IDs explicitly supplied in
    acknowledge are marked acknowledged; reading status/inbox never does that.
    """
    for message in acknowledge:
        answer = await _lead_request("message/ack", {"message": message}, cwd, session_id)
        if '"code"' in answer:
            return answer
    return await _lead_request("message/inbox", {"recipient": "lead", "cursor": cursor, "limit": limit}, cwd, session_id)


@_lead_tool()
async def coordinator_unit(action: str = "status", unit: str = "", cwd: str = "", session_id: str = "",
                           paths: list[str] | None = None, unknown: str = "", revision: str = "",
                           revisions: dict[str, str | None] | None = None, custody: str = "") -> str:
    """Inspect status/object diff/UNKNOWN, reconcile effects or custody, restore a
    known revision, reopen a seal, recover publication, or abandon an unapplied
    publication. Actions: status, diff, unknown, reconcile, restore, reopen,
    recover, abandon. Restoration writes the chosen known bytes and grants no
    authorship to unexplained bytes. A new exact edit is still required.
    """
    methods = {"status": "unit/status" if unit or session_id else "run/status", "diff": "unit/diff",
               "unknown": "unknown/list", "reconcile": "custody/reconcile" if custody else "checkout/reconcile",
               "restore": "unknown/restore", "reopen": "unit/reopen", "recover": "unit/recover", "abandon": "unit/recover"}
    if action not in methods:
        return await _coordinator_notice("[Unsupported unit action.]")
    params = {}
    if action in ("unknown", "reconcile") and paths is not None:
        params["paths"] = paths
    if action == "reconcile" and custody:
        params = {"custody": custody}
    if action == "restore":
        params = {"unknown": unknown, "revision": revision, "revisions": revisions or {}}
    if action == "abandon":
        params["action"] = "abandon"
    return await _lead_request(methods[action], params, cwd, session_id, own=action == "restore", unit=unit)


@_lead_tool()
async def coordinator_seal(unit: str, validation: list[dict] = [], cwd: str = "", session_id: str = "") -> str:
    """Freeze this idle unit's receipt-backed candidate and validation evidence.
    UNKNOWN or unresolved effects refuse. Review the returned immutable candidate
    with code_review(sealed_unit=unit) before publication.
    """
    return await _lead_request("unit/seal", {"validation": validation}, cwd, session_id, unit=unit)


@_lead_tool()
async def coordinator_publish(unit: str, review: str, metadata: dict, cwd: str = "", session_id: str = "") -> str:
    """Publish the exact sealed/reviewed candidate using explicit metadata:
    message, author_name, author_email, author_date, committer_name,
    committer_email, committer_date. No acknowledgement bypass exists for stale
    parent/seal/provenance, missing review or UNKNOWN. No working-file rewrite.
    """
    return await _lead_request("unit/publish", {"review": review, "metadata": metadata}, cwd, session_id, unit=unit)


async def _native_abraham(task: str, context: str, constraints: str, images: list[str], cwd: str,
                          claims: list[str] | None, allow_dirty: bool, session_id: str,
                          ctx: Any, request_t0: float) -> tuple[str | None, str]:
    """Return a durable native result, or a proven pre-dispatch fallback reason."""
    from coordinator.client import CoordinatorError
    import uuid
    model = _get_codex_model()
    runid = "run_" + uuid.uuid4().hex
    phase = "startup"
    live_path, live_fh, stream_fh, log_tag = _open_live_log("abraham-native")
    def emit(message):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{runid}] {message}\n"
        for stream in (live_fh, stream_fh):
            if stream:
                with contextlib.suppress(OSError):
                    stream.write(line)
                    stream.flush()
    activity = {"activity": "starting coordinator; first native admission takes tens of seconds"}
    emit(activity["activity"])
    heartbeat = asyncio.create_task(_heartbeat_loop(ctx, request_t0, request_t0, activity, model, emit)) if ctx else None
    try:
        top = await asyncio.to_thread(subprocess.run, _treedigest.git_argv(cwd, ("rev-parse", "--show-toplevel")),
                                      env=_treedigest.git_env(), capture_output=True, text=True, timeout=10)
        if top.returncode:
            return None, "CHECKOUT_ROOT"
        root = str(Path(top.stdout.strip()).resolve())
        image_paths = [str(Path(p).resolve()) for p in images]
        if any(not Path(p).is_file() for p in image_paths):
            return "[Codex model: " + model + " | tool:abraham | status:error | tree:unknown]\nImage input is missing; nothing was dispatched.", ""
        checkout = await _coordinator_call("checkout/register", {"path": root}, start=True)
        compact, why = _auto_compact_limit(model)
        workflow = {"kind": "abraham", "request_id": runid, "dispatch_cwd": str(Path(_get_cwd()).resolve()),
                    "analysis": INFRA_INSTRUCTIONS + _advisor_context() + "\n\n" + _abraham_analysis_prompt(task, context, constraints, native=True),
                    "implementation": IMPLEMENTATION_INSTRUCTIONS + _abraham_implementation_prompt(task, constraints, "<C2_ANALYSIS_BRIEF>"),
                    "images": image_paths, "model": model, "effort": "max", "compact_limit": compact,
                    "allow_dirty": allow_dirty, "deadline": time.time() + MAX_RUNTIME_SECONDS, "session_id": session_id}
        params = {"checkout": checkout["id"], "mode": "native", "task": task, "paths": claims, "workflow": workflow}
        if os.environ.get("SOFTWARE_WORKFLOWS_FIXTURE"):
            params["fixture"] = os.environ["SOFTWARE_WORKFLOWS_FIXTURE"]
        emit(f"admission requested; autocompact={compact or 'vendor-default'} ({why}); log={live_path}")
        phase = "dispatch"
        result = await _coordinator_call("run/start", params, key=runid, timeout=1800)
        emit("durable native handle " + json.dumps(result))
        phase = "observing"
        previous = None
        while True:
            result = await _coordinator_call("run/result", {"run": runid})
            current = (result["phase"], result["state"])
            activity["activity"] = "native " + str(current) + " " + runid
            if current != previous:
                emit(activity["activity"])
                previous = current
            if result["state"] in {"completed", "failed", "blocked", "interrupted", "cancelled", "refused", "submission_uncertain"}:
                text = result["text"]
                if result.get("next_cursor") is not None:
                    text += f"\n[Result continues: codex_run_log(run='{runid}', cursor={result['next_cursor']}, result=True).]"
                return await _coordinator_notice(text), ""
            if time.monotonic() - request_t0 >= MAX_RUNTIME_SECONDS:
                return await _coordinator_notice(result["text"] + "\n[Observation budget ended. Collect this durable run with codex_resume_run; no phase will be replayed.]"), ""
            await asyncio.sleep(1)
    except CoordinatorError as exc:
        emit("coordinator refusal " + exc.code)
        if exc.code in {"DIRTY_UNEXPLAINED", "CLAIM_CONFLICT", "CLAIM_PATHS", "WORKFLOW_IMAGES", "WORKFLOW_FIELDS"}:
            return await _coordinator_notice(f"[Codex model: {model} | tool:abraham | status:error | tree:unknown]\n" + json.dumps(exc.envelope())), ""
        if phase == "startup" or exc.details.get("safe_to_fallback"):
            return None, exc.code
        return await _coordinator_notice(f"[Codex model: {model} | tool:abraham | status:error | tree:unknown]\n"
                 f"Native dispatch requires reconciliation ({exc.code}). Durable handle: {runid}. "
                 "Use codex_runs and codex_resume_run; execution was not replayed through exec."), ""
    except (OSError, ValueError) as exc:
        if phase == "startup":
            return None, "COORDINATOR_START_" + type(exc).__name__
        return f"[Codex model: {model} | tool:abraham | status:error | tree:unknown]\nNative handle {runid}; RPC outcome uncertain ({type(exc).__name__}). Collect or reconcile; do not redispatch.", ""
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        for stream in (live_fh, stream_fh):
            if stream:
                stream.close()


def _abraham_analysis_prompt(task: str, context: str, constraints: str, native: bool = False) -> str:
    analysis_parts = [
        "PHASE 1 of 2 — ANALYSIS ONLY. You are a senior engineer "
        "preparing an implementation. A SEPARATE process will do the "
        "writing, with the same full access you have (no sandbox, "
        "network, live web search, MCP servers and live "
        "infrastructure); it starts from ONLY your final message. So "
        "your final message must be a complete IMPLEMENTATION BRIEF:",
        "",
        "## Findings — how the relevant system actually works, with "
        "file:line references (trace real call paths, don't guess).",
        "## Live state — what the live infrastructure shows "
        "(read-only investigation) where it bears on the task.",
        "## External facts — every API/version/vendor behavior the "
        "implementer must rely on, verified against current primary "
        "sources, with URLs and EXACT values.",
    ]
    analysis_parts += [
        "## Plan — the minimal COMPLETE change: ordered edits per "
        "file, including error paths and edge cases; no artificial "
        "caps, no silently deferred scope.",
        "## Risks — what could break, and what to check.",
        "## Verification — how to see it work: the real runs to make "
        "(the changed command, app or endpoint, plus the existing "
        "build/linters) and the results to expect. No test cases "
        "unless the user explicitly asked for them.",
        "",
        "Do NOT modify any file in this phase.",
        "",
        "## Task",
        task,
    ]
    if context:
        analysis_parts += ["", "## Context (from the caller)", context]
    if constraints:
        analysis_parts += [
            "", "## Constraints (hard boundaries)", constraints]
    prompt = "\n".join(analysis_parts)
    if native:
        prompt = prompt.replace("A SEPARATE process will do the writing", "A second turn in this native thread will do the writing").replace("it starts from ONLY your final message", "it receives your persisted final brief in the next turn")
    return prompt


def _abraham_implementation_prompt(task: str, constraints: str, brief: str) -> str:
    impl_parts = [
        "PHASE 2 of 2 — IMPLEMENT. A read-only analysis run has "
        "already investigated this task; its brief is below. You have "
        "full access: re-read the code, look things up and reach the "
        "project's systems as you need — the brief is where you start, "
        "not the only thing you may know.",
        "",
        "WORKFLOW: follow the brief's Plan (deviate only where the "
        "code proves it wrong, and say so); match the surrounding "
        "code's style; reuse existing helpers; then run the brief's "
        "Verification checks that exist and fix what they catch; "
        "finally REPORT: what changed and why, file by file; what you "
        "verified and how; any deviation from the brief and its "
        "reason.",
        "",
        "## Task",
        task,
    ]
    if constraints:
        impl_parts += ["", "## Constraints (hard boundaries)", constraints]
    impl_parts += ["", "## Implementation brief (from the analysis phase)",
                   brief]
    return "\n".join(impl_parts)


@_lead_tool()
async def abraham(
    task: str,
    context: str = "",
    constraints: str = "",
    web_search: bool = True,
    infra: bool = True,
    allow_dirty: bool = False,
    images: list[str] = [],
    cwd: str = "",
    full_access: bool = True,
    claims: list[str] | None = None,
    session_id: str = "",
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """
    WRITE MODE — the one tool that EDITS FILES, as two phases in one call,
    BOTH with full access: no sandbox, network, live web search, your MCP
    servers and your live infrastructure (user ruling 2026-10-18; the sealed
    mode was removed in 2.0.0):

    1. ANALYSIS: codex investigates the codebase, the live web and the live
       infrastructure (read-only by instruction) and writes an
       implementation brief.
    2. IMPLEMENTATION: the coordinator submits a second turn on the same
       native thread after successful analysis and settled operations, runs the
       project's real checks and reports their real results.

    A git work tree is required. Concurrent native writers coordinate path
    claims, exact edit receipts and process custody. Unexplained dirt refuses
    dispatch unless allow_dirty=true; this flag never grants attribution or
    clears UNKNOWN. A supervisor-held compatibility lease excludes legacy exec
    writers. Native results identify the run/unit, policy and receipt authors;
    complete results and events remain retrievable through the run tools.
    Startup/admission failure before production execution can select the
    explicit, exclusive two-process exec fallback, with its failed check named.
    Started or uncertain native work is never replayed through exec. All
    outcomes leave changes uncommitted for the lead to review and record.
    Long implementation runs auto-compact codex's context at ~65% of the
    model window (read from the deployed binary's own registry, never
    guessed).

    This is a DIRECTIVE dispatch, not an advisory one: state the desired
    outcome plainly — no anchoring lint, no caller_hypothesis; the contract
    is "implement", not "judge". Use the coordinator tools to exchange
    messages, inspect claims and arrange handback before overlapping edits.

    Args:
        task: What to build/fix/change, with the desired outcome and
            acceptance criteria; repro steps for bugfixes.
        context: What you already know — relevant paths, prior findings,
            decisions already made and why, and the access paths you know
            (a key-based SSH host, a `docker exec` target, a local test
            database). Never credentials: codex finds its own way in from
            the project's docs and your local configuration.
        constraints: Hard boundaries — files/areas NOT to touch, APIs to
            keep stable, scope rules beyond the repo's own docs.
        web_search: Live web search in both phases. Always on; False is
            refused.
        infra: Live-infrastructure access (SSH, databases, logs, your MCP
            servers) in both phases. Always on; False is refused.
        allow_dirty: Accept a dirty working tree (default False — see
            safety envelope).
        images: Local image paths (mockups, error screenshots) — attached
            to both phases.
        full_access: Always True; False is refused. For work that must not
            write, use code_review, architect_review, research or
            codex_query.
        cwd: Target git work tree, when it is not the server's own cwd —
            any git repository on this machine: a repository below this
            workspace (a multi-repo project root is not itself a work tree)
            or another project. A relative path is taken from the server's
            cwd. The run is listed and resumable from this session's folder
            and from the target. Empty = server cwd.
        claims: Initial repository-relative paths. Omit to acquire claims as
            native edits declare their paths; an overlap names its holder.
        session_id: Actual Claude session ID supplied by the host hook or
            explicitly by the caller. It is never inferred from MCP transport.
    """
    if not task.strip():
        return "[abraham refused: empty task — state what to implement.]"
    off = [name for name, value in (("full_access", full_access), ("infra", infra),
                                    ("web_search", web_search)) if not value]
    if off:
        return (
            f"[abraham refused: {', '.join(name + '=false' for name in off)} — every "
            "abraham run has full access, infra and live web search in both "
            "phases since 2.0.0 (user ruling 2026-10-18); there is no sealed "
            "mode. For work that must not write, use code_review, "
            "architect_review, research or codex_query.]"
        )
    sealed = _sealed_env_refusal()
    if sealed:
        return f"[abraham refused: {sealed}]"
    if _IS_WINDOWS:
        # Fail closed BEFORE any precondition: the one-writer lock cannot
        # follow the codex child on Windows (a byte-range lock's ownership
        # does not transfer with an inherited handle), so a write run could
        # outlive its server unlocked.
        return (
            "[abraham is not available on Windows: the one-writer lock cannot "
            "follow the codex child there, so a write run could outlive its "
            "server unlocked. Run abraham from macOS or Linux.]"
        )

    # Anchor the heartbeat deadline to the REQUEST, not to each codex run:
    # phase 2 starts long after the client's ~120s backgrounding, and a
    # per-run clock would resurrect dead-token sends there.
    request_t0 = time.monotonic()

    # Dispatch tracer FIRST (global journal, off-thread so a slow disk
    # cannot stall the event loop): "the call never entered the function"
    # vs "entered and stalled later" must be decidable from artifacts alone
    # (Windows, 2026-08-21 — valid-cwd dispatches left no trace anywhere).
    await asyncio.to_thread(
        _journal,
        {"run": "abraham-dispatch", "phase": "dispatch",
         "ts": time.time(), "cwd": _get_cwd(), "target": cwd},
    )

    if cwd:
        # No workspace fence (user ruling 2026-10-18: "everything each and
        # every repo"): any git work tree is a target. What keeps a write
        # safe works in every repository — git as the undo, the tree's write
        # lock, the changed-files report — and the run is journaled with
        # both its target and this session's folder (dispatch_cwd).
        target = Path(cwd)
        if not target.is_absolute():
            target = Path(_get_cwd()) / target
        target = target.resolve()
        if not target.is_dir():
            return f"[abraham refused: cwd '{cwd}' is not a directory.]"
        cwd = str(target)
    else:
        cwd = _get_cwd()
    ok, dirty, _head = _git_state(cwd)
    if not ok:
        return (
            f"[abraham refused: {cwd} is not inside a git work tree, or its "
            f"state could not be read ({_GIT_STATE_REASON}). "
            "Autonomous writes without version control have no undo.]"
        )
    native_result, fallback = await _native_abraham(task, context, constraints, list(images or []), cwd,
                                                   claims, allow_dirty, session_id, ctx, request_t0)
    if native_result is not None:
        return native_result
    fallback_note = f"[Native admission/startup failed: {fallback}. Using the exclusive legacy exec backend.]\n"
    if dirty and not allow_dirty:
        sample = "\n".join(f"  {ln}" for ln in sorted(dirty)[:10])
        more = f"\n  … +{len(dirty) - 10} more" if len(dirty) > 10 else ""
        return (
            fallback_note + "[abraham refused: the working tree is DIRTY and the implementer "
            "may overwrite uncommitted work irrecoverably (committed work is "
            "always recoverable).\n"
            f"{sample}{more}\n"
            "Commit that work first — or pass allow_dirty=true to accept the "
            "risk; the final report will separate this run's changes from the "
            "pre-existing dirt.]"
        )

    # One writer per tree — the authoritative lock is held across BOTH
    # phases (the brief describes the tree as analyzed; another writer
    # landing between the phases would invalidate it). The journal check
    # stays as an advisory belt for server processes running older code.
    live_run = _active_write_run(cwd)
    if live_run:
        return (
            fallback_note + f"[abraham refused: write run {live_run} is still LIVE in this "
            "workspace (one writer per tree). Wait for it or cancel it; an "
            "interrupted write run is resumable via codex_resume_run.]"
        )
    got_lock, holder = _acquire_write_lock(cwd, "abraham")
    if not got_lock:
        return (
            fallback_note + f"[abraham refused: another write run holds this tree's lock "
            f"({holder}). One writer per tree.]"
        )
    try:
        # Phase 1 has full access and is read-only only by instruction: its
        # changes must not be filed as pre-existing dirt by phase 2's report
        # (architect review, 2026-10-18), so the tree is read on both sides.
        before0 = _tree_snapshot(cwd)
        # ---- Phase 1: read-only analysis → implementation brief ----
        brief = await _run_codex(
            _abraham_analysis_prompt(task, context, constraints),
            infra=True, ctx=ctx, web_search=True,
            images=list(images or []),
            tool_name="abraham", workdir=cwd,
            request_started=request_t0,
            custody_cwd=cwd,  # abraham holds this tree's write lock NOW (round 17)
        )
        # POSITIVE gate (round 14): phase 2 starts only on the server-stamped
        # ok signature with a nonempty brief body — enumerating failure
        # prefixes let unmatched forms ("[dispatch refused: …]",
        # status:timeout partials) through as the "implementation brief".
        # ANCHORED (round 24): the FIRST LINE must be exactly the server's
        # ok header — a substring search over the first 300 chars was
        # forgeable by failure text that happened to contain the marker.
        first_line, _, rest = brief.partition("\n")
        ok_header = re.fullmatch(
            r"\[Codex model: [^\]\n]* \| tool:abraham \| status:ok \| "
            r"tree:(?:[0-9a-fA-F]{4,64}|nogit|unknown)\]", first_line)
        brief_body = rest.strip()
        if not ok_header or not brief_body:
            return (
                brief.partition("\n")[0] + "\n" + fallback_note +
                "[abraham: ANALYSIS phase failed — phase 2 never ran.]\n\n"
                + _analysis_note(before0, _tree_snapshot(cwd), phase2=False)
                + brief.partition("\n")[2]
            )
        phase1_note = _analysis_note(before0, _tree_snapshot(cwd), phase2=True)

        # ---- Phase 2: implementation, full access ----
        result = await _run_codex(
            _abraham_implementation_prompt(task, constraints, brief),
            write=True, infra=False, ctx=ctx, web_search=True,
            images=list(images or []),
            tool_name="abraham", workdir=cwd,
            request_started=request_t0,
        )
        return (
            result.partition("\n")[0] + "\n" + fallback_note +
            "[abraham — phase 1 analyzed (read-only by instruction; infra, "
            "live web, MCP servers); phase 2 implemented (FULL ACCESS: no "
            "sandbox, network, live web and MCP servers — "
            "--dangerously-bypass-approvals-and-sandbox)]\n\n"
            + phase1_note + result.partition("\n")[2]
        )
    finally:
        _release_write_lock(cwd)


async def _resume_run(
    run: str = "",
    nudge: str = "",
    infra: bool | None = None,
    web_search: bool | None = None,
    images: list[str] | None = None,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """
    Continue a codex run that failed, timed out, or was interrupted —
    WITH ITS ORIGINAL CONTEXT, not a re-ask.

    Codex persists each run's session, so a run killed mid-flight (MCP
    server restart, plugin reload, cancelled call, transient API error
    that outlived the automatic retries) can be continued from where it
    stopped. Never re-dispatch the whole question after a failure — resume
    it: the model still has the reasoning and tool output it had built up.

    A finished-but-undelivered run (e.g. the reload killed the call after
    codex answered) returns its stored answer immediately, at no cost —
    unless you pass a nudge, which continues the thread with your new
    instructions instead of replaying the old answer.

    A continuation of a failed or interrupted run RESTATES the original task
    from the run journal: a hard-killed turn may have persisted none of its
    input vendor-side (measured 2026-08-09), so the thread cannot be trusted
    to remember the question. Threads that did retain context see a
    labelled duplicate. A COMPLETED run's thread holds its task already, so
    a nudge to one continues it as is (a follow-up question to an advisory
    session is the tool's own `session` argument).

    SWITCH CAPABILITIES MID-COURSE. A resume may change the run's settings,
    so a run that turns out to need something it didn't have can be rescued
    instead of restarted: cancel the call, then resume the SAME thread — all
    its accumulated reasoning and tool output intact — with new settings and
    new instructions. The classic case: research that needs the live system.

        codex_resume_run(run="codex3·8123", infra=True,
                         nudge="SSH to the app host and check the celery "
                               "queue depth, then finish the analysis")

    Write runs (abraham) resume as write runs, with full access, network
    and live web search (a run that began sealed under 1.x continues with
    full access), the git contract and the changed-files report intact.
    The write axis itself has no override: read runs cannot be escalated to
    write by resuming, and write runs cannot be downgraded.

    Args:
        run: The run id from the failure message ("run id: codex7·21746").
            Omit to resume the most recent recoverable run. Pass "list" to
            see the recent runs and their statuses instead.
        nudge: Instructions for the continuation — additional direction, not
            just "carry on". Default asks for the complete final answer from
            where it left off.
        infra: Change live-infrastructure access for the continuation.
            None (default) inherits the original run's setting; True grants
            shell/network + your MCP servers; False takes it away.
        web_search: Change live web search for the continuation. None
            inherits the original run's setting.
    """
    # WORKSPACE SCOPING (security): the journal is global across every project
    # on this machine. Listing or auto-resuming must not surface another
    # workspace's prompts/answers — an untrusted repo could otherwise drive a
    # cross-project read. Scope discovery to the current cwd; an EXPLICIT id
    # from another workspace is refused rather than silently retrieved. A run
    # belongs to its target tree AND to the folder it was dispatched from.
    # Anchor the heartbeat deadline to THIS request's start: a fresh per-run
    # clock would resurrect dead-token sends (round-2 review, 2026-08-21).
    request_t0 = time.monotonic()
    images = [images] if isinstance(images, str) else list(images or [])
    if any(not isinstance(p, str) or not Path(p).is_file() for p in images):
        missing = [str(p) for p in images if not isinstance(p, str) or not Path(p).is_file()]
        return "[Not delivered: image paths do not exist: " + ", ".join(missing) + "]"
    images = list(dict.fromkeys(str(Path(p).resolve()) for p in images))
    instructed = bool(nudge) or bool(images)
    if images and not nudge:
        nudge = IMAGES_NUDGE

    if run.startswith("run_"):
        if infra is False or web_search is False:
            return await _coordinator_notice("[Not delivered: native Abraham preserves full access and live web; policy downgrades cannot be applied on resume.]")
        return await _native_run_action("run/resume", run, text=nudge, images=images, deadline=time.time() + MAX_RUNTIME_SECONDS)
    if not run:
        discovery = await _native_runs()
        if not discovery["available"]:
            return _native_unavailable(discovery) + " Automatic resume refused because a newer native run may exist."
        candidates = [r for r in discovery["data"] if r["state"] != "completed"]
        latest_exec = max((float(r.get("first_ts") or r.get("ts") or 0) for r in _workspace_runs()), default=0)
        if candidates and candidates[-1]["created"] >= latest_exec:
            if infra is False or web_search is False:
                return await _coordinator_notice("[Not delivered: native Abraham preserves full access and live web; policy downgrades cannot be applied on resume.]")
            return await _native_run_action("run/resume", candidates[-1]["run"], text=nudge, images=images, deadline=time.time() + MAX_RUNTIME_SECONDS)

    cwd = _get_cwd()
    all_runs = _journal_runs()

    # has_start excludes evidence-only journal groups (abraham-dispatch
    # tracers, the old write-probe verdicts) — they are diagnostics, not
    # runs, and cannot be resumed.
    runs = {k: v for k, v in all_runs.items()
            if _run_belongs(v, cwd) and v.get("has_start")}

    if run == "list":
        listing = await codex_runs()
        if instructed:
            listing += '\n[Not delivered: run="list" only lists runs; the nudge and images were not sent.]'
        return listing

    if _IS_WINDOWS:
        # Round 6: read-mode continuations lacked durable claims on Windows —
        # a run claim there cannot be transferred to or survive its owning
        # process (byte-range locks are not inherited, and a parent's exit
        # does not end its children), so a second continuation of the same
        # thread cannot be excluded. Refuse them all.
        return (
            "[continuations (resume/collect/adopt) are not available on "
            "Windows: the run claim cannot outlive or be handed off from the "
            "owning process there, so a second continuation of the same "
            "thread cannot be excluded. Inspect with codex_runs / "
            "codex_run_log and re-dispatch the question.]"
        )

    if run:
        rec = runs.get(run)
        if rec is None:
            # Distinguish "unknown" from "belongs to another workspace" —
            # never return that run, but say why so it isn't a silent miss.
            if run in all_runs:
                return (
                    f"[Run '{run}' belongs to a different workspace and cannot "
                    f"be resumed from here.] Re-dispatch it in its own project."
                )
            known = ", ".join(list(runs)[-5:]) or "(none)"
            return f"[Unknown run id '{run}'.] Recent runs here: {known}"
    else:
        candidates = [
            r for r in runs.values()
            if r.get("status") != "ok" or not r.get("has_end")
        ]
        if not candidates:
            return (
                "[No failed or interrupted codex run to resume in this "
                "workspace — the most recent runs all completed successfully.]"
            )
        rec = candidates[-1]
        run = str(rec.get("run", ""))

    # Finished after all (the answer just never reached the caller) — but
    # only short-circuit when the caller wants THAT answer. An explicit nudge
    # means "continue this thread with new instructions"; returning the old
    # answer instead would silently discard the nudge.
    # DETACHED (outlived its server): collect the still-running or finished
    # process instead of re-asking the thread. Falls through to the thread
    # resume below only when the detached process died without an answer.
    if _is_detached(rec):
        collected = await _collect_detached(rec, run, ctx, request_t0, nudge)
        if collected is not None:
            if instructed:
                collected += "\n[Not delivered with this call: the nudge and images were not sent because this call handled a detached run. Once it has ended, call codex_resume_run again to continue with them.]"
            return collected
        rec = _journal_runs().get(run, rec)
    if (rec.get("has_end") and str(rec.get("status") or "") == "cancelled"
            and not nudge):
        # CANCELLED IS A DECISION (round 9): a bare resume must not resurrect
        # a thread someone deliberately stopped — the collector's None (a
        # refold that found the canceller's terminal record) otherwise fell
        # through to a fresh continuation of the cancelled thread. An
        # explicit nudge is the deliberate override.
        return (
            f"[Run {run} was CANCELLED — not resuming it without explicit "
            f"instructions. Pass a nudge to deliberately continue the thread.]"
        )
    result_file = str(rec.get("result_file") or "")
    if result_file and not nudge and Path(result_file).exists():
        stored = Path(result_file).read_text(encoding="utf-8", errors="replace").strip()
        if stored:
            return (
                f"[Recovered run {run} — it had COMPLETED; returning its "
                f"stored answer (no new model call). Pass a nudge to "
                f"continue the thread instead.]\n\n{stored}"
            )

    tid = str(rec.get("thread_id") or "")
    if not tid:
        return (
            f"[Run {run} has no codex session id — it failed before the "
            f"session started, so there is no context to continue.]\n"
            f"Re-dispatch the original question instead. "
            f"Error: {str(rec.get('error') or '(none)')[:300]}"
        )

    # MID-COURSE SWITCH: the continuation may change the run's capabilities.
    # None = inherit the original run's setting; True/False = override. This is
    # how you rescue a run that turned out to need live infrastructure: cancel
    # it, then resume the SAME thread (all its accumulated reasoning intact)
    # with infra=true and instructions for what to go look at.
    was_infra = bool(rec.get("infra"))
    use_infra = was_infra if infra is None else bool(infra)
    use_web = bool(rec.get("web_search", True)) if web_search is None else bool(web_search)

    # WRITE INHERITS, ALWAYS — deliberately no override parameter. Escalating
    # a read run to write on resume would bypass abraham's dispatch guards
    # (git precondition, one-writer-per-tree, the implementation contract);
    # downgrading a write run mid-implementation would strand half-applied
    # changes behind a read-only sandbox. Re-dispatch through the right tool
    # instead of flipping this axis mid-thread.
    use_write = bool(rec.get("write"))
    resume_custody = str(rec.get("custody_cwd") or "") if not use_write else ""
    was_sealed = use_write and not rec.get("full_access")
    if use_write and _IS_WINDOWS:  # unreachable (all Windows continuations refused above); second fence
        return "[write runs cannot be resumed on Windows in 1.17.x (no inheritable lock); see abraham.]"
    if use_write:
        # A write continuation always runs with full access, network and
        # live web search (2.0.0): a run that began sealed under 1.x gains
        # capability, which strands nothing. An explicit false is refused,
        # as abraham refuses it; infra is part of full access, so the read
        # tools' infra switch has no separate meaning here.
        off = [name for name, value in (("infra", infra), ("web_search", web_search))
               if value is False]
        if off:
            return (
                f"[resume refused: {', '.join(name + '=false' for name in off)} — "
                "a write continuation always has full access, infra and live "
                "web search since 2.0.0; there is no sealed mode.]"
            )
        sealed = _sealed_env_refusal()
        if sealed:
            return f"[resume refused: {sealed}]"
        use_infra, use_web = False, True
        other = _active_write_run(cwd, exclude_run=run)
        if other:
            return (
                f"[resume refused: write run {other} is still LIVE in this "
                "workspace (one writer per tree). Wait for it or cancel it "
                "first.]"
            )

    # GUARD: never resume a thread that is still being written. Two codex
    # processes on one rollout can corrupt it. A run with no end record whose
    # log grew in the last minute is alive, not orphaned.
    if not rec.get("has_end"):
        if _pid_alive(int(rec.get("pid") or 0), str(rec.get("pid_start") or "")) and not _is_detached(rec):
            return (
                f"[Run {run} is still RUNNING (codex pid {rec.get('pid')} is "
                f"alive, attached to another call). Resuming a live thread can "
                f"corrupt its session. Wait for it, watch it with "
                f"codex_run_log, or stop it with codex_cancel_run first.]"
            )
        log_path = str(rec.get("log") or "")
        with contextlib.suppress(OSError):
            if log_path and Path(log_path).is_file():
                idle = time.time() - Path(log_path).stat().st_mtime
                if idle < 60:
                    return (
                        f"[Run {run} is still ACTIVELY RUNNING (its live log was "
                        f"written {int(idle)}s ago). Resuming a live thread can "
                        f"corrupt its session. Cancel that call first (or wait "
                        f"for it to finish), then resume.]"
                    )

    switches = ["sealed → full access (2.0.0 has no sealed mode)"] if was_sealed else []
    if use_infra != was_infra:
        switches.append(f"infra {was_infra} → {use_infra}")
    if use_web != bool(rec.get("web_search", True)):
        switches.append(f"web_search {bool(rec.get('web_search', True))} → {use_web}")
    note = (f"[Resuming {run} with changed settings: {', '.join(switches)}]\n\n"
            if switches else "")

    # RESTATE THE ORIGINAL TASK. MEASURED 2026-08-09: a turn that is truly
    # killed early (process-group kill, mid-turn) may persist NOTHING of its
    # input in the rollout — the resumed model then has no idea what the
    # question was and confidently fabricates (it mined a marker out of the
    # cwd PATH in the regression test). The journal holds the full prompt we
    # sent (advisor context included), so the continuation carries it; a
    # thread that did retain context treats it as a stated duplicate.
    continuation = nudge or RESUME_NUDGE
    original_prompt = str(rec.get("prompt") or "")
    completed = bool(rec.get("has_end")) and str(rec.get("status") or "") == "ok"
    restored_missing = []
    if not completed:
        for img in rec.get("images") or []:
            if not isinstance(img, str):
                continue
            path = str(Path(img).resolve())
            if path in images:
                continue
            if Path(path).is_file():
                images.append(path)
            else:
                restored_missing.append(img)
        if restored_missing:
            note += "[Interrupted-turn images no longer exist and were not re-attached: " + ", ".join(restored_missing) + "]\n\n"
    if original_prompt and not completed:
        continuation = (
            "[RECOVERY CONTEXT — this thread's interrupted turn may not have "
            "persisted its input. The original task is restated below; if "
            "you already have it in-thread, treat this as a duplicate and "
            "continue.]\n\n<original_task>\n"
            f"{original_prompt}\n</original_task>\n\n"
            f"[CONTINUATION INSTRUCTIONS]\n{continuation}"
        )

    # The continuation must run in the ORIGINAL run's tree (abraham may have
    # targeted a subtree of this workspace) — locking or writing against the
    # server cwd would miss the tree the brief describes.
    run_cwd = str(rec.get("cwd") or cwd)
    lock_cwd = resume_custody or run_cwd
    needs_lock = use_write or bool(resume_custody)
    if needs_lock:
        # EVERY write continuation takes the tree lock (round 40: the
        # acquisition once sat inside a sealed-only branch, so a full-access
        # resume ran unlocked, failed its child publication at the execution
        # barrier, and released a lock it never held).
        got_lock, holder = _acquire_write_lock(lock_cwd, f"resume:{run}")
        if not got_lock:
            return (
                f"[resume refused: another write run holds this tree's "
                f"lock ({holder}). One writer per tree.]"
            )
    # Claims in FIXED ORDER (round 8): the run-terminal claim first, then
    # the THREAD claim (A → B → C are one thread; concurrent resumes of any
    # two of them must collide — round 5). The continuation child inherits
    # the thread-claim descriptor.
    claimed, holder = _acquire_run_claim(_run_terminal_claim_key(run))
    if not claimed:
        if needs_lock:
            _release_write_lock(lock_cwd)
        return (
            f"[Run {run} is already owned by another call ({holder}) — being "
            f"collected, resumed, or cancelled. Wait for that result "
            f"(codex_runs() shows the status).]"
        )
    claimed_t, holder_t = _acquire_run_claim(_thread_claim_key(tid))
    if not claimed_t:
        _release_run_claim(_run_terminal_claim_key(run))
        if needs_lock:
            _release_write_lock(lock_cwd)
        return (
            f"[Thread {tid} (run {run}) is already being resumed or collected by "
            f"another call ({holder_t}). Two resumes of one thread double-execute "
            f"it — wait for that result (codex_runs() shows the status).]"
        )
    try:
        return note + await _run_codex(
            continuation,
            infra=use_infra,
            write=use_write,
            ctx=ctx,
            web_search=use_web,
            resume_tid=tid,
            parent_run=run,
            tool_name=str(rec.get("tool") or ""),
            workdir=run_cwd,
            request_started=request_t0,
            claim_key=_thread_claim_key(tid),
            images=images,
            custody_cwd=resume_custody,
        )
    finally:
        _release_run_claim(_thread_claim_key(tid))
        _release_run_claim(_run_terminal_claim_key(run))
        if needs_lock:
            _release_write_lock(lock_cwd)


# ---------------------------------------------------------------------------
# Detached-run adoption + run operations (1.17.0)
# ---------------------------------------------------------------------------

_STOPPABLE = ("RUNNING", "DETACHED", "DETACHED-ENDED", "ORPHANED", "ORPHANED-WRITE", "CANCELLING")


def _run_status(rec: dict[str, Any]) -> str:
    """One word for a journal record: RUNNING / DETACHED / ok / error /
    cancelled / timeout / hung / INTERRUPTED (no end record, process gone)."""
    if rec.get("has_end"):
        return str(rec.get("status") or "?")
    if (rec.get("has_cancel_requested")
            or _cancel_requested(str(rec.get("run") or ""))):
        # ANY nonterminal record with a standing cancellation source — the
        # journal record OR the on-disk marker — reads CANCELLING, BEFORE
        # pid classification (round 12: a stale recorded pid pushed a
        # marker-only state to INTERRUPTED, which is not stoppable, so the
        # promised retry could never terminalize). CANCELLING is stoppable.
        return "CANCELLING"
    alive = _pid_alive(int(rec.get("pid") or 0), str(rec.get("pid_start") or ""))
    if _is_orphaned_write(rec):
        return "ORPHANED-WRITE"
    if _is_detached(rec):
        return "DETACHED" if alive else "DETACHED-ENDED"
    owner = int(rec.get("server_pid") or 0)
    if alive and owner and not _server_alive(
            owner, str(rec.get("server_start") or "")):
        return "ORPHANED"  # alive, owner gone, no watchdog — stop it
    if alive:
        return "RUNNING"
    log_path = str(rec.get("log") or "")
    with contextlib.suppress(OSError):
        if log_path and time.time() - Path(log_path).stat().st_mtime < 60:
            return "RUNNING"
    return "INTERRUPTED"


def _workspace_runs(limit: int = 0) -> list[dict[str, Any]]:
    cwd = _get_cwd()
    runs = [
        r for r in _journal_runs().values()
        if r.get("has_start") and _run_belongs(r, cwd)
    ]
    return runs[-limit:] if limit else runs


def _replay_spool(rec: dict[str, Any], emit, max_bytes: int = REPLAY_MAX_BYTES) -> dict[str, Any]:
    """Digest the TAIL of a run's stdout spool into a fresh state (bounded:
    the terminal events — answer, turn.completed, errors — are at the end).
    Returns the position replayed up to in state["_pos"]."""
    state: dict[str, Any] = {"activity": "", "last_message": "", "last_error": "",
                             "last_item_error": "", "usage": "", "thread_id": ""}
    path = Path(str(rec.get("stdout") or ""))
    buf = bytearray()
    pos = 0
    with contextlib.suppress(OSError):
        size = path.stat().st_size
        with open(path, "rb") as fh:
            start = max(0, size - max_bytes)
            fh.seek(start)
            if start:
                # Discard through the first newline WITHIN the window using
                # fixed-size reads — readline() on a newline-free region
                # allocates it whole before the record cap applies (round 6).
                scanned = 0
                while scanned < max_bytes:
                    probe = fh.read(min(READ_CHUNK_SIZE, max_bytes - scanned))
                    if not probe:
                        break
                    nl = probe.find(b"\n")
                    if nl >= 0:
                        fh.seek(start + scanned + nl + 1)
                        break
                    scanned += len(probe)
            pos = fh.tell()
            while True:
                chunk = fh.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                pos += len(chunk)
                _feed_jsonl(buf, chunk, state, emit)
    if not state.get("thread_id"):
        # thread.started is the FIRST record — a tail window on a spool
        # larger than max_bytes never sees it (round 8). One bounded head
        # read recovers it.
        with contextlib.suppress(OSError):
            with open(path, "rb") as hf:
                head = hf.read(READ_CHUNK_SIZE)
            for raw in head.split(b"\n")[:64]:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "thread.started" and ev.get("thread_id"):
                    state["thread_id"] = str(ev["thread_id"])
                    break
    state["_pos"] = pos
    state["_buf"] = bytes(buf)  # an incomplete trailing record continues in adoption
    return state


def _cancel_marker(run: str) -> Path:
    return _run_dir_root() / "cancel" / (run.replace("·", "-") + ".cancel")


def _request_cancel(run: str) -> bool:
    """A cancellation INTENT: honoured by the run's owner before/at spawn
    (round 3: a pre-spawn run was journaled 'cancelled' while its owner went
    on to spawn). Returns False when the marker could not be written — the
    caller must report that, never swallow it."""
    try:
        p = _cancel_marker(run)
        p.parent.mkdir(parents=True, exist_ok=True)
        _private(p.parent, 0o700)
        p.write_text(f"pid={os.getpid()} t={int(time.time())}\n", encoding="utf-8")
        _private(p, 0o600)
        return True
    except OSError:
        return False


def _cancel_requested(run: str) -> bool:
    return _cancel_marker(run).exists()


def _clear_cancel(run: str) -> None:
    with contextlib.suppress(OSError):
        _cancel_marker(run).unlink()


def _run_claim_path(run: str) -> Path:
    return _run_dir_root() / "claims" / (run.replace("·", "-") + ".lock")


def _run_terminal_claim_key(run: str) -> str:
    """Claim key for the right to WRITE this run's terminal record. Held by
    the attached owner for the run's whole lifetime, by a collector for the
    collection, and taken by a canceller before it terminalizes — so there is
    exactly ONE terminal writer, with a STABLE identity (round 8: keying by
    `thread_id or run` changed identity when replay recovered the thread id,
    letting a collector and a canceller into their critical sections at
    once). Prefixes are filename-safe on Windows (no colon)."""
    return "run-" + run


def _thread_claim_key(tid: str) -> str:
    """Claim key for continuations of one codex THREAD (A→B→C are one
    thread). Always acquired AFTER the run-terminal claim (fixed order —
    no deadlock between a collector and a canceller)."""
    return "tid-" + tid


def _acquire_run_claim(run: str) -> tuple[bool, str]:
    """One collector/resumer per run at a time (two collectors of a dead
    detached run both fell through to a thread resume). A kernel-held lock:
    exclusive for the holder's lifetime, gone with it."""
    return _acquire_os_lock(
        _run_claim_path(run), f"pid={os.getpid()} start={_SERVER_START or '-'} t={int(time.time())}\n")


def _release_run_claim(run: str) -> None:
    _release_os_lock(_run_claim_path(run))


async def _collect_detached(
    rec: dict[str, Any], run: str, ctx: Any, request_t0: float, nudge: str,
) -> str | None:
    """Adopt a run that outlived its server: wait for the detached codex
    process (heartbeating, replaying its spool into a fresh live log), then
    deliver its answer exactly like a normal run — no model re-ask. Returns
    None when the process died WITHOUT an answer so the caller can fall back
    to a thread resume."""
    pid = int(rec.get("pid") or 0)
    pgid = int(rec.get("pgid") or 0)
    model = str(rec.get("model") or "?")
    reasoning = str(rec.get("reasoning") or "?")
    tool_name = str(rec.get("tool") or "")
    # A record without a dispatch-time digest (pre-1.17.2) must never vouch
    # for whatever the tree looks like now: stamp `unknown`, which the push
    # gate parses syntactically and can never render as VERIFIED.
    tree = str(rec.get("tree") or "") or "unknown"
    start = str(rec.get("pid_start") or "")
    out = Path(str(rec.get("output_file") or ""))
    deadline = float(rec.get("deadline_ts") or 0)
    alive = _pid_alive(pid, start)
    if alive and nudge:
        return (
            f"[Run {run} is still RUNNING detached (codex pid {pid}). Its thread "
            f"cannot take new instructions while it is being written: call "
            f"codex_resume_run(run=\"{run}\") without a nudge to wait for and "
            f"collect its answer, or codex_cancel_run(run=\"{run}\") to stop it.]"
        )
    # FIXED claim order (round 8): the STABLE run-terminal claim first, then
    # the thread claim when the thread is already known. A thread id
    # recovered later, during replay, is claimed then and released by the
    # same finally (held list shared with the collection).
    held: list[str] = []
    keys = [_run_terminal_claim_key(run)]
    tid0 = str(rec.get("thread_id") or "")
    if tid0:
        keys.append(_thread_claim_key(tid0))
    for key in keys:
        claimed, holder = _acquire_run_claim(key)
        if not claimed:
            for k in reversed(held):
                _release_run_claim(k)
            return (
                f"[Run {run} is already being collected, resumed, or cancelled by "
                f"another call ({holder}). Wait for that result instead — "
                f"codex_runs() shows the run's status; a second collector would "
                f"double-execute the thread.]"
            )
        held.append(key)
    try:
        return await _collect_detached_claimed(
            rec, run, ctx, request_t0, pid, pgid, model, reasoning, tool_name,
            tree, out, deadline, alive, start, held)
    finally:
        for k in reversed(held):
            _release_run_claim(k)


async def _collect_detached_claimed(
    rec, run, ctx, request_t0, pid, pgid, model, reasoning, tool_name, tree,
    out, deadline, alive, start="", held_keys: list[str] | None = None,
) -> str | None:
    rec = _journal_runs().get(run, rec)
    if rec.get("has_end"):
        return None  # another collector finished it while we waited for the claim
    live_path, live_fh, stream_fh, tag = _open_live_log("adopt")
    t0 = time.monotonic()

    def _emit(text: str) -> None:
        _live_write(live_fh, t0, text)
        _live_write(stream_fh, t0, text, tag)

    if live_fh is not None:
        with contextlib.suppress(Exception):
            live_fh.write(
                f"# codex-oracle ADOPTION of detached run {run} — "
                f"{time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
                f"# model={model} effort={reasoning} pid={pid} "
                f"alive={alive} spool={out.parent}\n\n"
            )
            live_fh.flush()
    _emit(f"▶ adopting detached run {run} (pid {pid}, alive={alive})")
    state = _replay_spool(rec, _emit)
    state["activity"] = f"adopted detached run {run}: waiting for pid {pid}"
    timed_out = False
    hb = (
        asyncio.create_task(_heartbeat_loop(ctx, request_t0, t0, state, model, _emit))
        if ctx is not None else None
    )
    try:
        spool = Path(str(rec.get("stdout") or ""))
        pos = int(state.pop("_pos", 0) or 0)
        buf = bytearray(state.pop("_buf", b"") or b"")
        while _writer_alive(pid, start, pgid, spool):
            if deadline and time.time() > deadline + 30:
                signalled = _kill_pgid(pgid, pid, start)
                death = time.time() + 3.0
                while ((_pid_alive(pid, start) or _pgid_alive(pgid))
                       and time.time() < death):
                    await asyncio.sleep(0.1)
                if _pid_alive(pid, start) or _pgid_alive(pgid):
                    # VERIFIED DEATH ONLY (round 11): journaling terminal
                    # `timeout` over a live process lets a later resume
                    # write the same thread concurrently with it.
                    _emit(f"✖ deadline kill FAILED (signalled={signalled}, "
                          f"pid {pid} still alive) — NOT terminalizing")
                    for _fh in (live_fh, stream_fh):
                        if _fh is not None:
                            with contextlib.suppress(Exception):
                                _fh.close()
                    return (
                        f"[collection of run {run} FAILED: it is past its "
                        f"deadline but its process (pid {pid}) could not be "
                        f"killed (signalled: {signalled}). Nothing was "
                        f"finalized — the run stays stoppable; stop it from "
                        f"the OS or retry codex_cancel_run(run=\"{run}\").]"
                    )
                timed_out = True
                _emit(f"■ detached run past its {MAX_RUNTIME_SECONDS}s run budget "
                      f"(CODEX_ORACLE_MAX_RUNTIME_S) "
                      f"— killed (death verified)")
                break
            with contextlib.suppress(OSError):
                with open(spool, "rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read(READ_CHUNK_SIZE)
                if chunk:
                    pos += len(chunk)
                    _feed_jsonl(buf, chunk, state, _emit)
                    continue
            await asyncio.sleep(1.0)
        try:
            marked_amb = _marked_survivors(run)
        except Exception:
            marked_amb = [-1]
        if not _pid_alive(pid, start) and (_pgid_alive(pgid) or marked_amb):
            # Round 13: _writer_alive's spool-freshness clause bounds the
            # WAIT (pgid recycling must not park a collector forever) — it
            # is not death evidence. A live group with a stale spool is
            # AMBIGUOUS: no terminal record, the run stays stoppable.
            _emit(f"✖ launcher pid {pid} is gone but group {pgid} still has "
                  f"live members and the spool is stale — NOT terminalizing")
            for _fh in (live_fh, stream_fh):
                if _fh is not None:
                    with contextlib.suppress(Exception):
                        _fh.close()
            return (
                f"[collection of run {run} FAILED: its recorded pid is gone "
                f"but its process group ({pgid}) still reads alive while the "
                f"spool has gone quiet — the writer's state is ambiguous. "
                f"Nothing was finalized; codex_cancel_run(run=\"{run}\") "
                f"kills marker-verified survivors (python3 procenv.py --list "
                f"{run} lists them), then collect again. "
                f"Never signal the bare group id — it may have been reused.]"
            )
        # Final drain after exit (chunked, bounded by the spool tail).
        with contextlib.suppress(OSError):
            with open(spool, "rb") as fh:
                fh.seek(pos)
                while True:
                    rest = fh.read(READ_CHUNK_SIZE)
                    if not rest:
                        break
                    _feed_jsonl(buf, rest, state, _emit)
    except asyncio.CancelledError:
        # The CALLER stopped waiting; the run was not theirs to kill — it
        # keeps running and stays collectable (codex_cancel_run stops it).
        _journal({"run": run, "phase": "collect_cancelled", "ts": time.time(),
                  "by_pid": os.getpid()})
        _emit(f"■ collector cancelled by caller — run {run} keeps running "
              f"(pid {pid}); collect again with codex_resume_run or stop it "
              f"with codex_cancel_run")
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()
        raise
    finally:
        if hb is not None:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
    if state.get("thread_id") and not str(rec.get("thread_id") or ""):
        # A thread id recovered only from replay must land in the journal
        # (round 6) — and the append must be CHECKED (round 7): terminalizing
        # a run whose thread id never persisted would strand the thread.
        sess_ok = False
        for sess_try in range(3):
            if sess_try:
                await asyncio.sleep(0.05)
            if _journal({"run": run, "phase": "session", "ts": time.time(),
                         "thread_id": str(state["thread_id"])}):
                sess_ok = True
                break
        if not sess_ok:
            _emit("✖ could not journal the recovered thread id — NOT terminalizing")
            for _fh in (live_fh, stream_fh):
                if _fh is not None:
                    with contextlib.suppress(Exception):
                        _fh.close()
            return (
                f"[collection of run {run} FAILED: the replay-recovered thread id "
                f"could not be journaled, so a terminal record would strand the "
                f"thread. Nothing was finalized — fix the state directory and "
                f"retry codex_resume_run(run=\"{run}\").]"
            )
        # The thread is known NOW: take its claim too (fixed order — we hold
        # the run-terminal claim), so no resume can continue this thread
        # while we finish, and no canceller keys past us (round 8). The
        # shared held list makes the outer finally release it.
        got_t, holder_t = _acquire_run_claim(_thread_claim_key(str(state["thread_id"])))
        if not got_t:
            _emit(f"✖ recovered thread is claimed elsewhere ({holder_t}) — NOT terminalizing")
            for _fh in (live_fh, stream_fh):
                if _fh is not None:
                    with contextlib.suppress(Exception):
                        _fh.close()
            return (
                f"[collection of run {run} deferred: its recovered thread is "
                f"claimed by another call ({holder_t}). Nothing was finalized — "
                f"retry codex_resume_run(run=\"{run}\") once that call ends.]"
            )
        if held_keys is not None:
            held_keys.append(_thread_claim_key(str(state["thread_id"])))
    final = ""
    with contextlib.suppress(OSError):
        if out.exists() and out.stat().st_size > 0:
            final = out.read_text(encoding="utf-8", errors="replace").strip()
    # OK is EARNED: the answer FILE (codex writes it only at turn end) AND a
    # turn.completed event. Anything less — an earlier agent_message, a spool
    # that stops mid-turn — is PARTIAL, never signed ok (review of 1.17.0:
    # partial commentary was signed status:ok). A RECOVERED error does not
    # unearn it (round 52): codex emits RETRY notifications as top-level
    # `error` events with will_retry, and the JSONL emitter serialises them
    # like any other, so a run that retried once and then finished with a real
    # answer was collected as an error. Both conditions above are written only
    # at a successful turn end, so an error that PRECEDES them was survived.
    completed = bool(final) and bool(state.get("turn_completed"))
    if not final and not timed_out:
        final = str(state.get("last_message") or "")
    status = "ok" if (completed and not timed_out) else ("timeout" if timed_out else "error")
    if status != "ok" and _cancel_requested(run):
        # A canceller killed the process while we held the claim (round 7):
        # we are the sole terminal writer, so WE fold the intent.
        status = "cancelled"
        state["last_error"] = str(
            state.get("last_error") or "cancelled by codex_cancel_run during collection")
    if status == "error" and not state.get("last_error"):
        lcf = str(state.get("last_command_failure") or "")
        lie = str(state.get("last_item_error") or "")
        state["last_error"] = (
            "the detached process ended without completing its turn "
            "(no turn.completed / no answer file; its exit status is not "
            "observable after detachment)"
            + (f"; last reported item error, for context: {lie}" if lie else "")
            + (f"; last failed command, for context: {lcf}" if lcf else "")
        )
    result_file = ""
    if status == "ok" and live_path is not None:
        with contextlib.suppress(Exception):
            rf = live_path.with_suffix(".result.txt")
            rf.write_text(final, encoding="utf-8")
            _private(rf, 0o600)
            result_file = str(rf)
    _emit(f"■ adoption finished: status={status} {state.get('usage') or ''}".rstrip())
    end_ok = _journal({
        "run": run, "phase": "end", "ts": time.time(), "status": status,
        "returncode": 0 if status == "ok" else 1, "adopted": True,
        "error": str(state.get("last_error") or ("timeout" if timed_out else ""))[:500]
        if status != "ok" else "",
        "result_file": result_file,
    })
    for _fh in (live_fh, stream_fh):
        if _fh is not None:
            with contextlib.suppress(Exception):
                _fh.close()
    late_cancel_note = ""
    if status == "ok" and _cancel_requested(run):
        # LINEARIZATION RULE (round 8): a cancel that lands after the run
        # already completed loses — the answer file and turn.completed exist,
        # and discarding finished work over a late kill is waste. Said out
        # loud so the canceller's caller is not left expecting `cancelled`.
        late_cancel_note = (
            "\n[note: a cancel intent arrived after this run had already "
            "completed — the answer wins and the run is journaled ok]"
        )
    if end_ok:
        _clear_cancel(run)  # any durable terminal record retires the intent
    log_note = f"\n\n[live log: {live_path}]" if live_path else ""
    if not end_ok:
        log_note += ("\n[⚠ the adoption's terminal record could not be "
                     "journaled — a later call may re-collect this run]")
    if status == "cancelled":
        # Never fall through to the thread-resume fallback: that would
        # resurrect a run the caller just cancelled.
        return (
            f"[Run {run} was cancelled (codex_cancel_run) while being collected "
            f"— no answer was produced. Resume deliberately with "
            f"codex_resume_run(run=\"{run}\", nudge=...) if it is still wanted.]"
            f"{log_note}"
        )
    if status == "ok":
        return (
            f"[Codex model: {model} | reasoning: {reasoning}"
            f"{_answer_sig(tool_name, 'ok', tree)}]\n"
            f"[collected from run {run}, which outlived its MCP call (server "
            f"restart) — no re-ask, no new model call]{late_cancel_note}{log_note}\n\n{final}"
        )
    if final:
        snippet = final if len(final) <= 4000 else final[:4000] + "\n… [partial output truncated]"
        return (
            f"[Codex error (detached run {run}){_answer_sig(tool_name, status, tree)}]\n"
            f"{'timed out' if timed_out else str(state.get('last_error'))[:300]}\n"
            f"[recoverable: call codex_resume_run with a nudge to continue the thread "
            f"(run id: {run})]\n\n[partial output before the failure — NOT the answer]\n{snippet}"
            f"{log_note}"
        )
    return None  # died without an answer → caller falls back to a thread resume


@_lead_tool()
async def codex_runs(limit: int = 10, session_id: str = "") -> str:
    """
    Status of this workspace's codex runs, newest last — RUNNING (attached
    to a live call), DETACHED (outlived its MCP call after a server restart
    and is STILL RUNNING; collect it with codex_resume_run), ok / error /
    cancelled / timeout, or INTERRUPTED — with elapsed time, attempts, the
    current activity and the live-log path. Use this instead of tailing
    ~/.claude/logs/codex-oracle by hand.

    Args:
        limit: How many most-recent runs to show (default 10).
    """
    discovery = await _native_runs(max(1, min(int(limit or 10), 50)))
    native_runs = discovery["data"]
    runs = _workspace_runs(max(1, min(int(limit or 10), 50)))
    if not runs and not native_runs:
        return "[No recorded codex runs for this workspace.]" if discovery["available"] else _native_unavailable(discovery) + " No recorded exec runs."
    lines = [f"Codex runs in this workspace (oldest → newest) — run budget "
             f"{MAX_RUNTIME_SECONDS}s ({MAX_RUNTIME_SOURCE}; CODEX_ORACLE_MAX_RUNTIME_S):"]
    now = time.time()
    for rec in runs:
        st = _run_status(rec)
        started = float(rec.get("first_ts") or rec.get("ts") or 0)
        ended = float(rec.get("last_ts") or now) if rec.get("has_end") else now
        span_s = int(ended - started) if started else 0
        activity = ""
        if st in ("RUNNING", "DETACHED"):
            tail = _run_log_lines(rec, 1)
            activity = tail[-1][:120] if tail else ""
        lines.append(
            f"  • {rec.get('run', '?')} [{rec.get('tool') or '?'}] {st}"
            f" · {span_s}s"
            + (f" · attempts {rec.get('attempts')}" if rec.get("attempts") else "")
            + (f" · thread {str(rec.get('thread_id'))[:13]}" if rec.get("thread_id") else "")
            + (f" · pid {rec.get('pid')}" if st in ("RUNNING", "DETACHED") else "")
            + (f"\n      now: {activity}" if activity else "")
            + (f"\n      error: {str(rec.get('error'))[:120]}" if rec.get("error") else "")
            + (f"\n      log: {rec.get('log')}" if rec.get("log") else "")
        )
    lines.append(
        "Collect a DETACHED run: codex_resume_run(run=<id>). Watch one: "
        "codex_run_log(run=<id>). Stop one: codex_cancel_run(run=<id>). "
        "An ORPHANED-WRITE run (write child outlived its server) must be "
        "stopped before any new write run in that tree; an ORPHANED run has "
        "no deadline watchdog — stop it with codex_cancel_run."
    )
    if not discovery["available"]:
        lines.append(_native_unavailable(discovery))
    if native_runs:
        lines.append("Native coordinator runs (durable across MCP restarts):\n" + json.dumps(native_runs, ensure_ascii=False, indent=2))
    return await _coordinator_notice("\n".join(lines))


async def _collect_bound_reviews(run: str = "") -> str:
    try:
        jobs = (await _coordinator_call("review/jobs", {"cwd": _get_cwd(), "run": run}, timeout=3))["data"]
        records = _journal_runs()
        completed = []
        for job in jobs:
            if job["state"] != "dispatched":
                continue
            record = records.get(job["run"], {})
            if not record.get("has_end") or not _run_belongs(record, _get_cwd()):
                continue
            answer = ""
            if record.get("status") == "ok" and record.get("result_file"):
                answer = Path(record["result_file"]).read_text(encoding="utf-8")
            value = await _coordinator_call("review/complete", {"job": job["id"], "completion": {
                "run": job["run"], "thread": record.get("thread_id"), "status": record.get("status"),
                "completed": record.get("status") == "ok" and bool(answer), "answer": answer}})
            completed.append(value)
        return "\n[Collected bound reviews]\n" + json.dumps(completed) if completed else ""
    except Exception as exc:
        return "\n[Bound review collection unavailable: " + str(getattr(exc, "code", type(exc).__name__)) + "]" if run else ""


@_lead_tool()
async def codex_resume_run(run: str = "", nudge: str = "", infra: bool | None = None,
                           web_search: bool | None = None, images: list[str] = [],
                           ctx: Context = None, session_id: str = "") -> str:
    """Continue a codex run that failed, timed out, or was interrupted —
    WITH ITS ORIGINAL CONTEXT, not a re-ask.

    Codex persists each run's session, so a run killed mid-flight (MCP
    server restart, plugin reload, cancelled call, transient API error
    that outlived the automatic retries) can be continued from where it
    stopped. Never re-dispatch the whole question after a failure — resume
    it: the model still has the reasoning and tool output it had built up.

    A finished-but-undelivered run (e.g. the reload killed the call after
    codex answered) returns its stored answer immediately, at no cost —
    unless you pass a nudge, which continues the thread with your new
    instructions instead of replaying the old answer.

    A continuation of a failed or interrupted run RESTATES the original task
    from the run journal: a hard-killed turn may have persisted none of its
    input vendor-side (measured 2026-08-09), so the thread cannot be trusted
    to remember the question. Threads that did retain context see a
    labelled duplicate. A COMPLETED run's thread holds its task already, so
    a nudge to one continues it as is (a follow-up question to an advisory
    session is the tool's own `session` argument).

    SWITCH CAPABILITIES MID-COURSE. A resume may change the run's settings,
    so a run that turns out to need something it didn't have can be rescued
    instead of restarted: cancel the call, then resume the SAME thread — all
    its accumulated reasoning and tool output intact — with new settings and
    new instructions. The classic case: research that needs the live system.

        codex_resume_run(run="codex3·8123", infra=True,
                         nudge="SSH to the app host and check the celery "
                               "queue depth, then finish the analysis")

    Write runs (abraham) resume as write runs, with full access, network
    and live web search (a run that began sealed under 1.x continues with
    full access), the git contract and the changed-files report intact.
    The write axis itself has no override: read runs cannot be escalated to
    write by resuming, and write runs cannot be downgraded.

    Args:
        run: The run id from the failure message ("run id: codex7·21746").
            Omit to resume the most recent recoverable run. Pass "list" to
            see the recent runs and their statuses instead.
        nudge: Instructions for the continuation — additional direction, not
            just "carry on". Default asks for the complete final answer from
            where it left off.
        infra: Change live-infrastructure access for the continuation.
            None (default) inherits the original run's setting; True grants
            shell/network + your MCP servers; False takes it away.
        web_search: Change live web search for the continuation. None
            inherits the original run's setting.
        images: Local image file paths to attach to the continuation's turn
            (screenshots, diagrams). Codex views them natively. Missing paths
            are rejected loudly before any spend. Images without a nudge
            continue the thread with a default one: they never ride the
            stored-answer replay or the cancelled-run refusal. Continuing a
            failed or interrupted run also re-attaches the images its
            interrupted turn was given.

    Native runs (run_ IDs) collect on an empty call without another model request,
    including cancelled or interrupted runs. A nudge or images explicitly requests
    continuation. Reconciliation must finish first: active, frozen, UNKNOWN or
    uncertain submissions refuse a second turn and report non-delivery. Images use
    native localImage inputs. Interrupted recovery restores surviving images from
    the persisted phase, including earlier continuations, and names missing historical
    paths. Missing newly supplied paths refuse before submission. No native RPC is
    blindly retried. Native writers preserve full access and live web search.

    Defaults: without images an exec recovery uses "The previous process was
    interrupted before your answer arrived. Continue from where you left off and
    provide the complete final answer." Images alone use "Images are attached to
    this turn. Look at each one, then continue from where you left off with them in mind."
    A completed exec run with no instructions returns its saved answer; a cancelled
    exec run requires explicit instructions. List mode and detached collection do
    not deliver supplied instructions or images and say so. Run discovery is scoped
    to this dispatch folder or the target checkout. Bound review collection records
    the original candidate's full answer, including after MCP restart.
    """
    answer = await _resume_run(run=run, nudge=nudge, infra=infra, web_search=web_search, images=images, ctx=ctx)
    if not run.startswith("run_"):
        answer += await _collect_bound_reviews(run)
        if run and run != "list":
            discovery = await _native_runs()
            if not discovery["available"]:
                answer += "\n" + _native_unavailable(discovery)
    return await _coordinator_notice(answer)


def _run_log_lines(rec: dict[str, Any], lines: int) -> list[str]:
    """Last N digested lines of a run: its live log, or — for a DETACHED run,
    whose live log froze at detach — a replay of its stdout spool."""
    out: list[str] = []
    if _is_detached(rec) and rec.get("stdout"):
        _replay_spool(rec, out.append)
        return out[-lines:]
    log_path = str(rec.get("log") or "")
    with contextlib.suppress(OSError):
        if log_path and Path(log_path).is_file():
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            out = [ln for ln in text.splitlines() if ln.strip()]
    return out[-lines:]


async def _codex_run_log(run: str = "", lines: int = 40, cursor: int = 0, result: bool = False, session_id: str = "") -> str:
    """
    What a codex run is doing RIGHT NOW — its live log (reasoning summaries,
    commands, web searches, errors, retries), in-conversation, instead of
    `tail -F` on ~/.claude/logs/codex-oracle. Works for running, detached and
    finished runs. The MCP task panel goes silent once Claude Code backgrounds
    a call (its progress token is deregistered at ~120 s); this is the
    channel that keeps working.

    Args:
        run: Run id ("codex7·21746"); omit for the most recent run here.
        lines: How many trailing lines (default 40, max 400).
    """
    if run.startswith("run_"):
        return await _native_run_action("run/result" if result else "run/events", run, cursor=cursor,
                                        limit=50000 if result else max(1, min(int(lines or 40), 400)))
    discovery = await _native_runs()
    if not run and not discovery["available"]:
        return _native_unavailable(discovery) + " Automatic selection refused; supply an explicit run ID."
    native_runs = discovery["data"]
    if not run and native_runs:
        previous = _workspace_runs()
        if not previous or native_runs[-1]["created"] >= float(previous[-1].get("first_ts") or previous[-1].get("ts") or 0):
            run = native_runs[-1]["run"]
    if run.startswith("run_"):
        return await _native_run_action("run/result" if result else "run/events", run, cursor=cursor,
                                        limit=50000 if result else max(1, min(int(lines or 40), 400)))
    runs = _workspace_runs()
    if not runs:
        return "[No recorded codex runs for this workspace.]"
    rec = None
    if run:
        rec = next((r for r in runs if r.get("run") == run), None)
        if rec is None:
            return f"[Unknown run id '{run}' in this workspace. Try codex_runs().]"
    else:
        rec = runs[-1]
    n = max(1, min(int(lines or 40), 400))
    tail = _run_log_lines(rec, n)
    st = _run_status(rec)
    head = (
        f"[{rec.get('run')} · {rec.get('tool') or '?'} · {st}"
        + (f" · pid {rec.get('pid')}" if st in ("RUNNING", "DETACHED") else "")
        + f" · last {len(tail)} lines"
        + (f" · {rec.get('log')}" if rec.get("log") else "")
        + "]"
    )
    body = "\n".join(tail) if tail else "(no log lines yet)"
    if len(body) > 20000:
        body = "…" + body[-20000:]
    return f"{head}\n{body}"


async def _codex_cancel_run(run: str = "", session_id: str = "") -> str:
    """
    Stop a RUNNING or DETACHED codex run: SIGKILL its process group (and its
    watchdog) and journal it as cancelled. Omit `run` to stop the most recent
    live run in this workspace. The thread stays on disk, so the run remains
    resumable with a nudge via codex_resume_run.

    While the run's owning server is alive, the terminal record is the
    OWNER's to write: this call kills the live attempt and leaves a standing
    cancel intent; codex_runs shows CANCELLING until the owner journals the
    cancellation (within ~1s — its waits poll the intent every second).

    Args:
        run: Run id from codex_runs / a failure message; "" = most recent live run.
    """
    if run.startswith("run_"):
        return await _native_run_action("run/interrupt", run)
    discovery = await _native_runs()
    if not run and not discovery["available"]:
        return _native_unavailable(discovery) + " Automatic selection refused; supply an explicit run ID."
    native_runs = discovery["data"]
    if not run:
        active = [r for r in native_runs if r["state"] not in {"completed", "failed", "cancelled", "refused", "blocked"}]
        previous = [r for r in _workspace_runs() if _run_status(r) in _STOPPABLE]
        if active and (not previous or active[-1]["created"] >= float(previous[-1].get("first_ts") or previous[-1].get("ts") or 0)):
            run = active[-1]["run"]
    if run.startswith("run_"):
        return await _native_run_action("run/interrupt", run)
    runs = _workspace_runs()
    live = [r for r in runs if _run_status(r) in _STOPPABLE]
    rec = None
    if run:
        rec = next((r for r in runs if r.get("run") == run), None)
        if rec is None:
            return f"[Unknown run id '{run}' in this workspace. Try codex_runs().]"
        if _run_status(rec) not in _STOPPABLE:
            return f"[Run {run} is not running (status {_run_status(rec)}); nothing to stop.]"
    else:
        if not live:
            return "[No running or detached codex run in this workspace.]"
        rec = live[-1]
    pid = int(rec.get("pid") or 0)
    pgid = int(rec.get("pgid") or 0)
    start = str(rec.get("pid_start") or "")
    tag = str(rec.get("run"))
    if not _request_cancel(tag):
        return f"[cancel FAILED for run {tag}: could not record the cancel intent (state dir unwritable).]"
    # GENERATION-AWARE: an automatic retry spawns a new child per attempt, so
    # the pid in any snapshot may be a previous, dead attempt while a newer
    # one runs (review round 5). Re-fold the journal, kill the CURRENT live
    # generation, and only journal a terminal cancellation once no live
    # generation remains; otherwise leave the intent for the owner.
    seen: set[int] = set()
    killed_pids: list[int] = []
    for _ in range(5):
        fresh = _journal_runs().get(tag, rec)
        rec = fresh
        pid = int(fresh.get("pid") or 0)
        pgid = int(fresh.get("pgid") or 0)
        start = str(fresh.get("pid_start") or "")
        if pid <= 0:
            # OWNERSHIP TEST (round 9): a live owner holds the run-terminal
            # claim from its start record on — if the claim is FREE and
            # nothing ever spawned, the owner is gone and REQUESTED-forever
            # helps nobody: terminalize here.
            got_pre, _pre_holder = _acquire_run_claim(_run_terminal_claim_key(tag))
            if got_pre:
                try:
                    refold0 = _journal_runs().get(tag, rec)
                    if refold0.get("has_end"):
                        _clear_cancel(tag)
                        return (
                            f"[Run {tag} already reached terminal status "
                            f"{str(refold0.get('status') or '?')!r}; nothing to stop.]"
                        )
                    if not int(refold0.get("pid") or 0):
                        if _journal({"run": tag, "phase": "end", "ts": time.time(),
                                     "status": "cancelled",
                                     "cancelled_by": "codex_cancel_run",
                                     "returncode": -9}):
                            _clear_cancel(tag)
                            return (
                                f"[Run {tag} cancelled before it ever spawned "
                                f"(no live owner holds it).]"
                            )
                        # Round 10 MEDIUM: never report success over a failed
                        # terminal append — the intent stays, the run stays
                        # stoppable, and a retry terminalizes.
                        return (
                            f"[Run {tag}: no live owner and nothing spawned, but "
                            f"the terminal record could NOT be journaled — the "
                            f"cancel intent is retained. Re-run codex_cancel_run "
                            f"once the state directory is writable.]"
                        )
                finally:
                    _release_run_claim(_run_terminal_claim_key(tag))
                continue  # a spawn appeared while we looked — re-enter the loop
            _journal({"run": tag, "phase": "cancel_requested", "ts": time.time(),
                      "by_pid": os.getpid()})
            return (
                f"[cancel REQUESTED for run {tag} — it has not spawned its codex "
                f"process yet; its owner honours the request at spawn and journals "
                f"the cancellation. codex_runs shows CANCELLING until then.]"
            )
        if pid in seen or not _pid_alive(pid, start):
            break  # this generation is gone (and no newer one appeared since)
        if not start:
            return (
                f"[stop REFUSED: run {tag} (pid {pid}) has no recorded process "
                f"identity (legacy record or a platform without one); killing by "
                f"pid alone could hit an unrelated process. Stop it from the OS; a "
                f"cancel intent was recorded for its owner.]"
            )
        killed = _kill_pgid(pgid, pid, start)
        deadline = time.time() + 3.0
        while _pid_alive(pid, start) and time.time() < deadline:
            await asyncio.sleep(0.1)
        if _pid_alive(pid, start):
            return (
                f"[stop FAILED: run {tag} (pid {pid}, pgid {pgid}) is still alive "
                f"(kill signalled: {killed}). Nothing was journaled; retry, or stop "
                f"it from the OS.]"
            )
        gdeadline = time.time() + 3.0
        while _pgid_alive(pgid) and time.time() < gdeadline:
            await asyncio.sleep(0.1)
        if _pgid_alive(pgid):
            # Round 12: the recorded pid can be a LAUNCHER whose child is
            # the real writer — the pid dying is not the run dying.
            return (
                f"[stop FAILED: run {tag}: pid {pid} is gone but its process "
                f"group ({pgid}) still reads alive (a launcher's child "
                f"survived the launcher, or the id was reused). Nothing was "
                f"journaled; retry codex_cancel_run — it kills marker-verified "
                f"survivors (python3 procenv.py --list {tag} lists them). "
                f"Never signal the bare group id.]"
            )
        seen.add(pid)
        killed_pids.append(pid)
        await asyncio.sleep(0.2)  # let a retrying owner publish its next generation
    fresh = _journal_runs().get(tag, rec)
    live_pid = int(fresh.get("pid") or 0)
    if live_pid and live_pid not in seen and _pid_alive(live_pid, str(fresh.get("pid_start") or "")):
        _journal({"run": tag, "phase": "cancel_requested", "ts": time.time(), "by_pid": os.getpid()})
        return (
            f"[cancel REQUESTED for run {tag}: killed attempt(s) {killed_pids}, but a "
            f"newer attempt (pid {live_pid}) is live; its owner honours the intent "
            f"before the next attempt. Call again to stop it now.]"
        )
    # ONE TERMINAL WRITER, ONE OWNERSHIP TEST (rounds 7-8): whoever holds the
    # STABLE run-terminal claim is responsible for the terminal record — the
    # attached owner (which holds it for the run's whole lifetime, including
    # capacity backoffs), or an active collector. If it is held, the kill is
    # done and the standing intent makes that holder journal `cancelled`
    # (a run that had already completed returns its answer instead — the
    # answer wins the completion-boundary race). If it is free, no task
    # remains responsible — dead owner, detached idle run, or an owner whose
    # terminal append failed — and WE terminalize under the claim.
    held_keys = [_run_terminal_claim_key(tag)]
    claimed, holder = _acquire_run_claim(held_keys[0])
    if not claimed:
        _journal({"run": tag, "phase": "cancel_requested", "ts": time.time(),
                  "by_pid": os.getpid()})
        did = f"killed live attempt pid(s) {killed_pids}; " if killed_pids else ""
        return (
            f"[cancel of run {tag}: {did}terminalization is owned by another "
            f"live call ({holder}) — it journals the terminal state; the "
            f"standing intent marks it cancelled (a run that had already "
            f"completed returns its answer). codex_runs shows CANCELLING "
            f"until then; the thread stays resumable: "
            f"codex_resume_run(run=\"{tag}\").]"
        )
    tid_f = str(fresh.get("thread_id") or "")
    if tid_f:
        got_t, holder_t = _acquire_run_claim(_thread_claim_key(tid_f))
        if not got_t:
            _release_run_claim(held_keys[0])
            _journal({"run": tag, "phase": "cancel_requested", "ts": time.time(),
                      "by_pid": os.getpid()})
            return (
                f"[cancel of run {tag}: killed pid(s) {killed_pids or [pid]}; its "
                f"thread is claimed by another live call ({holder_t}) — that call "
                f"owns the terminal state; the standing intent marks it cancelled.]"
            )
        held_keys.append(_thread_claim_key(tid_f))
    try:
        refold = _journal_runs().get(tag, fresh)
        if refold.get("has_end"):
            _clear_cancel(tag)  # someone else's durable terminal record retires the intent
            did = (f" — this call killed attempt pid(s) {killed_pids} and the owner "
                   f"journaled the cancellation") if killed_pids else ""
            return (
                f"[Run {tag} already reached terminal status "
                f"{str(refold.get('status') or '?')!r} while stopping{did}; nothing more to do.]"
            )
        gpgid = int(refold.get("pgid") or 0)
        try:
            marked_left = _marked_survivors(tag)
            if marked_left:
                # Round 19: the canceller KILLS marked escapees (revalidated
                # at signal time), then verifies — refusal is for what
                # survives the kill, not for what was merely found.
                _kill_marked(tag, marked_left)
                kill_deadline = time.time() + 3.0
                while time.time() < kill_deadline:
                    marked_left = _marked_survivors(tag)
                    if not marked_left:
                        break
                    await asyncio.sleep(0.1)
        except Exception as e:
            return (
                f"[stop FAILED for run {tag}: survivor scan failed ({e}) — "
                f"cannot verify the run's processes are gone. Nothing was "
                f"journaled; the cancel intent is retained.]"
            )
        if marked_left:
            return (
                f"[stop FAILED for run {tag}: process(es) {marked_left} still "
                f"carry its spawn marker (codex shell tools setsid out of the "
                f"group — round 16). Nothing was journaled and the cancel "
                f"intent is retained. Re-run codex_cancel_run — it kills marker-"
                f"verified survivors at signal time (pids listed now: "
                f"{marked_left}; inspect with python3 procenv.py --list {tag}). "
                f"Never signal a pid from an old listing — it may have been reused.]"
            )
        if _pgid_alive(gpgid) and not _pid_alive(
                int(refold.get("pid") or 0), str(refold.get("pid_start") or "")):
            # Round 13: the recorded pid being dead is NOT the run being dead
            # — a launcher's surviving child still holds the thread. No
            # terminal record over a live group; the marker stays for the
            # retry after the operator stops the group.
            return (
                f"[stop FAILED for run {tag}: its recorded pid is gone but "
                f"its process group ({gpgid}) still reads alive (a launcher's "
                f"child survived, or the id was reused). Nothing was journaled "
                f"and the cancel intent is retained; re-run codex_cancel_run — "
                f"it kills marker-verified survivors (python3 procenv.py --list "
                f"{tag} lists them). Never signal the bare "
                f"group id.]"
            )
        wd = int(rec.get("watchdog_pid") or 0)
        if wd and _pid_alive(wd, str(rec.get("watchdog_start") or ""), strict=True):
            # Only OUR watchdog (verified identity) — a reused pid is left alone.
            with contextlib.suppress(Exception):
                os.kill(wd, signal.SIGTERM)
        end_ok = _journal({"run": tag, "phase": "end", "ts": time.time(), "status": "cancelled",
                           "cancelled_by": "codex_cancel_run", "returncode": -9})
        if end_ok:
            _clear_cancel(tag)  # terminal record is durable; the intent has served
            custody_tree = (str(refold.get("custody_cwd") or "")
                            or (str(refold.get("cwd") or "") if refold.get("write") else ""))
            if custody_tree:
                lp = _write_lock_path(custody_tree)
                if str(lp) in _LOCK_CUSTODY:
                    # Group death was verified above (round 13's check) and
                    # the run is durably terminal — custody ends. Lock-held
                    # READS release via their journaled custody_cwd (round
                    # 18: the write-only check wedged phase-1 custody).
                    # Residual: custody is in-memory, so only the OWNING
                    # server can release it — a cross-server cancel leaves
                    # the owner's lock held (fail-closed over-hold) until
                    # that server exits.
                    _LOCK_CUSTODY.discard(str(lp))
                    _release_write_lock(custody_tree)
    finally:
        for k in reversed(held_keys):
            _release_run_claim(k)
    if not end_ok:
        # Round 7 MEDIUM: never report success over a failed terminal append.
        return (
            f"[Run {tag}: process stopped (pid(s) {killed_pids or [pid]}), but the "
            f"terminal record could NOT be journaled — the cancel intent is "
            f"retained and status displays may show the run live. Re-run "
            f"codex_cancel_run once the state directory is writable.]"
        )
    return (
        f"[Run {tag} stopped (pid(s) {killed_pids or [pid]}). "
        f"Its thread is on disk: codex_resume_run(run=\"{tag}\", nudge=...) "
        f"continues it if needed.]"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@_lead_tool()
async def codex_run_log(run: str = "", lines: int = 40, cursor: int = 0, result: bool = False, session_id: str = "") -> str:
    """What a codex run is doing RIGHT NOW — its live log (reasoning summaries,
    commands, web searches, errors, retries), in-conversation, instead of
    `tail -F` on ~/.claude/logs/codex-oracle. Works for running, detached and
    finished runs. The MCP task panel goes silent once Claude Code backgrounds
    a call (its progress token is deregistered at ~120 s); this is the
    channel that keeps working.

    Args:
        run: Run id ("codex7·21746"); omit for the most recent run here.
        lines: How many trailing lines (default 40, max 400)."""
    answer = await _codex_run_log(run=run, lines=lines, cursor=cursor, result=result, session_id=session_id)
    if run and not run.startswith("run_"):
        discovery = await _native_runs()
        if not discovery["available"]:
            answer += "\n" + _native_unavailable(discovery)
    return await _coordinator_notice(answer)


@_lead_tool()
async def codex_cancel_run(run: str = "", session_id: str = "") -> str:
    """Stop a RUNNING or DETACHED codex run: SIGKILL its process group (and its
    watchdog) and journal it as cancelled. Omit `run` to stop the most recent
    live run in this workspace. The thread stays on disk, so the run remains
    resumable with a nudge via codex_resume_run.

    While the run's owning server is alive, the terminal record is the
    OWNER's to write: this call kills the live attempt and leaves a standing
    cancel intent; codex_runs shows CANCELLING until the owner journals the
    cancellation (within ~1s — its waits poll the intent every second).

    Args:
        run: Run id from codex_runs / a failure message; "" = most recent live run."""
    answer = await _codex_cancel_run(run=run, session_id=session_id)
    if run and not run.startswith("run_"):
        discovery = await _native_runs()
        if not discovery["available"]:
            answer += "\n" + _native_unavailable(discovery)
    return await _coordinator_notice(answer)



if __name__ == "__main__":
    _install_shutdown_handlers(hard_exit=True)
    _register_server()  # mixed-version write barrier: mark this process 1.17.2+
    try:
        mcp.run(transport="stdio")
    finally:
        if _SHUTDOWN.is_set():
            # Cleanup has run (journals fsync'd, logs closed); do not wait
            # for the stdio reader thread the client may never release.
            os._exit(0)
