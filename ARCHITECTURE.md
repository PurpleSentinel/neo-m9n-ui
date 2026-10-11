# Architecture

The application is a single-process Python CLI that owns one Linux tty for a session. It immediately parses received UBX/NMEA frames and refreshes a terminal or JSON snapshot periodically. It has no daemon, API server, cloud dependency or background storage service.

| Module | Responsibility |
| --- | --- |
| `hostinfo.py` | Read-only host inventory: USB tty discovery with `/dev/serial/by-id` aliases, u-blox auto-selection, access/driver checks and the diagnostics document; command execution is left to the front end |
| `transport.py` | Termios raw-mode transport, read polling, queued non-blocking writes, locking and disconnect detection |
| `protocol.py` | Incremental mixed-protocol framing, UBX/NMEA checksums, bounds/recovery and selected message decoding |
| `session.py` | Read-only UBX poll schedule, poll deadlines, rejected-message handling and logging |
| `state.py` | Latest messages, frame/message counts, monotonic freshness/epoch progress, health checks and reports |
| `controller.py` | UI-independent session lifecycle: configuration validation, source (device/demo/replay), evidence files, snapshot/issue callbacks, ordered stop and guaranteed tty release |
| `serialize.py` | Control-character escaping and JSON serialization shared by front ends |
| `display.py` | Plain/ANSI terminal rendering |
| `cli.py` | Commands, host diagnostics, console logging, interruption handling and exit codes; drives `controller.py` |
| `demo.py` | Explicitly synthetic GNSS frames for demonstration and tests |

## Session lifecycle

`SessionController` owns one session. A front end calls `on_readable()` when the tty fd is readable and `tick()` about every 100 ms; `run_until_finished()` provides that loop for the CLI. The device is opened before any evidence file is created, and every output path is validated up front. `stop()` always runs the same order: close the tty (restore termios, clear `TIOCEXCL`, release the lock), build the report or final snapshot, then write the report file, append the final JSONL record and close every file. The result is held in memory before any disk write, so a failing disk cannot discard a completed test. Tty writes are queued and never block the caller; an overdue write is a transport failure.

## Communication and polling

The port is opened nonblocking with read/write access, an advisory lock and kernel tty exclusivity. Raw mode disables canonical buffering and echo. Original tty attributes are restored on close where the kernel still permits it. Closing a missing/unplugged port tolerates restoration errors.

| UBX poll | Interval | Evidence |
| --- | --- | --- |
| MON-VER | 30 s | Software/hardware version, protocol/model extensions |
| NAV-PVT | 1 s | Position, velocity, UTC, fix validity and accuracy |
| NAV-SAT | 2 s | Per-satellite information and C/N0 |
| NAV-STATUS | 2 s | Receiver uptime, TTFF and spoof detector state |
| MON-RF | 5 s | RF blocks, antenna supervisor and interference information |

Polls have empty payloads. No CFG, reset, firmware update, output-rate or configuration-save commands are sent. `--passive` bypasses the poll scheduler. The app never automatically re-enables UBX if someone disabled it on the device; it explains why diagnostics may be unavailable instead.

Each outstanding poll has a 1.5-second response deadline. A matching received message satisfies it, including periodic output. Unanswered polls increment counters and generate a warning on the transition to unavailable; recovery is logged. ACK-NAK marks a target unavailable and stops repeating that target's poll for the session. A normal poll response is an actual message, not an ACK requirement.

## Framing and recovery

USB read boundaries are unrelated to GNSS frame boundaries. The parser retains partial frames between reads, searches for UBX sync or NMEA start delimiters and validates checksums before decoding. UBX payloads are capped at 8192 bytes, NMEA sentences at 1024 bytes, and incomplete frames time out after 2 seconds. The implemented diagnostics fit well inside these limits. A rejected UBX frame is rescanned to recover subsequent/embedded sync; invalid NMEA lines are discarded. These parser faults remain visible in counters and logs.

Unknown well-formed UBX messages retain class/id/length metadata. Short payloads or unsupported versions of implemented repeated-block messages are flagged, not silently decoded using the wrong layout.

## State and validity

Monotonic host time measures age; host UTC is recorded separately. A fresh NAV-PVT is preferred for navigation. Otherwise GGA/RMC is used. Complementary NMEA values merge only for the same UTC epoch and fix validity; old altitude/satellite values are not silently carried forward. GSA may supplement current DOP fields when recent.

Valid position output requires a valid position fix, supported fix type and a clear invalid-LLH flag for NAV-PVT; NMEA uses its fix/status flags. Invalid fixes have null coordinates. A valid old fix retains its last received coordinates but is explicitly marked stale. Navigation requires both recent reception and a progressing iTOW/UTC epoch when that timestamp exists. Constant polled epochs therefore cannot appear indefinitely healthy simply because USB is still sending bytes.

Navigation/satellite/status freshness defaults to 5 seconds; RF freshness uses 15 seconds because its poll interval is 5 seconds. Identity assessment uses the observed MON-VER and reported model. The tool does not cryptographically authenticate the hardware. Sample age accompanies diagnostic data; missing and expired diagnostic results are not assertions of health.

## Output and failure handling

Interactive terminals get ANSI redraw; pipes get plain text unless `--json` is selected. Receiver text is sanitized before terminal rendering. JSON always escapes control characters. Snapshot files flush each interval; raw RX files flush each read. Debug logs include RX/TX hex and parsed values. INF/TXT messages are captured if the firmware is already emitting them; the app does not configure firmware to enable additional internal debug output.

`test --json` prints one final object. Receiver silence/corruption and GNSS fix availability are separate checks. Transport errors after the test opens the port are included in a final FAIL report. Local setup failures before the session starts return an actionable stderr error. Ctrl-C releases the tty and restores the terminal cursor.

No reconnect is attempted automatically. A device reconnect may appear as a different tty; use `list` or a persistent `/dev/serial/by-id/` path and start another session.
