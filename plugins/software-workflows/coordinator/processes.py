"""Process identity and lineage. Native terminal identifiers are never OS PIDs.

macOS environment parsing follows ../procenv.py's KERN_PROCARGS2 boundary. Only
Codex identity markers are returned; process environments are never logged.
"""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .client import CoordinatorError


class ProcBSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "rfu")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid", "nice")]
    _fields_ += [("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


class ProcUniqueInfo(ctypes.Structure):
    # XNU PROC_PIDUNIQIDENTIFIERINFO (17), the measured 56-byte installed ABI. The original
    # parent's unique ID survives reparenting; a PID or executable name does not.
    _fields_ = [("uuid", ctypes.c_uint8 * 16), ("unique", ctypes.c_uint64),
                ("parent_unique", ctypes.c_uint64), ("version", ctypes.c_int32),
                ("parent_version", ctypes.c_int32), ("reserved2", ctypes.c_uint64),
                ("reserved3", ctypes.c_uint64)]


class ProcBSDShortInfo(ctypes.Structure):
    # XNU PROC_PIDT_SHORTBSDINFO (13), 64 bytes. Unlike PROC_PIDTBSDINFO it answers for a
    # process of another user too (measured on macOS 26: every one of 216 such processes).
    _fields_ = [(name, ctypes.c_uint32) for name in ("pid", "ppid", "pgid", "status")]
    _fields_ += [("comm", ctypes.c_char * 16)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("flags", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "rfu")]


def _gone(lib, pid):
    """After a failed kernel read: whether no process of this user holds the PID. The kernel refuses
    both the read and a signal for a process of another user, such as a root daemon that took the PID
    of one that exited. None of its user ids is this user's, so it is no writer's process, hook or
    service: those run as this user, and keep its real uid across a set-user-ID program."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        short = ProcBSDShortInfo()
        if (lib.proc_pidinfo(pid, 13, 0, ctypes.byref(short), ctypes.sizeof(short)) == ctypes.sizeof(short)
                and os.getuid() not in (short.uid, short.ruid, short.svuid)):
            return True
        raise CoordinatorError("PROCESS_UNREADABLE", "Cannot verify process birth identity", {"pid": pid})
    return False


def identity(pid, _attempt=0):
    """The process's birth identity, or None when no process of this user holds the PID."""
    pid = int(pid)
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        info = ProcBSDInfo()
        size = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info):
            if _gone(lib, pid):
                return None
            if _attempt < 3:
                time.sleep(0.002)
                return identity(pid, _attempt + 1)
            raise CoordinatorError("PROCESS_UNREADABLE", "Cannot verify process birth identity", {"pid": pid})
        unique = ProcUniqueInfo()
        size = lib.proc_pidinfo(pid, 17, 0, ctypes.byref(unique), ctypes.sizeof(unique))
        if size != 56:
            if _gone(lib, pid):
                return None
            if _attempt < 3:
                time.sleep(0.002)
                return identity(pid, _attempt + 1)
            raise CoordinatorError("PROCESS_KERNEL_IDENTITY", "Kernel process identity is unavailable", {"pid": pid})
        # Check the PID did not change between the two kernel reads.
        after = ProcBSDInfo()
        if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(after), ctypes.sizeof(after)) != ctypes.sizeof(after):
            if _gone(lib, pid):
                return None
            if _attempt < 3:
                time.sleep(0.002)
                return identity(pid, _attempt + 1)
            raise CoordinatorError("PROCESS_UNREADABLE", "Cannot recheck process birth identity", {"pid": pid})
        if (after.start_sec, after.start_usec) != (info.start_sec, info.start_usec):
            raise CoordinatorError("PROCESS_REUSED", "PID changed during kernel identity lookup", {"pid": pid})
        return {"pid": pid, "birth": str(info.start_sec) + ":" + str(info.start_usec),
                "ppid": info.ppid, "uid": info.uid, "zombie": info.status == 5,
                "kernel_id": unique.unique, "parent_kernel_id": unique.parent_unique}
    if sys.platform.startswith("linux"):
        directory = Path("/proc") / str(pid)
        try:
            parts = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            return {"pid": pid, "birth": parts[19], "ppid": int(parts[1]),
                    "uid": directory.stat().st_uid, "zombie": parts[0] == "Z"}
        except FileNotFoundError:
            return None
        except PermissionError:
            # /proc mounted with hidepid=1 refuses another user's process. The directory's owner is only its
            # effective uid: a set-user-ID child of a writer keeps the writer's real uid, so nothing here shows
            # the PID is no writer's (not run on Linux).
            raise CoordinatorError("PROCESS_UNREADABLE", "Cannot verify process birth identity", {"pid": pid})
    raise CoordinatorError("PROCESS_PLATFORM", "No reliable process identity backend")


