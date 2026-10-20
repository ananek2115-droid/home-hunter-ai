from __future__ import annotations
import re
import unicodedata
from decimal import Decimal
from .model import Offer


def normalize(s: str) -> str:
    s=''.join(c for c in unicodedata.normalize('NFKD',str(s).casefold()) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',s))


def jaccard(a: str,b: str) -> float:
    aa=set(normalize(a).split()); bb=set(normalize(b).split())
    return len(aa & bb)/len(aa | bb) if (aa|bb) else 0


def compare(a: Offer,b: Offer) -> str:
    """same / possible / different — never auto merge just because titles resemble one another."""
    if normalize(a.city) != normalize(b.city):
        return 'different'
    if a.source == b.source and a.listing_id and b.listing_id and a.listing_id == b.listing_id:
        return 'same'
    if abs(a.area_m2-b.area_m2) > Decimal('0.5') or a.rooms != b.rooms:
        return 'different'
    if a.floor is not None and b.floor is not None and a.floor != b.floor:
        return 'different'
    if a.photo_fingerprint and a.photo_fingerprint == b.photo_fingerprint:
        return 'same'
    if a.street and b.street and normalize(a.street)!=normalize(b.street):
        return 'different'
    if a.street and b.street and normalize(a.street)==normalize(b.street):
        if a.house_no and b.house_no and normalize(a.house_no)!=normalize(b.house_no):
            return 'different'
        # Adjacent flats in the same block can share dimensions. Human review is mandatory.
        return 'possible'
    return 'possible' if jaccard(a.notes,b.notes)>.80 and a.notes and b.notes else 'different'

