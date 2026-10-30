"""Atlas Triple Signal generator.

Inputs (same folder unless overridden):
  pbuys.json     insider open-market purchases (FMP search-insider-trades, P-Purchase)
  cross.json     /api/smartmoney/cross-fund?minHolders=1&limit=20000
  universe.json  {ticker: {name, price, yearLow, yearHigh, ...}}  (screener)
  analysts.json  optional {ticker: {buy, hold, sell, consensus, target}}
  quotes.json    optional {ticker: {price, yearLow, yearHigh}} fresher prices
Output: triple-signal.json  (read by the Terminal's Triple Signal section)

Rules: signal 1 = open-market buys (P-Purchase, A) by officers/directors (pure 10% owners
excluded) with transactionDate in the last WINDOW days, total >= MIN_USD, OR a company
buyback (bb-quarterly.json: net repurchases >= MIN_BB_PCT of market cap in the latest
quarter, and only if the company can afford it: Health 50+ or 12m FCF >= half of it, see triple_gate.py; Japan: jp-buybacks.json, EDINET monthly reports >= MIN_JPY_BUYBACK in 60 days);
held by >= 1 tracked fund (bad-CIK funds excluded); price <= MAX_FROM_LOW
above the 52-week low.
"""
import json, sys, datetime as dt, collections, os

WINDOW = 60
MIN_USD = 1_000_000          # US (SEC Form 4)
MIN_USD_INTL = 250_000       # other markets (MAR / national regulators): insider buys are smaller
MAX_FROM_LOW = 20.0   # 20% since 24 Oct 2026 (Javier: one rule across Atlas)
MIN_FUNDS = 1
SHOW = 12
SM_ALIAS = {}  # {insider ticker: ticker the funds hold} when they differ
KEY_FIX = {'DAN.MI': 'DANR.MI'}  # feed reports Danieli savings-share buys under the ordinary ticker
SUF = {'XNSE': '.NS', 'XBOM': '.BO', 'XLON': '.L', 'XTSX': '.V', 'XPAR': '.PA', 'XTSE': '.TO', 'MTAA': '.MI', 'XMIL': '.MI',
       'EXGM': '.MI', 'XHEL': '.HE', 'XSTO': '.ST', 'XSWX': '.SW', 'XETR': '.DE', 'XBRU': '.BR', 'XAMS': '.AS', 'XCSE': '.CO',
       'XOSL': '.OL', 'SSME': '.ST', 'XNGM': '.ST', 'AIMX': '.L', 'XLIS': '.LS', 'XDUB': '.IR', 'XETA': '.DE', 'XFRA': '.F', 'XMCE': '.MC', 'BMEX': '.MC', 'ALXP': '.PA', 'XATH': '.AT', 'XMAD': '.MC', 'XLIS': '.LS', 'XWBO': '.VI', 'XASX': '.AX', 'XTKS': '.T', 'XHKG': '.HK'}
CUR = {'.PA': '€', '.MI': '€', '.AS': '€', '.DE': '€', '.HE': '€', '.BR': '€', '.MC': '€', '.LS': '€', '.VI': '€', '.L': 'GBp ',
       '.SW': 'CHF ', '.ST': 'SEK ', '.CO': 'DKK ', '.OL': 'NOK ', '.TO': 'C$', '.V': 'C$', '.NS': '₹', '.BO': '₹', '.AX': 'A$', '.T': '¥', '.HK': 'HK$'}
BAD_FUNDS = {'sequoia', 'kahn', 'semper_augustus'}  # wrong CIK in registry (pending backend fix)

today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
since = today - dt.timedelta(days=WINDOW)
J = lambda f, d=None: json.load(open(f)) if os.path.exists(f) else d

