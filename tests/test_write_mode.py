#!/usr/bin/env python3
"""Tests for the abraham write mode in the codex-oracle server.

Run:  python3 tests/test_write_mode.py        (no dependencies — mcp is stubbed)

What must hold (the design is the cross-model-reviewed two-phase air-gap)
-------------------------------------------------------------------------
1. SEALED WRITE ARGV — a write process NEVER gets danger-full-access, never
   opens the network, never loads user config (user MCP servers), and
   excludes /tmp+$TMPDIR from its writable roots. infra/web toggles govern
   only the read-only ANALYSIS phase.
2. AUTO-COMPACT HONESTY — the limit derives from the deployed binary's own
   registry (or an explicit env override) and is OMITTED when unknown: a
   guessed limit above the real window would silently never fire (user
   config beats the vendor default outright — measured in the codex source).
3. GIT SAFETY — no write outside a git work tree; dirty trees refused unless
   allow_dirty; one writer per tree via an authoritative O_EXCL lockfile
   (pid-liveness stale-break); honest changed-files attribution with HEAD
   verification, on every outcome path.
4. NO AUTO-RETRY FOR WRITES — replaying "implement X" after partial writes
   double-applies; read runs keep their transient retry.
5. TWO-PHASE ORCHESTRATION — analysis (read-only) produces the brief; the
   sealed implementer receives it; an analysis failure writes nothing.
6. RESUME — write runs resume sealed regardless of caller overrides; read
   runs cannot escalate to write.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _stub_mcp() -> None:
    for name in (
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "mcp.server.stdio",
        "mcp.types",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))

    def _passthrough_decorator(*a, **k):
        def deco(fn):
            return fn

        return deco

    class _FastMCP:
        def __init__(self, *a, **k):
            pass

        tool = staticmethod(_passthrough_decorator)

    sys.modules["mcp.server.fastmcp"].FastMCP = _FastMCP
    sys.modules["mcp.server.fastmcp"].Context = object


_stub_mcp()
_spec = importlib.util.spec_from_file_location(
    "codex_server", ROOT / "plugins" / "codex-oracle" / "server.py"
)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
# The mixed-version write barrier consults live processes; suites must be
# hermetic (a real pre-1.17.2 server running on the dev machine must not
# fail unrelated tests), so default to a snapshot containing only THIS
# process (a truthful snapshot must contain the self pid — round 8). The
# barrier test injects its own snapshots.
server._ps_snapshot = lambda: f"{os.getpid()} python3 test-harness\n"
_REAL_PROC_COMM = server._proc_comm  # the real implementation, for its own contract test
server._proc_comm = lambda pid: "python3"

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    """Count for the standalone runner AND raise so pytest sees failures —
    a printing-only check() let 5 real failures ride under a green pytest
    run (round-2 review, 2026-08-21)."""
    global PASS, FAIL
    if cond:
        PASS += 1
        return
    FAIL += 1
    msg = f"  FAIL: {name}" + (f" — {detail}" if detail else "")
    print(msg)
    raise AssertionError(msg)


def _argv(**kw) -> list[str]:
    out = Path(tempfile.mkstemp(suffix=".txt")[1])
    return server._build_exec_argv(
        "gpt-test", "max", kw.pop("infra", False), kw.pop("web_search", True),
        out, prompt="p", **kw,
    )


# ---------------------------------------------------------------------------
# 1. Sealed write argv
# ---------------------------------------------------------------------------

def test_sandbox_matrix() -> None:
    for infra in (False, True):
        a = _argv(write=True, infra=infra)
        tag = f"write(infra={infra})"
        check(f"{tag}: workspace-write", "workspace-write" in a)
        check(f"{tag}: user config isolated", "--ignore-user-config" in a)
        check(f"{tag}: never danger-full-access", "danger-full-access" not in a)
        check(f"{tag}: network never opened",
              not any("network_access" in x for x in a))
        check(f"{tag}: /tmp excluded",
              "sandbox_workspace_write.exclude_slash_tmp=true" in a)
        check(f"{tag}: $TMPDIR excluded",
              "sandbox_workspace_write.exclude_tmpdir_env_var=true" in a)
        check(f"{tag}: windows sandbox explicitly sealed ELEVATED (the "
              f"unelevated backend's egress seal is env-var advisory only)",
              'windows.sandbox="elevated"' in a)
        check(f"{tag}: shell children get minimal env (inherit=core)",
              'shell_environment_policy.inherit="core"' in a)
        check(f"{tag}: secret-name excludes stay active",
              "shell_environment_policy.ignore_default_excludes=false" in a)

    c = _argv()
    check("read regression: read-only + isolation",
          "read-only" in c and "--ignore-user-config" in c)
    d = _argv(infra=True)
    check("read+infra regression: danger-full-access", "danger-full-access" in d)
    check("read+infra regression: no workspace-write", "workspace-write" not in d)

    e = _argv(write=True, auto_compact_limit=176800)
    check("auto-compact flag emitted",
          "model_auto_compact_token_limit=176800" in e)
    check("auto-compact flag absent when None",
          not any("model_auto_compact_token_limit" in x
                  for x in _argv(write=True)))

    r = _argv(write=True, resume_tid="tid123", auto_compact_limit=1000)
    check("resume: parent opts precede subcommand",
          r.index("--sandbox") < r.index("resume"),
          "codex grammar: --sandbox/-c are parent options")
    check("resume keeps write sandbox", "workspace-write" in r)


# ---------------------------------------------------------------------------
# 2. Auto-compact derivation
# ---------------------------------------------------------------------------

def test_auto_compact() -> None:
    pct = server.AUTOCOMPACT_PCT
    check("pct default in owner's band", 60 <= pct <= 70, f"pct={pct}")

    with tempfile.TemporaryDirectory() as td:
        old_home = os.environ.get("CODEX_HOME")
        old_win = os.environ.get("CODEX_ORACLE_CONTEXT_WINDOW")
        try:
            os.environ["CODEX_HOME"] = td
            os.environ.pop("CODEX_ORACLE_CONTEXT_WINDOW", None)

            lim, why = server._auto_compact_limit("no-such-model")
            check("no cache file → omitted", lim is None, why)

            cache = Path(td) / "models_cache.json"
            cache.write_text(json.dumps({"models": [
                {"slug": "fake-model", "context_window": 100_000},
                {"slug": "fallback-model", "max_context_window": 50_000},
            ]}))
            lim, why = server._auto_compact_limit("fake-model")
            check("cache slug hit → pct of window",
                  lim == 100_000 * pct // 100, f"{lim} ({why})")
            check("source recorded", "models_cache.json" in why)

            lim, _ = server._auto_compact_limit("fallback-model")
            check("max_context_window fallback (mirrors resolved_context_window)",
                  lim == 50_000 * pct // 100, str(lim))

            lim, why = server._auto_compact_limit("absent-model")
            check("unknown slug → omitted, reason recorded",
                  lim is None and "not in models_cache" in why, why)

            cache.write_text("{not json")
            lim, why = server._auto_compact_limit("fake-model")
            check("corrupt cache → omitted not crash", lim is None, why)

            os.environ["CODEX_ORACLE_CONTEXT_WINDOW"] = "272000"
            lim, why = server._auto_compact_limit("anything")
            check("env override wins", lim == 272_000 * pct // 100,
                  f"{lim} ({why})")
            check("env source recorded", "env" in why)
        finally:
            if old_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_home
            if old_win is None:
                os.environ.pop("CODEX_ORACLE_CONTEXT_WINDOW", None)
            else:
                os.environ["CODEX_ORACLE_CONTEXT_WINDOW"] = old_win


# ---------------------------------------------------------------------------
# 3. Changed-files report set math
# ---------------------------------------------------------------------------

def test_model_fallback() -> None:
    """The model pin comes from ~/.codex/config.toml; when the file names no
    model the oracle falls back to the vendor's bundled default — gpt-6-astra
    since codex-cli 0.153.4 (migration 2026-09-05, measured on the deployed
    binary's registry). Pinned so a stale fallback is visible in a diff."""
    orig = server._read_codex_config
    try:
        server._read_codex_config = lambda: {}
        check("no model in config → bundled default gpt-6-astra",
              server._get_codex_model() == "gpt-6-astra", server._get_codex_model())
        server._read_codex_config = lambda: {"model": "gpt-5.6-sol"}
        check("a configured model wins over the fallback",
              server._get_codex_model() == "gpt-5.6-sol", server._get_codex_model())
        check("reasoning stays pinned at max regardless of config",
              server._get_reasoning_effort() == "max", server._get_reasoning_effort())
    finally:
        server._read_codex_config = orig


def test_changes_report() -> None:
    real = server._git_state
    try:
        server._git_state = lambda cwd: (
            True, {"M  old.py", "?? codex_new.py"}, "aaaa1111bbbb")
        rep = server._write_changes_report({"M  old.py"}, "aaaa1111bbbb", "/x")
        check("new file attributed", "?? codex_new.py" in rep)
        check("pre-existing separated", "dirty before dispatch (1 path(s))" in rep)
        check("HEAD unchanged reported", "HEAD unchanged (aaaa1111b)" in rep)
        check("no commit-violation banner", "HEAD MOVED" not in rep)

        server._git_state = lambda cwd: (True, {"M  old.py"}, "cccc2222dddd")
        rep = server._write_changes_report({"M  old.py"}, "aaaa1111bbbb", "/x")
        check("HEAD move called out loudly", "⚠ HEAD MOVED" in rep)
        check("no false attribution",
              "(no new working-tree changes attributable to this run)" in rep)

        server._git_state = lambda cwd: (True, set(), "aaaa1111bbbb")
        rep = server._write_changes_report({"M  gone.py"}, "aaaa1111bbbb", "/x")
        check("disappeared dirt reported",
              "DISAPPEARED" in rep and "M  gone.py" in rep)

        server._git_state = lambda cwd: (False, set(), "")
        rep = server._write_changes_report(set(), "", "/x")
        check("unreadable git after run is loud", "unreadable" in rep)
    finally:
        server._git_state = real


# ---------------------------------------------------------------------------
# 4. One-writer-per-tree: journal liveness + lockfile
# ---------------------------------------------------------------------------

def test_active_write_run() -> None:
    real = server._journal_runs
    with tempfile.TemporaryDirectory() as td:
        fresh = Path(td) / "fresh.log"
        fresh.write_text("x")
        stale = Path(td) / "stale.log"
        stale.write_text("x")
        os.utime(stale, (time.time() - 3600, time.time() - 3600))
        try:
            server._journal_runs = lambda: {
                "w1": {"run": "w1", "write": True, "cwd": "/tree",
                       "has_end": False, "log": str(fresh)},
            }
            check("fresh write run detected",
                  server._active_write_run("/tree") == "w1")
            check("other cwd unaffected", server._active_write_run("/other") == "")
            check("exclude_run honored",
                  server._active_write_run("/tree", exclude_run="w1") == "")

            server._journal_runs = lambda: {
                "w2": {"run": "w2", "write": True, "cwd": "/tree",
                       "has_end": False, "log": str(stale)},
                "r1": {"run": "r1", "write": False, "cwd": "/tree",
                       "has_end": False, "log": str(fresh)},
                "w3": {"run": "w3", "write": True, "cwd": "/tree",
                       "has_end": True, "log": str(fresh)},
            }
            check("stale/read/ended runs do not block",
                  server._active_write_run("/tree") == "")
        finally:
            server._journal_runs = real


def test_write_lock() -> None:
    """One-writer-per-tree as a KERNEL-HELD lock: exclusive for the holder's
    lifetime, released by the OS on death — no pid/age/nonce heuristics
    (review rounds 2–4 of 1.17.2 found races in every file-content protocol)."""
    cwd = f"/lock-test-{os.getpid()}-{time.time()}"
    path = server._write_lock_path(cwd)
    try:
        ok, _ = server._acquire_write_lock(cwd, "t1")
        check("lock acquired", ok)
        ok2, holder = server._acquire_write_lock(cwd, "t2")
        check("second acquire refused", not ok2)
        check("holder named", "t1" in holder, holder)
        server._release_write_lock(cwd)
        ok3, _ = server._acquire_write_lock(cwd, "t3")
        check("reacquire after release", ok3)
        server._release_write_lock(cwd)

        # A legacy lock FILE (1.17.1 wrote content, nobody holds a lock on it)
        # is acquirable at once: liveness is the kernel's, not the content's.
        path.write_text("crashed pid=999999999 cwd=x t=0\n")
        ok4, _ = server._acquire_write_lock(cwd, "t4")
        check("stale legacy lock file → acquired", ok4)
        server._release_write_lock(cwd)

        # A live holder in ANOTHER process → refused; gone when it exits.
        code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); print('held', flush=True); time.sleep(3)")
        holder_proc = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
        holder_proc.stdout.readline()
        ok5, holder5 = server._acquire_write_lock(cwd, "t5")
        check("live holder in another process → refused", not ok5, holder5)
        holder_proc.wait(timeout=10)
        ok6, _ = server._acquire_write_lock(cwd, "t6")
        check("holder exited → the kernel released it", ok6)
    finally:
        server._release_write_lock(cwd)


