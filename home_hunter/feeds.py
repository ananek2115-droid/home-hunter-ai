"""Official property RSS feeds. Feed presence is not seller-confirmed availability."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .dedupe import normalize
from .discover import CITIES, _extract_area, _extract_price
from .model import Offer
from .storage import utc_now
from .verify import inspect, safe_public_url

FEEDS = (
    ("Domy Gdańsk", "https://domy.pl/rss/mieszkania--gdansk-pl", "Gdańsk"),
    ("Domy Gdynia", "https://domy.pl/rss/mieszkania--gdynia-pl", "Gdynia"),
    ("Domy Sopot", "https://domy.pl/rss/mieszkania--sopot-pl", "Sopot"),
    ("Domy Toruń", "https://domy.pl/rss/mieszkania--torun-pl", "Toruń"),
    ("Domiporta Pomorskie", "https://www.domiporta.pl/rss?CategoryName=mieszkania&Localizations%5B0%5D.Id=41&Rynek=Wtorny", None),
    ("Domiporta Kujawsko-Pomorskie", "https://www.domiporta.pl/rss?CategoryName=mieszkania&Localizations%5B0%5D.Id=32&Rynek=Wtorny", None),
)
HOSTS = {"domy.pl", "www.domy.pl", "domiporta.pl", "www.domiporta.pl"}
MAX_BYTES = 1_250_000

def tag(node):
    return node.tag.rsplit("}", 1)[-1].lower()

def node_text(node, *names):
    for child in node:
        if tag(child) in names:
            if child.text:
                return child.text.strip()
            if tag(child) == "link" and child.attrib.get("href"):
                return child.attrib["href"]
    return ""

def parse_feed(payload):
    root = ET.fromstring(payload)
    if tag(root) not in {"rss", "rdf", "feed"}:
        raise ValueError("Not RSS/Atom")
    entries = [e for e in root.iter() if tag(e) in {"item", "entry"}]
    out = []
    for e in entries[:150]:
        link = node_text(e, "link")
        title = node_text(e, "title")
        descr = node_text(e, "description", "summary", "encoded", "content")
        title = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", title)).split())[:250]
        descr = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", descr)).split())[:1200]
        if link and title:
            out.append({"url": link, "title": title, "snippet": descr})
    return out

class FeedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.hostname not in HOSTS:
            raise urllib.error.URLError("RSS redirect to unexpected host")
        return super().redirect_request(req, fp, code, msg, headers, url)

def fetch_feed(url):
    if url not in {feed[1] for feed in FEEDS}:
        raise ValueError("Not a configured official feed")
    req = urllib.request.Request(url, headers={
        "User-Agent": "HomeHunterRSS/0.3", "Accept": "application/rss+xml,application/xml",
    })
    with urllib.request.build_opener(FeedRedirect).open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError(f"RSS HTTP {response.status}")
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Oversized feed")
    return content

def listing_url(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host not in HOSTS or not safe_public_url(url):
        return None
    path = p.path
    if not path or path == "/" or path.startswith(("/rss", "/kanaly-rss")):
        return None
    if host.endswith("domy.pl"):
        # Domy typically embeds the numeric listing id in its URL.
        if not (re.search(r"[/_-]\d{5,}", path) or re.search(r"/mieszkanie/", path)):
            return None
    else:
        if not ("mieszkan" in path.lower() or "sprzedam" in path.lower()):
            return None
    return urllib.parse.urlunsplit(("https", host.removeprefix("www."), path, "", ""))

def candidate(entry, expected_city=None):
    url = listing_url(entry["url"])
    if url is None:
        return None
    text = " ".join((entry["title"], entry["snippet"], urllib.parse.unquote(urllib.parse.urlsplit(url).path)))
    norm = " " + normalize(text) + " "
    if not any(x in norm for x in (" mieszkanie", " mieszkani", " apartament", " pokoj", " kawalerk")):
        return None
    if any(x in norm for x in (" wynajme ", " do wynajecia ", " najem okazjonalny ")):
        return None
    matches = [c for c in CITIES if f" {normalize(c)} " in norm]
    if len(matches) == 1 and (expected_city is None or matches[0] == expected_city):
        city = matches[0]
    elif not matches and expected_city:
        city = expected_city
    else:
        return None
    price = _extract_price(entry["title"] + " " + entry["snippet"])
    area = _extract_area(entry["title"] + " " + entry["snippet"])
    if area is not None and area < 35:
        return None
    if price is not None and (price < 100000 or Decimal(price) * Decimal("1.02") + 5000 > 750000):
        return None
    return {"url": url, "city": city, "source": urllib.parse.urlsplit(url).hostname,
            "title": entry["title"], "snippet": entry["snippet"],
            "price_pln": price, "area_m2": area,
            "status": "preliminary" if price and area else "needs_details"}

def ingest_feeds(conn, fetch=fetch_feed):
    run_id = "official-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    now = utc_now()
    success = failures = new_total = 0
    for name, url, city in FEEDS:
        count = added = 0
        status = "ok"
        error = ""
        try:
            entries = parse_feed(fetch(url))
            count = len(entries)
            for entry in entries:
                item = candidate(entry, city)
                if not item:
                    continue
                if conn.execute("SELECT 1 FROM offers WHERE url IN (?,?)",
                                (item["url"], item["url"].replace("https://", "https://www.", 1))).fetchone():
                    continue
                old = conn.execute("SELECT 1 FROM discoveries WHERE url=?", (item["url"],)).fetchone()
                conn.execute(
                    """INSERT INTO discoveries
                    (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,snippet=excluded.snippet,
                    price_pln=COALESCE(excluded.price_pln,discoveries.price_pln),
                    area_m2=COALESCE(excluded.area_m2,discoveries.area_m2),
                    last_seen=excluded.last_seen""",
                    (item["url"], item["city"], item["source"], item["title"], item["snippet"],
                     item["price_pln"], item["area_m2"], item["status"], now, now))
                added += old is None
            new_total += added
            success += 1
        except (ValueError, OSError, TimeoutError, ET.ParseError, urllib.error.URLError) as exc:
            failures += 1
            status = "error"
            error = f"{type(exc).__name__}: {exc}"[:200]
        conn.execute(
            """INSERT INTO discovery_runs
             (run_id,run_at,city,query,status,results_count,new_count,error)
             VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, now, city or name, "feed:" + url, status, count, added, error))
        conn.commit()
        print(f"RSS {name}: {status}; wpisów {count}; nowych {added}" + (f"; {error}" if error else ""))
    return {"ok": success, "failed": failures, "new": new_total}

