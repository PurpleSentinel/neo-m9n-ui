import time
import unittest
from datetime import datetime, timezone

from m9n import clock
from m9n.controller import SessionConfig, SessionController
from m9n.demo import sample
from m9n.protocol import Framer, decode, decode_nmea, nmea
from m9n.session import Session

UTC = timezone.utc
NOON = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()


def pvt(second, nano=0, valid=True, resolved=True):
    when = datetime.fromtimestamp(NOON + second, UTC)
    return {"name": "NAV-PVT", "utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "utc_valid": valid,
            "utc_fully_resolved": resolved, "nano": nano}


def rmc(text, date="111026", fix=True):
    return decode_nmea(nmea(f"GNRMC,{text},{'A' if fix else 'V'},5410.00,N,00427.00,W,0.1,,{date},,,A,V")
                       .decode()[1:-5])


class ClockCheckTests(unittest.TestCase):
    def feed(self, check, events, offset, latency=0.0):
        """Each event read offset (+ latency) seconds after the GNSS time it carries."""
        for event in events:
            _source, when = clock.gnss_time(event)
            check.add(event, when.timestamp() + offset + latency)
        return check.result(when.timestamp() + offset)

    def test_offset_sign(self):
        fast = self.feed(clock.ClockCheck(), [pvt(s) for s in range(5)], 1.3)
        self.assertEqual((fast["offset_s"], fast["samples"], fast["source"]), (1.3, 5, "NAV-PVT"))
        self.assertEqual(clock.describe(fast["offset_s"]), "1.3 s fast")
        slow = self.feed(clock.ClockCheck(), [pvt(s) for s in range(5)], -42)
        self.assertEqual(clock.describe(slow["offset_s"]), "42 s slow")

    def test_nano_is_signed(self):
        # The seconds field is rounded: 12:00:01 with nano -0.4 s is 12:00:00.6.
        self.assertEqual(clock.gnss_time(pvt(1, nano=-400_000_000))[1].timestamp(), NOON + 0.6)

    def test_median_rejects_outliers(self):
        check = clock.ClockCheck()
        for second in range(30):
            late = 2.5 if second % 7 == 0 else 0.1  # A few reads held up by USB or a busy computer.
            check.add(pvt(second), NOON + second + late)
        self.assertAlmostEqual(check.result(NOON + 30)["offset_s"], 0.1)
        for second in range(30, 60):
            check.add(pvt(second), NOON + second + 0.1)
        self.assertEqual(check.result(NOON + 60)["samples"], clock.WINDOW)

    def test_nothing_until_the_time_is_fully_resolved(self):
        check = clock.ClockCheck()
        check.add(pvt(0, valid=False), NOON)
        check.add(pvt(1, resolved=False), NOON + 1)
        check.add(rmc("120002.00", fix=False), NOON + 2)  # Time from the receiver's own clock, before a fix.
        check.add({**rmc("120003.00"), "date": None}, NOON + 3)
        self.assertIsNone(check.result(NOON + 3))
        check.add(pvt(4), NOON + 4)
        self.assertIsNotNone(check.result(NOON + 4))

    def test_polled_messages_and_repeats_are_skipped(self):
        check = clock.ClockCheck()
        check.add(pvt(0), NOON + 0.9, polled=True)  # A poll's answer can be up to a second old.
        self.assertIsNone(check.result(NOON + 1))
        check.add(pvt(1), NOON + 1.1)
        check.add(pvt(1), NOON + 1.8)  # The same epoch again.
        self.assertEqual(check.result(NOON + 2)["samples"], 1)
        self.assertAlmostEqual(check.result(NOON + 2)["offset_s"], 0.1)
        check.add(pvt(2), None)  # Replay: no host time.
        self.assertEqual(check.result(NOON + 2)["samples"], 1)

    def test_nmea_fallback_and_preference(self):
        check = clock.ClockCheck()
        for second in range(3):
            check.add(rmc(f"1200{second:02d}.00"), NOON + second + 0.25)
        self.assertEqual(check.result(NOON + 3), {"offset_s": 0.25, "samples": 3, "source": "RMC"})
        check.add(pvt(3), NOON + 3.05)
        self.assertEqual(check.result(NOON + 4)["source"], "NAV-PVT")  # Preferred while it keeps coming.
        self.assertEqual(check.result(NOON + 3 + clock.FRESH_S + 1), None)  # Both stopped.
        for second in range(20, 23):
            check.add(rmc(f"1200{second:02d}.50"), NOON + second + 0.5 + 0.25)
        self.assertEqual(check.result(NOON + 23)["source"], "RMC")  # NAV-PVT stopped: back to RMC.
        self.assertAlmostEqual(check.result(NOON + 23)["offset_s"], 0.25)

    def test_zda(self):
        event = decode_nmea("GNZDA,120005.00,11,10,2026,00,00")
        self.assertEqual(clock.gnss_time(event), ("ZDA", datetime(2026, 10, 11, 12, 0, 5, tzinfo=UTC)))
        self.assertIsNone(clock.gnss_time(decode_nmea("GNZDA,120005.00,,,,00,00")))

    def test_day_rollover(self):
        # Read just after midnight on the computer, 0.1 s after the last epoch of the GNSS day.
        midnight = datetime(2026, 10, 12, tzinfo=UTC).timestamp()
        check = clock.ClockCheck()
        check.add(rmc("235959.95", "111026"), midnight + 0.05)
        check.add(rmc("000000.95", "121026"), midnight + 1.05)
        self.assertAlmostEqual(check.result(midnight + 1.1)["offset_s"], 0.1)
        check = clock.ClockCheck()
        check.add(pvt(12 * 3600 - 1), midnight + 0.1)  # 23:59:59 the day before.
        self.assertAlmostEqual(check.result(midnight + 0.1)["offset_s"], 1.1)

    def test_describe_and_level(self):
        cases = [(0.05, "in step (±0.2 s)", "ok"), (-0.19, "in step (±0.2 s)", "ok"), (0.3, "0.3 s fast", "ok"),
                 (1.34, "1.3 s fast", "warning"), (-5.0, "5.0 s slow", "warning"), (42.2, "42 s fast", "error"),
                 (-185, "3 min 5 s slow", "error"), (7200, "2 h fast", "error"), (-3 * 86400, "3 days slow", "error")]
        for offset, text, level in cases:
            with self.subTest(offset):
                self.assertEqual((clock.describe(offset), clock.level(offset)), (text, level))


