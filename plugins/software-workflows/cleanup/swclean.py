#!/usr/bin/env python3
"""swclean: software-workflows' enforced cleanup (2.5.1). Linux (systemd user manager) only; stdlib only.

The owner's rule (2026-11-07, 2026-11-21): stop what you start; worktrees are cleared and deleted
after use; CPU, RAM and disk are given back after testing, enforced by mechanisms that run every
time (Claude Code hooks, Codex hooks, systemd), never by instructions a model can forget.

WHAT IS ENFORCED, AND BY WHAT
1. Nothing a command starts outlives the command, unless it was given a unit of its own.
   - Claude Code: the PreToolUse hook (`hook pre`) puts SW_CALL=<tool_use_id> and SW_AGENT=<agent id or
     "lead"> into each Bash call's environment (updatedInput, no permission decision; only in
     bypassPermissions mode, where no permission rule reads the command text). Every process the call
     starts inherits them. PostToolUse (`hook post`) then stops each process still carrying that call's
     tag once the call has returned (re-parented to the user's systemd, still in the session's own
     cgroup), with its descendants, and tells the model what it stopped.
   - Codex: Codex itself puts CODEX_THREAD_ID and CODEX_TOOL_CALL_ID into every command it runs, so its
     user-level PostToolUse hook (`hook codex-post`, installed into ~/.codex/hooks.json) does the same.
   - "A unit of its own" is the escape hatch: `swctl run` (below), or any process someone deliberately
     placed in another systemd unit (systemd-run --scope / --unit), or a line in the janitor's keep file
     (~/.config/janitor/keep, kinds cmd / port / path). Those are never stopped here.
2. Things that must keep running get a deadline and limits: `swctl run --name N --ttl 2h -- CMD` starts a
   transient systemd user service in sw.slice with RuntimeMaxSec, KillMode=control-group and memory, CPU
   and task limits; `--compose PROJECT` takes that Compose project down when the unit stops.
3. Worktrees: Claude removes a native subagent worktree that has no changes; one WITH changes stays until
   the lead lands it and removes it with Git's own commands (agent-teams skill, references/writers.md §5).
   Nothing here deletes a worktree: ignored files and index flags (assume-unchanged, skip-worktree) hide
   work from Git's status checks, and `git worktree remove` deletes both without asking (Git 2.56.0,
   measured). At the end of every lead turn (Stop) the hook refuses the stop once while the session's own
   finished native writers' worktrees still hold work that was neither landed nor dropped. `swctl wt list`
   shows every linked worktree.
4. Backstops: SubagentStop, Stop (turn end), SessionEnd (a detached sweep; the hook itself has 1.5 s) and
   a systemd user timer every 10 minutes (`sweep`): leftovers of finished calls, of dead sessions and of
   Codex calls, and the environment files of `swctl run` starts whose unit no longer uses them.

TRUST MODEL: a guardrail against forgetting, not a security boundary. Anything that clears its
environment (env -i, sudo), runs as another user, or lives in Docker's daemon is not seen by the process
rules; Docker belongs to Platform's run-guard (and to `swctl run --compose`).

Records: ~/.local/state/software-workflows/cleanup/ (cleanup.log JSONL: pid, name, event, session, call;
never command lines or environment values).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import sys
import time

# subprocess, shutil, hashlib and shlex are imported where they are used: the per-call hooks
# (pre / post) never need them, and they are most of the start-up cost of a hook (~40 ms, measured).

VERSION = "2.5.1"
HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"),
                     "software-workflows", "cleanup")
SHARE = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"),
                     "software-workflows")
STABLE = os.path.join(SHARE, "swclean.py")          # what Codex hooks, the timer and swctl run
CODEX_HOOKS = os.path.join(os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex"), "hooks.json")
SYSTEMD_USER = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "systemd", "user")
# nucleus's janitor (Platform tools/janitor/janitor.py) keeps its settings and keep file in this fixed
# folder (not XDG-aware); its unit may name another keep file with `--keep`.
JANITOR_DIR = os.path.join(HOME, ".config", "janitor")
MARK = "software-workflows cleanup"                 # identifies our entries in ~/.codex/hooks.json
TAG_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
WANTED = {b"CLAUDE_CODE_SESSION_ID", b"CLAUDE_PID", b"SW_CALL", b"SW_AGENT", b"CODEX_THREAD_ID",
          b"CODEX_TOOL_CALL_ID"}

# Limits for `swctl run` (named threat: one forgotten or runaway test stack starving the leads and
# T3 sessions on nucleus: 18 cores, 94 GB RAM, 5-10 writers plus leads). Sized from nucleus's measured
# capacity, with the slice-wide caps leaving more than a third of the machine to everything else.
# Adjustable per call (--ttl / --mem / --cpu) and per user (environment); never silent: an over-cap
# request is refused with the cap and how to raise it.
DEFAULT_TTL_S = 2 * 3600
MAX_TTL_S = int(os.environ.get("SW_RUN_MAX_TTL_S") or 24 * 3600)
DEFAULT_MEM = os.environ.get("SW_RUN_DEFAULT_MEM") or "8G"
DEFAULT_CPU = os.environ.get("SW_RUN_DEFAULT_CPU") or "400%"
SLICE_MEM = os.environ.get("SW_SLICE_MEM") or "56G"
SLICE_CPU = os.environ.get("SW_SLICE_CPU") or "1200%"
TERM_GRACE_S = 3.0
ORPHAN_AGE_S = 300       # the timer stops an orphan of an unfinished-looking call only after this long
CODEX_ORPHAN_AGE_S = 1800


# ----------------------------------------------------------------------------------------------
# small utilities
# ----------------------------------------------------------------------------------------------

def _now():
    return time.time()


class contextlib_suppress:
    """contextlib.suppress without importing contextlib in the per-call hooks."""

    def __init__(self, *excs):
        self.excs = excs

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        return kind is not None and issubclass(kind, self.excs)


def _log(event, **fields):
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        rec = {"t": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, **fields}
        with open(os.path.join(STATE, "cleanup.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError:
        pass


def _git(cwd, *args, timeout=20, check=False):
    import subprocess  # noqa: lazy (see the module imports)
    cmd = ["git", "-c", "core.fsmonitor=", "--no-pager", "-C", cwd, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise RuntimeError(f"git {' '.join(args)}: {exc}") from exc
        return 1, "", str(exc)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r.returncode, r.stdout, r.stderr


def _human_s(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _parse_duration(text):
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", text or "")
    if not m:
        raise ValueError(f"not a duration: {text!r} (use 90s, 30m, 2h, 1d)")
    n = int(m.group(1))
    return n * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


# ----------------------------------------------------------------------------------------------
# processes
# ----------------------------------------------------------------------------------------------

def _clk_tck():
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return 100


def _boot_time():
    try:
        with open("/proc/stat", "rb") as fh:
            for line in fh:
                if line.startswith(b"btime "):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def _stat(pid):
    """(ppid, uid, starttime ticks, comm) or None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        uid = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None
    try:
        r = data.rindex(b")")
        comm = data[data.index(b"(") + 1:r].decode("utf-8", "replace")
        fields = data[r + 2:].split()
        return int(fields[1]), uid, int(fields[19]), comm
    except (ValueError, IndexError):
        return None


def _state(pid):
    """The process state letter (R, S, Z ...), or "" when it is gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        return data[data.rindex(b")") + 2:].split()[0].decode()
    except (OSError, ValueError, IndexError):
        return ""


def _table():
    """pid -> (ppid, uid, start, comm) for every process."""
    out = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            st = _stat(int(name))
            if st:
                out[int(name)] = st
    return out


def _environ(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            raw = fh.read(1 << 20)
    except OSError:
        return None
    env = {}
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep and k in WANTED:
            env[k.decode()] = v.decode("utf-8", "replace")
    return env


def _cgroup(pid):
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("0::"):
                    return line[3:].strip()
    except OSError:
        pass
    return None


def _cmdline(pid, limit=16 << 20):
    """The whole argument list, joined by spaces and read through EOF (the janitor reads all of it too),
    or None when it cannot be read or is longer than `limit`: a keep check then counts it as unknown
    and spares the process (review of 2.5.1, turn 14: a 64 KiB read missed a keep token at 70 KB)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        return None
    return data.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _managers(table):
    """Processes that adopt orphans here: PID 1 and this user's `systemd --user` (a subreaper)."""
    me = os.getuid()
    return {1} | {pid for pid, (_, uid, _, comm) in table.items() if comm == "systemd" and uid == me}


