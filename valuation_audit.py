#!/usr/bin/env python3
"""Weekly audit of the Valuation tab of Atlas company pages (atlasglobalterminal.com/stock/TICKER).

Opens each ticker in valuation_audit_tickers.json in a headless browser, reads the DCF result, the
other models and the warnings, and reports results that look inconsistent with the share price and are
NOT explained on the page (no warning, not marked as a poor fit or as the no-growth EPV floor).
Usage: python3 valuation_audit.py [--base https://atlasglobalterminal.com] [--out audit.json]
Exit code 0 = no new issues, 1 = new issues (listed in the summary), 2 = the audit could not run.
"""
import asyncio, json, re, sys, argparse
from playwright.async_api import async_playwright

# Reviewed on 23 Oct 2026: inconsistencies that are real and explained (not data errors)
KNOWN = {
    ('NOVO-B.CO', 'P/E VS OWN HISTORY'): 'trades far below its own history after the 2024-26 de-rating',
    ('NOVO-B.CO', 'EV/EBITDA VS OWN HISTORY'): 'same',
    ('NOVO-B.CO', 'EV/SALES VS OWN HISTORY'): 'same',
    ('SEM.LS', 'EV/EBITDA VS OWN HISTORY'): 'holding with minority interests (Navigator)',
    ('SEM.LS', 'EV/SALES VS OWN HISTORY'): 'same',
    ('0001.HK', 'EARNINGS POWER VALUE'): 'conglomerate with associates and minorities',
    ('ENOG.L', 'EV/SALES VS OWN HISTORY'): 'revenue history changed with the Israel gas fields',
}
def n(s):
    m = re.search(r'(-?[\d,]+\.?\d*)', s or '')
    return float(m.group(1).replace(',', '')) if m else None

JS = r"""() => { const q = s => document.querySelector(s), tx = s => { const e = q(s); return e ? e.innerText : null };
  return { name: tx('#nm'), dk: [...document.querySelectorAll('#dcfOut .dk')].map(e => e.innerText.replace(/\n/g, ' ')),
    dwarn: !!q('#dcfOut .dwarn'), models: [...document.querySelectorAll('.vmc')].map(e => ({ t: e.querySelector('.t').innerText.toUpperCase(),
    v: e.querySelector('.v').innerText.replace(/\n/g, ' '), flagged: !!e.querySelector('.fit') || /floor|if it never grows/i.test(e.innerText) && /EARNINGS POWER/i.test(e.querySelector('.t').innerText) })),
    text: (q('#p-dcf') || document.body).innerText.slice(0, 20000),
    peSum: (() => { const r = [...document.querySelectorAll('#keystats tr')].find(x => /P\/E/.test(x.innerText)); return r ? r.innerText : null })(),
    peStats: (() => { const r = [...document.querySelectorAll('#statsBody .kv')].find(x => /^P\/E/.test(x.innerText)); return r ? r.innerText : null })(),
    peSwot: (() => { const r = [...document.querySelectorAll('#swotBody .fig')].find(x => /P\/E/.test(x.innerText)); return r ? r.innerText : null })() } }"""

async def run(base, tickers, out):
    res = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={'width': 1280, 'height': 900})
        qq = asyncio.Queue()
        for t in tickers: qq.put_nowait(t)
        async def worker():
            pg = await ctx.new_page(); errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            while not qq.empty():
                t = qq.get_nowait(); errs.clear()
                try:
                    await pg.goto(f'{base}/stock/{t}#dcf', timeout=90000)
                    try: await pg.wait_for_selector('#dcfOut .dk, #dcfOut .dwarn, #dcfInner .note', timeout=120000)
                    except Exception: pass
                    await pg.wait_for_timeout(1500)
                    try: await pg.wait_for_selector('#swotBody .swot', timeout=30000)
                    except Exception: pass
                    r = await pg.evaluate(JS); r['t'] = t; r['errs'] = list(errs); res.append(r)
                except Exception as e:
                    res.append({'t': t, 'fail': str(e)[:150]})
            await pg.close()
        await asyncio.gather(*[worker() for _ in range(4)])
        await b.close()
    json.dump(res, open(out, 'w'))
    return res

def analyze(res):
    issues, fails = [], []
    for r in res:
        t = r['t']
        if r.get('fail'): fails.append(f"{t}: page failed ({r['fail'][:80]})"); continue
        if r['errs']: issues.append(f"{t}: JavaScript error: {'; '.join(r['errs'])[:150]}")
        if any(x in (r.get('text') or '') for x in ['NaN', 'undefined', 'Infinity']): issues.append(f'{t}: NaN/undefined/Infinity shown in the Valuation tab')
        pes = [x for x in (n((r.get('peSum') or '').split('E')[-1]), n((r.get('peStats') or '').split('\n')[-1]), n(r.get('peSwot'))) if x]
        if len(pes) >= 2 and max(pes) / min(pes) > 1.06: issues.append(f'{t}: P/E differs between sections {pes}')
        dcf = pxs = None
        for x in r['dk']:
            X = x.upper()
            if X.startswith('VALUE PER SHARE'): dcf = n(re.sub(r'^VALUE PER SHARE[^0-9-]*', '', X))
            if X.startswith('SHARE PRICE'): pxs = n(re.sub(r'^SHARE PRICE( IN [A-Z]+)?', '', X))
        if dcf and pxs and not r['dwarn'] and (dcf / pxs > 2.5 or dcf / pxs < 0.25):
            issues.append(f'{t}: DCF {dcf:,.2f} vs price {pxs:,.2f} with no warning')
        for m in r['models']:
            v = n(m['v'])
            if v is None or not pxs or m['flagged'] or (t, m['t']) in KNOWN: continue
            if v / pxs > 3 or v / pxs < 0.2: issues.append(f"{t}: {m['t'].title()} {v:,.2f} vs price {pxs:,.2f} ({v / pxs:.2f}x), not explained")
    return issues, fails

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--base', default='https://atlasglobalterminal.com'); ap.add_argument('--out', default='valuation_audit_out.json')
    ap.add_argument('--tickers', default='valuation_audit_tickers.json')
    ap.add_argument('--batch', type=int, default=None, help='0-based slice number (with --of): audit a large list in parts, one per day')
    ap.add_argument('--of', type=int, default=7); a = ap.parse_args()
    tickers = json.load(open(a.tickers))
    if a.batch is not None: tickers = tickers[a.batch % a.of::a.of]
    try: res = asyncio.run(run(a.base, tickers, a.out))
    except Exception as e: print('AUDIT COULD NOT RUN:', e); sys.exit(2)
    issues, fails = analyze(res)
    ok = len(res) - len(fails)
    print(f'Valuation audit: {ok}/{len(tickers)} pages read, {len(issues)} unexplained issues, {len(fails)} pages failed')
    for x in issues: print(' ISSUE', x)
    for x in fails: print(' FAIL ', x)
    if ok < 0.8 * len(tickers): print('Too many pages failed: the site or the backend may be down'); sys.exit(2)
    sys.exit(1 if issues else 0)
