"""Background sync while kassist runs: on start, then every `sync_minutes` (due actions + messages),
messages every `inbox_minutes` while ads are online. One run at a time; manual "sync now" joins in.
With nothing online and nothing due (no approved new ad, no expiry) the scheduled sync is skipped:
no browser, no traffic to Kleinanzeigen. The button always syncs."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from .. import engine, planner
from ..config import Settings
from ..state import Store

ACTIVE = ("online", "reserved")


class Scheduler:
    def __init__(self, settings: Settings, clock: Callable[[], datetime] = datetime.now) -> None:
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False
        self._thread: threading.Thread | None = None
        self.running: str | None = None          # "sync" | "inbox" while working
        self.last_sync: datetime | None = None
        self.last_inbox: datetime | None = None
        self.last_report: list[str] = []
        self.last_error: str | None = None
        self.idle = False                        # last scheduled sync skipped: nothing to do
        self.started = clock()
        self._start_due = False

    # --- control ---------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            if self.settings["auto"].get("on_start", True):
                self._start_due = True
            self._thread = threading.Thread(target=self._loop, name="kassist-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Sync now (from the button)."""
        self._force = True
        self._wake.set()

    # --- state -------------------------------------------------------------------------------------
    def _cfg(self) -> dict[str, Any]:
        return self.settings["auto"]

    def next_sync(self) -> datetime | None:
        if not self._cfg().get("enabled", True):
            return None
        return (self.last_sync or self.started) + timedelta(minutes=int(self._cfg()["sync_minutes"]))

    def next_inbox(self, active: bool) -> datetime | None:
        if not self._cfg().get("enabled", True) or not active:
            return None
        base = max(t for t in (self.last_inbox, self.last_sync, self.started) if t is not None)
        return base + timedelta(minutes=int(self._cfg()["inbox_minutes"]))

    def has_work(self) -> bool:
        """Anything online, or an action due offline (approved new ad, republish, …)?"""
        if self.has_active_ads():
            return True
        store = Store(self.settings.db_path)
        try:
            items = {i.key: i for i in store.items()}
        finally:
            store.close()
        found = list(engine.listings(self.settings).values())
        return bool(planner.plan(found, items, self.clock().date()))

    def has_active_ads(self) -> bool:
        store = Store(self.settings.db_path)
        try:
            return any(i.status in ACTIVE for i in store.items())
        finally:
            store.close()

    def status(self) -> dict[str, Any]:
        today = self.clock().date()
        fmt = lambda d: (d.strftime("%H:%M") if d.date() == today else d.strftime("%d.%m. %H:%M")) if d else None  # noqa: E731
        return {"running": self.running, "last_sync": fmt(self.last_sync), "last_inbox": fmt(self.last_inbox),
                "next_sync": fmt(self.next_sync()), "error": self.last_error, "idle": self.idle,
                "report": self.last_report[-5:], "auto": bool(self._cfg().get("enabled", True))}

    # --- work --------------------------------------------------------------------------------------
    def due(self, now: datetime) -> str | None:
        """What to run now: "sync", "inbox" or None."""
        if self._force:
            return "sync"
        if not self._cfg().get("enabled", True):
            return None
        nxt = self.next_sync()
        if self._start_due or (nxt is not None and now >= nxt):
            self._start_due = False
            try:
                work = self.has_work()
            except Exception:  # noqa: BLE001 - when unsure (folder not readable), sync
                work = True
            self.idle = not work
            if work:
                return "sync"
            self.last_sync = now                  # skipped: check again after a full interval
            return None
        inbox = self.next_inbox(self.has_active_ads())
        if inbox is not None and now >= inbox:
            return "inbox"
        return None

    def run_once(self, kind: str) -> None:
        if not self._lock.acquire(blocking=False):
            return
        self.running = kind
        store = Store(self.settings.db_path)
        try:
            if kind == "sync":
                self._force = False
                report = engine.tick(self.settings, store)
                try:
                    engine.poll_inbox(self.settings, store)
                    self.last_inbox = self.clock()
                except Exception as exc:  # noqa: BLE001 - messages failing must not hide the run report
                    report.append(f"❌ inbox: {exc}")
                self.last_report = report
                self.last_sync = self.clock()
            else:
                engine.poll_inbox(self.settings, store)
                self.last_inbox = self.clock()
            self.last_error = next((line for line in self.last_report if line.startswith("❌")), None) \
                if kind == "sync" else None
        except Exception as exc:  # noqa: BLE001 - keep the loop alive, show the error
            self.last_error = str(exc)[:300]
            if kind == "sync":
                self.last_sync = self.clock()    # wait a full interval before retrying
            else:
                self.last_inbox = self.clock()
        finally:
            store.close()
            self.running = None
            self._lock.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            kind = self.due(self.clock())
            if kind:
                self.run_once(kind)
            self._wake.wait(30)
            self._wake.clear()
