import pytest
import yaml
from fastapi.testclient import TestClient

from kassist import engine, prices
from kassist.config import DEFAULTS, Settings, _merge
from kassist.prices import Offer, default_query, parse_price, parse_results, search_path, summarize
from kassist.state import Store

# Shape of the classic result list (aditem); fields read by class name fragments.
HTML = """
<ul id="srchrslt-adtable">
 <li class="ad-listitem"><article class="aditem" data-adid="111" data-href="/s-anzeige/dji-mini-2/111-1-2">
  <div class="aditem-main--top"><div class="aditem-main--top--left"> 20095 Hamburg </div>
   <div class="aditem-main--top--right"> Heute, 10:12 </div></div>
  <div class="aditem-main--middle"><h2 class="text-module-begin"><a class="ellipsis" href="/s-anzeige/dji-mini-2/111-1-2">DJI Mini 2 Fly More Combo</a></h2>
   <p class="aditem-main--middle--price-shipping--price"> 290 € VB </p>
   <p class="aditem-main--middle--price-shipping--old-price">320 €</p></div></article></li>
 <li class="ad-listitem"><article class="aditem" data-adid="112" data-href="/s-anzeige/x/112">
  <h2><a class="ellipsis" href="/s-anzeige/x/112">DJI Mini 2 defekt</a></h2>
  <p class="aditem-main--middle--price-shipping--price">80 €</p></article></li>
 <li class="ad-listitem"><article class="aditem" data-adid="113" data-href="/s-anzeige/y/113">
  <h2><a class="ellipsis" href="/s-anzeige/y/113">Mini 2 &amp; Akku</a></h2>
  <p class="aditem-main--middle--price-shipping--price">1.250 €</p></article></li>
 <li class="ad-listitem"><article class="aditem" data-adid="114"><h2><a class="ellipsis" href="/s-anzeige/z/114">Mini 2</a></h2>
  <p class="aditem-main--middle--price-shipping--price">VB</p></article></li>
 <li class="ad-listitem"><article class="aditem" data-adid="111"><h2>Doppelt</h2></article></li>
</ul>"""


