"""The supervisor's single process authority; no ledger or native protocol here."""
import fcntl
import json
import os
from pathlib import Path
import select
import selectors
import signal
import socket
import subprocess
import sys
import time

from . import processes
from .client import CoordinatorError, launch_environment, validate_state, venv_fds
from .leases import MAX_FRAME, Custodian, RecoveryEndpoint, receive, send
from .owner_journal import Journal

TERM_SECONDS = 5.0  # Codex's 2 s MCP escalation plus measured idle shutdown and headroom.
KILL_SECONDS = 2.0
# owner/status and owner/gone: one page's families, encoded as send encodes them. Half the owner channel's frame: the
# rest of a reply is under 2 KB (an identity, the journal's status, a count per kind and state), and a page still holds
# ~3,140 of the 167-byte listener entries (measured, 2.4.18).
PAGE_BYTES = MAX_FRAME // 2


class Owner:
    def __init__(self, root, journal):
        self.root, self.journal = root, journal
        self.identity = processes.identity(os.getpid())
        self.families = dict(journal.state["families"])
        self.children = {}
        self.waiters = {}
        self.deadlines = {}
        self.pending_gone = set()
        self.forgotten = 0

    def record(self, event, family, cleanup=False):
        sequence = self.journal.append(event, cleanup=cleanup, family=family)
        family["owner_sequence"] = sequence
        self.families[family["listener"]] = family
        return sequence

    def snapshot(self, family, cleanup=False):
        # In the opt-out model a surviving worker starts its replacement owner.
        # That independent authority and its families are never worker children
        # for cleanup purposes, despite the temporary kernel parent link.
        # A GONE family compacted to its retained form keeps an empty member list (owner_journal.retained): its root.
        members = processes.lineage(family.get("members") or [family["root"]], exclude=(os.getpid(),))
        family = dict(family, members=members)
        try:
            self.record("SNAPSHOT", family, cleanup=cleanup)
        except CoordinatorError:
            if not cleanup:
                raise
            # Read-only emergency cleanup retains the new snapshot in memory.
            self.families[family["listener"]] = family
        return family

    def launch(self, request, descriptors):
        ident = request["listener"]
        existing = self.families.get(ident)
        if existing and existing["state"] != "GONE":
            raise CoordinatorError("OWNER_EXISTS", "Listener already has a registered family", {"listener": ident})
        if self.journal.state["inhibited"]:
            raise CoordinatorError("AUTOSTART_INHIBITED", "Coordinator is explicitly stopped; run daemon start", operational=True)
        read_fd, write_fd = os.pipe()
        process = None
        try:
            script = ('IFS= read -r p <&' + str(read_fd) + ' || exit 125; [ "$p" = GO ] || exit 125; '
                      'exec ' + str(read_fd) + '<&-; exec "$@"')
            stdio = request.get("stdio", {})
            options = {name: descriptors[index] for name, index in stdio.items()}
            environment = launch_environment()
            environment.update(request.get("env_delta", {}))
            argv = list(request["argv"])
            for index, descriptor in request.get("fd_arguments", {}).items():
                argv[int(index)] = str(descriptors[descriptor])
            process = subprocess.Popen(["/bin/sh", "-c", script, "sw-gate", *argv],
                cwd=request.get("cwd"), env=environment, stdin=options.get("stdin", subprocess.DEVNULL),
                stdout=options.get("stdout", subprocess.DEVNULL), stderr=options.get("stderr", subprocess.DEVNULL),
                pass_fds=(read_fd, *descriptors), close_fds=True, start_new_session=True)
            identity = processes.identity(process.pid)
            if identity is None:
                raise CoordinatorError("OWNER_GATE", "Child exited before registration", operational=True)
            family = {"listener": ident, "kind": request["kind"], "state": "OWNED", "root": identity,
                      "members": [identity], "supervisor": self.identity, "worker": None}
            self.record("OWNED", family)
            self.children[ident] = process
            if request["kind"] == "worker":
                self.journal.append("WORKER", worker=identity)
            # Only a durable OWNED record opens the gate. No argv/env enters the journal.
            os.write(write_fd, b"GO\n")
            return family
        except BaseException:
            if ident in self.families:
                self.begin_end(ident)
            raise
        finally:
            os.close(read_fd)
            os.close(write_fd)  # failure/death before GO means EOF and exit 125
            if process is not None and ident not in self.children:
                process.wait()  # its closed gate must exit; never signal an unregistered payload

    def grant(self, ident, worker):
        family = self.families[ident]
        if family["state"] in ("REAPING", "GONE"):
            raise CoordinatorError("OWNER_REAPING", "Family is no longer available for adoption", {"listener": ident})
        try:
            family = self.snapshot(family)
            family = dict(family, state="GRANTED", worker=worker, supervisor=self.identity)
            self.record("GRANTED", family)
        except BaseException:
            self.begin_end(ident)
            raise
        return family

    def adopt(self, ident, worker):
        family = self.families.get(ident)
        if not family:
            raise CoordinatorError("OWNER_UNREGISTERED", "No owner journal identity for this listener", {"listener": ident})
        if family["state"] in ("REAPING", "GONE"):
            raise CoordinatorError("OWNER_REAPING", "Cleanup already owns this family", {"listener": ident})
        if processes.exit_evidence(family["root"]):
            self.begin_end(ident)
            raise CoordinatorError("OWNER_EXITED", "Registered listener has exited", {"listener": ident})
        if family["state"] == "ORPHANED":
            self.record("ADOPTED", dict(family, state="OWNED", supervisor=self.identity))
        return self.grant(ident, worker)

    def adopt_legacy(self, listener, worker):
        """Import a predecessor's durable listener identity before using it.

        The adopted worker supplies only its saved listener/lineage evidence,
        never a process name or an arbitrary PID to discover. Threadless legacy
        bootstraps remain unregistered; their only safe automatic end is verified
        exit. Registration and a fresh lineage snapshot precede the grant.
        """
        ident = listener["id"]
        if ident in self.families:
            return self.adopt(ident, worker)  # journal commit may precede ledger save
        if not listener.get("root_thread") or not listener.get("birth"):
            raise CoordinatorError("OWNER_UNREGISTERED", "Legacy listener lacks a saved thread and process identity",
                                   {"listener": ident}, True)
        if processes.exit_evidence(listener):
            raise CoordinatorError("OWNER_EXITED", "Legacy listener has exited", {"listener": ident}, True)
        root = processes.identity(listener["pid"])
        if not root or root["birth"] != listener["birth"] or root["uid"] != os.getuid() or any(
                listener.get(key) is not None and root.get(key) != listener[key] for key in ("kernel_id", "boot_session")):
            raise CoordinatorError("OWNER_UNREGISTERED", "Legacy listener identity changed during adoption",
                                   {"listener": ident}, True)
        if any(f["root"].get("kernel_id") == root["kernel_id"] and f["root"].get("boot_session") == root["boot_session"]
               and f["state"] != "GONE" for f in self.families.values()):
            raise CoordinatorError("OWNER_EXISTS", "Legacy identity already belongs to another registered family",
                                   {"listener": ident}, True)
        members = [root, *(p for p in listener.get("process_lineage", []) if p.get("lineage"))]
        family = {"listener": ident, "kind": "listener", "state": "OWNED", "root": root,
                  "members": processes.lineage(members, exclude=(os.getpid(),)),
                  "supervisor": self.identity, "worker": None, "legacy": True}
        self.record("LEGACY_ADOPTED", family)
        return self.grant(ident, worker)

    def begin_end(self, ident):
        family = self.families.get(ident)
        if not family:
            raise CoordinatorError("OWNER_UNREGISTERED", "Cannot end an unregistered family", {"listener": ident})
        if family["state"] == "GONE" or ident in self.deadlines:
            return
        family = self.snapshot(family, cleanup=True)  # must precede every signal
        family = dict(family, state="REAPING")
        try:
            self.record("REAPING", family, cleanup=True)
        except CoordinatorError:
            self.families[ident] = family
        self.deadlines[ident] = ("root", time.monotonic() + TERM_SECONDS)
        try:
            processes.signal_identity(family["root"], signal.SIGTERM)
            processes.signal_identity(family["root"], signal.SIGCONT)
        except (CoordinatorError, OSError):
            pass  # identity unreadable: retained and retried, never GONE

    def service(self):
        now = time.monotonic()
        for ident, (stage, deadline) in list(self.deadlines.items()):
            family = self.families[ident]
            try:
                root_gone = processes.exit_evidence(family["root"])
                if stage == "root" and (root_gone or now >= deadline):
                    family = self.snapshot(family, cleanup=True)
                    for member in family["members"]:
                        processes.signal_identity(member, signal.SIGTERM)
                        processes.signal_identity(member, signal.SIGCONT)
                    stage, deadline = "term", now + KILL_SECONDS
                    self.deadlines[ident] = (stage, deadline)
                evidence = [processes.exit_evidence(member) for member in family["members"]]
                if all(evidence):
                    family = dict(family, state="GONE", exit_evidence=evidence, gone_at=time.time())
                    child = self.children.get(ident)
                    if child:
                        family["returncode"] = child.poll()
                    try:
                        self.record("FAMILY_GONE", family, cleanup=True)
                    except CoordinatorError:
                        self.pending_gone.add(ident)
                        continue  # custody waits for a durable FAMILY_GONE
                    self.pending_gone.discard(ident)
                    del self.deadlines[ident]
                elif stage != "root" and now >= deadline:
                    for member in family["members"]:
                        processes.signal_identity(member, signal.SIGKILL)
                    self.deadlines[ident] = ("kill", now + KILL_SECONDS)
            except (CoordinatorError, OSError):
                # Unreadable identities and survivors remain REAPING; retry only while cleanup is active.
                continue

    def forget(self, idents):
        """Drop GONE families whose FAMILY_GONE receipt the worker now holds in its ledger, so the next compaction no
        longer carries them (2.4.12). A family that is not GONE, or that an `end` still awaits, stays; a listener this
        owner does not hold is ignored, so a repeated forget is harmless."""
        gone = [ident for ident in dict.fromkeys(idents)
                if (self.families.get(ident) or {}).get("state") == "GONE" and ident not in self.waiters]
        if gone:
            self.journal.append("FORGOTTEN", cleanup=True, listeners=gone)
            for ident in gone:
                del self.families[ident]
                self.children.pop(ident, None)
            self.forgotten += len(gone)
        return len(gone)

    def prune(self):
        """After a compaction: drop from memory the GONE families it left out (past the retention), with their
        reaped child handles, and keep the other GONE ones that are no listener's in the form it kept
        (owner_journal.retained, 2.4.19). One an end still awaits stays whole."""
        kept = self.journal.state["families"]
        for ident, family in list(self.families.items()):
            if family["state"] != "GONE" or ident in self.waiters:
                continue
            if ident not in kept:
                del self.families[ident]
                self.children.pop(ident, None)
            elif family.get("kind") != "listener":
                self.families[ident] = kept[ident]

    @staticmethod
    def entry(family):
        return {"listener": family["listener"], "state": family["state"], "kind": family["kind"],
                "pid": family["root"]["pid"], "members": len(family.get("members") or ()),
                "owner_sequence": family.get("owner_sequence"), "worker": (family.get("worker") or {}).get("kernel_id")}

    def page(self, chosen, after=None):
        """The families `chosen` accepts in listener order after `after`, as many as PAGE_BYTES holds as send encodes
        them, and the last one listed when more follow (else None)."""
        page, size = [], 0
        for ident in sorted(i for i, f in self.families.items() if chosen(f) and (after is None or i > after)):
            entry = self.entry(self.families[ident])
            size += len(json.dumps(entry)) + 2
            if page and size > PAGE_BYTES:
                return page, page[-1]["listener"]
            page.append(entry)
        return page, None

    def status(self, after=None):
        """Every family counted by kind and state (`counts`), and those not GONE listed a page at a time after `after`
        (`next` continues). Not GONE is no live process: a family whose every member has exited stays REAPING while
        its FAMILY_GONE cannot be written (service), so that list is bounded by the journal alone too (review turn
        111). The GONE ones, kept for the journal's retention (2.4.12), are read with owner/gone. Through 2.4.17 every
        family was listed at once: on the user's journal on 10-29, 6,446 GONE of 6,463 families, 6,437 of them hook
        helpers, encoded to 1,064,936 bytes, past the owner channel's frame (leases.MAX_FRAME, 1 MiB). Every
        daemon/status then failed OWNER_OPERATION (send's ValueError), and every Client.ensure with it."""
        counts = {}
        for family in self.families.values():
            key = family["kind"] + ":" + family["state"]
            counts[key] = counts.get(key, 0) + 1
        families, following = self.page(lambda f: f["state"] != "GONE", after)
        return {"custodian": self.identity, "journal": self.journal.status(),
                "inhibited": self.journal.state["inhibited"],
                "cleanup": "launchd restart" if os.environ.get("SOFTWARE_WORKFLOWS_LAUNCH_AGENT") else
                           "crash cleanup on next start only",
                "gone_retained": sum(f["state"] == "GONE" for f in self.families.values()), "forgotten": self.forgotten,
                "counts": counts, "families": families, "next": following}

    def gone(self, kind, after=None):
        """The GONE families of `kind` a page at a time after `after`, `next` continuing (the abandoned-launch sweep
        asks for listeners)."""
        families, following = self.page(lambda f: f["state"] == "GONE" and f["kind"] == kind, after)
        return {"families": families, "next": following}


