"""Normalize messagebox JSON into rows for the store. Tolerant: the web app's field names are not a contract."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return default


def conversation_row(raw: dict[str, Any]) -> dict[str, Any]:
    cid = _first(raw, "id", "conversationId")
    if not cid:
        raise ValueError("conversation without id")
    role = str(_first(raw, "role", default="")).lower()
    partner = _first(raw, "buyerName", "userNameBuyer") if role != "buyer" else _first(raw, "sellerName", "userNameSeller")
    partner = partner or _first(raw, "otherParticipantName", "counterPartyName", "email", default="?")
    unread = _first(raw, "unreadMessagesCount", "numUnreadMessages", default=None)
    if unread is None:
        unread = 1 if raw.get("unread") else 0
    ad_id = _first(raw, "adId", "ad_id")
    return {
        "id": str(cid),
        "ad_id": int(ad_id) if str(ad_id or "").isdigit() else None,
        "ad_title": _first(raw, "adTitle", "title", default=""),
        "partner": str(partner),
        "last_message_at": _first(raw, "receivedDate", "lastMessageDate", "lastModified", default=""),
        "unread": int(unread),
        "raw": json.dumps(raw, ensure_ascii=False)[:20000],
    }


def message_rows(cid: str, detail: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, m in enumerate(detail.get("messages") or []):
        text = _first(m, "text", "textShort", "body", default="")
        if not text:
            continue
        bound = str(_first(m, "boundness", "direction", default="")).upper()
        stable = hashlib.sha1(f"{cid}|{i}|{text}".encode()).hexdigest()[:16]
        mid = _first(m, "messageId", "id", default=f"{cid}:{stable}")
        rows.append({
            "id": str(mid),
            "conversation_id": cid,
            "outgoing": 1 if bound in ("OUTBOUND", "OUTGOING", "SENT") else 0,
            "text": str(text),
            "sent_at": _first(m, "receivedDate", "sentDate", "date", default=""),
        })
    return rows
