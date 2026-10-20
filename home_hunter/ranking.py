"""Data quality gate and actionable report for independently discovered properties.

Historical discoveries remain in SQLite but are excluded from recommendations
when source details contradict the intended city, area or budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .discover import CITIES, candidate_from_result
from .feeds import candidate as feed_candidate

OFFICIAL_SOURCES = {"domy.pl", "domiporta.pl"}
ALL_SOURCES = OFFICIAL_SOURCES | {"olx.pl", "otodom.pl"}
BUDGET = Decimal("750000")
MIN_AREA = Decimal("35")


def _date(value):
    if not value:
        return None
    try:
        instant = datetime.fromisoformat(value)
        return instant.astimezone(timezone.utc) if instant.tzinfo else None
    except (TypeError, ValueError):
        return None


def _recent(value, hours=48, now=None):
    instant = _date(value)
    now = now or datetime.now(timezone.utc)
    return instant is not None and timedelta(0) <= now-instant <= timedelta(hours=hours)


def minimum_cost(price):
    """Lower bound, not an all-in investment or personalized tax calculation."""
    if price is None:
        return None
    return Decimal(str(price)) * Decimal("1.02") + Decimal("5000")


def _clean_candidate(row):
    source = row["source"].removeprefix("www.")
    listing = {"url": row["url"], "title": row["title"], "snippet": row["snippet"] or ""}
    if source in OFFICIAL_SOURCES:
        return feed_candidate(listing)
    if source in ("olx.pl", "otodom.pl") and row["city"] in CITIES:
        return candidate_from_result(row["city"], listing)
    return None


def reconcile_discoveries(conn):
    """Idempotent migration of historical discoveries, keeping original timestamps.

    Reject false locations, 26m² flats and >750k listings that slipped through an
    earlier parser. A corrected price/area invalidates any previous verification.
    """
    changed = rejected = 0
    rows = conn.execute("SELECT * FROM discoveries ORDER BY first_seen").fetchall()
    for row in rows:
        parsed = _clean_candidate(row)
        if parsed is None:
            if row["status"] != "excluded":
                conn.execute(
                    """UPDATE discoveries SET status='excluded',
                    verification_status=NULL, verified_at=NULL,
                    verification_note='Rejected by city, listing URL, area or budget validation',
                    verification_http_status=NULL WHERE url=?""",
                    (row["url"],),
                )
                rejected += 1
            continue
        significant = any(row[key] != parsed[key] for key in ("city", "price_pln", "area_m2"))
        new_status = parsed["status"]
        if significant or row["status"] != new_status:
            conn.execute(
                """UPDATE discoveries
                SET city=?, price_pln=?, area_m2=?, status=?,
                verification_status=CASE WHEN ? THEN NULL ELSE verification_status END,
                verified_at=CASE WHEN ? THEN NULL ELSE verified_at END,
                verification_note=CASE WHEN ? THEN 'Source details changed; check again' ELSE verification_note END,
                verification_http_status=CASE WHEN ? THEN NULL ELSE verification_http_status END
                WHERE url=?""",
                (parsed["city"], parsed["price_pln"], parsed["area_m2"], new_status,
                 significant, significant, significant, significant, row["url"]),
            )
            changed += 1
    conn.commit()
    return {"examined": len(rows), "corrected": changed, "excluded": rejected}


def _label(row, now):
    check = row["verification_status"]
    if check == "online_matched" and _recent(row["verified_at"], 48, now):
        return "strona dopasowana (bez potwierdzenia sprzedającego)"
    if check == "online_matched":
        return "dawne dopasowanie — wymaga ponownej kontroli"
    if check == "access_restricted":
        return "portal blokuje kontrolę — niepotwierdzone"
    if check in ("removed_link", "content_mismatch", "invalid_url"):
        return "link nieprawidłowy/niedopasowany — do odrzucenia"
    if check == "needs_manual_review":
        return "weryfikacja ręczna potrzebna"
    return "jeszcze bez weryfikacji strony"


def _eligible(row):
    if row["status"] == "excluded" or row["city"] not in CITIES:
        return False
    p, a = row["price_pln"], row["area_m2"]
    return p is not None and a is not None and Decimal(str(a)) >= MIN_AREA and minimum_cost(p) <= BUDGET


def _line(row, now):
    title = " ".join((row["title"] or "Ogłoszenie").replace("[", "(").replace("]", ")").replace("\n", " ").split())[:105]
    price, area = row["price_pln"], row["area_m2"]
    pretty_price = f"{price:,}".replace(",", " ") if price is not None else "nieznana"
    sqm = f"{area:g}" if area is not None else "nieznany"
    unit = f"{price/area:,.0f}".replace(",", " ") if price and area else "nieznana"
    min_total = minimum_cost(price)
    total = f"{min_total:,.0f}".replace(",", " ") if min_total else "nieznane"
    return (
        f"- **{row['city']}** — [{title}]({row['url']}) — "
        f"**{pretty_price} zł**, {sqm} m² ({unit} zł/m²); "
        f"minimalny nakład modelowy **{total} zł**; "
        f"status: {_label(row, now)}; pierwszy raz wykryto {row['first_seen']}."
    )


def discovery_report(conn, limit=16, now=None):
    """One report over RSS/Bing discoveries, clearly separate from manual watchlist."""
    now = now or datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM discoveries ORDER BY first_seen DESC, url"
    ).fetchall()]
    good = [r for r in rows if _eligible(r)]
    incomplete = [r for r in rows if r["status"] != "excluded" and not _eligible(r)]
    excluded = [r for r in rows if r["status"] == "excluded"]
    matched = [r for r in good if r["verification_status"] == "online_matched"
               and _recent(r["verified_at"], 48, now)]
    fresh = [r for r in good if _recent(r["first_seen"], 24, now)]
    fresh.sort(key=lambda r: (r["first_seen"], r["url"]), reverse=True)
    matched.sort(key=lambda r: (r["verified_at"] or "", r["first_seen"]), reverse=True)
    by_unit = sorted(good, key=lambda r: (r["price_pln"]/r["area_m2"], -(_date(r["first_seen"]) or now).timestamp()))
    official_runs = conn.execute(
        "SELECT run_id FROM discovery_runs WHERE run_id LIKE 'official-%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    source_status = ""
    if official_runs:
        counts = conn.execute(
            "SELECT status,COUNT(*) AS n FROM discovery_runs WHERE run_id=? GROUP BY status",
            (official_runs["run_id"],)
        ).fetchall()
        statuses = {row["status"]: row["n"] for row in counts}
        source_status = f"Źródła oficjalne ostatnio: **{statuses.get('ok',0)} udanych**, **{statuses.get('error',0)} błędnych**."
    lines = [
        "## Automatycznie wyszukane mieszkania — Trójmiasto i Toruń", "",
        f"**{len(rows)}** adresów w historii; **{len(good)}** kompletnych, wstępnie w budżecie; "
        f"**{len(fresh)}** pierwszych wykryć w ostatnich 24 godzinach; "
        f"**{len(matched)}** niedawno dopasowanych stron; "
        f"**{len(excluded)}** wykluczonych błędnych rekordów; "
        f"**{len(incomplete)}** z brakami danych.",
        source_status, "",
        "**Uwaga:** „pierwsze wykrycie” nie jest datą publikacji ogłoszenia. "
        "Obecność w RSS/wyszukiwarce nie potwierdza, że nieruchomość nadal jest na sprzedaż. "
        "Nieznane prowizje i remonty mogą przekroczyć budżet 750 tys. zł. "
        "Nikt nie potwierdził stanu prawnego ani czynszu najmu.", "",
        "### 1. Nowo wykryte — sprawdź w pierwszej kolejności", "",
    ]
    lines.extend(_line(r, now) for r in fresh[:limit])
    if not fresh:
        lines.append("_Brak nowych, kompletnych kandydatów w ostatnich 24 godzinach._")
    lines += ["", "### 2. Strony dopasowane automatycznie (niepotwierdzone przez sprzedającego)", ""]
    lines.extend(_line(r, now) for r in matched[:limit])
    if not matched:
        lines.append("_Brak pozytywnego dopasowania strony w ostatnich 48 godzinach; nie oznacza to braku ofert._")
    lines += ["", "### 3. Pozostałe potencjalnie interesujące — najniższa cena za m²", ""]
    shown = {r["url"] for r in fresh[:limit] + matched[:limit]}
    other = [r for r in by_unit if r["url"] not in shown
             and r["verification_status"] not in ("removed_link", "content_mismatch", "invalid_url")]
    lines.extend(_line(r, now) for r in other[:limit])
    if not other:
        lines.append("_Brak kolejnych kompletnych kandydatów._")
    lines += ["", "### 4. Wymagające uzupełnienia danych", ""]
    lines.extend(_line(r, now) for r in incomplete[:8])
    if not incomplete:
        lines.append("_Brak._")
    lines += ["", "### 5. Wykluczone po ponownym sprawdzeniu danych", "",
              f"Wykluczono **{len(excluded)}** historycznych wpisów z niezgodną lokalizacją, ceną, "
              "metrażem lub adresem. Nie są częścią rankingu.", ""]
    lines.extend(f"- {r['title'][:90]} — {r['url']}" for r in excluded[:5])
    lines += ["", "Kalkulacja minimalnego nakładu: **cena z ogłoszenia + wariant PCC 2% + 5 tys. zł "
              "rezerwy**. To dolne oszacowanie, nie pełna cena zakupu; "
              "PCC zależy od transakcji. Brak danych o porównywalnym najmie = brak wiarygodnego ROI.", ""]
    return lines
