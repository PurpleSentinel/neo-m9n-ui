import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from m9n.controller import validate
from m9n.settings import SessionSettings

NOW = datetime(2026, 10, 11, 12, 0, 0)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)

    def settings(self, **values):
        settings = SessionSettings(output_dir=str(self.path), **values)
        settings.report = values.get("report", False)
        return settings

    def test_defaults_are_a_valid_watch_session(self):
        config = self.settings().to_config(NOW)
        self.assertEqual((config.source, config.mode, config.port, config.duration), ("device", "watch", None, 0.0))
        self.assertEqual(self.settings().problems(NOW), {})
        validate(config)

    def test_timestamped_outputs_by_kind(self):
        config = self.settings(raw_log=True, json_log=True, log_file=True, report=True, mode="test").to_config(NOW)
        self.assertEqual(config.raw_log, self.path / "m9n-20261011-120000.ubx")
        self.assertEqual(config.json_log, self.path / "m9n-20261011-120000.jsonl")
        self.assertEqual(config.log_file, self.path / "m9n-20261011-120000.log")
        self.assertEqual(config.report, self.path / "m9n-20261011-120000-report.json")

    def test_raw_capture_never_collides(self):
        (self.path / "m9n-20261011-120000.ubx").touch()
        config = self.settings(raw_log=True).to_config(NOW)
        self.assertEqual(config.raw_log.name, "m9n-20261011-120000-2.ubx")
        self.assertEqual(self.settings(raw_log=True).problems(NOW), {})

    def test_outputs_that_do_not_apply_are_dropped(self):
        demo = self.settings(source="demo", raw_log=True, report=True, fault="rf").to_config(NOW)
        self.assertIsNone(demo.raw_log)
        self.assertIsNone(demo.report)
        self.assertEqual(demo.fault, "rf")
        replay = self.settings(source="replay", mode="test", capture="x.ubx", report=True, port="/dev/x").to_config(NOW)
        self.assertEqual((replay.mode, replay.duration, replay.port, replay.report), ("watch", 0.0, None, None))
        device = self.settings(fault="rf", capture="x.ubx").to_config(NOW)
        self.assertIsNone(device.fault)
        self.assertIsNone(device.capture)

    def test_durations_follow_mode(self):
        self.assertEqual(self.settings(watch_duration=30).to_config(NOW).duration, 30)
        self.assertEqual(self.settings(mode="test", test_duration=120).to_config(NOW).duration, 120)
        self.assertIn("test_duration", self.settings(mode="test", test_duration=0).problems(NOW))

    def test_problems_name_the_setting(self):
        self.assertIn("capture", self.settings(source="replay").problems(NOW))
        self.assertIn("interval", self.settings(interval=0).problems(NOW))
        self.assertIn("output_dir", SessionSettings(json_log=True, report=False).problems(NOW))
        pinned = self.settings(json_log=True, pinned={"json_log": "/nonexistent-dir/x.jsonl"})
        self.assertIn("does not exist", pinned.problems(NOW)["json_log"])
        existing = self.path / "taken.ubx"
        existing.touch()
        self.assertIn("never overwritten", self.settings(raw_log=True, pinned={"raw_log": str(existing)}).problems(NOW)["raw_log"])

    def test_missing_auto_output_folder_is_created_on_start(self):
        folder = self.path / "new" / "NEO-M9N"
        settings = SessionSettings(output_dir=str(folder), json_log=True, report=False)
        self.assertEqual(settings.problems(NOW), {})
        self.assertEqual(settings.prepare_outputs(), folder)
        self.assertTrue(folder.is_dir())
        self.assertIsNone(SessionSettings(output_dir=str(self.path / "unused"), report=False).prepare_outputs())

    def test_round_trip_and_tolerant_load(self):
        file = self.path / "settings.json"
        original = self.settings(source="demo", fault="frozen", baud=115200, interval=.5, json_log=True,
                                 pinned={"json_log": "/tmp/a.jsonl"})
        original.save(file)
        self.assertEqual(SessionSettings.load(file), original)
        file.write_text(json.dumps({"baud": 1234, "interval": "fast", "debug": 1, "source": "radio",
                                    "pinned": {"bogus": 1}, "mode": "test", "unknown": True}))
        loaded = SessionSettings.load(file)
        self.assertEqual((loaded.baud, loaded.interval, loaded.debug, loaded.source, loaded.pinned, loaded.mode),
                         (9600, 1.0, False, "device", {}, "test"))
        file.write_text("not json")
        self.assertEqual(SessionSettings.load(file), SessionSettings())
        self.assertEqual(SessionSettings.load(self.path / "missing.json"), SessionSettings())


if __name__ == "__main__":
    unittest.main()
