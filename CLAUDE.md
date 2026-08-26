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

A voter no longer sees one show's `show_songs` categories in isolation —
since section 12, the categories on screen are the union across every show
that curates a song, and which songs appear at all follows the live playlist,
not a chosen show. `show_songs` itself and its per-show vocabulary are
unchanged; see section 12 for what changed above them.

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
Global across the whole install since section 12, not per show — one
allowance, one cooldown, one set of rounds.
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
page what is stored. The raw token never reaches the database. Wire this up
at stage 5.

**No reset button, by deliberate choice (2026-08-25, reversing an earlier
one).** The page originally offered "Reset my voting ID," a condition Paulin
set when he first approved the token approach. It turned out to be the
easiest possible abuse vector: tap it, get a fresh identity, vote again,
bypassing the per-round allowance with no technical effort at all — easier
than anything a bot would need to do. Removing the button trades away that
original "give the viewer a way out" commitment in exchange for closing the
one-tap version of that hole; a determined person can still clear cookies or
open a private window, which is a real but meaningfully higher bar, and
proportionate to a ~100-votes-a-night show. Do **not** "fix" this by minting
a new identity automatically on every page load instead — that was floated
and rejected: reloads happen constantly and involuntarily (a phone waking
up, a backgrounded tab), so it would reset the allowance by accident for
ordinary viewers far more often than any bad actor would exploit it on
purpose, and it would also break "reload the page, still see your vote
highlighted," which depends on the same token surviving a reload.

### 9. A second, global tally sits alongside the per-round one.
`Store.tally(round_id)` (section 7) is operational: it picks what plays next,
resets every song, and is scoped to one show because a round belongs to one
show. `Store.cumulative_tally()` / `todays_tally()` / `daily_tallies()` are a
*different* thing — admin-facing analytics, added 2026-08-24 at Paulin's
request. They are deliberately **global across every show**, not per-show:
splitting them would need a non-arbitrary way to decide whose tally a vote
belongs to when a live playlist's songs straddle more than one show, and
Paulin's own call was that he cares about the total, not the split. Backed by
the same `votes` table, no new counters: `cumulative_tally` is just
`created_at >= ?` against `votes`, and "reset" (`reset_tally`) moves that
marker forward in `settings` rather than deleting anything — every vote ever
cast stays in `votes`, append-only, per the top of this file.

### 10. Voting can be paused without touching FPP or the follower.
`settings.voting_enabled` (default on) gates `POST /api/vote` and tells the
voter page to show a paused message instead of the song list. It does **not**
stop the follower, close rounds, or touch `fppd` — the show keeps playing and
rounds keep opening and closing underneath a pause exactly the way they do
when nobody happens to be voting. Pausing is a presentation and
vote-acceptance gate only, nothing structural, which is what makes it safe to
flip mid-show without disturbing anything else.

### 11. The voter page's header text and its four outbound links are
editorial, not identity. `shows.name` and `shows.note` used to render as a
small "which show is this" badge; Paulin repurposed them (2026-08-24) into
two admin-editable announcement lines — larger and smaller respectively —
that don't have to say a show name or a date at all. The four social/donate
links (own site, Google review, SCCSS donate, Instagram) are fixed content he
specified directly, not a general "admin can add arbitrary links" system —
matching this project's own bias against building generality nothing asked
for yet. Icons are inlined SVG, not fetched images, for the same reason
everything else here is self-contained: no third-party request at page load.

### 12. The app accepts any FPP playlist. A show no longer gatekeeps voting.
(2026-08-25) Originally a playlist had to name-match one of a fixed set of
`shows` before anyone could vote at all. That was the wrong shape once Paulin
wanted to point the service at whatever playlist FPP happens to be running,
built or renamed on the Pi, with no database change required to match it.
Voting is now driven entirely by what FPP reports playing, computed fresh by
the follower every tick (`Follower._tick`, `voteable_keys_from_entries`,
`Store.voteable_catalog`):

- **Voteable** = the live playlist's main section (leadIn/leadOut are already
  excluded — `parse_playlist` only ever reads `mainPlaylist`), filtered to
  entries with real media (an animation-only sequence simply has no
  `mediaName` in FPP's JSON at all — that is the only signal, and it is
  reliable), filtered again to sequences that already exist in `songs` — i.e.
  have been reconciled at some point. The currently-*playing* sequence still
  gets a minimal `songs` row auto-upserted if it has none
  (`Follower._ensure_song_catalogued`, via `catalog.parser.clean_title`), so
  `rounds.song_key`'s foreign key is always satisfiable — that does not make
  it voteable, since the media/animation filter runs first and an
  animation-only entry never reaches the database step at all.
