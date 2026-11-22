"""Item folders: one subfolder of the source directory is one listing.

A folder holds photos and a `listing.yaml` in a fixed structure (see examples/listing.yaml):
item text, standard text blocks, seller profile, pricing (start, automatic reduction floor,
lowest sale price), availability window, location, shipping, republish and price-check
settings. Missing parts come from `listing_defaults` in the config. Folders without a listing
can be imported from an existing `*_inserat_*.md` draft (approved: false until approved).
"""
from __future__ import annotations

import contextlib

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .pricing import PriceSchedule

LISTING_FILE = "listing.yaml"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGES = 20  # Kleinanzeigen allows up to 20 photos per ad (private accounts)
MAX_TITLE = 65
TOP_LEVEL = {"approved", "title", "description", "category", "seller", "texts", "pricing", "availability",
             "location", "shipping", "republish", "price_check", "attributes", "photos", "market", "facts"}
CONDITIONS = ("new_sealed", "new", "like_new", "very_good", "good", "used", "defect")
# key data of an item: (key, line in the ad text, shown by default). `show: false` keeps a value as
# context for the AI (reply drafts) without putting it into the ad.
FACTS = (("condition", None, True), ("purchase_price", "Kaufpreis: {v}", True),
         ("market_new", "Marktneupreis: {v}", False), ("market_used", "Gebraucht gehandelt um: {v}", False))
PRICE_FACTS = ("purchase_price", "market_new", "market_used")
MARKET_MARKER = "# --- market: written by `kassist prices`, keeps the last 3 checks; edits below are overwritten ---"
MARKET_KEEP = 3


def write_market(folder: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Put the newest price check on top of the `market` block at the end of listing.yaml.

    Only the block after MARKET_MARKER is rewritten, so the owner's text, order and comments stay."""
    path = folder / LISTING_FILE
    text = path.read_text(encoding="utf-8")
    head, _, _ = text.partition(MARKET_MARKER)
    if MARKET_MARKER not in text and re.search(r"^market:", text, re.MULTILINE):
        raise ValueError("listing.yaml has a hand-written market block; remove it first")
    old = (yaml.safe_load(text) or {}).get("market") or []
    history = [entry] + [e for e in old if isinstance(e, dict)][: MARKET_KEEP - 1]
    block = yaml.safe_dump({"market": history}, allow_unicode=True, sort_keys=False, width=110)
    path.write_text(head.rstrip("\n") + "\n\n" + MARKET_MARKER + "\n" + block, encoding="utf-8")
    return history


@dataclass
class Catalog:
    """What a listing needs from the config besides its own file."""
    defaults: dict[str, Any] = field(default_factory=dict)
    sellers: dict[str, dict[str, Any]] = field(default_factory=dict)
    text_blocks: dict[str, str] = field(default_factory=dict)
    condition_texts: dict[str, str] = field(default_factory=dict)


@dataclass
class Listing:
    folder: Path
    title: str
    body: str                         # item-specific text
    schedule: PriceSchedule           # start price -> automatic reduction floor
    min_sale: int                     # lowest price to accept in chats (never below this)
    approved: bool = False
    texts: list[str] = field(default_factory=list)       # names of standard text blocks
    description: str = ""             # body + text blocks, as published
    price_type: str = "NEGOTIABLE"
    category: str | None = None
    seller: str | None = None
    contact: dict[str, Any] = field(default_factory=dict)  # name, zipcode, location, street
    shipping: bool = False
    shipping_cost: float | None = None
    available_from: date | None = None
    available_until: date | None = None
    republish: bool = True
    republish_after_days: int = 14
    max_republish: int = 2
    price_query: str | None = None
    price_exclude: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)  # category attributes, passed to the bot
    condition: str | None = None      # one of CONDITIONS
    purchase_price: float | None = None  # what the owner paid
    facts: list[tuple[str, str, bool]] = field(default_factory=list)  # (label, value, shown in the ad)

    @property
    def key(self) -> str:
        return self.folder.name

    @property
    def shipping_type(self) -> str:
        return "SHIPPING" if self.shipping else "PICKUP"


