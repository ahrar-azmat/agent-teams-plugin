"""Python 3.9 JSONL client. The client never imports the ledger; it opens one read-only only to
see what an older release's daemon has in flight before stopping it (lead_work_in_flight)."""
import contextlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid


class CoordinatorError(RuntimeError):
    def __init__(self, code, message, details=None, operational=False, *, answered=False):
        super().__init__(message)
        self.code, self.details, self.operational = code, details or {}, operational
        # Local validation failures also use refusal codes. Only a validated
        # daemon error envelope proves that the coordinator answered this call.
        self.answered = answered

    def envelope(self):
        return {"code": self.code, "message": str(self), "details": self.details,
                "kind": "failure" if self.operational else "refusal"}


def stop_control_command(command):
    """A whole shell command containing only the documented stop invocation."""
    if not isinstance(command, str) or any(mark in command for mark in (";", "|", "`", "$(", "<", ">", "\n")):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if words[:1] == ["cd"]:
        if len(words) < 4 or words[2] != "&&" or Path(words[1]).resolve() != Path(__file__).resolve().parents[1]:
            return False
        words = words[3:]
    if not words or not Path(words.pop(0)).name.lower().startswith("python"):
        return False
    if words[:1] == ["-B"]:
        words.pop(0)
    if words[:2] != ["-m", "coordinator"]:
        return False
    words = words[2:]
    if words[:1] == ["--state"] and len(words) >= 2:
        words = words[2:]
    if words[:2] != ["daemon", "stop"]:
        return False
    words = words[2:]
    if "--park" in words:
        words.remove("--park")
    if words[:1] == ["--state"] and len(words) == 2:
        words = []
    return not words


def state_root(value=None):
    return Path(value or os.environ.get("SOFTWARE_WORKFLOWS_STATE") or
                "~/.local/state/software-workflows").expanduser().absolute()


def private_parents(path):
    """Create missing components privately; never chmod an existing ancestor."""
    path = checked_path(path)
    missing = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise CoordinatorError("STATE_SYMLINK", "State ancestor must not be a symlink")
        missing.append(current)
        current = current.parent
    info = current.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
        raise CoordinatorError("STATE_PERMISSIONS", "State ancestor must be an owned directory")
    for component in reversed(missing):
        try:
            component.mkdir(mode=0o700)
        except FileExistsError:
            pass  # Another starter may have created it; validate that result.
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CoordinatorError("STATE_PERMISSIONS", "Created state ancestors must be owned and private")


def checked_path(path):
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            # macOS provides these root-owned system aliases. User-provided
            # aliases must not hide state/runtime ownership checks.
            system_alias = (component != path and info.st_uid == 0 and
                            str(component) in ("/tmp", "/var") and
                            str(component.resolve()) == "/private" + str(component))
            if not system_alias:
                raise CoordinatorError("STATE_SYMLINK", "State/runtime ancestors must not be user-provided symlinks")
        elif not stat.S_ISDIR(info.st_mode):
            raise CoordinatorError("STATE_PERMISSIONS", "State/runtime ancestors must be directories")
        elif info.st_uid not in (0, os.getuid()):
            raise CoordinatorError("STATE_PERMISSIONS", "State/runtime ancestors must be owned directories")
    return path.resolve()


def validate_state(path, create=False):
    path = checked_path(path)
    plugin = Path(__file__).resolve().parents[1]
    if path == plugin or plugin in path.parents:
        raise CoordinatorError("STATE_IN_PLUGIN", "State must be outside the plugin installation")
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise CoordinatorError("STATE_IN_CHECKOUT", "State must be outside every checkout")
    if create:
        private_parents(path)
    if path.exists():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CoordinatorError("STATE_PERMISSIONS", "State must be an owned directory with mode 0700")
    return path


