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
import itertools
import hashlib
import json
import math
import os
import random
import threading
import re
import shutil
import signal
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
# Prevents zombie processes that start but never finish. 60 minutes covers
# max effort (deepest single-agent reasoning) and infra-mode investigations
# (SSH/DB/log exploration) while still preventing the multi-hour hangs we
# observed with stuck processes.
MAX_RUNTIME_SECONDS = 3600  # 60 minutes

# ---------------------------------------------------------------------------
# Write mode (abraham): auto-compaction
# ---------------------------------------------------------------------------
# Implementation runs are LONG. codex only auto-compacts its own history at
# 90% of the context window by default (0.147.0-generation source:
# resolved_context_window * 9 / 10 in openai_models.rs; the registry carries
# no per-model override for the gpt-5.6 family), which leaves the tail of a
# long write run degraded. Write runs therefore pass an explicit
# -c model_auto_compact_token_limit at AUTOCOMPACT_PCT of the window.
#
# Two measured traps shape this code:
# 1. A user-config value WINS OUTRIGHT over the model-derived default
#    (config.model_auto_compact_token_limit.or_else(model default) — no min()
#    with the window on that path), so a limit above the real window would
#    simply NEVER fire, silently disabling compaction. The flag is only
#    passed when the window is KNOWN.
# 2. The window must come from the deployed binary's OWN registry
#    (models_cache.json under CODEX_HOME — the same base its 90% default
#    derives from), never from recalled docs: gpt-5.6-sol is 272_000 there
#    (measured 2026-08-14) while API-era docs suggest 400_000. A guessed 400k
#    base would have put "65%" at 95.6% of the real window — LATER than the
#    default it replaced.
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
    "- A test that passes on the library's DEFAULT backend proves nothing "
    "about the deployed one. Flag missing coverage on the real engine.\n"
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

    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        _config_cache = {}
        return _config_cache

    try:
        mtime = config_path.stat().st_mtime
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
    return _read_codex_config().get("model", "gpt-5.6-sol")


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
    """12-hex digest of the workspace state (HEAD + tracked diff + status).

    Stamped into answer headers by ``_answer_sig`` and recomputed by the
    push-gate hook at push time: a mismatch means the tree changed after the
    answer, so a completed review no longer vouches for the push. Duplicated
    in hooks/push_gate.py (hooks cannot import this module — it needs the
    mcp package); tests/test_push_gate.py pins behavioral parity.
    """
    try:
        parts = []
        for args in (("rev-parse", "HEAD"), ("diff", "HEAD"), ("status", "--porcelain")):
            proc = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0:
                # A digest over PARTIAL state is worse than no digest: a
                # failed diff/status hashed as empty could still open the
                # gate. Every command must succeed or the digest is void.
                return "nogit" if args[0] == "rev-parse" else "unknown"
            parts.append(proc.stdout)
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:12]
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _answer_sig(tool_name: str, status: str) -> str:
    """Machine-verifiable answer signature, appended inside the result header.

    hooks/push_gate.py opens the push gate only for a header carrying
    ``tool:code_review | status:ok`` and a ``tree:`` digest matching the
    workspace at push time — so a TIMEOUT partial, another tool's answer, or
    a review of an older tree can never satisfy the gate.
    """
    if not tool_name:
        return ""
    return f" | tool:{tool_name} | status:{status} | tree:{_workspace_digest(_get_cwd())}"


# The sealed write sandbox, verbatim — a single source shared by the
# implementation phase (_build_exec_argv) and the write-capability probe
# below, so the probe can never drift from what the real run executes.
WRITE_SANDBOX_ARGS = (
    "--sandbox", "workspace-write",
    "--ignore-user-config",
    "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
    "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
    # Windows: sandbox mode DEFAULTS TO DISABLED in codex 0.147, and a
    # disabled mode downgrades WorkspaceWrite → ReadOnly (upstream
    # config_toml.rs; the enable normally lives in user config, which
    # --ignore-user-config strips). Seal the intended mode explicitly —
    # and it must be "elevated": the unelevated/restricted-token backend
    # only INJECTS proxy/offline env vars (advisory — a raw-socket child
    # ignores them, reproduced upstream in codex#35940), while firewall
    # enforcement is tied to the elevated sandbox identities. abraham's
    # air-gap promises OS-ENFORCED no-egress, so a machine that cannot run
    # the elevated sandbox fails the write probe and abraham refuses —
    # fail closed, never a silently advisory seal. Key calibrated on the
    # installed 0.147.0 binary 2026-08-21: --strict-config accepts
    # "elevated"/"unelevated", rejects others; non-Windows parses and
    # ignores it.
    "-c", 'windows.sandbox="elevated"',
    # The sealed writer's SHELL CHILDREN get a minimal environment: codex
    # 0.147 defaults shell subprocesses to inherit=all, so repository
    # instructions or the phase-1 brief could otherwise read the parent's
    # env (API keys, tokens) into tool output or the worktree (round-3
    # review, 2026-08-21). core = HOME/PATH/USER-class vars only, and the
    # default KEY/SECRET/TOKEN name excludes stay active. Both keys
    # calibrated on the installed 0.147.0 binary: --strict-config accepts
    # them ("core"/"all"/"none" variants), rejects bogus values.
    "-c", 'shell_environment_policy.inherit="core"',
    "-c", "shell_environment_policy.ignore_default_excludes=false",
)

WRITE_PROBE_TIMEOUT_SECONDS = _env_seconds(
    "CODEX_ORACLE_WRITE_PROBE_TIMEOUT", 240.0, 30.0
)

# Test/override seam: when set, used verbatim for every workspace.
_write_capability: tuple[bool, str] | None = None

# Real cache, keyed by normalized workspace — a green proven on one volume
# says nothing about another's ACLs/controlled folders (round-2 review).
# Entry: (ok, detail, ts, conclusive). Conclusive verdicts live for the
# process; inconclusive ones (auth/rate-limit/CLI noise) expire so a burst
# of dispatches shares one probe but a later dispatch re-tests.
_write_probe_cache: dict[str, tuple[bool, str, float, bool]] = {}
_WRITE_PROBE_INCONCLUSIVE_TTL = 60.0

# Single-flight per event loop: concurrent first dispatches must not each
# launch a paid probe and race the memo. Keyed by loop id because tests run
# separate asyncio.run() loops and a Lock cannot cross loops.
_write_probe_locks: dict[int, asyncio.Lock] = {}


def _write_probe_lock() -> asyncio.Lock:
    return _write_probe_locks.setdefault(
        id(asyncio.get_running_loop()), asyncio.Lock()
    )


