"""Main window: header bar, adaptive page switcher, banner, toasts and session control."""
from gi.repository import Adw, Gio, GLib, Gtk

from ..controller import COMPLETED, INTERRUPTED, OUTPUT_ERROR, TRANSPORT_ERROR, USER_STOPPED, SessionConfig
from ..serialize import safe
from .pages import PAGES, empty_page
from .runner import SessionRunner

NARROW = "max-width: 600sp"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="NEO-M9N Monitor", default_width=1000, default_height=720, **kwargs)
        self.set_size_request(360, 480)

        self.stack = Adw.ViewStack(vexpand=True)
        self.pages = {}
        for page in PAGES:
            widget = empty_page(page)
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

        self.switcher = switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar(title_widget=switcher)
        header.pack_start(self.session_button)
        header.pack_end(menu_button)

        self.banner = Adw.Banner(use_markup=False)
        self.switcher_bar = switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        self.toasts = Adw.ToastOverlay(child=self.stack)

        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        toolbar.add_bottom_bar(switcher_bar)
        self.set_content(toolbar)

        # Narrow windows move the page switcher from the header to the bottom bar.
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        breakpoint.add_setter(switcher_bar, "reveal", True)
        breakpoint.add_setter(switcher, "visible", False)
        self.add_breakpoint(breakpoint)

        self.runner = SessionRunner()
        self.runner.connect("started", self.on_started)
        self.runner.connect("finished", self.on_finished)
        self._inhibit_cookie = 0
        self._close_after_stop = False
        toggle = Gio.SimpleAction.new("toggle-session", None)
        toggle.connect("activate", self.toggle_session)
        self.add_action(toggle)
        self.connect("close-request", self.on_close_request)

    def show_page(self, key):
        self.stack.set_visible_child_name(key)

    def toast(self, text, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout, use_markup=False))

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
        """The configuration Start uses; the setup page replaces this (#6)."""
        return SessionConfig()

    def toggle_session(self, *_):
        if self.runner.running:
            self.runner.stop(USER_STOPPED)
        else:
            self.start_session(self.session_config())

    def start_session(self, config):
        try:
            self.runner.start(config)
        except (OSError, ValueError) as exc:
            self.alert("Session Could Not Start", str(exc))
            return False
        return True

    def on_started(self, runner):
        self.session_button.set_label("Stop")
        self.session_button.remove_css_class("suggested-action")
        self.session_button.add_css_class("destructive-action")
        source = runner.config.source
        self.set_banner({"demo": "SYNTHETIC DEMO: generated data, not from a receiver",
                         "replay": "REPLAY: recorded data without original timing"}.get(source))
        # Suspend would silently cut the USB session and corrupt a timed test.
        self._inhibit_cookie = self.get_application().inhibit(
            self, Gtk.ApplicationInhibitFlags.SUSPEND | Gtk.ApplicationInhibitFlags.LOGOUT,
            "GNSS session in progress")

    def on_finished(self, runner, result):
        self.session_button.set_label("Start")
        self.session_button.remove_css_class("destructive-action")
        self.session_button.add_css_class("suggested-action")
        if self._inhibit_cookie:
            self.get_application().uninhibit(self._inhibit_cookie)
            self._inhibit_cookie = 0
        if result.reason == TRANSPORT_ERROR:
            self.set_banner(f"Device disconnected: {safe(result.transport_error)}. Reconnect it and start a new session.")
        elif runner.config.source == "device":
            self.set_banner(None)
        self.toast({COMPLETED: "Session completed", USER_STOPPED: "Session stopped",
                    INTERRUPTED: "Session interrupted", TRANSPORT_ERROR: "Session ended: receiver disconnected",
                    OUTPUT_ERROR: "Session stopped: an evidence file could not be written"}[result.reason])
        if result.output_errors:
            self.alert("Evidence Files Incomplete", "\n".join(result.output_errors))
        if self._close_after_stop:
            self.close()

    def on_close_request(self, _window):
        if not self.runner.running:
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
