import json
import os
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
        self.assertIn("dnf install gtk4 libadwaita python3-gobject gobject-introspection", result.stderr)

    @unittest.skipIf(gui_runtime_missing() and not os.environ.get("M9N_REQUIRE_GUI"), "GTK runtime not installed")
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
        self.assertEqual(result["pages"], ["dashboard", "satellites", "receiver", "vehicle", "map", "messages", "test",
                                           "host"])
        self.assertTrue(result["empty_states"])
        self.assertTrue(result["toggle_enabled"])
        self.assertEqual(result["accels"]["app.quit"], ["<Control>q"])
        self.assertEqual(result["accels"]["win.toggle-session"], ["F5"])
        self.assertEqual(result["about"], "AboutDialog")
        self.assertTrue(result["app_icon"])  # Packaged icon resolves even when not installed.
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


class VehicleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "vehicle", **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result)
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["empty_before"], "empty")
        self.assertFalse(result["overflow"])
        return result

    def test_drive_shows_live_instruments_units_and_reset(self):
        result = self.probe(M9N_FAULT="drive")
        live = result["live"]
        self.assertEqual((live["visible"], live["status"], live["speed_status"]), ("content", "Live", None))
        self.assertTrue(3 * 3.6 <= live["speed"] <= 25 * 3.6, live["speed"])
        self.assertEqual(live["unit"], "km/h")
        self.assertIn(live["maximum"], (60, 120))
        self.assertTrue(0 <= live["heading"] < 360)
        self.assertRegex(live["altitude"], r"^\d+$")
        self.assertEqual(live["altitude_unit"], "m")
        self.assertGreater(live["trip"]["distance_m"], 10)
        self.assertGreater(live["trip"]["moving_s"], 0)
        self.assertGreaterEqual(live["profile"], 2)
        distance, unit = live["tiles"]["distance"]
        self.assertEqual(unit, "m")
        self.assertAlmostEqual(float(distance), live["trip"]["distance_m"], delta=1)
        self.assertEqual(live["tiles"]["average"][1], "km/h")
        self.assertRegex(live["tiles"]["moving"][0], r"^0:0\d$")
        self.assertEqual(result["main_orientation"], "horizontal")
        self.assertTrue(result["idle_inhibited"])  # Screen kept on while driving with the page open.
        idle = [entry for entry in result["inhibits"] if entry[0] == "inhibit" and entry[2] == "Vehicle display in use"]
        self.assertEqual(len(idle), 1)
        self.assertEqual(idle[0][1], 8)  # Gtk.ApplicationInhibitFlags.IDLE

        imperial = result["imperial"]
        self.assertEqual((imperial["unit"], imperial["altitude_unit"]), ("mph", "ft"))
        self.assertEqual(imperial["tiles"]["distance"][1], "mi")
        self.assertIn(imperial["maximum"], (40, 80))
        self.assertEqual(result["saved_units"], "imperial")

        self.assertEqual(result["after_reset_profile"], 0)
        self.assertLess(result["after_reset"]["trip"]["distance_m"], live["trip"]["distance_m"])
        self.assertLessEqual(result["after_reset"]["trip"]["epochs"], 3)
        self.assertFalse(result["idle_after_leaving"])
        self.assertFalse(result["idle_after_stop"])
        # Broadway has no window manager to grant fullscreen; the button must follow the real state.
        self.assertEqual(result["fullscreen_icon"],
                         "view-restore-symbolic" if result["fullscreened"] else "view-fullscreen-symbolic")
        self.assertEqual(result["fullscreen_accels"], ["F11"])

    def test_no_fix_dims_the_instruments(self):
        result = self.probe(M9N_FAULT="no-fix")
        live = result["live"]
        self.assertEqual((live["status"], live["speed_status"]), ("No Fix", "No Fix"))
        self.assertIsNone(live["speed"])
        self.assertIsNone(live["heading"])
        self.assertEqual(live["altitude"], "–")
        self.assertEqual(live["tiles"]["distance"], ["0", "m"])
        self.assertEqual(live["profile"], 0)

    def test_frozen_epochs_are_stale_not_live(self):
        result = self.probe(M9N_FAULT="frozen", M9N_STALE="0.5", M9N_WAIT_MS="1800")
        self.assertEqual(result["live"]["status"], "Stale")
        self.assertIsNone(result["live"]["speed"])

    def test_static_demo_has_speed_but_no_distance(self):
        # The static demo reports 1.25 m/s (above the heading threshold) but never moves.
        result = self.probe()
        live = result["live"]
        self.assertAlmostEqual(live["speed"], 4.5, delta=0.01)
        self.assertEqual(live["trip"]["distance_m"], 0)
        self.assertAlmostEqual(live["heading"], 90)

    def test_phone_width_stacks_the_instruments(self):
        result = self.probe(M9N_FAULT="drive", M9N_PROBE_WIDTH="360")
        self.assertEqual(result["main_orientation"], "vertical")


class MapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "map_page", timeout=40, **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result)
        self.assertEqual(result["exit"], 0)
        self.assertFalse(result["overflow"])
        return result

    def test_off_online_consent_cache_and_policy(self):
        result = self.probe()
        self.assertTrue(result["shumate"])
        # Off by default: a local grid, no HTTP session, nothing requested, even during a live session.
        self.assertEqual(result["initial"], {"mode": "off", "visible": "content", "sessions": 0, "source": "blank"})
        live = result["live"]
        self.assertEqual((live["mode"], live["status"], live["sessions"], live["requests"]), ("off", "Live", 0, 0))
        self.assertGreaterEqual(live["track"], 2)
        self.assertIsNotNone(live["fix"][3])  # Moving: the marker is a heading arrow.
        self.assertEqual(result["marker_offset"], [0.0, 0.0])  # Following keeps the position centred.
        self.assertEqual((result["after_pan"]["following"], result["after_pan"]["recentre"]), (False, True))
        self.assertEqual((result["after_recentre"]["following"], result["after_recentre"]["recentre"]), (True, False))
        # Informed consent; cancelling changes nothing.
        self.assertEqual(result["consent_heading"], "Use Online Maps?")
        self.assertTrue(result["consent_mentions"])
        self.assertEqual((result["after_cancel"]["mode"], result["after_cancel"]["sessions"]), ("off", 0))
        self.assertFalse(result["consented_after_cancel"])
        # Online: visible area only, the policy's User-Agent, no no-cache headers, an indicator.
        online = result["online"]
        self.assertEqual((online["mode"], online["source"], online["indicator"]), ("online", "osm", True))
        self.assertTrue(0 < online["requests"] < 100, online["requests"])
        self.assertEqual(result["user_agents"], ["NEO-M9N-Monitor/2.0.1 (+https://github.com/PurpleSentinel/neo-m9n-ui)"
                                                 .replace("2.0.1", __import__("m9n").__version__)])
        self.assertFalse(result["no_cache_sent"])
        # Revisiting expired tiles revalidates them conditionally; consent is not asked twice.
        self.assertFalse(result["dialog_second_time"])
        self.assertGreater(result["second_requests"], 0)
        self.assertEqual(result["conditional"], result["second_requests"])
        # Nothing is requested while the page is hidden, or after switching Off.
        self.assertEqual(result["hidden_requests"], 0)
        self.assertEqual(result["off_requests"], 0)
        self.assertEqual((result["off"]["source"], result["off"]["indicator"]), ("blank", False))
        self.assertRegex(result["cache_usage"], r" in [1-9][0-9]* tiles$")
        self.assertTrue(result["cache_after_clear"].endswith(" in 0 tiles"))
        self.assertEqual(result["after_reset"]["track"], 0)
        self.assertEqual(result["saved"], ["off", True])

    def test_no_fix_dims_the_marker(self):
        result = self.probe(M9N_FAULT="no-fix")
        self.assertEqual((result["live"]["status"], result["live"]["dimmed"]), ("No Fix", "No Fix"))
        self.assertEqual(result["live"]["track"], 0)
        self.assertIsNone(result["live"]["fix"])

    def test_without_libshumate_the_track_is_drawn_on_a_grid(self):
        result = self.probe(M9N_NO_SHUMATE="1")
        self.assertFalse(result["shumate"])
        self.assertIsNone(result["initial"]["source"])
        self.assertGreaterEqual(result["live"]["track"], 2)
        self.assertEqual(result["live"]["sessions"], 0)

    def test_saved_online_mode_is_restored(self):
        import json
        settings = Path(self.env["XDG_CONFIG_HOME"]) / "neo-m9n" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"map_mode": "online", "map_consented": True}))
        # A dead loopback URL: the restored online map must not reach a real tile server in tests.
        process, result = run_probe(self.env, "map_restore", M9N_TILE_URL="http://127.0.0.1:9/{z}/{x}/{y}.png")
        self.assertIsNotNone(result, process.stderr)
        self.assertEqual((result["mode"], result["indicator"]), ("online", True))
        self.assertEqual(result["saved"], ["online", True])
        self.assertEqual(result["saved_later"], "online")
        settings.unlink()

    def test_offline_map_file(self):
        process, result = run_probe(self.env, "map_offline", timeout=40)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result)
        self.assertFalse(result["offline_sensitive_before"])  # Offline needs a map file.
        self.assertFalse(result["bad_file"])
        self.assertIn("Raster", result["bad_label"])
        self.assertTrue(result["good_file"])
        self.assertTrue(result["offline_sensitive"])
        self.assertEqual((result["mode"], result["source"]), ("offline", "offline"))
        self.assertEqual(result["indicator"], "Offline: Test Map, data 2026-10-08")
        self.assertEqual(result["license"], "© OpenMapTiles © OpenStreetMap contributors")
        # Drawn from the file; zero network: no HTTP session, and the style's tile URL is never fetched.
        self.assertGreater(result["tiles_served"], 0)
        self.assertEqual(result["sessions"], 0)
        self.assertEqual(result["style_url_requests"], 0)
        self.assertEqual(result["style_url_requests_after"], 0)
        self.assertTrue(result["inside"])
        self.assertTrue(result["outside"])
        self.assertTrue(result["dark_rebuilt"])
        self.assertEqual(result["saved"][0], "offline")
        self.assertTrue(result["saved"][1].endswith("test.mbtiles"))
        self.assertEqual(result["after_remove"], ["off", False, ""])

    def test_phone_width(self):
        result = self.probe(M9N_PROBE_WIDTH="360", M9N_MAP_QUICK="1")
        self.assertEqual(result["live"]["status"], "Live")


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


