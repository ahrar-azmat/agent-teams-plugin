#!/usr/bin/env python3
"""A stand-in for the codex CLI that speaks `codex exec --json`'s contract.

Used by tests/test_detach.py and selftest_detach.py so the run-survivability
path (file-backed spool, detach on shutdown, watchdog, adoption) is exercised
with REAL processes and no API spend. It mirrors the argv grammar server.py
emits (`exec [-i F]... [--sandbox M] [--ignore-user-config] --json --model M
--skip-git-repo-check --color never --output-last-message FILE -c k=v ...
[resume TID] PROMPT`) and the event shapes verified against codex 0.151.0:
thread.started{thread_id}, turn.started, item.completed{item:{type,text}},
turn.completed{usage}, error{message} + turn.failed{error:{message}}.

Knobs (env): FAKE_CODEX_SLEEP (seconds of "work" before answering, default
0.3), FAKE_CODEX_FAIL=capacity|quota|disconnect (fail the turn with codex's
exact rendered text for that class), FAKE_CODEX_PRELUDE (an agent_message
emitted BEFORE the failure/answer — real failed runs carry commentary),
FAKE_CODEX_THREAD (fixed thread id), FAKE_CODEX_ANSWER (answer text),
FAKE_CODEX_FAIL_STDERR_ONLY=1 (with FAKE_CODEX_FAIL: the failure text reaches
STDERR only — no `error`/`turn.failed` event — and the process exits 1; the
shape of a stream that died outside the JSONL contract, round 47).
"""
import json
import os
import sys
import time
import uuid


