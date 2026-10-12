"""GUI scenarios executed in a subprocess by test_gui.py. Prints `RESULT <json>`."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from m9n.controller import SessionConfig  # noqa: E402
from m9n.gui.app import Application  # noqa: E402

SCENARIO = sys.argv[1]
WIDTH = int(os.environ.get("M9N_PROBE_WIDTH", "1000"))
result = {"activations": 0}
app = Application()


def later(ms, func):
    GLib.timeout_add(ms, lambda: bool(func()) and False)


def wait_for(predicate, then, timeout_ms=3000):
    """Call then(bool) once predicate holds or the deadline passes; layout is asynchronous."""
    deadline = GLib.get_monotonic_time() + timeout_ms * 1000

    def poll():
        ok = predicate()
        if ok or GLib.get_monotonic_time() > deadline:
            then(ok)
            return False
        return True
    GLib.timeout_add(50, poll)


def page_names(win):
    pages = win.stack.get_pages()
    return [pages.get_item(i).get_name() for i in range(pages.get_n_items())]


def dialog_name(win, action):
    app.activate_action(action, None)
    dialog = win.get_visible_dialog()
    name = type(dialog).__name__ if dialog else None
    if dialog:
        dialog.force_close()
    return name


def shell(win):
    result["pages"] = page_names(win)
    # Messages, Map and Host always show content: none needs a session.
    result["empty_states"] = all(isinstance(w.stack.get_visible_child(), Adw.StatusPage)
                                 for key, w in win.pages.items() if key not in ("messages", "map", "host"))
    result["toggle_enabled"] = win.lookup_action("toggle-session").get_enabled()
    result["accels"] = {a: app.get_accels_for_action(a) for a in ("app.quit", "app.shortcuts", "win.toggle-session")}
    from gi.repository import Gdk
    from m9n.gui.app import APP_ID
    result["app_icon"] = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(APP_ID)
    result["about"] = dialog_name(win, "about")
    result["shortcuts"] = dialog_name(win, "shortcuts")
    win.toast("probe")
    win.set_banner("SYNTHETIC DEMO")
    result["banner_shown"] = win.banner.get_revealed()
    win.set_banner(None)
    result["banner_hidden"] = not win.banner.get_revealed()
    win.show_page("host")
    result["visible_page"] = win.stack.get_visible_child_name()

    def layout():
        result["bottom_switcher"] = win.switcher_bar.get_reveal()
        result["header_switcher"] = win.switcher.get_visible()
        result["sidebar_collapsed"] = win.split.get_collapsed()
        app.activate_action("quit", None)
    later(400, layout)


def unique(win):
    second = subprocess.Popen([sys.executable, "-m", "m9n"], cwd=Path(__file__).resolve().parents[1])

    def wait():
        if second.poll() is None:
            return True
        result["second_exit"] = second.returncode
        result["windows"] = len(app.get_windows())
        app.activate_action("quit", None)
        return False
    GLib.timeout_add(100, wait)


def tty_open(port):
    """True while any descriptor of this process still refers to the tty."""
    target = os.path.realpath(port)
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}") == target:
                return True
        except OSError:
            pass
    return False


def answer_close_dialog(win, response):
    dialog = win.get_visible_dialog()
    result["close_blocked"] = bool(dialog) and win.runner.running
    result["dialog_heading"] = dialog.get_heading() if dialog else None
    if dialog:
        # As on a button click: the dialog closes, then reports its response.
        dialog.force_close()
        dialog.emit("response", response)


def raise_error():
    raise RuntimeError("probe failure")


def lifecycle(win):
    port, out, trigger = os.environ["M9N_PORT"], Path(os.environ["M9N_OUT"]), os.environ["M9N_TRIGGER"]
    mode = os.environ.get("M9N_MODE", "watch")
    duration = float(os.environ.get("M9N_DURATION", "60" if mode == "test" else "0"))
    config = SessionConfig(source="device", mode=mode, port=port, duration=duration,
                           raw_log=out / "rx.ubx", json_log=out / "log.jsonl",
                           report=out / "report.json" if mode == "test" else None)

    def finished(_runner, outcome):
        result.update(reason=outcome.reason, status=outcome.report and outcome.report["status"],
                      tty_open_at_finish=tty_open(port), button=win.session_button.get_label(),
                      banner=win.banner.get_title() if win.banner.get_revealed() else None)
        if trigger in ("stop", "unplug", "complete", "cancel-close"):
            later(200, app.quit)
    win.runner.connect("finished", finished)
    alert = win.alert
    win.alert = lambda title, message: result.update(alert=[title, message]) or alert(title, message)  # Diagnosable.
    result["started"] = win.start_session(config) and win.runner.running
    result["running_button"] = win.session_button.get_label()
    result["inhibit"] = win._inhibit_cookie

    def act():
        if trigger == "stop":
            win.activate_action("win.toggle-session", None)
        elif trigger in ("close", "quit", "cancel-close"):
            win.close() if trigger != "quit" else app.activate_action("quit", None)
            answer_close_dialog(win, "cancel" if trigger == "cancel-close" else "stop")
            if trigger == "cancel-close":
                result["still_running_after_cancel"] = win.runner.running
                win.activate_action("win.toggle-session", None)
        elif trigger in ("sigint", "sigterm", "sighup"):
            os.kill(os.getpid(), getattr(signal, trigger.upper()))
        elif trigger == "exception":
            GLib.idle_add(raise_error)
            later(500, lambda: (result.update(error_dialog=type(win.get_visible_dialog()).__name__), app.quit()))
    if trigger not in ("unplug", "complete"):
        later(800, act)


def setup(win):
    panel = win.setup
    visible = lambda row: row.get_visible()  # noqa: E731
    result["demo_rows"] = {"device": visible(panel.device_row), "fault": visible(panel.fault_row),
                           "raw": visible(panel.output_rows["raw_log"]), "report": visible(panel.output_rows["report"]),
                           "receiver": visible(panel.receiver_group)}
    result["valid_demo"] = win.toggle_action.get_enabled()
    panel.source_row.set_selected(2)  # Replay without a capture file.
    result["replay_invalid"] = {"enabled": win.toggle_action.get_enabled(),
                                "capture_error": panel.capture_row.has_css_class("error"),
                                "message": panel.problem_label.get_label(), "mode_visible": visible(panel.mode_row)}
    panel.source_row.set_selected(1)
    panel.interval_row.set_value(.5)
    result["valid_again"] = win.toggle_action.get_enabled()
    win.activate_action("win.toggle-sidebar", None)
    result["sidebar_hidden"] = not win.split.get_show_sidebar()
    win.activate_action("win.toggle-sidebar", None)

    def finished(_runner, outcome):
        result.update(reason=outcome.reason, status=outcome.report["status"],
                      sensitive_after=panel.get_sensitive())
        later(700, app.quit)  # Past the save delay, so settings reach disk.
    win.runner.connect("finished", finished)
    win.activate_action("win.toggle-session", None)
    result["running"] = win.runner.running
    result["sensitive_while_running"] = panel.get_sensitive()
    result["config"] = {"mode": win.runner.config.mode, "fault": win.runner.config.fault,
                        "interval": win.runner.config.interval}


def dashboard(win):
    page = win.pages["dashboard"]
    result["empty_before"] = page.stack.get_visible_child_name()
    fault = os.environ.get("M9N_FAULT") or None
    win.start_session(SessionConfig(source="demo", fault=fault, stale_after=float(os.environ.get("M9N_STALE", "5"))))
    toasts = []
    original = win.toast
    win.toast = lambda text, timeout=3: toasts.append(text) or original(text, timeout)

    def inspect():
        result["visible"] = page.stack.get_visible_child_name()
        result["fix_title"] = page.fix_title.get_label()
        result["fix_detail"] = page.fix_detail.get_label()
        result["values"] = {key: row.value.get_label() for key, row in page.rows.items()}
        result["issues"] = [row.get_title() for row in page.issue_rows]
        result["toasts"] = list(toasts)
        page.copy_coordinates(None)
        win.get_clipboard().read_text_async(None, clipboard)

    def clipboard(board, outcome):
        try:
            result["clipboard"] = board.read_text_finish(outcome)
        except GLib.Error:
            result["clipboard"] = None  # Nothing was copied.
        snapshot = json.loads(json.dumps(page.snapshot))
        snapshot["identity"]["software"] = "EVIL\x1b[2J\x07FW"
        snapshot["issues"] = ["Bell\x07 issue"]
        page.update(snapshot)
        result["sanitised"] = [page.rows["software"].value.get_label(), page.issue_rows[0].get_title()]
        win.runner.stop()
        result["after_stop_visible"] = page.stack.get_visible_child_name()
        later(100, app.quit)
    later(int(os.environ.get("M9N_WAIT_MS", "1300")), inspect)


def satellites(win):
    page = win.pages["satellites"]
    capture = os.environ.get("M9N_CAPTURE")
    if capture:
        config = SessionConfig(source="replay", capture=Path(capture))
    else:
        config = SessionConfig(source="demo", fault=os.environ.get("M9N_FAULT") or None)

    def rows():
        model = page.selection
        return [(model.get_item(i).data["gnss"], model.get_item(i).data["sv"]) for i in range(model.get_n_items())]

    def first_refresh():
        result["inner"] = page.inner.get_visible_child_name()
        result["nodata_text"] = page.nodata.get_description()
        result["rows"] = rows()
        result["summary"] = page.summary.get_label()
        result["constellations"] = page.constellations.get_label()
        result["sky_points"] = len(page.sky.satellites)
        result["used"] = sum(item.data["used"] for item in page.items.values())
        if capture or not result["rows"]:
            win.runner.stop() if win.runner.running else None
            return later(100, app.quit)
        page.selection.set_selected(2)
        result["selected_before"] = rows()[2]
        later(2200, after_refreshes)

    def after_refreshes():
        position = page.selection.get_selected()
        result["selected_after"] = rows()[position] if position != Gtk.INVALID_LIST_POSITION else None
        result["refreshes"] = page.store.get_n_items()
        sv_column = page.view.get_columns().get_item(1)
        page.view.sort_by_column(sv_column, Gtk.SortType.ASCENDING)
        result["sorted_by_sv"] = rows()
        win.runner.stop()
        later(100, app.quit)
    win.start_session(config)
    later(1300, first_refresh)


def receiver(win):
    page = win.pages["receiver"]
    capture = os.environ.get("M9N_CAPTURE")
    config = (SessionConfig(source="replay", capture=Path(capture)) if capture else
              SessionConfig(source="demo", fault=os.environ.get("M9N_FAULT") or None))

    def inspect():
        alarms = lambda rows: sorted(k for k, r in rows.items() if getattr(r, "alarm_icon", None)  # noqa: E731
                                     and r.alarm_icon.get_visible())
        result["note"] = page.rf_note.get_title()
        result["rf"] = [{k: r.value.get_label() for k, r in rows.items()} for rows in page.rf_rows]
        result["rf_alarms"] = [alarms(rows) for rows in page.rf_rows]
        result["values"] = {k: r.value.get_label() for k, r in page.rows.items()}
        result["alarms"] = alarms(page.rows)
        if win.runner.running:
            win.runner.stop()
        later(100, app.quit)
    win.start_session(config)
    later(1300, inspect)


def messages(win):
    import logging
    page = win.pages["messages"]
    app_log, receiver_log = page.application, page.receiver
    result["visible_before_session"] = page.stack.get_visible_child_name()
    win.show_page("messages")
    page.views.set_visible_child_name("application")
    win.start_session(SessionConfig(source="demo", debug=True))

    def step1():
        win.runner.emit("message", {"host_utc": "2026-10-11T12:00:00.000+00:00", "severity": "ERROR",
                                    "name": "INF-ERROR", "text": "bad\x1b[31m\x07text"})
        receiver_log.flush()
        app_log.flush()
        result["receiver"] = [receiver_log.store.get_item(i).line() for i in range(receiver_log.store.get_n_items())]
        result["store"] = app_log.store.get_n_items()
        result["info_visible"] = app_log.filtered.get_n_items()
        app_log.level_menu.set_selected(3)
        result["debug_visible"] = app_log.filtered.get_n_items()
        app_log.search.set_text("NAV-PVT")
        app_log.on_search()
        lines = app_log.visible_text().splitlines()
        result["search_hits"] = len(lines)
        result["search_all_match"] = all("NAV-PVT" in line for line in lines)
        app_log.search.set_text("")
        app_log.on_search()
        win.runner.stop()
        try:
            raise ValueError("probe traceback")
        except ValueError:
            logging.getLogger("m9n").exception("Handled failure")
        app_log.flush()
        result["traceback_logged"] = any("probe traceback" in app_log.store.get_item(i).text
                                         for i in range(app_log.store.get_n_items()))
        for i in range(6000):
            app_log.add(app_log.store.get_item(0).__class__("t", logging.INFO, "Info", f"bulk {i}"))
        result["pending_before_flush"] = len(app_log.pending)
        app_log.flush()
        result["bounded"] = app_log.store.get_n_items()
        result["last_line"] = app_log.store.get_item(app_log.store.get_n_items() - 1).text
        adj = app_log.scrolled.get_vadjustment()
        wait_for(lambda: app_log.following and adj.get_upper() > adj.get_page_size()
                 and adj.get_value() >= adj.get_upper() - adj.get_page_size() - 4, step2)

    def step2(following):
        adj = app_log.scrolled.get_vadjustment()
        result["following"] = following
        adj.set_value(0)
        for i in range(50):
            app_log.add(app_log.store.get_item(0).__class__("t", logging.INFO, "Info", f"more {i}"))
        app_log.flush()
        later(300, step3)

    def step3():
        result["paused_at_top"] = app_log.scrolled.get_vadjustment().get_value() == 0 and not app_log.following
        app.quit()
    later(1500, step1)


def devicetest(win):
    page = win.pages["test"]
    out = Path(os.environ["M9N_OUT"])
    trigger = os.environ.get("M9N_TRIGGER", "complete")
    settings = win.setup.settings
    if os.environ.get("M9N_PORT"):
        settings.source, settings.port = "device", os.environ["M9N_PORT"]
    else:
        settings.source, settings.fault = "demo", os.environ.get("M9N_FAULT", "")
    settings.test_duration = float(os.environ.get("M9N_DURATION", "1.5"))
    settings.output_dir = str(out)
    settings.report = trigger in ("configured", "write-fails")
    settings.pinned = {}  # Saved settings are shared between probes in one test class.
    if trigger == "write-fails":
        (out / "gone").mkdir()
        settings.pinned = {"report": str(out / "gone" / "report.json")}
    result["empty_has_button"] = page.stack.get_visible_child_name() == "empty"
    win.activate_action("win.run-test", None)
    result["mode"] = win.runner.config.mode if win.runner.running else None
    result["run_test_enabled_while_running"] = win.run_test_action.get_enabled()
    if trigger == "write-fails":
        (out / "gone").rmdir()

    def running():
        result["running_view"] = page.views.get_visible_child_name()
        result["fraction"] = page.progress.get_fraction()
        result["remaining"] = page.remaining.get_label()
        if trigger == "cancel":
            win.activate_action("win.toggle-session", None)

    def finished(_runner, outcome):
        later(100, lambda: inspect(outcome))

    def inspect(outcome):
        result["visible_page"] = win.stack.get_visible_child_name()
        result["view"] = page.views.get_visible_child_name()
        result["title"] = page.status_title.get_label()
        result["checks"] = {row.get_title(): row.get_subtitle() for row in page.check_rows}
        result["report_subtitle"] = page.report_row.get_subtitle()
        result["save_label"] = page.save_button.get_label()
        result["report_status"] = outcome.report["status"]
        if trigger in ("complete", "write-fails"):
            target = out / "saved-report.json"
            result["saved"] = page.write_report(str(target))
            result["saved_matches"] = json.loads(target.read_text()) == outcome.report
            result["subtitle_after_save"] = page.report_row.get_subtitle()
        if trigger == "configured":
            files = list(out.glob("*-report.json"))
            result["configured_file_matches"] = bool(files) and json.loads(files[0].read_text()) == outcome.report
        later(100, app.quit)
    win.runner.connect("finished", finished)
    later(700, running)


def host(win):
    from m9n import hostinfo
    page = win.pages["host"]
    port = os.environ["M9N_HOST_PORT"]
    os.environ["PATH"] = os.environ["M9N_FAKE_PATH"]  # Fake lsusb/systemctl/udevadm/fuser for determinism.
    hostinfo.devices = lambda: [
        {"port": port, "vid": "1546", "pid": "01a9", "serial": None, "product": "u-blox GNSS receiver",
         "manufacturer": "u-blox AG", "by_id": ["/dev/serial/by-id/usb-u-blox-if00"]},
        {"port": "/dev/ttyUSB7", "vid": "0403", "pid": "6001", "serial": "A1", "product": "FT232R",
         "manufacturer": "FTDI", "by_id": []}]
    if os.environ.get("M9N_SESSION_PORT"):
        win.start_session(SessionConfig(port=os.environ["M9N_SESSION_PORT"]))
    gaps, last = [], [GLib.get_monotonic_time()]

    def ticker():
        now = GLib.get_monotonic_time()
        gaps.append((now - last[0]) / 1000)
        last[0] = now
        return page.collecting or page.document is None

    def done(_ok):
        doc = page.document
        result["keys"] = list(doc)
        result["checks"] = {k: r.get_subtitle() for k, r in page.checks.items()}
        result["check_icons"] = {k: r.icon.get_icon_name() for k, r in page.checks.items()}
        result["fix_visible"] = page.fix.get_visible()
        result["devices"] = [r.get_title() for r in page.device_rows]
        result["outputs"] = {k: r.get_subtitle() for k, r in page.output_rows.items() if r.get_visible()}
        result["max_gap_ms"] = max(gaps)
        result["tty_fds"] = sum(1 for fd in os.listdir("/proc/self/fd")
                                if os.path.realpath(f"/proc/self/fd/{fd}") == os.path.realpath(port))
        target = Path(os.environ["M9N_OUT"]) / "host.json"
        result["saved"] = page.write(str(target)) and json.loads(target.read_text()) == doc
        if win.runner.running:
            win.runner.stop()
        later(100, app.quit)
    win.show_page("host")  # Mapping the page starts the first collection.
    GLib.timeout_add(50, ticker)
    wait_for(lambda: page.document is not None, done, timeout_ms=8000)


def replay(win):
    kind = os.environ["M9N_KIND"]
    out = Path(os.environ["M9N_OUT"])
    toasts = []
    original = win.toast
    win.toast = lambda text, timeout=3: toasts.append(text) or original(text, timeout)
    if kind == "demo":
        win.start_session(SessionConfig(source="demo", fault="corrupt"))
    else:
        win.start_session(SessionConfig(source="replay", capture=Path(os.environ["M9N_CAPTURE"]),
                                        json_log=out / "replay.jsonl"))
    result["banner"] = win.banner.get_title() if win.banner.get_revealed() else None
    gaps, last, fractions = [], [GLib.get_monotonic_time()], []

    def ticker():
        now = GLib.get_monotonic_time()
        gaps.append((now - last[0]) / 1000)
        last[0] = now
        if win.progress.get_visible():
            fractions.append(win.progress.get_fraction())
        if kind == "cancel" and fractions and fractions[-1] > .2 and win.runner.running:
            win.activate_action("win.toggle-session", None)
        return win.runner.running

    def finished(_runner, outcome):
        later(150, lambda: inspect(outcome))

    def inspect(outcome):
        dialog = win.get_visible_dialog()
        result.update(reason=outcome.reason, toasts=toasts, max_gap_ms=max(gaps or [0]),
                      fractions=[min(fractions or [0]), max(fractions or [0])],
                      progress_visible_after=win.progress.get_visible(),
                      dialog=dialog.get_heading() if dialog else None,
                      banner_after=win.banner.get_title() if win.banner.get_revealed() else None,
                      dashboard=win.pages["dashboard"].stack.get_visible_child_name(),
                      checksum=outcome.snapshot["counters"].get("checksum_errors", 0))
        log = out / "replay.jsonl"
        result["jsonl_lines"] = len(log.read_text().splitlines()) if log.exists() else None
        result["files_open"] = sum(1 for fd in os.listdir("/proc/self/fd")
                                   if os.path.realpath(f"/proc/self/fd/{fd}").startswith(str(out.resolve())))
        if dialog:
            dialog.force_close()
        later(100, app.quit)
    win.runner.connect("finished", finished)
    GLib.timeout_add(20, ticker)
    if kind == "demo":
        later(1300, lambda: win.runner.stop())


def vehicle(win):
    """Vehicle page with the demo (M9N_FAULT: drive, no-fix, frozen, ...); units, reset, inhibit, layout."""
    page = win.pages["vehicle"]
    page.set_units("metric")  # Probes share one config directory.
    # A headless session has no session manager, so record inhibit requests instead of trusting cookies.
    inhibits = result.setdefault("inhibits", [])
    original_inhibit, original_uninhibit = app.inhibit, app.uninhibit
    app.inhibit = lambda window, flags, reason: inhibits.append(["inhibit", int(flags), reason]) or (
        original_inhibit(window, flags, reason) or 1000 + len(inhibits))
    app.uninhibit = lambda cookie: inhibits.append(["uninhibit", cookie]) or (
        original_uninhibit(cookie) if cookie < 1000 else None)
    result["empty_before"] = page.stack.get_visible_child_name()
    win.show_page("vehicle")
    fault = os.environ.get("M9N_FAULT") or None
    win.start_session(SessionConfig(source="demo", fault=fault, stale_after=float(os.environ.get("M9N_STALE", "5"))))

    def read():
        return {"visible": page.stack.get_visible_child_name(), "status": page.status.get_label(),
                "speed": page.speed.value, "maximum": page.speed.maximum, "unit": page.speed.unit,
                "speed_status": page.speed.status, "heading": page.compass.heading, "hint": page.compass.hint,
                "altitude": page.altitude.value, "altitude_unit": page.altitude.unit,
                "profile": len(page.profile),
                "tiles": {k: [t.value, t.unit] for k, t in page.tiles.items()},
                "trip": (page.snapshot or {}).get("trip")}

    def first():
        result["live"] = read()
        result["idle_inhibited"] = bool(win._idle_cookie)
        width = page.adaptive.get_width()
        result["overflow"] = page.adaptive.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0] > width
        result["main_orientation"] = page.main.get_orientation().value_nick
        page.imperial.set_active(True)
        later(1200, units)

    def units():
        result["imperial"] = read()
        result["saved_units"] = win.setup.settings.units
        page.reset_trip()
        result["after_reset_profile"] = len(page.profile)
        later(1100, after_reset)

    def after_reset():
        result["after_reset"] = read()
        page.metric.set_active(True)
        win.show_page("dashboard")
        result["idle_after_leaving"] = bool(win._idle_cookie)
        win.activate_action("win.fullscreen", None)
        later(300, fullscreen)

    def fullscreen():
        result["fullscreen_icon"] = page.fullscreen_button.get_icon_name()
        result["fullscreened"] = win.is_fullscreen()
        result["fullscreen_accels"] = app.get_accels_for_action("win.fullscreen")
        win.runner.stop()
        result["idle_after_stop"] = bool(win._idle_cookie)
        result["stopped"] = read()
        later(100, app.quit)

    later(int(os.environ.get("M9N_WAIT_MS", "3300")), first)


def fake_tile_server():
    """A loopback tile server recording each request's headers; answers revalidation with 304."""
    import http.server
    import io
    import threading
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 256, 256)
    context = cairo.Context(surface)
    context.set_source_rgb(0.85, 0.9, 0.8)
    context.paint()
    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    png, requests = buffer.getvalue(), []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            etag = f'"{self.path}"'
            requests.append({"path": self.path, "user_agent": self.headers.get("User-Agent"),
                             "if_none_match": self.headers.get("If-None-Match"),
                             "cache_control": self.headers.get("Cache-Control"),
                             "pragma": self.headers.get("Pragma")})
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "max-age=0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "max-age=0")  # Expire at once, so a revisit revalidates.
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/{{z}}/{{x}}/{{y}}.png", requests


