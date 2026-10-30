"""Quality gate for the Triple Signal list (30 Oct 2026, Javier: the Coursera case).

A company buyback without insider buying only counts when the business can afford it:
  Atlas Health Good (50+)  OR  trailing-12-month free cash flow >= half of the buyback.
A buyback paid with debt or with cash taken over in an acquisition is not the "people who know the
business best think it is cheap" signal. Insider buys (alone or with a buyback) are not affected.
Every row also gets its Atlas Health score ('health'), so the Terminal can prefer healthy insider names
for Today's Picks.

Usage: imported by gen_triple.py, or `python3 triple_gate.py triple-signal.json` to re-gate a published file.
"""
import json, sys, urllib.request

API = 'https://atlas-v2-jcsn.onrender.com'
MIN_HEALTH = 50


def _get(path, timeout=40):
    for _ in range(2):
        try:
            with urllib.request.urlopen(API + path, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            pass
    return None


def gate(rows, log=print):
    keep, dropped = [], []
    for r in rows:
        t = r['ticker']
        h = _get(f'/api/health/{t}') or {}
        r['health'] = h.get('score') if isinstance(h.get('score'), (int, float)) else None
        if r.get('signal') != 'buyback':
            keep.append(r)
            continue
        bb = (r.get('buyback') or {}).get('usd') or r.get('insiderUsd') or 0
        s = ((_get(f'/api/screener/lookup/{t}') or {}).get('data')) or {}
        fy, mc = s.get('fcfYield'), s.get('marketCap')
        fcf = fy * mc if isinstance(fy, (int, float)) and isinstance(mc, (int, float)) else None
        ok_h = r['health'] is not None and r['health'] >= MIN_HEALTH
        ok_f = fcf is not None and bb > 0 and fcf >= 0.5 * bb
        if ok_h or ok_f or (r['health'] is None and fcf is None):   # no data at all: do not drop blindly
            keep.append(r)
        else:
            dropped.append(t)
            log(f'gate: {t} dropped (buyback {bb/1e6:.0f}M$, FCF 12m {"n/a" if fcf is None else f"{fcf/1e6:.0f}M$"}, Health {r["health"]})')
    return keep, dropped


if __name__ == '__main__':
    f = sys.argv[1] if len(sys.argv) > 1 else 'triple-signal.json'
    d = json.load(open(f))
    d['stocks'], dropped = gate(d['stocks'])
    d['total'] = len(d['stocks'])
    d.setdefault('rules', {})['buybackGate'] = f'buyback-only needs Health {MIN_HEALTH}+ or 12m FCF >= 50% of the buyback'
    json.dump(d, open(f, 'w'), indent=1)
    print('dropped:', dropped)
