"""Page registry, the empty states shown before a session starts and shared page layout."""
from gi.repository import Adw, Gtk

from .layout import balanced_split


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
    Page("vehicle", "Vehicle", "emoji-travel-symbolic", "No Vehicle Data",
         "Start a session to see speed, trip distance, heading and altitude, sized for reading on the move."),
    Page("map", "Map", "mark-location-symbolic", "No Map",
         "Your position and track appear here on a grid, or on an online map if you choose."),
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
        self.columns = None  # A ColumnPage built by the subclass, rebalanced after each update.
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
            if self.columns:
                self.columns.reflow()

    def session_finished(self, result):
        self.update(result.snapshot)

    def show(self, snapshot):
        pass


# Page widths (not window widths) at which a ColumnPage drops to fewer columns.
TWO_COLUMNS = "max-width: 1100sp"
ONE_COLUMN = "max-width: 640sp"
GAP = 24


class ColumnPage(Adw.BreakpointBin):
    """Scrolling page content in up to three columns, chosen by the page's own width.

    Opening the setup sidebar narrows the page, so it reflows too. Bands are fixed rows
    of side-by-side columns (stacked when narrow); the flow is a list of groups in reading
    order, cut into balanced columns: down the first column, then the next. Pages call
    reflow() after their content changes shape; it moves nothing unless the cut changes.
    """

    def __init__(self):
        super().__init__(width_request=280, height_request=200)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, margin_top=GAP,
                               margin_bottom=GAP, margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=1800, tightening_threshold=1200, child=self.content)
        self.set_child(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True))
        # When several breakpoints match, the last one added applies.
        self.breakpoints = []
        for count, condition in ((2, TWO_COLUMNS), (1, ONE_COLUMN)):
            breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(condition))
            self.add_breakpoint(breakpoint)
            self.breakpoints.append((breakpoint, count))
        self.one_column = self.breakpoints[-1][0]
        self.flow = []
        self.flow_box = Gtk.Box(spacing=GAP, homogeneous=True)
        self.cut = None
        self.connect("notify::current-breakpoint", lambda *_: self.reflow())

    @property
    def column_count(self):
        current = self.get_current_breakpoint()
        return next((count for breakpoint, count in self.breakpoints if breakpoint == current), 3)

    def add_band(self, *columns):
        """A fixed row of columns, each a list of widgets."""
        band = Gtk.Box(spacing=GAP, homogeneous=True)
        for widgets in columns:
            band.append(self.column(widgets))
        # Stacked columns keep their natural heights; side by side they share the width equally.
        self.one_column.add_setter(band, "orientation", Gtk.Orientation.VERTICAL)
        self.one_column.add_setter(band, "homogeneous", False)
        self.content.append(band)
        return band

    def add_flow(self, groups):
        """Groups in reading order, balanced across the columns that fit."""
        self.flow = list(groups)
        self.content.append(self.flow_box)
        self.reflow()

    def column(self, widgets):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP)
        for widget in widgets:
            column.append(widget)
        return column

    def reflow(self):
        if not self.flow:
            return
        count = self.column_count
        width = self.flow_box.get_width()
        width = (width - GAP * (count - 1)) // count if width > 0 else -1
        heights = [group.measure(Gtk.Orientation.VERTICAL, width)[1] if group.get_visible() else 0
                   for group in self.flow]
        cut = balanced_split(heights, count)
        if cut == self.cut:
            return
        self.cut = cut
        for group in self.flow:
            if group.get_parent():
                group.get_parent().remove(group)
        while child := self.flow_box.get_first_child():
            self.flow_box.remove(child)
        start = 0
        for run in cut:
            self.flow_box.append(self.column(self.flow[start:start + run]))
            start += run


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
