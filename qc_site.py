"""Atlas visual check: opens the live Terminal and Screener in a headless browser, the way a user would,
and flags cards or rows with missing information (empty name, "—" in key columns, NaN/undefined, etc.).
Added 13 Oct 2026. Usage: python3 qc_site.py  → prints problems, writes qc-site.json, exit 1 on errors.
Needs Playwright with Chromium (pip install playwright && playwright install chromium if missing).
"""
import asyncio, json, sys
from playwright.async_api import async_playwright

issues = []
def err(w, m): issues.append({'level': 'ERROR', 'where': w, 'msg': m})
def warn(w, m): issues.append({'level': 'WARN', 'where': w, 'msg': m})

BAD = ('undefined', 'NaN', 'null', '[object Object]', 'Infinity')

async def terminal(b):
    pg = await b.new_page(viewport={'width': 1500, 'height': 1000})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    await pg.goto('https://atlasglobalterminal.com/', wait_until='domcontentloaded', timeout=90000)
    try:
        await pg.wait_for_selector('#tripleGrid .card-triple', timeout=180000)
    except Exception:
        err('terminal', 'Triple Signal cards did not load in 3 minutes')
    await pg.wait_for_timeout(15000)   # let the other sections load from the backend
    if await pg.query_selector('#tsMore'):
        await pg.click('#tsMore'); await pg.wait_for_timeout(800)
    cards = await pg.evaluate("""() => [...document.querySelectorAll('#tripleGrid .card-triple')].map(c => ({
        t: (c.querySelector('.card-ticker')||{}).textContent || '', n: (c.querySelector('.card-name')||{}).textContent || '',
        who: (c.querySelector('.ts-who')||{}).textContent || '', txt: c.textContent,
        rows: [...c.querySelectorAll('.ts-sig')].map(r => r.textContent.replace(/\\s+/g,' ').trim()),
        holders: c.querySelectorAll('.card-holder-chip').length }))""")
    if len(cards) < 5: err('terminal §04', f'only {len(cards)} Triple Signal cards on screen')
    for c in cards:
        w = f"terminal §04 {c['t'] or '?'}"
        if not c['t'].strip(): err(w, 'card without ticker')
        if not c['n'].strip() or c['n'].strip().upper() == c['t'].strip().upper(): err(w, f"company name missing ({c['n']!r})")
        if not c['who'].strip(): err(w, 'missing "who bought" line')
        if c['holders'] < 1: err(w, 'no Smart Money chips')
        for bad in BAD:
            if bad in c['txt']: err(w, f'shows "{bad}"')
        for r in c['rows'][:3]:
            if r.endswith('—') or r.endswith('$0') or ' 0 funds' in r: err(w, f'empty value: {r}')
    # Other card grids on the home page (Top Conviction etc.)
    other = await pg.evaluate("""() => [...document.querySelectorAll('.card')].filter(c => !c.classList.contains('card-triple') && c.querySelector('.card-ticker'))
        .map(c => ({t: c.querySelector('.card-ticker').textContent.trim(), n: ((c.querySelector('.card-name')||{}).textContent||'').trim(), txt: c.textContent}))""")
    for c in other:
        w = f"terminal card {c['t'] or '?'}"
        if not c['t']: err(w, 'card without ticker')
        if not c['n']: warn(w, 'card without company name')
        for bad in BAD:
            if bad in c['txt']: err(w, f'shows "{bad}"')
    for e in errs[:5]:
        warn('terminal', f'JavaScript error: {e[:160]}')
    await pg.close()

async def screener(b):
    pg = await b.new_page(viewport={'width': 1500, 'height': 1000})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    await pg.goto('https://screener.atlasglobalterminal.com/', wait_until='domcontentloaded', timeout=90000)
    loaded = False
    for _ in range(180):   # up to 15 minutes, every 5 s (wait_for_function is blocked by the site's CSP)
        await pg.wait_for_timeout(5000)
        try:
            loaded = await pg.evaluate("() => { const el = document.getElementById('loadingEl'); return !!el && el.style.display === 'none' && typeof UNIVERSE !== 'undefined' && UNIVERSE.length > 0; }")
        except Exception:
            loaded = False
        if loaded: break
    if not loaded:
        err('screener', 'universe did not load in 15 minutes'); await pg.close(); return
    n = await pg.evaluate('UNIVERSE.length')
    if n < 20000: err('screener', f'only {n} stocks in the universe')
    for kind in ('triple', 'double'):
        await pg.click(f'.sig-btn[data-sig="{kind}"]'); await pg.wait_for_timeout(2500)
        rows = await pg.evaluate("""() => [...document.querySelectorAll('#resultsTbody tr')].filter(tr => tr.children.length > 10).map(tr => {
            const c = [...tr.children].map(td => td.textContent.replace(/\\s+/g,' ').trim());
            return {t: c[0], name: c[1], sector: c[2], region: c[3], mcap: c[4]}; })""")
        cnt = await pg.evaluate('FILTERED.length')
        if cnt < 5: err(f'screener {kind}', f'only {cnt} results')
        for r in rows:
            w = f"screener {kind} {r['t'].split(' ')[0]}"
            empty = [k for k in ('name', 'sector', 'region', 'mcap') if r[k] in ('', '—')]
            if empty: err(w, f'empty cells: {", ".join(empty)}')
            for bad in BAD:
                if bad in ' '.join(r.values()): err(w, f'shows "{bad}"')
        await pg.click(f'.sig-btn[data-sig="{kind}"]'); await pg.wait_for_timeout(500)   # switch off
    for e in errs[:5]:
        warn('screener', f'JavaScript error: {e[:160]}')
    await pg.close()


