"""Plain-text and ANSI live views. Receiver text is escaped for terminal safety."""
import shutil

from .serialize import json_line, safe  # noqa: F401  (re-exported)


def fmt(value, digits=2):
    return f"{value:.{digits}f}" if value is not None else "unknown"


def render(snapshot, interactive=False):
    nav = snapshot["navigation"] or {}
    identity = snapshot["identity"] or {}
    fix = "VALID" if nav.get("fix_ok") and nav.get("fresh") else "STALE" if nav and not nav.get("fresh") else "NO FIX"
    lines = [f"NEO-M9N USB monitor | {snapshot['source']} | {snapshot['host_utc']} | {snapshot['elapsed_s']:.1f}s",
             f"Fix: {fix} ({nav.get('fix_type', 'unknown')}) | used satellites: {nav.get('satellites_used', 'unknown')}",
             f"Position: {fmt(nav.get('latitude'), 7)}, {fmt(nav.get('longitude'), 7)} | MSL altitude: {fmt(nav.get('altitude_msl_m'))} m",
             f"Speed: {fmt(nav.get('speed_m_s'))} m/s | course: {fmt(nav.get('heading_deg'))} deg | hAcc: {fmt(nav.get('horizontal_accuracy_m'))} m",
             f"DOP: position={fmt(nav.get('pdop'))} horizontal={fmt(nav.get('hdop'))} vertical={fmt(nav.get('vdop'))}",
             f"GNSS UTC: {nav.get('utc') or 'unknown'} | source: {nav.get('source_message', 'unknown')} | age: {nav.get('age_s', 'unknown')}s",
             f"Firmware: {identity.get('software', 'unknown')} | HW: {identity.get('hardware', 'unknown')}",
             "Identity: " + ", ".join(identity.get("extensions", [])),
             f"RX bytes: {snapshot['counters'].get('bytes', 0)} | UBX: {snapshot['counters'].get('ubx_frames', 0)} | NMEA: {snapshot['counters'].get('nmea_frames', 0)} | checksum errors: {snapshot['counters'].get('checksum_errors', 0)}",
             f"Diagnostic ages: RF={snapshot['rf_age_s']}s, SAT={snapshot['satellites_age_s']}s, STATUS={snapshot['status_age_s']}s"]
    for rf in snapshot["rf"]:
        lines.append(f"RF {rf['block']}: antenna={rf['antenna_status']}/{rf['antenna_power']} | jamming={rf['jamming_state']} | CW={rf['cw_suppression']} | noise={rf['noise_per_ms']} | AGC={rf['agc_count']}")
    status = snapshot["receiver_status"]
    if status:
        lines.append(f"Receiver status: TTFF={status['ttff_s']}s | uptime={status['uptime_s']}s | spoof detector={status['spoof_state']} (indication only)")
    lines.extend("WARNING: " + issue for issue in snapshot["issues"])
    if snapshot["recent_messages"]:
        lines.append("Recent receiver/parser messages:")
        lines.extend(f"  {e['severity']} {e['name']}: {e['text']}" for e in snapshot["recent_messages"][-3:])
    if interactive:
        height = shutil.get_terminal_size((100, 30)).lines
        available = max(0, height - len(lines) - 3)
    else:
        available = 8
    sats = sorted(snapshot["satellites"], key=lambda s: (not s["used"], -s["cno_dbhz"]))
    if sats and available:
        lines.append("GNSS       SV  C/N0 dB-Hz  Elev  Azim  Used")
        for sat in sats[:available]:
            lines.append(f"{sat['gnss']:<10} {sat['sv']:>3} {sat['cno_dbhz']:>10} {sat['elevation_deg']:>5} {sat['azimuth_deg']:>5}  {'yes' if sat['used'] else 'no'}")
        if len(sats) > available:
            lines.append(f"... {len(sats) - available} more satellites in JSON output")
    width, height = shutil.get_terminal_size((100, 30))
    clean = [safe(line) for line in lines]
    if interactive:
        clean = [line[:max(1, width - 1)] for line in clean[:max(1, height - 1)]]
        return "\x1b[H\x1b[2J" + "\n".join(clean)
    return "\n".join(clean) + "\n"


def report_text(report):
    lines = [f"Device test: {report['status']} (exit {report['exit_code']})"]
    lines.extend(f"[{c['status']}] {c['name']}: {safe(c['detail'])}" for c in report["checks"])
    return "\n".join(lines)