def _emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--version":
        print("codex-cli 0.0.0-fake")
        return 0
    out_file = ""
    prompt = ""
    resume_tid = ""
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--output-last-message", "-o"):
            out_file = argv[i + 1]
            i += 2
            continue
        if a in ("-c", "--config", "-m", "--model", "-s", "--sandbox", "-i",
                 "--image", "--color", "--add-dir"):
            i += 2
            continue
        if a == "resume":
            resume_tid = argv[i + 1]
            prompt = argv[i + 2] if i + 2 < len(argv) else ""
            i = len(argv)
            continue
        if a.startswith("-") or a == "exec":
            i += 1
            continue
        prompt = a
        i += 1
    tid = resume_tid or os.environ.get("FAKE_CODEX_THREAD") or str(uuid.uuid4())
    _emit({"type": "thread.started", "thread_id": tid})
    _emit({"type": "turn.started"})
    _emit({"type": "item.completed",
           "item": {"type": "reasoning", "text": "**Considering the task**"}})
    total = float(os.environ.get("FAKE_CODEX_SLEEP", "0.3"))
    slept = 0.0
    while slept < total:
        step = min(0.5, total - slept)
        time.sleep(step)
        slept += step
        _emit({"type": "item.completed",
               "item": {"type": "command_execution", "command": f"sleep {step}",
                        "exit_code": 0}})
    failed_cmd = os.environ.get("FAKE_CODEX_FAILED_COMMAND")
    if failed_cmd:  # a tool command that exited non-zero (codex marks it status "failed")
        _emit({"type": "item.completed",
               "item": {"type": "command_execution", "command": failed_cmd, "status": "failed",
                        "exit_code": 1, "aggregated_output": ""}})
    prelude = os.environ.get("FAKE_CODEX_PRELUDE")
    if prelude:
        _emit({"type": "item.completed", "item": {"type": "agent_message", "text": prelude}})
        hang = float(os.environ.get("FAKE_CODEX_HANG_AFTER_PRELUDE", "0") or 0)
        if hang > 0:
            time.sleep(hang)  # a turn cut here leaves commentary but no answer
    fail = os.environ.get("FAKE_CODEX_FAIL")
    once = os.environ.get("FAKE_CODEX_FAIL_ONCE")  # path: fail only the FIRST spawn (creates the file)
    if once and not fail:
        if not os.path.exists(once):
            open(once, "w").close()
            fail = "disconnect"
    if fail:
        msg = {
            # rendered texts from codex-rs/protocol/src/error.rs @ rust-v0.151.0
            "capacity": "Selected model is at capacity. Please try a different model.",
            "quota": "Quota exceeded. Check your plan and billing details.",
            "disconnect": "stream disconnected before completion: connection reset by peer",
        }.get(fail, fail)
        if os.environ.get("FAKE_CODEX_FAIL_STDERR_ONLY"):
            # No terminal event: the JSONL stream just stops and the text lands
            # on stderr. This is the ONLY shape in which the run's terminal-
            # error slot is not overwritten by an event, so it is the shape a
            # regression test for that slot must use (round 47, MEDIUM: an
            # event-emitting disconnect classified the same on the baseline).
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            return 1
        _emit({"type": "error", "message": msg})
        _emit({"type": "turn.failed", "error": {"message": msg}})
        return 1
    if os.environ.get("FAKE_CODEX_LEAK"):
        # Leak a same-group descendant that outlives this process — the
        # "codex left spawned processes running" shape (round 14).
        import subprocess as _sp
        leaked = _sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                           stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        _emit({"type": "item.completed",
               "item": {"type": "command_execution",
                        "command": f"leaked pid {leaked.pid}", "exit_code": 0}})
    if os.environ.get("FAKE_CODEX_LEAK_SCRUBBED"):
        # A descendant that scrubs EVERY userspace channel at once: fresh
        # session+group, empty environment, closed descriptors. Pins the
        # 1.17.x containment boundary (round 17) — only kernel custody
        # (the 1.18 daemon) can hold this.
        import subprocess as _sp
        marker = os.environ.get("FAKE_CODEX_SCRUBBED_PIDFILE")
        # start_new_session=True already made it a session leader; a second
        # setsid() would be EPERM (measured).
        code = ("import os, time, sys; "
                "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)")
        _sp.Popen([sys.executable, "-c", code, marker or "/dev/null"],
                  env={}, close_fds=True, start_new_session=True,
                  stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    if os.environ.get("FAKE_CODEX_LEAK_SETSID"):
        # Leak a descendant that setsid()s OUT of the group — the codex
        # shell-tool topology (round 16). Only the inherited env marker can
        # find it.
        import subprocess as _sp
        _sp.Popen([sys.executable, "-c",
                   "import os, time; os.setsid(); time.sleep(30)"],
                  stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    ctf = os.environ.get("FAKE_CODEX_COMPLETE_THEN_FAIL_ONCE")
    if ctf and not os.path.exists(ctf):
        # Attempt 1: a COMPLETED turn whose delivery then dies (transient) —
        # turn.completed emitted, but exit 1 with a disconnect and NO answer
        # file. A retry must not inherit this completion evidence (round 16).
        open(ctf, "w").close()
        _emit({"type": "item.completed",
               "item": {"type": "agent_message", "text": "finished thinking"}})
        _emit({"type": "turn.completed",
               "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}})
        _emit({"type": "error",
               "message": "stream disconnected before completion: connection reset by peer"})
        return 1
    if ctf:
        # Attempt 2: a PARTIAL — one agent message, exit 0, no completion,
        # no answer file.
        _emit({"type": "item.completed",
               "item": {"type": "agent_message", "text": "partial only"}})
        return 0
    if os.environ.get("FAKE_CODEX_NO_ANSWER"):
        return 0  # exit 0 with NO answer, NO turn.completed, NO output file
    answer = os.environ.get("FAKE_CODEX_ANSWER") or f"ANSWER:{prompt[:40]}"
    _emit({"type": "item.completed", "item": {"type": "agent_message", "text": answer}})
    _emit({"type": "turn.completed",
           "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}})
    if out_file:
        with open(out_file, "w", encoding="utf-8") as fh:
            fh.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
