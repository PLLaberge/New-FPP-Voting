# FPP Voting

A self-hosted song-voting plugin for the [Falcon Player](https://falconplayer.com).
Viewers scan a QR code, browse your playlist on their phone, and vote for the
next song. Runs entirely on your Pi — no third-party server.

## Status

| Stage | State |
|---|---|
| Catalog parser + reconciler | Done, tested |
| Database layer | Done, tested |
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

All 79 tests should pass. That confirms your environment works.

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

## Build the database

```bash
python3 scripts/init_db.py
```

Creates `data/fppvote.db`, seeds the three shows and their category chips,
syncs both playlists and applies the curated categories. It prints what it did
and what still needs a human.

**Run it as often as you like.** Every write is additive: a re-run picks up
playlist changes and never overwrites a category, artist or title you have
edited since. It also reports anything it refused, such as a category assigned
to a show that has no such chip.

```bash
python3 scripts/init_db.py --recategorise   # reapply metadata.py over the top
python3 scripts/init_db.py --db /tmp/try.db # somewhere disposable
```

The database is generated and gitignored. Deleting it loses vote history and
nothing else — re-running the script rebuilds the rest.

## Layout

```
src/fppvote/
  catalog/     parser.py, reconcile.py, metadata.py  — tested
  db/          schema.sql, connection.py, store.py   — eight tables, tested
  fpp/         adapter.py                            — the only FPP touchpoint
  web/static/  vote.html                             — the voter page
tests/         pytest suite + playlist fixtures
scripts/       build_catalog.py, init_db.py
data/          catalog.json, fppvote.db (generated)
```

All database access goes through `db/store.py`. Nothing above it writes SQL.

See `CLAUDE.md` for architecture decisions and why they were made.
