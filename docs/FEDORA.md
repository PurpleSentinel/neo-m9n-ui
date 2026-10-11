# Fedora setup and dependencies

## Dependency map

| Component | Fedora package | Required? | Purpose |
| --- | --- | --- | --- |
| Python 3.10 or newer | `python3` | Yes | Application runtime and test suite |
| PyGObject ≥ 3.46 | `python3-gobject` | Yes | GTK/GLib bindings |
| GObject typelibs | `gobject-introspection` | Yes | Includes the `cairo` typelib the bindings need; `python3-gobject` does not pull it in by itself |
| GTK ≥ 4.12 | `gtk4` | Yes | Toolkit |
| libadwaita ≥ 1.5 | `libadwaita` | Yes | GNOME widgets and styling |
| Native USB serial driver | Fedora kernel `cdc_acm` module | Yes for native USB | Exposes the receiver as a tty; no proprietary driver download |
| USB inventory | `usbutils` | Optional | `lsusb` on the Host page |
| Host utilities | `util-linux` | Optional, generally installed | tty/file inspection utilities |
| Port-owner inspection | `psmisc` | Optional | `fuser` on the Host page |
| udev/systemd commands | Fedora system installation | Optional diagnostics and access rule | `udevadm`, `systemctl`, `journalctl` |
| Git | `git` | Optional | Clone and update the source |
| pip/build tooling | `python3-pip`, `python3-setuptools` | Optional | Conventional Python package installation |

Serial I/O uses the standard library (`termios`, `tty`, `select`, `fcntl`), so there are no pyserial or other pip dependencies and no compiler is needed. Fedora 40 or newer provides the required GTK, libadwaita and PyGObject versions; the app checks them at startup and prints the `dnf` command if anything is missing. These package names are not version pins; Fedora resolves updates for your installed release.

## Workstation/Server installation

```bash
sudo dnf install python3 python3-gobject gobject-introspection gtk4 libadwaita usbutils util-linux psmisc
python3 scripts/install-user.py
```

(`bash scripts/install-fedora.sh` runs both steps.) The user installer:
- copies the package to `~/.local/share/neo-m9n` and writes the launcher `~/.local/bin/m9n`
- installs the desktop entry, AppStream metadata and icons under `~/.local/share`, so **NEO-M9N Monitor** appears in Activities

Re-running it upgrades the copy and removes files that no longer exist upstream, as well as a 1.x `~/.local/share/neo-m9n-cli` install. It does not alter shell files or USB permissions. The installed code runs from the copy; to run your checkout directly, use `python3 -m m9n` there.

The desktop entry starts the launcher by its full path. To run `m9n` from a terminal, bash may need:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To uninstall:

```bash
python3 scripts/install-user.py --uninstall
```

This removes the package, launcher, desktop entry, metadata and icons. Your settings (`~/.config/neo-m9n`) and evidence files are left in place. If you installed the optional udev rule, remove `/etc/udev/rules.d/70-ublox-gnss.rules`, reload the rules and replug separately.

## Optional pip installation

The user installer is the simplest route and works offline once the packages above are present. For standard Python packaging, the virtual environment must see the system PyGObject:

```bash
sudo dnf install python3-pip
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install .
.venv/bin/m9n
```

The distribution is named `neo-m9n-monitor` (2.0.1 and earlier used `neo-m9n-cli`; uninstall that first with `.venv/bin/python -m pip uninstall neo-m9n-cli` when upgrading an existing environment, since both provide the same files). pip obtains the `setuptools>=77` build dependency if necessary; it is not a runtime dependency. A pip install provides the `m9n` command but no desktop entry; use the user installer for that. Avoid installing into Fedora's system Python with `sudo pip`.

## USB permissions: local desktop

The Host page shows the selected port, whether you have read/write access and the device's udev properties. From a terminal:

```bash
ls -l /dev/ttyACM0
udevadm info --query=property --name=/dev/ttyACM0
```

Install the supplied udev rule to give the active local seat user access, and to tell ModemManager to ignore this u-blox tty. The Host page offers the same commands to copy when access is denied:

```bash
sudo install -m 0644 udev/70-ublox-gnss.rules /etc/udev/rules.d/70-ublox-gnss.rules
sudo udevadm control --reload-rules
```

Unplug and reconnect, then press Refresh on the Host page. The rule deliberately runs before the standard seat/uaccess processing and applies to all u-blox VID `1546` tty devices. If you have other u-blox hardware, narrow it with a verified `ATTRS{idProduct}` or USB serial number. It is not world-writable.

## USB permissions: SSH/headless

`TAG+="uaccess"` is for an active local desktop seat and usually does not grant an SSH user access. Check the tty's owning group rather than assuming its name:

```bash
stat -c '%A %U %G %n' /dev/ttyACM0
id
```

If the device is group-writable and owned by `dialout`, for example:

```bash
sudo usermod -aG dialout "$USER"
```

Use the group actually shown on your machine, then log out and back in. Group membership affects every device owned by that group. For a dedicated service account, use a narrowly scoped per-device udev group rule instead. Do not use a permanent `chmod 666`, and do not run the application as root to get around this.

## Device detection and competing services

```bash
lsusb
journalctl -k -b --no-pager
sudo modprobe cdc_acm
```

The Host page reads the USB inventory, kernel-module visibility, tty access, udev properties, port owners and the gpsd/ModemManager service state. It never loads modules, stops services or changes permissions itself. An active service is a clue, not proof that it owns the GNSS port. ModemManager is harmless when udev marks the receiver `ID_MM_DEVICE_IGNORE=1`, which the supplied rule and ModemManager's own rules both do. `fuser` may need administrator access to show other users' processes.

If gpsd really owns this receiver and it is safe to stop it for the test:

```bash
sudo systemctl stop gpsd.service gpsd.socket
```

Stopping the socket matters because it can reactivate the service. Afterwards, restore the services to the state you want. Prefer the per-device ModemManager ignore rule to disabling modem services system-wide.

The app takes an advisory lock and `TIOCEXCL` on the tty. This blocks later opens by unprivileged processes; it cannot evict a process that already had the tty open. If data is intermittent, check ownership on the Host page before running a test.

## Fedora Atomic desktops

On Silverblue, Kinoite and other Atomic variants, layer the runtime with `rpm-ostree install python3-gobject gobject-introspection gtk4 libadwaita` (or use a toolbox that has them), then run `python3 -m m9n` from the checkout or use the user installer. The `dnf` helper targets Workstation/Server, not immutable hosts. A toolbox container must have the receiver tty and permissions available inside it; using the host directly avoids an extra device-access boundary. udev rules and kernel modules belong on the host. A Flatpak is tracked in issue #31.