@pytest.mark.parametrize("text, expected", [
    (" 290 € VB ", (290, True)), ("1.250 €", (1250, False)), ("VB", (None, True)),
    ("Zu verschenken", (0, False)), ("45,50 €", (45, False)), ("", (None, False)),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_parse_results_reads_fields_and_skips_duplicates():
    offers = parse_results(HTML)
    assert [o.id for o in offers] == ["111", "112", "113", "114"]
    first = offers[0]
    assert (first.title, first.price, first.negotiable) == ("DJI Mini 2 Fly More Combo", 290, True)
    assert first.url == "https://www.kleinanzeigen.de/s-anzeige/dji-mini-2/111-1-2"
    assert first.location == "20095 Hamburg" and first.date == "Heute, 10:12"
    assert offers[2].title == "Mini 2 & Akku" and offers[2].price == 1250
    assert offers[3].price is None


# Shape of the redesigned result list (utility classes only, images without closing tags, svg icons,
# ld+json per result). Synthetic content.
REDESIGN = """
<article class="flex justify-between p-medium" data-adid="201" data-href="/s-anzeige/mini-2/201-168-1?cid=x">
 <div class="relative"><script type="application/ld+json">{"title":"Mini 2","description":"99 €"}</script>
  <a href="/s-anzeige/mini-2/201-168-1?cid=x"><div><img class="size-full" src="a.jpg" alt="x"><div>6</div></div></a></div>
 <div class="z-raised flex grow pl-medium"><div class="mb-xsmall flex">
   <div class="flex"><svg viewBox="0 0 24 24"><path d="M1"/></svg><span>70599 Birkach</span></div>
   <div class="flex"><svg viewBox="0 0 24 24"><path d="M2"/></svg><span>Heute, 15:32</span></div></div>
  <div class="flex flex-col"><h3 class="line-clamp-2"><a href="/s-anzeige/mini-2/201-168-1?cid=x">DJI Mini 2 Combo</a></h3>
   <p class="text-bodyRegular">Mit drei Akkus, Preis 99 € fest...</p>
   <div class="flex"><p class="line-through">320 €</p><p class="my-xsmall text-title3">275 € VB</p></div></div>
  <div><p class="flex"><span>Direkt kaufen</span><span>Versand möglich</span></p></div></div></article>
<article class="flex" data-adid="202" data-href="/s-anzeige/x/202"><img src="b.jpg">
 <div><span>20095 Hamburg</span><span>12.11.2026</span></div><h3><a href="/s-anzeige/x/202">Mini 2 Fly More</a></h3>
 <p>Preis: VB</p><p>VB</p></article>"""


def test_parse_redesigned_results():
    offers = parse_results(REDESIGN)
    assert [o.id for o in offers] == ["201", "202"]
    a, b = offers
    assert (a.title, a.price, a.negotiable) == ("DJI Mini 2 Combo", 275, True)
    assert (a.location, a.date) == ("70599 Birkach", "Heute, 15:32")
    assert a.url == "https://www.kleinanzeigen.de/s-anzeige/mini-2/201-168-1"
    assert (b.title, b.price, b.negotiable, b.location, b.date) == ("Mini 2 Fly More", None, True,
                                                                    "20095 Hamburg", "12.11.2026")


def test_old_price_before_current_price_is_ignored():
    html = """<article data-adid="9"><h2><a class="ellipsis" href="/s-anzeige/a/9">Helm</a></h2>
      <p class="aditem-main--middle--price-shipping--old-price">120 €</p>
      <p class="aditem-main--middle--price-shipping--price">95 € VB</p></article>"""
    assert parse_results(html)[0].price == 95


def test_summarize_excludes_words_own_ads_and_outliers():
    offers = [Offer(str(i), f"Mini 2 Nr {i}", p, False, "") for i, p in enumerate([250, 260, 280, 290, 300, 310, 2000])]
    offers += [Offer("x", "Mini 2 defekt", 50, False, ""), Offer("own", "Mini 2", 999, False, ""),
               Offer("vb", "Mini 2", None, True, "")]
    s = summarize(offers, own_ids={"own"})
    assert s["found"] == 10 and s["used"] == 6 and s["outliers"] == 1
    assert (s["min"], s["median"], s["max"]) == (250, 285, 310)
    assert s["p25"] <= s["median"] <= s["p75"]


def test_summarize_without_prices():
    assert summarize([Offer("1", "Suche Mini 2", 100, False, "")]) == {"found": 1, "used": 0, "outliers": 0}


@pytest.mark.parametrize("title, query", [
    ("DJI Mini 2 Fly More Combo wie neu, unfallfrei, mit OVP", "DJI Mini 2 Fly More"),
    ("Braun Series 9 Pro Rasierer mit Reinigungsstation - OHNE Klinge", "Braun Series 9 Pro Rasierer"),
    ("AVM FRITZ!Box 7590 DSL-Router mit Netzteil", "AVM FRITZ!Box 7590 DSL-Router Netzteil"),
])
def test_default_query(title, query):
    assert default_query(title) == query


def test_search_path_encodes_and_pages():
    assert search_path("Fritz!Box 7590") == "/s-suchanfrage.html?keywords=Fritz%21Box+7590"
    assert search_path("a b", 2).endswith("&pageNum=2")


def test_price_check_stores_summary_and_panel_shows_it(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "Drohne").mkdir(parents=True)
    (src / "Drohne" / "a.jpg").write_bytes(b"\xff\xd8")
    (src / "Drohne" / "listing.yaml").write_text(yaml.safe_dump({
        "title": "DJI Mini 2 Fly More Combo wie neu", "description": "x",
        "pricing": {"start": 330, "reduce_to": 270}, "price_check": {"exclude": ["akku"]}}), encoding="utf-8")
    settings = Settings(_merge(DEFAULTS, {"source_dir": str(src), "data_dir": str(tmp_path / "data")}))
    store = Store(settings.db_path)
    seen = []

    class FakePortal:
        def __init__(self, port, require_login=True):
            assert require_login is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get_html(self, path):
            seen.append(path)
            return HTML

    monkeypatch.setattr(engine, "Portal", FakePortal)
    s = engine.price_check(settings, store, key="Drohne", sleep=lambda x: None)
    assert seen == ["/s-suchanfrage.html?keywords=DJI+Mini+2+Fly+More"]   # < 20 results: one page only
    assert s["query"] == "DJI Mini 2 Fly More" and s["used"] == 1 and s["median"] == 290  # defekt, Akku dropped
    assert store.last_price_check("Drohne")["summary"]["median"] == 290
    store.close()



def test_query_words_must_all_appear_as_whole_words():
    offers = [Offer("1", "DJI Mini 2 Fly More Combo", 270, False, ""),
              Offer("2", "DJI Mini 5 Pro Fly More Combo", 750, False, ""),
              Offer("3", "DJI Mini 2 SE Fly More", 200, False, ""),
              Offer("4", "dji mini 2, fly more", 250, False, ""),
              Offer("5", "DJI Mini 20 Fly More", 999, False, ""),
              Offer("6", "DJI Mini 5 Pro Fly More Combo mit RC 2", 800, False, ""),
              Offer("7", "DJI Mini-2 Fly More", 230, False, "")]
    s = summarize(offers, query="DJI Mini 2 Fly More")
    assert s["used"] == 4 and s["max"] == 270
    assert summarize(offers)["used"] == 7


def test_long_queries_need_most_words_and_hyphens_split():
    q = "4K Dual DisplayPort KVM-Switch 2x2"
    assert matches_query("KVM Switch 2x2 DisplayPort 4K USB 3.0", q)        # 5 of 6 words
    assert matches_query("Dual Monitor KVM-Switch DisplayPort", q)           # 4 of 6
    assert not matches_query("HDMI Switch 4K", q)                            # 2 of 6
    assert not matches_query("Mini 5 Pro Fly More RC 2", "DJI Mini 2 Fly More")
    assert not matches_query("Galaxy S23", "iPhone 13")                     # number missing


from kassist.prices import matches_query  # noqa: E402
