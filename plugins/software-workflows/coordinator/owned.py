"""Async subprocess-shaped handles whose actual parent and signaller is the owner."""
import asyncio
import os
import subprocess
import time
import uuid

from . import leases, processes
from .client import CoordinatorError


class Process:
    def __init__(self, authority, family):
        self.authority, self.family = authority, family
        self.pid, self.listener = family["root"]["pid"], family["listener"]
        self.stdin = self.stdout = self.stderr = None
        self._returncode = None
        self.ending = None

    @property
    def returncode(self):
        return self._returncode

    async def poll(self):
        if self._returncode is None:
            try:
                result = await self.authority.call("poll", payload={"listener": self.listener})
            except CoordinatorError as exc:
                # Queued behind an end that finished it, the owner may have dropped the family (2.4.12): that end's
                # outcome stands. Without one, the error stays.
                if exc.code != "OWNER_UNREGISTERED" or self._returncode is None:
                    raise
                return self._returncode
            if self._returncode is None:  # never over an outcome an end set while this poll waited
                self._returncode = result["returncode"]
        return self._returncode

    async def wait(self):
        while await self.poll() is None:
            await asyncio.sleep(.02)  # only an explicit wait, never an idle owner wakeup
        return self._returncode

    async def end(self):
        if self.family.get("state") == "GONE":
            return self._returncode  # answered: a retried retirement, or a second close, asks again
        try:
            response = await self.authority.call("end", payload={"listener": self.listener, "mode": "drain"})
        except CoordinatorError as exc:
            # A granted family leaves the owner only once GONE (2.4.12: forgotten, or compacted past the journal's
            # retention): unregistered, with its root's exit evidenced here too, it is that finished end, begun by
            # this handle's poll or by another. A live or unreadable root keeps the error.
            if exc.code != "OWNER_UNREGISTERED" or not processes.exit_evidence(self.family["root"]):
                raise
            self.family = dict(self.family, state="GONE")
            return self._returncode
        self.family = response["family"]
        self._returncode = self.family.get("returncode", 0)
        return self._returncode

    def terminate(self):
        if self.ending is None:
            self.ending = asyncio.create_task(self.end())

    kill = terminate  # all escalation stays in O7, never a worker signal

    async def communicate(self, input=None):
        if self.stdin:
            if input:
                self.stdin.write(input)
                await self.stdin.drain()
            self.stdin.close()
        async def read(stream):
            return await stream.read() if stream else None
        stdout, stderr = await asyncio.gather(read(self.stdout), read(self.stderr))
        await self.wait()
        return stdout, stderr


async def spawn(*argv, kind="helper", listener=None, **options):
    if "app-server" in argv:
        from .memory import before_launch
        before_launch()
    authority = leases.CURRENT
    if authority is None:
        # Standalone capability fixtures have no daemon/worker. Production workers
        # always install their authority before starting discovery or readers.
        options.pop("fd_arguments", None)
        return await asyncio.create_subprocess_exec(*argv, **options)
    ident = listener or kind + "-" + uuid.uuid4().hex
    descriptors, local, stdio = [], {}, {}
    result = None
    submitted = False
    try:
        for name in ("stdin", "stdout", "stderr"):
            value = options.get(name, subprocess.DEVNULL)
            if value == subprocess.PIPE:
                read_fd, write_fd = os.pipe()
                remote, here = (read_fd, write_fd) if name == "stdin" else (write_fd, read_fd)
                local[name] = here
            elif isinstance(value, int) and value >= 0:
                remote = os.dup(value)
            elif hasattr(value, "fileno"):
                remote = os.dup(value.fileno())
            else:
                continue
            stdio[name] = len(descriptors)
            descriptors.append(remote)
        passed = {}
        for descriptor in options.get("pass_fds", ()):
            passed[descriptor] = len(descriptors)
            descriptors.append(os.dup(descriptor))
        fd_arguments = {str(index): passed[descriptor] for index, descriptor in options.get("fd_arguments", {}).items()}
        submitted = True
        response = await authority.call("spawn", cwd=str(options.get("cwd") or os.getcwd()),
            payload={"kind": kind, "listener": ident, "argv": [str(x) for x in argv],
                     "env_delta": dict(options.get("env") or os.environ), "stdio": stdio,
                     "fd_arguments": fd_arguments}, descriptors=descriptors)
        result = Process(authority, response["family"])
        loop = asyncio.get_running_loop()
        for name, descriptor in list(local.items()):
            if name == "stdin":
                protocol = asyncio.streams.FlowControlMixin(loop=loop)
                transport, _ = await loop.connect_write_pipe(lambda: protocol, os.fdopen(descriptor, "wb", buffering=0))
                result.stdin = asyncio.StreamWriter(transport, protocol, None, loop)
            else:
                reader = asyncio.StreamReader(limit=options.get("limit", 65536))
                protocol = asyncio.StreamReaderProtocol(reader)
                await loop.connect_read_pipe(lambda: protocol, os.fdopen(descriptor, "rb", buffering=0))
                setattr(result, name, reader)
            del local[name]
        return result
    except BaseException:
        if submitted:
            from .locks import finish
            try:
                # The exchange can commit/grant before cancellation wins its
                # await. Its preassigned identity exists even without a result.
                ended = await finish(authority.call("end", payload={"listener": ident, "mode": "drain"}))
            except CoordinatorError as exc:
                if exc.code != "OWNER_UNREGISTERED":
                    raise
            else:
                if kind == "listener" and ended["family"]["state"] == "GONE":
                    # No listener record will name this launch, so nothing can ask for its receipt (2.4.12); a forget
                    # refused here is sent again by the next start's sweep.
                    try:
                        await finish(authority.call("forget", payload={"listeners": [ident]}))
                    except (CoordinatorError, OSError):
                        pass
        raise
    finally:
        for descriptor in descriptors + list(local.values()):
            os.close(descriptor)


async def end(listener):
    if leases.CURRENT is None:
        raise CoordinatorError("CUSTODIAN_REQUIRED", "Process cleanup requires its supervisor")
    return await leases.CURRENT.call("end", payload={"listener": listener, "mode": "drain"})


async def verify_watcher():
    """Measure the actual supervisor path: STOP is not exit and cannot stall RPC."""
    from .children import STALL_S
    process = await spawn("/bin/sleep", "60", kind="watcher-probe")
    try:
        await process.authority.call("watcher-stop", payload={"listener": process.listener})
        began = time.monotonic()
        stopped = await process.poll() is None
        await asyncio.sleep(.05)
        gap = time.monotonic() - began
        await process.end()
        exited = process.family["state"] == "GONE" and bool(process.family.get("exit_evidence"))
        return {"backend": "supervisor waitpid WNOHANG", "stopped_not_exited": stopped,
                "stopped_gap_s": round(gap, 3), "exit_reported": exited,
                "ok": stopped and gap < .05 + STALL_S and exited}
    finally:
        from .locks import finish
        await finish(process.end())
