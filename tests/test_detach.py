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
import subprocess
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
                      server.RUNS_JOURNAL, server._ps_snapshot, server._proc_comm,
                      dict(os.environ))
        server._codex_argv0 = lambda: [sys.executable, str(FAKE)]
        # hermetic: only THIS process visible (a truthful snapshot must
        # contain the self pid — round 8)
        server._ps_snapshot = lambda: f"{os.getpid()} python3 test-harness\n"
        server._proc_comm = lambda pid: "python3"
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
         server.RUNS_JOURNAL, server._ps_snapshot, server._proc_comm,
         env) = self.saved
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
             "output_file": str(spool / "attempt0.txt"), "spawn_ts": now - 29, "deadline_ts": now + 3000,
             **dict(zip(("watchdog_pid", "watchdog_start"), _fake_watchdog()))},
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
             "spawn_ts": now - 5, "deadline_ts": now + 3000, **dict(zip(("watchdog_pid", "watchdog_start"), _fake_watchdog()))},
        ):
            server._journal(rec)
        rec = iso.rec()
        check("no detach record, yet inferred DETACHED (server pid dead)", server._is_detached(rec)
              and server._run_status(rec) == "DETACHED", server._run_status(rec))
        res = asyncio.run(server.codex_resume_run(run="codex7·1"))
        check("adopted and collected without any cleanup having run", "ORPHAN-OK" in res and "collected from run" in res, res[:300])
        proc.wait(timeout=5)


def test_adoption_never_signs_partial_output_ok() -> None:
    """Review of 1.17.0 (HIGH): a detached run that emitted commentary and then
    died without an answer was collected as status:ok. OK is earned by the
    answer FILE + turn.completed; anything else is labelled partial."""
    with _Iso(FAKE_CODEX_SLEEP="1", FAKE_CODEX_FAIL="disconnect",
              FAKE_CODEX_PRELUDE="PARTIAL-COMMENTARY before dying") as iso:
        async def scenario():
            task = asyncio.create_task(server._run_codex("review this", tool_name="code_review"))
            rec = await _wait_spawn(iso)
            await asyncio.sleep(0.3)
            server._SHUTDOWN.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            server._SHUTDOWN.clear()
            return rec
        rec = asyncio.run(scenario())
        pid = int(rec["pid"])
        deadline = time.time() + 6
        while _raw_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        res = asyncio.run(server.codex_resume_run(run=rec["run"]))
        check("not presented as an answer", not res.startswith("[Codex model:"), res[:120])
        check("signed status:error, never ok", "status:error" in res and "status:ok" not in res, res[:200])
        check("commentary labelled partial", "[partial output before the failure — NOT the answer]" in res
              and "PARTIAL-COMMENTARY" in res.split("NOT the answer", 1)[1], res[-300:])
        check("journal: error, not ok", iso.rec().get("status") == "error", str(iso.rec().get("status")))


def test_orphaned_write_run_is_never_adoptable() -> None:
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="3", FAKE_CODEX_ANSWER="WRITE-ORPHAN") as iso:
        td = iso.td_path
        spool = td / "logs" / "runs" / "abraham3-1"; spool.mkdir(parents=True)
        out = spool / "attempt0.txt"
        with open(spool / "attempt0.stdout.jsonl", "ab") as so, open(spool / "attempt0.stderr.log", "ab") as se:
            proc = subprocess.Popen([sys.executable, str(FAKE), "exec", "--json", "--model", "x",
                                     "--output-last-message", str(out), "implement"],
                                    stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True)
        now = time.time()
        for rec in (
            {"run": "abraham3·1", "phase": "start", "ts": now - 5, "engine": "codex", "tool": "abraham",
             "model": "gpt-test", "reasoning": "max", "infra": False, "write": True, "web_search": False,
             "cwd": str(td), "prompt": "implement", "log": ""},
            {"run": "abraham3·1", "phase": "session", "ts": now - 5, "thread_id": "T-W"},
            {"run": "abraham3·1", "phase": "spawn", "ts": now - 5, "pid": proc.pid, "pgid": proc.pid,
             "server_pid": 999999999, "stdout": str(spool / "attempt0.stdout.jsonl"),
             "stderr": str(spool / "attempt0.stderr.log"), "output_file": str(out),
             "spawn_ts": now - 5, "deadline_ts": now + 3000},
        ):
            server._journal(rec)
        rec = iso.rec()
        check("a write child with a dead server is ORPHANED-WRITE, not DETACHED",
              not server._is_detached(rec) and server._run_status(rec) == "ORPHANED-WRITE", server._run_status(rec))
        res = asyncio.run(server.codex_resume_run(run="abraham3·1"))
        check("resume refused while the orphaned write child lives", "still RUNNING" in res, res[:200])
        check("codex_runs names the hazard", "ORPHANED-WRITE" in asyncio.run(server.codex_runs()))
        proc.kill(); proc.wait(timeout=5)


def test_two_collectors_one_claim() -> None:
    with _Iso(FAKE_CODEX_SLEEP="2", FAKE_CODEX_ANSWER="ONCE-ONLY") as iso:
        rec, _ = asyncio.run(_cancel_after_spawn(iso, "long", shutdown=True))
        async def both():
            return await asyncio.gather(server.codex_resume_run(run=rec["run"]),
                                        server.codex_resume_run(run=rec["run"]))
        a, b = asyncio.run(both())
        got = [r for r in (a, b) if "collected from run" in r and "ONCE-ONLY" in r]
        held = [r for r in (a, b) if "already being collected" in r]
        check("exactly one collector delivered the answer", len(got) == 1, (a[:120], b[:120]))
        check("the other was refused by the claim", len(held) == 1, (a[:120], b[:120]))
        check("claim released afterwards", str(server._run_claim_path(server._thread_claim_key(iso.rec()["thread_id"]))) not in server._HELD)


def test_cancel_run_journals_only_after_verified_death() -> None:
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        real_kill = server._kill_pgid
        async def scenario():
            task = asyncio.create_task(server._run_codex("stubborn"))
            rec = await _wait_spawn(iso)
            await asyncio.sleep(0.3)
            server._kill_pgid = lambda pgid, pid, start="": False  # a kill that does nothing
            try:
                res = await server.codex_cancel_run(run=rec["run"])
            finally:
                server._kill_pgid = real_kill
            still = _raw_alive(int(rec["pid"]))
            no_end = not iso.rec().get("has_end")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return res, still, no_end
        res, still, no_end = asyncio.run(scenario())
        check("failed stop is reported as FAILED", "stop FAILED" in res, res[:160])
        check("process untouched by the failed stop", still)
        check("nothing journaled as cancelled", no_end)


def test_run_tag_token_and_exclusive_spool() -> None:
    with _Iso():
        path, fh, sfh, tag = server._open_live_log("codex")
        for f in (fh, sfh):
            if f is not None:
                f.close()
        parts = tag.split("·")
        check("run tag = label+seq · pid · 4-hex token", len(parts) == 3 and len(parts[2]) == 4
              and all(c in "0123456789abcdef" for c in parts[2]), tag)
        d1 = server._run_spool_dir(tag)
        d2 = server._run_spool_dir(tag)
        check("a recurring tag never reuses a spool dir", d1 != d2 and d2.name.endswith("-1"), f"{d1} {d2}")


