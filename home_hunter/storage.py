from __future__ import annotations
import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from .model import Offer, offer_to_json, offer_from_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(path: str):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS offers (
      uid INTEGER PRIMARY KEY AUTOINCREMENT,
      url TEXT NOT NULL UNIQUE,
      source TEXT NOT NULL,
      listing_id TEXT,
      offer_json TEXT NOT NULL,
      verification_status TEXT NOT NULL DEFAULT 'never_checked',
      checked_at TEXT,
      check_note TEXT,
      final_url TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      offer_uid INTEGER NOT NULL REFERENCES offers(uid) ON DELETE CASCADE,
      recorded_at TEXT NOT NULL,
      asking_price_pln TEXT NOT NULL,
      observation_type TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS checks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      offer_uid INTEGER NOT NULL REFERENCES offers(uid) ON DELETE CASCADE,
      checked_at TEXT NOT NULL,
      result TEXT NOT NULL,
      http_status INTEGER,
      final_url TEXT,
      detail TEXT
    );
    CREATE TABLE IF NOT EXISTS duplicate_candidates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      first_uid INTEGER NOT NULL,
      second_uid INTEGER NOT NULL,
      verdict TEXT NOT NULL,
      UNIQUE(first_uid,second_uid)
    );
    ''')
    c.commit();return c


def upsert_offer(c, offer: Offer):
    now=utc_now(); value=json.dumps(offer_to_json(offer),ensure_ascii=False)
    prev=c.execute('SELECT uid,offer_json FROM offers WHERE url=?',(offer.url,)).fetchone()
    if prev:
        uid=prev['uid']; old=json.loads(prev['offer_json'])
        new=offer_to_json(offer)
        identity_fields=('price_pln','area_m2','rooms','city','street','listing_id')
        needs_recheck=any(old.get(key)!=new.get(key) for key in identity_fields)
        if needs_recheck:
            c.execute('''UPDATE offers SET offer_json=?,updated_at=?,source=?,listing_id=?,
                         verification_status='never_checked',checked_at=NULL,
                         check_note='Key data changed: revalidation required',final_url=NULL
                         WHERE uid=?''',
                      (value,now,offer.source,offer.listing_id,uid))
        else:
            c.execute('UPDATE offers SET offer_json=?,updated_at=?,source=?,listing_id=? WHERE uid=?',
                      (value,now,offer.source,offer.listing_id,uid))
        if old.get('price_pln') != str(offer.price_pln):
            c.execute('INSERT INTO observations (offer_uid,recorded_at,asking_price_pln,observation_type) VALUES (?,?,?,?)',
                      (uid,now,str(offer.price_pln),'imported_price_change'))
    else:
        cur=c.execute('INSERT INTO offers (url,source,listing_id,offer_json,updated_at) VALUES (?,?,?,?,?)',
                      (offer.url,offer.source,offer.listing_id,value,now));uid=cur.lastrowid
        c.execute('INSERT INTO observations (offer_uid,recorded_at,asking_price_pln,observation_type) VALUES (?,?,?,?)',
                  (uid,now,str(offer.price_pln),'first_import'))
    c.commit();return uid


def list_offers(c):
    return [(r['uid'],offer_from_json(json.loads(r['offer_json'])),dict(r)) for r in c.execute('SELECT * FROM offers ORDER BY uid').fetchall()]


def write_check(c, uid, result, http_status=None, final_url='',detail=''):
    now=utc_now()
    c.execute('UPDATE offers SET verification_status=?,checked_at=?,check_note=?,final_url=? WHERE uid=?',
              (result,now,detail,final_url,uid))
    c.execute('INSERT INTO checks (offer_uid,checked_at,result,http_status,final_url,detail) VALUES (?,?,?,?,?,?)',
              (uid,now,result,http_status,final_url,detail))
    c.commit()


def set_candidate(c, a,b,verdict):
    if verdict=='different':return
    x,y=sorted((a,b))
    c.execute('INSERT INTO duplicate_candidates(first_uid,second_uid,verdict) VALUES (?,?,?) ON CONFLICT(first_uid,second_uid) DO UPDATE SET verdict=excluded.verdict',(x,y,verdict))
    c.commit()

