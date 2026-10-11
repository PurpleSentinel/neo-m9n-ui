# Hardware and connection

| Item | Required | Notes |
| --- | --- | --- |
| SparkFun u-blox NEO-M9N board | Yes | Direct native USB connection; chip antenna, u.FL or SMA breakout variants |
| USB data cable for the GNSS board | Yes | USB-C on common SparkFun NEO-M9N boards; charge-only cables power the board but do not enumerate |
| Appropriate GNSS antenna | Depends on board | Chip-antenna board has its antenna; external connector variants need a suitable antenna and adapter if necessary |
| Fedora host | Yes | Free USB port and Python 3.10+ |
| MicroMod Main Board/power | MicroMod variant only | Its GNSS USB connector alone does not power the full Function Board system |

Use the GNSS board's USB connector rather than an attached microcontroller's USB port. A Qwiic connection to a RedBoard does not automatically expose the GNSS module directly as a host tty. USB-to-UART bridges can be selected explicitly but require the correct module UART wiring and UART rate.

Start with a direct host connection without a hub. Attach the board's intended antenna, keep it away from obvious noise sources and give it a clear view of the sky. Allow a longer test window for initial acquisition. A board power LED confirms power only, not USB data, protocol operation or a satellite fix.

The tool does not test electrical USB signalling, measure supply voltage, validate the RF circuit with instruments or provide a manufacturer production self-test. It exercises the host communication path and interprets receiver telemetry. Antenna supervision may be unavailable or may not detect passive antennas in the way you expect; compare a reported OPEN/SHORT to the specific board schematic and antenna type before concluding the antenna is faulty.

Vendor hardware guides are in [REFERENCES.md](REFERENCES.md). Check your exact board variant against those guides.
