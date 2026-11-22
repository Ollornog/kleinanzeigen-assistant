from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from kassist.catalog import Listing
from kassist.planner import apply_online, plan, plan_item
from kassist.pricing import PriceSchedule
from kassist.state import ItemState

TODAY = date(2026, 11, 22)


def listing(key="KVM", approved=True, **kw) -> Listing:
    sched = kw.pop("schedule", PriceSchedule(start=49, floor=42, hold_days=14, step="5", every_days=7))
    return Listing(folder=Path("/src") / key, title="T", body="D", schedule=sched, min_sale=sched.floor,
                   approved=approved, **kw)


def online(key="KVM", days=0, price=49, **kw) -> ItemState:
    start = TODAY - timedelta(days=days)
    return ItemState(key=key, status="online", ad_id=1, price=price, schedule_origin=start,
                     published_on=start, publish_count=1, **kw)


def test_unapproved_and_final_items_get_nothing():
    assert plan_item(listing(approved=False), ItemState("KVM"), TODAY) == []
    assert plan_item(listing(), replace(online(), status="sold"), TODAY) == []
    assert plan_item(listing(), replace(online(), status="withdrawn"), TODAY) == []


def test_new_item_is_published_at_start_price():
    [a] = plan_item(listing(), ItemState("KVM"), TODAY)
    assert (a.kind, a.price) == ("publish", 49)


def test_price_follows_schedule_and_is_idempotent():
    assert plan_item(listing(), online(days=13), TODAY) == []
    [a] = plan_item(listing(), online(days=14), TODAY)
    assert (a.kind, a.price) == ("update_price", 44)
    assert plan_item(listing(), online(days=14, price=44), TODAY) == []
    [b] = plan_item(listing(), online(days=21, price=44), TODAY)
    assert b.price == 42
    assert plan_item(listing(), online(days=90, price=42), TODAY) == []


def test_reserved_item_keeps_its_price():
    assert plan_item(listing(), replace(online(days=30), status="reserved"), TODAY) == []


def test_expired_item_waits_then_republishes_at_last_price_limited_times():
    exp = replace(online(days=70, price=42), status="expired", expired_on=TODAY - timedelta(days=13))
    assert plan_item(listing(), exp, TODAY) == []
    exp = replace(exp, expired_on=TODAY - timedelta(days=14))
    [a] = plan_item(listing(), exp, TODAY)
    assert (a.kind, a.price) == ("publish", 42)
    assert plan_item(listing(), replace(exp, publish_count=3), TODAY) == []   # 2 republishes done
    assert plan_item(listing(republish=False), exp, TODAY) == []


def test_apply_online_maps_states():
    item = online()
    assert apply_online(item, {1: {"state": "paused", "endDate": "2027-01-10"}}, TODAY).status == "reserved"
    got = apply_online(item, {1: {"state": "active", "endDate": "10.01.2027"}}, TODAY)
    assert got.status == "online" and got.end_date == date(2027, 1, 10)
    gone = apply_online(item, {}, TODAY)
    assert gone.status == "expired" and gone.expired_on == TODAY
    assert apply_online(item, {1: {"state": "inactive"}}, TODAY).status == "expired"
    sold = replace(item, status="sold")
    assert apply_online(sold, {}, TODAY) is sold


def test_apply_online_keeps_first_expiry_day():
    exp = replace(online(), status="expired", expired_on=TODAY - timedelta(days=3))
    assert apply_online(exp, {}, TODAY).expired_on == TODAY - timedelta(days=3)


def test_plan_caps_publishes_and_total_actions():
    listings = [listing(key=f"I{i}") for i in range(10)]
    actions = plan(listings, {}, TODAY, max_publish=3, max_actions=8)
    assert [a.kind for a in actions] == ["publish"] * 3
    items = {f"I{i}": online(key=f"I{i}", days=14) for i in range(10)}
    assert len(plan(listings, items, TODAY, max_actions=8)) == 8


def test_availability_window():
    li = listing(available_from=TODAY + timedelta(days=3), available_until=TODAY + timedelta(days=30))
    assert plan_item(li, ItemState("KVM"), TODAY) == []                      # not yet
    [a] = plan_item(li, ItemState("KVM"), TODAY + timedelta(days=3))
    assert a.kind == "publish"
    late = TODAY + timedelta(days=31)
    [w] = plan_item(li, online(days=28), late)
    assert w.kind == "withdraw"
    assert plan_item(li, replace(online(days=28), status="reserved"), late)[0].kind == "withdraw"
    exp = replace(online(days=60), status="expired", expired_on=TODAY - timedelta(days=30))
    assert plan_item(li, exp, late) == []                                     # no republish after the window
    assert plan_item(li, ItemState("KVM"), late) == []
