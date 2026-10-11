"""Vehicle page: large, colourful instruments readable at a glance on the move.

A speed gauge, a compass, trip tiles and an altitude profile, all drawn with cairo so
they scale with the page. Trip figures come from the session core (snapshot["trip"]),
which counts every navigation epoch; the altitude profile is sampled from snapshots.
Without a valid, fresh fix the instruments dim and say why: a stale value is never
shown as live.
"""
import math

import cairo
from gi.repository import Adw, Gtk, Pango, PangoCairo

from . import format as fmt
from .pages import SessionPage

HEADING_MIN_M_S = 1.0  # GNSS course over ground is noise below about walking pace.
PROFILE_POINTS = 1800  # Altitude samples kept; older halves are thinned when full.
PORTRAIT = "max-aspect-ratio: 1/1"
COMPACT = "max-width: 520sp"
LARGE = "min-width: 1200sp and min-height: 760sp"
TILE_HEIGHT, LARGE_TILE_HEIGHT = 96, 150
DIM = 0.3  # Opacity of an instrument without a live fix.

# Colour stops from the GNOME palette; they read on both the light and dark styles.
SPEED_STOPS = ((0, (0.20, 0.82, 0.48)), (0.5, (0.96, 0.83, 0.18)), (0.75, (1.0, 0.47, 0.0)),
               (1, (0.88, 0.11, 0.14)))
RING_STOPS = ((0, (0.21, 0.52, 0.89)), (0.33, (0.57, 0.25, 0.67)), (0.66, (0.15, 0.64, 0.65)),
              (1, (0.21, 0.52, 0.89)))
POINTER = ((1.0, 0.75, 0.44), (1.0, 0.47, 0.0))
NORTH = (0.88, 0.11, 0.14)
TILE_COLOURS = {"distance": ((0.11, 0.44, 0.85), (0.15, 0.64, 0.65)),
                "average": ((0.51, 0.24, 0.61), (0.75, 0.38, 0.80)),
                "max": ((1.0, 0.47, 0.0), (0.88, 0.11, 0.14)),
                "moving": ((0.13, 0.56, 0.64), (0.20, 0.82, 0.48))}
ALTITUDE_FILL = ((0.18, 0.76, 0.49), (0.11, 0.44, 0.85))
STATUS = {"valid": ("Live", "success"), "none": ("No Fix", "error"), "stale": ("Stale", "warning"),
          "waiting": ("Waiting", "dim-label")}
SEGMENTS = 96  # Arc pieces per full gradient sweep; enough for a smooth blend.


def blend(stops, x):
    """Colour at x (0..1) between the gradient stops."""
    x = min(1.0, max(0.0, x))
    for (p1, c1), (p2, c2) in zip(stops, stops[1:]):
        if x <= p2:
            f = (x - p1) / (p2 - p1) if p2 > p1 else 0
            return tuple(a + (b - a) * f for a, b in zip(c1, c2))
    return stops[-1][1]


def gradient_arc(cr, cx, cy, radius, width, start, sweep, stops, alpha=1.0, offset=0.0, span=None):
    """Stroke an arc whose colour follows the stops; offset/span place it within a longer gradient."""
    span = span or sweep
    pieces = max(1, int(SEGMENTS * abs(sweep) / (2 * math.pi)) + 1)
    # Draw opaque (pieces overlap slightly to hide seams), then fade the whole arc at once.
    cr.push_group()
    cr.set_line_width(width)
    cr.set_line_cap(0)  # Butt: the pieces join without overlaps darkening the gradient.
    for i in range(pieces):
        a1 = start + sweep * i / pieces
        a2 = start + sweep * (i + 1) / pieces + (0.01 if i < pieces - 1 else 0)
        cr.set_source_rgb(*blend(stops, offset + (sweep * (i + 0.5) / pieces) / span))
        cr.new_sub_path()
        cr.arc(cx, cy, radius, a1, a2)
        cr.stroke()
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)