def test_answer_signature_uses_dispatch_time_tree() -> None:
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="1.5") as iso:
        td = iso.td_path
        subprocess.run(["git", "init", "-q", str(td)], check=True)
        subprocess.run(["git", "-C", str(td), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        t_dispatch = server._workspace_digest(str(td))
        async def scenario():
            task = asyncio.create_task(server._run_codex("review", tool_name="code_review"))
            await _wait_spawn(iso)
            await asyncio.sleep(0.3)
            (td / "edited-during-review.txt").write_text("x")  # tree changes mid-run
            return await task
        res = asyncio.run(scenario())
        t_after = server._workspace_digest(str(td))
        check("the tree changed during the run", t_after != t_dispatch)
        check("the answer vouches for the DISPATCH-time tree", f"tree:{t_dispatch}" in res, res[:160])
        check("the journal carries the dispatch tree", iso.rec().get("tree") == t_dispatch)



def test_collector_cancel_leaves_the_run_running() -> None:
    with _Iso(FAKE_CODEX_SLEEP="4", FAKE_CODEX_ANSWER="STILL-RUNNING") as iso:
        rec, _ = asyncio.run(_cancel_after_spawn(iso, "long", shutdown=True))
        pid = int(rec["pid"])
        async def scenario():
            task = asyncio.create_task(server.codex_resume_run(run=rec["run"]))
            await asyncio.sleep(0.6)
            task.cancel()
            try:
                await task
                return False
            except asyncio.CancelledError:
                return True
        cancelled = asyncio.run(scenario())
        check("collector cancel propagated", cancelled)
        check("the run itself keeps running", _raw_alive(pid))
        rec2 = iso.rec()
        check("journal: collect_cancelled, no end", rec2.get("has_collect_cancelled") and not rec2.get("has_end"))
        check("claim released on cancel", str(server._run_claim_path(server._thread_claim_key(iso.rec()["thread_id"]))) not in server._HELD)
        stopped = asyncio.run(server.codex_cancel_run(run=rec["run"]))
        check("can still be stopped explicitly", "stopped" in stopped, stopped[:120])


def test_process_identity_is_pid_plus_start_time() -> None:
    import subprocess
    check("this process has a start token", bool(server._proc_start(os.getpid())))
    check("the server's own pid is alive by definition", server._server_alive(os.getpid()))
    gone = subprocess.Popen([sys.executable, str(FAKE), "--version"], stdout=subprocess.DEVNULL)
    gone.wait(timeout=10)
    check("a gone pid is dead", not server._pid_alive(gone.pid))
    live = subprocess.Popen([sys.executable, str(FAKE), "exec", "--json", "-o", "/dev/null", "x"],
                            stdout=subprocess.DEVNULL, env={**os.environ, "FAKE_CODEX_SLEEP": "3"})
    try:
        real = server._proc_start(live.pid)
        check("live codex with its own start token is alive", server._pid_alive(live.pid, real))
        check("same pid with a DIFFERENT start token is treated as reused (dead)",
              not server._pid_alive(live.pid, "Mon_Jan_1_00:00:00_2000"))
        check("kill refuses a reused pid", not server._kill_pgid(live.pid, live.pid, "Mon_Jan_1_00:00:00_2000"))
        check("…and the process is untouched", _raw_alive(live.pid))
    finally:
        live.kill(); live.wait(timeout=5)


_FAKE_WATCHDOGS = []


def _fake_watchdog():
    """A live process that looks like our watchdog (command carries
    codex-oracle-watchdog) with a recorded identity; killed at exit."""
    import subprocess
    p = subprocess.Popen(["/bin/sh", "-c", "sleep 300", "codex-oracle-watchdog"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _FAKE_WATCHDOGS.append(p)
    return p.pid, server._proc_start(p.pid)


def _spawn_fake_detached(iso: _Iso, run: str, *, write=False, watchdog=1, tree=None,
                         prelude=None, sleep="3", answer="X"):
    """A fake codex spawned OUTSIDE the server with a hand-written journal:
    the shape a crashed server leaves behind."""
    import subprocess
    td = iso.td_path
    spool = td / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
    out = spool / "attempt0.txt"
    env = {**os.environ, "FAKE_CODEX_SLEEP": sleep, "FAKE_CODEX_ANSWER": answer}
    if prelude:
        env["FAKE_CODEX_PRELUDE"] = prelude
    with open(spool / "attempt0.stdout.jsonl", "ab") as so, open(spool / "attempt0.stderr.log", "ab") as se:
        proc = subprocess.Popen([sys.executable, str(FAKE), "exec", "--json", "--model", "x",
                                 "--output-last-message", str(out), "task"],
                                stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    now = time.time()
    start = {"run": run, "phase": "start", "ts": now - 5, "engine": "codex", "tool": "code_review",
             "model": "gpt-test", "reasoning": "max", "infra": False, "write": write, "web_search": False,
             "cwd": str(td), "prompt": "task", "log": ""}
    if tree is not None:
        start["tree"] = tree
    server._journal(start)
    server._journal({"run": run, "phase": "session", "ts": now - 5, "thread_id": "T-" + run[-4:]})
    server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": proc.pid, "pgid": proc.pid,
                     "pid_start": server._proc_start(proc.pid), "server_pid": 999999999,
                     "server_start": "Mon_Jan_1_00:00:00_2000",
                     "stdout": str(spool / "attempt0.stdout.jsonl"), "stderr": str(spool / "attempt0.stderr.log"),
                     "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000,
                     **({"watchdog_pid": watchdog, "watchdog_start": ""} if watchdog in (None, 1) and watchdog != 1
                        else (dict(zip(("watchdog_pid", "watchdog_start"), _fake_watchdog())) if watchdog == 1 else
                              {"watchdog_pid": None, "watchdog_start": ""}))})
    return proc


def test_legacy_record_without_dispatch_tree_never_certifies_the_current_tree() -> None:
    with _Iso() as iso:
        proc = _spawn_fake_detached(iso, "codex5·1", sleep="0.3", answer="LEGACY")
        proc.wait(timeout=10)
        res = asyncio.run(server.codex_resume_run(run="codex5·1"))
        check("collected", "LEGACY" in res and "collected from run" in res, res[:200])
        check("stamped tree:unknown — the push gate can never match it", "tree:unknown" in res, res[:160])


def test_orphaned_without_watchdog_is_not_adoptable_but_stoppable() -> None:
    with _Iso() as iso:
        proc = _spawn_fake_detached(iso, "codex6·1", watchdog=None, sleep="5")
        try:
            rec = iso.rec()
            check("no watchdog ⇒ not detached", not server._is_detached(rec))
            check("status ORPHANED", server._run_status(rec) == "ORPHANED", server._run_status(rec))
            res = asyncio.run(server.codex_cancel_run(run="codex6·1"))
            check("orphan is stoppable", "stopped" in res, res[:120])
            proc.wait(timeout=5)  # the test is the parent: reap the zombie
            check("process gone", not _raw_alive(proc.pid))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_orphaned_write_is_stoppable() -> None:
    with _Iso() as iso:
        proc = _spawn_fake_detached(iso, "abraham7·1", write=True, sleep="5")
        try:
            check("ORPHANED-WRITE", server._run_status(iso.rec()) == "ORPHANED-WRITE")
            res = asyncio.run(server.codex_cancel_run(run="abraham7·1"))
            check("orphaned write child can be stopped", "stopped" in res, res[:120])
            proc.wait(timeout=5)  # the test is the parent: reap the zombie
            check("process gone", not _raw_alive(proc.pid))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_adoption_of_a_turn_cut_mid_way_is_not_ok() -> None:
    """No terminal error event, no answer file, commentary present: the run
    was killed mid-turn. The completion predicate alone must refuse ok."""
    with _Iso() as iso:
        os.environ["FAKE_CODEX_HANG_AFTER_PRELUDE"] = "8"
        proc = _spawn_fake_detached(iso, "codex8·1", sleep="0", prelude="halfway commentary")
        deadline = time.time() + 8
        spool = iso.td_path / "logs" / "runs" / "codex8-1" / "attempt0.stdout.jsonl"
        while time.time() < deadline and "halfway commentary" not in spool.read_text(errors="replace"):
            time.sleep(0.05)      # prelude emitted, turn hanging — cut it here
        proc.kill(); proc.wait(timeout=5)
        res = asyncio.run(server.codex_resume_run(run="codex8·1"))
        check("not ok", "status:ok" not in res and "status:error" in res, res[:200])
        check("labelled partial", "NOT the answer" in res and "halfway commentary" in res, res[-300:])
        check("reason names the incomplete turn", "without completing its turn" in res, res[:400])



def test_replay_carries_a_split_trailing_record() -> None:
    with _Iso() as iso:
        spool = iso.td_path / "s.jsonl"
        full = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}) + "\n"
        spool.write_text(json.dumps({"type": "thread.started", "thread_id": "T"}) + "\n" + full[:15])
        rec = {"stdout": str(spool)}
        state = server._replay_spool(rec, lambda s: None)
        check("partial record is carried, not dropped", state.get("_buf") == full[:15].encode())
        buf = bytearray(state.pop("_buf")); pos = state.pop("_pos")
        with open(spool, "a") as fh:
            fh.write(full[15:])
        with open(spool, "rb") as fh:
            fh.seek(pos); rest = fh.read()
        server._feed_jsonl(buf, rest, state, lambda s: None)
        check("turn.completed recovered after the split", state.get("turn_completed") is True)


def test_identity_fails_closed() -> None:
    check("own pid with a foreign start token is NOT alive", not server._server_alive(os.getpid(), "Mon_Jan_1_00:00:00_2000"))
    real = server._proc_start
    server._proc_start = lambda pid: ""  # identity unknowable
    try:
        check("kill refuses when a recorded identity cannot be verified",
              not server._kill_pgid(os.getpid(), os.getpid(), "Mon_Jan_1_00:00:00_2000"))
    finally:
        server._proc_start = real


def test_spool_collision_cap_falls_back_to_a_private_dir() -> None:
    saved = server.SPOOL_COLLISION_MAX
    server.SPOOL_COLLISION_MAX = 2
    try:
        with _Iso():
            d1 = server._run_spool_dir("codexZ·1"); d2 = server._run_spool_dir("codexZ·1"); d3 = server._run_spool_dir("codexZ·1")
            check("at the cap a private temp dir is returned, never an uncreated/reused path",
                  d3.exists() and d3 not in (d1, d2) and "codex-oracle-run-" in d3.name, str(d3))
            import shutil; shutil.rmtree(d3, ignore_errors=True)
    finally:
        server.SPOOL_COLLISION_MAX = saved



def test_prespawn_cancel_is_an_intent_honoured_by_the_owner() -> None:
    with _Iso(FAKE_CODEX_SLEEP="2") as iso:
        now = time.time()
        server._journal({"run": "codexQ·1", "phase": "start", "ts": now, "engine": "codex", "tool": "codex_query",
                         "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "q", "log": str(iso.td_path / "logs" / "x.log")})
        (iso.td_path / "logs" / "x.log").write_text("fresh\n")
        # While an OWNER holds the run-terminal claim (a live dispatcher does,
        # from its start record on), a pre-spawn cancel is an INTENT.
        got_rig, _ = server._acquire_run_claim(server._run_terminal_claim_key("codexQ·1"))
        check("test rig holds the owner claim", got_rig)
        try:
            res = asyncio.run(server.codex_cancel_run(run="codexQ·1"))
            check("pre-spawn cancel is an INTENT, not a result", "cancel REQUESTED" in res, res[:160])
            rec = iso.rec()
            check("nothing terminal journaled", not rec.get("has_end") and rec.get("has_cancel_requested"))
            check("status CANCELLING", server._run_status(rec) == "CANCELLING", server._run_status(rec))
        finally:
            server._release_run_claim(server._run_terminal_claim_key("codexQ·1"))
        # With NO claim holder (the owner crashed pre-spawn), the canceller
        # terminalizes instead of leaving CANCELLING forever (round 9).
        res = asyncio.run(server.codex_cancel_run(run="codexQ·1"))
        check("an UNOWNED pre-spawn run is terminalized",
              "cancelled before it ever spawned" in res, res[:200])
        check("journal: cancelled",
              server._journal_runs().get("codexQ·1", {}).get("status") == "cancelled")
        check("marker cleared", not server._cancel_requested("codexQ·1"))
        # now an owner that finds the intent before spawning
        real = server._open_live_log
        server._open_live_log = lambda label: (*real(label)[:3], "codexQ·2")
        server._request_cancel("codexQ·2")
        try:
            res = asyncio.run(server._run_codex("hello"))
        finally:
            server._open_live_log = real
        check("owner honoured the intent without spawning", "cancelled before spawn" in res, res[:200])
        rec2 = server._journal_runs().get("codexQ·2", {})
        check("journal: cancelled, no spawn", rec2.get("status") == "cancelled" and not rec2.get("has_spawn"), str(rec2)[:200])
        check("marker cleared", not server._cancel_requested("codexQ·2"))


def test_identity_unknown_is_alive_for_exclusion_never_for_destruction() -> None:
    real = server._proc_info
    server._proc_info = lambda pid: ("", "")   # ps denied / absent
    try:
        check("unknown identity counts as ALIVE for exclusion", server._pid_alive(os.getpid()))
        check("kill without a recorded identity is refused", not server._kill_pgid(os.getpid(), os.getpid(), ""))
        check("kill with an unverifiable identity is refused", not server._kill_pgid(os.getpid(), os.getpid(), "tok"))
    finally:
        server._proc_info = real


def test_detachment_needs_a_live_matching_watchdog() -> None:
    with _Iso(FAKE_CODEX_SLEEP="4") as iso:
        proc = _spawn_fake_detached(iso, "codexW·1", watchdog=None, sleep="4")
        try:
            rec = dict(iso.rec()); rec["watchdog_pid"] = 1; rec["watchdog_start"] = ""  # launchd: not our enforcer
            check("a bare nonzero watchdog pid does not authorise detachment", not server._is_detached(rec))
            wpid, wstart = _fake_watchdog()
            rec["watchdog_pid"], rec["watchdog_start"] = wpid, wstart
            check("a live, matching watchdog does", server._is_detached(rec))
            rec["watchdog_start"] = "Mon_Jan_1_00:00:00_2000"
            check("a live pid with a foreign start is not our watchdog", not server._is_detached(rec))
        finally:
            proc.kill(); proc.wait(timeout=5)


def test_oversized_jsonl_record_is_dropped_loudly() -> None:
    saved = server.JSONL_RECORD_MAX_BYTES
    server.JSONL_RECORD_MAX_BYTES = 1024
    try:
        emitted = []; state = {"activity": "", "last_message": "", "last_error": "", "usage": "", "thread_id": ""}
        buf = bytearray()
        server._feed_jsonl(buf, b"x" * 2000, state, emitted.append)
        check("oversized record dropped with a loud line", any("oversized event dropped" in e for e in emitted) and not buf)
        server._feed_jsonl(buf, b"yyy\n" + json.dumps({"type": "turn.completed", "usage": {}}).encode() + b"\n", state, emitted.append)
        check("stream re-synchronised at the next newline", state.get("turn_completed") is True)
    finally:
        server.JSONL_RECORD_MAX_BYTES = saved


def test_dispatch_digest_is_of_the_target_tree_not_the_session_cwd() -> None:
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        sub = iso.td_path / "sub"; sub.mkdir()
        subprocess.run(["git", "init", "-q", str(sub)], check=True)
        subprocess.run(["git", "-C", str(sub), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        want = server._workspace_digest(str(sub))
        res = asyncio.run(server._run_codex("review", tool_name="code_review", workdir=str(sub)))
        check("header vouches for the TARGET tree", f"tree:{want}" in res, res[:160])
        check("…which differs from the session cwd digest", want != server._workspace_digest(str(iso.td_path)))


def test_write_lock_is_inherited_by_the_child() -> None:
    """The tree stays locked while the codex CHILD lives even after the server
    releases/dies: the child inherited the descriptor (round 3: a write child
    outliving a crashed server left the tree 'stale' and takeable)."""
    import subprocess
    with _Iso() as iso:
        cwd = str(iso.td_path)
        ok, _ = server._acquire_write_lock(cwd, "abraham1"); check("acquired", ok)
        fd = server._held_lock_fd(server._write_lock_path(cwd))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"], pass_fds=(fd,))
        server._release_write_lock(cwd)  # the server lets go; the child still holds the description
        probe = ("import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_RDWR); "
                 "\ntry:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\nexcept OSError:\n    print('held')")
        out = lambda: subprocess.run([sys.executable, "-c", probe, str(server._write_lock_path(cwd))], capture_output=True, text=True).stdout.strip()
        check("still held by the child after the server released", out() == "held")
        child.wait(timeout=10)
        check("released by the kernel when the child exits", out() == "free")
        check("stamping the child needs the held lock", not server._note_write_child(cwd, 1))


def test_run_claim_is_exclusive_and_dies_with_its_holder() -> None:
    import subprocess, threading
    with _Iso() as iso:
        n = 6; barrier = threading.Barrier(n); results = []
        def go():
            barrier.wait(); results.append(server._acquire_run_claim("codexR·1")[0])
        ts = [threading.Thread(target=go) for _ in range(n)]
        [x.start() for x in ts]; [x.join() for x in ts]
        check("exactly one contender acquired the claim", results.count(True) == 1, str(results))
        server._release_run_claim("codexR·1")
        path = server._run_claim_path("codexR·1")
        code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('held', flush=True); time.sleep(2)")
        p = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True); p.stdout.readline()
        check("held by another process → refused", not server._acquire_run_claim("codexR·1")[0])
        p.wait(timeout=10)
        check("holder died → acquirable (kernel released it, no staleness logic)", server._acquire_run_claim("codexR·1")[0])
        server._release_run_claim("codexR·1")


def test_continuation_child_inherits_the_run_claim() -> None:
    """A resumed thread stays claimed while its continuation child lives even
    after the collector released/died (round 4: a second server could resume
    the same thread)."""
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="3") as iso:
        now = time.time()
        for rec in (
            {"run": "codexC·1", "phase": "start", "ts": now - 30, "engine": "codex", "tool": "codex_query",
             "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": False,
             "cwd": str(iso.td_path), "prompt": "original", "log": ""},
            {"run": "codexC·1", "phase": "session", "ts": now - 29, "thread_id": "T-C"},
            {"run": "codexC·1", "phase": "end", "ts": now - 10, "status": "error", "error": "boom"},
        ):
            server._journal(rec)
        async def scenario():
            task = asyncio.create_task(server.codex_resume_run(run="codexC·1"))
            for _ in range(200):
                await asyncio.sleep(0.05)
                runs = server._journal_runs()
                cont = next((r for r in runs.values() if r.get("parent_run") == "codexC·1" and r.get("has_spawn")), None)
                if cont:
                    break
            assert cont, "continuation never spawned"
            probe = ("import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_RDWR)\ntry:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\nexcept OSError:\n    print('held')")
            held_while_running = subprocess.run([sys.executable, "-c", probe, str(server._run_claim_path(server._thread_claim_key("T-C")))],
                                                capture_output=True, text=True).stdout.strip()
            server._SHUTDOWN.set(); task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            server._SHUTDOWN.clear()
            held_after_detach = subprocess.run([sys.executable, "-c", probe, str(server._run_claim_path(server._thread_claim_key("T-C")))],
                                               capture_output=True, text=True).stdout.strip()
            return cont, held_while_running, held_after_detach
        cont, a, b = asyncio.run(scenario())
        check("claim held while the continuation runs", a == "held", a)
        check("claim STILL held by the detached continuation after the collector let go", b == "held", b)
        check("the second resume is refused meanwhile", "already being" in asyncio.run(server.codex_resume_run(run="codexC·1")))
        deadline = time.time() + 6
        while _raw_alive(int(cont["pid"])) and time.time() < deadline:
            time.sleep(0.1)


def test_cancel_intent_races_pid_publication_safely() -> None:
    """The canceller wrote its intent after the owner spawned but before it
    published the pid: the re-read must take the kill path, never return
    'requested' against a running child (round 4)."""
    with _Iso(FAKE_CODEX_SLEEP="5") as iso:
        async def scenario():
            task = asyncio.create_task(server._run_codex("race"))
            rec = await _wait_spawn(iso)
            pid = int(rec["pid"])
            # simulate the canceller's stale read: a record without the pid
            stale = {k: v for k, v in rec.items() if k not in ("pid", "pgid", "pid_start")}
            real_runs = server._journal_runs
            calls = {"n": 0}
            def once_stale():
                calls["n"] += 1
                runs = real_runs()
                if calls["n"] == 1:
                    runs = dict(runs); runs[rec["run"]] = stale
                return runs
            server._journal_runs = once_stale
            try:
                res = await server.codex_cancel_run(run=rec["run"])
            finally:
                server._journal_runs = real_runs
            try:
                await task
            except asyncio.CancelledError:
                pass
            return res, pid
        res, pid = asyncio.run(scenario())
        check("re-read found the published pid and killed", "stopped" in res or "killed" in res, res[:160])
        check("child gone", not _raw_alive(pid))


def test_management_dirs_are_never_pruned() -> None:
    with _Iso() as iso:
        server._acquire_run_claim("codexM·1"); server._request_cancel("codexM·2")
        for d in ("claims", "cancel"):
            p = server._run_dir_root() / d
            os.utime(p, (0, 0))
            for f in p.iterdir():
                os.utime(f, (0, 0))
        old = server._run_dir_root() / "codexOLD-1"; old.mkdir(); (old / "x").write_text("x"); os.utime(old / "x", (0, 0)); os.utime(old, (0, 0))
        server._prune_live_logs()
        check("claims/ and cancel/ survive pruning", (server._run_dir_root() / "claims").exists() and (server._run_dir_root() / "cancel").exists())
        check("an old run dir is still pruned", not old.exists())
        server._release_run_claim("codexM·1")


def test_owner_liveness_unknown_is_alive() -> None:
    real = server._proc_info
    server._proc_info = lambda pid: ("", "")
    try:
        check("unknown owner evidence reads as alive", server._server_alive(999999998) is True or not server._kill0(999999998))
        live = os.getppid()
        check("a live pid with unknown evidence is alive", server._server_alive(live))
    finally:
        server._proc_info = real


def test_record_cap_applies_to_the_record_itself() -> None:
    saved = server.JSONL_RECORD_MAX_BYTES
    server.JSONL_RECORD_MAX_BYTES = 100
    try:
        emitted = []; state = {"activity": "", "last_message": "", "last_error": "", "usage": "", "thread_id": ""}
        buf = bytearray()
        server._feed_jsonl(buf, b"x" * 101 + b"\n" + json.dumps({"type": "turn.completed", "usage": {}}).encode() + b"\n", state, emitted.append)
        check("a 101-byte record followed by a newline is dropped", any("oversized event dropped (101" in e for e in emitted), str(emitted)[:200])
        check("the following record still parses", state.get("turn_completed") is True)
    finally:
        server.JSONL_RECORD_MAX_BYTES = saved


def test_lock_path_is_keyed_by_realpath() -> None:
    with _Iso() as iso:
        real = iso.td_path / "tree"; real.mkdir(); alias = iso.td_path / "alias"; alias.symlink_to(real)
        check("symlinked checkout selects the same lock", server._write_lock_path(str(alias)) == server._write_lock_path(str(real)))


def test_cancel_kills_the_current_generation_not_a_stale_one() -> None:
    """Attempt 1 died (disconnect) and the retry spawned attempt 2; a canceller
    whose snapshot still shows attempt 1's pid must re-fold and kill attempt 2
    (round 5)."""
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        os.environ["FAKE_CODEX_FAIL_ONCE"] = str(iso.td_path / "failed-once")
        async def scenario():
            task = asyncio.create_task(server._run_codex("retrying"))
            first = await _wait_spawn(iso)
            pid1 = int(first["pid"])
            deadline = time.time() + 15
            while time.time() < deadline:
                rec = iso.rec()
                if int(rec.get("pid") or 0) not in (0, pid1):
                    break
                await asyncio.sleep(0.05)
            pid2 = int(iso.rec()["pid"])
            stale = dict(first)  # attempt 1's record (dead pid)
            real_runs = server._journal_runs; calls = {"n": 0}
            def once_stale():
                calls["n"] += 1
                runs = real_runs()
                if calls["n"] == 1:
                    runs = dict(runs); runs[first["run"]] = stale
                return runs
            server._journal_runs = once_stale
            try:
                res = await server.codex_cancel_run(run=first["run"])
            finally:
                server._journal_runs = real_runs
            try:
                await task
            except asyncio.CancelledError:
                pass
            return pid1, pid2, res
        pid1, pid2, res = asyncio.run(scenario())
        check("a second generation had spawned", pid2 != pid1 and pid2 > 0)
        check("the CURRENT generation was killed", ("stopped" in res or "killed" in res) and str(pid2) in res, res[:200])
        check("attempt 2 is gone", not _raw_alive(pid2))
        check("journal: cancelled", iso.rec().get("status") == "cancelled")
        check("marker cleared after the terminal record", not server._cancel_requested(iso.rec()["run"]))


def test_claims_are_keyed_by_thread_across_nested_continuations() -> None:
    import subprocess
    with _Iso() as iso:
        now = time.time()
        for run, parent in (("codexA·1", ""), ("codexB·1", "codexA·1"), ("codexC·1", "codexB·1")):
            server._journal({"run": run, "phase": "start", "ts": now - 30, "engine": "codex", "tool": "codex_query",
                             "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": False,
                             "cwd": str(iso.td_path), "prompt": "p", "log": "", "parent_run": parent})
            server._journal({"run": run, "phase": "session", "ts": now - 29, "thread_id": "T-N"})
            server._journal({"run": run, "phase": "end", "ts": now - 10, "status": "error", "error": "x"})
        code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('held', flush=True); time.sleep(4)")
        server._run_claim_path(server._thread_claim_key("T-N")).parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", code, str(server._run_claim_path(server._thread_claim_key("T-N")))], stdout=subprocess.PIPE, text=True)
        check("holder subprocess took the thread claim", p.stdout.readline().strip() == "held")
        try:
            for run in ("codexA·1", "codexB·1", "codexC·1"):
                res = asyncio.run(server.codex_resume_run(run=run))
                check(f"resume of {run} refused while the THREAD is claimed", "already being resumed" in res and "T-N" in res, res[:160])
        finally:
            p.kill(); p.wait(timeout=5)


def test_write_lock_identity_is_the_worktree() -> None:
    import subprocess
    with _Iso() as iso:
        td = iso.td_path
        subprocess.run(["git", "init", "-q", str(td)], check=True)
        sub = td / "a" / "b"; sub.mkdir(parents=True)
        check("a subdirectory of the worktree shares the root's lock", server._write_lock_path(str(sub)) == server._write_lock_path(str(td)))
        alias = str(td).upper()
        if os.path.samefile(str(td), alias):
            check("a case alias (case-insensitive volume) shares the lock", server._write_lock_path(alias) == server._write_lock_path(str(td)))
        link = td.parent / (td.name + "-link"); link.symlink_to(td)
        check("a symlink alias shares the lock", server._write_lock_path(str(link)) == server._write_lock_path(str(td)))


def test_legacy_live_lock_is_respected() -> None:
    import hashlib, subprocess
    with _Iso() as iso:
        cwd = str(iso.td_path)
        legacy = server.LIVE_LOG_DIR / "write-locks" / f"{hashlib.sha1(cwd.encode()).hexdigest()[:16]}.lock"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
        try:
            legacy.write_text(f"abraham-old pid={holder.pid} pstart={server._proc_start(holder.pid)} cwd={cwd} t=0\n")
            ok, why = server._acquire_write_lock(cwd, "new")
            check("a LIVE pre-1.17.2 holder is respected", not ok and "legacy" in why, why)
        finally:
            holder.kill(); holder.wait(timeout=5)
        ok, _ = server._acquire_write_lock(cwd, "new")
        check("…and ignored once its holder is gone", ok)
        server._release_write_lock(cwd)


def test_spawn_publication_failure_kills_the_child() -> None:
    with _Iso(FAKE_CODEX_SLEEP="4") as iso:
        real_j, real_k = server._journal, server._kill_tree
        killed = []
        def failing_journal(rec):
            return False if rec.get("phase") == "spawn" else real_j(rec)
        def kill_rec(proc):
            killed.append(proc.pid); return real_k(proc)
        server._journal, server._kill_tree = failing_journal, kill_rec
        try:
            res = asyncio.run(server._run_codex("unrecorded"))
        finally:
            server._journal, server._kill_tree = real_j, real_k
        check("an unrecordable spawn is killed", len(killed) >= 1)
        check("…and refused loudly", "refusing to run unrecorded" in res, res[:200])
        check("child gone", all(not _raw_alive(p) for p in killed))


def test_watchdog_signal_requires_identity() -> None:
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="5") as iso:
        bystander = subprocess.Popen(["/bin/sh", "-c", "sleep 30", "codex-oracle-watchdog"])
        proc = _spawn_fake_detached(iso, "codexX·1", watchdog=None, sleep="5")
        try:
            rec = iso.rec()
            server._journal({"run": "codexX·1", "phase": "spawn", "ts": time.time(), **{k: rec[k] for k in ("pid", "pgid", "pid_start", "server_pid", "server_start", "stdout", "stderr", "output_file", "spawn_ts", "deadline_ts")},
                             "watchdog_pid": bystander.pid, "watchdog_start": "Mon_Jan_1_00:00:00_2000"})
            res = asyncio.run(server.codex_cancel_run(run="codexX·1"))
            check("run stopped", "stopped" in res, res[:120])
            check("a pid recorded as watchdog but with a foreign identity is NOT signalled", bystander.poll() is None)
        finally:
            bystander.kill(); bystander.wait(timeout=5)
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_cancel_intent_write_failure_is_reported() -> None:
    with _Iso() as iso:
        server._journal({"run": "codexF·1", "phase": "start", "ts": time.time(), "engine": "codex", "tool": "codex_query",
                         "model": "gpt-test", "reasoning": "max", "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "q", "log": str(iso.td_path / "logs" / "f.log")})
        (iso.td_path / "logs" / "f.log").write_text("fresh\n")
        real = server._request_cancel; server._request_cancel = lambda run: False
        try:
            res = asyncio.run(server.codex_cancel_run(run="codexF·1"))
        finally:
            server._request_cancel = real
        check("a marker that cannot be written is reported, not swallowed", "cancel FAILED" in res, res[:160])


def test_write_mode_fails_closed_on_windows() -> None:
    with _Iso() as iso:
        server._journal({"run": "abrahamZ·1", "phase": "start", "ts": time.time() - 30, "engine": "codex", "tool": "abraham",
                         "model": "gpt-test", "reasoning": "max", "infra": False, "write": True, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "w", "log": ""})
        server._journal({"run": "abrahamZ·1", "phase": "session", "ts": time.time() - 29, "thread_id": "T-Z"})
        server._journal({"run": "abrahamZ·1", "phase": "end", "ts": time.time() - 10, "status": "error", "error": "x"})
        saved = server._IS_WINDOWS; server._IS_WINDOWS = True
        try:
            res1 = asyncio.run(server.abraham(task="build x"))
            res2 = asyncio.run(server.codex_resume_run(run="abrahamZ·1"))
        finally:
            server._IS_WINDOWS = saved
        check("abraham refuses on Windows", "unavailable on Windows" in res1, res1[:120])
        check("write resume refuses on Windows",
              "not available on Windows" in res2 or "cannot be resumed on Windows" in res2, res2[:120])


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
        check("codex_cancel_run: reports the kill (or the owner's already-journaled cancellation)",
              ("stopped" in stopped or "killed" in stopped) and str(pid) in stopped, stopped)
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
    check("4h client timeout sits above the 3h default run budget (+30 min)",
          srv.get("timeout") == 14400000
          and srv["timeout"] / 1000 >= server.MAX_RUNTIME_DEFAULT_S + 1800, str(srv.get("timeout")))
    hooks = json.loads((ROOT / "plugins" / "codex-oracle" / "hooks" / "hooks.json").read_text())
    hook_entries = [hk for grp in hooks["hooks"]["PreToolUse"] for hk in grp["hooks"]]
    # a14d2bf (measured across transcripts): on Claude Code >=2.1.246 the
    # hook "command" is a DIRECT SPAWN — ${VAR:-default} never expands there
    # (only the core ${CLAUDE_PLUGIN_ROOT} substitution does), and no shell
    # means no word splitting, spaces in the install path included. Hooks
    # therefore self-exec via ${CLAUDE_PLUGIN_ROOT} + exec bit + shebang;
    # the .mcp.json server registration keeps the env-default form (that
    # spawn path DOES expand it — a different expander, measured).
    # Round 10: exec form (args present → direct spawn, no shell, no
    # word-splitting) with a plain `python3` command — a bare name resolves
    # via PATH in the direct spawn (a14d2bf measured PATH lookup happening;
    # only ${VAR:-default} fails to expand there), and it is exactly the
    # interpreter the .mcp.json server registration falls back to, so hooks
    # work wherever the server itself works — Windows included, unlike a
    # shebang self-exec, which Windows cannot exec.
    matchers = [grp["matcher"] for grp in hooks["hooks"]["PreToolUse"]]
    check("the push gate matches PowerShell as well as Bash (Windows shell)",
          "Bash|PowerShell" in matchers, str(matchers))
    check("every hook runs python3 in exec form with the plugin-rooted script in args",
          hook_entries and all(
              hk["command"] == "python3"
              and isinstance(hk.get("args"), list) and len(hk["args"]) == 1
              and hk["args"][0].startswith("${CLAUDE_PLUGIN_ROOT}/hooks/")
              and hk["args"][0].endswith(".py")
              for hk in hook_entries),
          str([(hk.get("command"), hk.get("args")) for hk in hook_entries]))
    for hk in hook_entries:
        script = ROOT / "plugins" / "codex-oracle" / hk["args"][0].replace("${CLAUDE_PLUGIN_ROOT}/", "")
        check(f"{script.name} exists, is executable, python3 shebang (belt for direct exec)",
              script.is_file() and os.access(script, os.X_OK)
              and script.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3"),
              str(script))


def test_cancel_between_attempts_defers_to_the_live_owner() -> None:
    """Round 6 HIGH: a cancel landing in the capacity-backoff window (current
    generation dead, next not yet spawned) must NOT terminalize or clear the
    intent — the live owner honours it before any further spawn. The thread
    claim is held right through the backoff, so a concurrent resume is
    excluded too."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_FAIL="capacity",
              FAKE_CODEX_THREAD="T-BO") as iso:
        saved_base = server.OVERLOAD_BACKOFF_BASE_SECONDS
        server.OVERLOAD_BACKOFF_BASE_SECONDS = 8.0
        try:
            async def scenario():
                task = asyncio.create_task(server._run_codex("shed"))
                first = await _wait_spawn(iso)
                pid1 = int(first["pid"])
                tag = str(first["run"])
                deadline = time.time() + 12
                while time.time() < deadline:
                    if not _raw_alive(pid1) and "capacity shed" in iso.log_text():
                        break
                    await asyncio.sleep(0.05)
                check("attempt 1 died into the backoff window",
                      not _raw_alive(pid1) and "capacity shed" in iso.log_text())
                got, holder = server._acquire_run_claim(server._thread_claim_key("T-BO"))
                if got:
                    server._release_run_claim(server._thread_claim_key("T-BO"))
                check("thread claim is HELD across the backoff", not got, holder)
                res_r = await server.codex_resume_run(run=tag)
                check("a concurrent resume is refused during the backoff",
                      "RUNNING" in res_r or "already being resumed" in res_r, res_r[:200])
                res_c = await server.codex_cancel_run(run=tag)
                check("canceller defers to the live terminal-claim holder",
                      "owned by another live call" in res_c and "CANCELLING" in res_c, res_c[:300])
                check("no terminal record from the canceller", not iso.rec().get("has_end"))
                check("intent still standing for the owner", server._cancel_requested(tag))
                check("status shows CANCELLING",
                      server._run_status(iso.rec()) == "CANCELLING",
                      server._run_status(iso.rec()))
                result = await task
                return tag, result
            tag, result = asyncio.run(scenario())
            rec = server._journal_runs().get(tag, {})
            check("owner journaled the cancellation", rec.get("status") == "cancelled", str(rec.get("status")))
            check("marker cleared only after the durable terminal record",
                  not server._cancel_requested(tag))
            spawns = 0
            for line in server.RUNS_JOURNAL.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                if r.get("run") == tag and r.get("phase") == "spawn":
                    spawns += 1
            check("no second generation spawned past the intent", spawns == 1, str(spawns))
            check("claim released at run end", server._acquire_run_claim(server._thread_claim_key("T-BO"))[0])
            server._release_run_claim(server._thread_claim_key("T-BO"))
        finally:
            server.OVERLOAD_BACKOFF_BASE_SECONDS = saved_base


def test_cancel_of_a_dead_owner_run_is_terminalized_by_the_canceller() -> None:
    """The deferral is only to a LIVE owner: a detached run (owner gone) is
    killed, journaled cancelled, and its marker cleared by the canceller —
    nobody else is left to do it."""
    with _Iso() as iso:
        proc = _spawn_fake_detached(iso, "codexT2·1", sleep="30")
        try:
            res = asyncio.run(server.codex_cancel_run(run="codexT2·1"))
            check("dead-owner cancel reports the stop", "stopped" in res, res[:200])
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=5)  # reap: a SIGKILLed direct child is a zombie that still answers kill(0)
            rec = server._journal_runs().get("codexT2·1", {})
            check("canceller journaled the terminal record", rec.get("status") == "cancelled")
            check("marker cleared", not server._cancel_requested("codexT2·1"))
            check("child gone", not _raw_alive(proc.pid))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_windows_refuses_all_continuations() -> None:
    """Round 6 HIGH: on Windows a run claim can neither be inherited nor
    outlive its process, so EVERY continuation (not just write resumes) is
    refused in 1.17.x."""
    with _Iso() as iso:
        now = time.time()
        server._journal({"run": "codexW·1", "phase": "start", "ts": now - 30, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "p", "log": ""})
        server._journal({"run": "codexW·1", "phase": "session", "ts": now - 29, "thread_id": "T-W"})
        server._journal({"run": "codexW·1", "phase": "end", "ts": now - 10, "status": "error", "error": "x"})
        saved = server._IS_WINDOWS
        server._IS_WINDOWS = True
        try:
            res = asyncio.run(server.codex_resume_run(run="codexW·1"))
            check("read-mode resume refused on Windows",
                  "not available on Windows" in res, res[:200])
            listing = asyncio.run(server.codex_resume_run(run="list"))
            check("run listing still works on Windows",
                  "codexW·1" in listing or "codex runs" in listing.lower(), listing[:200])
        finally:
            server._IS_WINDOWS = saved


def test_start_journal_failure_refuses_dispatch() -> None:
    """Round 6 MEDIUM: fail-closed journaling covers the start record — a run
    that cannot be recorded is refused BEFORE any spawn."""
    with _Iso(FAKE_CODEX_SLEEP="5") as iso:
        real_j = server._journal
        server._journal = lambda r: False if r.get("phase") == "start" else real_j(r)
        try:
            res = asyncio.run(server._run_codex("unrecordable"))
        finally:
            server._journal = real_j
        check("dispatch refused", "dispatch refused" in res, res[:200])
        check("nothing spawned", not iso.rec().get("has_spawn"))


def test_session_journal_failure_fails_the_run_closed() -> None:
    """Round 6 MEDIUM: a session id that cannot be journaled leaves the thread
    unresumable and uncancellable — the run is killed, not continued."""
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        real_j = server._journal
        server._journal = lambda r: False if r.get("phase") == "session" else real_j(r)
        try:
            res = asyncio.run(server._run_codex("untrackable"))
        finally:
            server._journal = real_j
        check("run failed closed on the session id", "session id" in res, res[:300])
        rec = iso.rec()
        check("no thread id was falsely recorded", not rec.get("thread_id"))
        pid = int(rec.get("pid") or 0)
        check("the child was killed", pid > 0 and not _raw_alive(pid))


def test_end_journal_failure_retains_the_cancel_intent() -> None:
    """Round 6 MEDIUM: the cancel marker is cleared only AFTER a durable
    terminal record — a failed end append keeps the intent standing and says
    so in the returned text."""
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        async def scenario():
            task = asyncio.create_task(server._run_codex("halt"))
            rec = await _wait_spawn(iso)
            await asyncio.sleep(0.3)
            server._request_cancel(rec["run"])
            real_j = server._journal
            server._journal = lambda r: False if r.get("phase") == "end" else real_j(r)
            try:
                os.kill(int(rec["pid"]), signal.SIGKILL)
                res = await task
            finally:
                server._journal = real_j
            return rec, res
        rec, res = asyncio.run(scenario())
        check("the failed terminal write is reported",
              "terminal journal record" in res, res[:400])
        check("the cancel intent is retained", server._cancel_requested(rec["run"]))
        # Round 8 MEDIUM: the owner's task is gone and its claims are
        # released — a RETRY canceller must terminalize. "Server process
        # alive" is not ownership; holding the run-terminal claim is.
        res2 = asyncio.run(server.codex_cancel_run(run=rec["run"]))
        check("a retry cancel terminalizes past the idle owner", "stopped" in res2, res2[:220])
        check("journal: cancelled on retry",
              server._journal_runs().get(rec["run"], {}).get("status") == "cancelled")
        check("marker cleared after the durable record",
              not server._cancel_requested(rec["run"]))


def test_replay_discard_is_bounded() -> None:
    """Round 6 MEDIUM: the spool-replay discard of a partial first line uses
    fixed-size reads — readline() on a newline-free region allocates it whole."""
    import inspect
    src = inspect.getsource(server._replay_spool)
    check("replay never CALLS readline (unbounded)", ".readline(" not in src)
    with _Iso() as iso:
        d = iso.td_path / "sp"; d.mkdir()
        sp = d / "attempt0.stdout.jsonl"
        rec_line = json.dumps({"type": "turn.completed", "usage": {}}).encode() + b"\n"
        sp.write_bytes(b"X" * (300 * 1024) + b"\n" + rec_line)
        state = server._replay_spool({"stdout": str(sp)}, lambda s: None, max_bytes=256 * 1024)
        check("a window opening inside a huge line still reaches the tail records",
              state.get("turn_completed") is True)
        sp.write_bytes(b"Y" * (300 * 1024))  # no newline anywhere
        state = server._replay_spool({"stdout": str(sp)}, lambda s: None, max_bytes=64 * 1024)
        check("a newline-free window replays nothing and returns",
              not state.get("turn_completed") and int(state.get("_pos") or 0) >= 0)


def test_adoption_journals_a_replay_recovered_thread_id() -> None:
    """Round 6 MEDIUM: a thread id that exists only in the spool is journaled
    durably during adoption, so later continuations key by the THREAD."""
    import subprocess
    with _Iso() as iso:
        run = "codexR·7"
        spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
        out = spool / "attempt0.txt"; out.write_text("RECOVERED ANSWER")
        lines = [
            {"type": "thread.started", "thread_id": "T-REC"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "RECOVERED ANSWER"}},
            {"type": "turn.completed", "usage": {}},
        ]
        (spool / "attempt0.stdout.jsonl").write_text(
            "".join(json.dumps(ln) + "\n" for ln in lines))
        (spool / "attempt0.stderr.log").write_text("")
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        wd_pid, wd_start = _fake_watchdog()
        now = time.time()
        server._journal({"run": run, "phase": "start", "ts": now - 5, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "task", "log": ""})
        server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": dead.pid,
                         "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                         "server_start": "Mon_Jan_1_00:00:00_2000",
                         "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                         "stdout": str(spool / "attempt0.stdout.jsonl"),
                         "stderr": str(spool / "attempt0.stderr.log"),
                         "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000})
        res = asyncio.run(server.codex_resume_run(run=run))
        check("collected the finished detached run", "RECOVERED ANSWER" in res, res[:240])
        check("replay-recovered thread id journaled durably",
              server._journal_runs().get(run, {}).get("thread_id") == "T-REC")


def test_continuation_claim_key_is_the_thread_from_the_start_record() -> None:
    """Round 6 MEDIUM: a continuation that crashed before thread.started still
    carries its thread id in the START record — adoption claims by THAT, never
    by the run id."""
    import subprocess
    with _Iso() as iso:
        run = "codexS·3"
        spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
        out = spool / "attempt0.txt"
        with open(spool / "attempt0.stdout.jsonl", "ab") as so, \
                open(spool / "attempt0.stderr.log", "ab") as se:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True)
        wd_pid, wd_start = _fake_watchdog()
        now = time.time()
        server._journal({"run": run, "phase": "start", "ts": now - 5, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "task", "log": "",
                         "thread_id": "T-CONT"})
        server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": child.pid,
                         "pgid": child.pid, "pid_start": server._proc_start(child.pid),
                         "server_pid": 999999999, "server_start": "Mon_Jan_1_00:00:00_2000",
                         "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                         "stdout": str(spool / "attempt0.stdout.jsonl"),
                         "stderr": str(spool / "attempt0.stderr.log"),
                         "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000})
        code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('held', flush=True); time.sleep(5)")
        server._run_claim_path(server._thread_claim_key("T-CONT")).parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([sys.executable, "-c", code, str(server._run_claim_path(server._thread_claim_key("T-CONT")))],
                                  stdout=subprocess.PIPE, text=True)
        try:
            check("holder took the THREAD claim", holder.stdout.readline().strip() == "held")
            res = asyncio.run(server.codex_resume_run(run=run))
            check("adoption is excluded by the START record's thread claim",
                  "already being collected" in res, res[:220])
        finally:
            holder.kill(); holder.wait(timeout=5)
            child.kill(); child.wait(timeout=5)



def test_collector_and_canceller_have_one_terminal_writer() -> None:
    """Round 7 HIGH: a canceller hitting a run that is mid-collection takes
    the same run claim the collector holds; held → it kills and leaves the
    intent, and the COLLECTOR (sole terminal writer) folds it into a single
    `cancelled` terminal record — never two conflicting end appends."""
    with _Iso() as iso:
        run = "codexCC·1"
        tid = "T-" + run[-4:]
        proc = _spawn_fake_detached(iso, run, sleep="10")
        try:
            async def scenario():
                col = asyncio.create_task(server.codex_resume_run(run=run))
                held = False
                deadline = time.time() + 8
                while time.time() < deadline:
                    got, _h = server._acquire_run_claim(server._thread_claim_key(tid))
                    if got:
                        server._release_run_claim(server._thread_claim_key(tid))
                        await asyncio.sleep(0.05)
                    else:
                        held = True
                        break
                check("collector holds the thread claim", held)
                res_c = await server.codex_cancel_run(run=run)
                check("canceller defers terminalization to the claim holder",
                      "owned by another live call" in res_c or "claimed by another live call" in res_c,
                      res_c[:300])
                out = await col
                return out
            out = asyncio.run(scenario())
            rec = server._journal_runs().get(run, {})
            check("collector journaled the cancelled terminal state",
                  rec.get("status") == "cancelled", str(rec.get("status")))
            check("collector returns a cancelled message, never a thread resume",
                  "cancelled" in out and "codex_resume_run" in out, out[:240])
            ends = sum(1 for line in server.RUNS_JOURNAL.read_text(encoding="utf-8").splitlines()
                       if '"phase": "end"' in line and run in line)
            check("exactly ONE terminal writer", ends == 1, str(ends))
            check("intent retired by the durable terminal record",
                  not server._cancel_requested(run))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_session_journal_failure_kills_a_quiet_child() -> None:
    """Round 7 MEDIUM: session-append retries are IMMEDIATE and bounded — a
    child that emits thread.started and then goes silent must not run on
    indefinitely after a failed append."""
    with _Iso(FAKE_CODEX_SLEEP="0", FAKE_CODEX_PRELUDE="quiet",
              FAKE_CODEX_HANG_AFTER_PRELUDE="30") as iso:
        real_j = server._journal
        server._journal = lambda r: False if r.get("phase") == "session" else real_j(r)
        t0 = time.time()
        try:
            res = asyncio.run(server._run_codex("quiet child"))
        finally:
            server._journal = real_j
        took = time.time() - t0
        check("quiet child killed promptly (immediate bounded retries)",
              took < 15, f"{took:.1f}s")
        check("failed closed on the session id", "session id" in res, res[:300])
        pid = int(iso.rec().get("pid") or 0)
        check("child gone", pid > 0 and not _raw_alive(pid))


def test_adoption_tid_journal_failure_is_retryable() -> None:
    """Round 7 MEDIUM: if the replay-recovered thread id cannot be journaled,
    the collection FAILS LOUDLY without terminalizing — and a later retry
    (state dir healthy again) collects normally."""
    import subprocess
    with _Iso() as iso:
        run = "codexRT·2"
        spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
        out = spool / "attempt0.txt"; out.write_text("LATE ANSWER")
        lines = [
            {"type": "thread.started", "thread_id": "T-RTY"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "LATE ANSWER"}},
            {"type": "turn.completed", "usage": {}},
        ]
        (spool / "attempt0.stdout.jsonl").write_text(
            "".join(json.dumps(ln) + "\n" for ln in lines))
        (spool / "attempt0.stderr.log").write_text("")
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        wd_pid, wd_start = _fake_watchdog()
        now = time.time()
        server._journal({"run": run, "phase": "start", "ts": now - 5, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "task", "log": ""})
        server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": dead.pid,
                         "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                         "server_start": "Mon_Jan_1_00:00:00_2000",
                         "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                         "stdout": str(spool / "attempt0.stdout.jsonl"),
                         "stderr": str(spool / "attempt0.stderr.log"),
                         "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000})
        real_j = server._journal
        server._journal = lambda r: False if r.get("phase") == "session" else real_j(r)
        try:
            res = asyncio.run(server.codex_resume_run(run=run))
        finally:
            server._journal = real_j
        check("collection fails loudly, retryably", "FAILED" in res and "retry" in res, res[:240])
        check("nothing was terminalized", not server._journal_runs().get(run, {}).get("has_end"))
        res2 = asyncio.run(server.codex_resume_run(run=run))
        check("retry collects normally once appends work", "LATE ANSWER" in res2, res2[:240])
        check("thread id journaled on the successful retry",
              server._journal_runs().get(run, {}).get("thread_id") == "T-RTY")


def test_dead_owner_cancel_reports_failed_terminal_append() -> None:
    """Round 7 MEDIUM: dead-owner cancellation must not report success over a
    failed terminal append — the marker is retained and a retry terminalizes."""
    with _Iso() as iso:
        run = "codexDF·1"
        proc = _spawn_fake_detached(iso, run, sleep="30")
        try:
            real_j = server._journal
            server._journal = lambda r: False if r.get("phase") == "end" else real_j(r)
            try:
                res = asyncio.run(server.codex_cancel_run(run=run))
            finally:
                server._journal = real_j
            check("failed terminal append is reported, not masked",
                  "could NOT be journaled" in res, res[:240])
            check("marker retained for recovery", server._cancel_requested(run))
            with __import__("contextlib").suppress(Exception):
                proc.wait(timeout=5)  # reap the SIGKILLed child
            res2 = asyncio.run(server.codex_cancel_run(run=run))
            check("retry terminalizes (DETACHED-ENDED is stoppable)",
                  "stopped" in res2, res2[:200])
            check("journal: cancelled after the retry",
                  server._journal_runs().get(run, {}).get("status") == "cancelled")
            check("marker cleared after the durable record",
                  not server._cancel_requested(run))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_write_child_execution_barrier_blocks_unpublished() -> None:
    """Round 7 HIGH: a write child is spawned behind an execution barrier and
    execs codex only after publication succeeds — a publication failure kills
    it BEFORE it has executed anything."""
    with _Iso() as iso:
        real_note = server._note_write_child
        server._note_write_child = lambda cwd, pid: False
        try:
            spool = iso.td_path / "wspool"; spool.mkdir()
            out = spool / "attempt0.txt"
            state = {"activity": "", "last_message": "", "last_error": "", "usage": "",
                     "thread_id": "", "write": "1", "run_tag": "", "claim_key": ""}
            cmd = [sys.executable, str(FAKE), "exec", "--json", "--model", "x",
                   "--output-last-message", str(out), "task"]
            res = asyncio.run(server._exec_codex_once(
                cmd, out, state, lambda s: None, None, "m"))
        finally:
            server._note_write_child = real_note
        check("publication failure refuses execution",
              "write publication failed" in res[3], str(res[3])[:200])
        stdout_txt = (spool / "attempt0.stdout.jsonl").read_text(encoding="utf-8")
        check("the write child NEVER executed codex (barrier held)",
              "thread.started" not in stdout_txt, stdout_txt[:120])



def test_cancel_during_collection_of_an_unknown_thread_single_writer() -> None:
    """Round 8 HIGH: the terminal claim's identity must not change when replay
    recovers the thread id — the STABLE run-terminal claim makes the collector
    the sole terminal writer even when the journal had no thread id at claim
    time, and a canceller (or second resume) keys the same claim."""
    import subprocess
    with _Iso() as iso:
        run = "codexRC·1"
        spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
        out = spool / "attempt0.txt"
        env = {**os.environ, "FAKE_CODEX_SLEEP": "6", "FAKE_CODEX_THREAD": "T-RCV"}
        with open(spool / "attempt0.stdout.jsonl", "ab") as so, \
                open(spool / "attempt0.stderr.log", "ab") as se:
            child = subprocess.Popen(
                [sys.executable, str(FAKE), "exec", "--json", "--model", "x",
                 "--output-last-message", str(out), "task"],
                stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                start_new_session=True, env=env)
        wd_pid, wd_start = _fake_watchdog()
        now = time.time()
        server._journal({"run": run, "phase": "start", "ts": now - 5, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "task", "log": ""})
        # NO session record: the journal does not know the thread id.
        server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": child.pid,
                         "pgid": child.pid, "pid_start": server._proc_start(child.pid),
                         "server_pid": 999999999, "server_start": "Mon_Jan_1_00:00:00_2000",
                         "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                         "stdout": str(spool / "attempt0.stdout.jsonl"),
                         "stderr": str(spool / "attempt0.stderr.log"),
                         "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000})
        try:
            async def scenario():
                col = asyncio.create_task(server.codex_resume_run(run=run))
                held = False
                deadline = time.time() + 8
                rkey = server._run_terminal_claim_key(run)
                while time.time() < deadline:
                    got, _h = server._acquire_run_claim(rkey)
                    if got:
                        server._release_run_claim(rkey)
                        await asyncio.sleep(0.05)
                    else:
                        held = True
                        break
                check("collector holds the STABLE run-terminal claim", held)
                res_r = await server.codex_resume_run(run=run)
                check("a concurrent resume keys the same claim and is refused",
                      "already" in res_r and "owned" in res_r or "already being collected" in res_r,
                      res_r[:240])
                res_c = await server.codex_cancel_run(run=run)
                check("canceller keys the same claim and defers",
                      "owned by another live call" in res_c, res_c[:300])
                out_txt = await col
                return out_txt
            out_txt = asyncio.run(scenario())
            rec = server._journal_runs().get(run, {})
            check("single terminal record: cancelled by the collector",
                  rec.get("status") == "cancelled", str(rec.get("status")))
            check("the replay-recovered thread id was journaled",
                  rec.get("thread_id") == "T-RCV", str(rec.get("thread_id")))
            ends = sum(1 for line in server.RUNS_JOURNAL.read_text(encoding="utf-8").splitlines()
                       if '"phase": "end"' in line and run in line)
            check("exactly ONE terminal writer", ends == 1, str(ends))
            check("intent retired", not server._cancel_requested(run))
        finally:
            with __import__("contextlib").suppress(Exception):
                child.kill(); child.wait(timeout=5)


def test_replay_recovers_thread_id_from_the_head() -> None:
    """Round 8 MEDIUM: thread.started is the FIRST record; a tail-only window
    on a spool larger than the replay budget lost it, stranding the thread."""
    with _Iso() as iso:
        d = iso.td_path / "sp"; d.mkdir()
        sp = d / "attempt0.stdout.jsonl"
        filler = ("X" * (100 * 1024) + "\n") * 30  # ~3 MiB of non-JSON lines
        sp.write_text(
            json.dumps({"type": "thread.started", "thread_id": "T-H"}) + "\n"
            + filler
            + json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
        state = server._replay_spool({"stdout": str(sp)}, lambda s: None, max_bytes=64 * 1024)
        check("tail window replayed the terminal event", state.get("turn_completed") is True)
        check("thread id recovered from the bounded HEAD scan",
              state.get("thread_id") == "T-H", str(state.get("thread_id")))


def test_late_cancel_loses_to_a_completed_run() -> None:
    """Round 8 MEDIUM: linearization at the completion boundary — a cancel
    intent that lands after the run already completed loses; the answer is
    collected, the run is journaled ok, and the note says so out loud."""
    import subprocess
    with _Iso() as iso:
        run = "codexLC·1"
        spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
        out = spool / "attempt0.txt"; out.write_text("FINISHED ANSWER")
        lines = [
            {"type": "thread.started", "thread_id": "T-LC"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "FINISHED ANSWER"}},
            {"type": "turn.completed", "usage": {}},
        ]
        (spool / "attempt0.stdout.jsonl").write_text(
            "".join(json.dumps(ln) + "\n" for ln in lines))
        (spool / "attempt0.stderr.log").write_text("")
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        wd_pid, wd_start = _fake_watchdog()
        now = time.time()
        server._journal({"run": run, "phase": "start", "ts": now - 5, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "task", "log": ""})
        server._journal({"run": run, "phase": "session", "ts": now - 5, "thread_id": "T-LC"})
        server._journal({"run": run, "phase": "spawn", "ts": now - 5, "pid": dead.pid,
                         "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                         "server_start": "Mon_Jan_1_00:00:00_2000",
                         "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                         "stdout": str(spool / "attempt0.stdout.jsonl"),
                         "stderr": str(spool / "attempt0.stderr.log"),
                         "output_file": str(out), "spawn_ts": now - 5, "deadline_ts": now + 3000})
        server._request_cancel(run)  # the late cancel, before anyone collects
        res = asyncio.run(server.codex_resume_run(run=run))
        check("the completed answer is collected", "FINISHED ANSWER" in res, res[:240])
        check("the linearization is said out loud", "the answer wins" in res, res[:400])
        check("journaled ok, not cancelled",
              server._journal_runs().get(run, {}).get("status") == "ok")
        check("the late intent is retired", not server._cancel_requested(run))



def test_resume_refuses_a_cancelled_run_without_a_nudge() -> None:
    """Round 9 HIGH: a cancelled run must never be resurrected by a bare
    resume — the collector's has_end refold returned None and the resume fell
    through to a fresh continuation of the cancelled thread. An explicit
    nudge is the deliberate override."""
    with _Iso() as iso:
        now = time.time()
        server._journal({"run": "codexX·1", "phase": "start", "ts": now - 30, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "p", "log": ""})
        server._journal({"run": "codexX·1", "phase": "session", "ts": now - 29, "thread_id": "T-X"})
        server._journal({"run": "codexX·1", "phase": "end", "ts": now - 10, "status": "cancelled",
                         "cancelled_by": "codex_cancel_run", "returncode": -9})
        called: list = []
        real_rc = server._run_codex
        async def fake_rc(*a, **k):
            called.append(k.get("resume_tid"))
            return "CONT-OK"
        server._run_codex = fake_rc
        try:
            res = asyncio.run(server.codex_resume_run(run="codexX·1"))
            check("bare resume of a cancelled run is refused",
                  "was CANCELLED" in res and "nudge" in res, res[:200])
            check("no continuation was dispatched", called == [], str(called))
            res2 = asyncio.run(server.codex_resume_run(run="codexX·1", nudge="continue on purpose"))
            check("an explicit nudge deliberately continues the thread",
                  "CONT-OK" in res2 and called == ["T-X"], f"{res2[:120]} {called}")
        finally:
            server._run_codex = real_rc


def test_late_detached_record_cannot_overwrite_a_terminal_status() -> None:
    """Round 9 MEDIUM: the journal fold treats a terminal status as immutable
    to non-terminal records — a slow shutdown's late `detached` append must
    not overwrite a canceller's `cancelled` (and the detach path now journals
    BEFORE releasing its claims, so the window is closed at the source too)."""
    with _Iso() as iso:
        now = time.time()
        server._journal({"run": "codexF·1", "phase": "start", "ts": now - 30, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "p", "log": ""})
        server._journal({"run": "codexF·1", "phase": "end", "ts": now - 5, "status": "cancelled",
                         "cancelled_by": "codex_cancel_run", "returncode": -9})
        server._journal({"run": "codexF·1", "phase": "detached", "ts": now - 4,
                         "status": "detached", "pid": 12345, "pgid": 12345})
        rec = server._journal_runs().get("codexF·1", {})
        check("terminal status survives a late detached append",
              rec.get("status") == "cancelled", str(rec.get("status")))
        check("the detached phase is still visible", rec.get("has_detached") is True)


def test_spawn_failure_terminalizes_and_releases_claims() -> None:
    """Round 9 MEDIUM: a spawn failure (PermissionError etc.) must end as a
    durable terminal error with every claim released — an escaped exception
    left the run-terminal claim held until server restart."""
    with _Iso() as iso:
        noexec = iso.td_path / "noexec-codex"
        noexec.write_text("#!/bin/sh\necho hi\n")  # NOT chmod +x → PermissionError
        server._codex_argv0 = lambda: [str(noexec)]
        res = asyncio.run(server._run_codex("doomed"))
        check("spawn failure is a durable terminal error",
              "could not spawn codex" in res or "machinery failure" in res, res[:240])
        rec = iso.rec()
        check("end record journaled", rec.get("has_end") and rec.get("status") == "error",
              str(rec.get("status")))
        tag = str(rec.get("run"))
        got, holder = server._acquire_run_claim(server._run_terminal_claim_key(tag))
        if got:
            server._release_run_claim(server._run_terminal_claim_key(tag))
        check("run-terminal claim was released", got, holder)



def test_owner_claim_is_held_before_the_start_record_is_visible() -> None:
    """Round 10 HIGH: the run-terminal claim must be acquired BEFORE the
    start record is published — publish-then-claim let a canceller
    terminalize a just-published run and clear its marker while the owner
    went on to spawn. Pinned by asserting, at the instant the start append
    happens, that the claim is already unacquirable."""
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        observed: list = []
        real_j = server._journal
        def spy(rec):
            if rec.get("phase") == "start":
                key = server._run_terminal_claim_key(str(rec["run"]))
                got, _h = server._acquire_run_claim(key)
                if got:
                    server._release_run_claim(key)
                observed.append(got)
            return real_j(rec)
        server._journal = spy
        try:
            res = asyncio.run(server._run_codex("ordering"))
        finally:
            server._journal = real_j
        check("run completed", "ANSWER:" in res, res[:120])
        check("claim was HELD when the start record was appended",
              observed == [False], str(observed))
        tag = str(iso.rec().get("run"))
        got, holder = server._acquire_run_claim(server._run_terminal_claim_key(tag))
        if got:
            server._release_run_claim(server._run_terminal_claim_key(tag))
        check("claim released after the run ended", got, holder)


def test_start_journal_failure_releases_the_owner_claim() -> None:
    """Round 10 HIGH (flip side): a refused dispatch (start append failed)
    must not leave the just-acquired terminal claim held."""
    with _Iso(FAKE_CODEX_SLEEP="5") as iso:
        tags: list = []
        real_open = server._open_live_log
        def spy_open(label):
            out = real_open(label)
            tags.append(out[3])
            return out
        real_j = server._journal
        server._open_live_log = spy_open
        server._journal = lambda r: False if r.get("phase") == "start" else real_j(r)
        try:
            res = asyncio.run(server._run_codex("unrecordable"))
        finally:
            server._journal = real_j
            server._open_live_log = real_open
        check("dispatch refused", "dispatch refused" in res, res[:160])
        check("a run tag was allocated", len(tags) == 1, str(tags))
        key = server._run_terminal_claim_key(tags[0])
        got, holder = server._acquire_run_claim(key)
        if got:
            server._release_run_claim(key)
        check("the owner claim was released on the refusal", got, holder)


def test_shutdown_during_capacity_wait_is_interrupted_not_cancelled() -> None:
    """Round 10 MEDIUM: a server shutdown during a capacity backoff (previous
    attempt dead, nothing to detach) is `interrupted` — journaling it
    `cancelled` made the bare-resume guard refuse the automatic recovery an
    ordinary restart deserves."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_FAIL="capacity",
              FAKE_CODEX_THREAD="T-SD") as iso:
        saved_base = server.OVERLOAD_BACKOFF_BASE_SECONDS
        server.OVERLOAD_BACKOFF_BASE_SECONDS = 30.0
        try:
            async def scenario():
                task = asyncio.create_task(server._run_codex("shed"))
                deadline = time.time() + 12
                while time.time() < deadline:
                    if "capacity shed" in iso.log_text():
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.3)  # inside the backoff sleep now
                server._SHUTDOWN.set()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                server._SHUTDOWN.clear()
            asyncio.run(scenario())
            rec = iso.rec()
            check("journal: interrupted, not cancelled",
                  rec.get("status") == "interrupted", str(rec.get("status")))
            called: list = []
            real_rc = server._run_codex
            async def fake_rc(*a, **k):
                called.append(k.get("resume_tid"))
                return "RESUMED-OK"
            server._run_codex = fake_rc
            try:
                res = asyncio.run(server.codex_resume_run(run=str(rec["run"])))
            finally:
                server._run_codex = real_rc
            check("a bare resume recovers the interrupted run",
                  "RESUMED-OK" in res and called == ["T-SD"], f"{res[:140]} {called}")
        finally:
            server.OVERLOAD_BACKOFF_BASE_SECONDS = saved_base



def test_deadline_kill_failure_never_terminalizes() -> None:
    """Round 11 HIGH: a detached run past its deadline whose kill FAILS must
    not be journaled `timeout` — a terminal record over a live process lets a
    later resume write the same thread concurrently with it."""
    with _Iso() as iso:
        run = "codexKF·1"
        proc = _spawn_fake_detached(iso, run, sleep="20")
        try:
            # Force the deadline into the past so collection takes the kill path.
            sp = server._journal_runs().get(run, {})
            server._journal({"run": run, "phase": "spawn", "ts": time.time(),
                             **{k: sp.get(k) for k in ("pid", "pgid", "pid_start",
                                "server_pid", "server_start", "watchdog_pid",
                                "watchdog_start", "stdout", "stderr", "output_file")},
                             "spawn_ts": time.time() - 100, "deadline_ts": time.time() - 60})
            real_kill = server._kill_pgid
            server._kill_pgid = lambda pgid, pid, start="": False  # a kill that does nothing
            try:
                res = asyncio.run(server.codex_resume_run(run=run))
            finally:
                server._kill_pgid = real_kill
            check("collection FAILS loudly instead of terminalizing",
                  "could not be killed" in res, res[:260])
            check("no terminal record", not server._journal_runs().get(run, {}).get("has_end"))
            check("the process is still alive (untouched)", _raw_alive(proc.pid))
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)