def _facts(raw: dict[str, Any], cat: Catalog) -> tuple[str | None, float | None, list, list[str]]:
    """facts block -> condition, new price, all facts (label, value, shown) and the lines for the ad."""
    facts: list[tuple[str, str, bool]] = []
    shown: list[str] = []
    condition = purchase_price = None
    for key, line, default_show in FACTS:
        entry = raw.get(key) or {}
        value = entry.get("value")
        if value in (None, ""):
            continue
        show = bool(entry.get("show", default_show))
        if key == "condition":
            if value not in CONDITIONS:
                raise ValueError(f"facts.condition must be one of {', '.join(CONDITIONS)}")
            condition = value
            text = (cat.condition_texts.get(value) or "").strip()
            facts.append(("Zustand", text or value, show))
            if show and text:
                shown.append(text)
            continue
        try:
            amount = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"facts.{key}: not a number: {value!r}") from exc
        if key == "purchase_price":
            purchase_price = amount
        text = line.format(v=euro(amount))
        facts.append((text.split(":")[0], euro(amount), show))
        if show:
            shown.append(text)
    for entry in raw.get("extra") or []:
        label, value = str(entry.get("label") or "").strip(), str(entry.get("value") or "").strip()
        if not (label and value):
            continue
        show = bool(entry.get("show", True))
        facts.append((label, value, show))
        if show:
            shown.append(f"{label}: {value}")
    return condition, purchase_price, facts, shown


def euro(amount: float) -> str:
    """German price: 110 € / 12,50 €."""
    return f"{int(amount)} €" if amount == int(amount) else f"{amount:.2f} €".replace(".", ",")


def content_snapshot(listing: Listing) -> dict[str, Any]:
    """What the ad shows (prices excluded: they follow the schedule). Used to stage changes to online ads."""
    return {
        "title": listing.title, "description": listing.description, "category": listing.category or "",
        "attributes": dict(sorted(listing.attributes.items())), "price_type": listing.price_type,
        "shipping": listing.shipping, "shipping_cost": listing.shipping_cost,
        "contact": dict(sorted(listing.contact.items())),
        "photos": [f"{p.name}:{p.stat().st_size}" if p.exists() else p.name for p in listing.images],
    }


def content_hash(listing: Listing) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(content_snapshot(listing), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class FolderState:
    folder: Path
    listing: Listing | None
    error: str | None = None
    importable: Path | None = None  # draft markdown that could become a listing.yaml


def all_images(folder: Path) -> list[Path]:
    """Every photo in the folder, by name."""
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                  key=lambda p: p.name)


