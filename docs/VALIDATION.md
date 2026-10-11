# Validation and hardware acceptance

## Automated checks

`python3 -m unittest discover -s tests` runs 135 tests on a machine with the GTK runtime. GitHub Actions (`.github/workflows/ci.yml`) runs them on every pull request:
- **Fedora container:** every test, including the GUI scenarios. `M9N_REQUIRE_GUI=1` is set, so the GUI tests cannot silently skip. Also validates the desktop entry and AppStream metainfo.
- **Python 3.10, 3.12 and 3.13:** the suite without GTK, where the GUI tests skip themselves.

They cover:

- **Protocol:** independent UBX/NMEA checksum vectors, every split boundary of a mixed stream, one-byte delivery, corruption recovery, noise, partial sync, frame length limits, incomplete-frame timeout. Also navigation/UTC/fix/invalid-LLH flags, units, constellation and RF offsets, truncated and unsupported payloads.
- **State:** stale and frozen epochs, no fix and RF alarms, absent diagnostics, unexpected model, receiver errors, read-only poll construction, passive mode, poll timeout/recovery, rejected polls.
- **Controller on real Linux PTYs:** healthy, no-fix, RF, frozen, silent, unplugged and passive receivers. For every stop reason it checks that the tty can be locked again and no file descriptors leaked. Also failed opens creating no files, write failures that keep the result, non-blocking writes, and draining data before a hang-up.
- **Settings, host inventory, installer and packaging:** tolerant settings load, timestamped evidence names, fake sysfs discovery and by-id aliases, the doctor document schema, the udev rule text, install/upgrade/uninstall into a temporary home, desktop entry and metainfo validation.
- **GUI**, in subprocesses on a private Broadway display and D-Bus session:
  - every exit path (Stop, close, Ctrl+Q, cancelled close, SIGINT/SIGTERM/SIGHUP, unhandled exception, unplug, completion) releases the tty and leaves complete evidence files
  - page contents for each source and fault
  - setup validation and persistence
  - the device test page for PASS, WARN, FAIL and INTERRUPTED
  - the Host page with fake tools, including a 3 s command timeout while the main loop stays responsive
  - large, empty and navigation-free replays
  - single-instance activation, narrow layouts

The fixtures are synthetic and don't establish how your physical device behaves. A PASS means all implemented checks had the expected evidence during the observation window; it is not a full electrical or RF manufacturing test.

## Observed on hardware

On 11 October 2026, one SparkFun NEO-M9N (firmware `EXT CORE 4.04`, `FWVER=SPG 4.04`, `PROTVER=32.01`, `MOD=NEO-M9N`) on Fedora 44 with GTK 4.22 and libadwaita 1.9:

| Check | Result |
| --- | --- |
| Discovery | `/dev/ttyACM0`, VID:PID `1546:01a9`, with its `/dev/serial/by-id` alias |
| Live dashboard | Valid 3D fix, 17–21 satellites used, NAV-PVT source, 0 checksum errors, no unanswered polls |
| Satellites | 45 tracked in a replay of a 15 s capture |
| Device test (15 s) | **WARN**. Every check passes except `rf_health`: the receiver's jamming monitor reports state 0 (unknown or disabled), which is assessed as unknown rather than healthy. The saved report matched the in-app report. |
| Passive session | Valid fix from NMEA (GNRMC) with zero UBX frames: nothing was transmitted |
| SIGTERM during a test | Exit status 143, report INTERRUPTED, JSONL and raw capture complete, receiver lockable again immediately |
| Host page | Access granted, `cdc_acm` loaded, no port owners, gpsd inactive, ModemManager active but ignoring the receiver (`ID_MM_DEVICE_IGNORE=1`) |

Still to be checked by hand: physically unplugging during a session, suspend inhibition while a session runs (`gnome-session-inhibit --list`), and keyboard and screen-reader use on a real desktop.

## Physical-board acceptance on Fedora

| Step | Action | Expected evidence |
| --- | --- | --- |
| 1 | Connect native USB with a data cable | The Host page lists a u-blox device (VID 1546) with a tty |
| 2 | Host page | Selected port accessible; review port owners and services |
| 3 | Device test, duration 120, report enabled, outdoors | MON-VER identifies NEO-M9N, navigation epochs advance, fix acquired, diagnostics present |
| 4 | Watch for 30 s with the JSONL log enabled | Several current fix and epoch observations, clean stream counters |
| 5 | Move the antenna indoors or shield its sky view without damaging it | Communication continues; fix availability and signal quality may degrade |
| 6 | Unplug during a running device test | Disconnect banner, FAIL report with `usb_transport`, receiver released |
| 7 | Close the window while a session runs | Confirmation dialog; after confirming, files are complete and the receiver is released |
| 8 | Capture raw/JSONL/debug output, then replay the capture | Raw frames decode; the source is labelled REPLAY |

Stop only services that actually use this receiver. Record the firmware, hardware and PROTVER/MOD extensions with the acceptance result. If a firmware doesn't return an optional diagnostic, expect WARN and inspect the message-specific evidence rather than forcing a PASS.