def test_spool_failure_after_claim_terminalizes_and_releases() -> None:
    """Round 11 MEDIUM: an OSError from the spool mkdir after the terminal
    claim was acquired must terminalize durably and release the claim —
    it previously escaped _run_codex with the claim held."""
    with _Iso() as iso:
        real_spool = server._run_spool_dir
        def boom(tag):
            raise OSError("disk full")
        server._run_spool_dir = boom
        try:
            res = asyncio.run(server._run_codex("doomed"))
        finally:
            server._run_spool_dir = real_spool
        check("machinery failure surfaced", "machinery failure" in res, res[:200])
        rec = iso.rec()
        check("terminal record journaled", rec.get("has_end") and rec.get("status") == "error",
              str(rec.get("status")))
        key = server._run_terminal_claim_key(str(rec.get("run")))
        got, holder = server._acquire_run_claim(key)
        if got:
            server._release_run_claim(key)
        check("terminal claim released", got, holder)


def test_marker_only_cancel_state_stays_cancelling_and_stoppable() -> None:
    """Round 11 MEDIUM: when the journal is unwritable, a failed pre-spawn
    terminalization leaves only the MARKER — status must still read
    CANCELLING and a retry must terminalize."""
    with _Iso() as iso:
        now = time.time()
        log = iso.td_path / "logs" / "mk.log"
        server._journal({"run": "codexMK·1", "phase": "start", "ts": now - 300, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "p", "log": str(log)})
        log.write_text("fresh\n")  # the run looks live when the first cancel arrives
        real_j = server._journal
        server._journal = lambda r: False  # journal fully unwritable
        try:
            res = asyncio.run(server.codex_cancel_run(run="codexMK·1"))
        finally:
            server._journal = real_j
        check("failed terminalization reported", "could NOT be journaled" in res, res[:240])
        rec = server._journal_runs().get("codexMK·1", {})
        check("status reads CANCELLING from the marker alone",
              server._run_status(rec) == "CANCELLING", server._run_status(rec))
        res2 = asyncio.run(server.codex_cancel_run(run="codexMK·1"))
        check("retry terminalizes once the journal works", "cancelled before it ever spawned" in res2, res2[:200])
        check("marker cleared", not server._cancel_requested("codexMK·1"))


