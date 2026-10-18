"""Short owned Unix endpoints and durable discovery (also used by Python 3.9)."""
from functools import lru_cache
import errno
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import uuid


def refusal(code, message, details=None):
    from .client import CoordinatorError
    return CoordinatorError(code, message, details)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_directory(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise refusal("SOCKET_DIRECTORY", "Socket runtime must be an owned private directory", {"path": str(path)})
    return info


def runtime_root(create=True):
    # Deliberately independent of TMPDIR and the persistent state path.
    from .client import checked_path, private_parents
    root = checked_path(os.environ.get("SOFTWARE_WORKFLOWS_RUNTIME", str(Path('/tmp').resolve() / ("c1-" + str(os.getuid())))))
    if create:
        private_parents(root)
    private_directory(root)
    return root


@lru_cache(maxsize=1)
def capacity():
    directory = Path(tempfile.mkdtemp(prefix="q", dir=runtime_root()))
    prefix = len(os.fsencode(str(directory))) + 1
    last = None
    try:
        # This is an experiment, not a platform constant or a connect-only check.
        for length in range(prefix + 1, prefix + 256):
            path = directory / ("s" * (length - prefix))
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(str(path))
                except OSError as exc:
                    if last is not None and (exc.errno == errno.ENAMETOOLONG or "path too long" in str(exc)):
                        return last
                    raise refusal("SOCKET_CAPACITY", "Disposable socket bind failed",
                                  {"path": str(path), "bytes": length, "errno": exc.errno}) from exc
                finally:
                    if path.exists():
                        path.unlink()
            last = length
        raise refusal("SOCKET_CAPACITY", "No pathname boundary observed by the bind experiment")
    finally:
        directory.rmdir()


def check_length(path):
    length, limit = len(os.fsencode(str(path))), capacity()
    if length > limit:
        raise refusal("SOCKET_PATH_LENGTH", "Physical Unix socket path exceeds the measured bind limit",
                      {"path": str(path), "bytes": length, "measured_limit": limit})
    return length


def atomic_json(path, value):
    path = Path(path)
    fd, pending = tempfile.mkstemp(prefix="manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def private_json(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise refusal("ENDPOINT_MANIFEST", "Endpoint manifest must be owned and private", {"path": str(path)})
        return json.load(stream)


def allocate(kind):
    directory = Path(tempfile.mkdtemp(prefix="s", dir=runtime_root()))
    info = private_directory(directory)
    record = {"directory": str(directory), "device": info.st_dev, "inode": info.st_ino,
              "token": uuid.uuid4().hex, "kind": kind, "path": str(directory / "s"),
              "measured_limit": capacity()}
    record["requested_bytes"] = check_length(record["path"])
    atomic_json(directory / "owner.json", record)
    return record


def validate_allocation(record, observational=False):
    directory = Path(record["directory"])
    if directory.parent != runtime_root(create=not observational) or record["path"] != str(directory / "s"):
        raise refusal("ENDPOINT_OWNER", "Endpoint is outside its allocated runtime directory")
    info = private_directory(directory)
    owner = private_json(directory / "owner.json")
    if (info.st_dev, info.st_ino) != (record["device"], record["inode"]) or owner != record:
        raise refusal("ENDPOINT_OWNER", "Endpoint allocation identity changed", {"path": record["path"]})


def endpoint_info(path, native=False, measured_limit=None):
    requested = Path(path)
    resolved = requested.resolve()
    limit = capacity() if measured_limit is None else measured_limit
    length = len(os.fsencode(str(resolved)))
    if type(limit) is not int or length > limit:
        raise refusal("SOCKET_PATH_LENGTH", "Endpoint exceeds its recorded capacity")
    private_directory(requested.parent)
    private_directory(resolved.parent)
    info = resolved.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise refusal("SOCKET_PERMISSIONS", "Endpoint must be an owned private socket",
                      {"path": str(resolved), "bytes": length, "measured_limit": limit})
    if not native and requested != resolved:
        raise refusal("SOCKET_ALIAS", "RPC endpoints cannot be symlinks")
    return {"requested": str(requested), "resolved": str(resolved), "requested_bytes": len(os.fsencode(str(requested))),
            "resolved_bytes": length, "measured_limit": limit, "mode": oct(stat.S_IMODE(info.st_mode)),
            "uid": info.st_uid}


def discover(state):
    manifest = private_json(Path(state) / "endpoint.json")
    if manifest.get("state") != str(Path(state).resolve()):
        raise refusal("ENDPOINT_OWNER", "Endpoint manifest belongs to a different state root")
    allocation = manifest["allocation"]
    validate_allocation(allocation, observational=True)
    return endpoint_info(allocation["path"], measured_limit=allocation["measured_limit"])["resolved"]


def cleanup(record):
    validate_allocation(record)
    path = Path(record["path"])
    if path.is_symlink() or path.exists():
        path.unlink()  # Native's physical socket belongs to native, not to us.
    (path.parent / "owner.json").unlink()
    path.parent.rmdir()
