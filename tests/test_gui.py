import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gui_support import ROOT, gui_runtime_missing, headless_display, run_probe
from m9n.transport import SerialPort
from ptysim import Receiver


class DependencyTests(unittest.TestCase):
    def test_missing_pygobject_is_actionable(self):
        code = "import sys; sys.modules['gi'] = None; from m9n.gui.application import main; raise SystemExit(main())"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dnf install gtk4 libadwaita python3-gobject", result.stderr)

    @unittest.skipIf(gui_runtime_missing(), "GTK runtime not installed")
    def test_old_versions_rejected(self):
        from m9n.gui import deps
        with patch("gi.version_info", (3, 40, 0)):
            self.assertIn("PyGObject 3.40.0 is older", deps.check())
        with patch.object(deps, "MIN_ADW", (99, 0)):
            self.assertIn("libadwaita", deps.check())
        self.assertIsNone(deps.check())


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, scenario, **env):
        process, result = run_probe(self.env, scenario, **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result)
        self.assertEqual(result["exit"], 0)
        return result

    def test_window_pages_actions_and_dialogs(self):
        result = self.probe("shell")
        self.assertEqual(result["pages"], ["dashboard", "satellites", "receiver", "messages", "test", "host"])
        self.assertTrue(result["empty_states"])
        self.assertTrue(result["toggle_enabled"])
        self.assertEqual(result["accels"]["app.quit"], ["<Control>q"])
        self.assertEqual(result["accels"]["win.toggle-session"], ["F5"])
        self.assertEqual(result["about"], "AboutDialog")
        self.assertIn(result["shortcuts"], ("ShortcutsDialog", "AlertDialog"))
        self.assertTrue(result["banner_shown"] and result["banner_hidden"])
        self.assertEqual(result["visible_page"], "host")
        self.assertFalse(result["bottom_switcher"])
        self.assertTrue(result["header_switcher"])
        self.assertFalse(result["sidebar_collapsed"])

    def test_narrow_layout_moves_switcher_to_bottom(self):
        result = self.probe("shell", M9N_PROBE_WIDTH="380")
        self.assertTrue(result["bottom_switcher"])
        self.assertFalse(result["header_switcher"])
        self.assertTrue(result["sidebar_collapsed"])

    def test_second_launch_presents_existing_instance(self):
        result = self.probe("unique")
        self.assertEqual(result["second_exit"], 0)
        self.assertEqual(result["activations"], 2)
        self.assertEqual(result["windows"], 1)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "dashboard", **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        self.assertEqual(result["empty_before"], "empty")
        self.assertEqual(result["visible"], "content")
        self.assertEqual(result["after_stop_visible"], "content")  # Final snapshot stays visible.
        return result

    def test_valid_demo_fix(self):
        result = self.probe()
        values = result["values"]
        self.assertEqual(result["fix_title"], "Valid Fix")
        self.assertEqual(result["fix_detail"], "3D · 10 satellites used")
        self.assertEqual(values["latitude"], "54.1667070° N")
        self.assertEqual(values["longitude"], "4.4503420° W")
        self.assertEqual(values["msl"], "10.00 m")
        self.assertEqual(values["speed"], "1.25 m/s (4.5 km/h)")
        self.assertEqual(values["hdop"], "unknown")  # NAV-PVT has no HDOP; never blank or guessed.
        self.assertEqual(values["software"], "SPG 4.04")
        self.assertIn("MOD=NEO-M9N", values["extensions"])
        self.assertEqual(values["source"], "SYNTHETIC DEMO")
        self.assertEqual(result["issues"], ["No active issues"])
        self.assertEqual(result["clipboard"], "54.1667070, -4.4503420")
        self.assertEqual(result["sanitised"], ["EVIL?[2J?FW", "Bell? issue"])

    def test_frozen_epochs_show_stale_with_issue_toast(self):
        result = self.probe(M9N_FAULT="frozen", M9N_STALE="0.5", M9N_WAIT_MS="1800")
        self.assertEqual(result["fix_title"], "Stale Navigation")
        self.assertTrue(any("stale" in issue for issue in result["issues"]))
        self.assertTrue(any("stale" in toast for toast in result["toasts"]))

    def test_no_fix_hides_coordinates(self):
        result = self.probe(M9N_FAULT="no-fix")
        self.assertEqual(result["fix_title"], "No Fix")
        self.assertEqual(result["values"]["latitude"], "unknown")
        self.assertIsNone(result["clipboard"])


def many_satellites_capture(path, count=35):
    """Synthetic NAV-SAT with mixed constellations, signal levels and one unknown position."""
    import struct
    from m9n.demo import sample
    from m9n.protocol import ubx
    payload = bytearray(struct.pack("<IBBH", 5000, 1, count, 0))
    for i in range(count):
        elevation = -91 if i == count - 1 else (i * 7) % 90
        flags = 8 | 5 | (1 << 4) if i % 3 else 2
        payload += struct.pack("<BBBbhhI", i % 7, i + 1, 20 + i % 25, elevation, (i * 37) % 360, 0, flags)
    path.write_bytes(sample() + ubx(1, 53, bytes(payload)))


class SatelliteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "satellites", **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        return result

    def test_live_table_order_selection_and_sorting(self):
        result = self.probe()
        self.assertEqual(result["inner"], "table")
        # Used first, then strongest C/N0 (42, 39, 35, 31 dB-Hz in the demo).
        self.assertEqual(result["rows"], [["GPS", 3], ["Galileo", 12], ["BeiDou", 5], ["GLONASS", 2]])
        self.assertIn("4 satellites tracked · 4 used", result["summary"])
        self.assertEqual(result["constellations"], "BeiDou 1/1 · GLONASS 1/1 · GPS 1/1 · Galileo 1/1")
        self.assertEqual(result["selected_after"], result["selected_before"])  # Survives refreshes.
        self.assertEqual([sv for _, sv in result["sorted_by_sv"]], [2, 3, 5, 12])

    def test_replay_with_many_satellites(self):
        with tempfile.TemporaryDirectory() as d:
            capture = Path(d) / "many.ubx"
            many_satellites_capture(capture)
            result = self.probe(M9N_CAPTURE=str(capture))
        self.assertEqual(len(result["rows"]), 35)
        self.assertIn("35 satellites tracked · 23 used", result["summary"])
        self.assertEqual(result["used"], 23)
        self.assertEqual(result["sky_points"], 34)  # The unknown-position satellite is not plotted.

    def test_silent_receiver_shows_no_data(self):
        result = self.probe(M9N_FAULT="silent")
        self.assertEqual(result["inner"], "nodata")
        self.assertIn("not evidence of a hardware fault", result["nodata_text"])


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "receiver", **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        return result

    def test_rf_fault_marks_antenna_jamming_and_spoofing(self):
        result = self.probe(M9N_FAULT="rf")
        rf = result["rf"][0]
        self.assertEqual(rf["antenna"], "SHORT")
        self.assertEqual(rf["jamming"], "critical interference (state 3)")
        self.assertEqual(rf["cw"], "220 / 255")
        self.assertEqual(result["rf_alarms"], [["antenna", "jamming"]])
        self.assertEqual(result["values"]["spoof"], "spoofing indicated (state 2)")
        self.assertIn("spoof", result["alarms"])
        self.assertEqual(result["values"]["mode"], "passive: no polls sent")

    def test_healthy_demo_has_no_alarms(self):
        result = self.probe()
        self.assertEqual(result["rf_alarms"], [[]])
        self.assertEqual(result["alarms"], [])
        self.assertEqual(result["rf"][0]["jamming"], "no interference (state 1)")
        self.assertTrue(result["note"].startswith("Received"))
        self.assertEqual(result["values"]["ttff"], "24.0 s")
        self.assertEqual(result["values"]["rejected"], "none")

    def test_disabled_jamming_monitor_explains_warn(self):
        import struct
        from m9n.demo import sample
        from m9n.protocol import ubx
        block = bytes((0, 0, 2, 1)) + bytes(8) + struct.pack("<HH", 90, 1000) + bytes(8)
        with tempfile.TemporaryDirectory() as d:
            capture = Path(d) / "rf0.ubx"
            capture.write_bytes(sample() + ubx(10, 56, bytes((0, 1, 0, 0)) + block))
            result = self.probe(M9N_CAPTURE=str(capture))
        self.assertIn("state 0", result["note"])
        self.assertIn("WARN", result["note"])
        self.assertEqual(result["rf"][0]["jamming"], "unknown or monitor disabled (state 0)")

    def test_silent_receiver_rf_unknown(self):
        result = self.probe(M9N_FAULT="silent")
        self.assertIn("unknown, not healthy", result["note"])
        self.assertEqual(result["values"]["MON-RF"], "never received")
        self.assertEqual(result["values"]["ttff"], "unavailable")


