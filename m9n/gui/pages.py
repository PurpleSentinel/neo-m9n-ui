"""Page registry and the empty states shown before a session starts."""
from gi.repository import Adw


class Page:
    def __init__(self, key, title, icon, empty_title, empty_text):
        self.key, self.title, self.icon = key, title, icon
        self.empty_title, self.empty_text = empty_title, empty_text


PAGES = [
    Page("dashboard", "Dashboard", "find-location-symbolic", "No Session Running",
         "Start a session to see the fix, position, timing and receiver identity."),
    Page("satellites", "Satellites", "network-cellular-signal-excellent-symbolic", "No Satellite Data",
         "Satellites appear here once the receiver answers NAV-SAT."),
    Page("receiver", "Receiver", "network-wireless-symbolic", "No Receiver Diagnostics",
         "RF, antenna and receiver status appear here during a session."),
    Page("messages", "Messages", "view-list-bullet-symbolic", "No Messages",
         "Receiver notices, parser errors and the application log appear here."),
    Page("test", "Test", "object-select-symbolic", "No Device Test",
         "Run a timed device test to get a PASS, WARN or FAIL report."),
    Page("host", "Host", "computer-symbolic", "Host Diagnostics",
         "USB devices, access permissions and competing services appear here."),
]


def empty_page(page):
    return Adw.StatusPage(icon_name=page.icon, title=page.empty_title, description=page.empty_text)
