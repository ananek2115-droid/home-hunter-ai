"""Conservative seller evidence from listing text.

The portal type (OLX/Domy) does NOT prove a private owner or 0% commission.
Claims in titles/snippets are leads requiring seller confirmation.
"""
from __future__ import annotations
from dataclasses import dataclass
from .dedupe import normalize

OWNER_CLAIMS = (
    "bez posrednikow", "bez posrednika", "sprzedaz prywatna",
    "osoba prywatna", "od wlasciciela", "wlasciciel sprzedaje",
    "sprzedam bezposrednio", "sprzedaz bezposrednia",
    "bezposrednio od wlasciciela", "bezposrednio bez posrednikow",
)
AGENCY_SIGNALS = (
    "biuro nieruchomosci", "agencja nieruchomosci",
    "oferta biura", "oferta agencji", "posrednictwo nieruchomosci",
    "agent nieruchomosci", "biuro posrednictwa",
)
ZERO_FEE_CLAIMS = (
    "0 prowizji", "0 procent prowizji", "zero prowizji",
    "bez prowizji dla kupujacego", "brak prowizji dla kupujacego",
    "kupujacy nie placi prowizji", "kupujacy nie ponosi prowizji",
    "bez prowizji od kupujacego", "prowizja po stronie sprzedajacego",
    "bez prowizji",
)
BUYER_FEE_CLAIMS = (
    "prowizja dla kupujacego", "prowizja po stronie kupujacego",
    "kupujacy placi prowizje", "koszt posrednictwa dla kupujacego",
)


@dataclass(frozen=True)
class SellerSignal:
    category: str  # owner_claim, zero_fee_claim, agency_unknown, agency_fee, conflict, unknown
    evidence: str
    rank: int  # lower is preferred; does not indicate confirmed availability

    @property
    def primary(self):
        return self.category in {"owner_claim", "zero_fee_claim"}


def _any(text: str, phrases: tuple[str, ...]) -> str:
    normalized = " " + normalize(text) + " "
    for phrase in phrases:
        if " " + phrase + " " in normalized:
            return phrase
    return ""


def classify_seller(title: str = "", snippet: str = "") -> SellerSignal:
    """Text is advertising evidence, NEVER confirmed fee/ownership."""
    text = " ".join((title or "", snippet or ""))
    owner = _any(text, OWNER_CLAIMS)
    agency = _any(text, AGENCY_SIGNALS)
    free = _any(text, ZERO_FEE_CLAIMS)
    paid = _any(text, BUYER_FEE_CLAIMS)
    if paid:
        return SellerSignal("agency_fee", "ogłoszenie wspomina prowizję kupującego: " + paid, 5)
    if owner and agency:
        return SellerSignal("conflict", "sprzeczne deklaracje: " + owner + " / " + agency, 4)
    if owner:
        return SellerSignal("owner_claim", "deklaracja sprzedaży prywatnej: " + owner, 0)
    if free:
        return SellerSignal("zero_fee_claim", "deklaracja 0% prowizji: " + free, 1)
    if agency:
        return SellerSignal("agency_unknown", "pośrednik: " + agency, 4)
    return SellerSignal("unknown", "brak potwierdzenia właściciela i prowizji", 3)


def known_offer_signal(offer):
    """Imported metadata is a declaration until seller/contract verifies it."""
    if offer.buyer_fee_status == "amount":
        return SellerSignal("agency_fee", "wpisana prowizja kupującego", 5)
    if offer.seller_type == "owner":
        return SellerSignal("owner_claim", "w CSV deklarowano właściciela; należy potwierdzić", 0)
    if offer.buyer_fee_status == "confirmed_zero":
        return SellerSignal("zero_fee_claim", "CSV: prowizja 0 zł; sprawdź źródło potwierdzenia", 1)
    if offer.seller_type == "agency":
        return SellerSignal("agency_unknown", "wpisano pośrednika; prowizja nieznana", 4)
    return SellerSignal("unknown", "brak potwierdzonych danych o sprzedającym", 3)