def images_in(folder: Path, order: list[str] | None = None, skip: list[str] | None = None) -> list[Path]:
    """Photos for the ad: `order` first (the first one is the cover), then any others by name;
    `skip` is left out. Names in `order` that no longer exist are ignored."""
    files = {p.name: p for p in all_images(folder)}
    skipped = set(skip or [])
    ordered = [files[n] for n in dict.fromkeys(order or []) if n in files and n not in skipped]
    rest = [p for name, p in files.items() if name not in skipped and p not in ordered]
    return (ordered + rest)[:MAX_IMAGES]


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Nested merge; empty values (None, "") in `over` do not replace values from `base`."""
    out = dict(base)
    for key, value in (over or {}).items():
        if value is None or value == "":
            continue
        out[key] = deep_merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return out


def _day(value: Any, name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be a date like 2026-12-31") from exc


def load_listing(folder: Path, cat: Catalog | None = None) -> Listing:
    raw = yaml.safe_load((folder / LISTING_FILE).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{LISTING_FILE} must be a mapping")
    return listing_from_dict(folder, raw, cat or Catalog())


def listing_from_dict(folder: Path, raw: dict[str, Any], cat: Catalog) -> Listing:
    unknown = set(raw) - TOP_LEVEL
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    d = deep_merge(cat.defaults, raw)
    for name in ("title", "description"):
        if d.get(name) in (None, ""):
            raise ValueError(f"missing field: {name}")
    title = " ".join(str(d["title"]).split())
    if len(title) > MAX_TITLE:
        raise ValueError(f"title longer than {MAX_TITLE} characters ({len(title)})")

    pricing = d.get("pricing") or {}
    if pricing.get("start") in (None, ""):
        raise ValueError("missing field: pricing.start")
    start = int(pricing["start"])
    reduce_to = int(pricing.get("reduce_to", start))
    min_sale = int(pricing.get("min_sale", reduce_to))
    if min_sale > reduce_to:
        raise ValueError(f"pricing.min_sale {min_sale} is above pricing.reduce_to {reduce_to}")
    sched = pricing.get("schedule") or {}
    own = ((raw.get("pricing") or {}).get("schedule") or {})
    # equal steps (`steps`) unless the item itself sets a fixed `step` without `steps`
    fixed = own.get("step") not in (None, "") and own.get("steps") in (None, "")
    count = 0 if fixed else int(sched.get("steps") or 0)
    schedule = PriceSchedule(start=start, floor=reduce_to, hold_days=int(sched.get("hold_days", 14)),
                             step=str(sched.get("step", "5")), every_days=int(sched.get("every_days", 7)),
                             count=count)

    # names of text blocks and sellers match regardless of upper/lower case ("alex" finds "Alex")
    block_names = {k.lower(): k for k in cat.text_blocks}
    asked = [str(t) for t in d.get("texts") or []]
    missing = [t for t in asked if t.lower() not in block_names]
    if missing:
        raise ValueError(f"unknown text block(s): {', '.join(missing)}")
    texts = [block_names[t.lower()] for t in asked]
    body = str(d["description"]).strip()
    condition, purchase_price, facts, shown = _facts(d.get("facts") or {}, cat)
    # standard texts in the order of the settings (sortable there), not the order in the item file
    description = "\n\n".join([body] + (["\n".join(shown)] if shown else [])
                              + [v.strip() for k, v in cat.text_blocks.items() if k in texts and v.strip()])

    seller = d.get("seller")
    if seller is not None:
        seller_names = {k.lower(): k for k in cat.sellers}
        if str(seller).lower() not in seller_names:
            raise ValueError(f"unknown seller: {seller}")
        seller = seller_names[str(seller).lower()]
    profile = cat.sellers.get(seller, {}) if seller else {}
    loc = deep_merge(profile.get("location") or {}, d.get("location") or {})
    contact = {k: v for k, v in {"name": profile.get("name"), "zipcode": loc.get("zipcode"),
                                 "location": loc.get("city"), "street": loc.get("street")}.items() if v}

    avail = d.get("availability") or {}
    available_from = _day(avail.get("from"), "availability.from")
    available_until = _day(avail.get("until"), "availability.until")
    if available_from and available_until and available_until < available_from:
        raise ValueError("availability.until is before availability.from")

    if d.get("approved") and not d.get("category"):
        raise ValueError("category is required before approval (Kleinanzeigen needs one)")

    ship = d.get("shipping") or {}
    rep = d.get("republish") or {}
    check = d.get("price_check") or {}
    return Listing(
        folder=folder, title=title, body=body, schedule=schedule, min_sale=min_sale,
        approved=bool(d.get("approved", False)), texts=texts, description=description,
        price_type=str(pricing.get("type", "NEGOTIABLE")), category=d.get("category"),
        seller=seller, contact=contact,
        shipping=bool(ship.get("enabled", False)),
        shipping_cost=float(ship["cost"]) if ship.get("cost") not in (None, "") else None,
        available_from=available_from, available_until=available_until,
        republish=bool(rep.get("enabled", True)), republish_after_days=int(rep.get("after_days", 14)),
        max_republish=int(rep.get("max", 2)),
        price_query=check.get("query"), price_exclude=[str(w).lower() for w in check.get("exclude") or []],
        images=images_in(folder, (d.get("photos") or {}).get("order"), (d.get("photos") or {}).get("skip")),
        attributes=dict(d.get("attributes") or {}), condition=condition, purchase_price=purchase_price, facts=facts,
    )


def scan(source: Path, cat: Catalog | None = None) -> list[FolderState]:
    if not source.is_dir():
        raise ValueError(f"source_dir {source} does not exist; set it in ~/.config/kassist/config.yaml")
    states: list[FolderState] = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if (folder / LISTING_FILE).is_file():
            try:
                listing = load_listing(folder, cat)
                if not listing.images:
                    states.append(FolderState(folder, None, error="no photos"))
                    continue
                states.append(FolderState(folder, listing))
            except (ValueError, TypeError, yaml.YAMLError) as exc:
                states.append(FolderState(folder, None, error=str(exc)))
            continue
        drafts = sorted(folder.glob("*_inserat_*.md"))
        states.append(FolderState(folder, None, importable=drafts[-1] if drafts else None))
    return states


# --- import from the existing markdown drafts -------------------------------------------------

_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TARGET = re.compile(r"Zielpreis[^:\n]*:\s*(\d+)\s*EUR", re.IGNORECASE)
_START = re.compile(r"mit\s+(\d+)\s*EUR\s+einstellen", re.IGNORECASE)
_SKIP_SECTIONS = ("titel", "preis")


def _sections(text: str) -> list[tuple[str, str]]:
    parts = _SECTION.split(text)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def _norm(text: str) -> str:
    return " ".join(text.split())


def shorten_title(title: str, limit: int = MAX_TITLE) -> str:
    """Cut at the last word boundary within the limit, without dangling brackets or separators."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[: limit + 1].rsplit(" ", 1)[0] if " " in title[:limit] else title[:limit]
    cut = cut[:limit]
    if cut.count("(") > cut.count(")"):
        cut = cut[: cut.rfind("(")]
    return cut.rstrip(" -,/;:(")


