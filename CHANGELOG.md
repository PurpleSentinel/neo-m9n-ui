# Changelog

## Unreleased

### Added
- **Mark position** (#56): a **Mark** button on the Vehicle page and over the Map, and the **M** key, record where you are as a numbered point (a parking spot, a hazard, a survey point).
  - **Recorded:** number, GNSS time, position, height and the accuracy estimate. A toast confirms *Mark 3 saved* with **Undo**.
  - **Map:** numbered magenta pins, readable on the grid and both map modes, light or dark. A **Marks** list shows each mark with Copy coordinates, Show on map, Delete (with Undo) and an optional note.
  - **Rules:** needs a valid, current fix (the button says so otherwise). Numbers are never reused after a delete. Reset Trip and a new session clear marks; after Stop they stay for export.
  - **Evidence and export:** snapshots gain `marks`, the JSONL log gains `mark` records, and Export Track writes them as GPX waypoints.

### Changed
- **Reset Trip** says it also clears the track and marks.
- **Vehicle page at phone width:** narrower margins and unit buttons make room for Mark.

## 3.1.3 (2026-10-13)

Clock check: how far this computer's clock is from GNSS time.

### Added
- **Clock check** (#59): how far this computer's clock is from GNSS time, e.g. *in step (±0.2 s)*, *1.3 s fast* or *42 s slow*.
  - **Where:** a System Clock group on the Host page and a row on the Receiver page. Green under 0.5 s, amber up to 5 s, red beyond with a hint to turn on automatic time.
  - **How:** the median over the last 30 epochs of fully resolved GNSS time, from NAV-PVT sent at its epoch or else NMEA RMC/ZDA; polled answers, which can be up to a second old, are not used. Stated to ±0.2 s. Not measured for replays. The app never changes the clock.
  - **Evidence:** snapshots and the JSONL log gain `host_clock`, and device test reports an informational `host_clock` check that doesn't change the result.
  - **Demo:** its GNSS time now follows the computer's clock, with new *clock fast* (42 s) and *clock slow* (1.5 s) faults.

## 3.1.2 (2026-10-12)

Export the trip's track as a GPX file.

### Added
- **Export Track** as GPX (#55): a button on the Vehicle and Map pages and **Ctrl+E**.
  - **Contents:** a GPX 1.1 file of every valid position since the trip started, with height, GNSS time, speed and course (Garmin TrackPointExtension). New segments start after gaps over 10 s.
  - **Saving:** written only where you choose. It works after Stop too.
  - **Checked:** against the GPX 1.1 and TrackPointExtension schemas.

### Changed
- **At phone width,** the Vehicle page hides its fullscreen button (F11 still works), as the Map page already did, and the Map page hides Export Track (use the Vehicle page or Ctrl+E).

## 3.1.1 (2026-10-12)

Night mode: switch the app to dark with one tap, or let it follow the sun.

### Added
- **Night mode** (#61): a moon button on the Vehicle and Map pages switches the whole app between light and dark with one tap.
  - **Main Menu → Appearance:** Follow System (default), Light, Dark, or **Automatic by Sun**, which is dark from civil dusk to dawn at the receiver's position, with hysteresis so it doesn't flicker.
  - **Saved:** the choice persists.
  - **Offline maps** switch to their dark style.

### Fixed
- **Map toolbar at phone width:** it keeps everything on screen with the online indicator showing. The phone-width test now measures that case.
- **Unplug test:** it allows for app start-up time, so a slow start no longer fails it. The lifecycle probe reports any start-up error message.

## 3.1.0 (2026-10-12)

The Dashboard shows sunrise, sunset and the sun's elevation for where the receiver is.

### Added
- **Sun** on the Dashboard (#60): sunrise, sunset, day length, civil twilight and the sun's elevation now, for the receiver's position.
  - **Time zone:** times are shown in the computer's time zone.
  - **Date:** they use GNSS time, or the computer's clock if the receiver has no date.
  - **Polar regions:** polar day and night are explained.
  - **No network:** the times are calculated locally (NOAA solar algorithm). They are checked against an independent ephemeris to within a minute.

## 3.0.1 (2026-10-12)

A small update to the offline map script.

### Changed
- `scripts/make-offline-map.sh` keeps its summary on screen (#52). Planetiler's full output now goes to `planetiler.log` in the working folder, and the script shows short progress steps instead. A failed build shows the end of the log. `--verbose` shows everything as before. Running it with no area now says so, with an example.

### Fixed
- The user guide said **Reset Trip** was on the Map page; it is on the Vehicle page (#51).

## 3.0.0 (2026-10-11)

A major release: maps. The new **Map** page shows your live position and track on a grid, on an opt-in online OpenStreetMap map, or on an **offline map file** made once at home, which works in a vehicle with no internet. Because the app can now go online (only when you switch the map to Online), and the Python package is renamed, this is 3.0.0.

### Added
- **Map** page, next to Vehicle (#45):
  - your live position, accuracy circle and the trip's track (coloured by speed), with follow and re-centre
  - **Off** (default): a coordinate grid, no network.
  - **Online**: an OpenStreetMap map, only after an informed privacy notice, with a visible *Online* indicator.
  - **Tile requests:** only for the visible area, only while the page is showing, never prefetched.
  - **Cache:** viewed tiles are cached in `~/.cache/neo-m9n/tiles` (size limit, **Clear Map Cache**), following OpenStreetMap's tile usage policy: its caching rules and an app User-Agent. The cache is never used offline.
  - **Without libshumate:** the track is drawn on a grid.
- **Offline maps** (#46): a real map in a vehicle with no internet and no tile server.
  - `scripts/make-offline-map.sh <area>` makes an OpenMapTiles vector MBTiles file at home from OpenStreetMap data, using a pinned Planetiler in Podman. It lists every host it contacts first and has `--dry-run`.
  - The Map page's **Offline** mode draws it with a bundled light and dark style, over-zoomed beyond the file's detail. It shows the data date and attribution, and falls back to the grid outside the file's area.
  - It makes zero network requests, proven by tests.
- **Reset Trip** also clears the map track. The screen is kept awake on the Map page too.

### Changed
- The header page switcher's compact style now starts below about 1320sp, so eight pages fit.
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
