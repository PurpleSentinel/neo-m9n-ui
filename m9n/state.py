"""Freshness-aware state and evidence-based health assessment."""
from collections import Counter, deque
from datetime import datetime, timezone


RF_FRESH_S = 15  # MON-RF is polled every 5 s.
JAMMING = {0: "unknown or monitor disabled", 1: "no interference", 2: "interference warning", 3: "critical interference"}
SPOOFING = {0: "unknown or detector disabled", 1: "no spoofing indicated", 2: "spoofing indicated",
            3: "multiple spoofing indications"}


def antenna_alarm(block):
    return block["antenna_status"] in ("SHORT", "OPEN")


def jamming_alarm(block):
    return block["jamming_state"] >= 2


def spoofing_alarm(spoof_state):
    return spoof_state >= 2


def rf_monitor_known(blocks):
    """The jamming monitor reported a state (0 means unknown or disabled) for every block."""
    return bool(blocks) and all(b["jamming_state"] != 0 for b in blocks)


def host_utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class State:
    def __init__(self, started, stale_after=5.0, passive=False, source="USB"):
        self.started = started
        self.stale_after = stale_after
        self.passive = passive
        self.source = source
        self.messages = {}
        self.seen = {}
        self.counts = Counter()
        self.recent = deque(maxlen=20)
        self.epochs = {}
        self.epoch_changed = {}
        self.poll_timeouts = Counter()
        self.pending = {}
        self.rejected = set()
        self.receiver_errors = 0
        self.decode_errors = 0

    def ingest(self, event, now):
        name = event["name"]
        self.counts[name] += 1
        self.messages[name] = event
        self.seen[name] = now
        self.pending.pop(name, None)
        if name in self.rejected:
            self.rejected.remove(name)
        epoch = event.get("itow_ms", event.get("epoch"))
        if epoch is not None:
            if name not in self.epochs or self.epochs[name] != epoch:
                self.epoch_changed[name] = now
            self.epochs[name] = epoch
        if name == "ACK-NAK":
            target = event["target"]
            self.pending.pop(target, None)
            self.rejected.add(target)
        severity = "NOTICE"
        if event["protocol"] == "ERROR" or name == "INF-ERROR":
            severity = "ERROR"
        elif name in ("INF-WARNING", "ACK-NAK"):
            severity = "WARNING"
        severity = event.get("severity", severity)
        if severity == "ERROR" and event["protocol"] != "ERROR":
            self.receiver_errors += 1
        if "text" in event or name == "ACK-NAK":
            self.recent.append({"host_utc": host_utc(), "severity": severity,
                                "name": name, "text": event.get("text", "Rejected poll: " + event.get("target", ""))})

    def fresh(self, name, now, limit=None):
        return name in self.seen and now - self.seen[name] <= (self.stale_after if limit is None else limit)

    def navigation(self, now):
        if self.fresh("NAV-PVT", now):
            name = "NAV-PVT"
        else:
            positions = [n for n, e in self.messages.items() if e.get("kind") == "position"]
            if positions:
                name = max(positions, key=self.seen.get)
            elif "NAV-PVT" in self.messages:
                name = "NAV-PVT"
            else:
                return None
        result = dict(self.messages[name])
        if result.get("kind") == "position":
            # Complementary NMEA messages may arrive in either order. Merge only
            # the same UTC epoch, a recent sample, and the same fix-validity state.
            for other_name, other in self.messages.items():
                if (other_name != name and other.get("kind") == "position"
                        and result.get("epoch") is not None
                        and other.get("epoch") == result.get("epoch")
                        and other.get("fix_ok") == result.get("fix_ok")
                        and self.fresh(other_name, now)):
                    for field in ("satellites_used", "hdop", "altitude_msl_m", "speed_m_s", "heading_deg", "date"):
                        if field not in result and field in other:
                            result[field] = other[field]
            for other_name, other in self.messages.items():
                if other_name.endswith("GSA") and self.fresh(other_name, now):
                    for field in ("pdop", "hdop", "vdop"):
                        if field not in result and field in other:
                            result[field] = other[field]
        age = now - self.seen[name]
        progress_age = now - self.epoch_changed.get(name, self.seen[name])
        result.update(source_message=name, age_s=round(age, 3),
                      epoch_progress_age_s=round(progress_age, 3),
                      fresh=age <= self.stale_after and progress_age <= self.stale_after)
        if result.get("latitude") is None or result.get("longitude") is None:
            result["fix_ok"] = False
        return result

    def snapshot(self, now, framer):
        nav = self.navigation(now)
        sat = self.messages.get("NAV-SAT")
        rf = self.messages.get("MON-RF")
        status = self.messages.get("NAV-STATUS")
        return {"schema_version": 1, "kind": "snapshot", "host_utc": host_utc(),
                "source": self.source, "elapsed_s": round(now - self.started, 3),
                "identity": self.messages.get("MON-VER"), "navigation": nav,
                "satellites": sat.get("satellites", []) if sat else [],
                "satellites_age_s": round(now - self.seen["NAV-SAT"], 3) if sat else None,
                "rf": rf.get("blocks", []) if rf else [],
                "rf_age_s": round(now - self.seen["MON-RF"], 3) if rf else None,
                "receiver_status": status,
                "status_age_s": round(now - self.seen["NAV-STATUS"], 3) if status else None,
                "counters": dict(framer.counts), "message_counts": dict(self.counts),
                "decode_errors": self.decode_errors, "poll_timeouts": dict(self.poll_timeouts),
                "rejected_polls": sorted(self.rejected),
                "message_ages_s": {k: round(now - v, 3) for k, v in self.seen.items()},
                "issues": self.issues(now, framer), "recent_messages": list(self.recent)}

    def issues(self, now, framer):
        issues = []
        elapsed = now - self.started
        if framer.counts["bytes"] == 0 and elapsed > 3:
            issues.append("No USB serial data: check power/data cable, port, competing services and UBX/NMEA output settings")
        if framer.counts["bytes"] and not (framer.counts["ubx_frames"] + framer.counts["nmea_frames"]):
            issues.append("Bytes arrive but no valid UBX/NMEA frames: inspect raw capture, protocol settings or UART-adapter baud")
        nav = self.navigation(now)
        if nav is None:
            issues.append("No position messages received yet")
        elif not nav["fresh"]:
            issues.append("Navigation is stale or its epoch stopped advancing; displayed values are last received")
        elif not nav["fix_ok"]:
            issues.append("Receiver communicates but has no valid position fix; check antenna and open-sky reception")
        if framer.counts["checksum_errors"]:
            issues.append("Corrupt/checksum-invalid frames observed; inspect cable, power and competing readers")
        if self.decode_errors:
            issues.append("Malformed or unsupported-version payloads observed; inspect debug log and firmware version")
        if elapsed > 3 and not self.fresh("MON-VER", now, 90) and not self.passive:
            issues.append("Firmware identity unavailable: UBX input/output may be disabled or MON-VER is unanswered")
        if self.rejected:
            issues.append("Receiver rejected polls: " + ", ".join(sorted(self.rejected)))
        if self.fresh("MON-RF", now, RF_FRESH_S):
            for block in self.messages["MON-RF"]["blocks"]:
                if antenna_alarm(block):
                    issues.append("Antenna supervisor reports " + block["antenna_status"] + "; interpret against this board's antenna/supervisor wiring")
                if jamming_alarm(block):
                    issues.append("Receiver reports RF interference (state " + str(block["jamming_state"]) + ")")
        if self.fresh("NAV-STATUS", now) and spoofing_alarm(self.messages["NAV-STATUS"]["spoof_state"]):
            issues.append("Receiver reports spoofing indication; verify location with an independent source")
        return issues

    def report(self, now, framer):
        checks = []

        def add(name, status, detail):
            checks.append({"name": name, "status": status, "detail": detail})

        count = framer.counts["ubx_frames"] + framer.counts["nmea_frames"]
        add("communication", "PASS" if count else "FAIL", f"{framer.counts['bytes']} bytes, {count} valid frames")
        identity = self.messages.get("MON-VER")
        extensions = identity.get("extensions", []) if identity else []
        model = next((e.split("=", 1)[1] for e in extensions if e.startswith("MOD=")), None)
        add("device_identity", "PASS" if model == "NEO-M9N" else "FAIL" if model else "WARN",
            f"Receiver model: {model}" if model else "NEO-M9N identity not confirmed; MON-VER missing or MOD field absent")
        nav = self.navigation(now)
        add("navigation", "PASS" if nav and nav["fresh"] else "FAIL",
            f"{nav['source_message']}; age {nav['age_s']}s; epoch progress age {nav['epoch_progress_age_s']}s" if nav else "No navigation data")
        add("position_fix", "PASS" if nav and nav["fresh"] and nav["fix_ok"] else "WARN",
            nav["fix_type"] if nav else "No fix evidence; test outdoors before concluding RF hardware is faulty")
        malformed = framer.counts["checksum_errors"] + framer.counts["truncated_frames"] + framer.counts["oversize_frame"] + self.decode_errors
        add("protocol_integrity", "WARN" if malformed else "PASS", f"{malformed} corrupt, truncated, oversized or undecodable frames")
        for name, limit in (("NAV-SAT", self.stale_after), ("MON-RF", RF_FRESH_S), ("NAV-STATUS", self.stale_after)):
            add(name, "PASS" if self.fresh(name, now, limit) else "WARN",
                "Recent response received" if self.fresh(name, now, limit) else "Unavailable/stale; not evidence of hardware failure by itself")
        rf_issues = [i for i in self.issues(now, framer) if "Antenna supervisor" in i or "RF interference" in i or "spoofing indication" in i]
        rf_blocks = self.messages.get("MON-RF", {}).get("blocks", [])
        rf_known = self.fresh("MON-RF", now, RF_FRESH_S) and rf_monitor_known(rf_blocks)
        add("rf_health", "WARN" if rf_issues or not rf_known else "PASS",
            "; ".join(rf_issues) or ("No RF alarm in recent MON-RF" if rf_known else "RF/interference health unknown or monitor unavailable"))
        add("receiver_errors", "FAIL" if self.receiver_errors else "PASS", f"{self.receiver_errors} receiver error messages during this session")
        status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "WARN" if any(c["status"] == "WARN" for c in checks) else "PASS"
        return {"schema_version": 1, "kind": "test_report", "host_utc": host_utc(), "source": self.source,
                "status": status, "exit_code": {"PASS": 0, "WARN": 2, "FAIL": 1}[status],
                "checks": checks, "snapshot": self.snapshot(now, framer)}
