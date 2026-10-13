"""Coordinate formats for display: decimal degrees, degrees-minutes, DMS, British National Grid, UTM.

Display only: logs, JSONL, reports and GPX always keep decimal WGS 84. GTK-free so it can be
unit-tested. The British National Grid uses the OS 7-parameter Helmert transformation from
WGS 84 to OSGB36 and Transverse Mercator on Airy 1830: about 3-5 m from OSTN15, which is fine
for display and the receiver's accuracy but not survey grade. UTM uses the Krüger series on
WGS 84 (sub-millimetre), with the Norway and Svalbard zone exceptions.
"""
import math

FORMATS = ("decimal", "dm", "dms", "bng", "utm")
OUTSIDE_BNG = "Outside the British National Grid"
OUTSIDE_UTM = "Outside UTM (polar region)"

# WGS 84 and Airy 1830 ellipsoids: (a, b).
WGS84 = (6378137.0, 6356752.314245)
AIRY = (6377563.396, 6356256.909)
# WGS 84 -> OSGB36: translations (m), scale (ppm), rotations (arc seconds).
HELMERT = (-446.448, 125.157, -542.060, 20.4894, -0.1502, -0.2470, -0.8421)
# National Grid projection: scale on the central meridian, true origin, false origin.
NG_F0, NG_LAT0, NG_LON0, NG_E0, NG_N0 = 0.9996012717, 49.0, -2.0, 400000.0, -100000.0

# Great Britain, the Isle of Man and their islands (Scilly to Shetland, St Kilda, the Smalls),
# as a coarse (lon, lat) outline that leaves out Ireland, the Channel Islands and France.
GB_OUTLINE = (
    (-6.6, 49.85), (-5.0, 49.8), (-1.5, 50.45), (0.5, 50.65), (1.2, 50.9), (1.65, 51.2),
    (1.95, 52.0), (1.95, 52.9), (0.5, 53.6), (-0.3, 54.5), (-1.3, 55.4), (-1.6, 56.1),
    (-1.5, 57.5), (-1.6, 58.0), (-0.6, 60.9), (-1.0, 61.0), (-2.2, 60.2), (-3.6, 59.4),
    (-5.1, 58.7), (-6.3, 58.7), (-8.8, 57.9), (-7.9, 56.7), (-7.0, 56.2), (-6.6, 55.6),
    (-5.95, 55.3), (-5.3, 54.9), (-5.0, 54.4), (-5.0, 53.95), (-5.0, 52.6), (-5.5, 52.3),
    (-5.8, 51.6), (-6.6, 50.3))

BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"  # 8° bands from 80° S; X covers 72-84° N.


# Degrees ------------------------------------------------------------------

def _hemisphere(value, shown, positive, negative):
    """The letter for value; a value that rounds to zero is on the positive side."""
    return positive if value >= 0 or shown == 0 else negative


def decimal(value, positive, negative, digits=7):
    """'54.1671200° N'."""
    size = round(abs(value), digits)
    return f"{size:.{digits}f}° {_hemisphere(value, size, positive, negative)}"


def degrees_minutes(value, positive, negative, digits=3):
    """'54° 10.027′ N': minutes rounded first, so 59.9996′ carries to the next degree."""
    units = round(abs(value) * 60 * 10 ** digits)
    degrees, minutes = divmod(units, 60 * 10 ** digits)
    width = 3 + digits if digits else 2
    text = f"{minutes / 10 ** digits:0{width}.{digits}f}"
    return f"{degrees}° {text}′ {_hemisphere(value, units, positive, negative)}"


def degrees_minutes_seconds(value, positive, negative, digits=1):
    """'54° 10′ 01.6″ N': seconds rounded first, so 59.96″ carries to the next minute."""
    units = round(abs(value) * 3600 * 10 ** digits)
    degrees, rest = divmod(units, 3600 * 10 ** digits)
    minutes, seconds = divmod(rest, 60 * 10 ** digits)
    width = 3 + digits if digits else 2
    text = f"{seconds / 10 ** digits:0{width}.{digits}f}"
    return f"{degrees}° {minutes:02d}′ {text}″ {_hemisphere(value, units, positive, negative)}"


# British National Grid ---------------------------------------------------

def _to_cartesian(lat, lon, height, ellipsoid):
    a, b = ellipsoid
    e2 = 1 - (b * b) / (a * a)
    phi, lam = math.radians(lat), math.radians(lon)
    nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((nu + height) * math.cos(phi) * math.cos(lam), (nu + height) * math.cos(phi) * math.sin(lam),
            ((1 - e2) * nu + height) * math.sin(phi))


