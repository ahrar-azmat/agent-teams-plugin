#!/usr/bin/env python3
"""Transient-failure classes and the capacity-shed retry path (2026-08-31).

Run:  python3 tests/test_transient_retry.py        (no dependencies — mcp is stubbed)

Why this file exists: on 2026-08-31 four max-effort reviews died with
"Selected model is at capacity. Please try a different model." and
attempts=1. codex renders CodexErr::ServerOverloaded with that text and marks
it NON-retryable (is_retryable() → false, codex-rs/protocol/src/error.rs), so
the wrapper is the only retry layer — and its classifier matched the variant
NAME ("overloaded"), never the MESSAGE. These checks pin:
  1. the classifier recognises the rendered text — read from the installed
     codex source (~/Documents/codex-installed, tag-aligned) when present, so
     a vendor rewording fails HERE before it fails in production;
  2. the two classes, their budgets and the backoff schedule;
  3. the orchestration: shed → wait → resume the SAME thread, bounded and
     journaled, model pin untouched; the amnesia guard still applies; a
     caller cancel during the wait leaves a resumable, journaled run.
"""
import asyncio
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_write_mode import server  # noqa: E402  (shares the mcp stub + module load)

CAPACITY_TEXT = "Selected model is at capacity. Please try a different model."
INSTALLED_SRC = Path.home() / "Documents" / "codex-installed"
INSTALLED_ERROR_RS = INSTALLED_SRC / "codex-rs" / "protocol" / "src" / "error.rs"
FAKE = Path(__file__).resolve().parent / "fake_codex.py"