def draw_text(widget, cr, string, x, y, size, weight=Pango.Weight.BOLD, rgba=None, align=0.5, valign=0.5,
              numeric=True, max_width=None):
    """Draw text centred (by default) on x, y at a pixel size; shrinks to fit max_width. Returns its size."""
    layout = widget.create_pango_layout(string)
    font = widget.get_pango_context().get_font_description().copy()
    font.set_weight(weight)
    font.set_absolute_size(max(1.0, size) * Pango.SCALE)
    layout.set_font_description(font)
    if numeric:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attrs)
    w, h = layout.get_pixel_size()
    if max_width and w > max_width > 0:
        font.set_absolute_size(max(1.0, size * max_width / w) * Pango.SCALE)
        layout.set_font_description(font)
        w, h = layout.get_pixel_size()
    if rgba is not None:
        cr.set_source_rgba(*rgba)
    cr.move_to(x - w * align, y - h * valign)
    PangoCairo.show_layout(cr, layout)
    cr.new_path()
    return w, h


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Instrument(Gtk.DrawingArea):
    """A drawn instrument that fills whatever space the page gives it."""

    def __init__(self, label, minimum=120):
        super().__init__(hexpand=True, vexpand=True, content_width=minimum, content_height=minimum)
        self.status = "Waiting"  # None when the data is live.
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def fg(self, alpha=1.0):
        c = self.get_color()
        return c.red, c.green, c.blue, alpha

    def _draw(self, _area, cr, width, height):
        if self.status:
            cr.push_group()
            self.draw(cr, width, height)
            cr.pop_group_to_source()
            cr.paint_with_alpha(DIM)
            self.draw_status(cr, width, height)
        else:
            self.draw(cr, width, height)

    def draw_status(self, cr, width, height):
        draw_text(self, cr, self.status.upper(), width / 2, height / 2, min(width, height) * 0.11,
                  weight=Pango.Weight.HEAVY, rgba=self.fg(0.9), numeric=False, max_width=width * 0.8)

    def describe(self, text):
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])
        self.queue_draw()


