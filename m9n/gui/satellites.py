"""Satellites page: summary, sky plot and a sortable per-satellite table from NAV-SAT."""
import math
from collections import Counter

from gi.repository import Adw, Gio, GObject, Gtk

from ..serialize import safe
from . import format as fmt
from .pages import SessionPage

QUALITY = {0: "no signal", 1: "searching", 2: "acquired", 3: "unusable", 4: "code locked",
           5: "carrier locked", 6: "carrier locked", 7: "carrier locked"}
HEALTH = {0: "unknown", 1: "healthy", 2: "unhealthy"}
CNO_MAX = 50  # dB-Hz; strong open-sky signals sit in the 40s.


def known_position(sat):
    """NAV-SAT marks elevation/azimuth unknown with an out-of-range elevation."""
    return -90 <= sat["elevation_deg"] <= 90


class Satellite(GObject.Object):
    def __init__(self, data):
        super().__init__()
        self.key = (data["gnss"], data["sv"])
        self.data = data


def ordering(a, b):
    return Gtk.Ordering.SMALLER if a < b else Gtk.Ordering.LARGER if a > b else Gtk.Ordering.EQUAL


def sorter(key):
    return Gtk.CustomSorter.new(lambda a, b, _: ordering(key(a.data), key(b.data)), None)


class SkyPlot(Gtk.DrawingArea):
    """Polar azimuth/elevation plot; used satellites filled in the accent colour."""

    def __init__(self):
        super().__init__(content_width=260, content_height=260, hexpand=False, vexpand=False)
        self.satellites = []
        self.set_draw_func(self.draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Sky plot"])

    def show(self, satellites):
        self.satellites = [s for s in satellites if known_position(s)]
        used = sum(s["used"] for s in self.satellites)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Sky plot: {len(self.satellites)} satellites with known position, {used} used"])
        self.queue_draw()

    def draw(self, _area, cr, width, height):
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 18
        cr.set_line_width(1)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, .25)
        for elevation in (0, 30, 60):
            cr.new_sub_path()
            cr.arc(cx, cy, radius * (90 - elevation) / 90, 0, 2 * math.pi)
            cr.stroke()
        cr.move_to(cx - radius, cy)
        cr.line_to(cx + radius, cy)
        cr.move_to(cx, cy - radius)
        cr.line_to(cx, cy + radius)
        cr.stroke()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, .7)
        cr.set_font_size(11)
        for text, x, y in (("N", cx, cy - radius - 9), ("E", cx + radius + 9, cy), ("S", cx, cy + radius + 9),
                           ("W", cx - radius - 9, cy)):
            extents = cr.text_extents(text)
            cr.move_to(x - extents.width / 2, y + extents.height / 2)
            cr.show_text(text)
            cr.new_path()
        for sat in sorted(self.satellites, key=lambda s: s["used"]):
            distance = radius * (90 - max(0, sat["elevation_deg"])) / 90
            angle = math.radians(sat["azimuth_deg"])
            x, y = cx + distance * math.sin(angle), cy - distance * math.cos(angle)
            cr.new_path()  # Text leaves a current point; never join it to the marker.
            cr.arc(x, y, 5, 0, 2 * math.pi)
            if sat["used"]:
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
                cr.fill()
            else:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, .45)
                cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, .8)
            cr.set_font_size(9)
            cr.move_to(x + 7, y + 3)
            cr.show_text(f"{sat['gnss'][:3]} {sat['sv']}")
            cr.new_path()


