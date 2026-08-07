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
- Headless one-shot queries via ``agy -p --output-format stream-json``:
  incremental init/step_update/result events (measured on agy 1.1.11) feed a
  per-run live log — ``tail -f ~/.claude/logs/antigravity/latest.log`` — and
  10s MCP progress heartbeats carrying the current activity; the final answer
  is extracted from the terminal ``result`` event. Older agy builds that
  reject the flag are downgraded to plain-text mode automatically.
- Model auto-discovery via ``agy models``. Since agy 1.1.x the output is
  tab-separated ``slug\tDisplay Name`` lines (e.g.
  ``gemini-3.1-pro-high\tGemini 3.1 Pro (High)``); older builds printed the
  display name only. Both line shapes are parsed; ``--model`` is passed the
  SLUG (measured on agy 1.1.11: slug and display name are both accepted —
  slug is canonical because it is space/paren-free).
- Default reasoning model is the deepest-thinking Pro tier available
  (``gemini-3.1-pro-high`` today; the picker auto-upgrades if Google ships a
  deeper tier), with automatic fallback to Flash on capacity/quota errors,
  and a one-shot registry force-refresh + retry if agy rejects a cached
  model id (lineup changes under the 1h cache).
- Anti-zombie subprocess handling: ``stdin=DEVNULL`` (so the spawned agy never
  consumes the MCP JSON-RPC stream), kill-on-cancel, and a total wall-clock
  timeout (agy buffers output until the final answer, so a "first byte" probe
  would be wrong here).
"""

import asyncio
import contextlib
import itertools
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, TextIO

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
# Model auto-discovery (agy model list)
# ---------------------------------------------------------------------------
# agy >= 1.1.x lists models as "slug\tDisplay Name" (older builds: display name
# only). Ranking parses the DISPLAY name — "Gemini 3.1 Pro (High)" — where the
# parenthesised token is the THINKING DEPTH; the SLUG is what --model gets.
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


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------
# Every query streams agy's event feed (``--output-format stream-json``:
# init / step_update / result events, verified incremental on agy 1.1.11)
# to a per-run log so the operator can watch what Gemini is doing:
#
#     tail -f ~/.claude/logs/antigravity/latest.log
#
# The final answer is extracted from the terminal ``result`` event; if the
# stream yields none, the raw text is used as before. Observability must
# never break the run — every live-view failure degrades silently.

LIVE_LOG_DIR = Path.home() / ".claude" / "logs" / "antigravity"
LIVE_LOG_RETENTION_DAYS = 7
# Merged viewer feed: every run ALSO appends tagged lines to stream.log so
# `tail -F stream.log` shows ALL concurrent runs (across sessions/processes —
# O_APPEND interleaves at line granularity). Truncated at run-start past this
# cap; it duplicates the per-run files (the archive), so truncation loses
# nothing durable. 128 MiB ≈ months of heavy use at observed run sizes.
STREAM_LOG_MAX_BYTES = 128 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = float(
    os.environ.get("ANTIGRAVITY_PROGRESS_INTERVAL", "10")
)
_live_log_seq = itertools.count(1)


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

    Returns ``(path, run_fh, stream_fh, tag)``. The tag (``query3·8123``)
    prefixes this run's lines in ``stream.log`` so concurrent runs stay
    tellable-apart in the merged view.
    """
    seq = next(_live_log_seq)
    tag = f"{label}{seq}·{os.getpid()}"
    path: Path | None = None
    fh: TextIO | None = None
    stream_fh: TextIO | None = None
    try:
        LIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _prune_live_logs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = LIVE_LOG_DIR / f"{stamp}-p{os.getpid()}-{seq}-{label}.log"
        fh = path.open("w", encoding="utf-8")
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


