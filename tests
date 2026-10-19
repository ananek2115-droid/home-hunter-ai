import dataclasses
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from home_hunter.model import Offer
from home_hunter.verify import inspect, safe_public_url
from home_hunter.dedupe import compare
from home_hunter.economics import analyze, target_price_for_yield
from home_hunter.storage import connect,upsert_offer,list_offers,write_check
from home_hunter.cli import generate_report


def offer(**changes):
    base=dict(source='olx', listing_id='ID1ABC123', url='https://www.olx.pl/d/oferta/mieszkanie-CID3-ID1ABC123.html',
              city='Gdańsk',street='Przymorze', price_pln=Decimal('650000'),area_m2=Decimal('42'),
              rooms=2,floor=3,seller_type='owner',buyer_fee_status='confirmed_zero',
              estimated_rent_pln=Decimal('2800'),rent_comps_count=4,preparation_pln=Decimal('10000'))
    base.update(changes)
    return Offer(**base)


class WebVerificationTests(unittest.TestCase):
    def test_missing_is_not_sold(self):
        result=inspect(offer(),fetch=lambda url:(410,url,''),robots_check=False)
        self.assertEqual(result.status,'removed_link')
        self.assertIn('not proven',result.reason)

    def test_403_is_not_removed(self):
        result=inspect(offer(),fetch=lambda url:(403,url,''),robots_check=False)
        self.assertEqual(result.status,'access_restricted')

    def test_redirect_to_another_listing_rejected(self):
        html='<html><title>Mieszkanie Gdańsk</title><p>Gdańsk 650 000 zł 42 m² 2 pokoje</p>'*10
        result=inspect(offer(),fetch=lambda url:(200,'https://www.olx.pl/d/oferta/inne-CID3-ID9XYZ999.html',html),robots_check=False)
        self.assertEqual(result.status,'content_mismatch')

    def test_content_mismatch_does_not_pass(self):
        html='<html><body>'+'<h1>Mieszkanie Warszawa 44 m² 720 000 zł</h1>'*10+'</body></html>'
        result=inspect(offer(),fetch=lambda url:(200,url,html),robots_check=False)
        self.assertEqual(result.status,'needs_manual_review')

    def test_synthetic_matching_page(self):
        html='<html><body>'+'<h1>Mieszkanie Gdańsk na sprzedaż 42 m² 650 000 zł</h1>'*10+'</body></html>'
        result=inspect(offer(),fetch=lambda url:(200,url,html),robots_check=False)
        self.assertEqual(result.status,'online_matched',result.reason)

    def test_dynamic_page_never_confirmed(self):
        result=inspect(offer(),fetch=lambda url:(200,url,'<html><div id="app"></div></html>'),robots_check=False)
        self.assertEqual(result.status,'needs_manual_review')

    def test_unsafe_urls_rejected(self):
        self.assertFalse(safe_public_url('https://127.0.0.1/admin'))
        self.assertFalse(safe_public_url('http://www.olx.pl/'))
        self.assertFalse(safe_public_url('https://localhost/'))
        self.assertTrue(safe_public_url('https://www.olx.pl/d/oferta/a'))


class DeduplicationTests(unittest.TestCase):
    def test_exact_id(self):
        a=offer();b=dataclasses.replace(a,url='https://www.olx.pl/d/oferta/new-link-ID1ABC123.html')
        self.assertEqual(compare(a,b),'same')

    def test_same_block_not_automatically_merged(self):
        a=offer(listing_id='one',house_no='10')
        b=offer(source='otodom',listing_id='two',house_no='10')
        self.assertEqual(compare(a,b),'possible')

    def test_different_floor_rejected(self):
        a=offer(floor=1,house_no='10')
        b=offer(source='otodom',floor=6,house_no='10')
        self.assertEqual(compare(a,b),'different')

    def test_picture_fingerprint(self):
        a=offer(photo_fingerprint='abc123',source='olx')
        b=offer(source='otodom',listing_id='xyz',photo_fingerprint='abc123')
        self.assertEqual(compare(a,b),'same')

    def test_different_city(self):
        self.assertEqual(compare(offer(),offer(city='Gdynia')),'different')


class StorageAndInvestmentTests(unittest.TestCase):
    def test_budget_and_tax(self):
        a=analyze(offer())
        self.assertEqual(a.total_cost, Decimal('678000.00'))
        self.assertEqual(a.effective_annual_rent,Decimal('30800'))
        self.assertEqual(a.after_tax_annual_net,Decimal('23382.000'))
        self.assertTrue(Decimal('3.4')<a.yield_pct<Decimal('3.5'))
        self.assertTrue(target_price_for_yield(offer())<Decimal('650000'))

    def test_fee_unknown_no_ready_scoring(self):
        a=analyze(offer(buyer_fee_status='unknown'))
        self.assertFalse(a.ready_for_scoring)
        self.assertFalse(a.cost_confirmed)

    def test_history_and_report_no_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'db.sqlite3'))
            original=offer()
            uid=upsert_offer(c,original)
            self.assertEqual(len(list_offers(c)),1)
            changed=dataclasses.replace(original,price_pln=Decimal('630000'))
            upsert_offer(c,changed)
            events=c.execute('SELECT * FROM observations').fetchall()
            self.assertEqual(len(events),2)
            path=str(Path(d)/'report.md')
            verified,review,archived=generate_report(c,path)
            self.assertEqual((verified,review,archived),(0,1,0))
            self.assertIn('Brak ofert',Path(path).read_text(encoding='utf8'))
            write_check(c,uid,'removed_link',410,original.url,'Gone')
            verified,review,archived=generate_report(c,path)
            self.assertEqual((verified,review,archived),(0,0,1))
            c.close()

if __name__=='__main__':unittest.main()

class AdditionalRegressions(unittest.TestCase):
    def test_domain_allowlist(self):
        self.assertFalse(safe_public_url('https://bad.example.org/page'))
        self.assertFalse(safe_public_url('https://olx.pl.evil.example.org/page'))

    def test_repeated_price_cycle_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.sqlite'))
            a=offer(price_pln=Decimal('650000'))
            upsert_offer(c,a)
            upsert_offer(c,dataclasses.replace(a,price_pln=Decimal('630000')))
            upsert_offer(c,a)
            prices=[x['asking_price_pln'] for x in c.execute('SELECT * FROM observations ORDER BY id')]
            self.assertEqual(prices,['650000','630000','650000'])
            c.close()

    def test_overbudget_not_in_main_report(self):
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.sqlite'))
            a=offer(price_pln=Decimal('749000'),preparation_pln=Decimal('20000'))
            uid=upsert_offer(c,a)
            write_check(c,uid,'online_matched',200,a.url,'Matched')
            main,review,archived=generate_report(c,str(Path(d)/'report.md'))
            self.assertEqual((main,review,archived),(0,1,0))
            c.close()

class VerificationInvalidationTests(unittest.TestCase):
    def test_changed_price_invalidates_prior_page_match(self):
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'state.db'))
            a=offer()
            uid=upsert_offer(c,a)
            write_check(c,uid,'online_matched',200,a.url,'Previously matched')
            upsert_offer(c,dataclasses.replace(a,price_pln=Decimal('620000')))
            info=c.execute('SELECT * FROM offers WHERE uid=?',(uid,)).fetchone()
            self.assertEqual(info['verification_status'],'never_checked')
            self.assertIsNone(info['checked_at'])
            c.close()
