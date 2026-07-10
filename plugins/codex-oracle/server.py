"""
Codex Oracle MCP Server
========================
Exposes OpenAI Codex CLI as MCP tools for Claude Code.
Codex runs in headless mode (codex exec) with maximum reasoning power.

Auto-detects model and reasoning effort from ~/.codex/config.toml.
All tools use deep analysis with extended timeouts.

Roles:
- Senior Architect: architecture & design review
- Code Reviewer: critical code analysis
- Research Analyst: deep research with web access
- General Oracle: freeform queries to Codex

All outputs are treated as authoritative second opinions that should be
critically verified — not blindly followed.
"""

import asyncio
import contextlib
import os
import tempfile
import tomllib
from pathlib import Path

from mcp.server.fastmcp import FastMCP

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
        "running at maximum reasoning power. Use these tools when you need an "
        "independent critical review, architecture guidance, or deep research "
        "from a different AI perspective. Codex responses are authoritative "
        "expert opinions — take them seriously, cross-reference with your own "
        "analysis, and flag any disagreements to the user."
    ),
)

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
    """Auto-detect reasoning effort from Codex config."""
    return _read_codex_config().get("model_reasoning_effort", "max")


def _get_cwd() -> str:
    """Get the working directory — prefer CLAUDE_CWD if set."""
    return os.environ.get("CLAUDE_CWD", os.getcwd())


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------

