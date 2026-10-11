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
    # Messages and Host always show content: neither needs a session.
    result["empty_states"] = all(isinstance(w.stack.get_visible_child(), Adw.StatusPage)
                                 for key, w in win.pages.items() if key not in ("messages", "host"))
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


def on_activate(_app):
    result["activations"] += 1
    if result["activations"] == 1:
        win = app.props.active_window
        win.set_default_size(WIDTH, 700)
        later(300, lambda: globals()[SCENARIO](win))


app.connect_after("activate", on_activate)
GLib.timeout_add_seconds(20, lambda: (result.setdefault("timeout", True), app.quit()) and False)
result["exit"] = app.run([sys.argv[0]])
result["exit_status"] = app.exit_status
if "M9N_PORT" in os.environ:
    result["tty_open_at_exit"] = tty_open(os.environ["M9N_PORT"])
print("RESULT " + json.dumps(result), flush=True)