class LayoutTests(unittest.TestCase):
    """Pages use the width they get: columns follow the page's own width, never overflow it."""

    COLUMN_PAGES = ("dashboard", "receiver", "test", "host")

    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        process, result = run_probe(self.env, "layout", **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result)
        self.assertEqual(result["exit"], 0)
        for key in self.COLUMN_PAGES:
            self.assertFalse(result[key]["overflow"], key)
        return result

    def test_desktop_window_with_sidebar_uses_two_columns(self):
        result = self.probe(M9N_SIDEBAR="1")
        for key in self.COLUMN_PAGES:
            self.assertEqual(result[key]["columns"], 2, key)
            self.assertLess(result[key]["width"], 1000, key)  # The sidebar narrows the page itself.
        self.assertEqual(result["dashboard"]["bands"], ["horizontal"])  # Fix status beside Active Issues.
        self.assertEqual(sum(result["dashboard"]["flow"]), 7)
        self.assertEqual(result["satellites"]["orientation"], "vertical")  # Too narrow for table and sky side by side.

    def test_desktop_window_without_sidebar(self):
        result = self.probe()
        for key in self.COLUMN_PAGES:
            self.assertEqual(result[key]["columns"], 2, key)
        self.assertEqual(result["satellites"]["orientation"], "horizontal")
        self.assertEqual(result["satellites"]["sky"], 260)

    def test_phone_width_stacks_everything(self):
        result = self.probe(M9N_PROBE_WIDTH="360")
        for key in self.COLUMN_PAGES:
            self.assertEqual(result[key]["columns"], 1, key)
            self.assertTrue(all(band == "vertical" for band in result[key]["bands"]), key)
        self.assertEqual(result["dashboard"]["flow"], [7])
        self.assertEqual(result["satellites"]["orientation"], "vertical")

    def test_wide_page_uses_three_columns_and_a_larger_sky_plot(self):
        # Breakpoints are in sp: halving the text scale makes a 1000 px page 2000 sp wide.
        result = self.probe(M9N_TEXT_SCALE="0.5")
        for key in self.COLUMN_PAGES:
            self.assertEqual(result[key]["columns"], 3, key)
        self.assertEqual(sum(result["dashboard"]["flow"]), 7)
        self.assertGreater(result["satellites"]["sky"], 260)


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


