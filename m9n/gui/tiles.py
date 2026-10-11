"""Map tile sources for the Map page. The app, not libshumate, decides when the network is used.

* BlankTiles draws nothing but a plain background, locally: the default "Off" mode never
  creates an HTTP session.
* OfflineTiles serves vector tiles from a local offline map file (m9n.mbtiles); it has no
  network code at all.
* OnlineTiles serves OpenStreetMap tiles only for tiles libshumate asks for (the visible
  area), only while enabled (Online and the Map page showing): cache first, conditional
  revalidation of expired tiles, then HTTPS with the app's own User-Agent. It has no
  prefetch or bulk path, and cached tiles are never served while it is disabled.
"""
import io
import logging
from urllib.parse import urlsplit

import cairo
from gi.repository import Gio, GLib, Shumate, Soup

from .. import __version__
from ..tilecache import TileCache

LOG = logging.getLogger("m9n.map")
USER_AGENT = f"NEO-M9N-Monitor/{__version__} (+https://github.com/PurpleSentinel/neo-m9n-ui)"
OSM = {"id": "osm", "name": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
       "attribution": "© OpenStreetMap contributors", "license_uri": "https://www.openstreetmap.org/copyright",
       "max_zoom": 19}
MAX_PARALLEL = 2  # Requests in flight at once; light use, as the OSM policy expects.
MIN_ZOOM, MAX_ZOOM, TILE_SIZE = 0, 19, 256
SESSIONS_CREATED = []  # Every HTTP session the map ever creates; tests check "Off" creates none.


def valid_url(template):
    """HTTPS tile URL with {z}/{x}/{y}; plain HTTP is allowed only for a loopback test server."""
    try:
        parts = urlsplit(template)
    except ValueError:
        return False
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))
    return secure and bool(parts.hostname) and all(f"{{{k}}}" in template for k in "zxy")


def loopback_url(template):
    """A test tile server URL, accepted only on this machine; None otherwise."""
    if not template or not valid_url(template):
        return None
    return template if urlsplit(template).hostname in ("127.0.0.1", "localhost") else None


def _error(message):
    return GLib.Error.new_literal(Gio.io_error_quark(), message, int(Gio.IOErrorEnum.FAILED))


def _deliver(request, data):
    GLib.idle_add(lambda: request.emit_data(GLib.Bytes.new(data), True) and False)


class BlankTiles(Shumate.DataSource):
    """A plain, theme-neutral background tile generated locally."""

    def __init__(self):
        super().__init__()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, TILE_SIZE, TILE_SIZE)
        buffer = io.BytesIO()
        surface.write_to_png(buffer)  # Fully transparent: the page background shows through.
        self.png = buffer.getvalue()

    def do_start_request(self, x, y, zoom, cancellable):
        request = Shumate.DataSourceRequest(x=x, y=y, zoom_level=zoom)
        _deliver(request, self.png)
        return request


def renderer(id_, name, license_text, license_uri, data_source, max_zoom=MAX_ZOOM):
    return Shumate.RasterRenderer.new_full(id_, name, license_text, license_uri, MIN_ZOOM, max_zoom, TILE_SIZE,
                                           Shumate.MapProjection.MERCATOR, data_source)


class OfflineTiles(Shumate.DataSource):
    """Vector tiles read from an offline map file. There is no network path in this class."""

    def __init__(self, map_file):
        super().__init__(min_zoom_level=map_file.min_zoom, max_zoom_level=map_file.max_zoom)
        self.map_file = map_file
        self.requests = 0

    def do_start_request(self, x, y, zoom, cancellable):
        request = Shumate.DataSourceRequest(x=x, y=y, zoom_level=zoom)
        self.requests += 1
        try:
            data = self.map_file.tile(zoom, x, y) or b""  # Empty: nothing mapped there (e.g. open sea).
        except Exception as exc:  # A damaged file must not take the page down.
            LOG.warning("Offline map tile %s/%s/%s unreadable: %s", zoom, x, y, exc)
            data = b""
        _deliver(request, data)
        return request


def offline_renderer(map_file, dark=False, tiles_url=None):
    """(renderer, data source) drawing an offline map file with the bundled style."""
    from .mapstyle import PLACEHOLDER_TILES, style
    source = OfflineTiles(map_file)
    renderer = Shumate.VectorRenderer.new("offline", style("dark" if dark else "light", map_file.max_zoom,
                                                            tiles_url or PLACEHOLDER_TILES))
    renderer.set_data_source("openmaptiles", source)
    renderer.set_license(map_file.attribution)
    renderer.set_min_zoom_level(0)
    renderer.set_max_zoom_level(MAX_ZOOM)
    return renderer, source


