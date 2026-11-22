from datetime import date
from pathlib import Path

import pytest
import yaml

from kassist.catalog import (LISTING_FILE, Catalog, import_draft, load_listing, parse_draft_markdown, scan,
                             shorten_title)

CONDITION = "Ich versuche, fair zu verkaufen. Fragt gerne nach."
PAYMENT = "PayPal, Überweisung oder bar. Abholung in Berlin möglich."

DRAFT = f"""# KVM-Switch - Kleinanzeigen-Inserat (V1)

## Titel
4K Dual DisplayPort KVM-Switch 2x2 USB 3.0 wie neu

## Verkaufstext

Moin Moin!
Ich biete hier einen KVM-Switch an.

## Zustand
{CONDITION}

## Zahlung & Versand
{PAYMENT}

## Preise
- Neupreis: ca. 60-90 EUR
- Zielpreis: 42 EUR (Verhandlungsbasis, z.B. mit 49 EUR einstellen)
"""

CAT = Catalog(
    defaults={"seller": "alex", "texts": ["condition", "payment"],
              "pricing": {"type": "NEGOTIABLE", "schedule": {"hold_days": 14, "step": "5", "every_days": 7}},
              "shipping": {"enabled": False}, "republish": {"enabled": True, "after_days": 14, "max": 2}},
    sellers={"alex": {"name": "Alex", "location": {"zipcode": "10115", "city": "Berlin"}},
             "sam": {"name": "Sam", "location": {"zipcode": "10115", "city": "Berlin"}}},
    text_blocks={"condition": CONDITION, "payment": PAYMENT, "legal": "Privatverkauf."},
)


def _folder(tmp_path: Path, name: str, photos: int = 2) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    for i in range(photos):
        (folder / f"PXL_{i}.jpg").write_bytes(b"\xff\xd8")
    return folder


def _write(folder: Path, data: dict) -> None:
    (folder / LISTING_FILE).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


BASE = {"title": "Shark Helm Größe S", "description": "Kaum getragen.", "pricing": {"start": 100}}


def test_full_structure_with_defaults_sellers_and_text_blocks(tmp_path):
    folder = _folder(tmp_path, "Helm")
    _write(folder, {**BASE, "approved": True, "category": "161/225/sonstiges", "seller": "sam", "texts": ["condition", "legal"],
                    "pricing": {"start": 100, "reduce_to": 90, "min_sale": 80, "schedule": {"step": "10%"}},
                    "availability": {"from": "2026-12-01", "until": "2027-03-31"},
                    "location": {"city": "Hamburg", "zipcode": "20095"},
                    "shipping": {"enabled": True, "cost": 6.19},
                    "republish": {"max": 1}, "price_check": {"query": "Shark Skwal", "exclude": ["Visier"]},
                    "attributes": {"condition": "like_new"}})
    li = load_listing(folder, CAT)
    assert (li.schedule.start, li.schedule.floor, li.min_sale) == (100, 90, 80)
    assert li.schedule.step == "10%" and li.schedule.hold_days == 14       # partly from defaults
    assert li.description == f"Kaum getragen.\n\n{CONDITION}\n\nPrivatverkauf."
    assert li.contact == {"name": "Sam", "zipcode": "20095", "location": "Hamburg"}
    assert (li.available_from, li.available_until) == (date(2026, 12, 1), date(2027, 3, 31))
    assert li.shipping and li.shipping_type == "SHIPPING" and li.shipping_cost == 6.19
    assert (li.republish, li.max_republish, li.republish_after_days) == (True, 1, 14)
    assert li.price_query == "Shark Skwal" and li.price_exclude == ["visier"]
    assert li.attributes == {"condition": "like_new"}


def test_minimal_listing_uses_defaults(tmp_path):
    folder = _folder(tmp_path, "Helm")
    _write(folder, BASE)
    li = load_listing(folder, CAT)
    assert (li.schedule.floor, li.min_sale) == (100, 100)    # no reduction unless reduce_to is set
    assert li.seller == "alex" and li.contact["zipcode"] == "10115"
    assert li.texts == ["condition", "payment"] and li.shipping_type == "PICKUP"
    assert li.approved is False