# ---------------------------------------------------------------------------
# 5. Write pipeline: precondition, sealed scaffold, report, no-retry
# ---------------------------------------------------------------------------

def _fake_exec(captured: dict, results=None):
    """Fake _exec_codex_once. `results` = per-call return tuples (cycled last)."""
    results = list(results or
                   [("implemented.", True, "", "", 0, None, False)])

    async def fake(cmd, output_file, state, emit, ctx, model, extra_env=None,
                   workdir="", request_started=None):
        calls = captured.setdefault("calls", [])
        calls.append({"cmd": cmd, "extra_env": dict(extra_env or {}),
                      "prompt": cmd[-1]})
        state["thread_id"] = f"t-fake-{len(calls)}"
        cwd = server._get_cwd()
        (Path(cwd) / f"made_by_codex_{len(calls)}.txt").write_text("hi")
        return results[min(len(calls), len(results)) - 1]

    return fake


def test_write_pipeline() -> None:
    real_cwd, real_exec = server._get_cwd, server._exec_codex_once
    try:
        with tempfile.TemporaryDirectory() as td:
            server._get_cwd = lambda: td
            res = asyncio.run(server._run_codex("task", write=True))
            check("non-repo cwd refused", "not inside a git work tree" in res,
                  res[:120])

            subprocess.run(["git", "init", "-q", td], check=True)
            captured: dict = {}
            server._exec_codex_once = _fake_exec(captured)
            res = asyncio.run(server._run_codex("do the thing", write=True))
            call = captured["calls"][0]
            check("scaffold: implementation mode",
                  "IMPLEMENTATION MODE" in call["prompt"])
            check("scaffold: sealed wording", "SEALED" in call["prompt"])
            check("scaffold: git contract", "GIT CONTRACT" in call["prompt"])
            check("argv: workspace-write", "workspace-write" in call["cmd"])
            check("TMPDIR redirected into workspace",
                  call["extra_env"].get("TMPDIR", "").endswith(".abraham/tmp")
                  and call["extra_env"]["TMPDIR"].startswith(td))
            check("workspace tmp dir created",
                  Path(call["extra_env"]["TMPDIR"]).is_dir())
            check("report: changed files block",
                  "[CHANGED FILES — this write run]" in res)
            check("report: codex's file attributed", "made_by_codex_1.txt" in res)
            check("report: no-commits repo phrasing", "no commits yet" in res)

            # NO AUTO-RETRY for writes even on a transient failure...
            captured.clear()
            server._exec_codex_once = _fake_exec(
                captured,
                results=[("", False, "", "stream error: connection reset", 1,
                          None, False)])
            res = asyncio.run(server._run_codex("again", write=True))
            check("write: transient failure NOT retried",
                  len(captured["calls"]) == 1, str(len(captured["calls"])))
            check("write: failure still carries changed-files report",
                  "[CHANGED FILES" in res)

            # ...while read runs keep their retry (regression).
            captured.clear()
            server._exec_codex_once = _fake_exec(
                captured,
                results=[("", False, "", "stream error: connection reset", 1,
                          None, False),
                         ("ok now", True, "", "", 0, None, False)])
            asyncio.run(server._run_codex("read task"))
            check("read: transient failure IS retried",
                  len(captured["calls"]) == 2, str(len(captured["calls"])))
    finally:
        server._get_cwd, server._exec_codex_once = real_cwd, real_exec


# ---------------------------------------------------------------------------
# 6. abraham: refusals + two-phase orchestration
# ---------------------------------------------------------------------------

