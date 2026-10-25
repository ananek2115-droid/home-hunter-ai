
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

class MonitorReliabilityTests(unittest.TestCase):
    def test_recent_checks_require_timezone_and_freshness(self):
        from datetime import datetime, timedelta, timezone
        from home_hunter.cli import is_recent_check
        now=datetime(2026,10,20,14,0,tzinfo=timezone.utc)
        self.assertTrue(is_recent_check((now-timedelta(hours=4)).isoformat(),now=now))
        self.assertFalse(is_recent_check((now-timedelta(hours=49)).isoformat(),now=now))
        self.assertFalse(is_recent_check((now+timedelta(hours=1)).isoformat(),now=now))
        self.assertFalse(is_recent_check("2026-10-20T12:00:00",now=now))
        self.assertFalse(is_recent_check(None,now=now))

    def test_stale_page_match_not_ranked(self):
        from home_hunter.cli import generate_report
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.sqlite'))
            a=offer()
            uid=upsert_offer(c,a)
            write_check(c,uid,'online_matched',200,a.url,'Matched in the past')
            c.execute("UPDATE offers SET checked_at='2020-01-01T00:00:00+00:00' WHERE uid=?",(uid,))
            c.commit()
            counts=generate_report(c,str(Path(d)/'report.md'))
            self.assertEqual(counts,(0,1,0))
            c.close()

    def test_rotation_checks_oldest_first(self):
        from unittest.mock import patch
        from home_hunter.cli import check_all
        from home_hunter.verify import CheckResult
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.sqlite'))
            first=offer()
            second=dataclasses.replace(first,url='https://www.olx.pl/d/oferta/another-ID2ABC.html',listing_id='ID2ABC')
            first_id=upsert_offer(c,first)
            second_id=upsert_offer(c,second)
            write_check(c,first_id,'needs_manual_review')
            observed=[]
            def fake_inspect(item):
                observed.append(item.listing_id)
                return CheckResult('needs_manual_review',None,item.url,'test')
            with patch('home_hunter.cli.inspect',fake_inspect):
                self.assertEqual(check_all(c,1),1)
            self.assertEqual(observed,['ID2ABC'])
            c.close()

    def test_negative_limit_is_invalid(self):
        from home_hunter.cli import check_all
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.sqlite'))
            with self.assertRaises(ValueError):
                check_all(c,-1)
            c.close()

class DiscoveryTests(unittest.TestCase):
    def test_torun_is_supported(self):
        from home_hunter.model import offer_from_row
        item=offer_from_row({'source':'olx','listing_id':'test','url':'https://www.olx.pl/d/oferta/test','city':'Toruń','street':'', 'price_pln':'400000','area_m2':'40','rooms':'2'})
        self.assertEqual(item.city,'Toruń')

    def test_feed_parsing_and_filtering(self):
        from home_hunter.discover import parse_rss, candidate_from_result, canonical_listing_url
        xml=b'<rss version="2.0"><channel><item><title>Mieszkanie Torun 42 m2 450 000 zl</title><link>https://www.olx.pl/d/oferta/abc?tracking=yes</link><description>Torun mieszkanie 2 pokoje</description></item></channel></rss>'
        item=parse_rss(xml)[0]
        found=candidate_from_result('Toruń',item)
        self.assertIsNotNone(found)
        self.assertEqual(found['price_pln'],450000)
        self.assertEqual(found['area_m2'],42.0)
        self.assertEqual(found['url'],'https://olx.pl/d/oferta/abc')
        self.assertIsNone(candidate_from_result('Gdynia',item))
        self.assertIsNone(canonical_listing_url('https://example.com/a'))

    def test_discovery_deduplicates(self):
        from home_hunter.discover import run_discovery
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'test.db'))
            xml=b'<rss><channel><item><title>Mieszkanie Torun 42 m2 450 000 zl</title><link>https://www.olx.pl/d/oferta/abc</link><description>Torun mieszkanie</description></item></channel></rss>'
            def fetch(query): return xml
            a=run_discovery(c,fetch=fetch,cities=('Toruń',))
            b=run_discovery(c,fetch=fetch,cities=('Toruń',))
            self.assertEqual(a['new'],1)
            self.assertEqual(b['new'],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM discoveries').fetchone()[0],1)
            c.close()

class OfficialFeedTests(unittest.TestCase):
    def test_official_rss_filter_torun(self):
        from home_hunter.feeds import parse_feed, candidate
        data=b'<rss><channel><item><title>Mieszkanie Torun 42 m2 450 000 zl</title><link>https://domy.pl/mieszkanie/10000001</link><description>Mieszkanie 2 pokoje, sprzedaz</description></item></channel></rss>'
        listings=parse_feed(data)
        self.assertEqual(len(listings),1)
        item=candidate(listings[0],expected_city='Toruń')
        self.assertIsNotNone(item)
        self.assertEqual(item['city'],'Toruń')

    def test_feed_dedup_and_verification(self):
        from home_hunter.feeds import ingest_feeds, verify_new
        from home_hunter.verify import CheckResult
        from home_hunter.feeds import FEEDS
        payload=b'<rss><channel><item><title>Mieszkanie Gdynia 42 m2 450 000 zl</title><link>https://domy.pl/mieszkanie/10000001</link><description>Mieszkanie Gdynia 42 m2 450 000 zl</description></item></channel></rss>'
        def fake_feed(url):return payload
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'h.sqlite3'))
            a=ingest_feeds(c,fetch=fake_feed)
            b=ingest_feeds(c,fetch=fake_feed)
            self.assertEqual(a['new'],1)
            self.assertEqual(b['new'],0)
            count=verify_new(c,checker=lambda o:CheckResult('access_restricted',403,o.url,'restricted'))
            self.assertEqual(count,1)
            status=c.execute('SELECT verification_status FROM discoveries').fetchone()[0]
            self.assertEqual(status,'access_restricted')
            c.close()

