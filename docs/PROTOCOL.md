# Protocol and output contract

The implementation uses the u-blox M9 SPG 4.04 / protocol 32.01 interface description for the selected messages; shared diagnostic layouts are also commonly present on earlier M9 firmware, but actual availability must be tested. MON-VER extensions retain the observed protocol version. No firmware version is hardcoded as a device requirement.

| Message | Fields exposed |
| --- | --- |
| MON-VER | Software, hardware and model/protocol extensions |
| NAV-PVT | iTOW, valid UTC, fix validity, lat/lon, MSL/ellipsoid height, accuracy, speed/course, satellites used, pDOP |
| NAV-SAT v1 | Constellation, SV, C/N0, elevation, azimuth, quality, health and use flag |
| NAV-STATUS | Navigation status, spoof detector state, TTFF and receiver uptime |
| MON-RF v0 | Per-block antenna status/power, POST word, noise, AGC and CW suppression |
| ACK-ACK / ACK-NAK | Named acknowledged/rejected target |
| INF messages | Received error/warning/notice/test/debug text |
| NMEA GGA / RMC | Position/fix, time of day, satellites/altitude/DOP or speed/course/date |
| NMEA GSA / GSV / TXT | DOP, satellite-in-view fragment metadata or receiver notices |

GSV fragments are preserved as metadata and counted; the detailed satellite table comes from NAV-SAT. NMEA-only sessions therefore have reduced diagnostics. Unknown sentences/messages still count as valid frames if their checksums validate.

## Units and interpretation

Application coordinates are decimal degrees, distances are metres, speed is metres/second and heading/course is degrees. C/N0 is dB-Hz. The receiver's hAcc/vAcc are estimates, not independent measurements. Heading is course over ground, not a magnetometer compass. GNSS TTFF is the receiver-reported time since its startup/reset acquisition, not the duration of this application session.

PVT UTC is rendered to whole seconds if validDate and validTime are set, with separate `nano`, `time_accuracy_ns` and `utc_fully_resolved` fields. NMEA UTC stays in its original `hhmmss.sss` time-of-day representation, and RMC date stays `ddmmyy`; the tool does not fabricate a full UTC date from GGA. Leap-second values are retained rather than converted through a host datetime that might reject them.

RF antenna status has explicit UNKNOWN/INIT possibilities. Antenna power OFF is not automatically an error for a passive-antenna system. CW suppression/noise/AGC raw values are diagnostic observations without arbitrary alarm thresholds. Only the firmware's RF/spoof flags generate those indications. No spoof indication is not a guarantee of trustworthy positioning.

## JSON schema version 1

Every snapshot has `schema_version`, `kind: snapshot`, host UTC, elapsed monotonic seconds and a source label. Important fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `identity` | object/null | Latest MON-VER |
| `navigation` | object/null | Latest preferred position, validity/freshness, age and epoch-progress age |
| `navigation.fix_ok` | boolean | Receiver reported a usable position fix |
| `navigation.fresh` | boolean | Both reception and available epoch progress within threshold |
| `satellites` | array | Latest NAV-SAT rows; see `satellites_age_s` |
| `rf` | array | Latest MON-RF blocks; see `rf_age_s` |
| `receiver_status` | object/null | Latest NAV-STATUS; see `status_age_s` |
| `counters` | object | Session bytes, valid frames, checksum/truncation/oversize/discard counters |
| `message_counts` | object | Per-decoded-message counts |
| `message_ages_s` | object | Time since each decoded message was received |
| `poll_timeouts` | object | Cumulative unanswered poll deadlines per target |
| `rejected_polls` | array | Targets NAKed and not subsequently observed |
| `issues` | array of strings | Human-readable current issues and session integrity warnings |
| `recent_messages` | array | Up to 20 receiver/parser notices, including severity and host UTC |
| `trip` | object | Since the session started or the trip was reset: `distance_m`, `moving_s`, `average_speed_m_s` (distance over moving time; null before any movement), `max_speed_m_s` (null without a speed), `epochs` (distinct valid fixes counted). Added in 2.0.1. |

Counter keys are sparse: absent means zero, not unsupported. Missing position/diagnostic values are null or absent, never fabricated zeroes. Use `fix_ok && fresh` before treating coordinates as current. Diagnostic arrays can retain old values, so check their ages before using them. Frozen PVT retains old coordinates with `fresh: false`.

Reports have `kind: test_report`, `status`, `exit_code`, an array of named `checks` (status/detail), and a final `snapshot`. `exit_code` keeps the 1.x meaning (0 PASS, 2 WARN, 1 FAIL, 130 INTERRUPTED) for tools that already read it. The report file holds one report. The JSONL log holds a snapshot per refresh interval and then the final report (device tests) or final snapshot (watch and demo); a replay writes only its final snapshot. Applications should check both record kind and schema version. The Host page's *Save as JSON* writes the host diagnostics document (the 1.x `doctor --json` format).

Machine logs include location and receiver identifiers. Receiver debug text is escaped in JSON. Raw RX files contain no wrapper/header and can be inspected by another UBX/NMEA parser; they exclude transmitted polls and contain no timing metadata.
