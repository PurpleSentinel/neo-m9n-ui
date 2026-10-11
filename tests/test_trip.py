import math
import unittest

from m9n.demo import drive, sample
from m9n.session import Session
from m9n.trip import Trip, distance_m

LAT, LON = 54.0, -4.0
M_PER_DEG_LAT = 111195.0


def pvt(itow_s, north_m=0.0, east_m=0.0, speed=10.0, fix_ok=True, fresh=True):
    return {"itow_ms": round(itow_s * 1000), "fix_ok": fix_ok, "fresh": fresh, "speed_m_s": speed,
            "latitude": LAT + north_m / M_PER_DEG_LAT,
            "longitude": LON + east_m / (M_PER_DEG_LAT * math.cos(math.radians(LAT)))}


class DistanceTests(unittest.TestCase):
    def test_haversine_against_known_distances(self):
        self.assertAlmostEqual(distance_m(0, 0, 1, 0), 111195, delta=1)
        self.assertAlmostEqual(distance_m(0, 0, 0, 1), 111195, delta=1)  # On the equator.
        self.assertAlmostEqual(distance_m(51.5007, -0.1246, 48.8584, 2.2945), 340_600, delta=1500)  # London-Paris
        self.assertEqual(distance_m(LAT, LON, LAT, LON), 0)


class TripTests(unittest.TestCase):
    def setUp(self):
        self.trip = Trip()

    def feed(self, *navs):
        return [self.trip.update(nav) for nav in navs]

    def test_straight_drive(self):
        self.feed(*(pvt(t, north_m=10 * t) for t in range(61)))
        self.assertAlmostEqual(self.trip.distance_m, 600, delta=1)
        self.assertEqual(self.trip.moving_s, 60)
        self.assertAlmostEqual(self.trip.average_speed_m_s, 10, delta=0.02)
        self.assertEqual(self.trip.max_speed_m_s, 10)
        summary = self.trip.summary()
        self.assertEqual((summary["distance_m"], summary["moving_s"], summary["epochs"]), (600.0, 60.0, 61))

    def test_stationary_jitter_is_not_travel(self):
        wobble = [(3, 0), (-2, 4), (1, -3), (0, 2)] * 10
        self.feed(*(pvt(t, n, e, speed=0.1) for t, (n, e) in enumerate(wobble)))
        self.assertEqual(self.trip.distance_m, 0)
        self.assertEqual(self.trip.moving_s, 0)
        self.assertIsNone(self.trip.average_speed_m_s)

    def test_stop_then_go_counts_only_moving_time(self):
        self.feed(pvt(0, 0), pvt(1, 10), pvt(2, 20, speed=0.0), pvt(30, 20.5, speed=0.1), pvt(31, 30), pvt(32, 40))
        self.assertAlmostEqual(self.trip.distance_m, 40, delta=0.6)
        self.assertEqual(self.trip.moving_s, 4)

    def test_duplicate_epoch_counted_once(self):
        self.assertEqual(self.feed(pvt(0, 0), pvt(1, 10), pvt(1, 10)), [True, True, False])
        self.assertAlmostEqual(self.trip.distance_m, 10, delta=0.1)

    def test_invalid_or_stale_fixes_are_ignored(self):
        self.assertEqual(self.feed(pvt(0, 0), pvt(1, 500, fix_ok=False), pvt(2, 500, fresh=False), None,
                                   {"fix_ok": True, "fresh": True, "latitude": None}), [True, False, False, False, False])
        self.assertEqual(self.trip.distance_m, 0)
        self.assertIsNone(self.trip.average_speed_m_s)

    def test_position_glitch_is_rejected_but_resynchronises(self):
        self.feed(pvt(0, 0), pvt(1, 10), pvt(2, 5000), pvt(3, 5010))
        self.assertAlmostEqual(self.trip.distance_m, 20, delta=0.2)

    def test_signal_gap_counts_the_straight_line(self):
        self.feed(pvt(0, 0), pvt(60, 600))  # A tunnel: one minute without fixes.
        self.assertAlmostEqual(self.trip.distance_m, 600, delta=1)
        self.assertEqual(self.trip.moving_s, 60)

    def test_week_and_day_rollover(self):
        self.feed(pvt(604799, 0), pvt(0, 10))
        self.assertAlmostEqual(self.trip.distance_m, 10, delta=0.1)
        self.assertEqual(self.trip.moving_s, 1)
        trip = Trip()
        for epoch, north in (("235959.00", 0), ("000000.00", 10)):
            trip.update({"epoch": epoch, "fix_ok": True, "fresh": True, "speed_m_s": 10,
                         "latitude": LAT + north / M_PER_DEG_LAT, "longitude": LON})
        self.assertAlmostEqual(trip.distance_m, 10, delta=0.1)

    def test_nmea_without_speed_uses_implied_speed(self):
        trip = Trip()
        for i, north in enumerate((0, 10, 20)):
            trip.update({"epoch": f"1200{i:02d}.00", "fix_ok": True, "fresh": True,
                         "latitude": LAT + north / M_PER_DEG_LAT, "longitude": LON})
        self.assertAlmostEqual(trip.distance_m, 20, delta=0.2)
        self.assertIsNone(trip.max_speed_m_s)

    def test_changing_clock_does_not_join_segments(self):
        self.feed(pvt(100, 0))
        self.trip.update({"epoch": "120000.00", "fix_ok": True, "fresh": True, "speed_m_s": 10,
                          "latitude": LAT + 100 / M_PER_DEG_LAT, "longitude": LON})
        self.assertEqual(self.trip.distance_m, 0)

    def test_reset(self):
        self.feed(pvt(0, 0), pvt(1, 10, speed=12))
        self.trip.reset()
        self.assertEqual(self.trip.summary(), {"distance_m": 0.0, "moving_s": 0.0, "average_speed_m_s": None,
                                               "max_speed_m_s": None, "epochs": 0})
        self.feed(pvt(5, 50))
        self.assertEqual(self.trip.distance_m, 0)  # The first epoch after a reset starts a new trip.


class DemoDriveTests(unittest.TestCase):
    def test_speed_and_heading_match_the_motion(self):
        for t in (0, 13.5, 47, 90, 200):
            lat1, lon1, speed, heading, _ = drive(t)
            lat2, lon2, *_ = drive(t + 0.01)
            self.assertAlmostEqual(distance_m(lat1, lon1, lat2, lon2) / 0.01, speed, delta=0.05)
            bearing = math.degrees(math.atan2(
                math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2)),
                math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
                - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))))
            self.assertAlmostEqual((bearing - heading + 180) % 360 - 180, 0, delta=0.1)
        speeds = [drive(t)[2] for t in range(0, 91)]
        self.assertAlmostEqual(min(speeds), 3, delta=0.1)
        self.assertAlmostEqual(max(speeds), 25, delta=0.1)

    def test_session_trip_from_demo_drive(self):
        session = Session(0.0, 5.0, True, "SYNTHETIC DEMO")
        for second in range(121):
            session.consume(sample(1000 + second * 1000, "drive"), float(second))
        trip = session.state.snapshot(120.0, session.framer)["trip"]
        travelled = 14 * 120 + 11 * 90 / (2 * math.pi) * (1 - math.cos(2 * math.pi * 120 / 90))
        self.assertAlmostEqual(trip["distance_m"], travelled, delta=travelled * 0.005)  # Chords of a 400 m loop.
        self.assertEqual(trip["moving_s"], 120)
        self.assertAlmostEqual(trip["max_speed_m_s"], 25, delta=0.1)
        self.assertEqual(trip["epochs"], 121)


if __name__ == "__main__":
    unittest.main()