def _installed_source_matches_binary() -> tuple[bool, str]:
    """The source worktree is evidence only when its tag matches the codex
    binary on PATH (scripts/codex_src.py keeps them aligned). Never silent:
    the reason for a skip is returned and printed."""
    import shutil
    import subprocess
    codex = shutil.which("codex")
    if not codex:
        return False, "no codex on PATH"
    try:
        ver = subprocess.run([codex, "--version"], capture_output=True, text=True,
                             timeout=20).stdout.strip()
        tag = subprocess.run(["git", "-C", str(INSTALLED_SRC), "describe", "--tags",
                              "--exact-match"], capture_output=True, text=True,
                             timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"cannot read versions: {e}"
    want = "rust-v" + ver.replace("codex-cli", "").strip()
    if tag != want:
        return False, f"source tag {tag!r} != binary {want!r} — run scripts/codex_src.py"
    return True, f"{tag} == binary"

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        return
    FAIL += 1
    msg = f"  FAIL: {name}" + (f" — {detail}" if detail else "")
    print(msg)
    raise AssertionError(msg)


def _installed_server_overloaded_text() -> str | None:
    """The #[error("...")] text rendered for ServerOverloaded in the installed
    codex source, or None when the aligned worktree is not on this machine."""
    try:
        lines = INSTALLED_ERROR_RS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        if line.strip() == "ServerOverloaded," and i > 0:
            prev = lines[i - 1].strip()
            if prev.startswith('#[error("') and prev.endswith('")]'):
                return prev[len('#[error("'):-len('")]')]
    return None


# ---------------------------------------------------------------------------
# 1. Classifier
# ---------------------------------------------------------------------------

def test_classifier_recognises_the_rendered_capacity_text() -> None:
    text = _installed_server_overloaded_text()
    aligned, why = _installed_source_matches_binary()
    if text is None or not aligned:
        text = CAPACITY_TEXT
        print(f"  SKIP source pin ({why if text is None or not aligned else ''}"
              f"{'; worktree absent' if text is None else ''}) — pinning the recorded literal")
    else:
        check(f"installed source ({why}) still renders the recorded literal",
              text == CAPACITY_TEXT, text)
    check("rendered ServerOverloaded text → overload",
          server._transient_class(f"TURN FAILED: {text}") == "overload")
    check("legacy boolean alias agrees", server._is_transient_error(text))
    for s in (
        'HTTP 503 {"error":{"code":"server_is_overloaded"}}',
        'error code slow_down',
        "The server is overloaded",
        "rate limit exceeded",
        "Too Many Requests",
        "error 503 service unavailable",
        "temporarily unavailable, retry later",
    ):
        check(f"overload: {s!r}", server._transient_class(s) == "overload")
    for s in (
        "stream disconnected before completion",
        "connection reset by peer",
        "error 502 bad gateway",
        "request timed out",
        "internal server error",
    ):
        check(f"disconnect: {s!r}", server._transient_class(s) == "disconnect")
    for s in (
        "401 Unauthorized: refresh token failed",
        "Quota exceeded. Check your plan and billing details.",
        "unknown configuration field `bogus_key` in -c/--config override",
        "You've hit your usage limit.",
        "",
    ):
        check(f"not transient: {s!r}", server._transient_class(s) is None)
    check("overload wins when both classes appear",
          server._transient_class("connection error: error 503") == "overload")


def test_classifier_covers_the_pinned_error_catalog() -> None:
    """Exact renderings from codex-rs/protocol/src/error.rs @ rust-v0.151.0
    (review of 1.16.2 found four retryable ones missing). Retryable-in-codex
    errors reach this wrapper after codex's own retries are spent, wrapped
    as RetryLimit or verbatim on stderr."""
    overload = (
        "Selected model is at capacity. Please try a different model.",
        "We're currently experiencing high demand, which may cause temporary errors.",
        "exceeded retry limit, last status: 503 Service Unavailable, request id: req_1",
        "rate limit exceeded: too many requests",
    )
    disconnect = (
        "stream disconnected before completion: connection reset by peer",
        "Connection failed: error sending request for url (https://api.openai.com/v1/responses)",
        "Error while reading the server response: unexpected EOF, request id: req_2",
        "internal error; agent loop died unexpectedly",
        "exceeded retry limit, last status: 500 Internal Server Error",
        "request timed out",
        "timeout waiting for child process to exit",
    )
    terminal = (
        "Quota exceeded. Check your plan and billing details.",
        "You've hit your usage limit. Upgrade to Pro or try again at 3pm.",
        "turn aborted. Something went wrong? Hit `/feedback` to report the issue.",
        "no thread with id: 01a0",
        "sandbox error: denied",
        "Fatal error: bad things",
        "unsupported operation: x",
        "Image poisoning",
        "duplicate tool: y",
    )
    for t in overload:
        check(f"overload ← {t[:50]!r}", server._transient_class(t) == "overload")
    for t in disconnect:
        check(f"disconnect ← {t[:50]!r}", server._transient_class(t) == "disconnect")
    for t in terminal:
        check(f"terminal (no retry) ← {t[:50]!r}", server._transient_class(t) is None)


def test_backoff_schedule_and_budgets() -> None:
    base = server.OVERLOAD_BACKOFF_BASE_SECONDS
    cap = server.OVERLOAD_BACKOFF_CAP_SECONDS
    sched = [server._overload_backoff_seconds(i) for i in range(6)]
    check("schedule is base·2^i capped",
          sched == [min(cap, base * 2 ** i) for i in range(6)], str(sched))
    check("schedule is monotone non-decreasing",
          all(a <= b for a, b in zip(sched, sched[1:])))
    check("cap is a ceiling", max(sched) <= cap)
    import os
    if ("CODEX_ORACLE_OVERLOAD_BACKOFF" not in os.environ
            and "CODEX_ORACLE_OVERLOAD_RETRIES" not in os.environ):
        check("default schedule 30/60/120/240 then capped at 300",
              sched == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0], str(sched))
        check("default overload budget 4 > disconnect budget 2",
              (server.OVERLOAD_MAX_RETRIES, server.MAX_TRANSIENT_RETRIES) == (4, 2))
    real = server.OVERLOAD_BACKOFF_BASE_SECONDS
    try:
        server.OVERLOAD_BACKOFF_BASE_SECONDS = 0.0
        check("backoff 0 → no wait", server._overload_backoff_seconds(3) == 0.0)
    finally:
        server.OVERLOAD_BACKOFF_BASE_SECONDS = real
    check("total ceiling = overload + disconnect budgets",
          server.MAX_TOTAL_RETRIES == server.OVERLOAD_MAX_RETRIES + server.MAX_TRANSIENT_RETRIES)
    check("overload retries env is bounded (≤12)", server.OVERLOAD_MAX_RETRIES <= 12)


