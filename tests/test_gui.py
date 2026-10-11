import subprocess
import sys
import unittest
from unittest.mock import patch

from gui_support import ROOT, gui_runtime_missing, headless_display, run_probe


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
        self.assertFalse(result["toggle_enabled"])
        self.assertEqual(result["accels"]["app.quit"], ["<Control>q"])
        self.assertEqual(result["accels"]["win.toggle-session"], ["F5"])
        self.assertEqual(result["about"], "AboutDialog")
        self.assertIn(result["shortcuts"], ("ShortcutsDialog", "AlertDialog"))
        self.assertTrue(result["banner_shown"] and result["banner_hidden"])
        self.assertEqual(result["visible_page"], "host")
        self.assertFalse(result["bottom_switcher"])
        self.assertTrue(result["header_switcher"])

    def test_narrow_layout_moves_switcher_to_bottom(self):
        result = self.probe("shell", M9N_PROBE_WIDTH="380")
        self.assertTrue(result["bottom_switcher"])
        self.assertFalse(result["header_switcher"])

    def test_second_launch_presents_existing_instance(self):
        result = self.probe("unique")
        self.assertEqual(result["second_exit"], 0)
        self.assertEqual(result["activations"], 2)
        self.assertEqual(result["windows"], 1)


if __name__ == "__main__":
    unittest.main()
