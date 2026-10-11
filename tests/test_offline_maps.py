import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mapfile  # noqa: E402
from m9n.mbtiles import DEFAULT_ATTRIBUTION, OPENMAPTILES_LAYERS, MapFile, MapFileError, plain_attribution  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make-offline-map.sh"


class MapFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def path(self, name="map.mbtiles", **kwargs):
        return mapfile.write(os.path.join(self.dir.name, name), **kwargs)

    def test_reads_metadata_and_tiles(self):
        map_file = MapFile(self.path())
        self.addCleanup(map_file.close)
        summary = map_file.summary()
        self.assertEqual((summary["name"], summary["min_zoom"], summary["max_zoom"]), ("Test Map", 0, 14))
        self.assertEqual(summary["data_date"], "2026-10-08")
        self.assertEqual(summary["attribution"], "© OpenMapTiles © OpenStreetMap contributors")
        x, y = mapfile.tile_range(14)[0]
        self.assertEqual(map_file.tile(14, x, y), mapfile.demo_tile())  # Gzip removed, TMS row flipped.
        self.assertIsNone(map_file.tile(14, 0, 0))  # Not in the file.
        self.assertIsNone(map_file.tile(15, x * 2, y * 2))  # Beyond its zoom: the renderer over-zooms z14.
        self.assertTrue(map_file.covers(54.17, -4.45))
        self.assertFalse(map_file.covers(51.5, -0.12))

    def test_plain_tiles_and_file_name_fallback(self):
        path = self.path(metadata={"name": "OpenMapTiles"})
        db = sqlite3.connect(path)
        db.execute("UPDATE tiles SET tile_data=?", (mapfile.demo_tile(),))
        db.commit()
        db.close()
        map_file = MapFile(path)
        self.addCleanup(map_file.close)
        self.assertEqual(map_file.name, "map")  # Planetiler's generic name gives way to the file name.
        x, y = mapfile.tile_range(14)[0]
        self.assertEqual(map_file.tile(14, x, y), mapfile.demo_tile())

    def test_unusable_files_explain_why(self):
        cases = {
            "missing": (os.path.join(self.dir.name, "nope.mbtiles"), "not found"),
            "not sqlite": (self._write_bytes("junk.mbtiles", b"not a database at all" * 10), "Not an MBTiles"),
            "raster": (self.path("raster.mbtiles", fmt="png"), "Raster"),
            "other format": (self.path("geojson.mbtiles", fmt="geojson"), "Unsupported"),
            "other schema": (self.path("shortbread.mbtiles", layers=["streets", "water_polygons"]), "OpenMapTiles"),
        }
        for label, (path, message) in cases.items():
            with self.subTest(label), self.assertRaises(MapFileError) as caught:
                MapFile(path)
            self.assertIn(message, str(caught.exception))

    def test_attribution_is_plain_text(self):
        self.assertEqual(plain_attribution("<a href='x'>&copy; A</a>  <b>B</b>"), "© A B")
        self.assertEqual(plain_attribution(""), DEFAULT_ATTRIBUTION)

    def _write_bytes(self, name, data):
        path = Path(self.dir.name) / name
        path.write_bytes(data)
        return str(path)


class StyleTests(unittest.TestCase):
    def test_both_variants_draw_the_same_openmaptiles_layers(self):
        try:
            from m9n.gui import mapstyle
        except ImportError as exc:  # Only json is needed, but keep the GUI package optional here.
            self.skipTest(str(exc))
        light, dark = (json.loads(mapstyle.style(variant)) for variant in ("light", "dark"))
        self.assertEqual([layer["id"] for layer in light["layers"]], [layer["id"] for layer in dark["layers"]])
        source_layers = {layer["source-layer"] for layer in light["layers"] if "source-layer" in layer}
        self.assertLessEqual(source_layers, OPENMAPTILES_LAYERS)
        self.assertEqual(light["layers"][0]["type"], "background")
        self.assertTrue(light["sources"]["openmaptiles"]["tiles"][0].startswith("file:///"))  # Never the network.
        self.assertEqual(json.loads(mapstyle.style("light", 12))["sources"]["openmaptiles"]["maxzoom"], 12)
        for variant in mapstyle.PALETTES.values():
            self.assertEqual(set(variant), set(mapstyle.PALETTES["light"]))


class ScriptTests(unittest.TestCase):
    def run_script(self, *args):
        env = {**os.environ, "HOME": tempfile.gettempdir(), "XDG_CACHE_HOME": tempfile.gettempdir()}
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)

    def test_dry_run_shows_the_plan_and_downloads_nothing(self):
        result = self.run_script("--dry-run", "isle-of-man", "--output", "/tmp/iom-test.mbtiles")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("ghcr.io/onthegomap/planetiler:0.10.2", out)
        for host in ("download.geofabrik.de", "osmdata.openstreetmap.de", "naciscdn.org", "github.com"):
            self.assertIn(host, out)
        self.assertNotIn("tile.openstreetmap.org", out)
        self.assertIn("--area=isle-of-man", out)
        self.assertIn("Output:          /tmp/iom-test.mbtiles", out)
        self.assertIn("Dry run: nothing was downloaded", out)
        self.assertFalse(Path("/tmp/iom-test.mbtiles").exists())

    def test_rejects_bad_input(self):
        self.assertEqual(self.run_script("Isle of Man").returncode, 2)
        self.assertEqual(self.run_script().returncode, 2)
        self.assertEqual(self.run_script("--frobnicate", "x").returncode, 2)
        self.assertEqual(self.run_script("--help").returncode, 0)
        self.assertIn("Geofabrik", self.run_script("--list").stdout)


if __name__ == "__main__":
    unittest.main()
