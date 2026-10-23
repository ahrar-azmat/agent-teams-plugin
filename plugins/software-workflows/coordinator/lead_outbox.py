"""Private, atomic completion outboxes shared with the Python 3.9 hook.

Each item has its own file: acknowledging one never rewrites the other inputs.
The directory rename commits the complete batch before its first RPC.
"""
import errno
import hashlib
import json
import os
import stat
import uuid


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def directory(path, parent=None, create=False):
    if create:
        try:
            os.mkdir(path, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise OSError("outbox directory is not private")
    return fd


def root(state, create=False):
    fd = directory(state)
    try:
        return directory("lead-hooks", fd, create)
    finally:
        os.close(fd)


def token(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino)


def write(fd, name, data):
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def persist(state, calls):
    from .processes import identity
    bodies = [encoded(call) for call in calls]
    name = "batch-" + hashlib.sha256(b"[" + b",".join(bodies) + b"]").hexdigest()
    producer = identity(os.getpid())
    if not producer:
        raise OSError("outbox producer identity unavailable")
    parent = root(state, create=True)
    # Ownership is in the directory name before the first item can be written.
    # Each JSON item is self-contained, even if death interrupts the manifest.
    stage = ".batch-{}-{}-{}".format(producer["pid"], producer["birth"].encode().hex(), uuid.uuid4().hex)
    fd = None
    delivered = False
    try:
        fd = directory(stage, parent, create=True)
        for index, body in enumerate(bodies):
            write(fd, str(index), body)
        write(fd, ".meta", encoded({"count": len(calls)}))
        os.fsync(fd)
        while True:
            try:
                os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                delivered = True
                return name, token(fd)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                try:
                    existing = directory(name, parent)
                except FileNotFoundError:
                    continue  # a concurrent acknowledgment just removed it
                try:
                    delivered = True
                    return name, token(existing)
                finally:
                    os.close(existing)
    finally:
        if fd is not None:
            os.close(fd)
        # An error must not erase already-written completions. After this
        # producer exits, the scanner recovers every complete item in its stage.
        try:
            temporary = directory(stage, parent) if delivered else None
        except FileNotFoundError:
            pass
        else:
            if temporary is not None:
                try:
                    for child in os.listdir(temporary):
                        os.unlink(child, dir_fd=temporary)
                finally:
                    os.close(temporary)
                os.rmdir(stage, dir_fd=parent)
        os.close(parent)


def acknowledge(state, receipt, indices=None):
    name, expected = receipt
    parent = root(state)
    try:
        try:
            fd = directory(name, parent)
        except FileNotFoundError:
            return
        try:
            if token(fd) != tuple(expected):
                return
            names = os.listdir(fd) if indices is None else [str(index) for index in indices]
            for child in names:
                if child.isdecimal():
                    try:
                        os.unlink(child, dir_fd=fd)
                    except FileNotFoundError:
                        pass
            os.fsync(fd)
            if not any(child.isdecimal() for child in os.listdir(fd)):
                try:
                    os.unlink(".meta", dir_fd=fd)
                except FileNotFoundError:
                    pass
                os.rmdir(name, dir_fd=parent)
                os.fsync(parent)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def pending(state):
    """Read through verified descriptors; callers run directory/JSON work off-loop."""
    try:
        parent = root(state)
    except FileNotFoundError:
        return []
    found = []
    try:
        for name in os.listdir(parent):
            staged = name.startswith(".batch-")
            if not staged and (not name.startswith("batch-") or len(name) != 70):
                continue
            try:
                if staged:
                    from .processes import identity
                    parts = name.split("-")
                    if len(parts) != 4:
                        continue  # old anonymous stages provide no proof their producer ended
                    pid, birth = int(parts[1]), bytes.fromhex(parts[2]).decode()
                    current = identity(pid)
                    if current and current["birth"] == birth and not current["zombie"]:
                        continue  # no age rule can take a live producer's stage
                fd = directory(name, parent)
                try:
                    calls = []
                    for child in sorted((n for n in os.listdir(fd) if n.isdecimal()), key=int):
                        item = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        with os.fdopen(item, "rb") as stream:
                            info = os.fstat(stream.fileno())
                            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                                raise OSError("outbox item is not a private regular file")
                            try:
                                calls.append(json.load(stream))
                            except (ValueError, UnicodeError):
                                if not staged:
                                    raise
                                # A dead producer can leave the last item half
                                # written. Import its complete siblings before
                                # acknowledgment removes the whole stage.
                    found.append(((name, token(fd)), calls, None))
                finally:
                    os.close(fd)
            except FileNotFoundError:
                continue  # hook acknowledged/replaced this entry while we listed it
            except Exception as exc:
                found.append(((name, None), None, type(exc).__name__))
    finally:
        os.close(parent)
    return found
