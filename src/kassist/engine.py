"""Glue: scan folders, sync with the account, plan, execute with pauses; inbox polling; manual actions."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import random
import shutil
import subprocess
import time
from pathlib import Path
from dataclasses import replace
from datetime import date, datetime
from typing import Callable, Iterator

from . import bot, browser, catalog, drafts, inbox, llm, market_ai, planner, prices
from .config import Settings
from .portal import LoggedOut, Portal, PortalError
from .state import ItemState, Store


@contextlib.contextmanager
def browser_lock(settings: Settings, wait: float = 600) -> Iterator[None]:
    """Only one user of the shared browser at a time (bot run, sync, inbox poll)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / "browser.lock", "w") as fh:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError("browser is busy (another kassist run holds the lock)")
                time.sleep(2)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "kassist", title, body[:300]], check=False)


def listings(settings: Settings) -> dict[str, catalog.Listing]:
    found = catalog.scan(settings.source_dir, settings.catalog())
    return {s.listing.key: s.listing for s in found if s.listing}


NEEDS_PERSON = ("captcha", "verification", "verifizierung", "sms", "ip_range", "login", "einloggen")


def attention(settings: Settings, reason: str, url: str | None = None) -> None:
    """A person is needed (login, captcha, SMS check, error): bring the browser window to the front."""
    notify("Kleinanzeigen: please check the browser", reason[:200])
    try:
        browser.show(settings, url)
    except Exception:  # noqa: BLE001 - showing the window must never break a run
        pass


def _portal(settings: Settings, **kwargs) -> Portal:
    browser.ensure(settings)
    return Portal(settings.cdp_port, **kwargs)


def run_bot(settings: Settings, command: str, ads: str | None = None) -> bot.BotResult:
    """kleinanzeigen-bot with the window shown while it works; captcha/SMS/login leave it in front."""
    browser.ensure(settings)
    with browser.shown_while(settings):
        result = bot.run(settings, command, ads=ads)
    if not result.ok and any(w in result.error.lower() for w in NEEDS_PERSON):
        attention(settings, result.error)
    return result


# --- sync and tick ------------------------------------------------------------------------------

async def _fetch_my_ads(settings: Settings) -> dict[int, dict]:
    async with _portal(settings) as portal:
        return {int(ad["id"]): ad for ad in await portal.my_ads() if str(ad.get("id", "")).isdigit()}


def sync(settings: Settings, store: Store, today: date) -> dict[int, dict]:
    online = asyncio.run(_fetch_my_ads(settings))
    for item in store.items():
        new = planner.apply_online(item, online, today)
        if new != item:
            store.save_item(new)
            if new.status != item.status:
                store.log("status", item.key, f"{item.status} -> {new.status}")
    return online


def _sent(listing: catalog.Listing) -> dict:
    """State fields for 'this content is now online'."""
    h = catalog.content_hash(listing)
    return {"published_hash": h, "approved_hash": h,
            "published_snapshot": json.dumps(catalog.content_snapshot(listing), ensure_ascii=False)}


TRASH = ".papierkorb"


def trash_folder(settings: Settings, store: Store, key: str) -> Path:
    """Move an item folder into <source>/.papierkorb/<name>-<time> (hidden from the list, restorable by
    moving it back). Refused while the ad is online or paused: delete the ad first."""
    folder = settings.source_dir / key
    if key.startswith(".") or "/" in key or folder.parent != settings.source_dir or not folder.is_dir():
        raise ValueError(f"unknown item: {key}")
    item = store.item(key)
    if item.status in ("online", "reserved"):
        raise ValueError("the ad is still online – delete the ad first")
    target = settings.source_dir / TRASH / f"{key}-{datetime.now():%Y%m%d-%H%M%S}"
    target.parent.mkdir(exist_ok=True)
    shutil.move(str(folder), str(target))
    store.delete_item(key)
    store.log("trash", key, str(target))
    return target


def approve_changes(settings: Settings, store: Store, key: str) -> str:
    """Approve the staged changes of an online ad; the next run sends them."""
    listing = listings(settings).get(key)
    if listing is None:
        raise ValueError(f"{key}: no valid listing")
    item = store.item(key)
    store.save_item(replace(item, approved_hash=catalog.content_hash(listing)))
    store.log("approve-changes", key, ", ".join(changed_fields(listing, item)))
    return key