def map_page(win):
    """Map page: Off by default with no network, track/follow, consent, online tiles, cache, visibility."""
    from m9n.gui import mappage
    page = win.pages["map"]
    sessions = mappage.tiles.SESSIONS_CREATED if mappage.tiles else []
    result["shumate"] = mappage.Shumate is not None
    result["initial"] = {"mode": page.mode, "visible": page.stack.get_visible_child_name(), "sessions": len(sessions),
                         "source": page.simple_map.get_map_source().get_id() if page.simple_map else None}
    url, requests = fake_tile_server()
    mappage.TEST_URL = url
    win.show_page("map")
    fault = os.environ.get("M9N_FAULT") or "drive"
    win.start_session(SessionConfig(source="demo", fault=fault))

    def state():
        return {"mode": page.mode, "status": page.status.get_label(), "dimmed": page.dimmed, "track": len(page.track),
                "fix": page.fix, "following": page.following, "recentre": page.recentre.get_visible(),
                "indicator": page.indicator.get_visible(), "sessions": len(sessions), "requests": len(requests),
                "source": page.simple_map.get_map_source().get_id() if page.simple_map else None}

    def live():
        result["live"] = state()
        if page.simple_map and page.fix:
            x, y = mappage.ShumateProjection(page.simple_map).to_px(*page.fix[:2])
            result["marker_offset"] = [x - page.simple_map.get_width() / 2, y - page.simple_map.get_height() / 2]
        width = page.adaptive.get_width()
        shown = page.indicator.get_visible()
        page.indicator.set_visible(True)  # Measure the widest toolbar: the online indicator showing.
        result["overflow"] = page.adaptive.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0] > width
        page.indicator.set_visible(shown)
        if not page.simple_map or os.environ.get("M9N_FAULT") or os.environ.get("M9N_MAP_QUICK"):
            win.runner.stop()
            later(100, app.quit)
            return
        viewport = page.simple_map.get_viewport()
        viewport.set_location(viewport.get_latitude() + 0.01, viewport.get_longitude())  # As a user pan.
        result["after_pan"] = state()
        page.recentre.emit("clicked")
        result["after_recentre"] = state()
        page.mode_buttons["online"].set_active(True)
        later(300, consent_cancel)

    def consent_cancel():
        dialog = win.get_visible_dialog()
        result["consent_heading"] = dialog.get_heading() if dialog else None
        result["consent_mentions"] = all(word in (dialog.get_body() if dialog else "")
                                         for word in ("IP address", "Never sent", "~/.cache/neo-m9n/tiles"))
        if dialog:
            dialog.force_close()
            dialog.emit("response", "cancel")
        result["after_cancel"] = state()
        result["consented_after_cancel"] = win.setup.settings.map_consented
        page.mode_buttons["online"].set_active(True)
        later(300, consent_accept)

    def consent_accept():
        dialog = win.get_visible_dialog()
        if dialog:
            dialog.force_close()
            dialog.emit("response", "online")
        later(2500, online)

    def online():
        result["online"] = state()
        result["user_agents"] = sorted({r["user_agent"] for r in requests})
        result["no_cache_sent"] = any(r["cache_control"] or r["pragma"] for r in requests)
        result["first_requests"] = len(requests)
        page.set_mode("off")
        page.mode_buttons["online"].set_active(True)  # Consent is recorded: no dialog this time.
        later(2500, revalidated)

    def revalidated():
        second = requests[result["first_requests"]:]
        result["dialog_second_time"] = win.get_visible_dialog() is not None
        result["conditional"] = sum(1 for r in second if r["if_none_match"])
        result["second_requests"] = len(second)
        win.show_page("dashboard")
        result["hidden_start"] = len(requests)
        viewport = page.simple_map.get_viewport()
        viewport.set_location(viewport.get_latitude() + 0.05, viewport.get_longitude() + 0.05)
        later(1500, hidden)

    def hidden():
        result["hidden_requests"] = len(requests) - result["hidden_start"]
        win.show_page("map")
        page.set_mode("off")
        result["off_start"] = len(requests)
        viewport = page.simple_map.get_viewport()
        viewport.set_location(viewport.get_latitude() - 0.05, viewport.get_longitude() - 0.05)
        later(1500, off)

    def off():
        result["off_requests"] = len(requests) - result["off_start"]
        result["off"] = state()
        page.show_usage()
        result["cache_usage"] = page.cache_usage.get_label()
        page.clear_cache()
        result["cache_after_clear"] = page.cache_usage.get_label()
        win.reset_trip()
        result["after_reset"] = state()
        result["saved"] = [win.setup.settings.map_mode, win.setup.settings.map_consented]
        win.runner.stop()
        later(100, app.quit)

    later(int(os.environ.get("M9N_WAIT_MS", "3000")), live)