async def _run_write_probe(tmp: Path) -> tuple[int | None, str]:
    """Spawn ONE sealed low-effort codex write in ``tmp``; return
    (returncode, output tail).

    Uses the exact sealed sandbox argv (WRITE_SANDBOX_ARGS) plus the same
    approval policy the real run carries — parity is the point. Low effort
    is deliberate: the sandbox is built by the CLI layer, not the model, so
    effort is orthogonal to the capability under test.
    """
    argv = [
        *_codex_argv0(), "exec", *WRITE_SANDBOX_ARGS,
        "--skip-git-repo-check", "--color", "never",
        "-c", "approval_policy=never",
        "-c", "model_reasoning_effort=low",
        "-c", "web_search=disabled",
        "Create a file named probe.txt containing exactly: ok\n"
        "Do nothing else, then stop.",
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(tmp),
        # NEVER inherit the parent's stdin: it is the MCP JSON-RPC channel,
        # and codex exec APPENDS piped stdin to its prompt — inheriting it
        # both corrupts the session and leaks client traffic into the model
        # (round-2 review, 2026-08-21). Mirrors the real runner.
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_codex_env(),
        # Own process group: the npm shim forwards INT/TERM but not KILL,
        # so only a group kill reaps the native binary underneath it.
        **_new_group_kwargs(),
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=WRITE_PROBE_TIMEOUT_SECONDS
        )
    except BaseException:
        # Timeout AND cancellation (CancelledError is not an Exception):
        # the probe child must never outlive the request — kill the whole
        # group and reap before propagating. The reap itself is bounded so
        # an unkillable child cannot wedge the cleanup path.
        _kill_tree(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=10)
        raise
    return proc.returncode, (out or b"")[-100_000:].decode(
        "utf-8", errors="replace"
    )


async def _ensure_write_capability(workspace: str) -> tuple[bool, str]:
    """Prove the deployed codex can actually WRITE under the sealed sandbox.

    Measured on Windows 2026-08-21 (codex 0.147.0): ``--sandbox
    workspace-write`` was ACCEPTED, the run EXITED 0 — and every write was
    rejected by codex's own tool router ("patch rejected: writing is blocked
    by read-only sandbox; rejected by user approval settings") because the
    restricted-token sandbox could not be built on that machine. Flag
    acceptance and exit code are union claims (Runtime Capability Law); the
    only proof of the capability is a file APPEARING ON DISK. The verdict is
    therefore structural — probe-file existence — and the rejection text
    only enriches the refusal message. Fails CLOSED on any probe-infra
    error (a machine where the probe cannot run cannot demonstrate writes
    either). Memoized per server process; a reconnect re-probes.
    CODEX_ORACLE_SKIP_WRITE_PROBE=1 bypasses — only for machines where
    writes are already verified by hand.
    """
    if os.environ.get("CODEX_ORACLE_SKIP_WRITE_PROBE") == "1":
        return True, "probe skipped (CODEX_ORACLE_SKIP_WRITE_PROBE=1)"
    if _write_capability is not None:  # test/override seam
        return _write_capability
    key = os.path.realpath(workspace)
    async with _write_probe_lock():
        cached = _write_probe_cache.get(key)
        if cached is not None:
            ok, detail, ts, conclusive = cached
            # monotonic: wall-clock rollback must not stretch a TTL
            if conclusive or (
                time.monotonic() - ts < _WRITE_PROBE_INCONCLUSIVE_TTL
            ):
                return ok, detail

        def _record(ok: bool, detail: str, conclusive: bool,
                    rc: int | None = None) -> tuple[bool, str]:
            _write_probe_cache[key] = (
                ok, detail, time.monotonic(), conclusive)
            _journal({"run": "write-probe", "phase": "probe",
                      "ts": time.time(), "workspace": key, "rc": rc,
                      "conclusive": conclusive, "ok": ok,
                      "detail": detail[:200]})
            return ok, detail

        tmp: Path | None = None
        try:
            try:
                # Probe INSIDE the target workspace (its .abraham metadata
                # dir): a green proven on some other volume says nothing
                # about this repo's ACLs/controlled folders. No git setup —
                # the probe argv carries --skip-git-repo-check. Removed in
                # the finally so it never appears in a changes report.
                base = Path(workspace) / ".abraham"
                base.mkdir(parents=True, exist_ok=True)
                tmp = Path(tempfile.mkdtemp(prefix="write-probe-", dir=base))
                rc, output = await _run_write_probe(tmp)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Probe infrastructure failure: INCONCLUSIVE — refuse this
                # dispatch (fail closed); the TTL cache shares this answer
                # with a concurrent burst, then a later dispatch re-probes.
                return _record(
                    False,
                    f"the write probe could not run ({exc!r}) — "
                    "INCONCLUSIVE; a later dispatch re-probes.",
                    conclusive=False,
                )
            if (tmp / "probe.txt").exists():
                # CAPABLE — the only structural proof. Cached for process.
                return _record(
                    True, f"write probe green (exit {rc})",
                    conclusive=True, rc=rc,
                )
            tail = output[-1500:]
            marker = next(
                (m for m in ("read-only sandbox",
                             "rejected by user approval settings")
                 if m in tail), "",
            )
            if marker:
                # INCAPABLE — the measured sandbox refusal. Cached.
                return _record(
                    False,
                    "codex ACCEPTED --sandbox workspace-write and exited "
                    f"{rc}, but the probe file never appeared on disk "
                    f'(codex reported "{marker}").',
                    conclusive=True, rc=rc,
                )
            # No file AND no sandbox marker: auth, rate limiting, model
            # noncompliance, or a CLI failure — INCONCLUSIVE.
            return _record(
                False,
                f"the write probe exited {rc} without writing and without "
                "a recognizable sandbox refusal — INCONCLUSIVE "
                "(auth/rate-limit/CLI failure?); a later dispatch "
                "re-probes.",
                conclusive=False, rc=rc,
            )
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)


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
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15,
        )
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _git_state(cwd: str) -> tuple[bool, set[str], str]:
    """(is_work_tree, dirty porcelain lines, HEAD sha — '' on a repo with no
    commits yet, which is still a valid write target)."""
    rc, out = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    if rc != 0 or out != "true":
        return False, set(), ""
    _, porcelain = _git(["status", "--porcelain"], cwd)
    lines = {ln for ln in porcelain.splitlines() if ln.strip()}
    # --verify --quiet: a repo with no commits yields rc!=0 and NO output.
    # Plain `rev-parse HEAD` echoes the literal string "HEAD" there, which
    # would masquerade as a sha (caught by the test suite).
    rc, head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd)
    return True, lines, head if rc == 0 else ""


_WRITE_REPORT_MAX_CHARS = 3500  # the report is a summary; the DIFF is the review surface


def _write_changes_report(before: set[str], head_before: str, cwd: str) -> str:
    """Attribution block appended to every write-run result.

    Porcelain-line set math cannot see codex piling FURTHER changes onto a
    file that was already dirty (same 'M path' line before and after) — the
    report says so instead of pretending; the caller's diff review covers
    those paths.
    """
    ok, after, head_after = _git_state(cwd)
    if not ok:
        return (
            "\n\n[CHANGED FILES: git state unreadable after the run — "
            "inspect the tree manually]"
        )
    new_lines = sorted(after - before)
    gone_lines = sorted(before - after)
    pre_dirty = sorted(before & after)
    parts = ["\n\n[CHANGED FILES — this write run]"]
    if new_lines:
        parts += [f"  {ln}" for ln in new_lines]
    else:
        parts.append("  (no new working-tree changes attributable to this run)")
    if gone_lines:
        parts.append(
            "  pre-existing dirty entries that DISAPPEARED "
            "(deleted, reverted, or renamed by the run):"
        )
        parts += [f"    {ln}" for ln in gone_lines]
    if pre_dirty:
        parts.append(
            f"  dirty before dispatch ({len(pre_dirty)} path(s)) — further "
            "changes to these are NOT separable here; review them in the diff:"
        )
        parts += [f"    {ln}" for ln in pre_dirty[:20]]
        if len(pre_dirty) > 20:
            parts.append(f"    … +{len(pre_dirty) - 20} more")
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


