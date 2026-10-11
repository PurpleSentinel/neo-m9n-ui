"""Trip statistics from successive navigation epochs: distance, moving time and speeds.

Fed every navigation epoch by State, so the figures do not depend on the display
refresh rate and a fast replay counts the same distance as the live drive. Only
valid, fresh fixes count. Below walking pace GNSS positions wander, so stationary
segments are skipped; a jump implying an impossible speed is treated as a glitch.
"""
import math

EARTH_RADIUS_M = 6371008.8
MOVING_M_S = 0.5  # Below this the receiver is treated as stationary.
GLITCH_M_S = 150.0  # Implied speed above this (540 km/h) is a position glitch, not travel.
WEEK_S = 604800
DAY_S = 86400


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


class Trip:
    def __init__(self):
        self.reset()

    def reset(self):
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
        if self.last and self.last[0] == clock:
            dt = t - self.last[1]
            if dt < 0:
                dt += WEEK_S if clock == "tow" else DAY_S
            step = distance_m(self.last[2], self.last[3], lat, lon)
            implied = step / dt if dt else 0
            # Moving at either end: the segment into a stop is still travel.
            moving = max(implied if speed is None else speed,
                         -1 if self.last[4] is None else self.last[4]) >= MOVING_M_S
            if 0 < dt and moving and step / dt <= GLITCH_M_S:
                self.distance_m += step
                self.moving_s += dt
        self.last = (clock, t, lat, lon, speed)
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
