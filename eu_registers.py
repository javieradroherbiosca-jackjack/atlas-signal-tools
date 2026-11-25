"""Nerva — Smart Money stakes in European companies from national major-holdings registers.

Complements the backend's /api/smartmoney/major-holdings (UK FCA, NL AFM, CH SIX) and the
weekly job's bafin.csv (DE) with: FR (AMF), IT (CONSOB), SE (Finansinspektionen), NO (Oslo
NewsWeb), DK (Finanstilsynet OAM) and BE (FSMA). Run by the weekly job before gen_triple.py
(it needs internet access to those sites; the backend's host is refused by some of them).

Inputs (same folder):
  major-holdings.json       backend response — only its "aliases" (fundId, manager, holder regex) are used
  prev-triple-signal.json   last published Triple Signal file — carries "euRegisters" (state + stakes),
                            so SE/NO only fetch notices newer than last week's cursor
Output:
  eu-registers.json         {asOf, sources:{fr,it,se,no,dk,be}, stakes:[...], state:{...}}
                            gen_triple.py merges the stakes and copies the state into triple-signal.json

Each stake: {fundId, manager, holder, issuer, isin, country, pct, date, source, url}.
Only managers Nerva tracks (aliases) are kept; pct >= 3 and <= 40 at output.
Usage: python3 eu_registers.py [--only fr,it,...] [--se-from ID] [--no-from YYYY-MM-DD]
"""
import json, os, re, sys, time, html, subprocess, tempfile, datetime as dt, urllib.request, urllib.parse, concurrent.futures as cf

UA = {'User-Agent': 'Mozilla/5.0 (compatible; NervaGlobalTerminal/1.0; +https://nervaglobalterminal.com)'}
TODAY = dt.date.today()
YEARS = 3
CUT = (TODAY - dt.timedelta(days=365 * YEARS)).isoformat()
J = lambda f, d=None: json.load(open(f)) if os.path.exists(f) else d

args = sys.argv[1:]
ONLY = set(args[args.index('--only') + 1].split(',')) if '--only' in args else None
SE_FROM = int(args[args.index('--se-from') + 1]) if '--se-from' in args else None
NO_FROM = args[args.index('--no-from') + 1] if '--no-from' in args else None

mh = J('major-holdings.json', {}) or {}
ALIASES = [(f, m, re.compile(rx, re.I)) for f, m, rx, *_ in mh.get('aliases', [])]
if not ALIASES:
    sys.exit('major-holdings.json has no "aliases" (backend /api/smartmoney/major-holdings)')
# Search terms: the most distinctive literal words of each holder regex (for full-text search boxes).
TERMS = {
    'dodge_cox': 'Dodge', 'oakmark': 'Harris Associates', 'first_eagle': 'First Eagle', 'tweedy_browne': 'Tweedy',
    'longleaf': 'Southeastern', 'baillie_gifford': 'Baillie Gifford', 'tci': "Children's Investment", 'cevian': 'Cevian',
    'flossbach': 'Flossbach', 'comgest': 'Comgest', 'carmignac': 'Carmignac', 'silchester': 'Silchester',
    'fundsmith': 'Fundsmith', 'lindsell_train': 'Lindsell', 'egerton': 'Egerton', 'ako': 'AKO Capital',
    'cobas': 'Cobas', 'azvalor': 'azValor', 'bestinver': 'Bestinver', 'magallanes': 'Magallanes', 'gqg': 'GQG',
    'handelsbanken': 'Handelsbanken Fonder', 'ohman': 'Öhman', 'exor': 'Exor', 'elliott': 'Elliott',
    'third_point': 'Third Point', 'trian': 'Trian', 'valueact': 'ValueAct', 'pershing': 'Pershing Square',
    'jupiter': 'Jupiter', 'polar_capital': 'Polar Capital', 'marathon': 'Marathon Asset', 'veritas': 'Veritas',
    'ifp': 'Independent Franchise', 'wcm': 'WCM', 'oasis': 'Oasis Management', 'lone_pine': 'Lone Pine',
    'viking': 'Viking Global', 'gmo': 'Grantham', 'man_group': 'Man Group', 'ruffer': 'Ruffer', 'mirabaud': 'Mirabaud',
    'bellevue': 'Bellevue', 'kiltearn': 'Kiltearn', 'hosking': 'Hosking', 'cantillon': 'Cantillon', 'davis': 'Davis Selected',
    'icahn': 'Icahn', 'soros': 'Soros', 'berkshire': 'Berkshire', 'sands_capital': 'Sands Capital', 'platinum': 'Platinum Investment',
    'orbis': 'Orbis', 'oaktree': 'Oaktree', 'blue_whale': 'Blue Whale', 'aspex': 'Aspex', 'fpa': 'First Pacific Advisors',
    'harris': 'Harris Associates', 'tweedy': 'Tweedy', 'third_avenue': 'Third Avenue', 'markel': 'Markel', 'fairfax': 'Fairfax',
}
prev = (J('prev-triple-signal.json', {}) or {}).get('euRegisters', {}) or {}
pstate = prev.get('state', {}) or {}
pstakes = prev.get('allStakes') or prev.get('stakes') or []


