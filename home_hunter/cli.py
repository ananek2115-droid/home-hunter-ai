from __future__ import annotations
import argparse, csv, json, sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from .dedupe import compare
from .economics import analyze, purchase_headroom, target_price_for_yield, BUDGET
from .model import offer_from_row
from .storage import connect,list_offers,set_candidate,upsert_offer,write_check
from .verify import inspect
from .discover import run_discovery, discovery_report_lines
from .feeds import ingest_feeds, verify_new
from .ranking import reconcile_discoveries, discovery_report


def fmt_num(num):
    return f'{int(num):,}'.replace(',',' ')


def import_csv(c, filename):
    good=bad=0
    with open(filename,encoding='utf-8-sig',newline='') as f:
        for i,row in enumerate(csv.DictReader(f),start=2):
            try:
                item=offer_from_row(row)
                upsert_offer(c,item);good+=1
            except (ValueError,TypeError) as e:
                print(f'WIERSZ {i}: {e}',file=sys.stderr);bad+=1
    return good,bad


def dedupe(c):
    data=list_offers(c);n=0
    for i,(a_uid,a,_) in enumerate(data):
        for b_uid,b,_ in data[i+1:]:
            verdict=compare(a,b)
            if verdict!='different':
                set_candidate(c,a_uid,b_uid,verdict);n+=1
    return n


def check_all(c,limit):
    if limit < 0:
        raise ValueError('Limit kontroli nie może być ujemny')
    # Always check never-inspected listings first, then the oldest inspections.
    # Otherwise --limit 40 silently starves all listings after the first 40.
    rows=sorted(list_offers(c), key=lambda x: (
        x[2]['checked_at'] is not None, x[2]['checked_at'] or '', x[0]))
    for idx,(uid,item,_) in enumerate(rows[:limit],1):
        result=inspect(item)
        write_check(c,uid,result.status,result.http_status,result.final_url,result.reason)
        print(f'{idx}/{min(len(rows),limit)} {item.source}:{item.listing_id} {result.status} — {result.reason}')
    return min(len(rows),limit)


def draft_contact(offer):
    fee = ('Potwierdza Pan/Pani, że zakup odbywa się bez prowizji po stronie kupującego? '
           if offer.buyer_fee_status != 'confirmed_zero' else '')
    return (f'Dzień dobry, czy mieszkanie w {offer.city} ({offer.street}, '
            f'{offer.area_m2} m², cena {fmt_num(offer.price_pln)} zł) jest nadal dostępne? '
            + fee + 'Jaki jest czynsz administracyjny latem i zimą, czy instalacje wymagają prac '
            'i czy budynek ma zaplanowane większe remonty? Czy można otrzymać dokumenty '
            'potwierdzające stan prawny oraz czy cena podlega negocjacji?')


def is_recent_check(checked_at, *, hours=48, now=None):
    """A previous content match expires; old checks cannot prove current listing state."""
    if not checked_at:
        return False
    try:
        checked=datetime.fromisoformat(checked_at)
    except (TypeError, ValueError):
        return False
    if checked.tzinfo is None:
        return False
    now=now or datetime.now(timezone.utc)
    age=now-checked.astimezone(timezone.utc)
    return timedelta(0) <= age <= timedelta(hours=hours)


