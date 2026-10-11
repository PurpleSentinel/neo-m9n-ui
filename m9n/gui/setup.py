"""Session setup sidebar: source, mode, receiver options, timing and evidence files.

Widgets only mirror a SessionSettings; every rule lives in m9n/settings.py.
"""
from pathlib import Path

from gi.repository import Adw, GLib, GObject, Gtk

from .. import hostinfo
from ..controller import OUTPUT_FIELDS
from ..settings import BAUD_RATES, OUTPUT_LABEL, OUTPUT_SUFFIX

SOURCES = [("device", "USB receiver"), ("demo", "Synthetic demo"), ("replay", "Replay capture")]
MODES = [("watch", "Watch"), ("test", "Device test")]
FAULTS = [("", "None"), ("no-fix", "No fix"), ("rf", "RF alarm"), ("corrupt", "Corrupt frames"),
          ("frozen", "Frozen epochs"), ("silent", "Silent receiver"), ("drive", "Moving vehicle")]
RESCAN_MS = 2000


def combo(title, labels, subtitle=None):
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def icon_button(icon, tooltip, callback):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    button.connect("clicked", callback)
    return button


class SetupPanel(Adw.Bin):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.problems = {}
        self._updating = False
        self._devices = []  # (value stored in settings, label) for discovered receivers
        page = Adw.PreferencesPage()
        self.set_child(page)

        group = Adw.PreferencesGroup(title="Source")
        self.source_row = combo("Source", [label for _, label in SOURCES])
        self.device_row = Adw.ComboRow(title="Receiver", model=Gtk.StringList())
        self.port_row = Adw.EntryRow(title="Port path")
        self.fault_row = combo("Simulated fault", [label for _, label in FAULTS])
        self.capture_row = Adw.ActionRow(title="Capture file")
        self.capture_row.add_suffix(icon_button("document-open-symbolic", "Choose a capture file", self.choose_capture))
        for row in (self.source_row, self.device_row, self.port_row, self.fault_row, self.capture_row):
            group.add(row)
        page.add(group)

        group = Adw.PreferencesGroup(title="Session")
        self.mode_row = combo("Mode", [label for _, label in MODES])
        self.duration_row = Adw.SpinRow.new_with_range(0, 7 * 86400, 1)
        self.duration_row.set_title("Duration")
        group.add(self.mode_row)
        group.add(self.duration_row)
        page.add(group)

        self.receiver_group = Adw.PreferencesGroup(title="Receiver")
        self.baud_row = combo("Baud rate", [str(rate) for rate in BAUD_RATES],
                              "Only matters for USB-to-UART adapters")
        self.passive_row = Adw.SwitchRow(title="Passive", subtitle="Listen only; send no diagnostic polls")
        self.receiver_group.add(self.baud_row)
        self.receiver_group.add(self.passive_row)
        page.add(self.receiver_group)

        group = Adw.PreferencesGroup(title="Timing")
        self.interval_row = Adw.SpinRow.new_with_range(.1, 60, .1)
        self.interval_row.set_digits(1)
        self.interval_row.set_title("Refresh interval")
        self.interval_row.set_subtitle("Seconds between snapshots; not the receiver's rate")
        self.stale_row = Adw.SpinRow.new_with_range(.5, 300, .5)
        self.stale_row.set_digits(1)
        self.stale_row.set_title("Stale after")
        self.stale_row.set_subtitle("Seconds without a new navigation epoch")
        group.add(self.interval_row)
        group.add(self.stale_row)
        page.add(group)

        group = Adw.PreferencesGroup(
            title="Evidence Files",
            description="Captures and logs contain your precise location and receiver identifiers.")
        self.folder_row = Adw.ActionRow(title="Folder")
        self.folder_row.add_suffix(icon_button("folder-open-symbolic", "Choose the evidence folder", self.choose_folder))
        group.add(self.folder_row)
        self.output_rows = {}
        self.clear_buttons = {}
        for kind in OUTPUT_FIELDS:
            row = Adw.SwitchRow(title=OUTPUT_LABEL[kind])
            row.add_suffix(icon_button("document-save-as-symbolic", "Use a specific file",
                                       lambda _b, k=kind: self.choose_output(k)))
            clear = icon_button("edit-clear-symbolic", "Use a timestamped file in the folder",
                                lambda _b, k=kind: self.clear_output(k))
            row.add_suffix(clear)
            self.output_rows[kind] = row
            self.clear_buttons[kind] = clear
            group.add(row)
        self.debug_row = Adw.SwitchRow(title="Debug logging", subtitle="Include RX/TX hex and decoded messages")
        group.add(self.debug_row)
        page.add(group)

        self.problem_label = Gtk.Label(wrap=True, xalign=0, margin_start=12, margin_end=12, margin_bottom=12)
        self.problem_label.add_css_class("error")
        status = Adw.PreferencesGroup()
        status.add(self.problem_label)
        page.add(status)

        self.field_rows = {"source": self.source_row, "port": self.port_row, "fault": self.fault_row,
                           "capture": self.capture_row, "mode": self.mode_row,
                           "watch_duration": self.duration_row, "test_duration": self.duration_row,
                           "interval": self.interval_row, "stale_after": self.stale_row,
                           "output_dir": self.folder_row, **self.output_rows}

        for row in (*set(self.field_rows.values()), self.device_row, self.baud_row, self.passive_row, self.debug_row):
            row.set_use_markup(False)  # Paths and device names are plain text, never markup.

        self.rescan()
        self.load()
        for row in (self.source_row, self.device_row, self.fault_row, self.mode_row, self.baud_row):
            row.connect("notify::selected", self.on_widget_changed)
        for row in (self.passive_row, self.debug_row, *self.output_rows.values()):
            row.connect("notify::active", self.on_widget_changed)
        for row in (self.duration_row, self.interval_row, self.stale_row):
            row.connect("notify::value", self.on_widget_changed)
        self.port_row.connect("changed", self.on_widget_changed)
        self._rescan_source = GLib.timeout_add(RESCAN_MS, self.on_rescan_timer)
        self.connect("destroy", lambda *_: GLib.source_remove(self._rescan_source))

    @property
    def valid(self):
        return not self.problems

    # Settings -> widgets ---------------------------------------------------

    def load(self):
        s = self.settings
        self._updating = True
        try:
            self.source_row.set_selected([k for k, _ in SOURCES].index(s.source))
            self.select_device(s.port)
            self.fault_row.set_selected(next((i for i, (k, _) in enumerate(FAULTS) if k == s.fault), 0))
            self.mode_row.set_selected([k for k, _ in MODES].index(s.mode))
            self.baud_row.set_selected(BAUD_RATES.index(s.baud))
            self.passive_row.set_active(s.passive)
            self.interval_row.set_value(s.interval)
            self.stale_row.set_value(s.stale_after)
            self.debug_row.set_active(s.debug)
            for kind, row in self.output_rows.items():
                row.set_active(getattr(s, kind))
            self.load_duration()
        finally:
            self._updating = False
        self.refresh()

    def load_duration(self):
        testing = self.settings.effective_mode == "test"
        self.duration_row.set_value(self.settings.test_duration if testing else self.settings.watch_duration)
        self.duration_row.set_subtitle("Seconds the test observes the receiver" if testing
                                       else "Seconds; 0 runs until stopped")

    def select_device(self, port):
        values = [value for value, _ in self._devices]
        if not port:
            self.device_row.set_selected(0)
        elif port in values:
            self.device_row.set_selected(values.index(port) + 1)
        else:
            self.device_row.set_selected(len(values) + 1)
            if self.port_row.get_text() != port:
                self.port_row.set_text(port)

    # Widgets -> settings ---------------------------------------------------

    def on_widget_changed(self, *_):
        if self._updating:
            return
        s = self.settings
        previous_mode = s.effective_mode
        s.source = SOURCES[self.source_row.get_selected()][0]
        s.fault = FAULTS[self.fault_row.get_selected()][0]
        s.mode = MODES[self.mode_row.get_selected()][0]
        s.baud = BAUD_RATES[self.baud_row.get_selected()]
        s.passive = self.passive_row.get_active()
        s.interval = self.interval_row.get_value()
        s.stale_after = self.stale_row.get_value()
        s.debug = self.debug_row.get_active()
        for kind, row in self.output_rows.items():
            setattr(s, kind, row.get_active())
        index = self.device_row.get_selected()
        if index == 0:
            s.port = ""
        elif index <= len(self._devices):
            s.port = self._devices[index - 1][0]
        else:
            s.port = self.port_row.get_text().strip()
        if s.effective_mode != previous_mode:
            self._updating = True
            self.load_duration()
            self._updating = False
        elif s.effective_mode == "test":
            s.test_duration = self.duration_row.get_value()
        else:
            s.watch_duration = self.duration_row.get_value()
        self.refresh()

    def refresh(self):
        s = self.settings
        device, demo, replay = (s.source == key for key, _ in SOURCES)
        self.device_row.set_visible(device)
        self.port_row.set_visible(device and self.device_row.get_selected() == len(self._devices) + 1)
        self.fault_row.set_visible(demo)
        self.capture_row.set_visible(replay)
        self.capture_row.set_subtitle(s.capture or "No file chosen")
        self.mode_row.set_visible(not replay)
        self.duration_row.set_visible(not replay)
        self.receiver_group.set_visible(device)
        self.folder_row.set_subtitle(s.output_dir or "No folder chosen")
        for kind, row in self.output_rows.items():
            row.set_visible(s.applies(kind))
            pinned = s.pinned.get(kind)
            row.set_subtitle(pinned or f"m9n-<date-time>{OUTPUT_SUFFIX[kind]}")
            self.clear_buttons[kind].set_visible(bool(pinned))
        self.problems = s.problems()
        for name, row in self.field_rows.items():
            row.remove_css_class("error")
            row.set_tooltip_text(None)
        for name, message in self.problems.items():
            row = self.field_rows.get(name)
            if row is not None:
                row.add_css_class("error")
                row.set_tooltip_text(message)
        self.problem_label.set_label(next(iter(self.problems.values()), ""))
        self.problem_label.set_visible(bool(self.problems))
        self.emit("changed")

    # Device discovery ------------------------------------------------------

    def rescan(self):
        devices = []
        for row in hostinfo.devices():
            value = row["by_id"][0] if row["by_id"] else row["port"]
            name = Path(row["port"]).name
            label = f"{name} · {row['product']}" if row["product"] else name
            devices.append((value, label + (" (u-blox)" if row["vid"] == hostinfo.UBLOX_VID else "")))
        if devices == self._devices:
            return False
        self._devices = devices
        self._updating = True
        try:
            labels = ["Automatic", *(label for _, label in devices), "Other path…"]
            self.device_row.set_model(Gtk.StringList.new(labels))
            self.select_device(self.settings.port)
        finally:
            self._updating = False
        return True

    def on_rescan_timer(self):
        if self.get_sensitive() and self.get_mapped() and self.rescan():
            self.refresh()
        return GLib.SOURCE_CONTINUE

    # File choosers ---------------------------------------------------------

    def window(self):
        return self.get_root()

    def choose_capture(self, _button):
        dialog = Gtk.FileDialog(title="Choose a Raw Capture", modal=True)
        dialog.open(self.window(), None, self.finish_choice, "capture", dialog.open_finish)

    def choose_folder(self, _button):
        dialog = Gtk.FileDialog(title="Choose the Evidence Folder", modal=True)
        dialog.select_folder(self.window(), None, self.finish_choice, "output_dir", dialog.select_folder_finish)

    def choose_output(self, kind):
        dialog = Gtk.FileDialog(title=f"Save {OUTPUT_LABEL[kind]} As", modal=True,
                                initial_name=f"m9n{OUTPUT_SUFFIX[kind]}")
        dialog.save(self.window(), None, self.finish_choice, kind, dialog.save_finish)

    def finish_choice(self, _dialog, result, target, finish):
        try:
            chosen = finish(result)
        except GLib.Error:
            return  # Cancelled or dismissed.
        path = chosen.get_path()
        if not path:
            return
        if target in ("capture", "output_dir"):
            setattr(self.settings, target, path)
        else:
            self.settings.pinned[target] = path
            self.output_rows[target].set_active(True)
        self.refresh()

    def clear_output(self, kind):
        self.settings.pinned.pop(kind, None)
        self.refresh()