def alias_of(text):
    return next((a for a in ALIASES if a[2].search(text or '')), None)


def get(url, data=None, headers=None, tries=3, timeout=40, raw=False):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                if r.geturl() and 'perfdrive' in r.geturl():
                    raise IOError('bot check')
                return b if raw else b.decode('utf-8', 'ignore')
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def pdf_text(b):
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        f.write(b); f.flush()
        return subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True, text=True, timeout=60).stdout


def num(s):
    try:
        return float(str(s).replace(' ', '').replace(' ', '').replace('%', '').replace(',', '.'))
    except Exception:
        return None


def stake(a, holder, issuer, pct, date, country, source, url, isin=None):
    return {'fundId': a[0], 'manager': a[1], 'holder': (holder or '')[:120], 'issuer': issuer, 'isin': isin,
            'country': country, 'pct': pct, 'date': date, 'source': source, 'url': url}


def latest_by_key(rows):
    out = {}
    for s in rows:
        k = f"{s['fundId']}|{(s['issuer'] or '').lower()}"
        if k not in out or (s.get('date') or '') >= (out[k].get('date') or ''):
            out[k] = s
    return out


# % held after the event, read next to the holder's name (not "the last % in the text", which in a notice is
# often another shareholder's or the threshold). None when not certain. Exits are flagged separately.
_PCT = r'(\d{1,2}(?:[.,]\d{1,4})?)\s?(?:%|per cent|percent|prosent)'
_PCT_PATS = [re.compile(p, re.I) for p in (
    r'(?:corresponding to|corresponds to|corresponded to|amounts? to|amounting to|representing|equal to|equivalent to|totall?ing|total of)\s+(?:approximately |approx\.? |about )?' + _PCT + r'\s+of\s+(?:the\s+)?(?:total\s+)?(?:number of\s+)?(?:issued\s+)?(?:share capital|voting rights|shares|issued|outstanding|capital|votes|all shares|registered)',
    r'(?:holds?|held|owns?|owned|now holds?|holding|holding of|holding amounts to|holding is|holdings? (?:amounts?|totals?) to)\s+(?:a total of\s+)?(?:approximately |approx\.? )?' + _PCT,
    r'Resulting situation[^%]{0,200}?' + _PCT,
    r'Total of both in %[^%]{0,300}?' + _PCT)]
# full exits only: "below 5%" notices usually give the % still held (4.9%), which stays a stake (>= 3%)
_EXIT = re.compile(r'no longer (?:indirectly |directly )?(?:holds?|owns?|has)|ceased to (?:hold|own)|disposed of all|sold all (?:of )?its|holds? no (?:shares|voting)|no longer a (?:major |substantial )?shareholder', re.I)


def read_holder_pct(text, rx):
    """(pct, exited) for the holder matched by rx, or (None, False) if the text doesn't name it."""
    m = rx.search(text or '')
    if not m:
        return None, False
    seg = text[max(0, m.start() - 300): m.start() + 2500]
    exited = bool(_EXIT.search(seg))
    for p in _PCT_PATS:
        mm = p.search(seg)
        if mm:
            v = num(mm.group(1))
            if v is not None and 0 < v <= 60:
                return (0.0 if exited else v), exited
    return (0.0 if exited else None), exited