class CitySourceIntegrityTests(unittest.TestCase):
    def test_unrelated_city_in_sopot_feed_is_rejected(self):
        from home_hunter.feeds import candidate
        item={'title':'Mieszkanie Bydgoszcz 42 m2 345 000 zl', 'snippet':'2 pokoje Bydgoszcz', 'url':'https://domy.pl/mieszkanie/bydgoszcz-2-pokoje-345000-pln-42m2-sba/dol1745211933'}
        self.assertIsNone(candidate(item,'Sopot'))

    def test_slug_price_over_budget_rejected(self):
        from home_hunter.feeds import candidate
        item={'title':'Mieszkanie Gdańsk Śródmieście', 'snippet':'3 pokoje', 'url':'https://domy.pl/mieszkanie/gdansk-3-pokoje-1730000-pln-79m2-sba/dol1745211758'}
        self.assertIsNone(candidate(item,'Gdańsk'))

    def test_slug_price_under_budget_parsed(self):
        from home_hunter.feeds import candidate
        item={'title':'Mieszkanie Gdynia Redłowo','snippet':'3 pokoje','url':'https://domy.pl/mieszkanie/gdynia-redlowo-665000-pln-54m2-sba/dol1745211406'}
        found=candidate(item,'Gdynia')
        self.assertEqual(found['price_pln'],665000)
        self.assertEqual(found['area_m2'],54.0)

