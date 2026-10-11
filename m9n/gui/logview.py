"""Bounded, filterable log list shared by receiver messages and the application log.

Entries are batched: callers queue them and the list is updated at most every
FLUSH_MS, so verbose debug output cannot flood the main loop with row inserts.
"""
import logging
from datetime import datetime, timezone

from gi.repository import Gio, GLib, GObject, Gtk, Pango

from ..serialize import safe

MAX_ROWS = 5000
FLUSH_MS = 200
LEVELS = [("Errors", logging.ERROR), ("Warnings", logging.WARNING), ("Info", logging.INFO), ("Debug", logging.DEBUG)]
LEVEL_ICON = {logging.ERROR: ("dialog-error-symbolic", "error"), logging.WARNING: ("dialog-warning-symbolic", "warning")}


class LogEntry(GObject.Object):
    def __init__(self, time, level, name, text):
        super().__init__()
        self.time, self.level, self.name, self.text = time, level, name, safe(text)

    def line(self):
        return f"{self.time} {logging.getLevelName(self.level)} {self.name}: {self.text}"


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LogList(Gtk.Box):
    def __init__(self, placeholder, default_level=logging.INFO, monospace=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pending = []
        self._flush_source = 0
        self.store = Gio.ListStore(item_type=LogEntry)
        self.level = default_level
        self.query = ""
        self.filter = Gtk.CustomFilter.new(self.matches, None)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)

        toolbar = Gtk.Box(spacing=6)
        self.level_menu = Gtk.DropDown.new_from_strings([label for label, _ in LEVELS])
        self.level_menu.set_selected([level for _, level in LEVELS].index(default_level))
        self.level_menu.set_tooltip_text("Minimum level shown")
        self.level_menu.connect("notify::selected", self.on_level)
        self.search = Gtk.SearchEntry(placeholder_text="Search", hexpand=True)
        self.search.connect("search-changed", self.on_search)
        toolbar.append(self.level_menu)
        toolbar.append(self.search)
        for icon, tooltip, callback in (("edit-copy-symbolic", "Copy visible lines", self.copy),
                                        ("document-save-as-symbolic", "Save visible lines…", self.save)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            button.connect("clicked", callback)
            toolbar.append(button)
        self.append(toolbar)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        self.list = Gtk.ListView(model=Gtk.NoSelection(model=self.filtered), factory=factory)
        if monospace:
            self.list.add_css_class("monospace")
        self.scrolled = Gtk.ScrolledWindow(child=self.list, vexpand=True)
        self.scrolled.add_css_class("card")
        self.empty = Gtk.Label(label=placeholder, vexpand=True)
        self.empty.add_css_class("dim-label")
        self.views = Gtk.Stack()
        self.views.add_named(self.empty, "empty")
        self.views.add_named(self.scrolled, "list")
        self.append(self.views)
        self.filtered.connect("items-changed", lambda *_: self.views.set_visible_child_name(
            "list" if self.filtered.get_n_items() else "empty"))

    # Rows ----------------------------------------------------------------

    def setup_row(self, _factory, item):
        icon = Gtk.Image(valign=Gtk.Align.START)
        time = Gtk.Label(xalign=0, valign=Gtk.Align.START)
        time.add_css_class("dim-label")
        time.add_css_class("numeric")
        name = Gtk.Label(xalign=0, valign=Gtk.Align.START)
        name.add_css_class("heading")
        text = Gtk.Label(xalign=0, hexpand=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
        row = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
        for widget in (icon, time, name, text):
            row.append(widget)
        item.set_child(row)

    def bind_row(self, _factory, item):
        entry = item.get_item()
        icon, time, name, text = self.children(item.get_child())
        icon_name, style = LEVEL_ICON.get(entry.level, (None, None))
        icon.set_from_icon_name(icon_name)
        icon.set_visible(icon_name is not None)
        for css in ("error", "warning"):
            icon.remove_css_class(css)
        if style:
            icon.add_css_class(style)
        time.set_label(entry.time[11:23] if len(entry.time) >= 23 else entry.time)
        name.set_label(entry.name)
        text.set_label(entry.text)
        item.get_child().update_property([Gtk.AccessibleProperty.LABEL], [entry.line()])

    @staticmethod
    def children(row):
        child, found = row.get_first_child(), []
        while child:
            found.append(child)
            child = child.get_next_sibling()
        return found

    # Filtering -----------------------------------------------------------

    def matches(self, entry, _data):
        if entry.level < self.level:
            return False
        return not self.query or self.query in entry.line().casefold()

    def on_level(self, *_):
        self.level = LEVELS[self.level_menu.get_selected()][1]
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def on_search(self, *_):
        self.query = self.search.get_text().casefold()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    # Batched appends -----------------------------------------------------

    def add(self, entry):
        self.pending.append(entry)
        if not self._flush_source:
            self._flush_source = GLib.timeout_add(FLUSH_MS, self.flush)

    def flush(self):
        self._flush_source = 0
        entries, self.pending = self.pending[-MAX_ROWS:], []
        if not entries:
            return GLib.SOURCE_REMOVE
        adjustment = self.scrolled.get_vadjustment()
        # Follow new lines only while the user is at the bottom; scrolling up pauses it.
        following = adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 4
        self.store.splice(self.store.get_n_items(), 0, entries)
        excess = self.store.get_n_items() - MAX_ROWS
        if excess > 0:
            self.store.splice(0, excess, [])
        count = self.filtered.get_n_items()
        if following and count:
            self.list.scroll_to(count - 1, Gtk.ListScrollFlags.NONE, None)
        return GLib.SOURCE_REMOVE

    def clear(self):
        self.pending.clear()
        self.store.remove_all()

    def visible_text(self):
        self.flush()
        return "\n".join(self.filtered.get_item(i).line() for i in range(self.filtered.get_n_items()))

    # Copy / save ---------------------------------------------------------

    def copy(self, _button=None):
        self.get_clipboard().set(self.visible_text())
        window = self.get_root()
        if hasattr(window, "toast"):
            window.toast("Lines copied")

    def save(self, _button=None):
        dialog = Gtk.FileDialog(title="Save Log", initial_name="m9n-log.txt", modal=True)
        dialog.save(self.get_root(), None, self.finish_save)

    def finish_save(self, dialog, result):
        try:
            path = dialog.save_finish(result).get_path()
        except GLib.Error:
            return
        window = self.get_root()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.visible_text() + "\n")
        except OSError as exc:
            if hasattr(window, "alert"):
                window.alert("Log Not Saved", str(exc))
            return
        if hasattr(window, "toast"):
            window.toast("Log saved")

    def do_unroot(self):
        if self._flush_source:
            GLib.source_remove(self._flush_source)
            self._flush_source = 0
        Gtk.Box.do_unroot(self)


class UILogHandler(logging.Handler):
    """Feeds the application log list; level filtering happens in the view."""

    def __init__(self, log_list):
        super().__init__(logging.DEBUG)
        self.log_list = log_list
        self.setFormatter(logging.Formatter())

    def emit(self, record):
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + self.formatter.formatException(record.exc_info)
            time = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
            self.log_list.add(LogEntry(time, record.levelno, record.levelname.title(), text))
        except Exception:
            self.handleError(record)