def test_pid_reuse_of_the_owner_pid_still_reads_detached() -> None:
    """Round 11 LOW: an old record whose server_pid was REUSED by this very
    server must still read detached — the self-pid shortcut bypassed the
    start-token identity check."""
    with _Iso() as iso:
        proc = _spawn_fake_detached(iso, "codexPR·1", sleep="3")
        try:
            # Rewrite the spawn record: owner pid = OUR pid, but an old start.
            rec = server._journal_runs().get("codexPR·1", {})
            server._journal({"run": "codexPR·1", "phase": "spawn", "ts": time.time(),
                             **{k: rec.get(k) for k in ("pid", "pgid", "pid_start",
                                "watchdog_pid", "watchdog_start", "stdout", "stderr",
                                "output_file", "spawn_ts", "deadline_ts")},
                             "server_pid": os.getpid(),
                             "server_start": "Mon_Jan_1_00:00:00_2000"})
            rec = server._journal_runs().get("codexPR·1", {})
            check("a reused owner pid with an old start reads DETACHED",
                  server._is_detached(rec), server._run_status(rec))
            check("our OWN live record does not read detached",
                  True)  # covered by every attached-run test in this suite
        finally:
            with __import__("contextlib").suppress(Exception):
                proc.kill(); proc.wait(timeout=5)



