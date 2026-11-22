import json

import pytest
from fastapi.testclient import TestClient

from kassist import ai_assist, bot, llm
from kassist.config import DEFAULTS, Settings, _merge
from kassist.gui import create_app

NAMES = ["Elektronik", "Elektronik > PC-Zubehör & Software", "Elektronik > PC-Zubehör & Software > Kabel & Adapter",
         "Haus & Garten"]


def reply(obj):
    return lambda prompt: "Gern:\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


def test_improve_text_keeps_answer_and_needs_text():
    assert ai_assist.improve_text(reply({"text": "Neuer Text\r\nZeile"}), "T", "alt") == "Neuer Text\nZeile"
    with pytest.raises(ValueError):
        ai_assist.improve_text(reply({"text": "x"}), "T", "  ")
    with pytest.raises(ValueError):
        ai_assist.improve_text(reply({"text": ""}), "T", "alt")


def test_titles_are_cut_to_65_and_deduplicated():
    long = "DJI Mini 2 Fly More Combo mit drei Akkus, Tasche und Ersatzpropellern, sehr guter Zustand"
    out = ai_assist.propose_titles(reply({"titles": [long, "DJI Mini 2 Fly More Combo", "DJI Mini 2 Fly More Combo", "kurz"]}),
                                   "", "Text")
    assert all(len(t) <= 65 for t in out) and out[1] == "DJI Mini 2 Fly More Combo" and len(out) == 2


def test_categories_only_from_the_list():
    got = ai_assist.propose_categories(
        reply({"categories": ["elektronik > pc-zubehör & software > kabel & adapter", "Erfunden > Kategorie", "Haus & Garten"]}),
        "KVM", "Text", NAMES)
    assert got == ["Elektronik > PC-Zubehör & Software > Kabel & Adapter", "Haus & Garten"]
    with pytest.raises(ValueError):
        ai_assist.propose_categories(reply({"categories": ["Erfunden"]}), "KVM", "Text", NAMES)


def test_prompts_carry_the_item_and_the_category_list():
    seen = []
    ai_assist.propose_categories(lambda p: seen.append(p) or '{"categories": ["Haus & Garten"]}', "Titel X", "Text Y", NAMES)
    assert "Titel X" in seen[0] and "Text Y" in seen[0] and "Elektronik > PC-Zubehör & Software > Kabel & Adapter" in seen[0]


def test_ai_route_uses_form_values_and_reports_errors(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "Helm").mkdir(parents=True)
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    c = TestClient(create_app(settings))
    prompts = []
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: prompts.append(prompt) or '{"titles": ["Shark Helm Größe S wie neu"]}')
    r = c.post("/items/Helm/ai/titles", json={"title": "alt", "description": "Ungespeicherter Text"})
    assert r.json() == {"titles": ["Shark Helm Größe S wie neu"]} and "Ungespeicherter Text" in prompts[0]
    monkeypatch.setattr(bot, "categories", lambda: {n: "x" for n in NAMES})
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: '{"categories": ["Haus & Garten"]}')
    assert c.post("/items/Helm/ai/categories", json={"title": "t", "description": "d"}).json() == {"categories": ["Haus & Garten"]}

    def fail(s, prompt, json_mode=False):
        raise llm.LLMError("Mistral: HTTP 401")
    monkeypatch.setattr(llm, "complete", fail)
    r = c.post("/items/Helm/ai/text", json={"title": "t", "description": "d"})
    assert r.status_code == 502 and "401" in r.json()["error"]
    assert c.post("/items/Helm/ai/nonsense", json={}).status_code == 400
    assert c.post("/items/..%2Fx/ai/text", json={}).status_code in (400, 404)


NOTES = """Bosch Akkuschrauber GSR 12V
Kaum benutzt, mit 2 Akkus und Koffer.
Will 60 Euro VB, nicht unter 45.
Nur Abholung."""


def test_listing_from_notes_keeps_only_stated_prices():
    fields, hints = ai_assist.extract_listing(reply({
        "title": "Bosch Akkuschrauber GSR 12V mit 2 Akkus", "description": "Kaum benutzt.\n- 2 Akkus\n- Koffer",
        "start_price": 60, "floor_price": 45, "min_sale": None, "price_type": "NEGOTIABLE",
        "shipping": False, "shipping_cost": None, "notes": ["Kategorie wählen"]}), NOTES, "Akkuschrauber")
    assert fields["pricing"] == {"start": 60, "reduce_to": 45, "min_sale": 45, "type": "NEGOTIABLE"}
    assert fields["shipping"] == {"enabled": False} and fields["approved"] is False and hints == ["Kategorie wählen"]


def test_listing_from_notes_drops_invented_values():
    fields, hints = ai_assist.extract_listing(reply({
        "title": "Akkuschrauber", "description": "Text", "start_price": 75, "floor_price": 50, "min_sale": 6,
        "price_type": "Sonderpreis", "shipping": "vielleicht", "shipping_cost": 4.99, "notes": []}),
        NOTES, "Akkuschrauber")
    assert "pricing" not in fields and "shipping" not in fields             # 75, 50, 4.99 are not in the notes
    assert any("75" in h for h in hints) and any("50" in h for h in hints)
    fields, _ = ai_assist.extract_listing(reply({"title": "", "description": "", "start_price": 45,
                                                 "floor_price": 60}), NOTES, "Akkuschrauber")
    assert fields["pricing"] == {"start": 45} and fields["title"] == "Akkuschrauber"    # floor above start dropped
    assert fields["description"] == NOTES                                    # no text from the AI: the notes


