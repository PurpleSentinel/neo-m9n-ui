"""Live dashboard: fix status, position, motion, timing, identity, stream health and issues."""
from datetime import datetime, timezone

from gi.repository import Adw, Gio, GLib, Gtk

from .. import coords, sun
from ..serialize import json_line, safe
from ..trip import distance_m
from . import format as fmt
from .pages import ColumnPage, SessionPage, copy_button, copy_text, value_row

# Fix state -> (style class, icon). The title text always accompanies the colour.
SUN_MOVE_M = 10000  # Sun times are recalculated when the position moves this far, or each minute.
FIX_STYLE = {"valid": ("success", "object-select-symbolic"), "stale": ("warning", "dialog-warning-symbolic"),
             "none": ("error", "dialog-error-symbolic"), "waiting": ("dim-label", "content-loading-symbolic")}


def coordinate_format_menu():
    """The main menu's Coordinate Format section (the win.coordinate-format action)."""
    section = Gio.Menu()
    for name, (label, _) in fmt.COORDINATE_FORMATS.items():
        section.append(label, f"win.coordinate-format::{name}")
    return section


def coordinate_format_button():
    """The Position group's format chooser, with an example under each format."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12,
                  margin_start=12, margin_end=12)
    title = Gtk.Label(label="Coordinate Format", xalign=0, margin_bottom=4)
    title.add_css_class("heading")
    box.append(title)
    for name, (label, _) in fmt.COORDINATE_FORMATS.items():
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=4)
        text.append(Gtk.Label(label=label, xalign=0))
        example = Gtk.Label(label=fmt.coordinate_example(name), xalign=0)
        for style in ("caption", "dim-label", "numeric"):
            example.add_css_class(style)
        text.append(example)
        check = Gtk.CheckButton(child=text, action_name="win.coordinate-format",
                                action_target=GLib.Variant("s", name))
        check.update_property([Gtk.AccessibleProperty.LABEL], [label])
        box.append(check)
    button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Coordinate format",
                            valign=Gtk.Align.CENTER, popover=Gtk.Popover(child=box))
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.LABEL], ["Coordinate format"])
    return button


class DashboardPage(SessionPage):
    coordinate_format = "decimal"

    def build(self):
        page = self.columns = ColumnPage()

        # Fix status: icon and text together, never colour alone.
        self.fix_icon = Gtk.Image(pixel_size=48)
        self.fix_title = Gtk.Label(xalign=0)
        self.fix_title.add_css_class("title-1")
        self.fix_detail = Gtk.Label(xalign=0, wrap=True)
        self.fix_detail.add_css_class("dim-label")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        text.append(self.fix_title)
        text.append(self.fix_detail)
        hero = Gtk.Box(spacing=18, margin_top=6, margin_bottom=6)
        hero.append(self.fix_icon)
        hero.append(text)
        self.hero = hero
        status = Adw.PreferencesGroup(valign=Gtk.Align.CENTER)
        status.add(hero)

        self.issues_group = Adw.PreferencesGroup(title="Active Issues")
        self.issue_rows = []

        self.rows = {}

        def group_of(title, rows, suffix=None):
            group = Adw.PreferencesGroup(title=title)
            if suffix:
                box = Gtk.Box(spacing=2)
                for widget in suffix if isinstance(suffix, list) else [suffix]:
                    box.append(widget)
                group.set_header_suffix(box)
            for key, label in rows:
                self.rows[key] = value_row(label, wrap=key in ("extensions", "source", "timeouts", "utc", "updated"))
                group.add(self.rows[key])
            return group

        page.add_band([status], [self.issues_group])
        # Navigation first, then receiver identity, session and stream health.
        page.add_flow(
            [group_of("Position", [("latitude", "Latitude"), ("longitude", "Longitude"),
                                   ("msl", "Altitude (mean sea level)"), ("ellipsoid", "Altitude (ellipsoid)"),
                                   ("hacc", "Horizontal accuracy"), ("vacc", "Vertical accuracy")],
                      [coordinate_format_button(), copy_button("Copy coordinates", self.copy_coordinates)]),
             group_of("Motion", [("speed", "Speed"), ("course", "Course over ground")]),
             group_of("Dilution of Precision", [("pdop", "Position (PDOP)"), ("hdop", "Horizontal (HDOP)"),
                                               ("vdop", "Vertical (VDOP)")]),
             group_of("Time", [("utc", "GNSS UTC"), ("tacc", "Time accuracy"), ("message", "Navigation message"),
                               ("age", "Sample age"), ("progress", "Epoch progress age")]),
             self.build_sun(group_of),
             group_of("Receiver Identity", [("software", "Firmware"), ("hardware", "Hardware"),
                                            ("extensions", "Extensions")]),
             group_of("Session", [("source", "Source"), ("elapsed", "Elapsed"), ("updated", "Updated (host UTC)")],
                      copy_button("Copy snapshot as JSON", self.copy_snapshot)),
             group_of("Stream", [("bytes", "Bytes received"), ("ubx", "UBX frames"), ("nmea", "NMEA frames"),
                                 ("checksum", "Checksum errors"), ("truncated", "Truncated frames"),
                                 ("oversize", "Oversized frames"), ("decode", "Undecodable payloads"),
                                 ("timeouts", "Unanswered polls"), ("rejected", "Rejected polls")])])
        return page

    def show(self, snapshot):
        nav = snapshot["navigation"] or {}
        state, title = fmt.fix_state(snapshot["navigation"])
        style, icon = FIX_STYLE[state]
        for name in ("success", "warning", "error", "dim-label"):
            self.fix_icon.remove_css_class(name)
            self.fix_title.remove_css_class(name)
        self.fix_icon.set_from_icon_name(icon)
        self.fix_icon.add_css_class(style)
        self.fix_title.add_css_class(style)
        self.fix_title.set_label(title)
        used = nav.get("satellites_used")
        self.fix_detail.set_label(f"{nav.get('fix_type', fmt.UNKNOWN)} · "
                                  f"{fmt.UNKNOWN if used is None else used} satellites used")
        self.hero.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}, {self.fix_detail.get_label()}"])

        counters = snapshot["counters"]
        identity = snapshot["identity"] or {}
        timeouts = sum(snapshot["poll_timeouts"].values())
        values = {
            "msl": fmt.number(nav.get("altitude_msl_m"), 2, "m"),
            "ellipsoid": fmt.number(nav.get("altitude_ellipsoid_m"), 2, "m"),
            "hacc": fmt.number(nav.get("horizontal_accuracy_m"), 2, "m"),
            "vacc": fmt.number(nav.get("vertical_accuracy_m"), 2, "m"),
            "speed": fmt.speed(nav.get("speed_m_s")),
            "course": fmt.number(nav.get("heading_deg"), 1, "°"),
            "pdop": fmt.number(nav.get("pdop")), "hdop": fmt.number(nav.get("hdop")),
            "vdop": fmt.number(nav.get("vdop")),
            "utc": fmt.utc(nav),
            "tacc": fmt.UNKNOWN if nav.get("time_accuracy_ns") is None else f"{nav['time_accuracy_ns']:,} ns",
            "message": nav.get("source_message", fmt.UNKNOWN),
            "age": fmt.seconds(nav.get("age_s")), "progress": fmt.seconds(nav.get("epoch_progress_age_s")),
            "software": identity.get("software", fmt.UNKNOWN), "hardware": identity.get("hardware", fmt.UNKNOWN),
            "extensions": "\n".join(identity.get("extensions", [])) or fmt.UNKNOWN,
            "source": snapshot["source"], "elapsed": fmt.seconds(snapshot["elapsed_s"]),
            "updated": snapshot["host_utc"],
            "bytes": fmt.integer(counters.get("bytes", 0)), "ubx": fmt.integer(counters.get("ubx_frames", 0)),
            "nmea": fmt.integer(counters.get("nmea_frames", 0)),
            "checksum": fmt.integer(counters.get("checksum_errors", 0)),
            "truncated": fmt.integer(counters.get("truncated_frames", 0)),
            "oversize": fmt.integer(counters.get("oversize_frame", 0)),
            "decode": fmt.integer(snapshot["decode_errors"]),
            "timeouts": f"{timeouts:,}" + (f" ({', '.join(sorted(snapshot['poll_timeouts']))})" if timeouts else ""),
            "rejected": ", ".join(snapshot["rejected_polls"]) or "none",
            **self.sun_values(nav),
        }
        for key, text in values.items():
            # Receiver strings are shown as plain text; safe() removes control characters.
            label = self.rows[key].value
            text = safe(text) if key != "extensions" else "\n".join(safe(line) for line in text.split("\n"))
            if label.get_label() != text:
                label.set_label(text)
        for key in ("checksum", "truncated", "oversize", "decode"):
            self.rows[key].value.remove_css_class("warning")
        for key, counter in (("checksum", counters.get("checksum_errors")),
                             ("truncated", counters.get("truncated_frames")),
                             ("oversize", counters.get("oversize_frame")), ("decode", snapshot["decode_errors"])):
            if counter:
                self.rows[key].value.add_css_class("warning")
        self.show_position(nav)
        self.show_issues(snapshot["issues"])

    def set_coordinate_format(self, name):
        self.coordinate_format = name
        if self.snapshot:
            self.show_position(self.snapshot["navigation"] or {})

    def show_position(self, nav):
        """Latitude and longitude, or a grid reference, in the chosen format; unused rows are hidden."""
        shown = fmt.position_rows(nav, self.coordinate_format)
        for index, key in enumerate(("latitude", "longitude")):
            row = self.rows[key]
            row.set_visible(index < len(shown))
            if index < len(shown):
                title, text = shown[index]
                row.set_title(title)
                if row.value.get_label() != text:
                    row.value.set_label(text)
        approximate = self.coordinate_format == "bng" and shown[0][1] not in (fmt.UNKNOWN, coords.OUTSIDE_BNG)
        self.rows["latitude"].set_subtitle(fmt.BNG_NOTE if approximate else "")

    def build_sun(self, group_of):
        self.sun_key = None
        self.sun_group = group_of("Sun", [("sunrise", "Sunrise"), ("sunset", "Sunset"), ("daylength", "Day length"),
                                          ("twilight", "Civil twilight"), ("elevation", "Sun elevation now")])
        return self.sun_group

    def sun_values(self, nav):
        """Sun rows for the fix's position and GNSS time (the computer's clock if the receiver has no date)."""
        lat, lon = nav.get("latitude"), nav.get("longitude")
        if lat is None or lon is None:
            self.sun_key = None
            self.sun_group.set_description(None)
            return dict.fromkeys(("sunrise", "sunset", "daylength", "twilight", "elevation"), "Needs a position fix")
        when = sun.gnss_datetime(nav)
        clock = "GNSS time" if when else "the computer's clock"
        when = when or datetime.now(timezone.utc)
        minute = when.replace(second=0, microsecond=0)
        if self.sun_key and self.sun_key[0] == minute and distance_m(lat, lon, *self.sun_key[1]) < SUN_MOVE_M:
            return self.sun_key[2]
        times = sun.day(when, lat, lon)
        height = sun.elevation(when, lat, lon)
        dawn, dusk = times["dawn"], times["dusk"]
        values = {"sunrise": fmt.sun_event(times["sunrise"], times["polar"]),
                  "sunset": fmt.sun_event(times["sunset"], times["polar"]),
                  "daylength": fmt.day_length(times["day_length"]),
                  "twilight": (f"{fmt.local_time(dawn)} – {fmt.local_time(dusk)}" if dawn and dusk
                               else fmt.sun_event(dawn or dusk, times["polar"])),
                  "elevation": fmt.sun_elevation(height, sun.phase(height))}
        self.sun_group.set_description(f"For the receiver's position, using {clock}. "
                                       f"Times in this computer's time zone.")
        self.sun_key = (minute, (lat, lon), values)
        return values

    def show_issues(self, issues):
        wanted = issues or ["No active issues"]
        if [row.get_title() for row in self.issue_rows] == wanted:
            return
        for row in self.issue_rows:
            self.issues_group.remove(row)
        self.issue_rows = []
        for text in wanted:
            row = Adw.ActionRow(title=safe(text), use_markup=False, title_lines=0)
            icon = Gtk.Image(icon_name="dialog-warning-symbolic" if issues else "object-select-symbolic")
            icon.add_css_class("warning" if issues else "success")
            row.add_prefix(icon)
            self.issues_group.add(row)
            self.issue_rows.append(row)

    def copy_coordinates(self, _button):
        text = fmt.coordinates_text(self.snapshot and self.snapshot["navigation"], self.coordinate_format)
        copy_text(self, text, "Coordinates copied" if text else "No position to copy")

    def copy_snapshot(self, _button):
        copy_text(self, self.snapshot and json_line(self.snapshot), "Snapshot JSON copied")
