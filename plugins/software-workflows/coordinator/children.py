"""Exit watching for the daemon's asyncio child processes that never blocks its event loop.

Where there is no pidfd (macOS), CPython 3.15's asyncio watches each child with _ThreadedChildWatcher
(asyncio/unix_events.py: chosen in _UnixSelectorEventLoop.__init__). Its thread waits in
os.waitid(P_PID, pid, WEXITED | WNOWAIT), then the loop reaps with a blocking os.waitpid(pid, 0) (_do_waitpid,
_reap_and_notify, _reap). macOS ends that waitid when the child stops (si_code CLD_STOPPED, measured on Darwin
25.6), and at once for a child already stopped: a SIGSTOP sent to an app-server, a probe or a lead command froze the
whole loop until the child was continued or died. This watcher waits for the exit itself, with kqueue EVFILT_PROC
NOTE_EXIT (which a stop or a continue never fires), and the loop reaps with WNOHANG only.
"""
import asyncio
import os
import select
import signal
import threading
import time
from .locks import finish_thread, retire

EXITED = tuple(getattr(os, name) for name in ("CLD_EXITED", "CLD_KILLED", "CLD_DUMPED") if hasattr(os, name))


class ExitWatcher:
    """A loop's child watcher (loop._watcher, called as asyncio calls it: add_child_handler(pid, callback, *args)).
    The thread waiting for each child holds the callback's arguments, the subprocess transport, until the child
    exits, as CPython's thread does: a transport held only in a table was collected at interpreter exit while its
    child ran, and its __del__ killed the child (measured)."""

    def __init__(self):
        self.reported = 0  # exits this watcher reported to the loop

    def add_child_handler(self, pid, callback, *args):
        loop = asyncio.get_running_loop()
        threading.Thread(target=self.wait, args=(loop, pid, callback, args), name="exit-watch-" + str(pid),
                         daemon=True).start()

    def wait(self, loop, pid, callback, args):
        queue = select.kqueue()
        try:
            try:
                queue.control([select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                             select.KQ_NOTE_EXIT)], 0)
            except ProcessLookupError:
                pass  # it has exited already: an exited process cannot be watched (measured)
            else:
                if running(pid):
                    while not queue.control(None, 1, None):
                        pass
        finally:
            queue.close()
        try:
            loop.call_soon_threadsafe(self.reap, loop, pid, callback, args)
        except RuntimeError:
            # Its loop has closed: reap it here, so no zombie is left. The exit has happened, so this does not block.
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def reap(self, loop, pid, callback, args):
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # Reaped elsewhere (Popen.send_signal polls first): reported as asyncio reports it.
            returncode = 255
        else:
            if not reaped:
                # Its exit was announced before it could be reaped (not seen in 200 measured exits): look again.
                loop.call_later(0.01, self.reap, loop, pid, callback, args)
                return
            returncode = os.waitstatus_to_exitcode(status)
        self.reported += 1
        callback(pid, returncode, *args)


def running(pid):
    """Whether `pid` is still a child of this process that has not exited (one that is stopped has not). A child
    reaped elsewhere, whose pid another process may hold by now, is reported at once instead of being waited for."""
    try:
        result = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return False
    return result is None or result.si_code not in EXITED


def installable(loop):
    """Whether an ExitWatcher can stand in for this loop's watcher: CPython's threaded one, on a kernel with kqueue
    EVFILT_PROC (macOS, the BSDs). Where a pidfd watches children (Linux), the loop waits for the exit only."""
    return (type(getattr(loop, "_watcher", None)).__name__ == "_ThreadedChildWatcher" and hasattr(select, "kqueue")
            and hasattr(select, "KQ_NOTE_EXIT") and hasattr(os, "waitid"))


def install(loop):
    """Put an ExitWatcher in place of CPython's threaded watcher on this loop, before it starts a child. None where
    it cannot stand in (installable)."""
    if not installable(loop):
        return None
    watcher = ExitWatcher()
    loop._watcher = watcher
    return watcher


# A probe's sleep that takes this much longer than asked is a frozen loop. Measured on Darwin 25.6 with CPython 3.15.0:
# 0.051 s for a 0.05 s sleep with the ExitWatcher, and the whole rescue time (0.5 or 2 s) with the threaded watcher.
STALL_S = 0.2
# The known-bad run's rescue: past STALL_S with room to spare, and short, since every daemon start pays it.
CALIBRATION_RESCUE_S = 0.5


def calibrate():
    """The probe (verify) on a loop of its own, in the calling thread, with the interpreter's own watcher: the
    configuration known bad on macOS, where it has to come back red for the probe to be trusted at all. A loop it
    freezes is this one, never the daemon's."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(verify(None, rescue_s=CALIBRATION_RESCUE_S))
    finally:
        loop.close()


async def start(loop):
    """Choose this loop's child watcher as the daemon starts, before it starts any child, and report what decided it.
    Where an ExitWatcher can stand in (installable), the probe is first calibrated on the interpreter's own watcher in
    a loop of its own: red (the stop defect is there), the ExitWatcher is installed and probed on this loop; green,
    the interpreter's watcher is kept, since it is correct here. Elsewhere (a pidfd) the interpreter's watcher is
    kept and probed. `ok` is the probe on this loop's watcher; `trusted`, whether that probe has shown a red."""
    default = type(getattr(loop, "_watcher", None)).__name__
    calibration = await finish_thread(calibrate) if installable(loop) else None
    watcher = install(loop) if calibration and not calibration["ok"] else None
    result = await verify(watcher)
    return dict(result, default=default, installed=watcher is not None, calibration=calibration,
                trusted=bool(calibration and not calibration["ok"]))


async def terminate(process, timeout=5, *, group=False):
    """Retire an unreaped direct child, including escalation and the final reap.

    returncode=None retains direct-child ownership, so its PID cannot be reused.
    The timeout bounds graceful exit, not a kernel that cannot reap SIGKILL.
    """
    async def close():
        if process.returncode is None:
            try:
                if group:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    if group:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    await retire(close)


async def verify(watcher, probe_s=0.05, rescue_s=2.0):
    """The capability probe a daemon runs as it starts, on its own loop: a child stopped by SIGSTOP leaves the loop
    running, and the child's exit is still reported. Returns what it measured. The child is killed after
    `rescue_s` from another thread whatever happens, so a loop the stop does freeze is freed again and the probe
    reports it (a longest gap near rescue_s), never hangs."""
    process = await asyncio.create_subprocess_exec("/bin/sleep", "60", stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                                   start_new_session=True)
    lock, state = threading.Lock(), {"killed": False}

    def kill():
        with lock:
            if not state["killed"] and process.returncode is None:  # not reaped yet: its pid is still this child's
                state["killed"] = True
                try:
                    os.kill(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    rescue = threading.Timer(rescue_s, kill)
    rescue.daemon = True
    rescue.start()
    reported = watcher.reported if watcher else None
    try:
        os.kill(process.pid, signal.SIGSTOP)
        began = time.monotonic()
        await asyncio.sleep(probe_s)
        gap = time.monotonic() - began
        kill()
        code = await asyncio.wait_for(process.wait(), rescue_s + 5)
    finally:
        rescue.cancel()
        kill()
        await retire(process.wait)
    seen = watcher is None or watcher.reported > reported
    return {"watcher": type(asyncio.get_running_loop()._watcher).__name__, "stopped_gap_s": round(gap, 3),
            "exit_code": code, "exit_reported": seen, "ok": gap < probe_s + STALL_S and code == -signal.SIGKILL and seen}
