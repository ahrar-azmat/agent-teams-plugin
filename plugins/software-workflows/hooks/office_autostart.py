#!/usr/bin/env python3
"""PreToolUse on the plugin's Codex tools: keep the Codex Office running, and show it once per session.

The Codex Office (office/office.py) is the live view of every Codex run: a pixel office in the browser, where
clicking a worker opens its whole conversation.
- On a session's first Codex call this hook starts the office in the background, replacing one started by an older
  plugin version or one whose Python was removed. It also opens the office in the browser, once per Claude Code
  session.
- On later calls it starts the office again only if it has stopped. Every 10 minutes it also has the office checked
  in full (that it answers, and that it is this plugin's own).
- It prints nothing, so it adds nothing to Claude's context and costs no tokens. It returns at once: the start runs
  detached.
- Until a start of the session's has the office running, each Codex call tries again, and the first one that works
  opens it. A failed start is told to the person once per session, with its reason or its log
  (office/autostart.log in the plugin's data directory).

Settings:

  office (the plugin's own setting: /plugin, Installed, software-workflows, Configure options; or /config;
  Claude Code passes it to this hook as CLAUDE_PLUGIN_OPTION_OFFICE)
                             open        start the office and open it in the browser once per session (default)
                             background  start the office but never open the browser; visit its address yourself
                             off         never start it from this hook; /software-workflows:office still does
  SOFTWARE_WORKFLOWS_OFFICE  the same three values; when set (under "env" in ~/.claude/settings.json, or exported
                             before starting Claude Code) it overrides the plugin setting for those sessions
  CODEX_OFFICE_PORT          the first port the office tries (default 8765; the next 19 are tried if it is taken)

Python 3.9+, standard library only.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time

MODES = ("open", "background", "off")
HERE = os.path.dirname(os.path.abspath(__file__))
OFFICE_DIR = os.path.join(os.path.dirname(HERE), "office")
OFFICE = os.path.join(OFFICE_DIR, "office.py")
DEFAULT_DATA = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "data", "software-workflows-agent-teams")
MARKER = re.compile(r"[0-9a-f]{32}(\.told)?")
MARKER_AGE_S, SWEEP_S, FULL_CHECK_S = 30 * 24 * 3600, 24 * 3600, 600
LOG_MAX_BYTES = 1 << 20  # a start writes ~300 bytes: about 3,000 starts, then the log begins again


def own_dir(path, private):
    """Create path if missing; refuse anything but this user's own real directory (a symlink is refused, so nothing
    below is ever reached through one). A private one (the session markers) must also be closed to others."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or private and info.st_mode & 0o077:
        raise OSError("not a private directory of this user: " + path)


def digest(session):
    return hashlib.sha256(session.encode()).hexdigest()[:32]


def claim(sessions, session):
    """This session's marker, open and locked, when this call is the one to start the office for the session (and
    open it); else None. None means either:
    - a start under way holds the lock, or
    - the session's start is done (office.py --session-marker writes "done" into the marker once the office runs).

    The start inherits the lock (pass_fds), and the kernel drops it when that process exits. So a start that failed or
    died, and a hook that died before spawning one, leave the marker unlocked and not done: the next call takes it
    over. Two calls at once open the office once."""
    path = os.path.join(sessions, digest(session))
    for _ in range(3):  # the path swept and made again between open and lock: claim the file it names now
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info, named = os.fstat(fd), os.lstat(path)
            if (info.st_ino, info.st_dev) != (named.st_ino, named.st_dev):
                os.close(fd)
                continue
            mine = stat.S_ISREG(info.st_mode) and info.st_size == 0
            if mine:
                os.utime(path)  # a marker taken over is in use again, so no sweep counts it old
        except OSError:  # BlockingIOError: the start under way holds it; FileNotFoundError: swept meanwhile
            mine = False
        if not mine:
            os.close(fd)
            return None
        sweep(sessions)
        return fd
    return None


def sweep(sessions):
    """Once a day, remove markers and notes last used MARKER_AGE_S ago or more. Only regular files named as markers
    are removed, and a marker only while no start holds it. Best effort: a sweep that cannot run never stands in
    the way of a start."""
    stamp, now = os.path.join(sessions, ".swept"), time.time()
    try:
        try:
            if now - os.lstat(stamp).st_mtime < SWEEP_S:
                return
        except FileNotFoundError:
            pass
        os.close(os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600))
        os.utime(stamp)
        entries = os.listdir(sessions)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(sessions, entry)
        try:
            info = os.lstat(path)
            if not (MARKER.fullmatch(entry) and stat.S_ISREG(info.st_mode) and now - info.st_mtime > MARKER_AGE_S):
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # held by a start or a claim: in use, kept
                # Judged again under the lock: a claim taken over (touched) or completed meanwhile is in use, and
                # the path must still name the file locked.
                held, named = os.fstat(fd), os.lstat(path)
                if (held.st_ino, held.st_dev) == (named.st_ino, named.st_dev) and now - held.st_mtime > MARKER_AGE_S:
                    os.unlink(path)
            finally:
                os.close(fd)
        except OSError:
            pass