def generate_report(c,output):
    all_items=list_offers(c);verified=[];review=[];archived=[]
    for uid,offer,info in all_items:
        item=(uid,offer,info,analyze(offer))
        if (info['verification_status']=='online_matched' and is_recent_check(info['checked_at'])
                and item[3].total_cost<=BUDGET):
            verified.append(item)
        elif info['verification_status'] in {'removed_link','content_mismatch','invalid_url'}:
            archived.append(item)
        else:
            review.append(item)
    # By yield only when available; no fabricated 0–100 scoring without price/rent evidence.
    verified.sort(key=lambda a: (a[3].yield_pct is not None,a[3].yield_pct or Decimal('-1000')),reverse=True)
    lines=['# HOME HUNTER AI 4.0 — raport audytowalny','',
           f'**Budżet całkowity: {fmt_num(BUDGET)} zł.**',
           '',f'Strony dopasowane w ostatnich 48 h i w budżecie: **{len(verified)}**. '
           f'Do kontroli: **{len(review)}**. Niedziałające/niezgodne linki: **{len(archived)}**.',
           '', '⚠️ Status `online_matched` oznacza dopasowanie zawartości strony, **nie** potwierdzenie dostępności przez sprzedającego.',
           'Szacunki są przed kredytem; rentowność jest modelowana. Rezerwa podatkowa PCC 2% jest domyślnym wariantem, nie rozstrzygnięciem prawnym.',
           '', '## Dopasowane w ostatnich 48 godzinach — kandydaci (bez potwierdzenia sprzedającego)','']
    if not verified:lines.append('_Brak ofert, które spełniły wszystkie warunki automatycznej weryfikacji._')
    for uid,o,info,a in verified:
        cost_ok=a.total_cost<=BUDGET
        fee_ok=o.seller_type=='owner' or o.buyer_fee_status=='confirmed_zero'
        lines.extend([
            f'### {o.city} / {o.street} — {fmt_num(o.price_pln)} zł',
            f'- Link: {o.url}',
            f'- Identyfikator: `{uid}` / `{o.source}:{o.listing_id}`; status: `{info["verification_status"]}`; sprawdzono: `{info["checked_at"]}`',
            f'- Powierzchnia: {o.area_m2} m², pokoje: {o.rooms}; pełny nakład modelowy: **{fmt_num(a.total_cost)} zł**.',
            f'- Najem prognozowany: {fmt_num(o.estimated_rent_pln)} zł; stopa netto: **{a.yield_pct:.2f}%**.' if a.yield_pct is not None else '- Najem i rentowność: **brak wystarczających danych**.',
            f'- Limit: {"w budżecie" if cost_ok else "POZA BUDŻETEM"}; bezpośrednio/0%: {"wstępnie tak" if fee_ok else "NIEPOTWIERDZONE"}; prowizja: `{o.buyer_fee_status}`.',
            f'- Liczba porównań najmu: {o.rent_comps_count}; ocena końcowa 0–100: **wstrzymana** bez rzetelnych porównań sprzedaży i stanu technicznego.',
            f'- Cena maksymalna dla 4% netto (model): {fmt_num(target_price_for_yield(o))} zł.' if target_price_for_yield(o) is not None else '- Cena maksymalna dla 4% netto: nieustalona.',
            f'- Uwagi: {o.notes or "brak"}',
            f'- Wiadomość do sprzedającego (do ręcznej wysyłki): {draft_contact(o)}', ''])
    lines += ['## Do weryfikacji (nie umieszczać w rankingu aktywnych)','']
    if not review:lines.append('_Brak._')
    for uid,o,info,a in review:
        lines.append(f'- **{o.city}, {o.street}** — {fmt_num(o.price_pln)} zł — `{info["verification_status"]}` — {info["check_note"] or "brak kontroli"} — {o.url}')
    lines += ['', '## Wykluczone linki','']
    if not archived:lines.append('_Brak._')
    for uid,o,info,a in archived:
        lines.append(f'- **{o.city}, {o.street}** — `{info["verification_status"]}` — {info["check_note"]} — {o.url}')
    lines += ['', '## Możliwe duplikaty — kontrola ręczna','']
    cand=c.execute('SELECT * FROM duplicate_candidates ORDER BY first_uid,second_uid').fetchall()
    if not cand:lines.append('_Brak._')
    for x in cand:lines.append(f'- Oferta #{x["first_uid"]} ↔ #{x["second_uid"]}: {x["verdict"]}')
    lines.extend([''] + discovery_report(c))
    lines+=['','## Zasada publikacji','',
            'Nie przedstawiaj oferty jako dostępnej do zakupu bez kontaktu ze sprzedającym. '
            'Nie traktuj 403, 429, CAPTCHA, strony ładowanej JS lub blokady robots jako dowodu usunięcia ogłoszenia. '
            'Nie traktuj czynszu z oferty jako gwarantowanego przychodu.', '',
            '## Jak interpretować wynik', '',
            'Ten program monitoruje tylko adresy umieszczone w config/watchlist.csv; '
            'nie znajduje automatycznie nowych ogłoszeń. '
            'Status access_restricted oznacza blokadę dostępu, a nie usuniętą ofertę. '
            'Dopasowanie starsze niż 48 godzin automatycznie wraca do kontroli ręcznej.', '']
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text('\n'.join(lines),encoding='utf-8')
    return len(verified),len(review),len(archived)


def main(argv=None):
    p=argparse.ArgumentParser(prog='home-hunter',description='Walidator nieruchomości bez zgadywania statusu')
    p.add_argument('--db',default='data/home_hunter.sqlite3')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init')
    x=sub.add_parser('import-csv');x.add_argument('file')
    sub.add_parser('dedupe')
    sub.add_parser('discover')
    sub.add_parser('discover-feeds')
    sub.add_parser('reconcile')
    x=sub.add_parser('verify-new');x.add_argument('--limit',type=int,default=12)
    x=sub.add_parser('check');x.add_argument('--limit',type=int,default=40)
    x=sub.add_parser('report');x.add_argument('--out',default='reports/latest.md')
    args=p.parse_args(argv)
    c=connect(args.db)
    try:
        if args.cmd=='init':print(f'Utworzono bazę: {args.db}')
        elif args.cmd=='import-csv':
            ok,bad=import_csv(c,args.file);print(f'Zaimportowano: {ok}; błędne: {bad}')
            if bad: return 1
        elif args.cmd=='reconcile':print(f'Porządkowanie bazy: {reconcile_discoveries(c)}')
        elif args.cmd=='discover-feeds':
            r=ingest_feeds(c);print(f'Oficjalne źródła: {r}')
            if r['ok']==0:return 2
        elif args.cmd=='verify-new':print(f'Zweryfikowano kandydatów: {verify_new(c,args.limit)}')
        elif args.cmd=='discover':
            result=run_discovery(c);print(f'Odkrywanie: {result}')
            if result['ok']==0: return 2
        elif args.cmd=='dedupe':print(f'Zapisano par do przeglądu: {dedupe(c)}')
        elif args.cmd=='check':print(f'Sprawdzono: {check_all(c,args.limit)}')
        elif args.cmd=='report':print(f'Raport: {args.out}; kategorie: {generate_report(c,args.out)}')
    finally:c.close()
    return 0

if __name__=='__main__':raise SystemExit(main())

