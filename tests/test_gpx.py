import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from m9n import gpx
from m9n.demo import sample
from m9n.session import Session
from m9n.trip import Track, Trip

NS = {"g": gpx.GPX_NS, "x": gpx.TPX_NS}
LAT, LON = 54.0, -4.0


def nav(second, north_m=0.0, speed=10.0, fix_ok=True, fresh=True, utc=True, alt=40.0):
    when = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc).timestamp() + second
    return {"itow_ms": round(second * 1000), "fix_ok": fix_ok, "fresh": fresh, "speed_m_s": speed,
            "heading_deg": 0.0, "altitude_msl_m": alt,
            "utc": datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if utc else None,
            "utc_valid": utc, "latitude": LAT + north_m / 111195.0, "longitude": LON}


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.trip = Trip()

    def points(self, *navs):
        for item in navs:
            self.trip.update(item)
        return self.trip.track.points

    def test_one_point_per_valid_epoch(self):
        points = self.points(nav(0), nav(0), nav(1, 10), nav(2, 20, fix_ok=False), nav(3, 30, fresh=False),
                             nav(4, 40))
        self.assertEqual([p["utc"][-3:] for p in points], ["00Z", "01Z", "04Z"])  # Duplicate and bad fixes skipped.
        self.assertEqual(points[1]["alt"], 40.0)
        self.assertEqual((points[1]["speed"], points[1]["course"]), (10.0, 0.0))

    def test_gap_starts_a_new_segment(self):
        points = self.points(nav(0), nav(1, 10), nav(11, 110), nav(22, 220), nav(23, 230))
        self.assertEqual([p["start"] for p in points], [True, False, False, True, False])  # 10 s is not a gap.

    def test_reset_clears_the_track(self):
        self.points(nav(0), nav(1, 10))
        self.trip.reset()
        self.assertEqual(self.trip.track.points, [])
        self.assertTrue(self.points(nav(5, 50))[0]["start"])

    def test_thinning_keeps_segment_starts_and_the_newest_points(self):
        track = Track(limit=100)
        for i in range(101):
            track.add(nav(i, i), gap_s=30 if i == 7 else 1)
        self.assertLessEqual(len(track.points), 100)
        self.assertEqual(track.points[-1]["lat"], nav(100, 100)["latitude"])  # Newest kept.
        newest = [p["lat"] for p in track.points[-50:]]
        self.assertEqual(newest, [nav(i, i)["latitude"] for i in range(51, 101)])  # Newer half untouched.
        starts = [p["lat"] for p in track.points if p["start"]]
        self.assertEqual(starts, [nav(0, 0)["latitude"], nav(7, 7)["latitude"]])

    def test_same_points_however_the_bytes_arrive(self):
        # A live session receives whole messages; a replay reads fixed-size chunks.
        data = [sample(1000 + second * 1000, "drive") for second in range(30)]
        live = Session(0.0, 5.0, True, "SYNTHETIC DEMO")
        for second, chunk in enumerate(data):
            live.consume(chunk, float(second))
        replay = Session(0.0, 5.0, True, "REPLAY")
        joined = b"".join(data)
        for start in range(0, len(joined), 777):
            replay.consume(joined[start:start + 777], start / len(joined) * 30)
        self.assertEqual(len(live.state.trip.track.points), 30)
        self.assertEqual(live.state.trip.track.points, replay.state.trip.track.points)


class WriterTests(unittest.TestCase):
    def document(self, points, **kwargs):
        data = gpx.to_bytes(points, "NEO-M9N Monitor 9.9", now=datetime(2026, 10, 12, tzinfo=timezone.utc), **kwargs)
        self.assertTrue(data.startswith(b"<?xml version='1.0' encoding='utf-8'?>"))
        return ET.fromstring(data)

    def test_gpx_11_track(self):
        trip = Trip()
        for item in (nav(0), nav(1, 10), nav(30, 300), nav(31, 310, utc=False, alt=None)):
            trip.update(item)
        root = self.document(trip.track.points, receiver="NEO-M9N")
        self.assertEqual(root.tag, f"{{{gpx.GPX_NS}}}gpx")
        self.assertEqual((root.get("version"), root.get("creator")), ("1.1", "NEO-M9N Monitor 9.9"))
        self.assertEqual(root.findtext("g:metadata/g:name", namespaces=NS), "Track 2026-10-11")
        self.assertEqual(root.findtext("g:metadata/g:desc", namespaces=NS),
                         "Recorded by NEO-M9N Monitor 9.9 from a u-blox NEO-M9N receiver")
        segments = root.findall("g:trk/g:trkseg", NS)
        self.assertEqual([len(s.findall("g:trkpt", NS)) for s in segments], [2, 2])
        first = segments[0].find("g:trkpt", NS)
        self.assertEqual((first.get("lat"), first.get("lon")), ("54.0000000", "-4.0000000"))
        self.assertEqual(first.findtext("g:ele", namespaces=NS), "40.00")
        self.assertEqual(first.findtext("g:time", namespaces=NS), "2026-10-11T12:00:00Z")
        self.assertEqual(first.findtext("g:extensions/x:TrackPointExtension/x:speed", namespaces=NS), "10.00")
        self.assertEqual(first.findtext("g:extensions/x:TrackPointExtension/x:course", namespaces=NS), "0.0")
        last = segments[1].findall("g:trkpt", NS)[-1]
        self.assertIsNone(last.find("g:time", NS))  # No valid date: no time rather than a wrong one.
        self.assertIsNone(last.find("g:ele", NS))
        # Schema order inside a point: ele, time, then extensions.
        self.assertEqual([child.tag.split("}")[1] for child in first], ["ele", "time", "extensions"])

    def test_nothing_identifying_the_computer(self):
        root = self.document([{**nav(0), "lat": LAT, "lon": LON, "alt": None, "speed": None, "course": None,
                               "start": True}])
        text = ET.tostring(root, encoding="unicode")
        self.assertNotIn("/home", text)
        self.assertEqual(root.findtext("g:metadata/g:desc", namespaces=NS), "Recorded by NEO-M9N Monitor 9.9")
        self.assertIsNone(root.find("g:trk/g:trkseg/g:trkpt/g:extensions", NS))

    def test_receiver_model(self):
        self.assertEqual(gpx.receiver_model({"extensions": ["PROTVER=32.01", "MOD=NEO-M9N"]}), "NEO-M9N")
        self.assertEqual(gpx.receiver_model({"software": "SPG 4.04", "extensions": []}), "SPG 4.04")
        self.assertIsNone(gpx.receiver_model(None))

    def test_segments(self):
        points = [{"start": s} for s in (True, False, True, False, False)]
        self.assertEqual([len(s) for s in gpx.segments(points)], [2, 3])
        self.assertEqual(gpx.segments([]), [])
        self.assertEqual(len(gpx.segments([{"start": False}])), 1)  # A first point always opens a segment.


if __name__ == "__main__":
    unittest.main()
