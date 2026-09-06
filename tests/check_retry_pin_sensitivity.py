#!/usr/bin/env python3
"""Sensitivity of the retry regression pin.

The defect: on 82fe2aa a failed tool command occupied the terminal-error slot,
so a stderr-only disconnect was classified None and never retried.

FOUR earlier versions of this instrument reported OK for the wrong reason, and
each correction narrowed what counts as evidence:
  53  "the baseline exited non-zero"            — any crash
  54  "the error CONTAINS `command failed`"     — an exception message, no child
  55  "the live log MENTIONS the disconnect"    — an assistant message said it
  56  "the SPOOL bytes are shaped right"        — the server never processed
                                                  them (`item_type` alias, an
                                                  unterminated final record)
Round 56's lesson: correctly shaped bytes do not establish that the server
dispatched them, and a re-implementation of the server's parser diverges from
it in exactly the places an adversary uses. So this version reads only what the
SERVER RENDERED when it dispatched an event — the live log's `$ <cmd> → failed
(exit 1)` line comes from the command_execution handler and the `! stream
disconnected …` line from the stderr reader; an assistant message renders as
`assistant: …` and cannot produce either — and it CALIBRATES ITSELF against a
MUTANT: the candidate with only the `last_command_failure` assignment reverted
to `last_error`. The mutant must exhibit the masking; if it does not, this
instrument cannot tell the fix from its absence and reports BROKEN.

KNOWN LIMIT (round 57, LOW): the rendered lines are NOT exclusive evidence of
dispatch. `_feed_jsonl` forwards a malformed (non-JSON) stdout line as raw text
with the same timestamp, so a child that prints `$ rg … → failed (exit 1)` as
plain text increments `cmd_failed` without any event being processed. The
dispatch counters are therefore a sanity check on the probe's OWN run, not the
guarantee; the guarantee is the mutant, which round 57 did not defeat — an
end-to-end false OK through this limit remains unproven, and a fifth-version
fix would read the server's structured event accounting rather than its log.

Run: python3 tests/check_retry_pin_sensitivity.py
"""
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "82fe2aa"
FAILED_COMMAND = "rg -n nothing-matches src/"
MASKED_ERROR = f"command failed: {FAILED_COMMAND} \u2192 exit 1"   # what the DEFECT records
DISCONNECT = "stream disconnected before completion"
# the one line the fix changes; reverting it alone reproduces the defect
FIX_LINE = 'state["last_command_failure"] = f"command failed: {command} \u2192 exit {code}"'
MUTANT_LINE = 'state["last_error"] = f"command failed: {command} \u2192 exit {code}"'

# what the server RENDERS when it dispatches each event (live-log line content)
CMD_RENDER = re.compile(r"^\[\s*[\d.]+s\] \$ " + re.escape(FAILED_COMMAND) + r" \u2192 failed \(exit 1\)$")
DISC_RENDER = re.compile(r"^\[\s*[\d.]+s\] ! " + re.escape(DISCONNECT))
MSG_RENDER = re.compile(r"^\[\s*[\d.]+s\] assistant: ")

PROBE = """
import asyncio, json, pathlib, sys
sys.path.insert(0, 'tests')
from test_transient_retry import _FakeCodex, server
with _FakeCodex(FAKE_CODEX_FAIL='disconnect', FAKE_CODEX_FAIL_STDERR_ONLY='1',
                FAKE_CODEX_SLEEP='0', FAKE_CODEX_FAILED_COMMAND='rg -n nothing-matches src/') as h:
    asyncio.run(server._run_codex('review this'))
    runs = server._journal_runs()
    waits = list(h.waits)
    logdir = pathlib.Path(server.LIVE_LOG_DIR)
    live = [p for p in logdir.glob('*-codex.log')]
    text = live[0].read_text(errors='replace') if len(live) == 1 else ''
rec = next(iter(runs.values()))
print('PROBE ' + json.dumps({
    'attempts': rec.get('attempts'), 'retry_classes': rec.get('retry_classes'),
    'error': str(rec.get('error'))[:300], 'waits': waits,
    'max_retries': server.MAX_TRANSIENT_RETRIES, 'spawned': bool(rec.get('has_spawn')),
    'live_logs': len(live), 'live': text[:20000],
}))
"""


