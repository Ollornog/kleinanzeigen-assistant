"""Decide what each item needs today. Pure: no browser, no clock, no files."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .catalog import Listing, content_hash
from .pricing import target_price
from .state import ItemState

FINAL = {"sold", "withdrawn"}


@dataclass(frozen=True)
class Action:
    kind: str          # publish | update_price
    key: str
    price: int
    reason: str


def apply_online(item: ItemState, online: dict[int, dict[str, Any]], today: date) -> ItemState:
    """Fold the account's ad list (id -> {state, endDate}) into the local item state."""
    if item.ad_id is None or item.status in FINAL:
        return item
    ad = online.get(item.ad_id)
    if ad is None or ad.get("state") == "inactive":
        if item.status in ("online", "reserved"):
            return replace(item, status="expired", expired_on=today)
        return item
    end = ad.get("endDate")
    end_date = _parse_day(end) if end else item.end_date
    status = "reserved" if ad.get("state") == "paused" else "online"
    return replace(item, status=status, end_date=end_date, expired_on=None)


def _parse_day(value: str) -> date | None:
    for candidate in (value[:10], value):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    try:  # Kleinanzeigen uses dd.mm.yyyy in places
        d, m, y = value.split(".")[:3]
        return date(int(y[:4]), int(m), int(d))
    except (ValueError, IndexError):
        return None


def staged(listing: Listing, item: ItemState) -> str | None:
    """None: the ad shows what the folder says. "pending": changed since publishing, not approved yet.
    "approved": changed and approved, to be sent with the next run."""
    if item.published_hash is None or item.status not in ("online", "reserved"):
        return None
    current = content_hash(listing)
    if current == item.published_hash:
        return None
    return "approved" if item.approved_hash == current else "pending"


def plan_item(listing: Listing, item: ItemState, today: date) -> list[Action]:
    if not listing.approved or item.status in FINAL:
        return []
    sched = listing.schedule
    if listing.available_until and today > listing.available_until:
        if item.status in ("online", "reserved") and item.ad_id is not None:
            return [Action("withdraw", listing.key, item.price or sched.start, f"available until {listing.available_until}")]
        return []
    if listing.available_from and today < listing.available_from:
        return []
    if item.status == "new" or (item.ad_id is None and item.status != "expired"):
        return [Action("publish", listing.key, sched.start, "new listing")]
    if item.status in ("online", "reserved") and staged(listing, item) == "approved":
        price = item.price if item.price is not None else sched.start
        if item.status == "online":
            origin = item.schedule_origin or item.published_on or today
            price = target_price(sched, (today - origin).days)
        return [Action("update_content", listing.key, price, "approved changes")]
    if item.status == "online":
        origin = item.schedule_origin or item.published_on or today
        want = target_price(sched, (today - origin).days)
        if item.price != want:
            return [Action("update_price", listing.key, want, f"schedule: {item.price} -> {want}")]
        return []
    if item.status == "expired":
        republished = max(0, item.publish_count - 1)
        if not listing.republish or republished >= listing.max_republish or item.expired_on is None:
            return []
        if (today - item.expired_on).days >= listing.republish_after_days:
            price = item.price if item.price is not None else sched.start
            return [Action("publish", listing.key, price, f"republish {republished + 1}/{listing.max_republish}")]
    return []


def plan(listings: list[Listing], items: dict[str, ItemState], today: date,
         max_publish: int = 3, max_actions: int = 8) -> list[Action]:
    """All actions for today, capped so one run never looks like a burst (captcha after ~10 ads)."""
    actions: list[Action] = []
    publishes = 0
    for listing in listings:
        for action in plan_item(listing, items.get(listing.key, ItemState(listing.key)), today):
            if action.kind == "publish":
                if publishes >= max_publish:
                    continue
                publishes += 1
            actions.append(action)
    return actions[:max_actions]
