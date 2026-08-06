#!/usr/bin/env python3
"""
Antigravity MCP Server — backed by the Antigravity CLI (`agy`).

Google retired the Gemini CLI OAuth path for individual / Google One users on
2026-06-18 and replaced it with the Antigravity CLI (`agy`):
https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/

This server now drives `agy` instead of the deprecated `gemini` CLI. The MCP
tool surface has been renamed to the ``antigravity_*`` tools (formerly
``gemini_*``); agent-teams / antigravity-planner / antigravity-research
integrations use the new names.

How it works:
- Headless one-shot queries via ``agy -p`` (clean text on stdout, no JSON).
- Model auto-discovery via ``agy models`` (display names like
  "Gemini 3.1 Pro (High)"; the thinking depth is encoded in the name).
- Default reasoning model is the deepest-thinking Pro tier available
  ("Gemini 3.1 Pro (High)" today; the picker auto-upgrades if Google ships a
  deeper tier), with automatic fallback to Flash on capacity/quota errors.
- Anti-zombie subprocess handling: ``stdin=DEVNULL`` (so the spawned agy never
  consumes the MCP JSON-RPC stream), kill-on-cancel, and a total wall-clock
  timeout (agy buffers output until the final answer, so a "first byte" probe
  would be wrong here).
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Safety-net truncation. When multiple MCP tools run in the same agent turn
# (e.g. Codex + Antigravity in agent-teams), their combined output can overflow
# context. Cap each tool's output to leave room for others.
MAX_OUTPUT_CHARS = 300_000


def _resolve_agy() -> str:
    """Absolute path to the agy binary so the MCP server's PATH is irrelevant."""
    candidate = Path.home() / ".local" / "bin" / "agy"
    if candidate.exists():
        return str(candidate)
    # Fall back to a PATH lookup (create_subprocess_exec uses the parent PATH,
    # not env=, to locate a bare command name).
    return shutil.which("agy") or "agy"


AGY_BIN = _resolve_agy()

# Per-call timeout handed to ``agy --print-timeout``. Generous so long reviews /
# brainstorms and agentic runs (agy buffers until the final answer) are not cut
# off mid-reason.
AGY_PRINT_TIMEOUT_SECONDS = 1200  # 20 minutes
# Overall wall-clock guard for the wrapper — above the agy timeout so a wedged
# process is reaped instead of hanging the MCP call forever.
AGY_OVERALL_TIMEOUT_SECONDS = AGY_PRINT_TIMEOUT_SECONDS + 120

# ---------------------------------------------------------------------------
# Model auto-discovery (agy display names)
# ---------------------------------------------------------------------------
# agy reports models as e.g. "Gemini 3.1 Pro (High)", "Gemini 3.5 Flash (Low)".
# The parenthesised token is the THINKING DEPTH — deeper = better reasoning.
_MODEL_RE = re.compile(
    r"Gemini\s+(\d+(?:\.\d+)?)\s+(Pro|Flash)(\s+Lite)?\s*\(([^)]+)\)",
    re.IGNORECASE,
)
# Rank thinking depth. "high" is the deepest tier agy exposes today for 3.1 Pro;
# the deeper-tier synonyms are listed above it so the picker auto-upgrades to the
# deepest available if Google ships one (e.g. XHigh / Max / Ultra).
_LEVEL_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "extra high": 4,
    "very high": 4,
    "deep": 4,
    "max": 5,
    "ultra": 6,
}


def _parse_model(name: str) -> tuple[tuple[int, ...], str, int] | None:
    """Return ``(version_tuple, tier, level_rank)`` for a Gemini agy name, else None.

    Version is a tuple of ints so 3.10 sorts above 3.1 (a float collapses them).
    """
    m = _MODEL_RE.search(name)
    if not m:
        return None
    try:
        version = tuple(int(p) for p in m.group(1).split("."))
    except ValueError:
        return None
    tier = "flash-lite" if m.group(3) else m.group(2).lower()
    level = _LEVEL_RANK.get(m.group(4).strip().lower(), 0)
    return (version, tier, level)