class RebuiltPipelineTests(unittest.TestCase):
    def seed(self, conn, url, city, title, price, area, when="2026-10-20T17:00:00+00:00"):
        conn.execute("""INSERT INTO discoveries
          (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (url,city,"domy.pl",title,title,price,area,"preliminary",when,when))
        conn.commit()

    def test_history_reconciled_without_losing_timestamps(self):
        from home_hunter.ranking import reconcile_discoveries
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/"x.db"))
            self.seed(c,"https://domy.pl/mieszkanie/gdansk-3-pokoje-1730000-pln-79m2-sba/dol1745211758",
                      "Gdańsk","Mieszkanie Gdańsk 1 730 000 PLN",730000,79)
            self.seed(c,"https://domy.pl/mieszkanie/bydgoszcz-2-pokoje-345000-pln-42m2-sba/dol1745211933",
                      "Sopot","Mieszkanie Bydgoszcz",345000,42)
            counts=reconcile_discoveries(c)
            self.assertEqual(counts['excluded'],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM discoveries WHERE status='excluded'").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0],2)
            c.close()

    def test_ranking_has_fresh_unverified_results(self):
        from datetime import datetime, timezone
        from home_hunter.ranking import reconcile_discoveries, discovery_report
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/"x.db"))
            self.seed(c,"https://domy.pl/mieszkanie/gdynia-2-pokoje-550000-pln-46m2-sba/dol1745212213",
                      "Gdynia","Mieszkanie Gdynia 550000 PLN 46m2",550000,46)
            reconcile_discoveries(c)
            report="\n".join(discovery_report(c, now=datetime(2026,10,20,20,0,tzinfo=timezone.utc)))
            self.assertIn("Nowo wykryte",report)
            self.assertIn("550 000 zł",report)
            self.assertIn("jeszcze bez weryfikacji",report)
            c.close()

class TricityProductionScopeTests(unittest.TestCase):
    def test_default_search_queries_exclude_torun(self):
        from home_hunter.discover import search_queries, TARGET_CITIES
        queries = search_queries()
        self.assertEqual({city for city, _ in queries}, set(TARGET_CITIES))
        self.assertFalse(any("Toruń" in query for _, query in queries))

    def test_official_feeds_skip_torun_even_when_feed_returns_it(self):
        from home_hunter.feeds import ingest_feeds
        payload=(b'<rss><channel><item><title>Mieszkanie Torun 42 m2 450 000 zl</title>'
                 b'<link>https://domy.pl/mieszkanie/10000001</link>'
                 b'<description>Mieszkanie Torun 42 m2 450 000 zl</description>'
                 b'</item></channel></rss>')
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'history.sqlite3'))
            r=ingest_feeds(c,fetch=lambda url:payload)
            self.assertEqual(r['ok'],4)  # Gdańsk, Gdynia, Sopot and Pomorskie
            self.assertEqual(r['new'],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0],0)
            c.close()

    def test_report_excludes_torun_but_retains_history(self):
        from home_hunter.ranking import discovery_report
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            c=connect(str(Path(d)/'history.sqlite3'))
            now=datetime(2026,10,25,11,0,tzinfo=timezone.utc)
            for city,slug,price in [('Toruń','torun',350000),('Gdynia','gdynia',430000)]:
                c.execute("""INSERT INTO discoveries
                   (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (f'https://domy.pl/mieszkanie/{slug}-2-pokoje-{price}-pln-40m2-sba/dol1745212000',
                    city,'domy.pl',f'Mieszkanie {city}',f'Mieszkanie {city}',
                    price,40,'preliminary',now.isoformat(),now.isoformat()))
            c.commit()
            report='\\n'.join(discovery_report(c,now=now))
            self.assertIn('Gdynia',report)
            self.assertNotIn('Toruń',report)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM discoveries').fetchone()[0],2)
            c.close()

class VerificationCoverageTests(unittest.TestCase):
    def test_verification_rotates_across_streets(self):
        from home_hunter.feeds import verify_new
        from home_hunter.verify import CheckResult
        with tempfile.TemporaryDirectory() as d:
            db=connect(str(Path(d)/'coverage.db'))
            cases=[]
            for i in range(10):
                cases.append(('Gdańsk','gdansk-brzezno-uczniowska',650000+i))
            cases += [
                ('Gdynia','gdynia-redlowo-hallera',620000),
                ('Gdańsk','gdansk-wrzeszcz-kosciuszki',590000),
            ]
            for ix,(city,stem,price) in enumerate(cases):
                url=f'https://domy.pl/mieszkanie/{stem}-2-pokoje-{price}-pln-40m2-sba/dol174500{ix:04d}'
                db.execute("""INSERT INTO discoveries
                   (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (url,city,'domy.pl',f'{city} mieszkanie',f'{city} mieszkanie',
                    price,40,'preliminary','2026-10-25T10:00:00+00:00','2026-10-25T10:00:00+00:00'))
            db.commit()
            checked=[]
            def checker(item):
                checked.append(item.url)
                return CheckResult('needs_manual_review',200,item.url,'Test only')
            self.assertEqual(verify_new(db,limit=3,checker=checker),3)
            self.assertEqual(len(checked),3)
            self.assertEqual(sum('uczniowska' in url for url in checked),1)
            self.assertTrue(any('hallera' in url for url in checked))
            self.assertTrue(any('kosciuszki' in url for url in checked))
            db.close()

    def test_verification_respects_target_cities(self):
        from home_hunter.feeds import verify_new
        from home_hunter.verify import CheckResult
        with tempfile.TemporaryDirectory() as d:
            db=connect(str(Path(d)/'coverage.db'))
            for city,slug in [('Toruń','torun'),('Gdynia','gdynia')]:
                url=f'https://domy.pl/mieszkanie/{slug}-2-pokoje-450000-pln-42m2-sba/dol17451234'
                db.execute("""INSERT INTO discoveries
                   (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (url,city,'domy.pl',f'Mieszkanie {city}',f'Mieszkanie {city}',
                    450000,42,'preliminary','2026-10-25T10:00:00+00:00','2026-10-25T10:00:00+00:00'))
            db.commit()
            checked=[]
            def checker(item):
                checked.append(item.city)
                return CheckResult('access_restricted',403,item.url,'Test restricted')
            self.assertEqual(verify_new(db,limit=2,checker=checker),1)
            self.assertEqual(checked,['Gdynia'])
            self.assertEqual(db.execute(
                "SELECT verification_status FROM discoveries WHERE city='Toruń'"
            ).fetchone()[0],None)
            db.close()

class SellerPriorityTests(unittest.TestCase):
    def test_owner_claim_is_priority_but_not_confirmed(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Sprzedaż prywatna - 2 pokoje", "Bez pośredników, od właściciela")
        self.assertEqual(item.category,'owner_claim')
        self.assertTrue(item.primary)
        self.assertIn("deklaracja",item.evidence)

    def test_agency_stays_out_of_priority(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Apartament 2 pokoje", "Oferta biura nieruchomości")
        self.assertEqual(item.category,'agency_unknown')
        self.assertFalse(item.primary)

    def test_agency_zero_buyer_fee_is_prioritized_but_marked_claim(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Mieszkanie 45m2 - bez prowizji dla kupującego",
                             "Biuro nieruchomości; cena 620 000 zł")
        self.assertEqual(item.category,'zero_fee_claim')
        self.assertTrue(item.primary)
        self.assertIn("deklaracja",item.evidence)

    def test_agency_fee_stays_out_even_with_owner_claim(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Bez pośredników", "Biuro nieruchomości. Prowizja dla kupującego")
        self.assertEqual(item.category,'agency_fee')
        self.assertFalse(item.primary)

    def test_ambiguous_no_owner_no_fee_never_becomes_zero(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Piękne mieszkanie w Gdańsku", "Mieszkanie na sprzedaż")
        self.assertEqual(item.category,'unknown')
        self.assertFalse(item.primary)

    def test_inconsistent_agency_owner_claim_is_conflict(self):
        from home_hunter.seller import classify_seller
        item=classify_seller("Sprzedaż prywatna", "Oferta biura nieruchomości")
        self.assertEqual(item.category,'conflict')
        self.assertFalse(item.primary)

    def test_bing_search_queries_prioritize_owner_and_zero_fee(self):
        from home_hunter.discover import search_queries
        found=[q for city,q in search_queries(cities=('Gdynia',))]
        self.assertIn("bez pośredników",found[0])
        self.assertTrue(any("od właściciela" in x for x in found))
        self.assertTrue(any("bez prowizji" in x for x in found))
        self.assertTrue(any('site:olx.pl mieszkanie' in x for x in found))

    def test_report_separates_owner_unknown_and_agency(self):
        from home_hunter.ranking import discovery_report
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            conn=connect(str(Path(d)/'buyers.sqlite3'))
            now=datetime(2026,10,25,10,30,tzinfo=timezone.utc)
            cases=[
                ('owner','Gdynia mieszkanie sprzedaż prywatna bez pośredników'),
                ('agency','Gdańsk mieszkanie biuro nieruchomości'),
                ('unknown','Sopot mieszkanie sprzedaż 2 pokoje'),
                ('zero','Gdańsk mieszkanie bez prowizji dla kupującego'),
            ]
            for label,title in cases:
                city = 'Gdynia' if label=='owner' else ('Sopot' if label=='unknown' else 'Gdańsk')
                conn.execute("""INSERT INTO discoveries
                (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (f'https://domy.pl/mieszkanie/{label}-2-pokoje-560000-pln-40m2-sba/dol1745111{len(label)}',
                city,'domy.pl',title,title,560000,40,'preliminary',now.isoformat(),now.isoformat()))
            conn.commit()
            report='\\n'.join(discovery_report(conn,limit=8,now=now))
            p1=report.split('### 1. ')[1].split('### 2. ')[0]
            p2=report.split('### 2. ')[1].split('### 3. ')[0]
            p3=report.split('### 3. ')[1].split('### 4. ')[0]
            self.assertIn('owner-2-pokoje',p1)
            self.assertIn('zero-2-pokoje',p1)
            self.assertNotIn('agency-2-pokoje',p1)
            self.assertNotIn('unknown-2-pokoje',p1)
            self.assertIn('unknown-2-pokoje',p2)
            self.assertIn('agency-2-pokoje',p3)
            self.assertIn('NIEPOTWIERDZONE',report)
            conn.close()

    def test_verification_prefers_private_owner_over_agency(self):
        from home_hunter.feeds import verify_new
        from home_hunter.verify import CheckResult
        with tempfile.TemporaryDirectory() as d:
            conn=connect(str(Path(d)/'priority.sqlite3'))
            inputs=[
                ('agency','Gdańsk mieszkanie, oferta agencji nieruchomości'),
                ('owner','Gdynia mieszkanie bez pośredników - sprzedaż prywatna'),
            ]
            for label,title in inputs:
                city = 'Gdynia' if label == 'owner' else 'Gdańsk'
                conn.execute("""INSERT INTO discoveries
                (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (f'https://domy.pl/mieszkanie/{label}-2-pokoje-600000-pln-45m2-sba/dol174598700{len(label)}',
                city,'domy.pl',title,title,600000,45,'preliminary',
                '2026-10-25T10:00:00+00:00','2026-10-25T10:00:00+00:00'))
            conn.commit()
            checked=[]
            def checker(offer):
                checked.append(offer.url)
                return CheckResult('access_restricted',403,offer.url,'blocked')
            self.assertEqual(verify_new(conn,limit=1,checker=checker),1)
            self.assertIn('/owner-',checked[0])
            conn.close()

class StrictBuyerCommissionTests(unittest.TestCase):
    def test_generic_without_commission_is_ambiguous_not_priority(self):
        from home_hunter.seller import classify_seller
        candidate=classify_seller("Mieszkanie 60 m²", "Bez prowizji, korzystne warunki")
        self.assertEqual(candidate.category,'fee_ambiguous')
        self.assertFalse(candidate.primary)

    def test_generic_zero_percent_is_ambiguous(self):
        from home_hunter.seller import classify_seller
        candidate=classify_seller("Apartament 0% prowizji", "Sprzedam mieszkanie")
        self.assertEqual(candidate.category,'fee_ambiguous')
        self.assertFalse(candidate.primary)

    def test_explicit_buyer_zero_commission_is_priority(self):
        from home_hunter.seller import classify_seller
        candidate=classify_seller("Oferta biura", "Kupujący nie płaci prowizji")
        self.assertEqual(candidate.category,'zero_fee_claim')
        self.assertTrue(candidate.primary)

    def test_generic_fee_claim_stays_in_unknown_section(self):
        from home_hunter.ranking import discovery_report
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            conn=connect(str(Path(d)/'fee.sqlite3'))
            now=datetime(2026,10,25,10,30,tzinfo=timezone.utc)
            for label,title in [('vague','Gdynia mieszkanie bez prowizji'),
                                ('buyer','Gdynia mieszkanie bez prowizji dla kupującego')]:
                conn.execute("""INSERT INTO discoveries
                   (url,city,source,title,snippet,price_pln,area_m2,status,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (f'https://domy.pl/mieszkanie/{label}-2-pokoje-550000-pln-40m2-sba/dol1745681{len(label)}',
                    'Gdynia','domy.pl',title,title,550000,40,'preliminary',
                    now.isoformat(),now.isoformat()))
            conn.commit()
            report='\\n'.join(discovery_report(conn,now=now))
            primary=report.split('### 1. ')[1].split('### 2. ')[0]
            needs_review=report.split('### 2. ')[1].split('### 3. ')[0]
            self.assertNotIn('/vague-',primary)
            self.assertIn('/buyer-',primary)
            self.assertIn('/vague-',needs_review)
            conn.close()
