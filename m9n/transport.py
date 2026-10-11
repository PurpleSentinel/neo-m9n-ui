"""Linux termios USB tty transport; no pip serial dependency."""
import errno
import fcntl
import os
import select
import termios
import time
import tty
from pathlib import Path


def devices(sys_root=Path("/sys"), dev_root=Path("/dev")):
    rows = []
    for entry in sorted((sys_root / "class/tty").glob("*")):
        if not entry.name.startswith(("ttyACM", "ttyUSB")):
            continue
        path = (entry / "device").resolve()
        row = {"port": str(dev_root / entry.name), "vid": None, "pid": None,
               "serial": None, "product": None, "manufacturer": None}
        for parent in [path, *path.parents]:
            if (parent / "idVendor").exists():
                for key, filename in (("vid", "idVendor"), ("pid", "idProduct"),
                                      ("serial", "serial"), ("product", "product"),
                                      ("manufacturer", "manufacturer")):
                    try:
                        row[key] = (parent / filename).read_text().strip()
                    except OSError:
                        pass
                break
        rows.append(row)
    return rows


def choose_port(explicit=None):
    if explicit:
        return explicit
    candidates = [d for d in devices() if d["vid"] == "1546"]
    if len(candidates) == 1:
        return candidates[0]["port"]
    if not candidates:
        raise OSError("No u-blox USB tty found. Run 'm9n doctor'; use --port /dev/ttyACM0 if needed.")
    raise OSError("Multiple u-blox devices found; select --port explicitly: " + ", ".join(d["port"] for d in candidates))


class SerialPort:
    def __init__(self, path, baud=9600):
        self.fd = None
        self.old_attrs = None
        self.exclusive = False
        self.path = path
        speed = getattr(termios, f"B{baud}", None)
        if speed is None:
            raise ValueError(f"Unsupported termios baud rate: {baud}")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Kernel exclusivity prevents subsequent tty opens, even by gpsd.
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.exclusive = True
            self.old_attrs = termios.tcgetattr(self.fd)
            tty.setraw(self.fd, termios.TCSANOW)
            attrs = termios.tcgetattr(self.fd)
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~getattr(termios, "CRTSCTS", 0)
            attrs[4] = attrs[5] = speed
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except (OSError, ValueError):
            self.close()
            raise

    def read(self, timeout=0.1):
        poll = select.poll()
        poll.register(self.fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        ready = poll.poll(max(0, int(timeout * 1000)))
        if not ready:
            return b""
        if ready[0][1] & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
            raise OSError("USB tty disconnected or reported a kernel I/O error")
        try:
            data = os.read(self.fd, 8192)
        except BlockingIOError:
            return b""
        if not data:
            raise OSError("USB tty reached EOF (device disconnected)")
        return data

    def write(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 1.0
        while view:
            if time.monotonic() > deadline:
                raise TimeoutError("USB tty write timed out")
            _, writable, _ = select.select([], [self.fd], [], 0.1)
            if not writable:
                continue
            try:
                count = os.write(self.fd, view)
            except BlockingIOError:
                continue
            if count <= 0:
                raise OSError(errno.EIO, "USB tty write made no progress")
            view = view[count:]

    def close(self):
        if self.fd is not None:
            if self.old_attrs is not None:
                try:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.old_attrs)
                except OSError:
                    pass
            if self.exclusive:
                try:
                    fcntl.ioctl(self.fd, termios.TIOCNXCL)
                except OSError:
                    pass
            os.close(self.fd)
            self.fd = None
