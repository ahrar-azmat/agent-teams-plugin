"""Lead-run, local-only Git capability observations for setup. Python 3.9.

Fixtures never use an existing repository or a network remote. Successful control
pushes calibrate failures; nonzero alone does not establish guard coverage.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import push_guard

CASES = ("control", "ordinary", "url", "file_url", "explicit_pushurl", "pushurl_no_verify", "specific_rewrite", "cleared_environment")
RESOLUTION_ENV = ("GIT_EXEC_PATH", "GIT_ALLOW_PROTOCOL", "GIT_PROTOCOL_FROM_USER")


def binaries(source):
    seen, result = set(), []
    candidates = [Path("/usr/bin/git")]
    candidates.extend(Path(p or ".") / "git" for p in source.get("PATH", os.defpath).split(os.pathsep))
    for path in candidates:
        try:
            info = path.stat()
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            result.append({"path": str(path.absolute()), "realpath": str(path.resolve()),
                           "identity": [*identity, info.st_size, info.st_mtime_ns, info.st_ctime_ns]})
        except OSError:
            continue
    return result


def key(binary, source):
    digest = hashlib.sha256(json.dumps(binary, sort_keys=True).encode())
    for path in (Path(__file__), Path(push_guard.__file__), Path(push_guard.HELPERS) / ("git-remote-" + push_guard.NAME)):
        digest.update(path.read_bytes())
        digest.update(str(path.stat().st_mode).encode())
    # Resolution changes cannot reuse another launch PATH's helper observation.
    digest.update(source.get("PATH", os.defpath).encode())
    env = {"PATH": source.get("PATH", os.defpath), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           **{name: source[name] for name in RESOLUTION_ENV if name in source}}
    digest.update(json.dumps(env, sort_keys=True).encode())
    descriptions = []
    for option in ("--version", "--exec-path"):
        done = subprocess.run([binary["path"], option], env=env, capture_output=True, timeout=5,
                              stdin=subprocess.DEVNULL)
        if done.returncode or not done.stdout.strip():
            raise RuntimeError("binary identity query failed")
        digest.update(done.stdout)
        descriptions.append(done.stdout.decode().strip())
    # /usr/bin/git can select another developer toolchain without its own inode
    # changing. Also bind the executable that would shadow our appended helper.
    helper = "git-remote-" + push_guard.NAME
    search = os.pathsep.join((descriptions[1], source.get("PATH", os.defpath), push_guard.HELPERS))
    resolved = shutil.which(helper, path=search)
    if not resolved:
        raise RuntimeError("push-refusal helper cannot be resolved")
    path = Path(resolved)
    digest.update(str(path.resolve()).encode())
    digest.update(str(path.stat().st_mode).encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(binary, source):
    """Actual Git operations. Caller is setup/the lead, never a Codex launch constructor."""
    if (push_guard.MARKER in os.environ or os.environ.get("SOFTWARE_WORKFLOWS_ROLE") == "writer" or
            any(key in os.environ for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"))):
        push_guard.fail("Capability controls publish only in disposable fixtures and must be run by the lead (Claude)")
    root = Path(tempfile.mkdtemp(prefix="r2428p-capability-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None))
    result = {"binary": binary, "scope": "isolated local fixtures; not an execution boundary", "cases": []}
    try:
        if shutil.disk_usage(root).free < 15 * 1024 ** 3:
            raise RuntimeError("disk floor: 15 GiB free required")
        env = {"PATH": source.get("PATH", os.defpath), "HOME": str(root), "LANG": "C",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "local capability probe",
               "GIT_COMMITTER_NAME": "local capability probe", "GIT_AUTHOR_EMAIL": "probe@example.invalid",
               "GIT_COMMITTER_EMAIL": "probe@example.invalid"}
        env.update({name: source[name] for name in RESOLUTION_ENV if name in source})
        executable = binary["path"]
        def run(args, current=None, environment=None, required=False):
            done = subprocess.run([executable, *args], cwd=current or root, env=environment or env,
                                  capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            if required and done.returncode:
                raise RuntimeError("fixture preparation failed: " + args[0] + " exit " + str(done.returncode))
            return done
        version = run(["--version"], required=True)
        result["version"], result["version_exit"] = version.stdout.strip(), version.returncode
        repo = root / "source"
        run(["init", "-q", "-b", "main", str(repo)], required=True)
        hooks = repo / ".husky"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf '%s\\n' HUSKY_FIXTURE_RAN >&2\n")
        hook.chmod(0o755)
        run(["config", "core.hooksPath", ".husky"], repo, required=True)
        (repo / "payload").write_text("local capability observation\n")
        run(["add", "payload"], repo, required=True)
        guarded = push_guard.environment(env)
        commit = run(["commit", "-qm", "local fixture"], repo, guarded, required=True)
        result["commit"] = {"exit": commit.returncode, "husky_ran": "HUSKY_FIXTURE_RAN" in commit.stderr}
        head = run(["rev-parse", "HEAD"], repo, required=True).stdout.strip()
        for case in CASES:
            remote = root / (case + ".git")
            run(["init", "-q", "--bare", "-b", "main", str(remote)], required=True)
            remote_url = str(remote)
            args = ["-c", "remote.origin.url=" + remote_url]
            if case in ("explicit_pushurl", "pushurl_no_verify"):
                args += ["-c", "remote.origin.pushurl=" + remote_url]
            if case == "specific_rewrite":
                args += ["-c", "url." + remote_url + ".pushInsteadOf=fixture:"]
            destination = remote_url if case == "url" else remote.as_uri() if case == "file_url" else "fixture:" if case == "specific_rewrite" else "origin"
            args += ["push", *( ["--no-verify"] if case in ("pushurl_no_verify", "specific_rewrite") else []), destination, "main"]
            before = run(["--git-dir", remote_url, "rev-parse", "--verify", "refs/heads/main"])
            current_env = env if case in ("control", "cleared_environment") else guarded
            done = run(args, repo, current_env)
            after = run(["--git-dir", remote_url, "rev-parse", "--verify", "refs/heads/main"])
            changed = after.returncode == 0 and after.stdout.strip() == head
            refusal = push_guard.REFUSAL in done.stderr
            status = ("published" if changed else "unverified") if case == "control" else (
                "not covered" if changed or done.returncode == 0 else "refused" if refusal else "unverified")
            result["cases"].append({"case": case, "command": [executable, *args], "exit": done.returncode,
                "status": status, "guard_present": push_guard.present(current_env), "refusal": push_guard.REFUSAL if refusal else None,
                "before": before.stdout.strip() if before.returncode == 0 else None, "before_exit": before.returncode,
                "after": after.stdout.strip() if after.returncode == 0 else None, "after_exit": after.returncode})
        control = result["cases"][0]
        if control["status"] != "published" or control["exit"] != 0:
            for row in result["cases"][1:]:
                row["status"] = "unverified"
            result["error"] = "unguarded control did not publish"
        remote = root / "control.git"
        clone = run(["clone", "-q", str(remote), str(root / "clone")], environment=guarded)
        fetch = run(["-c", "remote.origin.url=" + str(remote), "fetch", "origin"], repo, guarded)
        status = run(["status", "--porcelain"], repo, guarded)
        result["reads"] = {"clone_exit": clone.returncode, "fetch_exit": fetch.returncode, "status_exit": status.returncode}
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        result["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        for row in result["cases"]:
            if row["case"] != "control":
                row["status"] = "unverified"
    finally:
        shutil.rmtree(root)
        result["fixture_removed"] = not root.exists()
    return result


def report(data_dir=None, quick=False):
    from .client import clean_environment
    source = clean_environment(codex=False)
    if os.sys.platform == "darwin" and "/opt/homebrew/bin" not in source.get("PATH", "").split(os.pathsep):
        source["PATH"] = "/opt/homebrew/bin:" + source.get("PATH", os.defpath)
    directory = Path(data_dir or os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".cache" / "software-workflows") / "push-capabilities"
    results = []
    for binary in binaries(source):
        try:
            identity = key(binary, source)
            path = directory / (identity + ".json")
            try:
                saved = json.loads(path.read_text())
                result = saved["result"]
                if (saved.get("identity") != identity or result.get("binary") != binary or
                        not result.get("fixture_removed") or not isinstance(result.get("cases"), list) or
                        [row.get("case") for row in result["cases"]] != list(CASES) or
                        any(row.get("status") not in ("refused", "not covered", "unverified", "published") for row in result["cases"])):
                    raise ValueError("invalid cache")
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                result = {"binary": binary, "cases": [], "error": "unverified: run setup check"} if quick else probe(binary, source)
                if not quick:
                    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                    fd, pending = tempfile.mkstemp(prefix="pending-", dir=directory)
                    try:
                        with os.fdopen(fd, "w") as stream:
                            json.dump({"identity": identity, "result": result}, stream, sort_keys=True)
                        os.replace(pending, path)
                    finally:
                        if os.path.exists(pending):
                            os.unlink(pending)
            results.append(result)
        except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as exc:
            results.append({"binary": binary, "cases": [],
                            "error": "unverified: " + str(getattr(exc, "code", type(exc).__name__))})
    return results or [{"binary": {"path": "git"}, "cases": [], "error": "unverified: no executable found"}]
