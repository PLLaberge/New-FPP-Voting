# FPP Voting

A self-hosted song-voting plugin for the [Falcon Player](https://falconplayer.com).
Viewers scan a QR code, browse your playlist on their phone, and vote for the
next song. Runs entirely on your Pi — no third-party server.

## Status

| Stage | State |
|---|---|
| Catalog parser + reconciler | Done, 29 tests passing |
| Database schema | Written, needs access layer |
| FPP adapter | Interface defined, implementations pending |
| Service (FastAPI) | Not started |
| Voter page | Prototype done, runs on simulated data |
| Admin page | Not started |
| Plugin packaging | Not started |

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

All 29 tests should pass. That confirms your environment works.

## Try the voter page

```bash
xdg-open src/fppvote/web/static/vote.html   # or just open it in a browser
```

It runs on your real 2025 playlists with simulated voting, so you can click
around before any backend exists.

## Rebuild the catalog from playlists

```bash
python3 scripts/build_catalog.py
```

Reads the playlist fixtures, applies the title-cleaning rules, joins curated
metadata, and writes `data/catalog.json` plus a reconciliation report.

## Layout

```
src/fppvote/
  catalog/     parser.py, reconcile.py, metadata.py  — tested
  db/          schema.sql                            — five tables
  fpp/         adapter.py                            — the only FPP touchpoint
  web/static/  vote.html                             — the voter page
tests/         pytest suite + playlist fixtures
scripts/       build_catalog.py
data/          catalog.json
```

See `CLAUDE.md` for architecture decisions and why they were made.
