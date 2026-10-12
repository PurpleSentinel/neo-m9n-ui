# Architecture

NEO-M9N Monitor is a single-process, single-threaded GTK 4 / libadwaita application (PyGObject) that owns one Linux tty per session. It parses received UBX/NMEA frames as they arrive and publishes a snapshot to the pages at the refresh interval. It has no daemon, API server, cloud dependency or background storage service.

The code is split into a GTK-free core, which can be unit-tested without a display, and the `gui` package, which only presents the core's state and starts and stops sessions.

### Core (`m9n/`)

| Module | Responsibility |
| --- | --- |
| `hostinfo.py` | Read-only host inventory: USB tty discovery with `/dev/serial/by-id` aliases, u-blox auto-selection, access/driver checks and the diagnostics document; command execution is left to the front end |
| `transport.py` | Termios raw-mode transport, read polling, queued non-blocking writes, locking and disconnect detection |
| `protocol.py` | Incremental mixed-protocol framing, UBX/NMEA checksums, bounds/recovery and selected message decoding |
| `session.py` | Read-only UBX poll schedule, poll deadlines, rejected-message handling and logging |
| `state.py` | Latest messages, frame/message counts, monotonic freshness/epoch progress, health checks and reports |
| `controller.py` | UI-independent session lifecycle: configuration validation, source (device/demo/replay), evidence files, snapshot/issue callbacks, ordered stop and guaranteed tty release |
| `serialize.py` | Control-character escaping and JSON serialization shared by front ends |
| `settings.py` | Persisted session settings (`~/.config/neo-m9n/settings.json`), timestamped evidence names, per-setting validation problems |
| `tilecache.py` | Online map tile cache following the OpenStreetMap tile policy: caching headers, a 7-day minimum, conditional revalidation, LRU size limit, atomic writes; never an offline map |
| `mbtiles.py` | Read-only offline map files: MBTiles with OpenMapTiles vector tiles; validation with user-facing reasons, metadata, coverage, TMS row flip, gzip, a small tile cache |
| `sun.py` | Sunrise, sunset, civil twilight and sun elevation for a position (NOAA solar algorithm, no network); the fix's GNSS UTC from NAV-PVT or NMEA RMC |
| `clock.py` | Clock check: median offset of the computer's clock from GNSS time (NAV-PVT when sent at its epoch, else NMEA RMC/ZDA), never polled answers; read-only |
| `trip.py` | Trip distance (haversine between valid, fresh epochs, timed by GNSS time), moving time and speeds, with stationary-jitter and glitch rejection; `Track` keeps every valid epoch's point (UTC, position, altitude, speed, course) in segments split at gaps over 10 s, thinning the older half beyond 200,000 points; `Marks` keeps numbered positions the user marked (numbers never reused after a delete) |
| `gpx.py` | GPX 1.1 export of a track, with speed and course in Garmin's TrackPointExtension; writes nothing that identifies the computer |
| `demo.py` | Explicitly synthetic GNSS frames for demonstration and tests, including a moving-vehicle loop |

### Desktop front end (`m9n/gui/`)

| Module | Responsibility |
| --- | --- |
| `application.py`, `deps.py` | Entry point; checks PyGObject/GTK/libadwaita versions before any Gtk import |
| `app.py` | Single-instance `Adw.Application`: actions, accelerators, signal and exception handling, shutdown backstop, icons |
| `window.py` | Main window: header bar, adaptive page switcher, setup sidebar, banner, progress, toasts, session control, close confirmation, track export (save dialog, atomic write) |
| `runner.py` | Drives `SessionController` from the GLib main loop (fd watch + tick) and re-emits its events as GObject signals |
| `setup.py` | Session setup sidebar mirroring `SessionSettings` |
| `pages.py`, `layout.py`, `format.py` | Page registry, base class (empty state until data) and `ColumnPage`, which flows groups into one to three columns by the page's own width; GTK-free column balancing and value formatting |
| `appearance.py` | Follow System / Light / Dark / Automatic by Sun (`sun.DarkBySun`, hysteresis at civil dusk) through libadwaita's style manager; the night-mode toggle on the Vehicle and Map pages |
| `dashboard.py`, `satellites.py`, `receiver.py`, `vehicle.py`, `messages.py`, `logview.py`, `testpage.py`, `host.py` | The seven pages and the shared bounded log list; `vehicle.py` draws its gauges with cairo and PangoCairo so they scale with the page; `mappage.py` puts a cairo overlay (grid, track, accuracy, marker) on libshumate's map, and `tiles.py` decides every network use: a local blank source when Off, a local offline map file through the vector renderer and the bundled `mapstyle.py` style when Offline, and OpenStreetMap only when Online, visible and consented |
| `data/` | Desktop entry, AppStream metainfo and icons (also used from a checkout) |

## Session lifecycle

`SessionController` owns one session. A front end calls `on_readable()` when the tty fd is readable and `tick()` about every 100 ms, and receives snapshots, issue changes, receiver messages and the final result through callbacks. The device is opened before any evidence file is created, and every output path is validated up front. `stop()` always runs the same order: close the tty (restore termios, clear `TIOCEXCL`, release the lock), build the report or final snapshot, then write the report file, append the final JSONL record and close every file. The result is held in memory before any disk write, so a failing disk cannot discard a completed test. Tty writes are queued and never block the caller; an overdue write is a transport failure.

## Desktop application lifecycle

