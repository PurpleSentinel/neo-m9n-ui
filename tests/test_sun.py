import unittest
from datetime import datetime, timedelta, timezone

from m9n import sun

UTC = timezone.utc
# (name, lat, lon, UTC time on the day, sunrise UTC, sunset UTC). References from the PyEphem
# ephemeris (sun's centre at -0°50'), which agree with NOAA's solar calculator.
REFERENCE = [
    ("London midsummer", 51.5074, -0.1278, datetime(2026, 6, 21, 12, tzinfo=UTC), "03:43", "20:22"),
    ("London midwinter", 51.5074, -0.1278, datetime(2026, 12, 21, 12, tzinfo=UTC), "08:04", "15:53"),
    ("Sydney midsummer", -33.8688, 151.2093, datetime(2026, 12, 21, 2, tzinfo=UTC), "18:41", "09:05"),
    ("Honolulu new year", 21.3069, -157.8583, datetime(2026, 1, 1, 22, tzinfo=UTC), "17:09", "04:01"),
    ("Quito equinox", -0.1807, -78.4678, datetime(2026, 3, 20, 17, tzinfo=UTC), "11:18", "23:24"),
    ("Isle of Man (demo)", 54.1667, -4.4503, datetime(2026, 10, 11, 12, tzinfo=UTC), "06:38", "17:30"),
]


def minutes(text):
    hours, mins = text.split(":")
    return int(hours) * 60 + int(mins)


class SunTests(unittest.TestCase):
    def assertClose(self, when, expected, name):
        got = when.hour * 60 + when.minute + when.second / 60
        diff = min(abs(got - minutes(expected)), 1440 - abs(got - minutes(expected)))
        self.assertLessEqual(diff, 1, f"{name}: {when:%H:%M} vs {expected}")

    def test_reference_sunrise_and_sunset(self):
        for name, lat, lon, when, rise, set_ in REFERENCE:
            with self.subTest(name):
                times = sun.day(when, lat, lon)
                self.assertClose(times["sunrise"], rise, name)
                self.assertClose(times["sunset"], set_, name)
                self.assertIsNone(times["polar"])
                self.assertEqual(times["day_length"], times["sunset"] - times["sunrise"])
                self.assertLess(times["dawn"], times["sunrise"])
                self.assertGreater(times["dusk"], times["sunset"])

    def test_events_belong_to_the_local_solar_day(self):
        # Sydney's morning is the previous UTC day; Honolulu's evening is the next one.
        sydney = sun.day(datetime(2026, 12, 21, 2, tzinfo=UTC), -33.8688, 151.2093)
        self.assertEqual(sydney["sunrise"].date().isoformat(), "2026-12-20")
        self.assertEqual(sydney["sunset"].date().isoformat(), "2026-12-21")
        honolulu = sun.day(datetime(2026, 1, 1, 22, tzinfo=UTC), 21.3069, -157.8583)
        self.assertEqual(honolulu["sunset"].date().isoformat(), "2026-01-02")
        # Any time during the same local day gives the same answer.
        late = sun.day(datetime(2026, 1, 2, 8, tzinfo=UTC), 21.3069, -157.8583)
        self.assertEqual(late["sunrise"], honolulu["sunrise"])

    def test_polar_day_and_night(self):
        summer = sun.day(datetime(2026, 6, 21, 12, tzinfo=UTC), 69.6492, 18.9553)  # Tromsø.
        self.assertEqual((summer["polar"], summer["sunrise"], summer["sunset"]), ("day", None, None))
        self.assertEqual(summer["day_length"], timedelta(hours=24))
        winter = sun.day(datetime(2026, 12, 21, 12, tzinfo=UTC), 69.6492, 18.9553)
        self.assertEqual((winter["polar"], winter["sunrise"], winter["sunset"]), ("night", None, None))
        self.assertEqual(winter["day_length"], timedelta(0))
        self.assertIsNotNone(winter["dawn"])  # Civil twilight still happens around noon.
        pole = sun.day(datetime(2026, 12, 21, 12, tzinfo=UTC), 89.9, 0)
        self.assertEqual((pole["polar"], pole["dawn"], pole["dusk"]), ("night", None, None))

    def test_elevation_and_phase(self):
        noon = sun.elevation(datetime(2026, 6, 21, 12, 2, tzinfo=UTC), 51.5074, -0.1278)
        self.assertAlmostEqual(noon, 90 - 51.5074 + 23.44, delta=0.2)  # Midsummer noon in London.
        self.assertLess(sun.elevation(datetime(2026, 6, 21, 0, tzinfo=UTC), 51.5074, -0.1278), -10)
        rise = sun.day(datetime(2026, 3, 20, 12, tzinfo=UTC), 0.0, 0.0)["sunrise"]
        self.assertAlmostEqual(sun.elevation(rise, 0.0, 0.0), sun.SUNRISE_DEG, delta=0.05)
        self.assertEqual([sun.phase(e) for e in (10, -0.5, -3, -9, -15, -30)],
                         ["day", "day", "civil twilight", "nautical twilight", "astronomical twilight", "night"])

    def test_dark_by_sun_switches_at_civil_dusk_without_flicker(self):
        night = sun.DarkBySun()
        self.assertIsNone(night.dark)
        self.assertFalse(night.update(-5.9))  # First reading: plain threshold at -6°.
        self.assertFalse(night.update(-6.3))  # Within the hysteresis band: no change.
        self.assertTrue(night.update(-6.6))
        self.assertTrue(night.update(-5.7))  # Back up a little: still dark.
        self.assertFalse(night.update(-5.4))
        self.assertTrue(sun.DarkBySun().update(-6.1))
        # A dusk sequence of noisy readings switches exactly once.
        night, switches = sun.DarkBySun(), 0
        last = night.update(-4.0)
        for elevation in (-5.6, -6.2, -5.8, -6.4, -6.1, -6.6, -6.3, -6.7, -7.5):
            switches += night.update(elevation) != last
            last = night.dark
        self.assertEqual((switches, night.dark), (1, True))

    def test_gnss_datetime(self):
        pvt = {"utc": "2026-10-11T12:00:05Z", "utc_valid": True}
        self.assertEqual(sun.gnss_datetime(pvt), datetime(2026, 10, 11, 12, 0, 5, tzinfo=UTC))
        self.assertIsNone(sun.gnss_datetime({**pvt, "utc_valid": False}))
        rmc = {"utc": "235959.50", "date": "111026"}
        self.assertEqual(sun.gnss_datetime(rmc), datetime(2026, 10, 11, 23, 59, 59, tzinfo=UTC))
        self.assertIsNone(sun.gnss_datetime({"utc": "120000.00"}))  # GGA: time but no date.
        self.assertIsNone(sun.gnss_datetime({"utc": "120000.00", "date": "321326"}))
        self.assertIsNone(sun.gnss_datetime(None))


if __name__ == "__main__":
    unittest.main()
