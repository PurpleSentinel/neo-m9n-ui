"""Map page: live position, accuracy and the trip's track, on a grid or an opt-in online map.

Modes: "off" (default) draws a coordinate grid locally and never uses the network;
"online" shows OpenStreetMap tiles after informed consent, only for the visible area and
only while this page is showing; "offline" draws a local offline map file (made with
scripts/make-offline-map.sh) and never uses the network. OpenStreetMap does not permit
offline use of its tiles, so the online cache is never shown offline.

libshumate provides the map widget (pan, zoom, scale, attribution). If it is missing the
page still draws the track on a grid fitted to it and explains how to install it.
"""
import math
import os
from pathlib import Path

import cairo
import gi
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..mbtiles import MapFile, MapFileError
from . import format as fmt
from .pages import SessionPage
from .appearance import night_button
from .vehicle import HEADING_MIN_M_S, POINTER, SPEED_STOPS, STATUS, blend, draw_text, export_button

try:
    if os.environ.get("M9N_NO_SHUMATE"):  # Tests: behave as if libshumate were not installed.
        raise ImportError("libshumate disabled for testing")
    gi.require_version("Shumate", "1.0")
    gi.require_version("Soup", "3.0")
    from gi.repository import Shumate
    from . import tiles
except (ImportError, ValueError):
    Shumate = tiles = None

MODES = ("off", "online", "offline")
FOLLOW_ZOOM = 16
MAX_TRACK = 5000  # Points kept; older halves are thinned when full.
INSTALL_HINT = "sudo dnf install libshumate"
TEST_URL = os.environ.get("M9N_TILE_URL")  # Tests only: honoured for a loopback tile server, nothing else.
STYLE_TILES_URL = None  # Tests only: the style's placeholder tile URL, to prove it is never fetched.
NO_MAP_FILE = ("No offline map file chosen. Make one at home with scripts/make-offline-map.sh (see the user "
               "guide), then choose it here.")
CONSENT = (
    "The map will download map images (tiles) from OpenStreetMap's tile servers, run by the OpenStreetMap "
    "Foundation, and any content delivery network they use.\n\n"
    "Sent to them: this computer's IP address, which tiles are requested (they show the area you view; zoomed in, "
    "that is roughly where you are), when, and the app's name.\n\n"
    "Never sent: coordinates as numbers, your track, receiver identifiers, evidence files or host diagnostics.\n\n"
    "Tiles are only requested for the area in view while this page is showing, and are kept in "
    "~/.cache/neo-m9n/tiles so they aren't downloaded again. You can switch back to Off at any time.")
GRID_STEPS = (1e-4, 2e-4, 5e-4, 1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30)


def thin(points, limit=MAX_TRACK):
    """Halve an over-long track, always keeping its newest point."""
    if len(points) <= limit:
        return points
    return points[:-1][::2] + points[-1:]


def grid_step(span_deg, lines=6):
    return next((step for step in GRID_STEPS if span_deg / step <= lines), GRID_STEPS[-1])


def meters_per_pixel(latitude, zoom, tile_size=256):
    return 2 * math.pi * 6378137 * math.cos(math.radians(latitude)) / (tile_size * 2 ** zoom)


class FitProjection:
    """Without libshumate: an equirectangular view fitted to the track."""

    def __init__(self, points, width, height, pad=40):
        lats = [p[0] for p in points] or [0.0]
        lons = [p[1] for p in points] or [0.0]
        self.lat0 = (min(lats) + max(lats)) / 2
        self.lon0 = (min(lons) + max(lons)) / 2
        kx = math.cos(math.radians(self.lat0))
        span = max((max(lons) - min(lons)) * kx, max(lats) - min(lats), 0.002)
        self.scale = min(width - 2 * pad, height - 2 * pad) / span  # Pixels per degree of latitude.
        self.kx, self.cx, self.cy = kx, width / 2, height / 2

    def to_px(self, lat, lon):
        return self.cx + (lon - self.lon0) * self.kx * self.scale, self.cy - (lat - self.lat0) * self.scale

    def to_location(self, x, y):
        return self.lat0 - (y - self.cy) / self.scale, self.lon0 + (x - self.cx) / (self.kx * self.scale)

    def meters_per_px(self, _lat):
        return 111320 / self.scale

    rotation = 0.0


