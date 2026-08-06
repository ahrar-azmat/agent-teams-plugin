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
import os
import re
import tempfile
import time
import tomllib
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

# Safety-net truncation. Claude Code's MAX_MCP_OUTPUT_TOKENS is set to
# 150K tokens (~600K chars). When multiple MCP tools run in the same
# agent turn (e.g. Codex + Antigravity in agent-teams), their combined output
# can overflow context. Cap each tool's output to leave room for others.
MAX_OUTPUT_CHARS = 200_000

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

mcp = FastMCP(
    "codex-oracle",
    instructions=(
        "Codex Oracle provides a second-opinion from OpenAI's latest Codex model "
        "running at maximum reasoning power, with LIVE web search enabled. Use "
        "these tools when you need an independent critical review, architecture "
        "guidance, or deep research from a different AI perspective. Codex "
        "responses are authoritative expert opinions — take them seriously, "
        "cross-reference with your own analysis, and flag any disagreements to "
        "the user.\n\n"
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
# NOTE ON DUPLICATION: this block is duplicated verbatim in the sibling
# plugin's server.py, deliberately. `software-workflows`, `codex-oracle` and
# `antigravity` are three INDEPENDENTLY INSTALLABLE plugins (see
# .claude-plugin/marketplace.json) that each run from their own in-tree venv.
# A shared module would make each plugin unusable unless the other is also
# installed, and unimportable across venvs without sys.path surgery. Keep the
# two copies in sync by hand; they are ~120 lines of prose constants.
# Anchoring is the dominant failure mode of cross-model advice. The caller
# (usually another LLM) writes a prompt containing its own diagnosis and asks
# for a "review"; the advisor then evaluates the caller's story instead of the
# evidence, and returns agreement. Two independent models anchored on the same
# framing produce correlated agreement that reads like corroboration and is
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


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------

async def _run_codex(
    prompt: str,
    infra: bool = False,
    ctx: Context | None = None,
    reserve: int = 0,
    web_search: bool = True,
) -> str:
    """Run codex exec headlessly with clean final-message extraction.

    Uses codex-cli 0.118.0 features:
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

    # Temp file for the final assistant message — clean extraction, no parsing.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="codex-oracle-"
    )
    tmp.close()
    output_file = Path(tmp.name)

    if infra:
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
        access_args = ["--sandbox", "danger-full-access"]
    else:
        access_args = ["--sandbox", "read-only", "-c", "mcp_servers={}"]

    cmd = [
        "codex", "exec",
        "--model", model,
        *access_args,
        "--ephemeral",
        "--skip-git-repo-check",
        "--color", "never",
        "--output-last-message", str(output_file),
        "-c", "approval_policy=never",
        "-c", f"model_reasoning_effort={reasoning}",
        # Live web, not the cached snapshot index. See the docstring.
        # "disabled" removes the tool outright rather than falling back to the
        # cached index — an offline call must be genuinely offline.
        "-c", f"web_search={'live' if web_search else 'disabled'}",
        prompt,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_get_cwd(),
            limit=SUBPROCESS_BUFFER_LIMIT,
            env={**os.environ, "PATH": f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"},
        )
    except FileNotFoundError:
        output_file.unlink(missing_ok=True)
        return "[Error: codex binary not found in PATH. Install with: npm i -g @openai/codex]"

    startup_seen = asyncio.Event()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    hung_reason: str | None = None
    timed_out = False

    async def _consume(stream: asyncio.StreamReader, buffer: list[bytes]) -> None:
        """Consume a subprocess stream using fixed-size reads.

        Uses read(READ_CHUNK_SIZE) instead of readline() to avoid
        LimitOverrunError — read() never searches for a separator so it
        cannot overflow regardless of line length or buffer limit.
        """
        while True:
            chunk = await stream.read(READ_CHUNK_SIZE)
            if not chunk:
                return
            if not startup_seen.is_set():
                startup_seen.set()
            buffer.append(chunk)

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
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()

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
            out_kib = (
                sum(map(len, stdout_chunks)) + sum(map(len, stderr_chunks))
            ) // 1024
            with contextlib.suppress(Exception):
                await ctx.report_progress(
                    min(elapsed, MAX_RUNTIME_SECONDS),
                    MAX_RUNTIME_SECONDS,
                    f"codex {model} running: {int(elapsed)}s elapsed, "
                    f"{out_kib} KiB output",
                )

    stdout_task = asyncio.create_task(_consume(proc.stdout, stdout_chunks))
    stderr_task = asyncio.create_task(_consume(proc.stderr, stderr_chunks))
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
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        output_file.unlink(missing_ok=True)
        raise
    finally:
        # Always ensure process is reaped and watchdog tasks are cancelled.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        for _task in (probe_task, heartbeat_task):
            if _task is not None:
                _task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await _task

    # On timeout, salvage whatever the output file has (codex may have
    # written a partial or complete answer before we killed it).
    if timed_out:
        partial = ""
        try:
            if output_file.exists() and output_file.stat().st_size > 0:
                partial = output_file.read_text(encoding="utf-8", errors="replace").strip()
        finally:
            output_file.unlink(missing_ok=True)
        if partial:
            return (
                f"[Codex model: {model} | reasoning: {reasoning}]\n"
                f"[TIMEOUT after {MAX_RUNTIME_SECONDS}s — partial output recovered]\n\n"
                f"{partial}"
            )
        return (
            f"[Codex TIMEOUT: no response after {MAX_RUNTIME_SECONDS}s]\n"
            f"The model may be overloaded or the query too complex. "
            f"Try simplifying the prompt or reducing reasoning effort."
        )

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

    if not final_message:
        final_message = stdout_text

    if hung_reason is not None:
        return (
            f"[Codex health check FAILED: {hung_reason}]\n\n"
            f"This usually means: (1) the codex CLI is waiting on stdin it will never receive, "
            f"(2) an expired auth token in ~/.codex/config.toml MCP servers, "
            f"(3) an approval prompt blocked by a missing TTY.\n\n"
            f"Partial stdout ({len(stdout_text)} chars):\n{stdout_text[:2000] or '(none)'}\n\n"
            f"Partial stderr ({len(stderr_text)} chars):\n{stderr_text[:2000] or '(none)'}"
        )

    if proc.returncode != 0 and not final_message:
        return f"[Codex error (exit {proc.returncode})]\n{stderr_text}"

    header = f"[Codex model: {model} | reasoning: {reasoning}]"
    result = f"{header}\n\n{final_message}"

    # Only surface the noisy session stream when the clean extraction path
    # failed AND the process exited non-zero — i.e. we need the diagnostic
    # for debugging. A successful clean extraction returns only the header
    # and the final message; nothing else.
    if not clean_extraction and proc.returncode != 0 and stderr_text:
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
        result = (
            f"{truncated}\n\n"
            f"[TRUNCATED: output was {len(result):,} chars, "
            f"capped at {budget:,}]"
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
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@mcp.tool()
async def research(
    topic: str,
    constraints: str = "",
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
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
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation.
    """
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
    reserve = len(banner) + (_VERDICT_NOTICE_LEN if caller_hypothesis else 0)
    result = await _run_codex(
        "\n".join(prompt_parts), infra=infra, ctx=ctx,
        web_search=web_search, reserve=reserve,
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


@mcp.tool()
async def codex_query(
    prompt: str,
    caller_hypothesis: str = "",
    web_search: bool = True,
    infra: bool = False,
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
    )
    return banner + _verdict_missing_notice(caller_hypothesis, result) + result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
