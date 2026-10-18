#!/usr/bin/env python3
"""Measure actual shell/MCP pre/post windows, including missing-finally paths."""
import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
import uuid

from _common import (ROOT, hook_command, hook_group, main, read_jsonl, require, states,
                     tool_items, trusted_config, write_json)
from _responses_fixture import ResponsesFixture, continue_session, mcp_call, shell_call


def mcp_configuration(manifest):
    return {"mcp_servers.u0_canary": {"command": sys.executable,
        "args": [str(ROOT / "_mcp_canary.py"), str(manifest)], "startup_timeout_sec": 10, "tool_timeout_sec": 10}}


def poll_or_finish(request):
    outputs = [i for i in request.get("input", []) if i.get("type") == "function_call_output"]
    if re.search(r"[Ss]ession (?:ID|id)[: ]+\d+", json.dumps(outputs[-1:])):
        return continue_session(request)
    return None


def run(probe):
    repo = probe.repo("calibration")
    manifest = probe.manifest(repo, ["shell.txt"])
    before = states(repo, ["shell.txt"])
    calibration_config = trusted_config({event: [hook_group(hook_command(manifest), "^Bash$")]
                                        for event in ("PreToolUse", "PostToolUse")})
    calibration_config["features.hooks"] = False
    command = shlex.join([sys.executable, "-c", "from pathlib import Path; Path('shell.txt').write_text('calibration')"])
    result = probe.codex("00-calibration", repo,
        "Use exec_command once, login=false, with exactly: " + command + ". Then say done; no other tools.", calibration_config)
    require((repo / "shell.txt").read_text() == "calibration" and tool_items(result["records"], "command_execution"), "Calibration shell never executed")
    logs = read_jsonl(json.loads(manifest.read_text())["log"])
    require(not logs, "Disabled-hook calibration unexpectedly called the coordinator")
    write_json(probe.evidence / "calibration-states.json", {"before": before, "after": states(repo, ["shell.txt"]), "coordinator_receipts": logs})
    probe.calibrate("features.hooks=false; authenticated shell mutation", "Shell mutated the file without any coordinator pre/post window.")
    probe.record("calibration", "Authenticated shell write succeeded without pre/post receipts.")

    def measure(name, mode="short", real=False, mcp=False, deny=False):
        repo = probe.repo(name)
        manifest = probe.manifest(repo, ["marker.txt"], fixture_id=uuid.uuid4().hex)
        data = json.loads(manifest.read_text())
        data.update(command_log=str(manifest.parent / "command.jsonl"), command_target=str(repo / "marker.txt"),
                    mcp_log=str(manifest.parent / "mcp.jsonl"), mcp_target=str(repo / "marker.txt"),
                    window_port=str(manifest.parent / "port"))
        write_json(manifest, data)
        server_log = (manifest.parent / "server.stderr").open("w+")
        server = probe.spawn([sys.executable, str(ROOT / "_coordinator.py"), "serve", str(manifest)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=server_log)
        try:
            deadline = time.monotonic() + 5
            while not Path(data["window_port"]).exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            require(Path(data["window_port"]).exists(), "Persistent window coordinator did not start")
            matcher = "^(Bash|mcp__u0_canary__write_marker)$"
            config = trusted_config({
                "PreToolUse": [hook_group(hook_command(manifest, "deny" if deny else "window"), matcher, 2)],
                "PostToolUse": [hook_group(hook_command(manifest, "window"), matcher, 2)]})
            command = shlex.join([sys.executable, str(ROOT / "_command_fixture.py"), str(manifest), mode])
            if mcp:
                config.update(mcp_configuration(manifest))
                arguments = {"fixture_id": data["fixture_id"], "data": "mcp marker", "mode": mode}
                steps = [mcp_call("mcp__u0_canary__write_marker", arguments)]
                prompt = "Call only u0_canary.write_marker once with exactly these arguments, then say done: " + json.dumps(arguments)
            else:
                kwargs = {}
                if mode in ("yield", "interactive", "interrupted"):
                    kwargs["yield_time_ms"] = 100
                if mode == "interactive":
                    kwargs["tty"] = True
                if mode == "workdir":
                    (repo / "alternate").mkdir()
                    kwargs["workdir"] = str(repo / "alternate")
                if mode == "handler-error":
                    kwargs["workdir"] = str(repo / "does-not-exist")
                if mode == "interrupted":
                    kwargs["yield_time_ms"] = 10000
                steps = [shell_call(command, **kwargs)]
                if mode == "yield":
                    steps += [poll_or_finish] * 4
                elif mode == "interactive":
                    steps += [lambda request: continue_session(request, "mutate\n"), poll_or_finish]
                prompt = "Use exec_command once, login=false, with exactly this command; then say done, no other tools: " + command
            if real:
                result = probe.codex(name, repo, prompt, config)
            else:
                with ResponsesFixture(probe.scratch, steps) as transport:
                    result = probe.codex(name, repo, "Run the fixture operation.", config, transport,
                                         interrupt_after=3 if mode == "interrupted" else None)
            if mode == "background":
                deadline = time.monotonic() + 5
                while not any(r["event"] == "mutation" for r in read_jsonl(data["command_log"])) and time.monotonic() < deadline:
                    time.sleep(0.05)
            logs = read_jsonl(data["log"])
            commands = read_jsonl(data["command_log"])
            mcp_log = read_jsonl(data["mcp_log"])
            write_json(probe.evidence / (name + "-receipts.json"), {"hooks": logs, "commands": commands, "mcp": mcp_log})
            pre = [r for r in logs if r.get("payload", {}).get("hook_event_name") == "PreToolUse"]
            post = [r for r in logs if r.get("payload", {}).get("hook_event_name") == "PostToolUse"]
            require(pre, name + ": verified pre callback missing")
            if mode == "handler-error":
                require(not commands and "No such file" in json.dumps(result), "Handler-error fixture did not produce requested error")
            elif mode == "interrupted":
                require(result["interrupted"] and any(r["event"] == "command_start" for r in commands), "Interrupted case never started command")
            elif deny:
                require(not mcp_log and not (repo / "marker.txt").exists(), "MCP denial did not suppress mutation")
            else:
                require((repo / "marker.txt").exists(), name + ": requested mutation missing")
            timeline = mcp_log if mcp else commands
            starts = [r["time_ns"] for r in timeline if r["event"] in ("command_start", "tool_entry")]
            ends = [r["time_ns"] for r in timeline if r["event"] in ("command_end", "tool_return")]
            mutations = [r["time_ns"] for r in timeline if r["event"] == "mutation"]
            ordered = bool(pre and post and starts and ends and pre[0]["time_ns"] <= min(starts) <= max(ends) <= post[-1]["time_ns"])
            descendant_after_post = bool(post and any(t > post[-1]["time_ns"] for t in mutations))
            if not deny and mode in ("short", "nonzero", "yield", "interactive", "workdir", "success", "delayed"):
                require(ordered, name + ": ordinary pre/start/end/post ordering failed")
            if mode == "workdir":
                require(commands[0]["cwd"] == str(repo / "alternate"), "Alternate workdir not used")
                require("workdir" not in pre[0]["payload"]["tool_input"], "Bash hook unexpectedly included workdir; update result interpretation")
            if mode == "nonzero":
                require(any(i.get("exit_code") == 7 for i in tool_items(result["records"], "command_execution")), "Nonzero process status not observed")
            if mode == "interactive":
                require(len(pre) == 1, "Interactive input unexpectedly produced a new pre-hook; inspect")
            summary = {"pre_count": len(pre), "post_count": len(post), "ordered_window": ordered,
                       "mutation_after_post": descendant_after_post, "tool_names": [r["payload"].get("tool_name") for r in pre],
                       "tool_use_ids": [r["payload"].get("tool_use_id") for r in pre + post],
                       "window_acquires": sum(r["event"] == "window_acquired" for r in logs),
                       "window_releases": sum(r["event"] == "window_released" for r in logs),
                       "file_states": states(repo, ["marker.txt"])}
            write_json(probe.evidence / (name + "-receipts.json"), {"summary": summary, "hooks": logs, "commands": commands, "mcp": mcp_log})
            probe.record(name, json.dumps(summary), transport=result["transport"])
            if mode == "background":
                require(descendant_after_post, "Background fixture did not demonstrate mutation after post")
            # Stop only fixture processes that never recorded their end and are still ours:
            # a recorded PID may have ended and been reused, so its argv must still name
            # this probe's scratch root (a failed check fails the case, never signals).
            started = {r["pid"] for r in commands if r["event"] == "command_start"}
            ended = {r["pid"] for r in commands if r["event"] == "command_end"}
            for pid in started - ended:
                if probe.owns(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            server.terminate()
            server.wait(timeout=5)
            server_log.seek(0)
            stderr = server_log.read()
            server_log.close()
            (probe.evidence / (name + "-window-server.txt")).write_text(stderr)

    measure("01-authenticated-short", real=True)
    for index, mode in enumerate(("short", "nonzero", "handler-error", "yield", "interactive", "background", "workdir", "interrupted"), 2):
        measure(f"{index:02}-{mode}", mode)
    measure("10-authenticated-mcp", "success", real=True, mcp=True)
    measure("11-mcp-success", "success", mcp=True)
    measure("12-mcp-denial", "success", mcp=True, deny=True)
    measure("13-mcp-error", "error", mcp=True)
    measure("14-mcp-delayed", "delayed", mcp=True)
    probe.record("conclusion", "Shell Bash and canonical MCP pre/post interception exist. Background mutations after post and missing error/interruption post callbacks prevent treating post as a universal finally block. Windows require persistent custody and explicit recovery.")
    return "red"


if __name__ == "__main__":
    raise SystemExit(main("probe_mutator_hooks", "Can native pre/post hooks bound every shell and MCP mutation for exactly its duration?", run))
