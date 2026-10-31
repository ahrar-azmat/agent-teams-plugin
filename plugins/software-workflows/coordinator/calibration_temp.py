"""Bounded ownership and lifetime for disposable calibration directories."""
import errno
import fcntl
import os
from pathlib import Path
import shutil
import stat
import time


PREFIXES = {"software-workflows-admission-", "software-workflows-git-"}
MAX_AGE = 60 * 60
LOCK = ".lifetime.lock"


def hold(directory):
    """Hold this calibration's lifetime until remove() finishes, including errors."""
    fd = os.open(Path(directory) / LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _error(result, path, operation, error):
    result["errors"].append({"path": str(path), "operation": operation,
                             "error": type(error).__name__, "errno": getattr(error, "errno", None)})


def _sizes(directory):
    """Count logical and allocated bytes without following any symbolic link."""
    size = allocated = 0
    pending = [directory]
    while pending:
        path = pending.pop()
        info = path.lstat()
        size += info.st_size
        allocated += getattr(info, "st_blocks", 0) * 512
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
    return size, allocated


def _remove(directory, result, cutoff=None, lifetime=None):
    lock = None
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            result["kept"] += 1
            return
        if lifetime is None:
            try:
                lock = os.open(directory / LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                if cutoff is not None:
                    # An older calibration may still be using this lockless tree.
                    result["legacy"] += 1
                    result["kept"] += 1
                    return
            if lock is not None:
                locked = os.fstat(lock)
                if not stat.S_ISREG(locked.st_mode) or locked.st_uid != os.getuid():
                    raise OSError("Invalid calibration lifetime lock")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    result["active"] += 1
                    result["kept"] += 1
                    return
        elif not os.path.samestat(os.fstat(lifetime), (directory / LOCK).lstat()):
            raise OSError("Calibration lifetime lock changed")
        if cutoff is not None and info.st_mtime >= cutoff:
            result["kept"] += 1
            return
        if not shutil.rmtree.avoids_symlink_attacks:
            raise OSError("Symlink-safe directory removal is unavailable")
        size, allocated = _sizes(directory)
        current = directory.lstat()
        if (current.st_dev, current.st_ino, current.st_uid, current.st_mtime) != (info.st_dev, info.st_ino, info.st_uid, info.st_mtime):
            result["kept"] += 1
            return
        shutil.rmtree(directory)
        result["removed"] += 1
        result["bytes"] += size
        result["allocated_bytes"] += allocated
    except FileNotFoundError as exc:
        # Another calibration can sweep the same stale directory concurrently.
        if directory.exists() or directory.is_symlink():
            _error(result, directory, "remove", exc)
    except OSError as exc:
        _error(result, directory, "remove", exc)
    finally:
        if lock is not None:
            os.close(lock)


def _result(root, prefix):
    return {"root": str(root), "prefix": prefix, "uid": os.getuid(), "removed": 0,
            "bytes": 0, "allocated_bytes": 0, "kept": 0, "active": 0, "legacy": 0, "errors": []}


def sweep(root, prefix):
    """Remove this UID's stale directories only while holding their lifetime lock."""
    root = Path(root)
    result = _result(root, prefix)
    result["older_than_seconds"] = MAX_AGE
    if prefix not in PREFIXES:
        _error(result, root, "prefix", ValueError("Unknown calibration prefix"))
        return result
    cutoff = time.time() - MAX_AGE
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.startswith(prefix):
                    _remove(Path(entry.path), result, cutoff)
    except OSError as exc:
        _error(result, root, "scan", exc)
    return result


def remove(directory, lifetime=None):
    """Retire one owned calibration before releasing its optional lifetime lock."""
    directory = Path(directory)
    prefix = next((value for value in PREFIXES if directory.name.startswith(value)), None)
    result = _result(directory.parent, prefix)
    result["directory"] = str(directory)
    try:
        if prefix is None:
            _error(result, directory, "prefix", ValueError("Unknown calibration prefix"))
        else:
            _remove(directory, result, lifetime=lifetime)
    finally:
        if lifetime is not None:
            os.close(lifetime)
    result["directory_removed"] = not os.path.lexists(directory)
    return result
