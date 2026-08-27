-- FPP Voting schema.
--
-- Design notes (the "why", so nobody undoes these by accident):
--
--  * song_key comes from the FPP sequenceName (the .fseq), never the playlist
--    index and never the media filename. Playlist indexes shift when you add a
--    song — that is the bug in the old plugin. Media filenames are not unique;
--    three 2025 Christmas entries shared one mp3.
--
--  * songs holds facts true regardless of show (artist, release year,
--    categories). show_songs holds facts true only for one show (is this
--    song currently in this show's playlist). Categories moved from
--    show_songs to songs on 2026-08-26 -- see CLAUDE.md: one global
--    vocabulary, not "Rock & Roll at Christmas, Dance Tunes at New Year's."
--
--  * songs.excluded (2026-08-27) is a manual, global "never voteable" flag --
--    separate from show_songs.active, which only reflects whether Reconcile
--    last saw a song in a particular show's live playlist and does not gate
--    voting at all any more (see CLAUDE.md section 12). excluded is checked
--    by Store.voteable_catalog directly, so it works even on a song that is
--    still sitting in the live FPP playlist right now.
--
--  * votes is append-only with a timestamp. Tallies are queries, never stored
--    counters, so history survives playlist edits and supports per-night stats.
--
--  * voter_hash is an HMAC of an opaque token the viewer's browser generates and
--    keeps. It is NOT derived from an IP address. Behind Cloudflare Tunnel every
--    viewer arrives from the same edge address, so IP-based identity would merge
--    the whole audience into one voter and break voting in exactly the
--    deployment we are shipping. The HMAC salt lives in `settings`.
--
-- NOTE: `PRAGMA foreign_keys` below applies only to the connection that runs
-- this script. It is per-connection, not per-database. Every connection must
-- set it again — db/connection.py does. Do not rely on this line alone.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- songs
CREATE TABLE IF NOT EXISTS songs (
    song_key         TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    artist           TEXT,
    year             INTEGER,
    sequence_name    TEXT NOT NULL,
    media_name       TEXT,
    duration_seconds REAL,
    display_override TEXT,                     -- manual title fix from admin UI
    categories       TEXT NOT NULL DEFAULT '[]', -- JSON array; global, not per show
    excluded         INTEGER NOT NULL DEFAULT 0, -- manual "never voteable" flag
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The controlled category vocabulary. One list for the whole install, not
-- per show (2026-08-26) -- see the note at the top of this file. Chips have
-- a fixed order and you cannot typo "Rock and Roll" against "Rock & Roll".
CREATE TABLE IF NOT EXISTS categories (
    name       TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL
);

-- Renaming a .fseq changes song_key. An alias forwards the old key so votes
-- and categories follow the rename instead of forking.
CREATE TABLE IF NOT EXISTS song_aliases (
    alias_key TEXT PRIMARY KEY,
    song_key  TEXT NOT NULL REFERENCES songs(song_key) ON DELETE CASCADE
);

-- ---------------------------------------------------------------- shows
CREATE TABLE IF NOT EXISTS shows (
    show_id         TEXT PRIMARY KEY,          -- 'christmas', 'nye', 'halloween'
    name            TEXT NOT NULL,
    playlist_name   TEXT NOT NULL,             -- the FPP playlist this maps to
    tagline         TEXT,
    note            TEXT,                      -- e.g. 'Dec 29 - Jan 3'
    votes_per_round INTEGER NOT NULL DEFAULT 3,
    cooldown_songs  INTEGER NOT NULL DEFAULT 4,
    theme           TEXT NOT NULL DEFAULT 'christmas',
    active          INTEGER NOT NULL DEFAULT 1,
    -- The 1-3 allowance is a product rule, so make it structural rather than
    -- trusting every caller to remember it.
    CHECK (votes_per_round BETWEEN 1 AND 3),
    CHECK (cooldown_songs >= 0)
);

-- ---------------------------------------------------------------- membership
-- Is this song currently part of this show's playlist? Nothing about
-- categorisation lives here any more -- see songs.categories above.
CREATE TABLE IF NOT EXISTS show_songs (
    show_id        TEXT NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    song_key       TEXT NOT NULL REFERENCES songs(song_key) ON DELETE CASCADE,
    active         INTEGER NOT NULL DEFAULT 1, -- 0 = left the playlist, kept
    playlist_index INTEGER,                    -- runtime only, never identity
    last_seen      TEXT,
    PRIMARY KEY (show_id, song_key)
);

-- ---------------------------------------------------------------- rounds
-- One row per song played. Votes attach to a round so "votes reset each song"
-- is a consequence of the data model, not a DELETE.
--
-- The two song columns are easy to confuse, so: song_key is what was PLAYING
-- while this round's votes were cast. winner_key is what those votes chose,
-- which plays in the FOLLOWING round. A round therefore ends with
-- winner_key == the next round's song_key, unless nobody voted.
--
-- ended_at IS NULL marks the one open round per show. A round is opened only
-- when FPP reports a genuinely different song; see Store.ensure_round.
CREATE TABLE IF NOT EXISTS rounds (
    round_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id    TEXT NOT NULL REFERENCES shows(show_id),
    song_key   TEXT REFERENCES songs(song_key),   -- what played this round
    winner_key TEXT REFERENCES songs(song_key),   -- what the vote chose next
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_rounds_show ON rounds(show_id, started_at);

-- ---------------------------------------------------------------- votes
CREATE TABLE IF NOT EXISTS votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id    TEXT NOT NULL REFERENCES shows(show_id),
    song_key   TEXT NOT NULL REFERENCES songs(song_key),
    round_id   INTEGER NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    voter_hash TEXT NOT NULL,                  -- salted, not an IP address
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_votes_round      ON votes(round_id);
CREATE INDEX IF NOT EXISTS ix_votes_show_song  ON votes(show_id, song_key);
CREATE INDEX IF NOT EXISTS ix_votes_created    ON votes(created_at);

-- One vote per person per song per round. The vote allowance (1-3 songs) is
-- enforced in the service; this index stops double-submits and replays.
CREATE UNIQUE INDEX IF NOT EXISTS ux_votes_round_voter_song
    ON votes(round_id, voter_hash, song_key);

-- ---------------------------------------------------------------- settings
-- Runtime configuration that belongs to this installation rather than to any
-- one show: the voter-identity HMAC salt, FPP host, MQTT on/off.
--
-- Deliberately NOT schema_meta. That table answers "which schema version is
-- this file?" and is read before any migration runs; mixing app config into it
-- means a config write and a migration touch the same rows.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- meta
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', '3');
