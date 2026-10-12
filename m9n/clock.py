"""Clock check: how far the computer's clock is from GNSS time. Read-only: never sets the clock.

Each sample is the computer's time when a message was read minus the GNSS time it carries,
so USB and output latency (roughly 0.05-0.3 s, varying) makes the computer look slightly
fast. The median over recent epochs rejects the odd late read; the result is stated to
±0.2 s, not milliseconds. Only messages sent at their epoch count: a polled NAV-PVT carries
the last solution, which can be up to a second old. GTK-free so it can be unit-tested.
"""
from collections import deque
from datetime import datetime, timezone
from statistics import median

WINDOW = 30  # Epochs in the median.
PRECISION_S = 0.2
IN_STEP_S = 0.5  # Under this: green.
WRONG_S = 5.0  # Over this: red.
FRESH_S = 10.0  # A source counts while its samples are this recent (host seconds).
SOURCES = ("NAV-PVT", "RMC", "ZDA")  # In order of preference.
HINT = "Turn on automatic time (Settings → System → Date & Time) or check chronyd."


def _nmea_time(text, day, month, year):
    """UTC datetime from NMEA hhmmss.ss and a date, or None."""
    try:
        seconds = float(text[4:])
        whole = int(seconds)
        return datetime(year, month, day, int(text[:2]), int(text[2:4]), whole,
                        round((seconds - whole) * 1e6), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def gnss_time(event):
    """(source, UTC datetime) for a message carrying a trustworthy GNSS time, else None."""
    name = event.get("name", "")
    if name == "NAV-PVT":
        if not (event.get("utc_valid") and event.get("utc_fully_resolved") and event.get("utc")):
            return None
        when = datetime.fromisoformat(event["utc"].replace("Z", "+00:00"))
        # nano is signed: the rounded seconds field may be up to half a second either way.
        return "NAV-PVT", datetime.fromtimestamp(when.timestamp() + event.get("nano", 0) / 1e9, timezone.utc)
    if name.endswith("RMC"):
        stamp, text = event.get("date") or "", event.get("utc") or ""
        if not event.get("fix_ok") or len(stamp) != 6 or not stamp.isdigit() or len(text) < 6:
            return None  # Before a fix the receiver's time may come from its own clock.
        when = _nmea_time(text, int(stamp[:2]), int(stamp[2:4]), 2000 + int(stamp[4:]))
        return ("RMC", when) if when else None
    if name.endswith("ZDA"):
        text = event.get("utc") or ""
        if len(text) < 6 or not all(event.get(k) for k in ("day", "month", "year")):
            return None  # u-blox leaves the fields empty until the time is known.
        when = _nmea_time(text, event["day"], event["month"], event["year"])
        return ("ZDA", when) if when else None
    return None


class ClockCheck:
    """Median offset of the computer's clock from GNSS time over the last WINDOW epochs."""

    def __init__(self):
        self.samples = {source: deque(maxlen=WINDOW) for source in SOURCES}
        self.seen = {}  # Source -> (last GNSS time, host time it was read).

    def add(self, event, received, polled=False):
        """Record a message read at received (the computer's UTC, as a POSIX timestamp)."""
        if received is None or polled:
            return
        found = gnss_time(event)
        if not found:
            return
        source, when = found
        last = self.seen.get(source)
        if last and last[0] == when:
            return  # The same epoch again: the first read is the closest to it.
        self.seen[source] = (when, received)
        self.samples[source].append(received - when.timestamp())

    def source(self, received):
        """The preferred source with recent samples, or None."""
        for source in SOURCES:
            last = self.seen.get(source)
            if last and self.samples[source] and abs(received - last[1]) <= FRESH_S:
                return source
        return None

    def result(self, received):
        """{offset_s, samples, source} (offset > 0: the computer is fast), or None while waiting."""
        source = self.source(received) if received is not None else None
        if source is None:
            return None
        values = self.samples[source]
        return {"offset_s": round(median(values), 3), "samples": len(values), "source": source}


def level(offset_s):
    """'ok' under 0.5 s, 'warning' up to 5 s, 'error' beyond."""
    size = abs(offset_s)
    return "ok" if size < IN_STEP_S else "warning" if size <= WRONG_S else "error"


def describe(offset_s):
    """'in step (±0.2 s)', '1.3 s fast', '42 s slow', '3 min 5 s fast', '2 days slow'."""
    size = abs(offset_s)
    if size < PRECISION_S:
        return f"in step (±{PRECISION_S:g} s)"
    way = "fast" if offset_s > 0 else "slow"
    if size < 10:
        return f"{size:.1f} s {way}"
    if size < 60:
        return f"{size:.0f} s {way}"
    if size < 3600:
        minutes, seconds = divmod(round(size), 60)
        return f"{minutes} min {seconds} s {way}" if seconds else f"{minutes} min {way}"
    if size < 2 * 86400:
        hours, rest = divmod(round(size), 3600)
        minutes = rest // 60
        return f"{hours} h {minutes} min {way}" if minutes else f"{hours} h {way}"
    return f"{size / 86400:.0f} days {way}"
