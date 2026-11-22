"""Engine with a fake bot and a fake account: no browser, no network."""
from datetime import date, timedelta

import pytest
import yaml

from kassist import bot, engine
from kassist.config import Settings, DEFAULTS, _merge
from kassist.state import Store

TODAY = date(2026, 11, 22)


@pytest.fixture
def env(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data"),
                                          "limits": {"pause_seconds": [0, 0]}}))
    store = Store(settings.db_path)
    account: dict[int, dict] = {}
    calls: list[tuple] = []
    next_id = [1000]

    def fake_run(s, command, ads=None, timeout=900):
        calls.append((command, ads))
        if command == "publish":
            for path in (s.bot_dir / "ads").glob("*.yaml"):
                ad = yaml.safe_load(path.read_text())
                if ad.get("active"):
                    account.pop(ad.get("id"), None)
                    next_id[0] += 1
                    ad["id"] = next_id[0]
                    account[ad["id"]] = {"id": ad["id"], "state": "active", "price": ad["price"]}
                    path.write_text(yaml.safe_dump(ad))
        elif command == "update":
            path = next(p for p in (s.bot_dir / "ads").glob("*.yaml") if yaml.safe_load(p.read_text()).get("id") == int(ads))
            account[int(ads)]["price"] = yaml.safe_load(path.read_text())["price"]
        elif command == "reserve":
            account[int(ads)]["state"] = "paused"
        elif command == "delete":
            account.pop(int(ads), None)
        return bot.BotResult(True, "ok")

    def fake_sync(s, st, today):
        from kassist import planner
        for item in st.items():
            new = planner.apply_online(item, account, today)
            if new != item:
                st.save_item(new)
        return account

    monkeypatch.setattr(bot, "run", fake_run)
    monkeypatch.setattr(engine, "sync", fake_sync)
    monkeypatch.setattr(engine, "notify", lambda *a: None)
    yield settings, store, src, account, calls
    store.close()


def add_item(src, name, approved=True, price=49, floor=42):
    folder = src / name
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"\xff\xd8")
    (folder / "listing.yaml").write_text(yaml.safe_dump({
        "title": f"{name} zu verkaufen", "description": "Text", "approved": approved, "category": "161/225/sonstiges",
        "pricing": {"start": price, "reduce_to": floor}}), encoding="utf-8")


def run(settings, store, day):
    return engine.tick(settings, store, today=day, sleep=lambda s: None)


def test_full_lifecycle(env):
    settings, store, src, account, calls = env
    add_item(src, "KVM")
    add_item(src, "Draft", approved=False)

    report = run(settings, store, TODAY)
    assert len(report) == 1 and "published as 1001 at 49" in report[0]
    item = store.item("KVM")
    assert (item.status, item.ad_id, item.price, item.schedule_origin) == ("online", 1001, 49, TODAY)
    assert store.item("Draft").status == "new"          # unapproved: untouched

    assert run(settings, store, TODAY + timedelta(days=13)) == []
    assert "49 -> 47" in run(settings, store, TODAY + timedelta(days=14))[0]     # default: 3 equal steps
    assert account[1001]["price"] == 47
    assert "47 -> 44" in run(settings, store, TODAY + timedelta(days=21))[0]
    assert "44 -> 42" in run(settings, store, TODAY + timedelta(days=28))[0]
    assert run(settings, store, TODAY + timedelta(days=40)) == []

    account.clear()                                      # Kleinanzeigen let it expire
    day = TODAY + timedelta(days=61)
    assert run(settings, store, day) == []
    assert store.item("KVM").status == "expired"
    report = run(settings, store, day + timedelta(days=14))
    assert "published as 1002 at 42" in report[0] and "republish 1/2" in report[0]
    assert store.item("KVM").schedule_origin == TODAY    # schedule keeps counting: stays at floor
    assert ("publish", "1001") in calls                  # republish names the old id


def test_manual_reserve_and_sold(env):
    settings, store, src, account, calls = env
    add_item(src, "Helm")
    run(settings, store, TODAY)
    assert "reserved" in engine.manual(settings, store, "Helm", "reserve")
    assert run(settings, store, TODAY + timedelta(days=30)) == []   # reserved: no price changes
    assert store.item("Helm").status == "reserved"
    assert "sold" in engine.manual(settings, store, "Helm", "sold")
    assert run(settings, store, TODAY + timedelta(days=200)) == []
    assert store.item("Helm").status == "sold"


def test_failing_bot_run_is_recorded_and_others_continue(env, monkeypatch):
    settings, store, src, account, calls = env
    add_item(src, "A")
    add_item(src, "B")
    real = bot.run

    def flaky(s, command, ads=None, timeout=900):
        active = [yaml.safe_load(p.read_text()) for p in (s.bot_dir / "ads").glob("*.yaml")]
        if any(a.get("active") and a["title"].startswith("A ") for a in active):
            return bot.BotResult(False, "boom")
        return real(s, command, ads, timeout)
    monkeypatch.setattr(bot, "run", flaky)
    report = run(settings, store, TODAY)
    assert report[0].startswith("❌ A") and report[1].startswith("✅ B")
    assert "boom" in store.item("A").last_error