def markers(pid):
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))
        size = ctypes.c_size_t(0)
        if lib.sysctl(mib, 3, None, ctypes.byref(size), None, 0):
            raise CoordinatorError("PROCESS_ENV", "Cannot inspect native identity markers", {"pid": pid})
        buffer = ctypes.create_string_buffer(size.value)
        if lib.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
            raise CoordinatorError("PROCESS_ENV", "Cannot inspect native identity markers", {"pid": pid})
        raw = buffer.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        position = rest.find(b"\0")
        while position + 1 < len(rest) and rest[position + 1] == 0:
            position += 1
        values = rest[position + 1:].split(b"\0")[argc:]
    else:
        values = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
    wanted = {b"CODEX_THREAD_ID", b"CODEX_SESSION_ID", b"CODEX_TOOL_CALL_ID", b"SOFTWARE_WORKFLOWS_OPERATION"}
    return {key.decode(): value.decode("utf-8", "strict") for item in values if b"=" in item
            for key, value in [item.split(b"=", 1)] if key in wanted}


def descendant(record, listener):
    """Verify a live process and its ancestry against the listener's birth."""
    current = identity(record["pid"])
    if not current or current["zombie"] or current["birth"] != record["birth"]:
        return False
    seen = set()
    while current and current["pid"] not in seen:
        seen.add(current["pid"])
        if current["pid"] == listener["pid"]:
            return current["birth"] == listener["birth"] and not current["zombie"]
        current = identity(current["ppid"]) if current["ppid"] > 1 else None
    return False


def terminal_root(listener, operation, terminal, known=()):
    # processId is a native opaque string. Only osPid can name an OS process.
    pid = terminal.get("osPid")
    if type(pid) is not int or pid <= 0:
        return None
    before = identity(pid)
    if not before or before["zombie"]:
        return None
    retained = any(p["pid"] == pid and p["birth"] == before["birth"] for p in known)
    if not retained and not descendant(before, listener):
        raise CoordinatorError("TERMINAL_LINEAGE", "Native terminal PID has no verified listener ancestry", {"pid": pid})
    after = identity(pid)
    if not after or after["birth"] != before["birth"]:
        raise CoordinatorError("PROCESS_REUSED", "Native terminal process changed during binding", {"pid": pid})
    return dict(before, native=operation["native"], process_id=terminal["processId"], lineage=True, terminal_bound=True)