def _descendants(table, roots):
    kids = {}
    for pid, (ppid, _, _, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = set(), list(roots)
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.add(p)
        todo.extend(kids.get(p, ()))
    return out


def _listening_ports(pids):
    """{pid: set(ports) or None} for TCP listeners owned by these pids (keep-file `port` lines), read in
    each process's OWN network namespace (/proc/<pid>/net/tcp*). None = could not be read: the caller
    spares that process rather than guess (review of 2.5.1, HIGH)."""
    by_ns = {}
    out = {}
    for pid in pids:
        try:
            ns = os.readlink(f"/proc/{pid}/ns/net")
            if ns not in by_ns:
                inodes = {}
                for name in ("tcp", "tcp6"):
                    try:
                        with open(f"/proc/{pid}/net/{name}", encoding="ascii", errors="replace") as fh:
                            next(fh, None)
                            for line in fh:
                                parts = line.split()
                                if len(parts) > 9 and parts[3] == "0A":   # LISTEN
                                    inodes[parts[9]] = int(parts[1].rsplit(":", 1)[1], 16)
                    except FileNotFoundError:
                        pass   # no IPv6 in that namespace
                by_ns[ns] = inodes
            ports = set()
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    link = os.readlink(f"/proc/{pid}/fd/{fd}")
                except FileNotFoundError:
                    continue   # closed while listed
                if link.startswith("socket:[") and link[8:-1] in by_ns[ns]:
                    ports.add(by_ns[ns][link[8:-1]])
            out[pid] = ports
        except (OSError, ValueError):
            out[pid] = None
    return out


class KeepFile:
    """The janitor's keep file, read with the janitor's own rules (Platform tools/janitor/janitor.py
    Keep): KIND NAME EXPIRES OWNER REASON, kinds db|path|container|port|cmd, an ISO date, a numeric port,
    an absolute path, expiry capped 90 days ahead. Any problem makes the janitor refuse every action,
    and this cleanup then spares every process too (ok=False). One deliberate difference: a line of an
    unknown kind whose expiry has passed protects nothing under any reading, so it is not a problem.
    Kinds used here: cmd, port, path (a process in another systemd unit is never stopped anyway).

    Which file: SW_KEEP_FILE when set, else the janitor's own (its unit's `--keep`, else its default).
    A missing file spares everything when it was named explicitly or the janitor is installed (its user
    unit, or its settings in its fixed folder), because the janitor then refuses everything too; with no
    janitor and no override there is simply no keep file to honour (review of 2.5.1, turn 13)."""
    KINDS = ("db", "path", "container", "port", "cmd")

    _janitor = None    # (installed, keep path or None), found once per process

    @classmethod
    def janitor(cls):
        """(installed, keep path) of nucleus's janitor, from its EFFECTIVE unit command: the user manager's
        structured argument lists (busctl JSON; drop-ins included), not the flattened display text, whose
        argument boundaries are lost (review of 2.5.1, turns 14-15). The keep path is None, and the caller
        then spares everything unless SW_KEEP_FILE names the file, when it cannot be told exactly: no
        answer from the manager while a unit file exists, unreadable arguments, a relative path (it would
        depend on the unit's working directory) or one systemd would still expand ($, %)."""
        if cls._janitor is not None:
            return cls._janitor
        import subprocess  # noqa: lazy (see the module imports)

        def run(argv):
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return None
            return r.stdout if r.returncode == 0 else None

        installed = os.path.exists(os.path.join(JANITOR_DIR, "settings.toml"))
        default = os.path.join(JANITOR_DIR, "keep")
        state = ""
        for line in (run(["systemctl", "--user", "show", "-p", "LoadState", "janitor.service"]) or "").splitlines():
            key, _, val = line.partition("=")
            if key == "LoadState":
                state = val.strip()
        if not state:
            unit = os.path.exists(os.path.join(SYSTEMD_USER, "janitor.service"))
            cls._janitor = (True, None) if unit else (installed, default)
            return cls._janitor
        if state in ("not-found", "masked"):
            cls._janitor = (installed, default)
            return cls._janitor
        # systemd 255 (measured): {"type":"a(sasbttttuii)","data":[["/usr/bin/python3",["/usr/bin/python3",
        # "-I",".../janitor.py"],false,...]]}, one entry per ExecStart command
        raw = run(["busctl", "--user", "get-property", "org.freedesktop.systemd1",
                   "/org/freedesktop/systemd1/unit/janitor_2eservice", "org.freedesktop.systemd1.Service",
                   "ExecStart", "--json=short"])
        try:
            commands = json.loads(raw)["data"] if raw else None
            argvs = [c[1] for c in commands if isinstance(c[1], list)] if isinstance(commands, list) else None
        except (ValueError, KeyError, TypeError, IndexError):
            argvs = None
        if not argvs:
            cls._janitor = (True, None)
            return cls._janitor
        keep = default
        for argv in argvs:
            for i in range(1, len(argv)):
                name, eq, val = str(argv[i]).partition("=")
                # the janitor's argparse takes any unique prefix of --keep (--k, --ke, --kee)
                if len(name) >= 3 and name.startswith("--k") and "--keep".startswith(name):
                    keep = val if eq else (str(argv[i + 1]) if i + 1 < len(argv) else None)
        if keep is None or not os.path.isabs(keep) or "$" in keep or "%" in keep:
            cls._janitor = (True, None)
        else:
            cls._janitor = (True, keep)
        return cls._janitor

    def __init__(self, path=None):
        self.ok, self.cmds, self.ports, self.paths, self.why = True, [], set(), [], ""
        explicit = path or os.environ.get("SW_KEEP_FILE") or ""
        installed, janitor_keep = self.janitor()
        if not explicit and janitor_keep is None:
            self.ok, self.why = False, "the janitor's unit exists but its keep file cannot be determined"
            return
        path = explicit or janitor_keep or ""
        problems = []
        today = dt.date.today()
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            if explicit:
                self.ok, self.why = False, f"keep file {path} (named by SW_KEEP_FILE) is missing"
            elif installed:
                self.ok, self.why = False, f"keep file {path} is missing while the janitor is installed"
            return
        except (OSError, UnicodeDecodeError) as exc:
            self.ok, self.why = False, f"keep file unreadable ({exc})"
            return
        for n, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 4)
            if len(parts) < 4:
                problems.append(f"line {n} is not KIND NAME EXPIRES OWNER REASON")
                continue
            kind, name, exp = parts[0], parts[1], parts[2]
            try:
                expires = dt.date.fromisoformat(exp)
            except ValueError:
                problems.append(f"line {n}: expiry {exp!r} is not YYYY-MM-DD")
                continue
            if kind not in self.KINDS:
                if expires < today:
                    continue             # an unknown kind that has expired protects nothing
                problems.append(f"line {n}: unknown kind {kind!r}")
                continue
            if kind == "port" and not name.isdigit():
                problems.append(f"line {n}: port {name!r} is not a number")
                continue
            if kind == "path" and not name.startswith("/"):
                problems.append(f"line {n}: path {name!r} is not absolute")
                continue
            if expires > today + dt.timedelta(days=90):
                problems.append(f"line {n}: expiry {exp} is more than 90 days ahead")   # as the janitor
                continue
            if today > expires:
                continue
            if kind == "cmd":
                self.cmds.append(name)
            elif kind == "port":
                self.ports.add(int(name))
            elif kind == "path":
                self.paths.append(os.path.realpath(name).rstrip("/"))
        if problems:
            self.ok, self.why = False, "keep file malformed: " + "; ".join(problems)[:300]

    def keeps(self, pid, ports):
        """True when a line protects this process, or when the evidence a line needs cannot be read
        (an unreadable command line, working directory or socket list spares the process). A zombie, or a
        process already gone, has no evidence left and protects nothing (review of 2.5.1, turn 14: a
        zombie's empty command line made its live parent look protected)."""
        if not self.ok:
            return True
        if _state(pid) in ("Z", "X", ""):
            return False
        if self.cmds:
            cmd = _cmdline(pid)
            if not cmd or any(c in cmd for c in self.cmds):
                return True
        if self.ports:
            mine = ports.get(pid)
            if mine is None or mine & self.ports:
                return True
        if self.paths:
            cwd = _cwd(pid)
            if not cwd or any(cwd == p or cwd.startswith(p + "/") for p in self.paths):
                return True
        return False


def _in_scope(pid, scopes):
    cg = _cgroup(pid)
    return bool(cg) and cg in scopes and "/sw.slice/" not in cg


def _protected(pid, scopes, ctx):
    """Whether `pid` must be spared, decided right before its own signal from a fresh read: the keep
    file, a fresh process table, and the evidence keep lines need (command line, folder, ports), read now
    for `pid`, its current ancestors up to the user's manager, and its current descendants. Spared when
    a keep line matches:
    - the process itself;
    - an ancestor, since a kept supervisor keeps its whole subtree (review of 2.5.1, turn 15: a check of
      the recipient's descendants alone let a kept supervisor's workers go);
    - or a descendant, since a parent such as `timeout` passes its own SIGTERM on to the child.
    Also spared: a process with a live descendant outside the stopping scope (another user's, or one
    placed in another unit such as `systemd-run --scope` or sw.slice), for the same reason (turn 14).
    The table is a full scan of /proc, because a process's own list of children can omit a live child
    while another one exits (proc_tid_children(5); turn 15). Returns None when the keep file is not usable
    now, which spares everything, as the janitor refuses everything."""
    keep = KeepFile()
    if not keep.ok:
        _log("refused", why=keep.why, **ctx)
        return None
    table, me = _table(), os.getuid()
    if pid not in table:
        return False
    managers = _managers(table)
    family = _descendants(table, [pid])
    ancestors, parent = [], table[pid][0]
    while parent in table and parent not in managers and parent not in ancestors and parent != pid:
        ancestors.append(parent)
        parent = table[parent][0]
    outside = [p for p in family if p != pid and _state(p) not in ("Z", "X", "")
               and (table[p][1] != me or not _in_scope(p, scopes))]
    kept = bool(outside)
    if not kept and (keep.cmds or keep.ports or keep.paths):
        evidence = family | set(ancestors)
        ports = _listening_ports(evidence) if keep.ports else {}
        kept = any(keep.keeps(p, ports) for p in evidence)
    if kept:
        _log("kept", pids=[pid], outside=len(outside), **ctx)
    return kept


def _stop_pids(table, pids, why, ctx, scopes):
    """SIGTERM, wait up to TERM_GRACE_S, SIGKILL survivors. Each pid is pinned by a pidfd opened after
    re-checking its start time, so a reused pid is never signalled, and its cgroup is checked again
    after pinning and before SIGKILL: a process moved into another unit meanwhile is left alone. The keep
    check runs fresh right before SIGTERM and again before SIGKILL: a survivor that entered a kept folder,
    opened a kept port, got a new keep line or started a kept child meanwhile is spared (review of 2.5.1,
    turn 13). Returns [(pid, comm, result)]."""
    pinned = []
    for pid in sorted(pids):
        st = table.get(pid)
        if not st:
            continue
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            continue
        cur = _stat(pid)
        if not cur or cur[2] != st[2] or not _in_scope(pid, scopes):
            os.close(fd)
            continue
        pinned.append((pid, st[3], fd))
    # The keep check runs for each process right before its own signal, over its current ancestors and
    # subtree: a snapshot taken once per batch could go stale for later recipients (review of 2.5.1,
    # turn 14). It costs one /proc scan (27 ms for 561 processes on nucleus) per signal.
    # A keep file that becomes unusable mid-batch stops all further signals.
    blocked = False

    def protected(pid):
        nonlocal blocked
        if blocked:
            return True
        verdict = _protected(pid, scopes, ctx)
        if verdict is None:
            blocked = True
            return True
        return verdict

    for pid, comm, fd in list(pinned):
        if protected(pid):
            os.close(fd)
            pinned.remove((pid, comm, fd))
            continue
        if not _in_scope(pid, scopes):    # moved into another unit since it was pinned
            continue
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except OSError:
            pass
    def still(pid):
        cur = _stat(pid)
        return bool(cur) and cur[2] == table[pid][2] and _state(pid) not in ("Z", "X", "")

    end = time.monotonic() + TERM_GRACE_S
    alive = list(pinned)
    while alive and time.monotonic() < end:
        time.sleep(0.1)
        alive = [x for x in alive if still(x[0])]
    results = []
    for pid, comm, fd in pinned:
        res = "stopped"
        if (pid, comm, fd) in alive:
            if protected(pid):
                res = "left (protected when SIGKILL was due)"
            elif not _in_scope(pid, scopes):
                res = "left (moved to another unit)"
            else:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                    res = "killed"
                except OSError:
                    res = "gone"
        os.close(fd)
        results.append((pid, comm, res))
        _log("stop", pid=pid, name=comm, result=res, why=why, **ctx)
    return results


