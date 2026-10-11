"""Messages page: receiver/parser messages for the session and the application log."""
import logging

from gi.repository import Gtk

from .logview import LogEntry, LogList, UILogHandler, now_utc
from .pages import SessionPage

LOG = logging.getLogger("m9n")
SEVERITY = {"ERROR": logging.ERROR, "WARNING": logging.WARNING, "NOTICE": logging.INFO}


class MessagesPage(SessionPage):
    def build(self):
        self.receiver = LogList("No receiver or parser messages in this session yet.")
        self.application = LogList("No application log entries yet.", monospace=True)
        self.views = Gtk.Stack()
        self.views.add_titled(self.receiver, "receiver", "Receiver Messages")
        self.views.add_titled(self.application, "application", "Application Log")
        switcher = Gtk.StackSwitcher(stack=self.views, halign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=18,
                      margin_start=18, margin_end=18)
        box.append(switcher)
        box.append(self.views)
        self.handler = UILogHandler(self.application)
        return box

    def __init__(self, page):
        super().__init__(page)
        # The application log matters before any session starts, so content is always shown.
        self.stack.set_visible_child_name("content")

    def do_root(self):
        SessionPage.do_root(self)
        LOG.addHandler(self.handler)

    def do_unroot(self):
        LOG.removeHandler(self.handler)
        SessionPage.do_unroot(self)

    def session_started(self, config):
        super().session_started(config)
        self.receiver.clear()
        LOG.info("Session started: %s source, %s mode%s", config.source, config.mode,
                 ", debug logging" if config.debug else "")

    def update(self, snapshot):
        self.snapshot = snapshot  # Lists are fed by messages and log records, not snapshots.

    def add_message(self, entry):
        self.receiver.add(LogEntry(entry.get("host_utc") or now_utc(), SEVERITY.get(entry["severity"], logging.INFO),
                                   entry["name"], entry["text"]))
