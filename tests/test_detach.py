#!/usr/bin/env python3
"""Run survivability across MCP server restarts + run operations (1.17.0).

Run:  python3 tests/test_detach.py        (no dependencies — mcp is stubbed;
                                          codex is tests/fake_codex.py, real
                                          processes, no API spend; ~25 s)

What is pinned:
  1. the child's stdio is FILE-backed (spool), tailed into the live view;
     the spawn record (pid/pgid/spool/deadline) is journaled;
  2. a caller cancel (no shutdown signal) still KILLS the process;
  3. a server-shutdown cancel DETACHES: the process survives, the journal
     says `detached`, and codex_resume_run ADOPTS it — waiting for a running
     one, returning a finished one's answer, falling back to a thread resume
     when it died without an answer;
  4. the detached watchdog enforces MAX_RUNTIME with no server alive;
  5. codex_runs / codex_run_log / codex_cancel_run;
  6. the shutdown signal handlers and the plugin's own MCP registration.
"""
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_write_mode import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_codex.py"

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


def _raw_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class _Iso:
    """Isolated logs/journal/cwd; codex = the fake; shutdown flag clear."""

    def __init__(self, **env):
        self.env = env

    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "logs").mkdir()
        self.saved = (server._codex_argv0, server._get_cwd, server.LIVE_LOG_DIR,
                      server.RUNS_JOURNAL, dict(os.environ))
        server._codex_argv0 = lambda: [sys.executable, str(FAKE)]
        server._get_cwd = lambda: str(td)
        server.LIVE_LOG_DIR = td / "logs"
        server.RUNS_JOURNAL = td / "logs" / "runs.jsonl"
        for k in list(os.environ):
            if k.startswith("FAKE_CODEX_"):
                del os.environ[k]
        os.environ.update(self.env)
        server._SHUTDOWN.clear()
        self.td_path = td
        return self

    def __exit__(self, *exc):
        (server._codex_argv0, server._get_cwd, server.LIVE_LOG_DIR,
         server.RUNS_JOURNAL, env) = self.saved
        os.environ.clear()
        os.environ.update(env)
        server._SHUTDOWN.clear()
        self.td.cleanup()
        return False

    def rec(self):
        runs = server._journal_runs()
        return list(runs.values())[-1] if runs else {}

    def log_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in sorted((self.td_path / "logs").glob("*.log")))


