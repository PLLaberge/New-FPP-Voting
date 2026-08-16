# FPP Voting — project context

Read this before making changes. It records decisions that were made
deliberately and that look arbitrary without the reasoning.

## What this is

A self-hosted replacement for the `brp-fpp-voting` plugin. Viewers of a
Raspberry Pi Christmas light show scan a QR code, browse the song list on their
phone, and vote for what plays next.

Owner: Paulin Laberge. ~100 votes a night, 1–3 concurrent voters. Three shows:
Christmas (65 songs), New Year's Eve (26 songs), Halloween (not built yet).

## Why not just fix the old plugin

The original is a thin client for `barkersrandomprojects.com`, which is a
single point of failure outside our control and is currently broken. It also
has: command injection via `shell_exec` of a value read off disk; process
management by `ps | grep` and `kill`; song identity by playlist index; a
recursive retry with a 60s sleep inside the main loop; and a real bug where
`current_song != x AND current_song_id != y` should be `OR`.

## Architecture decisions

### 1. Fully self-hosted. No third-party server.
The Pi serves the voting page itself. Public access via **Cloudflare Tunnel**
(outbound only, no port forwarding, needs a domain for a stable URL so the
printed QR code never goes stale). Local WiFi is the fallback.

### 2. song_key comes from `sequenceName`, never the playlist index or media name.
- Playlist index shifts when a song is added. That is the old plugin's stats bug.
- Media filenames are **not unique** — three 2025 Christmas entries shared one
  mp3. Keying on media would silently merge distinct songs.
- The `.fseq` is what a viewer is actually voting for.

Display titles come from `mediaName`, cleaned. Identity and display are
different jobs and different fields.

### 3. Three kinds of data, three lifetimes.
- `songs` — artist, year. True regardless of show. Curate once.
- `show_songs` — categories. True only for one show. "Zero" is *Rock & Roll* at
  Christmas and *Dance Tunes* at New Year's.
- `votes` — append-only, timestamped. Tallies are queries, never counters.

One database, not one per show: 20 of 26 New Year's songs are also in the
Christmas playlist. Separate databases would duplicate them and drift.

### 4. Categories are editorial and never touched by the parser.
The parser can say a song exists; it can never say it is "Traditional".
Reconciliation is **additive and idempotent** — see `catalog/reconcile.py`.
Re-running it after curation must change nothing. A song removed from a
playlist is deactivated, not deleted, so categories and votes survive.

**An uncategorised song still appears to voters under "All".** A curation gap
must never hide a song from the people voting.

### 5. All FPP access goes through `fpp/adapter.py`. No exceptions.
FPP 10 ships mid-August 2026 and major releases break things. One file to fix.
Contract tests run the same assertions against every implementation, including
captured real responses.

Do **not** widen `TESTED_FPP_VERSIONS` without running the contract tests. The
old plugin declared support for versions that did not exist yet and shipped
broken to people who trusted the range.

### 6. MQTT preferred, HTTP polling as fallback.
FPP publishes `playlist/sequence/status`, `playlist/media/title`,
`playlist_details` (with `secondsRemaining`), and accepts
`set/playlist/<name>/startPosition`. Event-driven beats the old 1 Hz busy loop.
But MQTT is optional in FPP settings, so it can never be the only path.

### 7. Voting rules
- Votes **reset every song**. Enforced by attaching votes to a `round`.
- Vote allowance is 1–3 per round, configurable. Default 3 — at 1–3 concurrent
  voters, one vote each makes the winner nearly random.
- At allowance 1, tapping a new song **moves** the vote rather than refusing it.
- Ties break toward **least-recently-played**, so low turnout rotates the
  catalogue instead of favouring whatever sorts first.
- The playing song and the last 4 played are locked.

## Reliability is the actual feature

The viewers already like the old broken app. The win is that this one keeps
working. Prefer boring and observable over clever: systemd over `ps | grep`,
a health endpoint, structured logs, and graceful degradation when FPP is
unreachable — the playlist should just keep playing.

Ship a **venv inside the plugin folder**. Do not touch system packages; FPP's
Python enforces PEP 668 externally-managed environments.

## Build order

1. ~~Catalog parser + reconciler~~ **done, tested**
2. Database layer — schema exists, needs the access code
3. FPP adapter — interface exists, needs Http/Mqtt/Fake implementations
4. Service — FastAPI, rounds, votes, WebSocket
5. Wire `web/static/vote.html` to live data (it currently self-simulates)
6. Admin page — reconciliation, category assignment, settings
7. Package as an FPP plugin, deploy, add the tunnel

Stages 1–6 run entirely on a laptop against `FakeFppAdapter`. No Pi needed.

## Known data issues (real, in Paulin's playlists)

- 6 songs have no artist, 23 have no release year. `year=None` means "needs
  review"; it sorts last under a "Year to confirm" heading.
- Two *Carol of the Bells* sequences shorten to the same display title. They
  are distinguished by artist (Monique Danielle / David Foster). A third is
  the Star Wars Imperial March mashup.
- FPP truncates long media filenames mid-word. The parser repairs these only
  when unambiguous and flags the rest rather than guessing.

## Conventions

- Python 3.11+, FastAPI, vanilla JS. No build step, no npm on the Pi.
- `pytest` from the repo root. Tests must pass before a commit.
- Never commit `*.db` — the database is generated.
