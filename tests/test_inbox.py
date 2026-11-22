import stat

import pytest
import yaml
from fastapi.testclient import TestClient

from kassist import config, drafts, engine
from kassist.catalog import Listing
from kassist.config import DEFAULTS, Settings, _merge
from kassist.inbox import conversation_row, message_rows
from kassist.pricing import PriceSchedule
from kassist.state import ItemState, Store


def test_conversation_row_seller_view_and_fallbacks():
    row = conversation_row({"id": "c1", "role": "Seller", "buyerName": "Anna", "adId": "123", "adTitle": "Helm",
                            "receivedDate": "2026-11-22T10:00:00", "unread": True})
    assert row["partner"] == "Anna" and row["ad_id"] == 123 and row["unread"] == 1
    with pytest.raises(ValueError):
        conversation_row({"buyerName": "x"})


def test_message_rows_direction_and_stable_ids():
    detail = {"messages": [{"messageId": "m1", "text": "Noch da?", "boundness": "INBOUND"},
                           {"text": "Ja", "boundness": "OUTBOUND"}, {"text": ""}]}
    rows = message_rows("c1", detail)
    assert [(r["id"], r["outgoing"]) for r in rows][0] == ("m1", 0)
    assert rows[1]["outgoing"] == 1 and len(rows) == 2
    assert message_rows("c1", detail)[1]["id"] == rows[1]["id"]


def test_store_messages_dedupe_and_notify(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    store.upsert_conversation(conversation_row({"id": "c1", "buyerName": "Anna", "adTitle": "Helm"}))
    msg = {"id": "m1", "conversation_id": "c1", "outgoing": 0, "text": "Hallo", "sent_at": "1"}
    assert store.add_message(msg) and not store.add_message(msg)
    assert [m["id"] for m in store.unnotified()] == ["m1"]
    store.mark_notified(["m1"])
    assert list(store.unnotified()) == []
    first = store.add_draft("c1", "A", "m1")
    store.add_draft("c1", "B", "m1")
    assert store.open_draft("c1")["text"] == "B"
    assert first != store.open_draft("c1")["id"]


def test_prompt_contains_price_rules_but_no_leak_instruction_missing():
    listing = Listing(folder=tmp_folder(), title="Helm", body="Größe S", description="Größe S",
                      schedule=PriceSchedule(100, 90), min_sale=85)
    prompt = drafts.build_prompt(listing, ItemState("Helm", status="online", price=95),
                                 [{"outgoing": 0, "text": "Gehen 60?"}])
    assert "95 EUR" in prompt and "85 EUR" in prompt and "Never mention, hint at or offer 85 EUR" in prompt
    assert prompt.rstrip().endswith("Reply to the last buyer message.") and "Buyer: Gehen 60?" in prompt


def tmp_folder():
    from pathlib import Path
    return Path("/nonexistent/Helm")


def test_draft_runs_command_with_prompt_on_stdin():
    assert drafts.draft(["cat"], "hallo") == "hallo"
    with pytest.raises(RuntimeError):
        drafts.draft(["false"], "x")


def test_secrets_file_must_be_private(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("KASSIST_KA_USER=a@b\nKASSIST_KA_PASSWORD='x y'\n# c\n")
    p.chmod(0o644)
    with pytest.raises(PermissionError):
        config.load_secrets(p)
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert config.load_secrets(p) == {"KASSIST_KA_USER": "a@b", "KASSIST_KA_PASSWORD": "x y"}


def test_old_database_gets_new_item_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE items (key TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'new', ad_id INTEGER,
                  price INTEGER, schedule_origin TEXT, published_on TEXT, end_date TEXT, expired_on TEXT,
                  publish_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at TEXT)""")
    db.execute("INSERT INTO items (key, status, ad_id) VALUES ('KVM', 'online', 5)")
    db.commit()
    db.close()
    store = Store(path)
    item = store.item("KVM")
    assert item.ad_id == 5 and item.published_hash is None
    from dataclasses import replace
    store.save_item(replace(item, published_hash="abc"))
    assert store.item("KVM").published_hash == "abc"


def _conv_with_buyer_message(store, cid="c1", text="Noch da?"):
    store.upsert_conversation(conversation_row({"id": cid, "buyerName": "Anna", "adTitle": "Helm"}))
    store.add_message({"id": f"{cid}-m1", "conversation_id": cid, "outgoing": 0, "text": text, "sent_at": "1"})


@pytest.mark.parametrize("mode, draft_text, sent", [
    ("off", "Ja, ist noch da.", False),
    ("draft", "Ja, ist noch da.", False),
    ("auto", "Ja, ist noch da.", True),
    ("auto", "[SCAM?]\nNur Abholung.", False),
    ("auto", "[OWNER]\nOkay, 40 € passt, morgen 18 Uhr?", False),
])
def test_reply_modes(tmp_path, monkeypatch, mode, draft_text, sent):
    settings = config.Settings(config._merge(config.DEFAULTS, {"data_dir": str(tmp_path), "replies": {"mode": mode, "max_auto": 3}}))
    store = Store(settings.db_path)
    _conv_with_buyer_message(store)
    monkeypatch.setattr(engine, "_poll", lambda s, st, force: _noop())
    made, sends = [], []

    def fake_make_draft(s, st, cid):
        made.append(cid)
        st.add_draft(cid, draft_text, f"{cid}-m1")
        return draft_text
    monkeypatch.setattr(engine, "make_draft", fake_make_draft)
    monkeypatch.setattr(engine, "send_reply", lambda s, st, cid, text, draft_id=None, sent_by="owner": sends.append((text, sent_by)))
    engine.poll_inbox(settings, store)
    assert made == ([] if mode == "off" else ["c1"])
    assert bool(sends) is sent
    if sent:
        assert sends == [(draft_text, "auto")]
    store.close()


def test_auto_replies_stop_after_the_limit(tmp_path, monkeypatch):
    settings = config.Settings(config._merge(config.DEFAULTS, {"data_dir": str(tmp_path), "replies": {"mode": "auto", "max_auto": 2}}))
    store = Store(settings.db_path)
    _conv_with_buyer_message(store)
    for n in range(2):
        did = store.add_draft("c1", f"Antwort {n}", "c1-m1")
        store.set_draft_status(did, "sent", "auto")
    assert store.auto_replies("c1") == 2
    sends = []
    monkeypatch.setattr(engine, "send_reply", lambda *a, **k: sends.append(a))
    assert engine.auto_reply(settings, store, "c1", "Noch eine Antwort") is False and sends == []
    store.close()


def test_markers_and_strip():
    assert drafts.markers("[SCAM?]\n[OWNER] x") == ["[SCAM?]", "[OWNER]"]
    assert drafts.markers("Ganz normal [OWNER] im Text weiter hinten, nicht am Anfang, sondern später.") == []
    assert drafts.strip_markers("[OWNER]\n[SCAM?] Hallo") == "Hallo"


async def _noop():
    return []



def test_counter_offer_is_two_thirds_up_from_the_floor():
    assert drafts.counter_offer(49, 35) == 44
    assert drafts.counter_offer(100, 90) == 97
    assert drafts.counter_offer(40, 40) == 40
    listing = Listing(folder=tmp_folder(), title="KVM", body="x", description="x", schedule=PriceSchedule(49, 40), min_sale=35)
    assert "exactly 44 EUR" in drafts.build_prompt(listing, ItemState("KVM", price=49), [{"outgoing": 0, "text": "30?"}])