def map_restore(win):
    """A saved, consented Online mode comes back on the next start."""
    page = win.pages["map"]
    result["mode"] = page.mode
    result["indicator"] = page.indicator.get_visible()
    result["saved"] = [win.setup.settings.map_mode, win.setup.settings.map_consented]
    later(800, lambda: (result.__setitem__("saved_later", win.setup.settings.map_mode), app.quit()))


def map_offline(win):
    """Offline map file: no network, draws from the file, outside coverage, dark style, bad files, removal."""
    import tempfile
    import mapfile
    from m9n.gui import mappage
    page = win.pages["map"]
    folder = tempfile.mkdtemp()
    good = mapfile.write(os.path.join(folder, "test.mbtiles"))
    raster = mapfile.write(os.path.join(folder, "raster.mbtiles"), fmt="png")
    url, requests = fake_tile_server()
    mappage.STYLE_TILES_URL = url.replace(".png", ".pbf")  # Any fetch of the style's URL would show up here.
    sessions = mappage.tiles.SESSIONS_CREATED
    result["offline_sensitive_before"] = page.mode_buttons["offline"].get_sensitive()
    result["bad_file"] = page.set_map_file(raster, quiet=True)
    result["bad_label"] = page.map_file_label.get_label()
    result["good_file"] = page.set_map_file(good)
    result["offline_sensitive"] = page.mode_buttons["offline"].get_sensitive()
    page.mode_buttons["offline"].set_active(True)
    result["mode"] = page.mode
    result["source"] = page.simple_map.get_map_source().get_id()
    result["indicator"] = page.indicator_label.get_label()
    result["license"] = page.simple_map.get_map_source().get_license()
    win.show_page("map")
    win.start_session(SessionConfig(source="demo", fault="drive"))

    def live():
        result["tiles_served"] = page.offline.requests
        result["style_url_requests"] = len(requests)
        result["sessions"] = len(sessions)
        result["inside"] = not page.outside_offline_map(mappage.ShumateProjection(page.simple_map),
                                                         page.simple_map.get_width(), page.simple_map.get_height())
        result["saved"] = [win.setup.settings.map_mode, win.setup.settings.map_offline_file]
        page.simple_map.get_viewport().set_location(51.5, -0.12)  # London: not in the file.
        result["outside"] = page.outside_offline_map(mappage.ShumateProjection(page.simple_map),
                                                     page.simple_map.get_width(), page.simple_map.get_height())
        before = page.offline
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        result["dark_rebuilt"] = page.offline is not before and page.mode == "offline"
        page.follow()
        later(800, removed)

    def removed():
        result["style_url_requests_after"] = len(requests)
        page.set_map_file(None)
        result["after_remove"] = [page.mode, page.mode_buttons["offline"].get_sensitive(),
                                  win.setup.settings.map_offline_file]
        win.runner.stop()
        later(100, app.quit)

    later(2500, live)


