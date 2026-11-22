"""GUI: pages render, forms round-trip into listing.yaml / config, guards hold. No browser, no network."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kassist import catalog, config, engine
from kassist.config import DEFAULTS, Settings, _merge
from kassist.gui import create_app, forms, i18n
from kassist.inbox import conversation_row
from kassist.state import Store

DE = {"accept-language": "de-DE"}


@pytest.fixture
def gui(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "Helm").mkdir(parents=True)
    (src / "Helm" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (src / "Helm" / "listing.yaml").write_text(
        "approved: false\ntitle: Shark Helm Größe S\ndescription: Kaum getragen.\npricing: {start: 100, reduce_to: 90}\n",
        encoding="utf-8")
    (src / "Neu").mkdir()
    (src / "Neu" / "x_inserat_v1.md").write_text(
        "## Titel\nKVM-Switch 2x2 wie neu\n\n## Verkaufstext\nText.\n\n## Preise\n- Zielpreis: 42 EUR (mit 49 EUR einstellen)\n",
        encoding="utf-8")
    cfg_path = tmp_path / "config.yaml"
    raw = _merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data"),
                            "sellers": {"alex": {"name": "Alex", "location": {"zipcode": "10115", "city": "Berlin"}}},
                            "text_blocks": {"legal": "Privatverkauf."},
                            "listing_defaults": {**DEFAULTS["listing_defaults"], "seller": "alex", "texts": ["legal"]}})
    config.save(raw, cfg_path)
    settings = config.load(cfg_path)
    monkeypatch.setattr(engine, "notify", lambda *a: None)
    return TestClient(create_app(settings, cfg_path)), settings, src, cfg_path


def test_i18n_tables_match():
    assert set(i18n.EN) == set(i18n.DE)
    assert i18n.pick("de-AT,de;q=0.9") == "de" and i18n.pick("en-US") == "en" and i18n.pick(None) == "en"


def test_pages_render_with_c22_assets(gui):
    c, settings, src, _ = gui
    for url in ("/", "/items/Helm", "/items/Neu", "/settings", "/settings/browse", "/inbox", "/log"):
        r = c.get(url, headers=DE)
        assert r.status_code == 200, url
        assert '/static/c22/css/c22.css' in r.text
    assert "Text stumpf übernehmen" in c.get("/items/Neu", headers=DE).text
    assert c.get("/static/c22/css/c22.css").status_code == 200
    assert c.get("/static/c22/js/basecoat.all.min.js").status_code == 200


def test_items_page_is_a_skeleton_and_rows_come_as_fragment(gui):
    c, settings, src, _ = gui
    from PIL import Image
    Image.new("RGB", (1600, 1200), (200, 30, 30)).save(src / "Helm" / "a.jpg", "JPEG")
    page = c.get("/", headers=DE).text
    assert 'class="ka-thumb skeleton"' in page and 'data-src="/fragments/items"' in page and "Shark" not in page
    rows = c.get("/fragments/items", headers=DE).text
    assert "/thumb/Helm/a.jpg?s=160" in rows and "noch keine Anzeige" in rows and "neu" in rows
    r = c.get("/thumb/Helm/a.jpg?s=160")
    small = Image.open(__import__("io").BytesIO(r.content))
    assert r.status_code == 200 and max(small.size) == 160
    cached = list((settings.data_dir / "thumbs").glob("*.jpg"))
    assert len(cached) == 1
    c.get("/thumb/Helm/a.jpg?s=150")                       # snaps to 160: same cache file
    assert len(list((settings.data_dir / "thumbs").glob("*.jpg"))) == 1
    assert c.get("/photo/Helm/a.jpg").content == (src / "Helm" / "a.jpg").read_bytes()
    assert c.get("/thumb/Helm/..%2F..%2Fconfig.yaml").status_code == 404
    (src / "Helm" / "b.jpg").write_bytes(b"not an image")
    assert c.get("/thumb/Helm/b.jpg").content == b"not an image"     # broken image: original as fallback


def test_editor_save_round_trips_all_fields_and_keeps_market(gui):
    c, settings, src, _ = gui
    catalog.write_market(src / "Helm", {"at": "2026-11-22 10:00", "query": "q", "offers": 3, "used": 3,
                                        "min": 80, "p25": 85, "median": 90, "p75": 95, "max": 99})
    form = {"approved": "on", "title": "Shark Helm Größe S", "description": "Zeile 1\r\n\r\nZeile 3",
            "texts": ["legal"], "seller": "alex", "category": "Auto, Rad & Boot > Motorradteile",
            "pricing.start": "110", "pricing.reduce_to": "95", "pricing.min_sale": "90", "pricing.type": "FIXED",
            "pricing.schedule.step": "10%", "fixed_step": "on", "availability.until": "2027-03-31", "shipping.enabled": "true",
            "shipping.cost": "6,19", "republish.enabled": "false", "price_check.exclude": "visier, pinlock",
            "attributes": "condition: like_new"}
    r = c.post("/items/Helm", data=form, follow_redirects=False)
    assert r.status_code == 303
    text = (src / "Helm" / "listing.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["approved"] is True and data["description"] == "Zeile 1\n\nZeile 3\n"
    assert data["pricing"] == {"start": 110, "reduce_to": 95, "min_sale": 90, "type": "FIXED",
                               "schedule": {"hold_days": None, "steps": None, "step": "10%", "every_days": None}}
    assert data["shipping"] == {"enabled": True, "cost": 6.19} and data["republish"]["enabled"] is False
    assert data["price_check"]["exclude"] == ["visier", "pinlock"] and data["attributes"] == {"condition": "like_new"}
    assert data["market"][0]["median"] == 90 and text.count(catalog.MARKET_MARKER) == 1
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert li.approved and li.schedule.hold_days == 14 and li.description.endswith("Privatverkauf.")
    page = c.get("/items/Helm", headers=DE).text
    assert 'value="110"' in page and "6.19" in page and "visier, pinlock" in page


def test_editor_rejects_invalid_values_without_writing(gui):
    c, settings, src, _ = gui
    before = (src / "Helm" / "listing.yaml").read_text(encoding="utf-8")
    r1 = c.post("/items/Helm", data={"title": "Helm Größe S", "description": "x", "pricing.start": "50",
                                      "pricing.reduce_to": "60"})
    r2 = c.post("/items/Helm", data={"title": "Helm Größe S", "description": "x", "pricing.start": "abc"})
    assert (src / "Helm" / "listing.yaml").read_text(encoding="utf-8") == before
    assert "floor" in r1.text and "not a number" in r2.text           # error shown after the redirect


def test_import_template_new_item_and_photo_upload(gui):
    c, settings, src, _ = gui
    c.post("/items/Neu/import")
    assert catalog.load_listing(src / "Neu", settings.catalog()).schedule.start == 49
    c.post("/items/new", data={"name": "Rasierer Braun"})
    assert (src / "Rasierer Braun").is_dir()
    c.post("/items/Rasierer Braun/template")
    assert "pricing:" in (src / "Rasierer Braun" / "listing.yaml").read_text(encoding="utf-8")
    c.post("/items/Rasierer Braun/photos", files=[("files", ("b.jpg", b"\xff\xd8", "image/jpeg")),
                                                   ("files", ("evil.sh", b"#!", "text/plain")),
                                                   ("files", ("../../x.jpg", b"\xff\xd8", "image/jpeg"))])
    names = sorted(p.name for p in (src / "Rasierer Braun").iterdir())
    assert names == ["b.jpg", "listing.yaml", "x.jpg"] and not (src.parent / "x.jpg").exists()


def test_path_tricks_are_refused(gui):
    c, settings, src, _ = gui
    (src.parent / "secret.jpg").write_bytes(b"x")
    assert c.get("/photo/..%2F/secret.jpg").status_code == 404
    assert c.get("/photo/Helm/..%2F..%2Fsecret.jpg").status_code == 404
    assert c.post("/items/new", data={"name": "../out"}, follow_redirects=False).status_code == 303
    assert not (src.parent / "out").exists()
    assert c.post("/items/.hidden/template", follow_redirects=False).status_code == 303


def test_host_and_origin_guard(gui):
    c, *_ = gui
    assert c.get("/", headers={"host": "evil.example"}).status_code == 403
    r = c.post("/items/new", data={"name": "X"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post("/items/new", data={"name": "Y"}, headers={"origin": "http://127.0.0.1:8765"},
                  follow_redirects=False).status_code == 303


def test_settings_save_and_folder_choice(gui, tmp_path):
    c, settings, src, cfg_path = gui
    data = {"source_dir": str(src), "seller.key": ["alex", "sam", ""], "seller.name": ["Alex", "Sam", ""],
            "seller.zipcode": ["10115", "", ""], "seller.city": ["Berlin", "", ""],
            "block.key": ["legal", "pay", ""], "block.text": ["Privatverkauf.", "Bar oder PayPal.", ""],
            "d.seller": "sam", "d.texts": ["legal", "pay"], "d.type": "NEGOTIABLE", "d.hold_days": "7", "d.step": "3", "d.steps": "4",
            "d.every_days": "7", "d.after_days": "14", "d.max": "1", "d.republish": "on",
            "l.max_publish": "2", "l.max_actions": "5", "l.pause_min": "30", "l.pause_max": "90",
            "llm.command": "claude -p --model sonnet"}
    assert c.post("/settings", data=data, follow_redirects=False).status_code == 303
    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert saved["sellers"]["sam"] == {"name": "Sam"} and saved["text_blocks"]["pay"] == "Bar oder PayPal."
    assert saved["listing_defaults"]["seller"] == "sam" and saved["listing_defaults"]["texts"] == ["legal", "pay"]
    assert saved["limits"] == {"max_publish": 2, "max_actions": 5, "pause_seconds": [30, 90]}
    assert settings.raw["listing_defaults"]["pricing"]["schedule"]["hold_days"] == 7     # reloaded in place
    assert settings.raw["listing_defaults"]["pricing"]["schedule"]["steps"] == 4
    c.post("/settings", data=dict(data, **{"d.steps": ""}))
    assert settings.raw["listing_defaults"]["pricing"]["schedule"]["steps"] == 3          # empty = default (3 equal steps)
    bad = dict(data, **{"d.seller": "nobody"})
    c.post("/settings", data=bad)
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["listing_defaults"]["seller"] == "sam"
    other = tmp_path / "other"
    other.mkdir()
    (other / "Sub").mkdir()
    assert "Sub" in c.get(f"/settings/browse?path={other}").text
    c.post("/settings/folder", data={"path": str(other)})
    assert settings.source_dir == other


def test_inbox_thread_and_send(gui, monkeypatch):
    c, settings, *_ = gui
    store = Store(settings.db_path)
    store.upsert_conversation(conversation_row({"id": "c1", "buyerName": "Anna", "adTitle": "Helm"}))
    store.add_message({"id": "m1", "conversation_id": "c1", "outgoing": 0, "text": "Gehen 60?", "sent_at": "1"})
    did = store.add_draft("c1", "[SCAM?]\nLieber Abholung.", "m1")
    store.close()
    sent = []
    monkeypatch.setattr(engine, "send_reply", lambda s, st, cid, text, draft_id=None: sent.append((cid, text, draft_id)))
    page = c.get("/inbox/c1").text
    assert "Gehen 60?" in page and "Lieber Abholung." in page
    c.post("/inbox/c1/send", data={"text": "[SCAM?]\nLieber Abholung.", "draft_id": str(did)})
    assert sent == [("c1", "Lieber Abholung.", did)]
    c.post("/inbox/c1/send", data={"text": "  ", "draft_id": ""})
    assert len(sent) == 1


def test_price_check_from_editor_shows_market(gui, monkeypatch):
    c, settings, src, _ = gui

    def fake(settings_, store, key=None, query=None, ai=False, **kw):
        catalog.write_market(src / key, {"at": "2026-11-22 12:00", "query": query or "Helm", "offers": 5, "used": 4,
                                         "min": 70, "p25": 80, "median": 88, "p75": 92, "max": 99,
                                         "ai": {"comparable": 3, "median": 85, "suggestion":
                                                {"start": 99, "reduce_to": 89, "min_sale": 80}, "note": "Passt."}})
        return {"query": "Helm", "median": 88, "ai": {"median": 85}}
    monkeypatch.setattr(engine, "price_check", fake)
    c.post("/items/Helm/prices", data={"ai": "on"})
    page = c.get("/items/Helm", headers=DE).text
    assert "88" in page and "99 → 89 €" in page and "Passt." in page


def test_form_helpers_reverse_each_other():
    raw = {"approved": True, "title": "T T T T T", "description": "a\nb\n", "pricing": {"start": 5, "reduce_to": 4},
           "shipping": {"enabled": False}, "price_check": {"exclude": ["x", "y"]}, "attributes": {"k": "v"}}
    form = forms.listing_form(raw)
    back = forms.listing_values(form, ["legal"])
    assert back["pricing"] == {"start": 5, "reduce_to": 4} and back["shipping"] == {"enabled": False}
    assert back["description"] == "a\nb\n" and back["price_check"]["exclude"] == ["x", "y"]
    assert back["attributes"] == {"k": "v"} and back["texts"] == ["legal"] and back["approved"] is True


def _jpeg(color=(200, 10, 10)):
    return b"\xff\xd8\xff\xe0" + bytes(color) * 10


def test_editor_has_action_card_help_plan_and_categories(gui):
    c, settings, src, _ = gui
    page = c.get("/items/Helm", headers=DE).text
    assert 'class="ka-actions-grid"' in page and "Speichern" in page and 'data-approve="on"' in page
    assert "Erst freigeben" in page                                                     # nothing due unapproved
    assert "Pflicht vor der Freigabe" in page and "Preisverlauf" in page and "Ende der Senkung" in page
    assert '<script type="application/json" id="cat-data">' in page and "PC-Zub" in page
    assert 'class="ka-about"' in page and "github.com/Ollornog" in page and "/static/icon.svg" in page
    # approve button = hidden field + submit
    c.post("/items/Helm", data={"approved": "on", "title": "Shark Helm Größe S", "description": "x",
                                "pricing.start": "100", "category": "Auto, Rad & Boot > Motorradteile & Zubehör"})
    page = c.get("/items/Helm", headers=DE).text
    assert "Freigegeben</button>" in page and "Jetzt veröffentlichen</button>" in page
    assert 'aria-pressed="true"><svg data-icon-lu="check"' in page and 'data-icon-lu="trash"' in page


def test_photo_order_cover_and_skip_saved_with_form(gui):
    c, settings, src, _ = gui
    for n in ("b.jpg", "c.jpg"):
        (src / "Helm" / n).write_bytes(_jpeg())
    form = {"title": "Shark Helm Größe S", "description": "Kaum getragen.", "pricing.start": "100",
            "photos.order": ["c.jpg", "a.jpg", "b.jpg"], "photos.skip": ["a.jpg"]}
    c.post("/items/Helm", data=form)
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert [p.name for p in li.images] == ["c.jpg", "b.jpg"]
    page = c.get("/items/Helm").text
    assert page.index("/photo/Helm/c.jpg") < page.index("/photo/Helm/b.jpg") < page.index("/photo/Helm/a.jpg")


def test_crop_saves_copy_replaces_original_in_order(gui):
    c, settings, src, _ = gui
    (src / "Helm" / "b.jpg").write_bytes(_jpeg())
    r = c.post("/items/Helm/photos/crop", data={"source": "a.jpg"}, files={"file": ("crop.jpg", _jpeg(), "image/jpeg")})
    assert r.json() == {"name": "a-crop1.jpg"}
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert [p.name for p in li.images] == ["a-crop1.jpg", "b.jpg"] and (src / "Helm" / "a.jpg").exists()
    r = c.post("/items/Helm/photos/crop", data={"source": "a.jpg"}, files={"file": ("crop.jpg", _jpeg(), "image/jpeg")})
    assert r.json()["name"] == "a-crop2.jpg"
    assert c.post("/items/Helm/photos/crop", data={"source": "../x.jpg"},
                  files={"file": ("c.jpg", _jpeg(), "image/jpeg")}).status_code == 400
    assert c.post("/items/Helm/photos/crop", data={"source": "a.jpg"},
                  files={"file": ("c.jpg", b"<svg/>", "image/jpeg")}).status_code == 400


def test_run_now_button_calls_engine_for_this_item(gui, monkeypatch):
    c, *_ = gui
    calls = []
    monkeypatch.setattr(engine, "run_item", lambda s, st, key: calls.append(key) or f"{key}: published")
    r = c.post("/items/Helm/run")
    assert calls == ["Helm"] and "published" in r.text


def test_bot_error_summary_reads_error_lines():
    from kassist.bot import BotResult
    out = ("banner\n[INFO] Config: x\n2026-11-22 16:26:24,862 [ERROR] 1 validation error for [ads/kvm.yaml]:\n"
           "- category: Field required\n[INFO] done\n")
    assert BotResult(False, out).error == "1 validation error for [ads/kvm.yaml]:\n- category: Field required"
    assert BotResult(False, "a\nb\nc").error == "a\nb\nc"


def test_price_plan_dates_and_end():
    from datetime import date
    from kassist.gui.viewmodels import price_plan
    from kassist.pricing import PriceSchedule
    plan = price_plan(PriceSchedule(49, 42, 14, "5", 7), date(2026, 11, 22))
    assert [(s["day"].isoformat(), s["price"], s["last"]) for s in plan] == [
        ("2026-11-22", 49, False), ("2026-12-06", 44, False), ("2026-12-13", 42, True)]
    assert price_plan(PriceSchedule(49, 49), date(2026, 11, 22)) == [{"day": date(2026, 11, 22), "price": 49, "last": True}]


def test_plan_is_shown_while_listing_is_invalid(gui):
    c, settings, src, _ = gui
    (src / "Helm" / "listing.yaml").write_text(
        "approved: true\ntitle: Shark Helm Größe S\ndescription: x\npricing: {start: 100, reduce_to: 90}\n", encoding="utf-8")
    page = c.get("/items/Helm", headers=DE).text
    assert "category is required" in page and "Ende der Senkung" in page and "Anzahl Senkungen" in page


def test_settings_auto_and_browser_switches(gui):
    c, settings, src, cfg_path = gui
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["Privatverkauf."], "d.seller": "alex",
            "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3", "l.max_actions": "8",
            "l.pause_min": "45", "l.pause_max": "150", "llm.command": "claude -p"}
    c.post("/settings", data=dict(base, **{"a.enabled": "on", "a.sync_hours": "12", "a.inbox_minutes": "10",
                                           "b.show_during_actions": "on"}))
    assert settings.raw["auto"] == {"enabled": True, "on_start": False, "sync_minutes": 720, "inbox_minutes": 10}
    assert settings.raw["browser"] == {"start_minimized": False, "show_during_actions": True}
    r = c.post("/settings", data=dict(base, **{"a.sync_hours": "0.5", "a.inbox_minutes": "1"}))
    assert "at most every" in r.text and settings.raw["auto"]["sync_minutes"] == 720          # too often: refused


def test_staged_changes_banner_and_approve_button(gui):
    c, settings, src, _ = gui
    from kassist import catalog as cat_mod
    (src / "Helm" / "listing.yaml").write_text(
        "approved: true\ntitle: Shark Helm Größe S\ndescription: x\ncategory: Auto, Rad & Boot\npricing: {start: 100}\n",
        encoding="utf-8")
    li = cat_mod.load_listing(src / "Helm", settings.catalog())
    store = Store(settings.db_path)
    store.save_item(ItemState("Helm", status="online", ad_id=7, price=100, published_hash="old",
                              approved_hash="old", published_snapshot='{"title": "Alter Titel"}'))
    store.close()
    page = c.get("/items/Helm", headers=DE).text
    assert "noch nicht übertragen" in page and "Geändert: Titel" in page and "Änderungen übertragen</button>" in page
    c.post("/items/Helm/approve-changes")
    page = c.get("/items/Helm", headers=DE).text
    assert "Änderungen freigegeben" in page


from kassist.state import ItemState  # noqa: E402


def test_ai_settings_store_key_and_test_button(gui, monkeypatch):
    c, settings, src, cfg_path = gui
    from kassist import llm
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["Privatverkauf."], "d.seller": "alex",
            "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3", "l.max_actions": "8",
            "l.pause_min": "45", "l.pause_max": "150", "llm.command": "claude -p",
            "llm.provider": "mistral", "llm.model": "mistral-medium-2508", "llm.key": "sk-test"}
    monkeypatch.setattr(llm, "mistral_models", lambda key=None: ["mistral-medium-2508", "mistral-small-2506"])
    c.post("/settings", data=base)
    assert config.load_secrets()["MISTRAL_API_KEY"] == "sk-test"
    assert settings.raw["llm"]["provider"] == "mistral" and settings.raw["llm"]["model"] == "mistral-medium-2508"
    assert "sk-test" not in cfg_path.read_text(encoding="utf-8")
    page = c.get("/settings", headers=DE).text
    assert "sk-test" not in page and "gespeichert – leer lassen" in page and "mistral-small-2506" in page
    r = c.post("/settings", data=dict(base, **{"llm.model": "mistral-large-latest", "llm.key": ""}))
    assert "pinned model" in r.text and settings.raw["llm"]["model"] == "mistral-medium-2508"
    asked = []
    monkeypatch.setattr(llm, "ask", lambda s, provider, prompt, json_mode=False: asked.append(provider) or "Ja, bereit.")
    r = c.post("/settings/ai-test?provider=command", data=dict(base, **{"llm.key": ""}))
    assert "command: Ja, bereit." in r.text and asked == ["command"]       # the button tests its own provider
    assert "❌" in c.post("/settings/ai-test?provider=evil", data=dict(base, **{"llm.key": ""})).text


def test_ai_settings_both_providers_and_fallback_switch(gui, monkeypatch):
    c, settings, src, cfg_path = gui
    from kassist import llm
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["Privatverkauf."], "d.seller": "alex",
            "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3", "l.max_actions": "8",
            "l.pause_min": "45", "l.pause_max": "150", "llm.command": "cat",
            "llm.provider": "command", "llm.model": "mistral-medium-2508", "llm.key": "sk-test", "llm.fallback": "on"}
    monkeypatch.setattr(llm, "mistral_models", lambda key=None: ["mistral-medium-2508"])
    c.post("/settings", data=base)
    cfg = settings.raw["llm"]
    assert cfg["provider"] == "command" and cfg["model"] == "mistral-medium-2508" and cfg["fallback"] is True
    page = c.get("/settings", headers=DE).text
    assert page.count("✅ eingerichtet") == 2 and "Claude (lokales Programm)" in page and "mistral-medium-2508" in page
    c.post("/settings", data={k: v for k, v in base.items() if k != "llm.fallback"})
    assert settings.raw["llm"]["fallback"] is False


def test_reply_mode_setting(gui):
    c, settings, src, cfg_path = gui
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["Privatverkauf."], "d.seller": "alex",
            "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3", "l.max_actions": "8",
            "l.pause_min": "45", "l.pause_max": "150", "llm.command": "claude -p"}
    c.post("/settings", data=dict(base, **{"r.mode": "auto", "r.max_auto": "2"}))
    assert settings.raw["replies"] == {"mode": "auto", "max_auto": 2}
    r = c.post("/settings", data=dict(base, **{"r.mode": "auto", "r.max_auto": "50"}))
    assert "1 to 10" in r.text and settings.raw["replies"]["max_auto"] == 2
    page = c.get("/settings", headers=DE).text
    assert "Antworten an Käufer" in page and 'value="auto" checked' in page


def test_camera_capture_saves_jpeg_with_unique_names(gui):
    c, settings, src, _ = gui
    jpeg = b"\xff\xd8\xff\xe0camera"
    names = [c.post("/items/Helm/photos/capture", files={"file": ("camera.jpg", jpeg, "image/jpeg")}).json()["name"]
             for _ in range(2)]
    assert names[0].startswith("cam-") and names[0] != names[1]
    assert all((src / "Helm" / n).read_bytes() == jpeg for n in names)
    assert c.post("/items/Helm/photos/capture", files={"file": ("x.jpg", b"<svg/>", "image/jpeg")}).status_code == 400
    assert c.post("/items/nope/photos/capture", files={"file": ("x.jpg", jpeg, "image/jpeg")}).status_code == 400
    assert 'id="cam-open"' in c.get("/items/Helm").text


def test_trash_moves_folder_and_refuses_online_ads(gui):
    c, settings, src, _ = gui
    store = Store(settings.db_path)
    store.save_item(ItemState("Helm", status="online", ad_id=7))
    store.close()
    r = c.post("/items/Helm/trash")
    assert "still online" in r.text and (src / "Helm").is_dir()
    store = Store(settings.db_path)
    store.save_item(ItemState("Helm", status="sold", ad_id=7))
    store.close()
    c.post("/items/Helm/trash")
    assert not (src / "Helm").exists()
    moved = list((src / ".papierkorb").iterdir())
    assert len(moved) == 1 and moved[0].name.startswith("Helm-") and (moved[0] / "listing.yaml").is_file()
    assert "Helm" not in c.get("/fragments/items").text                     # hidden from the list
    store = Store(settings.db_path)
    assert store.item("Helm").status == "new"                                # state row removed
    store.close()
    assert c.post("/items/.papierkorb/trash", follow_redirects=False).status_code == 303


def test_settings_rows_have_delete_buttons(gui):
    c, *_ = gui
    page = c.get("/settings", headers=DE).text
    assert page.count("data-delete-row") >= 2


def test_success_banners_say_what_happened(gui):
    c, settings, src, _ = gui
    r = c.post("/items/Helm", data={"title": "Shark Helm Größe S", "description": "x", "pricing.start": "100"}, headers=DE)
    assert "✅ Gespeichert." in r.text
    r = c.post("/items/Helm/photos", files=[("files", ("n.jpg", b"\xff\xd8", "image/jpeg"))], headers=DE)
    assert "✅ 1 Foto(s) hinzugefügt." in r.text
    r = c.post("/items/Helm", data={"title": "Shark Helm Größe S", "description": "x", "pricing.start": "100"},
               headers={"accept-language": "en"})
    assert "✅ Saved." in r.text


def test_text_block_order_follows_the_form(gui):
    c, settings, src, cfg_path = gui
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "d.seller": "alex", "d.type": "NEGOTIABLE", "l.max_publish": "3",
            "l.max_actions": "8", "l.pause_min": "45", "l.pause_max": "150", "llm.command": "claude -p",
            "block.key": ["pay", "legal", ""], "block.text": ["Bar.", "Privatverkauf.", ""], "d.texts": ["pay", "legal"]}
    c.post("/settings", data=base)
    assert list(settings.raw["text_blocks"]) == ["pay", "legal"]
    page = c.get("/settings").text
    assert page.index('value="pay"') < page.index('value="legal"') and "ka-grip" in page



def test_fixed_step_switch_in_the_editor(gui):
    c, settings, src, _ = gui
    base = {"title": "Shark Helm Größe S", "description": "x", "pricing.start": "100", "pricing.reduce_to": "80",
            "pricing.schedule.steps": "4", "pricing.schedule.step": "7"}
    c.post("/items/Helm", data=base)                                   # switch off: steps count, step ignored
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert li.schedule.count == 4
    c.post("/items/Helm", data=dict(base, fixed_step="on"))           # switch on: fixed 7 EUR, count ignored
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert li.schedule.count == 0 and li.schedule.step == "7"
    assert 'name="fixed_step" id="fixed-step" checked' in c.get("/items/Helm").text
    c.post("/items/Helm", data=dict(base, **{"pricing.schedule.steps": ""}))   # off, no count: step not kept
    assert catalog.load_raw(src / "Helm")["pricing"]["schedule"].get("step") is None
    assert catalog.load_listing(src / "Helm", settings.catalog()).schedule.count == 3


def test_names_keep_their_case_and_match_any_case(gui):
    c, settings, src, cfg_path = gui
    base = {"source_dir": str(src), "seller.key": ["Alex"], "seller.name": ["Alex B."], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "d.seller": "Alex", "d.type": "NEGOTIABLE", "l.max_publish": "3",
            "l.max_actions": "8", "l.pause_min": "45", "l.pause_max": "150", "llm.command": "claude -p",
            "block.key": ["Rechtliches"], "block.text": ["Privatverkauf."], "d.texts": ["Rechtliches"]}
    c.post("/settings", data=base)
    assert "Alex" in settings.raw["sellers"] and settings.raw["listing_defaults"]["seller"] == "Alex"
    (src / "Helm" / "listing.yaml").write_text("title: Shark Helm Größe S\ndescription: x\nseller: ALEX\n"
                                               "texts: [RECHTLICHES]\npricing: {start: 100}\n", encoding="utf-8")
    li = catalog.load_listing(src / "Helm", settings.catalog())
    assert li.seller == "Alex" and li.contact["name"] == "Alex B." and li.description.endswith("Privatverkauf.")


def test_folder_scan_is_cached_until_kassist_changes_something(gui, monkeypatch):
    c, settings, src, _ = gui
    calls = []
    real = catalog.scan
    monkeypatch.setattr(catalog, "scan", lambda *a, **k: calls.append(1) or real(*a, **k))
    c.get("/fragments/items")
    c.get("/fragments/items")
    assert len(calls) == 1                                        # second view from the cache
    (src / "Neu2").mkdir()
    assert "Neu2" not in c.get("/fragments/items").text           # outside change: shows after the TTL …
    c.post("/items/Helm", data={"title": "Shark Helm Größe S", "description": "x", "pricing.start": "100"})
    assert "Neu2" in c.get("/fragments/items").text and len(calls) == 2   # … or after any change by kassist


def test_static_files_cached_and_versioned(gui):
    c, *_ = gui
    page = c.get("/").text
    assert "/static/app.css?v=" in page and "/static/c22/css/c22.css?v=" in page
    assert c.get("/static/app.css").headers["cache-control"] == "max-age=86400"
    assert "cache-control" not in {k.lower() for k in c.get("/fragments/items").headers}


def test_inbox_is_a_skeleton_with_rows_as_fragment(gui):
    c, settings, *_ = gui
    store = Store(settings.db_path)
    store.upsert_conversation(conversation_row({"id": "c9", "buyerName": "Ben", "adTitle": "Helm"}))
    store.close()
    page = c.get("/inbox", headers=DE).text
    assert 'data-src="/fragments/inbox"' in page and "Ben" not in page
    rows = c.get("/fragments/inbox", headers=DE).text
    assert "Ben" in rows and "/inbox/c9" in rows


def test_model_list_is_cached_per_key(monkeypatch):
    from kassist import llm
    calls = []
    monkeypatch.setattr(llm, "_request", lambda path, key, body=None, timeout=60: calls.append(key) or {"data": [{"id": "m-1"}]})
    assert llm.mistral_models("k1") == ["m-1"] and llm.mistral_models("k1") == ["m-1"] and calls == ["k1"]
    llm.mistral_models("k2")
    assert calls == ["k1", "k2"]


def test_condition_texts_are_editable(gui):
    c, settings, src, cfg_path = gui
    base = {"source_dir": str(src), "seller.key": ["alex"], "seller.name": ["Alex"], "seller.zipcode": ["10115"],
            "seller.city": ["Berlin"], "block.key": ["legal"], "block.text": ["Privatverkauf."], "d.seller": "alex",
            "d.texts": ["legal"], "d.type": "NEGOTIABLE", "l.max_publish": "3", "l.max_actions": "8",
            "l.pause_min": "45", "l.pause_max": "150", "llm.command": "cat", "cond.defect": "Kaputt, nur Teile."}
    c.post("/settings", data=base)
    assert settings.catalog().condition_texts["defect"] == "Kaputt, nur Teile."
    assert settings.catalog().condition_texts["new"].startswith("Zustand: neu")   # others keep the default
    assert "Kaputt, nur Teile." in cfg_path.read_text(encoding="utf-8")


def test_facts_in_editor(gui):
    c, settings, src, _ = gui
    page = c.get("/items/Helm", headers=DE).text
    assert 'name="facts.condition"' in page and "Gebraucht, wie neu" in page and 'name="facts.market_new"' in page
    assert "idealo" not in page and 'id="facts-preview"' in page and 'name="fx.label"' in page
    c.post("/items/Helm", data={"title": "Shark Helm Größe S", "description": "x", "pricing.start": "100",
                                "facts.condition": "new_sealed", "facts.condition.show": "on",
                                "facts.purchase_price": "199,00", "facts.market_used": "80",
                                "fx.label": ["Lieferumfang", "Gekauft", ""], "fx.value": ["Visier", "2023", ""],
                                "fx.show": ["0"]})
    facts = catalog.load_raw(src / "Helm")["facts"]
    assert facts["condition"] == {"value": "new_sealed", "show": True}
    assert facts["purchase_price"] == {"value": 199, "show": False} and facts["market_used"] == {"value": 80, "show": False}
    assert facts["extra"] == [{"label": "Lieferumfang", "value": "Visier", "show": True},
                              {"label": "Gekauft", "value": "2023", "show": False}]
    form = c.get("/items/Helm", headers=DE).text
    assert 'value="new_sealed" data-text="Zustand: neu und originalverpackt (OVP)." selected' in form
    assert 'value="Gekauft"' in form and 'name="fx.show" value="1" data-fact-show' in form
    assert "Zustandstexte" in c.get("/settings", headers=DE).text


def test_median_button_from_the_last_price_check(gui):
    c, settings, src, _ = gui
    catalog.write_market(src / "Helm", {"date": "2026-11-22", "median": 85, "min": 50, "max": 120})
    page = c.get("/items/Helm", headers=DE).text
    assert 'data-copy-to="f-market_used" data-value="85"' in page