def _write_lock_path(cwd: str) -> Path:
    return (
        LIVE_LOG_DIR / "write-locks"
        / f"{hashlib.sha1(cwd.encode('utf-8', 'replace')).hexdigest()[:16]}.lock"
    )


def _acquire_write_lock(cwd: str, run_hint: str) -> tuple[bool, str]:
    """One-writer-per-tree MUTUAL EXCLUSION, across server processes.

    The journal liveness check in _active_write_run is advisory only — two
    dispatches in the same second both pass it. This O_EXCL lockfile is the
    authoritative gate (cross-model review finding: an mtime check is not a
    lock). Staleness: no run outlives MAX_RUNTIME_SECONDS, so an older lock
    belongs to a dead process and is broken. Unusable lock dir FAILS CLOSED —
    write dispatch without mutual exclusion is not an acceptable fallback.

    Returns (acquired, holder_description_when_refused).
    """
    path = _write_lock_path(cwd)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _private(path.parent, 0o700)
    except OSError as e:
        return False, f"write-lock dir unusable ({e}) — refusing to write unlocked"
    payload = f"{run_hint} pid={os.getpid()} cwd={cwd} t={int(time.time())}\n"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            return True, ""
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                holder = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue  # holder released between EXISTS and stat — retry
            # A lock whose recorded holder PROCESS is dead is stale now, not
            # in MAX_RUNTIME seconds — a crashed server must not block the
            # recovery resume of its own run for an hour. (Age remains the
            # fallback for unparseable payloads and pid reuse.)
            holder_dead = False
            m = re.search(r"\bpid=(\d+)\b", holder)
            if m and os.name != "nt":
                try:
                    os.kill(int(m.group(1)), 0)
                except ProcessLookupError:
                    holder_dead = True
                except (PermissionError, OSError):
                    pass  # exists (or unknowable) — treat as alive
            if holder_dead or age > MAX_RUNTIME_SECONDS:
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            return False, holder or "unknown holder"
        except OSError as e:
            return False, f"write-lock unusable ({e}) — refusing to write unlocked"
    return False, "lock contention"


def _release_write_lock(cwd: str) -> None:
    with contextlib.suppress(OSError):
        _write_lock_path(cwd).unlink()


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