def _summary(results):
    names = {}
    for _, comm, _ in results:
        names[comm] = names.get(comm, 0) + 1
    return ", ".join(f"{c}×{n}" if n > 1 else c for c, n in sorted(names.items()))


# ----------------------------------------------------------------------------------------------
# leftover finding
# ----------------------------------------------------------------------------------------------

class Snapshot:
    """One read of the process table plus the tags of re-parented processes."""

    def __init__(self):
        self.table = _table()
        self.managers = _managers(self.table)
        me = os.getuid()
        self.orphans = {}          # pid -> env, for this user's re-parented processes with any tag
        for pid, (ppid, uid, _, _) in self.table.items():
            if uid == me and ppid in self.managers and pid != os.getpid():
                env = _environ(pid)
                if env and (env.get("SW_CALL") or env.get("CODEX_TOOL_CALL_ID")
                            or env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_THREAD_ID")):
                    self.orphans[pid] = env

    def age(self, pid):
        st = self.table.get(pid)
        if not st:
            return 0
        return max(0.0, _now() - (_boot_time() + st[2] / _clk_tck()))

    def stop(self, roots, scopes, why, ctx):
        """Stop roots (and their descendants) that are still in one of `scopes` (cgroup paths: the agent
        session's own) and not kept. A process someone placed in another unit (systemd-run, swctl run)
        is in another cgroup and is never stopped here. Returns the results list."""
        scopes = {s for s in scopes if s and "/sw.slice/" not in s}
        if not scopes:
            return []
        roots = [p for p in roots if _in_scope(p, scopes)]
        if not roots:
            return []
        victims = {p for p in _descendants(self.table, roots)
                   if self.table.get(p, (0, -1))[1] == os.getuid() and _in_scope(p, scopes)}
        return _stop_pids(self.table, victims, why, ctx, scopes)   # the keep check runs in there, fresh

    def live_calls(self, anchors, key):
        """Calls still running: the tag (env[key]) of any process that still descends from one of
        `anchors` (the agent process). Claude's tag is exported by the call's own shell after it
        started, so that shell's /proc environ lacks it; its command line carries the rewritten
        command (`export SW_CALL=<id> …`), which counts too (review of 2.5.1, HIGH)."""
        live = set()
        for pid in _descendants(self.table, anchors):
            if pid in anchors:
                continue
            env = _environ(pid)
            if env and env.get(key):
                live.add(env[key])
            if key == "SW_CALL":
                live.update(re.findall(r"SW_CALL=([A-Za-z0-9_-]{1,128})", _cmdline(pid) or ""))
        return live


# ----------------------------------------------------------------------------------------------
# hooks
# ----------------------------------------------------------------------------------------------

def _read_payload():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def hook_pre(data):
    """Claude PreToolUse (Bash): tag the call. bypassPermissions only: in other modes permission rules
    read the command text, and a prefix would change what they match."""
    if data.get("tool_name") != "Bash":
        return
    mode = data.get("permission_mode") or ""
    if mode != "bypassPermissions" and os.environ.get("SW_CLEANUP_TAG") != "always":
        return
    tin = data.get("tool_input") or {}
    cmd = tin.get("command")
    call = str(data.get("tool_use_id") or "")
    agent = str(data.get("agent_id") or "lead")
    if not isinstance(cmd, str) or not cmd.strip() or not TAG_RE.fullmatch(call) or not TAG_RE.fullmatch(agent):
        return
    new = dict(tin)
    new["command"] = f"export SW_CALL={call} SW_AGENT={agent}; {cmd}"
    _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": new}})


RULE_NOTE = ("Rule (owner, 2026-11-07): nothing a command starts may outlive it. To keep something running, "
             "start it with `swctl run --name <name> --ttl <2h> -- <command>` (a systemd unit with a deadline "
             "and limits; `swctl run list` / `swctl run stop <name>`), or add a line to ~/.config/janitor/keep.")


def hook_post(data, codex=False):
    """PostToolUse / PostToolUseFailure (Bash): stop what this call left running."""
    tin = data.get("tool_input") or {}
    if not codex and (data.get("tool_name") != "Bash" or tin.get("run_in_background")):
        return
    resp = data.get("tool_response")
    if isinstance(resp, dict) and any(v for k, v in resp.items() if "background" in k.lower()):
        # Claude moved this foreground call to the background (backgroundTaskId): it is still
        # running, and what it started belongs to it until it ends (review of 2.5.1, HIGH)
        return
    call = str(data.get("tool_use_id") or "")
    if not TAG_RE.fullmatch(call):
        return
    session = str(data.get("session_id") or "")
    snap = Snapshot()
    if codex:
        roots = [p for p, env in snap.orphans.items() if env.get("CODEX_TOOL_CALL_ID") == call
                 and (not session or env.get("CODEX_THREAD_ID") in (session, None))]
    else:
        roots = [p for p, env in snap.orphans.items() if env.get("SW_CALL") == call
                 and env.get("CLAUDE_CODE_SESSION_ID") == session]
    if not roots:
        return
    ctx = {"session": session, "call": call, "agent": data.get("agent_id") or "lead", "codex": codex}
    results = snap.stop(roots, {_cgroup(os.getpid())}, "left running after its call returned", ctx)
    if not results:
        return
    msg = (f"[cleanup] This command left {len(results)} process(es) running after it returned; they were "
           f"stopped: {_summary(results)}. " + RULE_NOTE)
    _emit({"hookSpecificOutput": {"hookEventName": data.get("hook_event_name") or "PostToolUse",
                                  "additionalContext": msg}})


def _agent_pid(codex):
    """The agent process this hook runs under: the nearest `claude` (or `codex…`) ancestor."""
    table = _table()
    pid = os.getppid()
    while pid > 1:
        st = table.get(pid)
        if not st:
            break
        if (st[3].startswith("codex") if codex else st[3] == "claude"):
            return pid
        pid = st[0]
    if not codex and (os.environ.get("CLAUDE_PID") or "").isdigit():
        return int(os.environ["CLAUDE_PID"])
    return os.getppid()


def _sessions_dir():
    path = os.path.join(STATE, "sessions")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _record_session(session, cwd):
    """At SessionStart: the session's own cgroup and agent process, so the timer can tell later that
    the session ended and which cgroup its leftovers are in."""
    if not TAG_RE.fullmatch(session or ""):
        return
    pid = _agent_pid(False)
    st = _stat(pid)
    rec = {"session": session, "pid": pid, "start": st[2] if st else 0, "cgroup": _cgroup(pid) or "",
           "cwd": cwd, "t": _now()}
    d = _sessions_dir()
    _write_if_changed(os.path.join(d, session + ".json"), json.dumps(rec) + "\n", 0o600)
    cutoff = _now() - 30 * 86400
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            pass


def _record_codex_session(thread):
    """Codex SessionStart: the thread's own agent process and cgroup, so the timer and SessionEnd stop
    only what is in THAT scope (review of 2.5.1, turn 10)."""
    if not TAG_RE.fullmatch(thread or ""):
        return
    pid = _agent_pid(True)
    st = _stat(pid)
    rec = {"session": thread, "pid": pid, "start": st[2] if st else 0, "cgroup": _cgroup(os.getpid()) or "",
           "codex": True, "t": _now()}
    _write_if_changed(os.path.join(_sessions_dir(), "codex-" + thread + ".json"), json.dumps(rec) + "\n", 0o600)


def _session_record(session):
    try:
        with open(os.path.join(_sessions_dir(), session + ".json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _sweep_finished(session, codex, why, agent=None):
    """Stop orphans of this session's calls that are no longer running."""
    snap = Snapshot()
    anchors = {_agent_pid(codex)}
    if codex:
        key, skey = "CODEX_TOOL_CALL_ID", "CODEX_THREAD_ID"
    else:
        key, skey = "SW_CALL", "CLAUDE_CODE_SESSION_ID"
    live = snap.live_calls(anchors, key)
    roots = [p for p, env in snap.orphans.items()
             if env.get(skey) == session and env.get(key) and env[key] not in live
             and (agent is None or env.get("SW_AGENT") == agent)]
    if not roots:
        return []
    return snap.stop(roots, {_cgroup(os.getpid())}, why, {"session": session, "agent": agent or "", "codex": codex})


def hook_subagent_stop(data):
    agent = str(data.get("agent_id") or "")
    session = str(data.get("session_id") or "")
    if TAG_RE.fullmatch(agent) and session:
        _sweep_finished(session, False, "left running by a finished subagent", agent=agent)


def hook_stop(data, codex=False):
    """Turn end. Stop leftovers of finished calls; for a Claude lead, refuse the stop once while finished
    native worktree writers still hold work that was neither landed nor dropped."""
    session = str(data.get("session_id") or "")
    if session:
        _sweep_finished(session, codex, "left running at turn end")
    if codex:
        return
    cwd = data.get("cwd") or os.getcwd()
    transcript = str(data.get("transcript_path") or "")
    owner_dir = os.path.splitext(transcript)[0] if transcript.endswith(".jsonl") else None
    try:
        pending = _tidy_worktrees(cwd, owner_dir=owner_dir)
    except Exception as exc:  # the stop must never break on a worktree read
        _log("worktree-error", error=str(exc)[:300])
        return
    if not pending or data.get("stop_hook_active"):
        return
    lines = [f"- {w['path']}: {w['state']}" for w in pending[:12]]
    _emit({"decision": "block", "reason": "[cleanup] Finished writers' worktrees still hold work that was "
           "neither landed nor dropped:\n" + "\n".join(lines) + "\n" + LANDING})


# The landing steps, as the Stop hook gives them (the agent-teams skill's references/writers.md §5 has the
# same steps with their reasons). Every warning below was measured on Git 2.56.0 (review of 2.5.1, turn 13).
LANDING = """For each, once its writer has finished (its task completed):
1. In the worktree, have the writer commit on its own branch everything that should land. `git status --short --ignored` and `git ls-files -v | grep -v '^H '` show what plain `git status` hides: ignored files, and edits hidden by assume-unchanged (h) or skip-worktree (S).
2. Note, as literal text (a shell variable does not survive between Bash calls), the main checkout's `git rev-parse HEAD` before the first landing, and the writer's tip `git rev-parse <branch>`.
3. In the main checkout, run `swctl wt check <branch>`: it lists the untracked files there that landing would overwrite or delete, over every commit picked, and prints the exact range to land. Move each listed file aside first (`--no-overwrite-ignore` guards only a fast-forward).
4. Land exactly the printed range: `git cherry-pick <base>..<tip>`, or `git merge --squash --no-overwrite-ignore <tip>` and then `git commit` (a squash only stages).
5. Review `git diff <noted HEAD>` (the landed commits plus later edits), not `git diff HEAD`, which no longer shows committed work. Give the blind review that literal range.
6. `git worktree remove <path>` deletes ignored files and edits hidden by index flags without asking: only after step 1 shows nothing left that you want.
7. Delete the branch only after `git diff <writer tip> HEAD -- <its paths>` shows nothing but later changes, and only at that tip: `git update-ref -d refs/heads/<branch> <writer tip>` (it refuses if the branch moved since). `git cherry` and `git branch -d` prove nothing here; if unsure, keep the branch.
If one must stay for the next turn, say which and why, then end the turn."""


def hook_session_end(data, codex=False):
    """SessionEnd has 1.5 s (Claude) / 1 s (Codex): hand the final sweep to a detached systemd unit."""
    import subprocess  # noqa: lazy (see the module imports)
    session = str(data.get("session_id") or "")
    if not TAG_RE.fullmatch(session):
        return
    me = os.path.abspath(__file__)
    script = STABLE if os.path.exists(STABLE) else me
    # The sweep runs later, detached: it gets the identity of the process that is ending now, so a
    # session resumed meanwhile (a new process, maybe a new cgroup) is never swept by the old end
    # (review of 2.5.1, HIGH). Codex: the hook's own cgroup is the ending app-server's.
    pid = _agent_pid(codex)
    st = _stat(pid)
    cgroup = _cgroup(os.getpid()) if codex else _cgroup(pid)
    cmd = [sys.executable, script, "sweep", "--session", session, "--cwd", data.get("cwd") or "",
           "--transcript", str(data.get("transcript_path") or ""), "--pid", str(pid),
           "--start", str(st[2] if st else 0), "--cgroup", cgroup or ""]
    if codex:
        cmd.append("--codex")
    unit = f"sw-end-{session[:12]}-{os.getpid()}"
    try:
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                            "-p", "RuntimeMaxSec=300", "--", *cmd],
                           timeout=1.0, capture_output=True)
        ok = r.returncode == 0
        if not ok:
            _log("session-end-unit-refused", session=session, rc=r.returncode,
                 err=r.stderr.decode("utf-8", "replace")[:200])
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok = False
        _log("session-end-unit-failed", session=session, error=str(exc)[:200])
    if not ok:
        try:
            subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            _log("session-end-sweep-not-started", session=session, error=str(exc)[:200])


def hook_codex_session_start(data):
    _record_codex_session(str(data.get("session_id") or ""))


def hook_session_start(data):
    _record_session(str(data.get("session_id") or ""), data.get("cwd") or "")
    try:
        install(quiet=True)
    except Exception as exc:
        _log("install-error", error=str(exc)[:300])


# ----------------------------------------------------------------------------------------------
# worktrees
# ----------------------------------------------------------------------------------------------

def _worktrees(repo):
    """Git's worktree list, or None when Git could not list it (not a repository, or a failure)."""
    rc, out, _ = _git(repo, "worktree", "list", "--porcelain", "-z", timeout=10)
    if rc != 0:
        return None
    items, cur = [], {}
    for field in out.split("\0"):
        if not field:
            if cur:
                items.append(cur)
                cur = {}
            continue
        key, _, val = field.partition(" ")
        cur[key] = val if val or key not in ("locked", "prunable", "bare", "detached") else True
    if cur:
        items.append(cur)
    return items


def _main_checkout(cwd):
    items = _worktrees(cwd) or []
    return items[0]["worktree"] if items and "worktree" in items[0] else None


# Folders a writer's tools regenerate; skipped when judging how long a worktree has been idle.
REGENERATED = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", ".turbo",
               ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".gradle", ".parcel-cache", "coverage"}