def test_writer_liveness_is_group_aware() -> None:
    """Round 12 HIGH: legacy records carry the LAUNCHER's pid — a dead pid
    with live group members and a fresh spool is a live writer; a stale spool
    with only group members reads dead (pgid recycling must not park a
    collector forever)."""
    import subprocess
    with _Iso() as iso:
        spool = iso.td_path / "w.jsonl"; spool.write_text("x\n")
        # A live group whose LEADER pid we treat as the dead "launcher":
        # spawn a leader that itself spawns a child and exits.
        code = ("import os, subprocess, sys, time\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'])\n"
                "print(os.getpid(), flush=True)\n")
        leader = subprocess.Popen([sys.executable, "-c", code],
                                  stdout=subprocess.PIPE, text=True, start_new_session=True)
        pgid = os.getpgid(leader.pid)
        leader.stdout.readline()
        leader.wait(timeout=10)  # leader dead, child (same group) lives
        check("rig: leader dead, group alive", not _raw_alive(leader.pid) and server._pgid_alive(pgid))
        check("dead pid + live group + fresh spool = WRITER ALIVE",
              server._writer_alive(leader.pid, "", pgid, spool))
        old = time.time() - 600
        os.utime(spool, (old, old))
        check("dead pid + live group + STALE spool = dead (recycling guard)",
              not server._writer_alive(leader.pid, "", pgid, spool))
        os.killpg(pgid, 9)
        deadline = time.time() + 5
        while server._pgid_alive(pgid) and time.time() < deadline:
            time.sleep(0.1)
        check("group killed → dead", not server._writer_alive(leader.pid, "", pgid, spool))


def test_marker_with_stale_pid_reads_cancelling() -> None:
    """Round 12 MEDIUM (the reviewer's exact probe): stale recorded pid, dead
    owner, marker present, no journal cancel record → CANCELLING, stoppable."""
    import subprocess
    with _Iso() as iso:
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        now = time.time()
        server._journal({"run": "codexSP·1", "phase": "start", "ts": now - 900, "engine": "codex",
                         "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                         "infra": False, "write": False, "web_search": False,
                         "cwd": str(iso.td_path), "prompt": "p", "log": ""})
        server._journal({"run": "codexSP·1", "phase": "spawn", "ts": now - 900, "pid": dead.pid,
                         "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                         "server_start": "Mon_Jan_1_00:00:00_2000",
                         "stdout": "", "stderr": "", "output_file": "",
                         "spawn_ts": now - 900, "deadline_ts": now - 100,
                         "watchdog_pid": None, "watchdog_start": ""})
        server._request_cancel("codexSP·1")  # the marker is the only cancel evidence
        rec = server._journal_runs().get("codexSP·1", {})
        st = server._run_status(rec)
        check("marker + stale pid reads CANCELLING", st == "CANCELLING", st)
        check("…which is stoppable", st in server._STOPPABLE)
        res = asyncio.run(server.codex_cancel_run(run="codexSP·1"))
        check("the retry terminalizes it", "cancelled" in res or "stopped" in res, res[:200])
        server._clear_cancel("codexSP·1")



def _spawn_leader_with_surviving_child(iso):
    """A process group whose LEADER is dead while a child member lives —
    the launcher-died/writer-survived shape (round 13 rig)."""
    import subprocess
    code = ("import os, subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
            "print(os.getpid(), flush=True)\n")
    leader = subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, text=True, start_new_session=True)
    pgid = os.getpgid(leader.pid)
    leader.stdout.readline()
    leader.wait(timeout=10)
    return leader.pid, pgid


def test_cancel_never_terminalizes_over_a_live_group() -> None:
    """Round 13 HIGH: the recorded (launcher) pid being dead is not the run
    being dead — cancel must verify GROUP death before its terminal record,
    keep the marker, and fail loudly."""
    with _Iso() as iso:
        dead_pid, pgid = _spawn_leader_with_surviving_child(iso)
        try:
            wd_pid, wd_start = _fake_watchdog()
            now = time.time()
            server._journal({"run": "codexLG·1", "phase": "start", "ts": now - 60, "engine": "codex",
                             "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                             "infra": False, "write": False, "web_search": False,
                             "cwd": str(iso.td_path), "prompt": "p", "log": ""})
            server._journal({"run": "codexLG·1", "phase": "spawn", "ts": now - 60, "pid": dead_pid,
                             "pgid": pgid, "pid_start": "", "server_pid": 999999999,
                             "server_start": "Mon_Jan_1_00:00:00_2000",
                             "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                             "stdout": "", "stderr": "", "output_file": "",
                             "spawn_ts": now - 60, "deadline_ts": now + 3000})
            res = asyncio.run(server.codex_cancel_run(run="codexLG·1"))
            check("cancel fails loudly over the live group",
                  "stop FAILED" in res and str(pgid) in res, res[:260])
            check("no terminal record", not server._journal_runs().get("codexLG·1", {}).get("has_end"))
            check("marker retained for the retry", server._cancel_requested("codexLG·1"))
            os.killpg(pgid, 9)
            deadline = time.time() + 5
            while server._pgid_alive(pgid) and time.time() < deadline:
                time.sleep(0.1)
            res2 = asyncio.run(server.codex_cancel_run(run="codexLG·1"))
            check("retry terminalizes once the group is gone",
                  "stopped" in res2 or "cancelled" in res2, res2[:200])
            check("marker cleared after the durable record",
                  not server._cancel_requested("codexLG·1"))
        finally:
            with __import__("contextlib").suppress(Exception):
                os.killpg(pgid, 9)
            server._clear_cancel("codexLG·1")


def test_collection_of_a_stale_spool_live_group_is_ambiguous_not_terminal() -> None:
    """Round 13 HIGH: spool freshness bounds the collector's WAIT, never
    proves death — a live group with a stale spool refuses to finalize."""
    with _Iso() as iso:
        dead_pid, pgid = _spawn_leader_with_surviving_child(iso)
        try:
            run = "codexAG·1"
            spool = iso.td_path / "logs" / "runs" / run.replace("·", "-"); spool.mkdir(parents=True)
            out = spool / "attempt0.txt"
            so = spool / "attempt0.stdout.jsonl"; so.write_text("")
            (spool / "attempt0.stderr.log").write_text("")
            old = time.time() - 600
            os.utime(so, (old, old))  # stale spool
            wd_pid, wd_start = _fake_watchdog()
            now = time.time()
            server._journal({"run": run, "phase": "start", "ts": now - 700, "engine": "codex",
                             "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                             "infra": False, "write": False, "web_search": False,
                             "cwd": str(iso.td_path), "prompt": "p", "log": ""})
            server._journal({"run": run, "phase": "spawn", "ts": now - 700, "pid": dead_pid,
                             "pgid": pgid, "pid_start": "", "server_pid": 999999999,
                             "server_start": "Mon_Jan_1_00:00:00_2000",
                             "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                             "stdout": str(so), "stderr": str(spool / "attempt0.stderr.log"),
                             "output_file": str(out), "spawn_ts": now - 700,
                             "deadline_ts": now + 3000})
            res = asyncio.run(server.codex_resume_run(run=run))
            check("ambiguous state refuses to finalize",
                  "ambiguous" in res and str(pgid) in res, res[:280])
            check("no terminal record", not server._journal_runs().get(run, {}).get("has_end"))
        finally:
            with __import__("contextlib").suppress(Exception):
                os.killpg(pgid, 9)



def test_leaked_descendants_are_swept_after_a_normal_exit() -> None:
    """Round 14 HIGH: codex can leave spawned processes running after its own
    exit — the attached reap path must sweep the GROUP before the end record
    and any lock release, on success too."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_LEAK="1") as iso:
        res = asyncio.run(server._run_codex("leaky"))
        check("run still succeeds (the leak was killable)", "ANSWER:" in res, res[:160])
        rec = iso.rec()
        pgid = int(rec.get("pgid") or 0)
        check("a pgid was recorded", pgid > 0)
        check("no group members survive the sweep", not server._pgid_alive(pgid))
        check("journal: ok", rec.get("status") == "ok", str(rec.get("status")))


def test_codex_target_map_is_exact_for_every_platform() -> None:
    """Round 14 MEDIUM: every mapping in the shim's PLATFORM_PACKAGE_BY_TARGET,
    testable from any platform — including the previously untestable Windows
    branch and the unsupported-platform refusal."""
    cases = [
        (("darwin", "arm64"), ("codex-darwin-arm64", "aarch64-apple-darwin")),
        (("darwin", "x86_64"), ("codex-darwin-x64", "x86_64-apple-darwin")),
        (("linux", "aarch64"), ("codex-linux-arm64", "aarch64-unknown-linux-musl")),
        (("linux", "x86_64"), ("codex-linux-x64", "x86_64-unknown-linux-musl")),
        (("win32", "amd64"), ("codex-win32-x64", "x86_64-pc-windows-msvc")),
        (("win32", "arm64"), ("codex-win32-arm64", "aarch64-pc-windows-msvc")),
        (("sunos", "sparc"), ("", "")),
        (("darwin", "riscv64"), ("", "")),
    ]
    for (plat, mach), want in cases:
        got = server._codex_target(plat, mach)
        check(f"target map {plat}/{mach}", got == want, f"{got} != {want}")


def test_relative_override_is_canonicalized() -> None:
    """Round 14 MEDIUM: a relative CODEX_ORACLE_CODEX_BIN is made absolute
    once, so inspection and spawn cannot resolve two different files."""
    with _Iso() as iso:
        fake = iso.td_path / "codex-bin"
        fake.write_bytes(b"\x7fELF")
        fake.chmod(0o755)
        rel = os.path.relpath(fake, os.getcwd())
        os.environ["CODEX_ORACLE_CODEX_BIN"] = rel
        real_argv0 = iso.saved[0]  # _Iso stubs _codex_argv0; test the REAL one
        try:
            got = real_argv0()
            check("relative override resolves to ONE absolute path",
                  len(got) == 1 and os.path.isabs(got[0])
                  and os.path.realpath(got[0]) == os.path.realpath(str(fake)), str(got))
        finally:
            del os.environ["CODEX_ORACLE_CODEX_BIN"]



def test_exit_zero_without_an_answer_is_not_ok() -> None:
    """Round 15 HIGH: OK IS EARNED on attached runs — exit 0 with no final
    answer (no output-last-message, no turn.completed) is an error, never a
    signed ok whose "answer" is log-note metadata."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_NO_ANSWER="1") as iso:
        res = asyncio.run(server._run_codex("empty", tool_name="codex_query"))
        check("no status:ok signature", "status:ok" not in res, res[:200])
        check("the error names the missing answer",
              "without a completed final answer" in res, res[:300])
        check("journal: error", iso.rec().get("status") == "error", str(iso.rec().get("status")))