class SatellitesPage(SessionPage):
    def build(self):
        self.items = {}
        self.store = Gio.ListStore(item_type=Satellite)
        self.view = Gtk.ColumnView(show_row_separators=True, reorderable=False)
        self.view.add_css_class("data-table")
        default = Gtk.MultiSorter()
        default.append(sorter(lambda d: not d["used"]))
        default.append(sorter(lambda d: -d["cno_dbhz"]))
        default.append(sorter(lambda d: (d["gnss"], d["sv"])))
        self.default_sorter = default
        order = Gtk.MultiSorter()
        order.append(self.view.get_sorter())  # The user's column choice wins...
        order.append(default)  # ...and the CLI's order breaks ties and applies by default.
        self.sorted = Gtk.SortListModel(model=self.store, sorter=order)
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.view.set_model(self.selection)

        self.add_column("Constellation", lambda d: d["gnss"], lambda d: d["gnss"])
        self.add_column("SV", lambda d: str(d["sv"]), lambda d: d["sv"], numeric=True)
        self.add_cno_column()
        self.add_column("Elevation", lambda d: f"{d['elevation_deg']}°" if known_position(d) else fmt.UNKNOWN,
                        lambda d: d["elevation_deg"], numeric=True)
        self.add_column("Azimuth", lambda d: f"{d['azimuth_deg']}°" if known_position(d) else fmt.UNKNOWN,
                        lambda d: d["azimuth_deg"], numeric=True)
        self.add_column("Used", lambda d: "yes" if d["used"] else "no", lambda d: not d["used"])
        self.add_column("Signal", lambda d: QUALITY.get(d["quality"], str(d["quality"])), lambda d: -d["quality"])
        self.add_column("Health", lambda d: HEALTH.get(d["health"], str(d["health"])), lambda d: d["health"])

        scrolled = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        scrolled.add_css_class("card")
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.summary.add_css_class("heading")
        self.constellations = Gtk.Label(xalign=0, wrap=True)
        self.constellations.add_css_class("dim-label")
        self.sky = SkyPlot()
        sky_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START, halign=Gtk.Align.CENTER)
        sky_card.add_css_class("card")
        sky_card.append(self.sky)

        table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        table.append(self.summary)
        table.append(self.constellations)
        table.append(scrolled)
        # The window's narrow breakpoint flips this to vertical (sky plot above the table).
        self.layout = Gtk.Box(spacing=18, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.layout.append(table)
        self.layout.append(sky_card)
        self.sky_card = sky_card

        self.nodata = Adw.StatusPage(icon_name=self.page.icon, title="No Satellite Data")
        self.inner = Gtk.Stack()
        self.inner.add_named(self.layout, "table")
        self.inner.add_named(self.nodata, "nodata")
        return self.inner

    def add_column(self, title, text, key, numeric=False):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            label = Gtk.Label(xalign=1 if numeric else 0)
            if numeric:
                label.add_css_class("numeric")
            item.set_child(label)

        factory.connect("setup", setup)
        factory.connect("bind", lambda _f, item: item.get_child().set_label(safe(text(item.get_item().data))))
        column = Gtk.ColumnViewColumn(title=title, factory=factory, sorter=sorter(key))
        self.view.append_column(column)
        return column

    def add_cno_column(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            label = Gtk.Label(xalign=1, width_chars=3)
            label.add_css_class("numeric")
            bar = Gtk.LevelBar(min_value=0, max_value=CNO_MAX, hexpand=True, valign=Gtk.Align.CENTER)
            bar.set_size_request(60, -1)
            box = Gtk.Box(spacing=8)
            box.append(label)
            box.append(bar)
            item.set_child(box)

        def bind(_factory, item):
            cno = item.get_item().data["cno_dbhz"]
            label, bar = item.get_child().get_first_child(), item.get_child().get_last_child()
            label.set_label(str(cno))
            bar.set_value(min(cno, CNO_MAX))
            bar.update_property([Gtk.AccessibleProperty.LABEL], [f"C/N0 {cno} dB-Hz"])

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        column = Gtk.ColumnViewColumn(title="C/N0 dB-Hz", factory=factory, expand=True,
                                      sorter=sorter(lambda d: d["cno_dbhz"]))
        self.view.append_column(column)

    def show(self, snapshot):
        satellites = snapshot["satellites"]
        if not satellites:
            rejected = "NAV-SAT" in snapshot["rejected_polls"]
            self.nodata.set_description(
                "The receiver rejected NAV-SAT polls." if rejected else
                "NAV-SAT has not been answered yet. Missing satellite data is not evidence of a hardware fault.")
            self.inner.set_visible_child_name("nodata")
            return
        self.inner.set_visible_child_name("table")
        self.sync(satellites)
        used = sum(s["used"] for s in satellites)
        self.summary.set_label(f"{len(satellites)} satellites tracked · {used} used in the solution · "
                               f"data age {fmt.seconds(snapshot['satellites_age_s'])}")
        per = Counter(s["gnss"] for s in satellites)
        per_used = Counter(s["gnss"] for s in satellites if s["used"])
        self.constellations.set_label(" · ".join(f"{name} {per_used[name]}/{count}"
                                                 for name, count in sorted(per.items())))
        self.sky.show(satellites)

    def sync(self, satellites):
        """Update rows in place so scrolling and selection survive each refresh."""
        incoming = {(s["gnss"], s["sv"]): s for s in satellites}
        for key in [k for k in self.items if k not in incoming]:
            found, position = self.store.find(self.items.pop(key))
            if found:
                self.store.remove(position)
        for key, data in incoming.items():
            item = self.items.get(key)
            if item is None:
                self.items[key] = Satellite(data)
                self.store.append(self.items[key])
            elif item.data != data:
                item.data = data
                found, position = self.store.find(item)
                self.store.items_changed(position, 1, 1)  # Rebinds the row and re-sorts.