def parse_draft_markdown(text: str, text_blocks: dict[str, str] | None = None) -> dict[str, Any]:
    """Turn a `*_inserat_*.md` draft into listing fields. Sections equal to a configured text block
    become a `texts` reference instead of item text. Raises ValueError if essentials are missing."""
    blocks = {_norm(v): k for k, v in (text_blocks or {}).items() if v.strip()}
    sections = _sections(text)
    title = next((body.splitlines()[0].strip() for name, body in sections
                  if name.lower().startswith("titel") and body), None)
    if not title:
        raise ValueError("no '## Titel' section")
    body_parts: list[str] = []
    texts: list[str] = []
    for name, body in sections:
        if name.lower().startswith(_SKIP_SECTIONS) or not body:
            continue
        if _norm(body) in blocks:
            texts.append(blocks[_norm(body)])
        else:
            body_parts.append(body)
    if not body_parts:
        raise ValueError("no text sections")
    prices = next((body for name, body in sections if name.lower().startswith("preis")), "")
    target = _TARGET.search(prices)
    start = _START.search(prices)
    if not target or not start:
        raise ValueError("no 'Zielpreis: X EUR … mit Y EUR einstellen' in '## Preise'")
    floor, begin = int(target.group(1)), int(start.group(1))
    if floor > begin:
        raise ValueError(f"target price {floor} above start price {begin}")
    fields: dict[str, Any] = {
        "approved": False,
        "title": shorten_title(title),
        "description": "\n\n".join(body_parts),
        "pricing": {"start": begin, "reduce_to": floor, "min_sale": floor},
        # always explicit: an empty list keeps the default blocks from being appended twice
        "texts": texts,
    }
    return fields


TEMPLATE = Path(__file__).with_name("listing_template.yaml")
_KEY_LINE = re.compile(r"^(\s*)([a-z_]+):(.*)$")
_MISSING = object()


def _lookup(values: dict[str, Any], path: list[str]) -> Any:
    cur: Any = values
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _scalar(value: Any) -> str:
    dumped = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10**6).strip()
    return dumped[:-3].strip() if dumped.endswith("...") else dumped


