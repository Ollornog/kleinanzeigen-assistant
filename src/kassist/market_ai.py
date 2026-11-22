"""AI cross-check of a price search: condition and contents of each comparable ad against ours.

The LLM gets our listing and the search results (title, asking price, VB, text snippet) and
returns, per offer, whether it is comparable, its condition, what differs in the bundle and an
adjusted price for our item's contents. From that: median of adjusted prices and a suggestion
for start / reduce_to / min_sale. Output is advice; nothing is changed without the owner.
"""
from __future__ import annotations

import json
import re
import statistics
from typing import Any, Callable, Sequence

from .prices import Offer, _quantile

PROMPT = """You compare second-hand offers on Kleinanzeigen (German classifieds) with an item we want to sell.

OUR ITEM
Title: {title}
Text:
{body}

OFFERS (asking prices, not sale prices; "VB" = negotiable)
{offers}

Step 1 – our item: write down its key specs that decide whether another offer is an alternative for a buyer
(for example ports, number of monitors, connection type, size, capacity, model/generation) and its brand
("no-name" if the text names none).

Step 2 – each offer:
- specs: its key specs as far as title and text show them (same keys as step 1).
- comparable: true only if it is the same kind of product AND every key spec from step 1 matches (more
  ports, fewer monitors, other connection types etc. = false). Brand does not decide comparability.
  False as well for accessories or parts only, defective items, wanted ads ("suche"), and bundles where our
  item is only a small part.
- condition: one of new, like_new, good, used, defective, unknown.
- diff: what differs from ours (brand, accessories, cables, box), German, max 12 words.
- adjusted: the asking price this offer would have for OUR item, whole euros (null if not comparable):
  remove included extras (cables, spare batteries …) at realistic part prices, add missing parts, adjust for
  condition, and for brand: if ours is no-name and the offer is a known brand, a no-name item usually asks
  only 40-60 % of the brand price.

Step 3 – suggest prices for OUR item: start (asking price to publish), reduce_to (lowest automatic price after
weekly reductions), min_sale (lowest price to accept in chats), with min_sale <= reduce_to <= start.
Asking prices on Kleinanzeigen are usually 10-20 % above final sale prices.

Answer with JSON only, no prose, in exactly this shape:
{{"ours": {{"specs": "...", "brand": "..."}},
  "offers": [{{"id": "...", "specs": "...", "comparable": true, "condition": "good", "diff": "...", "adjusted": 123}}],
  "suggestion": {{"start": 0, "reduce_to": 0, "min_sale": 0}},
  "note": "one or two German sentences on the market and the suggestion"}}"""


def build_prompt(title: str, body: str, offers: Sequence[Offer], limit: int = 60) -> str:
    lines = []
    for o in [o for o in offers if o.price][:limit]:
        price = f"{o.price} EUR" + (" VB" if o.negotiable else "")
        lines.append(f"- id={o.id} | {price} | {o.title} | {o.snippet[:300]}")
    return PROMPT.format(title=title, body=body[:3000], offers="\n".join(lines) or "(none)")


def parse_answer(text: str) -> dict[str, Any]:
    """The first JSON object in the answer (models sometimes wrap it in a code fence)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("AI answer contains no JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        raise ValueError("AI answer has no offers list")
    return data


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    adjusted = sorted(int(o["adjusted"]) for o in data["offers"]
                      if o.get("comparable") and isinstance(o.get("adjusted"), (int, float)) and o["adjusted"] > 0)
    out: dict[str, Any] = {"comparable": len(adjusted)}
    if len(adjusted) >= 4:                      # same outlier rule as the search summary (1.5 × IQR)
        q1, q3 = _quantile(adjusted, 0.25), _quantile(adjusted, 0.75)
        kept = [a for a in adjusted if q1 - 1.5 * (q3 - q1) <= a <= q3 + 1.5 * (q3 - q1)]
        out["outliers"] = len(adjusted) - len(kept)
        adjusted = kept
    if adjusted:
        out.update({"min": adjusted[0], "median": round(statistics.median(adjusted)), "max": adjusted[-1]})
    ours = data.get("ours") or {}
    if isinstance(ours, dict) and ours.get("specs"):
        specs = ours["specs"]
        text = ", ".join(f"{k}: {v}" for k, v in specs.items()) if isinstance(specs, dict) else str(specs)
        out["ours"] = text[:240] + (f" · {ours['brand']}" if ours.get("brand") else "")
    sug = data.get("suggestion") or {}
    if all(isinstance(sug.get(k), (int, float)) for k in ("start", "reduce_to", "min_sale")):
        s = {k: int(sug[k]) for k in ("start", "reduce_to", "min_sale")}
        out["suggestion" if s["min_sale"] <= s["reduce_to"] <= s["start"] else "suggestion_raw"] = s
    if data.get("note"):
        out["note"] = str(data["note"])[:400]
    return out


def check(complete: Callable[[str], str], title: str, body: str, offers: Sequence[Offer]) -> dict[str, Any]:
    """`complete(prompt) -> text` is the language model (see llm.complete)."""
    data = parse_answer(complete(build_prompt(title, body, offers)))
    result = summarize(data)
    result["per_offer"] = {str(o.get("id")): {k: o.get(k) for k in ("comparable", "specs", "condition", "diff", "adjusted")}
                           for o in data["offers"] if o.get("id") is not None}
    return result
