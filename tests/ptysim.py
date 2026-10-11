"""Synthetic receiver on a PTY master for tests; the code under test opens the slave tty."""
import os
import pty
import select
import threading
import time

from m9n.demo import sample


class Receiver:
    """Synthetic receiver on a PTY master; the controller opens the slave tty."""

    def __init__(self, fault=None, silent=False, unplug_after=None):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.fault, self.silent, self.unplug_after = fault, silent, unplug_after
        self.transmitted = bytearray()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        start = time.monotonic()
        while not self.stopped.wait(.05):
            try:
                if select.select([self.master], [], [], 0)[0]:
                    self.transmitted.extend(os.read(self.master, 4096))
                elapsed = time.monotonic() - start
                if self.unplug_after is not None and elapsed > self.unplug_after:
                    os.close(self.master)
                    self.master = None
                    return
                if not self.silent:
                    epoch = 1000 if self.fault == "frozen" else 1000 + int(elapsed * 1000)
                    os.write(self.master, sample(epoch, None if self.fault == "frozen" else self.fault))
            except OSError:
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopped.set()
        self.thread.join(2)
        os.close(self.slave)
        if self.master is not None:
            os.close(self.master)


def run_until_finished(controller, wait=0.1):
    """Blocking stand-in for an event loop: drive a controller until its session ends."""
    while controller.running:
        fd = controller.fileno()
        if fd is not None:
            poll = select.poll()
            poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
            if poll.poll(int(wait * 1000)):
                controller.on_readable()
        elif controller.config.source == "demo":
            time.sleep(0.05)
        controller.tick()
    return controller.result
