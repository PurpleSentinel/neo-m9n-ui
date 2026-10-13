import math
import unittest

from m9n import coords, demo

# Reference values from PROJ 9.8 (EPSG:4326 -> EPSG:27700 through the OSTN15 grid, and -> UTM zones).
BNG_REFERENCE = {
    "Demo fix (demo.ORIGIN), Douglas": ((54.1667070, -4.4503420), (240131.690, 477318.043)),
    "Isle of Man, the issue's example": ((54.16712, -4.48134), (238110.039, 477434.590)),
    "London Eye": ((51.5033, -0.1196), (530607.757, 179938.557)),
    "Ben Nevis": ((56.7969, -5.0036), (216671.111, 771289.240)),
    "Lerwick, Shetland": ((60.1550, -1.1450), (447567.818, 1141474.523)),
    "St Mary's, Scilly": ((49.9140, -6.3150), (90346.921, 10463.353)),
    "Lowestoft Ness": ((52.4810, 1.7630), (655609.726, 293663.777)),
    "Dover": ((51.1290, 1.3220), (632529.989, 141882.813)),
    "St Kilda": ((57.8130, -8.5760), (9733.044, 899376.738)),
    "Muckle Flugga": ((60.8560, -0.8850), (460679.610, 1219757.935)),
    "Mull of Kintyre": ((55.3110, -5.8000), (158954.076, 608459.251)),
}
UTM_REFERENCE = [
    ((54.16712, -4.48134), (30, "U", 403291.5570, 6003129.4109)),
    ((-33.8568, 151.2153), (56, "H", 334900.5697, 6252288.7529)),  # Southern hemisphere.
    ((40.7128, -74.0060), (18, "T", 583959.3723, 4507350.9982)),
    ((60.0, 5.0), (32, "V", 276979.9264, 6658157.2024)),  # Norway: 32V, not 31V.
    ((78.22, 15.65), (33, "X", 514813.5273, 8683004.1533)),  # Svalbard.
    ((0.0, 0.0), (31, "N", 166021.4431, 0.0)),
    ((-0.0001, -0.0001), (30, "M", 833967.4140, 9999988.9317)),
    ((-79.9, -70.0), (19, "C", 480423.3890, 1129407.4826)),
]


class DegreeTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(coords.decimal(54.16712, "N", "S"), "54.1671200° N")
        self.assertEqual(coords.degrees_minutes(54.16712, "N", "S"), "54° 10.027′ N")
        self.assertEqual(coords.degrees_minutes(-4.48134, "E", "W"), "4° 28.880′ W")
        self.assertEqual(coords.degrees_minutes_seconds(54.16712, "N", "S"), "54° 10′ 01.6″ N")
        self.assertEqual(coords.degrees_minutes_seconds(-4.48134, "E", "W"), "4° 28′ 52.8″ W")
        self.assertEqual(coords.degrees_minutes(-4.14, "E", "W"), "4° 08.400′ W")  # Minutes padded.

    def test_rounding_carries(self):
        self.assertEqual(coords.degrees_minutes_seconds(1 + 59 / 60 + 59.96 / 3600, "N", "S"), "2° 00′ 00.0″ N")
        self.assertEqual(coords.degrees_minutes_seconds(1 + 10 / 60 + 59.99 / 3600, "N", "S"), "1° 11′ 00.0″ N")
        self.assertEqual(coords.degrees_minutes(9 + 59.9996 / 60, "E", "W"), "10° 00.000′ E")
        self.assertEqual(coords.decimal(179.99999999, "E", "W"), "180.0000000° E")

    def test_zero_and_hemispheres(self):
        self.assertEqual(coords.decimal(0.0, "N", "S"), "0.0000000° N")
        self.assertEqual(coords.decimal(-0.0, "E", "W"), "0.0000000° E")
        self.assertEqual(coords.degrees_minutes_seconds(-0.000001, "N", "S"), "0° 00′ 00.0″ N")  # Rounds to 0.
        self.assertEqual(coords.degrees_minutes_seconds(-0.0001, "N", "S"), "0° 00′ 00.4″ S")
        self.assertEqual(coords.degrees_minutes(-33.8568, "N", "S"), "33° 51.408′ S")
        self.assertEqual(coords.degrees_minutes(151.2153, "E", "W"), "151° 12.918′ E")


class NationalGridTests(unittest.TestCase):
    def test_os_worked_example(self):
        # Ordnance Survey's guide, Transverse Mercator example: OSGB36 52°39′27.2531″N 1°43′4.5177″E.
        lat = 52 + 39 / 60 + 27.2531 / 3600
        lon = 1 + 43 / 60 + 4.5177 / 3600
        easting, northing = coords.national_grid(lat, lon)
        self.assertAlmostEqual(easting, 651409.903, places=2)
        self.assertAlmostEqual(northing, 313177.270, places=2)

    def test_within_five_metres_of_ostn15(self):
        for name, ((lat, lon), (easting, northing)) in BNG_REFERENCE.items():
            with self.subTest(name):
                found = coords.bng(lat, lon)
                self.assertIsNotNone(found)
                self.assertLess(math.dist(found, (easting, northing)), 5.0)

    def test_grid_references(self):
        self.assertEqual(coords.grid_reference(238110.039, 477434.590), "SC 38110 77434")
        self.assertEqual(coords.grid_reference(238110.039, 477434.590, figures=6), "SC 381 774")
        self.assertEqual(coords.grid_reference(530607.757, 179938.557), "TQ 30607 79938")
        self.assertEqual(coords.grid_reference(447567.818, 1141474.523), "HU 47567 41474")
        self.assertEqual(coords.grid_reference(90346.921, 10463.353), "SV 90346 10463")
        self.assertEqual(coords.grid_reference(9733.044, 899376.738), "NF 09733 99376")
        self.assertEqual(coords.grid_reference(651409.9, 313177.3, figures=6), "TG 514 131")
        self.assertEqual(coords.grid_reference(0, 0), "SV 00000 00000")

    def test_outside_the_grid(self):
        for name, (lat, lon) in {"Belfast": (54.597, -5.93), "Dublin": (53.35, -6.26), "Rathlin": (55.29, -6.2),
                                 "Calais": (50.951, 1.859), "Jersey": (49.19, -2.11), "Paris": (48.8566, 2.3522),
                                 "New York": (40.7128, -74.006), "Equator": (0.0, 0.0)}.items():
            with self.subTest(name):
                self.assertIsNone(coords.bng(lat, lon))
                self.assertEqual(coords.rows(lat, lon, "bng"), [("Grid reference", coords.OUTSIDE_BNG)])


