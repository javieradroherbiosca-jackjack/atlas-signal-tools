"""Category size check (Javier, 10 Nov 2026): every Today's Picks category must show at least 12 companies and,
shows up to 24 (rules widen only while fewer than 12 pass).

Opens https://atlasglobalterminal.com in a headless browser, waits for the lists to fill and counts the cards of each
category. Exit code 0 = all categories >= 12; 1 = some category below 12 (ERROR lines); 2 = could not run.
Usage: python3 count_check.py
"""
import asyncio, json, sys
GRIDS = {'Top conviction': '#opportunitiesGrid', 'Most owned': '#mostOwnedGrid', 'New conviction': '#newConvGrid', 'Triple Signal': '#tripleGrid', 'Undervalued': '#jewelsGrid',
         'Small caps': '#lotteryGrid', 'International': '#internationalGrid', 'Near lows': '#lowsGrid',
         'Quality': '#qualityGridSm', 'Health Signal': ['#healthSmGrid', '#healthLgGrid'], 'Cash & Cheap': '#rareGrid'}
MIN, TARGET = 12, 24


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print('ERROR playwright missing (pip install playwright --break-system-packages)'); return 2
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1400, 'height': 1000})
        await pg.goto('https://atlasglobalterminal.com/#health', timeout=180000)
        counts = {}
        for _ in range(12):                      # up to ~3 minutes for the lists to fill
            await pg.wait_for_timeout(15000)
            counts = await pg.evaluate("""G => { const n = s => { const g = document.querySelector(s); return g ? g.querySelectorAll(':scope > .card').length : 0; };
                const o = {}; for (const [k, v] of Object.entries(G)) o[k] = Array.isArray(v) ? v.reduce((t, s) => t + n(s), 0) : n(v); return o; }""", GRIDS)
            if all(v >= MIN for v in counts.values()):
                break
        await b.close()
    bad = {k: v for k, v in counts.items() if v < MIN}
    capped = {k: min(v, TARGET) for k, v in counts.items()}
    avg = sum(capped.values()) / len(capped)
    for k, v in counts.items():
        print(('ERROR ' if v < MIN else 'ok    ') + f'{k}: {v}')
    print(f'average (each capped at {TARGET}): {avg:.1f}')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