def home_country(issuer, default):
    """Country of the company (a Finnish Oyj listed in Oslo or Stockholm is still Finnish)."""
    i = issuer or ''
    for rx, c in ((r'\bOyj\b|\bAbp\b', 'FI'), (r'\bA/S\b', 'DK'), (r'\bASA\b', 'NO'), (r'\bAB\b(?: \(publ\))?', 'SE'), (r'\bhf\.?$', 'IS')):
        if re.search(rx, i):
            return c
    return default


# ── FR: AMF BDIF (declarations de franchissement de seuil) ─────────────────────
def build_fr():
    done = dict(pstate.get('fr_docs', {}))   # numero → parsed stake (or None): PDFs are fetched once
    rows = []
    for fid, term in TERMS.items():
        a = next((x for x in ALIASES if x[0] == fid), None)
        if not a:
            continue
        q = urllib.parse.urlencode({'TypesInformation': 'SPDE', 'RechercheTexte': term, 'From': 0, 'Size': 60})
        try:
            d = json.loads(get(f'https://bdif.amf-france.org/back/api/v1/informations?{q}', headers={'Accept': 'application/json'}))
        except Exception as e:
            print('  FR search failed', term, e); continue
        seen_issuer = set()
        for r in d.get('result', []):
            date = (r.get('dateInformation') or '')[:10]
            if date < CUT:
                continue
            issuer = ', '.join(s.get('raisonSociale') or '' for s in r.get('societes', []))
            if issuer in seen_issuer:          # results come newest first: the first one is the current stake
                continue
            num_ = r.get('numero')
            key = f'{num_}|{fid}'
            if key not in done:
                docs = [x for x in r.get('documents', []) if x.get('path')]
                st = None
                if docs:
                    try:
                        t = pdf_text(get('https://bdif.amf-france.org/back/api/v1/documents/' + docs[0]['path'], raw=True))
                        tt = re.sub(r'\s+', ' ', t)
                        if a[2].search(tt):
                            # the last "soit X% du capital" is the most recent crossing in the notice
                            m = re.findall(r'soit\s+([\d\s]+,\d+|\d+)\s*%\s*du capital', tt)
                            mv = re.findall(r'([\d\s]+,\d+|\d+)\s*%\s*des droits de vote', tt)
                            pct = num(m[-1]) if m else (num(mv[-1]) if mv else None)
                            isin = (re.search(r'\b([A-Z]{2}[A-Z0-9]{9}\d)\b', tt) or [None, None])[1]
                            st = stake(a, term, issuer, pct, date, 'FR', 'AMF (BDIF)',
                                       'https://bdif.amf-france.org/fr/details/' + str(num_), isin)
                    except Exception as e:
                        print('  FR pdf failed', num_, e)
                        continue
                    time.sleep(0.3)
                done[key] = st
            if done[key]:
                rows.append(done[key]); seen_issuer.add(issuer)
        time.sleep(0.4)
    return list(latest_by_key(rows).values()), {'fr_docs': {k: v for k, v in done.items() if v}}