def changed_fields(listing: catalog.Listing, item: ItemState) -> list[str]:
    if not item.published_snapshot:
        return []
    old = json.loads(item.published_snapshot)
    new = catalog.content_snapshot(listing)
    return [k for k in new if old.get(k) != new[k]]


def _execute(settings: Settings, store: Store, listing: catalog.Listing, action: planner.Action, today: date) -> str:
    item = store.item(listing.key)
    bot.write_ad(settings, listing, action.price, active=True)
    bot.set_active_only(settings, {listing.key})
    if action.kind == "publish":
        old_id = bot.read_ad(settings, listing.key).get("id")
        result = run_bot(settings, "publish", ads=str(old_id) if old_id else "new")
        new_id = bot.read_ad(settings, listing.key).get("id")
        if not result.ok or not new_id or (old_id and new_id == old_id and item.status == "expired"):
            raise RuntimeError(f"publish failed: {result.error}")
        first = item.publish_count == 0
        store.save_item(replace(
            item, status="online", ad_id=int(new_id), price=action.price, published_on=today,
            schedule_origin=today if first else (item.schedule_origin or today),
            expired_on=None, end_date=None, publish_count=item.publish_count + 1, last_error=None,
            **_sent(listing)))
        return f"published as {new_id} at {action.price} EUR ({action.reason})"
    if action.kind == "update_content":
        result = run_bot(settings, "update", ads=str(item.ad_id))
        if not result.ok:
            raise RuntimeError(f"update failed: {result.error}")
        store.save_item(replace(item, price=action.price, last_error=None, **_sent(listing)))
        return f"changes sent ({action.price} EUR)"
    if action.kind == "update_price":
        result = run_bot(settings, "update", ads=str(item.ad_id))
        if not result.ok:
            raise RuntimeError(f"update failed: {result.error}")
        store.save_item(replace(item, price=action.price, last_error=None))
        return f"price {item.price} -> {action.price} EUR"
    if action.kind == "withdraw":
        result = run_bot(settings, "delete", ads=str(item.ad_id))
        if not result.ok:
            raise RuntimeError(f"delete failed: {result.error}")
        store.save_item(replace(item, status="withdrawn", last_error=None))
        return f"withdrawn ({action.reason})"
    raise ValueError(action.kind)


def tick(settings: Settings, store: Store, today: date | None = None, dry_run: bool = False,
         sleep: Callable[[float], None] = time.sleep) -> list[str]:
    today = today or date.today()
    found = listings(settings)
    report: list[str] = []
    with browser_lock(settings):
        if not dry_run:
            try:
                sync(settings, store, today)
            except LoggedOut as exc:
                attention(settings, str(exc), browser.LOGIN_URL)
                return [f"❌ {exc}"]
        items = {i.key: i for i in store.items()}
        limits = settings["limits"]
        actions = planner.plan(list(found.values()), items, today,
                               max_publish=int(limits["max_publish"]), max_actions=int(limits["max_actions"]))
        lo, hi = limits["pause_seconds"]
        for n, action in enumerate(actions):
            line = f"{action.kind} {action.key} {action.price} EUR ({action.reason})"
            if dry_run:
                report.append("would " + line)
                continue
            if n:
                sleep(random.uniform(float(lo), float(hi)))
            try:
                done = _execute(settings, store, found[action.key], action, today)
                store.log(action.kind, action.key, done)
                report.append(f"✅ {action.key}: {done}")
            except Exception as exc:  # noqa: BLE001 - one failing item must not stop the run
                store.save_item(replace(store.item(action.key), last_error=str(exc)[:1000]))
                store.log("error", action.key, str(exc)[:1000])
                report.append(f"❌ {action.key}: {str(exc)[:300]}")
                if "captcha" in str(exc).lower() or "IP_RANGE" in str(exc):
                    notify("Kleinanzeigen: stopped", "Captcha or IP block, run stopped")
                    break
    return report


