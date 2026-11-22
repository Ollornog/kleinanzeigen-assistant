"""AI help in the editor: improve the ad text, propose titles, propose categories.

Everything is a proposal; the GUI shows it and the owner decides. Answers are JSON; titles are cut
to Kleinanzeigen's 65 characters, categories are only taken from the known category list.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from .catalog import MAX_TITLE, shorten_title

Complete = Callable[[str], str]

TEXT_PROMPT = """You improve the text of a private second-hand ad on Kleinanzeigen (German classifieds).
Write in German, friendly and clear, short paragraphs, a short list for key facts and what is included.
Keep every fact; never invent features, condition details, accessories or prices. Remove repetitions.
Do NOT add payment, shipping or legal paragraphs (standard texts are appended separately), no greeting
formula at the end, no emojis, no markdown headings.

Title: {title}
Current text:
{body}

Answer as JSON: {{"text": "<improved text>"}}"""

TITLE_PROMPT = """Propose 3 titles for a private second-hand ad on Kleinanzeigen (German), each at most {limit}
characters. Put what buyers search for first: brand, model, type, key spec; condition at the end if
there is room ("wie neu", "neuwertig"). No exclamation marks, no capitals-only words, no emojis.
Use only facts from the text.

Current title: {title}
Text:
{body}

Answer as JSON: {{"titles": ["...", "...", "..."]}}"""

CATEGORY_PROMPT = """Choose the best Kleinanzeigen categories for this ad. Answer only with names copied exactly
from the list below, the best first, up to 3, most specific level that fits.

Title: {title}
Text:
{body}

Categories:
{names}

Answer as JSON: {{"categories": ["...", "...", "..."]}}"""


def _json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI answer contains no JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI answer is not an object")
    return data


def improve_text(complete: Complete, title: str, body: str) -> str:
    if not body.strip():
        raise ValueError("no text to improve")
    data = _json(complete(TEXT_PROMPT.format(title=title, body=body[:6000])))
    text = str(data.get("text") or "").strip()
    if not text:
        raise ValueError("AI returned no text")
    return text.replace("\r\n", "\n")


def propose_titles(complete: Complete, title: str, body: str) -> list[str]:
    if not (body.strip() or title.strip()):
        raise ValueError("no text to derive a title from")
    data = _json(complete(TITLE_PROMPT.format(limit=MAX_TITLE, title=title, body=body[:6000])))
    out: list[str] = []
    for t in data.get("titles") or []:
        t = shorten_title(" ".join(str(t).split()).strip(' "'))
        if len(t) >= 10 and t not in out:
            out.append(t)
    if not out:
        raise ValueError("AI returned no usable title")
    return out[:3]


def propose_categories(complete: Complete, title: str, body: str, names: Sequence[str]) -> list[str]:
    known = {n.lower(): n for n in names}
    data = _json(complete(CATEGORY_PROMPT.format(title=title, body=body[:3000], names="\n".join(names))))
    out: list[str] = []
    for c in data.get("categories") or []:
        name = known.get(" ".join(str(c).split()).lower())
        if name and name not in out:
            out.append(name)
    if not out:
        raise ValueError("AI returned no category from the list")
    return out[:3]


LISTING_PROMPT = """You turn the owner's notes into the fields of a private second-hand ad on Kleinanzeigen.
Use ONLY what the notes say. Anything they do not state is null. Never invent or estimate prices,
condition, accessories, shipping or dimensions.

- title: what buyers search for (brand, model, type, key spec), at most {limit} characters, from the notes.
- description: the ad text in German for buyers, built from the notes: keep every fact about the item,
  short paragraphs, a short list for key facts. Leave out the owner's internal notes (target or lowest
  price, to-dos, reminders). No payment/shipping/legal paragraphs, no greeting, no emojis, no headings.
- start_price: the asking price the notes name, whole euros, else null.
- floor_price: the lowest price / target price the notes name for reductions, else null.
- min_sale: the lowest price the owner would accept in chats, if named separately, else null.
- price_type: "NEGOTIABLE" (VB, Verhandlungsbasis), "FIXED" (Festpreis) or "GIVE_AWAY" (zu verschenken)
  only if the notes say so, else null.