LIVE_LOG_DIR = Path.home() / ".claude" / "logs" / "codex-oracle"
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
            if not d.is_dir():
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
    tag = f"{label}{seq}·{os.getpid()}"
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
        usage = ev.get("usage") or {}
        if usage:
            state["usage"] = (
                f"tokens in={usage.get('input_tokens', '?')} "
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
                    state["last_error"] = f"command failed: {command} → exit {code}"
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
                state["last_error"] = msg
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
_SHUTDOWN = threading.Event()


def _run_dir_root() -> Path:
    return LIVE_LOG_DIR / "runs"


def _run_spool_dir(run_tag: str) -> Path:
    """Per-run private dir for the child's stdout/stderr spool + answer file.
    Falls back to a temp dir rather than break the run."""
    d = _run_dir_root() / run_tag.replace("·", "-")
    try:
        d.mkdir(parents=True, exist_ok=True)
        _private(_run_dir_root(), 0o700)
        _private(d, 0o700)
        return d
    except OSError:
        return Path(tempfile.mkdtemp(prefix="codex-oracle-run-"))


_WATCHDOG_SH = (
    'pid=$1; pgid=$2; deadline=$3; s=; '
    "trap 'kill $s 2>/dev/null; exit 0' TERM INT; "
    'while kill -0 "$pid" 2>/dev/null; do '
    'if [ "$(date +%s)" -ge "$deadline" ]; then '
    'kill -9 -- "-$pgid" 2>/dev/null; exit 0; fi; '
    'sleep 5 & s=$!; wait $s; done'
)


def _spawn_watchdog(pid: int, pgid: int, deadline_ts: float) -> subprocess.Popen | None:
    """POSIX: a tiny detached /bin/sh that SIGKILLs the codex process group
    at the run deadline, or exits within 5 s of codex ending on its own. The
    MAX_RUNTIME bound therefore holds with NO server alive — a detached run
    has none. Windows keeps the in-server timeout only (returns None)."""
    if os.name == "nt":
        return None
    try:
        wd = subprocess.Popen(
            ["/bin/sh", "-c", _WATCHDOG_SH, "codex-oracle-watchdog",
             str(pid), str(pgid), str(int(deadline_ts))],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        )
        return wd
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    """Is this codex process still running? os.kill(0) plus, on POSIX, a
    guard against pid reuse (the command line must still be a codex)."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout
            return "codex" in out.lower()
    return True


def _kill_pgid(pgid: int, pid: int) -> bool:
    """SIGKILL a run's process group (falls back to the pid). Never raises."""
    try:
        if os.name != "nt" and pgid:
            os.killpg(pgid, signal.SIGKILL)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        return False


def _feed_jsonl(linebuf: bytearray, chunk: bytes, state: dict[str, Any], emit) -> None:
    """Split a `codex exec --json` byte stream into events and digest them
    (shared by the live tail and by adoption replay)."""
    linebuf.extend(chunk)
    while True:
        nl = linebuf.find(b"\n")
        if nl < 0:
            return
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


def _server_alive(pid: int) -> bool:
    """Is the codex-oracle server process that owns a run still alive?"""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if os.name != "nt":
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            return "server.py" in out or "codex-oracle" in out or "run_server" in out
    return True


def _is_detached(rec: dict[str, Any]) -> bool:
    """A run is DETACHED when it has no end record and either its cleanup
    journaled the detach, or the server that spawned it is gone — the
    latter needs no cooperation from a shutdown that may have been torn."""
    if rec.get("has_end") or not rec.get("has_spawn"):
        return False
    if rec.get("has_detached"):
        return True
    owner = int(rec.get("server_pid") or 0)
    return bool(owner) and owner != os.getpid() and not _server_alive(owner)
RESUME_NUDGE = (
    "The previous process was interrupted before your answer arrived. "
    "Continue from where you left off and provide the complete final answer."
)


_journal_lock = threading.Lock()


def _journal(rec: dict[str, Any]) -> None:
    """Append one record to runs.jsonl, flushed so a kill cannot lose it.

    Serialized: journal writes now also come from worker threads
    (asyncio.to_thread for the dispatch tracer), and two unlocked rotations
    could clobber runs.jsonl.1."""
    with contextlib.suppress(Exception), _journal_lock:
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
# CodexErr::ServerOverloaded — pinned by tests/test_transient_retry.py
# against the installed codex source when that worktree is present.
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

    Images attach via ``-i`` on fresh runs only (vision verified live on
    0.144.1); on resume the originals are already part of the persisted
    thread, so re-attaching is unnecessary. ``--image`` is VARIADIC
    (``<FILE>...``) — measured live: if the ``-i`` list sits immediately
    before the positional prompt, clap swallows the prompt as another image
    path and codex reports "No prompt provided via stdin". So images go RIGHT
    AFTER ``exec``, where the following flag terminates the value list and the
    prompt stays a clean trailing positional.

    Write modes (2026-08-14, probed live on 0.147.0 — see PLAN_ABRAHAM_WRITE_
    MODE.md §10): ``write=True`` is the SEALED implementation phase — always
    workspace-write + --ignore-user-config, never danger-full-access, and
    ``infra`` deliberately does NOT open the network here (the read-only
    analysis phase is where infra/web live). Probe: workspace-write created
    files while ``curl`` could not resolve DNS (egress sealed); read-only
    refused the same write (probe calibration). ``auto_compact_limit`` emits
    ``model_auto_compact_token_limit`` — the caller passes it ONLY when the
    model's window is known, because a user-config value beats the vendor's
    90% default OUTRIGHT and a limit above the real window would never fire
    at all.
    """
    argv = [*_codex_argv0(), "exec"]
    if resume_tid is None:
        for img in images or []:
            argv += ["-i", img]
    if write:
        # SEALED implementation process (cross-model review verdict,
        # 2026-08-14: two independent advisors both required the air-gap
        # at the time).
        # Write capability NEVER shares a process with untrusted external
        # content or live credentials: no network egress, no web search
        # (forced off below), no user config → no user MCP servers. The
        # analysis phase (a separate read-only run) had the web/infra access;
        # its brief travels here via the prompt. /tmp and $TMPDIR are
        # excluded from the writable roots — a /tmp artifact would outlive
        # the run OUTSIDE the reviewed diff; build tools get a TMPDIR
        # redirected inside the workspace instead (see _run_codex).
        argv += [*WRITE_SANDBOX_ARGS]
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
        return [override]
    if os.name != "nt":
        return ["codex"]
    shim = shutil.which("codex")
    if not shim:
        return ["codex"]  # spawn fails; the caller reports the install hint
    pkg = Path(shim).parent / "node_modules" / "@openai" / "codex"
    for exe in sorted(pkg.glob("node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe")):
        return [str(exe)]
    js = pkg / "bin" / "codex.js"
    node = shutil.which("node")
    if js.is_file() and node:
        return [node, str(js)]
    return [shim]


def _codex_env() -> dict[str, str]:
    """Subprocess env. On macOS ensure Homebrew's bin dir is on PATH; on
    other platforms the parent PATH passes through untouched (the previous
    unconditional '/opt/homebrew/bin:' prefix corrupted the first PATH entry
    on Windows, where the separator is ';')."""
    env = dict(os.environ)
    if sys.platform == "darwin":
        env["PATH"] = "/opt/homebrew/bin" + os.pathsep + env.get("PATH", "")
    return env


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
        err_fh = open(stderr_path, "ab")
    except OSError as e:
        return (
            "", False, "",
            f"cannot open run spool files under {output_file.parent}: {e}",
            1, None, False,
        )
    _private(stdout_path, 0o600)
    _private(stderr_path, 0o600)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            cwd=workdir or _get_cwd(),
            env={**_codex_env(), **(extra_env or {})},
            # Own process group so _kill_tree reaps the node shim AND the
            # vendored codex grandchild together (POSIX: setsid + killpg;
            # Windows: its own group, reaped via taskkill /T).
            **_new_group_kwargs(),
        )
    except FileNotFoundError:
        return (
            "", False, "",
            "codex binary not found in PATH. Install with: npm i -g @openai/codex",
            127, None, False,
        )
    finally:
        out_fh.close()
        err_fh.close()

    pgid = proc.pid
    if os.name != "nt":
        with contextlib.suppress(OSError):
            pgid = os.getpgid(proc.pid)
    spawn_ts = time.time()
    deadline_ts = spawn_ts + MAX_RUNTIME_SECONDS
    watchdog = _spawn_watchdog(proc.pid, pgid, deadline_ts)
    watchdog_pid = watchdog.pid if watchdog is not None else None
    state["spawn"] = {
        "pid": proc.pid, "pgid": pgid, "watchdog_pid": watchdog_pid,
        "server_pid": os.getpid(),
        "stdout": str(stdout_path), "stderr": str(stderr_path),
        "output_file": str(output_file),
        "spawn_ts": spawn_ts, "deadline_ts": deadline_ts,
    }
    state.pop("detached", None)
    emit(f"▶ codex pid {proc.pid} (pgid {pgid}) spool={output_file.parent}")

    startup_seen = asyncio.Event()
    exited = asyncio.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
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
            chunk = _read_more(path, pos)
            if chunk:
                pos += len(chunk)
                if not startup_seen.is_set():
                    startup_seen.set()
                buffer.append(chunk)
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
                    buffer.append(chunk)
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

    try:
        # Wall-clock timeout prevents zombie processes that start but never
        # finish (observed: research calls stuck for 6+ hours at xhigh).
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task),
            timeout=MAX_RUNTIME_SECONDS,
        )
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
    except asyncio.CancelledError:
        if _SHUTDOWN.is_set() and not state.get("write"):
            # DETACH: the server is going down, not the caller's interest.
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
            if watchdog is not None:
                # Reap it too — a signalled-but-unwaited child is a zombie
                # that still answers kill(0) (measured in the detach suite).
                with contextlib.suppress(Exception):
                    watchdog.terminate()
                    watchdog.wait(timeout=3)
        for _task in (probe_task, heartbeat_task, reaper_task):
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
) -> None:
    """Sit out a provider capacity shed WITHOUT going dark.

    The spinner keeps saying what the run is doing, and while the request's
    progress token is still alive heartbeats keep flowing — _heartbeat_loop
    owns that geometry (same envelope as a running attempt); this only
    brackets the sleep with it. Cancellation propagates to the caller, which
    journals it exactly like a cancel during an attempt.
    """
    if seconds <= 0:
        return
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
        await asyncio.sleep(seconds)
    finally:
        if hb is not None:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb


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
) -> str:
    """Run codex exec headlessly with clean final-message extraction.

    ``workdir`` overrides the run's working tree (abraham targeting a git
    repo BELOW the server's cwd — e.g. a multi-repo project root that is
    not itself a work tree). Empty = the server cwd, unchanged behavior.

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
    write_before: set[str] = set()
    write_head = ""
    extra_env: dict[str, str] = {}
    if write:
        ok, write_before, write_head = _git_state(eff_cwd)
        if not ok:
            return (
                f"[write run refused: {eff_cwd} is not inside a git work "
                "tree. Autonomous writes without version control have no "
                "undo. Run from a git checkout (or `git init` first), or use "
                "the read-only tools instead.]"
            )
        # /tmp and $TMPDIR are excluded from the sandbox's writable roots
        # (_build_exec_argv): a /tmp artifact would outlive the run OUTSIDE
        # the reviewed diff. Build tools still need scratch space, so TMPDIR
        # points at a workspace-local dir — same sandbox, and anything left
        # behind is visible to the review.
        tmp_dir = Path(eff_cwd) / ".abraham" / "tmp"
        with contextlib.suppress(OSError):
            tmp_dir.mkdir(parents=True, exist_ok=True)
            extra_env["TMPDIR"] = str(tmp_dir)

    # Inject the curated external-safe ADVISOR_CONTEXT.md (never CLAUDE.md or
    # memory — those carry live secrets). Fresh threads only: a resumed thread
    # received it on turn one and re-sending would bloat the rollout.
    if resume_tid is None:
        advisor_ctx = _advisor_context()
        if advisor_ctx:
            prompt = f"{advisor_ctx}\n\n{prompt}"

    if write:
        # Sealed implementation phase. Ordered ahead of the infra branch: a
        # write run must get THIS scaffold and never danger-full-access —
        # infra/web belong to the separate read-only analysis phase.
        prompt = (
            "IMPLEMENTATION MODE — you may create, edit and delete files "
            "INSIDE this workspace only (OS-enforced sandbox; writes outside "
            "it will fail).\n"
            "This process is deliberately SEALED: no shell network egress, "
            "no web search, no external tools — untrusted external content "
            "and live credentials never share a process with write access. "
            "Every external fact you need is in your instructions; "
            "everything local you may read from the workspace itself.\n"
            "GIT CONTRACT (non-negotiable): leave ALL changes as uncommitted "
            "working-tree edits for the caller to review. NEVER run git "
            "commit, push, checkout, switch, restore, reset, stash, clean, "
            "rebase, merge, or any branch/tag operation; never touch .git "
            "internals; no bulk deletes. Violations are detected after the "
            "run and reported to the caller.\n\n"
        ) + prompt
    elif infra:
        # Infra mode: full sandbox (network + shell) so Codex can SSH to the
        # server, query the live DB, read container/Dokploy logs, etc., and
        # the MCP servers from ~/.codex/config.toml (auth0, temporal, ...)
        # stay enabled. Read-only DISCIPLINE is enforced via prompt only —
        # callers opt in per call and must trust the investigation prompt.
        prompt = (
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
        ) + prompt

    t0 = time.monotonic()
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
                             "last_error": "", "usage": "", "thread_id": "",
                             "write": "1" if write else ""}

    journaled_tid = ""
    journaled_spawn_pid = 0

    def _emit(text: str) -> None:
        """One event → per-run log, tagged merged stream, session journal.

        The session id is journaled the INSTANT it streams in (not after the
        attempt returns) — a run cancelled mid-flight must still leave a
        resumable handle behind. Regression-tested by the kill-then-recover
        case in the verification suite.
        """
        nonlocal journaled_tid, journaled_spawn_pid
        _live_write(live_fh, t0, text)
        _live_write(stream_fh, t0, text, run_tag)
        tid = state.get("thread_id") or ""
        if tid and tid != journaled_tid:
            journaled_tid = tid
            _journal({"run": run_tag, "phase": "session", "ts": time.time(),
                      "thread_id": tid})
        # The spawn record (pid/pgid/spool paths/deadline) is what lets a
        # LATER server adopt or cancel this process — journaled the instant
        # the child exists, once per attempt.
        sp = state.get("spawn")
        if isinstance(sp, dict) and sp.get("pid") and sp.get("pid") != journaled_spawn_pid:
            journaled_spawn_pid = int(sp["pid"])
            _journal({"run": run_tag, "phase": "spawn", "ts": time.time(), **sp})

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

    _journal({
        "run": run_tag, "phase": "start", "ts": time.time(), "engine": "codex",
        "tool": tool_name,
        "model": model, "reasoning": reasoning, "infra": infra, "write": write,
        "web_search": web_search, "parent_run": parent_run,
        "images": list(images or []), "cwd": eff_cwd,
        "prompt": prompt, "log": str(live_path or ""),
    })

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
            sp = state.get("spawn") or {}
            remaining = int(max(0.0, float(sp.get("deadline_ts") or 0) - time.time()))
            _journal({"run": run_tag, "phase": "detached", "ts": time.time(),
                      "status": "detached", "pid": sp.get("pid"),
                      "pgid": sp.get("pgid"), "attempts": attempt + 1,
                      "retry_classes": list(retry_classes),
                      "capacity_wait_s": int(waited_total)})
            with contextlib.suppress(Exception):
                _emit(
                    f"■ server shutting down — codex keeps running DETACHED "
                    f"(pid {sp.get('pid')}, hard deadline in {remaining}s). "
                    f"Collect it from the next connection: "
                    f"codex_resume_run(run=\"{run_tag}\")"
                )
        else:
            _journal({"run": run_tag, "phase": "end", "ts": time.time(),
                      "status": "cancelled", "attempts": attempt + 1,
                      "retry_classes": list(retry_classes),
                      "capacity_wait_s": int(waited_total)})
            with contextlib.suppress(Exception):
                _emit(
                    "■ run cancelled by caller"
                    + (f" {where}" if where else "")
                    + " (resume later: codex_resume_run)"
                )
        for _fh in (live_fh, stream_fh):
            if _fh is not None:
                with contextlib.suppress(Exception):
                    _fh.close()

    # One request-time anchor for EVERY attempt: retries must not reset the
    # heartbeat deadline (the client's progress token is request-scoped).
    if request_started is None:
        request_started = time.monotonic()
    final_message = ""
    clean_extraction = False
    stdout_text = stderr_text = ""
    returncode = 0
    hung_reason: str | None = None
    timed_out = False

    while True:
        # Per-run spool: answer file + the child's stdout/stderr live here
        # (file-backed so the run survives this server; see RUN SURVIVABILITY).
        output_file = _run_spool_dir(run_tag) / f"attempt{attempt}.txt"
        cmd = _build_exec_argv(
            model, reasoning, infra, web_search, output_file,
            prompt=prompt, resume_tid=resume_tid, images=images,
            write=write, auto_compact_limit=ac_limit,
        )
        expected_tid = resume_tid
        state["last_error"] = ""
        state["last_message"] = ""  # attempt N's commentary is not attempt N+1's answer
        try:
            (final_message, clean_extraction, stdout_text, stderr_text,
             returncode, hung_reason, timed_out) = await _exec_codex_once(
                cmd, output_file, state, _emit, ctx, model,
                extra_env=extra_env, workdir=eff_cwd,
                request_started=request_started)
        except asyncio.CancelledError:
            _cancelled("")
            raise

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
            attempt += 1
            retry_classes.append(klass)
            resume_tid = state.get("thread_id") or None
            wait = 0.0
            if klass == "overload":
                # ±20% jitter so runs shed together do not return together;
                # clamped so the cap is the cap.
                wait = min(
                    OVERLOAD_BACKOFF_CAP_SECONDS,
                    _overload_backoff_seconds(overload_retries)
                    * random.uniform(0.8, 1.2),
                )
                overload_retries += 1
                waited_total += wait
            else:
                disconnect_retries += 1
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
                f"total attempt {attempt + 1}"
                + (f", after a {int(wait)}s wait)" if wait else ")")
            )
            if wait:
                try:
                    await _wait_for_capacity(
                        wait, ctx, request_started, t0, state, model, _emit
                    )
                except asyncio.CancelledError:
                    _cancelled("during the capacity wait")
                    raise
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
    _journal({
        "run": run_tag, "phase": "end", "ts": time.time(), "status": status,
        "returncode": returncode, "attempts": attempt + 1,
        "retry_classes": retry_classes, "capacity_wait_s": int(waited_total),
        "error": (state["last_error"] or stderr_text)[:500] if status != "ok" else "",
        "result_file": result_file,
    })

    # Every write-run outcome — success, timeout, hang, error — reports what
    # changed on disk: a timed-out run may still have written files, and the
    # caller's next step is reviewing exactly that.
    write_report = (
        _write_changes_report(write_before, write_head, eff_cwd)
        if write else ""
    )

    if timed_out:
        if final_message:
            return (
                f"[Codex model: {model} | reasoning: {reasoning}"
                f"{_answer_sig(tool_name, 'timeout')}]\n"
                f"[TIMEOUT after {MAX_RUNTIME_SECONDS}s — partial output recovered]"
                f"{log_note}\n\n"
                f"{final_message}{write_report}"
            )
        return (
            f"[Codex TIMEOUT: no response after {MAX_RUNTIME_SECONDS}s]\n"
            f"The model may be overloaded or the query too complex. "
            f"Try simplifying the prompt or reducing reasoning effort."
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
                f"{MAX_RUNTIME_SECONDS}s deadline)"
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
            f"{_answer_sig(tool_name, 'error')}]\n{detail}"
            f"{capacity_note}\n"
            f"[recoverable: call codex_resume_run to continue this run "
            f"(run id: {run_tag})]{partial}{write_report}"
            f"{_stderr_diagnostics()}{log_note}"
        )
    else:
        # status:ok is EARNED by exit 0 (the push gate reads the signature).
        header = (
            f"[Codex model: {model} | reasoning: {reasoning}"
            f"{_answer_sig(tool_name, 'ok')}]"
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
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def architect_review(
    description: str,
    files: str = "",
    concerns: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
    images: list[str] = [],
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
    result = await _run_codex(
        "\n".join(prompt_parts), infra=infra, ctx=ctx,
        web_search=web_search, reserve=reserve,
        images=list(images or []),
        tool_name="architect_review",
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@mcp.tool()
async def code_review(
    code_or_diff: str,
    context: str = "",
    focus: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
    images: list[str] = [],
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
    """
    hits = _detect_anchoring({"context": context, "focus": focus})

    prompt_parts = [
        "You are an elite code reviewer who has prevented production outages.",
        "Perform a deep, line-by-line review. Think step by step.",
        "Look for: bugs, security vulnerabilities (OWASP Top 10),",
        "performance issues (N+1 queries, memory leaks), race conditions,",
        "edge cases (empty, null, overflow), error handling gaps,",
        "and violations of clean code principles.",
        "Do NOT compliment the code. Find real problems.",
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
    result = await _run_codex(
        "\n".join(prompt_parts), infra=infra, ctx=ctx,
        web_search=web_search, reserve=reserve,
        images=list(images or []),
        tool_name="code_review",
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@mcp.tool()
async def research(
    topic: str,
    constraints: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool | None = None,
    images: list[str] = [],
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
    result = await _run_codex(
        "\n".join(prompt_parts), infra=bool(infra), ctx=ctx,
        web_search=web_search, reserve=reserve,
        images=list(images or []),
        tool_name="research",
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
    result = await _run_codex(
        preamble + body, infra=infra, ctx=ctx,
        web_search=web_search, reserve=reserve,
        images=list(images or []),
        tool_name="codex_query",
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@mcp.tool()
async def abraham(
    task: str,
    context: str = "",
    constraints: str = "",
    web_search: bool = True,
    infra: bool = False,
    allow_dirty: bool = False,
    images: list[str] = [],
    cwd: str = "",
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """
    WRITE MODE — the one tool that EDITS FILES, as TWO air-gapped phases:

    1. ANALYSIS (read-only): codex investigates the codebase, the live web
       (`web_search`), and live infrastructure (`infra` — SSH, DB, logs,
       your MCP servers) and produces an implementation brief.
    2. IMPLEMENTATION (sealed): a separate codex process with
       workspace-write file access and NOTHING else — no network, no web,
       no user MCP servers — implements the brief and reports. Untrusted
       external content and live credentials never share a process with
       write capability.

    Mode algebra: read and write are separate TOOLS (structural
    exclusivity); `infra`/`web_search` compose with abraham by governing
    its ANALYSIS phase. The implementation phase is always sealed.

    Safety envelope (enforced, not trusted): git work tree required; a
    DIRTY tree is refused unless `allow_dirty=true` (the implementer may
    legitimately rewrite files, and uncommitted edits it touches have no
    undo — committed work is always recoverable); one writer per tree via
    an authoritative lockfile held across both phases; no automatic retry
    once a write process starts (a replay after partial writes
    double-applies); codex must not commit/push — every outcome ends with a
    CHANGED FILES report (this run's changes vs pre-existing dirt, HEAD
    verified unmoved) for you to review with `git diff`. Long
    implementation runs auto-compact codex's context at ~65% of the model
    window (read from the deployed binary's own registry, never guessed).

    This is a DIRECTIVE dispatch, not an advisory one: state the desired
    outcome plainly — no anchoring lint, no caller_hypothesis; the contract
    is "implement", not "judge". While it runs, do NOT edit the tree
    yourself; afterwards, review the diff before anything is committed.

    Args:
        task: What to build/fix/change, with the desired outcome and
            acceptance criteria; repro steps for bugfixes.
        context: What you already know — relevant paths, prior findings,
            decisions already made and why. Feed `research` output here to
            shorten the analysis phase (or to skip web entirely).
        constraints: Hard boundaries — files/areas NOT to touch, APIs to
            keep stable, scope rules beyond the repo's own docs.
        web_search: Analysis-phase live-web verification of external
            claims. The implementation phase never has web access either
            way.
        infra: Analysis-phase read-only live-infrastructure investigation.
            The implementation phase never has infra access either way.
        allow_dirty: Accept a dirty working tree (default False — see
            safety envelope).
        images: Local image paths (mockups, error screenshots) — attached
            to both phases.
        cwd: Target git work tree, when it is not the server's own cwd —
            required for multi-repo project roots that are not themselves
            git repos. Must be the server cwd itself or a directory BELOW
            it (never outside the workspace). Empty = server cwd.
    """
    if not task.strip():
        return "[abraham refused: empty task — state what to implement.]"

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
        base = Path(_get_cwd()).resolve()
        target = Path(cwd)
        if not target.is_absolute():
            target = base / target
        target = target.resolve()
        if not target.is_dir():
            return f"[abraham refused: cwd '{cwd}' is not a directory.]"
        # Workspace fence: same reasoning as resume's scoping — a subtree of
        # the current workspace IS this workspace; anything outside it is a
        # different project and must be dispatched from there.
        if target != base and base not in target.parents:
            return (
                f"[abraham refused: cwd '{target}' is outside the server's "
                f"workspace ({base}). Dispatch from that project instead.]"
            )
        cwd = str(target)
    else:
        cwd = _get_cwd()
    ok, dirty, _head = _git_state(cwd)
    if not ok:
        return (
            f"[abraham refused: {cwd} is not inside a git work tree. "
            "Autonomous writes without version control have no undo.]"
        )
    if dirty and not allow_dirty:
        sample = "\n".join(f"  {ln}" for ln in sorted(dirty)[:10])
        more = f"\n  … +{len(dirty) - 10} more" if len(dirty) > 10 else ""
        return (
            "[abraham refused: the working tree is DIRTY and the sealed "
            "implementer may overwrite uncommitted work irrecoverably "
            "(committed work is always recoverable).\n"
            f"{sample}{more}\n"
            "Commit or stash first — or pass allow_dirty=true to accept "
            "the risk; the final report will separate this run's changes "
            "from the pre-existing dirt.]"
        )

    # The deployed codex must PROVE it can write under the sealed sandbox
    # before anything runs — measured on Windows 2026-08-21: workspace-write
    # silently downgraded to read-only + approval prompts and every write
    # was rejected while the run still EXITED 0, so an unprobed abraham
    # "succeeds" having written nothing.
    can_write, probe_detail = await _ensure_write_capability(cwd)
    if not can_write:
        return (
            "[abraham refused: this machine's codex cannot WRITE under the "
            f"sealed sandbox — {probe_detail} A run here could complete "
            "without writing anything. Fix the codex sandbox (on Windows "
            "the ELEVATED sandbox backend must be available — the sealed "
            "argv requires it because only that backend enforces the "
            "no-egress air-gap), or set CODEX_ORACLE_SKIP_WRITE_PROBE=1 "
            "only if you have verified writes AND egress sealing by hand.]"
        )

    # One writer per tree — the authoritative lock is held across BOTH
    # phases (the brief describes the tree as analyzed; another writer
    # landing between the phases would invalidate it). The journal check
    # stays as an advisory belt for server processes running older code.
    live_run = _active_write_run(cwd)
    if live_run:
        return (
            f"[abraham refused: write run {live_run} is still LIVE in this "
            "workspace (one writer per tree). Wait for it or cancel it; an "
            "interrupted write run is resumable via codex_resume_run.]"
        )
    got_lock, holder = _acquire_write_lock(cwd, "abraham")
    if not got_lock:
        return (
            f"[abraham refused: another write run holds this tree's lock "
            f"({holder}). One writer per tree.]"
        )
    try:
        # ---- Phase 1: read-only analysis → implementation brief ----
        analysis_parts = [
            "PHASE 1 of 2 — ANALYSIS ONLY. You are a senior engineer "
            "preparing an implementation. A SEPARATE, SEALED process will "
            "do the writing: it has full read/write access to this "
            "workspace but NO web search, NO network and NO external "
            "tools, and it will see ONLY your final message. So your final "
            "message must be a complete IMPLEMENTATION BRIEF:",
            "",
            "## Findings — how the relevant system actually works, with "
            "file:line references (trace real call paths, don't guess).",
        ]
        if infra:
            analysis_parts.append(
                "## Live state — what the live infrastructure shows "
                "(read-only investigation) where it bears on the task."
            )
        if web_search:
            analysis_parts.append(
                "## External facts — every API/version/vendor behavior the "
                "implementer must rely on, verified against current "
                "primary sources, with URLs and EXACT values (the "
                "implementer cannot look anything up)."
            )
        analysis_parts += [
            "## Plan — the minimal COMPLETE change: ordered edits per "
            "file, including error paths and edge cases; no artificial "
            "caps, no silently deferred scope.",
            "## Risks — what could break, and what to check.",
            "## Verification — the exact fast checks to run "
            "(tests/linters/build) and expected outcomes.",
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

        brief = await _run_codex(
            "\n".join(analysis_parts),
            infra=infra, ctx=ctx, web_search=web_search,
            images=list(images or []),
            tool_name="abraham", workdir=cwd,
            request_started=request_t0,
        )
        for marker in ("[Codex TIMEOUT", "[Codex error",
                       "[Codex health check FAILED"):
            if marker in brief[:200]:
                return (
                    "[abraham: ANALYSIS phase failed — nothing was "
                    f"written. Phase-1 result follows.]\n\n{brief}"
                )

        # ---- Phase 2: sealed implementation ----
        impl_parts = [
            "PHASE 2 of 2 — IMPLEMENT. A read-only analysis run has "
            "already investigated this task; its brief is below. You have "
            "full local code access — re-read anything you need — but no "
            "web/network/external tools: every external fact you need is "
            "in the brief.",
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

        result = await _run_codex(
            "\n".join(impl_parts),
            write=True, infra=False, ctx=ctx, web_search=False,
            images=list(images or []),
            tool_name="abraham", workdir=cwd,
            request_started=request_t0,
        )
        return (
            "[abraham — phase 1 analyzed (read-only"
            + (", infra" if infra else "")
            + (", live web" if web_search else "")
            + "); phase 2 implemented (sealed: no web/network/MCP)]\n\n"
            + result
        )
    finally:
        _release_write_lock(cwd)


@mcp.tool()
async def codex_resume_run(
    run: str = "",
    nudge: str = "",
    infra: bool | None = None,
    web_search: bool | None = None,
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

    Every continuation RESTATES the original task from the run journal:
    a hard-killed turn may have persisted none of its input vendor-side
    (measured 2026-08-09), so the thread cannot be trusted to remember the
    question. Threads that did retain context see a labelled duplicate.

    SWITCH CAPABILITIES MID-COURSE. A resume may change the run's settings,
    so a run that turns out to need something it didn't have can be rescued
    instead of restarted: cancel the call, then resume the SAME thread — all
    its accumulated reasoning and tool output intact — with new settings and
    new instructions. The classic case: research that needs the live system.

        codex_resume_run(run="codex3·8123", infra=True,
                         nudge="SSH to the app host and check the celery "
                               "queue depth, then finish the analysis")

    Write runs (abraham) resume as write runs — sandbox, git contract and
    changed-files report intact. The write axis itself has no override:
    read runs cannot be escalated to write by resuming, and write runs
    cannot be downgraded mid-implementation.

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
    # from another workspace is refused rather than silently retrieved.
    # Anchor the heartbeat deadline to THIS request's start: a write resume
    # can spend up to WRITE_PROBE_TIMEOUT_SECONDS in the capability gate
    # before codex spawns, and a fresh per-run clock there would resurrect
    # dead-token sends (round-2 review, 2026-08-21).
    request_t0 = time.monotonic()

    cwd = _get_cwd()
    all_runs = _journal_runs()

    def _in_workspace(run_cwd: str) -> bool:
        return _run_in_workspace(run_cwd, cwd)

    # has_start excludes evidence-only journal groups (abraham-dispatch
    # tracers, write-probe verdicts) — they are diagnostics, not runs, and
    # cannot be resumed.
    runs = {k: v for k, v in all_runs.items()
            if _in_workspace(str(v.get("cwd") or "")) and v.get("has_start")}

    if run == "list":
        return await codex_runs()

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
            return collected
        rec = _journal_runs().get(run, rec)
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
    if use_write:
        # A write continuation is SEALED like every write process — even if
        # the caller asked for infra/web, and regardless of what an older
        # journal record claims. The analysis phase is where those lived.
        use_infra = False
        use_web = False
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
        if _pid_alive(int(rec.get("pid") or 0)) and not _is_detached(rec):
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

    switches = []
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
    if original_prompt:
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
    if use_write:
        # Same gate as a fresh abraham dispatch: a resumed write run on a
        # machine whose sandbox cannot write reproduces the original
        # exit-0/no-write failure (round-2 review, 2026-08-21). Keyed to
        # the run's OWN tree — the thing that will actually be written.
        can_write, probe_detail = await _ensure_write_capability(run_cwd)
        if not can_write:
            return (
                "[resume refused: this machine's codex cannot WRITE under "
                f"the sealed sandbox — {probe_detail}]"
            )
        got_lock, holder = _acquire_write_lock(run_cwd, f"resume:{run}")
        if not got_lock:
            return (
                f"[resume refused: another write run holds this tree's "
                f"lock ({holder}). One writer per tree.]"
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
        )
    finally:
        if use_write:
            _release_write_lock(run_cwd)


# ---------------------------------------------------------------------------
# Detached-run adoption + run operations (1.17.0)
# ---------------------------------------------------------------------------

def _run_status(rec: dict[str, Any]) -> str:
    """One word for a journal record: RUNNING / DETACHED / ok / error /
    cancelled / timeout / hung / INTERRUPTED (no end record, process gone)."""
    if rec.get("has_end"):
        return str(rec.get("status") or "?")
    alive = _pid_alive(int(rec.get("pid") or 0))
    if _is_detached(rec):
        return "DETACHED" if alive else "DETACHED-ENDED"
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
        if r.get("has_start") and _run_in_workspace(str(r.get("cwd") or ""), cwd)
    ]
    return runs[-limit:] if limit else runs


def _replay_spool(rec: dict[str, Any], emit) -> dict[str, Any]:
    """Digest a run's stdout spool from the start into a fresh state."""
    state: dict[str, Any] = {"activity": "", "last_message": "", "last_error": "",
                             "usage": "", "thread_id": ""}
    path = Path(str(rec.get("stdout") or ""))
    buf = bytearray()
    with contextlib.suppress(OSError):
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                _feed_jsonl(buf, chunk, state, emit)
    return state


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
    out = Path(str(rec.get("output_file") or ""))
    deadline = float(rec.get("deadline_ts") or 0)
    alive = _pid_alive(pid)
    if alive and nudge:
        return (
            f"[Run {run} is still RUNNING detached (codex pid {pid}). Its thread "
            f"cannot take new instructions while it is being written: call "
            f"codex_resume_run(run=\"{run}\") without a nudge to wait for and "
            f"collect its answer, or codex_cancel_run(run=\"{run}\") to stop it.]"
        )
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
        pos = 0
        with contextlib.suppress(OSError):
            pos = spool.stat().st_size
        buf = bytearray()
        while _pid_alive(pid):
            if deadline and time.time() > deadline + 30:
                _kill_pgid(pgid, pid)
                timed_out = True
                _emit(f"■ detached run past its {MAX_RUNTIME_SECONDS}s deadline "
                      f"— killed")
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
        # Final drain after exit.
        with contextlib.suppress(OSError):
            with open(spool, "rb") as fh:
                fh.seek(pos)
                rest = fh.read()
            if rest:
                _feed_jsonl(buf, rest, state, _emit)
    finally:
        if hb is not None:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await hb
    final = ""
    with contextlib.suppress(OSError):
        if out.exists() and out.stat().st_size > 0:
            final = out.read_text(encoding="utf-8", errors="replace").strip()
    if not final and not timed_out:
        final = str(state.get("last_message") or "")
    status = "ok" if (final and not timed_out and not state.get("last_error")) else (
        "timeout" if timed_out else "error")
    result_file = ""
    if status == "ok" and live_path is not None:
        with contextlib.suppress(Exception):
            rf = live_path.with_suffix(".result.txt")
            rf.write_text(final, encoding="utf-8")
            _private(rf, 0o600)
            result_file = str(rf)
    _emit(f"■ adoption finished: status={status} {state.get('usage') or ''}".rstrip())
    _journal({
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
    log_note = f"\n\n[live log: {live_path}]" if live_path else ""
    if status == "ok":
        return (
            f"[Codex model: {model} | reasoning: {reasoning}"
            f"{_answer_sig(tool_name, 'ok')}]\n"
            f"[collected from run {run}, which outlived its MCP call (server "
            f"restart) — no re-ask, no new model call]{log_note}\n\n{final}"
        )
    if final:
        return (
            f"[Codex model: {model} | reasoning: {reasoning}"
            f"{_answer_sig(tool_name, status)}]\n"
            f"[collected from detached run {run} — it ended with "
            f"{'a timeout' if timed_out else 'an error: ' + str(state.get('last_error'))[:300]}; "
            f"partial output follows]{log_note}\n\n{final}"
        )
    return None  # died without an answer → caller falls back to a thread resume


@mcp.tool()
async def codex_runs(limit: int = 10) -> str:
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
    runs = _workspace_runs(max(1, min(int(limit or 10), 50)))
    if not runs:
        return "[No recorded codex runs for this workspace.]"
    lines = ["Codex runs in this workspace (oldest → newest):"]
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
        "codex_run_log(run=<id>). Stop one: codex_cancel_run(run=<id>)."
    )
    return "\n".join(lines)


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


@mcp.tool()
async def codex_run_log(run: str = "", lines: int = 40) -> str:
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


@mcp.tool()
async def codex_cancel_run(run: str = "") -> str:
    """
    Stop a RUNNING or DETACHED codex run: SIGKILL its process group (and its
    watchdog) and journal it as cancelled. Omit `run` to stop the most recent
    live run in this workspace. The thread stays on disk, so the run remains
    resumable with a nudge via codex_resume_run.

    Args:
        run: Run id from codex_runs / a failure message; "" = most recent live run.
    """
    runs = _workspace_runs()
    live = [r for r in runs if _run_status(r) in ("RUNNING", "DETACHED")]
    rec = None
    if run:
        rec = next((r for r in runs if r.get("run") == run), None)
        if rec is None:
            return f"[Unknown run id '{run}' in this workspace. Try codex_runs().]"
        if _run_status(rec) not in ("RUNNING", "DETACHED"):
            return f"[Run {run} is not running (status {_run_status(rec)}); nothing to stop.]"
    else:
        if not live:
            return "[No running or detached codex run in this workspace.]"
        rec = live[-1]
    pid = int(rec.get("pid") or 0)
    pgid = int(rec.get("pgid") or 0)
    killed = _kill_pgid(pgid, pid) if pid else False
    wd = int(rec.get("watchdog_pid") or 0)
    if wd:
        with contextlib.suppress(Exception):
            os.kill(wd, signal.SIGTERM)
    tag = str(rec.get("run"))
    _journal({"run": tag, "phase": "end", "ts": time.time(), "status": "cancelled",
              "cancelled_by": "codex_cancel_run", "returncode": -9})
    return (
        f"[Run {tag} stopped (pid {pid}, pgid {pgid}, killed={killed}). "
        f"Its thread is on disk: codex_resume_run(run=\"{tag}\", nudge=...) "
        f"continues it if needed.]"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _install_shutdown_handlers(hard_exit=True)
    try:
        mcp.run(transport="stdio")
    finally:
        if _SHUTDOWN.is_set():
            # Cleanup has run (journals fsync'd, logs closed); do not wait
            # for the stdio reader thread the client may never release.
            os._exit(0)
