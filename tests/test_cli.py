import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import runpy
from pathlib import Path
from unittest.mock import patch

from m9n.cli import parser, validate_outputs
from m9n.demo import sample
from m9n.protocol import nmea
from m9n.hostinfo import choose_port
from m9n.transport import SerialPort

ROOT = Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "m9n", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=10)


class CLITests(unittest.TestCase):
    def test_help_and_version(self):
        self.assertEqual(cli("--help").returncode, 0)
        self.assertIn("1.0.0", cli("--version").stdout)

    def test_no_device_actionable(self):
        result = cli("test", "--port", "/dev/not-a-gnss-device", "--duration", ".1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("doctor", result.stderr)

    def test_demo_json(self):
        result = cli("demo", "--json", "--duration", ".2")
        self.assertEqual(result.returncode, 0)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(all(r["source"] == "SYNTHETIC DEMO" for r in records))
        self.assertTrue(records[-1]["navigation"]["fix_ok"])

    def test_demo_fault_json(self):
        result = cli("demo", "--json", "--fault", "no-fix", "--duration", ".1")
        record = json.loads(result.stdout.splitlines()[-1])
        self.assertFalse(record["navigation"]["fix_ok"])

    def test_replay_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            raw, logs = Path(d) / "capture.ubx", Path(d) / "out.jsonl"
            raw.write_bytes(sample())
            result = cli("replay", raw, "--json", "--json-log", logs)
            self.assertEqual(result.returncode, 0)
            self.assertIn("REPLAY", json.loads(result.stdout)["source"])
            self.assertEqual(len(logs.read_text().splitlines()), 1)

    def test_replay_empty_fails(self):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "empty.ubx"
            raw.touch()
            self.assertEqual(cli("replay", raw, "--json").returncode, 1)

    def test_distinct_outputs(self):
        args = parser().parse_args(["watch", "--raw-log", "same", "--json-log", "same"])
        with self.assertRaises(ValueError):
            validate_outputs(args)

    def test_replay_input_protected(self):
        args = parser().parse_args(["replay", "same", "--log-file", "same"])
        with self.assertRaises(ValueError):
            validate_outputs(args)

    def test_device_output_rejected(self):
        args = parser().parse_args(["watch", "--log-file", "/dev/null"])
        with self.assertRaises(ValueError):
            validate_outputs(args)

    def test_user_installer(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("pathlib.Path.home", return_value=Path(d)):
                runpy.run_path(str(ROOT / "scripts/install-user.py"), run_name="__main__")
            launcher = Path(d) / ".local/bin/m9n"
            result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertIn("1.0.0", result.stdout)

    def test_numbers_validated(self):
        for args in (("demo", "--interval", "nan"), ("test", "--duration", "-1"), ("demo", "--stale-after", "0")):
            self.assertEqual(cli(*args).returncode, 2)
        self.assertEqual(cli("test", "--duration", "0").returncode, 1)

    def test_auto_discovery_selection(self):
        with patch("m9n.hostinfo.devices", return_value=[{"vid": "1546", "port": "/dev/ttyACM0"}]):
            self.assertEqual(choose_port(), "/dev/ttyACM0")
        with patch("m9n.hostinfo.devices", return_value=[]):
            with self.assertRaises(OSError):
                choose_port()
        with patch("m9n.hostinfo.devices", return_value=[{"vid": "1546", "port": "a"}, {"vid": "1546", "port": "b"}]):
            with self.assertRaises(OSError):
                choose_port()

    def with_pty(self, fault=None, silent=False, unplug=False, passive=False):
        master, slave = pty.openpty()
        name = os.ttyname(slave)
        stopped = threading.Event()
        transmitted = bytearray()

        def receiver():
            start = time.monotonic()
            while not stopped.wait(.05):
                try:
                    ready, _, _ = select.select([master], [], [], 0)
                    if ready:
                        transmitted.extend(os.read(master, 4096))
                    elapsed = time.monotonic() - start
                    if unplug and elapsed > .4:
                        os.close(master)
                        return
                    if not silent:
                        raw = nmea("GNGGA,120000,5409.000,N,00427.000,W,1,08,1.2,25,M,0,M,,") if passive else sample(1000 + int(elapsed * 1000), fault)
                        os.write(master, raw)
                except OSError:
                    return

        thread = threading.Thread(target=receiver, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                report, raw = Path(d) / "report.json", Path(d) / "capture.ubx"
                args = ["test", "--port", name, "--duration", ".9", "--json", "--report", report, "--raw-log", raw]
                if passive:
                    args.append("--passive")
                result = cli(*args)
                saved = json.loads(report.read_text())
                self.assertEqual(saved, json.loads(result.stdout))
                return result, saved, raw.read_bytes(), bytes(transmitted)
        finally:
            stopped.set()
            thread.join(2)
            os.close(slave)
            if not unplug:
                os.close(master)

    def test_real_termios_pty_healthy(self):
        result, report, raw, sent = self.with_pty()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(raw)
        self.assertIn(b"\xb5\x62\x0a\x04", sent)

    def test_real_termios_pty_no_fix(self):
        result, report, _, _ = self.with_pty(fault="no-fix")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "WARN")

    def test_real_termios_pty_rf_alarm(self):
        result, report, _, _ = self.with_pty(fault="rf")
        self.assertEqual(result.returncode, 2)
        self.assertIn("RF interference", " ".join(report["snapshot"]["issues"]))

    def test_real_termios_pty_silent(self):
        result, report, raw, _ = self.with_pty(silent=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(raw, b"")

    def test_real_termios_pty_unplug(self):
        result, report, _, _ = self.with_pty(unplug=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["checks"][-1]["name"], "usb_transport")

    def test_real_termios_pty_passive(self):
        result, report, _, sent = self.with_pty(passive=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["snapshot"]["counters"].get("ubx_frames", 0), 0)
        # PTY can echo initial data before the CLI sets raw mode; no UBX polls are emitted.
        self.assertNotIn(b"\xb5\x62", sent)

    def test_transport_read_and_exclusive_lock(self):
        master, slave = pty.openpty()
        port = SerialPort(os.ttyname(slave))
        try:
            os.write(master, b"test")
            self.assertEqual(port.read(.2), b"test")
            port.write(b"reply")
            self.assertEqual(os.read(master, 5), b"reply")
            self.assertEqual(port.read(.01), b"")
            with self.assertRaises(OSError):
                SerialPort(os.ttyname(slave))
        finally:
            port.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
