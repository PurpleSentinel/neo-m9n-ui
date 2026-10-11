"""Device Test page: run a timed test, follow its progress, read and keep the report."""
from gi.repository import Adw, GLib, Gtk

from ..serialize import json_line, safe
from . import format as fmt
from .pages import ColumnPage, SessionPage, copy_text

PROGRESS_MS = 250
STATUS = {
    "PASS": ("success", "object-select-symbolic",
             "Valid recent communication and an advancing position fix, a confirmed NEO-M9N model, diagnostic "
             "responses and no observed integrity or error warnings."),
    "WARN": ("warning", "dialog-warning-symbolic",
             "Communication and navigation work, but fix, identity or diagnostic evidence is incomplete, or RF or "
             "integrity warnings were observed."),
    "FAIL": ("error", "dialog-error-symbolic",
             "No usable navigation or communication, stale or frozen navigation, an unexpected model, a receiver "
             "error, a disconnect or a local I/O failure."),
    "INTERRUPTED": ("dim-label", "process-stop-symbolic",
                    "The test was stopped before its duration elapsed. The evidence collected so far is recorded."),
}
CHECKS = {"communication": "Communication", "device_identity": "Device identity", "navigation": "Navigation",
          "position_fix": "Position fix", "protocol_integrity": "Protocol integrity", "rf_health": "RF health",
          "receiver_errors": "Receiver errors", "usb_transport": "USB transport"}
GUIDANCE = ("No fix is not proof of broken hardware: indoor reception, antenna placement, initial acquisition and "
            "receiver configuration can all prevent one. RF detector values are indications, not certification. "
            "An unavailable message is reported as unknown rather than healthy.")


