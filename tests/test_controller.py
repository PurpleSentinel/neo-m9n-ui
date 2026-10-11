import json
import os
import pty
import select
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from m9n.controller import (COMPLETED, INTERRUPTED, OUTPUT_ERROR, TRANSPORT_ERROR, USER_STOPPED,
                            SessionConfig, SessionController, validate)
from m9n.demo import sample
from m9n.transport import SerialPort
from ptysim import Receiver, run_until_finished


def open_fds():
    return set(os.listdir("/proc/self/fd"))


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ControllerDeviceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)

    def device_test(self, receiver, **overrides):
        config = SessionConfig(source="device", mode="test", port=receiver.port, duration=.9,
                               report=self.path / "report.json", json_log=self.path / "log.jsonl",
                               raw_log=self.path / "rx.ubx", log_file=self.path / "app.log")
        for key, value in overrides.items():
            setattr(config, key, value)
        before = open_fds()
        controller = SessionController(config)
        controller.start()
        result = run_until_finished(controller)
        controller.close()
        self.assertFalse(open_fds() - before, "file descriptors leaked")
        self.assert_released(receiver.port)
        return result

    def assert_released(self, port):
        # flock + TIOCEXCL succeed again only if the previous session let go.
        # An unplugged node disappears; the fd-leak check covers that case.
        if os.path.exists(port):
            SerialPort(port).close()

    def test_healthy_pass_and_evidence(self):
        with Receiver() as receiver:
            result = self.device_test(receiver)
            sent = bytes(receiver.transmitted)
        self.assertEqual(result.reason, COMPLETED)
        self.assertEqual(result.report["status"], "PASS")
        self.assertIn(b"\xb5\x62\x0a\x04", sent)
        self.assertEqual(json.loads((self.path / "report.json").read_text()), result.report)
        lines = (self.path / "log.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(lines[-1])["kind"], "test_report")
        self.assertTrue((self.path / "rx.ubx").read_bytes())
        self.assertIn("Opened", (self.path / "app.log").read_text())

    def test_fault_outcomes(self):
        cases = (("no-fix", {}, "WARN"), ("rf", {}, "WARN"), ("frozen", {"stale_after": .3}, "FAIL"))
        for fault, overrides, status in cases:
            with self.subTest(fault=fault):
                for name in ("report.json", "log.jsonl", "rx.ubx", "app.log"):
                    (self.path / name).unlink(missing_ok=True)
                with Receiver(fault=fault) as receiver:
                    self.assertEqual(self.device_test(receiver, **overrides).report["status"], status)

    def test_silent_fails(self):
        with Receiver(silent=True) as receiver:
            result = self.device_test(receiver)
        self.assertEqual(result.report["status"], "FAIL")
        self.assertEqual((self.path / "rx.ubx").read_bytes(), b"")

    def test_unplug_fails_and_releases(self):
        with Receiver(unplug_after=.3) as receiver:
            result = self.device_test(receiver)
        self.assertEqual(result.reason, TRANSPORT_ERROR)
        self.assertEqual(result.report["checks"][-1]["name"], "usb_transport")
        self.assertEqual(json.loads((self.path / "report.json").read_text())["status"], "FAIL")

    def test_passive_sends_nothing(self):
        with Receiver() as receiver:
            result = self.device_test(receiver, passive=True)
            sent = bytes(receiver.transmitted)
        self.assertNotIn(b"\xb5\x62", sent)
        self.assertEqual(result.report["snapshot"]["poll_timeouts"], {})

    def test_user_stop_is_interrupted_and_flushed(self):
        with Receiver() as receiver:
            before = open_fds()
            config = SessionConfig(source="device", mode="test", port=receiver.port, duration=60,
                                   report=self.path / "report.json", json_log=self.path / "log.jsonl",
                                   raw_log=self.path / "rx.ubx")
            finished = []
            controller = SessionController(config, on_finished=finished.append)
            controller.start()
            deadline = time.monotonic() + .5
            while time.monotonic() < deadline:
                if select.select([controller.fileno()], [], [], .05)[0]:
                    controller.on_readable()
                controller.tick()
            result = controller.stop(USER_STOPPED)
            self.assertFalse(open_fds() - before)
            self.assert_released(receiver.port)
        self.assertEqual(finished, [result])
        self.assertEqual(result.report["status"], "INTERRUPTED")
        self.assertEqual(result.report["exit_code"], 130)
        self.assertEqual(json.loads((self.path / "log.jsonl").read_text().splitlines()[-1])["status"], "INTERRUPTED")
        self.assertTrue((self.path / "rx.ubx").read_bytes())

    def test_close_while_running_interrupts(self):
        with Receiver() as receiver:
            controller = SessionController(SessionConfig(port=receiver.port))
            controller.start()
            controller.close()
            controller.close()
            self.assertEqual(controller.result.reason, INTERRUPTED)
            self.assert_released(receiver.port)

    def test_failed_open_creates_no_outputs(self):
        raw = self.path / "rx.ubx"
        config = SessionConfig(port=str(self.path / "missing-tty"), raw_log=raw, json_log=self.path / "a.jsonl")
        for _ in range(2):
            with self.assertRaises(FileNotFoundError):
                SessionController(config).start()
        self.assertFalse(raw.exists())
        self.assertFalse((self.path / "a.jsonl").exists())


class ControllerValidationTests(unittest.TestCase):
    def test_missing_output_directory_rejected_before_start(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate(SessionConfig(mode="test", duration=1, report=Path("/nonexistent-dir/report.json")))

    def test_existing_raw_capture_rejected(self):
        with tempfile.NamedTemporaryFile() as handle:
            with self.assertRaisesRegex(ValueError, "never overwritten"):
                validate(SessionConfig(raw_log=Path(handle.name)))

    def test_output_paths_must_be_distinct_and_safe(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            validate(SessionConfig(raw_log=Path("same"), json_log=Path("same")))
        with self.assertRaisesRegex(ValueError, "replay capture"):
            validate(SessionConfig(source="replay", capture=Path("same"), log_file=Path("same")))
        with self.assertRaisesRegex(ValueError, "regular file"):
            validate(SessionConfig(log_file=Path("/dev/null")))

    def test_invalid_combinations(self):
        for config in (SessionConfig(mode="test"), SessionConfig(source="replay", mode="test", duration=1),
                       SessionConfig(source="replay"), SessionConfig(fault="rf"),
                       SessionConfig(source="demo", raw_log=Path("x.ubx")), SessionConfig(report=Path("r.json")),
                       SessionConfig(interval=float("nan")), SessionConfig(source="bogus")):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate(config)


class ControllerOfflineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)

    def test_demo_snapshots_follow_interval(self):
        clock = FakeClock()
        snapshots, issues = [], []
        config = SessionConfig(source="demo", duration=3, interval=.5, json_log=self.path / "d.jsonl")
        controller = SessionController(config, clock=clock, on_snapshot=snapshots.append,
                                       on_issue=lambda kind, text: issues.append(kind))
        controller.start()
        while controller.running:
            controller.tick()
            clock.now += .25  # Exact in binary floating point.
        self.assertEqual(len(snapshots), 6)
        self.assertTrue(snapshots[-1]["navigation"]["fix_ok"])
        self.assertEqual(len((self.path / "d.jsonl").read_text().splitlines()), 7)
        self.assertEqual(controller.progress(), 1.0)

    def test_demo_test_mode_fault(self):
        clock = FakeClock()
        controller = SessionController(SessionConfig(source="demo", mode="test", fault="rf", duration=2), clock=clock)
        controller.start()
        while controller.running:
            controller.tick()
            clock.now += .25
        self.assertEqual(controller.result.report["status"], "WARN")

    def test_report_write_failure_keeps_result(self):
        clock = FakeClock()
        target = self.path / "reports"
        target.mkdir()
        controller = SessionController(SessionConfig(source="demo", mode="test", duration=1,
                                                     report=target / "r.json"), clock=clock)
        controller.start()
        target.rmdir()
        clock.now += 2
        controller.tick()
        self.assertEqual(controller.result.report["kind"], "test_report")
        self.assertIn("Report not written", controller.result.output_errors[0])

    def test_replay_progress_and_single_jsonl_line(self):
        capture = self.path / "c.ubx"
        capture.write_bytes(sample() * 50)
        config = SessionConfig(source="replay", capture=capture, replay_chunk=1000, json_log=self.path / "r.jsonl")
        controller = SessionController(config)
        controller.start()
        seen = []
        while controller.running:
            controller.tick()
            seen.append(controller.progress())
        self.assertEqual(seen[-1], 1.0)
        self.assertLess(seen[0], .1)
        self.assertEqual(controller.result.reason, COMPLETED)
        self.assertEqual(len((self.path / "r.jsonl").read_text().splitlines()), 1)
        self.assertIsNotNone(controller.result.snapshot["navigation"])

    def test_jsonl_write_failure_stops_session(self):
        clock = FakeClock()
        controller = SessionController(SessionConfig(source="demo", json_log=self.path / "j.jsonl"), clock=clock)
        controller.start()

        class Broken:
            name = "broken"

            def write(self, _):
                raise OSError(28, "No space left on device")

            def close(self):
                pass

        controller._jsonl.close()
        controller._jsonl = Broken()
        controller.tick()
        self.assertEqual(controller.result.reason, OUTPUT_ERROR)
        self.assertIn("No space", controller.result.output_errors[0])


class TransportTests(unittest.TestCase):
    def test_hangup_drains_pending_data(self):
        master, slave = pty.openpty()
        port = SerialPort(os.ttyname(slave))

        class HungUp:
            def register(self, *_):
                pass

            def poll(self, _):
                return [(port.fd, select.POLLIN | select.POLLHUP)]

        reads = iter([b"last words", b""])
        try:
            with patch("m9n.transport.select.poll", HungUp), \
                    patch("m9n.transport.os.read", lambda fd, n: next(reads)):
                self.assertEqual(port.read(.2), b"last words")
                with self.assertRaises(OSError):
                    port.read(.2)
        finally:
            port.close()
            os.close(master)
            os.close(slave)

    def test_read_write_and_exclusive_lock(self):
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

    def test_write_never_blocks_and_times_out(self):
        master, slave = pty.openpty()
        port = SerialPort(os.ttyname(slave), write_timeout=.05)
        try:
            for _ in range(20):  # Fill the tty queues; nobody reads the master.
                try:
                    while True:
                        os.write(port.fd, b"\0" * 4096)
                except BlockingIOError:
                    time.sleep(.02)
            started = time.monotonic()
            port.write(b"poll")
            self.assertLess(time.monotonic() - started, .05)
            self.assertTrue(port.tx)
            time.sleep(.1)
            with self.assertRaises(TimeoutError):
                port.flush()
        finally:
            port.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
