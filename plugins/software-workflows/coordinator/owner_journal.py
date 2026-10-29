"""Supervisor-only ownership log. Appends overwrite already allocated bytes.

The final quarter is reserved for cleanup. A failed append never authorizes a
spawn, grant or adoption. Read-only consumers use the same CRC/sequence parser.
"""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import time
import uuid
import zlib

from .client import CoordinatorError
from .processes import boot_session

HEADER = struct.Struct("!4sIQ16sI")
MAGIC = b"SWOJ"
# 30 writers, two admission incarnations each, 16 observed family members and
# repeated snapshots. The CP1 report measures encoded lifecycles against this
# allocation. This is a storage budget, not a limit on families or descendants.
DEFAULT_BYTES = 8 * 1024 * 1024
# How long a compaction keeps a GONE family that no worker forgets: one of a helper, member, watcher probe or worker.
# Only in-memory handles of the worker that launched it ask about it, within seconds of its FAMILY_GONE; a few days
# (the user's choice, 2026-10-28) is headroom on that. A listener's family (a writer's app-server) is asked about
# across worker restarts, so only the worker that holds its receipt in the ledger drops it (FORGOTTEN). Through 2.4.11
# every family stayed in every compaction, and 8 MiB filled in 9 days.
DEFAULT_RETENTION_S = 3 * 86400


def retention_seconds():
    """SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S, read at every start (unlike the size, fixed at creation)."""
    value = os.environ.get("SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S")
    if value is None:
        return DEFAULT_RETENTION_S
    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0
    if not seconds >= 0:  # also refuses nan
        raise CoordinatorError("INVALID_SETTING", "SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S must be a number of "
                               "seconds, 0 or more", {"setting": "SOFTWARE_WORKFLOWS_OWNER_JOURNAL_RETENTION_S"})
    return seconds


def exhausted(message, **details):
    return CoordinatorError("RESOURCE_EXHAUSTED", message, dict(details, retryable=True, resource="owner_journal"), True)


def frame(sequence, value, boot=None):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    boot = uuid.UUID(boot or boot_session()).bytes
    prefix = struct.pack("!IQ16s", len(data), sequence, boot)
    return HEADER.pack(MAGIC, len(data), sequence, boot, zlib.crc32(prefix + data)) + data


def replay(path, visit, events=None):
    """Give `visit` every intact record of the journal at `path` in order, as a row: its value with its sequence and
    boot session. With `events`, only the rows of those events: every record's frame, sequence and CRC are still
    checked, but a payload naming none of them (as frame encodes an event, a quoted name) is never decoded. Returns
    (used, damaged, the last sequence). Through 2.4.18 every reader decoded the whole journal into a list first: 309 MB
    resident for the user's 55,932 records (60.5 MB), 0.77 s for the two control values a client asks."""
    markers = None if events is None else tuple(json.dumps(event).encode() for event in events)
    offset, previous, damaged = 0, 0, False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return offset, damaged, previous
    try:
        size = os.fstat(fd).st_size
        while offset + HEADER.size <= size:
            header = os.pread(fd, HEADER.size, offset)
            if header == bytes(HEADER.size):
                break
            if len(header) != HEADER.size:
                damaged = True
                break
            magic, length, sequence, boot, crc = HEADER.unpack(header)
            if magic != MAGIC or length > size - offset - HEADER.size or sequence <= previous:
                damaged = True
                break
            data = os.pread(fd, length, offset + HEADER.size)
            if zlib.crc32(struct.pack("!IQ16s", length, sequence, boot) + data) != crc:
                damaged = True
                break
            if markers is None or any(marker in data for marker in markers):
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError):
                    damaged = True
                    break
                if events is None or value.get("event") in events:
                    visit(dict(value, sequence=sequence, boot_session=str(uuid.UUID(bytes=boot))))
            previous = sequence
            offset += HEADER.size + length
    finally:
        os.close(fd)
    return offset, damaged, previous


def read(path):
    records = []
    used, damaged, _ = replay(path, records.append)
    return records, used, damaged


def empty():
    return {"families": {}, "worker": None, "custodian": None, "inhibited": False}


