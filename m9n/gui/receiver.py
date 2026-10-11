"""Receiver diagnostics: MON-RF blocks, NAV-STATUS, diagnostic freshness and poll health.

Warning styling uses the same predicates as State.issues(), never its own thresholds.
"""
from gi.repository import Adw, Gtk

from ..state import (JAMMING, RF_FRESH_S, SPOOFING, antenna_alarm, jamming_alarm, rf_monitor_known,
                     spoofing_alarm)
from . import format as fmt
from .pages import SessionPage, value_row

AGC_MAX = 8191  # MON-RF agcCnt range.
WIRING_NOTE = ("Antenna status depends on how this board wires the antenna supervisor. Values are "
               "indications, not proof of an antenna fault or of interference.")


def mark(row, alarm):
    (row.value.add_css_class if alarm else row.value.remove_css_class)("warning")
    icon = getattr(row, "alarm_icon", None)
    if icon is None:
        icon = row.alarm_icon = Gtk.Image(icon_name="dialog-warning-symbolic", visible=False)
        icon.add_css_class("warning")
        icon.update_property([Gtk.AccessibleProperty.LABEL], ["Warning"])
        row.add_prefix(icon)
    icon.set_visible(alarm)


class ReceiverPage(SessionPage):
    def build(self):
        self.prefs = Adw.PreferencesPage()
        self.rf_group = Adw.PreferencesGroup(title="RF and Antenna (MON-RF)", description=WIRING_NOTE)
        self.prefs.add(self.rf_group)
        self.rf_rows = []
        self.rf_note = Adw.ActionRow(use_markup=False, title_lines=0)
        self.rf_group.add(self.rf_note)

        self.rows = {}

        def group(title, rows, description=None):
            g = Adw.PreferencesGroup(title=title, description=description)
            for key, label in rows:
                self.rows[key] = value_row(label)
                g.add(self.rows[key])
            self.prefs.add(g)

        group("Receiver Status (NAV-STATUS)", [("ttff", "Time to first fix"), ("uptime", "Receiver uptime"),
                                               ("spoof", "Spoofing detector")],
              "The spoofing detector is an indication only; verify location with an independent source.")
        group("Diagnostic Freshness", [("MON-VER", "Identity (MON-VER)"), ("NAV-PVT", "Navigation (NAV-PVT)"),
                                       ("NAV-SAT", "Satellites (NAV-SAT)"), ("NAV-STATUS", "Status (NAV-STATUS)"),
                                       ("MON-RF", "RF (MON-RF)")],
              "Time since each message was last received. Missing data is not evidence of a hardware fault.")
        group("Polling", [("timeouts", "Unanswered polls"), ("rejected", "Rejected polls"),
                          ("mode", "Mode")])
        return self.prefs

    def session_started(self, config):
        super().session_started(config)
        self.rows["mode"].value.set_label("passive: no polls sent" if config.passive or config.source != "device"
                                          else "read-only diagnostic polls")

    def show(self, snapshot):
        self.show_rf(snapshot)
        status = snapshot["receiver_status"]
        if status:
            spoof = status["spoof_state"]
            self.set("ttff", fmt.seconds(status["ttff_s"]) if status["ttff_s"] else "no fix yet")
            self.set("uptime", fmt.seconds(status["uptime_s"]))
            self.set("spoof", f"{SPOOFING.get(spoof, spoof)} (state {spoof})")
            mark(self.rows["spoof"], spoofing_alarm(spoof))
        else:
            for key in ("ttff", "uptime", "spoof"):
                self.set(key, "unavailable")
        ages = snapshot["message_ages_s"]
        for name in ("MON-VER", "NAV-PVT", "NAV-SAT", "NAV-STATUS", "MON-RF"):
            self.set(name, fmt.seconds(ages[name]) + " ago" if name in ages else "never received")
        timeouts = snapshot["poll_timeouts"]
        self.set("timeouts", ", ".join(f"{name} ×{count}" for name, count in sorted(timeouts.items())) or "none")
        self.set("rejected", ", ".join(snapshot["rejected_polls"]) or "none")
        mark(self.rows["timeouts"], bool(timeouts))
        mark(self.rows["rejected"], bool(snapshot["rejected_polls"]))

    def show_rf(self, snapshot):
        blocks, age = snapshot["rf"], snapshot["rf_age_s"]
        fresh = age is not None and age <= RF_FRESH_S
        if not blocks:
            note = "MON-RF unavailable: not answered or rejected. RF health is unknown, not healthy."
        elif not fresh:
            note = f"MON-RF is stale ({fmt.seconds(age)} old); values below are the last received."
        elif not rf_monitor_known(blocks):
            note = ("The jamming monitor reports state 0 (unknown or disabled), so a device test "
                    "reports RF health as WARN rather than PASS.")
        else:
            note = f"Received {fmt.seconds(age)} ago."
        self.rf_note.set_title(note)
        if len(self.rf_rows) != len(blocks):
            for rows in self.rf_rows:
                for row in rows.values():
                    self.rf_group.remove(row)
            self.rf_rows = []
            for block in blocks:
                rows = {}
                for key, label in (("antenna", "Antenna status"), ("power", "Antenna power"),
                                   ("jamming", "Jamming state"), ("cw", "CW interference suppression"),
                                   ("noise", "Noise level"), ("agc", "AGC monitor"), ("post", "POST status")):
                    rows[key] = value_row(f"Block {block['block']}: {label}" if len(blocks) > 1 else label)
                    self.rf_group.add(rows[key])
                self.rf_rows.append(rows)
        for rows, block in zip(self.rf_rows, blocks):
            jam = block["jamming_state"]
            values = {"antenna": block["antenna_status"], "power": block["antenna_power"],
                      "jamming": f"{JAMMING.get(jam, jam)} (state {jam})",
                      "cw": f"{block['cw_suppression']} / 255", "noise": f"{block['noise_per_ms']} per ms",
                      "agc": f"{block['agc_count']} ({block['agc_count'] / AGC_MAX:.0%})",
                      "post": f"0x{block['post_status']:08X}"}
            for key, text in values.items():
                if rows[key].value.get_label() != text:
                    rows[key].value.set_label(text)
            mark(rows["antenna"], fresh and antenna_alarm(block))
            mark(rows["jamming"], fresh and jamming_alarm(block))

    def set(self, key, text):
        label = self.rows[key].value
        if label.get_label() != text:
            label.set_label(text)