class SpeedGauge(Instrument):
    """270° gauge: a green-to-red gradient track filled up to the current speed."""

    START, SWEEP = math.radians(135), math.radians(270)

    def __init__(self):
        super().__init__("Speed", minimum=160)
        self.value = None
        self.maximum = 60
        self.unit = "km/h"

    def show(self, value, maximum, unit, status):
        self.value, self.maximum, self.unit, self.status = value, maximum, unit, status
        self.describe(f"Speed {'unknown' if value is None else f'{value:.0f}'} {unit}"
                      + (f", {status}" if status else ""))

    def draw(self, cr, width, height):
        # The arc's lowest points sit at 45° below centre, so it is 1.71 radii tall.
        radius = max(10.0, min(width / 2 - 6, (height - 12) / 1.78))
        thick = radius * 0.15
        radius -= thick / 2
        cx, cy = width / 2, (height - 1.71 * (radius + thick / 2)) / 2 + radius + thick / 2
        gradient_arc(cr, cx, cy, radius, thick, self.START, self.SWEEP, SPEED_STOPS, alpha=0.16)
        fraction = 0 if self.value is None else min(1.0, max(0.0, self.value / self.maximum))
        if fraction > 0:
            gradient_arc(cr, cx, cy, radius, thick, self.START, self.SWEEP * fraction, SPEED_STOPS,
                         span=self.SWEEP)
            # Round the start of the filled band.
            cr.set_source_rgb(*blend(SPEED_STOPS, 0))
            cr.arc(cx + radius * math.cos(self.START), cy + radius * math.sin(self.START), thick / 2, 0, 2 * math.pi)
            cr.fill()
            end = self.START + self.SWEEP * fraction
            ex, ey = cx + radius * math.cos(end), cy + radius * math.sin(end)
            cr.set_source_rgb(*blend(SPEED_STOPS, fraction))
            cr.arc(ex, ey, thick * 0.62, 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(max(1.5, thick * 0.14))
            cr.stroke()
        # Ticks and scale labels inside the track.
        majors = 6
        for i in range(majors * 2 + 1):
            angle = self.START + self.SWEEP * i / (majors * 2)
            major = i % 2 == 0
            inner = radius - thick * (1.35 if major else 1.0)
            outer = radius - thick * 0.62
            cr.set_source_rgba(*self.fg(0.75 if major else 0.4))
            cr.set_line_width(max(1.0, thick * (0.16 if major else 0.09)))
            cr.set_line_cap(1)
            cr.move_to(cx + inner * math.cos(angle), cy + inner * math.sin(angle))
            cr.line_to(cx + outer * math.cos(angle), cy + outer * math.sin(angle))
            cr.stroke()
            if major:
                at = radius - thick * 2.3
                draw_text(self, cr, f"{self.maximum * i / (majors * 2):.0f}", cx + at * math.cos(angle),
                          cy + at * math.sin(angle), radius * 0.085, weight=Pango.Weight.SEMIBOLD, rgba=self.fg(0.7))
        text = "–" if self.value is None else f"{self.value:.0f}"
        draw_text(self, cr, text, cx, cy - radius * 0.02, radius * 0.48, weight=Pango.Weight.HEAVY,
                  rgba=self.fg(), max_width=radius * 1.2)
        draw_text(self, cr, self.unit, cx, cy + radius * 0.36, radius * 0.12, weight=Pango.Weight.SEMIBOLD,
                  rgba=self.fg(0.65), numeric=False)


class Compass(Instrument):
    """North-up compass rose; a chevron on the gradient ring marks the course over ground."""

    def __init__(self):
        super().__init__("Heading")
        self.heading = None
        self.hint = None

    def show(self, heading, status, hint=None):
        self.heading, self.status, self.hint = heading, status, hint
        self.describe("Heading " + (f"{fmt.cardinal(heading)} {heading:.0f} degrees" if heading is not None
                                    else hint or "unknown") + (f", {status}" if status else ""))

    def draw(self, cr, width, height):
        radius = max(10.0, min(width, height) / 2 - 6)
        thick = radius * 0.13
        ring = radius - thick / 2
        cx, cy = width / 2, height / 2
        # Angles here are compass bearings: 0 = north (up), clockwise.
        to_cairo = lambda bearing: math.radians(bearing - 90)  # noqa: E731
        gradient_arc(cr, cx, cy, ring, thick, to_cairo(0), 2 * math.pi, RING_STOPS, alpha=0.9)
        for bearing in range(0, 360, 10):
            major = bearing % 30 == 0
            a = to_cairo(bearing)
            inner = ring - thick * (1.2 if major else 0.85)
            cr.set_source_rgba(*self.fg(0.7 if major else 0.35))
            cr.set_line_width(max(1.0, radius * (0.018 if major else 0.01)))
            cr.set_line_cap(1)
            cr.move_to(cx + inner * math.cos(a), cy + inner * math.sin(a))
            cr.line_to(cx + (ring - thick * 0.6) * math.cos(a), cy + (ring - thick * 0.6) * math.sin(a))
            cr.stroke()
        for bearing, label in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = to_cairo(bearing)
            at = ring - thick * 2.2
            draw_text(self, cr, label, cx + at * math.cos(a), cy + at * math.sin(a), radius * 0.15,
                      weight=Pango.Weight.HEAVY, rgba=(*NORTH, 1) if label == "N" else self.fg(0.8), numeric=False)
        if self.heading is None:
            draw_text(self, cr, "–", cx, cy - radius * 0.05, radius * 0.3, rgba=self.fg(0.6))
            if self.hint:
                draw_text(self, cr, self.hint, cx, cy + radius * 0.22, radius * 0.075, weight=Pango.Weight.NORMAL,
                          rgba=self.fg(0.6), numeric=False, max_width=radius * 1.1)
            return
        a = to_cairo(self.heading)
        # A faint course line from the centre, then the chevron riding on the ring.
        cr.set_source_rgba(*POINTER[1], 0.35)
        cr.set_line_width(max(1.5, radius * 0.025))
        cr.move_to(cx + radius * 0.42 * math.cos(a), cy + radius * 0.42 * math.sin(a))
        cr.line_to(cx + (ring - thick) * math.cos(a), cy + (ring - thick) * math.sin(a))
        cr.stroke()
        tip, base, half = ring + thick * 0.75, ring - thick * 1.6, math.radians(9)
        cr.move_to(cx + tip * math.cos(a), cy + tip * math.sin(a))
        cr.line_to(cx + base * math.cos(a - half), cy + base * math.sin(a - half))
        cr.line_to(cx + (base + thick * 0.55) * math.cos(a), cy + (base + thick * 0.55) * math.sin(a))
        cr.line_to(cx + base * math.cos(a + half), cy + base * math.sin(a + half))
        cr.close_path()
        gradient = cairo.LinearGradient(cx + base * math.cos(a), cy + base * math.sin(a),
                                        cx + tip * math.cos(a), cy + tip * math.sin(a))
        gradient.add_color_stop_rgb(0, *POINTER[0])
        gradient.add_color_stop_rgb(1, *POINTER[1])
        cr.set_source(gradient)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.0, radius * 0.012))
        cr.stroke()
        draw_text(self, cr, fmt.cardinal(self.heading), cx, cy - radius * 0.08, radius * 0.3,
                  weight=Pango.Weight.HEAVY, rgba=self.fg(), numeric=False)
        draw_text(self, cr, f"{self.heading:.0f}°", cx, cy + radius * 0.2, radius * 0.13,
                  weight=Pango.Weight.SEMIBOLD, rgba=self.fg(0.7))


