#!/usr/bin/env python3
"""Workspace digest + filter-free worktree status for the Codex review gate —
ONE implementation (stdlib only).

server.py stamps the digest into every answer header at dispatch
(`tree:<digest>`) and hooks/push_gate.py recomputes it at push time; a
mismatch means the tree changed after the answer. Both load this file by
path (hooks cannot import the server module, and a package-relative import
is not available to a file loaded by path), so the two can never drift
(review rounds 29-31 chased twin parity by hand; a single module ends that).

The digest is sha256 over the CONTENT of the worktree: every tracked and
untracked entry's git identity (exec bit; a symlink IS its target path; a
submodule IS its checked-out HEAD) and BYTES, read by this module itself.
No `git diff`, no `git status` (round 34): both consult repository
configuration that can run configured helper commands (diff drivers,
textconv, and — even with drivers and textconv disabled — the clean filter
of a `filter=` attribute, reproduced on Git 2.55.0: `git status` runs it for
any file whose stat data changed); the only git calls left are metadata
listings (`rev-parse`, `ls-files`, `ls-tree`, `check-attr`, `config`,
`rev-list --count`) that execute nothing. The digest is HEAD-INDEPENDENT:
a review vouches for content, so committing the reviewed content leaves the
review VERIFIED through `git push`. 12 hex chars; "nogit" outside a
repository; "unknown" whenever a budget is exceeded or anything fails — an
unknown digest never matches anything.

WORKTREE STATUS (round 36). The same walk also yields a porcelain-shaped
status — `XY path` lines, X = index vs HEAD, Y = worktree vs index — from
the index listing, HEAD's tree listing and this module's own byte reads
(git's own blob ids, `sha1("blob <n>\\0" + bytes)` or sha256 in a sha256
repository), so server.py's write-mode snapshot and the hook's index/HEAD
consistency check run no `git status` either. Vocabulary: `A `/`M `/`D `/
`T ` staged, ` M`/` D`/` T` unstaged, `??` untracked (every file, never a
collapsed directory), `UU` unmerged, and ` ~` = the worktree bytes differ
from the index blob but a conversion attribute (filter / text / eol / ident /
working-tree-encoding, or core.autocrlf) applies to the path, so the byte
comparison is not authoritative without running that helper — reported,
never hidden, and treated as dirty. Submodules: ` M` when the checked-out
HEAD is not the gitlink or the submodule's own status is dirty (recursive,
same rules, shared budget); an uninitialised submodule is clean, as in git.

STRICT VERIFIER (round 37). The display status follows git's own view
(core.filemode, core.symlinks, skip-worktree); the strong REVIEW VERIFIED
wording must not — it asks whether the OBJECTS a commit or push records are
exactly the reviewed worktree bytes. So `inspect` also returns `strict`:
{path: reason} for every entry the byte comparison cannot vouch for — a
skip-worktree / assume-unchanged entry (its blob was never read), an index
mode the filesystem cannot represent (core.filemode=false with 100755), an
index symlink materialised as a file (core.symlinks=false), a conversion-
attributed path whose bytes differ, an HEAD/index mode-only difference the
display suppresses — and every ordinary difference. `strict_commit` is the
subset that concerns the INDEX (what a commit records); `strict` (all) is
what a push must be free of. A `binding` (HEAD + the raw index listing +
the toplevel) is returned for the hook's one-shot token so a token minted
for one index state cannot be consumed after the index changed.

HARD DEADLINE (round 31). A PreToolUse hook that outlives its timeout fails
OPEN, and no in-process deadline can interrupt a blocking read (a hung git,
a stalled network mount, a pipe that never closes). So callers use
`digest_hard()`: the computation runs in a CHILD in its own process group
and the parent waits at most deadline + grace, then SIGKILLs the whole
group. Inside the child every git output is streamed under select() with a
byte cap and the deadline is re-checked after every blocking step, so the
common case ends early and cleanly; the parent kill is the backstop.

GROUP IDENTITY (round 36-37). A group id is the LEADER's pid, and a reaped
pid can already belong to a stranger — so `run_contained()` never reaps
before it sweeps: it reads the child's stdout to EOF, to the deadline, or
to the LEADER's EXIT (observed with waitid WNOWAIT, so a helper holding the
pipe cannot hold the call — round 37), sweeps the group by the still-
reserved id, drains what the pipe still holds, and only then reaps.
`_kill_group()` refuses to signal a group whose leader Popen has already
reaped, and a killpg that EPERM answers is verified against a process
listing — a live member we cannot signal is a containment FAILURE the
caller sees (never read as "gone"). Payloads travel to a child over a pipe
fed by a writer thread — no temporary file, so a machine without a writable
temporary directory cannot make a caller crash (round 37).

BUDGETS. Named threat: the host's hook timeout (fail-open). Sized from the
trees this gate serves, MEASURED 2026-09-04 (tracked + untracked content):
agent-teams-plugin 1.0 MB / 38 files, Smartpay_Backend 24.9 MB / 1,108
files, SmartPay_Frontend 11.8 MB / 1,294 files; largest file 1.3 MB; hashing
everything ≤ 0.25 s — the budgets below carry ≥ 40× headroom. A tree beyond
them reads "unknown", which only makes the gate MORE conservative (the gate
says why) and never opens anything. Pinned at-cap/over-cap by
tests/test_push_gate.py.

CLI:  python3 treedigest.py [--deadline S] [--status] -- <cwd>
      prints the digest, or (--status) `<digest> <head>` then the status lines
"""
import hashlib
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time

DEADLINE_S = 20.0          # the whole computation (inside the child)
GIT_TIMEOUT_S = 10.0       # any single git call
GRACE_S = 3.0              # parent waits deadline + grace before SIGKILL
MAX_FILE_BYTES = 64 * 1024 * 1024        # ≈ 50× the largest measured file
MAX_TOTAL_BYTES = 1024 * 1024 * 1024     # ≈ 40× the largest measured tree
MAX_FILES = 100000                       # ≈ 75× the most files measured
MAX_GIT_BYTES = 64 * 1024 * 1024         # any single listing (≈ 100 B per entry)
MAX_SUBMODULE_DEPTH = 8                  # nested submodule recursion (shares the deadline)
PS_BIN = "/bin/ps"                       # deployment-verified absolute path (macOS and Linux)