class TestPage(SessionPage):
    def __init__(self, page):
        super().__init__(page)
        self.report = None
        self.saved_to = None
        self._progress_source = 0
        run = Gtk.Button(label="Run Device Test", action_name="win.run-test", halign=Gtk.Align.CENTER)
        run.add_css_class("pill")
        run.add_css_class("suggested-action")
        self.stack.get_child_by_name("empty").set_child(run)

    def build(self):
        self.running = Adw.StatusPage(icon_name="content-loading-symbolic", title="Device Test Running")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, width_request=320)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.remaining = Gtk.Label()
        self.remaining.add_css_class("numeric")
        self.remaining.add_css_class("dim-label")
        cancel = Gtk.Button(label="Cancel Test", action_name="win.toggle-session", halign=Gtk.Align.CENTER)
        cancel.add_css_class("pill")
        cancel.add_css_class("destructive-action")
        for widget in (self.progress, self.remaining, cancel):
            box.append(widget)
        self.running.set_child(box)

        page = self.columns = ColumnPage()
        self.status_icon = Gtk.Image(pixel_size=48)
        self.status_title = Gtk.Label(xalign=0)
        self.status_title.add_css_class("title-1")
        self.status_text = Gtk.Label(xalign=0, wrap=True)
        self.status_text.add_css_class("dim-label")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        text.append(self.status_title)
        text.append(self.status_text)
        self.hero = Gtk.Box(spacing=18, margin_top=6, margin_bottom=6)
        self.hero.append(self.status_icon)
        self.hero.append(text)
        group = Adw.PreferencesGroup()
        group.add(self.hero)
        page.add_band([group])
        self.checks_group = Adw.PreferencesGroup(title="Checks")
        self.check_rows = []

        report = Adw.PreferencesGroup(title="Report")
        self.report_row = Adw.ActionRow(title="Report file", use_markup=False, subtitle_selectable=True)
        save = Gtk.Button(label="Save Report…", valign=Gtk.Align.CENTER)
        save.connect("clicked", self.save_report)
        self.save_button = save
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy report JSON", valign=Gtk.Align.CENTER)
        copy.update_property([Gtk.AccessibleProperty.LABEL], ["Copy report JSON"])
        copy.add_css_class("flat")
        copy.connect("clicked", lambda _b: copy_text(self, self.report and json_line(self.report),
                                                      "Report JSON copied"))
        self.report_row.add_suffix(save)
        self.report_row.add_suffix(copy)
        report.add(self.report_row)
        page.add_flow([self.checks_group, report,
                       Adw.PreferencesGroup(title="Interpreting the Result", description=GUIDANCE)])

        self.views = Gtk.Stack()
        self.views.add_named(self.running, "running")
        self.views.add_named(page, "result")
        return self.views

    # Session events ------------------------------------------------------

    def session_started(self, config):
        super().session_started(config)
        if config.mode != "test":
            return  # A watch session leaves the last result in place.
        self.running.set_description(f"Observing the receiver for {fmt.seconds(config.duration)}. "
                                     "Live data stays available on the other pages.")
        self.views.set_visible_child_name("running")
        self.stack.set_visible_child_name("content")
        self.tick()
        self._progress_source = GLib.timeout_add(PROGRESS_MS, self.tick)

    def update(self, snapshot):
        self.snapshot = snapshot  # Progress comes from the runner, results from the report.

    def session_finished(self, result):
        self.stop_progress()
        if result.report is None:
            return
        self.report = result.report
        failed = any(e.startswith("Report not written") for e in result.output_errors)
        self.saved_to = None if failed else self.config.report
        self.show_report(result.report, failed)
        self.views.set_visible_child_name("result")
        self.stack.set_visible_child_name("content")
        self.columns.reflow()

    def tick(self):
        window = self.get_root()
        runner = getattr(window, "runner", None)
        fraction = runner.progress() if runner and runner.running else None
        if fraction is None:
            return GLib.SOURCE_CONTINUE
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"{fraction:.0%}")
        self.remaining.set_label(f"{fmt.seconds(max(0.0, self.config.duration * (1 - fraction)))} remaining")
        return GLib.SOURCE_CONTINUE

    def stop_progress(self):
        if self._progress_source:
            GLib.source_remove(self._progress_source)
            self._progress_source = 0

    def do_unroot(self):
        self.stop_progress()
        SessionPage.do_unroot(self)

    # Result --------------------------------------------------------------

    def show_report(self, report, write_failed=False):
        status = report["status"]
        style, icon, meaning = STATUS[status]
        for name in ("success", "warning", "error", "dim-label"):
            self.status_icon.remove_css_class(name)
            self.status_title.remove_css_class(name)
        self.status_icon.set_from_icon_name(icon)
        self.status_icon.add_css_class(style)
        self.status_title.add_css_class(style)
        self.status_title.set_label(status)
        self.status_text.set_label(meaning)
        self.hero.update_property([Gtk.AccessibleProperty.LABEL], [f"Device test {status}. {meaning}"])
        for row in self.check_rows:
            self.checks_group.remove(row)
        self.check_rows = []
        for check in report["checks"]:
            style, icon, _ = STATUS.get(check["status"], STATUS["WARN"])
            row = Adw.ActionRow(title=CHECKS.get(check["name"], check["name"]), subtitle=safe(check["detail"]),
                                use_markup=False, subtitle_lines=0)
            image = Gtk.Image(icon_name=icon)
            image.add_css_class(style)
            badge = Gtk.Label(label=check["status"], valign=Gtk.Align.CENTER)
            badge.add_css_class(style)
            badge.add_css_class("caption-heading")
            row.add_prefix(image)
            row.add_suffix(badge)
            self.checks_group.add(row)
            self.check_rows.append(row)
        self.show_saved(write_failed)

    def show_saved(self, write_failed=False):
        if self.saved_to:
            self.report_row.set_subtitle(f"Saved to {self.saved_to}")
            self.save_button.set_label("Save a Copy…")
            self.save_button.remove_css_class("suggested-action")
        else:
            self.report_row.set_subtitle("Writing the configured report file failed; save it now."
                                         if write_failed else "Not saved yet: no report file was configured.")
            self.save_button.set_label("Save Report…")
            self.save_button.add_css_class("suggested-action")

    def save_report(self, _button):
        dialog = Gtk.FileDialog(title="Save Test Report", initial_name="m9n-report.json", modal=True)
        dialog.save(self.get_root(), None, self.finish_save)

    def finish_save(self, dialog, result):
        try:
            path = dialog.save_finish(result).get_path()
        except GLib.Error:
            return
        self.write_report(path)

    def write_report(self, path):
        window = self.get_root()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json_line(self.report) + "\n")
        except OSError as exc:
            window.alert("Report Not Saved", str(exc))
            return False
        self.saved_to = path
        self.show_saved()
        window.toast("Report saved")
        return True
