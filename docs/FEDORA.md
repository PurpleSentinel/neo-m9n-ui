# Fedora setup and dependencies

## Dependency map

| Component | Fedora package | Required? | Purpose |
| --- | --- | --- | --- |
| Python 3.10 or newer | `python3` | Yes | Entire CLI runtime and test suite |
| Native USB serial driver | Fedora kernel `cdc_acm` module | Yes for native USB | Exposes the receiver as a tty; no proprietary driver download |
| USB inventory | `usbutils` | Optional | `lsusb` in `doctor` |
| Host utilities | `util-linux` | Optional, generally installed | tty/file inspection utilities |
| Port-owner inspection | `psmisc` | Optional | `fuser` in `doctor` |
| udev/systemd commands | Fedora system installation | Optional diagnostics/access rule | `udevadm`, `systemctl`, `journalctl` |
| Git | `git` | Optional | Publish/version source |
| ZIP extraction | `unzip` | Optional | Extract the delivered archive |
| pip/build tooling | `python3-pip`, `python3-setuptools` | Optional | Conventional Python package installation |

The runtime uses `termios`, `tty`, `select`, `fcntl`, `argparse`, `struct`, `json`, logging and other Python standard-library modules. No pyserial/rich/pynmea2 dependencies are needed. No development compiler is needed. These package names are not version pins; Fedora resolves updates for your installed release.

## Workstation/Server installation

```bash
sudo dnf install python3 usbutils util-linux psmisc
python3 scripts/install-user.py
~/.local/bin/m9n --version
```

The user installer copies the package to `~/.local/share/neo-m9n-cli/m9n` and writes `~/.local/bin/m9n`. Re-running it upgrades this copy. It does not alter shell files or USB permissions. Installed code runs from the copied package; editing the original checkout requires reinstalling or running `python3 -m m9n` there.

For bash, if required:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to `~/.bashrc` for future shells. To uninstall:

```bash
rm ~/.local/bin/m9n
rm -r ~/.local/share/neo-m9n-cli
```

Delete only those application-owned paths. If you installed the optional udev rule, remove `/etc/udev/rules.d/70-ublox-gnss.rules`, reload rules and replug separately.

## Optional pip installation

The direct user installer is the simplest route and works without network access once Python is present. For standard Python packaging:

```bash
sudo dnf install python3-pip
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/m9n --help
```

pip obtains the `setuptools>=61` build dependency if necessary; it is not a runtime dependency. Avoid installing into Fedora's system Python with `sudo pip`. An offline build can use a pre-provisioned environment with setuptools and `pip install --no-build-isolation .`, or skip packaging entirely and use the source/user installer.

## USB permissions: local desktop

First inspect the actual port:

```bash
m9n list
ls -l /dev/ttyACM0
udevadm info --query=property --name=/dev/ttyACM0
```

Install the supplied udev rule to give the active local seat user access and tell ModemManager to ignore this u-blox tty:

```bash
sudo install -m 0644 udev/70-ublox-gnss.rules /etc/udev/rules.d/70-ublox-gnss.rules
sudo udevadm control --reload-rules
```

Unplug and reconnect, then run `m9n doctor`. The rule intentionally runs before the standard seat/uaccess processing and applies to all u-blox VID `1546` tty devices. Narrow it with a verified `ATTRS{idProduct}` or USB serial number if you have other u-blox hardware. It is not world-writable.

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

Use the group actually shown on your machine and log out/in. Group membership affects every device owned by that group. For a dedicated service account, use a narrowly scoped per-device udev group rule instead. Do not use permanent `chmod 666` or run the whole CLI as root to bypass this.

## Device detection and competing services

```bash
lsusb
journalctl -k -b --no-pager
sudo modprobe cdc_acm
m9n doctor --port /dev/ttyACM0
```

`doctor` reads USB inventory, kernel-module visibility, tty accessibility, udev properties, port owners and gpsd/ModemManager service state. It never loads modules, stops services or changes permissions itself. An active service is a clue, not proof that it owns the GNSS port. `fuser` may need administrator access to show other users' processes.

If gpsd really owns this receiver and it is safe to stop it for the test:

```bash
sudo systemctl stop gpsd.service gpsd.socket
```

Stopping the socket matters because it can reactivate the service. Restore the services to their previous desired state afterwards. Prefer the supplied per-device ModemManager ignore rule to disabling modem services system-wide.

The app takes an advisory lock and `TIOCEXCL` on the tty. This blocks later opens; it cannot evict a process that already had the tty open. Check ownership before running a test if data is intermittent.

## Fedora Atomic desktops

On Silverblue/Kinoite or other Atomic variants, run this dependency-free CLI using the host's Python if available:

```bash
python3 --version
python3 -m m9n list
```

Use the appropriate host package mechanism if Python/optional utilities are absent. The `dnf` helper targets Workstation/Server, not immutable hosts. A toolbox container must have the receiver tty and permissions available inside it; testing host USB directly avoids an extra device-access boundary. udev rules and kernel modules belong on the host.