def appearance(win):
    """Night mode: the menu, the toolbar toggles, the saved setting and Automatic by Sun."""
    manager = Adw.StyleManager.get_default()
    win.appearance.set_mode("system")  # Probes share one config directory.

    def state():
        return {"scheme": manager.get_color_scheme().value_nick, "dark": manager.get_dark(),
                "night": win.lookup_action("night-mode").get_state().get_boolean(),
                "mode": win.lookup_action("appearance").get_state().get_string(),
                "saved": win.setup.settings.appearance}

    def walk(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from walk(child)
            child = child.get_next_sibling()

    buttons = [w for key in ("vehicle", "map") for w in walk(win.pages[key])
               if isinstance(w, Gtk.ToggleButton) and w.get_action_name() == "win.night-mode"]
    result["buttons"] = len(buttons)
    menu = next(w for w in walk(win) if isinstance(w, Gtk.MenuButton) and w.get_primary()).get_menu_model()
    submenu = menu.get_item_link(0, "submenu")
    result["menu"] = [menu.get_item_attribute_value(0, "label").get_string(),
                      [submenu.get_item_attribute_value(i, "target").get_string() for i in range(submenu.get_n_items())]]
    result["start"] = state()
    win.activate_action("win.night-mode", None)
    result["night_on"] = {**state(), "button": buttons[0].get_active()}
    win.activate_action("win.night-mode", None)
    result["night_off"] = state()
    win.activate_action("win.appearance", GLib.Variant("s", "dark"))
    result["menu_dark"] = state()
    win.activate_action("win.appearance", GLib.Variant("s", "sun"))
    result["sun_waiting"] = state()  # No position yet: looks like the system.
    win.start_session(SessionConfig(source="demo"))  # Isle of Man, 12:00 UTC: sun at about +29°.

    def daytime():
        result["sun_day"] = state()
        night = json.loads(json.dumps(win.pages["dashboard"].snapshot))
        night["navigation"]["utc"] = "2026-10-11T21:00:00Z"  # Well after dusk.
        win.appearance.update(night)
        result["sun_night"] = state()
        win.appearance.update({"navigation": {}})  # Fix lost: keep the last decision.
        result["sun_no_fix"] = state()
        win.activate_action("win.night-mode", None)  # A manual toggle leaves Automatic by Sun.
        result["manual_after_sun"] = state()
        win.runner.stop()
        win.appearance.set_mode("system")
        later(100, app.quit)
    later(1500, daytime)


def layout(win):
    """Columns per page after a short demo device test, at M9N_PROBE_WIDTH with or without the sidebar."""
    win.split.set_show_sidebar(os.environ.get("M9N_SIDEBAR") == "1")
    keys = ["dashboard", "receiver", "test", "host", "satellites"]

    def visit():
        if not keys:
            app.quit()
            return
        key = keys.pop(0)
        win.show_page(key)
        page = win.pages[key]
        content = page.layout if key == "satellites" else page.columns
        wait_for(lambda: content.get_width() > 0, lambda _ok: later(200, lambda: inspect(key)))

    def inspect(key):
        page = win.pages[key]
        if key == "satellites":
            result[key] = {"orientation": page.layout.get_orientation().value_nick,
                           "sky": page.sky.get_content_width(), "width": page.get_width()}
        else:
            columns = page.columns
            bands = [child for child in iter_children(columns.content) if child is not columns.flow_box]
            result[key] = {
                "columns": len(list(iter_children(columns.flow_box))),
                "flow": [len(list(iter_children(column))) for column in iter_children(columns.flow_box)],
                "bands": [band.get_orientation().value_nick for band in bands],
                "width": columns.get_width(),
                # Content wider than the page would be clipped: the scrolled window never scrolls sideways.
                "overflow": columns.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0] > columns.get_width(),
            }
        visit()

    win.runner.connect("finished", lambda *_: later(300, visit))
    win.start_session(SessionConfig(source="demo", mode="test", duration=1.5))


def iter_children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


def on_activate(_app):
    result["activations"] += 1
    if result["activations"] == 1:
        win = app.props.active_window
        if "M9N_TEXT_SCALE" in os.environ:  # Breakpoints are in sp, so a smaller text scale is a wider page.
            Gtk.Settings.get_default().set_property("gtk-xft-dpi", int(96 * 1024 * float(os.environ["M9N_TEXT_SCALE"])))
        win.set_default_size(WIDTH, 700)
        later(300, lambda: globals()[SCENARIO](win))


app.connect_after("activate", on_activate)
GLib.timeout_add_seconds(20, lambda: (result.setdefault("timeout", True), app.quit()) and False)
result["exit"] = app.run([sys.argv[0]])
result["exit_status"] = app.exit_status
if "M9N_PORT" in os.environ:
    result["tty_open_at_exit"] = tty_open(os.environ["M9N_PORT"])
print("RESULT " + json.dumps(result), flush=True)
