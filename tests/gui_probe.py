"""GUI scenarios executed in a subprocess by test_gui.py. Prints `RESULT <json>`."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from m9n.gui.app import Application  # noqa: E402

SCENARIO = sys.argv[1]
WIDTH = int(os.environ.get("M9N_PROBE_WIDTH", "1000"))
result = {"activations": 0}
app = Application()


def later(ms, func):
    GLib.timeout_add(ms, lambda: bool(func()) and False)


def page_names(win):
    pages = win.stack.get_pages()
    return [pages.get_item(i).get_name() for i in range(pages.get_n_items())]


def dialog_name(win, action):
    app.activate_action(action, None)
    dialog = win.get_visible_dialog()
    name = type(dialog).__name__ if dialog else None
    if dialog:
        dialog.force_close()
    return name


def shell(win):
    result["pages"] = page_names(win)
    result["empty_states"] = all(isinstance(w, Adw.StatusPage) for w in win.pages.values())
    result["toggle_enabled"] = win.lookup_action("toggle-session").get_enabled()
    result["accels"] = {a: app.get_accels_for_action(a) for a in ("app.quit", "app.shortcuts", "win.toggle-session")}
    result["about"] = dialog_name(win, "about")
    result["shortcuts"] = dialog_name(win, "shortcuts")
    win.toast("probe")
    win.set_banner("SYNTHETIC DEMO")
    result["banner_shown"] = win.banner.get_revealed()
    win.set_banner(None)
    result["banner_hidden"] = not win.banner.get_revealed()
    win.show_page("host")
    result["visible_page"] = win.stack.get_visible_child_name()

    def layout():
        result["bottom_switcher"] = win.switcher_bar.get_reveal()
        result["header_switcher"] = win.switcher.get_visible()
        app.activate_action("quit", None)
    later(400, layout)


def unique(win):
    second = subprocess.Popen([sys.executable, "-m", "m9n"], cwd=Path(__file__).resolve().parents[1])

    def wait():
        if second.poll() is None:
            return True
        result["second_exit"] = second.returncode
        result["windows"] = len(app.get_windows())
        app.activate_action("quit", None)
        return False
    GLib.timeout_add(100, wait)


def on_activate(_app):
    result["activations"] += 1
    if result["activations"] == 1:
        win = app.props.active_window
        win.set_default_size(WIDTH, 700)
        later(300, lambda: globals()[SCENARIO](win))


app.connect_after("activate", on_activate)
GLib.timeout_add_seconds(20, lambda: (result.setdefault("timeout", True), app.quit()) and False)
result["exit"] = app.run([sys.argv[0]])
print("RESULT " + json.dumps(result), flush=True)