def test_dry_run_changes_nothing(env):
    settings, store, src, account, calls = env
    add_item(src, "X")
    assert run_dry(settings, store) == ["would publish X 49 EUR (new listing)"]
    assert calls == [] and store.items() == []


def run_dry(settings, store):
    return engine.tick(settings, store, today=TODAY, dry_run=True, sleep=lambda s: None)


def test_bot_ad_file_carries_contact_shipping_attributes_and_keeps_bot_fields(env):
    settings, store, src, account, calls = env
    from kassist.catalog import Catalog, load_listing
    add_item(src, "Helm")
    raw = yaml.safe_load((src / "Helm" / "listing.yaml").read_text())
    raw.update({"seller": "d", "texts": ["legal"], "shipping": {"enabled": True, "cost": 6.19},
                "attributes": {"condition": "like_new"}, "category": "161/278"})
    (src / "Helm" / "listing.yaml").write_text(yaml.safe_dump(raw))
    cat = Catalog(sellers={"d": {"name": "Alex", "location": {"zipcode": "10115", "city": "Berlin"}}},
                  text_blocks={"legal": "Privatverkauf."})
    li = load_listing(src / "Helm", cat)
    path = bot.write_ad(settings, li, 44, active=True)
    ad = yaml.safe_load(path.read_text())
    assert ad["contact"] == {"name": "Alex", "zipcode": "10115", "location": "Berlin"}
    assert (ad["shipping_type"], ad["shipping_costs"], ad["price"]) == ("SHIPPING", 6.19, 44)
    assert ad["special_attributes"] == {"condition": "like_new"} and ad["category"] == "161/278"
    assert ad["description"].endswith("Privatverkauf.")
    ad["id"] = 4711
    path.write_text(yaml.safe_dump(ad))
    raw["shipping"] = {"enabled": False}
    (src / "Helm" / "listing.yaml").write_text(yaml.safe_dump(raw))
    ad = yaml.safe_load(bot.write_ad(settings, load_listing(src / "Helm", cat), 40, active=False).read_text())
    assert ad["id"] == 4711 and ad["shipping_type"] == "PICKUP" and "shipping_costs" not in ad


def test_withdraw_after_availability_window(env):
    settings, store, src, account, calls = env
    add_item(src, "Ski")
    raw = yaml.safe_load((src / "Ski" / "listing.yaml").read_text())
    raw["availability"] = {"until": str(TODAY + timedelta(days=10))}
    (src / "Ski" / "listing.yaml").write_text(yaml.safe_dump(raw))
    run(settings, store, TODAY)
    report = run(settings, store, TODAY + timedelta(days=11))
    assert "withdrawn" in report[0] and store.item("Ski").status == "withdrawn"
    assert ("delete", "1001") in calls and 1001 not in account


def test_cli_new_writes_template_once(env, monkeypatch, capsys):
    settings, store, src, account, calls = env
    from kassist import cli
    (src / "Neu").mkdir()
    monkeypatch.setattr(cli, "load", lambda: settings)
    assert cli.main(["new", "Neu"]) == 0
    assert "pricing:" in (src / "Neu" / "listing.yaml").read_text()
    assert cli.main(["new", "Neu"]) == 1
    assert cli.main(["new", "../data"]) == 1          # existing folder outside source_dir
    assert not (settings.data_dir / "listing.yaml").exists()


def test_changes_to_online_ads_are_staged_until_approved(env):
    settings, store, src, account, calls = env
    add_item(src, "KVM")
    run(settings, store, TODAY)
    item = store.item("KVM")
    assert item.published_hash and item.approved_hash == item.published_hash
    from kassist import planner
    from kassist.catalog import load_listing
    path = src / "KVM" / "listing.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["title"] = "KVM zu verkaufen, neuer Titel"
    path.write_text(yaml.safe_dump(raw))
    li = load_listing(src / "KVM", settings.catalog())
    assert planner.staged(li, store.item("KVM")) == "pending"
    assert engine.changed_fields(li, store.item("KVM")) == ["title"]
    calls.clear()
    assert run(settings, store, TODAY + timedelta(days=1)) == []          # not approved: nothing sent
    engine.approve_changes(settings, store, "KVM")
    assert planner.staged(li, store.item("KVM")) == "approved"
    report = run(settings, store, TODAY + timedelta(days=1))
    assert "changes sent" in report[0] and ("update", "1001") in calls
    assert planner.staged(li, store.item("KVM")) is None
    assert run(settings, store, TODAY + timedelta(days=2)) == []
