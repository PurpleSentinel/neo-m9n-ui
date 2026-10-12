"""Sunrise, sunset, twilight and the sun's elevation for a position, with no network.

The NOAA solar position algorithm (the one behind NOAA's solar calculator), accurate to
about a minute between latitudes ±72° and still sensible beyond. Times are UTC datetimes;
the GUI shows them in the computer's local time zone. GTK-free so it can be unit-tested.
"""
import math
from datetime import datetime, time, timedelta, timezone

SUNRISE_DEG = -0.833  # Sun's centre at rise and set: refraction plus the sun's semi-diameter.
CIVIL_DEG = -6.0
NAUTICAL_DEG = -12.0
ASTRONOMICAL_DEG = -18.0


def _julian_century(when):
    jd = when.timestamp() / 86400 + 2440587.5
    return (jd - 2451545.0) / 36525


def _sun(t):
    """(declination in radians, equation of time in minutes) for Julian century t."""
    l0 = math.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360)
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    centre = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * m) * (0.019993 - 0.000101 * t)
              + math.sin(3 * m) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    apparent = math.radians(math.degrees(l0) + centre - 0.00569 - 0.00478 * math.sin(omega))
    mean_obliquity = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(obliquity) * math.sin(apparent))
    y = math.tan(obliquity / 2) ** 2
    equation = 4 * math.degrees(y * math.sin(2 * l0) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0)
                                - 0.5 * y * y * math.sin(4 * l0) - 1.25 * e * e * math.sin(2 * m))
    return declination, equation


def elevation(when, lat, lon):
    """The sun's geometric elevation in degrees (no refraction) at a UTC datetime."""
    declination, equation = _sun(_julian_century(when))
    minutes = when.hour * 60 + when.minute + when.second / 60 + when.microsecond / 6e7
    hour_angle = math.radians(((minutes + equation + 4 * lon) % 1440) / 4 - 180)
    phi = math.radians(lat)
    cos_zenith = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def solar_date(when, lon):
    """The date of the solar day at a longitude: the UTC date shifted by the longitude's time."""
    return (when + timedelta(minutes=4 * lon)).date()


def _event(day, lat, lon, altitude, rising):
    """UTC datetime when the sun's centre crosses altitude on the solar day, or None if it doesn't."""
    midnight = datetime.combine(day, time(), timezone.utc)
    guess = 720 - 4 * lon  # Minutes after UTC midnight; start from solar noon.
    for _ in range(3):  # Each pass uses the sun's position at the previous estimate.
        declination, equation = _sun(_julian_century(midnight + timedelta(minutes=guess)))
        phi = math.radians(lat)
        cos_h = ((math.sin(math.radians(altitude)) - math.sin(phi) * math.sin(declination))
                 / (math.cos(phi) * math.cos(declination)))
        if not -1 <= cos_h <= 1:
            return None
        h = math.degrees(math.acos(cos_h))
        guess = 720 - 4 * lon - equation + (-4 * h if rising else 4 * h)
    return midnight + timedelta(minutes=guess)


def day(when, lat, lon):
    """Sun times for the solar day containing when, at a position.

    Returns a dict of UTC datetimes or None: sunrise, sunset, dawn and dusk (civil twilight),
    day_length (a timedelta), and polar: "day" if the sun stays up, "night" if it stays down.
    """
    on = solar_date(when, lon)
    result = {"date": on,
              "sunrise": _event(on, lat, lon, SUNRISE_DEG, True), "sunset": _event(on, lat, lon, SUNRISE_DEG, False),
              "dawn": _event(on, lat, lon, CIVIL_DEG, True), "dusk": _event(on, lat, lon, CIVIL_DEG, False),
              "polar": None}
    if result["sunrise"] and result["sunset"]:
        result["day_length"] = result["sunset"] - result["sunrise"]
    else:
        noon = datetime.combine(on, time(), timezone.utc) + timedelta(minutes=720 - 4 * lon)
        up = elevation(noon, lat, lon) > SUNRISE_DEG
        result["polar"] = "day" if up else "night"
        result["day_length"] = timedelta(hours=24) if up else timedelta(0)
    return result


def phase(elevation_deg):
    """Day, or which twilight, for a sun elevation."""
    if elevation_deg >= SUNRISE_DEG:
        return "day"
    if elevation_deg >= CIVIL_DEG:
        return "civil twilight"
    if elevation_deg >= NAUTICAL_DEG:
        return "nautical twilight"
    if elevation_deg >= ASTRONOMICAL_DEG:
        return "astronomical twilight"
    return "night"


def gnss_datetime(nav):
    """The fix's UTC time from the receiver (NAV-PVT, or NMEA RMC time and date), or None."""
    nav = nav or {}
    text = nav.get("utc")
    if not text or nav.get("utc_valid") is False:
        return None
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    stamp = nav.get("date")
    if not stamp or len(stamp) != 6 or len(text) < 6:
        return None
    try:
        return datetime(2000 + int(stamp[4:]), int(stamp[2:4]), int(stamp[:2]), int(text[:2]), int(text[2:4]),
                        int(float(text[4:])), tzinfo=timezone.utc)
    except ValueError:
        return None

