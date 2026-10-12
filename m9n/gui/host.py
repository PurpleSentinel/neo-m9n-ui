"""Host diagnostics page (replaces `m9n doctor` and `m9n list`).

Read-only: never opens the tty and never runs privileged commands. External
commands run through Gio.Subprocess so the window stays responsive.
"""
import json
import shutil

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import clock, hostinfo
from ..serialize import json_line, safe
from . import format as fmt
from .pages import ColumnPage, SessionPage, copy_button, copy_text

COMMAND_TITLES = {"lsusb": "USB devices (lsusb)", "udev": "udev properties", "owners": "Port owners (fuser)",
                  "gpsd": "gpsd service", "modemmanager": "ModemManager service"}


class Collector:
    """Run hostinfo.commands() concurrently and build the diagnostics document."""

    def __init__(self, explicit_port, done):
        self.done = done
        self.rows = hostinfo.devices()
        self.port, self.error = hostinfo.select_port(explicit_port, self.rows)
        self.results = {}
        self.specs = hostinfo.commands(self.port)
        for key, command in self.specs.items():
            if not shutil.which(command[0]):
                self.finish(key, hostinfo.command_result(command, available=False))
                continue
            try:
                process = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            except GLib.Error as exc:
                self.finish(key, hostinfo.command_result(command, error=exc.message))
                continue
            cancellable = Gio.Cancellable()
            timer = GLib.timeout_add_seconds(hostinfo.COMMAND_TIMEOUT, self.expire, process, cancellable)
            process.communicate_utf8_async(None, cancellable, self.communicated, (key, command, timer))

    def expire(self, process, cancellable):
        cancellable.cancel()
        process.force_exit()
        return GLib.SOURCE_REMOVE

    def communicated(self, process, outcome, data):
        key, command, timer = data
        try:
            _ok, stdout, stderr = process.communicate_utf8_finish(outcome)
        except GLib.Error as exc:
            text = "timed out" if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED) else exc.message
            self.finish(key, hostinfo.command_result(command, error=text))
            return
        GLib.source_remove(timer)
        code = process.get_exit_status() if process.get_if_exited() else None
        self.finish(key, hostinfo.command_result(command, exit_code=code, stdout=stdout or "", stderr=stderr or ""))

    def finish(self, key, result):
        self.results[key] = result
        if len(self.results) == len(self.specs):
            self.done(hostinfo.diagnostics(self.port, self.error, self.rows, self.results))


def check_row(title):
    row = Adw.ActionRow(title=title, use_markup=False, subtitle_lines=0)
    row.icon = Gtk.Image()
    row.add_prefix(row.icon)
    return row


def set_check(row, ok, text):
    """ok: True (good), False (problem) or None (neutral information)."""
    icon, style = {True: ("object-select-symbolic", "success"), False: ("dialog-warning-symbolic", "warning"),
                   None: ("dialog-information-symbolic", "dim-label")}[ok]
    for css in ("success", "warning", "dim-label"):
        row.icon.remove_css_class(css)
    row.icon.set_from_icon_name(icon)
    row.icon.add_css_class(style)
    row.set_subtitle(safe(text))


LEVELS = {"ok": ("object-select-symbolic", "success"), "warning": ("dialog-warning-symbolic", "warning"),
          "error": ("dialog-error-symbolic", "error"), None: ("dialog-information-symbolic", "dim-label")}


def set_level(row, level, text):
    """Like set_check, with red for a problem that needs fixing: level is 'ok', 'warning', 'error' or None."""
    icon, style = LEVELS[level]
    for css in ("success", "warning", "error", "dim-label"):
        row.icon.remove_css_class(css)
    row.icon.set_from_icon_name(icon)
    row.icon.add_css_class(style)
    row.set_subtitle(safe(text))


