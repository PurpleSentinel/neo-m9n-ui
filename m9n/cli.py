"""Command-line entry point, Linux host diagnostics and lifecycle handling."""
import argparse
import contextlib
import logging
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .demo import sample
from .display import json_line, render, report_text, safe
from .session import Session
from .transport import SerialPort, choose_port, devices

LOG = logging.getLogger("m9n")


def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return result


def duration(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return result


def parser():
    p = argparse.ArgumentParser(description="Fedora CLI diagnostics for SparkFun u-blox NEO-M9N over USB")
    p.add_argument("--version", action="version", version=f"m9n {__version__}")
    subs = p.add_subparsers(dest="command", required=True)
    ls = subs.add_parser("list", help="List USB serial ports and USB identity")
    ls.add_argument("--json", action="store_true")
    doctor = subs.add_parser("doctor", help="Read-only Fedora/USB/access/service diagnostics")
    doctor.add_argument("--port")
    doctor.add_argument("--json", action="store_true")
    for command, help_text in (("watch", "Live receiver dashboard"), ("test", "Timed receiver diagnostic report"),
                               ("demo", "Synthetic live data; no hardware required"), ("replay", "Decode a saved raw RX capture")):
        s = subs.add_parser(command, help=help_text)
        s.add_argument("--json", action="store_true", help="Emit JSON to stdout; diagnostics stay on stderr")
        s.add_argument("--plain", action="store_true", help="Disable terminal redraw")
        s.add_argument("--interval", type=positive, default=1.0, help="Display/snapshot interval in seconds (default 1)")
        s.add_argument("--stale-after", type=positive, default=5.0, help="Navigation age/epoch timeout in seconds (default 5)")
        s.add_argument("--debug", action="store_true", help="Verbose RX/TX/decoded diagnostics")
        s.add_argument("--log-file", type=Path, help="Write application/debug logs to this file")
        s.add_argument("--json-log", type=Path, help="Append JSONL snapshots (and test report) to this file")
        if command in ("watch", "test"):
            s.add_argument("--port", help="tty path; auto-selects a single u-blox VID 1546 device")
            s.add_argument("--baud", type=int, default=9600, help="termios line setting; native USB is not UART-rate limited")
            s.add_argument("--passive", action="store_true", help="Listen without transmitting any UBX polls")
            s.add_argument("--raw-log", type=Path, help="Save exact received bytes (exclusive new file)")
            s.add_argument("--duration", type=duration, default=60.0 if command == "test" else 0.0,
                           help="Seconds; 0 means until Ctrl-C for watch; test requires >0")
            if command == "test":
                s.add_argument("--report", type=Path, help="Write final JSON test report")
        elif command == "demo":
            s.add_argument("--duration", type=duration, default=10.0)
            s.add_argument("--fault", choices=("no-fix", "rf", "corrupt", "frozen", "silent"))
        else:
            s.add_argument("capture", type=Path)
    return p


def setup_logging(args, stack, interactive):
    handlers = []
    if args.log_file:
        handler = logging.FileHandler(args.log_file, encoding="utf-8")
        stack.callback(handler.close)
        handlers.append(handler)
    # Logs must not overwrite a live dashboard. File handler retains verbose data.
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.ERROR if interactive else logging.DEBUG if args.debug else logging.INFO)
    handlers.append(console)
    LOG.handlers.clear()
    LOG.propagate = False
    LOG.setLevel(logging.DEBUG if args.debug else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
        LOG.addHandler(handler)


def emit(snapshot, args, interactive, json_log):
    if json_log:
        json_log.write(json_line(snapshot) + "\n")
        json_log.flush()
    if args.command == "test" and args.json:
        # Test JSON stdout is exactly one final report, suitable for jq/automation.
        return
    print(json_line(snapshot) if args.json else render(snapshot, interactive), flush=True)


def same_file(a, b):
    if a.resolve() == b.resolve():
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def validate_outputs(args):
    outputs = [getattr(args, key, None) for key in ("raw_log", "json_log", "log_file", "report")]
    outputs = [p for p in outputs if p is not None]
    for i, path in enumerate(outputs):
        if path.exists() and not path.is_file():
            raise ValueError(f"Output must be a regular file, not a device or directory: {path}")
        for other in outputs[i + 1:]:
            if same_file(path, other):
                raise ValueError("Each output must have a distinct file path")
        if args.command == "replay" and same_file(path, args.capture):
            raise ValueError("Output path must not overwrite the replay capture")
        if args.command in ("watch", "test") and args.port and same_file(path, Path(args.port)):
            raise ValueError("Output path must not be the serial device")


def run(args):
    if args.command == "test" and not args.duration:
        raise ValueError("test --duration must be greater than zero")
    validate_outputs(args)
    started = time.monotonic()
    interactive = args.command != "test" and sys.stdout.isatty() and not args.plain and not args.json
    with contextlib.ExitStack() as stack:
        setup_logging(args, stack, interactive)
        json_log = stack.enter_context(args.json_log.open("a", encoding="utf-8")) if args.json_log else None
        raw_log = stack.enter_context(args.raw_log.open("xb")) if getattr(args, "raw_log", None) else None
        port = None
        capture = None
        if args.command in ("watch", "test"):
            selected = choose_port(args.port)
            port = SerialPort(selected, args.baud)
            stack.callback(port.close)
            source = selected
            LOG.info("Opened %s; %s", selected, "passive mode" if args.passive else "diagnostic polls enabled; receiver configuration unchanged")
        elif args.command == "replay":
            capture = stack.enter_context(args.capture.open("rb"))
            source = "REPLAY (recorded data): " + str(args.capture)
        else:
            source = "SYNTHETIC DEMO"
        session = Session(started, args.stale_after, getattr(args, "passive", False) or port is None, source)
        next_display = started
        next_demo = started
        previous_issues = set()
        received_something = False
        interrupted = False
        transport_error = None
        if interactive:
            print("\x1b[?25l", end="", flush=True)
        try:
            while True:
                now = time.monotonic()
                if args.command != "replay" and args.duration and now - started >= args.duration:
                    break
                if port:
                    try:
                        session.poll(port, now)
                        data = port.read(0.1)
                    except OSError as exc:
                        transport_error = str(exc)
                        LOG.error("USB transport failed: %s", safe(exc))
                        break
                    now = time.monotonic()
                elif capture:
                    data = capture.read(256)
                    if not data:
                        # Flush any partial last frame without real-time waiting.
                        session.consume(b"", now + session.framer.timeout)
                        break
                else:
                    data = b""
                    if now >= next_demo:
                        epoch = 1000 if args.fault == "frozen" else 1000 + int((now - started) * 1000)
                        data = b"" if args.fault == "silent" else sample(epoch, args.fault)
                        next_demo = now + 1
                    time.sleep(0.05)
                if raw_log and data:
                    raw_log.write(data)
                    raw_log.flush()
                received_something |= bool(data)
                session.consume(data, now)
                issues = set(session.state.issues(now, session.framer))
                for issue in sorted(issues - previous_issues):
                    LOG.warning("%s", issue)
                for issue in sorted(previous_issues - issues):
                    LOG.info("Resolved: %s", issue)
                previous_issues = issues
                if now >= next_display and not capture:
                    emit(session.snapshot(now), args, interactive, json_log)
                    next_display = now + args.interval
        except KeyboardInterrupt:
            interrupted = True
            LOG.info("Stopped by user")
        finally:
            if interactive:
                print("\x1b[?25h", flush=True)
        now = time.monotonic()
        if args.command == "test":
            report = session.report(now)
            if transport_error:
                report["checks"].append({"name": "usb_transport", "status": "FAIL", "detail": transport_error})
                report["status"] = "FAIL"
                report["exit_code"] = 1
            if interrupted:
                report["status"] = "INTERRUPTED"
                report["exit_code"] = 130
            if args.report:
                args.report.write_text(json_line(report) + "\n", encoding="utf-8")
            if json_log:
                json_log.write(json_line(report) + "\n")
                json_log.flush()
            print(json_line(report) if args.json else report_text(report), flush=True)
            return report["exit_code"]
        emit(session.snapshot(now), args, False, json_log)
        if transport_error:
            return 1
        if interrupted:
            return 130
        if args.command == "replay":
            return 0 if received_something and session.state.navigation(now) else 1
        if args.command == "watch":
            return 0 if session.framer.counts["ubx_frames"] + session.framer.counts["nmea_frames"] else 1
        return 0


def command_output(command):
    if not shutil.which(command[0]):
        return {"available": False, "command": command, "output": "Optional command not installed"}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        return {"available": True, "command": command, "exit_code": result.returncode,
                "output": (result.stdout + result.stderr).strip()[:16000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "command": command, "output": str(exc)}


def doctor(args):
    rows = devices()
    port = args.port
    error = None
    if not port:
        try:
            port = choose_port()
        except OSError as exc:
            error = str(exc)
    accessible = bool(port and os.access(port, os.R_OK | os.W_OK))
    data = {"python": platform.python_version(), "kernel": platform.release(), "platform": platform.system(),
            "devices": rows, "selected_port": port, "read_write_access": accessible,
            "selection_error": error, "cdc_acm_loaded": Path("/sys/module/cdc_acm").exists(),
            "lsusb": command_output(["lsusb"]),
            "gpsd": command_output(["systemctl", "is-active", "gpsd.service", "gpsd.socket"]),
            "modemmanager": command_output(["systemctl", "is-active", "ModemManager.service"]),
            "hints": ["Use a USB data cable and connect the GNSS board's USB port.",
                      "If USB enumerates but no tty appears, inspect 'journalctl -k -b' and try 'sudo modprobe cdc_acm'.",
                      "For access errors, inspect 'ls -l /dev/ttyACM0' and install the supplied uaccess udev rule or join the actual tty owning group.",
                      "If another process owns the port, stop only the relevant gpsd service/socket or exclude this device from ModemManager.",
                      "No position fix alone does not establish a hardware fault; check antenna and sky view."]}
    if port:
        data["udev"] = command_output(["udevadm", "info", "--query=property", "--name=" + port])
        data["owners"] = command_output(["fuser", "-v", port])
    if args.json:
        print(json_line(data))
    else:
        print(f"Fedora host diagnostics | Python {data['python']} | kernel {data['kernel']}")
        print(f"Port: {safe(port)} | read/write access: {accessible} | cdc_acm loaded: {data['cdc_acm_loaded']}")
        if error:
            print(safe(error))
        for key in ("lsusb", "udev", "owners", "gpsd", "modemmanager"):
            if key in data:
                print(f"\n{key}:\n{safe(data[key]['output'])}")
        print("\n" + "\n".join(data["hints"]))
    return 0 if accessible else 1


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            rows = devices()
            if args.json:
                print(json_line(rows))
            elif not rows:
                print("No USB serial ports found. Run 'm9n doctor' for USB/kernel diagnostics.")
            else:
                for row in rows:
                    print(safe(f"{row['port']}  {row['vid']}:{row['pid']}  {row['product']}  serial={row['serial']}"))
            return 0
        if args.command == "doctor":
            return doctor(args)
        return run(args)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {safe(exc)}", file=sys.stderr)
        if args.command in ("watch", "test"):
            print("Run 'm9n doctor --port /dev/ttyACM0'. Check cable, access permissions, port ownership and capture paths. See docs/TROUBLESHOOTING.md.", file=sys.stderr)
        if getattr(args, "debug", False):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
