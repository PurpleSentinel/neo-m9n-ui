# SparkFun NEO-M9N USB CLI for Linux

`m9n` tests a SparkFun u-blox NEO-M9N connected directly over USB and displays live GNSS data in your terminal. It provides explicit evidence for USB/serial failures, unavailable diagnostics, loss of fix, frozen navigation epochs, checksum failures and receiver-reported RF or firmware errors.

**Python 3.10+ and Linux only. No third-party runtime packages, cloud services, Arduino, gpsd or u-center are required.** The source targets Fedora Workstation/Server and runs directly from an extracted checkout. It uses the kernel USB tty device, normally `/dev/ttyACM0`.

The default mode sends only empty-payload UBX diagnostic polls. It does not change GNSS configuration, reset the receiver or write flash. `--passive` disables all transmissions. Diagnostic availability depends on firmware, USB protocol configuration and antenna supervisor wiring.

## Quick start on Fedora

Extract this repository and enter its directory:

```bash
unzip neo-m9n-cli.zip
cd neo-m9n-cli
sudo dnf install python3
python3 scripts/install-user.py
```

If `m9n` is not found, use `~/.local/bin/m9n` or add `~/.local/bin` to your shell's PATH. You can also run every command without installing by replacing `m9n` with `python3 -m m9n` from this directory.

Optional host troubleshooting tools:

```bash
sudo dnf install usbutils util-linux psmisc
```

The combined helper `bash scripts/install-fedora.sh` installs these Fedora packages and the CLI. See [Fedora installation and USB access](docs/FEDORA.md) for permissions, Atomic desktops and an optional pip install.

Connect the GNSS board's USB port using a **USB data cable**, attach the appropriate antenna and give it an open view of the sky. A USB-C plug does not guarantee the cable carries data. SparkFun's MicroMod Function Board variant also needs the Main Board powered separately; see [hardware notes](docs/HARDWARE.md).

```bash
m9n list
m9n doctor
m9n test --duration 60
m9n watch
```

If access is denied, install the supplied rule, then unplug/replug the receiver:

```bash
sudo install -m 0644 udev/70-ublox-gnss.rules /etc/udev/rules.d/70-ublox-gnss.rules
sudo udevadm control --reload-rules
```

That rule grants access to the active local desktop user for u-blox tty devices. For SSH/headless sessions, follow the owning-group instructions in [FEDORA.md](docs/FEDORA.md).

Choose explicitly when auto-selection cannot identify one u-blox device:

```bash
m9n test --port /dev/ttyACM0 --duration 120
m9n watch --port /dev/serial/by-id/YOUR_DEVICE
```

Auto-selection matches u-blox USB vendor ID `1546` and refuses ambiguous devices. A matching USB VID alone is not model authentication; `test` checks the receiver's `MON-VER` model when it is supplied.

## What you get

| Capability | Detail |
| --- | --- |
| Live terminal display | Position, UTC, fix validity, satellites used, altitude, speed, course, horizontal accuracy, firmware and stream counters |
| Satellite table | GNSS constellation, SV identifier, C/N0, elevation, azimuth and use in solution, when NAV-SAT is available |
| Receiver diagnostics | MON-VER identity, MON-RF antenna/interference information, NAV-STATUS TTFF/uptime/spoof detector and any received INF/TXT notices |
| Fault detection | Unplug/I/O errors, silence, missing polls, malformed frames, receiver error messages, stale or frozen epochs and invalid fixes |
| JSON and evidence | JSON stdout, JSONL snapshot logs, exact raw RX captures, verbose RX/TX hex logs and structured test reports |
| Offline development | Synthetic demo modes, raw capture replay, PTY receiver simulator and automated protocol/CLI tests |

Native USB does not require a UART baud-rate hunt. `--baud` sets the tty line coding, primarily useful if you deliberately use a USB-to-UART adapter with `--port`. It does not configure the receiver's UART or its navigation update rate.

