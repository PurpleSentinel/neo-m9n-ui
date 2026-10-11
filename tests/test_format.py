import unittest

from m9n.gui import format as fmt


class FormatTests(unittest.TestCase):
    def test_missing_values_are_unknown(self):
        for text in (fmt.number(None), fmt.integer(None), fmt.seconds(None), fmt.speed(None),
                     fmt.coordinate(None, "N", "S"), fmt.utc(None), fmt.utc({"utc": None})):
            self.assertEqual(text, "unknown")

    def test_numbers_and_units(self):
        self.assertEqual(fmt.number(1.456, 1, "m"), "1.5 m")
        self.assertEqual(fmt.number(0.0), "0.00")
        self.assertEqual(fmt.integer(23631), "23,631")
        self.assertEqual(fmt.seconds(0.906), "0.9 s")
        self.assertEqual(fmt.seconds(171.2), "2.9 min")
        self.assertEqual(fmt.speed(1.25), "1.25 m/s (4.5 km/h)")

    def test_hemispheres(self):
        self.assertEqual(fmt.coordinate(54.166707, "N", "S"), "54.1667070° N")
        self.assertEqual(fmt.coordinate(-4.450342, "E", "W"), "4.4503420° W")
        self.assertEqual(fmt.coordinate(0.0, "E", "W"), "0.0000000° E")
        self.assertEqual(fmt.coordinates_text({"latitude": 54.166707, "longitude": -4.450342}), "54.1667070, -4.4503420")
        self.assertIsNone(fmt.coordinates_text({"latitude": None, "longitude": 1}))
        self.assertIsNone(fmt.coordinates_text(None))

    def test_fix_state_never_reports_stale_as_valid(self):
        self.assertEqual(fmt.fix_state(None)[0], "waiting")
        self.assertEqual(fmt.fix_state({"fresh": False, "fix_ok": True})[0], "stale")
        self.assertEqual(fmt.fix_state({"fresh": True, "fix_ok": True})[0], "valid")
        self.assertEqual(fmt.fix_state({"fresh": True, "fix_ok": False})[0], "none")

    def test_utc_validity_is_annotated(self):
        nav = {"utc": "2026-10-11T12:00:00Z", "utc_valid": True, "utc_fully_resolved": True}
        self.assertEqual(fmt.utc(nav), "2026-10-11T12:00:00Z")
        self.assertIn("not fully resolved", fmt.utc({**nav, "utc_fully_resolved": False}))
        self.assertIn("not valid", fmt.utc({**nav, "utc_valid": False}))
        self.assertEqual(fmt.utc({"utc": "120000.00"}), "120000.00")  # NMEA time-of-day only.


if __name__ == "__main__":
    unittest.main()
