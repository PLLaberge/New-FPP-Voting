# FPP Voting

A self-hosted song-voting plugin for the [Falcon Player](https://falconplayer.com).
Viewers scan a QR code, browse your playlist on their phone, and vote for the
next song. Runs entirely on your Pi — no third-party server.

## Status

| Stage | State |
|---|---|
| Catalog parser + reconciler | Done, tested |
| Database layer | Done, tested |
| FPP adapter | Done, tested against constructed responses — see below |
| Service (FastAPI) | Done, tested |
| Voter page | Done — live data over WebSocket |
| Admin page | Done — `/admin`, gated by an optional admin token |
| Plugin packaging | Done — installed and running on the real Pi, live at songvote.ca |

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # also installs this project into the venv
pytest
```

All 251 tests should pass. That confirms your environment works.

## Try the voter page

```bash
python3 tools/init_db.py                       # once
FPPVOTE_FAKE=1 uvicorn fppvote.service:app       # then open http://localhost:8000
```

That is the real page on real data, with a simulated show playing behind it —
vote from two browser windows and watch both update. Opening the HTML file
directly no longer works: it has no data of its own any more.

## Rebuild the catalog from playlists

```bash
python3 tools/build_catalog.py
```

Reads the playlist fixtures, applies the title-cleaning rules, joins curated
metadata, and writes `data/catalog.json` plus a reconciliation report.

## Build the database

```bash
python3 tools/init_db.py
```

Creates `data/fppvote.db`, seeds the three shows and their category chips,
syncs both playlists and applies the curated categories. It prints what it did
and what still needs a human.

**Run it as often as you like.** Every write is additive: a re-run picks up
playlist changes and never overwrites a category, artist or title you have
edited since. It also reports anything it refused, such as a category assigned
to a show that has no such chip.

```bash
python3 tools/init_db.py --recategorise   # reapply metadata.py over the top
python3 tools/init_db.py --db /tmp/try.db # somewhere disposable
```

The database is generated and gitignored. Deleting it loses vote history and
nothing else — re-running the script rebuilds the rest.

## Layout

```
src/fppvote/
  catalog/     parser.py, reconcile.py, metadata.py  — tested
  db/          schema.sql, connection.py, store.py   — eight tables, tested
  fpp/         adapter.py, http.py, mqtt.py, fake.py — the only FPP touchpoint
  service/     config.py, follower.py, server.py    — FastAPI, rounds, votes
  web/static/  vote.html, admin.html                 — voter page, admin page
tests/         pytest suite + playlist fixtures
tools/         build_catalog.py, init_db.py, capture_fpp.py
data/          catalog.json, fppvote.db (generated)
scripts/       FPP plugin lifecycle hooks — see "Deploy to the Pi" below
deploy/        fppvote.service.template (systemd unit)
pluginInfo.json, menu.inc — what FPP's Plugin Manager reads
```

All database access goes through `db/store.py`. Nothing above it writes SQL.
All FPP access goes through `fpp/`. Nothing else talks to FPP.

`scripts/` and `tools/` are deliberately not the same directory: FPP's plugin
convention reserves `scripts/` for its own lifecycle hooks
(`fpp_install.sh` and friends), so this project's dev/build tooling lives in
`tools/` instead rather than fighting that name.

## Run the service

On a laptop, with a simulated show and no Pi anywhere:

```bash
FPPVOTE_FAKE=1 uvicorn fppvote.service:app --reload
```

Then <http://localhost:8000/api/state> for what the voter page will consume,
and <http://localhost:8000/api/health> for whether it can see FPP. Against a
real Pi, drop `FPPVOTE_FAKE` and set `FPPVOTE_FPP_URL=http://<pi>`.

| Variable | Default | |
|---|---|---|
| `FPPVOTE_DB` | `data/fppvote.db` | database path |
| `FPPVOTE_FPP_URL` | `http://localhost` | where FPP is |
| `FPPVOTE_MQTT_HOST` | *(empty)* | empty disables MQTT; HTTP polling is always the fallback |
| `FPPVOTE_POLL_SECONDS` | `1.0` | status poll interval |
| `FPPVOTE_HANDOVER_LEAD` | `2.0` | jump to the winner this long before the song ends |
| `FPPVOTE_FAKE` | `0` | `1` runs a simulated show |
| `FPPVOTE_ADMIN_TOKEN` | *(empty)* | empty leaves `/admin` open; set before it's reachable from outside your LAN |

## Deploy to the Pi

Installing the plugin on real FPP hardware and putting a Cloudflare Tunnel in
front of it are covered in **`docs/DEPLOY.md`** — that's the one part of this
project that needs the actual Pi rather than `FPPVOTE_FAKE=1`.

## Day-to-day: installing, updating, and running `/admin`

**`docs/USER_GUIDE.md`** — the owner-facing reference: both install paths
(Plugin Manager URL and manual SSH), the routine `git pull` update, and a
walkthrough of every `/admin` card and control.

## Capture your FPP's responses

**Do this before trusting the adapter.** The contract tests currently run
against responses *constructed* from FPP's documented API, not captured from a
real Pi — so a green suite proves the parsing is self-consistent, not that the
field names match your FPP.

```bash
python3 tools/capture_fpp.py --host 192.168.1.50   # your Pi's address
```

Capture **while a show is playing** — an idle FPP omits most of the fields that
matter. It writes `tests/fixtures/captured/*.json`; `pytest` picks them up
automatically and starts checking the parsing against what FPP actually said.
Commit them. They are what makes the FPP 10 upgrade a five-second check.

See `CLAUDE.md` for architecture decisions and why they were made.