The GUI is single-threaded. `gui/runner.py` attaches the controller to the GLib main loop: a `GLib.unix_fd_add_full()` watch on the tty (IN/HUP/ERR) and a 100 ms tick (replay ticks from an idle source). The controller's `on_stopping` hook removes those sources before the descriptor is closed, so no callback can see a closed or reused fd.

Every exit goes through `SessionController.stop()`:

| Trigger | Behaviour |
| --- | --- |
| Stop button / F5 | Stop as `user_stopped` |
| Window close, Ctrl+Q | Confirmation dialog while a session runs (a running test is recorded as INTERRUPTED), then stop and close |
| SIGINT, SIGTERM, SIGHUP | Stop as `interrupted`, quit, exit status 128 + signal |
| Unhandled exception | `sys.excepthook` stops the session, releases the tty and shows the error |
| Unplug / kernel I/O error | Transport FAIL, fd released at once, disconnect banner; start a new session after re-plugging |
| Application shutdown | Backstop: any remaining session is aborted |

While a session runs the application inhibits suspend and logout. `Gtk.Application` uniqueness means a second launch only presents the existing window, so two processes never compete for the receiver.

## Communication and polling

The port is opened nonblocking with read/write access, an advisory lock and kernel tty exclusivity. Raw mode disables canonical buffering and echo. Original tty attributes are restored on close where the kernel still permits it. Closing a missing/unplugged port tolerates restoration errors.

| UBX poll | Interval | Evidence |
| --- | --- | --- |
| MON-VER | 30 s | Software/hardware version, protocol/model extensions |
| NAV-PVT | 1 s | Position, velocity, UTC, fix validity and accuracy |
| NAV-SAT | 2 s | Per-satellite information and C/N0 |
| NAV-STATUS | 2 s | Receiver uptime, TTFF and spoof detector state |
| MON-RF | 5 s | RF blocks, antenna supervisor and interference information |

Polls have empty payloads. No CFG, reset, firmware update, output-rate or configuration-save commands are sent. The **Passive** setting bypasses the poll scheduler. The app never automatically re-enables UBX if someone disabled it on the device; it explains why diagnostics may be unavailable instead.

Each outstanding poll has a 1.5-second response deadline. A matching received message satisfies it, including periodic output. Unanswered polls increment counters and generate a warning on the transition to unavailable; recovery is logged. ACK-NAK marks a target unavailable and stops repeating that target's poll for the session. A normal poll response is an actual message, not an ACK requirement.

## Framing and recovery

USB read boundaries are unrelated to GNSS frame boundaries. The parser retains partial frames between reads, searches for UBX sync or NMEA start delimiters and validates checksums before decoding. UBX payloads are capped at 8192 bytes, NMEA sentences at 1024 bytes, and incomplete frames time out after 2 seconds. The implemented diagnostics fit well inside these limits. A rejected UBX frame is rescanned to recover subsequent/embedded sync; invalid NMEA lines are discarded. These parser faults remain visible in counters and logs.

Unknown well-formed UBX messages retain class/id/length metadata. Short payloads or unsupported versions of implemented repeated-block messages are flagged, not silently decoded using the wrong layout.

## State and validity

Monotonic host time measures age; host UTC is recorded separately. A fresh NAV-PVT is preferred for navigation. Otherwise GGA/RMC is used. Complementary NMEA values merge only for the same UTC epoch and fix validity; old altitude/satellite values are not silently carried forward. GSA may supplement current DOP fields when recent.

Valid position output requires a valid position fix, supported fix type and a clear invalid-LLH flag for NAV-PVT; NMEA uses its fix/status flags. Invalid fixes have null coordinates. A valid old fix retains its last received coordinates but is explicitly marked stale. Navigation requires both recent reception and a progressing iTOW/UTC epoch when that timestamp exists. Constant polled epochs therefore cannot appear indefinitely healthy simply because USB is still sending bytes.

Navigation/satellite/status freshness defaults to 5 seconds; RF freshness uses 15 seconds because its poll interval is 5 seconds. Identity assessment uses the observed MON-VER and reported model. The tool does not cryptographically authenticate the hardware. Sample age accompanies diagnostic data; missing and expired diagnostic results are not assertions of health.

## Output and failure handling

Receiver text is shown as plain text, never as markup, with control characters escaped. JSON always escapes control characters. Snapshot files flush each interval; raw RX files flush each read. Debug logs include RX/TX hex and parsed values. INF/TXT messages are captured if the firmware is already emitting them; the app does not configure firmware to enable additional internal debug output.

A device test produces one final report, shown on the Test page and optionally written to the report file and the JSONL log. Receiver silence/corruption and GNSS fix availability are separate checks. Transport errors after the test opens the port are included in a final FAIL report. Setup failures before the session starts (invalid settings, a missing or busy device) are shown in the window and create no evidence files. The application log is mirrored to stderr at warning level for terminal launches.

No reconnect is attempted automatically. A device reconnect may appear as a different tty; the setup sidebar lists receivers by their persistent `/dev/serial/by-id/` names, so start another session from there.

## Tests

`tests/` covers the protocol, state, controller (real PTYs), settings, host inventory, installer and packaging metadata without a display. `tests/test_gui.py` runs GUI scenarios from `tests/gui_probe.py` in subprocesses on a private Broadway display and D-Bus session, so nothing touches a real desktop. They check widget state, device release (`/proc/self/fd`), evidence files and main-loop responsiveness. CI runs everything on Fedora (with `M9N_REQUIRE_GUI=1`, so GUI tests cannot silently skip) and the core suite on Python 3.10–3.13.
