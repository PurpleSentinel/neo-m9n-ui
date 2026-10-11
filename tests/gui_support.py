"""Run GUI probes on a private Broadway display and D-Bus session bus."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gui_runtime_missing():
    from m9n.gui.deps import check
    return check()


@contextlib.contextmanager
def headless_display():
    """Yield an environment for GTK clients that never touches the desktop session."""
    missing = gui_runtime_missing()
    for tool in ("gtk4-broadwayd", "dbus-run-session"):
        if not missing and not shutil.which(tool):
            missing = f"{tool} not installed"
    if missing:
        if os.environ.get("M9N_REQUIRE_GUI"):
            raise RuntimeError(f"GUI tests are required here but cannot run: {missing}")
        raise unittest.SkipTest(missing)
    display = 40 + os.getpid() % 50
    cache = tempfile.TemporaryDirectory()
    # Without XDG_RUNTIME_DIR, GLib puts the Broadway socket in the cache directory, so the
    # display server and the probes must share the same (private) cache home.
    server = subprocess.Popen(["gtk4-broadwayd", f":{display}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env={**os.environ, "XDG_CACHE_HOME": cache.name})
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    socket = Path(runtime) / f"broadway{display + 1}.socket" if runtime else None
    deadline = time.monotonic() + 5
    while socket and not socket.exists() and time.monotonic() < deadline and server.poll() is None:
        time.sleep(.05)
    if not socket:
        time.sleep(1)
    env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    config = tempfile.TemporaryDirectory()
    # Private config and cache homes keep tests away from the user's settings and map tiles.
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{display}", PYTHONPATH=str(ROOT),
               ADW_DISABLE_PORTAL="1", GTK_USE_PORTAL="0", XDG_CONFIG_HOME=config.name,
               XDG_CACHE_HOME=cache.name)
    try:
        yield env
    finally:
        server.terminate()
        server.wait(5)
        config.cleanup()
        cache.cleanup()


def run_probe(env, scenario, timeout=30, **extra_env):
    """Run tests/gui_probe.py SCENARIO on its own session bus; return (process, result dict)."""
    process = subprocess.run(["dbus-run-session", "--", sys.executable, str(ROOT / "tests/gui_probe.py"), scenario],
                             env={**env, **extra_env}, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    lines = [line for line in process.stdout.splitlines() if line.startswith("RESULT ")]
    return process, json.loads(lines[-1][7:]) if lines else None
