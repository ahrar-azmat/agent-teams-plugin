"""Redundant copies of the same kernel lease, transferred before admission.

Length-framed SCM_RIGHTS messages have explicit acknowledgments. On any uncertain
handoff the sender retains its reference. A surviving worker restores a custodian
using inherited duplicates; neither side releases/reacquires the flock.
"""
import array
import asyncio
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import uuid

from .client import CoordinatorError, launch_environment, venv_fds


MAX_FRAME = 1024 * 1024


def send(connection, message, fds=()):
    data = json.dumps(message).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Lease frame exceeds limit")
    packet = struct.pack("!I", len(data)) + data
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
    sent = connection.sendmsg([packet], ancillary)
    connection.sendall(packet[sent:])


def receive(connection, count=0):
    descriptors = []
    try:
        # Receive only the header with recvmsg so descriptors cannot be lost to
        # a buffered stream or accidentally attached to the following frame.
        data, ancillary, flags, _ = connection.recvmsg(4, socket.CMSG_SPACE(64 * array.array("i").itemsize))
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                values.frombytes(payload[:len(payload) - len(payload) % values.itemsize])
                descriptors.extend(values)
        if flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC):
            raise ValueError("Truncated lease frame/descriptors")
        while len(data) < 4:
            chunk = connection.recv(4 - len(data))
            if not chunk:
                raise EOFError("Custodian disconnected")
            data += chunk
        size = struct.unpack("!I", data)[0]
        if size > MAX_FRAME:
            raise ValueError("Lease frame exceeds limit")
        body = b""
        while len(body) < size:
            chunk = connection.recv(size - len(body))
            if not chunk:
                raise EOFError("Custodian disconnected")
            body += chunk
        message = json.loads(body)
        expected = count if message.get("ok", True) else 0
        if len(descriptors) != expected:
            raise ValueError("Unexpected lease descriptors")
        for fd in descriptors:
            os.set_inheritable(fd, False)
        return message, descriptors
    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise


class RecoveryEndpoint:
    """A private, birth-bound reconnection route to a surviving custodian."""
    def __init__(self, root):
        from . import runtime, processes
        self.allocation = runtime.allocate("custodian")
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.allocation["path"])
        os.chmod(self.allocation["path"], 0o600)
        self.socket.listen(4)
        runtime.atomic_json(Path(root) / "custodian.json", {
            "state": str(root), "allocation": self.allocation,
            "process": processes.identity(os.getpid())})

    def close(self):
        from . import runtime
        self.socket.close()
        runtime.cleanup(self.allocation)


