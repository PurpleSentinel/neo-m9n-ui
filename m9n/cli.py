"""Command-line entry point, Linux host diagnostics and lifecycle handling."""
import argparse
import logging
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .controller import FORMATTER, INTERRUPTED, SessionConfig, SessionController, run_until_finished, validate
from .display import render, report_text
from .serialize import json_line, safe
from .transport import choose_port, devices

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


def setup_logging(args, interactive):
    # Logs must not overwrite a live dashboard. A log file keeps the full verbosity.
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.ERROR if interactive else logging.DEBUG if args.debug else logging.INFO)
    console.setFormatter(FORMATTER)
    LOG.handlers.clear()
    LOG.propagate = False
    LOG.addHandler(console)


def config_from_args(args):
    command = args.command
    return SessionConfig(
        source={"watch": "device", "test": "device"}.get(command, command),
        mode="test" if command == "test" else "watch",
        port=getattr(args, "port", None), baud=getattr(args, "baud", 9600),
        passive=getattr(args, "passive", False), fault=getattr(args, "fault", None),
        capture=getattr(args, "capture", None), duration=getattr(args, "duration", 0.0),
        interval=args.interval, stale_after=args.stale_after, debug=args.debug,
        raw_log=getattr(args, "raw_log", None), json_log=args.json_log, log_file=args.log_file,
        report=getattr(args, "report", None))


def validate_outputs(args):
    validate(config_from_args(args))


def run(args):
    config = config_from_args(args)
    validate(config)
    interactive = args.command != "test" and sys.stdout.isatty() and not args.plain and not args.json

    def show(snapshot):
        if args.command == "test" and args.json:
            # Test JSON stdout is exactly one final report, suitable for jq/automation.
            return
        print(json_line(snapshot) if args.json else render(snapshot, interactive), flush=True)

    setup_logging(args, interactive)
    controller = SessionController(config, on_snapshot=show)
    controller.start()
    if interactive:
        print("\x1b[?25l", end="", flush=True)
    try:
        try:
            run_until_finished(controller)
        except KeyboardInterrupt:
            controller.stop(INTERRUPTED)
    finally:
        controller.close()
        if interactive:
            print("\x1b[?25h", flush=True)
    result = controller.result
    if args.command == "test":
        print(json_line(result.report) if args.json else report_text(result.report), flush=True)
        code = result.report["exit_code"]
    else:
        print(json_line(result.snapshot) if args.json else render(result.snapshot, False), flush=True)
        if result.transport_error:
            code = 1
        elif result.reason == INTERRUPTED:
            code = 130
        elif args.command == "replay":
            code = 0 if result.received_bytes and result.snapshot["navigation"] else 1
        elif args.command == "watch":
            code = 0 if result.valid_frames else 1
        else:
            code = 0
    for error in result.output_errors:
        print(f"ERROR: {safe(error)}", file=sys.stderr)
    return 1 if result.output_errors and code in (0, 2) else code


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
