"""Helper-free Git plumbing; reuse the repository's audited argv/environment primitives."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import selectors
import tempfile
import time

from treedigest import git_argv, git_env, GIT_SAFE_CONFIG, routing_env_names
from .client import CoordinatorError
from . import telemetry, calibration_temp


@telemetry.measured("git")
def git(root, args, data=None, index=None, normal_config=False, extra_env=None, check=True):
    env = git_env()
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    env.update(extra_env or {})
    # Config reads must see the real value, including an unsafe fsmonitor. The
    # plumbing that inspects objects still disables that executable helper.
    config = (() if args[0] == "config" else ("-c", "core.fsmonitor=false")) if normal_config else GIT_SAFE_CONFIG
    result = subprocess.run(git_argv(str(root), list(args), config=config), input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=120)
    if check and result.returncode:
        # stderr can contain config or credentials; return the operation and code only.
        raise CoordinatorError("GIT_FAILED", "Git plumbing failed", {"command": args[0], "exit_code": result.returncode}, True)
    return result


def calibrate(state):
    """Prove missing-object reads cannot invoke a promisor remote helper.

    Keep both the deliberately unsafe observation and the safe observation.
    This cache belongs to C1, never to treedigest's legacy user-home cache.
    """
    names = routing_env_names()
    if names:
        raise CoordinatorError("GIT_ROUTING_ENV", "Git routing overrides require an unambiguous checkout environment", {"names": names})
    binary = Path(shutil.which("git") or "git").resolve()
    fingerprint = {"binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                   "version": subprocess.run([str(binary), "--version"], capture_output=True, check=True).stdout.decode().strip(),
                   "primitives": hashlib.sha256(Path(__file__).parents[1].joinpath("treedigest.py").read_bytes()).hexdigest(),
                   "probe": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "temporary_directories": hashlib.sha256(Path(calibration_temp.__file__).read_bytes()).hexdigest()}
    key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    cache = Path(state) / "calibrations" / ("git-" + key + ".json")
    if cache.exists():
        value = json.loads(cache.read_text())
        if value.get("completed") is True and value.get("fingerprint") == fingerprint:
            return value
    value = {"fingerprint": fingerprint, "completed": False, "created": time.time(),
             "evidence": {"directory": None, "removed": False},
             "sweep": calibration_temp.sweep(tempfile.gettempdir(), "software-workflows-git-")}
    directory = None
    lifetime = None
    try:
        directory = Path(tempfile.mkdtemp(prefix="software-workflows-git-")).resolve()
        lifetime = calibration_temp.hold(directory)
        value["evidence"]["directory"] = str(directory)
        value.update(_calibrate_probe(directory))
        if not value["completed"]:
            raise CoordinatorError("GIT_LAZY_FETCH_UNSAFE", "Missing-object helper suppression did not calibrate", value)
    except BaseException as exc:
        value["error"] = getattr(exc, "code", type(exc).__name__)
        raise
    finally:
        if directory is not None:
            value["cleanup"] = calibration_temp.remove(directory, lifetime)
            value["evidence"]["removed"] = value["cleanup"]["directory_removed"]
        # Failed observations are useful status too, but never a cache hit.
        fd, pending = tempfile.mkstemp(prefix="git-pending-", dir=cache.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(value, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, cache)
            from .store import fsync_dir
            fsync_dir(cache.parent)
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    return value


def _calibrate_probe(directory):
    marker, helper, repo = directory / "helper-ran", directory / "helper.sh", directory / "repo"
    helper.write_text("#!/bin/sh\nprintf 'ran\\n' >> " + shlex.quote(str(marker)) + "\nexit 1\n")
    helper.chmod(0o700)
    repo.mkdir(mode=0o700)
    env = git_env()
    env.pop("GIT_NO_LAZY_FETCH", None)
    def invoke(args, safe=False):
        actual = dict(env, GIT_NO_LAZY_FETCH="1") if safe else env
        return subprocess.run(git_argv(str(repo), args), env=actual, capture_output=True, timeout=20)
    for args in (["init", "-q"], ["config", "remote.origin.url", "ext::" + str(helper) + " %S"],
                 ["config", "remote.origin.promisor", "true"], ["config", "remote.origin.partialclonefilter", "blob:none"],
                 ["config", "extensions.partialClone", "origin"], ["config", "protocol.ext.allow", "always"]):
        if invoke(args).returncode:
            raise CoordinatorError("GIT_LAZY_FETCH_PROBE", "Disposable promisor repository setup failed", {"evidence": str(directory)})
    missing = hashlib.sha1(b"C1 deliberately missing object").hexdigest()
    red = invoke(["cat-file", "-e", missing])
    before = marker.read_bytes() if marker.exists() else b""
    green = invoke(["cat-file", "-e", missing], safe=True)
    after = marker.read_bytes() if marker.exists() else b""
    return {"red_exit": red.returncode, "green_exit": green.returncode,
            "red_helper_ran": bool(before), "green_helper_ran": after != before,
            "completed": bool(before) and after == before and red.returncode != 0 and green.returncode != 0}


def text(root, args, **kwargs):
    return git(root, args, **kwargs).stdout.decode("utf-8").strip()


# A batch read that makes no progress for this long fails, as one `cat-file blob` call did (git()).
BLOB_IDLE_S = 120


@contextmanager
def blobs(root):
    """One `cat-file --batch` process for many blob reads; git flushes after each object."""
    process = subprocess.Popen(git_argv(str(root), ["cat-file", "--batch"]), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=git_env(),
                               close_fds=True, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    def take(size=None):
        while (size is None and b"\n" not in pending) or (size is not None and len(pending) < size):
            if not selector.select(BLOB_IDLE_S):
                raise CoordinatorError("GIT_TIMEOUT", "Git blob reads stopped making progress", {"seconds": BLOB_IDLE_S}, True)
            chunk = os.read(process.stdout.fileno(), 1 << 20)
            if not chunk:
                raise CoordinatorError("GIT_FAILED", "Git blob reader ended early", operational=True)
            pending.extend(chunk)
        end = pending.index(b"\n") + 1 if size is None else size
        data = bytes(pending[:end])
        del pending[:end]
        return data
    def read(oid):
        process.stdin.write(oid.encode() + b"\n")
        header = take().split()
        if len(header) != 3 or header[1] != b"blob":
            raise CoordinatorError("GIT_FAILED", "Git did not return the blob", {"oid": oid}, True)
        data = take(int(header[2]) + 1)
        if data[-1:] != b"\n":
            raise CoordinatorError("GIT_FAILED", "Git blob read was truncated", {"oid": oid}, True)
        return data[:-1]
    finished = False
    try:
        yield read
        finished = True
    finally:
        selector.close()
        if not finished:
            process.kill()  # a stalled or misreading child never outlives the failure
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def entries(root, tree=None, index=None):
    args = ["ls-tree", "-rz", "--full-tree", tree] if tree else ["ls-files", "--stage", "-z"]
    result = {}
    for entry in git(root, args, index=index).stdout.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        path = name.decode("utf-8", "strict")
        fields = meta.decode().split()
        mode, oid, stage = (fields[0], fields[2], "0") if tree else fields
        result.setdefault(path, []).append({"mode": mode, "oid": oid, "stage": stage})
    return result


def entries_at(root, path):
    """The branch commit's and the index's entries at exactly `path` (as `entries` gives them; [] where absent), read
    with literal pathspecs."""
    literal = {"GIT_LITERAL_PATHSPECS": "1"}
    found = []
    for args in (["ls-tree", "-z", "--full-tree", "HEAD", "--", path], ["ls-files", "--stage", "-z", "--", path]):
        listed = []
        for entry in git(root, args, extra_env=literal).stdout.split(b"\0"):
            if not entry:
                continue
            meta, name = entry.split(b"\t", 1)
            if name.decode("utf-8", "strict") != path:
                continue  # an index entry inside a folder of that name
            fields = meta.decode().split()
            mode, oid, stage = (fields[0], fields[2], "0") if args[0] == "ls-tree" else fields
            listed.append({"mode": mode, "oid": oid, "stage": stage})
        found.append(listed)
    return found[0], found[1]


def tree_entry(root, commit, path):
    """An exact literal path in an immutable tree, including directory entries."""
    result = git(root, ["ls-tree", "-z", "--full-tree", commit, "--", path],
                 extra_env={"GIT_LITERAL_PATHSPECS": "1"})
    for entry in result.stdout.split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            if name.decode("utf-8", "strict") == path:
                mode, kind, oid = meta.decode("ascii").split()
                return {"path": path, "mode": mode, "type": kind, "oid": oid}
    return None


def blob_oid(data, algorithm="sha1"):
    return hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def put_entries(root, index, changes):
    lines = []
    for path, entry in changes.items():
        name = path.encode("utf-8")
        # Remove old stages first, then insert a stage-zero entry if present.
        lines.append(b"0 " + b"0" * len(entry.get("oid", "0" * 40)) + b"\t" + name + b"\0")
        if entry.get("mode"):
            lines.append((entry["mode"] + " " + entry["oid"] + "\t").encode() + name + b"\0")
    git(root, ["update-index", "-z", "--index-info"], data=b"".join(lines), index=index)


def reference_update(root, branch, new, old, identity_guard=None):
    """Prepare native locks, verify the symbolic branch under lock, then commit.

    Git 2.56 rejects symref-verify HEAD plus an explicit update of its referent
    as duplicate HEAD updates. Updating through HEAD locks both HEAD and the
    resolved branch. The independent read below is inside those native locks;
    a wrong branch aborts before any reference changes.
    """
    if identity_guard:
        identity_guard()
    process = subprocess.Popen(git_argv(str(root), ["update-ref", "--stdin"],
                               config=("-c", "core.fsmonitor=false")),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=git_env(), close_fds=True, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    def reply(expected):
        if not selector.select(30) or process.stdout.readline() != expected:
            raise CoordinatorError("REF_TRANSACTION", "Native reference transaction did not reach its required phase", operational=True)
    try:
        process.stdin.write(("start\nupdate HEAD " + new + " " + old + "\nprepare\n").encode())
        process.stdin.flush()
        reply(b"start: ok\n")
        reply(b"prepare: ok\n")
        if text(root, ["symbolic-ref", "HEAD"]) != branch or text(root, ["rev-parse", branch]) != old:
            raise CoordinatorError("BRANCH_CHANGED", "Native locked HEAD does not resolve to the reviewed branch and base")
        if identity_guard:
            identity_guard()  # Prepared native locks are held; EOF aborts on refusal.
        process.stdin.write(b"commit\n")
        process.stdin.flush()
        reply(b"commit: ok\n")
        process.stdin.close()
        if process.wait(timeout=30):
            raise CoordinatorError("REF_TRANSACTION", "Native reference transaction failed", operational=True)
    finally:
        selector.close()
        if process.poll() is None:
            try:
                process.stdin.close()  # EOF aborts a prepared, uncommitted native transaction.
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


@contextmanager
def reference_verification(root, branch, tip):
    """Hold native HEAD/referent locks without updating either reference."""
    process = subprocess.Popen(git_argv(str(root), ["update-ref", "--stdin"]),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=git_env(), close_fds=True, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    def reply(expected):
        if not selector.select(30) or process.stdout.readline() != expected:
            raise CoordinatorError("REF_VERIFICATION", "Cannot hold a prepared reference verification", operational=True)
    try:
        process.stdin.write(("start\nverify HEAD " + tip + "\nprepare\n").encode())
        process.stdin.flush()
        reply(b"start: ok\n")
        reply(b"prepare: ok\n")
        if text(root, ["symbolic-ref", "HEAD"]) != branch or text(root, ["rev-parse", branch]) != tip:
            raise CoordinatorError("BRANCH_CHANGED", "Locked symbolic HEAD or branch tip differs from the publication")
        yield
    finally:
        selector.close()
        if process.poll() is None:
            try:
                process.stdin.close()  # EOF aborts; verification never commits.
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


# This is the actual accepted shell contract, not a rewrite of an audited command.
SHELL_GIT_ENV = ("PATH=/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_SYSTEM=/dev/null",
                 "GIT_CONFIG_GLOBAL=/dev/null", "GIT_ATTR_NOSYSTEM=1", "GIT_NO_LAZY_FETCH=1")


def shell_git_binary():
    return str(Path(shutil.which("git") or "/usr/bin/git").resolve())


def shell_env_binary():
    # Do not let a PATH wrapper or shell function replace environment isolation.
    return str(Path("/usr/bin/env").resolve())


def safe_shell_git(args):
    import shlex
    return shlex.join([shell_env_binary(), "-i", *SHELL_GIT_ENV, shell_git_binary(), "--no-pager", "--no-optional-locks",
        "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "submodule.recurse=false", *args])