buys = J('pbuys.json', [])   # new FMP P-Purchase rows (only the last ~10 days are needed: older ones come from the ledger)
cross = J('cross.json')['holdings']
uni = J('universe.json')
an = J('analysts.json', {})
qt = J('quotes.json', {})
jp_bb = (J('jp-buybacks.json', {}) or {}).get('companies', [])   # backend /api/japan/buybacks
MIN_JPY_BUYBACK = 150_000_000   # ~$1M: Japan has no public insider trades, so the company's own buyback is signal 1
# Elsewhere, signal 1 is "insiders OR the company buying back its own shares": both say the people who know the
# business best think the shares are cheap. Source: latest reported quarter's cash-flow statement (backend
# /api/buybacks/quarterly). Routine buybacks that only offset stock awards are common, so the bar is high:
# net of shares issued, at least MIN_BB_PCT of market cap in one quarter, in a quarter that ended in the last BB_FRESH days.
bbq = (J('bb-quarterly.json', {}) or {}).get('companies', [])
MIN_BB_PCT = float(os.environ.get('MIN_BB_PCT', 4.0))
BB_FRESH = 150
FX_USD = {'USD': 1, 'EUR': 1.08, 'GBP': 1.27, 'CHF': 1.12, 'SEK': .095, 'DKK': .145, 'NOK': .093, 'CAD': .73, 'AUD': .65,
          'HKD': .128, 'BRL': .18, 'PLN': .25, 'INR': .012, 'ZAR': .055, 'MXN': .055, 'JPY': .0067, 'CNY': .14, 'KRW': .00073,
          'TWD': .031, 'SGD': .74, 'IDR': .000063, 'THB': .028, 'MYR': .21, 'ILS': .27, 'TRY': .03, 'CLP': .00105, 'COP': .00025}   # approximate, only for the % of market cap
LIST_CCY = {'.L': 'GBP', '.SW': 'CHF', '.ST': 'SEK', '.CO': 'DKK', '.OL': 'NOK', '.TO': 'CAD', '.V': 'CAD', '.AX': 'AUD',
            '.HK': 'HKD', '.SA': 'BRL', '.WA': 'PLN', '.NS': 'INR', '.BO': 'INR', '.JO': 'ZAR', '.MX': 'MXN'}
intl_raw = (J('intl.json', {}) or {}).get('trades', []) + (J('eu-buys.json', {}) or {}).get('trades', [])   # eu-buys: /api/insider/international/buys (60 days, buys only)   # /api/insider/international?limit=2000
prev = J('prev-triple-signal.json', {}) or {}            # last published file: carries the international ledger


def intl_norm(x):
    try:
        t = x['transaction']
        if t['classification']['normalized_operation'] != 'BUY':
            return None
        iss = x['security']['issuer']; rp = x['reporting_person']
        mic = iss.get('exchange_mic'); tk = (iss.get('ticker') or '').upper()
        qty = float(t['quantity']['amount'] or 0); px = float(t['price']['amount'] or 0); usd = float(t['gross_value_usd']['amount'] or 0)
        who = rp.get('display_name') or rp.get('closely_affiliated_name')
        if not (tk and mic in SUF and who and qty >= 10 and px > 0 and usd > 0):
            return None   # drops unnamed / aggregated rows (e.g. qty 1 at a multi-million price)
        return {'id': x['id'], 'key': KEY_FIX.get(tk + SUF[mic], tk + SUF[mic]), 'issuer': iss.get('name'), 'person': who,
                'role': (rp.get('position') or {}).get('normalized') or 'Insider', 'date': t['execution_date'], 'usd': round(usd), 'qty': qty, 'px': px}
    except Exception:
        return None


ledger = {r['id']: r for r in prev.get('intlLedger', [])}
for x in intl_raw:
    r = intl_norm(x)
    if r:
        ledger[r['id']] = r
ledger = {k: r for k, r in ledger.items() if since.isoformat() <= r['date'] <= today.isoformat()}


NAME_FIX = {'Tan Anthony Ping Yeow': 'Anthony Tan', 'Zambrano Lozano Rogelio': 'Rogelio Zambrano Lozano', 'Lozano Rogelio Zambrano': 'Rogelio Zambrano Lozano'}


