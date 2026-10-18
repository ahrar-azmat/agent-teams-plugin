"""Timestamped, fixture-only shell mutations, including lingering descendants."""
import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import subprocess
import time

from _common import append_json


def run(path, mode):
    manifest = json.loads(Path(path).read_text())
    def log(event, **fields):
        append_json(manifest["command_log"], {"event": event, "time_ns": time.monotonic_ns(),
                    "pid": os.getpid(), "cwd": os.getcwd(), **fields})
    log("command_start", mode=mode)
    if mode == "background":
        child = subprocess.Popen([sys.executable, __file__, path, "child"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        # Retain the PID for explicit teardown if the timed write does not finish.
        log("child_created", child_pid=child.pid)
    else:
        if mode in ("yield", "interrupted"):
            time.sleep(2 if mode == "yield" else 20)
        if mode == "child":
            time.sleep(1)
        if mode == "interactive":
            print("U0_WAITING", flush=True)
            sys.stdin.readline()
        Path(manifest["command_target"]).write_text(mode)
        log("mutation")
    log("command_end")
    return 7 if mode == "nonzero" else 0


if __name__ == "__main__":
    raise SystemExit(run(*sys.argv[1:]))