def test_empty_template_values_do_not_override_defaults(tmp_path):
    from kassist.cli import TEMPLATE
    raw = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    raw.update({"title": "Shark Helm Größe S", "description": "Text"})
    raw["pricing"]["start"] = 100
    folder = _folder(tmp_path, "T")
    _write(folder, raw)
    li = load_listing(folder, CAT)
    assert li.seller == "alex" and li.contact["zipcode"] == "10115"
    assert li.texts == ["condition", "payment"] and li.schedule.every_days == 7 and li.republish


@pytest.mark.parametrize("change, message", [
    ({"pricing": {"start": 100, "reduce_to": 90, "min_sale": 95}}, "min_sale"),
    ({"pricing": {"start": 100, "reduce_to": 120}}, "floor"),
    ({"pricing": {}}, "pricing.start"),
    ({"seller": "nobody"}, "seller"),
    ({"texts": ["missing"]}, "text block"),
    ({"availability": {"from": "2027-01-02", "until": "2027-01-01"}}, "until"),
    ({"availability": {"from": "morgen"}}, "date"),
    ({"price": 100}, "unknown field"),
    ({"title": "x" * 66}, "title"),
    ({"description": ""}, "description"),
])
def test_invalid_listings_are_rejected(tmp_path, change, message):
    folder = _folder(tmp_path, "X")
    _write(folder, {**BASE, **change})
    with pytest.raises(ValueError, match=message):
        load_listing(folder, CAT)


def test_parse_draft_maps_prices_and_standard_sections():
    fields = parse_draft_markdown(DRAFT, CAT.text_blocks)
    assert fields["title"] == "4K Dual DisplayPort KVM-Switch 2x2 USB 3.0 wie neu"
    assert fields["pricing"] == {"start": 49, "reduce_to": 42, "min_sale": 42}
    assert fields["texts"] == ["condition", "payment"]
    assert fields["description"] == "Moin Moin!\nIch biete hier einen KVM-Switch an."


def test_parse_draft_keeps_sections_that_differ_from_blocks():
    fields = parse_draft_markdown(DRAFT.replace(CONDITION, "Kleiner Kratzer oben."), CAT.text_blocks)
    assert fields["texts"] == ["payment"] and "Kleiner Kratzer oben." in fields["description"]
    plain = parse_draft_markdown(DRAFT)        # no blocks configured: everything is item text
    assert plain["texts"] == [] and PAYMENT in plain["description"]


def test_parse_draft_with_label_suffix_in_target_line():
    text = DRAFT.replace("- Zielpreis: 42 EUR", "- Zielpreis (ohne Scherkopf, daher Abzug): 42 EUR")
    assert parse_draft_markdown(text)["pricing"]["reduce_to"] == 42


@pytest.mark.parametrize("broken", [
    DRAFT.replace("## Titel", "## Überschrift"),
    DRAFT.replace("mit 49 EUR einstellen", "irgendwas"),
    DRAFT.replace("Zielpreis: 42", "Zielpreis: 60"),
])
def test_parse_draft_rejects_incomplete_drafts(broken):
    with pytest.raises(ValueError):
        parse_draft_markdown(broken)


def test_imported_listing_loads_and_is_unapproved(tmp_path):
    folder = _folder(tmp_path, "KVM Switch")
    draft = folder / "kvm_inserat_v1.md"
    draft.write_text(DRAFT, encoding="utf-8")
    written = import_draft(folder, draft, CAT.text_blocks)
    li = load_listing(folder, CAT)
    assert not li.approved and li.schedule.start == 49 and li.texts == ["condition", "payment"]
    with pytest.raises(FileExistsError):
        import_draft(folder, draft)
    assert written.read_text(encoding="utf-8").startswith("# Imported from kvm_inserat_v1.md")


def test_scan_reports_listing_importable_and_errors(tmp_path):
    ok = _folder(tmp_path, "A ok")
    _write(ok, {**BASE, "approved": True, "category": "161/225/sonstiges"})
    imp = _folder(tmp_path, "B import")
    (imp / "b_inserat_v1.md").write_text(DRAFT, encoding="utf-8")
    nophoto = _folder(tmp_path, "C nophoto", photos=0)
    _write(nophoto, BASE)
    bad = _folder(tmp_path, "D bad")
    _write(bad, {"title": "Ding Ding Ding"})
    (tmp_path / ".hidden").mkdir()

    states = {s.folder.name: s for s in scan(tmp_path, CAT)}
    assert set(states) == {"A ok", "B import", "C nophoto", "D bad"}
    assert states["A ok"].listing.approved and len(states["A ok"].listing.images) == 2
    assert states["B import"].importable.name == "b_inserat_v1.md"
    assert states["C nophoto"].error == "no photos"
    assert "description" in states["D bad"].error