async def _wait_spawn(iso: _Iso, timeout: float = 10.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rec = iso.rec()
        if rec.get("has_spawn") and rec.get("pid"):
            return rec
        await asyncio.sleep(0.05)
    raise AssertionError("no spawn record within timeout")


async def _cancel_after_spawn(iso: _Iso, prompt: str, shutdown: bool) -> tuple[dict, bool]:
    task = asyncio.create_task(server._run_codex(prompt))
    rec = await _wait_spawn(iso)
    await asyncio.sleep(0.3)  # let the first events stream in
    if shutdown:
        server._SHUTDOWN.set()
    task.cancel()
    try:
        await task
        cancelled = False
    except asyncio.CancelledError:
        cancelled = True
    server._SHUTDOWN.clear()
    return rec, cancelled


# ---------------------------------------------------------------------------

def test_spool_stream_and_spawn_journal() -> None:
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        res = asyncio.run(server._run_codex("hello world"))
        rec = iso.rec()
        log = iso.log_text()
        check("answer delivered from the spool answer file", "ANSWER:hello world" in res, res[:200])
        check("journal: spawn record with pid/pgid/spool", rec.get("has_spawn") and rec.get("pid")
              and rec.get("pgid") and rec.get("stdout") and rec.get("output_file"), str(rec)[:300])
        check("journal: end ok", rec.get("has_end") and rec.get("status") == "ok")
        check("spool stdout is the JSONL event stream",
              Path(rec["stdout"]).exists() and "thread.started" in Path(rec["stdout"]).read_text())
        check("spool answer file kept as evidence", Path(rec["output_file"]).read_text().startswith("ANSWER:"))
        check("spool lives under logs/runs/<run>", "/runs/" in rec["stdout"])
        check("live log digested the stream", "thread started" in log and "▶ codex pid" in log, log[-400:])
        check("codex process reaped", not _raw_alive(int(rec["pid"])))
        wd = int(rec.get("watchdog_pid") or 0)
        check("watchdog spawned", wd > 0)
        time.sleep(0.3)
        check("watchdog gone after a normal finish", not _raw_alive(wd))


def test_caller_cancel_still_kills() -> None:
    with _Iso(FAKE_CODEX_SLEEP="20") as iso:
        rec, cancelled = asyncio.run(_cancel_after_spawn(iso, "slow task", shutdown=False))
        pid = int(rec["pid"])
        check("cancel propagated", cancelled)
        deadline = time.time() + 3
        while _raw_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        check("codex killed on caller cancel", not _raw_alive(pid))
        rec = iso.rec()
        check("journal: end cancelled, not detached",
              rec.get("has_end") and rec.get("status") == "cancelled" and not rec.get("has_detached"))
        check("live log says cancelled by caller", "cancelled by caller" in iso.log_text())


def test_shutdown_detaches_and_resume_adopts_running() -> None:
    with _Iso(FAKE_CODEX_SLEEP="3", FAKE_CODEX_ANSWER="SURVIVED-42") as iso:
        rec, cancelled = asyncio.run(_cancel_after_spawn(iso, "long review", shutdown=True))
        pid = int(rec["pid"])
        check("cancel propagated", cancelled)
        check("codex STILL RUNNING after server-shutdown cancel", _raw_alive(pid))
        rec = iso.rec()
        check("journal: detached, no end", rec.get("has_detached") and not rec.get("has_end"), str(rec)[:200])
        check("live log explains the detach",
              "keeps running DETACHED" in iso.log_text() and "codex_resume_run" in iso.log_text())
        status = asyncio.run(server.codex_runs())
        check("codex_runs shows DETACHED with pid", "DETACHED" in status and str(pid) in status, status)
        nudged = asyncio.run(server.codex_resume_run(run=rec["run"], nudge="hurry"))
        check("a nudge is refused while the detached run still runs", "cannot take new instructions" in nudged, nudged[:200])
        t0 = time.time()
        res = asyncio.run(server.codex_resume_run(run=rec["run"]))
        check("adoption waited for the process", time.time() - t0 >= 1.0)
        check("adoption delivered the answer", "SURVIVED-42" in res and "collected from run" in res, res[:300])
        check("no re-ask: answer header carries model/reasoning", res.startswith("[Codex model:"), res[:80])
        rec = iso.rec()
        check("journal: end ok, adopted", rec.get("has_end") and rec.get("status") == "ok" and rec.get("adopted"))
        check("result file persisted", rec.get("result_file") and Path(rec["result_file"]).exists())
        check("adoption live log replayed the spool",
              any("ADOPTION" in p.read_text() and "thread started" in p.read_text()
                  for p in (iso.td_path / "logs").glob("*adopt.log")))
        again = asyncio.run(server.codex_resume_run(run=rec["run"]))
        check("second collect returns the stored answer at no cost", "had COMPLETED" in again and "SURVIVED-42" in again)


def test_detached_finished_before_collection() -> None:
    with _Iso(FAKE_CODEX_SLEEP="0.3", FAKE_CODEX_ANSWER="DONE-EARLY") as iso:
        rec, _ = asyncio.run(_cancel_after_spawn(iso, "quick", shutdown=True))
        pid = int(rec["pid"])
        deadline = time.time() + 5
        while _raw_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        check("detached process finished on its own", not _raw_alive(pid))
        res = asyncio.run(server.codex_resume_run())  # most recent recoverable run
        check("finished detached run collected without a model call",
              "DONE-EARLY" in res and "collected from run" in res, res[:300])


def test_detached_died_without_answer_falls_back_to_thread_resume() -> None:
    with _Iso() as iso:
        td = iso.td_path
        spool = td / "logs" / "runs" / "codex9-1" ; spool.mkdir(parents=True)
        (spool / "attempt0.stdout.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "T-9"}) + "\n")
        now = time.time()
        for rec in (
            {"run": "codex9·1", "phase": "start", "ts": now - 30, "engine": "codex", "tool": "codex_query",
             "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": True,
             "cwd": str(td), "prompt": "original question", "log": ""},
            {"run": "codex9·1", "phase": "session", "ts": now - 29, "thread_id": "T-9"},
            {"run": "codex9·1", "phase": "spawn", "ts": now - 29, "pid": 999999999, "pgid": 999999999,
             "stdout": str(spool / "attempt0.stdout.jsonl"), "stderr": str(spool / "attempt0.stderr.log"),
             "output_file": str(spool / "attempt0.txt"), "spawn_ts": now - 29, "deadline_ts": now + 3000},
            {"run": "codex9·1", "phase": "detached", "ts": now - 10, "status": "detached", "pid": 999999999},
        ):
            server._journal(rec)
        captured = {}

        async def fake_run(prompt, **kw):
            captured["prompt"], captured["kw"] = prompt, kw
            return "[resumed answer]"

        real = server._run_codex
        server._run_codex = fake_run
        try:
            res = asyncio.run(server.codex_resume_run(run="codex9·1"))
        finally:
            server._run_codex = real
        check("fell back to a thread resume of the SAME thread", captured.get("kw", {}).get("resume_tid") == "T-9", str(captured)[:300])
        check("original task restated", "original question" in captured.get("prompt", ""))
        check("resumed answer returned", "[resumed answer]" in res)
        rec = iso.rec()
        check("journal: the dead detached process was closed out as error", rec.get("has_end") and rec.get("status") == "error", str(rec)[:200])


def test_detached_is_inferred_from_a_dead_server_pid() -> None:
    """A torn shutdown may never journal `detached`. The spawn record carries
    the owning server pid; a live codex whose server is gone IS detached."""
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="2", FAKE_CODEX_ANSWER="ORPHAN-OK") as iso:
        td = iso.td_path
        spool = td / "logs" / "runs" / "codex7-1"; spool.mkdir(parents=True)
        out = spool / "attempt0.txt"
        with open(spool / "attempt0.stdout.jsonl", "ab") as so, open(spool / "attempt0.stderr.log", "ab") as se:
            proc = subprocess.Popen([sys.executable, str(FAKE), "exec", "--json", "--model", "x",
                                     "--output-last-message", str(out), "orphan task"],
                                    stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True)
        now = time.time()
        for rec in (
            {"run": "codex7·1", "phase": "start", "ts": now - 5, "engine": "codex", "tool": "codex_query",
             "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": True,
             "cwd": str(td), "prompt": "orphan task", "log": ""},
            {"run": "codex7·1", "phase": "spawn", "ts": now - 5, "pid": proc.pid, "pgid": proc.pid,
             "server_pid": 999999999, "stdout": str(spool / "attempt0.stdout.jsonl"),
             "stderr": str(spool / "attempt0.stderr.log"), "output_file": str(out),
             "spawn_ts": now - 5, "deadline_ts": now + 3000},
        ):
            server._journal(rec)
        rec = iso.rec()
        check("no detach record, yet inferred DETACHED (server pid dead)", server._is_detached(rec)
              and server._run_status(rec) == "DETACHED", server._run_status(rec))
        res = asyncio.run(server.codex_resume_run(run="codex7·1"))
        check("adopted and collected without any cleanup having run", "ORPHAN-OK" in res and "collected from run" in res, res[:300])
        proc.wait(timeout=5)


def test_watchdog_enforces_deadline_without_a_server() -> None:
    saved = server.MAX_RUNTIME_SECONDS
    server.MAX_RUNTIME_SECONDS = 2
    try:
        with _Iso(FAKE_CODEX_SLEEP="60") as iso:
            rec, _ = asyncio.run(_cancel_after_spawn(iso, "runaway", shutdown=True))
            pid = int(rec["pid"])
            check("detached and alive right after the shutdown", _raw_alive(pid))
            deadline = time.time() + 12  # watchdog ticks every 5 s past a 2 s budget
            while _raw_alive(pid) and time.time() < deadline:
                time.sleep(0.25)
            check("watchdog killed the detached run at the deadline (no server involved)", not _raw_alive(pid))
    finally:
        server.MAX_RUNTIME_SECONDS = saved


def test_ops_tools_runs_log_cancel() -> None:
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        async def scenario():
            task = asyncio.create_task(server._run_codex("watch me"))
            rec = await _wait_spawn(iso)
            await asyncio.sleep(0.4)
            runs = await server.codex_runs()
            log = await server.codex_run_log(run=rec["run"], lines=8)
            stopped = await server.codex_cancel_run(run=rec["run"])
            try:
                result = await task
            except asyncio.CancelledError:
                result = "CANCELLED"
            return rec, runs, log, stopped, result
        rec, runs, log, stopped, result = asyncio.run(scenario())
        pid = int(rec["pid"])
        check("codex_runs: RUNNING with pid and activity", "RUNNING" in runs and str(pid) in runs and "now:" in runs, runs)
        check("codex_run_log: header + digested lines", rec["run"] in log and ("thread started" in log or "codex pid" in log), log[:300])
        check("codex_cancel_run: reports the kill", "stopped" in stopped and str(pid) in stopped, stopped)
        check("process gone", not _raw_alive(pid))
        check("the attached call reports the signal kill", "killed by signal" in result, result[:300])
        after = asyncio.run(server.codex_runs())
        check("codex_runs afterwards: not RUNNING", "RUNNING" not in after.split(rec["run"], 1)[1].split("\n", 1)[0], after)
        check("codex_run_log with no run picks the most recent", rec["run"] in asyncio.run(server.codex_run_log()))
        check("codex_cancel_run with nothing live says so", "No running" in asyncio.run(server.codex_cancel_run()))


def test_shutdown_signal_handlers() -> None:
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        server._SHUTDOWN.clear()
        server._install_shutdown_handlers()
        h = signal.getsignal(signal.SIGTERM)
        check("handler installed for SIGTERM", callable(h))
        raised = False
        try:
            h(signal.SIGTERM, None)
        except KeyboardInterrupt:
            raised = True
        check("first signal → KeyboardInterrupt (normal cancel path)", raised)
        check("first signal → shutdown flag raised", server._SHUTDOWN.is_set())
        check("later signals ignored so the cleanup is not torn",
              signal.getsignal(signal.SIGTERM) is signal.SIG_IGN and signal.getsignal(signal.SIGINT) is signal.SIG_IGN)
        import threading
        check("no hard-exit backstop thread under tests (hard_exit defaults to False)",
              not any(t.name == "shutdown-backstop" for t in threading.enumerate()))
    finally:
        for s, old in saved.items():
            signal.signal(s, old)
        server._SHUTDOWN.clear()


def test_plugin_mcp_registration() -> None:
    cfg = json.loads((ROOT / "plugins" / "codex-oracle" / ".mcp.json").read_text())
    srv = cfg["mcpServers"]["codex-oracle"]
    check("command is python3 with an env override (python is ENOENT on stock macOS)",
          srv["command"] == "${CODEX_ORACLE_PYTHON:-python3}", srv["command"])
    check("launcher is the venv-bootstrapping run_server.py", srv["args"] == ["${CLAUDE_PLUGIN_ROOT}/run_server.py"])
    check("2h client timeout kept", srv.get("timeout") == 7200000)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = []
    for t in tests:
        t0 = time.time()
        try:
            t()
            print(f"  ok  {t.__name__} ({time.time() - t0:.1f}s)")
        except AssertionError:
            failed.append(t.__name__)
        except Exception as e:  # noqa: BLE001
            FAIL += 1
            failed.append(f"{t.__name__} ({type(e).__name__}: {e})")
            print(f"  ERROR in {t.__name__}: {type(e).__name__}: {e}")
    print(f"{'✓' if not failed else '✗'} detach: {PASS} passed, {FAIL} failed" + (f" — {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