class Tile(Instrument):
    """A gradient card with a title and a large value."""

    def __init__(self, title, colours):
        super().__init__(title, minimum=72)
        self.set_size_request(140, TILE_HEIGHT)
        self.set_vexpand(False)  # The gauges take the spare height.
        self.title, self.colours = title, colours
        self.value, self.unit = "–", ""
        self.status = None  # Trip figures stay valid without a fix.

    def show(self, value, unit=""):
        if (value, unit) != (self.value, self.unit):
            self.value, self.unit = value, unit
            self.describe(f"{self.title} {value} {unit}".strip())

    def draw(self, cr, width, height):
        rounded_rect(cr, 2, 2, width - 4, height - 4, min(width, height) * 0.14)
        gradient = cairo.LinearGradient(0, 0, width, height)
        gradient.add_color_stop_rgb(0, *self.colours[0])
        gradient.add_color_stop_rgb(1, *self.colours[1])
        cr.set_source(gradient)
        cr.fill()
        pad = min(width, height) * 0.14
        draw_text(self, cr, self.title.upper(), pad, pad, min(height * 0.15, width * 0.075),
                  weight=Pango.Weight.BOLD, rgba=(1, 1, 1, 0.85), align=0, valign=0, numeric=False,
                  max_width=width - 2 * pad)
        unit_w = 0
        size = min(height * 0.44, width * 0.3)
        if self.unit:
            unit_w, _ = draw_text(self, cr, self.unit, width - pad, height - pad, size * 0.42,
                                  weight=Pango.Weight.SEMIBOLD, rgba=(1, 1, 1, 0.85), align=1, valign=1,
                                  numeric=False)
        draw_text(self, cr, self.value, pad, height - pad * 0.7, size, weight=Pango.Weight.HEAVY,
                  rgba=(1, 1, 1, 1), align=0, valign=1, max_width=width - 2 * pad - unit_w - pad * 0.5)


class AltitudeChart(Instrument):
    """Current altitude over a gradient-filled profile of the trip."""

    def __init__(self):
        super().__init__("Altitude")
        self.points = []
        self.value, self.unit, self.scale = "–", "m", 1.0

    def show(self, points, value, unit, scale, status):
        self.points, self.value, self.unit, self.scale, self.status = points, value, unit, scale, status
        self.describe(f"Altitude {value} {unit}" + (f", {status}" if status else ""))

    def draw(self, cr, width, height):
        rounded_rect(cr, 2, 2, width - 4, height - 4, min(width, height) * 0.08)
        cr.set_source_rgba(*self.fg(0.06))
        cr.fill()
        pad = min(width, height) * 0.08
        title = min(height * 0.09, width * 0.06)
        draw_text(self, cr, "ALTITUDE (MSL)", pad, pad, title, rgba=self.fg(0.6), align=0, valign=0,
                  numeric=False, max_width=width - 2 * pad)
        w, _ = draw_text(self, cr, self.value, pad, pad + title * 1.4, min(height * 0.24, width * 0.16),
                         weight=Pango.Weight.HEAVY, rgba=self.fg(), align=0, valign=0)
        draw_text(self, cr, self.unit, pad + w + pad * 0.4, pad + title * 1.4 + min(height * 0.24, width * 0.16),
                  title * 1.3, weight=Pango.Weight.SEMIBOLD, rgba=self.fg(0.6), align=0, valign=1.25,
                  numeric=False)
        top, bottom, left, right = height * 0.5, height - pad, pad, width - pad
        if len(self.points) < 2 or bottom - top < 8:
            draw_text(self, cr, "Profile appears as the trip goes on", width / 2, (top + bottom) / 2,
                      title, weight=Pango.Weight.NORMAL, rgba=self.fg(0.5), numeric=False,
                      max_width=width - 2 * pad)
            return
        t0, t1 = self.points[0][0], self.points[-1][0]
        altitudes = [a for _, a in self.points]
        low, high = min(altitudes), max(altitudes)
        if high - low < 10:  # Keep a flat road looking flat.
            mid = (high + low) / 2
            low, high = mid - 5, mid + 5
        span_t = (t1 - t0) or 1

        def xy(t, a):
            return left + (right - left) * (t - t0) / span_t, bottom - (bottom - top) * (a - low) / (high - low)

        cr.move_to(*xy(*self.points[0]))
        for point in self.points[1:]:
            cr.line_to(*xy(*point))
        line = cr.copy_path()
        cr.line_to(right, bottom)
        cr.line_to(left, bottom)
        cr.close_path()
        fill = cairo.LinearGradient(0, top, 0, bottom)
        fill.add_color_stop_rgba(0, *ALTITUDE_FILL[0], 0.85)
        fill.add_color_stop_rgba(1, *ALTITUDE_FILL[1], 0.12)
        cr.set_source(fill)
        cr.fill()
        cr.new_path()
        cr.append_path(line)
        stroke = cairo.LinearGradient(left, 0, right, 0)
        stroke.add_color_stop_rgb(0, *ALTITUDE_FILL[1])
        stroke.add_color_stop_rgb(1, *ALTITUDE_FILL[0])
        cr.set_source(stroke)
        cr.set_line_width(max(2.0, height * 0.014))
        cr.set_line_join(1)
        cr.stroke()
        for value, y in ((high, top), (low, bottom)):
            draw_text(self, cr, f"{value * self.scale:,.0f}", right, y, title * 0.85, weight=Pango.Weight.SEMIBOLD,
                      rgba=self.fg(0.55), align=1, valign=1.1)


