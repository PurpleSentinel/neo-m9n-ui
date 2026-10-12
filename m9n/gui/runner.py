"""Drive a SessionController from the GLib main loop without threads.

The tty fd is watched with GLib.unix_fd_add_full() and a 100 ms timeout runs
tick(). Replay ticks from an idle source so large captures stay responsive.
Every source is removed *before* the controller closes the fd, so a callback
can never run on a closed or reused descriptor.
"""
import logging

from gi.repository import GLib, GObject

from ..controller import INTERRUPTED, USER_STOPPED, SessionController

LOG = logging.getLogger("m9n")
TICK_MS = 100


class SessionRunner(GObject.Object):
    __gsignals__ = {
        "started": (GObject.SignalFlags.RUN_LAST, None, ()),
        "snapshot": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "issue": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "message": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "finished": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self):
        super().__init__()
        self.controller = None
        self._sources = []

    @property
    def running(self):
        return bool(self.controller and self.controller.running)

    @property
    def config(self):
        return self.controller.config if self.controller else None

    def start(self, config):
        """Open the source and attach it; raises ValueError/OSError without side effects."""
        if self.running:
            raise RuntimeError("A session is already running")
        controller = SessionController(config, on_snapshot=lambda s: self.emit("snapshot", s),
                                       on_issue=lambda kind, text: self.emit("issue", kind, text),
                                       on_finished=lambda result: self.emit("finished", result),
                                       on_stopping=self._detach,
                                       on_message=lambda entry: self.emit("message", entry))
        controller.start()
        self.controller = controller
        fd = controller.fileno()
        if fd is not None:
            condition = GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR
            self._sources.append(GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, fd, condition, self._on_fd))
        if config.source == "replay":
            self._sources.append(GLib.idle_add(self._on_tick, priority=GLib.PRIORITY_DEFAULT_IDLE))
        else:
            self._sources.append(GLib.timeout_add(TICK_MS, self._on_tick))
        self.emit("started")

    def stop(self, reason=USER_STOPPED):
        """Detach from the main loop, then let the controller release the tty and files."""
        self._detach()
        if self.controller:
            self.controller.stop(reason)
            self.controller.close()

    def abort(self):
        """Last-resort stop for signals, shutdown and unexpected errors."""
        if self.running:
            self.stop(INTERRUPTED)
        else:
            self._detach()
            if self.controller:
                self.controller.close()

    def progress(self):
        return self.controller.progress() if self.controller else None

    def reset_trip(self):
        if self.controller:
            self.controller.reset_trip()

    def track_points(self):
        return self.controller.track_points() if self.controller else []

    def _detach(self):
        sources, self._sources = self._sources, []
        for source in sources:
            # The currently dispatching source may already be gone; that is fine.
            context = GLib.MainContext.default()
            if context.find_source_by_id(source):
                GLib.source_remove(source)

    def _on_fd(self, _fd, _condition):
        # A hang-up or error makes the next read raise, which ends the session as a transport failure.
        self.controller.on_readable()
        return self.running

    def _on_tick(self):
        self.controller.tick()
        return self.running
