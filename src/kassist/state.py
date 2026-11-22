"""Local state in SQLite: item lifecycle, inbox copy, reply drafts, event log."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY,           -- folder name
  status TEXT NOT NULL DEFAULT 'new',  -- new | online | reserved | expired | sold | withdrawn
  ad_id INTEGER,                  -- current Kleinanzeigen ad id
  price INTEGER,                  -- price currently online
  schedule_origin TEXT,           -- day the price schedule counts from (ISO date)
  published_on TEXT,              -- day of the current publication
  end_date TEXT,                  -- expiry as reported by Kleinanzeigen
  expired_on TEXT,                -- day we noticed the ad is gone
  publish_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  published_hash TEXT,            -- content of the ad as last sent to Kleinanzeigen
  approved_hash TEXT,             -- staged changes the owner approved for sending
  published_snapshot TEXT,        -- JSON of that content, to show which fields changed
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  ad_id INTEGER,
  ad_title TEXT,
  partner TEXT,
  last_message_at TEXT,
  unread INTEGER NOT NULL DEFAULT 0,
  raw TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  outgoing INTEGER NOT NULL,      -- 1 = sent by us
  text TEXT NOT NULL,
  sent_at TEXT,
  notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',  -- open | sent | discarded
  created_at TEXT NOT NULL,
  for_message_id TEXT,
  sent_by TEXT                    -- owner | auto
);
CREATE TABLE IF NOT EXISTS price_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT,
  query TEXT NOT NULL,
  at TEXT NOT NULL,
  summary TEXT NOT NULL,          -- JSON
  offers TEXT NOT NULL            -- JSON list
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  item_key TEXT,
  kind TEXT NOT NULL,
  detail TEXT
);
"""


@dataclass
class ItemState:
    key: str
    status: str = "new"
    ad_id: int | None = None
    price: int | None = None
    schedule_origin: date | None = None
    published_on: date | None = None
    end_date: date | None = None
    expired_on: date | None = None
    publish_count: int = 0
    last_error: str | None = None
    published_hash: str | None = None
    approved_hash: str | None = None
    published_snapshot: str | None = None


_DATE_FIELDS = {"schedule_origin", "published_on", "end_date", "expired_on"}


def _to_row(item: ItemState) -> dict[str, Any]:
    row = {f.name: getattr(item, f.name) for f in fields(ItemState)}
    for name in _DATE_FIELDS:
        row[name] = row[name].isoformat() if row[name] else None
    return row


def _from_row(row: sqlite3.Row) -> ItemState:
    data = {f.name: row[f.name] for f in fields(ItemState)}
    for name in _DATE_FIELDS:
        data[name] = date.fromisoformat(data[name]) if data[name] else None
    return ItemState(**data)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        for col in ("published_hash", "approved_hash", "published_snapshot"):    # databases from 0.1 drafts
            if col not in cols:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
        if "sent_by" not in {r[1] for r in self.db.execute("PRAGMA table_info(drafts)")}:
            self.db.execute("ALTER TABLE drafts ADD COLUMN sent_by TEXT")

    def close(self) -> None:
        self.db.close()

    # items
    def item(self, key: str) -> ItemState:
        row = self.db.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
        return _from_row(row) if row else ItemState(key=key)

    def items(self) -> list[ItemState]:
        return [_from_row(r) for r in self.db.execute("SELECT * FROM items ORDER BY key")]

    def save_item(self, item: ItemState) -> None:
        row = _to_row(item)
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "key")
        with self.db:
            self.db.execute(f"INSERT INTO items ({cols}) VALUES ({marks}) ON CONFLICT(key) DO UPDATE SET {updates}", row)

    def delete_item(self, key: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM items WHERE key = ?", (key,))

    def log(self, kind: str, item_key: str | None = None, detail: str | None = None) -> None:
        with self.db:
            self.db.execute("INSERT INTO events (at, item_key, kind, detail) VALUES (?, ?, ?, ?)",
                            (datetime.now().isoformat(timespec="seconds"), item_key, kind, detail))

    def events(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # price checks
    def add_price_check(self, item_key: str | None, query: str, summary: dict, offers: list[dict]) -> None:
        with self.db:
            self.db.execute("INSERT INTO price_checks (item_key, query, at, summary, offers) VALUES (?, ?, ?, ?, ?)",
                            (item_key, query, datetime.now().isoformat(timespec="seconds"),
                             json.dumps(summary), json.dumps(offers, ensure_ascii=False)))

    def last_price_check(self, item_key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM price_checks WHERE item_key = ? ORDER BY id DESC LIMIT 1",
                              (item_key,)).fetchone()
        if row is None:
            return None
        return {"query": row["query"], "at": row["at"], "summary": json.loads(row["summary"]),
                "offers": json.loads(row["offers"])}

    # inbox
    def upsert_conversation(self, conv: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO conversations (id, ad_id, ad_title, partner, last_message_at, unread, raw)
                   VALUES (:id, :ad_id, :ad_title, :partner, :last_message_at, :unread, :raw)
                   ON CONFLICT(id) DO UPDATE SET ad_id=excluded.ad_id, ad_title=excluded.ad_title,
                     partner=excluded.partner, last_message_at=excluded.last_message_at,
                     unread=excluded.unread, raw=excluded.raw""", conv)

    def add_message(self, msg: dict[str, Any]) -> bool:
        """Insert a message; True if it was new."""
        with self.db:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO messages (id, conversation_id, outgoing, text, sent_at)
                   VALUES (:id, :conversation_id, :outgoing, :text, :sent_at)""", msg)
        return cur.rowcount == 1

    def conversations(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM conversations ORDER BY last_message_at DESC").fetchall()

    def conversation(self, cid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()

    def messages(self, cid: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY sent_at, id",
                               (cid,)).fetchall()

    def unnotified(self) -> Iterator[sqlite3.Row]:
        return iter(self.db.execute(
            """SELECT m.*, c.ad_title, c.partner FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE m.notified = 0 AND m.outgoing = 0 ORDER BY m.sent_at""").fetchall())

    def mark_notified(self, message_ids: list[str]) -> None:
        with self.db:
            self.db.executemany("UPDATE messages SET notified = 1 WHERE id = ?", [(m,) for m in message_ids])

    def add_draft(self, cid: str, text: str, for_message_id: str | None) -> int:
        with self.db:
            self.db.execute("UPDATE drafts SET status='discarded' WHERE conversation_id=? AND status='open'", (cid,))
            cur = self.db.execute(
                "INSERT INTO drafts (conversation_id, text, created_at, for_message_id) VALUES (?, ?, ?, ?)",
                (cid, text, datetime.now().isoformat(timespec="seconds"), for_message_id))
        return int(cur.lastrowid)

    def open_draft(self, cid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM drafts WHERE conversation_id=? AND status='open' ORDER BY id DESC",
                               (cid,)).fetchone()

    def set_draft_status(self, draft_id: int, status: str, sent_by: str | None = None) -> None:
        with self.db:
            self.db.execute("UPDATE drafts SET status=?, sent_by=COALESCE(?, sent_by) WHERE id=?",
                            (status, sent_by, draft_id))

    def auto_replies(self, cid: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM drafts WHERE conversation_id=? AND sent_by='auto'",
                                   (cid,)).fetchone()[0])
