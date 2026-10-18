"""Python 3.9 JSONL client. The client never imports or opens the ledger."""
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid


class CoordinatorError(RuntimeError):
    def __init__(self, code, message, details=None, operational=False):
        super().__init__(message)
        self.code, self.details, self.operational = code, details or {}, operational

    def envelope(self):
        return {"code": self.code, "message": str(self), "details": self.details,
                "kind": "failure" if self.operational else "refusal"}


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


def launch_environment():
    environment = dict(os.environ)
    plugin = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = plugin + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    return environment


class Client:
    def __init__(self, state=None, timeout=30):
        self.state = validate_state(state_root(state))
        self.timeout = timeout

    def call(self, method, params=None, key=None):
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "method": method, "params": params or {}}
        if key is not None:
            request["key"] = key
        from .runtime import discover
        address = discover(self.state)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(str(address))
            connection.sendall((json.dumps(request, ensure_ascii=True) + "\n").encode())
            with connection.makefile("rb") as stream:
                line = stream.readline()
            if not line:
                raise CoordinatorError("RPC_UNCERTAIN", "Connection ended without a result; reconcile before retrying",
                                       {"id": request_id, "key": key}, True)
            response = json.loads(line)
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise CoordinatorError("RPC_ID", "Mismatched RPC response", operational=True)
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise CoordinatorError("RPC_RESPONSE", "Malformed coordinator error response", operational=True)
            raise CoordinatorError(error["code"], error["message"], error.get("details"),
                                   error.get("kind") == "failure")
        if "result" not in response:
            raise CoordinatorError("RPC_RESPONSE", "Coordinator response has no result", operational=True)
        return response["result"]

    def ensure(self, timeout=30, python=None, inherited_fds=None):
        try:
            return self.call("daemon/status")
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        self.state = validate_state(self.state, create=True)
        # Each detached supervisor competes for the same flock; losers simply exit.
        started = time.time()
        inherited_fds = venv_fds() if inherited_fds is None else inherited_fds
        environment = launch_environment()
        if inherited_fds:
            environment["CODEX_ORACLE_VENV_LOCK"] = str(inherited_fds[0])
        subprocess.Popen([python or daemon_python(), "-B", "-m", "coordinator", "daemon", "serve",
                          "--state", str(self.state)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True, pass_fds=inherited_fds,
                         cwd=str(Path(__file__).resolve().parents[1]), env=environment)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.call("daemon/status")
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
