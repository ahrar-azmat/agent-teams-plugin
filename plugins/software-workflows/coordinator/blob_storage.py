"""Immutable logical receipts, with raw and independently framed block representations.

No codec is imported until a daemon constructs Storage. Hooks can import Store
without acquiring a compression dependency. SQLite barriers stay with Store.
"""
from contextlib import contextmanager
import hashlib
import io
import os
import re
import stat
import shutil
import struct
import tempfile
import threading

from .client import CoordinatorError
from . import telemetry

SUFFIX = ".zlib-blocks-v1"
HEADER = struct.Struct(">8sIIQQ32s")
ENTRY = struct.Struct(">QQQQ32s")
MAGIC = b"SWBLK001"
CHUNK = 65536


def codec():
    try:
        import zlib
        return zlib
    except ImportError as exc:
        raise CoordinatorError("BLOB_CODEC_UNAVAILABLE", "zlib is required for content storage") from exc


def probe():
    try:
        zlib = codec()
        data = bytes(range(256)) * 17
        decoder = zlib.decompressobj(15)
        result = decoder.decompress(zlib.compress(data, 6), len(data) + 1)
        if result != data or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("round trip differs")
    except Exception as exc:
        raise CoordinatorError("BLOB_CODEC_UNAVAILABLE", "zlib encode/decode capability probe failed") from exc


def sync(fd):
    import fcntl
    os.fsync(fd)
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sync(fd)
    finally:
        os.close(fd)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def valid_name(name):
    return isinstance(name, str) and len(name) == 64 and all(c in "0123456789abcdef" for c in name)


def corrupt(name):
    return CoordinatorError("BLOB_CORRUPT", "Receipt representation failed verification", {"blob": name}, True)