import re as _re
async def sanity(b):
    """13 Oct 2026: plausibility checks on every visible number (Javier: no data may cost credibility)."""
    pg = await b.new_page(viewport={'width': 1500, 'height': 1000})
    await pg.goto('https://atlasglobalterminal.com/', wait_until='domcontentloaded', timeout=90000)
    await pg.wait_for_timeout(40000)
    for btn in await pg.query_selector_all('.v2-more, #tsMore'):
        try: await btn.click(); await pg.wait_for_timeout(200)
        except Exception: pass
    for t in await pg.query_selector_all('.v2-tab'):
        try:
            if (await t.inner_text()).strip().lower().startswith('whole'): await t.click(); await pg.wait_for_timeout(1500)
        except Exception: pass
    await pg.wait_for_timeout(6000)
    cards = await pg.evaluate("""() => [...document.querySelectorAll('.card')].filter(c => c.offsetParent).map(c => ({
        t: ((c.querySelector('.card-ticker')||{}).textContent||'').trim(), txt: c.innerText.replace(/\\s+/g,' ')}))""")
    for c in cards:
        w = f"terminal card {c['t'] or '?'}"
        for m in _re.finditer(r'ROIC\s*([-\d.,]+)%', c['txt']):
            v = float(m.group(1).replace(',', ''))
            if v > 100 or v < -100: err(w, f'implausible ROIC {v}%')
        for m in _re.finditer(r'UPSIDE(?: \(IF WORKS\))?\s*\+([\d.,]+)%', c['txt'], _re.I):
            if float(m.group(1).replace(',', '')) > 150: err(w, f'implausible upside +{m.group(1)}%')
        if '.' in c['t'] and _re.search(r'(PRICE|CURRENT PRICE)\s*\$', c['txt']): err(w, 'non-US stock priced in $')
    # academic portfolios: medians must be believable
    await pg.goto('https://atlasglobalterminal.com/?view=academics', wait_until='domcontentloaded', timeout=90000)
    await pg.wait_for_timeout(4000)
    for key in ['value','growth','garp','quality','momentum','deepvalue','dividend','near52']:
        try:
            await pg.evaluate(f"builderSelect('{key}', document.querySelector('[data-philo={key}]'))")
            for _ in range(40):
                st = await pg.evaluate("document.getElementById('builderStatus').textContent")
                if 'names' in st or 'No stocks' in st or '⚠' in st: break
                await pg.wait_for_timeout(2000)
            met = await pg.evaluate("document.getElementById('builderMetrics').innerText")
            m = _re.search(r'MEDIAN ROIC\s*([-\d.]+)%', met, _re.I)
            if m and float(m.group(1)) > 45: err(f'academics {key}', f'median ROIC {m.group(1)}% looks implausible')
            rows = await pg.evaluate("[...document.querySelectorAll('#builderTableBody tr')].map(r=>r.innerText.replace(/\\s+/g,' '))")
            for r in rows:
                if _re.search(r'\$0\.00\b', r): err(f'academics {key}', f'price $0.00: {r[:60]}')
        except Exception as e:
            warn(f'academics {key}', f'check failed: {e}')
    await pg.close()

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for fn in (terminal, sanity, screener):
            try:
                await fn(b)
            except Exception as e:
                err(fn.__name__, f'check crashed: {e}')
        await b.close()

asyncio.run(main())
for i in issues:
    print(f"{i['level']:5} {i['where']}: {i['msg']}")
e = sum(1 for i in issues if i['level'] == 'ERROR')
print(f"\n{e} errors, {len(issues) - e} warnings (site)")
json.dump(issues, open('qc-site.json', 'w'), indent=1, ensure_ascii=False)
sys.exit(1 if e else 0)
