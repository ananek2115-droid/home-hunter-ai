"""Conservative, autonomous discovery via Bing public RSS search index.

The index is not proof of an active listing or current price. No portal scraping.
"""
from __future__ import annotations

import re
import uuid
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .dedupe import normalize
from .storage import utc_now
from .verify import safe_public_url

CITIES = ("Gdańsk", "Gdynia", "Sopot", "Toruń")
SITES = (("olx.pl", "/d/oferta/"), ("otodom.pl", "/pl/oferta/"))
MAX_FEED_BYTES = 500_000
BUDGET = 750_000
MIN_AREA = 35
SEARCH_USER_AGENT = "HomeHunterPersonalRssReader/0.2"
PRICE_RE = re.compile(
    r"(?<!\d)((?:[1-9]\d{2}(?:[ \u00a0.]\d{3})+)|(?:[1-9]\d{5,6}))\s*(?:zł|zl|pln)\b",
    re.IGNORECASE,
)
AREA_RE = re.compile(r"(?<!\d)(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:m²|m2|m\s*kw\.?)\b", re.IGNORECASE)


def search_queries():
    return tuple(
        (city, f"site:{domain}{path} mieszkanie sprzedaż {city}")
        for city in CITIES for domain, path in SITES
    )


class BingOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"www.bing.com", "bing.com"}:
            raise urllib.error.URLError("Unexpected RSS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_rss(query):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "setlang": "pl"}
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": SEARCH_USER_AGENT, "Accept": "application/rss+xml,application/xml"},
    )
    opener = urllib.request.build_opener(BingOnlyRedirect)
    with opener.open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError(f"Bing RSS HTTP {response.status}")
        body = response.read(MAX_FEED_BYTES + 1)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError("Bing RSS exceeds size limit")
        return body


def parse_rss(data):
    root = ET.fromstring(data)
    if root.tag.lower() != "rss" or root.find("channel") is None:
        raise ValueError("Search provider did not return RSS XML")
    result = []
    for item in root.findall("./channel/item")[:20]:
        url = (item.findtext("link") or "").strip()
        title = " ".join((item.findtext("title") or "").split())[:240]
        snippet = " ".join((item.findtext("description") or "").split())[:650]
        if url and title:
            result.append({"url": url, "title": title, "snippet": snippet})
    return result


def canonical_listing_url(url):
    if not safe_public_url(url):
        return None
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    patterns = dict(SITES)
    if hostname not in patterns or not parsed.path.startswith(patterns[hostname]):
        return None
    return urllib.parse.urlunsplit(("https", hostname, parsed.path, "", ""))


def _extract_price(text):
    candidates = [int(re.sub(r"\D", "", m.group(1))) for m in PRICE_RE.finditer(text)]
    return min((p for p in candidates if 100_000 <= p <= 20_000_000), default=None)


def _extract_area(text):
    values = [float(m.group(1).replace(",", ".")) for m in AREA_RE.finditer(text)]
    return min((a for a in values if 15 <= a <= 350), default=None)


def candidate_from_result(city, item):
    url = canonical_listing_url(item["url"])
    if url is None:
        return None
    text = item["title"] + " " + item["snippet"]
    normalized = " " + normalize(text) + " "
    matches = [c for c in CITIES if f" {normalize(c)} " in normalized]
    if matches != [city]:
        return None
    if not any(x in normalized for x in (" mieszkanie ", " mieszkani", " pokoj", " lokal ")):
        return None
    price = _extract_price(text)
    area = _extract_area(text)
    if area is not None and area < MIN_AREA:
        return None
    # Minimum estimated costs only; agent fee and renovation remain unknown.
    if price is not None and price * 1.02 + 5000 > BUDGET:
        return None
    return {
        "url": url, "city": city,
        "source": urllib.parse.urlsplit(url).hostname,
        "title": item["title"], "snippet": item["snippet"],
        "price_pln": price, "area_m2": area,
        "status": "preliminary" if price is not None and area is not None else "needs_details",
    }