def alive(state):
    """Whether the office recorded in server.json is still that process: its pid, and the start time recorded with
    it, so a pid the system gave to another process since does not count. That it answers, and that it is this
    plugin's, is checked by a start (each session's first call, and every FULL_CHECK_S)."""
    try:
        with open(os.path.join(state, "server.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        pid, started = int(info["pid"]), info["pid_start"]
        if pid <= 0 or not started:
            return False
        os.kill(pid, 0)
        out = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
        return "_".join(out.split()) == started
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def full_check_due(sessions, session):
    """Whether this session's office was last checked in full FULL_CHECK_S ago or more; if so, that check is taken
    now (its marker's time is the last one). Without a session there is no clock, and alive() alone decides."""
    if not session:
        return False
    path = os.path.join(sessions, digest(session))
    try:
        if time.time() - os.lstat(path).st_mtime < FULL_CHECK_S:
            return False
        os.utime(path)
        return True
    except OSError:
        return False


def once(sessions, session):
    """True the first time in a session (its .told note): the person sees one message about the office per session."""
    try:
        os.close(os.open(os.path.join(sessions, digest(session) + ".told"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        return True
    except OSError:  # FileExistsError: told already
        return False


def told(state, sessions, session):
    """The person's message about a failed automatic start, once per session; None when there is none to give."""
    try:
        with open(os.path.join(state, "autostart.failed"), encoding="utf-8") as fh:
            info = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        info = None
    if not once(sessions, session):
        return None
    error = info.get("error") if isinstance(info, dict) else None
    return ("software-workflows: the Codex Office did not start%s; its log is %s. It is tried again on the next "
            "Codex call; /software-workflows:office starts it by hand."
            % (" (%s)" % error if error else "", os.path.join(state, "autostart.log")))


def failed(state, error):
    """Record a start that could not be spawned, which therefore reports nothing itself. told() then gives it to this
    session now, and to every other session at its next call. A start that has the office running clears it."""
    try:
        fd = os.open(os.path.join(state, "autostart.failed"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                     0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"time": time.time(), "log": os.path.join(state, "autostart.log"), "error": error}, fh)
    except OSError:
        pass


def spawn(argv, state, keep):
    """Start argv detached, its output appended to <state>/autostart.log (begun again past LOG_MAX_BYTES). keep: the
    descriptors it inherits (the session's locked marker)."""
    path = os.path.join(state, "autostart.log")
    try:
        fresh = os.lstat(path).st_size > LOG_MAX_BYTES  # one start writes a few lines; keep the latest only
    except FileNotFoundError:
        fresh = False
    log = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_TRUNC if fresh else os.O_APPEND), 0o600)
    try:
        # Isolated, in the office's own folder: nothing of the project the session works in is ever imported.
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=OFFICE_DIR,
                         start_new_session=True, close_fds=True, pass_fds=keep)
    finally:
        os.close(log)


def main():
    override = os.environ.get("SOFTWARE_WORKFLOWS_OFFICE", "").strip()
    name = "SOFTWARE_WORKFLOWS_OFFICE" if override else "the plugin's office setting"
    mode = (override or os.environ.get("CLAUDE_PLUGIN_OPTION_OFFICE", "").strip() or "open").lower()
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    session = payload.get("session_id") if isinstance(payload, dict) else None
    session = session if isinstance(session, str) and session else None
    if mode == "off":
        return 0
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or DEFAULT_DATA
    state, sessions = os.path.join(data, "office"), os.path.join(data, "office", "sessions")
    try:
        os.makedirs(data, exist_ok=True)
        own_dir(state, private=False)
        own_dir(sessions, private=True)
    except OSError as exc:
        return say(session, "software-workflows: the Codex Office cannot keep its state in %s (%s), so it was not "
                            "started; /software-workflows:office starts it by hand." % (state, exc))
    if mode not in MODES:
        return say(session and once(sessions, session),
                   "software-workflows: %s is %r, not one of %s, so the Codex Office was not started. Change it in "
                   "/plugin (software-workflows, Configure options) or in your environment."
                   % (name, mode, ", ".join(MODES)))
    message = told(state, sessions, session) if session else None
    lock = claim(sessions, session) if session else None
    try:
        if lock is None and alive(state) and not full_check_due(sessions, session):
            return emit(message)
        argv = [sys.executable, "-I", OFFICE, "start", "--data", data, "--autostart"]
        if lock is not None:
            argv += (["--open"] if mode == "open" else []) + ["--session-marker", os.path.join(sessions, digest(session))]
        try:
            spawn(argv, state, () if lock is None else (lock,))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # No start ran, so nothing else reports it. The claim is dropped below, and the next call tries again.
            failed(state, "%s: %s" % (type(exc).__name__, exc))
            message = message or (told(state, sessions, session) if session else None)
        return emit(message)
    finally:
        if lock is not None:
            os.close(lock)


def say(first, text):
    """Tell the person (systemMessage is shown to them, not added to Claude's context) when first is true."""
    return emit(text if first else None)


def emit(text):
    if text:
        print(json.dumps({"systemMessage": text}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a viewer must never stand in the way of the Codex call it shows
        sys.exit(0)