def pretty_name(raw):
    if raw in NAME_FIX or raw.title() in NAME_FIX:
        return NAME_FIX.get(raw) or NAME_FIX[raw.title()]
    # SEC style "Last First Middle" -> "First Middle Last"
    p = raw.replace(',', ' ').split()
    if len(p) < 2:
        return raw.title()
    suf = [w for w in p if w.rstrip('.').lower() in ('jr', 'sr', 'ii', 'iii', 'iv')]
    p = [w for w in p if w not in suf]
    nm = ' '.join(p[1:] + p[:1] + [w.rstrip('.').capitalize() + ('.' if w.lower().startswith(('jr', 'sr')) else '') for w in suf])
    return ' '.join((w + '.' if len(w) == 1 else w if w.endswith('.') else w.capitalize()) for w in nm.split())


def role(t):
    t = (t or '').lower()
    for k, v in (('chief executive', 'CEO'), ('ceo', 'CEO'), ('chief financial', 'CFO'), ('cfo', 'CFO'),
                 ('president', 'President'), ('chair', 'Chair'), ('director', 'Director'), ('officer', 'Officer')):
        if k in t:
            return v
    return 'Insider'


# US ledger carried in the published file: compact trades {s,c,n,o,d,q,p}
for r in prev.get('usLedger', []):
    buys.append({'symbol': r['s'], 'reportingCik': r['c'], 'reportingName': r['n'], 'typeOfOwner': r['o'], 'transactionDate': r['d'],
                 'securitiesTransacted': r['q'], 'price': r['p'], 'transactionType': 'P-Purchase', 'acquisitionOrDisposition': 'A'})
us_ledger = []
seen = set()
agg = collections.defaultdict(lambda: {'usd': 0.0, 'people': collections.defaultdict(float), 'roles': {}, 'last': '', 'sh': 0, 'cost': 0.0})
for b in buys:
    if b.get('transactionType') != 'P-Purchase' or b.get('acquisitionOrDisposition') != 'A':
        continue
    own = (b.get('typeOfOwner') or '').lower()
    if own.startswith('10 percent owner') and 'director' not in own and 'officer' not in own:
        continue
    td = b.get('transactionDate') or ''
    if not td or td < since.isoformat() or td > today.isoformat():
        continue
    key = (b['symbol'], b.get('reportingCik'), td, b.get('securitiesTransacted'), b.get('price'))
    if key in seen:  # amended filings (4/A) duplicate the original
        continue
    seen.add(key)
    usd = (b.get('securitiesTransacted') or 0) * (b.get('price') or 0)
    if usd <= 0:
        continue
    us_ledger.append({'s': b['symbol'], 'c': b.get('reportingCik'), 'n': b['reportingName'], 'o': b.get('typeOfOwner'), 'd': td,
                      'q': b.get('securitiesTransacted'), 'p': b.get('price')})
    a = agg[b['symbol']]
    a['usd'] += usd; a['sh'] += b.get('securitiesTransacted') or 0; a['cost'] += usd
    a['people'][b['reportingName']] += usd
    a['roles'][b['reportingName']] = role(own)
    a['last'] = max(a['last'], td)

def _ref_price(sym):
    u = dict(uni.get(sym) or {}); u.update({k: v for k, v in (qt.get(sym) or {}).items() if v})
    return u.get('price')

