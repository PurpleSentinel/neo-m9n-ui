"""Developer helper: render the main window to PNG, e.g. for docs screenshots.

Run it on a throwaway display so nothing appears on the desktop:

    gtk4-broadwayd :7 &
    GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \\
        python3 tests/screenshot.py out.png 1000 700 [page] [app.ACTION|win.ACTION ...]
"""
import faulthandler
import sys
from pathlib import Path

faulthandler.dump_traceback_later(15, exit=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

from m9n.gui import window  # noqa: E402
from m9n.gui.app import Application  # noqa: E402

out, width, height = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
page = sys.argv[4] if len(sys.argv) > 4 else None
actions = sys.argv[5:]

# Size the window before it is first shown so breakpoints apply from the start.
original_init = window.MainWindow.__init__


def sized_init(self, **kwargs):
    original_init(self, **kwargs)
    self.set_default_size(width, height)


window.MainWindow.__init__ = sized_init
app = Application()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)


def render(widget, path):
    w, h = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        return False  # Not drawn yet.
    texture = widget.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(path)
    return True


def shoot():
    win = app.props.active_window
    if not render(win, out):
        win.queue_draw()
        return True
    print("saved", out, win.get_width(), win.get_height())
    app.quit()
    return False


def start():
    win = app.props.active_window
    if page:
        win.show_page(page)
    for action in actions:
        scope, name = action.split(".", 1)
        if scope == "app":
            app.activate_action(name, None)
        else:
            win.activate_action(action, None)
    GLib.timeout_add(700, shoot)
    return False


app.connect_after("activate", lambda *_: GLib.idle_add(start))
app.run(sys.argv[:1])
