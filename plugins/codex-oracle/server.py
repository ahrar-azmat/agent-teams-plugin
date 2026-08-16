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
import os
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

# Progress heartbeat interval. Claude Code aborts any MCP tool call that
# produces no response AND no progress notification for 30 minutes
# ("idle timeout 1800s") — long max-effort runs and laptop-sleep gaps both
# crossed it (2026-07-27: a review was killed at 5929s idle after a ~94-min
# lid-close; the 60-min MAX_RUNTIME budget was unreachable through MCP
# without progress). Heartbeats reset the client's idle timer and surface
# liveness (elapsed time + output bytes). Env knob exists for the selftest.
PROGRESS_INTERVAL_SECONDS = float(
    os.environ.get("CODEX_ORACLE_PROGRESS_INTERVAL", "10")
)

# STOP heartbeating once the client has BACKGROUNDED the call — measured
# incident 2026-08-09. Claude Code moves an MCP call to a background task at
# ~120s and DEREGISTERS that request's progress token. Our heartbeat kept
# sending on it every 10s; each one came back as
#   "Connection error: Received a progress notification for an unknown token"
# and after enough of them the client KILLED THE SERVER
#   ("SIGINT failed, sending SIGTERM to MCP server process")
# taking every SIBLING in-flight run down with it
#   ("Tool 'architect_review' failed after 269s: MCP error -32000: Connection
#    closed").
# Heartbeats only ever existed to stop the client's 30-min idle-abort while it
# WAITS on the call; once the call is backgrounded the client no longer waits
# (it gets a completion notification instead), so further progress is useless
# AND actively harmful. Stop just past the backgrounding threshold. The live
# log keeps streaming regardless, so nothing observable is lost.
PROGRESS_MAX_SECONDS = float(
    os.environ.get("CODEX_ORACLE_PROGRESS_MAX_SECONDS", "150")
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
    """Drop run logs older than the retention window. Never raises."""
    cutoff = time.time() - LIVE_LOG_RETENTION_DAYS * 86_400
    with contextlib.suppress(OSError):
        for p in LIVE_LOG_DIR.glob("*.log"):
            if p.is_symlink():
                continue
            with contextlib.suppress(OSError):
                if p.stat().st_mtime < cutoff:
                    p.unlink()


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
RESUME_NUDGE = (
    "The previous process was interrupted before your answer arrived. "
    "Continue from where you left off and provide the complete final answer."
)


def _journal(rec: dict[str, Any]) -> None:
    """Append one record to runs.jsonl, flushed so a kill cannot lose it."""
    with contextlib.suppress(Exception):
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
    return runs


def _is_transient_error(text: str) -> bool:
    """Failures worth an automatic resume/retry: infrastructure, not semantics.

    Deliberately excludes auth (needs a human), usage/quota policy denials,
    and argument errors (retrying can't fix them).
    """
    t = text.lower()
    signals = (
        "stream disconnected",
        "stream closed",
        "connection reset",
        "connection closed",
        "connection refused",
        "connection error",
        "network error",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
        "internal server error",
        "bad gateway",
        "gateway timeout",
        "error 500",
        "error 502",
        "error 503",
        "error 504",
        " 429",
        "rate limit",
        "too many requests",
        "retry later",
    )
    return any(s in t for s in signals)


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
        argv += [
            "--sandbox", "workspace-write",
            "--ignore-user-config",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        ]
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
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
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
) -> tuple[str, bool, str, str, int, str | None, bool]:
    """One codex exec attempt (spawn → stream → reap).

    Returns ``(final_message, clean_extraction, stdout_text, stderr_text,
    returncode, hung_reason, timed_out)``. The final message is read from
    ``output_file`` (deleted here), falling back to the last parsed
    agent_message. Live-log handles stay OPEN — the orchestrator owns them
    across retry attempts.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_get_cwd(),
            limit=SUBPROCESS_BUFFER_LIMIT,
            env={**_codex_env(), **(extra_env or {})},
            # Own process group so _kill_tree reaps the node shim AND the
            # vendored codex grandchild together (POSIX: setsid + killpg;
            # Windows: its own group, reaped via taskkill /T).
            **_new_group_kwargs(),
        )
    except FileNotFoundError:
        output_file.unlink(missing_ok=True)
        return (
            "", False, "",
            "codex binary not found in PATH. Install with: npm i -g @openai/codex",
            127, None, False,
        )

    startup_seen = asyncio.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    hung_reason: str | None = None
    timed_out = False
    stdout_linebuf = bytearray()

    def _feed_stdout(chunk: bytes) -> None:
        """Split the JSONL event stream into lines and feed the live view."""
        stdout_linebuf.extend(chunk)
        while True:
            nl = stdout_linebuf.find(b"\n")
            if nl < 0:
                return
            raw = bytes(stdout_linebuf[: nl]).strip()
            del stdout_linebuf[: nl + 1]
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                line = _process_exec_event(ev, state)
            except ValueError:
                # Not JSON (stray CLI noise) — show it verbatim.
                line = raw.decode("utf-8", errors="replace")
            if line:
                emit(line)

    def _feed_stderr(chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="replace")
        for ln in text.splitlines():
            if ln.strip():
                emit(f"! {ln}")

    async def _consume(
        stream: asyncio.StreamReader,
        buffer: list[bytes],
        on_chunk,
    ) -> None:
        """Consume a subprocess stream using fixed-size reads.

        Uses read(READ_CHUNK_SIZE) instead of readline() to avoid
        LimitOverrunError — read() never searches for a separator so it
        cannot overflow regardless of line length or buffer limit.
        The live view must never break the run: feeder errors are swallowed.
        """
        while True:
            chunk = await stream.read(READ_CHUNK_SIZE)
            if not chunk:
                return
            if not startup_seen.is_set():
                startup_seen.set()
            buffer.append(chunk)
            with contextlib.suppress(Exception):
                on_chunk(chunk)

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

    async def _heartbeat() -> None:
        """Emit MCP progress every PROGRESS_INTERVAL_SECONDS while codex runs.

        Resets Claude Code's 30-min idle-abort timer (the cause of the
        2026-07-27 "hangs") and gives the operator a live elapsed/output
        signal. A failed notification must never affect the run itself.
        """
        assert ctx is not None
        started = time.monotonic()
        while True:
            await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
            elapsed = time.monotonic() - started
            if elapsed > PROGRESS_MAX_SECONDS:
                # Past the client's backgrounding threshold: the request's
                # progress token is gone, and sending on it kills the whole
                # server (see PROGRESS_MAX_SECONDS). Stop — the live log
                # continues to carry every event.
                emit(
                    f"⏱ progress notifications stopped at {int(elapsed)}s "
                    f"(call backgrounded by the client; live log continues)"
                )
                return
            with contextlib.suppress(Exception):
                await ctx.report_progress(
                    min(elapsed, MAX_RUNTIME_SECONDS),
                    MAX_RUNTIME_SECONDS,
                    f"codex {model} · {int(elapsed)}s · "
                    f"{state['activity'][:140]}",
                )

    stdout_task = asyncio.create_task(_consume(proc.stdout, stdout_chunks, _feed_stdout))
    stderr_task = asyncio.create_task(_consume(proc.stderr, stderr_chunks, _feed_stderr))
    probe_task = asyncio.create_task(_startup_probe())
    heartbeat_task = (
        asyncio.create_task(_heartbeat()) if ctx is not None else None
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
        _kill_tree(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        output_file.unlink(missing_ok=True)
        raise
    finally:
        # Always ensure process is reaped and watchdog tasks are cancelled.
        if proc.returncode is None:
            _kill_tree(proc)
        with contextlib.suppress(Exception):
            await proc.wait()
        for _task in (probe_task, heartbeat_task):
            if _task is not None:
                _task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await _task

    stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()

    final_message = ""
    clean_extraction = False
    try:
        if output_file.exists() and output_file.stat().st_size > 0:
            final_message = output_file.read_text(encoding="utf-8", errors="replace").strip()
            clean_extraction = bool(final_message)
    finally:
        output_file.unlink(missing_ok=True)

    if not final_message and not timed_out:
        # stdout is a JSONL event stream — the parsed last agent message is
        # the meaningful fallback; raw stdout only as a last resort.
        final_message = state["last_message"] or stdout_text

    return (
        final_message, clean_extraction, stdout_text, stderr_text,
        proc.returncode or 0, hung_reason, timed_out,
    )


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------

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
) -> str:
    """Run codex exec headlessly with clean final-message extraction.

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
    write_before: set[str] = set()
    write_head = ""
    extra_env: dict[str, str] = {}
    if write:
        ok, write_before, write_head = _git_state(_get_cwd())
        if not ok:
            return (
                f"[write run refused: {_get_cwd()} is not inside a git work "
                "tree. Autonomous writes without version control have no "
                "undo. Run from a git checkout (or `git init` first), or use "
                "the read-only tools instead.]"
            )
        # /tmp and $TMPDIR are excluded from the sandbox's writable roots
        # (_build_exec_argv): a /tmp artifact would outlive the run OUTSIDE
        # the reviewed diff. Build tools still need scratch space, so TMPDIR
        # points at a workspace-local dir — same sandbox, and anything left
        # behind is visible to the review.
        tmp_dir = Path(_get_cwd()) / ".abraham" / "tmp"
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
    state: dict[str, str] = {"activity": "launching codex", "last_message": "",
                             "last_error": "", "usage": "", "thread_id": ""}

    journaled_tid = ""

    def _emit(text: str) -> None:
        """One event → per-run log, tagged merged stream, session journal.

        The session id is journaled the INSTANT it streams in (not after the
        attempt returns) — a run cancelled mid-flight must still leave a
        resumable handle behind. Regression-tested by the kill-then-recover
        case in the verification suite.
        """
        nonlocal journaled_tid
        _live_write(live_fh, t0, text)
        _live_write(stream_fh, t0, text, run_tag)
        tid = state.get("thread_id") or ""
        if tid and tid != journaled_tid:
            journaled_tid = tid
            _journal({"run": run_tag, "phase": "session", "ts": time.time(),
                      "thread_id": tid})

    if live_fh is not None:
        with contextlib.suppress(Exception):
            live_fh.write(
                f"# codex-oracle live view — {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
                f"# model={model} effort={reasoning} "
                f"mode={mode_str}{ac_note} "
                f"web_search={'live' if web_search else 'disabled'} cwd={_get_cwd()}\n"
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
        "images": list(images or []), "cwd": _get_cwd(),
        "prompt": prompt, "log": str(live_path or ""),
    })

    attempt = 0
    final_message = ""
    clean_extraction = False
    stdout_text = stderr_text = ""
    returncode = 0
    hung_reason: str | None = None
    timed_out = False

    while True:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="codex-oracle-"
        )
        tmp.close()
        output_file = Path(tmp.name)
        cmd = _build_exec_argv(
            model, reasoning, infra, web_search, output_file,
            prompt=prompt, resume_tid=resume_tid, images=images,
            write=write, auto_compact_limit=ac_limit,
        )
        expected_tid = resume_tid
        state["last_error"] = ""
        try:
            (final_message, clean_extraction, stdout_text, stderr_text,
             returncode, hung_reason, timed_out) = await _exec_codex_once(
                cmd, output_file, state, _emit, ctx, model,
                extra_env=extra_env)
        except asyncio.CancelledError:
            _journal({"run": run_tag, "phase": "end", "ts": time.time(),
                      "status": "cancelled"})
            with contextlib.suppress(Exception):
                _emit("■ run cancelled by caller (resume later: codex_resume_run)")
            for _fh in (live_fh, stream_fh):
                if _fh is not None:
                    with contextlib.suppress(Exception):
                        _fh.close()
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
        if (
            failed
            and not write
            and attempt < MAX_TRANSIENT_RETRIES
            and _is_transient_error(
                f"{stderr_text}\n{state['last_error']}\n{stdout_text[-2000:]}"
            )
        ):
            attempt += 1
            resume_tid = state.get("thread_id") or None
            if resume_tid:
                _emit(
                    f"⟲ transient failure — resuming thread {resume_tid} "
                    f"(attempt {attempt + 1}/{MAX_TRANSIENT_RETRIES + 1})"
                )
            else:
                _emit(
                    f"⟲ transient failure before the session started — "
                    f"retrying fresh (attempt {attempt + 1}/{MAX_TRANSIENT_RETRIES + 1})"
                )
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
        "error": (state["last_error"] or stderr_text)[:500] if status != "ok" else "",
        "result_file": result_file,
    })

    # Every write-run outcome — success, timeout, hang, error — reports what
    # changed on disk: a timed-out run may still have written files, and the
    # caller's next step is reviewing exactly that.
    write_report = (
        _write_changes_report(write_before, write_head, _get_cwd())
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

    if returncode != 0 and not final_message:
        detail = state["last_error"] or stderr_text
        retry_note = (
            f" after {attempt + 1} attempts" if attempt else ""
        )
        return (
            f"[Codex error (exit {returncode}){retry_note}]\n{detail}{log_note}\n"
            f"[recoverable: call codex_resume_run to continue this run "
            f"(run id: {run_tag})]{write_report}"
        )

    # status:ok is EARNED by exit 0 — a non-zero run that still produced a
    # final_message reaches this branch, and stamping it ok would let the
    # push gate accept a failed review (probed in review round 3).
    header = (
        f"[Codex model: {model} | reasoning: {reasoning}"
        f"{_answer_sig(tool_name, 'ok' if returncode == 0 else 'error')}]"
    )
    result = f"{header}\n\n{final_message}"
    if attempt and returncode == 0:
        result += (
            f"\n\n[note: recovered automatically after a transient failure — "
            f"{attempt + 1} attempts, context preserved via codex session resume]"
        )
    if state["last_error"] and returncode != 0:
        result += f"\n\n[run reported an error: {state['last_error']}]"
    result += write_report
    result += log_note

    # Only surface the noisy session stream when the clean extraction path
    # failed AND the process exited non-zero — i.e. we need the diagnostic
    # for debugging. A successful clean extraction returns only the header
    # and the final message; nothing else.
    if not clean_extraction and returncode != 0 and stderr_text:
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
        stderr_lines = [
            line for line in stderr_text.splitlines()
            if line.strip() and not any(pat in line for pat in noise_patterns)
        ]
        if stderr_lines:
            result += "\n\n[stderr]\n" + "\n".join(stderr_lines)

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
    """
    if not task.strip():
        return "[abraham refused: empty task — state what to implement.]"

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
            tool_name="abraham",
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
            tool_name="abraham",
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
    cwd = _get_cwd()
    all_runs = _journal_runs()
    runs = {k: v for k, v in all_runs.items() if v.get("cwd") == cwd}

    if run == "list":
        if not runs:
            return "[No recorded codex runs for this workspace.]"
        lines = ["Recent codex runs in this workspace (oldest → newest):"]
        for rec in list(runs.values())[-10:]:
            lines.append(
                f"  • {rec.get('run', '?')}: {rec.get('status') or 'RUNNING/INTERRUPTED'}"
                f" — {str(rec.get('prompt') or '')[:80]!r}"
                + (f" [thread {rec.get('thread_id')}]" if rec.get("thread_id") else "")
            )
        return "\n".join(lines)

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

    if use_write:
        got_lock, holder = _acquire_write_lock(cwd, f"resume:{run}")
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
        )
    finally:
        if use_write:
            _release_write_lock(cwd)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