def verify_new(conn, limit=12, checker=inspect):
    """Page-check complete RSS candidates with existing robots-aware validator."""
    if limit < 0:
        raise ValueError("Negative limit")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT * FROM discoveries
           WHERE source IN ('domy.pl','domiporta.pl')
             AND price_pln IS NOT NULL AND area_m2 IS NOT NULL
             AND (verified_at IS NULL OR verified_at < ?)
           ORDER BY verified_at IS NOT NULL, verified_at, first_seen LIMIT ?""",
        (cutoff, limit)).fetchall()
    for row in rows:
        offer = Offer(source=row["source"], listing_id="", url=row["url"],
                      city=row["city"], street="", price_pln=Decimal(row["price_pln"]),
                      area_m2=Decimal(str(row["area_m2"])), rooms=1)
        result = checker(offer)
        conn.execute(
            """UPDATE discoveries SET verification_status=?,verified_at=?,
               verification_note=?,verification_http_status=? WHERE url=?""",
            (result.status, utc_now(), result.reason, result.http_status, row["url"]))
        conn.commit()
        print(f"VERIFY {row['city']}: {result.status} — {row['url']}")
    return len(rows)

def feed_report(conn, limit=35):
    last = conn.execute(
        "SELECT run_id FROM discovery_runs WHERE run_id LIKE 'official-%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    lines = ["## Automatyczne RSS: Domy.pl i Domiporta — Trójmiasto + Toruń", ""]
    if not last:
        return lines + ["_Brak uruchomienia._", ""]
    runs = conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (last["run_id"],)).fetchall()
    good = sum(r["status"] == "ok" for r in runs)
    lines += [f"Udane źródła: **{good}/{len(runs)}**. Nowe adresy: **{sum(r['new_count'] for r in runs)}**.",
              "RSS i dopasowanie strony nie potwierdzają dostępności u sprzedawcy.",
              ""]
    for r in runs:
        if r["status"] != "ok":
            lines.append(f"- Nieudany kanał: {r['city']}: {r['error']}")
    if not good:
        lines.append("**Brak danych źródłowych — nie interpretować jako brak ofert.**")
    rows = conn.execute(
        """SELECT * FROM discoveries WHERE source IN ('domy.pl','domiporta.pl')
           ORDER BY (verification_status='online_matched') DESC,
                    price_pln IS NULL, area_m2 IS NULL,
                    CAST(price_pln AS REAL)/NULLIF(area_m2,0), first_seen DESC LIMIT ?""",
        (limit,)).fetchall()
    lines += ["", "### Kandydaci — cena za m² (nie prognoza ROI)", ""]
    if not rows:
        lines.append("_Brak kandydatów w bazie RSS._")
    for r in rows:
        title = r["title"].replace("[", "(").replace("]", ")")[:110]
        price = f"{r['price_pln']:,} zł".replace(",", " ") if r["price_pln"] else "brak ceny"
        area = f"{r['area_m2']:g} m²" if r["area_m2"] else "brak metrażu"
        rate = f"{r['price_pln']/r['area_m2']:,.0f} zł/m²".replace(",", " ") if r["price_pln"] and r["area_m2"] else "nieustalona cena/m²"
        lines.append(f"- **{r['city']}** [{title}]({r['url']}) — {price}, {area}, {rate}; "
                     f"kontrola: {r['verification_status'] or 'tylko RSS'}; pierwszy raz: {r['first_seen']}")
    lines += ["", "Pełne koszty, prowizja, dokumenty i stan prawny wymagają niezależnego potwierdzenia.", ""]
    return lines
