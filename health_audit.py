#!/usr/bin/env python3
"""Atlas data-consistency audit (24 Oct 2026).

Checks what Atlas computes on top of the raw data, so a wrong score or a stale figure is caught before
a user sees it:
  A. Atlas Health: no company rated Good or better may carry a red flag (same rules as the backend).
  B. The company page (/api/health/T) and the Screener (universe rows) must give the same score.
  C. Impossible values in scored rows (score outside 0-100, price or market value <= 0).
  D. Analyst upside must match target price / price.
  E. "Who owns it": the owners endpoint must return the same company (catches name/ADR mix-ups).
  G. Second source: for US companies in the sample, sales growth (last fiscal year) and net margin (last
     twelve months) are recomputed from the SEC's official XBRL filings and compared with Atlas.
  F. Writes crosscheck.json: our figures for a sample of stocks (today's picks, Insider Picks and a random
     set), for the scheduled task to compare against a second data source.

Usage: python3 health_audit.py [--sample 40] [--out audit_health.json]
Exit code: 0 = clean, 1 = errors found, 2 = could not run.
"""
import json, random, re, sys, urllib.request, urllib.parse, time

BK = 'https://atlas-v2-jcsn.onrender.com'
FIN = re.compile(r'bank|insurance|capital markets|asset management|reit', re.I)
SOFT_IND = re.compile(r'engineering & construction|infrastructure operations', re.I)


def get(path, tries=3, timeout=120):
    for i in range(tries):
        try:
            with urllib.request.urlopen(BK + path, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f'{path}: {err}')


def num(m, k):
    v = m.get(k)
    return v if isinstance(v, (int, float)) and v == v else None


UA = {'User-Agent': 'Atlas Global Terminal data audit javier.adroher@atlasglobalterminal.com'}
REV_TAGS = ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet', 'RevenueFromContractWithCustomerIncludingAssessedTax']
NI_TAGS = ['NetIncomeLoss', 'ProfitLoss']