class ShumateProjection:
    def __init__(self, simple_map):
        self.map = simple_map.get_map()
        self.viewport = simple_map.get_viewport()

    def to_px(self, lat, lon):
        return self.viewport.location_to_widget_coords(self.map, lat, lon)

    def to_location(self, x, y):
        return self.viewport.widget_coords_to_location(self.map, x, y)

    def meters_per_px(self, lat):
        source = self.viewport.get_reference_map_source()
        zoom = self.viewport.get_zoom_level()
        if source:
            return source.get_meters_per_pixel(zoom, lat, 0)
        return meters_per_pixel(lat, zoom)

    @property
    def rotation(self):
        return self.viewport.get_rotation()


class MapOverlay(Gtk.DrawingArea):
    """Grid, track, accuracy circle and marker, drawn over (or instead of) the map tiles."""

    def __init__(self, page):
        super().__init__(hexpand=True, vexpand=True, can_target=False)
        self.page = page
        self.set_draw_func(self.draw)

    def fg(self, alpha=1.0):
        c = self.get_color()
        return c.red, c.green, c.blue, alpha

    def draw(self, _area, cr, width, height):
        page = self.page
        projection = page.projection(width, height)
        if projection is None:
            return
        outside = page.outside_offline_map(projection, width, height)
        if page.mode == "off" or page.simple_map is None or outside:
            self.draw_grid(cr, projection, width, height)
        if outside:
            draw_text(self, cr, "Outside offline map", width / 2, height - 30, 13, weight=Pango.Weight.BOLD,
                      rgba=self.fg(0.6), numeric=False)
        self.draw_track(cr, projection)
        self.draw_marker(cr, projection)
        if page.dimmed:
            draw_text(self, cr, page.dimmed.upper(), width / 2, 28, 18, weight=Pango.Weight.HEAVY,
                      rgba=self.fg(0.8), numeric=False)

    def draw_grid(self, cr, projection, width, height):
        corners = [projection.to_location(x, y) for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
        lats, lons = [c[0] for c in corners], [c[1] for c in corners]
        south, north = max(-85.0, min(lats)), min(85.0, max(lats))
        west, east = min(lons), max(lons)
        # Square-ish cells: the spacing is chosen from the shorter side of the view.
        lat_span, lon_span = north - south, east - west
        step = grid_step(min(lat_span, lon_span * math.cos(math.radians((north + south) / 2))), lines=4)
        digits = max(0, -math.floor(math.log10(step)))
        cr.set_line_width(1)
        for lat in self._range(south, north, step):
            self._line(cr, projection, (lat, west), (lat, east))
            x, y = projection.to_px(lat, west)
            if y > height - 70:
                continue  # The scale bar's corner.
            draw_text(self, cr, f"{lat:.{digits}f}°", 6, y - 2, 11, weight=Pango.Weight.NORMAL,
                      rgba=self.fg(0.45), align=0, valign=1)
        for lon in self._range(west, east, step):
            self._line(cr, projection, (south, lon), (north, lon))
            x, y = projection.to_px(north, lon)  # Along the top: the scale bar sits bottom left.
            if x > width - 80:
                continue  # The zoom buttons' corner.
            draw_text(self, cr, f"{lon:.{digits}f}°", x + 4, 6, 11, weight=Pango.Weight.NORMAL,
                      rgba=self.fg(0.45), align=0, valign=0)

    @staticmethod
    def _range(low, high, step):
        value = math.ceil(low / step) * step
        while value <= high and step > 0:
            yield value
            value += step

    def _line(self, cr, projection, start, end):
        cr.set_source_rgba(*self.fg(0.12))
        cr.move_to(*projection.to_px(*start))
        cr.line_to(*projection.to_px(*end))
        cr.stroke()

    def draw_track(self, cr, projection):
        points = self.page.track
        if len(points) < 2:
            return
        maximum = max(p[2] or 0 for p in points) or 1
        pixels = [projection.to_px(lat, lon) for lat, lon, _ in points]
        cr.set_line_cap(1)
        cr.set_line_join(1)
        # A soft casing keeps the line readable over any map.
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.set_line_width(8)
        cr.move_to(*pixels[0])
        for px in pixels[1:]:
            cr.line_to(*px)
        cr.stroke()
        cr.set_line_width(5)
        for (a, b), point in zip(zip(pixels, pixels[1:]), points[1:]):
            cr.set_source_rgb(*blend(SPEED_STOPS, (point[2] or 0) / maximum))
            cr.move_to(*a)
            cr.line_to(*b)
            cr.stroke()

    def draw_marker(self, cr, projection):
        fix = self.page.fix
        if fix is None:
            return
        lat, lon, accuracy, heading = fix
        x, y = projection.to_px(lat, lon)
        accent = (0.21, 0.52, 0.89)
        alpha = 0.45 if self.page.dimmed else 1.0
        if accuracy:
            radius = accuracy / max(projection.meters_per_px(lat), 1e-9)
            if 6 < radius < 4000:
                cr.arc(x, y, radius, 0, 2 * math.pi)
                cr.set_source_rgba(*accent, 0.15 * alpha)
                cr.fill_preserve()
                cr.set_source_rgba(*accent, 0.55 * alpha)
                cr.set_line_width(1.5)
                cr.stroke()
        if heading is not None and not self.page.dimmed:
            angle = math.radians(heading) + projection.rotation
            size = 16
            points = [(0, -size * 1.3), (size * 0.8, size), (0, size * 0.45), (-size * 0.8, size)]
            cr.move_to(*self._rotate(points[0], angle, x, y))
            for point in points[1:]:
                cr.line_to(*self._rotate(point, angle, x, y))
            cr.close_path()
            gradient = cairo.LinearGradient(*self._rotate((0, size), angle, x, y), *self._rotate((0, -size), angle, x, y))
            gradient.add_color_stop_rgb(0, *POINTER[0])
            gradient.add_color_stop_rgb(1, *POINTER[1])
            cr.set_source(gradient)
            cr.fill_preserve()
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2.5)
            cr.stroke()
        else:
            cr.arc(x, y, 9, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.fill()
            cr.arc(x, y, 6.5, 0, 2 * math.pi)
            cr.set_source_rgba(*(accent if not self.page.dimmed else (0.5, 0.5, 0.5)), alpha)
            cr.fill()

    @staticmethod
    def _rotate(point, angle, x, y):
        px, py = point
        return (x + px * math.cos(angle) - py * math.sin(angle), y + px * math.sin(angle) + py * math.cos(angle))


class MapPage(SessionPage):
    def __init__(self, page):
        self.settings = None  # Bound by the window: SessionSettings with the map_* fields.
        self.on_settings_changed = None
        self.track = []  # (lat, lon, speed m/s)
        self.fix = None  # (lat, lon, horizontal accuracy m, heading or None) of the last fix shown
        self.dimmed = "Waiting"
        self.mode = "off"
        self.following = True
        self.page_visible = False
        self.online = None  # tiles.OnlineTiles while online
        self.offline = None  # tiles.OfflineTiles while offline
        self.map_file = None  # m9n.mbtiles.MapFile, once chosen
        self.load_error = None
        self.cache_limit_mb = 200
        self._epoch = None
        self._moving_map = False
        self._zoomed_in = False
        super().__init__(page)
        self.stack.set_visible_child_name("content")  # The map is useful before a session starts.

    # Building ------------------------------------------------------------

    def build(self):
        self.status = Gtk.Label(label="Waiting", valign=Gtk.Align.CENTER)
        self.status.add_css_class("heading")
        self.mode_buttons = {}
        modes = Gtk.Box(valign=Gtk.Align.CENTER)
        modes.add_css_class("linked")
        for key, label, tooltip in (("off", "Off", "Grid only: no network"),
                                    ("online", "Online", "OpenStreetMap tiles (asks first)"),
                                    ("offline", "Offline", "Offline map file: no network")):
            button = Gtk.ToggleButton(label=label, tooltip_text=tooltip,
                                      group=self.mode_buttons.get("off"))
            button.connect("toggled", self.on_mode_toggled, key)
            modes.append(button)
            self.mode_buttons[key] = button
        self.mode_buttons["off"].set_active(True)
        self.mode_buttons["offline"].set_sensitive(False)
        self.indicator = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, visible=False)
        self.indicator_icon = Gtk.Image(icon_name="network-transmit-receive-symbolic")
        self.indicator.append(self.indicator_icon)
        self.indicator_label = Gtk.Label(label="Online: OpenStreetMap")
        self.indicator.append(self.indicator_label)
        self.indicator.add_css_class("accent")
        self.indicator.set_tooltip_text("Map squares are downloaded from OpenStreetMap as it draws them, "
                                        "so they stay light in night mode.")
        self.indicator.update_property([Gtk.AccessibleProperty.LABEL], ["Map is online: OpenStreetMap"])

        self.storage = Gtk.MenuButton(icon_name="drive-harddisk-symbolic", tooltip_text="Map storage",
                                      valign=Gtk.Align.CENTER, popover=self.build_storage())
        self.storage.update_property([Gtk.AccessibleProperty.LABEL], ["Map storage"])
        self.fullscreen_button = Gtk.Button(icon_name="view-fullscreen-symbolic", action_name="win.fullscreen",
                                            valign=Gtk.Align.CENTER, tooltip_text="Fullscreen (F11)")
        self.fullscreen_button.update_property([Gtk.AccessibleProperty.LABEL], ["Toggle fullscreen"])
        toolbar = self.toolbar = Gtk.Box(spacing=8, margin_top=12, margin_bottom=6, margin_start=12, margin_end=12)
        toolbar.append(self.status)
        toolbar.append(modes)
        toolbar.append(self.indicator)
        toolbar.append(Gtk.Box(hexpand=True))
        toolbar.append(self.storage)
        self.export_button = export_button()
        toolbar.append(self.export_button)
        toolbar.append(night_button())
        toolbar.append(self.fullscreen_button)

        self.overlay_area = MapOverlay(self)
        self.recentre = Gtk.Button(icon_name="find-location-symbolic", tooltip_text="Re-centre and follow",
                                   halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=12, margin_bottom=56,
                                   visible=False)
        self.recentre.add_css_class("osd")
        self.recentre.add_css_class("circular")
        self.recentre.update_property([Gtk.AccessibleProperty.LABEL], ["Re-centre and follow"])
        self.recentre.connect("clicked", lambda _b: self.follow())
        overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        if Shumate:
            self.simple_map = Shumate.SimpleMap(vexpand=True, hexpand=True)
            self.simple_map.set_map_source(self.blank_source())
            viewport = self.simple_map.get_viewport()
            viewport.set_zoom_level(2)
            for name in ("latitude", "longitude"):
                viewport.connect(f"notify::{name}", self.on_view_moved)
            for name in ("zoom-level", "rotation"):
                viewport.connect(f"notify::{name}", lambda *_: self.overlay_area.queue_draw())
            overlay.set_child(self.simple_map)
            self.missing = None
        else:
            self.simple_map = None
            overlay.set_child(Gtk.Box(vexpand=True, hexpand=True))
            self.missing = Gtk.Label(wrap=True, margin_start=12, margin_end=12, xalign=0,
                                     label=f"Maps need libshumate, GNOME's map widget. Install it with: {INSTALL_HINT}"
                                           " — until then the track is drawn on a grid.")
            self.missing.add_css_class("dim-label")
            for button in self.mode_buttons.values():
                button.set_sensitive(False)
        overlay.add_overlay(self.overlay_area)
        overlay.add_overlay(self.recentre)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(toolbar)
        if self.missing:
            content.append(self.missing)
        content.append(overlay)
        self.adaptive = Adw.BreakpointBin(child=content, width_request=280, height_request=200)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 520sp"))
        compact.add_setter(self.indicator_label, "visible", False)  # The icon alone still shows the mode.
        compact.add_setter(self.fullscreen_button, "visible", False)  # F11 still works; phones rarely need it.
        compact.add_setter(self.export_button, "visible", False)  # Still on the Vehicle page, and Ctrl+E.
        compact.add_setter(self.toolbar, "spacing", 4)
        for side in ("margin-start", "margin-end"):
            compact.add_setter(self.toolbar, side, 6)  # Room for night mode beside the online indicator.
        self.adaptive.add_breakpoint(compact)
        return self.adaptive

    def build_storage(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12, width_request=300)
        title = Gtk.Label(label="Online map cache", xalign=0)
        title.add_css_class("heading")
        self.cache_usage = Gtk.Label(xalign=0)
        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, label=(
            "Tiles viewed online are kept in ~/.cache/neo-m9n/tiles so they aren't downloaded again. They show "
            "which areas you have viewed. They are only used while online, never as an offline map."))
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        limit_row = Gtk.Box(spacing=8)
        limit_row.append(Gtk.Label(label="Limit (MB)", xalign=0, hexpand=True))
        self.limit = Gtk.SpinButton.new_with_range(20, 5000, 10)
        self.limit.connect("value-changed", self.on_limit_changed)
        limit_row.append(self.limit)
        self.clear_button = Gtk.Button(label="Clear Map Cache")
        self.clear_button.add_css_class("destructive-action")
        self.clear_button.connect("clicked", self.clear_cache)
        offline_title = Gtk.Label(label="Offline map file", xalign=0, margin_top=8)
        offline_title.add_css_class("heading")
        self.map_file_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, selectable=True, label=NO_MAP_FILE)
        self.map_file_label.add_css_class("caption")
        file_buttons = Gtk.Box(spacing=8)
        choose = Gtk.Button(label="Choose…", hexpand=True)
        choose.connect("clicked", self.choose_map_file)
        self.remove_button = Gtk.Button(label="Remove", hexpand=True, sensitive=False,
                                        tooltip_text="Stop using this file (it isn't deleted)")
        self.remove_button.connect("clicked", lambda _b: self.set_map_file(None))
        file_buttons.append(choose)
        file_buttons.append(self.remove_button)
        for widget in (title, self.cache_usage, note, limit_row, self.clear_button, Gtk.Separator(margin_top=4),
                       offline_title, self.map_file_label, file_buttons):
            box.append(widget)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda _p: self.show_usage())
        return popover

    def blank_source(self):
        return tiles.renderer("blank", "Grid", "", "", tiles.BlankTiles())

    # Settings and modes --------------------------------------------------

    def bind_settings(self, settings, on_changed):
        # Restore before connecting the save callback: the widgets' change handlers save.
        self.cache_limit_mb = settings.map_cache_mb
        self.limit.set_value(settings.map_cache_mb)
        self.settings = settings
        if settings.map_offline_file:
            self.set_map_file(settings.map_offline_file, quiet=True)
        if settings.map_mode == "online" and settings.map_consented and Shumate:
            self.set_mode("online")
        elif settings.map_mode == "offline" and self.map_file and Shumate:
            self.set_mode("offline")
        self.on_settings_changed = on_changed
        Adw.StyleManager.get_default().connect("notify::dark", self.on_style_changed)

    def save(self):
        if self.settings and self.on_settings_changed:
            self.settings.map_mode = self.mode
            self.settings.map_cache_mb = int(self.cache_limit_mb)
            self.settings.map_offline_file = str(self.map_file.path) if self.map_file else ""
            self.on_settings_changed()

    def on_mode_toggled(self, button, key):
        if not button.get_active() or key == self.mode:
            return
        if key == "online" and not (self.settings and self.settings.map_consented):
            self.ask_consent()
            return
        self.set_mode(key)

    def ask_consent(self):
        dialog = Adw.AlertDialog(heading="Use Online Maps?", body=CONSENT)
        dialog.add_response("cancel", "Keep Off")
        dialog.add_response("online", "Use Online Maps")
        dialog.set_response_appearance("online", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_consent)
        self.consent_dialog = dialog
        dialog.present(self.get_root())

    def on_consent(self, _dialog, response):
        self.consent_dialog = None
        if response == "online":
            if self.settings:
                self.settings.map_consented = True
            self.set_mode("online")
        else:
            self.mode_buttons[self.mode].set_active(True)

    def set_mode(self, mode):
        if Shumate is None:
            return
        if self.online:
            self.online.set_enabled(False)
            self.online.cache.close()
            self.online = None
        self.offline = None
        if mode == "offline" and not self.map_file:
            mode = "off"
        if mode == "online":
            renderer, self.online = tiles.osm_renderer(self.cache_limit_mb, tiles.loopback_url(TEST_URL))
            self.simple_map.set_map_source(renderer)
            self.online.set_enabled(self.page_visible)
            self.indicator_icon.set_from_icon_name("network-transmit-receive-symbolic")
            self.indicator_label.set_label("Online: OpenStreetMap")
        elif mode == "offline":
            renderer, self.offline = tiles.offline_renderer(self.map_file, Adw.StyleManager.get_default().get_dark(),
                                                            STYLE_TILES_URL)
            self.simple_map.set_map_source(renderer)
            self.indicator_icon.set_from_icon_name("network-offline-symbolic")
            self.indicator_label.set_label(f"Offline: {self.map_file.name}, data {self.map_file.data_date:%Y-%m-%d}")
        else:
            self.simple_map.set_map_source(self.blank_source())
        self.mode = mode
        self.mode_buttons[mode].set_active(True)
        self.indicator.set_visible(mode != "off")
        self.indicator.update_property([Gtk.AccessibleProperty.LABEL], [self.indicator_label.get_label()])
        self.overlay_area.queue_draw()
        self.save()

    def on_style_changed(self, *_):
        if self.mode == "offline":
            self.set_mode("offline")  # Redraw with the light or dark style.

    # Offline map file ----------------------------------------------------

    def choose_map_file(self, _button=None):
        dialog = Gtk.FileDialog(title="Choose an Offline Map File", modal=True)
        maps = Gtk.FileFilter(name="Offline maps (*.mbtiles)")
        maps.add_pattern("*.mbtiles")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(maps)
        dialog.set_filters(filters)
        documents = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or str(Path.home())
        default = Path(documents) / "NEO-M9N" / "maps"  # Where make-offline-map.sh writes maps.
        if default.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(default)))
        self.storage.popdown()
        dialog.open(self.get_root(), None, self.on_map_file_chosen)

    def on_map_file_chosen(self, dialog, result):
        try:
            path = dialog.open_finish(result).get_path()
        except GLib.Error:
            return  # Cancelled.
        self.set_map_file(path)

    def set_map_file(self, path, quiet=False):
        """Use an offline map file (None stops using it); returns True if it can be used."""
        if self.map_file:
            self.map_file.close()
        self.map_file, error = None, None
        if path:
            try:
                self.map_file = MapFile(path)
            except MapFileError as exc:
                error = str(exc)
        self.mode_buttons["offline"].set_sensitive(bool(self.map_file) and Shumate is not None)
        self.remove_button.set_sensitive(bool(self.map_file))
        if self.map_file:
            info = self.map_file.summary()
            self.map_file_label.set_label(
                f"{info['name']}: map data from {info['data_date']}, zoom {info['min_zoom']}–{info['max_zoom']}, "
                f"{GLib.format_size(info['size'])}.\n{info['path']}\nRead locally; nothing is downloaded.")
        else:
            self.map_file_label.set_label(f"{error}\n\n{NO_MAP_FILE}" if error else NO_MAP_FILE)
        if self.mode == "offline":
            self.set_mode("offline" if self.map_file else "off")
        else:
            self.save()
        window = self.get_root()
        if error and not quiet and hasattr(window, "alert"):
            window.alert("Offline Map Not Usable", error)
        elif error and quiet:
            self.load_error = error
        return self.map_file is not None

    def outside_offline_map(self, projection, width, height):
        if self.mode != "offline" or not self.map_file or self.simple_map is None:
            return False
        lat, lon = projection.to_location(width / 2, height / 2)
        return not self.map_file.covers(lat, lon)

    def set_page_visible(self, visible):
        """Tiles are only requested while this page is on screen."""
        self.page_visible = visible
        if self.online:
            self.online.set_enabled(visible)

    # Storage -------------------------------------------------------------

    def cache(self):
        from ..tilecache import TileCache
        return self.online.cache if self.online else TileCache(source="osm", limit_mb=self.cache_limit_mb)

    def show_usage(self):
        cache = self.cache()
        size, count = cache.usage()
        if cache is not (self.online and self.online.cache):
            cache.close()
        self.cache_usage.set_label(f"{GLib.format_size(size)} in {count:,} tiles")
        self.clear_button.set_sensitive(count > 0)

    def on_limit_changed(self, spin):
        self.cache_limit_mb = spin.get_value()
        if self.online:
            self.online.cache.set_limit(self.cache_limit_mb)
        self.save()

    def clear_cache(self, _button=None):
        cache = self.cache()
        cache.clear()
        if cache is not (self.online and self.online.cache):
            cache.close()
        self.show_usage()
        window = self.get_root()
        if hasattr(window, "toast"):
            window.toast("Map cache cleared")

    # Position ------------------------------------------------------------

    def session_started(self, config):
        super().session_started(config)
        self.trip_reset()
        self._zoomed_in = False

    def update(self, snapshot):
        self.snapshot = snapshot
        self.show(snapshot)

    def show(self, snapshot):
        nav = snapshot["navigation"] or {}
        state, _ = fmt.fix_state(snapshot["navigation"])
        text, style = STATUS[state]
        for name in ("success", "error", "warning", "dim-label"):
            self.status.remove_css_class(name)
        self.status.add_css_class(style)
        self.status.set_label(text)
        live = state == "valid"
        self.dimmed = None if live else text
        lat, lon = nav.get("latitude"), nav.get("longitude")
        if lat is not None and lon is not None and nav.get("fix_ok"):
            speed = nav.get("speed_m_s")
            heading = nav.get("heading_deg") if live and (speed or 0) >= HEADING_MIN_M_S else None
            self.fix = (lat, lon, nav.get("horizontal_accuracy_m"), heading)
            epoch = nav.get("itow_ms", nav.get("epoch"))
            if live and epoch != self._epoch:
                self._epoch = epoch
                self.track.append((lat, lon, speed))
                self.track = thin(self.track)
            if live and self.following:
                self.centre_on(lat, lon)
        self.recentre.set_visible(not self.following and self.fix is not None)
        self.overlay_area.queue_draw()

    def trip_reset(self):
        self.track = []
        self._epoch = None
        self.overlay_area.queue_draw()

    def centre_on(self, lat, lon):
        if self.simple_map is None:
            return
        self._moving_map = True
        try:
            if not self._zoomed_in:
                self.simple_map.get_viewport().set_zoom_level(FOLLOW_ZOOM)
                self._zoomed_in = True
            self.simple_map.get_map().center_on(lat, lon)
        finally:
            self._moving_map = False

    def follow(self):
        self.following = True
        self.recentre.set_visible(False)
        if self.fix:
            self.centre_on(self.fix[0], self.fix[1])

    def on_view_moved(self, *_):
        if not self._moving_map and self.following and self.fix is not None:
            self.following = False  # The user panned: stop following until Re-centre.
            self.recentre.set_visible(True)
        self.overlay_area.queue_draw()

    def projection(self, width, height):
        if self.simple_map is not None:
            return ShumateProjection(self.simple_map)
        points = self.track[:] + ([self.fix[:2] + (None,)] if self.fix else [])
        return FitProjection(points, width, height)

    def set_fullscreen(self, fullscreen):
        self.fullscreen_button.set_icon_name("view-restore-symbolic" if fullscreen else "view-fullscreen-symbolic")

    def do_unroot(self):
        if self.online:
            self.online.set_enabled(False)
        SessionPage.do_unroot(self)