def run_item(settings: Settings, store: Store, key: str, today: date | None = None) -> str:
    """Do what is due for one item now (publish or price update), outside the daily run."""
    today = today or date.today()
    listing = listings(settings).get(key)
    if listing is None:
        raise ValueError(f"{key}: no valid listing")
    if not listing.approved:
        raise ValueError(f"{key}: not approved")
    with browser_lock(settings):
        try:
            sync(settings, store, today)
        except LoggedOut as exc:
            attention(settings, str(exc), browser.LOGIN_URL)
            raise
        actions = planner.plan_item(listing, store.item(key), today)
        if not actions:
            return f"{key}: nothing due"
        done = []
        for action in actions:
            try:
                done.append(_execute(settings, store, listing, action, today))
                store.log(action.kind, key, done[-1])
            except Exception as exc:
                store.save_item(replace(store.item(key), last_error=str(exc)[:1000]))
                store.log("error", key, str(exc)[:1000])
                raise
    return f"{key}: " + "; ".join(done)


# --- price overview -----------------------------------------------------------------------------

def price_check(settings: Settings, store: Store, key: str | None = None, query: str | None = None,
                pages: int = 2, ai: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Search Kleinanzeigen for similar ads, summarize their asking prices, optionally let the AI
    cross-check condition and contents, and keep the last checks in the item's listing.yaml."""
    exclude = list(prices.DEFAULT_EXCLUDE)
    listing = listings(settings).get(key) if key else None
    if listing is None and key:          # not ready for approval yet: still good enough for a price check
        listing = catalog.load_draft(settings.source_dir / key, settings.catalog())
    if listing is not None:
        exclude += listing.price_exclude
        query = query or listing.price_query or prices.default_query(listing.title)
    if key and not query:   # folder without listing.yaml: search words from its draft or its name
        state = next((s for s in catalog.scan(settings.source_dir, settings.catalog()) if s.folder.name == key), None)
        title = key
        if state and state.importable:
            try:
                title = catalog.parse_draft_markdown(state.importable.read_text(encoding="utf-8"))["title"]
            except ValueError:
                pass
        query = prices.default_query(title)
    if not query:
        raise ValueError("no search words")
    own = {str(i.ad_id) for i in store.items() if i.ad_id}

    async def _search() -> list[prices.Offer]:
        found: list[prices.Offer] = []
        async with _portal(settings, require_login=False) as portal:
            for page in range(1, pages + 1):
                if page > 1:
                    sleep(random.uniform(3, 7))
                batch = prices.parse_results(await portal.get_html(prices.search_path(query, page)))
                found.extend(batch)
                if len(batch) < 20:
                    break
        return found

    with browser_lock(settings):
        offers = asyncio.run(_search())
    summary = prices.summarize(offers, tuple(exclude), own, query=query)
    summary["query"] = query
    if ai:
        if listing is None:
            raise ValueError("the AI check needs a listing.yaml (title and text of the item)")
        candidates = [o for o in offers if o.price and o.id not in own]
        summary["ai"] = market_ai.check(lambda prompt: llm.complete(settings, prompt, json_mode=True),
                                        listing.title, listing.body, candidates)
    store.add_price_check(key, query, summary, prices.as_rows(offers))
    if listing is not None and (summary.get("used") or summary.get("ai")):
        entry = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "query": query, "offers": summary["found"],
                 "used": summary["used"], **{k: summary[k] for k in ("min", "p25", "median", "p75", "max") if k in summary}}
        if summary.get("ai"):
            entry["ai"] = {k: v for k, v in summary["ai"].items() if k != "per_offer"}
        catalog.write_market(listing.folder, entry)
    store.log("prices", key, f"{query}: {summary.get('used', 0)} used, median {summary.get('median', '-')}")
    return summary


# --- manual actions -----------------------------------------------------------------------------

MANUAL = {"reserve": ("reserve", "reserved"), "activate": ("activate", "online"),
          "sold": ("delete", "sold"), "withdraw": ("delete", "withdrawn")}


def manual(settings: Settings, store: Store, key: str, what: str) -> str:
    command, status = MANUAL[what]
    item = store.item(key)
    if item.ad_id is None or item.status not in ("online", "reserved"):
        if what in ("sold", "withdraw"):   # not online: only mark locally
            store.save_item(replace(item, status=status))
            store.log(what, key, "marked locally")
            return f"{key}: marked {status}"
        raise ValueError(f"{key} is not online")
    with browser_lock(settings):
        bot.set_active_only(settings, {key})
        result = run_bot(settings, command, ads=str(item.ad_id))
    if not result.ok:
        store.log("error", key, result.error)
        raise RuntimeError(f"{command} failed: {result.error}")
    store.save_item(replace(item, status=status, last_error=None))
    store.log(what, key, f"{command} {item.ad_id}")
    return f"{key}: {status}"


