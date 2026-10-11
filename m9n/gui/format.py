"""Text for displayed values. GTK-free; missing values are always "unknown", never blank."""
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


def coordinate(value, positive, negative):
    if value is None:
        return UNKNOWN
    return f"{abs(value):.7f}° {positive if value >= 0 else negative}"


def coordinates_text(navigation):
    """Plain decimal degrees for the clipboard, or None without a position."""
    lat, lon = (navigation or {}).get("latitude"), (navigation or {}).get("longitude")
    return None if lat is None or lon is None else f"{lat:.7f}, {lon:.7f}"


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
