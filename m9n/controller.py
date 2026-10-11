"""UI-independent session lifecycle: source, evidence files, polling and reports.

A front end drives one SessionController from its own loop. It calls
on_readable() when fileno() is readable and tick() about every 100 ms.
stop() and close() release the tty and close every evidence file, whatever
ended the session. No GTK or terminal code belongs here.
"""
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .demo import sample
from .serialize import json_line, safe
from .session import Session
from .hostinfo import choose_port
from .transport import SerialPort

LOG = logging.getLogger("m9n")
FORMATTER = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

SOURCES = ("device", "demo", "replay")
MODES = ("watch", "test")
FAULTS = ("no-fix", "rf", "corrupt", "frozen", "silent", "drive")  # "drive" is motion, not a fault.

COMPLETED = "completed"
USER_STOPPED = "user_stopped"
INTERRUPTED = "interrupted"
TRANSPORT_ERROR = "transport_error"
OUTPUT_ERROR = "output_error"


@dataclass
class SessionConfig:
    source: str = "device"
    mode: str = "watch"
    port: str | None = None
    baud: int = 9600
    passive: bool = False
    fault: str | None = None
    capture: Path | None = None
    duration: float = 0.0
    interval: float = 1.0
    stale_after: float = 5.0
    debug: bool = False
    raw_log: Path | None = None
    json_log: Path | None = None
    log_file: Path | None = None
    report: Path | None = None
    replay_chunk: int = 16384

    def outputs(self):
        return [p for p in (self.raw_log, self.json_log, self.log_file, self.report) if p is not None]


@dataclass
class SessionResult:
    reason: str
    mode: str
    source: str
    snapshot: dict
    report: dict | None = None
    transport_error: str | None = None
    received_bytes: bool = False
    output_errors: list = field(default_factory=list)

    @property
    def valid_frames(self):
        counters = self.snapshot["counters"]
        return counters.get("ubx_frames", 0) + counters.get("nmea_frames", 0)


def same_file(a, b):
    if a.resolve() == b.resolve():
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


OUTPUT_FIELDS = ("raw_log", "json_log", "log_file", "report")


def problems(config):
    """Yield (field, message) for everything that would stop the session starting."""
    if config.source not in SOURCES:
        yield "source", f"Unknown source: {config.source}"
        return
    if config.mode not in MODES:
        yield "mode", f"Unknown mode: {config.mode}"
        return
    for name in ("interval", "stale_after"):
        value = getattr(config, name)
        if not math.isfinite(value) or value <= 0:
            yield name, f"{name} must be a finite number greater than zero"
    if not math.isfinite(config.duration) or config.duration < 0:
        yield "duration", "duration must be a finite non-negative number"
    if config.mode == "test":
        if config.source == "replay":
            yield "mode", "A device test needs a live or demo source, not a replay"
        elif not config.duration:
            yield "duration", "test --duration must be greater than zero"
    elif config.report is not None:
        yield "report", "A test report is only written in test mode"
    if config.source == "replay" and config.capture is None:
        yield "capture", "Replay needs a capture file"
    if config.fault is not None and (config.source != "demo" or config.fault not in FAULTS):
        yield "fault", f"Fault {config.fault!r} is only available for the demo source"
    if config.raw_log is not None and config.source != "device":
        yield "raw_log", "Raw RX capture is only available for a USB device source"
    outputs = [(name, getattr(config, name)) for name in OUTPUT_FIELDS if getattr(config, name) is not None]
    for i, (name, path) in enumerate(outputs):
        if path.exists() and not path.is_file():
            yield name, f"Output must be a regular file, not a device or directory: {path}"
        for _, other in outputs[i + 1:]:
            if same_file(path, other):
                yield name, "Each output must have a distinct file path"
        if config.capture is not None and same_file(path, config.capture):
            yield name, "Output path must not overwrite the replay capture"
        if config.port and same_file(path, Path(config.port)):
            yield name, "Output path must not be the serial device"
        parent = path.parent
        if not parent.is_dir():
            yield name, f"Output directory does not exist: {parent}"
        elif not os.access(parent, os.W_OK | os.X_OK) and not os.access(path, os.W_OK):
            yield name, f"Output directory is not writable: {parent}"
    if config.raw_log is not None and config.raw_log.exists():
        yield "raw_log", f"Raw capture already exists and is never overwritten: {config.raw_log}"