async def _run_codex(prompt: str, infra: bool = False) -> str:
    """Run codex exec headlessly with clean final-message extraction.

    Uses codex-cli 0.118.0 features:
    - ``--output-last-message FILE`` — clean final-message extraction.
    - ``-c approval_policy=never`` — prevents blocking on approval prompts.
    - ``-c mcp_servers={}`` — disables codex's built-in MCP servers
      (default mode only; ``infra=True`` keeps them and lifts the sandbox).
    - ``--color never`` — strips ANSI sequences.
    - ``stdin=DEVNULL`` — prevents reading the parent's MCP JSON-RPC stdin.

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

    stdout_task = asyncio.create_task(_consume(proc.stdout, stdout_chunks))
    stderr_task = asyncio.create_task(_consume(proc.stderr, stderr_chunks))
    probe_task = asyncio.create_task(_startup_probe())

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
        raise
    finally:
        # Always ensure process is reaped and probe is cancelled.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        probe_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await probe_task

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

    # Truncate to avoid exceeding Claude Code's MCP result limit.
    if len(result) > MAX_OUTPUT_CHARS:
        truncated = result[:MAX_OUTPUT_CHARS]
        last_nl = truncated.rfind("\n")
        if last_nl > MAX_OUTPUT_CHARS * 0.8:
            truncated = truncated[:last_nl]
        result = (
            f"{truncated}\n\n"
            f"[TRUNCATED: output was {len(result):,} chars, "
            f"capped at {MAX_OUTPUT_CHARS:,}]"
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
    infra: bool = False,
) -> str:
    """
    Senior architect review of a design, approach, or implementation.
    Runs Codex at maximum reasoning depth. Use for architecture decisions,
    system design, API contracts, data modeling, or structural patterns.

    Args:
        description: What to review — the architecture, approach, or decision
        files: Comma-separated file paths for Codex to examine
        concerns: Specific concerns or trade-offs to evaluate
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation. Slower startup;
            use only when live state matters to the review.
    """
    prompt_parts = [
        "You are a principal software architect with 20+ years of experience.",
        "Perform a deep, critical architecture review. Think step by step.",
        "Be direct and opinionated. Do not hedge or be diplomatic.",
        "Flag every risk, anti-pattern, and scalability concern you find.",
        "Suggest concrete alternatives with trade-off analysis.",
        "",
        f"## Review Request\n{description}",
    ]
    if files:
        prompt_parts.append(f"\n## Files to examine (read these files)\n{files}")
    if concerns:
        prompt_parts.append(f"\n## Specific concerns\n{concerns}")

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Get straight to findings.\n\n"
        "1. **Verdict**: APPROVE / CONCERNS / REJECT\n"
        "2. **Executive summary**: 2-3 sentences\n"
        "3. **Critical findings**: Severity-ranked list\n"
        "4. **Recommendations**: Concrete, actionable changes\n"
        "5. **Risks**: What happens if current approach ships as-is\n"
        "6. **Alternative approaches**: If CONCERNS/REJECT, suggest alternatives\n\n"
        "Skip sections with zero findings. Do not repeat yourself."
    )

    return await _run_codex("\n".join(prompt_parts), infra=infra)


@mcp.tool()
async def code_review(
    code_or_diff: str,
    context: str = "",
    focus: str = "",
    infra: bool = False,
) -> str:
    """
    Deep critical code review from Codex at maximum reasoning power.
    Use for independent review of code changes, diffs, or implementations.
    Codex will read files in the working directory if given paths.

    Args:
        code_or_diff: Code snippet, diff, or file paths to review
        context: Background context about the codebase or feature
        focus: Areas to focus on (security, performance, correctness, etc.)
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation. Slower startup;
            use only when live state matters to the review.
    """
    prompt_parts = [
        "You are an elite code reviewer who has prevented production outages.",
        "Perform a deep, line-by-line review. Think step by step.",
        "Look for: bugs, security vulnerabilities (OWASP Top 10),",
        "performance issues (N+1 queries, memory leaks), race conditions,",
        "edge cases (empty, null, overflow), error handling gaps,",
        "and violations of clean code principles.",
        "Do NOT compliment the code. Find real problems.",
        "",
        f"## Code to review\n```\n{code_or_diff}\n```",
    ]
    if context:
        prompt_parts.append(f"\n## Context\n{context}")
    if focus:
        prompt_parts.append(f"\n## Focus areas\n{focus}")

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Get straight to findings.\n\n"
        "**Verdict**: Ship it / Needs changes / Do not ship\n"
        "**Findings** (highest severity first, one per line):\n"
        "  [CRITICAL/HIGH/MEDIUM/LOW] file:line — issue → fix\n"
        "  (include code snippets only for CRITICAL/HIGH fixes)\n\n"
        "Skip sections with zero findings. Do not repeat yourself."
    )

    return await _run_codex("\n".join(prompt_parts), infra=infra)


@mcp.tool()
async def research(
    topic: str,
    constraints: str = "",
    infra: bool = False,
) -> str:
    """
    Deep technical research using Codex's web access and full reasoning.
    Always runs at maximum depth. Use for up-to-date information,
    library comparisons, best practices, or technical investigation.

    Args:
        topic: What to research — be specific
        constraints: Specific versions, frameworks, date ranges, etc.
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation.
    """
    prompt_parts = [
        "You are a senior technical researcher with web access.",
        "Search the web extensively for current, accurate information.",
        "Think deeply. Cross-reference multiple sources.",
        "Cite URLs for every claim. Distinguish facts from opinions.",
        "Flag anything uncertain or conflicting.",
        "",
        f"## Research topic\n{topic}",
    ]
    if constraints:
        prompt_parts.append(f"\n## Constraints\n{constraints}")

    prompt_parts.append(
        "\n## Required output format (CONCISE — under 1500 words total)\n"
        "No preamble, no filler. Lead with the answer.\n\n"
        "**Recommendation**: Your pick + reasoning (HIGH/MEDIUM/LOW confidence)\n"
        "**Key findings**: Bulleted, one line each — with source URLs\n"
        "**Trade-offs**: Brief pros/cons per approach\n\n"
        "Do not repeat yourself."
    )

    return await _run_codex("\n".join(prompt_parts), infra=infra)


@mcp.tool()
async def codex_query(
    prompt: str,
    infra: bool = False,
) -> str:
    """
    Freeform deep query to Codex at maximum reasoning power.
    Use for anything — explanations, comparisons, debugging hypotheses,
    or any task where a second AI perspective is valuable.
    Codex can read files in the current working directory.

    Args:
        prompt: The question or task for Codex
        infra: Enable live-infrastructure access (SSH, live DB, Dokploy,
            logs, MCP tools) for read-only investigation.
    """
    preamble = (
        "Think deeply and step by step. Be thorough and precise. "
        "If you need to read files, do so. If you need to search the web, do so. "
        "Provide your analysis with evidence and reasoning. "
        "Keep your response concise — under 1500 words. No filler or preamble.\n\n"
    )
    return await _run_codex(preamble + prompt, infra=infra)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
