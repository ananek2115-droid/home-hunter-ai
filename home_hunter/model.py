from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


def money(value) -> Decimal:
    try:
        return Decimal(str(value).replace(' ', '').replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError(f"Nieprawidłowa kwota: {value!r}") from exc


@dataclass(frozen=True)
class Offer:
    source: str
    listing_id: str
    url: str
    city: str
    street: str
    price_pln: Decimal
    area_m2: Decimal
    rooms: int
    floor: Optional[int] = None
    district: str = ''
    house_no: str = ''
    seller_type: str = 'unknown'  # owner, agency, unknown
    buyer_fee_status: str = 'unknown'  # confirmed_zero, amount, unknown
    buyer_fee_pln: Decimal = Decimal('0')
    obligatory_extras_pln: Decimal = Decimal('0')
    preparation_pln: Decimal = Decimal('0')
    estimated_rent_pln: Decimal = Decimal('0')
    rent_comps_count: int = 0
    yearly_owner_cost_pln: Decimal = Decimal('4800')
    photo_fingerprint: str = ''
    notes: str = ''

    def validate(self):
        from urllib.parse import urlsplit
        x = urlsplit(self.url)
        if x.scheme != 'https' or not x.hostname or x.username or x.password:
            raise ValueError('Adres ogłoszenia musi być pełnym publicznym HTTPS URL')
        if self.city.casefold().strip() not in {'gdańsk','gdansk','gdynia','sopot'}:
            raise ValueError('Poza Trójmiastem')
        if self.area_m2 < Decimal('35'):
            raise ValueError('Metraż poniżej 35 m²')
        if self.price_pln <= 0 or self.rooms <= 0:
            raise ValueError('Cena i liczba pokoi muszą być dodatnie')
        if self.seller_type not in {'owner','agency','unknown'}:
            raise ValueError('Nieznany rodzaj sprzedającego')
        if self.buyer_fee_status not in {'confirmed_zero','amount','unknown'}:
            raise ValueError('Nieznany status prowizji')
        if self.buyer_fee_status == 'amount' and self.buyer_fee_pln <= 0:
            raise ValueError('Dla prowizji amount podaj kwotę dodatnią')
        if any(x < 0 for x in (self.buyer_fee_pln, self.obligatory_extras_pln, self.preparation_pln, self.estimated_rent_pln, self.yearly_owner_cost_pln)):
            raise ValueError('Koszty nie mogą być ujemne')


def offer_from_row(row: dict) -> Offer:
    def d(key, default='0'):
        return money(row.get(key) or default)
    def integer(key, default=0):
        v = row.get(key)
        return int(v) if v not in (None,'') else default
    item=Offer(
        source=(row.get('source') or '').strip().lower(),
        listing_id=(row.get('listing_id') or '').strip(),
        url=(row.get('url') or '').strip(),
        city=(row.get('city') or '').strip(),
        street=(row.get('street') or '').strip(),
        price_pln=d('price_pln'), area_m2=d('area_m2'), rooms=integer('rooms'),
        floor=integer('floor') if row.get('floor') not in (None,'') else None,
        district=(row.get('district') or '').strip(), house_no=(row.get('house_no') or '').strip(),
        seller_type=(row.get('seller_type') or 'unknown').strip().lower(),
        buyer_fee_status=(row.get('buyer_fee_status') or 'unknown').strip().lower(),
        buyer_fee_pln=d('buyer_fee_pln'), obligatory_extras_pln=d('obligatory_extras_pln'),
        preparation_pln=d('preparation_pln'), estimated_rent_pln=d('estimated_rent_pln'),
        rent_comps_count=integer('rent_comps_count'), yearly_owner_cost_pln=d('yearly_owner_cost_pln','4800'),
        photo_fingerprint=(row.get('photo_fingerprint') or '').strip(), notes=(row.get('notes') or '').strip(),
    )
    item.validate()
    return item


def offer_to_json(offer: Offer) -> dict:
    return {k: str(v) if isinstance(v, Decimal) else v for k,v in dataclasses.asdict(offer).items()}


def offer_from_json(value: dict) -> Offer:
    return offer_from_row(value)