def fold(result, row):
    """Apply one row to a journal state, in place: `state` is every row folded in order."""
    kind = row["event"]
    if "family" in row:
        result["families"][row["family"]["listener"]] = dict(row["family"], owner_sequence=row["sequence"])
    if kind in ("WORKER", "ADOPTED_WORKER"):
        result["worker"] = row["worker"]
    elif kind == "WORKER_GONE":
        result["worker"] = None
    elif kind == "CUSTODIAN":
        result["custodian"] = row
    elif kind == "INHIBIT":
        result["inhibited"] = row["value"]
    elif kind == "FORGOTTEN":
        for ident in row["listeners"]:
            result["families"].pop(ident, None)
    return result


def state(records):
    result = empty()
    for row in records:
        fold(result, row)
    return result


# What a GONE family that is no listener's keeps once compacted: what the handle that launched it still reads (its
# root, state, exit evidence and return code; owned.Process) and what the retention needs (gone_at). Its member
# identities and the supervisor's and worker's go: 712 of the 1,161 bytes a GONE helper took on the user's journal on
# 10-29, kept for the three-day retention at ~500 helpers an hour (2.4.19).
RETAINED = ("listener", "kind", "state", "root", "exit_evidence", "returncode", "gone_at")


def retained(family):
    """The form of `family` a compaction writes: whole, unless it is GONE and no listener's. Its member list is left
    empty, not out: 2.4.18 and earlier read one from every family they list or snapshot."""
    if family["state"] != "GONE" or family.get("kind") == "listener":
        return family
    return dict({key: family[key] for key in RETAINED if key in family}, members=[])


def inspect(root):
    result = empty()
    used, damaged, _ = replay(Path(root) / "owner.journal", lambda row: fold(result, row))
    return dict(result, used=used, damaged=damaged)


CONTROL = ("CUSTODIAN", "INHIBIT", "WORKER", "ADOPTED_WORKER", "WORKER_GONE")


def control_state(root):
    """inspect's custodian, worker and inhibited, with used and damaged, decoding only the control rows (CONTROL):
    what a client reads before it starts, finds or resumes a supervisor. No families. Measured on the user's journal
    on 10-29: the same answer as inspect in 0.15 s instead of 0.77. A record whose payload passes its CRC but is no
    JSON, which only a defective writer leaves, is damage to inspect and to the supervisor's own read, unseen here."""
    result = empty()
    used, damaged, _ = replay(Path(root) / "owner.journal", lambda row: fold(result, row), events=CONTROL)
    del result["families"]
    return dict(result, used=used, damaged=damaged)


