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
FAKE_CODEX_THREAD (fixed thread id), FAKE_CODEX_ANSWER (answer text).
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
    prelude = os.environ.get("FAKE_CODEX_PRELUDE")
    if prelude:
        _emit({"type": "item.completed", "item": {"type": "agent_message", "text": prelude}})
    fail = os.environ.get("FAKE_CODEX_FAIL")
    if fail:
        msg = {
            # rendered texts from codex-rs/protocol/src/error.rs @ rust-v0.151.0
            "capacity": "Selected model is at capacity. Please try a different model.",
            "quota": "Quota exceeded. Check your plan and billing details.",
            "disconnect": "stream disconnected before completion: connection reset by peer",
        }.get(fail, fail)
        _emit({"type": "error", "message": msg})
        _emit({"type": "turn.failed", "error": {"message": msg}})
        return 1
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