def test_stated_numbers_match_whole_words_only():
    assert ai_assist._stated(60, "60 Euro") == 60 and ai_assist._stated(6, "60 Euro") is None
    assert ai_assist._stated(50, "150 Euro") is None and ai_assist._stated(50, "1.50 Euro") is None
    assert ai_assist._stated(4.5, "Versand 4,50 €") == 4.5 and ai_assist._stated(0, "nichts") is None
    assert ai_assist._stated("abc", "abc") is None and ai_assist._stated(-5, "-5") is None


def test_import_text_as_is_or_with_ai(tmp_path, monkeypatch):
    from kassist import catalog
    src = tmp_path / "src"
    (src / "Schrauber").mkdir(parents=True)
    (src / "Schrauber" / "notizen.txt").write_text(NOTES, encoding="utf-8")
    (src / "Lampe").mkdir()
    (src / "Lampe" / "info.md").write_text("Eine alte Stehlampe aus Messing, funktioniert, Schirm leicht vergilbt.",
                                           encoding="utf-8")
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    c = TestClient(create_app(settings))
    page = c.get("/items/Schrauber", headers={"accept-language": "de"}).text
    assert "notizen.txt" in page and 'value="ai"' in page and 'value="plain"' in page

    c.post("/items/Lampe/import", data={"source": "info.md", "how": "plain"})
    raw = catalog.load_raw(src / "Lampe")
    assert raw["title"] == "Lampe" and raw["description"].startswith("Eine alte Stehlampe")  # long line: folder name
    assert raw["approved"] is False and not (raw.get("pricing") or {}).get("start")

    prompts = []
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: prompts.append(prompt) or json.dumps(
        {"title": "Bosch GSR 12V Akkuschrauber", "description": "Kaum benutzt.", "start_price": 60,
         "floor_price": 99, "notes": ["Fotos fehlen"]}))
    r = c.post("/items/Schrauber/import", data={"source": "notizen.txt", "how": "ai"}, headers={"accept-language": "de"})
    assert "listing.yaml aus notizen.txt angelegt" in r.text and "Nur Abholung" in prompts[0]
    text = (src / "Schrauber" / "listing.yaml").read_text(encoding="utf-8")
    raw = catalog.load_raw(src / "Schrauber")
    assert raw["pricing"]["start"] == 60 and not raw["pricing"].get("reduce_to")   # 99 not in the notes
    assert "# - Fotos fehlen" in text
    r = c.post("/items/Schrauber/import", data={"source": "notizen.txt", "how": "plain"}, headers={"accept-language": "de"})
    assert "❌" in r.text                                                           # never overwrites


def test_plain_import_short_first_line_is_the_title():
    from kassist import catalog
    f = catalog.plain_fields("# Shark Helm S\n\nWie neu.\nMit Visier.", "Helm")
    assert f["title"] == "Shark Helm S" and f["description"] == "Wie neu.\nMit Visier."
    long = "Ich verkaufe hier meinen gut erhaltenen Motorradhelm, den ich nur zwei Sommer lang getragen habe"
    f = catalog.plain_fields(long + "\nGröße S.", "Helm")
    assert f["title"] == "Helm" and f["description"].startswith(long)        # a sentence is no title
    with pytest.raises(ValueError):
        catalog.plain_fields("  \n", "Helm")


def test_text_sources_put_structured_drafts_first(tmp_path):
    from kassist import catalog
    for name in ("b.txt", "a.md", "x_inserat_1.md", ".hidden.md", "foto.jpg"):
        (tmp_path / name).write_text("x")
    assert [p.name for p in catalog.text_sources(tmp_path)] == ["x_inserat_1.md", "a.md", "b.txt"]


def test_listing_from_notes_condition_and_purchase_price():
    fields, _ = ai_assist.extract_listing(reply({"title": "Bosch GSR", "description": "x", "condition": "like_new",
                                                 "purchase_price": 110}), NOTES + "\nNeu 110 bezahlt.", "Schrauber")
    assert fields["facts"] == {"condition": {"value": "like_new", "show": True}, "purchase_price": {"value": 110, "show": False}}
    fields, _ = ai_assist.extract_listing(reply({"title": "Bosch GSR", "description": "x", "purchase_price": "110.0"}),
                                          NOTES + "\nNeu 110 bezahlt.", "Schrauber")
    assert fields["facts"]["purchase_price"]["value"] == 110 and isinstance(fields["facts"]["purchase_price"]["value"], int)
    fields, _ = ai_assist.extract_listing(reply({"title": "Bosch GSR", "description": "x", "condition": "mint",
                                                 "purchase_price": 130}), NOTES, "Schrauber")
    assert "facts" not in fields                                           # unknown level, price not in the notes


def test_reply_prompt_gets_hidden_facts_only():
    from types import SimpleNamespace
    from kassist import drafts
    from kassist.pricing import PriceSchedule
    listing = SimpleNamespace(title="Helm", description="Kaum getragen.\nKaufpreis: 249 €", min_sale=80,
                              schedule=PriceSchedule(start=100, floor=80),
                              facts=[("Kaufpreis", "249 €", True), ("Gekauft", "2023", False)])
    prompt = drafts.build_prompt(listing, None, [{"outgoing": False, "text": "Wie alt?"}])
    notes = prompt.split("Seller's notes")[1].split("Conversation")[0]
    assert "- Gekauft: 2023" in notes and "249" not in notes