def _to_geodetic(x, y, z, ellipsoid):
    a, b = ellipsoid
    e2 = 1 - (b * b) / (a * a)
    p = math.hypot(x, y)
    phi = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        phi = math.atan2(z + e2 * nu * math.sin(phi), p)
    return math.degrees(phi), math.degrees(math.atan2(y, x))


def wgs84_to_osgb36(lat, lon, height=0.0):
    """WGS 84 latitude and longitude -> OSGB36 (Airy 1830), by the 7-parameter Helmert transformation."""
    tx, ty, tz, s, rx, ry, rz = HELMERT
    s *= 1e-6
    rx, ry, rz = (math.radians(r / 3600) for r in (rx, ry, rz))
    x, y, z = _to_cartesian(lat, lon, height, WGS84)
    moved = (tx + (1 + s) * x - rz * y + ry * z,
             ty + rz * x + (1 + s) * y - rx * z,
             tz - ry * x + rx * y + (1 + s) * z)
    return _to_geodetic(*moved, AIRY)


def national_grid(lat, lon):
    """OSGB36 latitude and longitude -> National Grid (easting, northing) in metres.

    Transverse Mercator as in Ordnance Survey's "A guide to coordinate systems in Great Britain".
    """
    a, b = AIRY
    f0 = NG_F0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    phi, lam = math.radians(lat), math.radians(lon)
    phi0, lam0 = math.radians(NG_LAT0), math.radians(NG_LON0)
    sin, cos, tan = math.sin(phi), math.cos(phi), math.tan(phi)
    nu = a * f0 / math.sqrt(1 - e2 * sin * sin)
    rho = a * f0 * (1 - e2) / (1 - e2 * sin * sin) ** 1.5
    eta2 = nu / rho - 1
    d, s = phi - phi0, phi + phi0
    m = b * f0 * ((1 + n + 5 / 4 * n ** 2 + 5 / 4 * n ** 3) * d
                  - (3 * n + 3 * n ** 2 + 21 / 8 * n ** 3) * math.sin(d) * math.cos(s)
                  + (15 / 8 * n ** 2 + 15 / 8 * n ** 3) * math.sin(2 * d) * math.cos(2 * s)
                  - 35 / 24 * n ** 3 * math.sin(3 * d) * math.cos(3 * s))
    one = m + NG_N0
    two = nu / 2 * sin * cos
    three = nu / 24 * sin * cos ** 3 * (5 - tan ** 2 + 9 * eta2)
    three_a = nu / 720 * sin * cos ** 5 * (61 - 58 * tan ** 2 + tan ** 4)
    four = nu * cos
    five = nu / 6 * cos ** 3 * (nu / rho - tan ** 2)
    six = nu / 120 * cos ** 5 * (5 - 18 * tan ** 2 + tan ** 4 + 14 * eta2 - 58 * tan ** 2 * eta2)
    dl = lam - lam0
    northing = one + two * dl ** 2 + three * dl ** 4 + three_a * dl ** 6
    easting = NG_E0 + four * dl + five * dl ** 3 + six * dl ** 5
    return easting, northing


def in_great_britain(lat, lon):
    """Whether a WGS 84 position is in Great Britain, the Isle of Man or their islands (coarse outline)."""
    inside = False
    points = GB_OUTLINE
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def bng(lat, lon):
    """WGS 84 -> National Grid (easting, northing) in metres, or None outside Great Britain and the Isle of Man."""
    if not in_great_britain(lat, lon):
        return None
    easting, northing = national_grid(*wgs84_to_osgb36(lat, lon))
    if not (0 <= easting < 700000 and 0 <= northing < 1300000):
        return None
    return easting, northing