# ---------------------------------------------------------------------------
# 2. Orchestration
# ---------------------------------------------------------------------------

def _scripted_exec(script, thread_ids=("t-shed-1",)):
    """Fake _exec_codex_once. script = [('fail', text) | ('ok', answer)] —
    the last entry repeats. thread_ids cycles per call (last repeats), so a
    test can model a resume that lands on a different thread."""
    calls: list[list[str]] = []

    async def fake(cmd, output_file, state, emit, ctx, model, extra_env=None,
                   workdir="", request_started=None):
        i = len(calls)
        calls.append(list(cmd))
        kind, payload = script[min(i, len(script) - 1)]
        state["thread_id"] = thread_ids[min(i, len(thread_ids) - 1)]
        if kind == "ok":
            return (payload, True, "", "", 0, None, False)
        state["last_error"] = payload
        return ("", False, "", payload, 1, None, False)

    return fake, calls


class _Harness:
    """Swap the collaborators _run_codex touches; restore on exit."""

    def __init__(self, exec_fake, wait_fake=None):
        self.exec_fake, self.wait_fake = exec_fake, wait_fake
        self.waits: list[float] = []

    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "logs").mkdir()
        self.saved = (
            server._exec_codex_once, server._wait_for_capacity, server._get_cwd,
            server.random, server.LIVE_LOG_DIR, server.RUNS_JOURNAL,
        )
        server._exec_codex_once = self.exec_fake
        if self.wait_fake is None:
            async def _record(seconds, *a, **k):
                self.waits.append(seconds)
            server._wait_for_capacity = _record
        else:
            server._wait_for_capacity = self.wait_fake
        server._get_cwd = lambda: str(td)
        server.random = types.SimpleNamespace(uniform=lambda a, b: 1.0)
        server.LIVE_LOG_DIR = td / "logs"
        server.RUNS_JOURNAL = td / "logs" / "runs.jsonl"
        return self

    def __exit__(self, *exc):
        (server._exec_codex_once, server._wait_for_capacity, server._get_cwd,
         server.random, server.LIVE_LOG_DIR, server.RUNS_JOURNAL) = self.saved
        self.td.cleanup()
        return False

    def log_text(self) -> str:
        return "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(Path(self.td.name, "logs").glob("*.log"))
        )


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_capacity_shed_waits_then_resumes_the_same_thread() -> None:
    fake, calls = _scripted_exec(
        [("fail", CAPACITY_TEXT), ("fail", CAPACITY_TEXT), ("ok", "verdict: clean")])
    with _Harness(fake) as h:
        res = asyncio.run(server._run_codex("review this"))
        log = h.log_text()
    check("three attempts", len(calls) == 3, str(len(calls)))
    check("attempt 1 is a fresh run", "resume" not in calls[0])
    check("attempt 2 resumes the same thread",
          "resume" in calls[1] and _after(calls[1], "resume") == "t-shed-1")
    check("attempt 3 resumes the same thread",
          "resume" in calls[2] and _after(calls[2], "resume") == "t-shed-1")
    model = _after(calls[0], "--model")
    check("model pin unchanged across attempts",
          all(_after(c, "--model") == model for c in calls))
    effort = [a for a in calls[0] if a.startswith("model_reasoning_effort=")]
    check("effort pin unchanged across attempts",
          all([a for a in c if a.startswith("model_reasoning_effort=")] == effort
              for c in calls))
    check("waited 30s then 60s (jitter pinned to 1.0)", h.waits == [30.0, 60.0],
          str(h.waits))
    check("answer delivered", "verdict: clean" in res)
    check("recovery note names the shed and the wait",
          "recovered automatically after a provider capacity shed (waited 90s)" in res
          and "3 attempts" in res, res[-300:])
    check("live log names the shed, the per-class count and the wait",
          "provider capacity shed — resuming thread t-shed-1 (overload retry 1/4, "
          "total attempt 2, after a 30s wait)" in log, log[-600:])


