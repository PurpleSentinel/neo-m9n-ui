"""GPX 1.1 export of a trip's track, for Google Earth, OsmAnd, QGIS, Strava and the like.

Speed and course go in Garmin's TrackPointExtension, which most readers understand and
the rest ignore. Nothing that identifies the computer is written: only the app, the
receiver model and the track itself. GTK-free so it can be unit-tested.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

GPX_NS = "http://www.topografix.com/GPX/1/1"
TPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"
SCHEMA = ("http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd "
          "http://www.garmin.com/xmlschemas/TrackPointExtension/v2 "
          "https://www8.garmin.com/xmlschemas/TrackPointExtensionv2.xsd")


def _sub(parent, tag, text=None, ns=GPX_NS, **attrib):
    element = ET.SubElement(parent, f"{{{ns}}}{tag}", attrib)
    if text is not None:
        element.text = text
    return element


def receiver_model(identity):
    """'NEO-M9N' from MON-VER's MOD= extension, else the firmware, else None."""
    identity = identity or {}
    for extension in identity.get("extensions", []):
        if extension.startswith("MOD="):
            return extension[4:].strip() or None
    return identity.get("software") or None


def segments(points):
    """Split points into lists, starting a new one at each point marked as a segment start."""
    result = []
    for point in points:
        if point["start"] or not result:
            result.append([])
        result[-1].append(point)
    return result


def to_bytes(points, creator, receiver=None, name=None, now=None):
    """The GPX document for a track's points (see trip.Track), as UTF-8 bytes."""
    ET.register_namespace("", GPX_NS)
    ET.register_namespace("gpxtpx", TPX_NS)
    root = ET.Element(f"{{{GPX_NS}}}gpx", {"version": "1.1", "creator": creator,
                                             "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation": SCHEMA})
    metadata = _sub(root, "metadata")
    first = next((p["utc"] for p in points if p["utc"]), None)
    name = name or (f"Track {first[:10]}" if first else "Track")
    _sub(metadata, "name", name)
    _sub(metadata, "desc", f"Recorded by {creator}" + (f" from a u-blox {receiver} receiver" if receiver else ""))
    _sub(metadata, "time", (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    track = _sub(root, "trk")
    _sub(track, "name", name)
    for segment in segments(points):
        trkseg = _sub(track, "trkseg")
        for point in segment:
            trkpt = _sub(trkseg, "trkpt", lat=f"{point['lat']:.7f}", lon=f"{point['lon']:.7f}")
            if point.get("alt") is not None:
                _sub(trkpt, "ele", f"{point['alt']:.2f}")
            if point.get("utc"):
                _sub(trkpt, "time", point["utc"])
            if point.get("speed") is not None or point.get("course") is not None:
                extension = _sub(_sub(trkpt, "extensions"), "TrackPointExtension", ns=TPX_NS)
                if point.get("speed") is not None:
                    _sub(extension, "speed", f"{point['speed']:.2f}", ns=TPX_NS)
                if point.get("course") is not None:
                    _sub(extension, "course", f"{point['course'] % 360:.1f}", ns=TPX_NS)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