_seen_intl = set()
for r in sorted(ledger.values(), key=lambda r: r['id']):
    # Same trade reported twice (amendments get a new id): keep one.
    dk = (r['key'], r['person'], r['date'], round(r['qty']), round(r['px'], 2))
    if dk in _seen_intl:
        continue
    _seen_intl.add(dk)
    # Price-scale errors in the feed (e.g. 330117 instead of 33.01): fix by 10/100/1000/10000 against the
    # current quote, or drop the row if it can't be reconciled.
    ref = _ref_price(r['key'])
    if ref:
        if not (ref / 5 < r['px'] < ref * 5):
            f = next((f for f in (1000, 100, 10000, 10, 0.01) if ref / 2.5 < r['px'] / f < ref * 2.5), None)
            if not f:
                continue
            if f >= 1:   # the feed's price (and so its value) is off by f
                r = {**r, 'px': r['px'] / f, 'usd': round(r['usd'] / f)}
            else:        # London quotes in pence vs the trade in pounds: value is right, show the price in pence
                r = {**r, 'px': r['px'] / f}
    # A "buy" worth more than 2% of the whole company in one go isn't an open-market purchase: it's shares
    # received in an acquisition (EQT/Coller), a placement or a takeover. Not a conviction signal.
    _mc = (uni.get(r['key']) or {}).get('marketCap') or (qt.get(r['key']) or {}).get('marketCap')
    if _mc and r['usd'] > 0.02 * _mc:
        continue
    a = agg[r['key']]
    a['intl'] = True
    a['usd'] += r['usd']; a['sh'] += r['qty']; a['cost'] += r['qty'] * r['px']
    a['people'][r['person']] += r['usd']
    a['roles'][r['person']] = role(r['role'])
    a['last'] = max(a['last'], r['date'])
    a.setdefault('issuer', r['issuer'])

for c in jp_bb:
    if (c.get('yen') or 0) < MIN_JPY_BUYBACK:
        continue
    a = agg[c['ticker']]
    a['intl'] = True; a['buyback'] = True
    a['usd'] += c.get('usd') or c['yen'] / 150
    a['sh'] += c.get('shares') or 0; a['cost'] += c['yen']
    who = c.get('nameEn') or c.get('name') or c['ticker']
    a['people'][who] += c.get('usd') or c['yen'] / 150
    a['roles'][who] = 'Company buyback'
    a['last'] = max(a['last'], c.get('lastPeriodEnd') or '')
    a.setdefault('issuer', c.get('nameEn') or c.get('name'))

bb_cut = (today - dt.timedelta(days=BB_FRESH)).isoformat()
for b in bbq:
    sym = b['symbol']
    if sym.endswith('.T') or (b.get('date') or '') < bb_cut or not b.get('netUsd'):
        continue
    # The screener's market caps are already in USD; FMP quotes (names the screener lacks) are in local currency.
    if (uni.get(sym) or {}).get('marketCap'):
        mcu = uni[sym]['marketCap']
    elif (qt.get(sym) or {}).get('marketCap'):
        ccy = 'USD' if '.' not in sym else LIST_CCY.get('.' + sym.split('.')[-1], 'EUR')
        mcu = qt[sym]['marketCap'] * FX_USD.get(ccy, 1)
    else:
        continue
    # half-yearly reporters: the statement covers 6 months → compare a quarter's worth with the quarterly bar
    pct = 100 * b['netUsd'] / mcu / max(1.0, (b.get('months') or 3) / 3)
    if pct < MIN_BB_PCT or b['netUsd'] < (MIN_USD if '.' not in sym else MIN_USD_INTL) or pct > 60:   # >60%: bad market cap
        continue
    a = agg[sym]
    a['bbq'] = {'usd': b['netUsd'], 'pctMcap': round(pct, 1), 'quarter': f"{b.get('period')} {b.get('fiscalYear')}", 'periodEnd': b['date'],
                'months': b.get('months') or 3}

holders = {}
for h in cross:
    t = (h.get('ticker') or '').upper()
    hs = [x for x in h.get('holders', []) if x.get('fundId') not in BAD_FUNDS]
    if t and hs:
        holders[t] = hs