HEX12 = re.compile(r"[0-9a-f]{12}")
OID_RE = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")

TRACKED_ARGS = ("ls-files", "-s", "-t", "-z")              # tag mode oid stage\tpath\0
OTHERS_ARGS = ("ls-files", "--others", "--exclude-standard", "-z")
HEAD_TREE_ARGS = ("ls-tree", "-r", "-z", "--full-tree", "HEAD")   # mode type oid\tpath\0
CONVERSION_ATTRS = ("filter", "text", "eol", "crlf", "ident", "working-tree-encoding")
GITLINK_MODE = b"160000"
SYMLINK_MODE = b"120000"
_UNSET = (b"unspecified", b"unset")
_FALSE = ("false", "off", "no", "0", "")


class Budget(Exception):
    """A budget or deadline was exceeded, or a listing could not be trusted —
    the digest is "unknown" and the status is unreadable."""


class StatusUnreadable(Budget):
    """A STATUS-ONLY failure (HEAD/tree/attribute/config listing, a submodule
    whose status cannot be established, a file that changed under the read):
    the content digest is still computed — by a second, status-free walk —
    and only `lines` is None, so a caller can say "content matches, index/
    HEAD consistency unknown" instead of "digest unknown"."""


# REPOSITORY-CONTROLLED EXECUTION (round 33): git honours configuration from
# the repository being READ — core.fsmonitor runs a helper on status/diff/
# ls-files, core.hooksPath aims hooks anywhere — so digesting an attacker-
# supplied repository could execute code outside every sandbox (the class
# fixed upstream in Codex, CVE-2026-19592; MEASURED here: a configured
# fsmonitor helper ran on `git status`). Every git call in this file and in
# server.py carries these command-line overrides, which outrank any config
# file; GIT_OPTIONAL_LOCKS=0 keeps `status` from writing the index and
# GIT_TERMINAL_PROMPT=0 keeps any credential path from waiting on a tty.
GIT_SAFE_CONFIG = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull)
# REPLACE REFS (round 37): `ls-tree HEAD` and `rev-list` honour refs/replace/*
# while push transfers the ORIGINAL objects (measured: a replaced HEAD read
# as 1 commit of history, 3 without replacement) — every read here sees
# what a push would send.
GIT_SAFE_GLOBAL = ("--no-replace-objects",)
# GIT_NO_LAZY_FETCH=1 (round 34): in a partial clone a missing object makes
# git run `fetch` — transport, proxy and credential helpers included.
GIT_SAFE_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
# ENVIRONMENT SCRUB (round 36): variables that re-aim git at ANOTHER
# repository, index or object store would make every `-C <path>` read
# answer for a different repository (a submodule's `rev-parse` under a
# caller's GIT_DIR names the caller's HEAD); the GIT_CONFIG_* injectors are
# dropped as defence in depth — MEASURED on 2.55.0: `-c` outranks both
# GIT_CONFIG_PARAMETERS and GIT_CONFIG_{COUNT,KEY_n,VALUE_n}, but a future
# precedence change must not be the thing that re-enables a helper. The
# hook ALSO reports these when they are present in its own environment: the
# shell that runs the command inherits them, so inspection (scrubbed) and
# execution (not) would otherwise disagree about which repository (round 37).
GIT_SCRUB_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                 "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
                 "GIT_CONFIG_SYSTEM", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
                 # GIT_CONFIG (round 37): honoured by `git config` ALONE — measured on
                 # 2.55.0: `GIT_CONFIG=/dev/null git config --list` is empty while
                 # `git remote get-url origin` still answers from the repository —
                 # so an ambient value would blind every config READ here while the
                 # command itself keeps the repository's configuration.
                 "GIT_CONFIG")
GIT_SCRUB_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
# CAPABILITY PROBE (round 38-39, Runtime Capability Law): whether the git
# on PATH honours GIT_NO_LAZY_FETCH in the configuration these reads run in
# is asked of the BINARY by doing the dangerous thing in a throwaway
# repository: a partial clone whose promisor remote is an `ext::` helper
# that records a marker. Reading a missing object runs that helper unless
# lazy fetching is off (MEASURED on 2.55.0: plain read → helper ran; with
# GIT_NO_LAZY_FETCH=1 → it did not). The probe is CALIBRATED every time —
# the known-red half must go red or the probe reports failure — and a git
# that runs the helper under our environment refuses every read. Option
# parsing (`--no-lazy-fetch --version`) was the round-38 probe; 2.44.1
# honours the variable without the option, 2.45.0 accepts the option while
# missing the CVE-2024-32465 fix, so the option is neither passed nor tested.
_git_version_cache = None
_lazy_fetch_cache = None
_lazy_fetch_cache_ts = 0.0
# SECURITY-VERSION POLICY (separate from capability, round 38): the
# CVE-2024-32465 batch — 2.45.1 and the maintenance releases 2.39.4 / 2.40.2
# / 2.41.1 / 2.42.2 / 2.43.4 / 2.44.1 (GHSA-vm9j-46j9-qvq4). Below it every
# read is refused as policy, whatever the probe says.
GIT_FIXED_PATCH = {39: 4, 40: 2, 41: 1, 42: 2, 43: 4, 44: 1}
GIT_MIN_VERSION = (2, 39, 4)