def sec_json(url):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def _days(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def sec_fy(f, tags):
    cands = []
    for t in tags:
        u = f['facts'].get('us-gaap', {}).get(t, {}).get('units', {}).get('USD')
        if not u:
            continue
        fy = {x['frame']: x['val'] for x in u if x.get('form') in ('10-K', '10-K/A') and x.get('fp') == 'FY' and x.get('frame') and len(x['frame']) == 6}
        if len(fy) >= 2:
            cands.append(dict(sorted(fy.items())))
    # 25 Oct 2026: companies switch revenue tags over the years; use the tag with the most recent year, and only if recent
    if not cands:
        return {}
    best = max(cands, key=lambda d: list(d)[-1])
    return best if int(list(best)[-1][2:]) >= int(time.strftime('%Y')) - 2 else {}


def sec_quarters(f, tags):
    best = {}
    for t in tags:
        u = f['facts'].get('us-gaap', {}).get(t, {}).get('units', {}).get('USD')
        if not u:
            continue
        q = {x['end']: x['val'] for x in u if x.get('start') and x.get('end') and 80 <= _days(x['start'], x['end']) <= 100}
        for x in u:   # fourth quarter = full year - nine months
            if x.get('start') and 350 <= _days(x['start'], x['end']) <= 380 and x['end'] not in q:
                nine = [y for y in u if y.get('start') == x['start'] and y.get('end') and 260 <= _days(y['start'], y['end']) <= 280]
                if nine:
                    q[x['end']] = x['val'] - nine[-1]['val']
        if len(q) > len(best):
            best = q
    return dict(sorted(best.items()))


def sec_ttm(s):
    ks = list(s)[-4:]
    if len(ks) < 4 or _days(ks[0], ks[-1]) > 300:
        return None, None
    return sum(s[k] for k in ks), ks[-1]


def red_flags(m):
    """Mirror of healthScore() red flags in server.js."""
    fin = bool(FIN.search(m.get('industry') or '')) and not re.search('credit services', m.get('industry') or '', re.I)
    soft = m.get('sector') == 'Utilities' or bool(SOFT_IND.search(m.get('industry') or ''))
    a, b, p, de0, sl, rg = (num(m, k) for k in ('altman', 'beneish', 'piotroski', 'debtEquity', 'sloanRatio', 'revenueGrowth'))
    ev, fy = num(m, 'evEbitda'), num(m, 'fcfYield')
    # 25 Oct 2026: debtEquity is net debt / EBITDA; with negative EBITDA the sign flips (see healthDerive in server.js)
    if de0 is not None and ev is not None and ev < 0:
        de = 99 if (de0 < 0 and fy is not None and fy < 0) else None
    else:
        de = None if de0 is None else max(0.0, de0)
    f = []
    if not soft and not fin and a is not None and a < 1.81 and ((de is not None and de > 2) or (p is not None and p <= 3) or a < 1.1):
        f.append('distress')
    if not fin and b is not None and b > -1.78 and ((sl is not None and sl > 0.1) or (rg is not None and rg < 0.3)):
        f.append('accounts')
    if p is not None and p <= 2:
        f.append('piotroski')
    if not soft and not fin and de is not None and de > 4:
        f.append('leverage')
    return f


def main():
    args = sys.argv[1:]
    n_sample = int(args[args.index('--sample') + 1]) if '--sample' in args else 40
    out_path = args[args.index('--out') + 1] if '--out' in args else 'audit_health.json'
    errors, warns = [], []
    try:
        uni = get('/api/screener/universe', timeout=300)
    except Exception as e:  # noqa: BLE001
        print('ERROR could not load the universe:', e)
        return 2
    rows = [m for m in uni.get('results', []) if m.get('ticker')]
    by_t = {m['ticker'].upper(): m for m in rows}
    scored = [m for m in rows if m.get('health') is not None]
    if len(scored) < 5000:
        print(f'ERROR only {len(scored)} rows carry Atlas Health (expected 20,000+)')
        return 2

    # A + C
    for m in scored:
        h = m['health']
        if not (0 <= h <= 100):
            errors.append({'t': m['ticker'], 'check': 'C', 'msg': f'health {h} outside 0-100'})
        for k in ('hValue', 'hProfit', 'hGrowth', 'hStrength', 'hMomentum'):
            v = m.get(k)
            if v is not None and not (0 <= v <= 100):
                errors.append({'t': m['ticker'], 'check': 'C', 'msg': f'{k} {v} outside 0-100'})
        if not (num(m, 'price') or 0) > 0 or not (num(m, 'marketCap') or 0) > 0:
            warns.append({'t': m['ticker'], 'check': 'C', 'msg': 'scored without a price or market value'})
        if h >= 55:
            f = red_flags(m)
            if f:
                errors.append({'t': m['ticker'], 'check': 'A', 'msg': f'health {h} (Good or better) with red flags {f}'})

    # sample: today's picks + Insider Picks names + random large caps
    sample = set()
    try:
        days = get('/api/picks/recent?days=2').get('days') or []
        for d in days[:1]:
            sample |= {p.get('t', '').upper() for p in (d.get('picks') or {}).values() if p.get('t')}
    except Exception as e:  # noqa: BLE001
        warns.append({'t': '-', 'check': 'F', 'msg': f'picks not readable: {e}'})
    try:
        sig = json.loads(urllib.request.urlopen('https://screener.atlasglobalterminal.com/signals.json', timeout=60).read().decode())
        cand = []
        for t, r in (sig.get('rows') or {}).items():
            m = by_t.get(t.upper())
            if not m or (m.get('health') or 0) < 55 or not m.get('price') or not m.get('yearLow'):
                continue
            if (m['price'] / m['yearLow'] - 1) * 100 <= 20:
                cand.append((m['health'], t.upper()))
        sample |= {t for _, t in sorted(cand, reverse=True)[:15]}
    except Exception as e:  # noqa: BLE001
        warns.append({'t': '-', 'check': 'F', 'msg': f'signals.json not readable: {e}'})
    big = [m['ticker'].upper() for m in scored if (m.get('marketCap') or 0) >= 3e8]
    random.seed(time.strftime('%Y%m%d'))
    sample |= set(random.sample(big, min(n_sample, len(big))))
    sample = sorted(t for t in sample if t in by_t)

    cross = []
    for t in sample:
        m = by_t[t]
        # B
        try:
            h = get('/api/health/' + urllib.parse.quote(t))
            if h.get('score') != m.get('health'):
                errors.append({'t': t, 'check': 'B', 'msg': f"page says {h.get('score')}, Screener says {m.get('health')}"})
            if (h.get('score') or 0) >= 55 and h.get('flags'):
                errors.append({'t': t, 'check': 'A', 'msg': f"page shows {h.get('score')} with flags {[f['k'] for f in h['flags']]}"})
        except Exception as e:  # noqa: BLE001
            errors.append({'t': t, 'check': 'B', 'msg': f'/api/health failed: {e}'})
        # D
        try:
            lk = (get('/api/screener/lookup/' + urllib.parse.quote(t)) or {}).get('data') or {}
            tp, px, up = num(lk, 'targetPrice'), num(lk, 'price'), num(lk, 'upside')
            if tp and px and up is not None and abs((tp / px - 1) * 100 - up) > 1.5:
                errors.append({'t': t, 'check': 'D', 'msg': f'upside {up}% but target {tp} / price {px} gives {(tp / px - 1) * 100:.1f}%'})
        except Exception as e:  # noqa: BLE001
            warns.append({'t': t, 'check': 'D', 'msg': f'lookup failed: {e}'})
        # E
        try:
            o = get('/api/smartmoney/owners/' + urllib.parse.quote(t) + '?limit=1')
            if o.get('count') and o.get('name') and m.get('name'):
                w = lambda s: re.sub(r'[^a-z0-9 ]', ' ', s.lower()).split()
                a, b = w(o['name']), w(m['name'])
                if a and b and a[0][:4] != b[0][:4] and o.get('matchedBy') == 'name':
                    errors.append({'t': t, 'check': 'E', 'msg': f"owners returned '{o['name']}' ({o.get('ticker')}) for '{m['name']}'"})
        except Exception as e:  # noqa: BLE001
            warns.append({'t': t, 'check': 'E', 'msg': f'owners failed: {e}'})
        cross.append({'ticker': t, 'name': m.get('name'), 'price': m.get('price'), 'marketCap': m.get('marketCap'),
                      'pe': m.get('pe'), 'revenueGrowth': m.get('revenueGrowth'), 'profitMargin': m.get('profitMargin'),
                      'debtEquity': m.get('debtEquity'), 'health': m.get('health')})

    # G: second source (SEC XBRL) for US companies in the sample
    sec_done = 0
    try:
        tick = sec_json('https://www.sec.gov/files/company_tickers.json')
        cik = {v['ticker'].upper(): v['cik_str'] for v in tick.values()}
    except Exception as e:  # noqa: BLE001
        cik = {}
        warns.append({'t': '-', 'check': 'G', 'msg': f'SEC ticker list not reachable: {e}'})
    for x in cross:
        t = x['ticker']
        if '.' in t or '-' in t or t not in cik:
            continue
        try:
            f = sec_json(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]:010d}.json')
            time.sleep(0.15)
        except Exception as e:  # noqa: BLE001
            warns.append({'t': t, 'check': 'G', 'msg': f'SEC facts not readable: {e}'})
            continue
        sec_done += 1
        rev = sec_fy(f, REV_TAGS)
        ks = list(rev)[-2:]
        if len(ks) == 2 and rev[ks[0]] and x.get('revenueGrowth') is not None:
            g = rev[ks[1]] / rev[ks[0]] - 1
            if abs(g - x['revenueGrowth']) > 0.03:
                errors.append({'t': t, 'check': 'G', 'msg': f"sales growth {x['revenueGrowth'] * 100:.1f}% on Atlas vs {g * 100:.1f}% in SEC filings ({ks[0]}→{ks[1]})"})
        r, re_ = sec_ttm(sec_quarters(f, REV_TAGS))
        n, ne = sec_ttm(sec_quarters(f, NI_TAGS))
        if r and n is not None and re_ == ne and x.get('profitMargin') is not None and _days(re_, time.strftime('%Y-%m-%d')) < 200:
            mg = n / r
            if abs(mg - x['profitMargin']) > 0.05:
                errors.append({'t': t, 'check': 'G', 'msg': f"net margin {x['profitMargin'] * 100:.1f}% on Atlas vs {mg * 100:.1f}% in SEC filings (twelve months to {re_})"})
            elif abs(mg - x['profitMargin']) > 0.02:
                warns.append({'t': t, 'check': 'G', 'msg': f"net margin {x['profitMargin'] * 100:.1f}% vs {mg * 100:.1f}% at the SEC (small gap: one-offs or discontinued operations?)"})
    print(f'second source: {sec_done} US companies checked against SEC filings')

    json.dump(cross, open('crosscheck.json', 'w'), indent=1)
    rep = {'checked_rows': len(scored), 'sample': len(sample), 'errors': errors, 'warnings': warns}
    json.dump(rep, open(out_path, 'w'), indent=1)
    for e in errors:
        print(f"ERROR [{e['check']}] {e['t']}: {e['msg']}")
    for w_ in warns[:30]:
        print(f"warn  [{w_['check']}] {w_['t']}: {w_['msg']}")
    print(f"\n{len(errors)} errors, {len(warns)} warnings · {len(scored)} scored rows, {len(sample)} stocks sampled · crosscheck.json written")
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
