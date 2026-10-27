"""Event-loop keyed locks with cancellation-safe holder/waiter accounting."""
import asyncio
import builtins
from functools import partial
from contextlib import AsyncExitStack, asynccontextmanager
import time


class KeyedLocks:
    def __init__(self):
        self.entries = {}

    def __len__(self):
        return len(self.entries)

    @asynccontextmanager
    async def hold_many(self, keys):
        # Lifecycle checkout locks precede unit locks, which precede ledger
        # locks. Publication order is independent of this acquisition order.
        async with AsyncExitStack() as stack:
            for key in sorted(set(keys)):
                await stack.enter_async_context(self.hold(key))
            yield

    @asynccontextmanager
    async def hold(self, key, deadline=None):
        if key is None:
            yield
            return
        entry = self.entries.setdefault(key, [asyncio.Lock(), 0])
        # No await between lookup and registration, including waiting callers.
        entry[1] += 1
        try:
            async with held(entry[0], deadline):
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self.entries[key]


@asynccontextmanager
async def held(lock, deadline=None):
    """Hold an asyncio lock; with a deadline (time.time() seconds), refuse once it passes instead of
    queueing on. A native hook has stopped waiting by then, so work admitted after it is for nobody."""
    from . import admissions
    admission_deadline = admissions.deadline()
    if admission_deadline is not None:
        deadline = min(deadline, admission_deadline) if deadline is not None else admission_deadline
    if deadline is None:
        async with lock:
            yield
        return
    try:
        await asyncio.wait_for(lock.acquire(), max(0.0, deadline - time.time()))
    except TimeoutError:
        raise admissions.timeout_error() from None
    try:
        if hasattr(lock, "adopt"):
            lock.adopt()
        admissions.check()
        yield
    finally:
        lock.release()


def busy():
    from .client import CoordinatorError
    return CoordinatorError("COORDINATOR_BUSY", "The coordinator could not admit this call before the native hook "
                            "deadline, so nothing ran. Retry it; batching read-only commands into one call queues less.",
                            {"retryable": True}, True)


async def joined(future, *, propagate_cancel=True):
    """Join the actual owner, even if our waiter is cancelled repeatedly.

    In particular, an executor Future must never receive the waiter's
    cancellation: cancelling its asyncio wrapper cannot stop a running thread.
    Only shutdown's final joins suppress cancellation after consuming outcomes.
    """
    cancelled = False
    while not future.done():
        # Only this waiter receives the owner's cancellation. The dependency
        # wakes it with a value, never its exception: a plain Future containing
        # Retired and a cancelled Task must both retain their actual outcome.
        waiter = asyncio.get_running_loop().create_future()
        def completed(_, waiter=waiter):
            if not waiter.done():
                waiter.set_result(None)
        future.add_done_callback(completed)
        try:
            await waiter
        except asyncio.CancelledError:
            cancelled = True
        finally:
            future.remove_done_callback(completed)
    try:
        result = future.result()
    except BaseException as exc:
        if cancelled and propagate_cancel:
            # Consume the worker's failure, but do not turn a cancelled owner
            # back into a live worker loop. Preserve the failure for diagnosis.
            raise asyncio.CancelledError() from exc
        raise
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError()
    return result


async def cancel_and_join(tasks, *, interval=1):
    """Retry cancellation of consumers without abandoning their owned workers.

    The interval is a retry cadence, not an IO deadline. joined() keeps an
    executor owner alive until its real work ends even under repeated cancels.
    """
    pending = set(tasks)
    while pending:
        for task in pending:
            task.cancel()
        try:
            done, pending = await asyncio.wait(pending, timeout=interval)
        except asyncio.CancelledError:
            continue
        for task in done:
            if not task.cancelled():
                task.exception()  # consume outcomes; registered callbacks log failures


async def retire(action, *args, **kwargs):
    """Finish an idempotent resource retirement before propagating cancellation.

    The owner can be cancelled repeatedly; a Runner-wide cancellation can also
    reach the retirement task itself. In that case retry the unfinished
    retirement, retaining its process, descriptors and allocation throughout.
    Ordinary failures still propagate; they never authorize resource release.
    """
    async def complete():
        task = asyncio.current_task()
        while True:
            cancellations = task.cancelling()
            try:
                return await action(*args, **kwargs)
            except asyncio.CancelledError:
                if task.cancelling() == cancellations:
                    # A dependency returned cancellation; restarting cannot repair
                    # a permanently cancelled completion future. Do not spin.
                    raise RuntimeError("Resource retirement dependency was cancelled") from None

    return await finish(complete())


async def wait_closed(writer):
    """Join a stream's shared completion without letting cancellation poison it.

    StreamWriter.wait_closed() directly awaits this protocol future on CPython
    3.11–3.15. Shielding a task that calls it is insufficient: Runner cancellation
    reaches that task too. Join the actual completion future instead.
    """
    await joined(writer._protocol._get_close_waiter(writer), propagate_cancel=False)


async def finish_thread(function, *args, **kwargs):
    """Keep ownership until the underlying executor call has actually ended."""
    from . import telemetry
    context = telemetry.detached_context(traces=telemetry.CURRENT.get())
    future = asyncio.get_running_loop().run_in_executor(
        None, context.run, partial(function, *args, **kwargs))
    return await joined(future)


async def finish_all(*awaitables, return_exceptions=False):
    """Join every sibling before exposing an error or cancellation to its owner."""
    # gather's default first-error return leaves siblings running. Its
    # return_exceptions form consumes every outcome before compensation begins.
    future = asyncio.gather(*awaitables, return_exceptions=True)
    try:
        results = await joined(future)
    except asyncio.CancelledError as exc:
        failures = [result for result in future.result()
                    if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)]
        if failures:
            raise exc from builtins.BaseExceptionGroup("Joined workers failed during cancellation", failures)
        raise
    if not return_exceptions:
        for result in results:
            if isinstance(result, BaseException):
                raise result
    return results


async def finish(awaitable):
    from . import telemetry
    task = asyncio.create_task(awaitable,
        context=telemetry.detached_context(traces=telemetry.CURRENT.get()))
    return await joined(task)
