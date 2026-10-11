"""Poll scheduler and stream processing, independent of terminal output."""
import logging

from .protocol import Framer, decode, ubx
from .state import State

LOG = logging.getLogger("m9n")
POLLS = [("MON-VER", 10, 4, 30.0), ("NAV-PVT", 1, 7, 1.0),
         ("NAV-SAT", 1, 53, 2.0), ("NAV-STATUS", 1, 3, 2.0), ("MON-RF", 10, 56, 5.0)]


class Session:
    def __init__(self, started, stale_after=5.0, passive=False, source="USB"):
        self.framer = Framer()
        self.state = State(started, stale_after, passive, source)
        self.next_poll = {name: started for name, *_ in POLLS}
        self.unanswered = set()

    def consume(self, data, now):
        if data:
            LOG.debug("RX %d bytes: %s", len(data), data.hex(" "))
        for frame in self.framer.feed(data, now):
            try:
                event = decode(frame)
            except (ValueError, IndexError) as exc:
                self.state.decode_errors += 1
                event = {"protocol": "ERROR", "name": "decode_error", "text": str(exc)}
            self.state.ingest(event, now)
            if event["name"] in self.unanswered:
                self.unanswered.remove(event["name"])
                LOG.info("%s response recovered", event["name"])
            if event["protocol"] == "ERROR" or event["name"] == "INF-ERROR" or event.get("severity") == "ERROR":
                LOG.error("%s: %s", event["name"], event.get("text"))
            elif event["name"] in ("ACK-NAK", "INF-WARNING") or event.get("severity") == "WARNING":
                LOG.warning("%s: %s", event["name"], event.get("text", event.get("target")))
            else:
                LOG.debug("Decoded %s: %s", event["name"], event)

    def poll(self, port, now):
        if self.state.passive:
            return
        for name, deadline in list(self.state.pending.items()):
            if now >= deadline:
                self.state.pending.pop(name)
                self.state.poll_timeouts[name] += 1
                if name not in self.unanswered:
                    LOG.warning("%s poll unanswered; UBX may be disabled, unsupported or delayed", name)
                    self.unanswered.add(name)
        for name, cls, ident, interval in POLLS:
            if name in self.state.rejected or name in self.state.pending or now < self.next_poll[name]:
                continue
            packet = ubx(cls, ident)
            port.write(packet)
            LOG.debug("TX poll %s: %s", name, packet.hex(" "))
            self.state.pending[name] = now + 1.5
            self.next_poll[name] = now + interval

    def snapshot(self, now):
        return self.state.snapshot(now, self.framer)

    def report(self, now):
        return self.state.report(now, self.framer)