# ── IT: CONSOB partecipazioni rilevanti (current snapshot, per issuer) ─────────
def build_it():
    base = 'https://www.consob.it/web/area-pubblica/quotate/azionariati-attuali?p_p_id=it_consob_QuotateAssettiPortlet&p_p_lifecycle=0&_it_consob_QuotateAssettiPortlet_delta=50&_it_consob_QuotateAssettiPortlet_cur='
    links = []
    for page in range(1, 8):
        t = get(base + str(page))
        ls = re.findall(r'href="(https://www\.consob\.it/web/area-pubblica/w/[^"?]*azionariato[^"?]*)', t)
        new = [x for x in dict.fromkeys(ls) if x not in links]
        if not new:
            break
        links += new
        time.sleep(1.5)
    rows, failed = [], 0
    for url in links:
        try:
            t = get(url)
        except Exception:
            failed += 1; time.sleep(5); continue
        issuer = html.unescape((re.search(r'<h1[^>]*>([^<]+)</h1>', t) or re.search(r'<title>([^<|]+)', t) or [None, url])[1]).strip()
        issuer = re.sub(r'\s*-?\s*azionariato.*$', '', issuer, flags=re.I).strip()
        m = re.search(r'<table[^>]*partecipazioni[\s\S]*?</table>', t)
        if m:
            for tr in re.findall(r'<tr[\s\S]*?</tr>', m.group(0)):
                c = [re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', x))).strip() for x in re.findall(r'<t[dh][\s\S]*?</t[dh]>', tr)]
                if len(c) < 9:
                    continue
                a = alias_of(c[0] + ' ' + c[1])
                if not a:
                    continue
                d, mo, y = (c[8].split('/') + ['', '', ''])[:3]
                rows.append(stake(a, c[0], issuer, num(c[7]) or num(c[2]), f'{y}-{mo}-{d}', 'IT', 'CONSOB', url))
        time.sleep(2.0)   # the site sits behind a bot manager: keep it slow
    if failed > len(links) / 3:
        raise IOError(f'CONSOB: {failed}/{len(links)} pages failed')
    return list(latest_by_key(rows).values()), {}


# ── SE: Finansinspektionen flaggningar (sequential ids; incremental) ───────────
def _fi(id_):
    t = get(f'https://finanscentralen.fi.se/search/EditFlagging.aspx?id={id_}', tries=2, timeout=30)
    v = lambda k: html.unescape((re.search(r'id="ctl00_main_ctlSharePresent_' + k + r'"[^>]*>([^<]*)<', t) or [None, ''])[1]).strip()
    names = [html.unescape(x).strip() for x in re.findall(r'grdHolders_ctl\d+_lblName"[^>]*>([^<]*)<', t)]
    pub = v('lblPublishingDate')
    return {'id': id_, 'issuer': v('lblEmittentValue'), 'holders': names, 'pct': num(v('lblVotingRightsPercentageTotal') or v('lblPostTransGroupVoteShare')),
            'date': v('lblTransDate') or pub[:10], 'pub': pub[:10], 'isin': (re.search(r'\b([A-Z]{2}[A-Z0-9]{9}\d)\b', v('lblInstrument')) or [None, None])[1],
            'comment': v('txtCommentary')[:300]}


def build_se():
    last = SE_FROM or int(pstate.get('se_last', 0) or 0)
    if not last:
        raise IOError('no SE cursor: run once with --se-from ID')
    rows = [s for s in pstakes if s.get('country') == 'SE']
    hi, misses, id_ = last, 0, last + 1
    with cf.ThreadPoolExecutor(4) as ex:
        while misses < 60:
            batch = list(range(id_, id_ + 40))
            id_ += 40
            for r in ex.map(lambda i: (_safe(_fi, i)), batch):
                if not r or not r['issuer'] or r['pub'].startswith('0001'):
                    misses += 1 if (not r or not r['issuer']) else 0
                    continue
                misses = 0
                hi = max(hi, r['id'])
                a = alias_of(' '.join(r['holders']))   # holder names only: comments mention buyers/sellers too
                if a:
                    rows.append(stake(a, ', '.join(r['holders']), r['issuer'], r['pct'], r['date'], 'SE', 'Finansinspektionen',
                                      f"https://finanscentralen.fi.se/search/EditFlagging.aspx?id={r['id']}", r['isin']))
    return list(latest_by_key(rows).values()), {'se_last': hi}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# ── NO: Oslo Børs NewsWeb, category FLAGGING (incremental by date) ─────────────
def build_no():
    since = NO_FROM or pstate.get('no_last') or (TODAY - dt.timedelta(days=14)).isoformat()
    rows = [s for s in pstakes if s.get('country') == 'NO']
    start = dt.date.fromisoformat(since)
    ids = []
    while start <= TODAY:
        end = min(start + dt.timedelta(days=30), TODAY)
        d = json.loads(get(f'https://api3.oslo.oslobors.no/v1/newsreader/list?category=1006&fromDate={start}&toDate={end}', data=b''))
        ids += [(m['messageId'], m['title'], m['issuerName'], m['issuerSign'], m['publishedTime'][:10]) for m in d['data']['messages']]
        start = end + dt.timedelta(days=1)
        time.sleep(0.5)

    def one(x):
        mid, title, issuer, sign, pub = x
        a = alias_of(title)
        body = ''
        if not a:
            try:
                body = json.loads(get(f'https://api3.oslo.oslobors.no/v1/newsreader/message?messageId={mid}', data=b''))['data']['message'].get('body') or ''
            except Exception:
                return None
            a = alias_of(body)
            if not a:
                return None
        elif True:
            try:
                body = json.loads(get(f'https://api3.oslo.oslobors.no/v1/newsreader/message?messageId={mid}', data=b''))['data']['message'].get('body') or ''
            except Exception:
                body = ''
        text = title + ' ' + re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', body)))
        pct, exited = read_holder_pct(text, a[2])
        if exited:
            pct = 0.0          # 26 Nov 2026 (TORM): "Oaktree no longer holds" used to be read as the 19.86% another holder keeps
        if pct is None:
            return None        # % not readable: keep the last notice we could read rather than overwrite it with a blank
        return stake(a, title, issuer, pct, pub, home_country(issuer, 'NO'), 'Oslo Børs NewsWeb',
                     f'https://newsweb.oslobors.no/message/{mid}')
    with cf.ThreadPoolExecutor(4) as ex:
        rows += [r for r in ex.map(one, ids) if r]
    return list(latest_by_key(rows).values()), {'no_last': TODAY.isoformat()}


# ── DK: Finanstilsynet OAM, major shareholder announcements ────────────────────
TR1_RES = re.compile(r'Resulting situation[^\d<]{0,120}?([<>]?\s*\d{1,3}[.,]\d+|\d{1,3})\s*%?\s+([<>]?\s*\d{1,3}[.,]\d+|\d{1,3})?\s*%?\s+([<>]?\s*\d{1,3}[.,]\d+|\d{1,3})?', re.I)


def build_dk():
    done = dict(pstate.get('dk_docs', {}))
    api = 'https://appft.gold.extension.gopublic.dk/api/9217fa13-5d9a-46c6-9921-69ee7e6cfaf6'
    rows = []
    for fid, term in TERMS.items():
        a = next((x for x in ALIASES if x[0] == fid), None)
        if not a:
            continue
        body = json.dumps({'query': term, 'filters': [{'type': 'dropdown', 'key': 'CategoryFilter', 'options': ['FT_MJSHLD', 'MJSHLD']}], 'page': 1, 'pageSize': 50}).encode()
        try:
            d = json.loads(get(api + '/search', data=body, headers={'Content-Type': 'application/json'}))
        except Exception as e:
            print('  DK search failed', term, e); continue
        for r in d.get('data', {}).get('rows', []):
            dd, mm, yy = r['PublicationDateColumn'][:10].split('-')
            date = f'{yy}-{mm}-{dd}'
            if date < CUT or not a[2].search(r.get('HeadlineColumn', '')):
                continue
            key = f"{r['id']}|{fid}"
            if key not in done:
                st = None
                try:
                    det = json.loads(get(f"{api}/details/{r['id']}"))
                    urls = [e['value']['url'] for s in det.get('sections', []) for e in s.get('elements', [])
                            if e.get('value', {}).get('type') == 'link' and e['value'].get('url', '').lower().endswith('.pdf')]
                    for u in urls:
                        tt = re.sub(r'\s+', ' ', pdf_text(get(urllib.parse.quote(u, safe=':/'), raw=True)))
                        m = TR1_RES.search(tt)
                        if m:
                            vals = [num((x or '').replace('<', '').replace('>', '')) for x in m.groups()]
                            pct = vals[2] if vals[2] is not None else vals[0]
                            st = stake(a, r['HeadlineColumn'], r['IssuerColumn'], pct, date, 'DK', 'Finanstilsynet (OAM)',
                                       f"https://oam.finanstilsynet.dk/#!/announcement/{r['id']}")
                            break
                except Exception as e:
                    print('  DK detail failed', r['id'], e)
                    continue
                done[key] = st
                time.sleep(0.3)
            if done[key]:
                rows.append(done[key])
        time.sleep(0.3)
    return list(latest_by_key(rows).values()), {'dk_docs': {k: v for k, v in done.items() if v}}


# ── DK / FI / SE / IS: Nasdaq Nordic company announcements, "Major shareholder announcements" ─────────
# Full-text search per manager; each announcement is read once (cached in state) for the % and exits.
NQ_COUNTRY = {'Copenhagen': 'DK', 'Helsinki': 'FI', 'Finland': 'FI', 'Stockholm': 'SE', 'Sweden': 'SE', 'Iceland': 'IS', 'Denmark': 'DK'}


def build_nq():
    done = dict(pstate.get('nq_docs', {}))
    rows, failed = [], 0
    for a in ALIASES:
        term = TERMS.get(a[0]) or a[1]
        q = urllib.parse.urlencode({'type': 'json', 'countResults': 'true', 'freeText': term, 'cnscategory': '144', 'globalGroup': 'exchangeNotice',
                                    'globalName': 'NordicAllMarkets', 'displayLanguage': 'en', 'language': 'en', 'limit': 100, 'start': 0, 'dir': 'DESC', 'showCompany': 'true'})
        try:
            d = json.loads(get('https://api.news.eu.nasdaq.com/news/query.action?' + q))
        except Exception as e:
            failed += 1; print('  NQ search failed', term, e); continue
        for it in ((d.get('results') or {}).get('item') or []):
            date = (it.get('published') or '')[:10]
            if date < CUT:
                continue
            key = f"{it.get('disclosureId')}|{a[0]}"
            if key not in done:
                try:
                    t = get(it['messageUrl'])
                except Exception:
                    continue
                t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<script.*?</script>|<style.*?</style>', ' ', t, flags=re.S))))
                pct, exited = read_holder_pct(t, a[2])
                if pct is None:
                    done[key] = None                         # manager not named (search noise) or % not readable: never guessed
                else:
                    ctry = next((c for k, c in NQ_COUNTRY.items() if k in (it.get('market') or '')), None)
                    done[key] = stake(a, it.get('headline'), it.get('company'), 0.0 if exited else pct, date, home_country(it.get('company'), ctry),
                                      'Nasdaq Nordic company announcement', it.get('messageUrl'))
                time.sleep(0.2)
            if done[key]:
                rows.append(done[key])
        time.sleep(0.3)
    if failed > len(ALIASES) / 3:
        raise IOError(f'Nasdaq Nordic: {failed} searches failed')
    return list(latest_by_key(rows).values()), {'nq_docs': done}


# ── BE: FSMA current shareholding structure (one PDF per issuer) ───────────────
def build_be():
    slugs = []
    for page in range(0, 20):
        u = f'https://www.fsma.be/en/data-portal?f%5B0%5D=fa_content_type%3Actissuer&f%5B1%5D=fa_law%3A106661&page={page}'
        t = get(u, headers={'Referer': u})
        s = [x for x in dict.fromkeys(re.findall(r'href="/en/issuer/([^"?#]+)"', t)) if x not in slugs]
        if not s:
            break
        slugs += s
        time.sleep(0.8)
    rows, failed = [], 0

    def one(slug):
        try:
            t = get(f'https://www.fsma.be/en/issuer/{slug}')
            pdfs = re.findall(r'(/sites/default/files/issuers/files/\d+_en\.pdf)', t)
            if not pdfs:
                return []
            txt = pdf_text(get('https://www.fsma.be' + pdfs[0], raw=True))
        except Exception:
            return None
        issuer = (re.search(r'resulting from the notifications filed with the FSMA\s+(.+)', txt) or [None, slug])[1].strip()
        out = []
        for blk in re.split(r'[\n\f]+\s*(?=Notification of \d{2}/\d{2}/\d{4})', txt):
            dm = re.match(r'Notification of (\d{2})/(\d{2})/(\d{4})', blk)
            if not dm:
                continue
            first = (re.search(r'Holder\(s\) of voting rights.*?\n\s*\d+\s+(.+?)\s{2,}', blk, re.S) or re.search(r'\n\s*1\s+(.+)', blk) or [None, ''])[1]
            a = alias_of(blk.split('TOTAL')[0])   # holder names of this notification only
            if not a:
                continue
            tot = re.search(r'TOTAL(?: voting rights)?\s+[\d.]+\s+([\d,]+)\s*%', blk)
            out.append(stake(a, first.strip(), issuer, num(tot[1]) if tot else None, f'{dm[3]}-{dm[2]}-{dm[1]}', 'BE', 'FSMA',
                             'https://www.fsma.be' + pdfs[0]))
        return out
    with cf.ThreadPoolExecutor(3) as ex:
        for r in ex.map(one, slugs):
            if r is None:
                failed += 1
            else:
                rows += r
    if failed > len(slugs) / 3:
        raise IOError(f'FSMA: {failed}/{len(slugs)} issuers failed')
    return list(latest_by_key(rows).values()), {}


# IT (CONSOB) is left out: its site sits behind a bot-check (captcha) for automated clients, and we don't get around those.
SOURCES = [('fr', build_fr), ('se', build_se), ('no', build_no), ('dk', build_dk), ('nq', build_nq), ('be', build_be)]
COUNTRY = {'fr': 'FR', 'se': 'SE', 'no': 'NO', 'dk': 'DK', 'be': 'BE', 'nq': 'NQ'}
stakes, state, sources = [], dict(pstate), {}
for k, fn in SOURCES:
    SRC = {'fr': 'AMF', 'se': 'Finansinspektionen', 'no': 'Oslo Børs NewsWeb', 'dk': 'Finanstilsynet (OAM)', 'nq': 'Nasdaq Nordic company announcement', 'be': 'FSMA'}
    old = [s for s in pstakes if (s.get('source') or '').startswith(SRC[k])]
    if ONLY and k not in ONLY:
        stakes += old; sources[k] = {'ok': True, 'skipped': True, 'stakes': len(old)}
        continue
    t0 = time.time()
    try:
        got, st = fn()
        state.update(st)
        stakes += got
        sources[k] = {'ok': True, 'stakes': len(got), 'secs': round(time.time() - t0)}
    except Exception as e:
        stakes += old   # keep last week's stakes for a source that fails
        sources[k] = {'ok': False, 'error': str(e)[:200], 'carried': len(old)}
    print(k, sources[k], flush=True)

# ISIN → home-exchange ticker (OpenFIGI, no key: 25 requests/min, 10 ISINs per request). The screener lacks
# some exchanges entirely (Euronext Paris), so name matching alone isn't enough there.
FIGI_EX = {'FR': ('FP', '.PA'), 'SE': ('SS', '.ST'), 'NO': ('NO', '.OL'), 'DK': ('DC', '.CO'), 'BE': ('BB', '.BR'), 'FI': ('FH', '.HE')}
isin_map = dict(state.get('isin_map', {}))
todo = sorted({(s['isin'], s['country']) for s in stakes if s.get('isin') and s.get('country') in FIGI_EX and s['isin'] not in isin_map})
for i in range(0, len(todo), 10):
    chunk = todo[i:i + 10]
    try:
        body = json.dumps([{'idType': 'ID_ISIN', 'idValue': isin, 'exchCode': FIGI_EX[c][0]} for isin, c in chunk]).encode()
        res = json.loads(get('https://api.openfigi.com/v3/mapping', data=body, headers={'Content-Type': 'application/json'}))
        for (isin, c), r in zip(chunk, res):
            d = (r.get('data') or [])
            eq = [x for x in d if x.get('securityType2') in ('Common Stock', 'Depositary Receipt')] or d
            isin_map[isin] = (eq[0]['ticker'].replace(' ', '-').replace('/', '-') + FIGI_EX[c][1]) if eq else None
    except Exception as e:
        print('  OpenFIGI failed', e)
        break
    time.sleep(2.6)
for s in stakes:
    if s.get('isin') and isin_map.get(s['isin']):
        s['ticker'] = isin_map[s['isin']]
state['isin_map'] = isin_map

# 26 Nov 2026: managers that left Smart Money (Handelsbanken, Polar Capital…) were still carried from last week's state
_ids = {a[0] for a in ALIASES}
stakes = [s for s in stakes if s.get('fundId') in _ids]
# the same stake can come from two sources (Oslo and Nasdaq for a dual listing): keep the latest announcement
stakes = list(latest_by_key(stakes).values())
keep = [s for s in stakes if s.get('pct') is not None]   # state keeps everything; output filters
out = [s for s in keep if 3 <= s['pct'] <= 40 or (s['fundId'] in ('exor', 'icahn', 'berkshire') and s['pct'] >= 3)]
json.dump({'asOf': TODAY.isoformat(), 'sources': sources, 'count': len(out), 'stakes': out,
           'state': state, 'allStakes': keep}, open('eu-registers.json', 'w'), ensure_ascii=False)
print('eu-registers.json:', len(out), 'stakes', {k: v.get('stakes', v.get('carried')) for k, v in sources.items()})