class OnlineTiles(Shumate.DataSource):
    def __init__(self, cache, url=OSM["url"]):
        super().__init__()
        if not valid_url(url):
            raise ValueError(f"Not an HTTPS tile URL with {{z}}/{{x}}/{{y}}: {url}")
        self.cache, self.url = cache, url
        self.enabled = False
        self.session = None  # Created on the first real download only.
        self.queue = []
        self.active = 0
        self.cancellable = Gio.Cancellable()
        self.downloads = 0  # Requests sent; for the page's indicator and tests.

    def set_enabled(self, enabled):
        """Only while enabled may tiles be served at all; disabling cancels everything pending."""
        self.enabled = enabled
        if not enabled:
            self.cancellable.cancel()
            self.cancellable = Gio.Cancellable()
            for request, *_ in self.queue:
                request.emit_error(_error("Map is not online"))
            self.queue.clear()

    def do_start_request(self, x, y, zoom, cancellable):
        request = Shumate.DataSourceRequest(x=x, y=y, zoom_level=zoom)
        if not self.enabled:
            GLib.idle_add(lambda: request.emit_error(_error("Map is not online")) and False)
            return request
        entry = self.cache.get(zoom, x, y)
        if entry and entry.fresh:
            _deliver(request, entry.data)
        else:
            self.queue.append((request, entry, cancellable))
            self._pump()
        return request

    def _pump(self):
        while self.enabled and self.active < MAX_PARALLEL and self.queue:
            request, entry, cancellable = self.queue.pop(0)
            if cancellable and cancellable.is_cancelled():
                continue  # libshumate no longer needs this tile (scrolled away).
            self._fetch(request, entry)

    def _fetch(self, request, entry):
        if self.session is None:
            self.session = Soup.Session(user_agent=USER_AGENT, max_conns_per_host=MAX_PARALLEL)
            SESSIONS_CREATED.append(self.session)
        z, x, y = request.get_zoom_level(), request.get_x(), request.get_y()
        message = Soup.Message.new("GET", self.url.format(z=z, x=x, y=y))
        headers = message.get_request_headers()
        for name, value in (entry.conditional_headers() if entry else {}).items():
            headers.append(name, value)
        self.active += 1
        self.downloads += 1
        self.session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, self.cancellable,
                                         self._done, (request, entry, message))

    def _done(self, session, result, context):
        request, entry, message = context
        self.active -= 1
        z, x, y = request.get_zoom_level(), request.get_x(), request.get_y()
        try:
            body = session.send_and_read_finish(result).get_data() or b""
        except GLib.Error as exc:
            if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                LOG.info("Map tile %s/%s/%s not downloaded: %s", z, x, y, exc.message)
            self._fallback(request, entry, exc.message)
            self._pump()
            return
        status = message.get_status()
        response = {}
        message.get_response_headers().foreach(lambda name, value: response.__setitem__(name, value))
        if status == 304 and entry:
            self.cache.revalidated(z, x, y, response)
            request.emit_data(GLib.Bytes.new(entry.data), True)
        elif status == 200 and self.cache.put(z, x, y, body, response):
            request.emit_data(GLib.Bytes.new(body), True)
        else:
            if status == 200:
                LOG.warning("Map tile %s/%s/%s was not an image; not shown or cached", z, x, y)
            else:
                LOG.info("Map tile %s/%s/%s: HTTP %s", z, x, y, status)
            self._fallback(request, entry, f"HTTP {status}")
        self._pump()

    def _fallback(self, request, entry, reason):
        # While online, an expired copy beats an empty square; offline nothing is served.
        if entry and self.enabled:
            request.emit_data(GLib.Bytes.new(entry.data), True)
        else:
            request.emit_error(_error(reason))


def osm_renderer(cache_limit_mb, url=None):
    """(renderer, data source) for OpenStreetMap; url overrides the tile URL for tests."""
    source = OnlineTiles(TileCache(source=OSM["id"], limit_mb=cache_limit_mb), url or OSM["url"])
    return renderer(OSM["id"], OSM["name"], OSM["attribution"], OSM["license_uri"], source, OSM["max_zoom"]), source