class VehiclePage(SessionPage):
    def __init__(self, page):
        self.units = "metric"
        self.profile = []  # (elapsed s, altitude MSL m)
        self._profile_epoch = None
        self.on_units_changed = None  # Called with "metric" or "imperial" when the user switches.
        super().__init__(page)

    def build(self):
        self.status = Gtk.Label(valign=Gtk.Align.CENTER)
        self.status.add_css_class("heading")
        self.metric = Gtk.ToggleButton(label="km/h", active=True, tooltip_text="Metric units")
        self.imperial = Gtk.ToggleButton(label="mph", group=self.metric, tooltip_text="Imperial units")
        units = Gtk.Box(valign=Gtk.Align.CENTER)
        units.add_css_class("linked")
        for button in (self.metric, self.imperial):
            button.set_size_request(56, -1)
            units.append(button)
        self.metric.connect("toggled", self.on_units_toggled)
        reset_content = Adw.ButtonContent(icon_name="edit-undo-symbolic", label="Reset Trip")
        reset = Gtk.Button(child=reset_content, valign=Gtk.Align.CENTER, tooltip_text="Start distance and speeds again")
        reset.update_property([Gtk.AccessibleProperty.LABEL], ["Reset trip"])
        reset.connect("clicked", self.reset_trip)
        self.fullscreen_button = Gtk.Button(icon_name="view-fullscreen-symbolic", action_name="win.fullscreen",
                                            valign=Gtk.Align.CENTER, tooltip_text="Fullscreen (F11)")
        self.fullscreen_button.update_property([Gtk.AccessibleProperty.LABEL], ["Toggle fullscreen"])
        toolbar = Gtk.Box(spacing=8)
        toolbar.append(self.status)
        toolbar.append(Gtk.Box(hexpand=True))
        for widget in (units, reset, self.fullscreen_button):
            toolbar.append(widget)

        self.speed = SpeedGauge()
        self.compass = Compass()
        self.altitude = AltitudeChart()
        self.tiles = {key: Tile(title, TILE_COLOURS[key]) for key, title in
                      (("distance", "Distance"), ("average", "Average speed"), ("max", "Max speed"),
                       ("moving", "Moving time"))}

        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, homogeneous=True)
        self.side.append(self.compass)
        self.side.append(self.altitude)
        self.main = Gtk.Box(spacing=12, homogeneous=True, vexpand=True)
        self.main.append(self.speed)
        self.main.append(self.side)
        self.tile_box = Gtk.FlowBox(homogeneous=True, vexpand=False, min_children_per_line=2, max_children_per_line=4,
                                    selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12)
        for tile in self.tiles.values():
            self.tile_box.append(tile)
            tile.get_parent().set_focusable(False)  # Tiles are read-only; keep Tab for the controls.

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
        content.append(toolbar)
        content.append(self.main)
        content.append(self.tile_box)
        scrolled = Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.adaptive = Adw.BreakpointBin(child=scrolled, width_request=280, height_request=200)
        # Large screens grow the tiles with the gauges. Added first: when several breakpoints
        # match, the last one added applies, so portrait and phone layouts win over this.
        large = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(LARGE))
        for tile in self.tiles.values():
            large.add_setter(tile, "height-request", LARGE_TILE_HEIGHT)
        self.adaptive.add_breakpoint(large)
        # Portrait (a phone or a dashboard-mounted tablet): speed on top, compass beside altitude.
        for condition in (PORTRAIT, COMPACT):
            breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(condition))
            breakpoint.add_setter(self.main, "orientation", Gtk.Orientation.VERTICAL)
            breakpoint.add_setter(self.side, "orientation", Gtk.Orientation.HORIZONTAL)
            breakpoint.add_setter(self.main, "homogeneous", False)
            if condition == COMPACT:  # Phone width: the reset button keeps only its icon.
                breakpoint.add_setter(reset_content, "label", "")
            self.adaptive.add_breakpoint(breakpoint)
        return self.adaptive

    # Session events ------------------------------------------------------

    def session_started(self, config):
        super().session_started(config)
        self.clear_profile()

    def clear_profile(self):
        self.profile = []
        self._profile_epoch = None

    def show(self, snapshot):
        nav = snapshot["navigation"] or {}
        state, _ = fmt.fix_state(snapshot["navigation"])
        text, style = STATUS[state]
        for name in ("success", "error", "warning", "dim-label"):
            self.status.remove_css_class(name)
        self.status.add_css_class(style)
        self.status.set_label(text)
        live = state == "valid"
        dimmed = None if live else text
        trip = snapshot.get("trip") or {}
        unit, _ = fmt.SPEED_UNIT[self.units]

        speed = fmt.speed_in(nav.get("speed_m_s"), self.units) if live else None
        peak = fmt.speed_in(trip.get("max_speed_m_s"), self.units)
        self.speed.show(speed, fmt.gauge_max(max(peak or 0, speed or 0), self.units), unit, dimmed)

        moving = live and (nav.get("speed_m_s") or 0) >= HEADING_MIN_M_S
        heading = nav.get("heading_deg") if moving else None
        self.compass.show(heading, dimmed, None if heading is not None else "Heading needs movement")

        altitude = nav.get("altitude_msl_m") if live else None
        epoch = nav.get("itow_ms", nav.get("epoch"))
        if altitude is not None and epoch != self._profile_epoch:
            self._profile_epoch = epoch
            self.profile.append((snapshot["elapsed_s"], altitude))
            if len(self.profile) > PROFILE_POINTS:
                self.profile = self.profile[::2]
        value, alt_unit = fmt.altitude(altitude, self.units)
        self.altitude.show(self.profile, "–" if altitude is None else value, alt_unit or
                           ("ft" if self.units == "imperial" else "m"),
                           fmt.FEET_PER_M if self.units == "imperial" else 1.0, dimmed)

        distance, distance_unit = fmt.trip_distance(trip.get("distance_m", 0.0), self.units)
        self.tiles["distance"].show(distance, distance_unit)
        for key, value_m_s in (("average", trip.get("average_speed_m_s")), ("max", trip.get("max_speed_m_s"))):
            shown = fmt.speed_in(value_m_s, self.units)
            self.tiles[key].show("–" if shown is None else f"{shown:.0f}", unit)
        self.tiles["moving"].show(fmt.duration(trip.get("moving_s", 0.0)))

    # Controls ------------------------------------------------------------

    def set_units(self, units):
        (self.imperial if units == "imperial" else self.metric).set_active(True)
        self.units = units

    def on_units_toggled(self, _button):
        units = "metric" if self.metric.get_active() else "imperial"
        if units == self.units:
            return
        self.units = units
        if self.snapshot:
            self.show(self.snapshot)
        if self.on_units_changed:
            self.on_units_changed(units)

    def reset_trip(self, _button=None):
        window = self.get_root()
        if hasattr(window, "reset_trip"):
            window.reset_trip()

    def trip_reset(self):
        self.clear_profile()

    def set_fullscreen(self, fullscreen):
        self.fullscreen_button.set_icon_name("view-restore-symbolic" if fullscreen else "view-fullscreen-symbolic")