## Live data and debug capture

```bash
# Terminal display, refreshed every second; Ctrl-C exits.
m9n watch --port /dev/ttyACM0

# JSON snapshots for scripts (errors/logs remain on stderr).
m9n watch --json --duration 30

# Create parent directories yourself; each output must have a separate path.
mkdir -p captures reports
m9n watch --duration 120 --debug \
  --raw-log captures/receiver.ubx \
  --json-log captures/live.jsonl \
  --log-file captures/debug.log

# Final stdout is exactly one JSON object with --json on test.
m9n test --duration 120 --json --report reports/test.json

# Listen without sending any polls.
m9n watch --passive --json

# Decode a saved raw byte stream, including mixed NMEA and UBX.
m9n replay captures/receiver.ubx --json
```

Raw files are created exclusively and never silently overwritten. JSONL and application logs append; report files replace their previous contents. Received bytes are flushed as they arrive, not buffered until the session ends. Captures contain location data and receiver identifiers. Inspect them before committing or sharing; default capture/report directories are gitignored.

The live view processes all incoming frames immediately. Display/JSON snapshots default to 1 Hz, while polled NAV-PVT is requested at 1 Hz. Existing faster receiver output is still consumed. This is a live diagnostic tool, not a hard real-time control loop. Lowering `--interval` increases display refresh, not the receiver's measurement rate.

## Device test results

`m9n test` observes the device for 60 seconds by default and assesses the latest navigation plus integrity/error evidence accumulated over the session. Extend this outdoors for a cold start or difficult reception.

| Exit | Result | Meaning |
| --- | --- | --- |
| `0` | PASS | Valid recent communication and advancing position fix, confirmed NEO-M9N model, diagnostic responses and no observed integrity/error warnings |
| `2` | WARN | Communication/navigation works but fix, identity or diagnostic evidence is incomplete, or RF/integrity warnings were observed |
| `1` | FAIL / command error | No usable navigation/communication, stale/frozen navigation, unexpected reported model, receiver error, disconnect or local I/O/setup failure |
| `130` | INTERRUPTED | The user stopped a running session with Ctrl-C |

Argparse also uses exit `2` for invalid command syntax; inspect stderr to distinguish it from a test WARN. A setup/open failure prints an actionable error and may occur before a report can be created. A disconnect during an opened test produces a FAIL report with a `usb_transport` check.

**No fix is not proof of broken hardware.** Indoor reception, antenna placement, initial acquisition and receiver configuration can all prevent a fix. RF detector values are indications, not certification of interference/spoofing or proof of an antenna fault. An unavailable message is reported as unknown rather than healthy.

See [troubleshooting](docs/TROUBLESHOOTING.md), [CLI reference](docs/CLI.md), [architecture](ARCHITECTURE.md), [protocol and data contract](docs/PROTOCOL.md), and [validation](docs/VALIDATION.md).

## Try without hardware

```bash
m9n demo --duration 10
m9n demo --fault rf --json --duration 5
m9n demo --fault frozen --duration 10
python3 scripts/simulate-device.py --fault no-fix
```

Demo output is labelled `SYNTHETIC DEMO`. The simulator prints a `/dev/pts/...` port that a second terminal can pass to `m9n test`. A test against that port tests the software path, not your real GNSS module.

```bash
python3 -m unittest discover -s tests -v
```

## Publish on your GitHub account

This archive contains a clean source tree, not a remotely created GitHub repository. From the extracted directory:

```bash
git init -b main
git add .
git commit -m "Add Fedora NEO-M9N USB diagnostics CLI"
git remote add origin https://github.com/YOUR_USERNAME/neo-m9n-cli.git
git push -u origin main
```

Create an empty GitHub repository first. GitHub Actions includes Linux protocol/PTY tests and a Fedora container job; those remote jobs run after you push. MIT licensed. Vendor specifications are linked in [REFERENCES.md](docs/REFERENCES.md).
