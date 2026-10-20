from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .model import Offer

BUDGET = Decimal('750000')

@dataclass(frozen=True)
class Investment:
    total_cost: Decimal
    gross_annual_rent: Decimal
    effective_annual_rent: Decimal
    after_tax_annual_net: Decimal
    yield_pct: Decimal | None
    cost_confirmed: bool
    ready_for_scoring: bool


def analyze(offer: Offer, *, pcc_rate: Decimal = Decimal('0.02'),
            transaction_reserve: Decimal = Decimal('5000'),
            vacant_months: int = 1, tax_rate: Decimal = Decimal('0.085')) -> Investment:
    if not 0 <= vacant_months <= 12:
        raise ValueError('Pustostan poza zakresem 0–12 miesięcy')
    if any(r < 0 for r in (pcc_rate, transaction_reserve, tax_rate)):
        raise ValueError('Ujemna stawka lub rezerwa')
    total=(offer.price_pln*(1+pcc_rate)+transaction_reserve+
           offer.obligatory_extras_pln+offer.preparation_pln+offer.buyer_fee_pln)
    full=offer.estimated_rent_pln*12
    collected=offer.estimated_rent_pln*(12-vacant_months)
    net=collected*(1-tax_rate)-offer.yearly_owner_cost_pln
    yield_pct=(net/total*100) if total>0 and offer.estimated_rent_pln>0 else None
    # Unconfirmed intermediary fee or poor rent comparables cannot become a definitive score.
    return Investment(total,full,collected,net,yield_pct,
                      offer.buyer_fee_status != 'unknown',
                      offer.buyer_fee_status != 'unknown' and offer.rent_comps_count>=3)


def purchase_headroom(offer: Offer, **kwargs) -> Decimal:
    return BUDGET-analyze(offer,**kwargs).total_cost


def target_price_for_yield(offer: Offer, target_pct=Decimal('4'), **kwargs) -> Decimal | None:
    if offer.estimated_rent_pln <= 0:
        return None
    current=analyze(offer,**kwargs)
    if current.after_tax_annual_net <= 0:
        return None
    # fixed extra costs and proportional PCC must be allocated consistently
    pcc_rate=kwargs.get('pcc_rate',Decimal('0.02'))
    reserve=kwargs.get('transaction_reserve',Decimal('5000'))
    nonprice=reserve+offer.obligatory_extras_pln+offer.preparation_pln+offer.buyer_fee_pln
    allowed_total=current.after_tax_annual_net*100/Decimal(str(target_pct))
    return (allowed_total-nonprice)/(1+pcc_rate)