def daemon_python():
    candidates = [os.environ.get("SOFTWARE_WORKFLOWS_PYTHON"), sys.executable]
    candidates += [shutil.which("python3." + str(n)) for n in range(15, 10, -1)]
    candidates += ["/opt/homebrew/opt/python@3.15/bin/python3.15", shutil.which("python3")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run([candidate, "-B", "-c",
                                     "import sys;raise SystemExit(sys.version_info < (3,11))"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise CoordinatorError("PYTHON_VERSION", "Daemon requires Python 3.11 or newer")


def venv_fds():
    """Only inherit the launcher's verified lock on this interpreter's venv."""
    value = os.environ.get("CODEX_ORACLE_VENV_LOCK")
    if value is None:
        return ()
    try:
        fd = int(value)
        if fd < 3 or not os.path.samestat(os.fstat(fd), (Path(sys.prefix) / ".in-use").stat()):
            raise ValueError("not this venv")
        return (fd,)
    except (ValueError, OSError) as exc:
        raise CoordinatorError("VENV_LOCK", "Cannot preserve the launcher's interpreter lifetime lock") from exc


def clean_environment(source=None):
    """Detach from the launching Claude session and its push gate, keeping user configuration."""
    environment = dict(os.environ if source is None else source)
    for option in ("WRITER_MCP_SERVERS", "WRITER_PARK_IDLE_SECONDS", "MEMORY_WARN_RATIO", "MEMORY_CRITICAL_RATIO",
                   "MEMORY_HYSTERESIS", "OWNER_JOURNAL_BYTES"):
        key = "CLAUDE_PLUGIN_OPTION_" + option
        if key in environment:
            environment.setdefault("SOFTWARE_WORKFLOWS_" + option, environment[key])
    for key in tuple(environment):
        if key in {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_ENV_FILE", "CLAUDE_EFFORT", "CLAUDE_PROJECT_DIR",
                   "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_JOB_DIR"} or key.startswith(
                       ("CLAUDE_CODE_", "CODEX_GATE_", "CLAUDE_PLUGIN_OPTION_")):
            del environment[key]

    def is_farm(directory):
        return os.path.isfile(os.path.join(directory, ".codex-gate-farm"))

    def is_shim(directory):
        # Traverse the directory before '..': a symlink to a shim has the shim's parent.
        return (os.path.isfile(os.path.join(directory, "git")) and
                os.path.isfile(os.path.join(directory, os.pardir, "push_gate.py")))

    if "PATH" in environment:
        environment["PATH"] = os.pathsep.join(d for d in environment["PATH"].split(os.pathsep)
                                             if not is_farm(d or ".") and not is_shim(d or "."))
    if environment.get("GIT_EXEC_PATH") and is_farm(environment["GIT_EXEC_PATH"]):
        del environment["GIT_EXEC_PATH"]
    return environment


def launch_environment():
    environment = clean_environment()
    plugin = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = plugin + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    return environment


def superseded(running):
    """Whether a running daemon is an older release than this client's code; one that reports no
    version predates 2.4.0. An unreadable version on either side never retires anything."""
    from .runtime import plugin_version
    mine = plugin_version()
    try:
        return mine is not None and (running is None or
                                     tuple(map(int, running.split("."))) < tuple(map(int, mine.split("."))))
    except (AttributeError, ValueError):
        return False


def _held(path):
    import fcntl
    try:
        with open(path, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    except FileNotFoundError:
        return False
    except BlockingIOError:
        return True
    return False


# Held by the one process starting or replacing the daemon, from its last look at the running daemon
# through the start of its own; the lead hooks wait while it is held instead of going unrecorded.
UPGRADE_LOCK = "upgrade.lock"
LEAD_OPERATIONS = ("lead-edit", "restore", "lead-command")


def lead_work_in_flight(state):
    """What a daemon from before 2.4.0 has in flight that its stop guard does not wait for: lead edits,
    hooks and commands, and publications, read from its ledger without writing. {class: count}."""
    import sqlite3
    from urllib.parse import quote
    found = {}
    try:
        db = sqlite3.connect("file:" + quote(str(Path(state) / "ledger.sqlite")) + "?mode=ro", uri=True, timeout=5)
        try:
            rows = db.execute("SELECT kind, json_extract(data,'$.state'), json_extract(data,'$.kind'), "
                              "json_extract(data,'$.deadline'), json_extract(data,'$.phase') FROM records "
                              "WHERE kind IN ('lead_hook','operation','publication')").fetchall()
        finally:
            db.close()
    except sqlite3.Error as exc:
        return {"unreadable ledger (" + type(exc).__name__ + ")": 1}
    now = time.time()
    for kind, status, operation, deadline, phase in rows:
        if kind == "operation" and operation in LEAD_OPERATIONS and status in ("prepared", "allowed", "running"):
            name = "lead " + operation
        elif kind == "lead_hook" and status in ("received", "observed") and (deadline is None or deadline > now):
            name = "lead hook"
        elif kind == "publication" and phase not in (None, "complete", "abandoned"):
            name = "publication"
        else:
            continue
        found[name] = found.get(name, 0) + 1
    return found


class Client:
    def __init__(self, state=None, timeout=30, deadline=None):
        self.state = validate_state(state_root(state))
        self.timeout = timeout
        # time.time() by which a call must end, whatever it waits on: a socket timeout bounds only
        # one operation, and connect, send and each receive would otherwise get the whole budget.
        self.deadline = deadline

    def remaining(self):
        if self.deadline is None:
            return self.timeout
        left = self.deadline - time.time()
        if left <= 0:
            raise socket.timeout("coordinator call deadline passed")
        return min(self.timeout, left)

    def call(self, method, params=None, key=None):
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "method": method, "params": params or {}}
        if key is not None:
            request["key"] = key
        from .runtime import discover
        address = discover(self.state)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.remaining())
            connection.connect(str(address))
            connection.settimeout(self.remaining())
            connection.sendall((json.dumps(request, ensure_ascii=True) + "\n").encode())
            chunks = []
            while True:
                connection.settimeout(self.remaining())
                chunk = connection.recv(65536)
                chunks.append(chunk)
                if not chunk or b"\n" in chunk:
                    break
            line = b"".join(chunks)
            line = line[:line.index(b"\n") + 1] if b"\n" in line else line
            if not line:
                raise CoordinatorError("RPC_UNCERTAIN", "Connection ended without a result; reconcile before retrying",
                                       {"id": request_id, "key": key}, True)
            response = json.loads(line)
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise CoordinatorError("RPC_ID", "Mismatched RPC response", operational=True)
        if "error" in response:
            error = response["error"]
            if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                    or not isinstance(error.get("message"), str) or error.get("kind") not in ("refusal", "failure")):
                raise CoordinatorError("RPC_RESPONSE", "Malformed coordinator error response", operational=True)
            raise CoordinatorError(error["code"], error["message"], error.get("details"),
                                   error.get("kind") == "failure", answered=True)
        if "result" not in response:
            raise CoordinatorError("RPC_RESPONSE", "Coordinator response has no result", operational=True)
        return response["result"]

    def status(self):
        """The running daemon's daemon/status, or None when none answers. A daemon on its way out
        answers DAEMON_DRAINING, which is raised."""
        try:
            return self.call("daemon/status", {"brief": True})
        except (FileNotFoundError, ConnectionRefusedError):
            return None

    def current(self, timeout):
        """daemon/status, or None when none answers, waiting out a daemon that is stopping: it either
        exits (then its locks are waited for too, so a new one can start) or, its stop refused, serves on.
        No answer while its locks are held is a daemon still exiting after its stop answered, or one not
        yet listening: either is waited for, since a new one started then loses the lock and exits. None
        means no daemon holds them."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                status = self.status()
            except CoordinatorError as exc:
                if exc.code != "DAEMON_DRAINING" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
                continue
            held = [name for name in ("supervisor.lock", "worker.lock") if _held(self.state / name)]
            if status is not None or not held:
                return status
            if time.monotonic() >= deadline:
                raise CoordinatorError("DAEMON_UNRESPONSIVE", "A coordinator process holds the daemon's locks but does "
                                       "not answer; retry once it exits", {"locks": held, "waited_seconds": timeout}, True)
            time.sleep(0.05)

    @contextlib.contextmanager
    def upgrading(self, timeout):
        """Hold <state>/upgrade.lock: one process at a time starts or replaces the daemon."""
        import fcntl
        self.state = validate_state(self.state, create=True)
        fd = os.open(str(self.state / UPGRADE_LOCK), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CoordinatorError("DAEMON_START", "Another process is still starting or replacing the "
                                               "coordinator", {"lock": str(self.state / UPGRADE_LOCK)}, True)
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    def stop_command(self):
        plugin = Path(__file__).resolve().parents[1]
        return "cd {} && {} -B -m coordinator --state {} daemon stop".format(
            shlex.quote(str(plugin)), shlex.quote(daemon_python()), shlex.quote(str(self.state)))

    def retire(self, status, timeout):
        """Stop the older release's daemon that `status` describes, so this client starts its own in its
        place; False when another process replaced it first. Only a daemon that drains (2.4.0 and later)
        is stopped, and only that process: it refuses while work is pending and admits nothing new while
        it decides. One from before 2.4.0 cannot promise that, so it is left running and reported."""
        details = {"running": status.get("version"), "pid": status.get("pid")}
        if "draining-stop" not in (status.get("capabilities") or []) or not status.get("boot"):
            command = self.stop_command()
            raise CoordinatorError("DAEMON_OUTDATED", "A coordinator from before 2.4.0 is running. It cannot confirm "
                                   "that no lead edit, hook or publication is in flight, so it is not replaced "
                                   "automatically: stop it once, when no session is editing, with `" + command +
                                   "`. The next call then starts this release.", dict(details, stop=command), True)
        try:
            self.call("daemon/stop", {"expect": {"pid": status.get("pid"), "boot": status["boot"]}})
        except (FileNotFoundError, ConnectionRefusedError):
            pass  # it is gone already
        except CoordinatorError as exc:
            if exc.code == "DAEMON_REPLACED":
                return False
            if not exc.operational or exc.code == "DAEMON_DRAINING":
                raise CoordinatorError("DAEMON_OUTDATED", "An older coordinator release is running with unsettled "
                                       "work; it is replaced once that work settles", dict(details, reason=exc.code,
                                       pending=exc.details.get("pending", exc.details)), True) from exc
            # Otherwise the answer was lost as it exited (RPC_UNCERTAIN): its locks say whether it did.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(_held(self.state / name) for name in ("supervisor.lock", "worker.lock")):
                return True
            time.sleep(0.05)
        raise CoordinatorError("DAEMON_OUTDATED", "The older coordinator release did not stop", details, True)

    def stop(self, timeout=30, park=False):
        """Stop the running daemon, holding the upgrade lock. One that drains (2.4.0 and later) decides
        itself; for one from before 2.4.0 its ledger is read first, and the stop is refused while lead
        work is in flight there."""
        with self.upgrading(timeout):
            status = self.call("daemon/status", {"brief": True})
            if "draining-stop" in (status.get("capabilities") or []) and status.get("boot"):
                return self.call("daemon/stop", {"expect": {"pid": status.get("pid"), "boot": status["boot"]}, "park": park})
            pending = lead_work_in_flight(self.state)
            if pending:
                raise CoordinatorError("NOT_QUIESCENT", "Lead work is in flight in the older coordinator; stop it "
                                       "once that settles", {"pending": pending})
            return self.call("daemon/stop")

    def ensure(self, timeout=30, python=None, inherited_fds=None):
        from .owner_journal import control_state
        if control_state(self.state)["inhibited"]:
            raise CoordinatorError("AUTOSTART_INHIBITED", "Coordinator was explicitly stopped; run daemon start to resume",
                                   {"start": self.stop_command().rsplit("stop", 1)[0] + "start"}, True)
        try:
            status = self.status()
        except CoordinatorError as exc:
            if exc.code != "DAEMON_DRAINING":
                raise
            status = None  # being stopped, most likely replaced: wait behind whoever holds the lock
        else:
            if status is not None and not superseded(status.get("version")):
                return status
        with self.upgrading(timeout):
            for _ in range(3):
                # Looked at again under the lock: another process may have started or replaced it.
                status = self.current(timeout)
                if status is None:
                    status = self.settled(self.start(timeout, python, inherited_fds), timeout)
                if not superseded(status.get("version")):
                    return status
                self.retire(status, timeout)
        raise CoordinatorError("DAEMON_OUTDATED", "An older coordinator release kept taking the daemon's place",
                               {"running": status.get("version"), "pid": status.get("pid")}, True)

    def resume(self):
        from .leases import control
        from .owner_journal import control_state
        owner = control_state(self.state)
        if owner["inhibited"]:
            try:
                control(self.state, "owner/start")
            except (FileNotFoundError, ConnectionRefusedError):
                os.environ["SOFTWARE_WORKFLOWS_EXPLICIT_START"] = "1"
                try:
                    return self.start()
                finally:
                    os.environ.pop("SOFTWARE_WORKFLOWS_EXPLICIT_START", None)
        return self.ensure()

    def settled(self, status, timeout):
        """The status of the daemon this client just started, once it has finished recovery and admits
        work (or the time is up): the upgrade lock stays held until then, so the lead hooks wait across
        the whole replacement instead of meeting RECOVERING."""
        deadline = time.monotonic() + timeout
        while not status.get("ready") and time.monotonic() < deadline:
            time.sleep(0.05)
            try:
                status = self.call("daemon/status", {"brief": True})
            except (FileNotFoundError, ConnectionRefusedError):
                break
        return status

    def start(self, timeout=30, python=None, inherited_fds=None):
        self.state = validate_state(self.state, create=True)
        # Each detached supervisor competes for the same flock; losers simply exit.
        started = time.time()
        inherited_fds = venv_fds() if inherited_fds is None else inherited_fds
        environment = launch_environment()
        if inherited_fds:
            environment["CODEX_ORACLE_VENV_LOCK"] = str(inherited_fds[0])
        from .owner_journal import control_state
        owner = control_state(self.state)["custodian"]
        if owner and owner.get("agent"):
            from .leases import control
            control(self.state, "owner/status")  # socket activation, never a competing supervisor
        else:
            subprocess.Popen([python or daemon_python(), "-B", "-m", "coordinator", "daemon", "serve",
                          "--state", str(self.state)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True, pass_fds=inherited_fds,
                             cwd=str(Path(__file__).resolve().parents[1]), env=environment)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.call("daemon/status", {"brief": True})
            except (FileNotFoundError, ConnectionRefusedError):
                from .runtime import private_json
                try:
                    failure = private_json(self.state / "startup-error.json")
                    if failure["time"] >= started:
                        error = failure["error"]
                        raise CoordinatorError(error["code"], error["message"], error.get("details"), True)
                except FileNotFoundError:
                    pass
                time.sleep(0.05)
        raise CoordinatorError("DAEMON_START", "Daemon did not become available; inspect the private startup diagnostic",
                               {"diagnostic": str(self.state / "startup-error.json")}, True)