def _git_version():
    """(major, minor, patch) of the git on PATH; (0, 0, 0) when unreadable."""
    global _git_version_cache
    if _git_version_cache is None:
        try:
            out = subprocess.run(["git", "--version"], capture_output=True, text=True,
                                 timeout=10, env=git_env()).stdout
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
            _git_version_cache = ((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
                                  if m else (int(0), int(0), int(0)))
        except Exception:
            _git_version_cache = (int(0), int(0), int(0))
    return _git_version_cache


def git_version_policy_ok(v):
    """The CVE-2024-32465 fixed-version policy (documentation of a decision,
    not a capability test — the capability is probed separately)."""
    major, minor, patch = (tuple(v) + (0, 0, 0))[:3]
    if major != 2:
        return major > 2
    if minor >= 45:
        return (minor, patch) >= (45, 1)
    need = GIT_FIXED_PATCH.get(minor)
    return need is not None and patch >= need


def _probe_git(where, args, env, timeout=10):
    return subprocess.run(["git", *GIT_SAFE_GLOBAL, *GIT_SAFE_CONFIG, "-C", where, *args],
                          capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)


def lazy_fetch_probe(budget_s=10.0):
    """(capable, why): build a throwaway partial clone whose promisor remote is
    an `ext::` helper that touches a marker, then read a missing object twice:
    WITHOUT the safe environment the helper must run (calibration — a probe
    that cannot go red is not a probe), WITH it the helper must not. Any
    failure to set up, calibrate, or clean up reads as not capable."""
    import shutil
    import tempfile
    try:
        d = tempfile.mkdtemp(prefix="codex-oracle-lazyprobe-")
    except Exception as exc:
        return False, f"no temporary directory: {exc}"
    end = time.monotonic() + max(0.5, float(budget_s))

    def left():
        return max(0.2, end - time.monotonic())

    try:
        marker = os.path.join(d, "helper-ran")
        helper = os.path.join(d, "helper.sh")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n" f"touch '{marker}'\n" "exit 1\n")
        os.chmod(helper, 0o700)
        repo = os.path.join(d, "repo")
        os.mkdir(repo)
        base = {k: v for k, v in os.environ.items()
                if k not in GIT_SCRUB_ENV and not k.startswith(GIT_SCRUB_PREFIXES)}
        base.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"})
        base.pop("GIT_NO_LAZY_FETCH", None)
        steps = (("init", "-q"), ("config", "remote.origin.url", f"ext::{helper} %S"),
                 ("config", "remote.origin.promisor", "true"),
                 ("config", "remote.origin.partialclonefilter", "blob:none"),
                 ("config", "extensions.partialClone", "origin"),
                 ("config", "protocol.ext.allow", "always"))
        for args in steps:
            if _probe_git(repo, args, base, timeout=left()).returncode != 0:
                return False, f"probe setup failed at git {args[0]}"
        missing = hashlib.sha1(b"blob 31\0codex-oracle lazy-fetch probe\n").hexdigest()
        _probe_git(repo, ("cat-file", "-e", missing), base, timeout=left())  # known-red: no safe env
        if not os.path.exists(marker):
            return False, "probe calibration failed: the promisor helper did not run without the safe environment"
        os.unlink(marker)
        _probe_git(repo, ("cat-file", "-e", missing), {**base, "GIT_NO_LAZY_FETCH": "1"}, timeout=left())
        if os.path.exists(marker):
            return False, "this git ran the promisor helper despite GIT_NO_LAZY_FETCH=1"
        return True, ""
    except Exception as exc:
        return False, f"probe error: {type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


PROBE_CACHE_TTL_S = 24 * 3600  # a binary's capability does not change; the key pins the binary anyway


def _probe_cache_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "logs", "codex-oracle", "lazy-fetch-probe.json")


def _probe_cache_key():
    """Backend + version + mode: the resolved git binary, its size/mtime and
    `git --version` — a swapped binary is a different key."""
    import shutil
    exe = shutil.which("git") or "git"
    try:
        real = os.path.realpath(exe)
        st = os.stat(real)
        ident = f"{real}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        ident = exe
    return hashlib.sha256(f"{ident}|{_git_version()}|env:GIT_NO_LAZY_FETCH=1".encode()).hexdigest()[:24]


NEGATIVE_PROBE_RETRY_S = 60.0  # a failed/timed-out probe is retried, never pinned for the process


def lazy_fetch_capable(budget_s=10.0):
    """(capable, why) — cached per process and, for a capable verdict, on
    disk for PROBE_CACHE_TTL_S keyed by the binary (path, size, mtime,
    version, mode): the probe runs the dangerous thing in a throwaway
    repository and costs ~0.7 s, and the hook is a new process on every
    command. A negative verdict is never cached on disk and is re-probed
    after NEGATIVE_PROBE_RETRY_S in-process (a long-lived server must not
    pin a transient failure). `budget_s` bounds the probe (the caller's
    remaining deadline). CODEX_ORACLE_LAZY_PROBE_CACHE_S=0 disables the
    disk cache."""
    global _lazy_fetch_cache, _lazy_fetch_cache_ts
    if _lazy_fetch_cache is not None:
        if _lazy_fetch_cache[0] or time.monotonic() - _lazy_fetch_cache_ts < NEGATIVE_PROBE_RETRY_S:
            return _lazy_fetch_cache
    try:
        ttl = float(os.environ.get("CODEX_ORACLE_LAZY_PROBE_CACHE_S", PROBE_CACHE_TTL_S))
    except ValueError:
        ttl = float(PROBE_CACHE_TTL_S)
    key = _probe_cache_key()
    path = _probe_cache_path()
    if ttl > 0:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
            if (rec.get("key") == key and rec.get("capable") is True
                    and 0 <= time.time() - float(rec.get("ts") or 0) <= ttl):
                _lazy_fetch_cache = (True, "")
                return _lazy_fetch_cache
        except Exception:
            pass
    _lazy_fetch_cache = lazy_fetch_probe(budget_s)
    _lazy_fetch_cache_ts = time.monotonic()
    if ttl > 0 and _lazy_fetch_cache[0]:
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"key": key, "capable": True, "ts": time.time(),
                           "backend": _probe_cache_key.__doc__ and "git", "version": list(_git_version())}, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            pass
    return _lazy_fetch_cache


def git_argv(where, args, config=GIT_SAFE_CONFIG):
    """The one argv shape for a metadata read of the repository at `where`.
    `config` is the safe overlay; `hook list` passes one WITHOUT the
    hooksPath override (it must see the hooks the command would run; it
    executes nothing — measured)."""
    return ["git", *GIT_SAFE_GLOBAL, *config, "-C", where, *args]


