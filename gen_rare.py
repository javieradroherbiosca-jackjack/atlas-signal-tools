"""Rare Opportunity — Atlas pick and section (Javier, 10 Nov 2026).

Healthy, very cheap companies with strong free cash flow, ranked by Momentum. No Smart Money needed: these are ideas
Atlas finds on its own, often before the great investors. Rules (refine as names come out):
  * market cap >= 750 M$; no funds / investment vehicles and no financials (FCF means nothing for banks and insurers)
  * P/E 5-10 (below 5 there is almost always a one-off or a bad figure); FCF yield 6-40%; ROIC >= 10%
  * revenue growth 0-50% (bigger jumps are usually one-offs); Atlas Health >= 55 (Good or better)
  * Quality Scores: Piotroski >= 6, Altman Z >= 1.81, Beneish < -1.78; emerging markets, where accounting transparency
    is weaker, must pass stricter ones: Piotroski >= 7, Altman Z >= 2.6, Beneish < -2.22 (no country is excluded)
  * price at most 50% above its 52-week low
  * one row per company (same name under two tickers counts once: the home-market ticker wins)
  * ranked by the Momentum pillar of Atlas Health; cyclicals (Basic Materials, Energy) count 0.6x so they do not take
    over the list, but they are never excluded
  * one-offs (Javier, 10 Nov 2026: "hay que vigilar que haya un repunte de caja libre por una venta extraordinaria"):
    free cash flow must be positive in at least 2 of the last 3 years; if the latest year is more than 2.5x the 3-year
    median, the FCF yield is recomputed on that median and must still pass; and earnings boosted by non-operating
    gains (pre-tax profit more than 25% above operating profit, and 20+ points above the company's own 3-year average)
    are left out, because the P/E would be flattering a one-off
  * at least 12, ideally 24: if fewer than 24 pass, widen step by step (P/E up to 12, then FCF down to 5%); health and
    the Quality Scores never relax. Each row carries its step ("tier").

Usage: python3 gen_rare.py AAAA-MM-DD  → rare-opportunity.json (publish with publish_signals.py).
Reads the universe from the backend (or universe-full.json if present) and Atlas Health per candidate.
"""
import json, os, sys, time, urllib.parse, urllib.request, concurrent.futures as cf

API = 'https://atlas-v2-jcsn.onrender.com'
TARGET, MIN_N = 24, 12
EXCLUDE = {'APH.JO'}   # Javier, 10 Nov 2026: Alphamin (Mauritius) out
EM = {'CN', 'HK', 'MO', 'BR', 'ZA', 'ID', 'IN', 'MY', 'TH', 'MX', 'TR', 'GR', 'PH', 'VN', 'AR', 'CL', 'CO', 'PE', 'EG', 'NG',
      'PK', 'SA', 'AE', 'QA', 'KW', 'MU', 'KZ', 'RU', 'TW', 'KR', 'PL', 'HU', 'CZ'}
CYCLICAL = {'Basic Materials', 'Energy'}
FUND_WORDS = ('fund', 'trust inc', 'income', ' etf', 'closed-end', 'capital allocation', 'strategic value', 'opportunities fund')
# steps: (tier, max P/E, min FCF yield)
STEPS = [(1, 10.0, 0.06), (2, 12.0, 0.06), (3, 12.0, 0.05)]


def get(path, timeout=180):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, headers={'User-Agent': 'AtlasRare/1.0'}), timeout=timeout) as r:
                return json.load(r)
        except Exception:
            time.sleep(3 + 5 * i)
    return None


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def universe():
    if os.path.exists('universe-full.json'):
        return json.load(open('universe-full.json'))
    first = get('/api/screener/search?limit=500&page=0') or {}
    pages = (int(first.get('total') or 0) + 499) // 500
    rows = list(first.get('results') or [])
    with cf.ThreadPoolExecutor(8) as ex:
        for d in ex.map(lambda p: get(f'/api/screener/search?limit=500&page={p}'), range(1, pages)):
            rows += (d or {}).get('results') or []
    json.dump(rows, open('universe-full.json', 'w'))
    return rows


