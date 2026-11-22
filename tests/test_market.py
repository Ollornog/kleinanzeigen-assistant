
import pytest
import yaml

from kassist import catalog, engine, llm, market_ai
from kassist.catalog import MARKET_MARKER, write_market
from kassist.config import DEFAULTS, Settings, _merge
from kassist.prices import Offer, parse_results
from kassist.state import Store

LISTING = """# my notes stay
approved: false
title: KVM-Switch 2x2 DisplayPort
description: |
  Wie neu, ohne Kabel.
pricing:
  start: 49   # my price
  reduce_to: 42
"""


def entry(n):
    return {"at": f"2026-11-2{n} 10:00", "query": "kvm", "offers": 10, "used": 8,
            "min": 20 + n, "p25": 25, "median": 30 + n, "p75": 35, "max": 40 + n}


def test_write_market_keeps_owner_text_and_last_three(tmp_path):
    (tmp_path / "listing.yaml").write_text(LISTING, encoding="utf-8")
    for n in range(1, 5):
        history = write_market(tmp_path, entry(n))
    text = (tmp_path / "listing.yaml").read_text(encoding="utf-8")
    assert text.startswith(LISTING.rstrip("\n")) and "# my price" in text
    assert text.count(MARKET_MARKER) == 1
    data = yaml.safe_load(text)
    assert [e["median"] for e in data["market"]] == [34, 33, 32] == [e["median"] for e in history]
    assert data["pricing"] == {"start": 49, "reduce_to": 42}


def test_market_block_does_not_break_loading(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "listing.yaml").write_text(LISTING, encoding="utf-8")
    write_market(tmp_path, entry(1))
    assert catalog.load_listing(tmp_path).schedule.start == 49


def test_hand_written_market_block_is_not_overwritten(tmp_path):
    (tmp_path / "listing.yaml").write_text(LISTING + "market:\n  - {median: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hand-written"):
        write_market(tmp_path, entry(1))


def test_snippet_from_ld_json():
    html = """<article data-adid="7"><script type="application/ld+json">{"title":"x","description":"Mit 3 Akkus\\nund Tasche"}</script>
      <h3><a href="/s-anzeige/a/7">Mini 2</a></h3><p>210 €</p></article>"""
    [o] = parse_results(html)
    assert o.snippet == "Mit 3 Akkus und Tasche" and o.price == 210


AI_ANSWER = """Here you go:
```json
{"offers": [
  {"id": "1", "comparable": true, "condition": "good", "diff": "mit Kabeln", "adjusted": 35},
  {"id": "2", "comparable": true, "condition": "like_new", "diff": "gleich", "adjusted": 45},
  {"id": "3", "comparable": false, "condition": "used", "diff": "4x4 Modell", "adjusted": 99},
  {"id": "4", "comparable": true, "condition": "defective", "diff": "defekt", "adjusted": 0}],
 "suggestion": {"start": 45, "reduce_to": 38, "min_sale": 35},
 "note": "Wenig Angebote."}
```"""


def test_ai_answer_parsing_and_summary():
    data = market_ai.parse_answer(AI_ANSWER)
    s = market_ai.summarize(data)
    assert s == {"comparable": 2, "min": 35, "median": 40, "max": 45,
                 "suggestion": {"start": 45, "reduce_to": 38, "min_sale": 35}, "note": "Wenig Angebote."}
    bad = market_ai.parse_answer(AI_ANSWER)
    bad["suggestion"] = {"start": 30, "reduce_to": 38, "min_sale": 35}     # inconsistent: dropped
    got = market_ai.summarize(bad)
    assert "suggestion" not in got and got["suggestion_raw"] == {"start": 30, "reduce_to": 38, "min_sale": 35}
    with pytest.raises(ValueError):
        market_ai.parse_answer("no json here")


def test_ai_prompt_contains_our_item_and_priced_offers():
    offers = [Offer("1", "KVM 2x2", 35, True, "", snippet="mit Kabeln"), Offer("2", "KVM", None, True, "")]
    p = market_ai.build_prompt("KVM-Switch", "Wie neu", offers)
    assert "id=1 | 35 EUR VB | KVM 2x2 | mit Kabeln" in p and "id=2" not in p and "Wie neu" in p