def git_env():
    env = {k: v for k, v in os.environ.items()
           if k not in GIT_SCRUB_ENV and not k.startswith(GIT_SCRUB_PREFIXES)}
    env.update(GIT_SAFE_ENV)
    return env


def routing_env_names(environ=None):
    """Names of repository-routing / config-injecting variables PRESENT in
    an environment (the hook reports them: the executing shell inherits
    them while inspection scrubs them)."""
    environ = os.environ if environ is None else environ
    return sorted(k for k in environ if k in GIT_SCRUB_ENV or k.startswith(GIT_SCRUB_PREFIXES))


def _start_writer(proc, payload):
    """Feed `payload` to the child's stdin from a thread and close it — no
    temporary file (round 37: a machine without a writable temporary
    directory crashed the hook before the deny could be printed) and no
    pipe the caller could block on. A dead child ends the writer (EPIPE)."""
    pipe = proc.stdin

    def run():
        try:
            pipe.write(payload)
            pipe.flush()
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t = threading.Thread(target=run, name="codex-oracle-stdin", daemon=True)
    t.start()
    return t


def _leader_exited(proc):
    """Has the child EXITED, without reaping it? waitid(WNOWAIT) where the
    platform has it (macOS and Linux, measured); None when it cannot be
    known without reaping (the caller then relies on EOF and the deadline)."""
    waitid = getattr(os, "waitid", None)
    if waitid is None or not hasattr(os, "WNOWAIT") or not hasattr(os, "P_PID"):
        return None
    try:
        return waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except OSError:
        return None