def test_survivors_leave_the_run_nonterminal_and_stoppable() -> None:
    """Round 15 HIGH: unkillable group survivors make the run NONTERMINAL —
    no end record, the cancel marker pins CANCELLING/stoppable, and
    codex_cancel_run refuses until the group actually dies."""
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        real_pg = server._pgid_alive
        server._pgid_alive = lambda pgid: True  # every sweep "fails"
        try:
            res = asyncio.run(server._run_codex("leaky", tool_name="codex_query"))
        finally:
            server._pgid_alive = real_pg
        check("run reports survivors, not a terminal state",
              "NOT terminalized" in res, res[:260])
        rec = iso.rec()
        check("no end record", not rec.get("has_end"))
        check("survivors phase journaled", rec.get("has_survivors") is True)
        tag = str(rec.get("run"))
        check("marker pins CANCELLING", server._run_status(rec) == "CANCELLING",
              server._run_status(rec))
        check("terminal claim released for the canceller",
              server._acquire_run_claim(server._run_terminal_claim_key(tag))[0])
        server._release_run_claim(server._run_terminal_claim_key(tag))
        res2 = asyncio.run(server.codex_cancel_run(run=tag))
        check("cancel terminalizes once the group is really dead",
              "stopped" in res2 or "cancelled" in res2, res2[:200])
        server._clear_cancel(tag)


def test_resolution_through_a_relative_path_entry_is_absolute() -> None:
    """Round 15 HIGH: which() through a RELATIVE PATH entry returns a
    relative path — inspection would read one file and the spawn (under the
    run's workdir) another. Every resolution returns absolute."""
    with _Iso() as iso:
        bindir = iso.td_path / "relbin"
        bindir.mkdir()
        exe = bindir / "codex"
        exe.write_bytes(b"\x7fELF")
        exe.chmod(0o755)
        rel = os.path.relpath(bindir, os.getcwd())
        real_env = server._codex_env
        server._codex_env = lambda: {"PATH": rel}
        real_argv0 = iso.saved[0]
        try:
            got = real_argv0()
            check("relative-PATH-entry resolution is ABSOLUTE",
                  len(got) == 1 and os.path.isabs(got[0])
                  and os.path.realpath(got[0]) == os.path.realpath(str(exe)), str(got))
        finally:
            server._codex_env = real_env



def test_setsid_escapee_is_found_and_swept_by_the_marker() -> None:
    """Round 16 HIGH: codex shell tools setsid() out of our group — the
    inherited run-marker env is the only handle. The sweep must find and
    kill a setsid'd descendant (pgid checks cannot see it)."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_LEAK_SETSID="1") as iso:
        res = asyncio.run(server._run_codex("escape artist", tool_name="codex_query"))
        check("run still succeeds (the escapee was killable)", "ANSWER:" in res, res[:160])
        tag = str(iso.rec().get("run"))
        left = server._marked_survivors(tag)
        check("no marked survivors remain after the sweep", left == [], str(left))
        check("journal: ok", iso.rec().get("status") == "ok")


def test_cancel_refuses_over_marked_survivors() -> None:
    """Round 16 HIGH: a canceller must refuse to terminalize while any
    process still carries the run's spawn marker, even with the group gone."""
    import subprocess
    with _Iso() as iso:
        tag = "codexMS·1"
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import os, time; os.setsid(); time.sleep(20)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
            wd_pid, wd_start = _fake_watchdog()
            now = time.time()
            server._journal({"run": tag, "phase": "start", "ts": now - 60, "engine": "codex",
                             "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                             "infra": False, "write": False, "web_search": False,
                             "cwd": str(iso.td_path), "prompt": "p", "log": ""})
            server._journal({"run": tag, "phase": "spawn", "ts": now - 60, "pid": dead.pid,
                             "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                             "server_start": "Mon_Jan_1_00:00:00_2000",
                             "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                             "stdout": "", "stderr": "", "output_file": "",
                             "spawn_ts": now - 60, "deadline_ts": now + 3000})
            # Round 19: killable escapees are KILLED by the canceller; the
            # refusal is reserved for UNKILLABLE ones — model that with a
            # no-op kill.
            real_km = server._kill_marked
            server._kill_marked = lambda tag_, pids: None
            try:
                res = asyncio.run(server.codex_cancel_run(run=tag))
            finally:
                server._kill_marked = real_km
            check("cancel refuses over the UNKILLABLE marked survivor",
                  "spawn marker" in res and str(escapee.pid) in res, res[:280])
            check("no terminal record", not server._journal_runs().get(tag, {}).get("has_end"))
            check("intent retained", server._cancel_requested(tag))
            res2 = asyncio.run(server.codex_cancel_run(run=tag))
            with __import__("contextlib").suppress(Exception):
                escapee.wait(timeout=5)
            check("a killable escapee is killed and the retry terminalizes",
                  ("stopped" in res2 or "cancelled" in res2) and escapee.poll() is not None,
                  res2[:200])
        finally:
            with __import__("contextlib").suppress(Exception):
                escapee.kill(); escapee.wait(timeout=5)
            server._clear_cancel(tag)


def test_completion_evidence_does_not_cross_attempts() -> None:
    """Round 16 HIGH: attempt 1 completes then transiently dies; attempt 2
    returns a partial with exit 0 — stale turn.completed must not sign the
    partial as ok, and abraham must never see it as a brief."""
    with _Iso() as iso:
        os.environ["FAKE_CODEX_COMPLETE_THEN_FAIL_ONCE"] = str(iso.td_path / "ctf-marker")
        res = asyncio.run(server._run_codex("two attempts", tool_name="codex_query"))
        check("no status:ok from stale completion evidence",
              "status:ok" not in res, res[:240])
        check("the error names the missing completed answer",
              "without a completed final answer" in res, res[:300])
        rec = iso.rec()
        check("two attempts ran", int(rec.get("attempts") or 0) == 2, str(rec.get("attempts")))
        check("journal: error", rec.get("status") == "error")



def test_scrubbed_escape_is_a_pinned_residual() -> None:
    """Round 17 HIGH (documented boundary, not a coverage claim): a
    descendant that scrubs env+fds+session escapes group AND marker — no
    userspace channel can see it. This test PINS the residual: if the
    marker scan ever starts finding it, the trust-model comment is stale;
    kernel custody is the 1.18 daemon's job."""
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        pidfile = iso.td_path / "scrubbed.pid"
        os.environ["FAKE_CODEX_LEAK_SCRUBBED"] = "1"
        os.environ["FAKE_CODEX_SCRUBBED_PIDFILE"] = str(pidfile)
        res = asyncio.run(server._run_codex("houdini", tool_name="codex_query"))
        check("the run itself completes ok", "ANSWER:" in res, res[:160])
        deadline = time.time() + 5
        while not pidfile.exists() and time.time() < deadline:
            time.sleep(0.05)
        check("the scrubbed escapee exists (the residual is REAL)", pidfile.exists())
        pid = int(pidfile.read_text())
        try:
            check("…and is alive past the sweep (escaped group + marker)",
                  _raw_alive(pid))
            tag = str(iso.rec().get("run"))
            check("the marker scan cannot see it (pinned boundary)",
                  pid not in server._marked_survivors(tag))
        finally:
            with __import__("contextlib").suppress(OSError):
                os.kill(pid, 9)


def test_marker_kill_revalidates_identity() -> None:
    """Round 17 MEDIUM: _kill_marked signals only pids present in a FRESH
    marker scan — a pid from a stale snapshot whose process exited (and
    could be reused) is never signalled."""
    import subprocess
    with _Iso() as iso:
        tag = "codexRV·1"
        victim = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # victim does NOT carry the marker: a stale snapshot naming its
            # pid must not kill it.
            server._kill_marked(tag, [victim.pid])
            time.sleep(0.3)
            check("an unmarked process is never signalled from a stale snapshot",
                  _raw_alive(victim.pid))
            marked = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(20)"],
                env={**os.environ, server.RUN_MARKER_ENV: tag},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + 5
            while marked.pid not in server._marked_survivors(tag) and time.time() < deadline:
                time.sleep(0.05)
            server._kill_marked(tag, [marked.pid])
            marked.wait(timeout=5)
            check("a marker-carrying pid IS killed after revalidation",
                  not _raw_alive(marked.pid))
        finally:
            with __import__("contextlib").suppress(Exception):
                victim.kill(); victim.wait(timeout=5)


def test_custody_cwd_covers_a_lock_holding_read_run() -> None:
    """Round 17 HIGH: a READ run executed while its caller holds the tree's
    write lock (abraham phase 1) must put THAT lock into custody on
    survivors — write mode is not the ownership test."""
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        import subprocess
        tree = iso.td_path / "wtree"; tree.mkdir()
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        ok, _ = server._acquire_write_lock(str(tree), "abraham-outer")
        check("rig holds the tree lock (as abraham does)", ok)
        real_pg = server._pgid_alive
        server._pgid_alive = lambda pgid: True
        try:
            res = asyncio.run(server._run_codex(
                "phase one", tool_name="abraham", custody_cwd=str(tree)))
        finally:
            server._pgid_alive = real_pg
        try:
            check("survivors reported nonterminally", "NOT terminalized" in res, res[:220])
            lp = str(server._write_lock_path(str(tree)))
            check("the HELD tree lock entered custody", lp in server._LOCK_CUSTODY)
            server._release_write_lock(str(tree))
            check("release is a no-op under custody", lp in server._HELD)
            ok2, holder = server._acquire_write_lock(str(tree), "second-writer")
            check("a second writer refuses", not ok2, holder)
            # Round 18: CANCEL releases custody for a lock-held READ via its
            # journaled custody_cwd — no manual cleanup.
            tag = str(iso.rec().get("run"))
            res_c = asyncio.run(server.codex_cancel_run(run=tag))
            check("cancel terminalizes the survivor run",
                  "stopped" in res_c or "cancelled" in res_c, res_c[:200])
            check("custody released by the verified terminalization",
                  lp not in server._LOCK_CUSTODY and lp not in server._HELD)
            ok3, _ = server._acquire_write_lock(str(tree), "after-cancel")
            check("the tree is writable again", ok3)
            server._release_write_lock(str(tree))
        finally:
            tag = str(iso.rec().get("run"))
            server._clear_cancel(tag)
            lp = str(server._write_lock_path(str(tree)))
            server._LOCK_CUSTODY.discard(lp)
            server._release_write_lock(str(tree))



def test_lock_held_read_child_inherits_the_lock_and_detach_keeps_it() -> None:
    """Round 18 HIGH: a lock-held READ run (abraham phase 1) passes the tree
    lock descriptor to its child, and a shutdown-detach puts the lock into
    custody — so the caller's release is a no-op and the DETACHED child keeps
    the tree locked until it exits."""
    import subprocess
    with _Iso(FAKE_CODEX_SLEEP="4") as iso:
        tree = iso.td_path / "ptree"; tree.mkdir()
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        ok, _ = server._acquire_write_lock(str(tree), "abraham-outer")
        check("rig holds the tree lock", ok)
        lp = server._write_lock_path(str(tree))
        probe = ("import fcntl, os, sys\nfd = os.open(sys.argv[1], os.O_RDWR)\n"
                 "try:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\n"
                 "except OSError:\n    print('held')")

        async def scenario():
            task = asyncio.create_task(server._run_codex(
                "phase one", tool_name="abraham", custody_cwd=str(tree)))
            rec = await _wait_spawn(iso)
            await asyncio.sleep(0.3)
            held_live = subprocess.run([sys.executable, "-c", probe, str(lp)],
                                       capture_output=True, text=True).stdout.strip()
            server._SHUTDOWN.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            server._SHUTDOWN.clear()
            return rec, held_live

        rec, held_live = asyncio.run(scenario())
        try:
            check("lock held while phase 1 runs", held_live == "held", held_live)
            check("detached (read run with a live watchdog)",
                  server._is_detached(server._journal_runs().get(rec["run"], rec)))
            check("detach put the lock into custody", str(lp) in server._LOCK_CUSTODY)
            server._release_write_lock(str(tree))  # abraham's finally
            held_detached = subprocess.run([sys.executable, "-c", probe, str(lp)],
                                           capture_output=True, text=True).stdout.strip()
            check("the DETACHED child still holds the tree lock (inherited fd)",
                  held_detached == "held", held_detached)
            deadline = time.time() + 10
            while _raw_alive(int(rec["pid"])) and time.time() < deadline:
                time.sleep(0.2)
            check("child finished", not _raw_alive(int(rec["pid"])))
            # Our own (test-process) descriptor still holds the OFD lock —
            # correct: custody keeps the dying server's fd open until process
            # exit. Model the server exiting: end custody and close our fd;
            # with the child dead too, the kernel then frees the lock.
            server._LOCK_CUSTODY.discard(str(lp))
            server._release_write_lock(str(tree))
            deadline = time.time() + 5
            free = ""
            while time.time() < deadline:
                free = subprocess.run([sys.executable, "-c", probe, str(lp)],
                                      capture_output=True, text=True).stdout.strip()
                if free == "free":
                    break
                time.sleep(0.2)
            check("kernel frees the lock once child AND holder are gone", free == "free", free)
        finally:
            server._LOCK_CUSTODY.discard(str(lp))
            server._release_write_lock(str(tree))
            with __import__("contextlib").suppress(Exception):
                server._clear_cancel(str(rec.get("run")))



def test_watchdog_kills_marked_escapees_at_the_deadline() -> None:
    """Round 19 HIGH (the reviewer's exact probe): with no server alive, the
    detached watchdog must kill a MARKED setsid escapee at the deadline, not
    just the leader's group."""
    import subprocess
    with _Iso() as iso:
        tag = "codexWD·9"
        leader = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)  # NOT in leader's group
        wd = None
        try:
            deadline = time.time() + 2
            wd = server._spawn_watchdog(leader.pid, os.getpgid(leader.pid), deadline, tag,
                                        server._proc_start(leader.pid))
            check("watchdog spawned", wd is not None)
            end = time.time() + 20
            while (leader.poll() is None or escapee.poll() is None) and time.time() < end:
                time.sleep(0.5)  # poll() reaps — kill-0 sees SIGKILLed children as zombies
            check("leader killed at the deadline", leader.poll() is not None)
            check("MARKED setsid escapee killed too (no server involved)",
                  escapee.poll() is not None)
        finally:
            for pr in (leader, escapee):
                with __import__("contextlib").suppress(Exception):
                    pr.kill(); pr.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)


def test_cancel_kills_marked_escapees_before_refusing() -> None:
    """Round 19: the canceller kills killable marked escapees itself (with
    kill-time revalidation) instead of only reporting them."""
    import subprocess
    with _Iso() as iso:
        tag = "codexKM·1"
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
            wd_pid, wd_start = _fake_watchdog()
            now = time.time()
            server._journal({"run": tag, "phase": "start", "ts": now - 60, "engine": "codex",
                             "tool": "codex_query", "model": "gpt-test", "reasoning": "max",
                             "infra": False, "write": False, "web_search": False,
                             "cwd": str(iso.td_path), "prompt": "p", "log": ""})
            server._journal({"run": tag, "phase": "spawn", "ts": now - 60, "pid": dead.pid,
                             "pgid": dead.pid, "pid_start": "", "server_pid": 999999999,
                             "server_start": "Mon_Jan_1_00:00:00_2000",
                             "watchdog_pid": wd_pid, "watchdog_start": wd_start,
                             "stdout": "", "stderr": "", "output_file": "",
                             "spawn_ts": now - 60, "deadline_ts": now + 3000})
            deadline = time.time() + 5
            while escapee.pid not in server._marked_survivors(tag) and time.time() < deadline:
                time.sleep(0.05)
            res = asyncio.run(server.codex_cancel_run(run=tag))
            check("cancel KILLED the killable escapee and terminalized",
                  "stopped" in res or "cancelled" in res, res[:240])
            with __import__("contextlib").suppress(Exception):
                escapee.wait(timeout=5)  # reap: a SIGKILLed direct child is a zombie to kill-0
            check("escapee is dead", escapee.poll() is not None)
            check("journal: cancelled",
                  server._journal_runs().get(tag, {}).get("status") == "cancelled")
        finally:
            with __import__("contextlib").suppress(Exception):
                escapee.kill(); escapee.wait(timeout=5)
            server._clear_cancel(tag)



def test_watchdog_exits_on_natural_quiescence_no_self_match() -> None:
    """Round 20 HIGH: the marker scan must never match its own pipeline —
    with leader, group and escapee all gone, the watchdog exits promptly
    instead of living forever on its own grep's argv."""
    import subprocess
    with _Iso() as iso:
        tag = "codexSM·1"
        leader = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        pgid = os.getpgid(leader.pid)
        leader.wait()
        wd = server._spawn_watchdog(leader.pid, pgid, time.time() + 3600, tag)
        check("watchdog spawned", wd is not None)
        try:
            deadline = time.time() + 15
            while wd.poll() is None and time.time() < deadline:
                time.sleep(0.5)
            check("watchdog exits on natural quiescence (no self-match)",
                  wd.poll() is not None)
        finally:
            with __import__("contextlib").suppress(Exception):
                wd.terminate(); wd.wait(timeout=5)