def failure(exc):
    """The answer to an owner operation that failed: its CoordinatorError, any other exception as OWNER_OPERATION."""
    error = exc if isinstance(exc, CoordinatorError) else CoordinatorError("OWNER_OPERATION", "Owner operation could not complete",
        {"cause": type(exc).__name__, "errno": getattr(exc, "errno", None), "retryable": True}, True)
    return {"ok": False, "error": error.envelope()}


def activated_socket():
    import ctypes
    lib = ctypes.CDLL(None, use_errno=True)
    descriptors, count = ctypes.POINTER(ctypes.c_int)(), ctypes.c_size_t()
    result = lib.launch_activate_socket(b"Custodian", ctypes.byref(descriptors), ctypes.byref(count))
    if result:
        raise OSError(result, "Cannot activate custodian socket")
    try:
        if count.value != 1:
            for i in range(count.value):
                os.close(descriptors[i])
            raise ValueError("Expected one custodian socket")
        return socket.socket(fileno=descriptors[0])
    finally:
        lib.free(descriptors)


def supervise(root):
    from .stop_control import StopControl
    with StopControl() as control:
        return _supervise(root, control)


def _supervise(root, control):
    root = validate_state(root, create=True)
    os.umask(0o077)
    agent = bool(os.environ.get("SOFTWARE_WORKFLOWS_LAUNCH_AGENT"))
    with open(root / "supervisor.lock", "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if agent else fcntl.LOCK_NB))
        except BlockingIOError:
            return 0
        journal = Journal(root)
        owner = Owner(root, journal)
        custodian = Custodian(json.loads(os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_LEASES", "[]")))
        rescue = None
        endpoint = activated_socket() if agent else None
        if endpoint is None:
            rescue = RecoveryEndpoint(root, journal=journal)
            endpoint = rescue.socket
        else:
            try:
                journal.append("CUSTODIAN", cleanup=True, process=owner.identity, state=str(root), address=endpoint.getsockname(), agent=True)
            except CoordinatorError:
                pass  # an activated endpoint can still report/read and reap without a new record
        if os.environ.pop("SOFTWARE_WORKFLOWS_EXPLICIT_START", None):
            journal.append("INHIBIT", cleanup=True, value=False)
            (root / "autostart-inhibit").unlink(missing_ok=True)
        recovered_fd = os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_FD", None)
        recovered = json.loads(os.environ.pop("SOFTWARE_WORKFLOWS_RECOVERY_WORKER", "null"))
        worker = recovered or journal.state["worker"]
        channel = socket.socket(fileno=int(recovered_fd)) if recovered_fd else None
        child = None
        started_worker = False
        stopping = False
        stop_worker_at = None
        retry_at = None
        selector = selectors.DefaultSelector()
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_read, False)
        os.set_blocking(wake_write, False)
        control.wake_fd = wake_write
        selector.register(wake_read, selectors.EVENT_READ, "stop")
        queue = select.kqueue()
        selector.register(queue, selectors.EVENT_READ, "exit")
        selector.register(endpoint, selectors.EVENT_READ, "accept")

        def watch(record):
            if record and not processes.exit_evidence(record):
                try:
                    queue.control([select.kevent(record["pid"], select.KQ_FILTER_PROC,
                                  select.KQ_EV_ADD | select.KQ_EV_ONESHOT, select.KQ_NOTE_EXIT)], 0)
                except ProcessLookupError:
                    return

        def register(connection):
            connection.settimeout(20)
            selector.register(connection, selectors.EVENT_READ, "request")

        def detached():
            nonlocal channel
            if channel:
                try:
                    selector.unregister(channel)
                except KeyError:
                    pass
                channel.close()
                channel = None

        if worker and processes.exit_evidence(worker):
            worker = None
        if worker:
            watch(worker)
        for ident, family in list(owner.families.items()):
            if family["state"] == "GONE":
                continue
            if family["kind"] == "worker":
                if not worker or family["root"]["kernel_id"] != worker["kernel_id"]:
                    owner.begin_end(ident)
                continue
            if worker and family["state"] != "REAPING":
                try:
                    owner.record("ORPHANED", dict(family, state="ORPHANED"), cleanup=True)
                except CoordinatorError:
                    owner.families[ident] = dict(family, state="ORPHANED")
            else:
                owner.begin_end(ident)
        if recovered:
            journal.append("ADOPTED_WORKER", worker=recovered)
            custodian.production_ready = False
        if channel:
            register(channel)
        try:
            while True:
                if control.requested and not stopping:
                    stopping = True
                    if agent:
                        journal.append("INHIBIT", cleanup=True, value=True)
                    for ident, family in list(owner.families.items()):
                        if family["state"] != "GONE":
                            owner.begin_end(ident)
                owner.service()
                for ident, connections in list(owner.waiters.items()):
                    if owner.families[ident]["state"] == "GONE":
                        for connection in connections:
                            try:
                                send(connection, {"ok": True, "family": owner.families[ident]})
                            except OSError:
                                pass
                            except Exception as exc:
                                # As a request's failure is answered (failure): send's ValueError for a family past the
                                # frame, raised here, ended the supervisor (review turn 111).
                                try:
                                    send(connection, failure(exc))
                                except OSError:
                                    pass
                        del owner.waiters[ident]
                if worker and processes.exit_evidence(worker):
                    if child:
                        child.poll()
                    detached()
                    try:
                        journal.append("WORKER_GONE", cleanup=True)
                    except CoordinatorError:
                        pass
                    worker = None
                    stop_worker_at = None
                    for ident, family in list(owner.families.items()):
                        if family["kind"] == "worker" and family["state"] != "GONE":
                            owner.begin_end(ident)
                        if family["state"] != "GONE":
                            owner.begin_end(ident)
                    if stopping or child and (child.returncode == 4 or child.returncode == 3 or child.returncode == 0 and not custodian.held):
                        stopping = True
                if stopping and not worker and not owner.deadlines:
                    return 0
                if stop_worker_at is not None and time.monotonic() >= stop_worker_at:
                    for ident, family in list(owner.families.items()):
                        if family["kind"] == "worker" and family["state"] != "GONE":
                            owner.begin_end(ident)
                    stop_worker_at = None
                if not worker and not stopping and not owner.deadlines and not journal.state["inhibited"] and (retry_at is None or time.monotonic() >= retry_at):
                    parent, peer = socket.socketpair()
                    environment = launch_environment()
                    environment["SOFTWARE_WORKFLOWS_CUSTODIAN_FD"] = str(peer.fileno())
                    if agent:
                        environment["SOFTWARE_WORKFLOWS_LAUNCH_AGENT"] = "1"
                    from .daemon import upgrade_claim
                    upgrade = None
                    try:
                        upgrade = upgrade_claim(root) if started_worker else None
                        if started_worker and upgrade is None:
                            retry_at = time.monotonic() + 1
                            parent.close()
                            continue
                        inherited = [peer.fileno(), *venv_fds()]
                        if upgrade is not None:
                            inherited.append(upgrade)
                            environment["SOFTWARE_WORKFLOWS_UPGRADE_FD"] = str(upgrade)
                        ident = "worker-" + str(journal.sequence + 1)
                        family = owner.launch({"kind": "worker", "listener": ident,
                            "argv": [sys.executable, "-B", "-m", "coordinator", "daemon", "worker", "--state", str(root)],
                            "cwd": str(Path(__file__).resolve().parents[1]), "env_delta": environment}, inherited)
                        worker = family["root"]
                        child = owner.children[ident]
                        started_worker = True
                        channel = parent
                        register(channel)
                        watch(worker)
                        retry_at = None
                    except (CoordinatorError, OSError):
                        parent.close()
                        retry_at = time.monotonic() + 1  # active failed spawn only; no idle timer
                    finally:
                        peer.close()
                        if upgrade is not None:
                            os.close(upgrade)
                timeout = None
                if journal.used > journal.size // 2 and not journal.damaged:
                    volume = os.statvfs(root)
                    # Compaction needs one replacement allocation and metadata headroom.
                    normal = volume.f_bavail * volume.f_frsize >= 3 * journal.size
                    try:
                        if journal.compact(normal):
                            owner.prune()
                    except OSError:
                        pass  # compact retains the authoritative descriptor and inhibits uncertain appends
                if owner.deadlines:
                    timeout = .05
                if retry_at is not None:
                    timeout = max(0, retry_at - time.monotonic()) if timeout is None else min(timeout, max(0, retry_at - time.monotonic()))
                if stop_worker_at is not None:
                    remaining = max(0, stop_worker_at - time.monotonic())
                    timeout = remaining if timeout is None else min(timeout, remaining)
                for key, _ in selector.select(timeout):
                    if key.data == "stop":
                        try:
                            os.read(wake_read, 4096)
                        except BlockingIOError:
                            pass
                        continue
                    if key.data == "exit":
                        queue.control(None, 128, 0)
                        continue
                    if key.data == "accept":
                        candidate, _ = endpoint.accept()
                        register(candidate)
                        continue
                    connection = key.fileobj
                    descriptors = []
                    try:
                        request, descriptors = receive(connection, count=None)
                        action = request["action"]
                        if action == "reconnect":
                            candidate = request.get("worker")
                            if not worker or processes.exit_evidence(worker) or any(candidate.get(k) != worker.get(k) for k in ("pid", "kernel_id", "boot_session")):
                                raise CoordinatorError("OWNER_WORKER", "Worker adoption identity does not match")
                            custodian.reconnect(connection, worker, request=request,
                                adopted=lambda: journal.append("ADOPTED_WORKER", worker=worker))
                            if channel is not connection:
                                detached()
                            channel = connection
                        elif action == "owner/status":
                            send(connection, {"ok": True, **owner.status(request.get("after"))})
                        elif action == "owner/gone":
                            send(connection, {"ok": True, **owner.gone(request["kind"], request.get("after"))})
                        elif action == "owner/diagnostic":
                            # A watchdog uses a separate connection while the
                            # worker's event loop is stalled. It may only ask for
                            # the fixed diagnostic signal to this recorded worker.
                            wanted = request.get("worker") or {}
                            if not worker or any(wanted.get(k) != worker.get(k) for k in ("pid", "kernel_id", "boot_session")):
                                raise CoordinatorError("OWNER_WORKER", "Diagnostic worker identity does not match")
                            sent = False
                            if not processes.exit_evidence(worker):
                                sent = processes.signal_identity(worker, signal.SIGUSR2)
                            send(connection, {"ok": True, "sent": sent})
                        elif action == "owner/start":
                            journal.append("INHIBIT", cleanup=True, value=False)
                            (root / "autostart-inhibit").unlink(missing_ok=True)
                            stopping = False
                            send(connection, {"ok": True})
                        elif connection is not channel:
                            raise CoordinatorError("OWNER_WORKER", "Ownership changes require the adopted worker channel")
                        elif action == "owner/stop":
                            if request.get("inhibit"):
                                journal.append("INHIBIT", cleanup=True, value=True)
                                try:
                                    (root / "autostart-inhibit").touch(mode=0o600)
                                except OSError:
                                    pass  # authoritative journal is already durable
                            stopping = True
                            send(connection, {"ok": True})
                            # Let the worker flush the caller's stop response and
                            # join compensation; the owner still owns its end.
                            stop_worker_at = time.monotonic() + TERM_SECONDS
                        elif action == "forget":
                            send(connection, {"ok": True, "forgotten": owner.forget(request.get("listeners") or [])})
                        elif action == "spawn":
                            custodian.refresh(worker)
                            family = owner.launch(request, descriptors)
                            try:
                                send(connection, {"ok": True, "family": family})
                                ack, _ = receive(connection)
                                if ack != {"action": "grant", "listener": family["listener"]}:
                                    raise ValueError("Missing ownership grant acknowledgement")
                                send(connection, {"ok": True, "family": owner.grant(family["listener"], worker)})
                            except BaseException:
                                owner.begin_end(family["listener"])
                                raise
                        elif action == "adopt":
                            custodian.refresh(worker)
                            send(connection, {"ok": True, "family": owner.adopt(request["listener"], worker)})
                        elif action == "adopt-legacy":
                            custodian.refresh(worker)
                            send(connection, {"ok": True, "family": owner.adopt_legacy(request["record"], worker)})
                        elif action == "snapshot":
                            family = owner.snapshot(owner.families[request["listener"]])
                            send(connection, {"ok": True, "family": family})
                        elif action == "end-member":
                            family = owner.snapshot(owner.families[request["listener"]])
                            wanted = request["member"]
                            member = next((m for m in family["members"] if m["pid"] == wanted["pid"]
                                           and m["birth"] == wanted["birth"]), None)
                            if not member:
                                raise CoordinatorError("OWNER_UNREGISTERED", "Process is not in the recorded family; no signal sent")
                            ident = family["listener"] + ":member:" + str(member["kernel_id"])
                            if ident not in owner.families:
                                owner.record("OWNED", dict(family, listener=ident, kind="member", root=member,
                                                            members=processes.lineage([member]), state="OWNED"))
                            owner.begin_end(ident)
                            if owner.families[ident]["state"] == "GONE":
                                send(connection, {"ok": True, "family": owner.families[ident]})
                            else:
                                owner.waiters.setdefault(ident, []).append(connection)
                        elif action == "end":
                            ident = request["listener"]
                            owner.begin_end(ident)
                            if owner.families[ident]["state"] == "GONE":
                                send(connection, {"ok": True, "family": owner.families[ident]})
                            else:
                                owner.waiters.setdefault(ident, []).append(connection)
                        elif action == "watcher-stop":
                            family = owner.snapshot(owner.families[request["listener"]])
                            if family["kind"] != "watcher-probe" or family["state"] != "GRANTED":
                                raise CoordinatorError("OWNER_PROBE", "Only a granted watcher probe may be stopped")
                            processes.signal_identity(family["root"], signal.SIGSTOP)
                            send(connection, {"ok": True})
                        elif action == "poll":
                            family = owner.families.get(request["listener"])
                            if not family:  # forgotten, or compacted past the retention, once GONE (2.4.12)
                                raise CoordinatorError("OWNER_UNREGISTERED", "No owner journal identity for this family",
                                                       {"listener": request["listener"]})
                            process = owner.children.get(request["listener"])
                            code = process.poll() if process else (0 if processes.exit_evidence(family["root"]) else None)
                            if code is not None and family["state"] != "GONE":
                                owner.begin_end(family["listener"])
                            send(connection, {"ok": True, "returncode": code})
                        else:
                            if action in ("ping", "acquire"):
                                for ident, family in list(owner.families.items()):
                                    if family["state"] == "ORPHANED" and (family.get("worker") or {}).get("kernel_id") == worker["kernel_id"]:
                                        owner.adopt(ident, worker)
                            custodian.request(connection, request)
                    except (EOFError, ConnectionError):
                        if connection is channel:
                            detached()
                        else:
                            selector.unregister(connection)
                            connection.close()
                    except Exception as exc:
                        try:
                            send(connection, failure(exc))
                        except OSError:
                            pass
                    finally:
                        for fd in descriptors:
                            os.close(fd)
        finally:
            detached()
            control.wake_fd = None
            selector.close()
            os.close(wake_read)
            os.close(wake_write)
            queue.close()
            if rescue:
                rescue.close()
            else:
                endpoint.close()
            journal.close()