class SetupTests(unittest.TestCase):
    def test_setup_drives_a_session_and_persists(self):
        with headless_display() as env, tempfile.TemporaryDirectory() as out:
            folder = Path(out) / "evidence"  # Missing: created when the session starts.
            saved = Path(env["XDG_CONFIG_HOME"]) / "neo-m9n" / "settings.json"
            saved.parent.mkdir(parents=True)
            saved.write_text(json.dumps({"source": "demo", "fault": "rf", "mode": "test", "test_duration": 1.5,
                                         "json_log": True, "log_file": True, "report": True,
                                         "raw_log": True, "output_dir": str(folder)}))
            process, result = run_probe(env, "setup")
            self.assertIsNotNone(result, process.stderr)
            self.assertNotIn("timeout", result, process.stderr)
            self.assertEqual(result["demo_rows"], {"device": False, "fault": True, "raw": False,
                                                   "report": True, "receiver": False})
            self.assertTrue(result["valid_demo"])
            invalid = result["replay_invalid"]
            self.assertFalse(invalid["enabled"])
            self.assertTrue(invalid["capture_error"])
            self.assertIn("capture file", invalid["message"])
            self.assertFalse(invalid["mode_visible"])
            self.assertTrue(result["valid_again"])
            self.assertTrue(result["sidebar_hidden"])
            self.assertTrue(result["running"])
            self.assertFalse(result["sensitive_while_running"])
            self.assertEqual(result["config"], {"mode": "test", "fault": "rf", "interval": .5})
            self.assertEqual(result["reason"], "completed")
            self.assertEqual(result["status"], "WARN")
            self.assertTrue(result["sensitive_after"])
            names = sorted(p.name for p in folder.iterdir())
            self.assertEqual(len(names), 3, names)  # No raw capture for a demo source.
            self.assertTrue(all(n.startswith("m9n-") for n in names))
            self.assertEqual(sorted(n.split(".", 1)[1] for n in names), ["json", "jsonl", "log"])
            self.assertTrue(any(n.endswith("-report.json") for n in names))
            self.assertEqual(json.loads(saved.read_text())["interval"], .5)


class LifecycleTests(unittest.TestCase):
    """Every exit path must release the tty and keep the evidence files complete."""

    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def run_trigger(self, trigger, mode="watch", unplug_after=None, **env):
        with tempfile.TemporaryDirectory() as out, Receiver(unplug_after=unplug_after) as receiver:
            process, result = run_probe(self.env, "lifecycle", M9N_PORT=receiver.port, M9N_OUT=out,
                                        M9N_TRIGGER=trigger, M9N_MODE=mode, **env)
            self.assertIsNotNone(result, process.stderr)
            self.assertNotIn("timeout", result, process.stderr)
            for warning in ("CRITICAL", "Source ID"):
                self.assertNotIn(warning, process.stderr)
            self.assertTrue(result["started"], process.stderr)
            self.assertEqual(result["running_button"], "Stop")
            self.assertFalse(result["tty_open_at_finish"], "tty still open when the session finished")
            self.assertFalse(result["tty_open_at_exit"], "tty still open at exit")
            if unplug_after is None:
                SerialPort(receiver.port).close()  # flock + TIOCEXCL succeed: released.
            files = {name: Path(out, name) for name in ("rx.ubx", "log.jsonl", "report.json")}
            self.assertTrue(files["rx.ubx"].read_bytes())
            last = json.loads(files["log.jsonl"].read_text().splitlines()[-1])
            report = json.loads(files["report.json"].read_text()) if files["report.json"].exists() else None
            return result, last, report

    def test_stop_action(self):
        result, last, _ = self.run_trigger("stop")
        self.assertEqual(result["reason"], "user_stopped")
        self.assertEqual(result["button"], "Start")
        self.assertEqual(last["kind"], "snapshot")

    def test_window_close_asks_then_stops(self):
        result, _, _ = self.run_trigger("close")
        self.assertTrue(result["close_blocked"])
        self.assertEqual(result["dialog_heading"], "Stop the Session and Quit?")
        self.assertEqual(result["reason"], "user_stopped")

    def test_quit_shortcut_during_test_records_interrupted(self):
        result, last, report = self.run_trigger("quit", mode="test")
        self.assertTrue(result["close_blocked"])
        self.assertEqual(report["status"], "INTERRUPTED")
        self.assertEqual(last["kind"], "test_report")

    def test_cancelled_close_keeps_session(self):
        result, _, _ = self.run_trigger("cancel-close")
        self.assertTrue(result["still_running_after_cancel"])
        self.assertEqual(result["reason"], "user_stopped")

    def test_signals(self):
        for trigger, status in (("sigint", 130), ("sigterm", 143), ("sighup", 129)):
            with self.subTest(trigger=trigger):
                result, last, report = self.run_trigger(trigger, mode="test")
                self.assertEqual(result["reason"], "interrupted")
                self.assertEqual(result["exit_status"], status)
                self.assertEqual(report["status"], "INTERRUPTED")
                self.assertEqual(last["status"], "INTERRUPTED")

    def test_unhandled_exception_releases_device(self):
        result, _, _ = self.run_trigger("exception")
        self.assertEqual(result["reason"], "interrupted")
        self.assertEqual(result["error_dialog"], "AlertDialog")

    def test_unplug_shows_banner(self):
        result, _, report = self.run_trigger("unplug", mode="test", unplug_after=1.2)
        self.assertEqual(result["reason"], "transport_error")
        self.assertIn("Device disconnected", result["banner"])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["checks"][-1]["name"], "usb_transport")

    def test_timed_test_completes(self):
        result, last, report = self.run_trigger("complete", mode="test", M9N_DURATION="1.5")
        self.assertEqual(result["reason"], "completed")
        self.assertEqual(last["kind"], "test_report")
        self.assertIn(report["status"], ("PASS", "WARN"))


if __name__ == "__main__":
    unittest.main()
