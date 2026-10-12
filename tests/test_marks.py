import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from m9n import gpx
from m9n.controller import SessionConfig, SessionController
from m9n.trip import Marks

NS = {"g": gpx.GPX_NS}


def nav(lat=54.1, lon=-4.4, utc="2026-10-11T12:00:05Z"):
    return {"latitude": lat, "longitude": lon, "altitude_msl_m": 40.0, "horizontal_accuracy_m": 1.5,
            "utc": utc, "utc_valid": True}


class MarksTests(unittest.TestCase):
    def test_fields(self):
        mark = Marks().add(nav())
        self.assertEqual(mark, {"number": 1, "utc": "2026-10-11T12:00:05Z", "lat": 54.1, "lon": -4.4, "alt": 40.0,
                                "accuracy_m": 1.5, "note": ""})
        self.assertIsNone(Marks().add({**nav(), "utc_valid": False})["utc"])  # No time rather than a wrong one.

    def test_numbering_after_undo_delete_and_restore(self):
        marks = Marks()
        for _ in range(3):
            marks.add(nav())
        self.assertEqual(marks.undo_add(3)["number"], 3)
        self.assertEqual(marks.add(nav())["number"], 3)  # Undoing the newest frees its number...
        deleted = marks.delete(2)
        self.assertEqual(marks.add(nav())["number"], 4)  # ...but a deleted mark's number is never reused.
        marks.undo_add(1)  # Not the newest: removed, number kept.
        self.assertEqual(marks.add(nav())["number"], 5)
        marks.restore(deleted)
        marks.restore(deleted)  # Twice is harmless.
        self.assertEqual([m["number"] for m in marks.items], [2, 3, 4, 5])
        self.assertIsNone(marks.delete(99))

    def test_notes_and_copies(self):
        marks = Marks()
        marks.add(nav())
        self.assertEqual(marks.set_note(1, "  Parking \n spot ")["note"], "Parking spot")
        self.assertEqual(len(marks.set_note(1, "x" * 500)["note"]), Marks.NOTE_MAX)
        self.assertIsNone(marks.set_note(7, "missing"))
        copy = marks.copy()
        copy[0]["note"] = "changed"
        self.assertNotEqual(marks.items[0]["note"], "changed")


class ControllerMarkTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = Path(self.dir.name) / "log.jsonl"

    def run_demo(self, fault=None, seconds=3):
        ticks = [100.0]
        controller = SessionController(SessionConfig(source="demo", fault=fault, json_log=self.log),
                                       clock=lambda: ticks[0])
        controller.start()
        for _ in range(seconds):
            controller.tick()
            ticks[0] += 1.0
        controller.tick()
        return controller

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_needs_a_valid_fresh_fix(self):
        controller = self.run_demo("no-fix")
        self.assertFalse(controller.can_mark())
        self.assertIsNone(controller.add_mark())
        controller.stop()
        frozen = self.run_demo("frozen", seconds=8)  # Epochs stop advancing: navigation goes stale.
        self.assertIsNone(frozen.add_mark())
        frozen.stop()
        self.assertFalse(frozen.can_mark())

    def test_snapshot_jsonl_reset_and_after_stop(self):
        controller = self.run_demo("drive")
        first = controller.add_mark()
        self.assertEqual(first["number"], 1)
        controller.set_mark_note(1, "Hazard")
        controller.add_mark()
        controller.delete_mark(2)
        snapshot = controller.session.snapshot(controller.clock())
        self.assertEqual([m["number"] for m in snapshot["marks"]], [1])
        self.assertEqual(snapshot["marks"][0]["note"], "Hazard")
        controller.reset_trip()
        self.assertEqual(controller.marks(), [])
        controller.add_mark()
        controller.stop()
        self.assertEqual([m["number"] for m in controller.marks()], [1])  # Kept for export after Stop.
        self.assertIsNotNone(controller.delete_mark(1))  # Still editable; the log is closed by then.
        marks = [(e["action"], e.get("mark", {}).get("number")) for e in self.events() if e["kind"] == "mark"]
        self.assertEqual(marks, [("added", 1), ("note", 1), ("added", 2), ("deleted", 2), ("cleared", None),
                                 ("added", 1)])
        self.assertEqual(self.events()[-1]["kind"], "snapshot")
        self.assertEqual(self.events()[-1]["marks"][0]["number"], 1)


class GpxWaypointTests(unittest.TestCase):
    def test_waypoints_before_the_track(self):
        marks = Marks()
        marks.add(nav())
        marks.add({**nav(54.2, -4.5), "altitude_msl_m": None, "utc_valid": False})
        marks.set_note(1, "Parking spot")
        point = {"utc": "2026-10-11T12:00:05Z", "lat": 54.1, "lon": -4.4, "alt": None, "speed": None,
                 "course": None, "start": True}
        root = ET.fromstring(gpx.to_bytes([point], "NEO-M9N Monitor 9.9", marks=marks.copy(),
                                          now=datetime(2026, 10, 12, tzinfo=timezone.utc)))
        self.assertEqual([c.tag.split("}")[1] for c in root], ["metadata", "wpt", "wpt", "trk"])
        first, second = root.findall("g:wpt", NS)
        self.assertEqual((first.get("lat"), first.get("lon")), ("54.1000000", "-4.4000000"))
        # Schema order inside a waypoint: ele, time, name, desc.
        self.assertEqual([c.tag.split("}")[1] for c in first], ["ele", "time", "name", "desc"])
        self.assertEqual((first.findtext("g:name", namespaces=NS), first.findtext("g:desc", namespaces=NS)),
                         ("Mark 1", "Parking spot"))
        self.assertEqual([c.tag.split("}")[1] for c in second], ["name"])

    def test_no_marks_no_waypoints(self):
        root = ET.fromstring(gpx.to_bytes([], "NEO-M9N Monitor 9.9"))
        self.assertIsNone(root.find("g:wpt", NS))


if __name__ == "__main__":
    unittest.main()
