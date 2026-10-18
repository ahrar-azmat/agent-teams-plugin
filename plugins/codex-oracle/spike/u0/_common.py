"""Shared, stdlib-only U0 measurement harness. Importing it has no side effects.

Write contract of a probe run: its scratch root (removed at the end), its evidence
directory, and — only with --update-report — the measured section of FEASIBILITY.md.
Authenticated model calls run with the caller's CODEX_HOME, where Codex leaves what any
run of it leaves (state databases, logs, installation metadata), and with the caller's
own Codex configuration: its MCP servers, hooks and plugins run too and write wherever
they write, which U0 neither inspects nor bounds. Fixture-transport calls get a scratch
CODEX_HOME."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[3]


class Inconclusive(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Inconclusive(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def append_json(path, value):
    # Each small receipt is a single append write, shared by fixture processes.
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def native_codex():
    search = os.pathsep.join(["/opt/homebrew/bin", os.environ.get("PATH", "")])
    override = os.environ.get("CODEX_ORACLE_CODEX_BIN", "").strip()
    candidate = override or "codex"
    path = Path(candidate if os.sep in candidate else (shutil.which(candidate, path=search) or candidate)).absolute().resolve()
    require(path.is_file(), "Installed codex not found")
    with path.open("rb") as source:
        launcher = source.read(2) == b"#!"
    if launcher:
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine().lower())
        require(arch, "Unsupported native architecture")
        npm_arch = "arm64" if arch == "aarch64" else "x64"
        if sys.platform == "darwin":
            package, triple = f"codex-darwin-{npm_arch}", f"{arch}-apple-darwin"
        elif sys.platform.startswith("linux"):
            package, triple = f"codex-linux-{npm_arch}", f"{arch}-unknown-linux-musl"
        elif os.name == "nt":
            package, triple = f"codex-win32-{npm_arch}", f"{arch}-pc-windows-msvc"
        else:
            raise Inconclusive("Unsupported native platform")
        exe = "codex.exe" if os.name == "nt" else "codex"
        package_root = path.parent.parent
        choices = [package_root / "node_modules" / "@openai" / package / "vendor" / triple / "bin" / exe,
                   package_root / "vendor" / triple / "bin" / exe]
        path = next((p for p in choices if p.is_file() and os.access(p, os.X_OK)), None)
        require(path, "Exact-target native executable not found behind launcher")
    with path.open("rb") as source:
        require(source.read(2) != b"#!", "Resolved executable is still a launcher")
    return str(path)


def git_env():
    # Synthetic metadata queries never consult global/system Git configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
    return env


def git(path, *args):
    return subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                           "-c", "core.hooksPath=" + os.devnull, "-C", str(path), *args],
                          env=git_env(), capture_output=True, text=True, timeout=20)


def init_repo(path, separate=None):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    args = ["init", "--quiet"]
    if separate:
        args += ["--separate-git-dir", str(separate)]
    result = git(path, *args)
    require(result.returncode == 0, "Synthetic git init failed: " + result.stderr)
    return path


def file_state(path):
    path = Path(path)
    try:
        st = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return {"type": "absent"}
    result = {"mode": stat.S_IMODE(st.st_mode)}
    if stat.S_ISREG(st.st_mode):
        result.update(type="file", sha256=digest(path.read_bytes()), size=st.st_size)
    elif stat.S_ISLNK(st.st_mode):
        result.update(type="symlink", target=os.readlink(path))
    elif stat.S_ISDIR(st.st_mode):
        result.update(type="directory")
    else:
        result.update(type="other")
    return result


def states(repo, paths):
    return {p: file_state(Path(repo) / p) for p in paths}


def toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
    raise TypeError(type(value))


def override_args(config):
    return [arg for k, v in config.items() for arg in ("-c", k + "=" + toml(v))]


def hook_group(command, matcher="^apply_patch$", timeout=2):
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command,
                                           "timeout": timeout, "async": False}]}


def trusted_config(groups, source="/<session-flags>/config.toml"):
    config = {"features.hooks": True, "hooks.state": {}}
    for event, entries in groups.items():
        config["hooks." + event] = entries
        label = {"PreToolUse": "pre_tool_use", "PostToolUse": "post_tool_use"}[event]
        for index, group in enumerate(entries):
            canonical = {"event_name": label, **group}
            hash_value = "sha256:" + digest(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
            key = f"{source}:{label}:{index}:0"
            # CLI -c dotted keys do not parse quoted TOML path components. Pass
            # the state map as one inline table so quotes are value syntax.
            config["hooks.state"][key] = {"trusted_hash": hash_value}
    return config


def hook_command(manifest, mode="observe", label="coordinator"):
    return shlex.join([sys.executable, str(ROOT / "_coordinator.py"), "hook",
                       str(manifest), mode, label])


def patch_add(path, data):
    return "*** Begin Patch\n*** Add File: " + path + "\n" + "\n".join("+" + s for s in data.splitlines()) + "\n*** End Patch\n"


def patch_update(path, before="base", after="changed"):
    return f"*** Begin Patch\n*** Update File: {path}\n@@\n-{before}\n+{after}\n*** End Patch\n"


def tool_items(records, kind):
    return [r["item"] for r in records if isinstance(r.get("item"), dict) and r["item"].get("type") == kind]


class Probe:
    def __init__(self, name, hypothesis, update_report=False):
        self.name, self.hypothesis, self.update_report = name, hypothesis, update_report
        self.cases = []
        self.calibration = {"verdict": "inconclusive", "configuration": "not run", "observed": "not run"}
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.evidence = ROOT / "evidence" / name / self.run_id
        self.evidence.mkdir(parents=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-u0-", dir="/tmp")
        self.scratch = Path(self.tmp.name).resolve()
        self.processes = []
        self.native = native_codex()
        version = subprocess.run([self.native, "--version"], capture_output=True, text=True, timeout=10)
        require(version.returncode == 0, "codex version unavailable")
        self.metadata = {"version": version.stdout.strip(), "native": self.native,
                         "sha256": digest(Path(self.native).read_bytes()), "scratch_root": str(self.scratch),
                         "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "python": sys.version,
                         "probe_code_sha256": {p.name: digest(p.read_bytes()) for p in sorted(ROOT.glob("*.py"))}}
        require(self.metadata["version"] == "codex-cli 0.163.0", "This probe specifies codex-cli 0.163.0")

    def repo(self, name):
        return init_repo(self.scratch / name)

    def manifest(self, repo, paths, **extra):
        directory = self.scratch / ("control-" + uuid.uuid4().hex[:10])
        directory.mkdir()
        path = directory / "manifest.json"
        value = {"repo": str(repo), "native": self.native, "paths": paths,
                 "expected": states(repo, paths), "log": str(directory / "hooks.jsonl"),
                 "lock": str(directory / "coordinator.lock"), "operations": str(directory / "operations.json"),
                 "operation_id": uuid.uuid4().hex, **extra}
        write_json(path, value)
        return path

    def spawn(self, argv, **kwargs):
        proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
        self.processes.append(proc)
        return proc

    def run(self, name, argv, prompt="", env=None, cwd=None, timeout=90, interrupt_after=None):
        start = time.monotonic_ns()
        proc = self.spawn(argv, cwd=cwd or self.scratch, env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        timed_out = False
        interrupted = False
        try:
            if interrupt_after is not None:
                try:
                    stdout, stderr = proc.communicate(prompt, timeout=interrupt_after)
                except subprocess.TimeoutExpired:
                    interrupted = True
                    os.kill(proc.pid, signal.SIGINT)
                    stdout, stderr = proc.communicate(timeout=15)
            else:
                stdout, stderr = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        result = {"name": name, "argv": argv, "prompt": prompt, "cwd": str(cwd or self.scratch),
                  "exit_code": proc.returncode, "stdout": stdout, "stderr": stderr,
                  "start_ns": start, "end_ns": time.monotonic_ns(), "timed_out": timed_out,
                  "interrupted": interrupted}
        write_json(self.evidence / (name + ".json"), result)
        return result

    def codex(self, name, repo, prompt, config=None, transport=None, flags=(), interrupt_after=None):
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        argv = [self.native, "exec", "--dangerously-bypass-approvals-and-sandbox", "--json", "--ephemeral",
                "--color", "never", "-C", str(repo), "-c", "model_reasoning_effort=low"]
        combined = dict(config or {})
        if transport:
            env["CODEX_HOME"] = str(transport.home)
            combined.update(transport.config)
        argv += override_args(combined) + list(flags) + ["-"]
        result = self.run(name, argv, prompt, env=env, cwd=repo, interrupt_after=interrupt_after)
        result["transport"] = "local_fixture_transport" if transport else "authenticated_model"
        records, errors = [], []
        for line in result["stdout"].splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                errors.append(line)
        result.update(records=records, malformed_lines=errors)
        if transport:
            result["requests"] = transport.requests.copy()
            result["transport_errors"] = transport.errors.copy()
        write_json(self.evidence / (name + ".json"), result)
        require(not result["timed_out"] and not errors, name + ": timeout or malformed JSON stream")
        if interrupt_after is None:
            require(result["exit_code"] == 0 and any(r.get("type") == "turn.completed" for r in records),
                    name + ": CLI/model did not complete; inspect evidence")
        if transport:
            require(not transport.errors and transport.requests, name + ": local transport not exercised")
        return result

    def record(self, name, observation, **extra):
        case = {"name": name, "observation": observation, **extra}
        self.cases.append(case)
        return case

    def calibrate(self, configuration, observed):
        self.calibration = {"verdict": "red", "configuration": configuration, "observed": observed}

    def owns(self, pid):
        """Whether `pid` is still one of this probe's fixture processes: its argv names the
        probe's unique scratch root. A recorded PID whose process ended may be another's now."""
        status = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
        return str(self.scratch) in status.stdout

    def finish(self, verdict, error=None):
        cleanup_start = time.monotonic_ns()
        cleanup_errors = []
        for proc in self.processes:
            try:
                if proc.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        # Native exec can detach a child into a different process group. Inspect
        # only PIDs recorded by our generated helpers, and require the unique
        # scratch path in argv before signalling anything (not a bare-PID sweep).
        fixture_pids = set()
        for manifest_path in self.scratch.glob("control-*/manifest.json"):
            manifest = json.loads(manifest_path.read_text())
            for key in ("log", "command_log", "mcp_log"):
                if key in manifest:
                    for entry in read_jsonl(manifest[key]):
                        for pid_key in ("pid", "child_pid"):
                            if isinstance(entry.get(pid_key), int):
                                fixture_pids.add(entry[pid_key])
        live_after = []
        for pid in sorted(fixture_pids):
            def owned_alive():
                return self.owns(pid)
            try:
                if owned_alive():
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
                    deadline = time.monotonic() + 5
                    while owned_alive() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    if owned_alive():
                        live_after.append(pid)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        try:
            self.tmp.cleanup()
        except Exception as exc:
            cleanup_errors.append(str(exc))
        gone = not self.scratch.exists()
        if cleanup_errors or not gone or live_after:
            verdict = "inconclusive"
        result = {"probe": self.name, "calibration": self.calibration,
                  "result": {"hypothesis": self.hypothesis, "cases": self.cases}, "verdict": verdict,
                  "evidence_paths": [str(self.evidence.relative_to(WORKSPACE))]}
        if error:
            result["error"] = error
        detail = {**result, "metadata": self.metadata,
                  "cleanup": {"scratch_root": str(self.scratch), "gone": gone,
                              "owned_processes_reaped": all(p.poll() is not None for p in self.processes),
                              "fixture_pids_checked": sorted(fixture_pids), "fixture_processes_remaining": live_after,
                              "errors": cleanup_errors, "elapsed_seconds": (time.monotonic_ns() - cleanup_start) / 1e9}}
        write_json(self.evidence / "result.json", detail)
        if self.update_report:
            render_report()
        else:
            print("FEASIBILITY.md not changed (--update-report rewrites its measured section).")
        print(f"{self.name}: calibration {self.calibration['verdict']}; {len(self.cases)} measured cases; {verdict}. Scratch removed: {gone}.")
        print(json.dumps(result, separators=(",", ":")))
        return 2 if verdict == "inconclusive" else 0


def render_report():
    # Separate probes may finish concurrently; serialize the measured-section
    # replacement so a slow writer cannot restore an older result set.
    with (ROOT / "evidence" / ".report.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _render_report()


def _render_report():
    report = ROOT / "FEASIBILITY.md"
    if not report.exists():
        return
    start, end = "<!-- U0 MEASURED START -->", "<!-- U0 MEASURED END -->"
    body = report.read_text()
    if start not in body or end not in body:
        return
    lines = [start, "", "Generated from retained measurements. All argv below are expanded argument arrays; stdin prompts and raw output are in the linked case files.", ""]
    for name in ("probe_apply_patch_mediation", "probe_mutator_hooks", "probe_instruction_loading", "probe_file_change_events"):
        results = sorted((ROOT / "evidence" / name).glob("*/result.json"))
        lines += ["### " + name, ""]
        if not results:
            lines += ["Not run.", ""]
            continue
        path = results[-1]
        value = json.loads(path.read_text())
        lines += [f"Verdict: **{value['verdict']}**. [Durable result]({path.relative_to(ROOT)}).", "",
                  "```json", json.dumps({"metadata": value["metadata"], "calibration": value["calibration"],
                                        "cleanup": value["cleanup"], "error": value.get("error")}, indent=2), "```", ""]
        for case in value["result"]["cases"]:
            lines += ["- **" + case["name"] + "**: " + case["observation"]]
        lines += ["", "<details><summary>Exact invocation arrays (calibration first)</summary>", ""]
        commands = []
        for p in path.parent.glob("*.json"):
            record = json.loads(p.read_text())
            if "argv" in record:
                commands.append((record.get("start_ns", 0), p, record))
        for _, p, record in sorted(commands):
            lines += [f"[{p.stem}]({p.relative_to(ROOT)}) — {record.get('transport', 'local subprocess')}; exit {record['exit_code']}.", "",
                      "```json", json.dumps(record["argv"], indent=2), "```", ""]
        lines += ["</details>", ""]
    lines += [end]
    # The report is committed to a public repository: the checkout and the home directory
    # are named, not spelled out (the retained evidence keeps the full paths).
    measured = "\n".join(lines).replace(str(WORKSPACE), "<checkout>").replace(str(Path.home()), "~")
    report.write_text(body[:body.index(start)] + measured + body[body.index(end) + len(end):])


def main(name, hypothesis, run):
    parser = argparse.ArgumentParser(description=hypothesis)
    parser.add_argument("--real", action="store_true", required=True,
                        help="Execute the installed native Codex, including authenticated tiny model calls. "
                             "Those calls use the caller's CODEX_HOME, and Codex leaves there what any run of "
                             "it leaves (state databases, logs, installation metadata); they also run the "
                             "caller's MCP servers, hooks and plugins, whose writes U0 does not bound. The "
                             "probe's own writes stay in its scratch root and its evidence directory")
    parser.add_argument("--update-report", action="store_true",
                        help="Also rewrite the measured section of FEASIBILITY.md from the retained results")
    args = parser.parse_args()
    probe = None
    try:
        probe = Probe(name, hypothesis, update_report=args.update_report)
        verdict = run(probe)
        return probe.finish(verdict)
    except (Exception, KeyboardInterrupt) as exc:
        if probe:
            write_json(probe.evidence / "harness-error.json", {"error": str(exc), "traceback": traceback.format_exc()})
            return probe.finish("inconclusive", str(exc))
        print(f"{name}: inconclusive startup: {exc}")
        print(json.dumps({"probe": name, "calibration": {"verdict": "inconclusive"}, "result": {},
                          "verdict": "inconclusive", "evidence_paths": [], "error": str(exc)}))
        return 2
