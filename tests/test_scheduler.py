from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kassist import bot, engine
from kassist.config import DEFAULTS, Settings, _merge
from kassist.gui import create_app
from kassist.gui.scheduler import Scheduler
from kassist.state import ItemState, Store

T0 = datetime(2026, 11, 22, 12, 0)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(tmp_path), "data_dir": str(tmp_path / "data")}))
    calls = []
    monkeypatch.setattr(engine, "tick", lambda s, st, **k: calls.append("tick") or ["✅ x: done"])
    monkeypatch.setattr(engine, "poll_inbox", lambda s, st, **k: calls.append("inbox") or [])
    clock = Clock()
    return settings, Scheduler(settings, clock=clock), clock, calls


def test_sync_on_start_then_daily(setup):
    settings, sched, clock, calls = setup
    sched.trigger()
    assert sched.due(clock.now) == "sync"
    sched.run_once("sync")
    assert calls == ["tick", "inbox"] and sched.last_sync == T0 and sched.last_report == ["✅ x: done"]
    store = Store(settings.db_path)
    store.save_item(ItemState("KVM", status="online", ad_id=1))
    store.close()
    clock.now = T0 + timedelta(hours=23, minutes=59)
    sched.last_inbox = clock.now
    assert sched.due(clock.now) is None
    clock.now = T0 + timedelta(hours=24)
    assert sched.due(clock.now) == "sync"                 # daily by default


def test_nothing_listed_nothing_due_no_sync(setup, tmp_path):
    settings, sched, clock, calls = setup
    sched._start_due = True                               # what start() does with on_start
    assert sched.due(clock.now) is None and sched.idle and sched.last_sync == T0
    assert sched.status()["idle"] is True
    clock.now = T0 + timedelta(days=1)
    assert sched.due(clock.now) is None                   # still nothing: no browser, no traffic
    sched.trigger()
    assert sched.due(clock.now) == "sync"                 # the button always syncs


def test_due_offline_action_starts_a_sync(setup, monkeypatch):
    settings, sched, clock, calls = setup
    from kassist import planner
    monkeypatch.setattr(planner, "plan", lambda found, items, today, **k: ["publish Helm"])
    sched._start_due = True
    assert sched.due(clock.now) == "sync" and not sched.idle  # approved new ad: sync even with nothing online


def test_start_sync_with_ads_online(setup):
    settings, sched, clock, calls = setup
    store = Store(settings.db_path)
    store.save_item(ItemState("KVM", status="reserved", ad_id=1))
    store.close()
    sched._start_due = True
    assert sched.due(clock.now) == "sync"


def test_inbox_every_ten_minutes_only_with_active_ads(setup):
    settings, sched, clock, calls = setup
    sched.run_once("sync")
    store = Store(settings.db_path)
    store.save_item(ItemState("KVM", status="online", ad_id=1))
    store.close()
    clock.now = T0 + timedelta(minutes=9)
    assert sched.due(clock.now) is None
    clock.now = T0 + timedelta(minutes=10)
    assert sched.due(clock.now) == "inbox"
    sched.run_once("inbox")
    assert calls[-1] == "inbox" and sched.last_inbox == clock.now
    clock.now += timedelta(minutes=5)
    assert sched.due(clock.now) is None


def test_disabled_auto_only_runs_on_demand(setup):
    settings, sched, clock, calls = setup
    settings.raw["auto"] = {**settings.raw["auto"], "enabled": False}
    clock.now = T0 + timedelta(days=1)
    assert sched.due(clock.now) is None and sched.status()["auto"] is False
    sched.trigger()
    assert sched.due(clock.now) == "sync"


def test_errors_are_kept_and_retry_waits(setup, monkeypatch):
    settings, sched, clock, calls = setup

    def boom(s, st, **k):
        raise RuntimeError("not logged in")
    monkeypatch.setattr(engine, "tick", boom)
    sched.run_once("sync")
    assert "not logged in" in sched.status()["error"] and sched.last_sync == T0
    assert sched.due(T0 + timedelta(minutes=1)) is None


def test_one_run_at_a_time(setup):
    settings, sched, clock, calls = setup
    sched._lock.acquire()
    sched.run_once("sync")
    assert calls == []
    sched._lock.release()


def test_sync_button_and_status_route(setup):
    settings, sched, clock, calls = setup
    c = TestClient(create_app(settings, scheduler=sched))
    r = c.post("/sync", headers={"referer": "http://127.0.0.1:8765/inbox"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inbox" and calls == ["tick", "inbox"]
    s = c.get("/status").json()
    assert s["last_sync"] == "12:00" and s["next_sync"] == "23.11. 12:00" and s["running"] is None
    page = c.get("/", headers={"accept-language": "de"}).text
    assert "Jetzt synchronisieren" in page and "zuletzt 12:00" in page and "Browser anzeigen" in page


def test_bot_errors_needing_a_person_bring_the_browser_up(setup, monkeypatch, no_real_browser_or_notifications):
    settings, *_ = setup
    shown = no_real_browser_or_notifications
    monkeypatch.setattr(bot, "run", lambda s, c, ads=None: bot.BotResult(False, "[ERROR] Captcha detected, please solve"))
    engine.run_bot(settings, "publish", "new")
    assert shown[0] == ("ensure", None)                                  # Chrome started on demand
    assert shown[1] == ("show", None) and shown[-1] == ("show", None)   # shown during the run, kept up after
    shown.clear()
    monkeypatch.setattr(bot, "run", lambda s, c, ads=None: bot.BotResult(False, "[ERROR] network down"))
    engine.run_bot(settings, "publish", "new")
    assert shown == [("ensure", None), ("show", None), ("hide", None)]   # plain error: back to minimized
    shown.clear()
    settings.raw["browser"] = {**settings.raw["browser"], "show_during_actions": False}
    monkeypatch.setattr(bot, "run", lambda s, c, ads=None: bot.BotResult(True, "ok"))
    engine.run_bot(settings, "update", "1")
    assert shown == [("ensure", None)]