class Storage:
    def __init__(self, store):
        probe()
        self.store, self.root = store, store.root / "blobs"
        try:
            self.block = int(os.environ.get("SOFTWARE_WORKFLOWS_BLOB_BLOCK_BYTES", 1 << 20))
            if not 0 < self.block < 1 << 32:
                raise ValueError()
        except ValueError as exc:
            raise CoordinatorError("INVALID_SETTING", "BLOB_BLOCK_BYTES must be a positive unsigned 32-bit integer") from exc
        try:
            # Default: the user's 15 GiB free-space floor. The existing
            # maintenance allowance (two ledger sizes) is added separately.
            self.background_reserve = int(os.environ.get("SOFTWARE_WORKFLOWS_BLOB_RESERVE_BYTES", 15 << 30))
            if self.background_reserve < 0:
                raise ValueError()
        except ValueError as exc:
            raise CoordinatorError("INVALID_SETTING", "BLOB_RESERVE_BYTES must be a nonnegative integer") from exc
        volume = os.statvfs(self.root)
        self.allocation_unit = volume.f_frsize or volume.f_bsize
        self.metrics = {"repaired": 0, "reclaimed_temporaries": 0, "reclaimed_temporary_bytes": 0,
                        "reclaimed_temporary_allocated_bytes": 0, "unexpected_entries": 0}
        self.guard, self.locks, self.cache = threading.RLock(), {}, {}
        self.reserved = 0
        self.changed = None

    def lock(self, name):
        with self.guard:
            return self.locks.setdefault(name, threading.RLock())

    @contextmanager
    def reserve(self, amount, background=False):
        self.take_space(amount, background)
        try:
            yield
        finally:
            with self.guard:
                self.reserved -= amount

    def take_space(self, amount, background=False):
        # Candidates and operation spools share this allowance. Maintenance can
        # need a second ledger and its replacement on the same volume.
        with self.guard:
            if background:
                ledger = self.store.root / "ledger.sqlite"
                maintenance = 2 * ledger.stat().st_size if ledger.exists() else 0
                if shutil.disk_usage(self.root).free - self.reserved - amount < self.background_reserve + maintenance:
                    raise CoordinatorError("RESOURCE_EXHAUSTED", "Insufficient background conversion space",
                                           {"resource": "disk", "retryable": True}, True)
            self.reserved += amount

    @contextmanager
    def opened(self, name):
        try:
            with self._opened(name) as opened:
                yield opened
        except OSError as exc:
            from .native import local_resource_failure
            if local_resource_failure(exc):
                raise
            raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True) from exc

    @contextmanager
    def _opened(self, name):
        if not valid_name(name):
            raise CoordinatorError("BLOB_ID", "Invalid receipt identifier")
        # A raw reader owns this lock until its descriptor closes. Publication
        # cannot unlink raw while an older reader is still consuming it.
        with self.lock(name):
            for suffix in (SUFFIX, "", SUFFIX):
                try:
                    stream = (self.root / (name + suffix)).open("rb")
                except FileNotFoundError:
                    continue
                with stream:
                    before = stamp(os.fstat(stream.fileno()))
                    cached = self.cache.get(name)
                    info = cached[2] if cached and cached[:2] == (suffix, before) else None
                    if info is None:
                        info = self.validate(stream, name, suffix, before)
                        self.cache[name] = (suffix, before, info)
                    yield stream, suffix, info
                    if stamp(os.fstat(stream.fileno())) != before:
                        self.cache.pop(name, None)
                        raise corrupt(name)
                return
        raise CoordinatorError("BLOB_MISSING", "Receipt bytes are unavailable", {"blob": name}, True)

    def index(self, stream, name):
        stream.seek(0)
        raw = stream.read(HEADER.size)
        if len(raw) != HEADER.size:
            raise corrupt(name)
        magic, version, block, length, count, digest = HEADER.unpack(raw)
        size = os.fstat(stream.fileno()).st_size
        if magic != MAGIC or version != 1 or not block or count != (length + block - 1) // block:
            raise corrupt(name)
        end = HEADER.size + count * ENTRY.size
        if end > size:
            raise corrupt(name)
        logical, physical, hashed = 0, end, hashlib.sha256()
        for number in range(count):
            entry = stream.read(ENTRY.size)
            if len(entry) != ENTRY.size:
                raise corrupt(name)
            hashed.update(entry)
            start, offset, plain, stored, _ = ENTRY.unpack(entry)
            if (start != logical or offset != physical or plain != min(block, length - logical)
                    or not stored or stored > size - physical):
                raise corrupt(name)
            logical += plain
            physical += stored
        if logical != length or physical != size or hashed.digest() != digest:
            raise corrupt(name)
        return block, length, count

    def decoded(self, stream, name, entry):
        _, offset, length, stored, expected = entry
        stream.seek(offset)
        decoder, hashed, total = codec().decompressobj(15), hashlib.sha256(), 0
        try:
            data = b""
            while stored or data or not decoder.eof:
                if not data and stored:
                    data = stream.read(min(CHUNK, stored))
                    if not data:
                        raise corrupt(name)
                    stored -= len(data)
                while True:
                    output = decoder.decompress(data, min(CHUNK, length - total + 1))
                    data = decoder.unconsumed_tail
                    total += len(output)
                    if total > length or decoder.unused_data or decoder.eof and (data or stored):
                        raise corrupt(name)
                    hashed.update(output)
                    yield output
                    if not data:
                        if stored or decoder.eof:
                            break
                        if not output:
                            raise corrupt(name)
                        # zlib can retain output after consuming its final input.
                        # Drain with a positive output bound, never flush().
                        continue
            if not decoder.eof or total != length or hashed.digest() != expected:
                raise corrupt(name)
        except codec().error as exc:
            raise corrupt(name) from exc

    def chunks(self, stream, name, suffix, info, offset=0, length=None):
        size = info[1]
        end = size if length is None else min(size, offset + length)
        if offset >= end:
            return
        if not suffix:
            stream.seek(offset)
            while offset < end:
                data = stream.read(min(CHUNK, end - offset))
                if not data:
                    raise corrupt(name)
                offset += len(data)
                yield data
            return
        block = info[0]
        for number in range(offset // block, (end - 1) // block + 1):
            stream.seek(HEADER.size + number * ENTRY.size)
            raw = stream.read(ENTRY.size)
            if len(raw) != ENTRY.size:
                raise corrupt(name)
            entry = ENTRY.unpack(raw)
            position = entry[0]
            for data in self.decoded(stream, name, entry):
                start, stop = max(0, offset - position), min(len(data), end - position)
                if start < stop:
                    yield data[start:stop]
                position += len(data)

    def validate(self, stream, name, suffix, before):
        info = self.index(stream, name) if suffix else (0, before[2], 0)
        hashed = hashlib.sha256()
        for data in self.chunks(stream, name, suffix, info):
            hashed.update(data)
        if hashed.hexdigest() != name or stamp(os.fstat(stream.fileno())) != before:
            raise corrupt(name)
        return info

    def verify(self, name):
        with self.opened(name) as (_, _, info):
            return info[1]

    def copy(self, name, output):
        with self.opened(name) as (stream, suffix, info):
            for data in self.chunks(stream, name, suffix, info):
                output.write(data)
            return info[1]

    def read(self, name, offset=0, length=None):
        if not isinstance(offset, int) or offset < 0 or length is not None and (not isinstance(length, int) or length < 0):
            raise CoordinatorError("BLOB_RANGE", "Receipt ranges must be nonnegative integers")
        with self.opened(name) as (stream, suffix, info):
            return b"".join(self.chunks(stream, name, suffix, info, offset, length)), info[1]

    def reusable(self, name):
        try:
            self.verify(name)
            return True
        except CoordinatorError as exc:
            if exc.code == "BLOB_MISSING":
                return False
            raise

    def encode(self, source, target, name, size, checkpoint=lambda: None):
        count = (size + self.block - 1) // self.block
        target.seek(HEADER.size + count * ENTRY.size)
        hashed, index_hash, logical = hashlib.sha256(), hashlib.sha256(), 0
        for number in range(count):
            checkpoint()
            position, plain = target.tell(), min(self.block, size - logical)
            encoder, block_hash, remaining = codec().compressobj(6), hashlib.sha256(), plain
            while remaining:
                checkpoint()
                data = source.read(min(CHUNK, remaining))
                if not data:
                    raise CoordinatorError("UNSTABLE_READ", "Source changed while encoding receipt")
                hashed.update(data)
                block_hash.update(data)
                remaining -= len(data)
                target.write(encoder.compress(data))
            target.write(encoder.flush())
            end = target.tell()
            entry = ENTRY.pack(logical, position, plain, end - position, block_hash.digest())
            index_hash.update(entry)
            target.seek(HEADER.size + number * ENTRY.size)
            target.write(entry)
            target.seek(end)
            logical += plain
        if source.read(1) or hashed.hexdigest() != name:
            raise CoordinatorError("UNSTABLE_READ", "Source changed while encoding receipt")
        target.seek(0)
        target.write(HEADER.pack(MAGIC, 1, self.block, size, count, index_hash.digest()))
        target.flush()
        self.validate(target, name, SUFFIX, stamp(os.fstat(target.fileno())))

    def publication_state(self, name):
        try:
            return self.reusable(name), False
        except CoordinatorError as exc:
            if exc.code != "BLOB_CORRUPT":
                raise
            return False, True

    def repaired(self, name, suffix):
        with self.guard:
            self.metrics["repaired"] += 1
        self.store.blob_event("blob/repaired", name, {"representation": suffix or "raw"})

    def reclaim_temporaries(self):
        """Called only by the daemon worker holding worker.lock, before serve."""
        removed = size = allocated = unexpected = 0
        with os.scandir(self.root) as entries:
            for entry in entries:
                name = entry.name
                identity = name[:-len(SUFFIX)] if name.endswith(SUFFIX) else name
                if valid_name(identity):
                    continue
                info = entry.stat(follow_symlinks=False)
                # These are precisely tempfile's store prefixes and random-name
                # alphabet. Foreign names, links and directories are not ours.
                temporary = re.fullmatch(r"(?:pending-(?:blocks-|raw-)?|tmp)[a-z0-9_]{8}", name)
                if temporary and stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid():
                    os.unlink(entry.path)
                    removed += 1
                    size += info.st_size
                    allocated += info.st_blocks * 512
                else:
                    unexpected += 1
        if removed:
            sync_dir(self.root)
        self.metrics.update(reclaimed_temporaries=removed, reclaimed_temporary_bytes=size,
                            reclaimed_temporary_allocated_bytes=allocated, unexpected_entries=unexpected)
        self.store.blob_event("blob/temporaries_reclaimed", None, dict(self.metrics))

    def publish(self, name, raw, checkpoint=lambda: None, background=False):
        physical = os.fstat(raw.fileno())
        size = physical.st_size
        # A nonempty container occupies at least one filesystem allocation unit.
        # Empty/raw single-unit content cannot improve on that allocation.
        if physical.st_blocks * 512 <= self.allocation_unit:
            return False
        with self.reserve(size + (size // self.block + 1) * ENTRY.size + (1 << 20), background):
            fd, pending = tempfile.mkstemp(prefix="pending-blocks-", dir=self.root)
            try:
                with os.fdopen(fd, "w+b") as candidate:
                    raw.seek(0)
                    self.encode(raw, candidate, name, size, checkpoint)
                    if os.fstat(candidate.fileno()).st_blocks >= physical.st_blocks:
                        return False
                    self.store.storage5()
                    sync(candidate.fileno())
                checkpoint()
                with self.lock(name):
                    encoded = self.root / (name + SUFFIX)
                    reusable, damaged = self.publication_state(name)
                    if not reusable or not encoded.exists():
                        os.replace(pending, encoded)
                        self.cache.pop(name, None)
                        sync_dir(self.root)
                        self.verify(name)
                    try:
                        (self.root / name).unlink()
                    except FileNotFoundError:
                        pass
                    sync_dir(self.root)
                    if self.changed:
                        self.changed(name)
                if damaged:
                    self.repaired(name, SUFFIX)
                return True
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)

    @staticmethod
    def hash_chunk(hashed, data):
        with telemetry.span("hashing", False):
            hashed.update(data)
        counter = telemetry.IO.get()
        if counter is not None:
            counter["bytes"] += len(data)

    def blob(self, content):
        if isinstance(content, bytes):
            content = io.BytesIO(content)
        expected = None
        try:
            content.seek(0)
        except (AttributeError, OSError):
            pass
        else:
            # A cold receipt lookup must not need a second copy of known bytes.
            hashed = hashlib.sha256()
            for data in iter(lambda: content.read(CHUNK), b""):
                self.hash_chunk(hashed, data)
            expected = hashed.hexdigest()
            if self.publication_state(expected)[0]:
                return expected
            content.seek(0)
        # New seekable input is rehashed while spooling; nonseekable input uses
        # this single bounded spool. The verified raw candidate can be renamed
        # directly when encoding cannot improve its allocation.
        fd, pending = tempfile.mkstemp(prefix="pending-raw-", dir=self.root)
        reserved = 0
        try:
            with os.fdopen(fd, "w+b") as raw:
                hashed = hashlib.sha256()
                for data in iter(lambda: content.read(CHUNK), b""):
                    self.take_space(len(data))
                    reserved += len(data)
                    raw.write(data)
                    self.hash_chunk(hashed, data)
                raw.flush()
                name = hashed.hexdigest()
                if expected is not None and name != expected:
                    raise CoordinatorError("UNSTABLE_READ", "Source changed while preparing receipt")
                self.validate(raw, name, "", stamp(os.fstat(raw.fileno())))
                if self.publication_state(name)[0]:
                    return name
                if self.publish(name, raw):
                    return name
                sync(raw.fileno())
                with self.lock(name):
                    reusable, damaged = self.publication_state(name)
                    if not reusable:
                        os.replace(pending, self.root / name)
                        sync_dir(self.root)
                        # Prefer a new verified raw representation only after it
                        # is durable; a corrupt container must not shadow it.
                        try:
                            (self.root / (name + SUFFIX)).unlink()
                        except FileNotFoundError:
                            pass
                        self.cache.pop(name, None)
                        sync_dir(self.root)
                        self.verify(name)
                        if self.changed:
                            self.changed(name)
                if damaged:
                    self.repaired(name, "")
                return name
        finally:
            with self.guard:
                self.reserved -= reserved
            if os.path.exists(pending):
                os.unlink(pending)

    def convert(self, name, checkpoint=lambda: None):
        with self.lock(name):
            self.verify(name)
            try:
                source = (self.root / name).open("rb")
            except FileNotFoundError:
                return False
        with source:
            return self.publish(name, source, checkpoint, background=True)

    @contextmanager
    def staged(self, expected):
        names = {image["blob"] for values in expected.values() for image in values.values() if image["blob"]}
        sizes = {name: self.verify(name) for name in names}
        with self.reserve(sum(sizes.values())), tempfile.TemporaryFile(dir=self.root) as spool:
            images = {}
            for name in names:
                offset = spool.tell()
                length = self.copy(name, spool)
                images[name] = (offset, length)
            spool.flush()
            yield spool.fileno(), images


def install(spool, image, output):
    fd, images = spool
    offset, length = images[image]
    while length:
        data = os.pread(fd, min(CHUNK, length), offset)
        if not data:
            raise CoordinatorError("BLOB_STAGING", "Verified installation spool became unavailable")
        output.write(data)
        offset += len(data)
        length -= len(data)
