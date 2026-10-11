"""Read-only access to an offline map file: vector tiles in an MBTiles (SQLite) database.

Offline map files are made once, at home, from OpenStreetMap data (see
scripts/make-offline-map.sh); reading one never uses the network. Only vector tiles in
the OpenMapTiles schema are supported, because the bundled map style is written for it.

GTK-free so the checks can be unit-tested.
"""
import gzip
import html
import json
import re
import sqlite3
import zlib
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ATTRIBUTION = "© OpenMapTiles © OpenStreetMap contributors"
# Layers the bundled style draws; a file needs most of them to be an OpenMapTiles map.
OPENMAPTILES_LAYERS = {"water", "landuse", "landcover", "park", "waterway", "transportation", "building",
                       "boundary", "place", "transportation_name", "water_name"}
CACHE_TILES = 256


class MapFileError(ValueError):
    """The file can't be used as an offline map; the message says why, for the user."""


def plain_attribution(text):
    """MBTiles attribution is often HTML; show it as plain text."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip() or DEFAULT_ATTRIBUTION


class MapFile:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise MapFileError(f"Offline map file not found: {self.path}")
        # Read-only and immutable: the app never changes a map file.
        self.db = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True)
        try:
            rows = self.db.execute("SELECT name, value FROM metadata").fetchall()
        except sqlite3.DatabaseError as exc:
            self.close()
            raise MapFileError(f"Not an MBTiles map file ({exc}).") from exc
        self.metadata = {name: value for name, value in rows}
        fmt = self.metadata.get("format", "")
        if fmt in ("png", "jpg", "jpeg", "webp"):
            self.close()
            raise MapFileError("Raster (image) map files aren't supported yet; use a vector OpenMapTiles file.")
        if fmt != "pbf":
            self.close()
            raise MapFileError(f"Unsupported map file format {fmt!r}; expected vector tiles (pbf).")
        try:
            layers = {layer["id"] for layer in json.loads(self.metadata.get("json", "{}")).get("vector_layers", [])}
        except (ValueError, TypeError, KeyError):
            layers = set()
        if len(layers & OPENMAPTILES_LAYERS) < len(OPENMAPTILES_LAYERS) - 2:
            self.close()
            raise MapFileError("This isn't an OpenMapTiles vector map, which the app's map style needs.")
        self.layers = layers
        self.min_zoom = int(self.metadata.get("minzoom", 0))
        self.max_zoom = int(self.metadata.get("maxzoom", 14))
        try:
            west, south, east, north = (float(v) for v in self.metadata["bounds"].split(","))
        except (KeyError, ValueError):
            west, south, east, north = -180.0, -85.05, 180.0, 85.05
        self.bounds = (west, south, east, north)
        self.attribution = plain_attribution(self.metadata.get("attribution"))
        self.name = self.metadata.get("name") if self.metadata.get("name") not in (None, "OpenMapTiles") \
            else self.path.stem
        self._cache = OrderedDict()

    def close(self):
        self.db.close()

    @property
    def data_date(self):
        """When the map data was taken from OpenStreetMap, or else when the file was made."""
        for key in ("planetiler:osm:osmosisreplicationtime", "planetiler:buildtime"):
            value = self.metadata.get(key)
            if value:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc)

    def covers(self, lat, lon):
        west, south, east, north = self.bounds
        return south <= lat <= north and west <= lon <= east

    def tile(self, z, x, y):
        """Uncompressed vector tile bytes for XYZ tile coordinates, or None if the file has no such tile."""
        key = (z, x, y)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if not self.min_zoom <= z <= self.max_zoom:
            return None
        row = self.db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                              (z, x, (1 << z) - 1 - y)).fetchone()  # MBTiles rows count from the south.
        data = None
        if row and row[0]:
            data = bytes(row[0])
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            elif data[:1] == b"\x78":
                data = zlib.decompress(data)
        self._cache[key] = data
        if len(self._cache) > CACHE_TILES:
            self._cache.popitem(last=False)
        return data

    def summary(self):
        west, south, east, north = self.bounds
        return {"name": self.name, "path": str(self.path), "size": self.path.stat().st_size,
                "min_zoom": self.min_zoom, "max_zoom": self.max_zoom,
                "bounds": [round(v, 5) for v in (west, south, east, north)],
                "data_date": self.data_date.date().isoformat(), "attribution": self.attribution}
