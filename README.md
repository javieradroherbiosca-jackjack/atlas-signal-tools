# Atlas signal tools

Scripts used by Atlas Global Terminal's scheduled tasks:

- `gen_triple.py` builds `triple-signal.json` (Terminal, Triple Signal) and `signals.json` (Screener, Triple and Double Signal).
- `eu_registers.py` reads the French, Swedish, Norwegian, Danish and Belgian major-holding registers.
- `check_atlas.py` (`local` or `live`) and `qc_site.py` are the data quality checks.
- `terminal_headers` and `screener_headers` are the security headers for the two sites.

No credentials or private data live here. The main Atlas codebase is private.
