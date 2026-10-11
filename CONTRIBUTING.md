# Contributing

Run `python3 -m unittest discover -s tests -v` before submitting changes. Parser/state tests should exercise independent byte vectors and failure conditions, not just repeat the decoder's formulas. PTY integration tests exercise the real termios transport, CLI subprocess, file outputs and exit codes without GNSS hardware.

For new firmware/message support, add the primary vendor reference, validate payload length/version and add malformed/truncated cases. Do not silently assume unsupported diagnostic values mean healthy hardware. Keep default operations to diagnostic polls; any future configuration commands need a separate explicit command and documented scope.

Include observed board/firmware, command, expected/actual behavior and a redacted test report for bug reports. Raw/debug captures may contain precise location and identifiers; review them before sharing.
