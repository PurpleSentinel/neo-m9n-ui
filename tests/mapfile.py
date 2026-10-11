"""Synthetic offline map files for tests: tiny OpenMapTiles-schema MBTiles, written from scratch.

Each zoom-14 tile around the demo loop holds a water polygon and a road line, hand-encoded
as Mapbox Vector Tile protobuf, so tests need no real map data and no network.
"""
import gzip
import json
import math
import sqlite3

LAYERS = ["water", "landuse", "landcover", "park", "waterway", "transportation", "building", "boundary", "place",
          "transportation_name", "water_name", "poi"]
BOUNDS = (-4.47, 54.16, -4.43, 54.18)  # Around the demo "Moving vehicle" loop.


def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field(number, wire, payload):
    key = varint(number << 3 | wire)
    if wire == 0:
        return key + varint(payload)
    return key + varint(len(payload)) + payload


def zigzag(n):
    return (n << 1) ^ (n >> 31)


def packed(values):
    return b"".join(varint(v) for v in values)


def feature(geometry_type, commands, tags=()):
    return field(2, 2, packed(tags)) + field(3, 0, geometry_type) + field(4, 2, packed(commands))


def layer(name, features, keys=(), values=()):
    body = field(15, 0, 2) + field(1, 2, name.encode())
    for item in features:
        body += field(2, 2, item)
    for key in keys:
        body += field(3, 2, key.encode())
    for value in values:
        body += field(4, 2, field(1, 2, value.encode()))
    return field(3, 2, body + field(5, 0, 4096))


def demo_tile():
    square = [9, zigzag(0), zigzag(0), 26, zigzag(4096), zigzag(0), zigzag(0), zigzag(2048), zigzag(-4096), zigzag(0),
              15]  # The north half of the tile: water.
    road = [9, zigzag(0), zigzag(3000), 10, zigzag(4096), zigzag(0)]  # A road across the south half.
    return (layer("water", [feature(3, square, (0, 0))], ["class"], ["sea"])
            + layer("transportation", [feature(2, road, (0, 0))], ["class"], ["primary"]))


def tile_range(z, bounds=BOUNDS):
    west, south, east, north = bounds
    n = 2 ** z

    def xy(lat, lon):
        return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)

    (x0, y0), (x1, y1) = xy(north, west), xy(south, east)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def write(path, fmt="pbf", layers=LAYERS, metadata=None, zoom=14):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    db.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
    meta = {"name": "Test Map", "format": fmt, "minzoom": "0", "maxzoom": str(zoom),
            "bounds": ",".join(str(v) for v in BOUNDS),
            "attribution": '<a href="https://www.openmaptiles.org/">&copy; OpenMapTiles</a> '
                           '<a href="https://www.openstreetmap.org/copyright">&copy; OpenStreetMap contributors</a>',
            "json": json.dumps({"vector_layers": [{"id": name} for name in layers]}),
            "planetiler:osm:osmosisreplicationtime": "2026-10-08T20:21:06Z", **(metadata or {})}
    db.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    data = gzip.compress(demo_tile())
    db.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                   [(zoom, x, (1 << zoom) - 1 - y, data) for x, y in tile_range(zoom)])
    db.commit()
    db.close()
    return path
