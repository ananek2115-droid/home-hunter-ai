from __future__ import annotations
from dataclasses import dataclass
import ipaddress, re, socket, urllib.error, urllib.parse, urllib.request, urllib.robotparser
from html.parser import HTMLParser
from .model import Offer
from .dedupe import normalize

MAX_BYTES=650000
# Reject private endpoints and restrict scraping to explicit public listing domains.
# Any additional provider must be reviewed before being added here.
ALLOWED_DOMAINS = (
    'olx.pl', 'otodom.pl', 'morizon.pl', 'gratka.pl',
    'nieruchomosci-online.pl', 'trojmiasto.pl', 'domiporta.pl',
    'oferty.net', 'domy.pl', 'rynekpierwotny.pl', 'lento.pl',
    'partnerzy.pl', 'szybko.pl', 'homeexperts.pl',
)

def allowed_domain(host: str) -> bool:
    return any(host==suffix or host.endswith('.'+suffix) for suffix in ALLOWED_DOMAINS)
USER_AGENT='HomeHunter-ResearchValidator/0.1 (+manual-review; respects robots.txt)'

@dataclass(frozen=True)
class CheckResult:
    status: str
    http_status: int|None
    final_url: str
    reason: str

class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.skip+=1
        if tag == 'meta':
            data=dict(attrs)
            if data.get('property') in {'og:title','og:description'} or data.get('name')=='description':
                self.text.append(data.get('content',''))
    def handle_endtag(self,tag):
        if tag in {'script','style'}: self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.text.append(data)

def safe_public_url(url: str) -> bool:
    try:
        parts=urllib.parse.urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.port not in (None,443):
            return False
        # Reject IP literals (especially private/metadata endpoints). DNS rebinding is an
        # additional concern if later accepting arbitrary untrusted URLs at scale.
        try:
            ipaddress.ip_address(parts.hostname)
            return False
        except ValueError:pass
        if parts.hostname.lower() in {'localhost'} or '.' not in parts.hostname:
            return False
        if not allowed_domain(parts.hostname.lower()):
            return False
        return True
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_public_url(newurl):
            raise urllib.error.URLError('Redirect to disallowed location')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str) -> tuple[int,str,str]:
    req=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'text/html'})
    opener=urllib.request.build_opener(SafeRedirect)
    try:
        with opener.open(req,timeout=12) as resp:
            return resp.status,resp.geturl(),resp.read(MAX_BYTES).decode('utf-8','replace')
    except urllib.error.HTTPError as exc:
        return exc.code,exc.geturl(),''


def _price_seen(text: str, price) -> bool:
    digits=str(int(price))
    # 650000, 650 000, 650.000 (but not a longer adjacent price)
    return bool(re.search(r'(?<!\d)'+r'[\s.,\u00a0]*'.join(map(re.escape,[digits[:3],digits[3:]])) +r'(?!\d)',text)) if len(digits)==6 else bool(re.search(r'(?<!\d)'+re.escape(digits)+r'(?!\d)',text))


def inspect(offer: Offer, fetch=None, robots_check=True) -> CheckResult:
    """'online_matched' means page identity checked, NOT that owner confirmed availability."""
    fetch=fetch or _default_fetch
    if not safe_public_url(offer.url):
        return CheckResult('invalid_url',None,'','Unsafe or invalid URL')
    host=urllib.parse.urlsplit(offer.url).hostname
    if robots_check:
        try:
            rp=urllib.robotparser.RobotFileParser(f'https://{host}/robots.txt')
            rp.read()
            if not rp.can_fetch(USER_AGENT,offer.url):
                return CheckResult('access_restricted',None,offer.url,'robots.txt disallows automated access')
        except (OSError,ValueError,TimeoutError,urllib.error.URLError):
            return CheckResult('needs_manual_review',None,offer.url,'Could not verify robots.txt')
    try:
        status,final_url,html=fetch(offer.url)
    except (OSError,TimeoutError,urllib.error.URLError,socket.timeout) as exc:
        return CheckResult('needs_manual_review',None,offer.url,f'Network error: {type(exc).__name__}')
    if status in (404,410):return CheckResult('removed_link',status,final_url,'Listing link is gone; sale not proven')
    if status in (401,403,429):return CheckResult('access_restricted',status,final_url,'Access denied/rate limited; no availability conclusion')
    if status!=200:return CheckResult('needs_manual_review',status,final_url,'Unexpected HTTP status')
    if not safe_public_url(final_url):return CheckResult('content_mismatch',status,final_url,'Redirect to unsafe/non-HTTPS target')
    original_host=urllib.parse.urlsplit(offer.url).hostname.removeprefix('www.')
    final_host=urllib.parse.urlsplit(final_url).hostname.removeprefix('www.')
    if original_host!=final_host:return CheckResult('needs_manual_review',status,final_url,'Cross-domain redirect requires manual validation')
    if offer.listing_id and offer.listing_id.casefold() in offer.url.casefold() and offer.listing_id.casefold() not in final_url.casefold():
        return CheckResult('content_mismatch',status,final_url,'Redirect to different listing ID')
    page=PageText();page.feed(html[:MAX_BYTES]);txt=' '.join(page.text)
    norm=normalize(txt)
    if len(norm)<80:return CheckResult('needs_manual_review',status,final_url,'Not enough visible page content / JavaScript rendering')
    if any(x in norm[:5000] for x in ('ogloszenie nie jest juz dostepne','ogloszenie wygaslo','oferta nieaktualna','listing no longer available')):
        return CheckResult('removed_link',status,final_url,'In-page notice says listing unavailable')
    if any(x in norm[:4000] for x in ('captcha','verify you are human','wlacz javascript','enable javascript')):
        return CheckResult('access_restricted',status,final_url,'Anti-bot / JavaScript-only page')
    # Prefer not to label anything as confirmed on weak text: require city AND area AND price.
    city=normalize(offer.city)
    city_ok=city in norm or (city=='gdansk' and 'gdańsk' in txt.casefold())
    area=float(offer.area_m2)
    patterns={f'{area:g}',f'{area:.2f}',f'{area:.2f}'.replace('.',','),f'{area:.1f}'.replace('.',',')}
    area_ok=any(re.search(r'(?<!\d)'+re.escape(p)+r'\s*(?:m2|m²|metrow|metr)',normalize(txt.replace('²','2'))) for p in patterns)
    # Also look at numeric area mention next to 'm', but never area alone.
    if not area_ok:area_ok=any(p in txt for p in patterns) and ('m²' in txt or 'm2' in norm)
    price_ok=_price_seen(txt,offer.price_pln)
    if city_ok and area_ok and price_ok:
        return CheckResult('online_matched',status,final_url,'City, area and asking price observed; owner not contacted')
    missing=', '.join(n for n,ok in [('city',city_ok),('area',area_ok),('price',price_ok)] if not ok)
    return CheckResult('needs_manual_review',status,final_url,f'Missing/mismatched fields: {missing}')