def grid_reference(easting, northing, figures=10):
    """'SC 38110 77434' (10 figures, 1 m) or 'SC 381 774' (6 figures, 100 m). Truncated, as grid references are."""
    e100, n100 = int(easting // 100000), int(northing // 100000)
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # 5 x 5 squares, no I.
    # First letter: 500 km square from the false origin of the lettering (S at 0, 0); second: 100 km square.
    first = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    second = (19 - n100) * 5 % 25 + e100 % 5
    digits = figures // 2
    scale = 10 ** (5 - digits)
    e = int(easting % 100000 // scale)
    n = int(northing % 100000 // scale)
    return f"{letters[first]}{letters[second]} {e:0{digits}d} {n:0{digits}d}"


# UTM -----------------------------------------------------------------------

def utm_zone(lat, lon):
    """(zone number, latitude band letter) with the Norway and Svalbard exceptions, or None beyond 80° S/84° N."""
    if not -80 <= lat <= 84:
        return None
    lon = (lon + 180) % 360 - 180
    zone = min(int((lon + 180) // 6) + 1, 60)
    band = BAND_LETTERS[min(int((lat + 80) // 8), len(BAND_LETTERS) - 1)]
    if band == "V" and 3 <= lon < 12:
        zone = 32  # South-west Norway.
    if band == "X" and 0 <= lon < 42:
        zone = 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37  # Svalbard.
    return zone, band


def utm(lat, lon):
    """WGS 84 -> (zone, band, easting, northing) in metres, or None in the polar regions.

    Krüger's series to sixth order in n (Karney 2011), as used by GeographicLib and PROJ.
    """
    found = utm_zone(lat, lon)
    if found is None:
        return None
    zone, band = found
    a, b = WGS84
    f = 1 - b / a
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64 + n ** 6 / 256)
    alpha = (None,
             n / 2 - 2 / 3 * n ** 2 + 5 / 16 * n ** 3 + 41 / 180 * n ** 4 - 127 / 288 * n ** 5 + 7891 / 37800 * n ** 6,
             13 / 48 * n ** 2 - 3 / 5 * n ** 3 + 557 / 1440 * n ** 4 + 281 / 630 * n ** 5 - 1983433 / 1935360 * n ** 6,
             61 / 240 * n ** 3 - 103 / 140 * n ** 4 + 15061 / 26880 * n ** 5 + 167603 / 181440 * n ** 6,
             49561 / 161280 * n ** 4 - 179 / 168 * n ** 5 + 6601661 / 7257600 * n ** 6,
             34729 / 80640 * n ** 5 - 3418889 / 1995840 * n ** 6,
             212378941 / 319334400 * n ** 6)
    e = math.sqrt(f * (2 - f))
    phi = math.radians(lat)
    lam = math.radians((lon - (zone * 6 - 183) + 180) % 360 - 180)
    tau = math.tan(phi)
    sigma = math.sinh(e * math.atanh(e * tau / math.sqrt(1 + tau * tau)))
    tau_p = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
    xi_p = math.atan2(tau_p, math.cos(lam))
    eta_p = math.asinh(math.sin(lam) / math.sqrt(tau_p * tau_p + math.cos(lam) ** 2))
    xi = xi_p + sum(alpha[j] * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p) for j in range(1, 7))
    eta = eta_p + sum(alpha[j] * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p) for j in range(1, 7))
    k0 = 0.9996
    easting = 500000 + k0 * big_a * eta
    northing = k0 * big_a * xi + (10000000 if lat < 0 else 0)
    return zone, band, easting, northing


def utm_text(lat, lon):
    """'30U 403291 6003129' (truncated to the metre), or OUTSIDE_UTM."""
    found = utm(lat, lon)
    if found is None:
        return OUTSIDE_UTM
    zone, band, easting, northing = found
    return f"{zone}{band} {int(easting):06d} {int(northing):07d}"


# Formatting a position ---------------------------------------------------

def rows(lat, lon, name):
    """[(title, text)] for showing a position in the named format (unknown names fall back to decimal)."""
    if name == "dm":
        return [("Latitude", degrees_minutes(lat, "N", "S")), ("Longitude", degrees_minutes(lon, "E", "W"))]
    if name == "dms":
        return [("Latitude", degrees_minutes_seconds(lat, "N", "S")),
                ("Longitude", degrees_minutes_seconds(lon, "E", "W"))]
    if name == "bng":
        grid = bng(lat, lon)
        if grid is None:
            return [("Grid reference", OUTSIDE_BNG)]
        return [("Grid reference", grid_reference(*grid)), ("Short form", grid_reference(*grid, figures=6))]
    if name == "utm":
        return [("UTM (WGS 84)", utm_text(lat, lon))]
    return [("Latitude", decimal(lat, "N", "S")), ("Longitude", decimal(lon, "E", "W"))]


def one_line(lat, lon, name):
    """The position as a single line in the named format: what Copy Coordinates copies."""
    if name == "bng":
        grid = bng(lat, lon)
        return grid_reference(*grid) if grid else OUTSIDE_BNG
    return ", ".join(text for _, text in rows(lat, lon, name))