def _wt_status(path, ignored=True, timeout=60):
    """(changed entries, ignored entries or None when not asked) from one `git status`, or (None, None)
    when unreadable."""
    rc, out, _ = _git(path, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all",
                      "--ignored=matching" if ignored else "--ignored=no", timeout=timeout)
    if rc != 0:
        return None, None
    entries = [x for x in out.split("\0") if x]
    return ([e for e in entries if not e.startswith("!! ")],
            [e[3:] for e in entries if e.startswith("!! ")] if ignored else None)


def _wt_hidden(path, timeout=30):
    """Index entries whose flags hide edits from `git status` (assume-unchanged: lowercase tag;
    skip-worktree: S), or None when unreadable."""
    rc, out, _ = _git(path, "ls-files", "-v", "-z", timeout=timeout)
    if rc != 0:
        return None
    hidden = []
    for entry in out.split("\0"):
        if len(entry) < 3:
            continue
        tag, name = entry[0], entry[2:]
        # a sparse checkout's skip-worktree entries have no file; one that has a file can hide an edit
        if tag.islower() or (tag == "S" and os.path.lexists(os.path.join(path, name))):
            hidden.append(name)
    return hidden


def _wt_meta(owner_dir, path):
    """The owning session's meta record for a native worktree's agent, or None."""
    name = os.path.basename(path.rstrip("/"))
    if not owner_dir or not name.startswith("agent-"):
        return None
    try:
        with open(os.path.join(owner_dir, "subagents", name + ".meta.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if os.path.exists(os.path.join(owner_dir, "subagents", name + ".jsonl")) else None


def _wt_idle(path, deadline=None):
    """Seconds since the newest change among (at most) 20,000 files, skipping regenerated folders; the walk
    also stops at `deadline` (time.monotonic())."""
    newest, budget = 0.0, 20000
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git" and d not in REGENERATED]
        for name in files:
            budget -= 1
            if budget % 256 == 0 and deadline is not None and time.monotonic() > deadline:
                break       # checked inside a folder too: one folder can hold many files (turn 15)
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                pass
        if budget <= 0 or (deadline is not None and time.monotonic() > deadline):
            break
    try:
        newest = max(newest, os.stat(path).st_mtime)
    except OSError:
        pass
    return _now() - newest if newest else 0


def _wt_state(main, wt, main_head=None, ignored=True, walk=True, deadline=None, timeout=60):
    """Describe a worktree: changed entries, ignored entries (when asked), entries hidden by index flags,
    commits not in the main checkout's HEAD, idle seconds (when `walk`). Each value is None when it could
    not be read. With `deadline` (time.monotonic()), every step gets only the time still left, and a step
    with under a second left is skipped (review of 2.5.1, turn 14)."""
    def budget(cap):
        if deadline is None:
            return cap
        left = deadline - time.monotonic()
        return int(min(cap, left)) if left >= 1 else 0

    path = wt["worktree"]
    t = budget(timeout)
    dirty, ign = _wt_status(path, ignored=ignored, timeout=t) if t else (None, None)
    t = budget(30)
    hidden = _wt_hidden(path, timeout=t) if t else None
    if main_head is None:
        t = budget(5)
        rc2, out, _ = _git(main, "rev-parse", "HEAD", timeout=t) if t else (1, "", "")
        main_head = out.strip() if rc2 == 0 else ""
    ahead = None
    t = budget(10)
    if t and main_head and wt.get("HEAD"):
        rc3, cnt, _ = _git(main, "rev-list", "--count", f"{main_head}..{wt['HEAD']}", timeout=t)
        ahead = int(cnt.strip()) if rc3 == 0 and cnt.strip().isdigit() else None
    idle = None
    if walk and budget(1):
        idle = _wt_idle(path, deadline)
    return {"dirty": dirty, "ignored": ign, "hidden": hidden, "ahead": ahead, "idle_s": idle}


def _native(main, path):
    return os.path.dirname(path) == os.path.join(main, ".claude", "worktrees")


STOP_WT_BUDGET_S = 40    # of the Stop hook's 60 s (hooks.json): the sweep before it and the reply after


def _tidy_worktrees(cwd, owner_dir=None, budget_s=STOP_WT_BUDGET_S):
    """Return the unlanded native worktrees that belong to the session whose transcript folder is
    `owner_dir`. Ownership is decided first, from the session's own subagent records, so another
    session's worktrees cost no Git call and no walk (review of 2.5.1, turn 13); a worktree the hook had
    no time left to inspect is reported as such. Nothing is ever deleted here: Claude removes a writer's
    worktree by itself when the writer changed nothing, and every other removal is the lead's, with Git's
    own commands (ignored files and index flags hide work from every status check, so an unattended
    deletion cannot be proven safe)."""
    deadline = time.monotonic() + budget_s
    main = _main_checkout(cwd)
    if not main or not owner_dir:
        return []
    rc, out, _ = _git(main, "rev-parse", "HEAD", timeout=5)
    main_head = out.strip() if rc == 0 else ""
    pending = []
    listing = _worktrees(main)
    if listing is None:     # a failed listing is not an empty one (review of 2.5.1, turn 15)
        return [{"path": main, "state": "Git could not list its worktrees: run `swctl wt list`"}]
    for wt in listing[1:]:
        path = wt.get("worktree", "")
        if not path or not _native(main, path) or wt.get("locked") or wt.get("bare"):
            continue
        meta = _wt_meta(owner_dir, path)
        if meta is None:
            continue    # another session's writer: not ours to report
        left = deadline - time.monotonic()
        if left < 2:
            pending.append({"path": path, "state": "not inspected (the hook ran out of time): run `swctl wt list`"})
            continue
        finished = bool(meta.get("finished"))
        st = _wt_state(main, wt, main_head, ignored=False, walk=not finished, deadline=deadline, timeout=20)
        if (st["dirty"] is None or st["ahead"] is None or st["hidden"] is None
                or (not finished and st["idle_s"] is None)):
            pending.append({"path": path, "state": "not fully inspected (a Git read failed or the hook ran out "
                                                   "of time): run `swctl wt list`"})
            continue
        if not st["dirty"] and st["ahead"] == 0 and not st["hidden"]:
            continue    # nothing to land
        # ours is reported once its writer finished (meta "finished"), or, without that record, after two
        # quiet minutes
        if not finished and st["idle_s"] < 120:
            continue
        parts = []
        if st["dirty"]:
            parts.append(f"{len(st['dirty'])} changed file(s)")
        if st["hidden"]:
            parts.append(f"{len(st['hidden'])} file(s) with index flags that hide edits")
        if st["ahead"]:
            parts.append(f"{st['ahead']} commit(s) not in {os.path.basename(main)}")
        idle = f", idle {_human_s(st['idle_s'])}" if st["idle_s"] is not None else ", writer finished"
        pending.append({"path": path, "state": ", ".join(parts) + idle})
    return pending


def _native_writer(path):
    """'finished' or 'running' for a native worktree's agent, from its owning session's subagent record
    (searched across Claude's transcript folders), or '' when there is none."""
    import glob  # noqa: lazy (see the module imports)
    name = os.path.basename(path.rstrip("/"))
    if not name.startswith("agent-") or not TAG_RE.fullmatch(name):
        return ""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
    for meta in glob.glob(os.path.join(base, "projects", "*", "*", "subagents", name + ".meta.json")):
        try:
            with open(meta, encoding="utf-8") as fh:
                return "finished" if json.load(fh).get("finished") else "running"
        except (OSError, ValueError):
            continue
    return ""


def wt_check(branch, base=None, cwd=None):
    """Run in the checkout a worktree's branch is about to land in: list the untracked files there that
    landing `base..branch` would overwrite or delete, and print the exact range to land. Git overwrites an
    ignored file without asking on a cherry-pick, and on a squash merge that is not a fast-forward even
    with --no-overwrite-ignore (Git 2.56.0, measured). So every commit being picked counts, not only the
    end result: a path one commit adds and a later one removes is written, then deleted. File-versus-folder
    clashes count too, and so does every untracked file, not only ignored ones, since a picked commit can
    change .gitignore before the next one writes (review of 2.5.1, turns 14-15). `base` defaults to the
    fork point (git merge-base HEAD branch); the lead lands exactly the printed range. Not seen: a file
    Git moves to a renamed folder (directory rename detection). Exit 1 when it lists anything."""
    import stat as stat_mod  # noqa: lazy (see the module imports)
    top_rc, top, err = _git(cwd or os.getcwd(), "rev-parse", "--show-toplevel", timeout=10)
    if top_rc != 0:
        raise SystemExit(f"swctl wt check: not in a git work tree ({err.strip()[:200]})")
    top = top.strip()
    rc, tip, _ = _git(top, "rev-parse", "--verify", "-q", branch + "^{commit}", timeout=10)
    if rc != 0:
        raise SystemExit(f"swctl wt check: {branch!r} is not a branch or commit here")
    tip = tip.strip()
    if base:
        rc, out, _ = _git(top, "rev-parse", "--verify", "-q", base + "^{commit}", timeout=10)
    else:
        rc, out, _ = _git(top, "merge-base", "HEAD", tip, timeout=60)
    if rc != 0 or not out.strip():
        raise SystemExit(f"swctl wt check: no base for {branch!r}: give the commit it started from with --base")
    base_sha = out.strip().split()[0]
    span = f"{base_sha}..{tip}"
    rc, out, err = _git(top, "rev-list", "--count", span, timeout=60)
    commits = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
    # every path any of those commits touches (a merge against each of its parents)
    rc, out, err = _git(top, "log", "--format=", "--name-only", "-z", "--no-renames", "-m", span, "--", timeout=300)
    if rc != 0 or commits is None:
        raise SystemExit(f"swctl wt check: git could not list the commits to land: {err.strip()[:200]}")
    incoming = {p for p in out.split("\0") if p}
    # every untracked entry, ignored or not; a wholly untracked folder is listed once, with a slash
    rc, out, err = _git(top, "ls-files", "-z", "--others", "--directory", timeout=300)
    if rc != 0:
        raise SystemExit(f"swctl wt check: git could not list the untracked files: {err.strip()[:200]}")
    untracked = [e for e in out.split("\0") if e]
    files = {e for e in untracked if not e.endswith("/")}
    folders = [e for e in untracked if e.endswith("/")]

    def non_dir(rel):          # lstat: a symlink to a folder is not a folder Git would keep
        try:
            return not stat_mod.S_ISDIR(os.lstat(os.path.join(top, rel)).st_mode)
        except OSError:
            return False

    def exists(rel):
        return os.path.lexists(os.path.join(top, rel))

    hits = set()
    for path in incoming:
        parts = path.split("/")
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts))]
        if path in files:
            hits.add(path)                                   # the same path
        hits.update(q for q in prefixes if q in files)       # an untracked file where the branch needs a folder
        under = path + "/"
        hits.update(e for e in untracked if e.startswith(under))   # untracked content where the branch puts a file
        for folder in folders:
            if not path.startswith(folder):
                continue
            # inside a wholly untracked folder: only what exists there now can be lost
            if exists(path):
                hits.add(path)
            hits.update(q for q in prefixes if len(q) >= len(folder.rstrip("/")) and exists(q) and non_dir(q))
    print(f"Checked {base_sha[:12]}..{tip[:12]}: {commits} commit(s) touching {len(incoming)} path(s).")
    if hits:
        print(f"{len(hits)} untracked file(s) or folder(s) here would be overwritten or deleted by landing them. "
              "Move each aside (or copy it out) before you land:")
        for h in sorted(hits)[:200]:
            print("  " + h)
        if len(hits) > 200:
            print(f"  … and {len(hits) - 200} more")
    else:
        print("No untracked file here would be overwritten or deleted by landing them.")
    print(f"Land exactly this range: git cherry-pick {span}")
    return 1 if hits else 0


