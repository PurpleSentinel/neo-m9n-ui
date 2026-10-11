"""Bounded incremental UBX/NMEA framing and selected M9 decoders.

All offsets are payload-relative; little endian. Unsupported messages are
preserved as metadata rather than guessed. No receiver configuration writes.
"""
import math
import struct
from collections import Counter

NAMES = {(10, 4): "MON-VER", (1, 7): "NAV-PVT", (1, 53): "NAV-SAT",
         (1, 3): "NAV-STATUS", (10, 56): "MON-RF", (5, 0): "ACK-NAK",
         (5, 1): "ACK-ACK"}
GNSS = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 4: "IMES",
        5: "QZSS", 6: "GLONASS"}
FIX = {0: "no fix", 1: "dead reckoning", 2: "2D", 3: "3D",
       4: "GNSS + dead reckoning", 5: "time only"}


def checksum(data):
    a = b = 0
    for value in data:
        a = (a + value) & 255
        b = (b + a) & 255
    return bytes((a, b))


def ubx(cls, ident, payload=b""):
    body = bytes((cls, ident)) + struct.pack("<H", len(payload)) + payload
    return b"\xb5\x62" + body + checksum(body)


def nmea(body):
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return f"${body}*{value:02X}\r\n".encode("ascii")


class Framer:
    def __init__(self, timeout=2.0):
        self.buffer = bytearray()
        self.since = None
        self.timeout = timeout
        self.counts = Counter()

    def feed(self, chunk, now):
        self.buffer.extend(chunk)
        self.counts["bytes"] += len(chunk)
        result = []
        while self.buffer:
            starts = [i for i in (self.buffer.find(b"\xb5"), self.buffer.find(b"$")) if i >= 0]
            if not starts:
                self.counts["discarded_bytes"] += len(self.buffer)
                self.buffer.clear()
                break
            start = min(starts)
            if start:
                self.counts["discarded_bytes"] += start
                del self.buffer[:start]
                self.since = None
            if self.since is None:
                self.since = now
            if self.buffer[0] == 0xB5:
                if len(self.buffer) >= 2 and self.buffer[1] != 0x62:
                    del self.buffer[0]
                    self.counts["discarded_bytes"] += 1
                    self.since = None
                    continue
                if len(self.buffer) >= 6:
                    length = struct.unpack_from("<H", self.buffer, 4)[0]
                    if length > 8192:
                        result.append(self.error("oversize_frame", "UBX payload exceeds 8192 bytes"))
                        del self.buffer[0]
                        self.since = None
                        continue
                    total = length + 8
                    if len(self.buffer) >= total:
                        frame = bytes(self.buffer[:total])
                        if checksum(frame[2:-2]) != frame[-2:]:
                            result.append(self.error("checksum_errors", "UBX checksum mismatch"))
                            # Scan inside the rejected frame to recover an embedded valid frame.
                            del self.buffer[0]
                        else:
                            del self.buffer[:total]
                            self.counts["ubx_frames"] += 1
                            result.append({"protocol": "UBX", "class": frame[2], "id": frame[3],
                                           "payload": frame[6:-2]})
                        self.since = None
                        continue
            else:
                end = self.buffer.find(b"\n")
                if end >= 0 and end < 1024:
                    frame = bytes(self.buffer[:end + 1]).rstrip(b"\r\n")
                    del self.buffer[:end + 1]
                    self.since = None
                    try:
                        body, check = frame[1:].rsplit(b"*", 1)
                        value = 0
                        for byte in body:
                            value ^= byte
                        if len(check) != 2 or int(check, 16) != value:
                            raise ValueError("checksum")
                        sentence = body.decode("ascii")
                    except (ValueError, UnicodeError):
                        result.append(self.error("checksum_errors", "NMEA checksum/encoding invalid"))
                        continue
                    self.counts["nmea_frames"] += 1
                    result.append({"protocol": "NMEA", "sentence": sentence})
                    continue
                if len(self.buffer) > 1024:
                    result.append(self.error("oversize_frame", "NMEA sentence exceeds 1024 bytes"))
                    del self.buffer[0]
                    self.since = None
                    continue
            if now - self.since >= self.timeout:
                result.append(self.error("truncated_frames", "Incomplete frame timed out; resynchronising"))
                del self.buffer[0]
                self.since = None
                continue
            break
        if not self.buffer:
            self.since = None
        return result

    def error(self, key, text):
        self.counts[key] += 1
        return {"protocol": "ERROR", "name": key, "text": text}