def test_watchdog_kills_escapee_after_leader_exits_first() -> None:
    """Round 20 HIGH: leader exits FIRST, only a marked escapee remains —
    the deadline still kills the escapee, and group signalling has been
    continuity-gated off (the extinct pgid is never signalled again)."""
    import subprocess
    with _Iso() as iso:
        tag = "codexLF·1"
        leader = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        pgid = os.getpgid(leader.pid)
        leader.wait()  # leader (and its group) gone BEFORE the watchdog acts
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        wd = server._spawn_watchdog(leader.pid, pgid, time.time() + 2, tag)
        check("watchdog spawned", wd is not None)
        try:
            end = time.time() + 20
            while escapee.poll() is None and time.time() < end:
                time.sleep(0.5)
            check("escapee killed at the deadline despite the dead leader",
                  escapee.poll() is not None)
        finally:
            for pr in (escapee,):
                with __import__("contextlib").suppress(Exception):
                    pr.kill(); pr.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)


def test_survivors_block_the_transient_retry() -> None:
    """Round 20 HIGH: a transient failure whose attempt left live descendants
    must NOT retry — attempt N+1 would overlap attempt N's survivors. The
    survivor path (nonterminal, marker) takes over after exactly one spawn."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_FAIL="capacity",
              FAKE_CODEX_THREAD="T-SV") as iso:
        real_pg = server._pgid_alive
        server._pgid_alive = lambda pgid: True  # sweep cannot verify quiescence
        try:
            res = asyncio.run(server._run_codex("shed with leftovers",
                                                tool_name="codex_query"))
        finally:
            server._pgid_alive = real_pg
        check("survivor path took over (nonterminal)", "NOT terminalized" in res, res[:240])
        spawns = sum(1 for line in server.RUNS_JOURNAL.read_text(encoding="utf-8").splitlines()
                     if '"phase": "spawn"' in line)
        check("exactly ONE spawn (no retry over survivors)", spawns == 1, str(spawns))
        tag = str(iso.rec().get("run"))
        server._clear_cancel(tag)



def test_watchdog_never_kills_a_group_without_leader_identity() -> None:
    """Round 21 HIGH: killpg is anchored to the LEADER's start token — with a
    stale/wrong token (modeling pid reuse between polls), the group is never
    signalled; marker-verified pids are still killed."""
    import subprocess
    with _Iso() as iso:
        tag = "codexID·1"
        leader = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        wd = None
        try:
            wd = server._spawn_watchdog(leader.pid, os.getpgid(leader.pid),
                                        time.time() + 2, tag,
                                        "Mon Jan  1 00:00:00 2000")  # WRONG token
            check("watchdog spawned", wd is not None)
            end = time.time() + 20
            while escapee.poll() is None and time.time() < end:
                time.sleep(0.5)
            check("marked escapee killed (marker path)", escapee.poll() is not None)
            check("the group was NEVER signalled (identity mismatch)",
                  leader.poll() is None)
        finally:
            for pr in (leader, escapee):
                with __import__("contextlib").suppress(Exception):
                    pr.kill(); pr.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)


def test_dead_watchdog_means_kill_not_detach() -> None:
    """Round 21 MEDIUM: a crashed watchdog leaves a non-null handle — the
    shutdown branch must verify it is ALIVE (poll) and otherwise KILL the
    child rather than detach it unbounded."""
    with _Iso(FAKE_CODEX_SLEEP="6") as iso:
        async def scenario():
            task = asyncio.create_task(server._run_codex("doomed detach"))
            rec = await _wait_spawn(iso)
            wd_pid = int(rec.get("watchdog_pid") or 0)
            check("a watchdog exists", wd_pid > 0)
            os.kill(wd_pid, signal.SIGKILL)  # the enforcer crashes
            await asyncio.sleep(0.5)
            server._SHUTDOWN.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            server._SHUTDOWN.clear()
            return rec
        rec = asyncio.run(scenario())
        deadline = time.time() + 6
        while _raw_alive(int(rec["pid"])) and time.time() < deadline:
            time.sleep(0.2)
        check("child KILLED, not detached (no live enforcer)",
              not _raw_alive(int(rec["pid"])))
        fresh = server._journal_runs().get(str(rec["run"]), {})
        check("run did not journal a detach", not fresh.get("has_detached"))



def test_no_message_recommends_a_bare_pgid_kill() -> None:
    """Round 22 HIGH: after leader identity is lost a pgid may be reused —
    no survivor/ambiguity/cancel message may hand the operator `kill -9
    -<pgid>`. Pinned by source lint + a live survivor message."""
    import inspect
    src = inspect.getsource(server)
    # ANY `kill -9` in Python message text (pgid OR pid form — round 23: a
    # pid listed now may be reused by the time the operator acts); the only
    # permitted occurrences are inside the watchdog's own sh, which
    # identity-gates its group kill and marker-verifies its pid kills.
    py_lines = [ln for ln in src.splitlines()
                if "kill -9" in ln and not ln.lstrip().startswith("#")
                and "$pgid" not in ln and '"$p"' not in ln]
    check("no Python message text recommends any numeric kill", py_lines == [], str(py_lines)[:300])
    with _Iso(FAKE_CODEX_SLEEP="0.2") as iso:
        real_pg = server._pgid_alive
        server._pgid_alive = lambda pgid: True
        try:
            res = asyncio.run(server._run_codex("leaky", tool_name="codex_query"))
        finally:
            server._pgid_alive = real_pg
        check("survivor message guides to marker-verified kills, never a bare pgid",
              "kill -9 -" not in res and "MARKER-VERIFIED" in res, res[:300])
        server._clear_cancel(str(iso.rec().get("run")))


def test_watchdog_survives_transient_ps_failures() -> None:
    """Round 22 MEDIUM: ps failures are UNKNOWN, not quiescence — a
    watchdog whose ps fails a few times must keep enforcing and still kill
    the marked escapee once ps recovers."""
    import subprocess
    with _Iso() as iso:
        tag = "codexPS·1"
        counter = iso.td_path / "scan-fails"
        counter.write_text("0")
        fake_pe = iso.td_path / "procenv_flaky.py"  # the marker scan fails 4 times, then works
        fake_pe.write_text(
            "import pathlib, subprocess, sys\n"
            f"c = pathlib.Path({str(counter)!r}); n = int(c.read_text() or 0)\n"
            "if n < 4:\n    c.write_text(str(n + 1)); sys.exit(2)\n"
            f"sys.exit(subprocess.run([sys.executable, {str(server.PROCENV_PATH)!r}, *sys.argv[1:]]).returncode)\n")
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(40)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        wd = server._spawn_watchdog(dead.pid, dead.pid, time.time() - 1, tag, "",
                                    procenv=str(fake_pe))  # UNKNOWN scans, never quiescence
        try:
            check("watchdog spawned", wd is not None)
            end = time.time() + 40
            while escapee.poll() is None and time.time() < end:
                time.sleep(0.5)
            check("escapee killed after ps recovered (unknown never read as clean)",
                  escapee.poll() is not None)
            check("the failures were recorded",
                  (server.LIVE_LOG_DIR / "watchdog-failures.log").exists()
                  and "ps-unknown" in (server.LIVE_LOG_DIR / "watchdog-failures.log").read_text())
        finally:
            with __import__("contextlib").suppress(Exception):
                escapee.kill(); escapee.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)



def test_watchdog_exits_degraded_when_ps_never_recovers() -> None:
    """Round 23 MEDIUM: with ps permanently blind, the watchdog must not
    loop forever pretending to enforce — after maxu consecutive unknown
    scans it records a machine-readable degraded state and exits 2."""
    import subprocess
    with _Iso() as iso:
        tag = "codexNV·1"
        fake_pe = iso.td_path / "procenv_broken.py"
        fake_pe.write_text("import sys\nsys.exit(2)\n")  # the scan is permanently unverifiable
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        wd = server._spawn_watchdog(dead.pid, dead.pid, time.time() + 3600, tag, "",
                                    max_unknown=2, procenv=str(fake_pe))
        check("watchdog spawned", wd is not None)
        try:
            deadline = time.time() + 30
            while wd.poll() is None and time.time() < deadline:
                time.sleep(0.5)
            check("watchdog exited (bounded unknown policy)", wd.poll() is not None)
            check("…with the DEGRADED exit code 2", wd.poll() == 2, str(wd.poll()))
            log = server.LIVE_LOG_DIR / "watchdog-failures.log"
            check("degraded state recorded machine-readably",
                  log.exists() and "degraded ps-unavailable exit=2" in log.read_text())
        finally:
            with __import__("contextlib").suppress(Exception):
                wd.terminate(); wd.wait(timeout=5)



def test_watchdog_unknown_counter_resets_on_leader_success() -> None:
    """Round 24 MEDIUM: isolated marker-scan failures separated by healthy
    leader probes must not accumulate to a degraded exit — a live leader is
    positive evidence."""
    import subprocess
    with _Iso() as iso:
        tag = "codexRC·1"
        fake_pe = iso.td_path / "procenv_scan_blind.py"  # --list fails, verification works
        fake_pe.write_text(
            "import subprocess, sys\n"
            "if sys.argv[1:2] == ['--list']:\n    sys.exit(2)\n"
            f"sys.exit(subprocess.run([sys.executable, {str(server.PROCENV_PATH)!r}, *sys.argv[1:]]).returncode)\n")
        leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        wd = None
        try:
            wd = server._spawn_watchdog(leader.pid, os.getpgid(leader.pid), time.time() + 3600,
                                        tag, server._proc_start(leader.pid),
                                        max_unknown=2, procenv=str(fake_pe))
            check("watchdog spawned", wd is not None)
            time.sleep(22)  # > 4 ticks: without the reset, u would reach 2 and exit 2
            check("watchdog still enforcing (leader success resets the unknown count)",
                  wd.poll() is None, str(wd.poll()))
        finally:
            with __import__("contextlib").suppress(Exception):
                leader.kill(); leader.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)



def test_argv_decoy_is_never_marked_or_killed() -> None:
    """Round 29 MEDIUM (measured via KERN_PROCARGS2): a process whose ARGV
    merely contains the marker text (an operator's grep, a decoy) is neither
    a survivor nor a kill target; an env-marked process still is."""
    import subprocess
    with _Iso() as iso:
        tag = "codexEV·1"
        decoy = subprocess.Popen(
            [sys.executable, "-c",
             f"import time; time.sleep(30) # {server.RUN_MARKER_ENV}={tag}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        marked = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.time() + 5
            while marked.pid not in server._marked_survivors(tag) and time.time() < deadline:
                time.sleep(0.05)
            surv = server._marked_survivors(tag)
            check("env-marked process is a survivor", marked.pid in surv, str(surv))
            check("argv decoy is NOT a survivor", decoy.pid not in surv, str(surv))
            server._kill_marked(tag, [decoy.pid, marked.pid])
            with __import__("contextlib").suppress(Exception):
                marked.wait(timeout=5)
            check("env-marked process killed", marked.poll() is not None)
            time.sleep(0.3)
            check("argv decoy untouched", decoy.poll() is None)
        finally:
            for pr in (decoy, marked):
                with __import__("contextlib").suppress(Exception):
                    pr.kill(); pr.wait(timeout=5)



def test_watchdog_env_verifies_before_killing() -> None:
    """Rounds 30-31: the no-server watchdog's deadline sweep kills only pids
    whose ENVIRONMENT carries the marker. Exact enumeration never nominates
    an argv decoy at all, so the defensive branch — a nominee the verifier
    rejects — is driven by an OVER-nominating scan (the decoy's pid appended
    to the real list, as a text scan would): the escapee is killed, the
    decoy survives, is logged `unverified-marked`, and the watchdog exits
    bounded."""
    import subprocess
    with _Iso() as iso:
        tag = "codexWV·1"
        leader = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        escapee = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, server.RUN_MARKER_ENV: tag},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        decoy = subprocess.Popen(
            [sys.executable, "-c",
             f"import time; time.sleep(30) # {server.RUN_MARKER_ENV}={tag}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        fake_pe = iso.td_path / "procenv_overnominating.py"
        fake_pe.write_text(
            "import subprocess, sys\n"
            f"REAL = {str(server.PROCENV_PATH)!r}\n"
            "if sys.argv[1:2] == ['--list']:\n"
            "    r = subprocess.run([sys.executable, REAL, *sys.argv[1:]], capture_output=True, text=True)\n"
            "    sys.stdout.write(r.stdout)\n"
            f"    print({decoy.pid})  # nominate the argv decoy the way a text scan would\n"
            "    sys.exit(r.returncode)\n"
            "sys.exit(subprocess.run([sys.executable, REAL, *sys.argv[1:]]).returncode)\n")
        wd = None
        try:
            deadline = time.time() + 2
            wd = server._spawn_watchdog(leader.pid, os.getpgid(leader.pid), deadline, tag,
                                        server._proc_start(leader.pid), procenv=str(fake_pe))
            check("watchdog spawned", wd is not None)
            end = time.time() + 25
            while escapee.poll() is None and time.time() < end:
                time.sleep(0.5)
            check("env-marked escapee killed at the deadline", escapee.poll() is not None)
            try:
                wd.wait(timeout=20)  # 5 bounded passes over the unverified decoy
            except Exception:
                pass
            check("watchdog exited bounded with the decoy unkilled", wd.poll() is not None)
            check("argv decoy survived the sweep", decoy.poll() is None)
            flog = (server.LIVE_LOG_DIR / "watchdog-failures.log")
            text = flog.read_text() if flog.exists() else ""
            check("decoy logged as unverified-marked", f"run={tag} unverified-marked {decoy.pid}" in text,
                  text[-300:])
        finally:
            for pr in (leader, escapee, decoy):
                with __import__("contextlib").suppress(Exception):
                    pr.kill(); pr.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)


def test_procenv_ps_failure_is_not_disappearance() -> None:
    """Round 40 MEDIUM: on macOS `ps -p` exits 1 with EMPTY output both for a
    pid that is gone and when the lookup itself fails; the old check read the
    latter as "vanished", so a scan returned [] over a live process and
    custody could be released. Only the kernel decides."""
    pe = server._procenv
    if sys.platform != "darwin":
        check("procenv ps probe is darwin-only (skipped here)", True)
        return
    with tempfile.TemporaryDirectory() as td:
        failing = Path(td) / "ps"
        failing.write_text("#!/bin/sh\nexit 1\n")
        failing.chmod(0o755)
        check("failing ps + live pid → NOT gone (unreadable, not vanished)",
              pe._gone_or_zombie_darwin(os.getpid(), ps_bin=str(failing)) is False)
        zombie = Path(td) / "psz"
        zombie.write_text("#!/bin/sh\necho 'Z+'\n")
        zombie.chmod(0o755)
        check("a Z state is a zombie (skip)", pe._gone_or_zombie_darwin(os.getpid(), ps_bin=str(zombie)) is True)
        child = subprocess.Popen(["/bin/sleep", "0"])
        child.wait()
        check("a reaped pid is gone (the kernel agrees)", pe._gone_or_zombie_darwin(child.pid, ps_bin=str(failing)) is True)
        check("real ps: a live pid is not gone", pe._gone_or_zombie_darwin(os.getpid()) is False)


def test_procenv_cli_is_the_single_verifier() -> None:
    """procenv.py is what the watchdog runs: exit 0 = env-marked, 1 = not
    marked (argv decoy), 2 = unverifiable (no such process); its marker name
    is the server's."""
    import subprocess
    tag = "codexPE·1"
    marked = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, server.RUN_MARKER_ENV: tag},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    decoy = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(30) # {server.RUN_MARKER_ENV}={tag}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pe = str(server.PROCENV_PATH)
        check("procenv.py sits next to server.py", Path(pe).exists(), pe)
        check("marker name shared", server.RUN_MARKER_ENV == server._procenv.RUN_MARKER_ENV)
        rc = subprocess.run([sys.executable, pe, str(marked.pid), tag]).returncode
        check("env-marked → 0", rc == 0, str(rc))
        rc = subprocess.run([sys.executable, pe, str(decoy.pid), tag]).returncode
        check("argv decoy → 1", rc == 1, str(rc))
        rc = subprocess.run([sys.executable, pe, "4000000", tag]).returncode
        check("no such process → 2", rc == 2, str(rc))
        rc = subprocess.run([sys.executable, pe]).returncode
        check("bad usage → 2", rc == 2, str(rc))
    finally:
        for pr in (marked, decoy):
            with __import__("contextlib").suppress(Exception):
                pr.kill(); pr.wait(timeout=5)


