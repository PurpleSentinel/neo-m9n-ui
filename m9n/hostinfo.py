"""Read-only Linux host inventory: USB ttys, access, kernel driver and services.

Discovery and the diagnostics document are pure functions. Running the external
commands is left to the caller (blocking for tests, Gio.Subprocess in the GUI);
every command result goes through command_result() so both look the same.
"""
import os
import platform
import shutil
import subprocess
from pathlib import Path

UBLOX_VID = "1546"
COMMAND_TIMEOUT = 3
OUTPUT_LIMIT = 16000
HINTS = ["Use a USB data cable and connect the GNSS board's USB port.",
         "If USB enumerates but no tty appears, inspect 'journalctl -k -b' and try 'sudo modprobe cdc_acm'.",
         "For access errors, inspect 'ls -l /dev/ttyACM0' and install the supplied uaccess udev rule or join the actual tty owning group.",
         "If another process owns the port, stop only the relevant gpsd service/socket or exclude this device from ModemManager.",
         "No position fix alone does not establish a hardware fault; check antenna and sky view."]

UDEV_RULE_PATH = "/etc/udev/rules.d/70-ublox-gnss.rules"
# Same rule as udev/70-ublox-gnss.rules in the repository (a test keeps them identical).
UDEV_RULE = 'SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"'


def udev_install_commands():
    """Commands the user runs themselves; the application never escalates privileges."""
    return [f"echo '{UDEV_RULE}' | sudo tee {UDEV_RULE_PATH}",
            "sudo udevadm control --reload-rules",
            "# Then unplug and reconnect the receiver."]


def unit_states(result):
    """Pair `systemctl is-active` output lines with their units, or None if unavailable."""
    if not result.get("available") or "exit_code" not in result:
        return None
    units = [arg for arg in result["command"][2:]]
    lines = result["output"].splitlines()
    return [(unit, lines[i] if i < len(lines) else "unknown") for i, unit in enumerate(units)]


def owners_text(result):
    """`fuser -v` exits 1 with no output when no process holds the port."""
    if not result.get("available"):
        return "fuser not installed"
    if result.get("exit_code") == 1 and not result["output"]:
        return "none"
    return result["output"] or "none"


def modemmanager_ignores(document):
    """True when udev marks the selected port ID_MM_DEVICE_IGNORE=1, so ModemManager leaves it alone."""
    udev = document.get("udev") or {}
    return "ID_MM_DEVICE_IGNORE=1" in udev.get("output", "").splitlines()


def by_id_aliases(dev_root=Path("/dev")):
    """Map each real tty path to its stable /dev/serial/by-id names."""
    aliases = {}
    for link in sorted((dev_root / "serial/by-id").glob("*")):
        try:
            target = str(link.resolve(strict=True))
        except OSError:
            continue
        aliases.setdefault(target, []).append(str(link))
    return aliases


def devices(sys_root=Path("/sys"), dev_root=Path("/dev")):
    rows = []
    aliases = by_id_aliases(dev_root)
    for entry in sorted((sys_root / "class/tty").glob("*")):
        if not entry.name.startswith(("ttyACM", "ttyUSB")):
            continue
        path = (entry / "device").resolve()
        port = dev_root / entry.name
        row = {"port": str(port), "vid": None, "pid": None,
               "serial": None, "product": None, "manufacturer": None,
               "by_id": aliases.get(str(port.resolve()), [])}
        for parent in [path, *path.parents]:
            if (parent / "idVendor").exists():
                for key, filename in (("vid", "idVendor"), ("pid", "idProduct"),
                                      ("serial", "serial"), ("product", "product"),
                                      ("manufacturer", "manufacturer")):
                    try:
                        row[key] = (parent / filename).read_text().strip()
                    except OSError:
                        pass
                break
        rows.append(row)
    return rows


def ublox_devices(rows=None):
    return [d for d in (devices() if rows is None else rows) if d["vid"] == UBLOX_VID]


def choose_port(explicit=None, rows=None):
    """Return the explicit port, or the single u-blox tty; refuse ambiguity."""
    if explicit:
        return explicit
    candidates = ublox_devices(rows)
    if len(candidates) == 1:
        return candidates[0]["port"]
    if not candidates:
        raise OSError("No u-blox USB tty found. Check the USB data cable and host diagnostics, "
                      "or select the port explicitly (normally /dev/ttyACM0).")
    raise OSError("Multiple u-blox devices found; select the port explicitly: "
                  + ", ".join(d["port"] for d in candidates))


def select_port(explicit=None, rows=None):
    """choose_port() for reporting: returns (port, error) instead of raising."""
    try:
        return choose_port(explicit, rows), None
    except OSError as exc:
        return None, str(exc)


def accessible(port):
    return bool(port and os.access(port, os.R_OK | os.W_OK))


def cdc_acm_loaded(sys_root=Path("/sys")):
    return (sys_root / "module/cdc_acm").exists()


def commands(port=None):
    """External read-only commands for the diagnostics document, by key."""
    specs = {"lsusb": ["lsusb"],
             "gpsd": ["systemctl", "is-active", "gpsd.service", "gpsd.socket"],
             "modemmanager": ["systemctl", "is-active", "ModemManager.service"]}
    if port:
        specs["udev"] = ["udevadm", "info", "--query=property", "--name=" + port]
        specs["owners"] = ["fuser", "-v", port]
    return specs


def command_result(command, available=True, exit_code=None, stdout="", stderr="", error=None):
    if not available:
        return {"available": False, "command": command, "output": "Optional command not installed"}
    if error is not None:
        return {"available": True, "command": command, "output": str(error)}
    return {"available": True, "command": command, "exit_code": exit_code,
            "output": (stdout + stderr).strip()[:OUTPUT_LIMIT]}


def run_command(command):
    """Blocking runner; GUI code uses Gio.Subprocess with the same result shape."""
    if not shutil.which(command[0]):
        return command_result(command, available=False)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return command_result(command, error=exc)
    return command_result(command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


def diagnostics(port, error, rows, results, sys_root=Path("/sys")):
    """Assemble the diagnostics document (the `doctor --json` schema)."""
    data = {"python": platform.python_version(), "kernel": platform.release(), "platform": platform.system(),
            "devices": rows, "selected_port": port, "read_write_access": accessible(port),
            "selection_error": error, "cdc_acm_loaded": cdc_acm_loaded(sys_root)}
    for key in ("lsusb", "gpsd", "modemmanager"):
        data[key] = results[key]
    data["hints"] = list(HINTS)
    for key in ("udev", "owners"):
        if key in results:
            data[key] = results[key]
    return data


def collect(explicit=None):
    """Blocking convenience: discover, run every command and build the document."""
    rows = devices()
    port, error = select_port(explicit, rows)
    results = {key: run_command(command) for key, command in commands(port).items()}
    return diagnostics(port, error, rows, results)