def _step_text(step: dict[str, Any]) -> str:
    """Best-effort human text for an agy step_update payload."""
    for key in ("text", "content", "description", "tool_name", "title"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _process_agy_event(ev: dict[str, Any], state: dict[str, str]) -> str | None:
    """Digest one agy stream-json event (measured on agy 1.1.11).

    Shapes: ``{"event":"init","init":{"model":...}}``,
    ``{"event":"step_update","step_update":{step_index,state,step_type,...}}``
    (agent_response steps carry incremental text), and the terminal
    ``{"event":"result","result":{"status":"SUCCESS","response":...}}``.
    Unknown shapes degrade to a raw JSON snippet, never a crash.
    """
    kind = str(ev.get("event", ""))
    if kind == "init":
        init = ev.get("init") or {}
        state["activity"] = "session started"
        return f"session started (model: {init.get('model', '?')})"
    if kind == "step_update":
        step = ev.get("step_update") or {}
        stype = str(step.get("step_type", "?"))
        sstate = str(step.get("state", "?"))
        text = _step_text(step)
        if stype == "agent_response":
            if sstate == "ACTIVE":
                # Incremental text — track for the heartbeat, log on DONE only.
                if text:
                    state["activity"] = f"responding: …{text[-90:]}"
                return None
            if text:
                state["activity"] = "response complete"
                return "response:\n" + "\n".join(f"  {ln}" for ln in text.splitlines())
            return "response step done"
        if stype in ("user_input", "checkpoint", "unknown"):
            return None  # lifecycle plumbing, not worth a log line
        label = f"step {step.get('step_index', '?')} {stype} {sstate}"
        state["activity"] = f"{stype} {sstate.lower()}"
        return f"{label}: {text}" if text else label
    if kind == "result":
        res = ev.get("result") or {}
        status = str(res.get("status", "?"))
        response = res.get("response")
        if isinstance(response, str) and response.strip():
            state["final"] = response
        state["status"] = status
        if status != "SUCCESS":
            state["last_error"] = (
                str(res.get("error") or res.get("message") or response or "")[:500]
                or f"agy result status={status}"
            )
        state["activity"] = f"result: {status}"
        return f"result: status={status} ({len(response) if isinstance(response, str) else 0} chars)"
    if kind == "error":
        msg = str(ev.get("message") or json.dumps(ev)[:300])
        state["last_error"] = msg
        state["activity"] = "stream error"
        return f"STREAM ERROR: {msg}"
    return f"event: {json.dumps(ev)[:300]}"


async def _report_progress(progress: float, total: float, message: str) -> None:
    """Send an MCP progress notification for the current request, if any.

    Uses the low-level Server's request context; silently a no-op when the
    client sent no progressToken. Resets Claude Code's 30-min idle-abort
    timer exactly like codex-oracle's FastMCP heartbeat does.
    """
    with contextlib.suppress(Exception):
        rc = server.request_context
        token = getattr(rc.meta, "progressToken", None) if rc.meta else None
        if token is None:
            return
        await rc.session.send_progress_notification(
            progress_token=token,
            progress=progress,
            total=total,
            message=message,
        )


class ModelRegistry:
    """Discovers and caches available Gemini models exposed by ``agy``."""

    CACHE_TTL = 3600  # Re-check every hour

    def __init__(self) -> None:
        # (slug, display) pairs; for old-format agy output slug == display.
        self._models: list[tuple[str, str]] = []
        self._last_fetch: float = 0.0
        # Defaults are SLUGS (the value passed to --model). Measured against
        # agy 1.1.11 (`agy -p … --model <slug>` headless): both the slug and
        # the display name are accepted; slug is canonical. These are only
        # used until the first successful `agy models` refresh.
        # Deepest-thinking Pro tier = strongest reasoning. "high" is the
        # deepest agy exposes for 3.1 Pro today; the picker upgrades
        # automatically when a deeper tier appears in the live list.
        self._default_pro: str = "gemini-3.1-pro-high"
        # Capacity/quota fallback — Flash has higher availability than Pro.
        self._fallback_pro: str = "gemini-3.6-flash-high"
        # Lightweight default ('flash'/'fast' alias + utility calls).
        self._default_flash: str = "gemini-3.6-flash-high"

    async def _fetch_models(self) -> list[tuple[str, str]]:
        """Run ``agy models`` and return parsed ``(slug, display)`` pairs.

        agy >= 1.1.x emits ``slug\\tDisplay Name`` per line; older builds
        emitted the display name only (then slug == display — the CLI accepts
        either form as ``--model``).

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
        models: list[tuple[str, str]] = []
        for raw in stdout.decode("utf-8", "replace").splitlines():
            line = raw.strip().strip("-•* ").strip("`")
            if not line or "sign in" in line.lower():
                continue
            slug, _, display = line.partition("\t")
            slug = slug.strip()
            display = display.strip() or slug
            # Real model lines carry "(Level)"/"(Thinking)" in the display
            # part; this also drops noise like "Fetching available models...".
            if "(" in display and ")" in display:
                models.append((slug, display))
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
        """Highest version, then deepest thinking level, for a given tier.

        Ranks on the DISPLAY name (where the thinking depth lives) and
        returns the SLUG — the value ``--model`` actually takes.
        """
        candidates: list[tuple[tuple[int, ...], int, str]] = []
        for slug, display in self._models:
            parsed = _parse_model(display)
            if not parsed:
                continue
            version, model_tier, level = parsed
            if model_tier == tier:
                candidates.append((version, level, slug))
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
    def available_models(self) -> list[tuple[str, str]]:
        """``(slug, display)`` pairs from the last successful discovery."""
        if self._models:
            return self._models
        return [
            (self._default_pro, self._default_pro),
            (self._default_flash, self._default_flash),
        ]

    def resolve_alias(self, model: str) -> str:
        """Resolve friendly aliases ('pro'/'latest'/'flash'/'fast') to slugs.

        Anything else passes through untouched — agy accepts both slugs and
        display names (measured on 1.1.11), so a caller may pin either.
        """
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
        # stream-json live view: flips False (for the process lifetime) the
        # first time agy rejects --output-format, e.g. an older CLI build.
        self._stream_ok = True

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
    def _is_invalid_model_error(text: str) -> bool:
        """agy rejected the --model value (fast exit 1, before any spend).

        Fires when the lineup/naming changed underneath a cached model id —
        e.g. agy 1.1.x switching `agy models` to slug\\tdisplay lines, or a
        model being retired inside the registry's 1h cache window.
        """
        t = text.lower()
        signals = (
            "invalid model selection",
            "not recognized as a known model",
            "unknown model",
        )
        return any(s in t for s in signals)

    @staticmethod
    def _is_flag_error(text: str) -> bool:
        """agy rejected ``--output-format`` (older CLI build without it)."""
        t = text.lower()
        if "output-format" not in t:
            return False
        return any(
            s in t
            for s in (
                "unknown flag",
                "unexpected argument",
                "invalid argument",
                "flag provided but not defined",
            )
        )

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
        """Assemble the agy argv for one non-interactive query.

        ``--output-format stream-json`` turns the buffered print mode into an
        incremental event feed (init/step_update/result — measured on agy
        1.1.11) that powers the live log; the final answer comes from the
        terminal ``result`` event instead of raw stdout. Omitted after the
        first "unknown flag" rejection from an older agy build.
        """
        cmd = [
            AGY_BIN,
            "-p", full_prompt,
            "--model", model,
            "--print-timeout", f"{AGY_PRINT_TIMEOUT_SECONDS}s",
            "--dangerously-skip-permissions",
        ]
        if self._stream_ok:
            cmd += ["--output-format", "stream-json"]
        return cmd

    # -- subprocess execution --------------------------------------------

    async def _execute_cli(
        self,
        cmd: list[str],
        env: dict,
        live_label: str = "query",
        prompt_preview: str = "",
    ) -> tuple[str, str, int, str | None, Path | None]:
        """Run an agy command, streaming its event feed to the live log.

        Returns ``(text, stderr, returncode, hung_reason, live_path)``:
        ``text`` is the final response from the stream-json ``result`` event
        when the stream produced one, else the raw stdout text (plain-text
        mode); ``hung_reason`` is non-None if the overall timeout tripped.

        - ``stdin=DEVNULL`` prevents the subprocess from consuming the MCP
          JSON-RPC stdin stream.
        - Events are consumed incrementally: each one lands in the live log
          (``~/.claude/logs/antigravity/latest.log``) and updates the
          heartbeat activity, so the operator can watch the run.
        - A stream-json run that exits 0 WITHOUT a ``result`` event is
          reported as a failure — the event soup is never returned as if it
          were the model's answer.
        - On caller ``CancelledError`` the process is SIGKILLed so it does
          not become a zombie.
        """
        t0 = time.monotonic()
        streaming = "--output-format" in cmd
        live_path, live_fh, stream_fh, run_tag = _open_live_log(live_label)
        state: dict[str, str] = {"activity": "launching agy", "final": "",
                                 "last_error": "", "status": ""}

        def _emit(text: str) -> None:
            """One event → the per-run log AND the tagged merged stream."""
            _live_write(live_fh, t0, text)
            _live_write(stream_fh, t0, text, run_tag)

        def _close_logs() -> None:
            for _fh in (live_fh, stream_fh):
                if _fh is not None:
                    with contextlib.suppress(Exception):
                        _fh.close()

        if live_fh is not None:
            with contextlib.suppress(Exception):
                shown = [a for a in cmd if a != prompt_preview]
                live_fh.write(
                    f"# antigravity live view — {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
                    f"# argv: {' '.join(shown)}\n"
                    f"# prompt ({len(prompt_preview)} chars): {prompt_preview[:400]!r}\n\n"
                )
                live_fh.flush()
        _live_write(
            stream_fh, t0,
            f"▶ start {' '.join(a for a in cmd[3:5])} "
            f"prompt {len(prompt_preview)} chars: {prompt_preview[:120]!r}",
            run_tag,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            _close_logs()
            return ("", f"agy CLI not found at {AGY_BIN}: {e}", 127, None, live_path)

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        linebuf = bytearray()

        def _feed_stdout(chunk: bytes) -> None:
            linebuf.extend(chunk)
            while True:
                nl = linebuf.find(b"\n")
                if nl < 0:
                    return
                raw = bytes(linebuf[:nl]).strip()
                del linebuf[: nl + 1]
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                    line = _process_agy_event(ev, state)
                except ValueError:
                    line = raw.decode("utf-8", errors="replace")
                if line:
                    _emit(line)

        def _feed_stderr(chunk: bytes) -> None:
            for ln in chunk.decode("utf-8", errors="replace").splitlines():
                if ln.strip():
                    _emit(f"! {ln}")

        async def _consume(stream: asyncio.StreamReader, buffer: list[bytes], feed) -> None:
            while True:
                chunk = await stream.read(65_536)
                if not chunk:
                    return
                buffer.append(chunk)
                with contextlib.suppress(Exception):
                    feed(chunk)

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
                elapsed = time.monotonic() - t0
                await _report_progress(
                    min(elapsed, AGY_OVERALL_TIMEOUT_SECONDS),
                    AGY_OVERALL_TIMEOUT_SECONDS,
                    f"antigravity · {int(elapsed)}s · {state['activity'][:140]}",
                )

        stdout_task = asyncio.create_task(_consume(process.stdout, stdout_chunks, _feed_stdout))
        stderr_task = asyncio.create_task(_consume(process.stderr, stderr_chunks, _feed_stderr))
        heartbeat_task = asyncio.create_task(_heartbeat())
        hung_reason: str | None = None

        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=AGY_OVERALL_TIMEOUT_SECONDS,
            )
            await process.wait()
        except asyncio.TimeoutError:
            await _kill_and_reap(process)
            hung_reason = f"no completion within {AGY_OVERALL_TIMEOUT_SECONDS}s"
        except asyncio.CancelledError:
            await _kill_and_reap(process)
            raise
        finally:
            heartbeat_task.cancel()
            for task in (stdout_task, stderr_task, heartbeat_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            with contextlib.suppress(Exception):
                # returncode None here = the MCP call was cancelled mid-run
                # (caller abort/Esc) and we killed agy — say so explicitly;
                # "exit=None" read like a defect in the 08-08 log sweep.
                rc_label = (
                    process.returncode
                    if process.returncode is not None
                    else "none (cancelled by caller)"
                )
                _emit(
                    f"■ run finished: exit={rc_label} "
                    f"status={state['status'] or 'n/a'} hung={hung_reason or 'no'}",
                )
            _close_logs()

        if hung_reason is not None:
            return ("", hung_reason, process.returncode or -1, hung_reason, live_path)

        stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        returncode = process.returncode or 0

        if streaming:
            if state["final"]:
                text = state["final"]
            else:
                # No result event: never hand the event soup back as an
                # answer. Distinguish agy-reported failure from a truncated
                # stream, and surface which one happened.
                text = ""
                detail = state["last_error"] or (
                    "stream-json run ended without a result event"
                )
                stderr_text = f"{detail}\n{stderr_text}".strip()
                if returncode == 0:
                    returncode = 1
        else:
            text = stdout_text
            if state["last_error"] and returncode == 0:
                stderr_text = f"{state['last_error']}\n{stderr_text}".strip()

        if state["status"] not in ("", "SUCCESS") and returncode == 0:
            # agy reported a non-success terminal status but exited 0 —
            # propagate the failure instead of trusting the exit code.
            returncode = 1
            stderr_text = f"{state['last_error'] or state['status']}\n{stderr_text}".strip()

        return (text, stderr_text, returncode, None, live_path)

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
           (``gemini-3.1-pro-high`` by default).
        2. If agy rejects the model id (lineup changed under the cache) and the
           caller did not pin a model, force-refresh the registry and retry once.
        3. On a capacity / quota signal (and only when the caller did not pin a
           model), fall back to Flash and prefix the answer with a notice.
        4. Auth failures raise with instructions (agy needs an interactive
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

        async def _attempt(target: str) -> tuple[str, str, int, str | None, Path | None]:
            return await self._execute_cli(
                self._build_query_cmd(full_prompt, target), env,
                prompt_preview=full_prompt,
            )

        stdout_text, stderr_text, returncode, hung, live_path = await _attempt(primary_model)
        combined = f"{stdout_text}\n{stderr_text}"

        # Older agy builds don't know --output-format: downgrade to plain-text
        # mode once (for the process lifetime) and retry.
        if (
            returncode != 0
            and hung is None
            and self._stream_ok
            and self._is_flag_error(combined)
        ):
            self._stream_ok = False
            stdout_text, stderr_text, returncode, hung, live_path = await _attempt(primary_model)
            combined = f"{stdout_text}\n{stderr_text}"

        # Self-heal a stale model id: agy rejects unknown models with a fast
        # exit 1 (no spend), so force-refresh the registry once and retry with
        # the re-discovered default. Only for non-pinned calls — a caller who
        # pinned a model should see the rejection verbatim.
        if (
            returncode != 0
            and hung is None
            and not user_specified
            and self._is_invalid_model_error(combined)
        ):
            await self.models.refresh(force=True)
            refreshed = self.models.default_pro
            if refreshed != primary_model:
                primary_model = refreshed
                stdout_text, stderr_text, returncode, hung, live_path = await _attempt(primary_model)
                combined = f"{stdout_text}\n{stderr_text}"

        log_note = f"\n\n[live log: {live_path}]" if live_path else ""

        if hung is not None:
            raise Exception(
                f"agy timed out for model '{primary_model}': {hung}. "
                f"Partial stderr: {stderr_text[:500] or '(none)'}"
                f"{log_note}"
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
                return f"{cleaned}{log_note}"
            # exit 0 but only noise/empty — surface clearly, never return "".
            raise Exception(
                f"agy returned exit 0 but no usable output for '{primary_model}'. "
                "It may have printed only status noise — please retry."
                f"{log_note}"
            )

        # Capacity/quota fallback: Pro -> Flash (only if caller didn't pin one).
        fallback_model = self.models.fallback_pro
        should_fallback = (
            not user_specified
            and primary_model != fallback_model
            and self._is_capacity_error(combined)
        )
        if should_fallback:
            stdout_text, stderr_text, returncode, hung, live_path = await _attempt(fallback_model)
            log_note = f"\n\n[live log: {live_path}]" if live_path else ""
            if hung is None and returncode == 0:
                cleaned = self._clean_output(stdout_text)
                if cleaned:
                    return (
                        f"[Fallback: '{primary_model}' hit a capacity/quota limit, "
                        f"answered by '{fallback_model}']\n\n{cleaned}{log_note}"
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
        raise Exception(f"agy error (exit {returncode}){hint}: {detail[:800]}{log_note}")

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
                "All models (slug — display name; pass either as `model`):",
            ]
            for slug, display in models:
                marker = ""
                if slug == gemini.models.default_pro:
                    marker = " ← default pro"
                elif slug == gemini.models.default_flash:
                    marker = " ← default flash"
                label = slug if slug == display else f"{slug} — {display}"
                lines.append(f"  • {label}{marker}")
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
