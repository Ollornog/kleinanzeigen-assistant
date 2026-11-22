"""Start kassist: local server, background sync and tray icon; the app window is only a view.

`kassist gui`               start (or reuse the running one) and open the window
`kassist gui --background`  start with the tray icon only (used at login)
`kassist panel`             serve only, no window, no tray (headless, e.g. a Pi without desktop)

Closing the window leaves kassist running in the tray; "Quit" in the tray (or the menu) ends it.
Linux Mint / Raspberry Pi OS: the window is Chrome or Chromium with --app and its own profile.
Elsewhere the default browser opens the page.
"""
from __future__ import annotations

import json
import locale
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from typing import Callable

import uvicorn

from ..config import Settings
from . import desktop, i18n
from .app import create_app
from .scheduler import Scheduler

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge")
install_desktop_entry = desktop.install_desktop_entry


def _wait_port(host: str, port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"GUI server did not start on {host}:{port}")


def already_running(url: str) -> bool:
    """Is a kassist answering there (not some other program on the port)?"""
    try:
        with urllib.request.urlopen(url + "ping", timeout=2) as resp:
            return json.loads(resp.read()).get("app") == "kassist"
    except (OSError, ValueError):
        return False


def window_opener(settings: Settings, url: str) -> Callable[[], None]:
    def open_window() -> None:
        exe = next((b for b in BROWSERS if shutil.which(b)), None)
        if not exe:
            webbrowser.open(url)
            return
        profile = settings.data_dir / "gui-window"
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1280,900"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return open_window


def tray_labels() -> dict[str, str]:
    lang_env = os.environ.get("LANG") or (locale.getlocale()[0] or "")
    t = i18n.TABLES[i18n.pick(lang_env.replace("_", "-"))]
    return {k: t[f"tray.{k}"] for k in ("title", "open", "sync", "browser", "quit", "running", "next")}


def run(settings: Settings, window: bool = True, background: bool = False, tray: bool = True) -> None:
    cfg = settings["panel"]
    host, port = cfg["host"], int(cfg["port"])
    url = f"http://{host}:{port}/"
    open_window = window_opener(settings, url)
    if already_running(url):                    # second start: just show the running one
        if window and not background:
            open_window()
        return
    scheduler = Scheduler(settings)
    if settings["auto"].get("enabled", True):
        scheduler.start()
    server = uvicorn.Server(uvicorn.Config(create_app(settings, scheduler=scheduler), host=host, port=port,
                                           log_level="warning"))
    app = server.config.app
    app.state.open_window = open_window
    app.state.quit = lambda: setattr(server, "should_exit", True)
    tray_proc: list = []

    def after_start() -> None:
        _wait_port(host, port)
        if tray:
            proc = desktop.start_tray(url, tray_labels())
            if proc:
                tray_proc.append(proc)
        if window and not background:
            open_window()
    threading.Thread(target=after_start, daemon=True).start()
    try:
        server.run()                            # until "Quit" (tray or menu) or Ctrl+C
    finally:
        scheduler.stop()
        for proc in tray_proc:
            proc.terminate()