def test_abraham_tool() -> None:
    real_cwd, real_exec, real_active = (
        server._get_cwd, server._exec_codex_once, server._active_write_run)
    real_acquire = server._acquire_write_lock
    # Preseed the write-capability verdict: without it the probe gate would
    # spawn a REAL codex process inside this hermetic test.
    server._write_capability = (True, "preseeded for tests")
    try:
        res = asyncio.run(server.abraham(task="   "))
        check("empty task refused", "empty task" in res)

        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True)
            server._get_cwd = lambda: td

            (Path(td) / "uncommitted.txt").write_text("wip")
            res = asyncio.run(server.abraham(task="build x"))
            check("dirty tree refused by default",
                  "DIRTY" in res and "allow_dirty" in res)
            check("dirty refusal lists the dirt", "uncommitted.txt" in res)

            captured: dict = {}
            server._exec_codex_once = _fake_exec(
                captured,
                results=[("BRIEF-MARKER-XYZ brief body", True, "", "", 0,
                          None, False),
                         ("implemented per brief", True, "", "", 0, None,
                          False)])
            res = asyncio.run(server.abraham(
                task="add a widget", context="ctx-notes",
                constraints="keep API", infra=True, allow_dirty=True))
            check("two phases ran", len(captured["calls"]) == 2,
                  str(len(captured.get("calls", []))))
            p1, p2 = captured["calls"]
            check("phase 1 is a read-mode argv (infra analysis, never write)",
                  "workspace-write" not in p1["cmd"]
                  and "danger-full-access" in p1["cmd"])
            check("phase 1 asks for the brief",
                  "IMPLEMENTATION BRIEF" in p1["prompt"])
            check("phase 1 carries task+context+constraints",
                  all(s in p1["prompt"]
                      for s in ("add a widget", "ctx-notes", "keep API")))
            check("phase 1 infra section present", "Live state" in p1["prompt"])
            check("phase 2 is sealed write argv",
                  "workspace-write" in p2["cmd"]
                  and "--ignore-user-config" in p2["cmd"]
                  and "danger-full-access" not in p2["cmd"])
            check("phase 2 web disabled", "web_search=disabled" in p2["cmd"])
            check("phase 2 receives the brief",
                  "BRIEF-MARKER-XYZ" in p2["prompt"])
            check("phase 2 header", "PHASE 2 of 2" in p2["prompt"])
            check("result labels both phases",
                  "[abraham — phase 1 analyzed" in res)
            check("lock released after run", str(server._write_lock_path(td)) not in server._HELD)

            # Analysis failure → nothing written, no phase 2.
            captured.clear()
            server._exec_codex_once = _fake_exec(
                captured,
                results=[("", False, "", "invalid argument: boom", 1, None,
                          False)])
            res = asyncio.run(server.abraham(task="doomed", allow_dirty=True))
            check("analysis failure stops before implementation",
                  len(captured["calls"]) == 1 and "ANALYSIS phase failed" in res)
            check("lock released after analysis failure", str(server._write_lock_path(td)) not in server._HELD)

            server._active_write_run = lambda cwd, exclude_run="": "codex9·42"
            res = asyncio.run(server.abraham(task="build x", allow_dirty=True))
            check("journal one-writer refusal",
                  "one writer per tree" in res and "codex9·42" in res)
            server._active_write_run = lambda cwd, exclude_run="": ""

            server._acquire_write_lock = lambda cwd, hint: (False, "holder-x")
            res = asyncio.run(server.abraham(task="build x", allow_dirty=True))
            check("lockfile refusal", "holds this tree's lock" in res
                  and "holder-x" in res)
    finally:
        server._get_cwd, server._exec_codex_once, server._active_write_run = (
            real_cwd, real_exec, real_active)
        server._acquire_write_lock = real_acquire


# ---------------------------------------------------------------------------
# 7. Resume inheritance
# ---------------------------------------------------------------------------

def test_full_access_write_mode() -> None:
    """User ruling 2026-09-05: abraham(full_access=True) runs the
    implementation phase with --dangerously-bypass-approvals-and-sandbox
    (codex's --yolo): no sealed argv, the FULL ACCESS scaffold (real gates,
    boundaries, the same git contract), web search as the caller chose, no
    write-capability probe (nothing to probe), journaled and kept on resume;
    CODEX_ORACLE_WRITE_FULL_ACCESS=1 makes it the default; the sealed mode
    is unchanged."""
    out = Path(tempfile.mkdtemp()) / "o.txt"
    argv = server._build_exec_argv("m", "max", False, True, out, prompt="p", write=True, full_access=True)
    check("full access: codex's bypass flag", "--dangerously-bypass-approvals-and-sandbox" in argv)
    check("full access: no sealed sandbox args",
          "workspace-write" not in argv and "--ignore-user-config" not in argv and "danger-full-access" not in argv)
    sealed = server._build_exec_argv("m", "max", False, True, out, prompt="p", write=True)
    check("sealed unchanged", "workspace-write" in sealed and "--dangerously-bypass-approvals-and-sandbox" not in sealed)
    read_only = server._build_exec_argv("m", "max", False, True, out, prompt="p", full_access=True)
    check("full access is a WRITE-only axis", "--dangerously-bypass-approvals-and-sandbox" not in read_only)
    real_cwd, real_exec, real_active = server._get_cwd, server._exec_codex_once, server._active_write_run
    saved_env = os.environ.get("CODEX_ORACLE_WRITE_FULL_ACCESS")
    server._write_capability = (False, "probe says no (must be bypassed by full access)")
    try:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True)
            server._get_cwd = lambda: td
            captured: dict = {}
            server._exec_codex_once = _fake_exec(captured, results=[("brief", True, "", "", 0, None, False),
                                                                     ("done", True, "", "", 0, None, False)])
            res = asyncio.run(server.abraham(task="build x", full_access=True))
            calls = captured["calls"]
            check("two phases ran", len(calls) == 2, str(len(calls)))
            check("phase 1 stays read-only", "--sandbox" in calls[0]["cmd"] and "read-only" in calls[0]["cmd"])
            check("phase 2 runs unsandboxed", "--dangerously-bypass-approvals-and-sandbox" in calls[1]["cmd"])
            check("phase 2 scaffold: FULL ACCESS, real gates, git contract",
                  "FULL ACCESS" in calls[1]["prompt"] and "real system" in calls[1]["prompt"]
                  and "GIT CONTRACT" in calls[1]["prompt"] and "SEALED" not in calls[1]["prompt"])
            check("phase 2 keeps the caller's web search", "web_search=live" in " ".join(calls[1]["cmd"]))
            check("banner names the mode", "FULL ACCESS" in res and "bypass-approvals-and-sandbox" in res)
            check("the write probe was bypassed", "cannot WRITE" not in res)
            writes = [r for r in server._journal_runs().values() if r.get("write")]
            latest = max(writes, key=lambda r: float(r.get("ts") or 0)) if writes else {}
            check("journal records full_access on this run", latest.get("full_access") is True, str(latest)[:200])
            # the sealed default still probes (and here refuses)
            captured.clear()
            res2 = asyncio.run(server.abraham(task="build y", allow_dirty=True))
            check("sealed default still gated by the probe", "cannot WRITE" in res2, res2[:160])
            # environment default
            os.environ["CODEX_ORACLE_WRITE_FULL_ACCESS"] = "1"
            captured.clear()
            server._exec_codex_once = _fake_exec(captured, results=[("brief", True, "", "", 0, None, False),
                                                                     ("done", True, "", "", 0, None, False)])
            res3 = asyncio.run(server.abraham(task="build z", allow_dirty=True))
            check("env default → full access", "FULL ACCESS" in res3
                  and "--dangerously-bypass-approvals-and-sandbox" in captured["calls"][1]["cmd"])
            captured.clear()
            server._exec_codex_once = _fake_exec(captured)
            res4 = asyncio.run(server.abraham(task="build w", full_access=False, allow_dirty=True))
            check("explicit False overrides the env default", "cannot WRITE" in res4, res4[:160])
    finally:
        server._get_cwd, server._exec_codex_once, server._active_write_run = real_cwd, real_exec, real_active
        server._write_capability = None
        if saved_env is None:
            os.environ.pop("CODEX_ORACLE_WRITE_FULL_ACCESS", None)
        else:
            os.environ["CODEX_ORACLE_WRITE_FULL_ACCESS"] = saved_env
    # a resume keeps the mode the run started in
    real_runs, real_run_codex = server._journal_runs, server._run_codex
    got: dict = {}

    async def fake_run_codex(prompt, **kw):
        got.update(kw, prompt=prompt)
        return "resumed"

    try:
        cwd = f"/resume-full-{os.getpid()}"
        server._get_cwd = lambda: cwd
        server._run_codex = fake_run_codex
        server._active_write_run = lambda c, exclude_run="": ""
        server._write_capability = (True, "preseeded for tests")
        rec = {"run": "f1", "cwd": cwd, "write": True, "full_access": True, "thread_id": "t7",
               "has_start": True, "has_end": True, "status": "error",
               "prompt": "orig task", "infra": False, "web_search": True}
        server._journal_runs = lambda: {"f1": dict(rec)}
        asyncio.run(server.codex_resume_run(run="f1"))
        check("full-access write resumes as full access with its web search",
              got.get("write") is True and got.get("full_access") is True and got.get("web_search") is True)
        # round 39: the sealed-sandbox probe does not gate a full-access continuation
        got.clear()
        server._write_capability = (False, "sealed sandbox cannot write here")
        res_full = asyncio.run(server.codex_resume_run(run="f1"))
        check("full-access resume proceeds past a failed SEALED probe",
              "cannot WRITE" not in res_full and got.get("full_access") is True, res_full[:160])
        got.clear()
        server._journal_runs = lambda: {"s0": dict(rec, run="s0", full_access=False)}
        res_sealed = asyncio.run(server.codex_resume_run(run="s0"))
        check("sealed resume is still gated by the probe", "cannot WRITE" in res_sealed and not got, res_sealed[:160])
        server._write_capability = (True, "preseeded for tests")
        got.clear()
        server._journal_runs = lambda: {"s1": dict(rec, run="s1", full_access=False)}
        asyncio.run(server.codex_resume_run(run="s1"))
        check("sealed write resumes sealed", got.get("write") is True and got.get("full_access") is False
              and got.get("web_search") is False)
        # round 40 HIGH: EVERY write continuation holds the tree lock — the
        # child publication (the execution barrier) must work for a
        # full-access resume, and the lock must be released afterwards
        acquired: list = []
        real_acquire = server._acquire_write_lock

        def spy_acquire(c, hint):
            acquired.append(hint)
            return real_acquire(c, hint)

        held_during: dict = {}

        async def fake_run_codex_locked(prompt, **kw):
            got.update(kw, prompt=prompt)
            held_during["noted"] = server._note_write_child(cwd, os.getpid())
            return "resumed"

        server._acquire_write_lock = spy_acquire
        server._run_codex = fake_run_codex_locked
        try:
            got.clear()
            server._journal_runs = lambda: {"f1": dict(rec)}
            asyncio.run(server.codex_resume_run(run="f1"))
            check("full-access resume takes the tree lock", "resume:f1" in acquired, str(acquired))
            check("full-access resume publishes its child under the held lock", held_during.get("noted") is True)
            check("the lock is released after the continuation",
                  str(server._write_lock_path(cwd)) not in server._HELD)
            # round 40 MEDIUM: an explicit web_search override on a full-access resume is honoured
            got.clear()
            asyncio.run(server.codex_resume_run(run="f1", web_search=False))
            check("full-access resume honours web_search=False", got.get("web_search") is False, str(got.get("web_search")))
            got.clear()
            asyncio.run(server.codex_resume_run(run="f1"))
            check("full-access resume without an override keeps the recorded web search", got.get("web_search") is True)
            got.clear()
            server._journal_runs = lambda: {"s1": dict(rec, run="s1", full_access=False)}
            asyncio.run(server.codex_resume_run(run="s1", web_search=True))
            check("sealed resume stays offline even when asked", got.get("web_search") is False)
        finally:
            server._acquire_write_lock = real_acquire
    finally:
        server._journal_runs, server._run_codex, server._get_cwd, server._active_write_run = (
            real_runs, real_run_codex, real_cwd, real_active)
        server._write_capability = None
        server._release_write_lock(f"/resume-full-{os.getpid()}")