def wt_list(cwd, as_json=False):
    """Every linked worktree with Git's own lock state. Whether its writer finished comes from the writer
    itself: a native agent's record, or T3's task (`t3_thread_read`); an unlocked worktree may still have
    a writer at work (T3 releases its creation lock before the writer starts; review of 2.5.1, turn 13)."""
    main = _main_checkout(cwd)
    if not main:
        raise SystemExit("swctl wt list: not in a git repository")
    rows = []
    listing = _worktrees(main)
    if listing is None:
        raise SystemExit(f"swctl wt list: Git could not list the worktrees of {main}")
    for wt in listing[1:]:
        path = wt.get("worktree", "")
        st = (_wt_state(main, wt) if os.path.isdir(path)
              else {"dirty": None, "ignored": None, "hidden": None, "ahead": None, "idle_s": 0})
        native = _native(main, path)
        rows.append({"path": path, "branch": wt.get("branch", "(detached)").replace("refs/heads/", ""),
                     "locked": bool(wt.get("locked")), "prunable": bool(wt.get("prunable")), "native": native,
                     "writer": (_native_writer(path) or "unknown") if native else "see its T3 task",
                     "changed": None if st["dirty"] is None else len(st["dirty"]),
                     "ignored": None if st["ignored"] is None else len(st["ignored"]),
                     "hidden": None if st["hidden"] is None else len(st["hidden"]),
                     "ahead": st["ahead"], "idle": _human_s(st["idle_s"] or 0)})
    if as_json:
        print(json.dumps(rows, indent=1))
        return
    if not rows:
        print(f"no linked worktrees in {main}")
        return
    for r in rows:
        lock = "locked" if r["locked"] else ("prunable" if r["prunable"] else "unlocked")
        print(f"{r['path']}  [{lock}] writer={r['writer']} branch={r['branch']} changed={r['changed']} "
              f"ignored={r['ignored']} hidden-by-flags={r['hidden']} ahead={r['ahead']} idle={r['idle']}")
    print("Before `git worktree remove`: it deletes ignored files and edits hidden by index flags without "
          "asking. Landing steps: the agent-teams skill, references/writers.md §5.")


# ----------------------------------------------------------------------------------------------
# swctl run: kept processes get a unit with a deadline and limits
# ----------------------------------------------------------------------------------------------

def _systemctl(*args, timeout=30):
    import subprocess  # noqa: lazy (see the module imports)
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _runs_dir():
    path = os.path.join(STATE, "runs")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


# Variables a kept service must not inherit: the cleanup's own call and session tags (a kept service
# carrying them would look like a leftover of that call or session), and the Compose project, which
# `--compose` sets itself (review of 2.5.1, HIGH and MEDIUM).
_ENV_DROP = {"SW_CALL", "SW_AGENT", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID", "CODEX_SESSION_ID",
             "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "COMPOSE_PROJECT_NAME"}


def _runtime_dir():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "software-workflows")


