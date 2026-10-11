# Contributing

## Running from a checkout

```bash
sudo dnf install python3 python3-gobject gobject-introspection gtk4 libadwaita
python3 -m m9n
```

No build step: the widgets are built in Python, and the icons, desktop entry and metainfo in `m9n/gui/data/` are found from the checkout. The core (`m9n/*.py`) must stay free of GTK imports; the `m9n/gui/` package only presents its state.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

GUI tests start their own private Broadway display (`gtk4-broadwayd`, part of `gtk4`) and D-Bus session (`dbus-run-session`), so they never open windows on your desktop or touch your saved settings. Without the GTK runtime they skip themselves; set `M9N_REQUIRE_GUI=1` to make that an error, as CI does. Each GUI scenario lives in `tests/gui_probe.py` and runs in a subprocess, and `tests/test_gui.py` asserts on the JSON it reports. The protocol, state, controller (with real PTYs), settings and host tests need no display.

Parser and state tests should exercise independent byte vectors and failure conditions, not just repeat the decoder's formulas. For new firmware or message support, add the primary vendor reference, validate payload length and version, and add malformed and truncated cases. Do not silently assume unsupported diagnostic values mean healthy hardware. Keep default operations to read-only diagnostic polls; any configuration command would need its own explicit control and documented scope.

## Screenshots

`tests/screenshot.py` renders the window to a PNG on a throwaway display. Use **synthetic data only** (the demo source or a generated capture), never a capture from your own receiver:

```bash
gtk4-broadwayd :7 &
GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 python3 tests/screenshot.py docs/screenshots/x.png 1024 768 dashboard win.toggle-session
```

## Bug reports

Include the observed board and firmware, what you did, expected and actual behaviour, and a redacted test report or Host page JSON. Raw captures and debug logs contain your precise location and receiver identifiers; review them before sharing.
