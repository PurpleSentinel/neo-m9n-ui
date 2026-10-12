"""Synthetic UBX fixture generation. Never presented as actual hardware data."""
import math
import struct
from datetime import datetime, timezone
from .protocol import nmea, ubx

ORIGIN = (54.1667070, -4.4503420)  # Latitude, longitude of the static demo fix.
LOOP_M = 400.0  # Radius of the "drive" scenario's circuit.


def drive(seconds):
    """(lat, lon, speed m/s, heading deg, altitude MSL m) on a clockwise loop with varying speed.

    Speed swings between 3 and 25 m/s every 90 s; the distance travelled is its exact
    integral, so position, speed and heading stay consistent. One hill per lap.
    """
    speed = 14 + 11 * math.sin(2 * math.pi * seconds / 90)
    travelled = 14 * seconds + 11 * 90 / (2 * math.pi) * (1 - math.cos(2 * math.pi * seconds / 90))
    angle = travelled / LOOP_M
    north, east = LOOP_M * math.cos(angle), LOOP_M * math.sin(angle)
    lat = ORIGIN[0] + north / 111320
    lon = ORIGIN[1] + east / (111320 * math.cos(math.radians(ORIGIN[0])))
    return lat, lon, speed, (math.degrees(angle) + 90) % 360, 40 + 25 * math.sin(angle)


def sample(epoch=1000, fault=None, utc=None):
    """One epoch of synthetic output. utc: its GNSS time (POSIX seconds); None: 2026-10-11 12:00:ss."""
    p = bytearray(92)
    if utc is None:
        struct.pack_into("<IHBBBBBB", p, 0, epoch, 2026, 10, 11, 12, 0, (epoch // 1000) % 60, 7)
    else:
        whole = round(utc)
        t = datetime.fromtimestamp(whole, timezone.utc)
        struct.pack_into("<IHBBBBBB", p, 0, epoch, t.year, t.month, t.day, t.hour, t.minute, t.second, 7)
        struct.pack_into("<Ii", p, 12, 30, round((utc - whole) * 1e9))  # tAcc 30 ns; signed nano.
    p[20:24] = bytes((0 if fault == "no-fix" else 3, 0 if fault == "no-fix" else 1, 0, 10))
    if fault == "drive":
        lat, lon, speed, heading, altitude = drive((epoch - 1000) / 1000)
        struct.pack_into("<iiiiII", p, 24, round(lon * 1e7), round(lat * 1e7), round((altitude + 35) * 1000),
                         round(altitude * 1000), 1500, 2500)
        struct.pack_into("<iii", p, 48, round(speed * 1000 * math.cos(math.radians(heading))),
                         round(speed * 1000 * math.sin(math.radians(heading))), 0)
        struct.pack_into("<ii", p, 60, round(speed * 1000), round(heading * 1e5))
    else:
        struct.pack_into("<iiiiII", p, 24, -44503420, 541667070, 45000, 10000, 1500, 2500)
        struct.pack_into("<ii", p, 60, 1250, 9000000)
    struct.pack_into("<H", p, 76, 125)
    sats = bytearray(struct.pack("<IBBH", epoch, 1, 4, 0))
    for gnss, sv, cno in ((0, 3, 42), (2, 12, 39), (3, 5, 35), (6, 2, 31)):
        sats.extend(struct.pack("<BBBbhhI", gnss, sv, cno, 45, 120, 3, 13))
    rf = bytearray(28)
    rf[1] = 1
    rf[4:8] = bytes((0, 3 if fault == "rf" else 1, 3 if fault == "rf" else 2, 1))
    struct.pack_into("<HH", rf, 16, 100, 2000)
    rf[20] = 220 if fault == "rf" else 5
    status = struct.pack("<IBBBBII", epoch, p[20], p[21], 0, 16 if fault == "rf" else 8, 24000, epoch)
    version = b"SPG 4.04".ljust(30, b"\0") + b"00190000".ljust(10, b"\0")
    version += b"MOD=NEO-M9N".ljust(30, b"\0") + b"PROTVER=32.01".ljust(30, b"\0")
    raw = ubx(10, 4, version) + ubx(1, 7, p) + ubx(1, 53, sats) + ubx(1, 3, status) + ubx(10, 56, rf)
    raw += nmea("GNTXT,01,01,02,Synthetic demonstration")
    if fault == "corrupt":
        broken = bytearray(ubx(1, 7, p))
        broken[-1] ^= 1
        raw += broken
    return raw