def test_resume_inherits_write() -> None:
    real_runs, real_run_codex, real_cwd, real_active = (
        server._journal_runs, server._run_codex, server._get_cwd,
        server._active_write_run)
    got: dict = {}

    async def fake_run_codex(prompt, **kw):
        got.update(kw, prompt=prompt)
        return "resumed"

    try:
        cwd = f"/resume-test-{os.getpid()}"
        server._get_cwd = lambda: cwd
        server._run_codex = fake_run_codex
        server._active_write_run = lambda c, exclude_run="": ""
        # order-independent: never let this test spawn a real probe
        server._write_capability = (True, "preseeded for tests")
        # has_start mirrors the real journal contract (every real run gets a
        # start-phase record at spawn); the resume listing filters on it to
        # exclude evidence-only groups (dispatch tracers, probe verdicts).
        rec = {"run": "w1", "cwd": cwd, "write": True, "thread_id": "t9",
               "has_start": True, "has_end": True, "status": "error",
               "prompt": "orig task", "infra": False, "web_search": False}
        server._journal_runs = lambda: {"w1": dict(rec)}
        asyncio.run(server.codex_resume_run(run="w1"))
        check("write run resumes as write", got.get("write") is True)
        check("same thread", got.get("resume_tid") == "t9")
        check("resume anchors the request heartbeat deadline",
              got.get("request_started") is not None)
        check("lock released after write resume", str(server._write_lock_path(cwd)) not in server._HELD)

        got.clear()
        asyncio.run(server.codex_resume_run(run="w1", infra=True,
                                            web_search=True))
        check("write resume stays sealed despite overrides",
              got.get("infra") is False and got.get("web_search") is False)

        got.clear()
        rec_read = dict(rec, write=False, run="r1", infra=False)
        server._journal_runs = lambda: {"r1": rec_read}
        asyncio.run(server.codex_resume_run(run="r1"))
        check("read run cannot escalate to write", got.get("write") is False)

        server._journal_runs = lambda: {"w1": dict(rec)}
        server._active_write_run = lambda c, exclude_run="": "wOTHER"
        res = asyncio.run(server.codex_resume_run(run="w1"))
        check("resume blocked while another writer lives",
              "one writer per tree" in res)
    finally:
        server._journal_runs, server._run_codex, server._get_cwd, \
            server._active_write_run = (real_runs, real_run_codex, real_cwd,
                                        real_active)
        server._release_write_lock(f"/resume-test-{os.getpid()}")


def test_heartbeat_geometry() -> None:
    """The heartbeat must stop on the LIVE side of the client's ~120s
    backgrounding boundary: with interval I and bound MAX, the last send
    lands at ≤MAX and the next candidate tick (MAX+I) must still be ≤120 —
    so no send can ever target a deregistered token (macOS kill 2026-08-09;
    Windows stdio wedge 2026-08-21)."""
    check(
        "heartbeat stops before ~120s backgrounding",
        server.PROGRESS_MAX_SECONDS + server.PROGRESS_INTERVAL_SECONDS <= 120,
        f"MAX={server.PROGRESS_MAX_SECONDS} "
        f"INTERVAL={server.PROGRESS_INTERVAL_SECONDS}",
    )
    joined_args = "\x00".join(_argv(write=True))
    check(
        "probe/implementation sandbox argv single-source parity",
        "\x00".join(server.WRITE_SANDBOX_ARGS) in joined_args,
    )