class Leases:
    def __init__(self, root):
        self.root = root
        self.lock = asyncio.Lock()
        self.held = {}
        self.connection = None
        self.replacement = None
        self.controller = None
        if os.environ.get("SOFTWARE_WORKFLOWS_CUSTODIAN_FD"):
            self.connection = socket.socket(fileno=int(os.environ.pop("SOFTWARE_WORKFLOWS_CUSTODIAN_FD")))
            self.connection.settimeout(15)

    def repair(self):
        from . import compat, processes, runtime
        if self.connection:
            self.connection.close()
        try:
            manifest = runtime.private_json(Path(self.root) / "custodian.json")
            current = processes.identity(manifest["process"]["pid"])
            living = current and not current["zombie"] and current["birth"] == manifest["process"]["birth"]
        except FileNotFoundError:
            living = False
        if living:
            if manifest["state"] != str(self.root):
                raise ValueError("Custodian recovery manifest belongs to another state root")
            allocation = manifest["allocation"]
            runtime.validate_allocation(allocation, observational=True)
            address = runtime.endpoint_info(allocation["path"], measured_limit=allocation["measured_limit"])["resolved"]
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(20)
            try:
                connection.connect(address)
                send(connection, {"action": "reconnect", "worker": processes.identity(os.getpid()), "count": len(self.held)})
                ready, _ = receive(connection)
                if not ready.get("ok"):
                    raise ValueError("Custodian refused worker reconnection")
                # A lost release receipt can leave the worker with the only
                # reference. Transfer those references back before proceeding.
                for record in self.held.values():
                    send(connection, {"lease": record}, (compat._HELD[record["path"]],))
                response, _ = receive(connection)
                for _ in range(response["count"]):
                    item, descriptors = receive(connection, 1)
                    record = item["lease"]
                    try:
                        compat.adopt_lease(record, descriptors[0])
                    except BaseException:
                        os.close(descriptors[0])
                        raise
                    self.held[record["path"]] = record
                send(connection, {"action": "ack", "tokens": sorted(r["token"] for r in self.held.values())})
                receipt, _ = receive(connection)
                if not receipt.get("ok"):
                    raise ValueError("Custodian did not confirm restored redundancy")
                self.controller = response["custodian"]
                self.connection = connection
                return
            except BaseException:
                connection.close()
                raise
        parent, child = socket.socketpair()
        parent.settimeout(15)
        environment = launch_environment()
        environment["SOFTWARE_WORKFLOWS_RECOVERY_FD"] = str(child.fileno())
        environment["SOFTWARE_WORKFLOWS_RECOVERY_WORKER"] = json.dumps(processes.identity(os.getpid()))
        records = [dict(record, fd=compat._HELD[record["path"]]) for record in self.held.values()]
        environment["SOFTWARE_WORKFLOWS_RECOVERY_LEASES"] = json.dumps(records)
        inherited = venv_fds() + (child.fileno(),) + tuple(r["fd"] for r in records)
        try:
            if self.replacement is not None:
                self.replacement.poll()
            self.replacement = subprocess.Popen([sys.executable, "-B", "-m", "coordinator", "daemon", "serve", "--state", str(self.root)],
                env=environment, cwd=str(Path(__file__).resolve().parents[1]), pass_fds=inherited,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
        self.connection = parent
        send(parent, {"action": "ping"})
        response, _ = receive(parent)
        if not response.get("ok"):
            raise CoordinatorError("CUSTODIAN_LOST", "Redundant custody could not be restored; production refused")
        self.controller = response["custodian"]

    def exchange(self, action, cwd):
        from . import compat
        if self.connection is None:
            raise CoordinatorError("CUSTODIAN_REQUIRED", "Native writers require Client.ensure supervision")
        send(self.connection, {"action": action, "cwd": cwd, "worker": os.getpid()})
        response, fds = receive(self.connection, 1 if action == "acquire" else 0)
        if not response["ok"]:
            raise CoordinatorError("EXCLUSIVE_WRITER", response["holder"])
        if response.get("custodian"):
            self.controller = response["custodian"]
        elif response.get("custodian_pid"):
            from . import processes
            self.controller = processes.identity(response["custodian_pid"])
        if action == "acquire":
            record = response["lease"]
            try:
                compat.adopt_lease(record, fds[0])
            except BaseException:
                os.close(fds[0])
                raise
            self.held[record["path"]] = record
            send(self.connection, {"action": "ack", "token": record["token"]})
            receipt, _ = receive(self.connection)
            if not receipt.get("ok") or receipt.get("token") != record["token"]:
                raise ValueError("Custodian did not acknowledge redundant ownership")
        elif action == "release":
            for path, record in list(self.held.items()):
                if record["cwd"] == cwd:
                    compat.release_lease(record, bridges=False)
                    del self.held[path]
        return response

    async def call(self, action, cwd=""):
        async with self.lock:
            try:
                return await asyncio.to_thread(self.exchange, action, cwd)
            except (OSError, EOFError, ValueError) as exc:
                # Acquiring may already have succeeded. Retain every reference
                # and rebuild redundancy before another admission can proceed.
                try:
                    await asyncio.to_thread(self.repair)
                except Exception as repair_error:
                    raise CoordinatorError("CUSTODIAN_LOST", "Lease references retained; redundancy unavailable, production refused", operational=True) from repair_error
                if action == "ping":
                    return {"ok": True}
                raise CoordinatorError("LEASE_UNCERTAIN", "Custodian replaced; retained leases require reconciliation before retry", operational=True) from exc


class Custodian:
    def __init__(self, records=()):
        from . import compat
        self.compat = compat
        self.held = {}
        compat._register_server()
        for record in records:
            fd = record.pop("fd")
            compat.adopt_lease(record, fd)
            self.held[compat._tree_identity(record["cwd"])] = record

    def reconnect(self, connection, worker):
        from . import processes
        request, _ = receive(connection)
        identity = request.get("worker", {})
        if request.get("action") != "reconnect" or not worker or any(identity.get(k) != worker.get(k) for k in ("pid", "birth")):
            raise ValueError("Custodian recovery requires the existing worker birth identity")
        count = request.get("count")
        if type(count) is not int or not 0 <= count <= 10000:
            raise ValueError("Invalid recovery lease count")
        send(connection, {"ok": True})
        for _ in range(count):
            item, descriptors = receive(connection, 1)
            record, fd = item["lease"], descriptors[0]
            try:
                key = self.compat._tree_identity(record["cwd"])
                old = self.held.get(key)
                if old and old != record:
                    raise ValueError("Conflicting recovered lease identity")
                self.compat.adopt_lease(record, fd)
            except BaseException:
                os.close(fd)
                raise
            self.held[key] = record
        for record in self.held.values():
            self.compat.share_lease(record, worker["pid"], os.getpid())
        send(connection, {"ok": True, "count": len(self.held), "custodian": processes.identity(os.getpid())})
        for record in self.held.values():
            send(connection, {"lease": record}, (self.compat._HELD[record["path"]],))
        ack, _ = receive(connection)
        if ack != {"action": "ack", "tokens": sorted(r["token"] for r in self.held.values())}:
            raise ValueError("Incomplete recovered lease acknowledgment")
        send(connection, {"ok": True})

    def request(self, connection, request):
        from . import processes
        action = request["action"]
        if action == "ping":
            send(connection, {"ok": True, "custodian": processes.identity(os.getpid())})
            return
        cwd = request["cwd"]
        identity = self.compat._tree_identity(cwd)
        if action == "acquire":
            if identity not in self.held:
                ok, holder = self.compat._acquire_write_lock(cwd, "coordinator-writer-group")
                if not ok:
                    send(connection, {"ok": False, "holder": holder})
                    return
                self.held[identity] = dict(self.compat.lease_record(cwd), token=uuid.uuid4().hex)
            record = self.held[identity]
            # Existing bridges already name the previous controllers. Before
            # production, repoint them to this worker and this custodian.
            self.compat.share_lease(record, request["worker"], os.getpid())
            send(connection, {"ok": True, "identity": identity, "custodian_pid": os.getpid(),
                              "custodian": processes.identity(os.getpid()), "lease": record},
                 (self.compat._HELD[record["path"]],))
            ack, _ = receive(connection)
            if ack != {"action": "ack", "token": record["token"]}:
                raise ValueError("Invalid lease acknowledgment")
            send(connection, {"ok": True, "token": record["token"]})
        elif action == "release":
            record = self.held.get(identity)
            if record:
                self.compat.release_lease(record)
                del self.held[identity]
            send(connection, {"ok": True, "identity": identity})
        else:
            raise ValueError("Unsupported custodian operation")