# European / UK stakes from the official major-holdings registers (backend /api/smartmoney/major-holdings):
# 13F only covers US listings, so these add what Atlas managers own in Europe (>= 3% stakes).
_mhj = J('major-holdings.json', {}) or {}
mh = list(_mhj.get('stakes', []))
# BaFin (DE) refuses the backend's host, so the weekly job downloads its CSV itself (bafin.csv) and we read it
# here with the same manager aliases the backend uses.
if os.path.exists('bafin.csv') and _mhj.get('aliases'):
    import re as _re0
    _al = [(f, m, _re0.compile(rx, _re0.I)) for f, m, rx, *_ in _mhj['aliases']]
    _have = {(x['fundId'], (x.get('issuer') or '').lower()) for x in mh}
    for _line in open('bafin.csv', encoding='utf-8-sig', errors='ignore'):
        _f = _line.rstrip('\r\n').split(';')
        if len(_f) < 10:
            continue
        _a = next((a for a in _al if a[2].search(_f[3])), None)
        if not _a or (_a[0], _f[0].lower()) in _have:
            continue
        try:
            _pct = float((_f[8] or _f[6]).replace(',', '.'))
        except ValueError:
            continue
        if _pct < 3 or _pct > 40:
            continue
        _d, _m, _y = (_f[9].split('.') + ['', '', ''])[:3]
        mh.append({'fundId': _a[0], 'manager': _a[1], 'holder': _f[3], 'issuer': _f[0], 'country': 'DE', 'pct': _pct,
                   'date': f"{_y}-{_m}-{_d}", 'source': 'BaFin register'})
        _have.add((_a[0], _f[0].lower()))
# FR / IT / SE / NO / DK / BE registers, read by eu_registers.py in the weekly job (eu-registers.json).
_eur = J('eu-registers.json', {}) or {}
_mhk = {(y['fundId'], (y.get('issuer') or '').lower()) for y in mh}
mh += [x for x in _eur.get('stakes', []) if (x.get('fundId'), (x.get('issuer') or '').lower()) not in _mhk]
MH_SUF = {'UK': ('.L',), 'NL': ('.AS',), 'DE': ('.DE', '.F'), 'CH': ('.SW',), 'FR': ('.PA',), 'IT': ('.MI',), 'SE': ('.ST',),
          'DK': ('.CO',), 'NO': ('.OL',), 'BE': ('.BR',), 'ES': ('.MC',)}
import re as _re
_STOP = r"\b(the|plc|p l c|n ?v|s ?e|se|ag|sa|s ?a|spa|s p a|asa|ab|publ|a s|oyj|ltd|limited|holdings?|group|groupe|company|co|inc|corporation|aktiengesellschaft|kgaa|& co|international)\b"
def _norm(x):
    x = (x or '').lower().replace('&', ' ')
    x = _re.sub(r"[^a-z0-9 ]", ' ', x)
    x = _re.sub(_STOP, ' ', x)
    return _re.sub(r"\s+", ' ', x).strip()
mh_noquote = set()
_by_name = collections.defaultdict(list)
for _src in (uni, qt):   # quotes.json also names tickers the screener lacks (Euronext Paris)
    for _t, _u in _src.items():
        if '.' in _t and (_u or {}).get('name') and _t not in _by_name.get(_norm(_u['name']), []):
            _by_name[_norm(_u['name'])].append(_t)
def mh_ticker(st):
    sufs = MH_SUF.get(st.get('country'), ())
    t = st.get('ticker')
    if t and '-.' in t:   # OpenFIGI "BP/" style → "BP-.L"; the screener has "BP.L"
        t = t.replace('-.', '.')
    if t and t.endswith(sufs):   # the ISIN resolver can pick a secondary listing (GCC.DE for C&C): home exchange only
        if t in uni:
            return t
        m = _re.match(r'^(.+?)([ABCD])(\.ST|\.CO|\.HE|\.OL)$', t)   # OpenFIGI "ELUXB" → screener "ELUX-B"
        if m and f'{m[1]}-{m[2]}{m[3]}' in uni:
            return f'{m[1]}-{m[2]}{m[3]}'
        if t.endswith('.PA'):   # Euronext Paris isn't in the screener universe: keep it, quotes come from FMP
            mh_noquote.add(t)
            return t
    n = _norm(st.get('issuer'))
    if not n:
        return None
    c = [x for x in _by_name.get(n, []) if x.endswith(sufs) and '-PREF' not in x and '-P.' not in x]
    if c and c[0] not in uni:
        mh_noquote.update(x for x in c if x.endswith('.PA'))
    # exact (normalised) name only: prefix matching mixed up Ashtead Group / Ashtead Technology
    return sorted(c, key=lambda x: -((uni.get(x) or qt.get(x) or {}).get('marketCap') or 0))[0] if c else None