- Nothing voteable at all -> the voter page says **"No songs to vote on at
  this time."**, not "waiting for the show" — that phrase means something
  different (FPP isn't playing a playlist at all) and reusing it would tell a
  viewer the wrong thing about what to expect.
- **`votes_per_round` / `cooldown_songs` are global settings** now
  (`Store.votes_per_round()` / `cooldown_songs()`, backed by the existing
  `settings` table — no schema migration), not columns on `shows`. One
  allowance, one cooldown, for the whole install, edited on the admin page's
  Voting card rather than per show. `shows.votes_per_round` and
  `shows.cooldown_songs` still exist on disk — no destructive migration for
  two now-unused columns — but nothing in the code reads or writes them any
  more.
- **A song's displayed categories are the union** of whatever every show that
  curates it has assigned it (`Store.voteable_catalog`). The **chip list** a
  voter sees is filtered further still, to only categories actually carried by
  tonight's voteable songs — the same "a chip with nothing behind it is a dead
  end" rule from section 4, now computed over the live playlist instead of one
  show's catalogue.
- **Rounds, votes, the locked set and the play-history tie-break are all
  global**, not scoped by show — one open round for the whole install
  (`Store.current_round()` takes no `show_id`; likewise `locked_keys()` and
  `last_played_round()`). `rounds.show_id` / `votes.show_id` stay `NOT NULL`
  in the schema and every round still records one — resolved by
  `Follower.resolve_display_show`, a majority-content-match over
  `Store.show_overlap_counts` (sticky when ambiguous, so the header does not
  flicker) — but purely as a best-guess label for the header text and visual
  theme, never as a filter anything downstream applies. Paulin's call
  (2026-08-25): the aggregate matters to him, not a per-show split, which
  sidesteps a genuine ambiguity — whose tally a vote belongs to when a live
  playlist's songs straddle more than one show — the same reasoning as
  section 9's global tally, now applied one level deeper.
- **Accepted edge case, knowingly.** Because "recently played" is now one
  shared history instead of one per former "show," a song that played at the
  very end of one season can stay locked into the start of the next if the two
  share it — real, since 20 of 26 New Year's songs are also at Christmas.
  Paulin's call: infrequent, fully under his control, and clearable by hand (a
  few real rounds on the new playlist push it back out of the cooldown
  window), which he judged simpler than resurrecting a per-show split once
  "which show" stops being a clean boundary to split by.
