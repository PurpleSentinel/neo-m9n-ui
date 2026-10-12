"""Appearance: follow the system, light, dark (night mode), or dark after civil dusk at the receiver.

Everything drawn by the app follows libadwaita's style manager, including the instruments
and the offline map style, so switching the colour scheme is all night mode needs.
"""
from datetime import datetime, timezone

from gi.repository import Adw, Gio, GLib, Gtk

from .. import sun

SCHEMES = {"system": Adw.ColorScheme.DEFAULT, "light": Adw.ColorScheme.FORCE_LIGHT,
           "dark": Adw.ColorScheme.FORCE_DARK}
MENU = (("Follow System", "system"), ("Light", "light"), ("Dark", "dark"), ("Automatic by Sun", "sun"))


class Appearance:
    """The win.appearance (system/light/dark/sun) and win.night-mode (on/off) actions."""

    def __init__(self, window, mode, on_changed):
        self.manager = Adw.StyleManager.get_default()
        self.on_changed = on_changed  # Called with the new mode when the user changes it.
        self.by_sun = sun.DarkBySun()
        self.sun_minute = None
        self.mode = None
        self.mode_action = Gio.SimpleAction.new_stateful("appearance", GLib.VariantType.new("s"),
                                                         GLib.Variant("s", mode))
        self.mode_action.connect("change-state", lambda _action, value: self.set_mode(value.get_string()))
        self.night_action = Gio.SimpleAction.new_stateful("night-mode", None, GLib.Variant("b", False))
        self.night_action.connect("change-state",
                                  lambda _action, value: self.set_mode("dark" if value.get_boolean() else "light"))
        window.add_action(self.mode_action)
        window.add_action(self.night_action)
        self.manager.connect("notify::dark", lambda *_: self.sync_night())
        self.set_mode(mode, save=False)

    @staticmethod
    def menu():
        section = Gio.Menu()
        for label, mode in MENU:
            section.append(label, f"win.appearance::{mode}")
        return section

    def set_mode(self, mode, save=True):
        if mode not in SCHEMES and mode != "sun":
            return
        self.mode = mode
        self.mode_action.set_state(GLib.Variant("s", mode))
        if mode == "sun":
            self.apply_sun()
        else:
            self.manager.set_color_scheme(SCHEMES[mode])
        self.sync_night()
        if save:
            self.on_changed(mode)

    def apply_sun(self):
        # Until the receiver's position is known, look like the system does.
        dark = self.by_sun.dark
        self.manager.set_color_scheme(SCHEMES["system" if dark is None else "dark" if dark else "light"])

    def sync_night(self):
        self.night_action.set_state(GLib.Variant("b", self.manager.get_dark()))

    def update(self, snapshot):
        """Automatic by sun: re-check once a minute of GNSS time, at the receiver's position."""
        if self.mode != "sun":
            return
        nav = (snapshot or {}).get("navigation") or {}
        lat, lon = nav.get("latitude"), nav.get("longitude")
        if lat is None or lon is None:
            return  # Keep the last decision while the fix is lost.
        when = sun.gnss_datetime(nav) or datetime.now(timezone.utc)
        minute = when.replace(second=0, microsecond=0)
        if minute == self.sun_minute:
            return
        self.sun_minute = minute
        self.by_sun.update(sun.elevation(when, lat, lon))
        self.apply_sun()
        self.sync_night()


def night_button():
    """A toolbar toggle for night mode, for the pages used on the move."""
    button = Gtk.ToggleButton(icon_name="weather-clear-night-symbolic", action_name="win.night-mode",
                              valign=Gtk.Align.CENTER, tooltip_text="Night mode: dark appearance")
    button.update_property([Gtk.AccessibleProperty.LABEL], ["Night mode"])
    return button
