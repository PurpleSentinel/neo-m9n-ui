import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from m9n import __version__

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def test_user_installer_launches_the_gui_entry_point(self):
        with tempfile.TemporaryDirectory() as d:
            self.install(Path(d))
            launcher = Path(d) / ".local/bin/m9n"
            self.assertIn("m9n.gui.application", launcher.read_text())
            result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"m9n {__version__}")

    def install(self, home, *args):
        with patch("pathlib.Path.home", return_value=home), patch("sys.argv", ["install-user.py", *args]):
            with self.assertRaises(SystemExit) as exit_:
                runpy.run_path(str(ROOT / "scripts/install-user.py"), run_name="__main__")
        self.assertEqual(exit_.exception.code, 0)

    def test_desktop_integration_upgrade_and_uninstall(self):
        app_id = "io.github.PurpleSentinel.NeoM9N"
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            share = home / ".local/share"
            (share / "neo-m9n-cli/m9n").mkdir(parents=True)  # A 1.x install.
            self.install(home)
            installed = [home / ".local/bin/m9n", share / f"applications/{app_id}.desktop",
                         share / f"metainfo/{app_id}.metainfo.xml",
                         share / f"icons/hicolor/scalable/apps/{app_id}.svg",
                         share / f"icons/hicolor/symbolic/apps/{app_id}-symbolic.svg",
                         share / "neo-m9n/m9n/gui/app.py", share / "neo-m9n/m9n/gui/data/icons"]
            for path in installed:
                self.assertTrue(path.exists(), path)
            self.assertFalse((share / "neo-m9n-cli").exists())
            desktop = (share / f"applications/{app_id}.desktop").read_text()
            self.assertIn(f"Exec={home / '.local/bin/m9n'}\n", desktop)
            if shutil.which("desktop-file-validate"):
                check = subprocess.run(["desktop-file-validate", str(share / f"applications/{app_id}.desktop")],
                                       capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            stale = share / "neo-m9n/m9n/removed_upstream.py"
            stale.write_text("")
            self.install(home)
            self.assertFalse(stale.exists())  # Upgrades replace the package instead of merging.
            self.install(home, "--uninstall")
            for path in installed:
                self.assertFalse(path.exists(), path)
            self.assertFalse((share / "neo-m9n").exists())

    def test_module_entry_point_version(self):
        result = subprocess.run([sys.executable, "-m", "m9n", "--version"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, f"m9n {__version__}"))

    def test_no_command_line_interface_remains(self):
        sources = "\n".join(p.read_text() for p in (ROOT / "m9n").rglob("*.py"))
        self.assertNotIn("argparse", sources)
        self.assertFalse((ROOT / "m9n/cli.py").exists())


if __name__ == "__main__":
    unittest.main()


class MetadataTests(unittest.TestCase):
    DATA = ROOT / "m9n/gui/data"

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "desktop-file-validate not installed")
    def test_desktop_file_is_valid(self):
        check = subprocess.run(["desktop-file-validate", str(self.DATA / "io.github.PurpleSentinel.NeoM9N.desktop")],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    @unittest.skipUnless(shutil.which("appstreamcli"), "appstreamcli not installed")
    def test_metainfo_is_valid(self):
        check = subprocess.run(["appstreamcli", "validate", "--no-net",
                                str(self.DATA / "io.github.PurpleSentinel.NeoM9N.metainfo.xml")],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_metainfo_release_matches_version(self):
        from m9n import __version__
        self.assertIn(f'<release version="{__version__}"',
                      (self.DATA / "io.github.PurpleSentinel.NeoM9N.metainfo.xml").read_text())