async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL a subprocess and reap it so it never lingers as a zombie/orphan."""
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=5)


class ModelRegistry:
    """Discovers and caches available Gemini models exposed by ``agy``."""

    CACHE_TTL = 3600  # Re-check every hour

    def __init__(self) -> None:
        self._models: list[str] = []
        self._last_fetch: float = 0.0
        # Deepest-thinking Pro tier = strongest reasoning. "High" is the deepest
        # agy exposes for 3.1 Pro today; the picker upgrades automatically.
        self._default_pro: str = "Gemini 3.1 Pro (High)"
        # Capacity/quota fallback — Flash has higher availability than Pro.
        self._fallback_pro: str = "Gemini 3.5 Flash (High)"
        # Lightweight default ('flash'/'fast' alias + utility calls).
        self._default_flash: str = "Gemini 3.5 Flash (High)"

    async def _fetch_models(self) -> list[str]:
        """Run ``agy models`` and return the raw display-name lines.

        ``stdin=DEVNULL`` is CRITICAL — without it the spawned process inherits
        the MCP server's JSON-RPC stdin and corrupts the protocol stream.
        """
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                AGY_BIN, "models",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.CancelledError:
            if proc is not None:
                await _kill_and_reap(proc)
            raise
        except (OSError, asyncio.TimeoutError):
            if proc is not None:
                await _kill_and_reap(proc)
            return []
        models: list[str] = []
        for raw in stdout.decode("utf-8", "replace").splitlines():
            line = raw.strip().strip("-•* ").strip("`")
            # Real model lines look like "Name (Level)"; skip blanks/errors/help.
            if line and "(" in line and ")" in line and "sign in" not in line.lower():
                models.append(line)
        return models

    async def refresh(self, force: bool = False) -> None:
        """Refresh the model list if stale or forced."""
        if not force and (time.time() - self._last_fetch) < self.CACHE_TTL and self._models:
            return
        models = await self._fetch_models()
        if models:
            self._models = models
            self._last_fetch = time.time()
            self._update_defaults()

    def _update_defaults(self) -> None:
        """Pick the deepest-thinking Pro and Flash from the discovered list."""
        best_pro = self._pick_best("pro")
        best_flash = self._pick_best("flash")
        if best_pro:
            self._default_pro = best_pro
        if best_flash:
            self._default_flash = best_flash
            self._fallback_pro = best_flash

    def _pick_best(self, tier: str) -> str | None:
        """Highest version, then deepest thinking level, for a given tier."""
        candidates: list[tuple[tuple[int, ...], int, str]] = []
        for name in self._models:
            parsed = _parse_model(name)
            if not parsed:
                continue
            version, model_tier, level = parsed
            if model_tier == tier:
                candidates.append((version, level, name))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    @property
    def default_pro(self) -> str:
        return self._default_pro

    @property
    def fallback_pro(self) -> str:
        """Flash fallback used when ``default_pro`` hits capacity/quota limits."""
        return self._fallback_pro

    @property
    def default_flash(self) -> str:
        return self._default_flash

    @property
    def available_models(self) -> list[str]:
        return self._models if self._models else [self._default_pro, self._default_flash]

    def resolve_alias(self, model: str) -> str:
        """Resolve friendly aliases ('pro'/'latest'/'flash'/'fast') to agy names."""
        aliases = {
            "pro": self._default_pro,
            "latest": self._default_pro,
            "flash": self._default_flash,
            "fast": self._default_flash,
        }
        return aliases.get(model.lower().strip(), model)


# ---------------------------------------------------------------------------
# Antigravity (agy) client
# ---------------------------------------------------------------------------

class AntigravityCLIClient:
    """Wraps the Antigravity CLI (`agy`) to answer prompts as Gemini models."""

    def __init__(self) -> None:
        self.secrets_path = Path.home() / ".claude" / "secrets.json"
        self.models = ModelRegistry()

    # -- environment ------------------------------------------------------

    def _load_secrets(self) -> dict:
        """Load shared secrets from the secrets file (best-effort)."""
        try:
            if self.secrets_path.exists():
                with open(self.secrets_path) as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
        return {}

    def _build_environment(self) -> dict:
        """Build the subprocess environment (PATH + shared secrets)."""
        env = os.environ.copy()
        # Ensure ~/.local/bin is on PATH so agy (and its self-update) resolve.
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", "").split(os.pathsep):
            env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
        for key, value in self._load_secrets().items():
            if not key.startswith("_"):
                env[key] = str(value)
        return env

    # -- error / output helpers ------------------------------------------

    @staticmethod
    def _check_auth_error(text: str) -> bool:
        """True if the output looks like an agy authentication failure."""
        t = text.lower()
        phrases = (
            "please sign in",
            "sign in to",
            "authentication required",
            "not authorized",
            "unauthorized",
            "login required",
            "invalid credentials",
            "token expired",
        )
        if any(s in t for s in phrases):
            return True
        # HTTP codes only as standalone tokens (avoid matching e.g. "4015").
        return bool(re.search(r"\b(401|403)\b", text))

    @staticmethod
    def _is_capacity_error(text: str) -> bool:
        """Detect capacity / quota / rate-limit errors in agy output."""
        if not text:
            return False
        if re.search(r"\b429\b", text):
            return True
        t = text.lower()
        signals = (
            "too many requests",
            "rate limit",
            "rate-limit",
            "ratelimit",
            "resource exhausted",
            "resource_exhausted",
            "resource has been exhausted",
            "quota exceeded",
            "quota exhausted",
            "quota limit",
            "exceeded your current quota",
            "no capacity",
            "out of capacity",
        )
        return any(s in t for s in signals)

    @staticmethod
    def _clean_output(text: str) -> str:
        """agy -p emits clean text; strip any stray update/log noise."""
        kept: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s:
                low = s.lower()
                if low.startswith(("an update is available", "update available", "downloading update")):
                    continue
                if re.match(r"^[IWEF]\d{4}\s", s):  # glog-style installer/setup lines
                    continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _build_prompt(prompt: str, system_instruction: str | None) -> str:
        if system_instruction:
            return f"[System: {system_instruction}]\n\n{prompt}"
        return prompt

    def _build_query_cmd(self, full_prompt: str, model: str) -> list[str]:
        """Assemble the agy argv for one non-interactive query."""
        return [
            AGY_BIN,
            "-p", full_prompt,
            "--model", model,
            "--print-timeout", f"{AGY_PRINT_TIMEOUT_SECONDS}s",
            "--dangerously-skip-permissions",
        ]

    # -- subprocess execution --------------------------------------------

    async def _execute_cli(
        self,
        cmd: list[str],
        env: dict,
    ) -> tuple[str, str, int, str | None]:
        """Run an agy command to completion under a total wall-clock timeout.

        Returns ``(stdout, stderr, returncode, hung_reason)`` where
        ``hung_reason`` is non-None if the overall timeout tripped.

        - ``stdin=DEVNULL`` prevents the subprocess from consuming the MCP
          JSON-RPC stdin stream.
        - agy buffers output until the final answer, so we wait for completion
          (no "first byte" probe) and bound the call with an overall timeout.
        - On caller ``CancelledError`` the process is SIGKILLed so it does not
          become a zombie.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            return ("", f"agy CLI not found at {AGY_BIN}: {e}", 127, None)

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=AGY_OVERALL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            await _kill_and_reap(process)
            reason = f"no completion within {AGY_OVERALL_TIMEOUT_SECONDS}s"
            return ("", reason, process.returncode or -1, reason)
        except asyncio.CancelledError:
            await _kill_and_reap(process)
            raise

        stdout_text = stdout_b.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_b.decode("utf-8", errors="replace").strip()
        return (stdout_text, stderr_text, process.returncode or 0, None)

    # -- auth -------------------------------------------------------------

    async def check_auth(self) -> tuple[bool, str]:
        """Lightweight auth probe via ``agy models`` (no model spend)."""
        env = self._build_environment()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                AGY_BIN, "models",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.CancelledError:
            if proc is not None:
                await _kill_and_reap(proc)
            raise
        except (OSError, asyncio.TimeoutError) as e:
            if proc is not None:
                await _kill_and_reap(proc)
            return (False, f"could not run agy: {e}")
        out = out_b.decode("utf-8", "replace")
        err = err_b.decode("utf-8", "replace")
        if self._check_auth_error(out + err) or "sign in" in (out + err).lower():
            return (False, "not signed in — run `agy` in a terminal to log in")
        if any(_parse_model(line) for line in out.splitlines()):
            return (True, "signed in")
        return (False, (err or out).strip()[:200] or "unknown auth state")

    # -- queries ----------------------------------------------------------

    async def query(
        self,
        prompt: str,
        model: str | None = None,
        system_instruction: str | None = None,
    ) -> str:
        """Query Antigravity via agy, with automatic Flash fallback on quota errors.

        1. First attempt uses the deepest-thinking Pro model
           ("Gemini 3.1 Pro (High)" by default).
        2. On a capacity / quota signal (and only when the caller did not pin a
           model), fall back to Flash and prefix the answer with a notice.
        3. Auth failures raise with instructions (agy needs an interactive
           browser login; there is no headless re-auth).
        """
        # Refresh the model list (TTL-cached: a real `agy models` fetch happens at
        # most once/hour) so the deepest-thinking Pro tier auto-upgrades if Google
        # ships a newer/deeper one. Failures keep the existing defaults.
        await self.models.refresh()

        if model is None:
            primary_model = self.models.default_pro
            user_specified = False
        else:
            primary_model = self.models.resolve_alias(model)
            user_specified = True

        full_prompt = self._build_prompt(prompt, system_instruction)
        env = self._build_environment()

        async def _attempt(target: str) -> tuple[str, str, int, str | None]:
            return await self._execute_cli(self._build_query_cmd(full_prompt, target), env)

        stdout_text, stderr_text, returncode, hung = await _attempt(primary_model)
        combined = f"{stdout_text}\n{stderr_text}"

        if hung is not None:
            raise Exception(
                f"agy timed out for model '{primary_model}': {hung}. "
                f"Partial stderr: {stderr_text[:500] or '(none)'}"
            )

        if returncode != 0 and self._check_auth_error(combined):
            raise Exception(
                "agy authentication required/expired. Run `agy` in a terminal, "
                "complete the Google sign-in, then retry. "
                f"Detail: {(stderr_text or stdout_text)[:300] or '(none)'}"
            )

        if returncode == 0:
            cleaned = self._clean_output(stdout_text)
            if cleaned:
                return cleaned
            # exit 0 but only noise/empty — surface clearly, never return "".
            raise Exception(
                f"agy returned exit 0 but no usable output for '{primary_model}'. "
                "It may have printed only status noise — please retry."
            )

        # Capacity/quota fallback: Pro -> Flash (only if caller didn't pin one).
        fallback_model = self.models.fallback_pro
        should_fallback = (
            not user_specified
            and primary_model != fallback_model
            and self._is_capacity_error(combined)
        )
        if should_fallback:
            stdout_text, stderr_text, returncode, hung = await _attempt(fallback_model)
            if hung is None and returncode == 0:
                cleaned = self._clean_output(stdout_text)
                if cleaned:
                    return (
                        f"[Fallback: '{primary_model}' hit a capacity/quota limit, "
                        f"answered by '{fallback_model}']\n\n{cleaned}"
                    )
            combined = f"{stdout_text}\n{stderr_text}"

        # Error path — auth first (clear remediation), then capacity, then generic.
        if self._check_auth_error(combined):
            raise Exception(
                "agy authentication required/expired. Run `agy` in a terminal, "
                "complete the Google sign-in, then retry. "
                f"Detail: {(stderr_text or stdout_text)[:300] or '(none)'}"
            )
        detail = stderr_text or stdout_text or "(no output)"
        hint = ""
        if self._is_capacity_error(combined):
            hint = " (capacity/quota limit — consumer Antigravity quota may be exhausted)"
        raise Exception(f"agy error (exit {returncode}){hint}: {detail[:800]}")

    async def query_with_tools(
        self,
        prompt: str,
        model: str | None = None,
        system_instruction: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Query agy in agentic mode.

        agy runs its own built-in tools (auto-approved via
        ``--dangerously-skip-permissions``); it has no per-call tool allowlist
        flag, so ``allowed_tools`` is surfaced to the model as guidance rather
        than enforced.
        """
        note = ""
        if allowed_tools:
            note = "You may use these tools if helpful: " + ", ".join(allowed_tools) + "."
        if system_instruction and note:
            merged: str | None = f"{system_instruction}\n{note}"
        else:
            merged = system_instruction or note or None
        return await self.query(prompt, model=model, system_instruction=merged)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("antigravity-oauth")
client: AntigravityCLIClient | None = None


def get_client() -> AntigravityCLIClient:
    global client
    if client is None:
        client = AntigravityCLIClient()
    return client


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
# Mirrors the same block in the codex-oracle plugin. Both advisors are dispatched
# in parallel precisely so their opinions are INDEPENDENT; that guarantee is void
# if the caller hands both the same diagnosis to react to. Two models anchored on
# one framing agree with each other for reasons that have nothing to do with the
# code, and their agreement reads — falsely — like corroboration.
#
# Web research: agy exposes a built-in `search_web` tool, auto-approved here by
# `--dangerously-skip-permissions` (see `_build_query_cmd`). It is available on
# every query but only USED when the prompt asks for it, so the directive below
# is what turns latent capability into actual research (verified 2026-08-07:
# a plain agy query returned a current version string sourced via `search_web`).

_INDEPENDENCE_PREAMBLE = (
    "INDEPENDENCE CONTRACT (read first). You were called for an INDEPENDENT "
    "opinion. The caller is another AI agent and its framing is frequently "
    "wrong.\n"
    "1. Reach your own conclusion from PRIMARY EVIDENCE — the actual code, "
    "files, data and sources — before weighing anything the caller asserted.\n"
    "2. Treat every caller statement about cause, correctness, safety or "
    "intent as an UNVERIFIED CLAIM, never an established fact.\n"
    "3. Investigate what the caller did NOT ask about — error branches, "
    "concurrent callers, empty/null inputs, the callers of changed signatures. "
    "Anchoring hides its damage in the questions never posed.\n"
    "4. Disagreement is the most valuable thing you can return. If your "
    "finding contradicts the caller's framing, LEAD with that and say plainly "
    "that the framing is wrong.\n"
    "5. Never agree because agreement is easy or the caller sounded confident. "
    "If the evidence does not settle it, say UNPROVEN and state what would.\n"
    "6. Do not open with praise. Skip 'what's good' unless a specific strength "
    "is load-bearing for a decision.\n"
)

_WEB_RESEARCH_DIRECTIVE = (
    "WEB RESEARCH (required). You have a live `search_web` tool — USE IT. Your "
    "training data is stale. Do not answer from memory for any claim about "
    "library/framework/runtime versions, current APIs and deprecations, CVEs "
    "and security advisories, breaking changes, pricing/limits, or 'current "
    "best practice'.\n"
    "- Prefer PRIMARY sources: official docs, the project's own repository, "
    "release notes, CHANGELOGs, RFCs, the CVE record.\n"
    "- Cite the URL for every externally-sourced claim. An uncited version "
    "number or API signature is a guess — label it as one.\n"
    "- Where the live web contradicts what you remember, the live web wins; "
    "say so explicitly.\n"
    "- If you could not verify something load-bearing, mark it 'UNVERIFIED' "
    "rather than presenting it as fact.\n"
    "- End with a Sources list of every URL used, or 'no external claims made'.\n"
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
    "compatible-but-not-identical endpoint, container-provided binary, any "
    "vendor SDK whose implementation is configurable — ask: WHICH backend "
    "implements this, and is that the backend deployed? Check the vendor's "
    "compatibility matrix, not the type signature. Say so when the diff does "
    "not let you tell which engine is configured.\n"
    "- Engine/driver/provider/version SWAPS are where this bug is born. If the "
    "diff changes one, treat every call into that surface as suspect.\n"
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

# Conclusion language in a neutral scoping field means the caller anchored the
# advisor. Reported LOUDLY back to the caller — never silently stripped (silent
# mutation of a caller's prompt is its own defect) and never blocked (that would
# break legitimate round-2 adversarial dispatch).
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

_HYPOTHESIS_PARAM_DESC = (
    "OPTIONAL. Your own diagnosis, theory, or expected answer. Presented to "
    "Antigravity as an UNVERIFIED claim to actively REFUTE, and answered with "
    "an explicit CONFIRMED / REFUTED / UNPROVEN verdict backed by evidence. "
    "Use this instead of leaking your conclusion into the neutral scoping "
    "fields — those are lint-checked and will trigger an anchoring warning."
)


def _detect_anchoring(fields: dict[str, str]) -> list[str]:
    """Return human-readable anchoring hits in caller scoping fields.

    ``caller_hypothesis`` is exempt by design — that parameter exists
    precisely to carry a conclusion safely.
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
                break
    return hits


def _neutralizer(hits: list[str]) -> str:
    """Extra counter-anchoring text injected when the caller anchored."""
    if not hits:
        return ""
    return (
        "\n\n⚠️ CALLER ANCHORING DETECTED. The caller's scoping text contains "
        "conclusion language: " + "; ".join(hits) + ". Discount it — those are "
        "the caller's guesses, and callers routinely state a wrong diagnosis "
        "as fact. Derive your findings from the primary evidence alone, then "
        "state explicitly whether the caller's implied conclusion survives."
    )


def _hypothesis_block(hypothesis: str) -> str:
    """Render the caller's hypothesis as a claim under test, never as fact."""
    if not hypothesis:
        return ""
    return (
        "\n\n## Caller's hypothesis — UNVERIFIED CLAIM UNDER TEST\n"
        "The following is what the caller BELIEVES. It is not evidence, not "
        "background, and may be entirely wrong. Do not adopt its vocabulary or "
        "framing. Form your own findings FIRST, then adversarially test it — "
        "actively try to REFUTE it:\n"
        f"<caller_hypothesis>\n{hypothesis}\n</caller_hypothesis>\n"
        "Required in your output, on its own line:\n"
        "**Hypothesis verdict**: CONFIRMED / REFUTED / UNPROVEN — with the "
        "specific evidence (file:line, source URL, observed behaviour) that "
        "decided it. If CONFIRMED, name the evidence that would have refuted "
        "it and why it is absent. 'It sounds plausible' is not a verdict."
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
        "Antigravity was instructed to discount it and reason from primary "
        "evidence, but you biased the sample. If this answer AGREES with you, "
        "treat that agreement as WEAK evidence — it may be an echo of your own "
        "framing, not independent corroboration. Disagreement below is still "
        "strong evidence.\n"
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


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Antigravity tools (backed by Antigravity `agy`).

    CRITICAL: this handler must return quickly — Claude Code has a short MCP
    handshake timeout. No subprocess calls / network I/O here; use the
    hard-coded defaults from ``ModelRegistry.__init__``.
    """
    gemini = get_client()

    # Model selection is intentionally NOT exposed to MCP callers — the wrapper
    # always uses the deepest-thinking Pro model, with Flash fallback only on
    # capacity errors. Letting an LLM override would risk a weaker/cheaper pick.
    model_policy = (
        f"Always uses the deepest-thinking Gemini Pro model "
        f"({gemini.models.default_pro}) via the Antigravity CLI, with automatic "
        f"fallback to {gemini.models.fallback_pro} only on capacity/quota "
        f"errors. Model selection is NOT a parameter — it is a wrapper decision."
    )

    return [
        Tool(
            name="antigravity_query",
            description=(
                f"Ask Antigravity a question or send a prompt, with live web research. "
                f"Uses your authenticated Google account via the Antigravity CLI (agy). "
                f"DISPATCH BLIND — ask the question, do not pitch the answer; put any "
                f"theory of your own in `caller_hypothesis` so it gets attacked rather "
                f"than confirmed. {model_policy}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "The prompt or question to send to Antigravity. Phrase it "
                            "neutrally — lint-checked for conclusion language."
                        )
                    },
                    "caller_hypothesis": {
                        "type": "string",
                        "description": _HYPOTHESIS_PARAM_DESC
                    },
                    "system_instruction": {
                        "type": "string",
                        "description": "Optional system instruction to guide Antigravity's behavior"
                    }
                },
                "required": ["prompt"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="antigravity_with_tools",
            description=f"Query Antigravity in agentic mode with tools enabled. {model_policy}",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt or question to send to Antigravity"
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tool names to suggest (e.g., ['read_file', 'write_file'])"
                    },
                    "caller_hypothesis": {
                        "type": "string",
                        "description": _HYPOTHESIS_PARAM_DESC
                    },
                    "system_instruction": {
                        "type": "string",
                        "description": "Optional system instruction"
                    }
                },
                "required": ["prompt", "allowed_tools"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="antigravity_analyze_code",
            description=(
                "Analyze code for quality, security, performance, or bugs using "
                "Antigravity, with live web research on APIs/CVEs. DISPATCH BLIND — "
                "send the code and let it find the problems; put your own diagnosis "
                "in `caller_hypothesis` to have it refuted rather than echoed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to analyze"
                    },
                    "language": {
                        "type": "string",
                        "description": "Programming language (e.g., python, javascript, rust)"
                    },
                    "focus": {
                        "type": "string",
                        "enum": ["quality", "security", "performance", "bugs", "all"],
                        "default": "all",
                        "description": "What aspect to focus the analysis on"
                    },
                    "caller_hypothesis": {
                        "type": "string",
                        "description": _HYPOTHESIS_PARAM_DESC
                    }
                },
                "required": ["code"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="antigravity_brainstorm",
            description=(
                "Brainstorm ideas with Antigravity, with live web research for prior "
                "art. State the PROBLEM, not your preferred solution — a topic that "
                "names the answer returns variations on it instead of alternatives."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "The problem to brainstorm about. Phrase as the problem "
                            "and its constraints, not as the solution you favour."
                        )
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Hard constraints and goals (neutral scoping field — "
                            "lint-checked for conclusion language)"
                        )
                    },
                    "num_ideas": {
                        "type": "integer",
                        "default": 5,
                        "description": "Number of ideas to generate"
                    },
                    "caller_hypothesis": {
                        "type": "string",
                        "description": (
                            "OPTIONAL. The approach you are already leaning toward. "
                            "Held back from idea generation entirely: the ideas are "
                            "produced in one call that never sees this, then a "
                            "SECOND isolated call critiques your leaning against "
                            "that frozen set and returns a CONFIRMED/REFUTED/"
                            "UNPROVEN verdict. Supplying it therefore costs two "
                            "model calls instead of one."
                        )
                    }
                },
                "required": ["topic"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="antigravity_summarize",
            description="Summarize text content using Antigravity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to summarize"
                    },
                    "format": {
                        "type": "string",
                        "enum": ["paragraph", "bullets", "outline"],
                        "default": "bullets",
                        "description": "Output format for the summary"
                    },
                    "max_length": {
                        "type": "string",
                        "enum": ["brief", "medium", "detailed"],
                        "default": "medium",
                        "description": "Desired length of summary"
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="antigravity_explain",
            description="Get a clear explanation of a concept, code, or technical topic.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic or concept to explain"
                    },
                    "audience": {
                        "type": "string",
                        "enum": ["beginner", "intermediate", "expert"],
                        "default": "intermediate",
                        "description": "Target audience level"
                    },
                    "include_examples": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to include examples"
                    }
                },
                "required": ["topic"]
            }
        ),
        Tool(
            name="antigravity_review_pr",
            description=(
                "Review code changes like a pull request reviewer, with live web "
                "research on APIs/CVEs. DISPATCH BLIND — send the diff and let it "
                "find the problems. Do not write 'I fixed X by doing Y, does that "
                "look right'; that buys agreement, not review. Put your belief in "
                "`caller_hypothesis` for an explicit refute-first verdict."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "diff": {
                        "type": "string",
                        "description": "The code diff or changes to review"
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "FACTUAL background only — what the feature does, which "
                            "invariants hold, how to run it. Neutral scoping field, "
                            "lint-checked: do NOT put 'the bug is X' or 'this fix is "
                            "correct' here."
                        )
                    },
                    "strictness": {
                        "type": "string",
                        "enum": ["lenient", "balanced", "strict"],
                        "default": "strict",
                        "description": (
                            "Severity threshold for what gets reported (NOT a tone "
                            "dial — the review is blunt at every level). lenient = "
                            "CRITICAL/HIGH only; balanced = adds MEDIUM; strict = "
                            "everything including style. Default strict."
                        )
                    },
                    "caller_hypothesis": {
                        "type": "string",
                        "description": _HYPOTHESIS_PARAM_DESC
                    }
                },
                "required": ["diff"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="antigravity_generate_tests",
            description="Generate test cases for code using Antigravity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to generate tests for"
                    },
                    "framework": {
                        "type": "string",
                        "description": "Testing framework (e.g., pytest, jest, unittest)"
                    },
                    "coverage": {
                        "type": "string",
                        "enum": ["basic", "comprehensive", "edge_cases"],
                        "default": "comprehensive",
                        "description": "Level of test coverage"
                    }
                },
                "required": ["code"]
            }
        ),
        Tool(
            name="antigravity_refactor",
            description="Get refactoring suggestions for code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to refactor"
                    },
                    "goals": {
                        "type": "string",
                        "description": "Refactoring goals (e.g., 'improve readability', 'reduce complexity')"
                    },
                    "preserve_behavior": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to strictly preserve existing behavior"
                    }
                },
                "required": ["code"]
            }
        ),
        Tool(
            name="antigravity_sync_mcp",
            description="(Informational) MCP/extension sync status for the Antigravity backend.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Unused — retained for backward compatibility."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="antigravity_auth_status",
            description="Check Antigravity (agy) authentication status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "force_reauth": {
                        "type": "boolean",
                        "default": False,
                        "description": "Report how to re-authenticate (agy login is interactive)."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="antigravity_list_models",
            description="List all models available via Antigravity and the auto-selected defaults.",
            inputSchema={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "default": False,
                        "description": "Force refresh the model list from agy"
                    }
                },
                "required": []
            }
        ),
    ]


def _reject_model_selection(arguments: dict[str, Any], gemini: "AntigravityCLIClient") -> str | None:
    """Return an error string if the caller tried to set a 'model' argument.

    Model choice is a wrapper decision (deepest-thinking Pro, Flash fallback on
    capacity). An LLM agent calling this MCP must NOT override it.
    """
    if "model" in arguments and arguments["model"] is not None:
        return (
            "Error: 'model' parameter is not allowed. The wrapper auto-selects "
            f"the deepest-thinking Pro model ({gemini.models.default_pro}), with "
            f"fallback to {gemini.models.fallback_pro} only on capacity errors. "
            "Re-issue the call WITHOUT the 'model' parameter."
        )
    return None


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    gemini = get_client()
    # Set by the advisory tools when the caller's scoping fields carried a
    # conclusion; prepended to the final result so the warning is impossible
    # to miss and survives truncation.
    anchor_banner = ""
    # The hypothesis the caller sent, if any — used at the end to check that a
    # CONFIRMED/REFUTED/UNPROVEN verdict actually came back.
    hypothesis_sent = ""

    try:
        if name == "antigravity_query":
            rejection = _reject_model_selection(arguments, gemini)
            if rejection is not None:
                return [TextContent(type="text", text=rejection)]
            hits = _detect_anchoring({"prompt": arguments["prompt"]})
            anchor_banner = _anchor_warning_banner(hits)
            hypothesis_sent = arguments.get("caller_hypothesis", "")
            caller_system = arguments.get("system_instruction") or ""
            system = (
                _INDEPENDENCE_PREAMBLE
                + "\n"
                + _WEB_RESEARCH_DIRECTIVE
                + ("\n" + caller_system if caller_system else "")
            )
            result = await gemini.query(
                prompt=(
                    arguments["prompt"]
                    + _hypothesis_block(arguments.get("caller_hypothesis", ""))
                    + _neutralizer(hits)
                    + "\n\nIf your conclusion contradicts what the question "
                    "presupposed, say so explicitly rather than answering around it."
                ),
                model=None,  # always wrapper-decided
                system_instruction=system,
            )

        elif name == "antigravity_with_tools":
            rejection = _reject_model_selection(arguments, gemini)
            if rejection is not None:
                return [TextContent(type="text", text=rejection)]
            hits = _detect_anchoring({"prompt": arguments["prompt"]})
            anchor_banner = _anchor_warning_banner(hits)
            hypothesis_sent = arguments.get("caller_hypothesis", "")
            caller_system = arguments.get("system_instruction") or ""
            result = await gemini.query_with_tools(
                prompt=(
                    arguments["prompt"]
                    + _hypothesis_block(arguments.get("caller_hypothesis", ""))
                    + _neutralizer(hits)
                ),
                model=None,  # always wrapper-decided
                system_instruction=(
                    _INDEPENDENCE_PREAMBLE
                    + "\n"
                    + _WEB_RESEARCH_DIRECTIVE
                    + ("\n" + caller_system if caller_system else "")
                ),
                allowed_tools=arguments.get("allowed_tools", []),
            )

        elif name == "antigravity_analyze_code":
            focus = arguments.get("focus", "all")
            language = arguments.get("language", "unknown")
            hits = _detect_anchoring({"focus": focus})
            anchor_banner = _anchor_warning_banner(hits)
            hypothesis_sent = arguments.get("caller_hypothesis", "")
            system = (
                f"You are an expert code reviewer. Analyze the following {language} code. "
                f"Focus on: {focus if focus != 'all' else 'quality, security, performance, and potential bugs'}. "
                f"Provide specific, actionable feedback with line references where applicable.\n\n"
                + _INDEPENDENCE_PREAMBLE
                + "Read the code before you read any description of it, and "
                "trust the code where they conflict. Check the paths nobody "
                "asked about: error branches, concurrent callers, empty/null "
                "inputs, and every caller of a changed signature.\n\n"
                + _WEB_RESEARCH_DIRECTIVE
                + "For code specifically: verify library/API usage against "
                "current upstream docs rather than memory — signatures, "
                "deprecations and security guidance move. Check whether any "
                "dependency touched here has a known CVE.\n\n"
                + _CAPABILITY_HUNT
            )
            result = await gemini.query(
                prompt=(
                    f"```{language}\n{arguments['code']}\n```"
                    + _hypothesis_block(arguments.get("caller_hypothesis", ""))
                    + _neutralizer(hits)
                ),
                system_instruction=system,
            )

        elif name == "antigravity_brainstorm":
            context = arguments.get("context", "")
            num_ideas = arguments.get("num_ideas", 5)
            hits = _detect_anchoring({"topic": arguments["topic"], "context": context})
            anchor_banner = _anchor_warning_banner(hits)
            hypothesis = arguments.get("caller_hypothesis", "")
            system = (
                f"You are a creative brainstorming partner. Generate {num_ideas} diverse, innovative ideas. "
                f"For each idea, provide: 1. A clear title 2. Brief description (2-3 sentences) "
                f"3. Potential challenges 4. Why it could work\n\n"
                "INDEPENDENCE: the caller is another AI agent whose framing is "
                "often narrower than the problem. Generate ideas that span "
                "genuinely different approaches — not variations on one theme, "
                "and not variations on whatever the topic seems to favour. At "
                "least two ideas must challenge an assumption embedded in the "
                "way the problem was posed; name the assumption you are "
                "dropping.\n\n"
                + _WEB_RESEARCH_DIRECTIVE
                + "Search for PRIOR ART before generating: how have others "
                "solved this, and what did they report going wrong? An idea "
                "list with no reference to how this has been tried before is "
                "speculation, not brainstorming."
            )
            prompt = f"Problem: {arguments['topic']}"
            if context:
                prompt += f"\n\nConstraints and goals: {context}"
            prompt += _neutralizer(hits)

            # TWO ISOLATED CALLS when a hypothesis is supplied. An earlier
            # version put the hypothesis in the same request behind a "generate
            # your ideas first" instruction — but the model sees the whole
            # prompt at once, so the preference was in context the entire time
            # and the advertised independence was not real. Telling the caller
            # their leaning was excluded when it was not is the exact
            # accepted-but-ignored failure this feature exists to prevent.
            # Call 1 generates and FREEZES the ideas with no knowledge of the
            # leaning; call 2 critiques the leaning against that frozen set.
            result = await gemini.query(prompt=prompt, system_instruction=system)

            if hypothesis:
                critique_system = (
                    "You are evaluating one proposed approach against a set of "
                    "alternatives that were generated INDEPENDENTLY, before the "
                    "proposal was known. Be blunt. Do not soften the verdict "
                    "because the caller proposed it.\n\n"
                    + _WEB_RESEARCH_DIRECTIVE
                )
                critique = await gemini.query(
                    prompt=(
                        "## Alternatives (generated with no knowledge of the "
                        f"proposal below)\n{result}\n\n"
                        "## The proposal to evaluate\n"
                        f"<caller_hypothesis>\n{hypothesis}\n</caller_hypothesis>\n\n"
                        "Answer, concisely:\n"
                        "1. Does any alternative above beat this proposal? Name "
                        "it and say why, or state plainly that none does.\n"
                        "2. What does the proposal assume that the alternatives "
                        "do not?\n"
                        "3. **Hypothesis verdict**: CONFIRMED (it is the best "
                        "of these) / REFUTED (a named alternative is better) / "
                        "UNPROVEN (cannot tell without stated evidence) — with "
                        "the reasoning that decided it."
                    ),
                    system_instruction=critique_system,
                )
                result = (
                    f"{result}\n\n"
                    f"{'─' * 72}\n"
                    "## Critique of the caller's leaning\n"
                    "_(second, isolated call — the ideas above were generated "
                    "before this proposal was revealed to the model)_\n\n"
                    f"{critique}"
                )

        elif name == "antigravity_summarize":
            format_type = arguments.get("format", "bullets")
            length = arguments.get("max_length", "medium")
            length_guide = {
                "brief": "2-3 sentences or 3-5 bullet points",
                "medium": "1 paragraph or 5-10 bullet points",
                "detailed": "2-3 paragraphs or 10-15 bullet points",
            }
            system = (
                f"Summarize the following content. Format: {format_type}. "
                f"Length: {length_guide.get(length, length_guide['medium'])}. "
                f"Focus on the key points and main takeaways."
            )
            result = await gemini.query(
                prompt=arguments["content"],
                system_instruction=system,
            )

        elif name == "antigravity_explain":
            audience = arguments.get("audience", "intermediate")
            include_examples = arguments.get("include_examples", True)
            system = (
                f"Explain the following topic clearly for a {audience} audience. "
                f"{'Include practical examples.' if include_examples else 'Focus on concepts without examples.'} "
                f"Use clear language and structure your explanation well."
            )
            result = await gemini.query(
                prompt=arguments["topic"],
                system_instruction=system,
            )

        elif name == "antigravity_review_pr":
            context = arguments.get("context", "")
            # Default flipped balanced -> strict: this tool exists to find
            # defects, and the old default reported only some of them.
            strictness = arguments.get("strictness", "strict")
            # Severity THRESHOLD, not tone. The previous guide encoded
            # agreeableness ("be encouraging", "acknowledge good practices"),
            # which is the opposite of what an independent advisor is for.
            strictness_guide = {
                "lenient": "Report CRITICAL and HIGH severity findings only; stay blunt about those",
                "balanced": "Report CRITICAL, HIGH and MEDIUM findings; stay blunt about all of them",
                "strict": "Report everything including LOW/nit and style; stay blunt about all of them",
            }
            hits = _detect_anchoring({"context": context})
            anchor_banner = _anchor_warning_banner(hits)
            hypothesis_sent = arguments.get("caller_hypothesis", "")
            system = (
                f"You are a senior code reviewer conducting a pull request review. "
                f"Reporting threshold: {strictness_guide.get(strictness, strictness_guide['strict'])}.\n\n"
                + _INDEPENDENCE_PREAMBLE
                + "Review specifics: a diff that claims to fix something is a "
                "CLAIM — verify the bug existed, verify the fix closes it, and "
                "verify it did not open another. Read the code before any "
                "description of it and trust the code where they conflict.\n\n"
                + _WEB_RESEARCH_DIRECTIVE
                + "For code specifically: verify library/API usage against "
                "current upstream docs rather than memory, and check whether "
                "any dependency touched here has a known CVE.\n\n"
                + _CAPABILITY_HUNT
                + "\n"
                "Output sections, in order: 1. Verdict (Ship it / Needs "
                "changes / Do not ship) 2. Findings, highest severity first, "
                "one line each as [CRITICAL/HIGH/MEDIUM/LOW] file:line — issue "
                "→ fix 3. 'Where I disagree with the caller's framing' "
                "(mandatory — state it or write 'none — framing held up') "
                "4. Sources (URLs for every externally-sourced claim, or 'no "
                "external claims made'). Do not open with praise and do not "
                "include a 'What's Good' section."
            )
            prompt = f"Code changes:\n```\n{arguments['diff']}\n```"
            if context:
                prompt += f"\n\nBackground (factual context, not findings): {context}"
            prompt += _hypothesis_block(arguments.get("caller_hypothesis", ""))
            prompt += _neutralizer(hits)
            result = await gemini.query(prompt=prompt, system_instruction=system)

        elif name == "antigravity_generate_tests":
            framework = arguments.get("framework", "pytest")
            coverage = arguments.get("coverage", "comprehensive")
            coverage_guide = {
                "basic": "Cover the main happy path",
                "comprehensive": "Cover happy paths, error cases, and boundary conditions",
                "edge_cases": "Focus on edge cases, error handling, and unusual inputs",
            }
            system = (
                f"Generate test cases using {framework}. "
                f"Coverage level: {coverage_guide.get(coverage, coverage_guide['comprehensive'])}. "
                f"For each test: 1. Clear test name describing what's being tested "
                f"2. Arrange-Act-Assert structure 3. Relevant assertions "
                f"4. Brief comment explaining the test purpose"
            )
            result = await gemini.query(
                prompt=f"Generate tests for:\n```\n{arguments['code']}\n```",
                system_instruction=system,
            )

        elif name == "antigravity_refactor":
            goals = arguments.get("goals", "improve code quality")
            preserve = arguments.get("preserve_behavior", True)
            system = (
                f"You are a refactoring expert. Suggest improvements for the following code. "
                f"Goals: {goals}. "
                f"{'IMPORTANT: Preserve existing behavior exactly.' if preserve else 'Behavior changes are acceptable if they improve the code.'} "
                f"Provide: 1. Analysis of current code issues 2. Refactored code "
                f"3. Explanation of changes 4. Any risks or considerations"
            )
            result = await gemini.query(
                prompt=f"```\n{arguments['code']}\n```",
                system_instruction=system,
            )

        elif name == "antigravity_sync_mcp":
            # agy manages its own extensions via `agy plugin`; nothing to sync.
            result = (
                "No-op: the Antigravity MCP now runs on the Antigravity CLI (agy), "
                "which manages its own extensions via `agy plugin`. There is no "
                "Claude->Antigravity MCP settings file to sync."
            )

        elif name == "antigravity_auth_status":
            force = arguments.get("force_reauth", False)
            if force:
                result = (
                    "agy re-authentication is interactive: run `agy` in a terminal "
                    "and complete the Google sign-in (use an Incognito window with a "
                    "single account). There is no headless re-auth."
                )
            else:
                ok, detail = await gemini.check_auth()
                result = (
                    "Authentication is valid (Antigravity/agy)."
                    if ok else f"Not authenticated: {detail}"
                )

        elif name == "antigravity_list_models":
            force = arguments.get("refresh", False)
            await gemini.models.refresh(force=force)
            models = gemini.models.available_models
            lines = [
                f"Available models ({len(models)}) via Antigravity (agy):",
                f"  Default Pro (deepest thinking): {gemini.models.default_pro}",
                f"  Default Flash / fallback:       {gemini.models.default_flash}",
                "",
                "All models:",
            ]
            for m in models:
                marker = ""
                if m == gemini.models.default_pro:
                    marker = " ← default pro"
                elif m == gemini.models.default_flash:
                    marker = " ← default flash"
                lines.append(f"  • {m}{marker}")
            lines.append("")
            lines.append("Aliases: 'pro'/'latest' → deepest pro, 'flash'/'fast' → flash")
            result = "\n".join(lines)

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        # Truncate to avoid exceeding Claude Code's MCP result limit. The
        # anchoring banner is reserved OUT of the budget rather than added on
        # top of it: the warning must survive truncation (it is the thing that
        # tells the caller not to trust an agreeing answer), and the total must
        # still respect the cap.
        budget = (
            MAX_OUTPUT_CHARS
            - len(anchor_banner)
            - (_VERDICT_NOTICE_LEN if hypothesis_sent else 0)
            - _TRUNC_NOTICE_ALLOWANCE
        )
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

        # Checked against the POST-truncation text — the verdict must be in what
        # the caller actually receives, not in a tail that was cut off.
        verdict_notice = _verdict_missing_notice(hypothesis_sent, result)
        return [
            TextContent(type="text", text=anchor_banner + verdict_notice + result)
        ]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