def test_mixed_failure_sequence_uses_per_class_budgets() -> None:
    # 2 disconnects (exhausts that class) then overloads: the overload class
    # still has its FULL budget; the total never exceeds the ceiling.
    disc = "stream disconnected before completion: reset"
    script = [("fail", disc), ("fail", disc)] + [("fail", CAPACITY_TEXT)] * 10
    fake, calls = _scripted_exec(script)
    with _Harness(fake) as h:
        res = asyncio.run(server._run_codex("review this"))
    n_over, n_disc = server.OVERLOAD_MAX_RETRIES, server.MAX_TRANSIENT_RETRIES
    check("attempts = 1 + disconnect budget + overload budget",
          len(calls) == 1 + n_disc + n_over, str(len(calls)))
    check("all overload waits happened", len(h.waits) == n_over, str(h.waits))
    check("failure names the capacity shed", "provider capacity" in res)
    # then the other order: an overload first must not eat the disconnect budget
    script = [("fail", CAPACITY_TEXT), ("fail", disc), ("fail", disc), ("fail", disc)]
    fake, calls = _scripted_exec(script)
    with _Harness(fake) as h:
        asyncio.run(server._run_codex("review this"))
    check("a disconnect after an overload keeps its own budget",
          len(calls) == 1 + 1 + n_disc, str(len(calls)))


def test_jitter_never_exceeds_the_cap() -> None:
    fake, calls = _scripted_exec([("fail", CAPACITY_TEXT)])
    with _Harness(fake) as h:
        server.random = types.SimpleNamespace(uniform=lambda a, b: 1.2)  # worst-case jitter
        saved = server.OVERLOAD_MAX_RETRIES
        server.OVERLOAD_MAX_RETRIES = 6
        try:
            asyncio.run(server._run_codex("review this"))
        finally:
            server.OVERLOAD_MAX_RETRIES = saved
    check("every jittered wait ≤ cap", all(w <= server.OVERLOAD_BACKOFF_CAP_SECONDS for w in h.waits), str(h.waits))
    check("early waits carry the +20% jitter", h.waits[0] == 36.0, str(h.waits[:2]))


def test_capacity_shed_exhausts_budget_and_hands_off_to_resume() -> None:
    fake, calls = _scripted_exec([("fail", CAPACITY_TEXT)])
    with _Harness(fake) as h:
        res = asyncio.run(server._run_codex("review this"))
        runs = server._journal_runs()
    n = server.OVERLOAD_MAX_RETRIES
    check("attempts = overload retries + 1", len(calls) == n + 1, str(len(calls)))
    check("waits follow the schedule",
          h.waits == [server._overload_backoff_seconds(i) for i in range(n)],
          str(h.waits))
    check("failure is reported, not disguised", res.startswith("[Codex error (exit 1)"))
    check("error header carries the tool/status signature for the push gate",
          "status:error" in res.split("]", 1)[0] or "tool:" not in res, res[:120])
    check("capacity note present with the counts",
          f"answered 'at capacity' on {n + 1} attempt(s)" in res
          and f"waited {int(sum(h.waits))}s" in res, res[:500])
    check("pin explicitly unchanged", "model/effort pin was NOT changed" in res)
    check("hands off to the explicit resume path",
          "codex_resume_run" in res and "run id:" in res)
    rec = next(iter(runs.values()))
    check("journal: attempts", rec.get("attempts") == n + 1, str(rec.get("attempts")))
    check("journal: retry classes", rec.get("retry_classes") == ["overload"] * n,
          str(rec.get("retry_classes")))
    check("journal: capacity wait seconds",
          rec.get("capacity_wait_s") == int(sum(h.waits)), str(rec.get("capacity_wait_s")))
    check("journal: status error", rec.get("status") == "error")


