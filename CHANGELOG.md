# Changelog

## Unreleased

### Added
- **Map** page, next to Vehicle (#45):
  - your live position, accuracy circle and the trip's track (coloured by speed), with follow and re-centre
  - **Off** (default): a coordinate grid, no network.
  - **Online**: an OpenStreetMap map, only after an informed privacy notice, with a visible *Online* indicator.
  - **Tile requests:** only for the visible area, only while the page is showing, never prefetched.
  - **Cache:** viewed tiles are cached in `~/.cache/neo-m9n/tiles` (size limit, **Clear Map Cache**), following OpenStreetMap's tile usage policy: its caching rules and an app User-Agent. The cache is never used offline.
  - **Without libshumate:** the track is drawn on a grid.
- **Reset Trip** also clears the map track. The screen is kept awake on the Map page too.

### Changed
- **Privacy:** the app now makes network connections, but only in the Map page's Online mode, which you switch on yourself.
- `libshumate` joins the Fedora package list (optional).
- The Python package (distribution) is renamed from `neo-m9n-cli` to **`neo-m9n-monitor`**, so wheels are named `neo_m9n_monitor-…whl`. The `m9n` command, the `m9n` import package, the app ID and the install locations are unchanged. If you installed an earlier wheel with pip, run `pip uninstall neo-m9n-cli` first; both provide the same files. `scripts/install-user.py` users need do nothing.

## 2.0.1 (2026-10-11)

### Added
- **Vehicle** page, between Receiver and Messages, for reading on the move (#40):
  - a speed gauge, a course-over-ground compass and an altitude profile
  - distance, average speed, maximum speed and moving-time tiles
  - large, colourful, gradient instruments that scale from phone width to fullscreen
  - km/h or mph
  - **Reset Trip**
  - **Fullscreen** (F11)
  - the screen is kept awake while a session runs on the page
- Trip statistics are calculated by the session core from every navigation epoch, using GNSS time, so they are as accurate in a fast replay as live. Snapshots, and so the JSONL log, gain a `trip` object.
- *Moving vehicle* demo scenario: a 400 m loop with varying speed, heading and altitude.

### Changed
- The header page switcher uses its compact style below about 1180sp, so seven pages fit.
- PangoCairo is now checked at startup (part of Pango; no new package).

## 2.0.0 (2026-10-11)

The command-line interface is replaced by a GTK 4 / libadwaita desktop application, **NEO-M9N Monitor** (`m9n`, or `python3 -m m9n` from a checkout). See epic #1.

### Breaking changes
- The `list`, `doctor`, `watch`, `test`, `demo` and `replay` commands and all their options are removed. Every capability is available in the window: session setup sidebar, Dashboard, Satellites, Receiver, Messages, Test and Host pages.
- **Scripted use through exit codes and stdout JSON is no longer possible.** Machine-readable evidence is still written to files:
  - the test report (`schema_version: 1`, unchanged, still including `exit_code`)
  - the JSONL snapshot log (snapshots plus the final report)
  - the raw RX capture and the application log
  
  The Host page saves the `doctor --json` document. A headless front end could be rebuilt on `m9n.controller.SessionController` if needed.
- The runtime now needs PyGObject ≥ 3.46, GTK ≥ 4.12 and libadwaita ≥ 1.5 (Fedora 40+: `sudo dnf install gtk4 libadwaita python3-gobject gobject-introspection`). There are still no pip dependencies.
- `m9n --version` remains for packaging checks.

### Added
- Pages: Dashboard, Satellites (sortable table and sky plot), Receiver (RF/antenna/status), Messages (receiver notices and application log), Test (progress and report) and Host (replaces `doctor`/`list`).
- Session setup sidebar with saved settings, timestamped evidence files and per-row validation.
- Pages use the available width: groups flow into one to three balanced columns based on the page's own width, and the Satellites sky plot moves and grows to fit (#35).
- Guaranteed clean exit: every way a session ends releases the receiver and completes the evidence files; signals exit with 128 + signal; suspend is inhibited during sessions.
- Desktop entry, AppStream metadata and icons; `scripts/install-user.py --uninstall`.
- CI on Fedora (including GUI tests on a headless Broadway display) and Python 3.10–3.13.
- Documentation rewritten for the application, with a [user guide](docs/USER-GUIDE.md) that maps every 1.x command and option to its control.

### Fixed
- A failed device open no longer leaves an empty raw capture that blocks the next run.
- A completed test result is no longer lost when its report file cannot be written.
- Closing an unplugged tty no longer raises an unhandled `termios.error`.
