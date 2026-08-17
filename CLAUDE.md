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

Chips are per show and a show has only the ones that suit it — Halloween might
be Scary / Spooky / Funny and share nothing with Christmas. `show_categories`
is that vocabulary, and `Store.set_categories` refuses anything outside it: an
unrecognised name renders no chip, so the song silently vanishes from every
filtered view while still appearing under "All".

A chip with no songs behind it is a dead end a viewer can only tap to see
nothing, so the voter page asks for `list_categories(show_id, non_empty=True)`
and the admin page asks for the full vocabulary plus `category_counts()`.
Counted over active songs, so a chip disappears when its last song leaves the
playlist and returns when it comes back — without the curated vocabulary itself
ever being edited.

### 5. All FPP access goes through `fpp/adapter.py`. No exceptions.
FPP 10 ships mid-August 2026 and major releases break things. One file to fix.
Contract tests run the same assertions against every implementation, including
captured real responses.

Do **not** widen `TESTED_FPP_VERSIONS` without running the contract tests. The
old plugin declared support for versions that did not exist yet and shipped
broken to people who trusted the range.

An untested version **warns, never refuses** — `untested_version_warning()`.
Taking the show down over a version string would be a worse bug than the one
being guarded against.

MQTT wraps HTTP rather than replacing it: `MqttFppAdapter(HttpFppAdapter(...))`.
Freshness is judged on a message timestamp, not a connection callback, because
a broker that connects and then goes silent looks healthy from the connection's
side. Stale means fall back to polling — no reconnect logic, no state to rebuild.

`get_status()` never raises; everything else does. A status read failing must
not stop the show, but a vote result silently going nowhere is worse than an
error someone can see. **`unknown` is not `idle`** — the service must not close
a round on `unknown`, or a network blip discards every vote cast so far.

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
- The winner takes over by **jumping a beat early** — `start_at_item` fires
  ~2s before the current song ends (`handover_lead_seconds`), so the winner
  starts clean at the cost of the outgoing song's last second or two, which is
  usually a fade. Paulin's call, made deliberately over the alternative of
  letting FPP advance and then cutting away from a second of the wrong song.
  If the lead-time window is missed, the service falls back to exactly that
  alternative rather than skipping the winner — a silently unplayed winner is
  the one outcome neither option may produce.

Enforced in `db/store.py`, not in the service. `cast_vote` does its
count-then-insert inside `BEGIN IMMEDIATE`, because the unique index catches an
exact double-submit but cannot catch "allowance 3, four different songs" — two
taps that both read a count of 2 and both insert. The tally, the lock set and
the tie-break are all queries over `votes`/`rounds`, never stored counters, so
they are correct after a restart with no state to rebuild.

`ensure_round`, not `open_round`: a round is started only when FPP reports a
genuinely *different* song. FPP replays the current song after every reconnect,
and opening a round per report would discard everyone's votes mid-song during
exactly the wobble the votes most need to survive.

### 8. Voter identity is a browser-held token, never an IP.
`votes.voter_hash` is an HMAC (per-install salt in `settings`) of an opaque
random token the viewer's own browser generates and keeps. Not an IP address:
behind Cloudflare Tunnel every viewer arrives from the same edge address, so
IP-derived identity would merge the whole audience into one voter and break
voting in precisely the deployment we are shipping.

It must also be **transparent to the viewer** — say in plain language on the
page what is stored, and offer a way to reset it. The raw token never reaches
the database. Wire this up at stage 5.

## Reliability is the actual feature

The viewers already like the old broken app. The win is that this one keeps
working. Prefer boring and observable over clever: systemd over `ps | grep`,
a health endpoint, structured logs, and graceful degradation when FPP is
unreachable — the playlist should just keep playing.

Ship a **venv inside the plugin folder**. Do not touch system packages; FPP's
Python enforces PEP 668 externally-managed environments.

## Build order

1. ~~Catalog parser + reconciler~~ **done, tested**
2. ~~Database layer~~ **done, tested** — `db/connection.py`, `db/store.py`,
   `scripts/init_db.py`. All SQL lives in `store.py`; nothing above it writes
   any. `reconcile()` stays pure and in-memory — the store is only the
   load/save boundary around it, which is why idempotence is still testable
   without a database.
3. ~~FPP adapter~~ **done, tested** — `fpp/http.py`, `fpp/mqtt.py`,
   `fpp/fake.py`, contract tests in `tests/test_adapter.py`.
   **Caveat that matters:** the canned responses are CONSTRUCTED from FPP's
   documented shapes, not captured from the Pi. Run
   `scripts/capture_fpp.py --host <pi>` and commit the result; until then
   `test_captured_responses_are_present` xfails and a green suite means only
   that the parsing is self-consistent. `start_at_item` over HTTP is the least
   verified call in the project — if votes tally but nothing changes song,
   start there.
4. ~~Service — FastAPI, rounds, votes, WebSocket~~ **done, tested** —
   `service/config.py`, `follower.py`, `server.py`. Handlers are sync `def` so
   FastAPI's threadpool meets the store's thread-local connections; the
   follower is an async task that offloads each pass with `asyncio.to_thread`.
   The show is derived from the playlist FPP reports playing, matched against
   `shows.playlist_name` — no admin toggle, right after a restart. FPP keeps
   playlists as `~/media/playlists/<name>.json` and refers to them without the
   suffix; matching forgives the suffix and case but nothing else, since
   underscores, spaces and hyphens are all meaningful in a real name. The module
   is `server.py`, never `app.py`: a submodule named `app` shadows the package
   `__getattr__` that builds the ASGI app, and uvicorn then fails with
   "'module' object is not callable" at the first request.
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
- ~~`300-violin-orchestra` was tagged **Instrumental** at Christmas when that
  was a New Year's chip only~~ **resolved.** Christmas now has its own
  Instrumental chip and all five of its instrumental tracks carry it: 300
  Violin Orchestra, Christmas Eve / Sarajevo 12/24, First Snow, Carol of the
  Bells (Foster) and Wizards in Winter. `CATEGORY_ALIASES` maps Instrumental
  straight across between the two shows now instead of degrading it to
  Contemporary. It was the only out-of-vocabulary assignment in either
  playlist, and `Store.set_categories` refuses new ones.
- ~~`music-box-dancer-radio-version` was Instrumental at New Year's but not at
  Christmas~~ **resolved** — an oversight, now tagged at both, bringing
  Christmas to six instrumentals. Categories are per show and editorial, but
  "is this an instrumental?" is a fact about the recording rather than about
  the night, so a song at both shows should not disagree with itself.
  `test_songs_in_both_shows_agree_on_being_instrumental` holds the line across
  all 20 shared songs.

## Conventions

- Python 3.11+, FastAPI, vanilla JS. No build step, no npm on the Pi.
- `pytest` from the repo root. Tests must pass before a commit.
- Never commit `*.db` — the database is generated.