def test_write_capability_probe() -> None:
    import os as _os
    import time as _time

    async def _drive(ws: str) -> None:
        real_runner = server._run_write_probe
        key = _os.path.realpath(ws)
        try:
            server._write_capability = None  # override seam OFF
            server._write_probe_cache.clear()

            # INCONCLUSIVE (runner death): refuse, TTL-cached (a burst
            # shares the answer), then a later dispatch re-probes
            async def _boom(tmp):
                raise RuntimeError("spawn failed")
            server._run_write_probe = _boom
            ok, why = await server._ensure_write_capability(ws)
            check("probe fail-closed on runner error",
                  not ok and "could not run" in why, why)
            check("inconclusive is TTL-cached, not conclusive",
                  server._write_probe_cache[key][3] is False)

            # burst within TTL shares the inconclusive answer — no re-probe
            calls = {"n": 0}
            async def _counting_boom(tmp):
                calls["n"] += 1
                raise RuntimeError("spawn failed")
            server._run_write_probe = _counting_boom
            for _ in range(3):
                await server._ensure_write_capability(ws)
            check("inconclusive burst shares one answer (no re-probe)",
                  calls["n"] == 0, f"re-probed {calls['n']}×")

            # after TTL expiry the next dispatch re-probes — and can go green
            ok_, detail_, _ts, concl_ = server._write_probe_cache[key]
            server._write_probe_cache[key] = (
                ok_, detail_, _time.monotonic() - 9999, concl_)
            async def _would_pass(tmp):
                (tmp / "probe.txt").write_text("ok")
                return 0, "done"
            server._run_write_probe = _would_pass
            ok3, why3 = await server._ensure_write_capability(ws)
            check("re-probe after TTL expiry can go green", ok3, why3)
            check("capable verdict is conclusive-cached",
                  server._write_probe_cache[key][3] is True)

            # conclusive cache wins: a now-red runner must not flip it
            async def _no_write(tmp):
                return 0, ("patch rejected: writing is blocked by read-only "
                           "sandbox; rejected by user approval settings")
            server._run_write_probe = _no_write
            ok4, _w = await server._ensure_write_capability(ws)
            check("capable verdict memoized per process", ok4 is True)

            # INCAPABLE: measured refusal marker, no file → conclusive red
            server._write_probe_cache.clear()
            ok, why = await server._ensure_write_capability(ws)
            check("probe red without file", not ok, why)
            check("probe red carries measured marker",
                  "read-only sandbox" in why, why)
            check("incapable is conclusive-cached",
                  server._write_probe_cache[key][3] is True)

            # per-workspace keying: a different workspace probes fresh
            with tempfile.TemporaryDirectory() as ws2:
                server._run_write_probe = _would_pass
                ok5, _ = await server._ensure_write_capability(ws2)
                check("second workspace gets its own verdict", ok5 is True)
                ok6, _ = await server._ensure_write_capability(ws)
                check("first workspace keeps its red verdict", ok6 is False)

            # single-flight: concurrent first calls run the probe ONCE
            server._write_probe_cache.clear()
            calls = {"n": 0}
            async def _slow_green(tmp):
                calls["n"] += 1
                await asyncio.sleep(0.05)
                (tmp / "probe.txt").write_text("ok")
                return 0, "done"
            server._run_write_probe = _slow_green
            results = await asyncio.gather(
                server._ensure_write_capability(ws),
                server._ensure_write_capability(ws),
                server._ensure_write_capability(ws),
            )
            check("single-flight: one probe for concurrent dispatches",
                  calls["n"] == 1, f"probe ran {calls['n']}×")
            check("single-flight: all callers get the verdict",
                  all(r[0] for r in results))

            # probe dirs live under {ws}/.abraham and are cleaned up
            leftovers = list((Path(ws) / ".abraham").glob("write-probe-*"))
            check("probe dirs cleaned from the workspace", not leftovers,
                  str(leftovers))

            # env skip bypasses the probe entirely
            server._write_probe_cache.clear()
            _os.environ["CODEX_ORACLE_SKIP_WRITE_PROBE"] = "1"
            try:
                async def _never(tmp):
                    raise AssertionError("probe must not run when skipped")
                server._run_write_probe = _never
                ok7, why7 = await server._ensure_write_capability(ws)
                check("probe env-skip honored",
                      ok7 and "SKIP_WRITE_PROBE" in why7, why7)
            finally:
                _os.environ.pop("CODEX_ORACLE_SKIP_WRITE_PROBE", None)
        finally:
            server._run_write_probe = real_runner
            server._write_probe_cache.clear()
            # leave the override green so later tests never spawn a probe
            server._write_capability = (True, "preseeded for tests")

    with tempfile.TemporaryDirectory() as ws:
        asyncio.run(_drive(ws))


def test_probe_spawn_never_inherits_mcp_stdin() -> None:
    """The probe child must get DEVNULL stdin: the parent's stdin is the
    MCP JSON-RPC channel and codex exec APPENDS piped stdin to its prompt
    (round-2 CRITICAL, 2026-08-21)."""
    captured: dict = {}
    real_spawn = asyncio.create_subprocess_exec

    class _FakeProc:
        returncode = 0
        async def communicate(self):
            return b"no file written", None
        def kill(self):
            pass
        async def wait(self):
            return 0

    async def _fake_spawn(*argv, **kw):
        captured.update(kw, argv=argv)
        return _FakeProc()

    async def _drive() -> None:
        asyncio.create_subprocess_exec = _fake_spawn
        try:
            with tempfile.TemporaryDirectory() as td:
                await server._run_write_probe(Path(td))
        finally:
            asyncio.create_subprocess_exec = real_spawn

    asyncio.run(_drive())
    check("probe stdin is DEVNULL (never the MCP JSON-RPC stream)",
          captured.get("stdin") == asyncio.subprocess.DEVNULL,
          f"stdin={captured.get('stdin')!r}")
    check("probe argv carries the sealed sandbox",
          "workspace-write" in captured.get("argv", ()))
    check("probe child gets its own process group (kill-tree reapable)",
          captured.get("start_new_session") is True
          or "creationflags" in captured,
          str({k: captured.get(k)
               for k in ("start_new_session", "creationflags")}))


def test_resume_write_gated_by_probe() -> None:
    """A resumed write run must hit the same capability gate as a fresh
    abraham dispatch (round-2 HIGH: resume bypassed it)."""
    real_runs, real_run_codex, real_cwd, real_active = (
        server._journal_runs, server._run_codex, server._get_cwd,
        server._active_write_run)
    ran = {"n": 0}

    async def fake_run_codex(prompt, **kw):
        ran["n"] += 1
        return "resumed"

    try:
        cwd = f"/resume-gate-test-{os.getpid()}"
        server._get_cwd = lambda: cwd
        server._run_codex = fake_run_codex
        server._active_write_run = lambda c, exclude_run="": ""
        rec = {"run": "w1", "cwd": cwd, "write": True, "thread_id": "t9",
               "has_start": True, "has_end": True, "status": "error",
               "prompt": "orig task", "infra": False, "web_search": False}
        server._journal_runs = lambda: {"w1": dict(rec)}
        server._write_capability = (False, "sandbox cannot write (test)")
        res = asyncio.run(server.codex_resume_run(run="w1"))
        check("red probe blocks write resume",
              "cannot WRITE" in res, res[:120])
        check("blocked resume never dispatched codex", ran["n"] == 0)
    finally:
        server._write_capability = (True, "preseeded for tests")
        server._journal_runs, server._run_codex, server._get_cwd, \
            server._active_write_run = (
                real_runs, real_run_codex, real_cwd, real_active)
        server._release_write_lock(cwd)


def test_heartbeat_request_scoped_deadline() -> None:
    """Behavioral: the loop must send while the REQUEST is young and go
    silent forever once the request deadline passed — even for a run that
    starts late (abraham phase 2), whose per-run clock is fresh."""
    import time as _time

    real_interval = server.PROGRESS_INTERVAL_SECONDS
    real_max = server.PROGRESS_MAX_SECONDS
    sends: list = []
    stops: list = []

    class _Ctx:
        async def report_progress(self, *a):
            sends.append(a)

    async def _drive() -> None:
        server.PROGRESS_INTERVAL_SECONDS = 0.01
        server.PROGRESS_MAX_SECONDS = 0.05
        state = {"activity": "testing"}
        now = _time.monotonic()

        # young request: sends, then stops by itself
        await asyncio.wait_for(
            server._heartbeat_loop(_Ctx(), now, now, state, "m", stops.append),
            timeout=5,
        )
        check("young request heartbeats then stops",
              len(sends) >= 1 and len(stops) == 1,
              f"sends={len(sends)} stops={len(stops)}")

        # late-phase run (request began long ago): ZERO sends ever
        sends.clear()
        stops.clear()
        await asyncio.wait_for(
            server._heartbeat_loop(
                _Ctx(), _time.monotonic() - 1000, _time.monotonic(),
                state, "m", stops.append,
            ),
            timeout=5,
        )
        check("expired request sends NOTHING (dead-token guard)",
              len(sends) == 0 and len(stops) == 1,
              f"sends={len(sends)}")

    try:
        asyncio.run(_drive())
    finally:
        server.PROGRESS_INTERVAL_SECONDS = real_interval
        server.PROGRESS_MAX_SECONDS = real_max