def validate(config):
    """Reject a configuration before anything is opened or created."""
    for _field, message in problems(config):
        raise ValueError(message)


class SessionController:
    def __init__(self, config, clock=time.monotonic, on_snapshot=None, on_issue=None, on_finished=None,
                 on_stopping=None, on_message=None):
        self.config = config
        self.clock = clock
        self.on_stopping = on_stopping
        self.on_message = on_message
        self.on_snapshot = on_snapshot
        self.on_issue = on_issue
        self.on_finished = on_finished
        self.state = "idle"
        self.source = None
        self.session = None
        self.port = None
        self.result = None
        self.started = None
        self.transport_error = None
        self.output_errors = []
        self.received_bytes = False
        self.issues = set()
        self._capture = None
        self._capture_size = 0
        self._raw = None
        self._jsonl = None
        self._log_handler = None
        self._next_display = self._next_demo = None

    @property
    def running(self):
        return self.state == "running"

    def fileno(self):
        return self.port.fd if self.port else None

    def start(self):
        if self.state != "idle":
            raise RuntimeError("A SessionController runs exactly one session")
        config = self.config
        validate(config)
        try:
            # The source opens first: a missing or busy device creates no files.
            self._open_source()
            self._open_outputs()
        except BaseException:
            self._release()
            raise
        self.started = self.clock()
        self._next_display = self._next_demo = self.started
        self.session = Session(self.started, config.stale_after, config.passive or self.port is None, self.source)
        self.session.state.on_recent = self.on_message
        self.state = "running"
        if self.port:
            LOG.info("Opened %s; %s", self.source,
                     "passive mode" if config.passive else "diagnostic polls enabled; receiver configuration unchanged")

    def _open_source(self):
        config = self.config
        if config.source == "device":
            selected = choose_port(config.port)
            self.port = SerialPort(selected, config.baud)
            self.source = selected
        elif config.source == "replay":
            self._capture = config.capture.open("rb")
            self._capture_size = os.fstat(self._capture.fileno()).st_size
            self.source = "REPLAY (recorded data): " + str(config.capture)
        else:
            self.source = "SYNTHETIC DEMO"

    def _open_outputs(self):
        config = self.config
        LOG.setLevel(logging.DEBUG if config.debug else logging.INFO)
        if config.log_file:
            self._log_handler = logging.FileHandler(config.log_file, encoding="utf-8")
            self._log_handler.setFormatter(FORMATTER)
            LOG.addHandler(self._log_handler)
        if config.json_log:
            self._jsonl = config.json_log.open("a", encoding="utf-8")
        if config.raw_log:
            self._raw = config.raw_log.open("xb")

    def reset_trip(self):
        """Start trip distance and speed statistics again from the next epoch."""
        if self.session:
            self.session.state.trip.reset()

    def progress(self, now=None):
        """Fraction complete for replay or a timed session, otherwise None."""
        if self.config.source == "replay":
            if self._capture is None:
                return 1.0 if self.state == "finished" else None
            return self._capture.tell() / self._capture_size if self._capture_size else 1.0
        if not self.config.duration or self.started is None:
            return None
        if self.state == "finished":
            return 1.0
        now = self.clock() if now is None else now
        return min(1.0, (now - self.started) / self.config.duration)

    def on_readable(self, now=None):
        if not self.running or not self.port:
            return
        now = self.clock() if now is None else now
        try:
            data = self.port.read(0)
        except OSError as exc:
            self._transport_failed(exc, now)
            return
        self._ingest(data, now)

    def tick(self, now=None):
        if not self.running:
            return
        now = self.clock() if now is None else now
        config = self.config
        if config.source != "replay" and config.duration and now - self.started >= config.duration:
            self.stop(COMPLETED, now)
            return
        if self.port:
            try:
                self.port.flush()
                self.session.poll(self.port, now)
            except OSError as exc:
                self._transport_failed(exc, now)
                return
        elif config.source == "replay":
            data = self._capture.read(config.replay_chunk)
            if not data:
                # Flush any partial last frame without real-time waiting.
                self.session.consume(b"", now + self.session.framer.timeout)
                self._track_issues(now)
                self.stop(COMPLETED, now)
                return
            if not self._ingest(data, now):
                return
        elif now >= self._next_demo:
            epoch = 1000 if config.fault == "frozen" else 1000 + int((now - self.started) * 1000)
            self._next_demo = now + 1
            if not self._ingest(b"" if config.fault == "silent" else sample(epoch, config.fault), now):
                return
        self._track_issues(now)
        if config.source != "replay" and now >= self._next_display:
            self._next_display = now + config.interval
            snapshot = self.session.snapshot(now)
            self._write_jsonl(snapshot)
            if self.on_snapshot and self.running:
                self.on_snapshot(snapshot)

    def _ingest(self, data, now):
        if data and self._raw:
            try:
                self._raw.write(data)
                self._raw.flush()
            except OSError as exc:
                self._output_failed("raw capture", exc, now)
        self.received_bytes |= bool(data)
        self.session.consume(data, now)
        return self.running

    def _track_issues(self, now):
        issues = set(self.session.state.issues(now, self.session.framer))
        for issue in sorted(issues - self.issues):
            LOG.warning("%s", issue)
            if self.on_issue:
                self.on_issue("added", issue)
        for issue in sorted(self.issues - issues):
            LOG.info("Resolved: %s", issue)
            if self.on_issue:
                self.on_issue("resolved", issue)
        self.issues = issues

    def _write_jsonl(self, value):
        if self._jsonl:
            try:
                self._jsonl.write(json_line(value) + "\n")
                self._jsonl.flush()
            except OSError as exc:
                self._output_failed("JSONL log", exc)

    def _transport_failed(self, exc, now):
        self.transport_error = str(exc)
        LOG.error("USB transport failed: %s", safe(exc))
        self.stop(TRANSPORT_ERROR, now)

    def _output_failed(self, what, exc, now=None):
        message = f"{what} write failed: {exc}"
        self.output_errors.append(message)
        LOG.error("%s", safe(message))
        attr = "_raw" if what == "raw capture" else "_jsonl"
        handle = getattr(self, attr)
        setattr(self, attr, None)
        try:
            handle.close()
        except OSError:
            pass
        if self.running:
            self.stop(OUTPUT_ERROR, now)

    def stop(self, reason=USER_STOPPED, now=None):
        """End the session: release the tty first, then finalise the evidence."""
        if self.state != "running":
            return self.result
        self.state = "stopping"
        if self.on_stopping:
            # Lets an event loop drop its fd watch before the descriptor is closed.
            self.on_stopping()
        now = self.clock() if now is None else now
        if reason in (USER_STOPPED, INTERRUPTED):
            LOG.info("Stopped by user")
        if self.port:
            try:
                self.port.close()
            except OSError as exc:
                LOG.error("Closing %s failed: %s", self.source, safe(exc))
        report = None
        if self.config.mode == "test":
            report = self.session.report(now)
            if self.transport_error:
                report["checks"].append({"name": "usb_transport", "status": "FAIL", "detail": self.transport_error})
                report["status"] = "FAIL"
                report["exit_code"] = 1
            if reason in (USER_STOPPED, INTERRUPTED):
                report["status"] = "INTERRUPTED"
                report["exit_code"] = 130
        snapshot = report["snapshot"] if report else self.session.snapshot(now)
        self.result = SessionResult(reason, self.config.mode, self.source, snapshot, report, self.transport_error,
                                    self.received_bytes, self.output_errors)
        # The result already exists in memory, so a failing disk cannot discard it.
        if report and self.config.report:
            try:
                self.config.report.write_text(json_line(report) + "\n", encoding="utf-8")
            except OSError as exc:
                self.output_errors.append(f"Report not written to {self.config.report}: {exc}")
                LOG.error("%s", safe(self.output_errors[-1]))
        self._write_jsonl(report or snapshot)
        self._release()
        self.state = "finished"
        if self.on_finished:
            self.on_finished(self.result)
        return self.result

    def _release(self):
        if self.port:
            try:
                self.port.close()
            except OSError:
                pass
            self.port = None
        for name in ("_raw", "_jsonl", "_capture"):
            handle = getattr(self, name)
            setattr(self, name, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError as exc:
                    self.output_errors.append(f"Closing {handle.name} failed: {exc}")
        if self._log_handler:
            LOG.removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    def close(self):
        """Idempotent: stop a running session as interrupted and release everything."""
        if self.running:
            self.stop(INTERRUPTED)
        self._release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

