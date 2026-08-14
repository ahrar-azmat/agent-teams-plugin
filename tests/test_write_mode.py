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

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


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

        # Dead-pid stale break: recovery must not wait MAX_RUNTIME.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("crashed pid=999999999 cwd=x t=0\n")
        ok4, _ = server._acquire_write_lock(cwd, "t4")
        check("dead holder pid → lock broken", ok4)
        server._release_write_lock(cwd)

        # Live pid + fresh mtime → genuinely held.
        path.write_text(f"held pid={os.getpid()} cwd=x t=0\n")
        ok5, holder5 = server._acquire_write_lock(cwd, "t5")
        check("live holder → refused", not ok5, holder5)

        # Live pid but ancient mtime → age fallback breaks it.
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        ok6, _ = server._acquire_write_lock(cwd, "t6")
        check("over-age lock broken despite live pid", ok6)
    finally:
        server._release_write_lock(cwd)


# ---------------------------------------------------------------------------
# 5. Write pipeline: precondition, sealed scaffold, report, no-retry
# ---------------------------------------------------------------------------

def _fake_exec(captured: dict, results=None):
    """Fake _exec_codex_once. `results` = per-call return tuples (cycled last)."""
    results = list(results or
                   [("implemented.", True, "", "", 0, None, False)])

    async def fake(cmd, output_file, state, emit, ctx, model, extra_env=None):
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
            check("lock released after run",
                  not server._write_lock_path(td).exists())

            # Analysis failure → nothing written, no phase 2.
            captured.clear()
            server._exec_codex_once = _fake_exec(
                captured,
                results=[("", False, "", "invalid argument: boom", 1, None,
                          False)])
            res = asyncio.run(server.abraham(task="doomed", allow_dirty=True))
            check("analysis failure stops before implementation",
                  len(captured["calls"]) == 1 and "ANALYSIS phase failed" in res)
            check("lock released after analysis failure",
                  not server._write_lock_path(td).exists())

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
        rec = {"run": "w1", "cwd": cwd, "write": True, "thread_id": "t9",
               "has_end": True, "status": "error", "prompt": "orig task",
               "infra": False, "web_search": False}
        server._journal_runs = lambda: {"w1": dict(rec)}
        asyncio.run(server.codex_resume_run(run="w1"))
        check("write run resumes as write", got.get("write") is True)
        check("same thread", got.get("resume_tid") == "t9")
        check("lock released after write resume",
              not server._write_lock_path(cwd).exists())

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


if __name__ == "__main__":
    for fn in (test_sandbox_matrix, test_auto_compact, test_changes_report,
               test_active_write_run, test_write_lock, test_write_pipeline,
               test_abraham_tool, test_resume_inherits_write):
        fn()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