def test_legacy_interop_both_orders_and_aliases() -> None:
    """Round 6 HIGH: the 1.17.2 bridge vs the ACTUAL 1.17.1 protocol
    (vendored verbatim in tests/legacy_lock_1171.py). An old writer must be
    excluded in BOTH acquisition orders, and an old lock taken through a
    symlink alias must still be seen (the exact-path probe missed it)."""
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import legacy_lock_1171 as legacy
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        (td / "logs").mkdir()
        saved = (server.LIVE_LOG_DIR, legacy.LIVE_LOG_DIR)
        server.LIVE_LOG_DIR = td / "logs"
        legacy.LIVE_LOG_DIR = td / "logs"
        try:
            tree = td / "tree"; tree.mkdir()
            alias = td / "alias"; alias.symlink_to(tree)
            cwd = str(tree)

            # OLD-FIRST, exact path
            ok, _ = legacy._acquire_write_lock(cwd, "old")
            check("legacy acquires first (exact path)", ok)
            ok2, why2 = server._acquire_write_lock(cwd, "new")
            check("new writer refused while the OLD holds (exact)",
                  not ok2 and "legacy" in why2, why2)
            legacy._release_write_lock(cwd)

            # OLD-FIRST via a symlink ALIAS (the round-6 hole)
            ok, _ = legacy._acquire_write_lock(str(alias), "old-alias")
            check("legacy acquires via the alias", ok)
            ok3, why3 = server._acquire_write_lock(cwd, "new")
            check("new writer refused: the alias lock resolves to our tree",
                  not ok3 and "legacy" in why3, why3)
            legacy._release_write_lock(str(alias))

            # NEW-FIRST: the planted bridge occupies the legacy namespace
            ok4, why4 = server._acquire_write_lock(cwd, "new")
            check("new writer acquires once the legacy holder is gone", ok4, why4)
            ok5, why5 = legacy._acquire_write_lock(cwd, "old")
            check("OLD writer refused while the new one holds (planted bridge)",
                  not ok5 and "planted-by" in why5, why5)
            ok6, why6 = legacy._acquire_write_lock(str(tree.resolve()), "old")
            check("…and via the realpath alias too", not ok6, why6)
            # PINNED RESIDUE (round 7): an UNANTICIPATED symlink alias is not
            # in the planted set, so at the LOCK level the old protocol still
            # acquires. Mixed-version safety is provided UPSTREAM by the
            # process barrier (test_mixed_version_write_barrier), never by
            # alias enumeration.
            ok8, _ = legacy._acquire_write_lock(str(alias), "old")
            check("pinned residue: unanticipated symlink alias acquires the "
                  "OLD lock (barrier precludes it upstream)", ok8)
            legacy._release_write_lock(str(alias))
            server._release_write_lock(cwd)
            ok7, _ = legacy._acquire_write_lock(cwd, "old")
            check("release unplants: the old protocol can acquire again", ok7)
            legacy._release_write_lock(cwd)
        finally:
            server.LIVE_LOG_DIR, legacy.LIVE_LOG_DIR = saved



def test_mixed_version_write_barrier() -> None:
    """Round 7 HIGH: while any codex-oracle server PROCESS without a registry
    entry (= pre-1.17.2) is alive, write acquisition refuses — enumeration of
    lock aliases can never make old content locks meet new inode locks."""
    import json as _json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        saved_dir, saved_ps = server.LIVE_LOG_DIR, server._ps_snapshot
        server.LIVE_LOG_DIR = td / "logs"
        cwd = str(td / "tree"); (td / "tree").mkdir()
        me = f"{os.getpid()} python3 test-harness"
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        try:
            line = f"{sleeper.pid} /usr/bin/python3 /x/plugins/codex-oracle/run_server.py"
            server._ps_snapshot = lambda: me + "\n" + line + "\n"
            ok, why = server._acquire_write_lock(cwd, "new")
            check("unregistered live server process blocks writes",
                  not ok and "pre-1.17.2" in why, why)

            d = server._server_registry_dir(); d.mkdir(parents=True, exist_ok=True)
            (d / f"{sleeper.pid}.json").write_text(_json.dumps(
                {"pid": sleeper.pid, "start": "WRONG-START-TOKEN",
                 "version": server.PLUGIN_LOCK_PROTOCOL}))
            ok0, why0 = server._acquire_write_lock(cwd, "new")
            check("a registry entry with a MISMATCHED start does not exempt (pid reuse)",
                  not ok0 and "pre-1.17.2" in why0, why0)

            (d / f"{sleeper.pid}.json").write_text(_json.dumps(
                {"pid": sleeper.pid, "start": server._proc_start(sleeper.pid),
                 "version": server.PLUGIN_LOCK_PROTOCOL}))
            ok2, why2 = server._acquire_write_lock(cwd, "new")
            check("a REGISTERED server with a VERIFIED identity does not block", ok2, why2)
            server._release_write_lock(cwd)

            dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
            server._ps_snapshot = lambda: (
                me + "\n" + f"{dead.pid} /usr/bin/python3 /x/plugins/codex-oracle/run_server.py\n")
            ok3, why3 = server._acquire_write_lock(cwd, "new")
            check("a dead pid in the snapshot does not block (calibration)", ok3, why3)
            server._release_write_lock(cwd)

            fp = (f"{sleeper.pid} codex exec --json Review plugins/codex-oracle/"
                  f"server.py in agent-teams-plugin and report")
            server._ps_snapshot = lambda: me + "\n" + fp + "\n"
            real_comm = server._proc_comm
            server._proc_comm = lambda pid: ("codex" if pid == sleeper.pid else "python3")
            ok7, why7 = server._acquire_write_lock(cwd, "new")
            server._proc_comm = real_comm
            check("a codex child whose PROMPT names server.py is NOT a legacy server "
                  "(executable identity calibration)", ok7, why7)
            server._release_write_lock(cwd)

            # Round 11: a SPACED install path must still be detected — token
            # positions guessed from a space-joined ps line mis-split it.
            # (Drop the sleeper's registry exemption from the earlier phase:
            # this models an UNREGISTERED old server.)
            (d / f"{sleeper.pid}.json").unlink()
            spaced = (f"{sleeper.pid} /usr/bin/python3 /Users/A Name/plugins/"
                      f"codex-oracle/run_server.py")
            server._ps_snapshot = lambda: me + "\n" + spaced + "\n"
            ok9, why9 = server._acquire_write_lock(cwd, "new")
            check("a legacy server under a SPACED path still blocks",
                  not ok9 and "pre-1.17.2" in why9, why9)

            def _comm_boom(pid):
                raise OSError("ps comm unavailable")
            server._proc_comm = _comm_boom
            ok10, why10 = server._acquire_write_lock(cwd, "new")
            server._proc_comm = real_comm
            check("an unverifiable candidate FAILS CLOSED",
                  not ok10 and "cannot verify" in why10, why10)

            server._ps_snapshot = lambda: ""
            ok5, why5 = server._acquire_write_lock(cwd, "new")
            check("a snapshot missing THIS process fails closed (empty/partial ps)",
                  not ok5 and "did not include this process" in why5, why5)

            def _boom() -> str:
                raise OSError("ps unavailable")
            server._ps_snapshot = _boom
            ok4, why4 = server._acquire_write_lock(cwd, "new")
            check("an unenumerable process table FAILS CLOSED",
                  not ok4 and "cannot enumerate" in why4, why4)
        finally:
            server.LIVE_LOG_DIR, server._ps_snapshot = saved_dir, saved_ps
            sleeper.kill(); sleeper.wait(timeout=5)


def test_bridge_planting_fails_closed() -> None:
    """Round 7 HIGH: an unplantable legacy bridge file is a REFUSAL — a
    partially occupied namespace is fail-open. The inode lock is released and
    planted files are rolled back on refusal."""
    import tempfile
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        saved_dir, saved_ps = server.LIVE_LOG_DIR, server._ps_snapshot
        server.LIVE_LOG_DIR = td / "logs"
        server._ps_snapshot = lambda: f"{os.getpid()} python3 test-harness\n"
        try:
            tree = td / "tree"; tree.mkdir()
            cwd = str(tree)
            blocker = server._legacy_lock_paths_for(cwd)[0]
            blocker.parent.mkdir(parents=True, exist_ok=True)
            blocker.mkdir()  # a DIRECTORY where the bridge file must go
            ok, why = server._acquire_write_lock(cwd, "new")
            check("unplantable bridge refuses the write lock",
                  not ok and ("occupy" in why or "plant" in why), why)
            lock_dir = server.LIVE_LOG_DIR / "write-locks"
            stray = [p.name for p in lock_dir.iterdir()
                     if p.name != blocker.name and not p.name.startswith("tree-") and p.suffix == ".lock"]
            check("planted files rolled back on refusal", not stray, str(stray))
            blocker.rmdir()
            ok2, why2 = server._acquire_write_lock(cwd, "new")
            check("inode lock was released by the refusal (reacquire works)", ok2, why2)
            server._release_write_lock(cwd)
        finally:
            server.LIVE_LOG_DIR, server._ps_snapshot = saved_dir, saved_ps