def baseline():
    """Complete birth identities before authorizing an opaque command."""
    try:
        result = subprocess.run(["/bin/ps", "-ax", "-o", "pid=,uid=,stat="], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:  # a fork refused under load, a ps that hangs
        raise CoordinatorError("PROCESS_SCAN", "Cannot establish a command's process baseline", {"error": type(exc).__name__})
    if result.returncode:
        raise CoordinatorError("PROCESS_SCAN", "Cannot establish a command's process baseline")
    records = []
    for line in result.stdout.splitlines():
        fields = line.split()
        pid, user = map(int, fields[:2])
        if user == os.getuid() and not fields[2].startswith("Z"):
            record = identity(pid)
            if record and not record["zombie"]:
                records.append(record)
    return records


def inventory(listener, root_thread, known=(), operation=None, services=(), ambiguous=(), baseline=None):
    """Find operation roots by native call identity, then retain birth-bound descendants.

    A kernel lineage identifies the private listener's ownership domain, not a
    particular overlapping command. Domain survivors retain EVERY potentially
    responsible reservation. Only native call markers identify exact roots.
    """
    try:
        result = subprocess.run(["/bin/ps", "-ax", "-o", "pid=,ppid=,uid=,stat="], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CoordinatorError("PROCESS_SCAN", "OS process inventory failed", {"error": type(exc).__name__}, operational=True)
    if result.returncode:
        raise CoordinatorError("PROCESS_SCAN", "OS process inventory failed", operational=True)
    candidates = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise CoordinatorError("PROCESS_SCAN", "Unrecognized process inventory row")
        pid, parent, user = map(int, fields[:3])
        if user == os.getuid() and not fields[3].startswith("Z"):
            candidates[pid] = parent
    parent_identity = identity(listener["pid"]) if listener.get("pid") else None
    listener_tree = {listener["pid"]} if parent_identity and parent_identity["birth"] == listener["birth"] and not parent_identity["zombie"] else set()
    def descendants(roots):
        while True:
            extended = roots | {pid for pid, parent in candidates.items() if parent in roots}
            if roots == extended:
                return roots
            roots = extended
    listener_tree = descendants(listener_tree)
    known_map = {entry["pid"]: entry for entry in known}
    ambiguous_pids = {entry["pid"] for entry in ambiguous}
    for entry in ambiguous:
        if entry["pid"] not in candidates:
            current = identity(entry["pid"])
            if current and not current["zombie"] and current["birth"] == entry["birth"]:
                raise CoordinatorError("PROCESS_AMBIGUOUS", "Retained ambiguous identity is alive outside the inventory",
                                       {"processes": [entry], "pids": [entry["pid"]]})
    service_map = {entry["pid"]: entry for entry in services}
    service_roots = set()
    for pid, entry in service_map.items():
        current = identity(pid)
        if current and not current["zombie"] and current["birth"] == entry["birth"]:
            service_roots.add(pid)
    # A baseline service is only this birth identity, never its future children.
    service_tree = service_roots
    def inspect(pid):
        try:
            return identity(pid)
        except CoordinatorError:
            if baseline is not None or pid in listener_tree or pid in known_map or pid in ambiguous_pids:
                raise
            return None
    observed, roots = {}, set()
    retained = list(listener.get("process_lineage", [])) + list(known)
    domain_ids = {p["kernel_id"] for p in retained if p.get("kernel_id") and p.get("lineage")}
    if parent_identity and parent_identity["birth"] == listener["birth"]:
        if parent_identity.get("kernel_id"):
            domain_ids.add(parent_identity["kernel_id"])
    elif listener.get("kernel_id"):
        domain_ids.add(listener["kernel_id"])
    identities = {pid: inspect(pid) for pid in candidates}
    while True:
        extended = domain_ids | {p["kernel_id"] for p in identities.values() if p and
                                p.get("parent_kernel_id") in domain_ids and p.get("kernel_id")}
        if extended == domain_ids:
            break
        domain_ids = extended
    ambiguous_map = {(p["pid"], p["birth"]): p for p in ambiguous}
    for pid in candidates:
        if pid in (os.getpid(), listener.get("pid")):
            continue
        before = identities[pid]
        if before is None or before["zombie"]:
            continue
        old = known_map.get(pid)
        known_birth = bool(old and old["birth"] == before["birth"])
        service = service_map.get(pid)
        service_birth = bool(service and service["birth"] == before["birth"] or pid in service_tree)
        domain = before.get("kernel_id") in domain_ids or pid in listener_tree
        # OWNERSHIP NEEDS EVIDENCE (2.3.0): a process is the writer's when it carries the writer's
        # Codex markers or its kernel lineage reaches the listener. One that appeared during a command
        # with neither is not held: on a working Mac, shells, Docker helpers and system services start
        # all the time, and holding every one kept an ordinary `/bin/sleep 0.2` from ever settling
        # (measured, C3). What escapes is a process that both clears its environment and loses its
        # parent before it is sampled; full access accepts that (README, abraham).
        try:
            env = markers(pid)
        except (CoordinatorError, OSError):
            after = inspect(pid)
            if after is None or after["zombie"]:
                continue
            env = {}
            if not (domain or known_birth or (pid, before["birth"]) in ambiguous_map):
                continue
        marked = bool(env.get("CODEX_SESSION_ID")) and env.get("CODEX_SESSION_ID") in (
            root_thread, (operation or {}).get("native", {}).get("thread"))
        after = inspect(pid)
        if after is None or after["zombie"]:
            continue
        if before["birth"] != after["birth"]:
            if baseline is not None or known_birth or pid in listener_tree:
                raise CoordinatorError("PROCESS_REUSED", "Process changed identity during inventory")
            continue
        if operation is None:
            matched = bool(root_thread and (env.get("CODEX_SESSION_ID") == root_thread or env.get("SOFTWARE_WORKFLOWS_OPERATION") == root_thread))
            owned = matched or (known_birth or domain) and not service_birth
            if (pid, before["birth"]) in ambiguous_map and not owned and not service_birth:
                observed[pid] = dict(before, markers=env, ambiguous=True)
                continue
        else:
            native = operation["native"]
            matched = (env.get("CODEX_SESSION_ID") in (root_thread, native.get("thread")) and
                       env.get("CODEX_THREAD_ID") == native.get("thread") and
                       env.get("CODEX_TOOL_CALL_ID") == native.get("call"))
            # A helper can be observed before its birth-verified hook RPC
            # arrives. Once that exact identity is attested, a prior domain
            # observation must not keep it in the preceding command's custody.
            # Its children are separate identities and remain in the inventory.
            owned = matched or (known_birth or domain) and not service_birth
            if (marked or domain or (pid, before["birth"]) in ambiguous_map) and not owned and not service_birth:
                # Siblings and helpers need their own birth-bound native evidence: a process with
                # the writer's markers or lineage that is not this call's is held for every
                # potentially responsible reservation.
                observed[pid] = dict(before, markers=env, ambiguous=True)
                continue
        observed[pid] = dict(old or {}, **before, markers=env, lineage=owned or domain, marked=matched,
                             ownership="native-call" if matched else "listener-domain" if domain else "retained")
        if owned:
            roots.add(pid)
    owned_tree = descendants(roots)
    ambiguous = [pid for pid, entry in observed.items() if entry.get("ambiguous") and pid not in owned_tree]
    if ambiguous:
        raise CoordinatorError("PROCESS_AMBIGUOUS", "New or retained process identities cannot be attributed; custody remains held until verified exit",
                               {"pids": ambiguous, "processes": [observed[pid] for pid in ambiguous]})
    return [dict(entry, lineage=True) for pid, entry in observed.items() if pid in owned_tree]


def terminate(record, root_thread):
    current = identity(record["pid"])
    if current is None or current["zombie"]:
        return {"gone": True}
    if current["birth"] != record["birth"]:
        raise CoordinatorError("PROCESS_REUSED", "Refusing to signal a reused PID")
    kernel_bound = record.get("kernel_id") and current.get("kernel_id") == record["kernel_id"]
    try:
        env = markers(record["pid"])
    except (CoordinatorError, OSError):
        env = {}
    if not record.get("lineage") or not (kernel_bound or env.get("CODEX_SESSION_ID") == root_thread):
        raise CoordinatorError("ORPHAN_IDENTITY", "Orphan ownership cannot still be verified")
    current = identity(record["pid"])
    if current is None or current["zombie"]:
        return {"gone": True}
    if current["birth"] != record["birth"]:
        raise CoordinatorError("PROCESS_REUSED", "Process identity changed before signalling")
    try:
        os.kill(record["pid"], signal.SIGKILL)
    except ProcessLookupError:
        return {"gone": True}
    return {"signalled": True, "pid": record["pid"]}