def quality_ok(r):
    pio, alt, ben = f(r.get('piotroski')), f(r.get('altman')), f(r.get('beneish'))
    if r.get('country') in EM:
        return (pio or 0) >= 7 and (alt or 0) >= 2.6 and ben is not None and ben < -2.22
    return (pio or 0) >= 6 and (alt or 0) >= 1.81 and (ben is None or ben < -1.78)


def company_key(r):
    w = [x for x in ''.join(c if c.isalnum() else ' ' for c in (r.get('name') or '').lower()).split()
         if x not in ('the', 'inc', 'co', 'corp', 'corporation', 'ltd', 'plc', 'sa', 'ag', 'nv', 'limited', 'holdings')]
    return ' '.join(w[:2]) or r['ticker']


def main(day):
    rows = universe()
    print('universe', len(rows))
    # one row per company: prefer the home-market ticker (with an exchange suffix when the company is not American)
    best = {}
    for r in rows:
        k = company_key(r)
        home = ('.' in r['ticker']) == (r.get('country') not in ('US', None))
        if k not in best or (home and not best[k][1]):
            best[k] = (r, home)
    rows = [v[0] for v in best.values()]
    pool = []
    for r in rows:
        t = r['ticker']
        mc, pe, fcf, roic, rg, p, lo, hi = [f(r.get(k)) for k in ('marketCap', 'pe', 'fcfYield', 'roic', 'revenueGrowth', 'price', 'yearLow', 'yearHigh')]
        if t in EXCLUDE or None in (mc, pe, fcf, roic, rg, p, lo, hi) or lo <= 0 or hi <= 0 or mc < 750e6:
            continue
        if r.get('sector') == 'Financial Services' or any(w in (r.get('name') or '').lower() for w in FUND_WORDS):
            continue
        if not (5 <= pe <= max(s[1] for s in STEPS)) or not (min(s[2] for s in STEPS) <= fcf <= 0.40) or roic < 0.10 or not (0 <= rg <= 0.50):
            continue
        if (p / lo - 1) * 100 > 50 or not quality_ok(r):
            continue
        tier = next((tr for tr, mpe, mfcf in STEPS if pe <= mpe and fcf >= mfcf), None)
        if tier:
            pool.append(dict(r, _tier=tier))
    print('pre-health pool', len(pool))

    def health(r):
        d = get('/api/health/' + urllib.parse.quote(r['ticker']), timeout=90) or {}
        r['_hs'], r['_hl'] = d.get('score'), d.get('label')
        r['_mom'] = ((d.get('pillars') or {}).get('momentum') or {}).get('score')
        return r
    with cf.ThreadPoolExecutor(6) as ex:
        pool = list(ex.map(health, pool))
    pool = [r for r in pool if (r.get('_hs') or 0) >= 55]

    # one-offs: an asset sale or another extraordinary item must not make a company look cheap or cash-rich
    def oneoff(r):
        d = get('/api/company/' + urllib.parse.quote(r['ticker']) + '/full', timeout=90) or {}
        ca = [f(x.get('fcf')) for x in (d.get('cashAnnual') or [])[:3]]
        ia = (d.get('incomeAnnual') or [])[:4]
        r['_fcfN'], r['_why'] = f(r.get('fcfYield')), None
        ca = [x for x in ca if x is not None]
        if len(ca) >= 3:
            if sum(1 for x in ca if x > 0) < 2:
                r['_why'] = 'free cash flow negative in 2 of the last 3 years'
            else:
                med = sorted(ca)[1]
                if med > 0 and ca[0] > 2.5 * med:
                    r['_fcfN'] = r['_fcfN'] * med / ca[0]
                    r['_spike'] = round(ca[0] / med, 1)
        sh = []
        for x in ia:
            pt, op = f(x.get('pretax')), f(x.get('op'))
            sh.append((pt - op) / abs(pt) if pt and op is not None else None)
        if sh and sh[0] is not None and sh[0] > 0.25:
            prev = [x for x in sh[1:] if x is not None]
            if not prev or sh[0] - sum(prev) / len(prev) > 0.20:
                r['_why'] = 'profit boosted by non-operating gains ({:.0%} of pre-tax profit)'.format(sh[0])
        return r
    with cf.ThreadPoolExecutor(6) as ex:
        pool = list(ex.map(oneoff, pool))
    for r in pool:
        if r.get('_why'):
            print('one-off:', r['ticker'], r['_why'])
        elif r.get('_spike'):
            print('one-off: {} FCF {}x its 3-year median, yield {:.1%} -> {:.1%}'.format(r['ticker'], r['_spike'], f(r['fcfYield']), r['_fcfN']))
    pool = [r for r in pool if not r.get('_why')]
    for r in pool:   # re-check the step with the normalised FCF yield
        r['_tier'] = next((tr for tr, mpe, mfcf in STEPS if f(r['pe']) <= mpe and (r['_fcfN'] or 0) >= mfcf), None)
    pool = [r for r in pool if r['_tier']]
    out, counts = [], {}
    for tr, *_ in STEPS:
        if len(out) >= TARGET:
            break
        add = [r for r in pool if r['_tier'] == tr]
        counts[tr] = len(add)
        out += add

    def score(r):
        m = r.get('_mom') or 0
        return m * (0.6 if r.get('sector') in CYCLICAL else 1.0)
    out.sort(key=lambda r: (r['_tier'], -score(r), -(f(r.get('fcfYield')) or 0)))
    if len(out) > TARGET and counts.get(1, 0) < TARGET:
        out = out[:TARGET]
    out.sort(key=lambda r: (-score(r), -(f(r.get('fcfYield')) or 0)))
    stocks = []
    for r in out:
        p, lo, hi = f(r['price']), f(r['yearLow']), f(r['yearHigh'])
        stocks.append({
            'ticker': r['ticker'], 'name': r.get('name'), 'country': r.get('country'), 'sector': r.get('sector'),
            'industry': r.get('industry'), 'marketCap': f(r['marketCap']), 'price': p, 'yearLow': lo, 'yearHigh': hi,
            'fromLow': round((p / lo - 1) * 100, 1), 'pe': round(f(r['pe']), 2), 'fcfYield': round(r['_fcfN'], 4),
            'fcfYieldReported': round(f(r['fcfYield']), 4), 'fcfSpike': r.get('_spike'),
            'roic': round(f(r['roic']), 4), 'revenueGrowth': round(f(r['revenueGrowth']), 4),
            'health': r.get('_hs'), 'healthLabel': r.get('_hl'), 'momentum': r.get('_mom'),
            'piotroski': f(r.get('piotroski')), 'altman': f(r.get('altman')), 'beneish': f(r.get('beneish')),
            'cyclical': r.get('sector') in CYCLICAL, 'smallCap': f(r['marketCap']) < 1e9, 'tier': r['_tier'],
        })
    res = {'asOf': day, 'total': len(stocks), 'tierCounts': counts, 'planB': counts.get(1, 0) < TARGET,
           'rules': {'minMarketCap': 750e6, 'pe': [5, 10], 'fcfYieldMin': 0.06, 'roicMin': 0.10, 'revenueGrowth': [0, 0.5],
                     'healthMin': 55, 'maxFromLowPct': 50, 'quality': 'Piotroski>=6, Altman>=1.81, Beneish<-1.78 (EM: 7, 2.6, -2.22)',
                     'rank': 'Momentum pillar; cyclicals x0.6',
                     'oneOffs': 'FCF positive 2 of 3 years; FCF > 2.5x 3-year median is normalised; non-operating gains > 25% of pre-tax profit (20+ pts above own average) excluded', 'ladder': [{'tier': t, 'maxPE': a, 'minFcf': b} for t, a, b in STEPS],
                     'target': TARGET, 'min': MIN_N, 'noFinancials': True, 'noFunds': True},
           'stocks': stocks}
    json.dump(res, open('rare-opportunity.json', 'w'), indent=1)
    print(f"rare-opportunity.json: {len(stocks)} stocks · tiers {counts} · planB {res['planB']}")
    if len(stocks) < MIN_N:
        raise SystemExit(f'only {len(stocks)} stocks (minimum {MIN_N})')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d'))