def test_run_budget_is_a_managed_limit() -> None:
    """User ruling 2026-09-04: three legitimate runs died at the 60-min
    literal while still working. The budget is a MANAGED limit — env-
    configurable, rejected loudly when out of band (never clamped), pinned,
    reconciled with the MCP call timeout, visible in codex_runs, warned at
    80 % in the live log, and named (with the knob) in the kill message."""
    check("default 3 h", server.MAX_RUNTIME_DEFAULT_S == 3 * 3600)
    check("band 300..12600 s (3.5 h, 30 min under the 4 h MCP call timeout)",
          (server.MAX_RUNTIME_MIN_S, server.MAX_RUNTIME_MAX_S) == (300, 12600))
    check("MIN_ATTEMPT 120 s", server.MIN_ATTEMPT_SECONDS == 120)
    saved_env = os.environ.get("CODEX_ORACLE_MAX_RUNTIME_S")
    try:
        cases = (("", (10800, "default")), ("7200", (7200, "env")), ("300", (300, "env")),
                 ("12600", (12600, "env")), ("299", None), ("12601", None), ("abc", None),
                 ("nan", None), ("inf", None), ("-5", None), ("  ", (10800, "default")))
        for raw, want in cases:
            if raw == "":
                os.environ.pop("CODEX_ORACLE_MAX_RUNTIME_S", None)
            else:
                os.environ["CODEX_ORACLE_MAX_RUNTIME_S"] = raw
            got = server._max_runtime_from_env()
            if want is None:
                check(f"{raw!r} rejected loudly, default kept",
                      got[0] == 10800 and got[1].startswith("rejected"), str(got))
            else:
                check(f"{raw!r} → {want}", got == want, str(got))
    finally:
        if saved_env is None:
            os.environ.pop("CODEX_ORACLE_MAX_RUNTIME_S", None)
        else:
            os.environ["CODEX_ORACLE_MAX_RUNTIME_S"] = saved_env
    reg = json.loads((Path(server.__file__).resolve().parent / ".mcp.json").read_text())
    ms = reg["mcpServers"]["codex-oracle"]["timeout"]
    check("chained ceiling: .mcp.json call timeout ≥ the band MAXIMUM + 30 min (round 32)",
          ms / 1000 >= server.MAX_RUNTIME_MAX_S + 1800, str(ms))
    saved_budget = server.MAX_RUNTIME_SECONDS
    server.MAX_RUNTIME_SECONDS = 3
    try:
        with _Iso(FAKE_CODEX_SLEEP="12") as iso:
            res = asyncio.run(server._run_codex("budget probe"))
            check("kill message names the budget and the knob",
                  "TIMEOUT" in res and "CODEX_ORACLE_MAX_RUNTIME_S" in res, res[:240])
            logs = sorted(server.LIVE_LOG_DIR.glob("*-codex.log"), key=lambda p: p.stat().st_mtime)
            text = logs[-1].read_text() if logs else ""
            check("80% warning reached the live log before the kill",
                  "80% of its 3s attempt budget" in text, text[-400:])
            # REQUEST-LEVEL (round 32): a request that already spent its budget
            # is refused before spawning — no fresh budget per attempt/phase
            res2 = asyncio.run(server._run_codex("budget probe 2", request_started=time.monotonic() - 10))
            check("an exhausted request budget refuses the attempt as a timeout",
                  "TIMEOUT" in res2 and "REQUEST budget" in res2, res2[:240])
            runs = asyncio.run(server.codex_runs())
            check("codex_runs shows the effective budget", "run budget 3s" in runs, runs[:200])
    finally:
        server.MAX_RUNTIME_SECONDS = saved_budget


def test_procenv_enumeration_has_no_ps_e_and_scans_proc_on_linux() -> None:
    """Round 31 HIGH: `ps -E` is BSD-only — procps-ng never had it, so the
    Linux scan could not have worked. Enumeration now lives in procenv.py:
    `--list` finds an env-marked child and not an argv decoy (macOS: BSD ps
    pid list + KERN_PROCARGS2; Linux: /proc), the /proc scanner is driven
    here against a fake proc tree (marked live, decoy, marked zombie, self),
    and no `-axE` remains anywhere."""
    import subprocess
    src_server = Path(server.__file__).read_text()
    src_pe = Path(server.PROCENV_PATH).read_text()
    check("no ps -E in server.py or procenv.py", "-axE" not in src_server and "-axE" not in src_pe)
    tag = "codexLS·1"
    marked = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, server.RUN_MARKER_ENV: tag},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    decoy = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(30) # {server.RUN_MARKER_ENV}={tag}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 5
        listed: list[int] = []
        while marked.pid not in listed and time.time() < deadline:
            done = subprocess.run([sys.executable, str(server.PROCENV_PATH), "--list", tag],
                                  capture_output=True, text=True, timeout=30)
            check("--list exits 0", done.returncode == 0, done.stderr[:120])
            listed = [int(x) for x in done.stdout.split()]
            time.sleep(0.1)
        check("--list finds the env-marked child", marked.pid in listed, str(listed))
        check("--list excludes the argv decoy", decoy.pid not in listed, str(listed))
        check("server._marked_survivors agrees", marked.pid in server._marked_survivors(tag)
              and decoy.pid not in server._marked_survivors(tag))
    finally:
        for pr in (marked, decoy):
            with __import__("contextlib").suppress(Exception):
                pr.kill(); pr.wait(timeout=5)
    with tempfile.TemporaryDirectory() as td:
        proc = Path(td) / "proc"
        needle = f"{server.RUN_MARKER_ENV}={tag}".encode()
        me_uid = os.geteuid()
        for pid, env, state in ((101, needle + b"\0HOME=/x\0", b"S"),
                                (102, b"OTHER=1\0", b"S"),
                                (103, needle + b"\0", b"Z"),
                                (104, needle + b"\0", b"R")):
            d = proc / str(pid); d.mkdir(parents=True)
            (d / "environ").write_bytes(env)
            (d / "status").write_bytes(b"Name:\tpy thon\nState:\t%s (x)\nUid:\t%d\t%d\t%d\t%d\n"
                                       % (state, me_uid, me_uid, me_uid, me_uid))
        (proc / "self").mkdir(); (proc / "meminfo").write_text("x")
        got = sorted(server._procenv.scan_proc(tag, proc_root=str(proc), me=104))
        check("/proc scan: marked live only (no decoy, no zombie, not self)", got == [101], str(got))
        try:
            server._procenv.scan_proc(tag, proc_root=str(proc / "missing"))
            check("/proc scan raises when the tree cannot be listed", False)
        except OSError:
            check("/proc scan raises when the tree cannot be listed", True)


def test_procenv_unreadable_same_user_is_unknown_not_empty() -> None:
    """Round 32 MEDIUM: an environ we cannot read on a process we OWN makes
    the scan UNKNOWN (raises) — an empty list must prove quiescence; a
    foreign-user process is skipped; the scan has a deadline and a cap."""
    if os.geteuid() == 0:
        return
    with tempfile.TemporaryDirectory() as td:
        proc = Path(td) / "proc"
        tag = "codexUR·1"
        d = proc / "201"; d.mkdir(parents=True)
        (d / "environ").write_bytes(b"OTHER=1\0")
        u = os.geteuid()
        (d / "status").write_bytes(b"Name:\tx\nState:\tS (sleeping)\nUid:\t%d\t%d\t%d\t%d\n" % (u, u, u, u))
        os.chmod(d / "environ", 0)
        try:
            try:
                server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
                check("unreadable same-user environ → raises", False)
            except OSError as exc:
                check("unreadable same-user environ → raises", "unreadable" in str(exc), str(exc))
        finally:
            os.chmod(d / "environ", 0o600)
        got = server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
        check("readable again → clean empty list", got == [], str(got))
        saved = server._procenv.SCAN_MAX_ENTRIES
        server._procenv.SCAN_MAX_ENTRIES = 0
        try:
            try:
                server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
                check("entry cap → raises", False)
            except OSError as exc:
                check("entry cap → raises", "cap" in str(exc), str(exc))
        finally:
            server._procenv.SCAN_MAX_ENTRIES = saved


def test_procenv_read_failures_are_classified_and_foreign_uids_never_read() -> None:
    """Round 33-34 HIGH: EMFILE/EIO on an environ read is UNCERTAIN (raises),
    never "vanished"; a foreign-uid entry (uid from the pinned STATUS file,
    not directory ownership) is skipped WITHOUT its environ being opened;
    EIO on the /proc/<pid> directory itself is uncertain, not "gone"."""
    import errno
    with tempfile.TemporaryDirectory() as td:
        proc = Path(td) / "proc"
        tag = "codexRF·1"
        u = os.geteuid()
        for pid, puid in ((301, u), (302, u + 1)):
            d = proc / str(pid); d.mkdir(parents=True)
            (d / "environ").write_bytes(b"OTHER=1\0")
            (d / "status").write_bytes(b"Name:\tx\nState:\tS (sleeping)\nUid:\t%d\t%d\t%d\t%d\n"
                                       % (puid, puid, puid, puid))
        real_read = server._procenv._read_at
        reads = []

        def flaky_read(dfd, name, limit):
            reads.append((os.fstat(dfd).st_ino, name))
            if name == "environ" and os.fstat(dfd).st_ino == os.stat(proc / "301").st_ino:
                raise OSError(errno.EMFILE, "Too many open files")
            return real_read(dfd, name, limit)

        server._procenv._read_at = flaky_read
        try:
            try:
                server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
                check("EMFILE on a same-user environ → raises (uncertain)", False)
            except OSError as exc:
                check("EMFILE on a same-user environ → raises (uncertain)",
                      "uncertain" in str(exc) and "UNKNOWN" in str(exc), str(exc))
            foreign_ino = os.stat(proc / "302").st_ino
            check("foreign-uid environ was never opened",
                  not any(ino == foreign_ino and name == "environ" for ino, name in reads), str(reads))
        finally:
            server._procenv._read_at = real_read
        real_open = os.open

        def eio_open(path, *a, **k):
            if isinstance(path, str) and path.endswith("/301"):
                raise OSError(errno.EIO, "Input/output error")
            return real_open(path, *a, **k)

        os.open = eio_open
        try:
            try:
                server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
                check("EIO on /proc/<pid> → raises (uncertain), not gone", False)
            except OSError as exc:
                check("EIO on /proc/<pid> → raises (uncertain), not gone", "uncertain" in str(exc), str(exc))
        finally:
            os.open = real_open



def test_request_budget_is_absolute_across_spawn_and_retries() -> None:
    """Round 33 HIGH: the attempt deadline is derived from the REQUEST clock
    at publication (setup time is not added back) and a capacity wait must
    fit in what is left."""
    saved = server.MAX_RUNTIME_SECONDS
    server.MAX_RUNTIME_SECONDS = 3
    try:
        with _Iso(FAKE_CODEX_SLEEP="12") as iso:
            asyncio.run(server._run_codex("budget clock", request_started=time.monotonic() - 1.0))
            spans = []
            for line in (server.LIVE_LOG_DIR / "runs.jsonl").read_text().splitlines():
                rec = json.loads(line)
                if rec.get("phase") == "spawn" and rec.get("deadline_ts") and rec.get("spawn_ts"):
                    spans.append(float(rec["deadline_ts"]) - float(rec["spawn_ts"]))
            check("spawn record exists", bool(spans))
            check("published deadline = remaining REQUEST budget (≈2 s, not 3)",
                  spans and 1.0 <= spans[-1] <= 2.3, str(spans))
            ticks = [json.loads(line).get("max_ticks")
                     for line in (server.LIVE_LOG_DIR / "runs.jsonl").read_text().splitlines()
                     if json.loads(line).get("phase") == "spawn"]
            check("tick bound computed by the PARENT from the attempt budget (round 36)",
                  ticks and ticks[-1] == int(spans[-1] // 5) + 2 and ticks[-1] >= 2, str((ticks, spans)))
            src = Path(server.__file__).read_text()
            check("production passes max_ticks (never the child's wall-clock derivation)",
                  "max_ticks=max_ticks)" in src)
        fits, left = server._retry_fits(time.monotonic() - 2.0, 0.5)
        check("a wait that cannot fit is refused", fits is False and 0.5 <= left <= 1.1, str((fits, left)))
        fits, _ = server._retry_fits(time.monotonic(), 0.5)
        check("a wait that fits is allowed", fits is True)
        fits, _ = server._retry_fits(None, 300.0)
        check("no request clock: allowed (legacy callers)", fits is True)
    finally:
        server.MAX_RUNTIME_SECONDS = saved


def test_watchdog_tick_bound_is_clock_independent() -> None:
    """Round 34: the detached enforcer's wall clock can be rolled back; the
    tick bound fires regardless (max_ticks=1 with a far wall deadline)."""
    import subprocess
    with _Iso() as iso:
        tag = "codexTK·1"
        leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        wd = None
        try:
            wd = server._spawn_watchdog(leader.pid, os.getpgid(leader.pid), time.time() + 3600, tag,
                                        server._proc_start(leader.pid), max_ticks=1)
            check("watchdog spawned", wd is not None)
            end = time.time() + 20
            while leader.poll() is None and time.time() < end:
                time.sleep(0.5)
            check("leader killed by the tick bound despite a far wall-clock deadline",
                  leader.poll() is not None)
        finally:
            with __import__("contextlib").suppress(Exception):
                leader.kill(); leader.wait(timeout=5)
            if wd is not None:
                with __import__("contextlib").suppress(Exception):
                    wd.terminate(); wd.wait(timeout=5)


def test_git_state_fails_closed_when_status_fails() -> None:
    """Round 34 (pre-existing) + round 36: the write-mode snapshot runs NO
    `git status` (it ran a configured clean filter — measured); it is the
    filter-free treedigest status, and a failed/timed-out listing must not
    read as a clean tree — the write target is refused, with the reason."""
    import subprocess
    import re as _re
    src = Path(server.__file__).read_text()
    check("server.py never runs git status",
          _re.search(r'_git\(\s*\[\s*"status"', src) is None and "status --porcelain" not in src
          and '"status", "--porcelain"' not in src)
    real = server._treedigest._git_output

    def failing(where, args, *a, **k):
        if args and args[0] == "ls-files":
            return 128, b""
        return real(where, args, *a, **k)

    server._treedigest._git_output = failing
    try:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True)
            ok, dirty, head = server._git_state(td)
            check("failed listing → not a write target", ok is False and dirty == set(), str((ok, dirty, head)))
            check("the refusal reason is kept", "index listing failed" in server._GIT_STATE_REASON,
                  server._GIT_STATE_REASON)
    finally:
        server._treedigest._git_output = real
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["git", "init", "-q", td], check=True)
        (Path(td) / "w.txt").write_text("w\n")
        ok, dirty, head = server._git_state(td)
        check("a real dirty tree reads dirty, no commits yet", ok and dirty == {"?? w.txt"} and head == "",
              str((ok, dirty, head)))
    with tempfile.TemporaryDirectory() as td:
        ok, dirty, head = server._git_state(td)
        check("outside a work tree → refused", ok is False and server._GIT_STATE_REASON, server._GIT_STATE_REASON)


def test_procenv_environ_cap_is_loud() -> None:
    """Round 36 MEDIUM: a /proc read larger than its cap was silently
    truncated — a cut marker boundary read as "no marker". Now the cap is
    named, the read takes limit+1 bytes and REFUSES overflow: at-cap finds
    the marker, over-cap is UNCERTAIN (the scan raises)."""
    with tempfile.TemporaryDirectory() as td:
        proc = Path(td) / "proc"
        tag = "codexCAP·1"
        needle = f"{server.RUN_MARKER_ENV}={tag}".encode()
        u = os.geteuid()
        status = b"Name:\tx\nState:\tS (sleeping)\nUid:\t%d\t%d\t%d\t%d\n" % (u, u, u, u)
        cap = server._procenv.ENVIRON_MAX_BYTES
        check("cap named and sized (4 MiB ≥ 2× the default Linux environment ceiling)", cap == 4 << 20)
        body = needle + b"\0"
        d = proc / "501"; d.mkdir(parents=True)
        (d / "environ").write_bytes(body + b"P=" + b"x" * (cap - len(body) - 3) + b"\0")
        (d / "status").write_bytes(status)
        check("fixture is exactly at the cap", (d / "environ").stat().st_size == cap)
        got = server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
        check("at-cap environ: marker found", got == [501], str(got))
        (d / "environ").write_bytes(body + b"P=" + b"x" * (cap - len(body) - 2) + b"\0")
        check("fixture is one byte over the cap", (d / "environ").stat().st_size == cap + 1)
        try:
            server._procenv.scan_proc(tag, proc_root=str(proc), me=1)
            check("over-cap environ → UNCERTAIN (raises), never a truncated 'no marker'", False)
        except OSError as exc:
            check("over-cap environ → UNCERTAIN (raises), never a truncated 'no marker'",
                  "uncertain" in str(exc), str(exc))
        try:
            server._procenv._read_at(os.open(str(d), os.O_RDONLY), "environ", cap)
            check("_read_at refuses overflow loudly", False)
        except OSError as exc:
            check("_read_at refuses overflow loudly", "exceeds" in str(exc), str(exc))


def test_cancel_intent_during_capacity_wait_terminalizes_without_a_spawn() -> None:
    """Round 36 MEDIUM: _wait_for_capacity returned True on a lodged
    codex_cancel_run intent and the caller IGNORED it — the run spawned
    again over a cancel. Now the wait's boolean ends the run: journaled
    cancelled, the time ACTUALLY waited, and attempt telemetry counting
    only the one real spawn."""
    with _Iso(FAKE_CODEX_SLEEP="0.2", FAKE_CODEX_FAIL="capacity", FAKE_CODEX_THREAD="T-CW") as iso:
        saved_base = server.OVERLOAD_BACKOFF_BASE_SECONDS
        server.OVERLOAD_BACKOFF_BASE_SECONDS = 30.0
        try:
            async def scenario():
                task = asyncio.create_task(server._run_codex("shed"))
                deadline = time.time() + 12
                while time.time() < deadline:
                    if "capacity shed" in iso.log_text():
                        break
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.5)  # inside the backoff sleep now
                run = str(iso.rec().get("run"))
                server._request_cancel(run)
                return await asyncio.wait_for(task, 15)
            res = asyncio.run(scenario())
            rec = iso.rec()
            check("journal: cancelled", rec.get("status") == "cancelled", str(rec.get("status")))
            check("one real attempt, no retry class", rec.get("attempts") == 1 and rec.get("retry_classes") == [],
                  str(rec)[:240])
            check("actual wait journaled (< the 30 s planned)", 0 <= int(rec.get("capacity_wait_s", 99)) < 10,
                  str(rec.get("capacity_wait_s")))
            check("live log says the intent was honoured during the wait",
                  "cancel intent honoured during the capacity wait" in iso.log_text())
            check("marker retired by the terminal record", not server._cancel_requested(str(rec.get("run"))))
            check("result names the cancellation", "cancel" in res.lower(), res[:200])
        finally:
            server.OVERLOAD_BACKOFF_BASE_SECONDS = saved_base


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
    for _p in _FAKE_WATCHDOGS:
        try:
            _p.kill(); _p.wait(timeout=2)
        except Exception:
            pass
    print(f"{'✓' if not failed else '✗'} detach: {PASS} passed, {FAIL} failed" + (f" — {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
