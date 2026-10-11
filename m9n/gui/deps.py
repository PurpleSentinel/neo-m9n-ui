"""Runtime check for PyGObject, GTK 4 and libadwaita before any Gtk import."""
MIN_PYGOBJECT = (3, 46)
MIN_GTK = (4, 12)
MIN_ADW = (1, 5)
INSTALL_HINT = ("Install the desktop runtime with: "
                "sudo dnf install gtk4 libadwaita python3-gobject gobject-introspection")


def version(parts):
    return ".".join(map(str, parts))


def check():
    """Return None when the GUI can start, otherwise an actionable message."""
    try:
        import gi
    except ImportError:
        return "PyGObject (python3-gobject) is not installed. " + INSTALL_HINT
    if tuple(gi.version_info[:2]) < MIN_PYGOBJECT:
        return f"PyGObject {version(gi.version_info)} is older than {version(MIN_PYGOBJECT)}. " + INSTALL_HINT
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
    except (ImportError, ValueError) as exc:
        return f"GTK 4 or libadwaita is not available ({exc}). " + INSTALL_HINT
    gtk = (Gtk.get_major_version(), Gtk.get_minor_version())
    if gtk < MIN_GTK:
        return f"GTK {version(gtk)} is older than {version(MIN_GTK)}. " + INSTALL_HINT
    adw = (Adw.get_major_version(), Adw.get_minor_version())
    if adw < MIN_ADW:
        return f"libadwaita {version(adw)} is older than {version(MIN_ADW)}. " + INSTALL_HINT
    return None
