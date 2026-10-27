"""Latch requested stops across Runner startup, execution and final teardown."""
import os
import signal


class StopControl:
    def __init__(self):
        self.requested = False
        self.wake_fd = None
        self.loop = self.callback = None
        self.previous = {}

    def __enter__(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            self.previous[number] = signal.signal(number, self.receive)
        return self

    def receive(self, number, frame):
        self.requested = True
        if self.wake_fd is not None:
            try:
                os.write(self.wake_fd, b"s")
            except OSError:
                pass
        if self.loop is not None and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.callback)
            except RuntimeError:
                pass  # loop closure won; the request remains latched

    def bind(self, loop, callback):
        self.loop, self.callback = loop, callback
        if self.requested:
            callback()

    def __exit__(self, *unused):
        self.loop = self.callback = None
        for number, previous in self.previous.items():
            signal.signal(number, previous)
