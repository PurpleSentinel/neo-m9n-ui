import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def test_user_installer_launches_the_gui_entry_point(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("pathlib.Path.home", return_value=Path(d)):
                runpy.run_path(str(ROOT / "scripts/install-user.py"), run_name="__main__")
            launcher = Path(d) / ".local/bin/m9n"
            self.assertIn("m9n.gui.application", launcher.read_text())
            result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "m9n 2.0.0")

    def test_module_entry_point_version(self):
        result = subprocess.run([sys.executable, "-m", "m9n", "--version"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "m9n 2.0.0"))

    def test_no_command_line_interface_remains(self):
        sources = "\n".join(p.read_text() for p in (ROOT / "m9n").rglob("*.py"))
        self.assertNotIn("argparse", sources)
        self.assertFalse((ROOT / "m9n/cli.py").exists())


if __name__ == "__main__":
    unittest.main()
