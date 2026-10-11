"""Text escaping and JSON serialization shared by every front end."""
import json


def safe(value):
    return "".join(c if c.isprintable() else "?" for c in str(value))


def json_line(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)
