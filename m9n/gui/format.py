"""Text for displayed values. GTK-free; missing values are always "unknown", never blank."""
from datetime import timedelta

from .. import clock, coords
from ..demo import ORIGIN

UNKNOWN = "unknown"


def number(value, digits=2, unit=""):
    if value is None:
        return UNKNOWN
    text = f"{value:.{digits}f}"
    return f"{text} {unit}" if unit else text


def integer(value):
    return UNKNOWN if value is None else f"{value:,}"


def seconds(value):
    if value is None:
        return UNKNOWN
    return f"{value:.1f} s" if value < 120 else f"{value / 60:.1f} min"


# Coordinate format -> (menu label, row titles). The chooser's examples use the demo's fix.
COORDINATE_FORMATS = {"decimal": ("Decimal Degrees", ("Latitude", "Longitude")),
                      "dm": ("Degrees and Minutes", ("Latitude", "Longitude")),
                      "dms": ("Degrees, Minutes, Seconds", ("Latitude", "Longitude")),
                      "bng": ("British National Grid", ("Grid reference", "Short form")),
                      "utm": ("UTM", ("UTM (WGS 84)",))}
BNG_NOTE = "approximate (±5 m)"


def coordinate_example(name):
    """One example line for the format chooser."""
    text = coords.one_line(*ORIGIN, name)
    return f"{text} · {BNG_NOTE}" if name == "bng" else text


def position_rows(navigation, name="decimal"):
    """[(title, text)] for the Dashboard's position rows in the chosen format."""
    lat, lon = (navigation or {}).get("latitude"), (navigation or {}).get("longitude")
    if lat is None or lon is None:
        return [(title, UNKNOWN) for title in COORDINATE_FORMATS.get(name, COORDINATE_FORMATS["decimal"])[1]]
    return coords.rows(lat, lon, name)


def position_text(lat, lon, name="decimal"):
    """A position on one line: signed decimal degrees by default (pastes into any map), else as displayed."""
    if name == "decimal" or name not in coords.FORMATS:
        return f"{lat:.7f}, {lon:.7f}"
    return coords.one_line(lat, lon, name)


def coordinates_text(navigation, name="decimal"):
    """The position for the clipboard, in the chosen format, or None without a position."""
    lat, lon = (navigation or {}).get("latitude"), (navigation or {}).get("longitude")
    return None if lat is None or lon is None else position_text(lat, lon, name)


def speed(value):
    return UNKNOWN if value is None else f"{value:.2f} m/s ({value * 3.6:.1f} km/h)"


def fix_state(navigation):
    """(state, title) where state is one of waiting, valid, stale, none."""
    if not navigation:
        return "waiting", "Waiting for Navigation"
    if not navigation.get("fresh"):
        return "stale", "Stale Navigation"
    if navigation.get("fix_ok"):
        return "valid", "Valid Fix"
    return "none", "No Fix"


def utc(navigation):
    nav = navigation or {}
    if not nav.get("utc"):
        return UNKNOWN
    if nav.get("utc_valid") is False:
        return f"{nav['utc']} (not valid)"
    if nav.get("utc_fully_resolved") is False:
        return f"{nav['utc']} (not fully resolved)"
    return nav["utc"]


# Vehicle page ----------------------------------------------------------------

UNIT_SYSTEMS = ("metric", "imperial")
SPEED_UNIT = {"metric": ("km/h", 3.6), "imperial": ("mph", 2.2369362920544)}
GAUGE_STEPS = {"metric": (60, 120, 180, 240, 300), "imperial": (40, 80, 120, 160, 200)}
CARDINALS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
FEET_PER_M = 3.28084
M_PER_MILE = 1609.344


def speed_in(value_m_s, units):
    """Speed in the display unit, or None."""
    return None if value_m_s is None else value_m_s * SPEED_UNIT[units][1]


def gauge_max(peak_display, units):
    """Smallest gauge scale that keeps the peak below 95 % of full scale."""
    steps = GAUGE_STEPS[units]
    peak = peak_display or 0
    return next((step for step in steps if peak <= step * 0.95), steps[-1])


def trip_distance(value_m, units):
    """(number, unit) for a trip distance: metres/km or miles."""
    if value_m is None:
        return UNKNOWN, ""
    if units == "imperial":
        miles = value_m / M_PER_MILE
        return (f"{miles:.2f}" if miles < 10 else f"{miles:.1f}"), "mi"
    if value_m < 1000:
        return f"{value_m:.0f}", "m"
    km = value_m / 1000
    return (f"{km:.2f}" if km < 10 else f"{km:.1f}"), "km"


def altitude(value_m, units):
    if value_m is None:
        return UNKNOWN, ""
    return (f"{value_m * FEET_PER_M:,.0f}", "ft") if units == "imperial" else (f"{value_m:,.0f}", "m")


def cardinal(heading_deg):
    return CARDINALS[round(heading_deg % 360 / 22.5) % 16]


def duration(value_s):
    """h:mm:ss, or m:ss under an hour."""
    if value_s is None:
        return UNKNOWN
    total = int(value_s)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


# Sun (Dashboard) ---------------------------------------------------------------

POLAR = {"day": "Sun up all day", "night": "Sun down all day"}


def local_time(when):
    """A UTC datetime as a time in the computer's time zone, e.g. 07:38 BST."""
    return (when + timedelta(seconds=30)).astimezone().strftime("%H:%M %Z").strip()  # Nearest minute.


def day_length(value):
    minutes = round(value.total_seconds() / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def sun_elevation(degrees, phase):
    text = f"{degrees:+.0f}°".replace("-", "−")
    return text if phase == "day" else f"{text} ({phase})"


def sun_event(when, polar):
    return local_time(when) if when else POLAR.get(polar, "None today")


CLOCK_SOURCES = {"NAV-PVT": "NAV-PVT", "RMC": "NMEA RMC", "ZDA": "NMEA ZDA"}


def host_clock(value, live=True):
    """(text, level) for a snapshot's host_clock; level is 'ok', 'warning', 'error' or None."""
    if not live:
        return "Not measured for recorded data", None
    if not value:
        return "Waiting for GNSS time", None
    return clock.describe(value["offset_s"]), clock.level(value["offset_s"])


def host_clock_basis(value):
    if not value:
        return "Needs a fully resolved UTC time from NAV-PVT, or NMEA RMC or ZDA with a fix"
    epochs = "epoch" if value["samples"] == 1 else "epochs"
    return (f"Median of {value['samples']} {epochs} of {CLOCK_SOURCES.get(value['source'], value['source'])}, "
            f"±{clock.PRECISION_S:g} s")
