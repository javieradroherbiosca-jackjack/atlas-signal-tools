"""Publish triple-signal.json and signals.json to the Atlas backend (30 Oct 2026).

Since 30 Oct 2026 both files are rebuilt every day and served by the backend at
  https://atlas-v2-jcsn.onrender.com/api/signals/triple-signal.json  and  /api/signals/signals.json
(the Terminal and the Screener read them there; the static copies on Netlify are only a fallback),
so publishing them no longer needs a Netlify deploy.

Usage: python3 publish_signals.py TOKEN [triple-signal.json] [signals.json]
The backend rejects a file without asOf, with fewer than 12 stocks or 200 rows, or without usLedger,
and keeps the previous version as <name>.prev. Exit code 0 = both published and verified.
"""
import json, sys, time, urllib.request

API = 'https://atlas-v2-jcsn.onrender.com'


def post(name, path, tok):
    body = open(path, 'rb').read()
    json.loads(body)
    req = urllib.request.Request(f'{API}/api/signals/publish/{name}', data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'x-signals-token': tok, 'User-Agent': 'AtlasSignals/1.0'})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:300]
            if e.code in (400, 401, 404):
                raise SystemExit(f'{name}: HTTP {e.code} {msg}')
            last = f'HTTP {e.code} {msg}'
        except Exception as e:
            last = str(e)
        time.sleep(20)
    raise SystemExit(f'{name}: failed after 3 tries ({last})')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tok = sys.argv[1]
    files = {'triple-signal': sys.argv[2] if len(sys.argv) > 2 else 'triple-signal.json',
             'signals': sys.argv[3] if len(sys.argv) > 3 else 'signals.json'}
    for name, path in files.items():
        r = post(name, path, tok)
        want = json.load(open(path)).get('asOf')
        time.sleep(2)
        with urllib.request.urlopen(f'{API}/api/signals/{name}.json?v={int(time.time())}', timeout=120) as x:
            live = json.load(x)
        # the GET is cached up to 5 minutes; the POST clears that cache, so the live asOf must match now
        if live.get('asOf') != want:
            raise SystemExit(f'{name}: published but the live file still says asOf {live.get("asOf")} (expected {want})')
        print(f'{name}: published and verified · asOf {r["asOf"]} · {r["n"]} entries')
