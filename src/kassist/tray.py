"""Tray icon for kassist (Linux desktops). Runs as its own small process with the SYSTEM Python, which
has the GTK bindings (python3-gi); the app itself lives in a venv without them. Talks to the running
kassist only over its local HTTP port, and ends when kassist is gone.

Cinnamon (Linux Mint): XApp.StatusIcon. Other desktops (Raspberry Pi OS, GNOME with the extension):
AyatanaAppIndicator3. This file imports nothing from kassist on purpose.

    /usr/bin/python3 tray.py <base-url> <icon-path> <labels-json>
"""
from __future__ import annotations

import json
import sys
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# the panel files the icon under the program name; "tray.py" would clash with other trays (mintreport)
GLib.set_prgname("kassist")
GLib.set_application_name("kassist")

BASE, ICON, LABELS = sys.argv[1].rstrip("/"), sys.argv[2], json.loads(sys.argv[3])
misses = 0


def call(path: str, method: str = "POST") -> dict:
    req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read()
    try:
        return json.loads(body)
    except ValueError:
        return {}


def action(path: str):
    def run(*_args) -> None:
        try:
            call(path)
        except OSError:
            pass
    return run


def quit_all(*_args) -> None:
    try:
        call("/quit")
    except OSError:
        pass
    Gtk.main_quit()


menu = Gtk.Menu()
for label, handler in ((LABELS["open"], action("/window")), (LABELS["sync"], action("/sync")),
                       (LABELS["browser"], action("/browser/show")), (None, None), (LABELS["quit"], quit_all)):
    item = Gtk.SeparatorMenuItem() if label is None else Gtk.MenuItem(label=label)
    if handler:
        item.connect("activate", handler)
    menu.append(item)
menu.show_all()

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp  # noqa: E402
    icon = XApp.StatusIcon()
    icon.set_name("kassist")
    icon.set_icon_name(ICON)
    icon.set_tooltip_text(LABELS["title"])
    icon.set_secondary_menu(menu)
    icon.set_primary_menu(None)
    icon.connect("activate", lambda *_a: action("/window")())       # left click opens the window

    def set_status(text: str) -> None:
        icon.set_tooltip_text(text)
except (ValueError, ImportError):
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
    icon = AppIndicator.Indicator.new("kassist", ICON, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    icon.set_menu(menu)
    icon.set_title(LABELS["title"])

    def set_status(text: str) -> None:
        icon.set_title(text)


def poll() -> bool:
    """Tooltip with the sync state; leave when kassist no longer answers."""
    global misses
    try:
        st = call("/status", "GET")
        misses = 0
        parts = [LABELS["title"]]
        if st.get("running"):
            parts.append(LABELS["running"])
        elif st.get("next_sync"):
            parts.append(f"{LABELS['next']} {st['next_sync']}")
        if st.get("error"):
            parts.append("❌ " + str(st["error"])[:80])
        set_status(" · ".join(parts))
    except OSError:
        misses += 1
        if misses >= 3:
            Gtk.main_quit()
            return False
    return True


poll()
GLib.timeout_add_seconds(20, poll)
Gtk.main()