def test_disconnect_keeps_the_short_budget_and_never_waits() -> None:
    fake, calls = _scripted_exec(
        [("fail", "stream disconnected before completion: connection reset by peer")])
    with _Harness(fake) as h:
        res = asyncio.run(server._run_codex("review this"))
    check("attempts = transient retries + 1",
          len(calls) == server.MAX_TRANSIENT_RETRIES + 1, str(len(calls)))
    check("no waits for a disconnect", h.waits == [], str(h.waits))
    check("no capacity note for a disconnect", "provider capacity" not in res)
    check("still hands off to resume", "codex_resume_run" in res)


def test_non_transient_failure_is_not_retried() -> None:
    fake, calls = _scripted_exec(
        [("fail", "Quota exceeded. Check your plan and billing details.")])
    with _Harness(fake) as h:
        asyncio.run(server._run_codex("review this"))
    check("one attempt only", len(calls) == 1, str(len(calls)))
    check("no waits", h.waits == [])


def test_amnesia_guard_still_applies_under_a_shed() -> None:
    # The measured amnesia shape: `exec resume <id>` that codex does not know
    # silently starts a FRESH context-less thread and exits 0 with an answer
    # to nothing. After a shed that answer must be refused, not delivered.
    fake, calls = _scripted_exec(
        [("fail", CAPACITY_TEXT), ("ok", "fresh-thread answer to nothing")],
        thread_ids=("t-a", "t-b"))
    with _Harness(fake) as h:
        res = asyncio.run(server._run_codex("review this"))
    check("stops after the first resume", len(calls) == 2, str(len(calls)))
    check("one wait happened", h.waits == [30.0], str(h.waits))
    check("continuity loss reported", "resume continuity lost" in res, res[:300])
    check("the amnesiac answer is not delivered", "answer to nothing" not in res)
    check("still names the shed it was recovering from",
          "answered 'at capacity' on 1 attempt(s); waited 30s" in res, res[:400])


def test_wait_for_capacity_keeps_heartbeating() -> None:
    progress: list[str] = []

    class Ctx:
        async def report_progress(self, p, total, message):
            progress.append(message)

    state = {"activity": "x"}
    emitted: list[str] = []
    saved = server.PROGRESS_INTERVAL_SECONDS
    try:
        server.PROGRESS_INTERVAL_SECONDS = 0.01
        t0 = time.monotonic()
        asyncio.run(server._wait_for_capacity(
            0.08, Ctx(), t0, t0, state, "gpt-test", emitted.append))
    finally:
        server.PROGRESS_INTERVAL_SECONDS = saved
    check("activity says why it is waiting", "provider at capacity" in state["activity"])
    check("heartbeats flowed during the wait", len(progress) >= 1, str(len(progress)))
    check("heartbeat carries the wait as activity",
          any("provider at capacity" in m for m in progress), str(progress[:2]))
    check("zero wait returns immediately without touching activity (no cancel seen)",
          asyncio.run(server._wait_for_capacity(0, Ctx(), t0, t0, {"activity": "a"},
                                                "m", emitted.append)) is False)
    # Once the request is past PROGRESS_MAX_SECONDS its token is dead: no
    # heartbeat may start, and no "notifications stopped" line may be logged.
    progress.clear(); emitted.clear()
    saved = server.PROGRESS_INTERVAL_SECONDS
    try:
        server.PROGRESS_INTERVAL_SECONDS = 0.01
        dead_t0 = time.monotonic() - (server.PROGRESS_MAX_SECONDS + 5)
        asyncio.run(server._wait_for_capacity(
            0.05, Ctx(), dead_t0, dead_t0, state, "gpt-test", emitted.append))
    finally:
        server.PROGRESS_INTERVAL_SECONDS = saved
    check("no heartbeat on a dead token", progress == [], str(progress))
    check("no spurious 'stopped' line on a dead token",
          not any("progress notifications stopped" in e for e in emitted), str(emitted))