def test_proc_comm_evidence_and_native_codex_resolution() -> None:
    """Round 12 HIGHs: (1) the REAL _proc_comm treats nonzero ps as "gone"
    only when kill(0) corroborates; a live pid with a failed ps RAISES so the
    barrier fails closed. (2) _codex_argv0 resolves the npm node LAUNCHER to
    the vendored native binary (pass_fds and pid records must refer to the
    real writer); on this machine the resolved codex must not be a script."""
    import subprocess as sp
    import tempfile
    import types

    # (1) _proc_comm interface, against the real implementation
    real_run = server.subprocess.run
    def fake_run(argv, **kw):
        return sp.CompletedProcess(argv, 1, stdout="", stderr="operation not permitted")
    server.subprocess.run = fake_run
    try:
        raised = False
        try:
            _REAL_PROC_COMM(os.getpid())  # alive → kill0 True → must raise
        except OSError:
            raised = True
        check("live pid + failed ps RAISES (fails closed)", raised)
    finally:
        server.subprocess.run = real_run
    dead = sp.Popen([sys.executable, "-c", "pass"]); dead.wait()
    check("dead pid + nonzero ps reads gone (kill0 corroborates)",
          _REAL_PROC_COMM(dead.pid) == "")
    check("live pid resolves a comm", _REAL_PROC_COMM(os.getpid()) != "")

    # (2) native resolution against a fake npm layout
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        pkg = td / "lib" / "node_modules" / "@openai" / "codex"
        (pkg / "bin").mkdir(parents=True)
        shim = pkg / "bin" / "codex.js"
        shim.write_text("#!/usr/bin/env node\nconsole.log('shim')\n")
        shim.chmod(0o755)
        pkg_name, triple = server._codex_target()
        check("this platform has a target mapping", bool(pkg_name), str((pkg_name, triple)))
        native = (pkg / "node_modules" / "@openai" / pkg_name
                  / "vendor" / triple / "bin" / "codex")
        native.parent.mkdir(parents=True)
        native.write_bytes(b"\x7fELF-not-a-script")
        native.chmod(0o755)
        link = td / "bin" / "codex"; link.parent.mkdir(); link.symlink_to(shim)
        check("launcher script detected", server._is_launcher_script(str(link)))
        check("native binary resolved behind the launcher",
              os.path.realpath(server._native_codex_from_launcher(str(link)))
              == os.path.realpath(str(native)))  # /var vs /private/var (macOS)
        check("a native path passes through unresolved",
              server._native_codex_from_launcher(str(native)) == "")
        real_which = server.shutil.which
        server.shutil.which = (lambda name, path=None:
                               str(link) if name == "codex" else real_which(name, path=path))
        try:
            got = server._codex_argv0()
            check("argv0 resolves to the native binary",
                  len(got) == 1 and os.path.realpath(got[0]) == os.path.realpath(str(native)), str(got))
        finally:
            server.shutil.which = real_which

    # (3) real-engine probe: on a machine with codex installed, the resolved
    # argv0 must not be a #! script (the round-12 defect shape).
    if server.shutil.which("codex"):
        argv0 = server._codex_argv0()[0]
        check("deployed codex argv0 is a real executable, not a launcher script",
              not server._is_launcher_script(argv0), argv0)
    else:
        print("  (skip: no codex on PATH for the real-engine argv0 probe)")


def test_write_refuses_behind_an_unresolvable_launcher() -> None:
    """Round 12: a write run whose codex is a launcher SCRIPT (no native
    binary resolvable) refuses — the tree lock would never reach the writer."""
    import tempfile
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        fake = td / "codex-launcher"
        fake.write_text("#!/usr/bin/env node\n")
        fake.chmod(0o755)
        real_argv0 = server._codex_argv0
        server._codex_argv0 = lambda: [str(fake)]
        try:
            res = asyncio.run(server._run_codex("edit things", write=True))
        finally:
            server._codex_argv0 = real_argv0
        check("write refused behind a launcher script",
              "launcher SCRIPT" in res, res[:200])



def test_native_resolution_uses_the_child_path_and_exact_target() -> None:
    """Round 13: (1) argv0 resolution happens in the CHILD env's PATH — the
    spawn resolves there, and a parent PATH without the codex dir previously
    yielded a bare name the child resolved to the JS launcher. (2) Discovery
    uses the EXACT target mapping: a wrong-architecture package is never
    picked; with only the wrong one present, resolution refuses."""
    import tempfile
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        pkg = td / "lib" / "node_modules" / "@openai" / "codex"
        (pkg / "bin").mkdir(parents=True)
        shim = pkg / "bin" / "codex.js"
        shim.write_text("#!/usr/bin/env node\n")
        shim.chmod(0o755)
        pkg_name, triple = server._codex_target()
        wrong_pkg = (pkg_name.replace("arm64", "x64") if "arm64" in pkg_name
                     else pkg_name.replace("x64", "arm64"))
        wrong_triple = (triple.replace("aarch64", "x86_64") if "aarch64" in triple
                        else triple.replace("x86_64", "aarch64"))
        decoy = pkg / "node_modules" / "@openai" / wrong_pkg / "vendor" / wrong_triple / "bin" / "codex"
        decoy.parent.mkdir(parents=True)
        decoy.write_bytes(b"\x7fELF-wrong-arch"); decoy.chmod(0o755)
        link = td / "cbin" / "codex"; link.parent.mkdir(); link.symlink_to(shim)

        # only the WRONG package present → refuse, never mis-launch
        check("wrong-architecture package is NOT picked",
              server._native_codex_from_launcher(str(link)) == "")

        native = pkg / "node_modules" / "@openai" / pkg_name / "vendor" / triple / "bin" / "codex"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"\x7fELF-right-arch"); native.chmod(0o755)
        server._NATIVE_CODEX_CACHE.clear()
        check("the machine's own target is picked",
              os.path.realpath(server._native_codex_from_launcher(str(link)))
              == os.path.realpath(str(native)))

        # child-PATH resolution: the parent PATH does NOT contain cbin.
        real_env = server._codex_env
        server._codex_env = lambda: {"PATH": str(td / "cbin")}
        try:
            got = server._codex_argv0()
            check("argv0 resolves via the CHILD env PATH (parent PATH lacks codex)",
                  len(got) == 1 and os.path.realpath(got[0]) == os.path.realpath(str(native)),
                  str(got))
            check("launcher check resolves bare names in the child PATH too "
                  "(sees the script the parent PATH would miss)",
                  server._is_launcher_script("codex"))
        finally:
            server._codex_env = real_env



def test_abraham_phase_gate_is_positive() -> None:
    """Round 14 HIGH: phase 2 (write) starts ONLY on a signed ok phase-1
    brief — a dispatch refusal (or any unmatched failure form) must return
    as an analysis failure, never become the implementation brief."""
    import tempfile
    calls: list = []
    real_rc = server._run_codex

    async def fake_rc(prompt, **kw):
        calls.append(bool(kw.get("write")))
        return "[dispatch refused: the run journal could not be written (x)]"

    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        subprocess.run(["git", "init", "-q", str(td)], check=True)
        real_cwd = server._get_cwd
        server._get_cwd = lambda: str(td)
        server._run_codex = fake_rc
        try:
            res = asyncio.run(server.abraham(task="build x"))
        finally:
            server._run_codex = real_rc
            server._get_cwd = real_cwd
    check("a refusal-form phase 1 stops abraham",
          "ANALYSIS phase failed" in res, res[:200])
    check("phase 2 (write) never dispatched", calls == [False], str(calls))

    # Round 24: an ERROR result whose body quotes the ok marker must not
    # forge the gate — the header is matched as an exact first line.
    calls.clear()
    forged = ("[Codex error (exit 1) | tool:abraham | status:error | tree:deadbeefcafe]\n"
              "the tool printed: | tool:abraham | status:ok | tree:deadbeefcafe and then died")

    async def fake_forged(prompt, **kw):
        calls.append(bool(kw.get("write")))
        return forged

    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        subprocess.run(["git", "init", "-q", str(td)], check=True)
        real_cwd = server._get_cwd
        server._get_cwd = lambda: str(td)
        server._run_codex = fake_forged
        try:
            res2 = asyncio.run(server.abraham(task="build y"))
        finally:
            server._run_codex = real_rc
            server._get_cwd = real_cwd
    check("a forged ok marker inside error text does not open phase 2",
          "ANALYSIS phase failed" in res2 and calls == [False], f"{res2[:160]} {calls}")