class MessagesTests(unittest.TestCase):
    def test_receiver_messages_and_application_log(self):
        with headless_display() as env:
            process, result = run_probe(env, "messages")
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        self.assertEqual(result["visible_before_session"], "content")
        receiver = result["receiver"]
        self.assertIn("GNTXT: Synthetic demonstration", receiver[0])
        self.assertTrue(receiver[-1].endswith("ERROR INF-ERROR: bad?[31m?text"), receiver[-1])
        self.assertGreater(result["store"], result["info_visible"])  # Debug lines hidden at Info.
        self.assertEqual(result["debug_visible"], result["store"])
        self.assertGreater(result["search_hits"], 0)
        self.assertTrue(result["search_all_match"])
        self.assertTrue(result["traceback_logged"])
        self.assertEqual(result["pending_before_flush"], 6000)  # Batched, not inserted one by one.
        self.assertEqual(result["bounded"], 5000)
        self.assertEqual(result["last_line"], "bulk 5999")
        self.assertTrue(result["following"])
        self.assertTrue(result["paused_at_top"])


class DeviceTestPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, **env):
        with tempfile.TemporaryDirectory() as out:
            process, result = run_probe(self.env, "devicetest", M9N_OUT=out, **env)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        self.assertTrue(result["empty_has_button"])
        self.assertEqual(result["mode"], "test")
        self.assertFalse(result["run_test_enabled_while_running"])
        self.assertEqual(result["running_view"], "running")
        self.assertIn("remaining", result["remaining"])
        self.assertEqual(result["visible_page"], "test")
        self.assertEqual(result["view"], "result")
        self.assertEqual(result["title"], result["report_status"])
        return result

    def test_healthy_demo_passes(self):
        result = self.probe()
        self.assertEqual(result["title"], "PASS")
        self.assertGreater(result["fraction"], 0)
        self.assertIn("no report file was configured", result["report_subtitle"])
        self.assertEqual(result["save_label"], "Save Report…")
        self.assertTrue(result["saved"] and result["saved_matches"])
        self.assertIn("saved-report.json", result["subtitle_after_save"])

    def test_rf_demo_warns_with_rf_detail(self):
        result = self.probe(M9N_FAULT="rf", M9N_TRIGGER="configured")
        self.assertEqual(result["title"], "WARN")
        self.assertIn("RF interference", result["checks"]["RF health"])
        self.assertIn("Saved to", result["report_subtitle"])
        self.assertEqual(result["save_label"], "Save a Copy…")
        self.assertTrue(result["configured_file_matches"])

    def test_cancel_is_interrupted(self):
        result = self.probe(M9N_TRIGGER="cancel", M9N_DURATION="60")
        self.assertEqual(result["title"], "INTERRUPTED")

    def test_failed_report_write_offers_save(self):
        result = self.probe(M9N_TRIGGER="write-fails")
        self.assertIn("failed", result["report_subtitle"])
        self.assertEqual(result["save_label"], "Save Report…")
        self.assertTrue(result["saved"] and result["saved_matches"])

    def test_unplug_fails_with_usb_transport(self):
        with Receiver(unplug_after=3.0) as receiver:  # Includes the probe's start-up time.
            result = self.probe(M9N_PORT=receiver.port, M9N_DURATION="30", M9N_TRIGGER="unplug")
        self.assertEqual(result["title"], "FAIL")
        self.assertIn("USB transport", result["checks"])


FAKE_TOOLS = {
    "systemctl": 'case "$2" in gpsd.service) printf "inactive\\ninactive\\n"; exit 3;; *) echo active;; esac',
    "udevadm": 'printf "ID_VENDOR_ID=1546\\nID_MM_DEVICE_IGNORE=1\\n"',
    "fuser": "sleep 10",  # Exceeds the 3 s command timeout.
}


