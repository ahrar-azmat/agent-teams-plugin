#!/usr/bin/env python3
"""Calibrated full-access patch mediation, failure, bypass, and hook-trust probes."""
import sys
sys.dont_write_bytecode = True

import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import time
import uuid

from _common import (ROOT, hook_command, hook_group, main, override_args, patch_add, patch_update,
                     read_jsonl, require, states, toml, tool_items, trusted_config, write_json)
from _responses_fixture import ResponsesFixture, patch_call, shell_call


def hooks_list(probe, name, home, repo, config):
    argv = [probe.native, "app-server", *override_args(config)]
    env = dict(os.environ, CODEX_HOME=str(home))
    proc = probe.spawn(argv, cwd=repo, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, bufsize=1)
    messages = []
    start = time.monotonic_ns()
    def exchange(message, ident):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 20
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(timeout=1):
                    continue
                line = proc.stdout.readline()
                require(line, "app-server closed before hooks/list response")
                record = json.loads(line)
                messages.append(record)
                if record.get("id") == ident:
                    return record
        raise RuntimeError("app-server hooks/list timeout")
    try:
        exchange({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "u0", "version": "1"},
                    "capabilities": {"experimentalApi": True}}}, 1)
        proc.stdin.write('{"method":"initialized"}\n')
        proc.stdin.flush()
        response = exchange({"id": 2, "method": "hooks/list", "params": {"cwds": [str(repo)]}}, 2)
    finally:
        proc.terminate()
        _, stderr = proc.communicate(timeout=10)
    write_json(probe.evidence / (name + "-hooks-list.json"), {"argv": argv, "start_ns": start,
               "exit_code": proc.returncode, "messages": messages, "stderr": stderr,
               "transport": "local_app_server_metadata"})
    require("result" in response, "hooks/list failed")
    return [h for entry in response["result"]["data"] for h in entry["hooks"]]


def trust_matrix(probe):
    cases = ("no-trust", "coordinator-only", "persisted-trust", "trusted-user-sources", "untrusted-user",
             "disabled-user", "stale-trust", "adjacent-ignore-user-config", "blanket-bypass")
    for index, name in enumerate(cases):
        repo = probe.repo("trust-" + name)
        home = probe.scratch / ("trust-home-" + name)
        home.mkdir()
        manifest = probe.manifest(repo, ["trusted.txt"])
        data = json.loads(manifest.read_text())
        coordinator = hook_group(hook_command(manifest, "deny"))
        groups = {"PreToolUse": [coordinator]}
        config = trusted_config(groups)
        user_config = {}
        flags = []
        expected = {"coordinator"}
        if name == "no-trust":
            config = {"features.hooks": True, "hooks.PreToolUse": [coordinator]}
            expected = set()
        elif name == "persisted-trust":
            user_config = trusted_config(groups, str(home / "config.toml"))
            config = {}
        elif name in ("trusted-user-sources", "untrusted-user", "disabled-user", "blanket-bypass", "adjacent-ignore-user-config"):
            canary = hook_group(hook_command(manifest, "observe", "user-toml"))
            adjacent = hook_group(hook_command(manifest, "observe", "adjacent-json"))
            project = hook_group(hook_command(manifest, "observe", "project-toml"))
            user_config = {"hooks.PreToolUse": [canary], "projects": {str(repo): {"trust_level": "trusted"}}}
            write_json(home / "hooks.json", {"hooks": {"PreToolUse": [adjacent]}})
            project_dir = repo / ".codex"
            project_dir.mkdir()
            (project_dir / "config.toml").write_text("hooks.PreToolUse=" + toml([project]) + "\n")
            if name in ("trusted-user-sources", "adjacent-ignore-user-config"):
                for source, group in ((home / "config.toml", canary), (home / "hooks.json", adjacent),
                                      (project_dir / "config.toml", project)):
                    trust = trusted_config({"PreToolUse": [group]}, str(source))
                    user_config.setdefault("hooks.state", {}).update(trust["hooks.state"])
                expected.update(("user-toml", "adjacent-json", "project-toml"))
            if name == "disabled-user":
                # Enable trust but persist disabled state independently of definition hash.
                user_config.update(trusted_config({"PreToolUse": [canary]}, str(home / "config.toml")))
                key = str(home / "config.toml") + ":pre_tool_use:0:0"
                user_config["hooks.state"][key]["enabled"] = False
            if name == "blanket-bypass":
                flags.append("--dangerously-bypass-hook-trust")
                expected.update(("user-toml", "adjacent-json", "project-toml"))
            if name == "adjacent-ignore-user-config":
                flags.append("--ignore-user-config")
                # Trust is invocation-scoped here; --ignore-user-config cannot hide
                # an adjacent hooks.json discovered beside the skipped config.
                config["hooks.state"].update(user_config["hooks.state"])
                config["projects"] = user_config["projects"]
                expected.discard("user-toml")
        elif name == "stale-trust":
            config["hooks.PreToolUse"] = [hook_group(hook_command(manifest, "deny", "changed-definition"))]
            expected = set()
        (home / "config.toml").write_text("\n".join(k + "=" + toml(v) for k, v in user_config.items()) + "\n")
        listed = hooks_list(probe, f"trust-{index:02}-{name}", home, repo, config)
        with ResponsesFixture(probe.scratch, [patch_call(patch_add("trusted.txt", "changed"))], home=home) as transport:
            result = probe.codex("trust-" + name, repo, "Run the one fixture operation.", config, transport, flags)
        logs = read_jsonl(data["log"])
        ran = {r["label"] for r in logs if r["event"] == "hook"}
        write_json(probe.evidence / ("trust-" + name + "-receipts.json"), {"listed": listed, "receipts": logs, "expected_labels": sorted(expected)})
        require(ran == expected, "Trust " + name + " observed unexpected canaries: " + str(ran))
        require((repo / "trusted.txt").exists() == ("coordinator" not in ran), "Trust case patch outcome inconsistent")
        if name == "coordinator-only":
            expected_hashes = {v["trusted_hash"] for v in config["hooks.state"].values()}
            require(expected_hashes <= {h["currentHash"] for h in listed}, "Local normalized fingerprint disagrees with hooks/list")
        probe.record("trust-" + name, "Canaries executed=" + str(sorted(ran)) + "; patch applied=" + str((repo / "trusted.txt").exists())
                     + "; hooks/list identity and trust state retained.", transport="local_fixture_transport")
    probe.record("trust-plugin-source", "No synthetic plugin is configured; plugin loading is not claimed measured. User TOML, adjacent JSON, project TOML, and invocation sources were measured.")