def render_listing(values: dict[str, Any], template: str | None = None) -> str:
    """Fill the commented template with values: the owner sees every field with its comment.
    Multi-line text becomes a `|` block; keys without a value keep the template line."""
    lines = (template if template is not None else TEMPLATE.read_text(encoding="utf-8")).splitlines()
    out: list[str] = []
    path: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _KEY_LINE.match(line)
        if not m or line.lstrip().startswith("#"):
            out.append(line)
            i += 1
            continue
        indent, key, rest = m.group(1), m.group(2), m.group(3)
        path = path[: len(indent) // 2] + [key]
        cm = re.search(r"\s+#.*$", rest)
        comment = cm.group(0) if cm else ""
        value_part = (rest[: cm.start()] if cm else rest).strip()
        block = value_part == "|"
        i += 1
        if block:   # skip the template's placeholder lines of the block
            while i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) > len(indent):  # blank lines stop it
                i += 1
        value = _lookup(values, path)
        has_children = i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) > len(indent) and not block
        if value is _MISSING or (isinstance(value, dict) and has_children):
            out.append(line)
            if block:
                out.append(indent + "  ...")
            continue
        if isinstance(value, str) and ("\n" in value or block):
            out.append(f"{indent}{key}: |{comment}")
            out.extend((indent + "  " + ln.rstrip()) if ln.strip() else "" for ln in value.strip("\n").splitlines())
            continue
        head = f"{indent}{key}: {_scalar(value)}"
        if comment:
            column = line.index("#", len(indent) + len(key) + 1)   # keep the template's comment column
            head += " " * max(1, column - len(head)) + comment.lstrip()
        out.append(head)
    return "\n".join(out) + "\n"


def load_raw(folder: Path) -> dict[str, Any]:
    """The item's own values (no defaults merged); {} if there is no listing.yaml yet."""
    path = folder / LISTING_FILE
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def load_draft(folder: Path, cat: Catalog | None = None) -> Listing | None:
    """The listing as far as it can be read while not ready for approval (e.g. category missing);
    for previews and price checks. None if there is no usable listing.yaml."""
    raw = {k: v for k, v in load_raw(folder).items() if k != "market"}
    if not raw:
        return None
    try:
        return listing_from_dict(folder, {**raw, "approved": False}, cat or Catalog())
    except (ValueError, TypeError):
        return None


def save_listing(folder: Path, values: dict[str, Any], cat: Catalog | None = None) -> Path:
    """Validate and write listing.yaml from the template; the kassist market block is kept."""
    values = {k: v for k, v in values.items() if k != "market"}
    listing_from_dict(folder, values, cat or Catalog())     # raises ValueError before anything is written
    path = folder / LISTING_FILE
    market = ""
    if path.is_file():
        old = path.read_text(encoding="utf-8")
        if MARKET_MARKER in old:
            market = "\n" + MARKET_MARKER + old.split(MARKET_MARKER, 1)[1]
    path.write_text(render_listing(values) + market, encoding="utf-8")
    return path


def import_draft(folder: Path, draft: Path, text_blocks: dict[str, str] | None = None) -> Path:
    """Write listing.yaml (the full commented template, filled) next to the draft. Never overwrites."""
    text = draft.read_text(encoding="utf-8")
    fields = parse_draft_markdown(text, text_blocks)
    header = (f"# Imported from {draft.name}. Check title, text and pricing (start, reduce_to, min_sale),\n"
              "# fill in what is missing, then set approved: true.\n")
    original = next((b.splitlines()[0].strip() for n, b in _sections(text) if n.lower().startswith("titel") and b), "")
    if " ".join(original.split()) != fields["title"]:
        header += f"# Title shortened to {MAX_TITLE} characters, original: {original}\n"
    return write_import(folder, fields, header)


def write_import(folder: Path, fields: dict[str, Any], header: str) -> Path:
    target = folder / LISTING_FILE
    if target.exists():
        raise FileExistsError(target)
    target.write_text(header + render_listing(fields), encoding="utf-8")
    return target


TEXT_SUFFIXES = (".md", ".txt")


def text_sources(folder: Path) -> list[Path]:
    """Text files in an item folder that can become the ad: structured drafts first."""
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
             and not p.name.startswith(".")]
    return sorted(files, key=lambda p: (not p.match("*_inserat_*.md"), p.name.lower()))


def plain_fields(text: str, folder_name: str, text_blocks: dict[str, str] | None = None) -> dict[str, Any]:
    """The file as it is: a structured draft is parsed, otherwise a short first line becomes the
    title and the rest the description. Prices stay empty."""
    with contextlib.suppress(ValueError):
        return parse_draft_markdown(text, text_blocks)
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("the file is empty")
    first, _, rest = text.partition("\n")
    first = first.strip().lstrip("#").strip()
    if 3 <= len(first) <= 80 and rest.strip():
        title, body = first, rest.strip()
    else:
        title, body = folder_name, text
    return {"approved": False, "title": shorten_title(title), "description": body}
