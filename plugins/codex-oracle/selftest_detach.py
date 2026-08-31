#!/usr/bin/env python3
"""E2E: a codex run SURVIVES its MCP server being killed exactly the way
Claude Code kills it on `/mcp` reconnect / plugin reload / exit — SIGINT, then
SIGTERM ~100 ms later (its own log, 2026-08-31) — and is COLLECTED by the next
server with `codex_resume_run`, no re-ask.

Spawns server.py over stdio as Claude Code does (raw newline-delimited
JSON-RPC, so this script owns the server pid), with codex = tests/fake_codex.py
(real processes, no API spend). `--real` uses the real codex CLI instead:
one tiny call whose turn runs `sleep 30` before answering, so the kill lands
mid-turn on the actual engine.

Run:  .venv/bin/python selftest_detach.py [--real]
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAKE = ROOT.parent.parent / "tests" / "fake_codex.py"
JOURNAL = Path.home() / ".claude" / "logs" / "codex-oracle" / "runs.jsonl"


_SERVERS: list = []


def die(msg: str) -> None:
    print(f"FAIL: {msg}")
    for srv in _SERVERS:  # never leave a server (and its child) behind
        with __import__("contextlib").suppress(Exception):
            srv.proc.kill()
    sys.exit(1)


class Server:
    """A codex-oracle server over stdio, driven with raw JSON-RPC."""

    def __init__(self, env: dict, cwd: str, tag: str):
        self.stderr = open(Path(cwd) / f"server-{tag}.stderr", "wb")
        self.proc = subprocess.Popen(
            [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            cwd=cwd, env=env,
        )
        self.next_id = 1
        _SERVERS.append(self)
        self._send({"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "selftest-detach", "version": "1"}}})
        self._read_response(self.next_id - 1, timeout=30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def call(self, tool: str, args: dict) -> int:
        i = self._id()
        self._send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                    "params": {"name": tool, "arguments": args}})
        return i

    def _read_response(self, want_id: int, timeout: float) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                die("server stdout closed before the response arrived")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == want_id:
                return msg
        die(f"no response for id {want_id} within {timeout}s")

    def result_text(self, want_id: int, timeout: float) -> str:
        msg = self._read_response(want_id, timeout)
        if "error" in msg:
            die(f"tool error: {msg['error']}")
        return "".join(c.get("text", "") for c in msg["result"].get("content", []))

    def kill_like_claude_code(self) -> int:
        os.kill(self.proc.pid, signal.SIGINT)
        time.sleep(0.1)
        with __import__("contextlib").suppress(ProcessLookupError):
            os.kill(self.proc.pid, signal.SIGTERM)
        try:
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            die("server did not exit within 10 s of SIGINT+SIGTERM")

    def close(self) -> None:
        with __import__("contextlib").suppress(Exception):
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        self.stderr.close()


def journal_runs(cwd: str) -> dict:
    runs: dict = {}
    with __import__("contextlib").suppress(OSError):
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            r = runs.setdefault(rec["run"], {})
            r.update({k: v for k, v in rec.items() if k != "phase"})
            r[f"has_{rec.get('phase')}"] = True
    # The server journals os.getcwd() — the RESOLVED path (/private/var on
    # macOS) — so compare realpaths, not the tempdir string we were handed.
    want = os.path.realpath(cwd)
    return {k: v for k, v in runs.items()
            if os.path.realpath(str(v.get("cwd") or "")) == want}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main() -> None:
    real = "--real" in sys.argv
    nonce = uuid.uuid4().hex[:8]
    answer = f"DETACH-OK-{nonce}"
    ws = os.path.realpath(tempfile.mkdtemp(prefix="codex-oracle-detach-e2e-"))
    env = dict(os.environ)
    env["CLAUDE_CWD"] = ws  # the server's workspace resolver honours this first
    env.pop("CODEX_ORACLE_CODEX_BIN", None)
    if not real:
        shim = Path(ws) / "codex"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        shim.chmod(0o755)
        env.update({"CODEX_ORACLE_CODEX_BIN": str(shim), "FAKE_CODEX_SLEEP": "8",
                    "FAKE_CODEX_ANSWER": answer})
        prompt = "selftest: survive a server restart"
        kill_after, finish_within = 1.5, 30
    else:
        prompt = (f"Run the shell command `sleep 30` (it is allowed), wait for it to finish, "
                  f"then reply with exactly: {answer}")
        kill_after, finish_within = 6.0, 240
    mode = "REAL codex" if real else "fake codex"
    print(f"[1] server A up ({mode}), workspace {ws}")
    a = Server(env, ws, "A")
    a.call("codex_query", {"prompt": prompt, "web_search": False})
    t0 = time.time()
    rec = {}
    while time.time() - t0 < 60:
        runs = journal_runs(ws)
        rec = next((r for r in runs.values() if r.get("has_spawn")), {})
        if rec:
            break
        time.sleep(0.2)
    if not rec:
        die("no spawn record journaled within 60 s")
    pid = int(rec["pid"])
    run = rec["run"]
    print(f"[2] run {run} spawned codex pid {pid}; waiting {kill_after}s then killing server A "
          f"the Claude Code way (SIGINT, +100ms SIGTERM)")
    time.sleep(kill_after)
    code = a.kill_like_claude_code()
    print(f"    server A exited with code {code}")
    time.sleep(0.5)
    if not alive(pid):
        die(f"codex pid {pid} died with the server — NOT detached")
    rec = journal_runs(ws).get(run, {})
    if not rec.get("has_detached") or rec.get("has_end"):
        die(f"journal did not record a detach: {rec}")
    print(f"[3] codex pid {pid} SURVIVED; journal: detached (deadline in "
          f"{int(float(rec.get('deadline_ts', 0)) - time.time())}s)")
    t0 = time.time()
    while alive(pid) and time.time() - t0 < finish_within:
        time.sleep(0.5)
    if alive(pid):
        die(f"codex pid {pid} still running after {finish_within}s")
    out = Path(rec["output_file"])
    if not (out.exists() and answer in out.read_text()):
        die(f"detached run did not write its answer to {out}")
    print(f"[4] detached run finished on its own and wrote its answer ({time.time() - t0:.0f}s)")
    b = Server(env, ws, "B")
    i = b.call("codex_resume_run", {"run": run})
    res = b.result_text(i, timeout=60)
    if answer not in res or "collected from run" not in res:
        die(f"server B did not collect the answer: {res[:400]}")
    i = b.call("codex_runs", {})
    status = b.result_text(i, timeout=30)
    b.close()
    print(f"[5] server B collected it via codex_resume_run (no re-ask):\n    {res.splitlines()[0][:110]}\n"
          f"    {res.splitlines()[1][:110]}")
    print("    codex_runs:", [ln for ln in status.splitlines() if run in ln][0][:110])
    print(f"PASS: run survived the server kill and was collected ({mode})")


if __name__ == "__main__":
    main()