mh_added = 0
mh_unmapped = []
for st in mh:
    if st.get('fundId') in BAD_FUNDS:
        continue
    t = mh_ticker(st)
    if not t:
        mh_unmapped.append(st.get('issuer'))
        continue
    hs = holders.setdefault(t, [])
    if any(x.get('fundId') == st['fundId'] for x in hs):
        continue
    hs.append({'fundId': st['fundId'], 'manager': st.get('manager'), 'fundName': st.get('manager'), 'value': 0,
               'stakePct': st.get('pct'), 'via': st.get('source')})
    mh_added += 1

out = []
needs = {'quotes': [], 'analysts': [], 'candidates': []}  # candidates: pass insider + Smart Money tests; refresh their quotes
for sym, a in agg.items():
    intl = a.get('intl', False) or '.' in sym
    hsym = SM_ALIAS.get(sym, sym)
    ins_ok = not a.get('buyback') and a['usd'] >= (MIN_USD_INTL if intl else MIN_USD)
    bb = a.get('bbq')
    if not (ins_ok or a.get('buyback') or bb) or hsym not in holders or len(holders[hsym]) < MIN_FUNDS:
        continue
    needs['candidates'].append(sym)
    u = dict(uni.get(sym) or {}); u.update({k: v for k, v in (qt.get(sym) or {}).items() if v})
    p, lo, hi = u.get('price'), u.get('yearLow'), u.get('yearHigh')
    if not (p and lo and hi) or lo <= 0:
        needs['quotes'].append(sym)
        continue
    fl = (p / lo - 1) * 100
    if fl > MAX_FROM_LOW:
        continue
    hs = sorted(holders[hsym], key=lambda x: -(x.get('value') or 0))
    market = 'JP' if sym.endswith('.T') else ('INTL' if intl else 'US')
    if ins_ok or a.get('buyback'):   # insider buys (or Japan's monthly buyback reports)
        top = sorted(a['people'].items(), key=lambda kv: -kv[1])
        sig = 'buyback' if a.get('buyback') else ('both' if bb else 'insider')
        s1 = {'insiderUsd': round(a['usd']), 'insiders': len(a['people']), 'lastBuy': a['last'],
              'avgBuyPrice': round(a['cost'] / a['sh'], 2) if a['sh'] else None,
              'topBuyer': {'name': top[0][0] if intl else pretty_name(top[0][0]), 'role': a['roles'][top[0][0]], 'usd': round(top[0][1])}}
    else:                            # company buyback only (latest quarter)
        sig = 'buyback'
        s1 = {'insiderUsd': round(bb['usd']), 'insiders': 0, 'lastBuy': bb['periodEnd'], 'avgBuyPrice': None,
              'topBuyer': {'name': u.get('name') or sym, 'role': 'Company buyback', 'usd': round(bb['usd'])}}
    row = {
        'ticker': sym, 'name': u.get('name') or a.get('issuer') or sym, 'market': market, 'signal': sig,
        'cur': next((c for k, c in CUR.items() if sym.endswith(k)), '$'),
        'price': round(p, 2), 'yearLow': round(lo, 2), 'yearHigh': round(hi, 2),
        'fromLow': round(fl, 1), 'fromHigh': round((p / hi - 1) * 100, 1),
        **s1,
        'funds': len(hs),
        'holders': [{'fundId': x['fundId'], 'manager': x.get('manager'), 'fundName': x.get('fundName'), **({'stakePct': x['stakePct']} if x.get('stakePct') else {})} for x in hs],
        'analysts': an.get(sym),
    }
    if bb:
        row['buyback'] = bb
    out.append(row)

