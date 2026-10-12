# Troubleshooting

Start with the **Host** page, then a 120-second outdoor **device test**. If the first evidence is inconclusive, turn on *Debug logging*, the *Application log* and the *Raw RX capture* in the setup sidebar's Evidence Files.

| Symptom/evidence | Likely area to check | Next action |
| --- | --- | --- |
| Power LED on, absent from `lsusb` | Cable carries power only, wrong USB connector, host port/power | Change to known data cable; direct host connection; check board variant power requirements |
| USB in `lsusb`, no ttyACM | Kernel driver, permissions or USB enumeration errors | Inspect `journalctl -k -b`, try `sudo modprobe cdc_acm`; see Fedora docs |
| Multiple USB receivers | Ambiguous automatic selection | Pick the receiver explicitly in the setup sidebar (listed by its by-id name) |
| Permission denied | Local seat ACL or group membership | Use the Host page's Grant Access commands (the supplied udev rule); replug; headless users inspect the actual tty group |
| Device/resource busy | Another program, gpsd, modem probes | Host page: *Processes holding the port*; stop the relevant gpsd service/socket or use the per-device modem ignore |
| No bytes | Wrong tty, disabled USB output, power or connection | Check USB/kernel evidence on the Host page, the configuration in vendor tools and a fresh cable |
| Bytes but no valid frames | Wrong protocol, interrupted stream, UART bridge rate | Save raw bytes; inspect protocol/baud settings, ownership, power; native USB needs no baud search |
| NMEA works but MON-VER/NAV/RF polls unanswered | UBX input or output disabled, unsupported message/firmware | Run a *Passive* session to verify NMEA independently; check firmware/USB protocol settings with vendor tooling |
| ACK-NAK | Receiver rejected a specific poll | Review named target and firmware; target is no longer polled in this session |
| No valid fix, valid frames | Sky visibility, antenna, acquisition or GNSS config | Move outdoors; confirm external antenna/adapter; run longer; don't assume USB or hardware failure |
| Fresh bytes but navigation stale | Constant/frozen iTOW or UTC, stopped nav engine | Inspect raw NAV-PVT/NMEA epochs and power-save/config state; compare vendor tool results |
| Repeated checksum failures | Corrupted stream, competing reader, cable/power, bridge setting | Inspect ownership and debug/raw capture, use direct cable, verify bridge baud if applicable |
| Short or unsupported payload | Different firmware layout, malformed telemetry | Save MON-VER and raw capture; compare relevant M9 specification; extend parser deliberately |
| Antenna OPEN/SHORT | Supervisor wiring/config or actual connection fault | Match exact SparkFun board schematic and antenna type; not all passive antennas support supervision |
| RF state 2/3 | Receiver reports interference warning/critical | Move antenna from noise sources; compare C/N0 and location; state is an indication |
| Spoof detector 2/3 | Receiver indication at that epoch | Verify position independently; detector state 0/1 does not prove absence of spoofing |
| System clock amber or red (Host page) | No time synchronisation: an offline vehicle PC, NTP off, a flat RTC battery | See [Fixing a wrong clock](#fixing-a-wrong-clock); evidence timestamps from this session use the wrong time |
| Disconnect/I/O error | Unplug, flaky cable/hub, power interruption | The window shows a disconnect banner; check kernel logs and the re-enumerated tty; start a new session (no automatic reconnect) |

## Fixing a wrong clock

The app only reads the clock; it never changes it. To fix it:

1. **Settings → System → Date & Time:** turn on *Automatic Date & Time* (needs a network).
2. **In a terminal:** `timedatectl` should show `System clock synchronized: yes` and `NTP service: active`. If not, run `sudo timedatectl set-ntp true`, then `chronyc tracking` to see chronyd's offset.
3. **Offline machines:** the clock drifts, and a flat RTC battery resets it at every boot. chronyd can use the receiver as a time source through gpsd (a GNSS fix is accurate to well under a second; the PPS pin, where wired, to microseconds). That setup is outside this app, and gpsd would then hold the port, so stop it before a session.

A reading within ±0.2 s is *in step*: the comparison includes a little USB delay, so it can't resolve milliseconds.

## A useful evidence bundle

1. **Host** page → *Save as JSON* (host and USB evidence).
2. Setup sidebar → *Mode: Device test*, *Duration* 120, and in *Evidence Files* turn on Raw RX capture, JSONL snapshot log, Application log, Test report and Debug logging.
3. **Run Device Test**. The files appear in the evidence folder with one shared timestamp, e.g. `m9n-20261011-120000.ubx`, `.jsonl`, `.log` and `-report.json`.
4. Optionally copy the **Messages** page's application log, or save it.

Timestamped names mean a raw capture is never overwritten. The JSON report includes per-message response ages, frame counts, poll timeout counts and current issues. Match host UTC across application logs and kernel logs. The GNSS UTC field may be unavailable before time is resolved; never assume it is synchronized with host time during startup.

If the tty disappears before the session can open it, the session doesn't start and no GNSS report exists, but the Host page still provides host evidence. If it disappears after opening, the test records `usb_transport: FAIL`, shows the report and writes it if its output path is usable; otherwise *Save Report…* lets you keep it.

Do not disable SELinux to troubleshoot a serial access problem. Inspect the actual error, permissions and audit evidence. The application makes no network connections unless you switch the Map page to Online, never runs privileged commands and needs no elevated privileges once suitable tty access is granted.
