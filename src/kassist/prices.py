"""Price overview from a Kleinanzeigen search: asking prices of similar ads (not sale prices).

The search page is fetched by the browser tab like a normal search (few pages, pauses).
Parsing is tolerant: the result list is read from `article` elements with a data-adid.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus

DEFAULT_EXCLUDE = ("defekt", "suche", "tausch", "ersatzteil", "bastler", "kaputt", "nur ")


@dataclass
class Offer:
    id: str
    title: str
    price: int | None
    negotiable: bool
    url: str
    location: str = ""
    date: str = ""
    snippet: str = ""        # start of the ad text as shown in the result list


_FILLER = {"wie", "neu", "neuwertig", "top", "zustand", "ovp", "mit", "ohne", "und", "inkl", "inkl.", "gebraucht",
           "sehr", "gut", "guter", "originalverpackung", "unfallfrei", "selten", "benutzt", "zu", "verkaufen"}


def default_query(title: str, max_words: int = 5) -> str:
    """Search words from a listing title: up to the first separator, without filler words."""
    head = re.split(r",| - | – |\(| / |\|", title, maxsplit=1)[0]
    words = [w for w in head.split() if w.lower().strip(".!") not in _FILLER]
    return " ".join(words[:max_words]) or title[:40]


def search_path(query: str, page: int = 1) -> str:
    path = f"/s-suchanfrage.html?keywords={quote_plus(query)}"
    return path if page == 1 else f"{path}&pageNum={page}"


def parse_price(text: str) -> tuple[int | None, bool]:
    """'1.234 € VB' -> (1234, True); 'VB' -> (None, True); 'Zu verschenken' -> (0, False)."""
    t = " ".join((text or "").split())
    negotiable = "VB" in t
    if "verschenken" in t.lower():
        return 0, False
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*€", t)
    if not m:
        return None, negotiable
    return int(m.group(1).replace(".", "")), negotiable


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "svg", "noscript"}
_PRICE_CHUNK = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?\s*€(?:\s*VB)?$|^VB$|^Zu verschenken$", re.I)
_LOCATION_CHUNK = re.compile(r"^\d{5}\s+\S")
_DATE_CHUNK = re.compile(r"^(Heute|Gestern|\d{2}\.\d{2}\.\d{4})", re.I)


class _ResultParser(HTMLParser):
    """Collects the text pieces of each result article in order, with the context they appear in.

    Field detection is by content, not by class names (the redesigned site uses utility classes
    only): title = text in a heading, price = a piece that is only a price, location = starts
    with a postcode, date = Heute/Gestern/dd.mm.yyyy. Struck-through old prices are skipped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.offers: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None
        self._stack: list[tuple[str, bool]] = []   # (tag, marks an old price)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "article" and a.get("data-adid") and self._cur is None:
            self._cur = {"id": a["data-adid"], "url": a.get("data-href", ""), "chunks": []}
            self._stack = [("article", False)]
            return
        if self._cur is None or tag in _VOID:
            return
        if tag == "script" and a.get("type") == "application/ld+json":
            self._cur["ld"] = ""
        if tag == "a" and not self._cur["url"] and a.get("href", "").startswith("/s-anzeige/"):
            self._cur["url"] = a["href"]
        old = tag in ("s", "del", "strike") or "old-price" in a.get("class", "") or "line-through" in a.get("class", "")
        self._stack.append((tag, old))

    def handle_endtag(self, tag: str) -> None:
        if self._cur is None or tag in _VOID:
            return
        # pop up to the matching tag (tolerates unclosed children)
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break
        if not self._stack:
            self.offers.append(self._cur)
            self._cur = None

    def handle_data(self, data: str) -> None:
        if self._cur is None:
            return
        tags = [t for t, _ in self._stack]
        if tags and tags[-1] == "script" and "ld" in self._cur and not self._cur.get("ld_done"):
            self._cur["ld"] += data
            try:
                desc = json.loads(self._cur["ld"]).get("description")
                self._cur["snippet"] = " ".join(str(desc or "").split())
                self._cur["ld_done"] = True
            except ValueError:
                pass
            return
        if any(t in _SKIP_TEXT for t in tags):
            return
        text = " ".join(data.split())
        if text:
            self._cur["chunks"].append((text, any(t in ("h2", "h3") for t in tags), any(o for _, o in self._stack)))


def _fields(chunks: list[tuple[str, bool, bool]]) -> dict[str, str]:
    title = " ".join(t for t, heading, _ in chunks if heading)
    price = next((t for t, heading, old in chunks if not heading and not old and _PRICE_CHUNK.match(t)), "")
    location = next((t for t, heading, _ in chunks if not heading and _LOCATION_CHUNK.match(t)), "")
    when = next((t for t, heading, _ in chunks if not heading and _DATE_CHUNK.match(t)), "")
    return {"title": title, "price": price, "location": location, "date": when}


def parse_results(html: str) -> list[Offer]:
    p = _ResultParser()
    p.feed(html)
    out = []
    seen = set()
    for o in p.offers:
        f = _fields(o["chunks"])
        if o["id"] in seen or not f["title"]:
            continue
        seen.add(o["id"])
        price, vb = parse_price(f["price"])
        url = o["url"].split("?")[0]
        out.append(Offer(id=o["id"], title=f["title"], price=price, negotiable=vb,
                         url=("https://www.kleinanzeigen.de" + url) if url.startswith("/") else url,
                         location=f["location"], date=f["date"], snippet=o.get("snippet", "")[:600]))
    return out


def _quantile(sorted_vals: list[int], q: float) -> float:
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def matches_query(title: str, query: str | None, min_share: float = 0.6) -> bool:
    """Relevance of a search result: model numbers right after their word, and most other search words
    as whole words in the title (search results also list similar models and accessories)."""
    if not query:
        return True
    low = title.lower().replace("-", " ")
    words = query.lower().replace("-", " ").split()
    has = lambda w: re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", low)  # noqa: E731
    # a model number must be there and follow its word directly ("Mini 2", "Mini-2", "Mini2"), not e.g. "RC 2"
    for i, word in enumerate(words):
        if word.isdigit():
            if i == 0 and not has(word):
                return False
            if i and not re.search(r"(?<![\w])" + re.escape(words[i - 1]) + r"\s?" + re.escape(word) + r"(?![\w])", low):
                return False
    others = [w for w in words if not w.isdigit()]
    return not others or sum(1 for w in others if has(w)) >= min_share * len(others)


def summarize(offers: list[Offer], exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
              own_ids: set[str] | None = None, query: str | None = None) -> dict[str, Any]:
    """Median and quartiles of usable asking prices; off-query titles, excluded words and IQR outliers dropped."""
    own_ids = own_ids or set()
    usable = [o for o in offers if o.price and o.id not in own_ids and matches_query(o.title, query)
              and not any(w in o.title.lower() for w in exclude)]
    prices = sorted(o.price for o in usable)
    result: dict[str, Any] = {"found": len(offers), "used": 0, "outliers": 0}
    if not prices:
        return result
    q1, q3 = _quantile(prices, 0.25), _quantile(prices, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    kept = [p for p in prices if lo <= p <= hi] if len(prices) >= 4 else prices
    result.update({
        "used": len(kept),
        "outliers": len(prices) - len(kept),
        "min": kept[0], "max": kept[-1],
        "p25": round(_quantile(kept, 0.25)), "median": round(statistics.median(kept)),
        "p75": round(_quantile(kept, 0.75)),
    })
    return result


def as_rows(offers: list[Offer]) -> list[dict[str, Any]]:
    return [asdict(o) for o in offers]