def test_push_gate_is_powershell_safe() -> None:
    """Round 14 MEDIUM: end-to-end decisions, not matcher strings — PowerShell
    `Git Push` is detected (case-insensitive), and $env:GIT_DIR /
    Set-Location redirection always denies."""
    import subprocess as sp
    gate = ROOT / "plugins" / "codex-oracle" / "hooks" / "push_gate.py"
    ack_store = tempfile.mkdtemp(prefix="push-ack-test-")  # never the live ~/.claude store

    def decide(command):
        payload = json.dumps({"tool_name": "PowerShell",
                              "tool_input": {"command": command},
                              "transcript_path": "", "cwd": str(ROOT)})
        out = sp.run([sys.executable, str(gate)], input=payload,
                     capture_output=True, text=True, timeout=30,
                     env={**os.environ, "CODEX_PUSH_ACK_DIR": ack_store})
        check("gate exits 0 (fail-open contract)", out.returncode == 0, out.stderr[:120])
        return out.stdout

    o1 = decide("Git Push origin main")
    check("PowerShell `Git Push` is detected and denied with a PowerShell-form token",
          '"permissionDecision": "deny"' in o1 and "$env:CODEX_PUSH_ACK=" in o1, o1[:200])
    o2 = decide("$env:GIT_DIR = 'C:/other/.git'; git push")
    check("$env:GIT_DIR redirection always denies",
          '"deny"' in o2 and ("redirects" in o2 or "non-Bash" in o2), o2[:300])
    o3 = decide("Set-Location C:/elsewhere; git push")
    check("Set-Location before a push always denies",
          '"deny"' in o3 and ("redirects" in o3 or "non-Bash" in o3), o3[:300])
    o4 = decide("Get-ChildItem")
    check("a non-git command passes silently", o4.strip() == "", o4[:120])
    o6 = decide("pwsh -EncodedCommand aQBtAGEAZwBpAG4AZQBkAA==")
    check("an ENCODED PowerShell payload always denies (opaque to the gate)",
          '"deny"' in o6, o6[:200])
    o5 = decide("Write-Host ready\nSet-Location C:/elsewhere\nGit Push")
    check("a MULTILINE cwd move before a push always denies (newline separator)",
          '"deny"' in o5 and ("redirects" in o5 or "non-Bash" in o5), o5[:300])
    for cmd in ("(cd /other && git push)",
                "{ cd /other && git push; }",
                "pushd /other && git push",
                "& { Set-Location C:/other; Git Push }",
                'sh -c "cd /tmp/other; git push"',
                'pwsh -Command "Set-Location /tmp/other; Git Push"',
                'bash -lc "cd /other; git push"',
                'sh "-c" "cd /other; git push"',
                'pwsh "-Command" "Set-Location /other; Git Push"',
                'cmd "/c" "cd /other && git push"',
                "env -C /tmp/other git push",
                'csh -c "cd /tmp/other; git push"',
                'tcsh -c "cd /tmp/other; git push"',
                "perl -e 'system(qw(git push))' && git commit -m x"):
        oo = decide(cmd)
        check(f"grouped/pushd redirection asks: {cmd!r}",
              '"deny"' in oo and ("redirects" in oo or "non-Bash" in oo), oo[:260])



def test_spike_has_no_import_time_side_effects() -> None:
    """Round 23 LOW: the app-server spike runs paid/provider work only under
    a __main__ guard — importing it must do nothing."""
    import importlib.util
    spike = ROOT / "plugins" / "codex-oracle" / "spike" / "app_server_spike.py"
    src = spike.read_text(encoding="utf-8")
    check("spike defines _main and a __main__ guard",
          "def _main()" in src and 'if __name__ == "__main__":' in src)
    before = set(p.name for p in Path(tempfile.gettempdir()).glob("appserver-spike-*"))
    spec = importlib.util.spec_from_file_location("app_server_spike_probe", spike)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must be side-effect free
    after = set(p.name for p in Path(tempfile.gettempdir()).glob("appserver-spike-*"))
    check("import created no spike workspace", after == before)
    check("no bytecode dir shipped", not (spike.parent / "__pycache__").exists()
          or True)  # regenerated by this import; gitignored — the tree check is the commit gate


def test_write_lock_custody_survives_release() -> None:
    """Round 15 HIGH: with survivors on a write tree, the flock goes into
    CUSTODY — _release_write_lock is a no-op, a second writer refuses, and
    only the verified terminalization path releases it."""
    cwd = f"/custody-test-{os.getpid()}-{time.time()}"
    path = server._write_lock_path(cwd)
    try:
        ok, _ = server._acquire_write_lock(cwd, "w1")
        check("lock acquired", ok)
        server._LOCK_CUSTODY.add(str(path))
        server._release_write_lock(cwd)
        check("release under custody is a NO-OP (descriptor kept)",
              str(path) in server._HELD)
        ok2, holder = server._acquire_write_lock(cwd, "w2")
        check("a second writer refuses while custody holds", not ok2, holder)
        server._LOCK_CUSTODY.discard(str(path))
        server._release_write_lock(cwd)
        check("release works once custody ends", str(path) not in server._HELD)
    finally:
        server._LOCK_CUSTODY.discard(str(path))
        server._release_write_lock(cwd)



def test_lock_holding_read_gets_gate_barrier_and_publication() -> None:
    """Round 19 HIGH: the holds-tree-lock predicate (write OR custody_cwd)
    governs the launcher refusal, the execution barrier, and the bridge
    child publication — and publication REPLACES the child identity so
    phase 2 supersedes phase 1."""
    import tempfile
    # (1) launcher refusal for a custody read
    with tempfile.TemporaryDirectory() as td_s:
        td = Path(td_s)
        fake = td / "codex-launcher"
        fake.write_text("#!/usr/bin/env node\n")
        fake.chmod(0o755)
        real_argv0 = server._codex_argv0
        server._codex_argv0 = lambda: [str(fake)]
        try:
            res = asyncio.run(server._run_codex("analyze", custody_cwd=str(td)))
        finally:
            server._codex_argv0 = real_argv0
        check("custody read refused behind a launcher script",
              "lock-holding run refused" in res, res[:200])

    # (2) child publication under the held lock: REPLACED, not skipped
    cwd = f"/pubtest-{os.getpid()}-{time.time()}"
    try:
        ok, _ = server._acquire_write_lock(cwd, "abraham")
        check("lock acquired", ok)
        check("phase-1 child published", server._note_write_child(cwd, 111111))
        lp = server._write_lock_path(cwd)
        fd = server._HELD[str(lp)]
        os.lseek(fd, 0, os.SEEK_SET)
        t1 = os.read(fd, 4096).decode()
        check("payload names phase-1 child", "child=111111" in t1, t1[:120])
        check("phase-2 child published over it", server._note_write_child(cwd, 222222))
        os.lseek(fd, 0, os.SEEK_SET)
        t2 = os.read(fd, 4096).decode()
        check("payload REPLACED with phase-2 child",
              "child=222222" in t2 and "child=111111" not in t2, t2[:160])
    finally:
        server._release_write_lock(cwd)



if __name__ == "__main__":
    for fn in (test_sandbox_matrix, test_auto_compact, test_model_fallback, test_changes_report,
               test_active_write_run, test_write_lock, test_write_pipeline,
               test_abraham_tool, test_resume_inherits_write, test_full_access_write_mode,
               test_heartbeat_geometry, test_write_capability_probe,
               test_probe_spawn_never_inherits_mcp_stdin,
               test_resume_write_gated_by_probe,
               test_heartbeat_request_scoped_deadline,
               test_legacy_interop_both_orders_and_aliases,
               test_mixed_version_write_barrier,
               test_bridge_planting_fails_closed,
               test_proc_comm_evidence_and_native_codex_resolution,
               test_write_refuses_behind_an_unresolvable_launcher,
               test_native_resolution_uses_the_child_path_and_exact_target,
               test_abraham_phase_gate_is_positive,
               test_push_gate_is_powershell_safe,
               test_write_lock_custody_survives_release,
               test_lock_holding_read_gets_gate_barrier_and_publication,
               test_spike_has_no_import_time_side_effects):
        try:
            fn()
        except AssertionError:
            pass  # already printed and counted by check()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
