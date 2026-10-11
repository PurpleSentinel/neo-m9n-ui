# Troubleshooting

Start with `m9n doctor --port /dev/ttyACM0`, then a 120-second outdoor test. Add `--debug --log-file` and `--raw-log` when the initial evidence is inconclusive.

| Symptom/evidence | Likely area to check | Next action |
| --- | --- | --- |
| Power LED on, absent from `lsusb` | Cable carries power only, wrong USB connector, host port/power | Change to known data cable; direct host connection; check board variant power requirements |
| USB in `lsusb`, no ttyACM | Kernel driver, permissions or USB enumeration errors | Inspect `journalctl -k -b`, try `sudo modprobe cdc_acm`; see Fedora docs |
| Multiple USB receivers | Ambiguous automatic selection | Use explicit `--port`, preferably by-id |
| Permission denied | Local seat ACL or group membership | Install supplied udev rule; replug; headless users inspect actual tty group |
| Device/resource busy | Another CLI, gpsd, modem probes | Check `fuser -v`; stop the relevant gpsd service/socket or use per-device modem ignore |
| No bytes | Wrong tty, disabled USB output, power or connection | Check USB/kernel evidence, configuration in vendor tools and a fresh cable; capture setup stderr |
| Bytes but no valid frames | Wrong protocol, interrupted stream, UART bridge rate | Save raw bytes; inspect protocol/baud settings, ownership, power; native USB needs no baud search |
| NMEA works but MON-VER/NAV/RF polls unanswered | UBX input or output disabled, unsupported message/firmware | Run passive to verify NMEA independently; check firmware/USB protocol settings with vendor tooling |
| ACK-NAK | Receiver rejected a specific poll | Review named target and firmware; target is no longer polled in this session |
| No valid fix, valid frames | Sky visibility, antenna, acquisition or GNSS config | Move outdoors; confirm external antenna/adapter; run longer; don't assume USB or hardware failure |
| Fresh bytes but navigation stale | Constant/frozen iTOW or UTC, stopped nav engine | Inspect raw NAV-PVT/NMEA epochs and power-save/config state; compare vendor tool results |
| Repeated checksum failures | Corrupted stream, competing reader, cable/power, bridge setting | Inspect ownership and debug/raw capture, use direct cable, verify bridge baud if applicable |
| Short or unsupported payload | Different firmware layout, malformed telemetry | Save MON-VER and raw capture; compare relevant M9 specification; extend parser deliberately |
| Antenna OPEN/SHORT | Supervisor wiring/config or actual connection fault | Match exact SparkFun board schematic and antenna type; not all passive antennas support supervision |
| RF state 2/3 | Receiver reports interference warning/critical | Move antenna from noise sources; compare C/N0 and location; state is an indication |
| Spoof detector 2/3 | Receiver indication at that epoch | Verify position independently; detector state 0/1 does not prove absence of spoofing |
| Disconnect/I/O error | Unplug, flaky cable/hub, power interruption | Check kernel logs and re-enumerated tty; re-run test; no automatic reconnect is attempted |

## A useful evidence bundle

```bash
mkdir -p captures reports
m9n doctor --json > reports/host.json
m9n test --port /dev/ttyACM0 --duration 120 --debug \
  --log-file captures/debug.log \
  --raw-log captures/receiver.ubx \
  --json-log captures/live.jsonl \
  --report reports/test.json
```

Choose a new raw filename on each run. The JSON report includes per-message response ages, frame counts, poll timeout counts and current issues. Match host UTC across application logs and kernel logs. The GNSS UTC field may be unavailable before time is resolved; never assume it is synchronized with host time during startup.

If the tty disappears before the program can open it, a GNSS report cannot be generated, but stderr and `doctor` still provide host evidence. If it disappears after opening, the timed test records `usb_transport: FAIL` and writes the final report if its output path is usable.

Do not disable SELinux to troubleshoot a serial access problem. Inspect the actual error, permissions and audit evidence. The application does not make network connections or require elevated privileges after suitable tty access is granted.
