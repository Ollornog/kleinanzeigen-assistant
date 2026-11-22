"""Form data <-> listing / settings values. Pure functions, no web framework types."""
from __future__ import annotations

import re
import shlex
from typing import Any, Iterable, Mapping

TEXT_FIELDS = ("title", "description", "category", "seller", "pricing.type", "pricing.schedule.step",
               "location.zipcode", "location.city", "price_check.query", "availability.from", "availability.until")
INT_FIELDS = ("pricing.start", "pricing.reduce_to", "pricing.min_sale", "pricing.schedule.hold_days", "pricing.schedule.steps",
              "pricing.schedule.every_days", "republish.after_days", "republish.max")
FLOAT_FIELDS = ("shipping.cost",)
FACT_KEYS = ("condition", "purchase_price", "market_new", "market_used")
FACT_SHOW_DEFAULT = {"condition": True, "purchase_price": True, "market_new": False, "market_used": False}
TRI_FIELDS = ("shipping.enabled", "republish.enabled")     # "" = default, "true", "false"


class FormError(ValueError):
    pass


def _put(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _get(source: Mapping[str, Any], path: str) -> Any:
    cur: Any = source
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _number(raw: str, path: str, kind: type) -> int | float:
    try:
        return kind(raw.replace(",", ".")) if kind is float else int(raw)
    except ValueError as exc:
        raise FormError(f"{path}: not a number: {raw!r}") from exc


def listing_values(form: Mapping[str, str], texts: Iterable[str],
                   photo_order: Iterable[str] = (), photo_skip: Iterable[str] = (),
                   extra: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    """Form fields (dotted names) to listing.yaml values; empty fields are left out (defaults apply)."""
    values: dict[str, Any] = {"approved": form.get("approved") == "on"}
    order, skip = [n for n in photo_order if n], [n for n in photo_skip if n]
    if order or skip:
        values["photos"] = {k: v for k, v in (("order", order), ("skip", skip)) if v}
    fixed = form.get("fixed_step") == "on"       # switch: fixed step instead of equal steps
    for path in TEXT_FIELDS:
        raw = (form.get(path) or "").replace("\r\n", "\n").strip()
        if path == "pricing.schedule.step" and not fixed:
            continue
        if raw:
            _put(values, path, raw + "\n" if path == "description" and "\n" in raw else raw)
    for path in INT_FIELDS:
        raw = (form.get(path) or "").strip()
        if path == "pricing.schedule.steps" and fixed:
            continue
        if raw:
            _put(values, path, _number(raw, path, int))
    for path in FLOAT_FIELDS:
        raw = (form.get(path) or "").strip()
        if raw:
            number = _number(raw, path, float)
            _put(values, path, int(number) if number == int(number) else number)
    for path in TRI_FIELDS:
        raw = form.get(path) or ""
        if raw in ("true", "false"):
            _put(values, path, raw == "true")
    facts = _facts_values(form, extra or {})
    if facts:
        values["facts"] = facts
    values["texts"] = list(texts)
    attrs = {}
    for line in (form.get("attributes") or "").splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            if key.strip():
                attrs[key.strip()] = val.strip()
    if attrs:
        values["attributes"] = attrs
    exclude = [w.strip() for w in (form.get("price_check.exclude") or "").split(",") if w.strip()]
    if exclude:
        _put(values, "price_check.exclude", exclude)
    return values


def _facts_values(form: Mapping[str, str], extra: Mapping[str, list[str]]) -> dict[str, Any]:
    """Key data: a value per fact and a tick "in the text"; extra rows come as lists (fx.label,
    fx.value, fx.show = indexes of the ticked rows)."""
    out: dict[str, Any] = {}
    for key in FACT_KEYS:
        raw = (form.get(f"facts.{key}") or "").strip()
        if not raw:
            continue
        value: Any = raw if key == "condition" else _number(raw, f"facts.{key}", float)
        if isinstance(value, float) and value == int(value):
            value = int(value)
        out[key] = {"value": value, "show": form.get(f"facts.{key}.show") == "on"}
    ticked = set(extra.get("fx.show", []))
    rows = [{"label": lab.strip(), "value": val.strip(), "show": str(i) in ticked}
            for i, (lab, val) in enumerate(zip(extra.get("fx.label", []), extra.get("fx.value", [])))
            if lab.strip() and val.strip()]
    if rows:
        out["extra"] = rows
    return out


def listing_form(raw: Mapping[str, Any]) -> dict[str, str]:
    """listing.yaml values to form field strings (the reverse of listing_values)."""
    out: dict[str, str] = {}
    for path in TEXT_FIELDS + INT_FIELDS + FLOAT_FIELDS:
        value = _get(raw, path)
        out[path] = "" if value is None else str(value).rstrip("\n") if path == "description" else str(value)
    for path in TRI_FIELDS:
        value = _get(raw, path)
        out[path] = "" if value is None else ("true" if value else "false")
    out["approved"] = "on" if raw.get("approved") else ""
    for key in FACT_KEYS:
        entry = _get(raw, f"facts.{key}") or {}
        out[f"facts.{key}"] = "" if entry.get("value") is None else str(entry["value"])
        out[f"facts.{key}.show"] = "on" if entry.get("show", FACT_SHOW_DEFAULT[key]) else ""
    out["fixed_step"] = "on" if out["pricing.schedule.step"] and not out["pricing.schedule.steps"] else ""
    out["attributes"] = "\n".join(f"{k}: {v}" for k, v in (raw.get("attributes") or {}).items())
    out["price_check.exclude"] = ", ".join(_get(raw, "price_check.exclude") or [])
    return out


_KEY = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß_-]{1,32}$")


def settings_values(form: Mapping[str, str], rows: Mapping[str, list[str]], current: dict[str, Any]) -> dict[str, Any]:
    """Settings form to the config dict. `rows` holds the repeated fields (lists in form order)."""
    raw = dict(current)
    raw["source_dir"] = (form.get("source_dir") or "").strip() or current["source_dir"]

    sellers: dict[str, Any] = {}
    for key, name, zipcode, city in zip(rows.get("seller.key", []), rows.get("seller.name", []),
                                        rows.get("seller.zipcode", []), rows.get("seller.city", [])):
        key = key.strip()
        if not key:
            continue
        if not _KEY.match(key):
            raise FormError(f"seller short name {key!r}: letters, digits, - or _ (no spaces)")
        loc = {k: v.strip() for k, v in (("zipcode", zipcode), ("city", city)) if v.strip()}
        sellers[key] = {"name": name.strip(), **({"location": loc} if loc else {})}
    raw["sellers"] = sellers

    blocks: dict[str, str] = {}
    for key, text in zip(rows.get("block.key", []), rows.get("block.text", [])):
        key = key.strip()
        if not key:
            continue
        if not _KEY.match(key):
            raise FormError(f"text block name {key!r}: letters, digits, - or _ (no spaces)")
        blocks[key] = text.replace("\r\n", "\n").strip()
    raw["text_blocks"] = blocks

    defaults = dict(current.get("listing_defaults") or {})
    seller = (form.get("d.seller") or "").strip()
    seller = next((k for k in sellers if k.lower() == seller.lower()), seller)
    if seller and seller not in sellers:
        raise FormError(f"default seller {seller!r} is not a profile")
    if seller:
        defaults["seller"] = seller
    else:
        defaults.pop("seller", None)
    names = {k.lower(): k for k in blocks}
    defaults["texts"] = [names[t.lower()] for t in rows.get("d.texts", []) if t.lower() in names]
    sched = {}
    for name in ("hold_days", "every_days", "steps"):
        value = (form.get(f"d.{name}") or "").strip()
        if value:
            sched[name] = _number(value, name, int)

    defaults["pricing"] = {"type": form.get("d.type") or "NEGOTIABLE", "schedule": sched}
    defaults["shipping"] = {"enabled": form.get("d.shipping") == "on"}
    if (form.get("d.shipping_cost") or "").strip():
        defaults["shipping"]["cost"] = _number(form["d.shipping_cost"].strip(), "shipping cost", float)
    defaults["republish"] = {"enabled": form.get("d.republish") == "on",
                             "after_days": _number(form.get("d.after_days") or "14", "after_days", int),
                             "max": _number(form.get("d.max") or "2", "max", int)}
    raw["listing_defaults"] = defaults
    from ..catalog import CONDITIONS
    texts = {c: (form.get(f"cond.{c}") or "").replace("\r\n", "\n").strip() for c in CONDITIONS if f"cond.{c}" in form}
    if texts:
        raw["condition_texts"] = texts        # an emptied text adds no sentence

    lo = _number(form.get("l.pause_min") or "45", "pause", int)
    hi = _number(form.get("l.pause_max") or "150", "pause", int)
    if lo > hi:
        raise FormError("pause: minimum above maximum")
    raw["limits"] = {"max_publish": _number(form.get("l.max_publish") or "3", "max_publish", int),
                     "max_actions": _number(form.get("l.max_actions") or "8", "max_actions", int),
                     "pause_seconds": [lo, hi]}
    sync_min = int(_number(form.get("a.sync_hours") or "24", "sync hours", float) * 60)
    inbox_min = _number(form.get("a.inbox_minutes") or "10", "message minutes", int)
    if sync_min < 60 or inbox_min < 5:
        raise FormError("sync at most every hour, messages at most every 5 minutes")
    raw["auto"] = {"enabled": form.get("a.enabled") == "on", "on_start": form.get("a.on_start") == "on",
                   "sync_minutes": sync_min, "inbox_minutes": inbox_min}
    raw["browser"] = {"start_minimized": form.get("b.start_minimized") == "on",
                      "show_during_actions": form.get("b.show_during_actions") == "on"}
    if form.get("r.mode") in ("off", "draft", "auto"):
        max_auto = _number(form.get("r.max_auto") or "3", "automatic replies", int)
        if not 1 <= max_auto <= 10:
            raise FormError("automatic replies per conversation: 1 to 10")
        raw["replies"] = {"mode": form["r.mode"], "max_auto": max_auto}
    llm_cfg = dict(current.get("llm") or {})
    command = (form.get("llm.command") or "").strip()
    if command:
        llm_cfg["command"] = shlex.split(command)
    if form.get("llm.provider") in ("mistral", "command"):
        llm_cfg["provider"] = form["llm.provider"]
    llm_cfg["fallback"] = form.get("llm.fallback") == "on"
    model = (form.get("llm.model") or "").strip()
    if model.endswith("-latest"):
        raise FormError("choose a pinned model name, not a '-latest' alias")
    if "llm.model" in form:
        llm_cfg["model"] = model
    raw["llm"] = llm_cfg
    return raw
