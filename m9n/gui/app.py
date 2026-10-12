"""Single-instance libadwaita application: actions, accelerators, dialogs, signals."""
import logging
import signal
import sys
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from .window import MainWindow  # noqa: E402

LOG = logging.getLogger("m9n")
APP_ID = "io.github.PurpleSentinel.NeoM9N"
APP_NAME = "NEO-M9N Monitor"
DATA = Path(__file__).resolve().parent / "data"
SHORTCUTS = [("Session", [("Start or stop the session", "F5"), ("Run a device test", "<Control>t"),
                          ("Show or hide session setup", "F9"), ("Export the track as GPX", "<Control>e")]),
             ("General", [("Fullscreen", "F11"), ("Keyboard shortcuts", "<Control>question"),
                          ("Quit", "<Control>q")])]


class Application(Adw.Application):
    def __init__(self):
        # DEFAULT_FLAGS keeps one instance: a second launch only presents this window,
        # so two processes never compete for the receiver's tty.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.exit_status = 0
        self._previous_excepthook = None
        self._handling_exception = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        # The packaged icons work from a checkout too; an installed copy in the theme takes precedence.
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(DATA / "icons"))
        Gtk.Window.set_default_icon_name(APP_ID)
        # The Messages page shows INFO and up (DEBUG while a debug session runs);
        # warnings also reach stderr for anyone who started the app from a terminal.
        LOG.setLevel(logging.INFO)
        LOG.propagate = False
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(console)
        self.add_simple_action("quit", self.on_quit, ["<Control>q"])
        self.add_simple_action("about", self.on_about)
        self.add_simple_action("shortcuts", self.on_shortcuts, ["<Control>question"])
        self.set_accels_for_action("win.toggle-session", ["F5"])
        self.set_accels_for_action("win.toggle-sidebar", ["F9"])
        self.set_accels_for_action("win.run-test", ["<Control>t"])
        self.set_accels_for_action("win.fullscreen", ["F11"])
        self.set_accels_for_action("win.export-track", ["<Control>e"])
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, self.on_signal, signum)
        self._previous_excepthook = sys.excepthook
        sys.excepthook = self.on_exception

    def do_shutdown(self):
        # Backstop for every exit path: nothing may outlive the main loop holding the tty.
        self.abort_sessions()
        if self._previous_excepthook:
            sys.excepthook = self._previous_excepthook
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        window = self.props.active_window or MainWindow(application=self)
        window.present()

    def add_simple_action(self, name, callback, accels=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action("app." + name, accels)

    def abort_sessions(self):
        for window in self.get_windows():
            if isinstance(window, MainWindow):
                window.runner.abort()

    def on_signal(self, signum):
        LOG.info("Received %s; stopping the session and releasing the receiver", signal.Signals(signum).name)
        self.exit_status = 128 + signum
        self.abort_sessions()
        self.quit()
        return GLib.SOURCE_REMOVE

    def on_exception(self, exc_type, exc, tb):
        if self._handling_exception:
            return sys.__excepthook__(exc_type, exc, tb)
        self._handling_exception = True
        try:
            LOG.critical("Unhandled exception; stopping the session and releasing the receiver",
                         exc_info=(exc_type, exc, tb))
            self.abort_sessions()
            window = self.props.active_window
            if isinstance(window, MainWindow):
                window.alert("Unexpected Error", "The session was stopped and the receiver released.\n\n"
                             + "".join(traceback.format_exception_only(exc_type, exc)).strip())
        finally:
            self._handling_exception = False

    def on_quit(self, *_):
        # Closing goes through each window's close-request, which owns session shutdown.
        for window in list(self.get_windows()):
            window.close()

    def on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=__version__,
            developer_name="Mark Rodman", license_type=Gtk.License.MIT_X11,
            website="https://github.com/PurpleSentinel/neo-m9n-ui",
            issue_url="https://github.com/PurpleSentinel/neo-m9n-ui/issues",
            comments="Read-only USB diagnostics and live monitoring for SparkFun u-blox NEO-M9N GNSS receivers.")
        about.present(self.props.active_window)

    def on_shortcuts(self, *_):
        dialog = Adw.ShortcutsDialog() if hasattr(Adw, "ShortcutsDialog") else None
        if dialog is None:
            return self.legacy_shortcuts()
        for title, items in SHORTCUTS:
            section = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                section.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dialog.add(section)
        dialog.present(self.props.active_window)

    def legacy_shortcuts(self):
        """libadwaita < 1.8 has no AdwShortcutsDialog; list them in an alert instead."""
        lines = []
        for _, items in SHORTCUTS:
            for label, accel in items:
                key, mods = Gtk.accelerator_parse(accel)
                lines.append(f"{Gtk.accelerator_get_label(key, mods)}: {label}")
        dialog = Adw.AlertDialog(heading="Keyboard Shortcuts", body="\n".join(lines))
        dialog.add_response("close", "Close")
        dialog.present(self.props.active_window)
