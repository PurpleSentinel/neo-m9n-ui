import unittest
from m9n.demo import sample
from m9n.protocol import nmea, ubx
from m9n.session import Session
from m9n.gui.format import fix_state
from m9n.serialize import json_line, safe


class FakePort:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


class StateTests(unittest.TestCase):
    def test_healthy_pass(self):
        session = Session(0)
        session.consume(sample(), .1)
        self.assertEqual(session.report(1)["status"], "PASS")

    def test_silent_fail(self):
        self.assertEqual(Session(0).report(10)["status"], "FAIL")

    def test_no_fix_warning(self):
        session = Session(0)
        session.consume(sample(fault="no-fix"), 0)
        self.assertEqual(session.report(1)["status"], "WARN")

    def test_stale_data_fails(self):
        session = Session(0)
        session.consume(sample(), 0)
        report = session.report(10)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["snapshot"]["navigation"]["fresh"])
        self.assertEqual(fix_state(report["snapshot"]["navigation"])[0], "stale")  # Never shown as valid.

    def test_frozen_epoch_despite_fresh_bytes(self):
        session = Session(0)
        session.consume(sample(), 0)
        session.consume(sample(), 6)
        self.assertFalse(session.snapshot(6)["navigation"]["fresh"])
        self.assertEqual(session.report(6)["status"], "FAIL")

    def test_advancing_epoch_recovers(self):
        session = Session(0)
        session.consume(sample(), 0)
        session.consume(sample(9000), 6)
        self.assertEqual(session.report(6)["status"], "PASS")

    def test_rf_alarm_warning(self):
        session = Session(0)
        session.consume(sample(fault="rf"), 0)
        report = session.report(1)
        self.assertEqual(report["status"], "WARN")
        self.assertTrue(any("spoofing" in i for i in report["snapshot"]["issues"]))

    def test_expired_rf_alarm_not_current(self):
        session = Session(0)
        session.consume(sample(fault="rf"), 0)
        self.assertFalse(any("RF interference" in i for i in session.snapshot(16)["issues"]))

    def test_unknown_rf_state_is_warning(self):
        session = Session(0)
        session.consume(sample(), 0)
        session.state.messages["MON-RF"]["blocks"][0]["jamming_state"] = 0
        report = session.report(1)
        self.assertEqual(next(c for c in report["checks"] if c["name"] == "rf_health")["status"], "WARN")

    def test_corruption_warning(self):
        session = Session(0)
        session.consume(sample(fault="corrupt"), 0)
        self.assertEqual(session.report(1)["status"], "WARN")

    def test_receiver_error_fail(self):
        session = Session(0)
        session.consume(sample() + ubx(4, 0, b"receiver error"), 0)
        self.assertEqual(session.report(1)["status"], "FAIL")

    def test_unexpected_model_fail(self):
        session = Session(0)
        session.consume(sample(), 0)
        session.state.messages["MON-VER"]["extensions"] = ["MOD=NEO-M8N"]
        self.assertEqual(session.report(1)["status"], "FAIL")

    def test_nmea_only_fallback(self):
        session = Session(0, passive=True)
        session.consume(nmea("GNGGA,120000,5409.000,N,00427.000,W,1,08,1.2,25,M,0,M,,"), 0)
        self.assertTrue(session.snapshot(1)["navigation"]["fix_ok"])
        self.assertEqual(session.report(1)["status"], "WARN")

    def test_invalid_nmea_replaces_valid_position(self):
        session = Session(0, passive=True)
        session.consume(nmea("GNRMC,120000,A,5409.000,N,00427.000,W,1.0,90,111026,,,A"), 0)
        session.consume(nmea("GNRMC,120001,V,,,,,,,111026,,,N"), 1)
        self.assertFalse(session.snapshot(1)["navigation"]["fix_ok"])

    def test_payload_error_count(self):
        session = Session(0)
        session.consume(ubx(1, 7, bytes(10)), 0)
        self.assertEqual(session.state.decode_errors, 1)

    def test_same_epoch_nmea_merge(self):
        session = Session(0, passive=True)
        session.consume(nmea("GNGGA,120000,5409.000,N,00427.000,W,1,08,1.2,25,M,0,M,,"), 0)
        session.consume(nmea("GNRMC,120000,A,5409.000,N,00427.000,W,1.0,90,111026,,,A"), .1)
        nav = session.snapshot(1)["navigation"]
        self.assertEqual(nav["satellites_used"], 8)
        self.assertEqual(nav["altitude_msl_m"], 25)
        self.assertAlmostEqual(nav["speed_m_s"], .514444)

    def test_different_epoch_nmea_not_merged(self):
        session = Session(0, passive=True)
        session.consume(nmea("GNGGA,120000,5409.000,N,00427.000,W,1,08,1.2,25,M,0,M,,"), 0)
        session.consume(nmea("GNRMC,120001,A,5409.000,N,00427.000,W,1.0,90,111026,,,A"), .1)
        self.assertNotIn("altitude_msl_m", session.snapshot(1)["navigation"])

    def test_polls_are_only_empty_payloads(self):
        session = Session(0)
        port = FakePort()
        session.poll(port, 0)
        self.assertEqual(len(port.writes), 5)
        self.assertTrue(all(data[4:6] == b"\0\0" for data in port.writes))
        self.assertTrue(all(data[2] in (1, 10) for data in port.writes))

    def test_passive_no_writes(self):
        session = Session(0, passive=True)
        port = FakePort()
        session.poll(port, 0)
        self.assertFalse(port.writes)

    def test_timeout_and_recovery(self):
        session = Session(0)
        port = FakePort()
        session.poll(port, 0)
        session.poll(port, 2)
        self.assertEqual(session.state.poll_timeouts["NAV-PVT"], 1)
        session.consume(sample(2000), 2.1)
        self.assertNotIn("NAV-PVT", session.unanswered)

    def test_nak_stops_repeat_poll(self):
        session = Session(0)
        port = FakePort()
        session.poll(port, 0)
        session.consume(ubx(5, 0, bytes((10, 56))), .1)
        session.poll(port, 10)
        count = sum(packet[2:4] == bytes((10, 56)) for packet in port.writes)
        self.assertEqual(count, 1)

    def test_receiver_text_is_escaped(self):
        session = Session(0)
        session.consume(ubx(4, 2, b"\x1b[2Jbad"), 0)
        text = session.snapshot(1)["recent_messages"][-1]["text"]
        self.assertEqual(safe(text), "?[2Jbad")
        self.assertNotIn("\x1b", json_line(session.snapshot(1)))


if __name__ == "__main__":
    unittest.main()
