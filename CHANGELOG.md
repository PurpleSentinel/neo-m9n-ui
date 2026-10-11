# Changelog

## 2.0.0 (unreleased)

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

### Fixed
- A failed device open no longer leaves an empty raw capture that blocks the next run.
- A completed test result is no longer lost when its report file cannot be written.
- Closing an unplugged tty no longer raises an unhandled `termios.error`.