- shipping: true if the notes offer shipping, false if they exclude it (nur Abholung), else null.
- shipping_cost: euros if named, else null.
- condition: one of {conditions} only if the notes describe the condition, else null
  (OVP/originalverpackt -> new_sealed, kaum benutzt/wie neu -> like_new, defekt -> defect).
- purchase_price: what the owner paid for it, if named, else null.
- notes: up to 3 short German hints on what the owner should still fill in.

Folder name: {folder}
Notes:
{text}

Answer as JSON: {{"title": ..., "description": ..., "start_price": ..., "floor_price": ..., "min_sale": ...,
"price_type": ..., "shipping": ..., "shipping_cost": ..., "condition": ..., "purchase_price": ..., "notes": [...]}}"""

PRICE_TYPES = ("NEGOTIABLE", "FIXED", "GIVE_AWAY")


def _stated(number: Any, text: str) -> float | None:
    """The number only if it is written in the notes (the model must not make prices up)."""
    try:
        value = float(str(number).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    whole = int(value) if value == int(value) else None
    forms = {f"{value:.2f}".replace(".", ","), f"{value:.2f}"}
    if whole is not None:
        forms.add(str(whole))
    if any(re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d])", text) for f in forms):
        return value
    return None


def extract_listing(complete: Complete, text: str, folder: str) -> tuple[dict[str, Any], list[str]]:
    """Listing fields from free notes, and hints for the owner. Prices the notes do not contain are
    dropped in code, whatever the model answers."""
    if not text.strip():
        raise ValueError("the file is empty")
    from .catalog import CONDITIONS
    data = _json(complete(LISTING_PROMPT.format(limit=MAX_TITLE, folder=folder, text=text[:8000],
                                                conditions=" | ".join(CONDITIONS))))
    hints = [str(n).strip() for n in (data.get("notes") or []) if str(n).strip()][:3]
    title = shorten_title(" ".join(str(data.get("title") or "").split()).strip(' "')) or shorten_title(folder)
    description = str(data.get("description") or "").strip().replace("\r\n", "\n") or text.strip()
    fields: dict[str, Any] = {"approved": False, "title": title, "description": description}
    prices = {k: _stated(data.get(k), text) if data.get(k) is not None else None
              for k in ("start_price", "floor_price", "min_sale")}
    for k in prices:
        if data.get(k) is not None and prices[k] is None:
            hints.append(f"{k}: {data.get(k)} steht nicht im Text – weggelassen")
    start, floor, min_sale = (int(prices[k]) if prices[k] is not None else None
                              for k in ("start_price", "floor_price", "min_sale"))
    if start is not None and floor is not None and floor > start:
        floor = None
    if start is not None and min_sale is not None and min_sale > start:
        min_sale = None
    pricing: dict[str, Any] = {}
    if start is not None:
        pricing["start"] = start
    if floor is not None:
        pricing["reduce_to"] = floor
    if min_sale is not None or floor is not None:
        pricing["min_sale"] = min_sale if min_sale is not None else floor
    if data.get("price_type") in PRICE_TYPES:
        pricing["type"] = data["price_type"]
    if pricing:
        fields["pricing"] = pricing
    shipping: dict[str, Any] = {}
    if isinstance(data.get("shipping"), bool):
        shipping["enabled"] = data["shipping"]
    if data.get("shipping_cost") is not None:
        cost = _stated(data["shipping_cost"], text)
        if cost is not None:
            shipping["cost"] = cost
    if shipping:
        fields["shipping"] = shipping
    facts: dict[str, Any] = {}
    if data.get("condition") in CONDITIONS:
        facts["condition"] = {"value": data["condition"], "show": True}
    if data.get("purchase_price") is not None:
        paid = _stated(data["purchase_price"], text)
        if paid is not None:
            # kept as context; the owner decides whether it goes into the text
            facts["purchase_price"] = {"value": int(paid) if paid == int(paid) else paid, "show": False}
    if facts:
        fields["facts"] = facts
    return fields, hints
