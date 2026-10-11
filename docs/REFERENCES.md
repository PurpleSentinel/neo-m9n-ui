# Primary references

Vendor and platform references used to design the implementation. Re-check the specification for your observed firmware/board revision.

- [u-blox M9 SPG 4.04 interface description, UBX-21022436 R02](https://content.u-blox.com/sites/default/files/u-blox-M9-SPG-4.04_InterfaceDescription_UBX-21022436.pdf): UBX framing/checksum/polls, selected GNSS/RF messages and NMEA interpretation.
- [u-blox document landing page](https://www.u-blox.com/docs/UBX-21022436).
- [SparkFun NEO-M9N hardware overview](https://learn.sparkfun.com/tutorials/sparkfun-gps-neo-m9n-hookup-guide/hardware-overview): breakout power, USB and antenna variants.
- [SparkFun MicroMod NEO-M9N hookup guide](https://learn.sparkfun.com/tutorials/micromod-gnss-function-board---neo-m9n-hookup-guide/all): separate Main Board power requirement and GNSS USB port.
- [Python termios](https://docs.python.org/3/library/termios.html) and [tty](https://docs.python.org/3/library/tty.html): standard-library POSIX tty control.
- [Linux tty ioctl documentation](https://man7.org/linux/man-pages/man2/ioctl_tty.2.html): tty exclusivity.
- [Fedora packages](https://packages.fedoraproject.org/): package lookup for host prerequisites.

Vendor manuals remain their owners' copyrighted works and are linked, not bundled. This project is independent and is not a SparkFun or u-blox official hardware qualification tool.