def test_price_check_with_ai_writes_market_into_listing(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "KVM").mkdir(parents=True)
    (src / "KVM" / "a.jpg").write_bytes(b"\xff\xd8")
    (src / "KVM" / "listing.yaml").write_text(LISTING, encoding="utf-8")
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    store = Store(settings.db_path)
    html = "".join(f'<article data-adid="{i}"><h3><a href="/s-anzeige/x/{i}">KVM-Switch 2x2 DisplayPort Nr {i}</a></h3>'
                   f'<p>{p} €</p></article>' for i, p in [(1, 30), (2, 40), (3, 50), (4, 45)])

    class FakePortal:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get_html(self, path):
            return html

    prompts = []
    monkeypatch.setattr(engine, "Portal", FakePortal)
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: prompts.append(prompt) or AI_ANSWER)
    s = engine.price_check(settings, store, key="KVM", ai=True, sleep=lambda x: None)
    assert s["median"] == 42 and s["ai"]["median"] == 40 and "Wie neu, ohne Kabel." in prompts[0]
    assert s["ai"]["per_offer"]["3"]["comparable"] is False
    data = yaml.safe_load((src / "KVM" / "listing.yaml").read_text(encoding="utf-8"))
    [m] = data["market"]
    assert (m["min"], m["median"], m["max"], m["used"]) == (30, 42, 50, 4)
    assert m["ai"]["suggestion"] == {"start": 45, "reduce_to": 38, "min_sale": 35} and "per_offer" not in m["ai"]
    assert data["pricing"]["start"] == 49            # own prices untouched
    store.close()


def test_ai_result_is_kept_even_without_usable_search_prices(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "KVM").mkdir(parents=True)
    (src / "KVM" / "a.jpg").write_bytes(b"\xff\xd8")
    (src / "KVM" / "listing.yaml").write_text(LISTING, encoding="utf-8")
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    store = Store(settings.db_path)

    class FakePortal:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get_html(self, path):
            return '<article data-adid="1"><h3><a href="/s-anzeige/x/1">HDMI Kabel</a></h3><p>9 €</p></article>'

    monkeypatch.setattr(engine, "Portal", FakePortal)
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: AI_ANSWER)
    s = engine.price_check(settings, store, key="KVM", ai=True, sleep=lambda x: None)
    assert s["used"] == 0
    [m] = yaml.safe_load((src / "KVM" / "listing.yaml").read_text(encoding="utf-8"))["market"]
    assert m["used"] == 0 and "median" not in m and m["ai"]["median"] == 40
    store.close()


def test_price_check_works_for_listing_not_ready_for_approval(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "KVM").mkdir(parents=True)
    (src / "KVM" / "a.jpg").write_bytes(b"\xff\xd8")
    (src / "KVM" / "listing.yaml").write_text(LISTING.replace("approved: false", "approved: true"), encoding="utf-8")
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    store = Store(settings.db_path)

    class FakePortal:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get_html(self, path):
            return '<article data-adid="1"><h3><a href="/s-anzeige/x/1">KVM-Switch 2x2 DisplayPort</a></h3><p>40 €</p></article>'

    monkeypatch.setattr(engine, "Portal", FakePortal)
    monkeypatch.setattr(llm, "complete", lambda s, prompt, json_mode=False: AI_ANSWER)
    s = engine.price_check(settings, store, key="KVM", ai=True, sleep=lambda x: None)    # approved, no category
    assert s["ai"]["median"] == 40
    assert yaml.safe_load((src / "KVM" / "listing.yaml").read_text(encoding="utf-8"))["market"][0]["ai"]["median"] == 40
    store.close()


def test_ai_adjusted_prices_drop_outliers_and_name_our_specs():
    data = {"ours": {"specs": {"ports": "2x2", "monitors": 2}, "brand": "no-name"},
            "offers": [{"id": str(i), "comparable": True, "adjusted": a} for i, a in enumerate([40, 42, 45, 48, 50, 180])]
            + [{"id": "x", "comparable": False, "adjusted": 999}]}
    s = market_ai.summarize(data)
    assert s["comparable"] == 6 and s["outliers"] == 1 and s["max"] == 50 and s["median"] == 45
    assert s["ours"] == "ports: 2x2, monitors: 2 · no-name"