def test_wait_for_capacity_without_ctx_and_cancellation() -> None:
    async def scenario():
        state = {"activity": "x"}
        t0 = time.monotonic()
        await server._wait_for_capacity(0.01, None, t0, t0, state, "m", lambda s: None)
        task = asyncio.create_task(
            server._wait_for_capacity(5.0, None, t0, t0, state, "m", lambda s: None))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False
    check("cancel propagates out of the wait", asyncio.run(scenario()))


def test_cancel_during_the_wait_is_journaled_and_resumable() -> None:
    fake, calls = _scripted_exec([("fail", CAPACITY_TEXT)])

    async def scenario():
        task = asyncio.create_task(server._run_codex("review this"))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if calls:
                break
        await asyncio.sleep(0.05)  # inside the (5s) capacity wait now
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    async def slow_wait(seconds, *a, **k):
        await asyncio.sleep(5.0)

    with _Harness(fake, wait_fake=slow_wait) as h:
        cancelled = asyncio.run(scenario())
        log = h.log_text()
        runs = server._journal_runs()
    check("caller cancel propagated", cancelled)
    check("exactly one attempt before the wait", len(calls) == 1, str(len(calls)))
    check("live log records the cancel during the wait",
          "run cancelled by caller during the capacity wait" in log, log[-400:])
    rec = next(iter(runs.values()))
    check("journal: cancelled", rec.get("status") == "cancelled", str(rec.get("status")))
    check("journal: thread id kept (resumable)", rec.get("thread_id") == "t-shed-1",
          str(rec.get("thread_id")))
    # Round 36: attempt telemetry counts REAL spawns only — a wait that was
    # cancelled never retried, so the record shows one attempt, no retry
    # class, and the time actually waited (not the planned 5 s).
    check("journal: cancel record counts the one real attempt and the actual wait",
          rec.get("attempts") == 1 and rec.get("retry_classes") == []
          and isinstance(rec.get("capacity_wait_s"), int) and rec.get("capacity_wait_s") < 5,
          str(rec)[:300])


# ---------------------------------------------------------------------------
# 3. Real JSONL failure shapes (review of 1.16.2: the fakes above return empty
#    stdout; production failures carry assistant commentary BEFORE the error)
# ---------------------------------------------------------------------------

class _FakeCodex:
    """Drive the REAL _exec_codex_once with tests/fake_codex.py; waits stubbed."""

    def __init__(self, **env):
        self.env = env
        self.waits: list[float] = []

    def __enter__(self):
        import os
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "logs").mkdir()
        self.saved = (server._codex_argv0, server._wait_for_capacity, server._get_cwd,
                      server.random, server.LIVE_LOG_DIR, server.RUNS_JOURNAL, dict(os.environ))
        server._codex_argv0 = lambda: [sys.executable, str(FAKE)]

        async def _record(seconds, *a, **k):
            self.waits.append(seconds)
        server._wait_for_capacity = _record
        server._get_cwd = lambda: str(td)
        server.random = types.SimpleNamespace(uniform=lambda a, b: 1.0)
        server.LIVE_LOG_DIR = td / "logs"
        server.RUNS_JOURNAL = td / "logs" / "runs.jsonl"
        for k in list(os.environ):
            if k.startswith("FAKE_CODEX_"):
                del os.environ[k]
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        import os
        (server._codex_argv0, server._wait_for_capacity, server._get_cwd, server.random,
         server.LIVE_LOG_DIR, server.RUNS_JOURNAL, env) = self.saved
        os.environ.clear()
        os.environ.update(env)
        self.td.cleanup()
        return False


