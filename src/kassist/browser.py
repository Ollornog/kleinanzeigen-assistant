"""The persistent Chrome that holds the Kleinanzeigen login.

A real, headed Chrome with its own profile: real GPU, home IP, long-lived cookies (headless is
detected by the site and leads to IP blocks). It runs minimized in the background and comes to
the front when a person is needed: login, captcha, SMS/device check, errors, and (by default)
briefly while ads are published or changed. Remote debugging listens on 127.0.0.1 only; anything
on this machine that can reach the port can use the session, so keep the port local.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import time
from typing import Iterator

from .config import Settings

START_URL = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
LOGIN_URL = "https://www.kleinanzeigen.de/m-einloggen.html"
# keep pages working while the window is minimized or covered
BACKGROUND_FLAGS = ("--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling")


def running(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(settings: Settings, wait: float = 15, minimized: bool | None = None) -> bool:
    """Start Chrome if it is not running yet. True if it was started now."""
    if running(settings.cdp_port):
        return False
    settings.chrome_profile.mkdir(parents=True, exist_ok=True)
    args = [
        settings["chrome"],
        f"--user-data-dir={settings.chrome_profile}",
        "--remote-debugging-host=127.0.0.1",
        f"--remote-debugging-port={settings.cdp_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,1000",
        *BACKGROUND_FLAGS,
        START_URL,
    ]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if running(settings.cdp_port):
            if minimized if minimized is not None else settings["browser"]["start_minimized"]:
                time.sleep(1.5)                     # let the first window appear before minimizing it
                with contextlib.suppress(Exception):
                    hide(settings)
            return True
        time.sleep(0.5)
    raise RuntimeError(f"Chrome did not open port {settings.cdp_port}")


async def _set_window(port: int, state: str, url: str | None = None) -> None:
    import nodriver
    from nodriver import Config, cdp

    cfg = Config(browser_executable_path="/usr/bin/true")   # only connecting, never launching
    cfg.host, cfg.port = "127.0.0.1", port
    browser = await nodriver.start(cfg)
    try:
        tab = browser.main_tab or (browser.tabs[0] if browser.tabs else None)
        if tab is None:
            return
        if url:
            await tab.get(url)
        window_id, _ = await tab.send(cdp.browser.get_window_for_target(tab.target_id))
        await tab.send(cdp.browser.set_window_bounds(window_id, cdp.browser.Bounds(
            window_state=cdp.browser.WindowState(state))))
        if state == "normal":
            await tab.send(cdp.target.activate_target(tab.target_id))
    finally:
        from .portal import disconnect
        await disconnect(browser)


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except RuntimeError as exc:        # called from inside a running loop: do it on a fresh thread
        if "running event loop" not in str(exc):
            raise
        import threading
        t = threading.Thread(target=asyncio.run, args=(coro,))
        t.start()
        t.join(20)


def ensure(settings: Settings) -> None:
    """Chrome only starts when kassist needs Kleinanzeigen (nothing listed: no browser, no traffic)."""
    if not running(settings.cdp_port):
        start(settings)


def show(settings: Settings, url: str | None = None) -> None:
    """Bring the Kleinanzeigen window to the front (starting Chrome if needed)."""
    if not running(settings.cdp_port):
        start(settings, minimized=False)
    _run(_set_window(settings.cdp_port, "normal", url))


def hide(settings: Settings) -> None:
    if running(settings.cdp_port):
        _run(_set_window(settings.cdp_port, "minimized"))


@contextlib.contextmanager
def shown_while(settings: Settings) -> Iterator[None]:
    """Show the window while the bot works on ads (setting browser.show_during_actions)."""
    if not settings["browser"]["show_during_actions"]:
        yield
        return
    with contextlib.suppress(Exception):
        show(settings)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            hide(settings)