def _env_file(unit, extra=()):
    """A private EnvironmentFile for ONE start (unique name: concurrent starts never share one), built
    from the caller's environment minus _ENV_DROP, plus `extra` (key, value) pairs."""
    import secrets  # noqa: lazy (see the module imports)
    d = _runtime_dir()
    path = os.path.join(d, f"{unit}-{secrets.token_hex(4)}.env")
    # The path goes into ExecStopPost's command line, where systemd splits on spaces and expands $ and %:
    # only a plain path is accepted (review of 2.5.1, turn 15: a path with spaces became three operands
    # of `rm`, one of which could name an unrelated file)
    if not re.fullmatch(r"/[A-Za-z0-9/._-]+", path):
        raise SystemExit(f"swctl run: the runtime folder {d!r} is not a plain path (letters, digits, / . _ -); "
                         "systemd would split or expand it in the unit's commands. Set XDG_RUNTIME_DIR to one.")
    os.makedirs(d, mode=0o700, exist_ok=True)
    lines = []
    items = [(k, v) for k, v in os.environ.items() if k not in _ENV_DROP] + list(extra)
    for k, v in items:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or "\n" in v:
            continue
        # systemd 255 EnvironmentFile (measured): backslash escapes inside double quotes; `$` is literal
        lines.append(f'{k}="' + v.replace("\\", "\\\\").replace('"', '\\"') + '"')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _envf_user(unit, envf):
    """Whether a unit uses the environment file `envf`: 'running' (it runs with it), 'stopping' (it has it
    and is stopping: ExecStopPost may still need it), 'pending' (it has it, and a job is queued for it),
    'none' (the manager answered and no unit uses it: the unit is unknown, or inactive with no job, or
    runs with another start's file) or 'unknown' (no answer). Only 'none' allows removing the file."""
    rc, out, _ = _systemctl("show", "-p", "ActiveState", "-p", "Job", "-p", "EnvironmentFiles",
                            unit + ".service", timeout=10)
    if rc != 0:
        return "unknown"
    # systemd 255 (measured): one `EnvironmentFiles=<path> (ignore_errors=no)` line per file;
    # `ActiveState=inactive` with exit 0 for a unit it does not know; `Job=<id>` while a job is queued or
    # running, `Job=` otherwise. The path is everything before the LAST " (" (a path may hold " (").
    state, job, uses = "", "", False
    for line in out.splitlines():
        key, _, val = line.partition("=")
        if key == "ActiveState":
            state = val.strip()
        elif key == "Job":
            job = val.strip()
        elif key == "EnvironmentFiles" and val.rsplit(" (", 1)[0] == envf:
            uses = True
    if not state:
        return "unknown"
    if not uses:
        return "none"
    if state in ("active", "activating", "reloading"):
        return "running"
    if job:
        return "pending"
    return "none" if state in ("inactive", "failed") else "stopping"


def _reconcile_env_files(min_age_s=300):
    """The timer's backstop for `swctl run` environment files: one no unit uses any more (a start that
    failed with no answer from the manager, or an ExecStopPost that never ran) is removed."""
    d = _runtime_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        m = re.fullmatch(r"(swrun-[a-z0-9][a-z0-9-]{0,39})-[0-9a-f]{8}\.env", name)
        path = os.path.join(d, name)
        try:
            if not m or _now() - os.lstat(path).st_mtime < min_age_s:
                continue
        except OSError:
            continue
        if _envf_user(m.group(1), path) == "none":
            with contextlib_suppress(OSError):
                os.unlink(path)
                _log("run-env-removed", unit=m.group(1))