- `shows` still exists and still matters, just for **curation only**: the
  category vocabulary each show's admin tab edits, its per-show header text
  (section 11's two dynamic lines) and theme, and `playlist_name` as the
  target the admin page's "Reconcile with FPP" button pulls against. It is
  never consulted to decide who can vote for what.

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
   `tools/init_db.py`. All SQL lives in `store.py`; nothing above it writes
   any. `reconcile()` stays pure and in-memory — the store is only the
   load/save boundary around it, which is why idempotence is still testable
   without a database.
3. ~~FPP adapter~~ **done, tested** — `fpp/http.py`, `fpp/mqtt.py`,
   `fpp/fake.py`, contract tests in `tests/test_adapter.py`.
   **Caveat that matters:** the canned responses are CONSTRUCTED from FPP's
   documented shapes, not captured from the Pi. Run
   `tools/capture_fpp.py --host <pi>` and commit the result; until then
   `test_captured_responses_are_present` xfails and a green suite means only
   that the parsing is self-consistent. `start_at_item` over HTTP is the least
   verified call in the project — if votes tally but nothing changes song,
   start there.
4. ~~Service — FastAPI, rounds, votes, WebSocket~~ **done, tested** —
   `service/config.py`, `follower.py`, `server.py`. Handlers are sync `def` so
   FastAPI's threadpool meets the store's thread-local connections; the
   follower is an async task that offloads each pass with `asyncio.to_thread`.
   The header's show label is a best guess at which catalogue the live
   playlist's songs mostly belong to (see section 12) — no admin toggle, right
   after a restart, and no playlist-name matching any more either. FPP keeps
   playlists as `~/media/playlists/<name>.json` and refers to them without the
   suffix; `normalise_playlist_name` still forgives the suffix and case for the
   playlist-entries cache, since underscores, spaces and hyphens are all
   meaningful in a real name, but nothing gates on a name match today. The module
   is `server.py`, never `app.py`: a submodule named `app` shadows the package
   `__getattr__` that builds the ASGI app, and uvicorn then fails with
   "'module' object is not callable" at the first request.
5. ~~Wire `web/static/vote.html` to live data~~ **done, tested** — the page
   fetches `/api/state`, subscribes to `/ws`, and posts votes. Its embedded
   copy of the catalogue is gone, so chips and categories now follow the
   database without anyone editing the file. The WebSocket is the fast path and
   polling is the fallback, the same shape as MQTT over HTTP in the adapter:
   the event feed is an optimisation, never the only way to be right. The show
   picker and vote-allowance controls are gone — both follow the server now.
   `tests/test_vote_page.py` checks the page without a browser: every id the
   script reaches for exists, every URL it calls is a real route.
6. ~~Admin page — reconciliation, category assignment, settings~~ **done,
   tested** — `/admin`, backed by `/api/admin/*` in `server.py`. Every write
   goes through an existing `Store` method (`update_show`, `set_categories`,
   `set_show_categories`, `set_display_override`, `set_song_metadata`,
   `sync_show`); the admin routes add no SQL of their own. Reconcile pulls
   the show's playlist straight from the adapter's `get_playlist` and runs it
   through the same `reconcile()` every seed script uses — additive,
   idempotent, never touching a category a human already set. Gated by
   `FPPVOTE_ADMIN_TOKEN` (`X-Admin-Token` header): empty by default, which is
   what every laptop run gets since nothing else is listening, but **must be
   set before the Cloudflare Tunnel goes up** — an admin page with no auth
   sitting on a public tunnel is a real hole, not a laptop-only convenience
   like the rest of the defaults in this file. `tests/test_admin.py` and
   `tests/test_admin_page.py` follow the same pattern as stage 5's tests: the
   backend against `FakeFppAdapter`, the page checked without a browser.
7. ~~Package as an FPP plugin~~ **packaged, not yet installed on a Pi** —
   `pluginInfo.json`, `menu.inc`, `scripts/fpp_install.sh` and friends at the
   repo root, per FPP's own plugin spec
   (github.com/FalconChristmas/fpp-plugin-Template). `srcURL` is
   `github.com/PLLaberge/New-FPP-Voting` — that repo is what FPP `git clone`s
   on install, so this repo's root now doubles as the plugin's root. Deploying
   it and standing up the Cloudflare Tunnel are in `docs/DEPLOY.md`, written as
   steps for Paulin to run — this is the one stage that needed the real Pi,
   which nothing here has access to.

   `scripts/` was FPP's before it was ours: the plugin spec reserves that name
   for lifecycle hooks (`fpp_install.sh`, `fpp_uninstall.sh`, `preStart.sh`,
   `postStart.sh`, `preStop.sh`, `postStop.sh`), so this project's own dev
   tooling — `init_db.py`, `build_catalog.py`, `capture_fpp.py` — moved to
   `tools/` to get out of the way, rather than fighting FPP for the name.

   `fpp_install.sh` creates the venv **inside the git-managed plugin
   directory** (`$PLUGINDIR/New-FPP-Voting/venv`), per the "ship a venv"
   decision above, and installs a `systemd` unit (`deploy/fppvote.service.template`)
   pointed at it — `Restart=on-failure` is the actual mechanism behind
   "systemd over `ps | grep`". `fppvote.db` lives outside that directory, in
   `$MEDIADIR/plugindata/New-FPP-Voting/`, so an uninstall — which deletes the
   plugin directory — cannot take vote history or curated categories with it.

   `preStart.sh`/`preStop.sh`/`postStop.sh` are deliberate no-ops rather than
   the `systemctl start/stop` the official plugin guidelines otherwise
   recommend: this service exists specifically to keep taking votes through an
   `fppd` hiccup, and tying its lifecycle to `fppd`'s restarts would silently
   undo that every time a playlist reloads. `postStart.sh` only self-heals —
   starts the unit if it is not already running — which is a no-op against an
   already-running service.

   `menu.inc` links to the running service by absolute `http://` URL rather
   than a PHP page, since the plugin has no PHP UI of its own — FPP renders
   any `menu.inc` entry beginning `http(s)://` as a plain external link
   instead of routing it through `plugin.php`. FPP's Status menu points
   straight at `/admin` (2026-08-25) rather than the voter page — Paulin
   wanted one admin-facing entry point from FPP, not two voting-related
   links. The voter page's URL lives inside `/admin` instead
   (`#voterPageLink`), not anywhere in FPP's own menu.

   **Still open, and Paulin's call, not something to guess at:** the domain
   for the tunnel. `docs/DEPLOY.md` is written generically
   (`vote.yourdomain.com`) until one is chosen.

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
