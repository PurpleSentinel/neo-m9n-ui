"""Persisted desktop session choices, converted to a SessionConfig on demand.

GTK-free so the setup rules can be unit-tested. Evidence files go to an output
folder with timestamped names unless the user pinned an explicit file, so a
raw capture (never overwritten) does not collide with an earlier session.
"""
import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .controller import FAULTS, OUTPUT_FIELDS, SessionConfig, problems
from .coords import FORMATS as COORDINATE_FORMATS

SOURCES = ("device", "demo", "replay")
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
APPEARANCES = ("system", "light", "dark", "sun")
OUTPUT_SUFFIX = {"raw_log": ".ubx", "json_log": ".jsonl", "log_file": ".log", "report": "-report.json"}
OUTPUT_LABEL = {"raw_log": "Raw RX capture", "json_log": "JSONL snapshot log",
                "log_file": "Application log", "report": "Test report"}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "neo-m9n" / "settings.json"


@dataclass
class SessionSettings:
    source: str = "device"
    port: str = ""  # Empty selects the single u-blox receiver automatically.
    baud: int = 9600
    passive: bool = False
    fault: str = ""
    capture: str = ""
    mode: str = "watch"
    watch_duration: float = 0.0  # 0 runs until stopped.
    test_duration: float = 60.0
    interval: float = 1.0
    stale_after: float = 5.0
    debug: bool = False
    output_dir: str = ""
    raw_log: bool = False
    json_log: bool = False
    log_file: bool = False
    report: bool = True
    pinned: dict = field(default_factory=dict)  # Output kind -> explicit file path.
    units: str = "metric"  # Vehicle page: "metric" or "imperial". Display only.
    map_mode: str = "off"  # Map page: "off" (grid, no network) or "online" (OpenStreetMap, after consent).
    map_consented: bool = False  # The user accepted the online map privacy notice.
    map_cache_mb: int = 200  # Online tile cache limit.
    map_offline_file: str = ""  # Offline map file (MBTiles) for the "offline" map mode.
    appearance: str = "system"  # "system", "light", "dark" or "sun" (dark after civil dusk at the receiver).
    coordinate_format: str = "decimal"  # How positions are shown (see coords.FORMATS). Display only.

    @property
    def effective_mode(self):
        return "watch" if self.source == "replay" else self.mode

    def applies(self, kind):
        """Whether an output can be used with the current source and mode."""
        if kind == "raw_log":
            return self.source == "device"
        if kind == "report":
            return self.effective_mode == "test"
        return True

    def wants(self, kind):
        return self.applies(kind) and getattr(self, kind)

    def output_path(self, kind, stamp):
        if self.pinned.get(kind):
            return Path(self.pinned[kind])
        path = Path(self.output_dir) / f"m9n-{stamp}{OUTPUT_SUFFIX[kind]}"
        counter = 2
        while kind == "raw_log" and path.exists():
            path = Path(self.output_dir) / f"m9n-{stamp}-{counter}{OUTPUT_SUFFIX[kind]}"
            counter += 1
        return path

    def to_config(self, now=None):
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        mode = self.effective_mode
        outputs = {kind: self.output_path(kind, stamp) if self.wants(kind) else None for kind in OUTPUT_FIELDS}
        return SessionConfig(
            source=self.source, mode=mode,
            port=(self.port or None) if self.source == "device" else None,
            baud=self.baud, passive=self.passive and self.source == "device",
            fault=(self.fault or None) if self.source == "demo" else None,
            capture=Path(self.capture) if self.source == "replay" and self.capture else None,
            duration=self.test_duration if mode == "test" else (0.0 if self.source == "replay" else self.watch_duration),
            interval=self.interval, stale_after=self.stale_after, debug=self.debug, **outputs)

    def auto_named(self, kind):
        return self.wants(kind) and not self.pinned.get(kind)

    def problems(self, now=None):
        """Map each setting to its first problem. Auto-named outputs report against output_dir."""
        found = {}
        if self.source not in SOURCES:
            return {"source": f"Unknown source: {self.source}"}
        if self.fault and self.fault not in FAULTS:
            found["fault"] = f"Unknown fault: {self.fault}"
        if any(self.auto_named(kind) for kind in OUTPUT_FIELDS) and not self.output_dir:
            found["output_dir"] = "Choose a folder for evidence files"
        config = self.to_config(now)
        for name, message in problems(config):
            if name in OUTPUT_FIELDS and self.auto_named(name):
                if message.startswith("Output directory does not exist"):
                    continue  # Created on start; see prepare_outputs().
                name = "output_dir"
            elif name == "duration":
                name = "test_duration" if config.mode == "test" else "watch_duration"
            found.setdefault(name, message)
        return found

    def prepare_outputs(self):
        """Create the output folder if a session needs it; returns the folder or None."""
        if self.output_dir and any(self.auto_named(kind) for kind in OUTPUT_FIELDS):
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            return Path(self.output_dir)
        return None

    @classmethod
    def load(cls, path=None):
        """Read saved settings; unknown keys and wrongly typed values fall back to defaults."""
        path = path or config_path()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for item in fields(cls):
            value = data.get(item.name)
            default = getattr(settings, item.name)
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, float):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = float(value) if ok else value
            elif isinstance(default, dict):
                ok = isinstance(value, dict) and all(k in OUTPUT_FIELDS and isinstance(v, str) for k, v in value.items())
            else:
                ok = isinstance(value, type(default)) and not isinstance(value, bool)
            if ok:
                setattr(settings, item.name, value)
        if settings.baud not in BAUD_RATES:
            settings.baud = cls.baud
        if settings.source not in SOURCES:
            settings.source = cls.source
        if settings.mode not in ("watch", "test"):
            settings.mode = cls.mode
        if settings.units not in ("metric", "imperial"):
            settings.units = cls.units
        if settings.appearance not in APPEARANCES:
            settings.appearance = cls.appearance
        if settings.coordinate_format not in COORDINATE_FORMATS:
            settings.coordinate_format = cls.coordinate_format
        if (settings.map_mode not in ("off", "online", "offline")
                or (settings.map_mode == "online" and not settings.map_consented)
                or (settings.map_mode == "offline" and not settings.map_offline_file)):
            settings.map_mode = cls.map_mode  # Never online without recorded consent.
        settings.map_cache_mb = min(5000, max(20, settings.map_cache_mb))
        return settings

    def save(self, path=None):
        path = Path(path or config_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
