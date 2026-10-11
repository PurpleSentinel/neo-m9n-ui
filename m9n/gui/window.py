"""Main window: header bar, adaptive page switcher, banner, toasts and session control."""
import logging
from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..controller import COMPLETED, INTERRUPTED, OUTPUT_ERROR, TRANSPORT_ERROR, USER_STOPPED
from ..serialize import safe
from ..settings import SessionSettings
from .dashboard import DashboardPage
from .host import HostPage
from .messages import MessagesPage
from .receiver import ReceiverPage
from .satellites import SatellitesPage
from .pages import PAGES, SessionPage
from .runner import SessionRunner
from .setup import SetupPanel
from .testpage import TestPage
from .vehicle import VehiclePage

LOG = logging.getLogger("m9n")
SAVE_DELAY_MS = 500
PROGRESS_MS = 200
PAGE_TYPES = {"dashboard": DashboardPage, "satellites": SatellitesPage, "receiver": ReceiverPage,
              "vehicle": VehiclePage, "messages": MessagesPage, "test": TestPage, "host": HostPage}


def default_output_dir():
    documents = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
    return str(Path(documents or Path.home() / "Documents") / "NEO-M9N")

NARROW = "max-width: 600sp"
COMPACT_SWITCHER = "max-width: 1180sp"  # Seven wide page buttons no longer fit in the header.


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="NEO-M9N Monitor", default_width=1000, default_height=720, **kwargs)
        self.set_size_request(360, 480)

        self.stack = Adw.ViewStack(vexpand=True)
        self.pages = {}
        for page in PAGES:
            widget = PAGE_TYPES.get(page.key, SessionPage)(page)
            self.pages[page.key] = widget
            self.stack.add_titled_with_icon(widget, page.key, page.title, page.icon)

        self.session_button = Gtk.Button(label="Start", action_name="win.toggle-session",
                                         tooltip_text="Start or stop the session (F5)")
        self.session_button.add_css_class("suggested-action")
        menu = Gio.Menu()
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("About NEO-M9N Monitor", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                     tooltip_text="Main Menu")

        settings = SessionSettings.load()
        settings.output_dir = settings.output_dir or default_output_dir()
        self.setup = SetupPanel(settings)
        self.setup.connect("changed", self.on_setup_changed)
        self._save_source = 0

        self.switcher = switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True,
                                               tooltip_text="Session Setup (F9)")
        header.pack_start(self.sidebar_button)
        header.pack_start(self.session_button)
        header.pack_end(menu_button)

        self.banner = Adw.Banner(use_markup=False)
        self.switcher_bar = switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        self.toasts = Adw.ToastOverlay(child=self.stack)

        self.split = Adw.OverlaySplitView(sidebar=self.setup, content=self.toasts,
                                          min_sidebar_width=300, max_sidebar_width=380)
        self.split.bind_property("show-sidebar", self.sidebar_button, "active",
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        toolbar = Adw.ToolbarView(content=self.split)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        # Replay and timed sessions show progress under the header bar.
        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("osd")
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], ["Session progress"])
        toolbar.add_top_bar(self.progress)
        self._progress_source = 0
        toolbar.add_bottom_bar(switcher_bar)
        self.set_content(toolbar)

        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(COMPACT_SWITCHER))
        compact.add_setter(switcher, "policy", Adw.ViewSwitcherPolicy.NARROW)
        self.add_breakpoint(compact)
        # Narrow windows move the page switcher from the header to the bottom bar.
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        breakpoint.add_setter(switcher_bar, "reveal", True)
        breakpoint.add_setter(switcher, "visible", False)
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.add_setter(self.split, "show-sidebar", False)
        self.add_breakpoint(breakpoint)

        self.runner = SessionRunner()
        self.runner.connect("started", self.on_started)
        self.runner.connect("finished", self.on_finished)
        self.runner.connect("snapshot", self.on_snapshot)
        self.runner.connect("issue", self.on_issue)
        self.runner.connect("message", lambda _runner, entry: self.pages["messages"].add_message(entry))
        self._issue_toasts = {}
        self._inhibit_cookie = 0
        self._close_after_stop = False
        self.toggle_action = Gio.SimpleAction.new("toggle-session", None)
        self.toggle_action.connect("activate", self.toggle_session)
        self.add_action(self.toggle_action)
        self.run_test_action = Gio.SimpleAction.new("run-test", None)
        self.run_test_action.connect("activate", self.run_test)
        self.add_action(self.run_test_action)
        sidebar = Gio.SimpleAction.new("toggle-sidebar", None)
        sidebar.connect("activate", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        self.add_action(sidebar)
        fullscreen = Gio.SimpleAction.new("fullscreen", None)
        fullscreen.connect("activate", lambda *_: self.unfullscreen() if self.is_fullscreen() else self.fullscreen())
        self.add_action(fullscreen)
        self.connect("notify::fullscreened", lambda *_: self.pages["vehicle"].set_fullscreen(self.is_fullscreen()))
        vehicle = self.pages["vehicle"]
        vehicle.set_units(settings.units)
        vehicle.on_units_changed = self.on_units_changed
        self._idle_cookie = 0
        self.stack.connect("notify::visible-child-name", lambda *_: self.update_idle_inhibit())
        self.on_setup_changed(self.setup)
        self.connect("close-request", self.on_close_request)

    def show_page(self, key):
        self.stack.set_visible_child_name(key)

    def toast(self, text, timeout=3):
        toast = Adw.Toast(title=text, timeout=timeout, use_markup=False)
        self.toasts.add_toast(toast)
        return toast

    def set_banner(self, text=None):
        """Persistent notice such as SYNTHETIC DEMO or a disconnect; None hides it."""
        if text:
            self.banner.set_title(text)
        self.banner.set_revealed(bool(text))

    def alert(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=safe(body))
        dialog.add_response("close", "Close")
        dialog.present(self)
        return dialog

    # Session control -----------------------------------------------------

    def session_config(self):
        """Configuration for Start: creates the evidence folder if this session needs it."""
        self.setup.settings.prepare_outputs()
        return self.setup.settings.to_config()

    def toggle_session(self, *_):
        if self.runner.running:
            self.runner.stop(USER_STOPPED)
            return
        try:
            config = self.session_config()
        except OSError as exc:
            self.alert("Session Could Not Start", f"The evidence folder could not be created: {exc}")
            return
        self.start_session(config)

    def run_test(self, *_):
        """Switch the setup to a device test and start it, or show why it cannot start."""
        settings = self.setup.settings
        if settings.source == "replay":
            settings.source = "device"
        settings.mode = "test"
        self.setup.load()
        if not self.setup.valid:
            self.split.set_show_sidebar(True)
            self.alert("Device Test Could Not Start", next(iter(self.setup.problems.values())))
            return
        self.toggle_session()

    def on_setup_changed(self, setup):
        self.toggle_action.set_enabled(self.runner.running or setup.valid)
        self.run_test_action.set_enabled(not self.runner.running)
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self.save_settings)

    def on_units_changed(self, units):
        self.setup.settings.units = units
        self.on_setup_changed(self.setup)

    def update_idle_inhibit(self):
        """Keep the screen on while the Vehicle page shows a running session."""
        wanted = self.runner.running and self.stack.get_visible_child_name() == "vehicle"
        if wanted and not self._idle_cookie:
            self._idle_cookie = self.get_application().inhibit(
                self, Gtk.ApplicationInhibitFlags.IDLE, "Vehicle display in use")
        elif not wanted and self._idle_cookie:
            self.get_application().uninhibit(self._idle_cookie)
            self._idle_cookie = 0

    def save_settings(self):
        self._save_source = 0
        try:
            self.setup.settings.save()
        except OSError as exc:
            LOG.warning("Settings not saved: %s", exc)
        return GLib.SOURCE_REMOVE

    def start_session(self, config):
        try:
            self.runner.start(config)
        except (OSError, ValueError) as exc:
            self.alert("Session Could Not Start", str(exc))
            return False
        return True

    def on_snapshot(self, _runner, snapshot):
        for page in self.pages.values():
            page.update(snapshot)

    def on_issue(self, _runner, kind, text):
        # One toast per issue at a time; a resolved issue withdraws its toast.
        toast = self._issue_toasts.pop(text, None)
        if toast:
            toast.dismiss()
        if kind == "added":
            self._issue_toasts[text] = self.toast(safe(text), timeout=6)

    def on_started(self, runner):
        for page in self.pages.values():
            page.session_started(runner.config)
        self.setup.set_sensitive(False)
        self.toggle_action.set_enabled(True)
        self.run_test_action.set_enabled(False)
        self.session_button.set_label("Stop")
        self.session_button.remove_css_class("suggested-action")
        self.session_button.add_css_class("destructive-action")
        source = runner.config.source
        self.set_banner({"demo": "SYNTHETIC DEMO: generated data, not from a receiver",
                         "replay": "REPLAY: recorded data without original timing"}.get(source))
        self.update_progress()
        self._progress_source = GLib.timeout_add(PROGRESS_MS, self.update_progress)
        # Suspend would silently cut the USB session and corrupt a timed test.
        self._inhibit_cookie = self.get_application().inhibit(
            self, Gtk.ApplicationInhibitFlags.SUSPEND | Gtk.ApplicationInhibitFlags.LOGOUT,
            "GNSS session in progress")
        self.update_idle_inhibit()

    def on_finished(self, runner, result):
        for page in self.pages.values():
            page.session_finished(result)
        for toast in self._issue_toasts.values():
            toast.dismiss()
        self._issue_toasts.clear()
        self.setup.set_sensitive(True)
        self.setup.refresh()  # A pinned raw capture now exists, for example.
        self.run_test_action.set_enabled(True)
        if result.report and not self._close_after_stop:
            self.show_page("test")
        self.session_button.set_label("Start")
        self.session_button.remove_css_class("destructive-action")
        self.session_button.add_css_class("suggested-action")
        if self._inhibit_cookie:
            self.get_application().uninhibit(self._inhibit_cookie)
            self._inhibit_cookie = 0
        self.update_idle_inhibit()
        if result.reason == TRANSPORT_ERROR:
            self.set_banner(f"Device disconnected: {safe(result.transport_error)}. Reconnect it and start a new session.")
        elif runner.config.source == "device":
            self.set_banner(None)
        if self._progress_source:
            GLib.source_remove(self._progress_source)
            self._progress_source = 0
        self.progress.set_visible(False)
        if runner.config.source == "replay" and result.reason == COMPLETED:
            self.replay_outcome(result)
        else:
            self.toast({COMPLETED: "Session completed", USER_STOPPED: "Session stopped",
                        INTERRUPTED: "Session interrupted", TRANSPORT_ERROR: "Session ended: receiver disconnected",
                        OUTPUT_ERROR: "Session stopped: an evidence file could not be written"}[result.reason])
        if result.output_errors:
            self.alert("Evidence Files Incomplete", "\n".join(result.output_errors))
        if self._close_after_stop:
            self.close()

    def update_progress(self):
        fraction = self.runner.progress() if self.runner.running else None
        self.progress.set_visible(fraction is not None)
        if fraction is not None:
            self.progress.set_fraction(fraction)
        return GLib.SOURCE_CONTINUE

    def replay_outcome(self, result):
        """The CLI's replay failure cases (exit 1) as explicit messages."""
        if not result.received_bytes:
            self.set_banner("REPLAY: the capture file is empty")
            self.alert("Capture Is Empty", "The replay file contains no bytes, so there is nothing to decode.")
        elif result.snapshot["navigation"] is None:
            self.set_banner("REPLAY: no navigation messages were found in this capture")
            self.alert("No Navigation Found", f"{result.valid_frames:,} valid frames were decoded, but none carried "
                       "a position (NAV-PVT, GGA or RMC). Check the protocol counters on the Dashboard.")
        else:
            self.toast(f"Replay complete: {result.valid_frames:,} valid frames decoded")

    def on_close_request(self, _window):
        if not self.runner.running:
            if self._save_source:
                GLib.source_remove(self._save_source)
                self.save_settings()
            return False
        testing = self.runner.config.mode == "test"
        dialog = Adw.AlertDialog(
            heading="Stop the Session and Quit?",
            body=("The device test is still running. It will be recorded as INTERRUPTED and its report saved."
                  if testing else "The receiver will be released and all evidence files closed."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("stop", "Stop and Quit")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_close_response)
        dialog.present(self)
        return True

    def on_close_response(self, _dialog, response):
        if response == "stop":
            # Let the dialog finish closing before the window goes away.
            GLib.idle_add(self.stop_and_close)

    def stop_and_close(self):
        self._close_after_stop = True
        if self.runner.running:
            self.runner.stop(USER_STOPPED)  # on_finished closes the window.
        else:
            self.close()
        return GLib.SOURCE_REMOVE
