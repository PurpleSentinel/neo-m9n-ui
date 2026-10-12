"""Trip statistics from successive navigation epochs: distance, moving time and speeds.

Fed every navigation epoch by State, so the figures do not depend on the display
refresh rate and a fast replay counts the same distance as the live drive. Only
valid, fresh fixes count. Below walking pace GNSS positions wander, so stationary
segments are skipped; a jump implying an impossible speed is treated as a glitch.
"""
import math

from .sun import gnss_datetime

EARTH_RADIUS_M = 6371008.8
MOVING_M_S = 0.5  # Below this the receiver is treated as stationary.
GLITCH_M_S = 150.0  # Implied speed above this (540 km/h) is a position glitch, not travel.
WEEK_S = 604800
DAY_S = 86400
SEGMENT_GAP_S = 10.0  # A longer gap between valid fixes (a tunnel) starts a new track segment.
MAX_POINTS = 200000  # About 55 hours at 1 Hz; beyond this the older half is thinned.


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def gnss_time(nav):
    """(clock, seconds) for the epoch: NAV-PVT time of week or NMEA time of day; None if unknown."""
    if nav.get("itow_ms") is not None:
        return "tow", nav["itow_ms"] / 1000
    epoch = nav.get("epoch")
    if isinstance(epoch, str) and len(epoch) >= 6:
        try:
            return "tod", int(epoch[:2]) * 3600 + int(epoch[2:4]) * 60 + float(epoch[4:])
        except ValueError:
            return None
    return None


class Track:
    """Every valid epoch's position since the trip started, for export (e.g. GPX).

    Each point is a dict: utc (ISO 8601 or None), lat, lon, alt (MSL, m), speed (m/s),
    course (deg) and start (True where a new segment begins: the first point, or after a gap).
    """

    def __init__(self, limit=MAX_POINTS):
        self.limit = limit
        self.points = []

    def reset(self):
        self.points = []

    def add(self, nav, gap_s):
        """Add an epoch; gap_s is the GNSS time since the previous point, or None if unknown."""
        when = gnss_datetime(nav)
        self.points.append({
            "utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
            "lat": nav["latitude"], "lon": nav["longitude"], "alt": nav.get("altitude_msl_m"),
            "speed": nav.get("speed_m_s"), "course": nav.get("heading_deg"),
            "start": not self.points or gap_s is None or gap_s > SEGMENT_GAP_S})
        if len(self.points) > self.limit:
            self.thin()

    def thin(self):
        """Drop every other point in the older half, keeping segment starts and the newest points."""
        half = len(self.points) // 2
        older = [p for i, p in enumerate(self.points[:half]) if i % 2 == 0 or p["start"]]
        self.points = older + self.points[half:]


class Marks:
    """Numbered positions the user marked during the trip (waypoints), oldest first.

    Each mark is a dict: number, utc (ISO 8601 or None), lat, lon, alt (MSL, m),
    accuracy_m (horizontal) and note. Numbers are never reused after a delete, so
    "Mark 3" in a log always means the same place; undoing the newest mark frees its number.
    """
    NOTE_MAX = 200

    def __init__(self):
        self.reset()

    def reset(self):
        self.items = []
        self.next_number = 1

    def add(self, nav):
        """Mark a navigation epoch's position; the caller checks the fix is valid and fresh."""
        when = gnss_datetime(nav)
        mark = {"number": self.next_number, "utc": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else None,
                "lat": nav["latitude"], "lon": nav["longitude"], "alt": nav.get("altitude_msl_m"),
                "accuracy_m": nav.get("horizontal_accuracy_m"), "note": ""}
        self.items.append(mark)
        self.next_number += 1
        return dict(mark)

    def find(self, number):
        return next((m for m in self.items if m["number"] == number), None)

    def delete(self, number):
        """Remove a mark; returns it (for undo) or None."""
        mark = self.find(number)
        if mark:
            self.items.remove(mark)
        return mark

    def undo_add(self, number):
        """Take back the newest mark, freeing its number for the next one."""
        mark = self.delete(number)
        if mark and number == self.next_number - 1:
            self.next_number -= 1
        return mark

    def restore(self, mark):
        """Put a deleted mark back in number order."""
        if self.find(mark["number"]) is None:
            self.items.append(dict(mark))
            self.items.sort(key=lambda m: m["number"])
            self.next_number = max(self.next_number, mark["number"] + 1)

    def set_note(self, number, note):
        mark = self.find(number)
        if mark is None:
            return None
        mark["note"] = " ".join(str(note).split())[:self.NOTE_MAX]
        return dict(mark)

    def copy(self):
        return [dict(m) for m in self.items]


class Trip:
    def __init__(self):
        self.track = Track()
        self.marks = Marks()
        self.reset()

    def reset(self):
        self.track.reset()
        self.marks.reset()
        self.distance_m = 0.0
        self.moving_s = 0.0
        self.max_speed_m_s = None
        self.epochs = 0  # Distinct valid epochs seen since the reset.
        self.last = None  # (clock, seconds, latitude, longitude, speed m/s or None)

    def update(self, nav):
        """Count one navigation epoch; returns True if it was a new, valid epoch."""
        if not nav or not nav.get("fix_ok") or not nav.get("fresh"):
            return False
        lat, lon, when = nav.get("latitude"), nav.get("longitude"), gnss_time(nav)
        if lat is None or lon is None or when is None:
            return False
        clock, t = when
        if self.last and self.last[:2] == (clock, t):
            return False  # Same epoch, e.g. GGA then RMC.
        speed = nav.get("speed_m_s")
        if speed is not None:
            self.max_speed_m_s = speed if self.max_speed_m_s is None else max(self.max_speed_m_s, speed)
        gap = None
        if self.last and self.last[0] == clock:
            dt = t - self.last[1]
            if dt < 0:
                dt += WEEK_S if clock == "tow" else DAY_S
            gap = dt
            step = distance_m(self.last[2], self.last[3], lat, lon)
            implied = step / dt if dt else 0
            # Moving at either end: the segment into a stop is still travel.
            moving = max(implied if speed is None else speed,
                         -1 if self.last[4] is None else self.last[4]) >= MOVING_M_S
            if 0 < dt and moving and step / dt <= GLITCH_M_S:
                self.distance_m += step
                self.moving_s += dt
        self.last = (clock, t, lat, lon, speed)
        self.track.add(nav, gap)
        self.epochs += 1
        return True

    @property
    def average_speed_m_s(self):
        return self.distance_m / self.moving_s if self.moving_s else None

    def summary(self):
        average = self.average_speed_m_s
        return {"distance_m": round(self.distance_m, 1), "moving_s": round(self.moving_s, 1),
                "average_speed_m_s": None if average is None else round(average, 3),
                "max_speed_m_s": self.max_speed_m_s, "epochs": self.epochs}
