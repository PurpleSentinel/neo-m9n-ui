import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from m9n import hostinfo

DOCTOR_KEYS = ["python", "kernel", "platform", "devices", "selected_port", "read_write_access",
               "selection_error", "cdc_acm_loaded", "lsusb", "gpsd", "modemmanager", "hints"]


class FakeHost:
    """Minimal sysfs/devfs layout: tty -> USB interface -> USB device attributes."""

    def __init__(self, root):
        self.sys, self.dev = root / "sys", root / "dev"
        (self.sys / "class/tty").mkdir(parents=True)
        (self.dev / "serial/by-id").mkdir(parents=True)

    def add_tty(self, name, vid=None, by_id=None, product="u-blox GNSS receiver"):
        usb = self.sys / "devices/pci0000:00/usb1" / name
        interface = usb / "1-1:1.0"
        interface.mkdir(parents=True)
        if vid:
            for filename, value in (("idVendor", vid), ("idProduct", "01a9"), ("serial", "SN1"),
                                    ("product", product), ("manufacturer", "u-blox AG")):
                (usb / filename).write_text(value + "\n")
        entry = self.sys / "class/tty" / name
        entry.mkdir()
        (entry / "device").symlink_to(interface)
        (self.dev / name).touch()
        if by_id:
            (self.dev / "serial/by-id" / by_id).symlink_to(Path("../..") / name)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.host = FakeHost(Path(self.dir.name))

    def rows(self):
        return hostinfo.devices(self.host.sys, self.host.dev)

    def test_usb_identity_and_by_id_alias(self):
        self.host.add_tty("ttyACM0", "1546", by_id="usb-u-blox_AG_GNSS_receiver-if00")
        self.host.add_tty("ttyS0")
        rows = self.rows()
        self.assertEqual(len(rows), 1, "only ttyACM/ttyUSB are listed")
        row = rows[0]
        self.assertEqual((row["vid"], row["pid"], row["serial"]), ("1546", "01a9", "SN1"))
        self.assertEqual(row["port"], str(self.host.dev / "ttyACM0"))
        self.assertEqual(row["by_id"], [str(self.host.dev / "serial/by-id/usb-u-blox_AG_GNSS_receiver-if00")])

    def test_device_without_usb_parent(self):
        self.host.add_tty("ttyUSB3")
        row = self.rows()[0]
        self.assertIsNone(row["vid"])
        self.assertEqual(row["by_id"], [])

    def test_broken_alias_ignored(self):
        (self.host.dev / "serial/by-id/stale").symlink_to("../../ttyACM9")
        self.assertEqual(hostinfo.by_id_aliases(self.host.dev), {})

    def test_selection_rules(self):
        self.host.add_tty("ttyACM0", "1546")
        self.host.add_tty("ttyUSB0", "0403")
        rows = self.rows()
        self.assertEqual(hostinfo.choose_port(rows=rows), rows[0]["port"])
        self.assertEqual(hostinfo.choose_port("/dev/explicit", rows), "/dev/explicit")
        self.host.add_tty("ttyACM1", "1546")
        port, error = hostinfo.select_port(rows=self.rows())
        self.assertIsNone(port)
        self.assertIn("Multiple", error)
        self.assertIn("No u-blox", hostinfo.select_port(rows=[])[1])


class DiagnosticsTests(unittest.TestCase):
    def test_commands_depend_on_port(self):
        self.assertEqual(set(hostinfo.commands()), {"lsusb", "gpsd", "modemmanager"})
        specs = hostinfo.commands("/dev/ttyACM0")
        self.assertEqual(specs["owners"], ["fuser", "-v", "/dev/ttyACM0"])
        self.assertIn("--name=/dev/ttyACM0", specs["udev"])

    def test_command_results(self):
        self.assertFalse(hostinfo.run_command(["definitely-not-a-command-m9n"])["available"])
        result = hostinfo.run_command(["sh", "-c", "echo out; echo err >&2; exit 3"])
        self.assertEqual((result["exit_code"], result["output"]), (3, "out\nerr"))
        self.assertEqual(len(hostinfo.command_result(["x"], stdout="a" * 20000)["output"]), hostinfo.OUTPUT_LIMIT)
        with patch("m9n.hostinfo.COMMAND_TIMEOUT", .1):
            self.assertIn("timed out", hostinfo.run_command(["sleep", "2"])["output"])

    def test_document_schema_matches_doctor(self):
        results = {key: hostinfo.command_result(cmd, exit_code=0) for key, cmd in hostinfo.commands("/dev/x").items()}
        data = hostinfo.diagnostics("/dev/x", None, [], results)
        self.assertEqual(list(data), DOCTOR_KEYS + ["udev", "owners"])
        self.assertFalse(data["read_write_access"])
        without_port = hostinfo.diagnostics(None, "none", [], {k: results[k] for k in ("lsusb", "gpsd", "modemmanager")})
        self.assertEqual(list(without_port), DOCTOR_KEYS)

    def test_access_check(self):
        with tempfile.NamedTemporaryFile() as handle:
            self.assertTrue(hostinfo.accessible(handle.name))
            os.chmod(handle.name, 0)
            if os.geteuid() != 0:
                self.assertFalse(hostinfo.accessible(handle.name))
        self.assertFalse(hostinfo.accessible(None))


if __name__ == "__main__":
    unittest.main()