def require(payload, minimum, name):
    if len(payload) < minimum:
        raise ValueError(f"{name}: short payload ({len(payload)} < {minimum})")


def unpack(fmt, payload, offset):
    return struct.unpack_from("<" + fmt, payload, offset)[0]


def number(value):
    if not value:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite NMEA field")
    return result


def coordinate(value, hemisphere):
    if not value:
        return None
    raw = number(value)
    if raw < 0:
        raise ValueError("negative NMEA coordinate field")
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    limit = 90 if hemisphere in ("N", "S") else 180
    if hemisphere not in ("N", "S", "E", "W") or not 0 <= minutes < 60:
        raise ValueError("invalid NMEA coordinate")
    result = degrees + minutes / 60
    if result > limit:
        raise ValueError("NMEA coordinate out of range")
    return -result if hemisphere in ("S", "W") else result


def decode(frame):
    if frame["protocol"] == "ERROR":
        return frame
    if frame["protocol"] == "NMEA":
        return decode_nmea(frame["sentence"])
    cls, ident, p = frame["class"], frame["id"], frame["payload"]
    name = NAMES.get((cls, ident), f"UBX-{cls:02X}-{ident:02X}")
    event = {"protocol": "UBX", "name": name, "class": cls, "id": ident}
    if name == "MON-VER":
        require(p, 40, name)
        event.update(software=p[:30].split(b"\0", 1)[0].decode("ascii", "replace"),
                     hardware=p[30:40].split(b"\0", 1)[0].decode("ascii", "replace"),
                     extensions=[p[i:i + 30].split(b"\0", 1)[0].decode("ascii", "replace")
                                 for i in range(40, len(p), 30)])
    elif name == "NAV-PVT":
        require(p, 92, name)
        year = unpack("H", p, 4)
        time_valid = bool(p[11] & 3 == 3)
        valid = bool(p[21] & 1) and p[20] in (2, 3, 4) and not (unpack("H", p, 78) & 1)
        event.update(itow_ms=unpack("I", p, 0), fix_type=FIX.get(p[20], "unknown"),
                     fix_ok=valid, satellites_used=p[23],
                     utc=(f"{year:04d}-{p[6]:02d}-{p[7]:02d}T{p[8]:02d}:{p[9]:02d}:{p[10]:02d}Z"
                          if time_valid else None),
                     utc_valid=time_valid, utc_fully_resolved=bool(p[11] & 4),
                     time_accuracy_ns=unpack("I", p, 12), nano=unpack("i", p, 16),
                     latitude=unpack("i", p, 28) * 1e-7 if valid else None,
                     longitude=unpack("i", p, 24) * 1e-7 if valid else None,
                     altitude_msl_m=unpack("i", p, 36) / 1000 if valid else None,
                     altitude_ellipsoid_m=unpack("i", p, 32) / 1000 if valid else None,
                     horizontal_accuracy_m=unpack("I", p, 40) / 1000 if valid else None,
                     vertical_accuracy_m=unpack("I", p, 44) / 1000 if valid else None,
                     speed_m_s=unpack("i", p, 60) / 1000 if valid else None,
                     heading_deg=unpack("i", p, 64) * 1e-5 if valid else None,
                     pdop=unpack("H", p, 76) / 100)
    elif name == "NAV-SAT":
        require(p, 8, name)
        if p[4] != 1:
            raise ValueError("Unsupported NAV-SAT version")
        require(p, 8 + p[5] * 12, name)
        sats = []
        for i in range(p[5]):
            o = 8 + i * 12
            flags = unpack("I", p, o + 8)
            sats.append({"gnss": GNSS.get(p[o], f"GNSS-{p[o]}"), "sv": p[o + 1],
                         "cno_dbhz": p[o + 2], "elevation_deg": unpack("b", p, o + 3),
                         "azimuth_deg": unpack("h", p, o + 4), "used": bool(flags & 8),
                         "quality": flags & 7, "health": (flags >> 4) & 3})
        event.update(itow_ms=unpack("I", p, 0), satellites=sats)
    elif name == "NAV-STATUS":
        require(p, 16, name)
        event.update(itow_ms=unpack("I", p, 0), fix_type=FIX.get(p[4], "unknown"),
                     fix_ok=bool(p[5] & 1), spoof_state=(p[7] >> 3) & 3,
                     ttff_s=unpack("I", p, 8) / 1000, uptime_s=unpack("I", p, 12) / 1000)
    elif name == "MON-RF":
        require(p, 4, name)
        if p[0] != 0:
            raise ValueError("Unsupported MON-RF version")
        require(p, 4 + p[1] * 24, name)
        blocks = []
        for i in range(p[1]):
            o = 4 + i * 24
            blocks.append({"block": p[o], "jamming_state": p[o + 1] & 3,
                           "antenna_status": {0: "INIT", 1: "UNKNOWN", 2: "OK", 3: "SHORT", 4: "OPEN"}.get(p[o + 2], "UNKNOWN"),
                           "antenna_power": {0: "OFF", 1: "ON", 2: "UNKNOWN"}.get(p[o + 3], "UNKNOWN"),
                           "post_status": unpack("I", p, o + 4),
                           "noise_per_ms": unpack("H", p, o + 12),
                           "agc_count": unpack("H", p, o + 14),
                           "cw_suppression": p[o + 16]})
        event["blocks"] = blocks
    elif cls == 4:
        event.update(name="INF-" + {0: "ERROR", 1: "WARNING", 2: "NOTICE", 3: "TEST", 4: "DEBUG"}.get(ident, str(ident)),
                     text=p.decode("ascii", "replace"))
    elif cls == 5:
        require(p, 2, name)
        event["target"] = NAMES.get(tuple(p[:2]), f"UBX-{p[0]:02X}-{p[1]:02X}")
    else:
        event["payload_bytes"] = len(p)
    return event


