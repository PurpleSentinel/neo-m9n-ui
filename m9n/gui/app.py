"""Single-instance libadwaita application: actions, accelerators, dialogs."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "io.github.PurpleSentinel.NeoM9N"
APP_NAME = "NEO-M9N Monitor"
SHORTCUTS = [("Session", [("Start or stop the session", "F5")]),
             ("General", [("Keyboard shortcuts", "<Control>question"), ("Quit", "<Control>q")])]


class Application(Adw.Application):
    def __init__(self):
        # DEFAULT_FLAGS keeps one instance: a second launch only presents this window,
        # so two processes never compete for the receiver's tty.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.add_simple_action("quit", self.on_quit, ["<Control>q"])
        self.add_simple_action("about", self.on_about)
        self.add_simple_action("shortcuts", self.on_shortcuts, ["<Control>question"])
        self.set_accels_for_action("win.toggle-session", ["F5"])

    def do_activate(self):
        window = self.props.active_window or MainWindow(application=self)
        window.present()

    def add_simple_action(self, name, callback, accels=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action("app." + name, accels)

    def on_quit(self, *_):
        # Closing goes through each window's close-request, which owns session shutdown.
        for window in list(self.get_windows()):
            window.close()

    def on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon="computer-symbolic", version=__version__,
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
