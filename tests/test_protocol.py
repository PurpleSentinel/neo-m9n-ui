import struct
import unittest

from m9n.demo import sample
from m9n.protocol import Framer, checksum, coordinate, decode, nmea, ubx


class ProtocolTests(unittest.TestCase):
    def decode_sample(self, fault=None):
        return {e["name"]: e for e in map(decode, Framer().feed(sample(1000, fault), 0))}

    def test_known_poll_checksum(self):
        self.assertEqual(ubx(10, 4).hex(), "b5620a0400000e34")

    def test_reference_nmea_checksum(self):
        frame = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
        result = decode(Framer().feed(frame, 0)[0])
        self.assertAlmostEqual(result["latitude"], 48.1173)
        self.assertAlmostEqual(result["longitude"], 11.5166666667)
        self.assertEqual(result["altitude_msl_m"], 545.4)

    def test_every_byte_boundary(self):
        raw = sample()
        for split in range(len(raw) + 1):
            framer = Framer()
            events = framer.feed(raw[:split], 0) + framer.feed(raw[split:], .1)
            self.assertEqual(len(events), 6, split)
            self.assertFalse(framer.buffer)

    def test_one_byte_chunks(self):
        framer = Framer()
        events = []
        for byte in sample():
            events.extend(framer.feed(bytes((byte,)), .1))
        self.assertEqual(len(events), 6)

    def test_corrupt_ubx_recovers(self):
        bad = bytearray(ubx(1, 3, bytes(16)))
        bad[-1] ^= 1
        framer = Framer()
        frames = framer.feed(bytes(bad) + ubx(10, 4, bytes(40)), 0)
        self.assertEqual(framer.counts["checksum_errors"], 1)
        self.assertEqual(frames[-1]["id"], 4)

    def test_corrupt_nmea_recovers(self):
        framer = Framer()
        result = framer.feed(b"$GNTXT,01,01,00,error*00\r\n" + nmea("GNTXT,01,01,02,OK"), 0)
        self.assertEqual(result[-1]["protocol"], "NMEA")
        self.assertEqual(framer.counts["checksum_errors"], 1)

    def test_no_checksum_rejected(self):
        self.assertEqual(Framer().feed(b"$GNTXT,01,01,02,OK\n", 0)[0]["protocol"], "ERROR")

    def test_truncated_frame_timeout(self):
        framer = Framer()
        self.assertEqual(framer.feed(b"\xb5\x62\x01\x07\xff\x01", 0), [])
        recovered = framer.feed(ubx(10, 4, bytes(40)), 2.1)
        self.assertTrue(any(f["protocol"] == "ERROR" for f in recovered))
        self.assertEqual(recovered[-1]["id"], 4)

    def test_oversize_frame(self):
        framer = Framer()
        framer.feed(b"\xb5\x62\x01\x07\xff\xff" + b"\0" * 20000, 0)
        self.assertFalse(framer.buffer)
        self.assertEqual(framer.counts["oversize_frame"], 1)

    def test_nmea_buffer_bounded(self):
        framer = Framer()
        framer.feed(b"$" + b"A" * 20000, 0)
        self.assertLessEqual(len(framer.buffer), 1024)

    def test_noise_and_partial_sync(self):
        framer = Framer()
        framer.feed(b"noise\xb5", 0)
        result = framer.feed(ubx(10, 4, bytes(40))[1:], .1)
        self.assertEqual(len(result), 1)
        self.assertEqual(framer.counts["discarded_bytes"], 5)

    def test_pvt_scaling_and_west_longitude(self):
        event = self.decode_sample()["NAV-PVT"]
        self.assertAlmostEqual(event["latitude"], 54.166707)
        self.assertAlmostEqual(event["longitude"], -4.450342)
        self.assertEqual(event["horizontal_accuracy_m"], 1.5)
        self.assertEqual(event["speed_m_s"], 1.25)
        self.assertAlmostEqual(event["heading_deg"], 90)
        self.assertEqual(event["altitude_msl_m"], 10)

    def test_fix_invalid_hides_coordinates(self):
        event = self.decode_sample("no-fix")["NAV-PVT"]
        self.assertFalse(event["fix_ok"])
        self.assertIsNone(event["latitude"])

    def test_invalid_llh_flag(self):
        p = bytearray(92)
        p[20], p[21] = 3, 1
        p[78] = 1
        event = decode({"protocol": "UBX", "class": 1, "id": 7, "payload": p})
        self.assertFalse(event["fix_ok"])

    def test_invalid_utc_not_displayed(self):
        p = bytearray(92)
        event = decode({"protocol": "UBX", "class": 1, "id": 7, "payload": p})
        self.assertIsNone(event["utc"])

    def test_satellite_constellations(self):
        sats = self.decode_sample()["NAV-SAT"]["satellites"]
        self.assertEqual([s["gnss"] for s in sats], ["GPS", "Galileo", "BeiDou", "GLONASS"])
        self.assertTrue(all(s["used"] for s in sats))

    def test_rf_offsets(self):
        rf = self.decode_sample("rf")["MON-RF"]["blocks"][0]
        self.assertEqual(rf["antenna_status"], "SHORT")
        self.assertEqual(rf["jamming_state"], 3)
        self.assertEqual(rf["noise_per_ms"], 100)
        self.assertEqual(rf["agc_count"], 2000)
        self.assertEqual(rf["cw_suppression"], 220)

    def test_spoof_flag(self):
        self.assertEqual(self.decode_sample("rf")["NAV-STATUS"]["spoof_state"], 2)

    def test_short_and_unsupported_payload(self):
        for cls, ident, size in ((1, 7, 91), (1, 3, 15), (10, 4, 39), (10, 56, 3), (1, 53, 7)):
            with self.assertRaises(ValueError):
                decode({"protocol": "UBX", "class": cls, "id": ident, "payload": bytes(size)})
        with self.assertRaises(ValueError):
            decode({"protocol": "UBX", "class": 1, "id": 53, "payload": bytes(8)})

    def test_truncated_sat_group(self):
        with self.assertRaises(ValueError):
            decode({"protocol": "UBX", "class": 1, "id": 53, "payload": struct.pack("<IBBH", 1000, 1, 3, 0)})

    def test_blank_no_fix_nmea(self):
        event = decode(Framer().feed(nmea("GNGGA,120000,,,,,0,00,99.9,,,,,,"), 0)[0])
        self.assertFalse(event["fix_ok"])
        self.assertIsNone(event["latitude"])

    def test_rmc_valid_and_invalid(self):
        for status in ("A", "V"):
            sentence = nmea(f"GNRMC,120000,{status},5409.000,N,00427.000,W,1.0,90,111026,,,A")
            event = decode(Framer().feed(sentence, 0)[0])
            self.assertEqual(event["fix_ok"], status == "A")
            if status == "A":
                self.assertAlmostEqual(event["speed_m_s"], .514444)

    def test_invalid_coordinate(self):
        for value, hemi in (("5499.0", "N"), ("9100", "N"), ("nan", "N"), ("5300", "Q")):
            with self.assertRaises(ValueError):
                coordinate(value, hemi)

    def test_unknown_message(self):
        event = decode({"protocol": "UBX", "class": 99, "id": 7, "payload": b"123"})
        self.assertEqual(event["payload_bytes"], 3)


if __name__ == "__main__":
    unittest.main()