def run(probe):
    repo = probe.repo("calibration")
    manifest = probe.manifest(repo, ["calibration.txt"])
    before = states(repo, ["calibration.txt"])
    patch = patch_add("calibration.txt", "calibration")
    result = probe.codex("00-calibration", repo,
        "Use apply_patch directly once with this exact patch; no other tools; then say done.\n" + patch,
        {"features.hooks": False})
    require((repo / "calibration.txt").read_text() == "calibration\n" and tool_items(result["records"], "file_change"), "Calibration direct patch did not execute")
    require(not read_jsonl(json.loads(manifest.read_text())["log"]), "Disabled-hook calibration produced a receipt")
    write_json(probe.evidence / "calibration-states.json", {"before": before, "after": states(repo, ["calibration.txt"]), "coordinator_receipts": []})
    probe.calibrate("features.hooks=false; authenticated direct patch", "Patch changed the fixture without a coordinator receipt.")
    probe.record("calibration", "Authenticated patch applied without mediation.")

    def measure(name, mode="observe", patch=None, initial=None, paths=None, mutate=None, real=False, steps=None, replacement=None):
        repo = probe.repo(name)
        initial = initial if initial is not None else {"a.txt": "base\n"}
        for p, content in initial.items():
            (repo / p).parent.mkdir(parents=True, exist_ok=True)
            (repo / p).write_text(content)
        patch = patch or patch_update("a.txt")
        paths = paths or list(initial)
        manifest = probe.manifest(repo, paths, **({"replacement": replacement} if replacement else {}))
        data = json.loads(manifest.read_text())
        if mutate:
            mutate(repo)
        before = states(repo, paths)
        groups = {"PreToolUse": [hook_group(hook_command(manifest, mode), timeout=1 if mode == "timeout" else 2)]}
        if name == "shell-patch":
            groups["PreToolUse"].append(hook_group(hook_command(manifest, "observe", "bash"), matcher="^Bash$"))
        config = trusted_config(groups)
        if real:
            prompt = "Use apply_patch directly exactly once with this patch. A coordinator may apply it and then block native execution. Do not retry or use another tool. Briefly report the actual tool result.\n" + patch
            result = probe.codex(name, repo, prompt, config)
        else:
            actual_steps = steps(repo, manifest) if steps else [patch_call(patch)]
            with ResponsesFixture(probe.scratch, actual_steps) as transport:
                result = probe.codex(name, repo, "Execute the supplied fixture operation once.", config, transport)
        logs = read_jsonl(data["log"])
        after = states(repo, paths)
        write_json(probe.evidence / (name + "-receipts.json"), {"before": before, "after": after, "expected": data["expected"],
                  "patch": patch, "receipts": logs})
        # The required tool must be observed through a hook, event, or exact tool output.
        outputs = [i for request in result.get("requests", [])[1:] for i in request.get("input", [])
                   if i.get("type") in ("custom_tool_call_output", "function_call_output")]
        require(logs or tool_items(result["records"], "file_change") or tool_items(result["records"], "command_execution") or outputs,
                name + ": requested tool never executed")
        probe.record(name, "Mode=" + mode + "; callbacks=" + str([r["payload"].get("tool_name") for r in logs if r["event"] == "hook"])
                     + "; changed=" + str(before != after) + "; CAS=" + str([r.get("outcome") for r in logs if r["event"] == "cas"]),
                     transport=result["transport"])
        return repo, result, logs, before, after

    multi = "*** Begin Patch\n*** Update File: a.txt\n@@\n-base\n+changed\n*** Add File: b.txt\n+second\n*** End Patch\n"
    _, _, logs, _, _ = measure("01-complete-input", patch=multi, paths=["a.txt", "b.txt"])
    require(any(r.get("payload", {}).get("tool_input", {}).get("command") == multi for r in logs), "Complete patch not received by hook")
    _, result, logs, before, after = measure("02-explicit-deny", "deny")
    require(before == after and "Command blocked by PreToolUse hook:" in json.dumps(result["requests"]), "Explicit deny did not suppress native patch")
    repo, _, _, _, _ = measure("03-rewrite", "rewrite", paths=["a.txt", "replacement.txt"], replacement=patch_add("replacement.txt", "replacement"))
    require((repo / "a.txt").read_text() == "base\n" and (repo / "replacement.txt").read_text() == "replacement\n", "Rewrite not respected")
    _, result, logs, _, _ = measure("04-coordinator-apply", "apply-and-deny")
    require([r["outcome"] for r in logs if r["event"] == "cas"] == ["applied"] and "Command blocked by PreToolUse hook:" in json.dumps(result["requests"])
            and not tool_items(result["records"], "file_change"), "Coordinator apply-and-deny not exactly once")
    _, _, logs, _, _ = measure("04b-duplicate-operation", "apply-and-deny",
                              steps=lambda r, m: [patch_call(patch_update("a.txt")), patch_call(patch_update("a.txt"))])
    require([r["outcome"] for r in logs if r["event"] == "cas"] == ["applied", "duplicate"], "Duplicate operation was not deduplicated")
    _, result, logs, _, _ = measure("05-authenticated-coordinator", "apply-and-deny", real=True)
    require([r["outcome"] for r in logs if r["event"] == "cas"] == ["applied"] and not tool_items(result["records"], "file_change"), "Real model did not use single mediated patch")
    probe.record("authenticated-model-response", "Model response after coordinator denial: " + "\n".join(i.get("text", "") for i in tool_items(result["records"], "agent_message")), transport="authenticated_model")
    _, _, logs, before, after = measure("06-stale-preimage", "apply-and-deny", mutate=lambda r: (r / "a.txt").write_text("other writer\n"))
    require(before == after and any(r.get("outcome") == "stale_preimage" for r in logs), "Stale CAS did not refuse")

    # Two independent operations share the same previously recorded base and lock.
    repo = probe.repo("concurrent-cas")
    (repo / "a.txt").write_text("base\n")
    manifest = probe.manifest(repo, ["a.txt"])
    base = json.loads(manifest.read_text())
    manifests = []
    for index in range(2):
        path = manifest.parent / f"concurrent-{index}.json"
        write_json(path, {**base, "operation_id": uuid.uuid4().hex})
        manifests.append(path)
    children = []
    start = time.monotonic_ns()
    for index, path in enumerate(manifests):
        argv = [sys.executable, str(ROOT / "_coordinator.py"), "hook", str(path), "apply-and-deny", f"concurrent-{index}"]
        child = probe.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child.stdin.write(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch_update("a.txt", after=f"writer-{index}")}}))
        child.stdin.close()
        child.stdin = None
        children.append((child, argv))
    for index, (child, argv) in enumerate(children):
        out, err = child.communicate(timeout=15)
        write_json(probe.evidence / f"concurrent-{index}.json", {"argv": argv, "exit_code": child.returncode, "stdout": out, "stderr": err, "start_ns": start})
        require(child.returncode == 0, "Concurrent coordinator failed")
    logs = read_jsonl(base["log"])
    outcomes = [r["outcome"] for r in logs if r["event"] == "cas"]
    require(sorted(outcomes) == ["applied", "stale_preimage"], "Two same-base cooperative CAS operations both applied or neither applied")
    write_json(probe.evidence / "concurrent-receipts.json", {"receipts": logs, "after": states(repo, ["a.txt"])})
    probe.record("concurrent-cooperative-cas", "Two operations with the same base and shared lock: " + str(outcomes) + ". Only one applied.")

    invalid = "*** Begin Patch\n*** Update File: a.txt\n@@\n-base\n+changed\n*** Update File: b.txt\n@@\n-not-the-base\n+changed\n*** End Patch\n"
    _, _, _, before, after = measure("07-validation-failure", patch=invalid, initial={"a.txt": "base\n", "b.txt": "base\n"})
    require(before == after, "Invalid-hunk test unexpectedly changed files")
    partial = "*** Begin Patch\n*** Update File: a.txt\n@@\n-base\n+changed\n*** Add File: blocker/child.txt\n+child\n*** End Patch\n"
    _, result, _, before, after = measure("08-runtime-partial-failure", patch=partial, initial={"a.txt": "base\n", "blocker": "regular file\n"}, paths=["a.txt", "blocker", "blocker/child.txt"])
    require(before["a.txt"] != after["a.txt"] and after["blocker/child.txt"]["type"] == "absent", "Runtime failure fixture did not partially apply")
    require(any(i["status"] == "failed" for i in tool_items(result["records"], "file_change")), "Partial failure did not emit failed event")
    measure("09-coordinator-partial-failure", "apply-and-deny", patch=partial,
            initial={"a.txt": "base\n", "blocker": "regular file\n"}, paths=["a.txt", "blocker", "blocker/child.txt"])
    failures = {}
    for mode in ("crash", "exit1", "exit2-reason", "exit2-empty", "malformed", "timeout", "apply-and-crash"):
        _, result, logs, before, after = measure("failure-" + mode, mode)
        require(any(r.get("event") == "hook" for r in logs), mode + ": failure hook never entered")
        tool_outputs = [i.get("output", "") for req in result["requests"][1:] for i in req.get("input", [])
                        if i.get("type") == "custom_tool_call_output"]
        # Validation can fail before file_change emission. An absent event does
        # not prove native application was suppressed after coordinator failure.
        validation_attempted = any("apply_patch verification failed:" in str(output) for output in tool_outputs)
        native_attempted = bool(tool_items(result["records"], "file_change")) or validation_attempted
        failures[mode] = {"native_attempted": native_attempted, "changed": before != after,
                          "native_validation_failed": validation_attempted, "tool_outputs": tool_outputs,
                          "statuses": [i["status"] for i in tool_items(result["records"], "file_change")]}
    probe.record("failure-matrix", json.dumps(failures))
    command = shlex.join([sys.executable, "-c", "from pathlib import Path; Path('a.txt').write_text('shell bypass\\n')"])
    _, _, logs, before, after = measure("shell-bypass", "deny", steps=lambda r, m: [shell_call(command)])
    require(before != after and not logs, "Shell bypass was not demonstrated")
    shell_patch = patch_update("a.txt")
    _, _, logs, before, after = measure("shell-patch", steps=lambda r, m: [shell_call("apply_patch <<'U0_PATCH'\n" + shell_patch + "U0_PATCH\n")])
    require(before != after, "Shell apply_patch path did not run")
    probe.record("shell-patch-callbacks", "Shell apply_patch callback names=" + str([r["payload"]["tool_name"] for r in logs if r["event"] == "hook"]))
    trust_matrix(probe)
    probe.record("conclusion", "Complete input, rejection, rewrite and cooperative CAS are usable. Full-access complete mediation is disproved by shell bypass and measured hook failure behavior.")
    return "red"


if __name__ == "__main__":
    raise SystemExit(main("probe_apply_patch_mediation", "Can hooks enforce complete patch mediation for concurrent full-access writers?", run))