def run_discovery(conn, fetch=fetch_rss):
    run_id = uuid.uuid4().hex
    run_at = utc_now()
    new = updated = ok = failed = 0
    for city, query in search_queries():
        count = added = 0
        status, error = "ok", ""
        try:
            items = parse_rss(fetch(query))
            count = len(items)
            for item in items:
                candidate = candidate_from_result(city, item)
                if candidate is None:
                    continue
                if conn.execute("SELECT 1 FROM offers WHERE url=?", (candidate["url"],)).fetchone():
                    continue
                old = conn.execute("SELECT 1 FROM discoveries WHERE url=?", (candidate["url"],)).fetchone()
                conn.execute(
                    """INSERT INTO discoveries
                      (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                      title=excluded.title, snippet=excluded.snippet,
                      price_pln=COALESCE(excluded.price_pln,discoveries.price_pln),
                      area_m2=COALESCE(excluded.area_m2,discoveries.area_m2),
                      status=CASE WHEN excluded.status='preliminary'
                                  THEN 'preliminary' ELSE discoveries.status END,
                      last_seen=excluded.last_seen""",
                    (candidate["url"], candidate["city"], candidate["source"],
                     candidate["title"], candidate["snippet"], candidate["price_pln"],
                     candidate["area_m2"], candidate["status"], run_at, run_at),
                )
                if old is None:
                    added += 1
                    new += 1
                else:
                    updated += 1
            ok += 1
        except (OSError, TimeoutError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
            failed += 1
            status = "error"
            error = f"{type(exc).__name__}: {exc}"[:250]
        conn.execute(
            """INSERT INTO discovery_runs
              (run_id,run_at,city,query,status,results_count,new_count,error)
              VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, run_at, city, query, status, count, added, error),
        )
        conn.commit()
        print(f"RSS {city}: {status}, wyników {count}, nowych {added}"
              + (f", błąd {error}" if error else ""))
    return {"run_id": run_id, "ok": ok, "failed": failed, "new": new, "updated": updated}


def discovery_report_lines(conn, max_items=30):
    last = conn.execute("SELECT run_id FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone()
    if last is None:
        return ["## Automatyczne wyszukiwanie: Trójmiasto i Toruń", "",
                "_Brak uruchomienia modułu odkrywania ogłoszeń._", ""]
    runs = conn.execute(
        "SELECT * FROM discovery_runs WHERE run_id=? ORDER BY id", (last["run_id"],)
    ).fetchall()
    successful = sum(row["status"] == "ok" for row in runs)
    errors = [row for row in runs if row["status"] != "ok"]
    count = sum(row["new_count"] for row in runs)
    lines = [
        "## Automatyczne wyszukiwanie: Trójmiasto i Toruń", "",
        f"Indeks Bing RSS: {successful}/{len(runs)} zapytań udanych; "
        f"nowe adresy znalezione w ostatnim uruchomieniu: {count}.",
        "Nowe oznacza pierwszy raz wykryte przez tego agenta, a NIE nowo opublikowane. "
        "Wynik wyszukiwarki nie potwierdza dostępności, ceny, prowizji ani aktualności.",
        "",
    ]
    if errors:
        lines += ["### Problemy ze źródłami", ""]
        for row in errors:
            lines.append(f"- {row['city']}: {row['error']}")
        lines.append("")
    if successful == 0:
        lines += ["**UWAGA: nie udało się pobrać żadnego źródła; to NIE jest brak ofert.**", ""]
    listings = conn.execute(
        "SELECT * FROM discoveries ORDER BY first_seen DESC, city, url LIMIT ?",
        (max_items,),
    ).fetchall()
    lines += ["### Kandydaci, wymagający potwierdzenia u sprzedawcy", ""]
    if not listings:
        lines += ["_Brak kandydatów w bazie odkryć._", ""]
    for item in listings:
        title = " ".join(item["title"].replace("[", "(").replace("]", ")").split())[:120]
        price = (f"{item['price_pln']:,} zł".replace(",", " ")
                 if item["price_pln"] is not None else "cena nieznana")
        area = (f"{item['area_m2']} m²" if item["area_m2"] is not None
                else "metraż nieznany")
        lines.append(
            f"- {item['city']}: [{title}]({item['url']}) — {price}, {area}; "
            f"status {item['status']}; pierwszy raz wykryto {item['first_seen']}."
        )
    lines += ["", "Wstępny limit kosztów: cena + 2% PCC + 5 tys. zł rezerwy. "
              "Nieznane koszty, prowizja i remont mogą przekroczyć 750 tys. zł.", ""]
    return lines
