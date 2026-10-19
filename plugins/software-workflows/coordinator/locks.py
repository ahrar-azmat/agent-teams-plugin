"""Event-loop keyed locks with cancellation-safe holder/waiter accounting."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager


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
    async def hold(self, key):
        if key is None:
            yield
            return
        entry = self.entries.setdefault(key, [asyncio.Lock(), 0])
        # No await between lookup and registration, including waiting callers.
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self.entries[key]


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
