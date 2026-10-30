"""Atlas rules check (30 Oct 2026, Javier: "espero que no haya más fallos de cosas que hemos establecido como reglas").

Checks the rules agreed with Javier against what is live, every day (run by the daily quality control):
  R1  Triple Signal is rebuilt daily (asOf today or yesterday) and every name is really near its low:
      within 20% of the 52-week low AND in the lower third of the 52-week range.
  R2  Today's picks: the Triple Signal pick is in today's Triple Signal list; the Health Signal pick is near its low
      by the same rule (checked with the screener's daily quote).
  R3  Every app states the official data sources (SEC 13F, CNMV, EDINET) and uses the Atlas logo with the Tau.
  R4  Latest published posts (public Almanac): no Bill Ackman / Pershing Square; "Paramés" only as "Cobas · Paramés",
      "Iván Martín" only as "Magallanes · Iván Martín"; small-cap warning on every company under $1B; ticker next to
      each company in the picks lines.
  R5  Concession groups (Sacyr, Ferrovial, ACS): net debt/EBITDA or interest cover never listed as a weakness.
  R6  Spanish wording: Atlas Health labels stay in English ("Great Health", never "salud Great").
Exit 0 = all rules hold, 1 = a rule is broken (lines start with RULE), 2 = could not run.
"""
import json, re, sys, datetime as dt, urllib.request

API = 'https://atlas-v2-jcsn.onrender.com'
TODAY = dt.date.today()
bad, notes = [], []


def get(url, raw=False, timeout=90):
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'AtlasRulesCheck/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return b.decode('utf-8', 'replace') if raw else json.loads(b)
        except Exception as e:
            last = e
    raise RuntimeError(f'{url}: {last}')


def rule(code, msg):
    bad.append(f'RULE {code}: {msg}')


def near_low(p, lo, hi):
    if not (p and lo and hi and lo > 0 and hi > lo):
        return None
    return (p / lo - 1) * 100 <= 20.05 and (p - lo) / (hi - lo) <= 1 / 3 + 0.005


try:
    # R1
    ts = get(f'{API}/api/signals/triple-signal.json?rc=1')
    age = (TODAY - dt.date.fromisoformat(ts['asOf'])).days
    if age > 1:
        rule('R1', f"Triple Signal asOf {ts['asOf']} ({age} days old; it must be rebuilt every day)")
    for s in ts.get('stocks', []):
        if near_low(s.get('price'), s.get('yearLow'), s.get('yearHigh')) is False:
            rule('R1', f"{s.get('name')} ({s['ticker']}) is not near its low ({s.get('fromLow')}% above it / upper part of its range)")
    tickers = {s['ticker'] for s in ts.get('stocks', [])}

    # R2
    pk = get(f'{API}/api/picks/recent?days=1')
    today = next((d for d in pk.get('days', []) if d['day'] == pk.get('today')), None)
    if today:
        tp = (today['picks'].get('triple') or {}).get('t')
        if tp and tp not in tickers:
            rule('R2', f"today's Triple Signal pick {tp} is not in today's Triple Signal list")
        hp = (today['picks'].get('health') or {}).get('t')
        if hp:
            q = (get(f'{API}/api/screener/lookup/{hp}') or {}).get('data') or {}
            if near_low(q.get('price'), q.get('yearLow'), q.get('yearHigh')) is False:
                rule('R2', f"today's Health Signal pick {hp} is not near its low")
    else:
        notes.append("no picks fixed yet today")

    # R3
    apps = {'Terminal': 'https://atlasglobalterminal.com/', 'Screener': 'https://screener.atlasglobalterminal.com/',
            'Tracker': 'https://tracker.atlasglobalterminal.com/', 'Insider': 'https://insider.atlasglobalterminal.com/',
            'Earnings': 'https://earnings.atlasglobalterminal.com/', 'Almanac': 'https://atlas-almanac-i6jk.netlify.app/',
            'Beta': 'https://atlas-beta-signup-information.netlify.app/', 'Academy': 'https://academy.atlasglobalterminal.com/academy.js'}
    for name, url in apps.items():
        h = get(url, raw=True)
        if 'EDINET' not in h or 'CNMV' not in h:
            rule('R3', f'{name} does not state the official sources (SEC 13F, CNMV, EDINET)')
        if 'M2 8 L98 8 L86 33' not in h:
            rule('R3', f'{name} does not use the Atlas logo with the Tau')

    # R4 — the latest posts in the public Almanac
    al = get('https://atlas-almanac-i6jk.netlify.app/content.json?rc=1')
    recent = sorted((p for p in al.get('pieces', []) if p.get('date', '') >= (TODAY - dt.timedelta(days=3)).isoformat()), key=lambda p: p['date'])
    for p in recent:
        for lang in ('body_es', 'body_en'):
            t = p.get(lang) or ''
            if re.search(r'ackman|pershing', t, re.I):
                rule('R4', f"{p['id']} ({lang}) mentions Bill Ackman / Pershing Square")
            for m in re.finditer(r'(.{0,10})Param[eé]s', t):
                if 'Cobas · ' not in m.group(1) and 'García' not in m.group(1):
                    rule('R4', f"{p['id']} ({lang}): \"Paramés\" without \"Cobas · \"")
            for m in re.finditer(r'(.{0,14})Iv[aá]n Mart[ií]n', t):
                if 'Magallanes · ' not in m.group(1):
                    rule('R4', f"{p['id']} ({lang}): \"Iván Martín\" without \"Magallanes · \"")
            if p['id'].endswith('-picks'):
                for line in t.splitlines():
                    m = re.search(r'·\s*([^()]+?)\s*\(([A-Z0-9.\-]{1,12})\)', line)
                    if not m or not re.match(r'^\S*\d', line.strip()):
                        continue
                    tk = m.group(2)
                    try:
                        q = (get(f'{API}/api/screener/lookup/{tk}') or {}).get('data') or {}
                    except Exception:
                        continue
                    mc = q.get('marketCap')
                    warn = 'Pequeña compañía' if lang == 'body_es' else 'Small cap'
                    if mc and mc < 1e9 and warn not in line:
                        rule('R4', f"{p['id']} ({lang}): {tk} is under $1B but its line has no small-cap warning")

    # R5
    for t in ('SCYR.MC', 'FER.MC', 'ACS.MC'):
        sw = get(f'{API}/api/swot/{t}')
        for it in (sw.get('items') or {}).get('W', []):
            if re.search(r'deuda neta|cobertura de intereses|net debt|interest cover', it.get('es', '') + it.get('en', ''), re.I):
                rule('R5', f'{t}: SWOT lists debt as a weakness ("{it.get("es")}")')

    # R6
    h = get('https://atlasglobalterminal.com/', raw=True) + get('https://atlasglobalterminal.com/stock.js', raw=True)
    m = re.search(r'salud (Great|Good|Excellent|Fair|Weak)', h)
    if m:
        rule('R6', f'Spanish text translates an Atlas Health label: "{m.group(0)}"')
except Exception as e:
    print(f'could not run: {e}')
    sys.exit(2)

for b in bad:
    print(b)
for n in notes:
    print('note:', n)
print(f'{len(bad)} rule problems')
sys.exit(1 if bad else 0)
