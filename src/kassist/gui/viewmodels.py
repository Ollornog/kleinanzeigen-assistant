"""View models: plain dicts for the templates. No HTML here, no web framework types."""
from __future__ import annotations

import shlex
from urllib.parse import quote
from datetime import date
from pathlib import Path
from typing import Any

from datetime import timedelta

from .. import bot, catalog, engine, planner
from ..config import Settings
from ..pricing import PriceSchedule, price_after
from ..state import ItemState, Store
from . import forms

STATE_VARIANT = {"new": "outline", "online": "success", "reserved": "warning", "expired": "muted",
                 "sold": "info", "withdrawn": "muted", "draft": "outline", "error": "destructive"}
ACTIONS_BY_STATUS = {"online": ["reserve", "sold", "withdraw"], "reserved": ["activate", "sold", "withdraw"],
                     "expired": ["sold", "withdraw"], "new": ["withdraw"]}


def _thumb(folder: Path, images: list[Path]) -> str | None:
    return f"/thumb/{quote(folder.name)}/{quote(images[0].name)}?s=160" if images else None


def items(settings: Settings, store: Store, today: date | None = None,
          scanned: list[catalog.FolderState] | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    states = {i.key: i for i in store.items()}
    rows = []
    if scanned is None:
        try:
            scanned = catalog.scan(settings.source_dir, settings.catalog())
        except ValueError:
            return []
    for s in scanned:
        name = s.folder.name
        item = states.get(name, ItemState(name))
        li = s.listing
        status = "error" if s.error else (item.status if li else "draft")
        check = store.last_price_check(name)
        summary = (check or {}).get("summary") or {}
        rows.append({
            "key": name, "title": li.title if li else "", "category": (li.category or "") if li else "",
            "thumb": _thumb(s.folder, li.images if li else catalog.images_in(s.folder)),
            "status": status, "variant": STATE_VARIANT.get(status, "outline"),
            "error": s.error or item.last_error,
            "approved": bool(li and li.approved),
            "price": item.price if item.price is not None else (li.schedule.start if li else None),
            "reduce_to": li.schedule.floor if li else None, "min_sale": li.min_sale if li else None,
            "market": summary.get("median"), "market_ai": (summary.get("ai") or {}).get("median"),
            "next": "; ".join(f"{a.kind} {a.price} €" for a in planner.plan_item(li, item, today)) if li else "",
        })
    return rows


def item(settings: Settings, store: Store, key: str) -> dict[str, Any]:
    folder = settings.source_dir / key
    cat = settings.catalog()
    raw = catalog.load_raw(folder)
    has_listing = (folder / catalog.LISTING_FILE).is_file()
    listing, error = None, None
    if has_listing:
        try:
            listing = catalog.load_listing(folder, cat)
        except ValueError as exc:
            error = str(exc)
    drafts = sorted(folder.glob("*_inserat_*.md"))
    state = store.item(key)
    actions = planner.plan_item(listing, state, date.today()) if listing else []
    defaults = cat.defaults
    texts_checked = raw.get("texts") if raw.get("texts") is not None else defaults.get("texts") or []
    return {
        "key": key, "has_listing": has_listing, "importable": bool(drafts) and not has_listing,
        "sources": [p.name for p in catalog.text_sources(folder)] if not has_listing else [], "error": error,
        "conditions": [{"key": c, "text": cat.condition_texts.get(c, "")} for c in catalog.CONDITIONS],
        "extra_facts": list(((raw.get("facts") or {}).get("extra") or [])) + [{"label": "", "value": "", "show": True}],
        "last_median": next((m.get("median") for m in raw.get("market") or [] if isinstance(m, dict) and m.get("median")), None),
        "form": forms.listing_form(raw), "texts": [{"key": k, "text": v, "checked": k in texts_checked}
                                                    for k, v in cat.text_blocks.items()],
        "sellers": sorted(cat.sellers), "default_seller": defaults.get("seller") or "",
        "placeholders": _placeholders(listing, defaults),
        "photos": photos(folder, raw.get("photos") or {}),
        "categories": sorted(bot.categories()),
        "plan": price_plan(schedule, state.schedule_origin or state.published_on or date.today())
        if (schedule := _schedule(folder, raw, cat, listing)) else [],
        "due": actions[0].kind if actions else None,
        "staged": planner.staged(listing, state) if listing else None,
        "online_action": online_action(listing, state, actions),
        "changed": engine.changed_fields(listing, state) if listing else [],
        "title": raw.get("title") or "",
        "state": state, "status": state.status, "variant": STATE_VARIANT.get(state.status, "outline"),
        "actions": ACTIONS_BY_STATUS.get(state.status, []) if listing else [],
        "market": raw.get("market") or [],
        "next": "; ".join(f"{a.kind} {a.price} €" for a in actions),
    }


def online_action(listing: catalog.Listing | None, state: ItemState, actions: list) -> dict[str, Any]:
    """The one button that changes the ad on Kleinanzeigen now: its label key and whether it can be used."""
    if listing is None:
        return {"label": "on.none", "enabled": False}
    if not listing.approved:
        return {"label": "on.approve_first", "enabled": False}
    if planner.staged(listing, state):
        return {"label": "on.changes", "enabled": True}
    kind = actions[0].kind if actions else None
    label = {"publish": "on.publish", "update_price": "on.price", "update_content": "on.changes",
             "withdraw": "on.withdraw"}.get(kind)
    if label:
        return {"label": label, "enabled": True}
    return {"label": "on.current" if state.status in ("online", "reserved") else "on.none", "enabled": False}


def photos(folder: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """All photos in ad order (used ones first, cover first), skipped ones at the end."""
    skip = set(cfg.get("skip") or [])
    used = catalog.images_in(folder, cfg.get("order"), list(skip))
    rest = [p for p in catalog.all_images(folder) if p not in used]
    return [{"name": p.name, "url": f"/photo/{quote(folder.name)}/{quote(p.name)}",
             "thumb": f"/thumb/{quote(folder.name)}/{quote(p.name)}?s=480", "skipped": p.name in skip,
             "cover": i == 0 and p.name not in skip} for i, p in enumerate(used + rest)]


def price_plan(schedule: PriceSchedule, origin: date, limit: int = 12) -> list[dict[str, Any]]:
    """Price steps with their first day: [{day, price, last}]; one entry if there is no reduction."""
    steps = [{"day": origin, "price": schedule.start, "last": schedule.start <= schedule.floor}]
    for n in range(1, limit + 1):
        price = price_after(schedule, n)
        if price >= steps[-1]["price"]:
            break
        day = origin + timedelta(days=schedule.hold_days + (n - 1) * schedule.every_days)
        steps.append({"day": day, "price": price, "last": price <= schedule.floor})
    return steps


def _schedule(folder: Path, raw: dict[str, Any], cat: catalog.Catalog,
              listing: catalog.Listing | None) -> PriceSchedule | None:
    """The price schedule, also while the listing is not valid yet (e.g. approved without category)."""
    if listing is not None:
        return listing.schedule
    draft = catalog.load_draft(folder, cat)
    return draft.schedule if draft else None


def _placeholders(listing: catalog.Listing | None, defaults: dict[str, Any]) -> dict[str, str]:
    sched = (defaults.get("pricing") or {}).get("schedule") or {}
    rep = defaults.get("republish") or {}
    out = {"pricing.schedule.hold_days": str(sched.get("hold_days", 14)),
           "pricing.schedule.step": str(sched.get("step", "5")),
           "pricing.schedule.every_days": str(sched.get("every_days", 7)),
           "pricing.schedule.steps": str(sched.get("steps", "")),
           "republish.after_days": str(rep.get("after_days", 14)), "republish.max": str(rep.get("max", 2))}
    if listing:
        out.update({"location.zipcode": str(listing.contact.get("zipcode", "")),
                    "location.city": str(listing.contact.get("location", "")),
                    "pricing.reduce_to": str(listing.schedule.floor), "pricing.min_sale": str(listing.min_sale)})
    return out


def settings_view(settings: Settings) -> dict[str, Any]:
    raw = settings.raw
    d = raw.get("listing_defaults") or {}
    sched = (d.get("pricing") or {}).get("schedule") or {}
    rep = d.get("republish") or {}
    lim = raw.get("limits") or {}
    pause = lim.get("pause_seconds") or [45, 150]
    return {
        "source_dir": str(settings.source_dir), "source_ok": settings.source_dir.is_dir(),
        "sellers": [{"key": k, "name": v.get("name", ""), "zipcode": (v.get("location") or {}).get("zipcode", ""),
                     "city": (v.get("location") or {}).get("city", "")} for k, v in (raw.get("sellers") or {}).items()],
        "blocks": [{"key": k, "text": v} for k, v in (raw.get("text_blocks") or {}).items()],
        "d": {"seller": d.get("seller", ""), "texts": d.get("texts") or [], "type": (d.get("pricing") or {}).get("type", "NEGOTIABLE"),
              "hold_days": sched.get("hold_days", ""), "step": sched.get("step", ""), "every_days": sched.get("every_days", ""),
              "steps": sched.get("steps", ""), "shipping_cost": (d.get("shipping") or {}).get("cost", ""),
              "shipping": bool((d.get("shipping") or {}).get("enabled")), "republish": bool(rep.get("enabled", True)),
              "after_days": rep.get("after_days", 14), "max": rep.get("max", 2)},
        "l": {"max_publish": lim.get("max_publish", 3), "max_actions": lim.get("max_actions", 8),
              "pause_min": pause[0], "pause_max": pause[1]},
        "llm": _llm_view(raw.get("llm") or {}),
        "auto": raw.get("auto") or {}, "login_start": _autostart(),
        "conditions": [{"key": c, "text": (raw.get("condition_texts") or {}).get(c, "")} for c in catalog.CONDITIONS], "browser": raw.get("browser") or {}, "replies": raw.get("replies") or {},
    }


def _autostart() -> bool:
    from . import desktop
    return desktop.autostart_enabled()


def _llm_view(cfg: dict[str, Any]) -> dict[str, Any]:
    from .. import llm
    key_set = bool(llm.load_secrets().get(llm.MISTRAL_KEY))
    models, error = [], None
    if key_set:
        try:
            models = llm.mistral_models()
        except llm.LLMError as exc:
            error = str(exc)
    command = cfg.get("command") or []
    program = llm.find_program(command[0]) if command else None
    return {"provider": cfg.get("provider", "command"), "fallback": cfg.get("fallback", True),
            "model": cfg.get("model", ""), "models": models, "models_error": error, "key_set": key_set,
            "command": shlex.join(command), "program": program,
            "ready": {"command": program is not None, "mistral": key_set and bool(cfg.get("model"))}}


def browse(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.is_dir():
        path = Path.home()
    try:
        dirs = sorted((p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.lower())
    except PermissionError:
        dirs = []
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
            "dirs": [{"name": p.name, "path": str(p)} for p in dirs[:500]]}
