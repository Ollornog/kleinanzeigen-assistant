"""Reply drafts. Markers at the start: [SCAM?] (suspicious) and [OWNER] (needs the owner's decision);
drafts with a marker are never sent automatically."""
from __future__ import annotations

import subprocess
from typing import Any, Sequence

PROMPT = """You draft replies for a private seller on Kleinanzeigen (German classifieds).
Write ONLY the reply text, in the buyer's language (usually German), friendly, short, no greeting
formulas beyond one line, no signature placeholders. Never invent facts that are not in the listing.

Rules:
- Price: the current asking price is {price} EUR; the lowest acceptable price is {floor} EUR (secret).
  Never mention, hint at or offer {floor} EUR unless the buyer already offered it or more.
  Offer at or above {floor}: accept, or meet in the middle if it is far below the asking price.
  Offer below {floor}: decline friendly and counter with exactly {counter} EUR.
- Status: the item is {status}. If reserved or sold, say so kindly.
- Scam patterns (shipping via a link, "PayPal Freunde" requests from the buyer side, courier pickup,
  paying more than the price, moving to WhatsApp/e-mail with links): answer neutrally, propose
  pickup with cash or the platform's own payment, and start your answer with the line [SCAM?].
- Questions the listing does not answer: say you will check and get back, and start with the line [OWNER].
- Never propose times, places or addresses yourself. If the buyer wants to meet or pick up, ask for their
  preferred day and time and start with the line [OWNER].
- Start with the line [OWNER] whenever the reply agrees to a sale or a final price at or above {floor} EUR,
  or shares an address or phone number.
- Do NOT add [OWNER] for: confirming the item is still available, answering from the listing text,
  declining an offer, or a counter-offer following the price rule above.

Listing title: {title}
Listing text:
{description}

Seller's notes (not in the ad; facts you may use when the buyer asks, never invent beyond them):
{notes}

Conversation (oldest first):
{history}

Reply to the last buyer message."""


def counter_offer(price: int, floor: int) -> int:
    """Two thirds of the way from the secret lowest price up to the asking price, whole euros."""
    if price <= floor:
        return price
    return int(floor + (price - floor) * 2 / 3 + 0.5)


def build_prompt(listing: Any, item: Any, messages: Sequence[Any]) -> str:
    history = "\n".join(("Seller: " if m["outgoing"] else "Buyer: ") + m["text"] for m in messages)
    price = item.price if item and item.price is not None else listing.schedule.start
    return PROMPT.format(
        price=price,
        floor=listing.min_sale,
        counter=counter_offer(price, listing.min_sale),
        status=item.status if item else "online",
        title=listing.title,
        description=listing.description[:4000],
        notes="\n".join(f"- {label}: {value}" for label, value, shown in getattr(listing, "facts", []) if not shown) or "-",
        history=history[-6000:],
    )


def draft(command: Sequence[str], prompt: str, timeout: int = 180) -> str:
    """Run a local LLM command (kept for callers; see llm.complete for the configured provider)."""
    from .llm import run_command
    return run_command(command, prompt, timeout)


MARKERS = ("[SCAM?]", "[OWNER]")


def markers(text: str) -> list[str]:
    """Markers at the very start (one after another), in order."""
    found, rest = [], text.strip()
    while True:
        m = next((m for m in MARKERS if rest.startswith(m)), None)
        if m is None:
            return found
        found.append(m)
        rest = rest[len(m):].lstrip()


def strip_markers(text: str) -> str:
    out = text.strip()
    changed = True
    while changed:
        changed = False
        for m in MARKERS:
            if out.startswith(m):
                out = out[len(m):].lstrip()
                changed = True
    return out
