"""Synthetic UBX fixture generation. Never presented as actual hardware data."""
import struct
from .protocol import nmea, ubx


def sample(epoch=1000, fault=None):
    p = bytearray(92)
    struct.pack_into("<IHBBBBBB", p, 0, epoch, 2026, 10, 11, 12, 0, (epoch // 1000) % 60, 7)
    p[20:24] = bytes((0 if fault == "no-fix" else 3, 0 if fault == "no-fix" else 1, 0, 10))
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