class UtmTests(unittest.TestCase):
    def test_against_proj(self):
        for (lat, lon), (zone, band, easting, northing) in UTM_REFERENCE:
            with self.subTest(lat=lat, lon=lon):
                found = coords.utm(lat, lon)
                self.assertEqual(found[:2], (zone, band))
                self.assertAlmostEqual(found[2], easting, places=2)
                self.assertAlmostEqual(found[3], northing, places=2)
        self.assertEqual(coords.utm_text(54.16712, -4.48134), "30U 403291 6003129")
        self.assertEqual(coords.utm_text(-33.8568, 151.2153), "56H 334900 6252288")

    def test_zones_and_exceptions(self):
        self.assertEqual(coords.utm_zone(54.0, -6.0001), (29, "U"))
        self.assertEqual(coords.utm_zone(54.0, -6.0), (30, "U"))  # A boundary belongs to the zone to its east.
        self.assertEqual(coords.utm_zone(55.99, 4.0), (31, "U"))
        self.assertEqual(coords.utm_zone(56.0, 4.0), (32, "V"))  # Norway.
        self.assertEqual(coords.utm_zone(56.0, 2.99), (31, "V"))
        self.assertEqual(coords.utm_zone(64.0, 4.0), (31, "W"))
        self.assertEqual(coords.utm_zone(75.0, 8.99), (31, "X"))  # Svalbard: 31X, 33X, 35X, 37X.
        self.assertEqual(coords.utm_zone(75.0, 9.0), (33, "X"))
        self.assertEqual(coords.utm_zone(75.0, 21.0), (35, "X"))
        self.assertEqual(coords.utm_zone(75.0, 33.0), (37, "X"))
        self.assertEqual(coords.utm_zone(75.0, 42.0), (38, "X"))
        self.assertEqual(coords.utm_zone(84.0, 0.0), (31, "X"))
        self.assertEqual(coords.utm_zone(-80.0, 179.9999), (60, "C"))
        self.assertEqual(coords.utm_zone(0.0, 180.0), (1, "N"))
        self.assertIsNone(coords.utm_zone(84.01, 0.0))
        self.assertIsNone(coords.utm_zone(-80.01, 0.0))
        self.assertEqual(coords.utm_text(89.0, 0.0), coords.OUTSIDE_UTM)


class PositionTests(unittest.TestCase):
    def test_demo_fix_in_every_format(self):
        lat, lon = demo.ORIGIN
        self.assertEqual(coords.one_line(lat, lon, "decimal"), "54.1667070° N, 4.4503420° W")
        self.assertEqual(coords.one_line(lat, lon, "dm"), "54° 10.002′ N, 4° 27.021′ W")
        self.assertEqual(coords.one_line(lat, lon, "dms"), "54° 10′ 00.1″ N, 4° 27′ 01.2″ W")
        self.assertEqual(coords.rows(lat, lon, "bng"), [("Grid reference", "SC 40131 77319"),
                                                        ("Short form", "SC 401 773")])  # OSTN15: SC 40131 77318.
        self.assertEqual(coords.one_line(lat, lon, "utm"), "30U 405314 6003041")

    def test_rows_and_one_line(self):
        lat, lon = 54.16712, -4.48134
        self.assertEqual(coords.rows(lat, lon, "decimal"), [("Latitude", "54.1671200° N"),
                                                            ("Longitude", "4.4813400° W")])
        self.assertEqual(coords.rows(lat, lon, "nonsense"), coords.rows(lat, lon, "decimal"))
        self.assertEqual(coords.one_line(lat, lon, "dm"), "54° 10.027′ N, 4° 28.880′ W")
        self.assertEqual(coords.one_line(lat, lon, "dms"), "54° 10′ 01.6″ N, 4° 28′ 52.8″ W")
        self.assertEqual(coords.rows(lat, lon, "utm"), [("UTM (WGS 84)", "30U 403291 6003129")])
        self.assertEqual(coords.one_line(lat, lon, "utm"), "30U 403291 6003129")
        grid = coords.rows(lat, lon, "bng")
        self.assertEqual([title for title, _ in grid], ["Grid reference", "Short form"])
        self.assertRegex(grid[0][1], r"^SC 381\d\d 774\d\d$")  # Helmert: within metres of SC 38110 77434.
        self.assertRegex(grid[1][1], r"^SC 381 774$")
        self.assertEqual(coords.one_line(lat, lon, "bng"), grid[0][1])
        self.assertEqual(coords.one_line(40.7, -74.0, "bng"), coords.OUTSIDE_BNG)


if __name__ == "__main__":
    unittest.main()