needs['analysts'] = [r['ticker'] for r in out if not r['analysts']]
# Fund-held stocks near the low (outside Japan): the list to check for company buybacks
# (backend /api/buybacks/quarterly, in chunks of 250) → bb-quarterly.json, then run again.
_bbc = []
for t, hs in holders.items():
    if t.endswith('.T'):
        continue
    u = dict(uni.get(t) or {}); u.update({k: v for k, v in (qt.get(t) or {}).items() if v})
    p, lo = u.get('price'), u.get('yearLow')
    if p and lo and lo > 0 and (p / lo - 1) * 100 <= MAX_FROM_LOW:
        _bbc.append(t)
needs['buyback_check'] = sorted(_bbc)
# register-held names the screener doesn't carry (Paris): ask for their quotes, then they enter the checks above
needs['quotes'] = sorted(set(needs['quotes']) | {t for t in mh_noquote if not (qt.get(t) or {}).get('price')})
json.dump(needs, open('needs.json', 'w'))
# 30 Oct 2026: buyback-only names must afford the buyback (Health 50+ or 12m FCF >= half of it); adds 'health' to every row
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from triple_gate import gate, MIN_HEALTH
out, gated = gate(out)
out.sort(key=lambda r: (-r['funds'], -r['insiderUsd']))
passed = len(out)
# Publish a multiple of 12 (fills 4, 3, 2 or 1 columns with no empty cells): keep the strongest.
if len(out) >= 12:
    # Trim from the weakest US names first, so the few international ones aren't the ones cut.
    drop = len(out) - len(out) // 12 * 12
    for r in sorted([r for r in out if r['market'] == 'US'], key=lambda r: (r['funds'], r['insiderUsd']))[:drop]:
        out.remove(r)
res = {
    'asOf': today.isoformat(),
    'rules': {'windowDays': WINDOW, 'minInsiderUsd': MIN_USD, 'minInsiderUsdIntl': MIN_USD_INTL, 'maxFromLowPct': MAX_FROM_LOW, 'minFunds': MIN_FUNDS, 'minBuybackPctMcap': MIN_BB_PCT, 'minJpyBuyback': MIN_JPY_BUYBACK, 'buybackGate': f'buyback-only needs Health {MIN_HEALTH}+ or 12m FCF >= 50% of the buyback'},
    'gated': gated,
    'total': len(out), 'passed': passed, 'show': SHOW, 'stocks': out,
    'intlLedger': sorted(ledger.values(), key=lambda r: r['date']),
    # only symbols some tracked fund holds (keeps the file small); a fund adding a name later re-enters it via new buys
    # state of eu_registers.py (cursors + stakes), so next week's run only fetches new notices
    'euRegisters': {k: _eur.get(k) for k in ('asOf', 'sources', 'stakes', 'allStakes', 'state')} if _eur else (prev.get('euRegisters') or {}),
    'usLedger': sorted(us_ledger, key=lambda r: r['d']),   # all names: the Screener's Double Signal uses them too
}
json.dump(res, open('triple-signal.json', 'w'), indent=1)

# ── signals.json for the Screener (Triple / Double Signal with adjustable thresholds) ───────────────
# Every stock with a signal-1 reading, Smart Money or not; the Screener applies the user's sliders.
# Row: [insiderUsd60d, insiders, lastBuy, buybackPctLastQuarter, buybackUsd, quarterEnd, jpBuybackUsd60d, funds,
#       market, (name, price, yearLow, yearHigh, marketCapUsd, country) only for names the screener lacks]
bulk = (J('bb-bulk.json', {}) or {}).get('rows', {})
bbq_map = {b['symbol']: b for b in bbq}
sig = {}
def _mcu(t):
    if (uni.get(t) or {}).get('marketCap'):
        return uni[t]['marketCap']
    q_ = qt.get(t) or {}
    if q_.get('marketCap'):
        return q_['marketCap'] * FX_USD.get('USD' if '.' not in t else LIST_CCY.get('.' + t.split('.')[-1], 'EUR'), 1)
    return None
