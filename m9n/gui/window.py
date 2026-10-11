"""Main window: header bar, adaptive page switcher, banner and toasts."""
from gi.repository import Adw, Gio, Gtk

from .pages import PAGES, empty_page

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

        # Enabled once session control exists (#5, #6).
        toggle = Gio.SimpleAction.new("toggle-session", None)
        toggle.set_enabled(False)
        self.add_action(toggle)

    def show_page(self, key):
        self.stack.set_visible_child_name(key)

    def toast(self, text, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout, use_markup=False))

    def set_banner(self, text=None):
        """Persistent notice such as SYNTHETIC DEMO or a disconnect; None hides it."""
        if text:
            self.banner.set_title(text)
        self.banner.set_revealed(bool(text))