def group_live_members(pgid, ps_bin=PS_BIN):
    """Pids of LIVE (non-zombie) members of process group `pgid`, from a
    process listing (`ps -A -o pid=,pgid=,stat=` — BSD and procps agree on
    these options); None when the listing itself failed (unknown)."""
    if os.name == "nt":
        return None
    try:
        done = subprocess.run([ps_bin, "-A", "-o", "pid=,pgid=,stat="],
                              capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    live = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, pg = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pg == pgid and not parts[2].startswith("Z"):
            live.append(pid)
    return live


def _kill_group(proc):
    """SIGKILL the process GROUP led by `proc` — by the recorded group id even
    when the leader already EXITED (round 32: an exited leader left its group
    alive) — then reap the leader. The leader must be UNREAPED (round 36):
    `proc.returncode` is Popen's own reap record, and once it is set the pid,
    hence the group id, may already belong to a stranger, so the group is
    NOT signalled (only the dead leader is reaped). Returns True only when
    the group was signalled or PROVEN empty: ESRCH proves it; EPERM (a
    zombie-led group answers EPERM on macOS — measured — but so would a live
    member we may not signal) is checked against a process listing, and a
    live member left behind makes this False (round 37: containment failure
    is the caller's to report, never "gone"). Windows has no groups: the
    leader alone is killed (documented, UNMEASURED)."""
    swept = False
    if os.name != "nt" and proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            swept = True
        except ProcessLookupError:
            swept = True  # ESRCH: nothing left in a group that is still ours
        except PermissionError:
            # EPERM = no member could be signalled: a zombie-led group (fine)
            # or a live member we may not signal — INCLUDING the leader
            # (round 37: excluding it read an unsignalable live leader as
            # swept). Only an empty live listing proves the group gone.
            live = group_live_members(proc.pid)
            swept = live == []
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    if proc.returncode is None:
        swept = False  # the leader itself could not be terminated or reaped
    return swept


def _kill_pid(proc):
    """Kill ONE process (a git child that shares OUR group — no nested
    session, so the outer group kill still covers it) and reap it."""
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _read_bounded_thread(proc, end, cap):
    """Windows reader (no select on pipes): a thread reads chunks, the caller
    waits with timeouts; on the deadline or the cap the child is killed so
    the blocked read ends. Returns (chunks, why) with why "" | "timeout" |
    "cap". UNMEASURED on Windows; exercised on POSIX by the tests."""
    chunks, why = [], ""
    state = {"done": False, "cap": False, "total": 0}
    pipe = proc.stdout

    def run():
        try:
            while True:
                chunk = pipe.read1(1 << 16) if hasattr(pipe, "read1") else pipe.read(1 << 16)
                if not chunk:
                    break
                state["total"] += len(chunk)
                if state["total"] > cap:
                    state["cap"] = True
                    break
                chunks.append(chunk)
        except Exception:
            pass
        finally:
            state["done"] = True

    t = threading.Thread(target=run, name="codex-oracle-stdout", daemon=True)
    t.start()
    while not state["done"]:
        remaining = end - time.monotonic()
        if remaining <= 0:
            why = "timeout"
            break
        t.join(min(0.25, remaining))
    if state["cap"]:
        why = "cap"
    if why:
        try:
            proc.kill()
        except Exception:
            pass
        t.join(5)
    return chunks, why


def run_contained(argv, timeout_s, cap=1 << 20, payload=None, grace_s=GRACE_S):
    """Run `argv` in its OWN session and return (returncode, stdout bytes,
    why) with why = "" | "timeout" | "cap" | "spawn: …" | "read: …" |
    "sweep: …". stdout is streamed under select() until EOF, the deadline, or
    the LEADER's EXIT (waitid WNOWAIT — a helper that inherited the pipe
    cannot hold the call open, round 37); `payload` (bytes) is fed to stdin
    by a writer thread. The group is swept on EVERY completion by a group id
    that is still reserved (the leader is reaped LAST), the pipe is drained
    after the sweep, and a member the sweep could not remove is reported as
    "sweep: …" — never silently. Windows: no sessions or groups — the
    leader alone is bounded by a reader thread (UNMEASURED)."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=(os.name != "nt"))
    except Exception as exc:
        return -1, b"", f"spawn: {exc}"
    writer = _start_writer(proc, payload) if payload is not None else None
    pipe = proc.stdout
    assert pipe is not None
    end = time.monotonic() + timeout_s
    if os.name == "nt":
        chunks, why = _read_bounded_thread(proc, end, cap)
        _kill_group(proc)
        return (proc.returncode if proc.returncode is not None else -1), b"".join(chunks), why
    chunks, total, why = [], 0, ""
    leader_done = False
    fd = pipe.fileno()
    try:
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                why = "timeout"
                break
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if not ready:
                if _leader_exited(proc):
                    leader_done = True  # a helper may still hold the pipe: sweep, then drain
                    break
                continue
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                why = "cap"
                break
            chunks.append(chunk)
    except Exception as exc:
        why = why or f"read: {exc}"
    finally:
        if not why and not leader_done:
            _settle(proc, grace_s)
        swept = _kill_group(proc)  # the leader is still OURS here: unreaped until this reaps it
        if not swept:
            # round 38: a breach already recorded must not HIDE a failed
            # containment — both are reported
            why = (why + "; " if why else "") + "sweep: a live member of the child's process group could not be signalled"
        if leader_done and not why:
            # the helpers holding the pipe are gone: take what they left
            try:
                os.set_blocking(fd, False)
                while total <= cap:
                    try:
                        chunk = os.read(fd, min(1 << 16, cap + 1 - total))  # never past the cap + 1 sentinel
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > cap:
                        why = "cap"  # the sentinel byte arrived: a loud cap violation, not a silent overrun
                        break
                    chunks.append(chunk)
            except Exception:
                pass
        try:
            pipe.close()
        except Exception:
            pass
        if writer is not None:
            writer.join(1.0)
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, b"".join(chunks), why


def _settle(proc, grace_s):
    """Wait up to `grace_s` for the leader to EXIT without reaping it —
    `waitid(WNOWAIT)` where the platform has it (macOS and Linux, measured)
    — so a leader that closed its stdout and is finishing is not killed
    mid-exit (its real exit status still decides) while its pid, hence the
    group id, stays reserved. Without waitid a short fixed pause is all that
    is possible; a leader still exiting then reads as killed (-9), which only
    makes the caller MORE conservative."""
    end = time.monotonic() + max(0.0, grace_s)
    while True:
        exited = _leader_exited(proc)
        if exited is None:
            time.sleep(min(0.2, max(0.0, grace_s)))
            return
        if exited or time.monotonic() >= end:
            return
        time.sleep(0.02)


def _git_output(where, args, deadline, git_timeout, cap, payload=None, config=GIT_SAFE_CONFIG):
    """Run git with stdout STREAMED under select(): every read is bounded by
    the remaining per-call / overall time, the output is capped at `cap`
    bytes, and the process is killed on any breach. `payload` bytes go in
    on stdin from a writer thread (check-attr --stdin). Returns
    (returncode, bytes)."""
    left = min(git_timeout, deadline - time.monotonic())
    if left <= 0:
        raise Budget("deadline before git")
    if not git_version_policy_ok(_git_version()):
        raise Budget("git below the CVE-2024-32465 fixed-version policy (2.39.4 / 2.40.2 / 2.41.1 / "
                     "2.42.2 / 2.43.4 / 2.44.1 / 2.45.1) — reads refused")
    capable, why = lazy_fetch_capable(budget_s=left)
    if not capable:
        raise Budget(f"lazy-fetch containment not proven on this git ({why}) — reads refused")
    # git stays in OUR process group (round 32: a nested session escaped the
    # outer group kill); a breach kills the git pid itself.
    proc = subprocess.Popen(
        git_argv(where, args, config), stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        env=git_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    writer = _start_writer(proc, payload) if payload is not None else None
    end = time.monotonic() + left
    chunks, total = [], 0
    pipe = proc.stdout
    assert pipe is not None
    try:
        if os.name == "nt":
            chunks, why = _read_bounded_thread(proc, end, cap)
            if why:
                raise Budget("git " + why)
        else:
            fd = pipe.fileno()
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise Budget("git timeout")
                ready, _, _ = select.select([fd], [], [], remaining)
                if not ready:
                    raise Budget("git timeout")
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise Budget("git output cap")
                chunks.append(chunk)
        rc = proc.wait(timeout=max(0.0, end - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        raise Budget("git exit timeout") from exc
    finally:
        _kill_pid(proc)
        try:
            pipe.close()
        except Exception:
            pass
        if writer is not None:
            writer.join(1.0)
    return rc, b"".join(chunks)


def _gitlink_head(top_b, rel, deadline, git_timeout):
    """A submodule's checked-out HEAD, by asking git INSIDE the submodule
    under the same safe configuration (`rev-parse` executes nothing). Round
    36: the former file reader could not follow commondir/worktrees, chained
    symrefs, reftable or a truncated packed-refs, and answered a STABLE "?"
    for all of them — a value that hid every difference. Now: b"absent" for
    a submodule that is not checked out (no <sub>/.git at all: git treats
    it as clean); the OID bytes otherwise; Budget (the whole digest is
    "unknown") when HEAD cannot be resolved or git answered for a different
    repository (its toplevel is checked against the submodule path)."""
    sub = os.path.join(top_b, rel)
    if not os.path.lexists(os.path.join(sub, b".git")):
        return b"absent"
    rc, out = _git_output(sub, ("rev-parse", "--show-toplevel", "HEAD"), deadline, git_timeout, 1 << 16)
    if rc != 0:
        raise Budget("submodule HEAD unresolvable")
    lines = out.split(b"\n")
    if len(lines) < 2:
        raise Budget("submodule rev-parse shape")
    top_line, head = lines[0].strip(), lines[1].strip()
    if os.path.realpath(top_line) != os.path.realpath(sub):
        raise Budget("git answered for another repository")
    if not OID_RE.fullmatch(head):
        raise Budget("submodule HEAD is not an object id")
    return head


def _blob_oid(algo, size, chunks):
    h = algo()
    h.update(b"blob %d\0" % size)
    for c in chunks:
        h.update(c)
    return h.hexdigest().encode("ascii")


def _converts(attrs, autocrlf):
    """Does git apply ANY content conversion between the index and the
    worktree for a path with these attributes? (The byte comparison is then
    not authoritative.)"""
    a = attrs or {}
    if a.get(b"filter", b"unspecified") not in _UNSET:
        return True
    if a.get(b"ident") == b"set":
        return True
    if a.get(b"working-tree-encoding", b"unspecified") not in _UNSET:
        return True
    text = a.get(b"text", b"unspecified")
    if text == b"unset":
        return False  # -text / binary: no end-of-line conversion at all
    if text != b"unspecified":
        return True   # set / auto
    if a.get(b"eol", b"unspecified") not in _UNSET:
        return True
    if a.get(b"crlf", b"unspecified") not in _UNSET:
        return True
    return autocrlf


def _type_class(mode):
    if mode == GITLINK_MODE:
        return b"g"
    if mode == SYMLINK_MODE:
        return b"l"
    return b"f"


def _cfg_key(key):
    parts = key.split(b".")
    if len(parts) >= 3:
        return b".".join([parts[0].lower(), *parts[1:-1], parts[-1].lower()])
    return key.lower()


def _display(rel):
    return rel.decode("utf-8", "backslashreplace")


def inspect(cwd, *, want_status=False, deadline_s=DEADLINE_S, git_timeout_s=GIT_TIMEOUT_S,
            max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_TOTAL_BYTES,
            max_files=MAX_FILES, max_git_bytes=MAX_GIT_BYTES, _depth=0):
    """ONE walk of the worktree → {"digest", "repo", "lines", "strict",
    "strict_commit", "head", "binding", "reason"}. digest: 12 hex | "nogit" |
    "unknown". repo: inside a work tree. lines: the display status set
    (want_status) or None when it could not be established. strict /
    strict_commit: {path: reason} the strong wording must be free of (push /
    commit). head: HEAD's object id ("" on an unborn branch). binding: 16
    hex over toplevel + HEAD + the raw index listing. Computed IN THIS
    PROCESS with soft deadlines (every blocking step is followed by a
    check); use digest_hard() where nothing above this process holds a kill."""
    deadline = time.monotonic() + deadline_s
    result = {"digest": "unknown", "repo": False, "lines": None, "strict": None,
              "strict_commit": None, "head": "", "binding": "", "reason": ""}

    def check():
        if time.monotonic() > deadline:
            raise Budget("deadline")

    def emit(x, y, rel):
        if x != " " or y != " ":
            lines.add(x + y + " " + _display(rel))

    try:
        rc, out = _git_output(cwd, ("rev-parse", "--show-toplevel"), deadline, git_timeout_s, 1 << 16)
        if rc != 0:
            result["digest"] = "nogit"
            result["reason"] = "not inside a git work tree"
            return result
        result["repo"] = True
        # TOPLEVEL-ANCHORED: a nested cwd would enumerate only its own subtree.
        top = out.decode("utf-8", "surrogateescape").strip()
        top_b = top.encode("utf-8", "surrogateescape")
        rc, tracked = _git_output(top, TRACKED_ARGS, deadline, git_timeout_s, max_git_bytes)
        if rc != 0:
            raise Budget("index listing failed")
        rc, others = _git_output(top, OTHERS_ARGS, deadline, git_timeout_s, max_git_bytes)
        if rc != 0:
            raise Budget("untracked listing failed")
        entries = {}   # path → (kind, extra) ; gitlinks carry the index oid — the DIGEST's view
        index = {}     # path → (tag, mode, oid) — the STATUS's view (last stage wins)
        conflicted = set()
        for raw in tracked.split(b"\0"):
            if not raw:
                continue
            meta, tab, rel = raw.partition(b"\t")
            fields = meta.split()
            if not tab or len(fields) != 4:
                raise Budget("a listing shape this reader does not understand")
            tag, mode, oid, stage = fields
            entries[rel] = (b"g", oid) if mode == GITLINK_MODE else (b"t", b"")
            index[rel] = (tag, mode, oid)
            if stage != b"0":
                conflicted.add(rel)
        for rel in others.split(b"\0"):
            if rel:
                entries.setdefault(rel, (b"u", b""))
        if len(entries) > max_files:
            raise Budget("too many entries")
        head = ""
        head_tree = {}
        attrs = {}
        filemode = True
        symlinks = True
        autocrlf = False
        algo = hashlib.sha1
        if want_status:
            rc, out = _git_output(top, ("rev-parse", "--verify", "--quiet", "HEAD"),
                                  deadline, git_timeout_s, 1 << 16)
            # --verify --quiet: an unborn branch yields rc != 0 and NO output
            # (plain `rev-parse HEAD` would echo the literal "HEAD" there).
            head = out.decode("ascii", "replace").strip() if rc == 0 else ""
            if head and not OID_RE.fullmatch(head.encode("ascii", "replace")):
                raise StatusUnreadable("HEAD is not an object id")
            if head:
                rc, out = _git_output(top, HEAD_TREE_ARGS, deadline, git_timeout_s, max_git_bytes)
                if rc != 0:
                    raise StatusUnreadable("HEAD tree listing failed")
                for raw in out.split(b"\0"):
                    if not raw:
                        continue
                    meta, tab, rel = raw.partition(b"\t")
                    f = meta.split()
                    if not tab or len(f) != 3:
                        raise StatusUnreadable("a tree listing shape this reader does not understand")
                    head_tree[rel] = (f[0], f[2])
            rc, out = _git_output(top, ("rev-parse", "--show-object-format"), deadline, git_timeout_s, 1 << 16)
            fmt = out.strip() if rc == 0 else b""
            if fmt == b"sha256":
                algo = hashlib.sha256
            elif fmt != b"sha1":
                raise StatusUnreadable("object format unknown")
            rc, out = _git_output(top, ("config", "-z", "--list"), deadline, git_timeout_s, 1 << 20)
            if rc != 0:
                raise StatusUnreadable("configuration unreadable")
            cfg = {}
            for entry in out.split(b"\0"):
                if entry:
                    key, _, val = entry.partition(b"\n")
                    cfg[_cfg_key(key)] = val
            filemode = cfg.get(b"core.filemode", b"true").strip().lower().decode("ascii", "replace") not in _FALSE
            symlinks = cfg.get(b"core.symlinks", b"true").strip().lower().decode("ascii", "replace") not in _FALSE
            autocrlf = cfg.get(b"core.autocrlf", b"false").strip().lower() in (b"true", b"input")
            paths = [rel for rel, (_t, mode, _o) in index.items() if mode != GITLINK_MODE]
            if paths:
                rc, out = _git_output(top, ("check-attr", "-z", "--stdin", *CONVERSION_ATTRS),
                                      deadline, git_timeout_s, max_git_bytes,
                                      payload=b"\0".join(paths) + b"\0")
                if rc != 0:
                    raise StatusUnreadable("attribute listing failed")
                fields = out.split(b"\0")
                if fields and fields[-1] == b"":
                    fields.pop()
                if len(fields) % 3:
                    raise StatusUnreadable("an attribute listing shape this reader does not understand")
                for i in range(0, len(fields), 3):
                    attrs.setdefault(fields[i], {})[fields[i + 1]] = fields[i + 2]
            result["binding"] = hashlib.sha256(
                top_b + b"\0" + head.encode("ascii") + b"\0" + tracked).hexdigest()[:16]
        check()
        parts = []
        lines = set()
        strict = {}         # every reason the PUSHED objects may not be the reviewed bytes
        strict_commit = {}  # the subset that concerns the INDEX (what a commit records)
        total = 0

        def note(rel, reason, commit_too=True):
            strict[_display(rel)] = reason
            if commit_too:
                strict_commit[_display(rel)] = reason

        for rel in sorted(entries):
            check()
            kind, extra = entries[rel]
            fp = os.path.join(top_b, rel)
            ie = index.get(rel)
            x = y = " "
            frozen = ie is not None and ie[0] in (b"S", b"h")  # skip-worktree / assume-unchanged: as git, unchanged
            if want_status and ie is not None and rel in conflicted:
                lines.add("UU " + _display(rel))
                note(rel, "unmerged entry")
            elif want_status and ie is not None:
                he = head_tree.get(rel)
                if he is None:
                    x = "A"
                elif he != (ie[1], ie[2]):
                    if _type_class(he[0]) != _type_class(ie[1]):
                        x = "T"
                    elif he[1] == ie[2] and not filemode:
                        x = " "  # a mode-only change git ignores under core.filemode=false
                        note(rel, "HEAD/index mode differ (core.filemode=false hides it)", commit_too=False)
                    else:
                        x = "M"
                if frozen:
                    note(rel, "skip-worktree/assume-unchanged entry: its blob was never read")
                if not filemode and ie[1] == b"100755":
                    note(rel, "index mode 100755 is not representable on this filesystem (core.filemode=false)")
                if not symlinks and ie[1] == SYMLINK_MODE:
                    note(rel, "index symlink materialised as a file (core.symlinks=false)")
            settled = want_status and ie is not None and rel not in conflicted
            if kind == b"g":
                sub_head = _gitlink_head(top_b, rel, deadline, git_timeout_s)
                parts.append(rel + b"\0g\0" + extra + b"\0" + sub_head)
                check()
                if settled and not frozen and sub_head != b"absent":
                    assert ie is not None
                    if sub_head != ie[2]:
                        y = "M"
                    else:
                        if _depth >= MAX_SUBMODULE_DEPTH:
                            raise StatusUnreadable("submodule nesting too deep")
                        inner = inspect(fp.decode("utf-8", "surrogateescape"), want_status=True,
                                        deadline_s=max(0.0, deadline - time.monotonic()),
                                        git_timeout_s=git_timeout_s, max_file_bytes=max_file_bytes,
                                        max_total_bytes=max_total_bytes, max_files=max_files,
                                        max_git_bytes=max_git_bytes, _depth=_depth + 1)
                        if inner["lines"] is None:
                            raise StatusUnreadable("submodule status unreadable: " + inner["reason"])
                        if inner["lines"]:
                            y = "M"
                    check()
                if settled:
                    emit(x, y, rel)
                    if x != " " or y != " ":
                        note(rel, "submodule differs", commit_too=(y != " "))
                continue
            try:
                st = os.lstat(fp)
            except FileNotFoundError:
                parts.append(rel + b"\0missing")  # tracked, deleted in the worktree
                if settled:
                    emit(x, " " if frozen else "D", rel)
                    if not frozen:
                        note(rel, "deleted in the worktree")
                    elif x != " ":
                        note(rel, "staged change", commit_too=False)
                continue
            check()
            if stat.S_ISLNK(st.st_mode):
                # a symlink's IDENTITY is its target path, as git stores it
                target = os.readlink(fp)
                parts.append(rel + b"\0l\0" + target)
                check()
                if want_status and ie is None:
                    lines.add("?? " + _display(rel))
                    note(rel, "untracked", commit_too=False)
                elif settled:
                    assert ie is not None
                    if frozen:
                        pass
                    elif ie[1] != SYMLINK_MODE:
                        y = "T"
                    elif _blob_oid(algo, len(target), (target,)) != ie[2]:
                        y = "M"
                    emit(x, y, rel)
                    if y != " ":
                        note(rel, "symlink differs from the index")
                    elif x != " ":
                        note(rel, "staged change", commit_too=False)
                continue
            if stat.S_ISDIR(st.st_mode):
                parts.append(rel + b"\0d")  # a directory where git expects a file
                if settled:
                    emit(x, " " if frozen else "D", rel)
                    if not frozen:
                        note(rel, "a directory where the index has a file")
                    elif x != " ":
                        note(rel, "staged change", commit_too=False)
                continue
            if not stat.S_ISREG(st.st_mode):
                raise Budget("special file")  # fifo/socket: void, never hang
            # OPEN ONCE, CLASSIFY THE DESCRIPTOR: the lstat above is advisory.
            # O_NOFOLLOW refuses a symlink swapped in, O_NONBLOCK makes a fifo
            # open return, fstat says what was really opened, the read is
            # capped at cap+1 bytes, and the deadline is checked after each.
            fd = os.open(fp, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
            chunks = []
            keep = settled and not frozen
            try:
                check()
                fst = os.fstat(fd)
                if not stat.S_ISREG(fst.st_mode) or fst.st_size > max_file_bytes:
                    raise Budget("file over the per-file cap")
                h = hashlib.sha256()
                remaining = max_file_bytes + 1
                size = 0
                while remaining > 0:
                    chunk = os.read(fd, min(1 << 20, remaining))
                    check()
                    if not chunk:
                        break
                    h.update(chunk)
                    if keep:
                        chunks.append(chunk)
                    size += len(chunk)
                    remaining -= len(chunk)
                    total += len(chunk)
                    if total > max_total_bytes:
                        raise Budget("tree over the total cap")
                if remaining <= 0:
                    raise Budget("file grew past the cap while being read")
            finally:
                os.close(fd)
            xbit = b"x" if fst.st_mode & 0o100 else b"-"  # git tracks the exec bit
            # the entry's INDEX state (tracked vs untracked) is not content:
            # `git add` must not change the digest, only bytes and identity do
            parts.append(rel + b"\0f" + xbit + b"\0" + h.digest())
            if not want_status:
                continue
            if ie is None:
                lines.add("?? " + _display(rel))
                note(rel, "untracked", commit_too=False)
                continue
            if not settled:
                continue
            if keep:
                assert ie is not None
                if size != fst.st_size:
                    raise StatusUnreadable("file changed while being read")
                if ie[1] == GITLINK_MODE:
                    y = "T"
                elif ie[1] == SYMLINK_MODE and symlinks:
                    y = "T"  # a regular file where the index has a symlink
                elif _blob_oid(algo, size, chunks) != ie[2]:
                    y = "~" if _converts(attrs.get(rel), autocrlf) else "M"
                elif ie[1] != SYMLINK_MODE and (ie[1] == b"100755") != bool(fst.st_mode & 0o100):
                    if filemode:
                        y = "M"
                    else:
                        note(rel, "exec bit differs from the index (core.filemode=false hides it)")
                chunks = []
            emit(x, y, rel)
            if y == "~":
                note(rel, "bytes differ under a conversion attribute (filter/eol/ident/encoding)")
            elif y != " ":
                note(rel, "worktree differs from the index")
            elif x != " ":
                note(rel, "staged change", commit_too=False)
        if want_status:
            for rel in head_tree:
                if rel not in index:
                    lines.add("D  " + _display(rel))
                    note(rel, "staged deletion", commit_too=False)
        check()
        result["digest"] = hashlib.sha256(b"\0".join(parts)).hexdigest()[:12]
        result["head"] = head
        if want_status:
            result["lines"] = lines
            result["strict"] = strict
            result["strict_commit"] = strict_commit
        return result
    except StatusUnreadable as exc:
        # STATUS-ONLY failure: the content digest still stands — recomputed by
        # a status-free walk within what is left of the deadline — and only
        # the status is unknown (the reason says why).
        again = inspect(cwd, want_status=False, deadline_s=max(0.0, deadline - time.monotonic()),
                        git_timeout_s=git_timeout_s, max_file_bytes=max_file_bytes,
                        max_total_bytes=max_total_bytes, max_files=max_files,
                        max_git_bytes=max_git_bytes, _depth=_depth)
        result["digest"] = again["digest"]
        result["lines"] = None
        result["strict"] = None
        result["strict_commit"] = None
        result["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return result
    except Exception as exc:
        # ANY other failure voids the digest — a crashed hook fails open, a
        # void digest only makes the gate more conservative — and the status.
        result["digest"] = "unknown"
        result["lines"] = None
        result["strict"] = None
        result["strict_commit"] = None
        result["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return result


def workspace_digest(cwd, **kw):
    """The CONTENT digest, computed IN THIS PROCESS with soft deadlines. Use
    digest_hard() where nothing above this process holds a kill."""
    return inspect(cwd, want_status=False, **kw)["digest"]


def worktree_status(cwd, **kw):
    """(ok, porcelain-shaped lines, HEAD oid or "", reason) — ok is False
    outside a work tree or whenever the status could not be established
    (budget, unreadable listing, unresolvable submodule); `reason` says why."""
    r = inspect(cwd, want_status=True, **kw)
    ok = bool(r["repo"]) and r["lines"] is not None
    return ok, (r["lines"] or set()), r["head"], r["reason"]


def digest_hard(cwd, deadline_s=DEADLINE_S, grace_s=GRACE_S, python=None):
    """The digest computed in a CHILD (its own process group) under a HARD
    deadline: the parent waits deadline + grace and SIGKILLs the whole group
    — a hung git or a stalled mount can no longer hold the hook past its
    timeout. Anything but a well-formed answer is "unknown"."""
    argv = [python or sys.executable, os.path.abspath(__file__),
            "--deadline", str(deadline_s), "--", str(cwd)]
    rc, out, why = run_contained(argv, deadline_s + grace_s, cap=4096, grace_s=grace_s)
    if rc != 0 or why:
        return "unknown"
    text = out.decode("ascii", "replace").strip()
    if text in ("nogit", "unknown") or HEX12.fullmatch(text):
        return text
    return "unknown"


def main(argv):
    deadline = DEADLINE_S
    status = False
    args = list(argv[1:])
    while args and args[0] in ("--deadline", "--status"):
        if args[0] == "--status":
            status = True
            args = args[1:]
            continue
        if len(args) < 2:
            return 2
        try:
            deadline = float(args[1])
        except ValueError:
            return 2
        args = args[2:]
    if args and args[0] == "--":
        args = args[1:]
    if len(args) != 1:
        return 2
    if status:
        r = inspect(args[0], want_status=True, deadline_s=deadline)
        print(r["digest"], r["head"] or "-")
        if r["lines"] is None:
            print("unknown:", r["reason"])
        else:
            for line in sorted(r["lines"]):
                print(line)
            for path, reason in sorted((r["strict"] or {}).items()):
                print("strict:", path, "—", reason)
        return 0
    print(workspace_digest(args[0], deadline_s=deadline))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