def decode_nmea(sentence):
    f = sentence.split(",")
    kind = f[0][-3:]
    event = {"protocol": "NMEA", "name": f[0], "sentence": sentence}
    if kind == "GGA":
        if len(f) < 15:
            raise ValueError("Short GGA")
        quality = int(f[6] or 0)
        valid = quality in (1, 2, 3, 4, 5)
        event.update(kind="position", fix_type=f"NMEA quality {quality}", fix_ok=valid,
                     utc=f[1] or None, epoch=f[1] or None,
                     latitude=coordinate(f[2], f[3]) if valid else None,
                     longitude=coordinate(f[4], f[5]) if valid else None,
                     satellites_used=int(f[7] or 0), hdop=number(f[8]),
                     altitude_msl_m=number(f[9]) if valid else None)
    elif kind == "RMC":
        if len(f) < 10:
            raise ValueError("Short RMC")
        valid = f[2] == "A" and (len(f) <= 12 or f[12] not in ("N", "E", "M", "S"))
        event.update(kind="position", fix_type="NMEA RMC active" if valid else "no fix",
                     fix_ok=valid, utc=f[1] or None, date=f[9] or None, epoch=f[1] or None,
                     latitude=coordinate(f[3], f[4]) if valid else None,
                     longitude=coordinate(f[5], f[6]) if valid else None,
                     speed_m_s=number(f[7]) * 0.514444 if valid and f[7] else None,
                     heading_deg=number(f[8]) if valid else None)
    elif kind == "GSA":
        if len(f) < 18:
            raise ValueError("Short GSA")
        event.update(pdop=number(f[15]), hdop=number(f[16]), vdop=number(f[17]))
    elif kind == "GSV":
        if len(f) < 4:
            raise ValueError("Short GSV")
        # Fragment metadata only: NAV-SAT is the authoritative satellite table.
        event.update(satellites_in_view=int(f[3] or 0), fragment=int(f[2] or 0))
    elif kind == "TXT":
        event["text"] = ",".join(f[4:])
        event["severity"] = {"00": "ERROR", "01": "WARNING"}.get(f[3] if len(f) > 3 else "", "NOTICE")
    return event
