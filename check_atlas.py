"""Atlas data-quality checks (added 13 Oct 2026, after an empty Bolloré row reached the Screener).

Usage:
  python3 check_atlas.py local    # before publishing: checks triple-signal.json and signals.json in this folder
  python3 check_atlas.py live     # the published files + backend + sites (daily control)
Optional: quotes.json ({ticker: {price, yearLow, yearHigh}}) from FMP → prices are cross-checked against it.

Prints one line per problem (ERROR = must not be published / must be fixed; WARN = review) and writes
check-report.json. Exit code 1 if there is any ERROR.
"""
import json, os, sys, datetime as dt, urllib.request

MODE = sys.argv[1] if len(sys.argv) > 1 else 'local'
TODAY = dt.date.today()
BACKEND = 'https://atlas-v2-jcsn.onrender.com'
issues = []


def err(where, msg):
    issues.append({'level': 'ERROR', 'where': where, 'msg': msg})


def warn(where, msg):
    issues.append({'level': 'WARN', 'where': where, 'msg': msg})


def get(url, timeout=90):
    req = urllib.request.Request(url, headers={'User-Agent': 'AtlasQualityCheck/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def load(name, url):
    if MODE == 'local':
        return json.load(open(name)) if os.path.exists(name) else None
    try:
        return json.loads(get(url)[0])
    except Exception as e:
        err(name, f'could not load {url}: {e}')
        return None


def days_ago(d):
    try:
        return (TODAY - dt.date.fromisoformat(str(d)[:10])).days
    except Exception:
        return None


SUFFIX_CUR = {'.PA': '€', '.MI': '€', '.AS': '€', '.DE': '€', '.HE': '€', '.BR': '€', '.MC': '€', '.LS': '€', '.VI': '€', '.L': 'GBp ',
              '.SW': 'CHF ', '.ST': 'SEK ', '.CO': 'DKK ', '.OL': 'NOK ', '.TO': 'C$', '.T': '¥', '.HK': 'HK$'}
quotes = json.load(open('quotes.json')) if os.path.exists('quotes.json') else {}

# ── Triple Signal (§04 of the Terminal) ─────────────────────────────────────────
ts = load('triple-signal.json', 'https://atlasglobalterminal.com/triple-signal.json?qc=1')
if ts:
    W = 'triple-signal'
    rules = ts.get('rules', {})
    maxlow = rules.get('maxFromLowPct', 25)
    age = days_ago(ts.get('asOf'))
    if age is None or age > 8:
        err(W, f"asOf {ts.get('asOf')} is {age} days old (rebuilt every Monday)")
    st = ts.get('stocks', [])
    if not (5 <= len(st) <= 120) or len(st) % 12 and len(st) >= 12:
        err(W, f'{len(st)} stocks (expected 5–120, a multiple of 12)')
    seen_t, seen_n = set(), {}
    for r in st:
        t = r.get('ticker', '?'); w = f'{W}:{t}'
        if t in seen_t: err(w, 'duplicate ticker')
        seen_t.add(t)
        nm = (r.get('name') or '').strip()
        if not nm or nm.upper() == t.upper(): err(w, f'missing company name ({nm!r})')
        key = nm.lower().split(' class ')[0]
        if key in seen_n: warn(w, f'same company as {seen_n[key]} (share classes?)')
        seen_n[key] = t
        p, lo, hi = r.get('price'), r.get('yearLow'), r.get('yearHigh')
        if not (p and lo and hi and p > 0 and lo > 0): err(w, f'price/52w data missing (price {p}, low {lo}, high {hi})'); continue
        if not (lo * 0.98 <= p <= hi * 1.02): err(w, f'price {p} outside its 52w range {lo}–{hi}')
        fl = (p / lo - 1) * 100
        if abs(fl - (r.get('fromLow') or 0)) > 0.6: err(w, f"fromLow {r.get('fromLow')} ≠ computed {fl:.1f}")
        if fl > maxlow + 0.05: err(w, f'{fl:.1f}% above the low (rule: ≤ {maxlow}%)')
        if (r.get('funds') or 0) < 1 or not r.get('holders'): err(w, 'no Smart Money holder')
        for h in r.get('holders', []):
            if not (h.get('manager') or h.get('fundName')): err(w, f"holder without a name ({h.get('fundId')})")
        suf = '.' + t.split('.')[-1] if '.' in t else ''
        exp_mkt = 'JP' if suf == '.T' else ('INTL' if suf else 'US')
        if r.get('market') != exp_mkt: err(w, f"market {r.get('market')} but ticker says {exp_mkt}")
        if SUFFIX_CUR.get(suf, '$') != r.get('cur'): err(w, f"currency symbol {r.get('cur')!r} doesn't match {suf or 'US'}")
        sig = r.get('signal')
        tb = r.get('topBuyer') or {}
        if sig in ('insider', 'both'):
            mn = 1_000_000 if exp_mkt == 'US' else 250_000
            if (r.get('insiderUsd') or 0) < mn: err(w, f"insider buying ${r.get('insiderUsd'):,} below the ${mn:,} rule")
            if (r.get('insiderUsd') or 0) > 500e6: err(w, f"insider buying ${r.get('insiderUsd'):,} implausible")
            if not tb.get('name'): err(w, 'insider without a name')
            ab = r.get('avgBuyPrice')
            if ab and not (p / 3 <= ab <= p * 3): err(w, f'average buy price {ab} vs price {p}: currency/scale error')
            a = days_ago(r.get('lastBuy'))
            if a is None or a > rules.get('windowDays', 60) + 3: err(w, f"last insider buy {r.get('lastBuy')} outside the 60-day window")
        if sig == 'buyback' or (sig == 'both'):
            bb = r.get('buyback')
            if exp_mkt == 'JP' and sig == 'buyback':
                if (r.get('insiderUsd') or 0) < 0.9e6: err(w, 'Japan buyback below ¥150M')
                a = days_ago(r.get('lastBuy'))
                if a is None or a > 75: err(w, f"last Japan buyback report {r.get('lastBuy')} too old")
            elif bb:
                pc = bb.get('pctMcap') or 0
                if not (rules.get('minBuybackPctMcap', 4) - 0.05 <= pc <= 60): err(w, f'buyback {pc}% of market value outside 4–60%')
                a = days_ago(bb.get('periodEnd'))
                if a is None or a > 155: err(w, f"buyback quarter ended {bb.get('periodEnd')} (> 150 days)")
            elif sig == 'buyback':
                err(w, 'buyback signal without buyback data')
        an = r.get('analysts') or {}
        if an.get('target') and not (p / 5 <= an['target'] <= p * 5): err(w, f"analyst target {an['target']} vs price {p}: wrong currency or data")
        q = quotes.get(t) or {}
        if q.get('price') and abs(q['price'] / p - 1) > 0.15: warn(w, f"price {p} differs {100*(q['price']/p-1):+.0f}% from today's quote {q['price']}")
    if not ts.get('usLedger'): err(W, 'usLedger empty (next week would lose the insider history)')
    if not ts.get('intlLedger'): err(W, 'intlLedger empty')
    if not (ts.get('euRegisters') or {}).get('state'): warn(W, 'euRegisters.state missing (next week re-reads all European notices)')

# ── Screener signals.json ──────────────────────────────────────────────────────
sj = load('signals.json', 'https://screener.atlasglobalterminal.com/signals.json?qc=1')
if sj:
    W = 'signals'
    age = days_ago(sj.get('asOf'))
    if age is None or age > 8: err(W, f"asOf {sj.get('asOf')} is {age} days old")
    rows = sj.get('rows', {})
    uni_ = json.load(open('universe.json')) if os.path.exists('universe.json') else {}
    if not (300 <= len(rows) <= 5000): err(W, f'{len(rows)} rows (expected 300–5000)')
    if uni_:
        if len(uni_) < 20000: err('universe', f'only {len(uni_)} stocks in the screener universe (pages start at page=0)')
        nolow = sum(1 for v in uni_.values() if not v.get('yearLow'))
        if nolow > 0.2 * len(uni_): err('universe', f'{nolow} of {len(uni_)} stocks have no 52-week low: the Screener signals cannot work (quotes refresh / enrich pending?)')
    for t, r in rows.items():
        w = f'{W}:{t}'
        if len(r) < 9: err(w, f'short row {r}'); continue
        ins, n, last, bbp, bbu, bbe, jp, funds, mkt = r[:9]
        suf = '.' + t.split('.')[-1] if '.' in t else ''
        exp = 'JP' if suf == '.T' else ('INTL' if suf else 'US')
        if mkt != exp: err(w, f'market {mkt} but ticker says {exp}')
        if bbp and not (0 < bbp <= 60): err(w, f'buyback {bbp}% implausible')
        if ins and ins > 500e6: err(w, f'insider buying ${ins:,} implausible')
        if not (ins or bbp or jp): err(w, 'row without any signal')
        if uni_ and t in uni_ and not (uni_[t].get('sector') and uni_[t].get('marketCap') and uni_[t].get('name')):
            err(w, 'screener row is incomplete (sector / market cap / name missing) — it would show empty cells')
        if len(r) > 9:   # injected: the screener universe lacks it, so the row must be complete
            name, price, lo, hi, mc = r[9:14]
            sector = r[15] if len(r) > 15 else None
            missing = [k for k, v in (('name', name), ('price', price), ('52w low', lo), ('52w high', hi), ('market cap', mc), ('sector', sector)) if not v]
            if missing or (name or '').upper() == t: err(w, f'incomplete row would show empty cells in the Screener: missing {", ".join(missing) or "name"}')
            elif not (lo * 0.98 <= price <= hi * 1.02): err(w, f'price {price} outside 52w {lo}–{hi}')

# ── Live: sites, backend, feeds ───────────────────────────────────────────────
if MODE == 'live':
    for url, needle in (('https://atlasglobalterminal.com/', 'tripleGrid'), ('https://screener.atlasglobalterminal.com/', 'sigSection'),
                        ('https://academy.atlasglobalterminal.com/', 'Academy')):
        try:
            body, h = get(url)
            hl = {k.lower(): v for k, v in h.items()}
            if 'content-security-policy' not in hl: err(url, 'security headers missing (Content-Security-Policy)')
            if needle.encode() not in body: err(url, f'page does not contain "{needle}" (wrong deploy?)')
        except Exception as e:
            err(url, f'not reachable: {e}')
    try:
        hlth = json.loads(get(BACKEND + '/health')[0])
        if not hlth.get('ok'): err('backend', f'/health not ok: {hlth}')
        if (hlth.get('fmp') or {}).get('status') != 'ok': err('backend', 'FMP not ok')
    except Exception as e:
        err('backend', f'/health failed: {e}')
    try:
        qp = json.loads(get(BACKEND + '/api/screener/quotes-progress')[0])
        if qp.get('status') != 'complete' or days_ago(qp.get('startTime')) not in (0, 1): warn('backend', f"screener quotes refresh: {qp.get('status')} {qp.get('startTime')}")
    except Exception as e:
        warn('backend', f'quotes-progress failed: {e}')
    try:
        ap = json.loads(get(BACKEND + '/api/autopilot/status')[0])
        for k in ('smRefreshError', 'universeError', 'postRebuildError'):
            if ap.get(k): err('autopilot', f'{k}: {ap[k]}')
    except Exception as e:
        err('autopilot', f'/api/autopilot/status failed: {e}')
    for ep, key, mn in (('jewels', 'jewels', 6), ('near-lows', 'nearLows', 6), ('lottery', 'lottery', 6), ('hot', 'hot', 6), ('radar', 'radar', 6)):
        try:
            d = json.loads(get(f'{BACKEND}/api/smartmoney/{ep}', 150)[0])
            if len(d.get(key) or []) < mn: err(f'terminal section {ep}', f'only {len(d.get(key) or [])} stocks (the section looks empty on the site)')
        except Exception as e:
            err(f'terminal section {ep}', f'failed: {e}')
    try:
        mh = json.loads(get(BACKEND + '/api/smartmoney/major-holdings', 120)[0])
        if mh.get('count', 0) < 100: err('major-holdings', f"only {mh.get('count')} European stakes")
        for k, v in (mh.get('sources') or {}).items():
            if not v.get('ok') and k != 'de': warn('major-holdings', f'source {k} failed: {v.get("error")}')
    except Exception as e:
        warn('major-holdings', str(e))
    try:
        cf = json.loads(get(BACKEND + '/api/smartmoney/cross-fund?minHolders=1&limit=200', 120)[0])
        hs = cf.get('holdings') or cf.get('stocks') or []
        if len(hs) < 100: err('cross-fund', f'only {len(hs)} holdings')
        for h in hs[:200]:
            if not h.get('ticker') or not h.get('holders'): err('cross-fund', f'holding without ticker/holders: {str(h)[:80]}')
    except Exception as e:
        err('cross-fund', str(e))

errors = [i for i in issues if i['level'] == 'ERROR']
warns = [i for i in issues if i['level'] == 'WARN']
for i in issues:
    print(f"{i['level']:5} {i['where']}: {i['msg']}")
print(f'\n{len(errors)} errors, {len(warns)} warnings ({MODE})')
json.dump({'date': TODAY.isoformat(), 'mode': MODE, 'errors': len(errors), 'warnings': len(warns), 'issues': issues},
          open('check-report.json', 'w'), indent=1, ensure_ascii=False)
sys.exit(1 if errors else 0)
