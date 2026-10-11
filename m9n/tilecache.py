"""Disk cache for online map tiles, following the OpenStreetMap tile usage policy.

Tiles are kept so repeat views do not download them again: the server's caching
headers (Cache-Control, Expires, ETag, Last-Modified) are honoured, with at least
seven days when the server gives none, and expired tiles are revalidated with a
conditional request. The cache is only ever read while the map is online; it is not
an offline map (offline use of OpenStreetMap tiles is not permitted).

GTK-free so the rules can be unit-tested. The index is a small SQLite database; the
tiles themselves are files, written atomically.
"""
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from email.utils import format_datetime, parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path

MIN_LIFETIME_S = 7 * 86400  # Policy: cache for at least 7 days if the headers can't be read.
DEFAULT_LIMIT_MB = 200
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "neo-m9n" / "tiles"


def is_image(data):
    return any(bytes(data[:len(magic)]) == magic for magic in IMAGE_MAGIC)


def _header(headers, name):
    """Case-insensitive lookup in a plain dict of response headers."""
    name = name.lower()
    return next((value for key, value in headers.items() if key.lower() == name), None)


def expiry(headers, now):
    """When a tile from a response with these headers expires; None if it must not be stored."""
    control = (_header(headers, "Cache-Control") or "").lower()
    if "no-store" in control:
        return None
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", control)
    if match:
        return now + int(match.group(1))
    expires = _header(headers, "Expires")
    if expires:
        try:
            return parsedate_to_datetime(expires).timestamp()
        except (TypeError, ValueError):
            pass  # An unreadable date is treated like no header at all.
    return now + MIN_LIFETIME_S


@dataclass
class Entry:
    data: bytes
    fresh: bool
    etag: str | None
    modified: str | None

    def conditional_headers(self):
        """Headers for revalidating this tile once it has expired."""
        headers = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.modified:
            headers["If-Modified-Since"] = self.modified
        return headers


class TileCache:
    def __init__(self, root=None, source="osm", limit_mb=DEFAULT_LIMIT_MB, clock=time.time):
        self.root = Path(root or cache_root())
        self.source = source
        self.limit_bytes = int(limit_mb * 1024 * 1024)
        self.clock = clock
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "index.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS tiles (source TEXT, z INT, x INT, y INT, size INT, used REAL,"
                        " expires REAL, etag TEXT, modified TEXT, PRIMARY KEY (source, z, x, y))")
        self.db.commit()

    def close(self):
        self.db.close()

    def path(self, z, x, y):
        return self.root / self.source / str(z) / str(x) / f"{y}.tile"

    def get(self, z, x, y):
        """The cached tile, marked fresh or expired, or None."""
        row = self.db.execute("SELECT expires, etag, modified FROM tiles WHERE source=? AND z=? AND x=? AND y=?",
                              (self.source, z, x, y)).fetchone()
        if row is None:
            return None
        try:
            data = self.path(z, x, y).read_bytes()
        except OSError:
            self._forget(z, x, y)
            return None
        now = self.clock()
        self.db.execute("UPDATE tiles SET used=? WHERE source=? AND z=? AND x=? AND y=?", (now, self.source, z, x, y))
        self.db.commit()
        return Entry(data, now < row[0], row[1], row[2])

    def put(self, z, x, y, data, headers):
        """Store a downloaded tile; returns False if it isn't an image or must not be stored."""
        until = expiry(headers, self.clock())
        if until is None or not is_image(data):
            return False
        path = self.path(z, x, y)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(bytes(data))
        os.replace(temporary, path)
        self.db.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (self.source, z, x, y, len(data), self.clock(), until,
                         _header(headers, "ETag"), _header(headers, "Last-Modified")))
        self.db.commit()
        self.evict()
        return True

    def revalidated(self, z, x, y, headers):
        """A 304 Not Modified: the cached tile is current again for the new lifetime."""
        until = expiry(headers, self.clock())
        if until is None:
            self._forget(z, x, y)
            return
        etag = _header(headers, "ETag")
        self.db.execute("UPDATE tiles SET expires=?, used=?, etag=COALESCE(?, etag) WHERE source=? AND z=? AND x=? "
                        "AND y=?", (until, self.clock(), etag, self.source, z, x, y))
        self.db.commit()

    def usage(self):
        """(bytes, tiles) stored for this source."""
        size, count = self.db.execute("SELECT COALESCE(SUM(size), 0), COUNT(*) FROM tiles WHERE source=?",
                                      (self.source,)).fetchone()
        return size, count

    def set_limit(self, limit_mb):
        self.limit_bytes = int(limit_mb * 1024 * 1024)
        self.evict()

    def evict(self):
        """Remove least recently used tiles until the cache fits its limit."""
        size, _ = self.usage()
        if size <= self.limit_bytes:
            return
        for z, x, y, tile_size in self.db.execute(
                "SELECT z, x, y, size FROM tiles WHERE source=? ORDER BY used", (self.source,)).fetchall():
            self._forget(z, x, y, commit=False)
            size -= tile_size
            if size <= self.limit_bytes:
                break
        self.db.commit()

    def clear(self):
        self.db.execute("DELETE FROM tiles WHERE source=?", (self.source,))
        self.db.commit()
        shutil.rmtree(self.root / self.source, ignore_errors=True)

    def _forget(self, z, x, y, commit=True):
        self.path(z, x, y).unlink(missing_ok=True)
        self.db.execute("DELETE FROM tiles WHERE source=? AND z=? AND x=? AND y=?", (self.source, z, x, y))
        if commit:
            self.db.commit()


def http_date(timestamp):
    return format_datetime(datetime.fromtimestamp(timestamp, timezone.utc), usegmt=True)