class SessionClockTests(unittest.TestCase):
    def test_snapshot_and_informational_report(self):
        now = time.time()
        session = Session(0.0, 5.0, True, "SYNTHETIC DEMO")
        session.consume(sample(1000, None, now - 42.0) + sample(2000, None, now - 41.0), 0.0, now)
        self.assertEqual(session.snapshot(0.1)["host_clock"],
                         {"offset_s": 41.5, "samples": 2, "source": "NAV-PVT"})  # Median of 42 and 41.
        report = session.report(0.1)
        check = next(c for c in report["checks"] if c["name"] == "host_clock")
        self.assertEqual(check["status"], "WARN")
        self.assertTrue(check["detail"].startswith("Computer clock 42 s fast against GNSS time (NAV-PVT, median of 2"))
        self.assertEqual(report["status"], "PASS")  # Informational: the receiver itself is fine.

    def test_replay_is_not_compared(self):
        session = Session(0.0, 5.0, True, "REPLAY")
        session.consume(sample(1000, None, time.time()), 0.0)
        self.assertIsNone(session.snapshot(0.1)["host_clock"])
        check = next(c for c in session.report(0.1)["checks"] if c["name"] == "host_clock")
        self.assertEqual(check["status"], "WARN")
        self.assertTrue(check["detail"].startswith("Not measured"))

    def test_polled_nav_pvt_is_not_used(self):
        session = Session(0.0, 5.0, False, "USB")
        session.state.pending["NAV-PVT"] = 1.5  # A poll is outstanding: this NAV-PVT answers it.
        session.consume(sample(1000, None, time.time()), 0.0, time.time() + 0.6)
        self.assertIsNone(session.snapshot(0.1)["host_clock"])

    def test_demo_time_encoding(self):
        utc = datetime(2026, 12, 31, 23, 59, 59, 700000, tzinfo=UTC).timestamp()
        event = {e["name"]: e for e in map(decode, Framer().feed(sample(1000, None, utc), 0))}["NAV-PVT"]
        self.assertEqual((event["utc"], round(event["nano"], -3)), ("2027-01-01T00:00:00Z", -300_000_000))
        self.assertAlmostEqual(clock.gnss_time(event)[1].timestamp(), utc, places=6)

    def test_demo_skew(self):
        for fault, expected in ((None, 0.0), ("clock-fast", 42.0), ("clock-slow", -1.5)):
            with self.subTest(fault):
                ticks, wall = [100.0], [NOON]
                controller = SessionController(SessionConfig(source="demo", fault=fault), clock=lambda: ticks[0],
                                               wall_clock=lambda: wall[0])
                controller.start()
                for _ in range(4):
                    controller.tick()
                    ticks[0] += 1.0
                    wall[0] += 1.0
                self.assertAlmostEqual(controller.session.state.clock.result(wall[0])["offset_s"], expected, places=3)
                controller.stop()
                controller.close()


if __name__ == "__main__":
    unittest.main()
