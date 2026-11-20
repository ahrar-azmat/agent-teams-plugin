"""Kernel-held claims for Oracle runs and Codex threads."""

import contextlib
import os
from pathlib import Path
import stat


_held: dict[Path, int] = {}


def _private_directory(path: Path) -> None:
    """Create missing directories privately and check existing ancestors before using them."""
    path = path.absolute()
    uid = os.getuid() if os.name != "nt" else None
    for directory in reversed((path, *path.parents)):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass  # another process may have created it
            info = directory.lstat()
            if uid is not None and (info.st_uid != uid or info.st_mode & 0o077):
                raise PermissionError(f"New claim directory is not private: {directory}")
        if stat.S_ISLNK(info.st_mode):
            # macOS exposes these root-owned system directories through symlinks.
            if (directory != path and uid is not None and info.st_uid == 0
                    and str(directory) in ("/tmp", "/var")
                    and str(directory.resolve()) == "/private" + str(directory)):
                continue
            raise PermissionError(f"Symlink in claim directory: {directory}")
        if not stat.S_ISDIR(info.st_mode) or (uid is not None and info.st_uid not in (0, uid)):
            raise PermissionError(f"Invalid claim directory: {directory}")
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def acquire(path: Path, payload: str) -> tuple[bool, str]:
    """Claim the file without waiting. On contention return the holder's diagnostic text."""
    try:
        _private_directory(path.parent)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        return False, f"lock dir unusable ({exc})"
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = ""
        with contextlib.suppress(OSError):
            holder = os.read(fd, 4096).decode("utf-8", errors="replace").strip()
        os.close(fd)
        return False, holder or "held by another process"
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    _held[path] = fd
    return True, ""


def descriptor(path: Path) -> int | None:
    return _held.get(path)


def release(path: Path) -> None:
    # Close only our copy. A running continuation may have inherited the same
    # open file description and must retain its claim until it exits.
    fd = _held.pop(path, None)
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)