def run_start(name, cmd, ttl, mem, cpu, compose, cwd, compose_volumes=False):
    import subprocess  # noqa: lazy (see the module imports)
    if not NAME_RE.fullmatch(name or ""):
        raise SystemExit("swctl run: --name must be 1-40 chars of a-z, 0-9 and '-', starting with a letter or digit")
    if not cmd:
        raise SystemExit("swctl run: give the command after --")
    ttl_s = _parse_duration(ttl) if ttl else DEFAULT_TTL_S
    if ttl_s > MAX_TTL_S:
        raise SystemExit(f"swctl run: --ttl {ttl} is over the cap of {_human_s(MAX_TTL_S)} (named threat: forgotten "
                         "long-lived processes). Raise SW_RUN_MAX_TTL_S for this user if a workload needs longer.")
    unit = f"swrun-{name}"
    rc, out, _ = _systemctl("is-active", unit + ".service", timeout=5)
    if out.strip() in ("active", "activating", "reloading"):
        raise SystemExit(f"swctl run: {unit} is already running. `swctl run stop {name}` first, or pick another name.")
    _systemctl("reset-failed", unit + ".service", timeout=5)
    install_slice()
    log = os.path.join(_runs_dir(), unit + ".log")
    if compose and not NAME_RE.fullmatch(compose):
        raise SystemExit("swctl run: --compose must be a Compose project name (a-z, 0-9, '-')")
    envf = _env_file(unit, [("COMPOSE_PROJECT_NAME", compose)] if compose else [])
    props = [f"RuntimeMaxSec={ttl_s}", "KillMode=control-group", "TimeoutStopSec=20s",
             f"MemoryMax={mem or DEFAULT_MEM}", f"CPUQuota={cpu or DEFAULT_CPU}", "TasksMax=4096",
             f"EnvironmentFile={envf}", f"StandardOutput=append:{log}", f"StandardError=append:{log}"]
    if compose:
        # "-": a failed teardown is recorded but does not skip the commands after it (systemd.service)
        down = f"-/usr/bin/env docker compose -p {compose} down --remove-orphans --timeout 10" + (" -v" if compose_volumes else "")
        props.append(f"ExecStopPost={down}")
    props.append(f"ExecStopPost=-/bin/rm -f {envf}")   # the start's own environment file always goes
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or ""
    desc = f"swctl run {name} (ttl {_human_s(ttl_s)}, owner {session[:8] or 'user'})"
    argv = ["systemd-run", "--user", f"--unit={unit}", "--collect", "--quiet", "--slice=sw.slice",
            f"--description={desc}", f"--working-directory={os.path.realpath(cwd or os.getcwd())}"]
    for p in props:
        argv += ["-p", p]
    argv += ["--", *cmd]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        failed, why = r.returncode != 0, r.stderr.strip()[:400]
    except OSError as exc:
        failed, why = True, str(exc)
    except subprocess.TimeoutExpired:
        failed, why = True, "systemd-run timed out"
        if _envf_user(unit, envf) == "running":
            failed = False         # it started, with THIS start's environment file
    if failed:
        # The file holds the caller's environment, so it must not stay behind; but a unit that uses it
        # may still need it for ExecStopPost (Compose teardown), so it goes only when no unit uses it.
        # Otherwise the unit's own ExecStopPost removes it, or the timer's sweep once the unit is gone.
        user = _envf_user(unit, envf)
        if user == "none":
            with contextlib_suppress(OSError):
                os.unlink(envf)
        raise SystemExit(f"swctl run: systemd-run failed: {why}"
                         + ("" if user == "none" else f" (the unit's state is {user}; its environment file "
                            "goes when the unit stops)"))
    rec = {"name": name, "unit": unit, "session": session, "agent": os.environ.get("SW_AGENT", ""),
           "cwd": os.path.realpath(cwd or os.getcwd()), "program": os.path.basename(cmd[0]),
           "started": _now(), "ttl_s": ttl_s, "log": log, "compose": compose or ""}
    with open(os.path.join(_runs_dir(), unit + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    _log("run-start", unit=unit, ttl_s=ttl_s, session=session, compose=compose or "")
    print(f"started {unit}.service: stops by itself in {_human_s(ttl_s)}; memory {mem or DEFAULT_MEM}, "
          f"cpu {cpu or DEFAULT_CPU}. Log: {log}. `swctl run stop {name}` stops it now.")


def run_list(as_json=False):
    rc, out, _ = _systemctl("list-units", "--all", "--plain", "--no-legend", "--output=json", "swrun-*.service", timeout=10)
    try:
        units = json.loads(out) if out.strip() else []
    except ValueError:
        units = []
    rows = []
    for u in units:
        unit = u.get("unit", "").removesuffix(".service")
        rec = {}
        try:
            with open(os.path.join(_runs_dir(), unit + ".json"), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            pass
        left = ""
        if rec.get("started") and rec.get("ttl_s"):
            left = _human_s(max(0, rec["started"] + rec["ttl_s"] - _now()))
        rows.append({"name": unit.removeprefix("swrun-"), "state": u.get("active", ""), "sub": u.get("sub", ""),
                     "left": left, "owner": (rec.get("session") or "")[:8], "cwd": rec.get("cwd", ""),
                     "compose": rec.get("compose", "")})
    if as_json:
        print(json.dumps(rows, indent=1))
        return
    if not rows:
        print("no swctl run units")
    for r in rows:
        print(f"{r['name']}: {r['state']}/{r['sub']} left={r['left'] or '?'} owner={r['owner'] or '?'} "
              f"cwd={r['cwd']}{' compose=' + r['compose'] if r['compose'] else ''}")


def run_stop(name):
    unit = f"swrun-{name}.service"
    rc, _, err = _systemctl("stop", unit, timeout=60)
    if rc != 0:
        raise SystemExit(f"swctl run stop: {err.strip()[:300]}")
    _log("run-stop", unit=unit)
    print(f"stopped {unit}")


def run_logs(name, lines=80):
    path = os.path.join(_runs_dir(), f"swrun-{name}.log")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            data = fh.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        raise SystemExit(f"swctl run logs: no log at {path}")
    print("\n".join(data))


# ----------------------------------------------------------------------------------------------
# the timer's sweep
# ----------------------------------------------------------------------------------------------

def _session_alive(rec):
    """The session's agent process, as recorded at SessionStart, is still that same process."""
    st = _stat(int(rec.get("pid") or 0)) if str(rec.get("pid") or "").isdigit() else None
    return bool(st) and st[3] == "claude" and st[2] == rec.get("start")


def sweep(session=None, cwd="", codex=False, transcript="", ending=None):
    """Backstop pass. With --session: the final pass for a session that just ended (SessionEnd), given
    the ending process's pid, start time and cgroup (`ending`). Without: every 10 minutes from the
    timer. Only processes still in the session's own cgroup are stopped; never anything in sw.slice."""
    snap = Snapshot()
    done = []
    me = os.getuid()
    codex_pids = {p for p, st in snap.table.items() if st[3].startswith("codex") and st[1] == me
                  and "/sw.slice/" not in (_cgroup(p) or "/sw.slice/")}
    if session:
        ending = ending or {}
        scope = ending.get("cgroup") or ""
        if codex:
            # Codex's SessionEnd also fires after 30 idle minutes, and the thread can come back: only
            # orphans of calls no Codex process still runs, in the ending app-server's cgroup
            live = snap.live_calls(codex_pids, "CODEX_TOOL_CALL_ID")
            roots = [p for p, env in snap.orphans.items() if env.get("CODEX_THREAD_ID") == session
                     and env.get("CODEX_TOOL_CALL_ID") and env["CODEX_TOOL_CALL_ID"] not in live]
        else:
            rec = _session_record(session)
            if rec and (rec.get("pid"), rec.get("start")) != (ending.get("pid"), ending.get("start")):
                # resumed since this end: the record names a newer process; its orphans are its own
                _log("sweep-skipped", session=session, why="the session was resumed before this sweep ran")
                return []
            roots = [p for p, env in snap.orphans.items() if env.get("CLAUDE_CODE_SESSION_ID") == session]
        done += snap.stop(roots, {scope}, "left running when its session ended", {"session": session, "codex": codex})
        if cwd and not codex:
            try:
                owner_dir = os.path.splitext(transcript)[0] if transcript.endswith(".jsonl") else None
                for w in _tidy_worktrees(cwd, owner_dir=owner_dir):
                    _log("worktree-unlanded-at-session-end", path=w["path"], state=w["state"])
            except Exception as exc:
                _log("worktree-error", error=str(exc)[:300])
    else:
        # 1-2. Claude sessions recorded at SessionStart: a session that has ended leaves nothing behind in
        #      its cgroup; a live one loses orphans of calls that ended more than ORPHAN_AGE_S ago
        by_session = {}
        for p, env in snap.orphans.items():
            sid = env.get("CLAUDE_CODE_SESSION_ID")
            if sid:
                by_session.setdefault(sid, []).append(p)
        for sid, pids in by_session.items():
            rec = _session_record(sid)
            if not rec or not rec.get("cgroup"):
                continue   # started before 2.5.1, or outside a hook: not ours to judge
            if not _session_alive(rec):
                done += snap.stop(pids, {rec["cgroup"]}, "left running by a session that has ended", {"session": sid})
                continue
            live = snap.live_calls({int(rec["pid"])}, "SW_CALL")
            stale = [p for p in pids if snap.orphans[p].get("SW_CALL")
                     and snap.orphans[p]["SW_CALL"] not in live and snap.age(p) >= ORPHAN_AGE_S]
            if stale:
                done += snap.stop(stale, {rec["cgroup"]}, "left running after its call ended (timer)", {"session": sid})
        # 3. Codex calls: orphans in a Codex app-server's cgroup whose call no Codex process still runs
        live_codex = snap.live_calls(codex_pids, "CODEX_TOOL_CALL_ID") if codex_pids else set()
        by_thread = {}
        for p, env in snap.orphans.items():
            if (env.get("CODEX_TOOL_CALL_ID") and env.get("CODEX_THREAD_ID")
                    and env["CODEX_TOOL_CALL_ID"] not in live_codex and snap.age(p) >= CODEX_ORPHAN_AGE_S):
                by_thread.setdefault(env["CODEX_THREAD_ID"], []).append(p)
        for thread, pids in by_thread.items():
            rec = _session_record("codex-" + thread)
            if rec and rec.get("cgroup"):   # threads seen by our SessionStart hook only
                done += snap.stop(pids, {rec["cgroup"]}, "left running after its Codex call ended (timer)",
                                  {"session": thread, "codex": True})
        # 4. environment files of `swctl run` starts that no unit uses any more
        _reconcile_env_files()
        # (worktrees: nothing here deletes or prunes anything; that is the lead's, with Git's own commands)
        # 5. our Codex hooks still trusted? (an edit of ~/.codex/hooks.json can shift their keys)
        if os.path.exists(STABLE) and _codex_trust_stale():
            try:
                install_codex_hooks(sys.executable or "/usr/bin/python3")
                trust_codex_hooks(sys.executable or "/usr/bin/python3")
            except Exception as exc:
                _log("codex-trust", result=f"error {exc}"[:300])
    _log("sweep", session=session or "", stopped=len(done))
    return done


# ----------------------------------------------------------------------------------------------
# install: stable copy, swctl, the timer, the slice, Codex hooks
# ----------------------------------------------------------------------------------------------

def _ver(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def _write_if_changed(path, text, mode=0o644):
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def install_slice():
    text = ("[Unit]\nDescription=software-workflows: units started by swctl run\n\n[Slice]\n"
            f"MemoryMax={SLICE_MEM}\nCPUQuota={SLICE_CPU}\nTasksMax=16384\n")
    if _write_if_changed(os.path.join(SYSTEMD_USER, "sw.slice"), text):
        _systemctl("daemon-reload", timeout=30)


def _codex_entries(py):
    def entry(sub, timeout, matcher=None):
        e = {"hooks": [{"type": "command", "command": f"{py} {STABLE} hook {sub}", "timeout": timeout,
                        "statusMessage": MARK}]}
        if matcher:
            e["matcher"] = matcher
        return e
    return {"PostToolUse": entry("codex-post", 30, "Bash"), "Stop": entry("codex-stop", 30),
            "SessionEnd": entry("codex-session-end", 1), "SessionStart": entry("codex-session-start", 5)}


def _merge_codex_hooks(data, py):
    """Our three handlers, each in a group of its own at the end of its event; every other handler,
    including one that shares a group with an old copy of ours, stays exactly where it is."""
    hooks = data.setdefault("hooks", {})
    for event, ours in _codex_entries(py).items():
        groups = []
        for g in hooks.get(event, []):
            kept = [h for h in g.get("hooks", []) if h.get("statusMessage") != MARK]
            if kept:
                groups.append({**g, "hooks": kept})
            elif not g.get("hooks"):
                groups.append(g)
        groups.append(ours)
        hooks[event] = groups
    return data


def install_codex_hooks(py):
    """Merge our entries into ~/.codex/hooks.json (review of 2.5.1, HIGH): only our own handlers are
    replaced, the file keeps its mode, and a file changed by someone else between our read and our
    write is merged again from its new content instead of being overwritten."""
    import fcntl, shutil  # noqa: lazy (see the module imports)
    if not os.path.isdir(os.path.dirname(CODEX_HOOKS)):
        return False
    lock = os.open(os.path.join(os.path.dirname(CODEX_HOOKS), ".software-workflows-hooks.lock"),
                   os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)     # our installers, one at a time
        return _install_codex_hooks_locked(py, shutil)
    finally:
        os.close(lock)


def _install_codex_hooks_locked(py, shutil):
    displaced = f"{CODEX_HOOKS}.displaced-{os.getpid()}"
    for _attempt in range(3):
        raw, mode = b'{"hooks": {}}', 0o600
        if os.path.exists(CODEX_HOOKS):
            try:
                with open(CODEX_HOOKS, "rb") as fh:
                    raw = fh.read()
                mode = os.stat(CODEX_HOOKS).st_mode & 0o777
                data = json.loads(raw or b"{}")
            except (OSError, ValueError) as exc:
                _log("codex-hooks-unreadable", error=str(exc)[:200])
                return False
        else:
            data = {"hooks": {}}
        text = json.dumps(_merge_codex_hooks(data, py), indent=2) + "\n"
        if text.encode() == raw:
            return False
        if os.path.exists(CODEX_HOOKS) and not os.path.exists(CODEX_HOOKS + ".before-software-workflows"):
            shutil.copy2(CODEX_HOOKS, CODEX_HOOKS + ".before-software-workflows")
        tmp = f"{CODEX_HOOKS}.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        # Pin the file we are about to replace (a hard link to its inode) and compare THAT with what
        # we merged from: an edit saved after our read is caught here and merged again, never lost.
        # What remains is the instant between this check and the replace (documented).
        try:
            os.link(CODEX_HOOKS, displaced)
            with open(displaced, "rb") as fh:
                now = fh.read()
        except FileNotFoundError:
            now = b'{"hooks": {}}'
        if now != raw:          # changed under us: merge again from the new content
            os.unlink(tmp)
            with contextlib_suppress(FileNotFoundError):
                os.unlink(displaced)
            continue
        os.replace(tmp, CODEX_HOOKS)
        with contextlib_suppress(FileNotFoundError):
            os.unlink(displaced)
        return True
    _log("codex-hooks-busy", error="hooks.json kept changing; not written")
    return False


def _codex_rpc(calls, timeout_s=20.0):
    """One short-lived `codex app-server --listen stdio://`, these JSON-RPC calls in order. Returns the
    results (None where a call failed or timed out). The app-server runs in its own process group,
    which is always killed at the end."""
    import shutil, subprocess  # noqa: lazy (see the module imports)
    codex = shutil.which("codex") or os.path.join(HOME, ".local", "bin", "codex")
    if not os.path.exists(codex):
        return [None] * len(calls)
    try:
        proc = subprocess.Popen([codex, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return [None] * len(calls)
    end = time.monotonic() + timeout_s
    buf = b""
    stdin, stdout = proc.stdin, proc.stdout
    assert stdin is not None and stdout is not None

    def send(msg):
        stdin.write((json.dumps(msg) + "\n").encode())
        stdin.flush()

    def recv(want):
        nonlocal buf
        import select
        while time.monotonic() < end:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == want:
                    return msg
            ready = select.select([stdout], [], [], max(0.0, min(1.0, end - time.monotonic())))[0]
            if ready:
                chunk = os.read(stdout.fileno(), 1 << 16)
                if not chunk:
                    return None
                buf += chunk
        return None

    results = []
    try:
        send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "software-workflows-cleanup",
                                                                          "version": VERSION}}})
        if not recv(0):
            return [None] * len(calls)
        send({"method": "initialized"})
        for i, (method, params) in enumerate(calls, 1):
            send({"id": i, "method": method, "params": params})
            msg = recv(i)
            results.append(msg.get("result") if msg and "result" in msg else None)
    except (OSError, ValueError):
        results += [None] * (len(calls) - len(results))
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
    return results


def trust_codex_hooks(py):
    """Codex runs a user-level hook only after it is trusted (hooks.state."<key>".trusted_hash in
    ~/.codex/config.toml; measured on 0.171.0: an untrusted hook is skipped without a word). Trust OUR
    entries, and only those whose command is exactly ours, through Codex's own config writer."""
    expected = {event[0].lower() + event[1:]: e["hooks"][0]["command"] for event, e in _codex_entries(py).items()}
    ours = set(expected.values())

    def listing():
        result = _codex_rpc([("hooks/list", {"cwds": [HOME]})])[0]
        try:
            return result["data"][0]["hooks"]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            return None

    def mine(hooks):
        return [h for h in hooks if h.get("statusMessage") == MARK and h.get("command") in ours
                and os.path.realpath(h.get("sourcePath") or "") == os.path.realpath(CODEX_HOOKS)]

    hooks = listing()
    if hooks is None:
        _log("codex-trust", result="hooks/list failed")
        return "unknown"
    pending = {h["key"]: h["currentHash"] for h in mine(hooks) if h.get("trustStatus") in ("untrusted", "modified")}
    if pending:
        res = _codex_rpc([("config/batchWrite", {
            "edits": [{"keyPath": "hooks.state", "value": {k: {"trusted_hash": v} for k, v in pending.items()},
                       "mergeStrategy": "upsert"}],
            "filePath": None, "expectedVersion": None, "reloadUserConfig": True})])[0]
        if not res or res.get("status") != "ok":
            _log("codex-trust", result="config/batchWrite failed", keys=sorted(pending))
            return "failed"
    # Success means: each expected event has exactly one of our handlers, from our file, enabled and
    # trusted, as Codex itself reports them after the write (review of 2.5.1, turn 10).
    hooks = listing() or []
    problems = []
    for event, command in expected.items():
        got = [h for h in mine(hooks) if h.get("eventName") == event and h.get("command") == command]
        if len(got) != 1:
            problems.append(f"{event}: {len(got)} handlers")
        elif not got[0].get("enabled", True):
            problems.append(f"{event}: disabled")
        elif got[0].get("trustStatus") != "trusted":
            problems.append(f"{event}: {got[0].get('trustStatus')}")
    if problems:
        _log("codex-trust", result="incomplete", problems=problems)
        return "incomplete: " + "; ".join(problems)
    _log("codex-trust", result="ok", trusted_now=sorted(pending), ours=len(expected))
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        with open(os.path.join(STATE, "codex-hooks-verified"), "w", encoding="utf-8") as fh:
            fh.write(str(os.path.getmtime(CODEX_HOOKS)))
    except OSError:
        pass
    return "ok"


def _codex_trust_stale():
    """True when ~/.codex/hooks.json changed since our entries were last verified as trusted."""
    try:
        with open(os.path.join(STATE, "codex-hooks-verified"), encoding="utf-8") as fh:
            return float(fh.read().strip()) != os.path.getmtime(CODEX_HOOKS)
    except (OSError, ValueError):
        return os.path.exists(CODEX_HOOKS)


def install(quiet=False, force=False):
    """Install or refresh. The success stamp is written only when the timer is active and our Codex
    hooks are trusted (or there is no Codex): a partial install is retried at the next session start,
    and `swctl install` (force) always runs in full (review of 2.5.1, MEDIUM)."""
    import hashlib, shlex  # noqa: lazy (see the module imports)
    if not sys.platform.startswith("linux"):
        return
    me = os.path.abspath(__file__)
    with open(me, encoding="utf-8") as fh:
        body = fh.read()
    stamp = os.path.join(SHARE, "installed")
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    try:
        with open(stamp, encoding="utf-8") as fh:
            have = fh.read().split()
        if os.path.exists(STABLE) and have and not force:
            if have[-1] == digest:
                return
            # never downgrade: an older session starting must not replace a newer stable copy
            if len(have) == 2 and _ver(have[0]) > _ver(VERSION):
                return
    except OSError:
        pass
    py = sys.executable or "/usr/bin/python3"
    _write_if_changed(STABLE, body, 0o755)
    bindir = os.path.join(HOME, ".local", "bin")
    _write_if_changed(os.path.join(bindir, "swctl"),
                      f"#!/bin/sh\nexec {shlex.quote(py)} {shlex.quote(STABLE)} \"$@\"\n", 0o755)
    service = ("[Unit]\nDescription=software-workflows cleanup sweep (leftover processes, idle worktrees)\n\n"
               f"[Service]\nType=oneshot\nExecStart={py} {STABLE} sweep\nTimeoutStartSec=180\nNice=10\n")
    timer = ("[Unit]\nDescription=software-workflows cleanup sweep every 10 minutes\n\n[Timer]\n"
             "OnBootSec=5min\nOnUnitActiveSec=10min\nAccuracySec=1min\n\n[Install]\nWantedBy=timers.target\n")
    changed = _write_if_changed(os.path.join(SYSTEMD_USER, "sw-sweep.service"), service)
    changed |= _write_if_changed(os.path.join(SYSTEMD_USER, "sw-sweep.timer"), timer)
    install_slice()
    if changed:
        _systemctl("daemon-reload", timeout=30)
    _systemctl("enable", "--now", "sw-sweep.timer", timeout=30)
    _rc, active, _ = _systemctl("is-active", "sw-sweep.timer", timeout=10)
    timer_ok = active.strip() == "active"
    install_codex_hooks(py)
    trust = trust_codex_hooks(py) if os.path.exists(CODEX_HOOKS) else "no Codex"
    complete = timer_ok and trust in ("ok", "no Codex")
    if complete:
        _write_if_changed(stamp, f"{VERSION} {digest}\n")
    _log("installed" if complete else "install-incomplete", version=VERSION, digest=digest,
         timer=active.strip(), codex_trust=trust)
    if not quiet:
        print(f"{'installed' if complete else 'INCOMPLETE install of'} swclean {VERSION}: {STABLE}, "
              f"~/.local/bin/swctl, sw.slice; sw-sweep.timer {active.strip() or 'unknown'}; Codex hooks: {trust}")
    if not complete and not quiet:
        raise SystemExit(1)


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------

USAGE = """swctl: software-workflows cleanup
  swctl run --name N [--ttl 2h] [--mem 8G] [--cpu 400%] [--compose PROJECT [--compose-volumes]] [--cwd DIR] -- CMD...
  swctl run list | stop N | logs N [-n LINES]
  swctl wt list [DIR] [--json]      (land a worktree with git: see the agent-teams skill, references/writers.md)
  swctl wt check BRANCH [--base SHA]  (in the checkout it lands in: untracked files landing BASE..BRANCH would
                                       overwrite or delete, and the exact range to land; BASE defaults to the fork point)
  swctl leftovers            list re-parented processes that carry a session or call tag
  swctl sweep                run the timer's pass now
  swctl install              (re)install the stable copy, swctl, the timer, the slice and the Codex hooks"""


def _cli_run(args):
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return
    if args[0] == "list":
        return run_list("--json" in args)
    if args[0] == "stop" and len(args) == 2:
        return run_stop(args[1])
    if args[0] == "logs" and len(args) >= 2:
        n = int(args[args.index("-n") + 1]) if "-n" in args else 80
        return run_logs(args[1], n)
    if "--" not in args:
        raise SystemExit("swctl run: put the command after --\n" + USAGE)
    i = args.index("--")
    opts, cmd = args[:i], args[i + 1:]
    kv = {"--name": None, "--ttl": None, "--mem": None, "--cpu": None, "--compose": None, "--cwd": None}
    flags = set()
    j = 0
    while j < len(opts):
        if opts[j] in kv and j + 1 < len(opts):
            kv[opts[j]] = opts[j + 1]
            j += 2
        elif opts[j] == "--compose-volumes":
            flags.add(opts[j])
            j += 1
        else:
            raise SystemExit(f"swctl run: unknown option {opts[j]}\n" + USAGE)
    run_start(kv["--name"], cmd, kv["--ttl"], kv["--mem"], kv["--cpu"], kv["--compose"], kv["--cwd"],
              "--compose-volumes" in flags)


def _cli_wt(args):
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return
    if args[0] == "list":
        rest = [a for a in args[1:] if a != "--json"]
        return wt_list(rest[0] if rest else os.getcwd(), "--json" in args)
    if args[0] == "check" and len(args) in (2, 4) and (len(args) == 2 or args[2] == "--base"):
        sys.exit(wt_check(args[1], args[3] if len(args) == 4 else None))
    raise SystemExit(USAGE)


def _cli_leftovers():
    snap = Snapshot()
    if not snap.orphans:
        print("no re-parented processes carry a session or call tag")
    for p, env in sorted(snap.orphans.items()):
        st = snap.table.get(p)
        print(f"{p} {st[3] if st else '?'} age={_human_s(snap.age(p))} session={env.get('CLAUDE_CODE_SESSION_ID', '')[:8]}"
              f" call={env.get('SW_CALL', '')[-8:]} codex_thread={env.get('CODEX_THREAD_ID', '')[:8]}"
              f" codex_call={env.get('CODEX_TOOL_CALL_ID', '')[-8:]} cgroup={(_cgroup(p) or '').rsplit('/', 1)[-1]}")


def main(argv):
    if not argv:
        print(USAGE)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "hook":
        if not sys.platform.startswith("linux"):
            return 0
        sub = args[0] if args else ""
        data = _read_payload()
        try:
            {"pre": lambda: hook_pre(data),
             "post": lambda: hook_post(data),
             "subagent-stop": lambda: hook_subagent_stop(data),
             "stop": lambda: hook_stop(data),
             "session-end": lambda: hook_session_end(data),
             "session-start": lambda: hook_session_start(data),
             "codex-post": lambda: hook_post(data, codex=True),
             "codex-stop": lambda: hook_stop(data, codex=True),
             "codex-session-end": lambda: hook_session_end(data, codex=True),
             "codex-session-start": lambda: hook_codex_session_start(data)}.get(sub, lambda: None)()
        except Exception as exc:   # a hook never breaks the agent's call
            _log("hook-error", hook=sub, error=f"{type(exc).__name__}: {exc}"[:300])
        return 0
    if cmd == "run":
        _cli_run(args)
    elif cmd == "wt":
        _cli_wt(args)
    elif cmd == "leftovers":
        _cli_leftovers()
    elif cmd == "sweep":
        session = args[args.index("--session") + 1] if "--session" in args else None
        cwd = args[args.index("--cwd") + 1] if "--cwd" in args else ""
        transcript = args[args.index("--transcript") + 1] if "--transcript" in args else ""
        ending = None
        if "--pid" in args:
            def arg(name):
                return args[args.index(name) + 1] if name in args else ""
            ending = {"pid": int(arg("--pid") or 0), "start": int(arg("--start") or 0), "cgroup": arg("--cgroup")}
        res = sweep(session, cwd, "--codex" in args, transcript, ending)
        if sys.stdout.isatty() or "--verbose" in args:
            print(f"stopped {len(res)} process(es)" + (f": {_summary(res)}" if res else ""))
    elif cmd == "install":
        install(force=True)
    else:
        print(USAGE)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