def test_real_jsonl_capacity_failure_with_prior_commentary() -> None:
    with _FakeCodex(FAKE_CODEX_FAIL="capacity", FAKE_CODEX_SLEEP="0",
                    FAKE_CODEX_PRELUDE="Looking at the diff now; two suspicious spots.") as h:
        res = asyncio.run(server._run_codex("review this"))
        runs = server._journal_runs()
    n = server.OVERLOAD_MAX_RETRIES
    check("all overload retries were spent", len(h.waits) == n, str(h.waits))
    check("the FAILURE is the message, not the commentary",
          res.startswith(f"[Codex error (exit 1) after {n + 1} attempts"), res[:120])
    check("capacity note + pin + hand-off present",
          "provider capacity" in res and "pin was NOT changed" in res
          and "codex_resume_run" in res and "run id:" in res, res[:600])
    check("prior commentary is labelled partial, not presented as the answer",
          "[partial output before the failure — NOT the answer]" in res
          and "two suspicious spots" in res.split("[partial output", 1)[1], res[-400:])
    check("no ok/answer header on a failed run", not res.startswith("[Codex model:"))
    rec = next(iter(runs.values()))
    check("journal: attempts and classes", rec.get("attempts") == n + 1
          and rec.get("retry_classes") == ["overload"] * n, str(rec)[:200])


def test_terminal_error_is_not_contaminated_by_model_output() -> None:
    # The model's own text mentions "at capacity"; the TERMINAL error is a
    # quota denial. Classification must read the terminal error only.
    with _FakeCodex(FAKE_CODEX_FAIL="quota", FAKE_CODEX_SLEEP="0",
                    FAKE_CODEX_PRELUDE="FYI the county portal said it was at capacity.") as h:
        res = asyncio.run(server._run_codex("review this"))
        runs = server._journal_runs()
    check("no retries, no waits for a quota denial", h.waits == [] and
          next(iter(runs.values())).get("attempts") == 1, str(h.waits))
    check("quota error surfaced verbatim", "Quota exceeded" in res, res[:200])
    check("no capacity note on a quota failure", "provider capacity" not in res)


def test_real_jsonl_disconnect_then_success_keeps_only_the_final_answer() -> None:
    # Attempt 1 fails after commentary; attempt 2 answers. The answer must be
    # attempt 2's, never attempt 1's stale commentary.
    with _FakeCodex(FAKE_CODEX_SLEEP="0", FAKE_CODEX_PRELUDE="stale commentary from attempt one") as h:
        import os
        calls = {"n": 0}
        real_exec = server._exec_codex_once

        async def flaky(*a, **k):
            calls["n"] += 1
            os.environ["FAKE_CODEX_FAIL"] = "disconnect" if calls["n"] == 1 else ""
            if not os.environ["FAKE_CODEX_FAIL"]:
                del os.environ["FAKE_CODEX_FAIL"]
                os.environ["FAKE_CODEX_PRELUDE"] = ""
                del os.environ["FAKE_CODEX_PRELUDE"]
            return await real_exec(*a, **k)
        server._exec_codex_once = flaky
        try:
            res = asyncio.run(server._run_codex("review this"))
        finally:
            server._exec_codex_once = real_exec
    check("two attempts", calls["n"] == 2)
    check("delivered attempt 2's answer", res.startswith("[Codex model:") and "ANSWER:review this" in res, res[:200])
    check("attempt 1's commentary did not leak into the answer", "stale commentary" not in res)
    check("recovery note present", "recovered automatically after a transient failure" in res)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError:
            failed.append(t.__name__)
        except Exception as e:  # noqa: BLE001
            FAIL += 1
            failed.append(f"{t.__name__} ({type(e).__name__}: {e})")
            print(f"  ERROR in {t.__name__}: {type(e).__name__}: {e}")
    print(f"{'✓' if not failed else '✗'} transient-retry: {PASS} passed, {FAIL} failed"
          + (f" — {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
