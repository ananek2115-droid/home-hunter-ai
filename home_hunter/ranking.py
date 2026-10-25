"""Data quality gate and actionable report for independently discovered properties.

Historical discoveries remain in SQLite but are excluded from recommendations
when source details contradict the intended city, area or budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .discover import CITIES, TARGET_CITIES, candidate_from_result
from .feeds import candidate as feed_candidate
from .seller import classify_seller

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
    if row["status"] == "excluded" or row["city"] not in TARGET_CITIES:
        return False
    p, a = row["price_pln"], row["area_m2"]
    return p is not None and a is not None and Decimal(str(a)) >= MIN_AREA and minimum_cost(p) <= BUDGET


def _seller(row):
    return classify_seller(row["title"] or "", row["snippet"] or "")


def _line(row, now):
    title = " ".join((row["title"] or "Ogłoszenie").replace("[", "(").replace("]", ")").replace("\\n", " ").split())[:105]
    price, area = row["price_pln"], row["area_m2"]
    pretty_price = f"{price:,}".replace(",", " ") if price is not None else "nieznana"
    sqm = f"{area:g}" if area is not None else "nieznany"
    unit = f"{price/area:,.0f}".replace(",", " ") if price and area else "nieznana"
    min_total = minimum_cost(price)
    total = f"{min_total:,.0f}".replace(",", " ") if min_total else "nieznane"
    seller = _seller(row)
    return (
        f"- **{row['city']}** — [{title}]({row['url']}) — "
        f"**{pretty_price} zł**, {sqm} m² ({unit} zł/m²); "
        f"minimalny nakład modelowy **{total} zł**; "
        f"sprzedający/prowizja: **{seller.category}** "
        f"({seller.evidence}; NIEPOTWIERDZONE przez sprzedającego); "
        f"status: {_label(row, now)}; pierwszy raz wykryto {row['first_seen']}."
    )


def discovery_report(conn, limit=16, now=None):
    """Owner-first Tricity report with agency and unknown inventory clearly separated."""
    now = now or datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM discoveries WHERE city IN (?,?,?) ORDER BY first_seen DESC, url",
        TARGET_CITIES,
    ).fetchall()]
    good = [r for r in rows if _eligible(r)]
    incomplete = [r for r in rows if r["status"] != "excluded" and not _eligible(r)]
    excluded = [r for r in rows if r["status"] == "excluded"]

    priority = [r for r in good if _seller(r).primary]
    unknown = [r for r in good if _seller(r).category == "unknown"]
    agency = [r for r in good if not _seller(r).primary
              and _seller(r).category != "unknown"]
    priority.sort(key=lambda r: (
        not _recent(r["verified_at"], 48, now) or r["verification_status"] != "online_matched",
        not _recent(r["first_seen"], 24, now),
        _seller(r).rank,
        r["price_pln"] / r["area_m2"],
    ))
    unknown.sort(key=lambda r: (
        not _recent(r["first_seen"], 24, now),
        r["price_pln"] / r["area_m2"],
    ))
    agency.sort(key=lambda r: (r["price_pln"] / r["area_m2"], r["first_seen"]), reverse=False)

    matched = [r for r in good if r["verification_status"] == "online_matched"
               and _recent(r["verified_at"], 48, now)]
    fresh = [r for r in good if _recent(r["first_seen"], 24, now)]
    official_runs = conn.execute(
        "SELECT run_id FROM discovery_runs WHERE run_id LIKE 'official-%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    source_status = ""
    if official_runs:
        counts = conn.execute(
            "SELECT status,COUNT(*) AS n FROM discovery_runs WHERE run_id=? GROUP BY status",
            (official_runs["run_id"],),
        ).fetchall()
        statuses = {row["status"]: row["n"] for row in counts}
        source_status = (f"Źródła RSS ostatnio: **{statuses.get('ok',0)} udanych**, "
                         f"**{statuses.get('error',0)} błędnych**.")
    lines = [
        "## Automatyczne kandydatury — Trójmiasto (najpierw właściciele i 0% prowizji)", "",
        f"**{len(rows)}** adresów w historii; **{len(good)}** kompletne, wstępnie w budżecie; "
        f"**{len(fresh)}** po raz pierwszy wykrytych w ostatnich 24 godzinach; "
        f"**{len(matched)}** dopasowanych stron w ostatnich 48 godzinach.",
        f"Podział według NIEPOTWIERDZONYCH informacji w treści: "
        f"**{sum(_seller(r).category == 'owner_claim' for r in good)}** deklaracji właściciela, "
        f"**{sum(_seller(r).category == 'zero_fee_claim' for r in good)}** deklaracji 0% prowizji, "
        f"**{len(unknown)}** nieznanych, **{len(agency)}** agencji/konfliktów/prowizji.",
        source_status, "",
        "**WAŻNE:** frazy »bez pośredników« lub »0% prowizji« to deklaracje w reklamie, "
        "**NIE potwierdzenie sprzedaży bez prowizji**. "
        "Nieznana prowizja nie jest równa 0 zł. Żadne ogłoszenie nie staje się "
        "potwierdzoną aktywną ofertą tylko dlatego, że pojawiło się w RSS.", "",
        "### 1. Priorytet: ogłoszenia deklarujące właściciela albo 0% prowizji", "",
    ]
    lines.extend(_line(r, now) for r in priority[:limit])
    if not priority:
        lines.append("_Brak ogłoszeń z wyraźną deklaracją właściciela lub 0% prowizji. "
                     "Nie zastępuj ich automatycznie ofertami agencyjnymi._")
    lines += ["", "### 2. Nieznany sprzedający lub prowizja — lista do wyjaśnienia", ""]
    lines.extend(_line(r, now) for r in unknown[:min(limit, 8)])
    if not unknown:
        lines.append("_Brak._")
    lines += ["", "### 3. Agencje, konflikty i prowizja kupującego — wyłącznie rezerwa", ""]
    lines.append("**Nie traktować jako rekomendacji zakupu.** "
                 "Promocja do rankingu wymaga potwierdzonego braku prowizji kupującego "
                 "albo udokumentowanej przewagi cenowej netto względem porównywalnych transakcji.")
    lines.extend(_line(r, now) for r in agency[:min(limit, 8)])
    if not agency:
        lines.append("_Brak._")
    lines += ["", "### 4. Braki danych i oferty do uzupełnienia", ""]
    lines.extend(_line(r, now) for r in incomplete[:8])
    if not incomplete:
        lines.append("_Brak._")
    lines += ["", "### 5. Wykluczone po kontroli integralności danych", "",
              f"Wykluczono **{len(excluded)}** historycznych wpisów z błędną lokalizacją, "
              "ceną, metrażem lub adresem; historia pozostaje w bazie.", ""]
    lines.extend(f"- {r['title'][:90]} — {r['url']}" for r in excluded[:5])
    lines += ["", "Koszt minimalny = cena + wariant PCC 2% + 5 tys. zł rezerwy. "
              "To NIE pełny koszt inwestycji. Nieznane prowizje, remont i obowiązkowe "
              "dodatki mogą przekroczyć 750 tys. zł. Brak danych o najmie = brak ROI.", ""]
    return lines