def test_scan_missing_source_dir_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="source_dir"):
        scan(tmp_path / "missing")


@pytest.mark.parametrize("title, expected", [
    ("Kurz und gut", "Kurz und gut"),
    ("Datenschutzrecht DatSchR dtv Beck-Texte 16. Auflage 2024 (DSGVO, KI-VO, Data Act)",
     "Datenschutzrecht DatSchR dtv Beck-Texte 16. Auflage 2024"),
    ("DORMA SVP 6000 Panikschloss 12V AS - 6710 / 6719 - Schüco-Stulp, Panik B (Preis pro Schloss)",
     "DORMA SVP 6000 Panikschloss 12V AS - 6710 / 6719 - Schüco-Stulp"),
    ("x" * 80, "x" * 65),
])
def test_shorten_title_cuts_at_word_boundary(title, expected):
    got = shorten_title(title)
    assert got == expected and len(got) <= 65


def test_import_notes_shortened_title(tmp_path):
    folder = _folder(tmp_path, "Long")
    draft = folder / "l_inserat_v1.md"
    long = "Ein sehr langer Titel mit vielen Wörtern der nicht passt, weil er viel zu lang ist (Zusatz)"
    draft.write_text(DRAFT.replace("4K Dual DisplayPort KVM-Switch 2x2 USB 3.0 wie neu", long), encoding="utf-8")
    text = import_draft(folder, draft).read_text(encoding="utf-8")
    assert "# Title shortened" in text and long in text


def test_import_without_standard_sections_does_not_get_default_blocks(tmp_path):
    folder = _folder(tmp_path, "Scanner")
    draft = folder / "s_inserat_v1.md"
    draft.write_text(DRAFT.replace(CONDITION, "Eigener Zustandstext.").replace(PAYMENT, "Nur Abholung."),
                     encoding="utf-8")
    import_draft(folder, draft, CAT.text_blocks)
    li = load_listing(folder, CAT)
    assert li.texts == [] and CONDITION not in li.description and "Nur Abholung." in li.description


def test_render_listing_fills_template_keeps_comments_and_round_trips():
    from kassist.catalog import render_listing
    values = {"approved": False, "title": "Helm: Größe S", "description": "Zeile 1\n\nZeile 3  \n",
              "pricing": {"start": 100, "reduce_to": 90, "min_sale": 85}, "texts": ["condition", "payment"]}
    text = render_listing(values)
    assert "  start: 100" in text and "# start price EUR / Startpreis" in text
    assert "description: |" in text and "  Zeile 1\n\n  Zeile 3\n" in text
    assert "availability:" in text and "shipping:" in text and "seller:" in text   # all fields visible
    data = yaml.safe_load(text)
    assert data["title"] == "Helm: Größe S" and data["description"] == "Zeile 1\n\nZeile 3\n"
    assert data["pricing"]["start"] == 100 and data["pricing"]["min_sale"] == 85
    assert data["texts"] == ["condition", "payment"] and data["seller"] is None
    start_line = next(ln for ln in text.splitlines() if ln.startswith("  start:"))
    template_line = next(ln for ln in render_listing({}).splitlines() if ln.startswith("  start:"))
    assert start_line.index("#") == template_line.index("#")                      # comment column kept


def test_render_listing_without_values_equals_template():
    from kassist.catalog import TEMPLATE, render_listing
    assert render_listing({}) == TEMPLATE.read_text(encoding="utf-8")


def test_approval_needs_a_category(tmp_path):
    folder = _folder(tmp_path, "X")
    _write(folder, {**BASE, "approved": True})
    with pytest.raises(ValueError, match="category"):
        load_listing(folder, CAT)
    _write(folder, BASE)                                   # drafts may lack it
    assert load_listing(folder, CAT).category is None


