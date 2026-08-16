-- FPP Voting schema.
--
-- Design notes (the "why", so nobody undoes these by accident):
--
--  * song_key comes from the FPP sequenceName (the .fseq), never the playlist
--    index and never the media filename. Playlist indexes shift when you add a
--    song — that is the bug in the old plugin. Media filenames are not unique;
--    three 2025 Christmas entries shared one mp3.
--
--  * songs holds facts true regardless of show (artist, release year).
--    show_songs holds facts true only for one show (categories). The same
--    "Zero" is Rock & Roll at Christmas and Dance Tunes at New Year's.
--
--  * votes is append-only with a timestamp. Tallies are queries, never stored
--    counters, so history survives playlist edits and supports per-night stats.

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
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
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
    active          INTEGER NOT NULL DEFAULT 1
);

-- Controlled vocabulary per show, so chips have a fixed order and you cannot
-- typo "Rock and Roll" against "Rock & Roll".
CREATE TABLE IF NOT EXISTS show_categories (
    show_id    TEXT NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (show_id, name)
);

-- ---------------------------------------------------------------- membership
CREATE TABLE IF NOT EXISTS show_songs (
    show_id        TEXT NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    song_key       TEXT NOT NULL REFERENCES songs(song_key) ON DELETE CASCADE,
    categories     TEXT NOT NULL DEFAULT '[]', -- JSON array of category names
    active         INTEGER NOT NULL DEFAULT 1, -- 0 = left the playlist, kept
    playlist_index INTEGER,                    -- runtime only, never identity
    source         TEXT NOT NULL DEFAULT 'needs_review',
                                               -- curated | suggested | needs_review
    last_seen      TEXT,
    PRIMARY KEY (show_id, song_key)
);

-- ---------------------------------------------------------------- rounds
-- One row per song played. Votes attach to a round so "votes reset each song"
-- is a consequence of the data model, not a DELETE.
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

-- ---------------------------------------------------------------- meta
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', '1');
