"""Page registry and the empty states shown before a session starts."""
from gi.repository import Adw, Gtk


class Page:
    def __init__(self, key, title, icon, empty_title, empty_text):
        self.key, self.title, self.icon = key, title, icon
        self.empty_title, self.empty_text = empty_title, empty_text


PAGES = [
    Page("dashboard", "Dashboard", "find-location-symbolic", "No Session Running",
         "Start a session to see the fix, position, timing and receiver identity."),
    Page("satellites", "Satellites", "network-cellular-signal-excellent-symbolic", "No Satellite Data",
         "Satellites appear here once the receiver answers NAV-SAT."),
    Page("receiver", "Receiver", "network-wireless-symbolic", "No Receiver Diagnostics",
         "RF, antenna and receiver status appear here during a session."),
    Page("messages", "Messages", "view-list-bullet-symbolic", "No Messages",
         "Receiver notices, parser errors and the application log appear here."),
    Page("test", "Test", "object-select-symbolic", "No Device Test",
         "Run a timed device test to get a PASS, WARN or FAIL report."),
    Page("host", "Host", "computer-symbolic", "Host Diagnostics",
         "USB devices, access permissions and competing services appear here."),
]


def empty_page(page):
    return Adw.StatusPage(icon_name=page.icon, title=page.empty_title, description=page.empty_text)


class SessionPage(Adw.Bin):
    """A page that shows its empty state until a session delivers data.

    Subclasses build their content in build() and fill it in show(snapshot).
    """

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.snapshot = None
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(empty_page(page), "empty")
        content = self.build()
        if content is not None:
            self.stack.add_named(content, "content")
        self.set_child(self.stack)

    def build(self):
        return None

    def session_started(self, config):
        self.config = config

    def update(self, snapshot):
        self.snapshot = snapshot
        if self.stack.get_child_by_name("content"):
            self.show(snapshot)
            self.stack.set_visible_child_name("content")

    def session_finished(self, result):
        self.update(result.snapshot)

    def show(self, snapshot):
        pass


def value_row(title, subtitle=None, wrap=False):
    """An ActionRow with a right-aligned, selectable, tabular value label."""
    row = Adw.ActionRow(title=title, use_markup=False)
    if subtitle:
        row.set_subtitle(subtitle)
    label = Gtk.Label(xalign=1, selectable=True, wrap=wrap, max_width_chars=40)
    label.add_css_class("numeric")
    row.add_suffix(label)
    row.value = label
    return row


def copy_button(tooltip, callback):
    button = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    button.connect("clicked", callback)
    return button


def copy_text(widget, text, toast):
    """Put text on the clipboard (if any) and confirm with a toast."""
    if text:
        widget.get_clipboard().set(text)
    window = widget.get_root()
    if hasattr(window, "toast"):
        window.toast(toast)