def test_photo_order_cover_and_skip(tmp_path):
    folder = _folder(tmp_path, "P", photos=4)              # PXL_0..PXL_3
    _write(folder, {**BASE, "photos": {"order": ["PXL_2.jpg", "gone.jpg", "PXL_0.jpg"], "skip": ["PXL_1.jpg"]}})
    li = load_listing(folder, CAT)
    assert [p.name for p in li.images] == ["PXL_2.jpg", "PXL_0.jpg", "PXL_3.jpg"]
    _write(folder, BASE)
    assert [p.name for p in load_listing(folder, CAT).images] == [f"PXL_{i}.jpg" for i in range(4)]


def test_equal_steps_by_default_fixed_step_when_item_sets_it(tmp_path):
    cat = Catalog(defaults={"pricing": {"schedule": {"hold_days": 14, "steps": 3, "every_days": 7, "step": "5"}}})
    folder = _folder(tmp_path, "S")
    _write(folder, {**BASE, "pricing": {"start": 49, "reduce_to": 42}})
    assert load_listing(folder, cat).schedule.count == 3
    _write(folder, {**BASE, "pricing": {"start": 49, "reduce_to": 42, "schedule": {"step": "5"}}})
    li = load_listing(folder, cat)
    assert li.schedule.count == 0 and li.schedule.step == "5"
    _write(folder, {**BASE, "pricing": {"start": 49, "reduce_to": 42, "schedule": {"step": "5", "steps": 2}}})
    assert load_listing(folder, cat).schedule.count == 2


def test_text_blocks_follow_the_settings_order(tmp_path):
    folder = _folder(tmp_path, "O")
    _write(folder, {**BASE, "texts": ["legal", "condition"]})
    assert load_listing(folder, CAT).description == f"Kaum getragen.\n\n{CONDITION}\n\nPrivatverkauf."


def _cat_with_conditions():
    from dataclasses import replace
    from kassist.config import DEFAULTS
    return replace(CAT, condition_texts=DEFAULTS["condition_texts"])


def test_facts_ticked_go_into_the_text_others_stay_context(tmp_path):
    cat = _cat_with_conditions()
    folder = _folder(tmp_path, "Helm")
    _write(folder, {**BASE, "texts": ["legal"], "facts": {
        "condition": {"value": "like_new"}, "purchase_price": {"value": 249},
        "market_new": {"value": 199.9, "show": True}, "market_used": {"value": 120},
        "extra": [{"label": "Lieferumfang", "value": "Visier, Tasche"},
                  {"label": "Gekauft", "value": "2023", "show": False}, {"label": "", "value": "leer"}]}})
    li = load_listing(folder, cat)
    assert li.condition == "like_new" and li.purchase_price == 249
    assert li.description == ("Kaum getragen.\n\nZustand: gebraucht, wie neu – kaum benutzt, keine Gebrauchsspuren.\n"
                              "Kaufpreis: 249 €\nMarktneupreis: 199,90 €\nLieferumfang: Visier, Tasche"
                              "\n\nPrivatverkauf.")
    hidden = [(label, value) for label, value, shown in li.facts if not shown]
    assert hidden == [("Gebraucht gehandelt um", "120 €"), ("Gekauft", "2023")]   # median hidden by default
    _write(folder, {**BASE, "texts": [], "facts": {"condition": {"value": "defect", "show": False}}})
    li = load_listing(folder, cat)
    assert li.description == "Kaum getragen." and li.facts[0][2] is False
    for bad in ({"condition": {"value": "kaputt"}}, {"purchase_price": {"value": "viel"}}):
        _write(folder, {**BASE, "facts": bad})
        with pytest.raises(ValueError, match="facts"):
            load_listing(folder, cat)


def test_shown_fact_change_stages_a_content_change_hidden_does_not(tmp_path):
    from kassist.catalog import content_hash
    cat = _cat_with_conditions()
    folder = _folder(tmp_path, "Helm")
    _write(folder, {**BASE, "facts": {"condition": {"value": "good"}, "market_used": {"value": 50}}})
    before = content_hash(load_listing(folder, cat))
    _write(folder, {**BASE, "facts": {"condition": {"value": "good"}, "market_used": {"value": 60}}})
    assert content_hash(load_listing(folder, cat)) == before                  # context only: nothing to upload
    _write(folder, {**BASE, "facts": {"condition": {"value": "defect"}, "market_used": {"value": 60}}})
    assert content_hash(load_listing(folder, cat)) != before
