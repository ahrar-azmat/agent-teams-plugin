"""Event-loop keyed locks with cancellation-safe holder/waiter accounting."""
import asyncio
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
    if deadline is None:
        async with lock:
            yield
        return
    try:
        await asyncio.wait_for(lock.acquire(), max(0.0, deadline - time.time()))
    except TimeoutError:
        raise busy() from None
    try:
        yield
    finally:
        lock.release()


def busy():
    from .client import CoordinatorError
    return CoordinatorError("COORDINATOR_BUSY", "The coordinator could not admit this call before the native hook "
                            "deadline, so nothing ran. Retry it; batching read-only commands into one call queues less.",
                            {"retryable": True}, True)


async def finish_thread(function, *args):
    """Cancellation must not release ownership while a filesystem worker runs."""
    return await finish(asyncio.to_thread(function, *args))


async def finish(awaitable):
    task = asyncio.create_task(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                result = task.result()
                break
    if cancelled:
        raise asyncio.CancelledError()
    return result