class HostPage(SessionPage):
    def __init__(self, page):
        super().__init__(page)
        self.document = None
        self.collecting = False
        self.stack.set_visible_child_name("content")  # Host checks never need a session.

    def build(self):
        actions = Gtk.Box(spacing=6)
        self.spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        self.spinner.set_visible(False)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Run the host checks again")
        refresh.update_property([Gtk.AccessibleProperty.LABEL], ["Refresh host diagnostics"])
        refresh.connect("clicked", lambda _b: self.refresh())
        save = Gtk.Button(icon_name="document-save-as-symbolic", tooltip_text="Save diagnostics as JSON…")
        save.update_property([Gtk.AccessibleProperty.LABEL], ["Save diagnostics as JSON"])
        save.connect("clicked", self.save)
        for widget in (self.spinner, refresh, copy_button("Copy diagnostics JSON", self.copy), save):
            actions.append(widget)

        self.summary = Adw.PreferencesGroup(title="Receiver Access", header_suffix=actions,
                                            description="Read-only checks; the receiver's tty is never opened here.")
        self.checks = {}
        for key, title in (("port", "Selected port"), ("access", "Read/write access"), ("driver", "cdc_acm driver"),
                           ("owners", "Processes holding the port"), ("gpsd", "gpsd"),
                           ("modemmanager", "ModemManager")):
            self.checks[key] = check_row(title)
            self.summary.add(self.checks[key])

        self.clock_group = Adw.PreferencesGroup(
            title="System Clock",
            description="This computer's clock compared with GNSS time from the receiver. Read-only: the "
                        "application never changes the clock.")
        self.clock_row = check_row("System clock")
        self.clock_basis = check_row("Compared with")
        for row in (self.clock_row, self.clock_basis):
            self.clock_group.add(row)
        self.show_clock(None)

        self.fix = Adw.PreferencesGroup(
            title="Grant Access",
            description="Run these yourself in a terminal; the application never asks for administrator rights. "
                        "The rule gives the active desktop user access to u-blox ttys. For SSH or headless use, "
                        "see docs/FEDORA.md.")
        for command in hostinfo.udev_install_commands():
            row = Adw.ActionRow(title=command, use_markup=False, title_selectable=True)
            row.add_css_class("monospace")
            if not command.startswith("#"):
                row.add_suffix(copy_button("Copy command", lambda _b, c=command: copy_text(self, c, "Command copied")))
            self.fix.add(row)

        self.devices = Adw.PreferencesGroup(title="USB Serial Devices")
        self.device_rows = []

        self.outputs = Adw.PreferencesGroup(title="Command Output")
        self.output_rows = {}
        for key, title in COMMAND_TITLES.items():
            row = Adw.ExpanderRow(title=title, use_markup=False)
            # Command output has long unbroken tokens (udev paths); breaking them keeps the column narrow.
            label = Gtk.Label(xalign=0, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
            label.add_css_class("monospace")
            row.add_row(label)
            row.label = label
            self.output_rows[key] = row
            self.outputs.add(row)
        hints = Adw.PreferencesGroup(title="Hints", description="\n".join(hostinfo.HINTS))
        page = self.columns = ColumnPage()
        page.add_flow([self.summary, self.clock_group, self.fix, self.devices, self.outputs, hints])
        return page

    def update(self, snapshot):
        self.snapshot = snapshot  # Host checks are independent of session data; the clock is not.
        self.show_clock(snapshot)

    def show_clock(self, snapshot):
        config = getattr(self, "config", None)
        if snapshot is None or config is None:
            set_level(self.clock_row, None, "Start a session to compare it with GNSS time")
            set_level(self.clock_basis, None, fmt.host_clock_basis(None))
            return
        value = snapshot.get("host_clock")
        text, level = fmt.host_clock(value, config.source != "replay")
        set_level(self.clock_row, level, text + (f". {clock.HINT}" if level == "error" else ""))
        set_level(self.clock_basis, None, fmt.host_clock_basis(value) if config.source != "replay"
                  else "A replay's GNSS times are from when it was recorded")

    def do_map(self):
        SessionPage.do_map(self)
        if self.document is None and not self.collecting:
            self.refresh()

    def explicit_port(self):
        settings = getattr(self.get_root(), "setup", None)
        settings = settings.settings if settings else None
        return settings.port if settings and settings.source == "device" and settings.port else None

    def refresh(self):
        if self.collecting:
            return
        self.collecting = True
        self.spinner.set_visible(True)
        Collector(self.explicit_port(), self.show_document)

    def show_document(self, document):
        self.collecting = False
        self.spinner.set_visible(False)
        self.document = document
        port, error = document["selected_port"], document["selection_error"]
        set_check(self.checks["port"], bool(port), port or error)
        access = document["read_write_access"]
        set_check(self.checks["access"], access if port else None,
                  "yes" if access else ("no: see Grant Access below" if port else "no port selected"))
        set_check(self.checks["driver"], document["cdc_acm_loaded"],
                  "loaded" if document["cdc_acm_loaded"] else "not loaded; try 'sudo modprobe cdc_acm'")
        if "owners" in document:
            owners = hostinfo.owners_text(document["owners"])
            held = {"none": True, "fuser not installed": None}.get(owners, False)
            set_check(self.checks["owners"], held, owners)
        else:
            set_check(self.checks["owners"], None, "no port selected")
        for key in ("gpsd", "modemmanager"):
            states = hostinfo.unit_states(document[key])
            if states is None:
                set_check(self.checks[key], None, safe(document[key]["output"]))
                continue
            active = [unit for unit, state in states if state == "active"]
            text = ", ".join(f"{unit}: {state}" for unit, state in states)
            if active and key == "modemmanager" and hostinfo.modemmanager_ignores(document):
                set_check(self.checks[key], True, text + "; udev tells it to ignore this receiver")
                continue
            # An active service may compete for the port; it is a hint, not a failure.
            set_check(self.checks[key], not active, text + (
                "; it may open GNSS ports, stop it or exclude this device" if active else ""))
        self.fix.set_visible(bool(port) and not access)
        self.show_devices(document["devices"])
        for key, row in self.output_rows.items():
            result = document.get(key)
            row.set_visible(result is not None)
            if result is not None:
                row.label.set_label(safe(result["output"]) or "(no output)")
                code = result.get("exit_code")
                row.set_subtitle("not installed" if not result["available"] else
                                 f"exit status {code}" if code is not None else "did not complete")
        self.columns.reflow()

    def show_devices(self, rows):
        for row in self.device_rows:
            self.devices.remove(row)
        self.device_rows = []
        if not rows:
            rows_to_add = [Adw.ActionRow(title="No ttyACM or ttyUSB devices",
                                         subtitle="Check the USB data cable and the receiver's power.")]
        else:
            rows_to_add = []
            for device in rows:
                row = Adw.ActionRow(title=device["port"], use_markup=False, subtitle_lines=0,
                                    subtitle=" · ".join(filter(None, [
                                        f"{device['vid']}:{device['pid']}" if device["vid"] else "no USB identity",
                                        device["product"], device["manufacturer"],
                                        device["serial"] and f"serial {device['serial']}",
                                        *device.get("by_id", [])])))
                if device["vid"] == hostinfo.UBLOX_VID:
                    badge = Gtk.Label(label="u-blox", valign=Gtk.Align.CENTER)
                    badge.add_css_class("caption-heading")
                    badge.add_css_class("accent")
                    row.add_suffix(badge)
                rows_to_add.append(row)
        for row in rows_to_add:
            row.set_use_markup(False)
            self.devices.add(row)
            self.device_rows.append(row)

    def copy(self, _button):
        copy_text(self, self.document and json.dumps(self.document, indent=2), "Diagnostics copied")

    def save(self, _button):
        if not self.document:
            return
        dialog = Gtk.FileDialog(title="Save Host Diagnostics", initial_name="m9n-host-diagnostics.json", modal=True)
        dialog.save(self.get_root(), None, self.finish_save)

    def finish_save(self, dialog, result):
        try:
            path = dialog.save_finish(result).get_path()
        except GLib.Error:
            return
        self.write(path)

    def write(self, path):
        window = self.get_root()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json_line(self.document) + "\n")
        except OSError as exc:
            window.alert("Diagnostics Not Saved", str(exc))
            return False
        window.toast("Diagnostics saved")
        return True