class HostPageTests(unittest.TestCase):
    def run_host(self, session_port=None):
        with headless_display() as env, tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "bin"
            fake.mkdir()
            for name, body in FAKE_TOOLS.items():
                tool = fake / name
                tool.write_text("#!/bin/sh\n" + body + "\n")
                tool.chmod(0o755)
            for name in ("sh", "sleep", "printf"):
                (fake / name).symlink_to(__import__("shutil").which(name))
            port = session_port or str(Path(d) / "ttyACM0")
            if not session_port:
                Path(port).touch(mode=0)  # Exists but not accessible: shows the Grant Access commands.
            extra = {"M9N_SESSION_PORT": session_port} if session_port else {}
            process, result = run_probe(env, "host", M9N_HOST_PORT=port, M9N_FAKE_PATH=str(fake), M9N_OUT=d, **extra)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        return result

    def test_diagnostics_without_access(self):
        result = self.run_host()
        self.assertEqual(result["keys"], ["python", "kernel", "platform", "devices", "selected_port",
                                          "read_write_access", "selection_error", "cdc_acm_loaded", "lsusb",
                                          "gpsd", "modemmanager", "hints", "udev", "owners"])
        checks, icons = result["checks"], result["check_icons"]
        self.assertIn("no: see Grant Access", checks["access"])
        self.assertTrue(result["fix_visible"])
        self.assertEqual(checks["gpsd"], "gpsd.service: inactive, gpsd.socket: inactive")
        self.assertIn("udev tells it to ignore this receiver", checks["modemmanager"])
        self.assertEqual(icons["modemmanager"], "object-select-symbolic")
        self.assertEqual(checks["owners"], "timed out")
        self.assertEqual(result["outputs"]["lsusb"], "not installed")
        self.assertEqual(result["outputs"]["owners"], "did not complete")
        self.assertEqual(len(result["devices"]), 2)
        self.assertLess(result["max_gap_ms"], 500)  # The window kept running during the slow command.
        self.assertEqual(result["tty_fds"], 0)  # Host checks never open the tty.
        self.assertTrue(result["saved"])

    def test_works_while_a_session_holds_the_device(self):
        with Receiver() as receiver:
            result = self.run_host(session_port=receiver.port)
        self.assertEqual(result["tty_fds"], 1)  # Only the session's descriptor.
        self.assertEqual(result["checks"]["access"], "yes")
        self.assertFalse(result["fix_visible"])


class ReplayDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = headless_display()
        cls.env = cls.display.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.display.__exit__(None, None, None)

    def probe(self, kind, capture_bytes=None):
        from m9n.demo import sample
        with tempfile.TemporaryDirectory() as out:
            capture = Path(out) / "capture" / "c.ubx"
            capture.parent.mkdir()
            if capture_bytes is None:
                unit = b"".join(sample(1000 + i * 1000) for i in range(100))
                capture_bytes = unit * (6 * 1024 * 1024 // len(unit))  # ~6 MB
            capture.write_bytes(capture_bytes)
            process, result = run_probe(self.env, "replay", M9N_KIND=kind, M9N_CAPTURE=str(capture),
                                        M9N_OUT=out, timeout=60)
        self.assertIsNotNone(result, process.stderr)
        self.assertNotIn("timeout", result, process.stderr)
        self.assertEqual(result["files_open"], 0)  # Capture and outputs closed in every outcome.
        self.assertFalse(result["progress_visible_after"])
        return result

    def test_large_replay_is_responsive_and_cancellable(self):
        result = self.probe("cancel")
        self.assertEqual(result["reason"], "user_stopped")
        self.assertIn("REPLAY", result["banner"])
        low, high = result["fractions"]
        self.assertTrue(0 < high < 1, result["fractions"])
        self.assertLess(result["max_gap_ms"], 250)
        self.assertEqual(result["jsonl_lines"], 1)

    def test_complete_replay(self):
        from m9n.demo import sample
        result = self.probe("complete", b"".join(sample(1000 + i * 1000) for i in range(20)))
        self.assertEqual(result["reason"], "completed")
        self.assertTrue(any(t.startswith("Replay complete: 120 valid frames") for t in result["toasts"]),
                        result["toasts"])
        self.assertEqual(result["dashboard"], "content")
        self.assertIsNone(result["dialog"])

    def test_empty_capture(self):
        result = self.probe("empty", b"")
        self.assertEqual(result["dialog"], "Capture Is Empty")
        self.assertIn("empty", result["banner_after"])

    def test_capture_without_navigation(self):
        from m9n.protocol import ubx
        version = b"SPG 4.04".ljust(30, b"\0") + b"00190000".ljust(10, b"\0")
        result = self.probe("nonav", ubx(10, 4, version) * 3)
        self.assertEqual(result["dialog"], "No Navigation Found")
        self.assertIn("no navigation", result["banner_after"])

    def test_demo_fault_is_labelled(self):
        result = self.probe("demo", b"")
        self.assertIn("SYNTHETIC DEMO", result["banner"])
        self.assertGreater(result["checksum"], 0)  # The corrupt fault really corrupts frames.


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
            for warning in ("-CRITICAL **", "Source ID"):  # GLib/GTK criticals, not our own log lines.
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
