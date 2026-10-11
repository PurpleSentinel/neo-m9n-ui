#!/usr/bin/env python3
"""Expose synthetic GNSS data through a Linux PTY for manual CLI testing."""
import argparse
import os
import pty
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m9n.demo import sample

p = argparse.ArgumentParser(description="Simulated receiver; does not validate physical hardware")
p.add_argument("--fault", choices=("no-fix", "rf", "corrupt", "frozen", "silent", "unplug"))
args = p.parse_args()
master, slave = pty.openpty()
print(f"SYNTHETIC PTY: {os.ttyname(slave)}\nIn NEO-M9N Monitor choose Source: USB receiver, Receiver: Other path…, "
      f"Port path: {os.ttyname(slave)}; then Start (F5) or Run Device Test (Ctrl+T). Ctrl+C here stops it.", flush=True)
started = time.monotonic()
next_send = started
try:
    while True:
        now = time.monotonic()
        if args.fault == "unplug" and now - started > 10:
            break
        ready, _, _ = select.select([master], [], [], .1)
        if ready:
            os.read(master, 4096)  # Consume host polls; output fixtures periodically.
        if now >= next_send:
            epoch = 1000 if args.fault == "frozen" else 1000 + int((now - started) * 1000)
            if args.fault != "silent":
                os.write(master, sample(epoch, args.fault))
            next_send = now + 1
except KeyboardInterrupt:
    pass
finally:
    os.close(master)
    os.close(slave)