class Journal:
    def __init__(self, root):
        self.path = Path(root) / "owner.journal"
        if not self.path.exists():
            size = int(os.environ.get("SOFTWARE_WORKFLOWS_OWNER_JOURNAL_BYTES", DEFAULT_BYTES))
            if size < HEADER.size * 4:
                raise exhausted("Owner journal budget cannot hold its control records", bytes=size)
            self.allocate(self.path, size)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.size = os.fstat(self.fd).st_size
        # The state, folded as the journal is read; its records are not kept (2.4.19).
        self.state = empty()
        self.used, self.damaged, self.sequence = replay(self.path, lambda row: fold(self.state, row))
        self.emergency = self.damaged
        self.retention = retention_seconds()

    @staticmethod
    def allocate(path, size):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            if sys.platform == "darwin":
                class Allocation(ctypes.Structure):
                    _fields_ = [("flags", ctypes.c_uint32), ("mode", ctypes.c_int32),
                                ("offset", ctypes.c_int64), ("length", ctypes.c_int64), ("allocated", ctypes.c_int64)]
                request = Allocation(4, 3, 0, size, 0)  # F_ALLOCATEALL, F_PEOFPOSMODE
                fcntl.fcntl(fd, 42, bytes(request))
            elif hasattr(os, "posix_fallocate"):
                os.posix_fallocate(fd, 0, size)
            block = bytes(65536)
            position = 0
            while position < size:
                written = os.pwrite(fd, block[:min(len(block), size - position)], position)
                if not written:
                    raise OSError("Owner journal initialization made no progress")
                position += written
            os.fsync(fd)
        except BaseException:
            # A partial initial allocation is never an owner journal. There are
            # no registered children until initialization has completed.
            Path(path).unlink(missing_ok=True)
            raise
        finally:
            os.close(fd)
        directory = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def append(self, event, cleanup=False, **values):
        value = dict(values, event=event)
        packet = frame(self.sequence + 1, value)
        boundary = self.size if cleanup else self.size * 3 // 4
        if self.damaged or self.used + len(packet) + HEADER.size > boundary:
            if cleanup:
                self.emergency = True
            raise exhausted("Owner journal cleanup reserve is unavailable" if cleanup else
                            "Owner journal cleanup reserve cannot admit new ownership", used=self.used, bytes=self.size)
        start = self.used
        try:
            sent = 0
            while sent < len(packet):
                count = os.pwrite(self.fd, packet[sent:], start + sent)
                if not count:
                    raise OSError("Owner journal append made no progress")
                sent += count
            os.fsync(self.fd)
        except OSError as exc:
            # A torn/uncertain transition is never overwritten or treated as an acknowledgement.
            self.damaged = self.emergency = True
            raise exhausted("Owner journal append could not be made durable", errno=exc.errno) from exc
        self.used += len(packet)
        self.sequence += 1
        # One row applied, not the whole log folded again: 17 to 20 ms an append at the user's 55,932 records (2.4.18).
        fold(self.state, dict(value, sequence=self.sequence, boot_session=boot_session()))
        return self.sequence

    def compact(self, normal):
        if not normal or self.damaged or self.used <= self.size // 2:
            return False
        current = self.state
        rows = []
        if current["custodian"]:
            rows.append({k: v for k, v in current["custodian"].items() if k not in ("sequence", "boot_session")})
        rows.append({"event": "INHIBIT", "value": current["inhibited"]})
        if current["worker"]:
            rows.append({"event": "WORKER", "worker": current["worker"]})
        # Dormant/recovering consumers may request these receipts after restart. A listener's FAMILY_GONE stays until
        # the worker, holding it durably in its ledger, forgets the family (FORGOTTEN: absent from `current`); a GONE
        # family of any other kind, which only in-memory handles ask about, stays for the retention (2.4.12).
        now = time.time()
        rows += [{"event": "SNAPSHOT", "family": retained(f)} for f in current["families"].values()
                 if not self.expired(f, now)]
        packets = [frame(self.sequence + i + 1, row) for i, row in enumerate(rows)]
        if sum(map(len, packets)) + HEADER.size > self.size // 2:
            return False  # retain the source and refuse new ownership before invading cleanup space
        temporary = self.path.with_name("owner-" + uuid.uuid4().hex + ".journal")
        fd = None
        try:
            self.allocate(temporary, self.size)
            fd = os.open(temporary, os.O_RDWR | os.O_CLOEXEC)
            position = 0
            for packet in packets:
                while packet:
                    count = os.pwrite(fd, packet, position)
                    if not count:
                        raise OSError("Owner compaction made no progress")
                    position += count
                    packet = packet[count:]
            os.fsync(fd)
            os.replace(temporary, self.path)
            # Replacement, not directory fsync, hands over the authoritative
            # inode. Never acknowledge another append against the unlinked one.
            old, self.fd, fd = self.fd, fd, None
            os.close(old)
            self.state = state(dict(row, sequence=self.sequence + i + 1, boot_session=boot_session())
                               for i, row in enumerate(rows))
            self.sequence += len(rows)
            self.used = position
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                self.damaged = self.emergency = True
                raise
            return True
        finally:
            if fd is not None:
                os.close(fd)
            temporary.unlink(missing_ok=True)

    def expired(self, family, now):
        """A GONE family nothing can still ask about: not a listener's (the worker forgets those), and past the
        retention since its FAMILY_GONE. One written before 2.4.12 has no time; the worker that launched it, the only
        one that could ask, belonged to an earlier start."""
        if family["state"] != "GONE" or family.get("kind") == "listener":
            return False
        gone = family.get("gone_at")
        return gone is None or now - gone >= self.retention

    def status(self):
        return {"bytes": self.size, "used": self.used, "sequence": self.sequence,
                "alarm": self.used >= self.size * .60, "cleanup_only": self.used >= self.size * .75,
                "read_only_cleanup": self.emergency, "damaged": self.damaged, "retention_s": self.retention}

    def close(self):
        os.close(self.fd)