for t, r in bulk.items():
    date, per, ccy, rep, iss = r
    if date < bb_cut or t.endswith('.T'):
        continue
    net = (rep - iss) * FX_USD.get(ccy, 0)
    mc = _mcu(t)
    if net <= 0 or not mc:
        continue
    pc = 100 * net / mc
    if 1 <= pc <= 60:
        sig.setdefault(t, {})['bb'] = (round(pc, 2), round(net), date)
for t, b in bbq_map.items():   # quarterly route (per symbol) wins over bulk when both exist
    if t.endswith('.T') or (b.get('date') or '') < bb_cut or not b.get('netUsd'):
        continue
    mc = _mcu(t)
    if mc and b['netUsd'] > 0:
        pc = 100 * b['netUsd'] / mc / max(1.0, (b.get('months') or 3) / 3)
        if 1 <= pc <= 60:
            sig.setdefault(t, {})['bb'] = (round(pc, 2), round(b['netUsd']), b['date'])
for t, a in agg.items():
    if a.get('buyback'):
        sig.setdefault(t, {})['jp'] = round(a['usd'])
    elif a['usd'] >= 100_000:
        sig.setdefault(t, {})['ins'] = (round(a['usd']), len(a['people']), a['last'])
rows = {}
profiles = J('profiles.json', {}) or {}
needs_prof = []
for t, v in sig.items():
    ins = v.get('ins') or (0, 0, '')
    bb_ = v.get('bb') or (0, 0, '')
    hs = holders.get(SM_ALIAS.get(t, t), [])
    row = [ins[0], ins[1], ins[2], bb_[0], bb_[1], bb_[2], v.get('jp', 0), len(hs),
           'JP' if t.endswith('.T') else ('INTL' if '.' in t else 'US')]
    if t in uni and not ((uni[t].get('sector')) and uni[t].get('marketCap') and uni[t].get('name')):
        continue   # the screener's own row is incomplete: it would show empty cells
    if t not in uni:
        # Names the screener lacks (e.g. Euronext Paris) are only shown with a complete profile:
        # never an empty row (sector / market cap missing). profiles.json = backend /api/profiles.
        q_ = qt.get(t) or {}
        pf = profiles.get(t) or {}
        if not q_.get('price'):
            continue
        needs_prof.append(t)
        mc_ = pf.get('marketCapUsd') or _mcu(t)
        if pf.get('isFund') or pf.get('isEtf') or not (q_.get('yearLow') and mc_ and pf.get('sector')):
            continue
        row += [pf.get('name') or q_.get('name') or t, q_.get('price'), q_.get('yearLow'), q_.get('yearHigh'), round(mc_),
                pf.get('country'), pf.get('sector'), pf.get('pe'), pf.get('exchange')]
    rows[t] = row
json.dump({'asOf': today.isoformat(), 'rules': res['rules'],
           'cols': ['insUsd', 'insiders', 'lastBuy', 'bbPct', 'bbUsd', 'bbEnd', 'jpBbUsd', 'funds', 'market', 'name', 'price', 'yearLow', 'yearHigh', 'mcapUsd', 'country', 'sector', 'pe', 'exchange'],
           'count': len(rows), 'rows': rows}, open('signals.json', 'w'), separators=(',', ':'))
print('signals.json:', len(rows), 'stocks with a signal-1 reading')
# names to look up with backend /api/profiles (→ profiles.json), then run again
_n = json.load(open('needs.json')); _n['profiles'] = sorted(t for t in set(needs_prof) if t not in profiles); json.dump(_n, open('needs.json', 'w'))
print(len(out), 'stocks', '| major-holdings stakes added:', mh_added, 'unmapped:', len(mh_unmapped))
for r in out:
    print(f"{r['signal'][:3]} {r['ticker']:8} {r['insiderUsd']/1e6:7.1f}M {r['insiders']}p +{r['fromLow']:4.1f}% {r['funds']:2}f  {r['topBuyer']['name']} ({r['topBuyer']['role']})  {r['lastBuy']}")
