import tempfile
import unittest
from pathlib import Path

from m9n.tilecache import MIN_LIFETIME_S, TileCache, expiry, http_date, is_image

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
NOW = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class ExpiryTests(unittest.TestCase):
    def test_headers_are_honoured(self):
        self.assertEqual(expiry({"Cache-Control": "public, max-age=3600"}, NOW), NOW + 3600)
        self.assertEqual(expiry({"cache-control": "max-age=0"}, NOW), NOW)  # Case-insensitive; revalidate at once.
        self.assertEqual(expiry({"Expires": http_date(NOW + 600)}, NOW), NOW + 600)
        self.assertEqual(expiry({"Cache-Control": "max-age=60", "Expires": http_date(NOW + 600)}, NOW), NOW + 60)

    def test_without_readable_headers_keep_seven_days(self):
        self.assertEqual(expiry({}, NOW), NOW + MIN_LIFETIME_S)
        self.assertEqual(expiry({"Expires": "not a date"}, NOW), NOW + MIN_LIFETIME_S)
        self.assertEqual(MIN_LIFETIME_S, 7 * 86400)

    def test_no_store_is_not_stored(self):
        self.assertIsNone(expiry({"Cache-Control": "no-store"}, NOW))

    def test_only_images_are_cached(self):
        self.assertTrue(is_image(PNG))
        self.assertTrue(is_image(b"\xff\xd8\xff\xe0rest"))
        self.assertFalse(is_image(b"<html>Blocked</html>"))
        self.assertFalse(is_image(b""))


class TileCacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.clock = Clock()
        self.cache = TileCache(self.dir.name, clock=self.clock)
        self.addCleanup(self.cache.close)

    def test_store_read_and_expire(self):
        self.assertIsNone(self.cache.get(15, 1, 2))
        self.assertTrue(self.cache.put(15, 1, 2, PNG, {"Cache-Control": "max-age=100", "ETag": '"abc"',
                                                       "Last-Modified": "Sun, 11 Oct 2026 12:00:00 GMT"}))
        entry = self.cache.get(15, 1, 2)
        self.assertEqual(entry.data, PNG)
        self.assertTrue(entry.fresh)
        self.clock.now += 101
        entry = self.cache.get(15, 1, 2)
        self.assertFalse(entry.fresh)
        self.assertEqual(entry.conditional_headers(), {"If-None-Match": '"abc"',
                                                       "If-Modified-Since": "Sun, 11 Oct 2026 12:00:00 GMT"})

    def test_revalidation_renews_the_lifetime(self):
        self.cache.put(15, 1, 2, PNG, {"Cache-Control": "max-age=10", "ETag": '"v1"'})
        self.clock.now += 20
        self.cache.revalidated(15, 1, 2, {"Cache-Control": "max-age=500", "ETag": '"v2"'})
        entry = self.cache.get(15, 1, 2)
        self.assertTrue(entry.fresh)
        self.assertEqual(entry.etag, '"v2"')

    def test_rejected_responses_are_not_stored(self):
        self.assertFalse(self.cache.put(15, 1, 2, b"<html>Access blocked</html>", {}))
        self.assertFalse(self.cache.put(15, 1, 2, PNG, {"Cache-Control": "no-store"}))
        self.assertEqual(self.cache.usage(), (0, 0))

    def test_least_recently_used_tiles_are_evicted(self):
        self.cache.set_limit(len(PNG) * 3 / (1024 * 1024))
        for x in range(3):
            self.clock.now += 1
            self.cache.put(15, x, 0, PNG, {})
        self.clock.now += 1
        self.cache.get(15, 0, 0)  # Tile 0 is now the most recently used.
        self.clock.now += 1
        self.cache.put(15, 3, 0, PNG, {})
        self.assertIsNone(self.cache.get(15, 1, 0))
        self.assertIsNotNone(self.cache.get(15, 0, 0))
        self.assertEqual(self.cache.usage(), (len(PNG) * 3, 3))
        self.assertFalse(self.cache.path(15, 1, 0).exists())

    def test_writes_are_atomic_and_missing_files_forgotten(self):
        self.cache.put(15, 1, 2, PNG, {})
        self.assertFalse(list(Path(self.dir.name).rglob("*.part")))
        self.cache.path(15, 1, 2).unlink()
        self.assertIsNone(self.cache.get(15, 1, 2))
        self.assertEqual(self.cache.usage(), (0, 0))

    def test_clear_and_sources_are_separate(self):
        other = TileCache(self.dir.name, source="other", clock=self.clock)
        self.addCleanup(other.close)
        self.cache.put(15, 1, 2, PNG, {})
        other.put(15, 1, 2, PNG, {})
        self.cache.clear()
        self.assertEqual(self.cache.usage(), (0, 0))
        self.assertEqual(other.usage(), (len(PNG), 1))
        self.assertFalse((Path(self.dir.name) / "osm").exists())

    def test_index_survives_reopening(self):
        self.cache.put(15, 1, 2, PNG, {})
        self.cache.close()
        reopened = TileCache(self.dir.name, clock=self.clock)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get(15, 1, 2).data, PNG)


if __name__ == "__main__":
    unittest.main()