def probe(tree: Path):
    p = subprocess.run([sys.executable, "-B", "-c", PROBE], cwd=tree,
                       capture_output=True, text=True, timeout=300)
    out = (p.stdout + p.stderr).strip()
    for line in out.splitlines():
        if line.startswith("PROBE "):
            return json.loads(line[6:]), out
    return None, out


def dispatched(r):
    """What the SERVER rendered, line by line — its own record of dispatch."""
    lines = r["live"].splitlines()
    return {
        "cmd_failed": sum(1 for l in lines if CMD_RENDER.match(l)),
        "disconnect": sum(1 for l in lines if DISC_RENDER.match(l)),
        "messages_naming_disconnect": sum(1 for l in lines if MSG_RENDER.match(l) and DISCONNECT in l),
    }


def exercised(r, label, problems):
    d = dispatched(r)
    if not r["spawned"]:
        problems.append(f"{label}: no child was spawned")
    if r["live_logs"] != 1:
        problems.append(f"{label}: expected one live log, found {r['live_logs']}")
    if d["cmd_failed"] < 1:
        problems.append(f"{label}: the server never rendered the failed command_execution dispatch")
    if d["disconnect"] < 1:
        problems.append(f"{label}: the server never rendered a stderr disconnect")
    if d["messages_naming_disconnect"]:
        problems.append(f"{label}: an assistant message names the disconnect")
    if "machinery failure" in r["error"]:
        problems.append(f"{label}: terminal error is a machinery failure")
    return d


def masked(r):
    return r["attempts"] == 1 and r["retry_classes"] == [] and r["error"] == MASKED_ERROR


def retried(r, n):
    return (r["attempts"] == n + 1 and r["retry_classes"] == ["disconnect"] * n
            and DISCONNECT in r["error"] and "command failed" not in r["error"] and r["waits"] == [])


def make_mutant(td: Path) -> Path:
    """The candidate with ONLY the fix line reverted."""
    mut = td / "mutant"
    for sub in ("plugins", "tests"):
        shutil.copytree(ROOT / sub, mut / sub, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    sp = mut / "plugins/codex-oracle/server.py"
    src = sp.read_text()
    assert src.count(FIX_LINE) == 1, "the fix line is not where this instrument expects it"
    sp.write_text(src.replace(FIX_LINE, MUTANT_LINE))
    return mut


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base = td / "baseline"
        base.mkdir()
        blob = subprocess.run(["git", "-C", str(ROOT), "archive", BASELINE],
                              capture_output=True, check=True).stdout
        tarfile.open(fileobj=io.BytesIO(blob)).extractall(base)
        for rel in ("tests/test_transient_retry.py", "tests/fake_codex.py"):
            shutil.copy(ROOT / rel, base / rel)
        assert "last_command_failure" not in (base / "plugins/codex-oracle/server.py").read_text()
        mutant = make_mutant(td)
        b, b_out = probe(base)
        m, m_out = probe(mutant)
        c, c_out = probe(ROOT)

    for name, r, out in (("baseline", b, b_out), ("mutant", m, m_out), ("candidate", c, c_out)):
        if r is None:
            print(f"{name} probe did not complete — MACHINERY failure, not evidence")
            print(out[-400:])
            print("SENSITIVITY BROKEN")
            return 1

    problems: list[str] = []
    for name, r in (("baseline", b), ("mutant", m), ("candidate", c)):
        exercised(r, name, problems)
    n = c["max_retries"]
    if not masked(b):
        problems.append(f"baseline did not exhibit the MASKING (attempts={b['attempts']} error={b['error'][:80]!r})")
    if not masked(m):
        problems.append("MUTANT (fix line reverted) did NOT exhibit the masking — this instrument cannot "
                        f"tell the fix from its absence (attempts={m['attempts']} error={m['error'][:80]!r})")
    if not retried(c, n):
        problems.append(f"candidate did not exhibit the RETRIED disconnect (attempts={c['attempts']} error={c['error'][:80]!r})")

    for name, r in (("baseline " + BASELINE, b), ("mutant (fix reverted)", m), ("candidate tree      ", c)):
        d = dispatched(r)
        print(f"{name}: spawned={r['spawned']} dispatched cmd_failed={d['cmd_failed']} "
              f"disconnect={d['disconnect']} attempts={r['attempts']} classes={r['retry_classes']} "
              f"error={r['error'][:55]!r}")
    for p in problems:
        print("  !", p)
    print("SENSITIVITY", "OK" if not problems else "BROKEN")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