# --- inbox ---------------------------------------------------------------------------------------

async def _poll(settings: Settings, store: Store, force: bool) -> list[str]:
    changed: list[str] = []
    async with _portal(settings) as portal:
        meta = await portal.inbox_meta()
        marker = settings.data_dir / "inbox.lastModified"
        last = marker.read_text().strip() if marker.exists() else ""
        if not force and str(meta.get("lastModified", "")) == last and last:
            return []
        known = {c["id"]: c["last_message_at"] for c in store.conversations()}
        for raw in await portal.conversations():
            row = inbox.conversation_row(raw)
            if known.get(row["id"]) == row["last_message_at"] and not force:
                continue
            store.upsert_conversation(row)
            detail = await portal.conversation(row["id"])
            for msg in inbox.message_rows(row["id"], detail):
                store.add_message(msg)
            changed.append(row["id"])
        marker.write_text(str(meta.get("lastModified", "")))
    return changed


def _listing_for(found: dict[str, catalog.Listing], store: Store, ad_id: int | None) -> tuple:
    for item in store.items():
        if ad_id is not None and item.ad_id == ad_id and item.key in found:
            return found[item.key], item
    return None, None


def make_draft(settings: Settings, store: Store, cid: str) -> str | None:
    conv = store.conversation(cid)
    msgs = store.messages(cid)
    if conv is None or not msgs or msgs[-1]["outgoing"]:
        return None
    listing, item = _listing_for(listings(settings), store, conv["ad_id"])
    if listing is None:
        return None
    text = llm.complete(settings, drafts.build_prompt(listing, item, msgs))
    store.add_draft(cid, text, msgs[-1]["id"])
    return text


def poll_inbox(settings: Settings, store: Store, force: bool = False, with_drafts: bool = True) -> list[str]:
    with browser_lock(settings):
        try:
            changed = asyncio.run(_poll(settings, store, force))
        except LoggedOut as exc:
            attention(settings, str(exc), browser.LOGIN_URL)
            raise
    new = list(store.unnotified())
    if new:
        first = new[0]
        notify(f"Kleinanzeigen: {len(new)} new message(s)",
               f"{first['partner']} ({first['ad_title']}): {first['text'][:140]}")
        store.mark_notified([m["id"] for m in new])
    mode = settings["replies"].get("mode", "draft")
    if with_drafts and mode != "off":
        for cid in {m["conversation_id"] for m in new}:
            try:
                text = make_draft(settings, store, cid)
                if text and mode == "auto":
                    auto_reply(settings, store, cid, text)
            except Exception as exc:  # noqa: BLE001
                store.log("error", None, f"draft {cid}: {exc}"[:1000])
    return changed


def auto_reply(settings: Settings, store: Store, cid: str, text: str) -> bool:
    """Send a fresh draft on its own unless it carries a marker or the conversation had enough
    automatic replies; otherwise it stays a draft and the owner is told."""
    conv = store.conversation(cid)
    title = conv["ad_title"] if conv else cid
    found = drafts.markers(text)
    limit = int(settings["replies"].get("max_auto", 3))
    if found or store.auto_replies(cid) >= limit:
        why = ", ".join(found) or f"{limit} automatic replies reached"
        notify("Kleinanzeigen: reply needs you", f"{title}: {why}")
        store.log("auto-reply-held", None, f"{cid}: {why}")
        return False
    draft = store.open_draft(cid)
    send_reply(settings, store, cid, drafts.strip_markers(text), draft["id"] if draft else None, sent_by="auto")
    notify("Kleinanzeigen: answered automatically", f"{title}: {text[:140]}")
    return True


def send_reply(settings: Settings, store: Store, cid: str, text: str, draft_id: int | None = None,
               sent_by: str = "owner") -> None:
    async def _send() -> None:
        async with _portal(settings) as portal:
            await portal.reply(cid, text)
            detail = await portal.conversation(cid)
            for msg in inbox.message_rows(cid, detail):
                store.add_message(msg)
    with browser_lock(settings):
        asyncio.run(_send())
    if draft_id is not None:
        store.set_draft_status(draft_id, "sent", sent_by)
    store.log("auto-reply" if sent_by == "auto" else "reply", None, f"{cid}: {text[:200]}")


__all__ = ["tick", "manual", "poll_inbox", "send_reply", "make_draft", "listings", "sync",
           "ItemState", "PortalError"]
