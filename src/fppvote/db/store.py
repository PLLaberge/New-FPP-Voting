"""
Every query in the project. The service layer above this file writes no SQL.

Three ideas run through the whole module:

1. TALLIES ARE QUERIES.  Vote counts, "what played least recently", and the
   locked set are all computed from `votes` and `rounds` on demand. Nothing is
   a stored counter, so editing the playlist, deactivating a song or replaying
   a night's history cannot leave a number stale.

2. WRITES ARE ADDITIVE.  Re-running a sync must not undo curation. upsert_song
   refreshes what the parser owns (title, sequence, media, duration) and only
   ever FILLS artist/year when they are empty; it never touches
   display_override. This is the same promise catalog/reconcile.py makes about
   categories, applied to the songs table.

3. RECONCILIATION STAYS PURE.  reconcile() takes a plain dict and knows nothing
   about SQLite. This module is the load/save boundary around it — see
   load_memberships / save_memberships / sync_show. Keeping it that way is why
   the idempotence guarantee is testable without a database.

The one place transaction boundaries are load-bearing is cast_vote. Everything
else would survive being sloppy; that one would not.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..catalog.reconcile import Membership, Report, reconcile
from .connection import Database

# Key under which the voter-identity HMAC salt lives in `settings`.
VOTER_SALT_KEY = "voter_salt"

# Settings keys for the admin page's tally and voting-pause controls.
VOTING_ENABLED_KEY = "voting_enabled"
TALLY_RESET_AT_KEY = "tally_reset_at"

# Global voting rules (2026-08-25) — moved off `shows` once a show no longer
# gatekeeps voting at all. One allowance and one cooldown for the whole
# install, not one per show.
VOTES_PER_ROUND_KEY = "votes_per_round"
COOLDOWN_SONGS_KEY = "cooldown_songs"

# cast_vote outcomes.
ACCEPTED = "accepted"            # vote recorded
MOVED = "moved"                  # allowance 1: previous vote replaced by this one
DUPLICATE = "duplicate"          # already voted for this song this round
LIMIT_REACHED = "limit_reached"  # allowance spent (allowance > 1)
LOCKED = "locked"                # playing now, or inside the cooldown window
NOT_IN_SHOW = "not_in_show"      # real song, not an active member of this show
UNKNOWN_SONG = "unknown_song"    # no such song_key at all
ROUND_CLOSED = "round_closed"    # round already ended, or never existed

_SUCCESS = frozenset({ACCEPTED, MOVED})

_UNSET = object()   # distinguishes "argument omitted" from "set this to None"


# --------------------------------------------------------------------- rows
@dataclass(frozen=True)
class Show:
    """A curation-side grouping — category vocabulary, header text, reconcile
    target. NOT a runtime voting concept any more: votes_per_round and
    cooldown_songs moved to global settings (2026-08-25, see CLAUDE.md) once
    the voter page stopped requiring a name-matched show to vote at all.
    schema.sql's shows.votes_per_round/cooldown_songs columns still exist on
    disk (no destructive migration for two now-unused columns) but nothing
    in this module reads or writes them any more."""
    show_id: str
    name: str
    playlist_name: str
    tagline: str | None
    note: str | None
    theme: str
    active: bool


@dataclass(frozen=True)
class Song:
    key: str
    title: str
    artist: str | None
    year: int | None
    sequence_name: str
    media_name: str | None
    duration_seconds: float | None
    display_override: str | None

    @property
    def display_title(self) -> str:
        """What a voter sees. The override wins when an admin has set one."""
        return self.display_override or self.title


@dataclass(frozen=True)
class ShowSong:
    """A song as it appears in one show — the songs/show_songs join."""
    key: str
    title: str
    artist: str | None
    year: int | None
    categories: list[str]
    active: bool
    playlist_index: int | None
    source: str

    @property
    def needs_review(self) -> bool:
        return not self.categories


@dataclass(frozen=True)
class Round:
    round_id: int
    show_id: str
    song_key: str | None
    winner_key: str | None
    started_at: str
    ended_at: str | None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True)
class VoteResult:
    outcome: str
    song_key: str
    removed_key: str | None = None   # set when outcome is MOVED
    votes_used: int = 0
    allowance: int = 0

    @property
    def accepted(self) -> bool:
        return self.outcome in _SUCCESS


# ----------------------------------------------------------------- helpers
def _duration_seconds(text: Any) -> float | None:
    """'03:34' -> 214.0, '1:02:03' -> 3723.0. None when unparseable.

    The parser carries FPP's mm:ss string through; the schema wants seconds so
    that arithmetic on it is possible.
    """
    if text is None:
        return None
    parts = str(text).strip().split(":")
    if not 1 <= len(parts) <= 3:
        return None
    total = 0.0
    for part in parts:
        try:
            total = total * 60 + float(part)
        except ValueError:
            return None
    return total


def _show(row) -> Show:
    return Show(
        show_id=row["show_id"], name=row["name"], playlist_name=row["playlist_name"],
        tagline=row["tagline"], note=row["note"],
        theme=row["theme"], active=bool(row["active"]),
    )


def _song(row) -> Song:
    return Song(
        key=row["song_key"], title=row["title"], artist=row["artist"], year=row["year"],
        sequence_name=row["sequence_name"], media_name=row["media_name"],
        duration_seconds=row["duration_seconds"], display_override=row["display_override"],
    )


def _round(row) -> Round:
    return Round(
        round_id=row["round_id"], show_id=row["show_id"], song_key=row["song_key"],
        winner_key=row["winner_key"], started_at=row["started_at"], ended_at=row["ended_at"],
    )


# ------------------------------------------------------------------- store
class Store:
    """All database access. Construct with Store.open(path)."""

    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def open(cls, path) -> "Store":
        return cls(Database(path))

    def close(self) -> None:
        self.db.close()

    def _q(self, sql: str, params: Sequence = ()):
        return self.db.connection.execute(sql, params)

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._q("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._q(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now')",
            (key, str(value)),
        )

    def voter_salt(self) -> str:
        """The per-install HMAC salt, generated on first use.

        Per-install and stable: it must survive a restart or every voter's
        identity resets mid-show and everyone gets a fresh allowance. It is a
        secret only in the sense that it stops someone who obtains the database
        from reversing hashes back to browser tokens.
        """
        salt = self.get_setting(VOTER_SALT_KEY)
        if salt is None:
            with self.db.transaction():
                # INSERT OR IGNORE, so two threads racing here agree on one salt
                # rather than the second overwriting the first's.
                self._q(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (VOTER_SALT_KEY, secrets.token_hex(32)),
                )
                salt = self.get_setting(VOTER_SALT_KEY)
        return salt

    def voter_hash(self, token: str) -> str:
        """Hash an opaque browser-held token into a voter identity.

        `token` is a random value the viewer's own browser generated and keeps.
        It is never an IP address: behind Cloudflare Tunnel every viewer shares
        the edge's address, so IP-derived identity would collapse the whole
        audience into a single voter. The raw token is never stored.
        """
        return hmac.new(
            self.voter_salt().encode(), token.encode(), hashlib.sha256
        ).hexdigest()

    # --------------------------------------------------------------- shows
    def create_show(
        self, show_id: str, name: str, playlist_name: str, *,
        tagline: str | None = None, note: str | None = None,
        theme: str = "christmas",
    ) -> bool:
        """Insert a show if it is missing. True if created, False if it existed.

        Insert-only on purpose. Seeding runs again every time the catalog is
        rebuilt, and an upsert here would quietly reset the descriptive fields
        every time — undoing whatever was set on the admin page. Use
        update_show to change a show.
        """
        cur = self._q(
            "INSERT OR IGNORE INTO shows(show_id, name, playlist_name, tagline, note,"
            " theme) VALUES(?,?,?,?,?,?)",
            (show_id, name, playlist_name, tagline, note, theme),
        )
        return cur.rowcount > 0

    def define_show(self, show_id: str, name: str, playlist_name: str, *,
                    tagline: str | None = None, note: str | None = None,
                    theme: str = "christmas") -> str:
        """Insert a show, or refresh its DESCRIPTIVE fields if it exists.

        Returns 'created', 'updated' or 'unchanged'.

        The split is the same one upsert_song makes. Descriptive fields come
        from SHOW_DEFS and are refreshed on every seed — editing the playlist
        name in that file has to actually reach the database, or you change it,
        re-run init_db.py, and silently get nothing. Behavioural settings
        (votes_per_round, cooldown_songs, active) are never touched here:
        those belong to whoever tuned them on the admin page.
        """
        existing = self.get_show(show_id)
        if existing is None:
            self._q(
                "INSERT INTO shows(show_id, name, playlist_name, tagline, note, theme)"
                " VALUES(?,?,?,?,?,?)",
                (show_id, name, playlist_name, tagline, note, theme),
            )
            return "created"

        changes = {"name": name, "playlist_name": playlist_name,
                   "tagline": tagline, "note": note, "theme": theme}
        if all(getattr(existing, field) == value for field, value in changes.items()):
            return "unchanged"
        assignments = ", ".join(f"{field} = ?" for field in changes)
        self._q(f"UPDATE shows SET {assignments} WHERE show_id = ?",
                (*changes.values(), show_id))
        return "updated"

    def update_show(self, show_id: str, **fields) -> None:
        """Change show settings. The admin page's write path.

        votes_per_round/cooldown_songs are NOT here — they're global settings
        now (set_votes_per_round/set_cooldown_songs below), not per-show."""
        allowed = {"name", "playlist_name", "tagline", "note", "theme", "active"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown show fields: {sorted(unknown)}")
        if not fields:
            return
        if "active" in fields:
            fields["active"] = int(bool(fields["active"]))
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._q(f"UPDATE shows SET {assignments} WHERE show_id = ?",
                (*fields.values(), show_id))

    def get_show(self, show_id: str) -> Show | None:
        row = self._q("SELECT * FROM shows WHERE show_id = ?", (show_id,)).fetchone()
        return _show(row) if row else None

    def list_shows(self, *, active_only: bool = True) -> list[Show]:
        sql = "SELECT * FROM shows"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY show_id"
        return [_show(r) for r in self._q(sql)]

    # ---------------------------------------------------------- categories
    def set_show_categories(self, show_id: str, names: Sequence[str]) -> list[str]:
        """Replace a show's controlled vocabulary, keeping the given order.

        Returns any category still assigned to songs but no longer in the
        vocabulary. show_songs.categories is a JSON array with no foreign key
        to lean on, so dropping a category silently orphans assignments — the
        admin page needs to be told, not left to notice missing chips.
        """
        with self.db.transaction():
            self._q("DELETE FROM show_categories WHERE show_id = ?", (show_id,))
            for order, name in enumerate(names):
                self._q(
                    "INSERT INTO show_categories(show_id, name, sort_order) VALUES(?,?,?)",
                    (show_id, name, order),
                )
            assigned: set[str] = set()
            for row in self._q("SELECT categories FROM show_songs WHERE show_id = ?",
                               (show_id,)):
                assigned.update(json.loads(row["categories"]))
        return sorted(assigned - set(names))

    def list_categories(self, show_id: str, *, non_empty: bool = False) -> list[str]:
        """This show's chips, in chip order.

        non_empty=True drops any category no song in the current playlist
        actually carries — what the voter page wants. The full vocabulary is
        what the admin page wants, so it stays the default.
        """
        if non_empty:
            return [c for c, n in self.category_counts(show_id).items() if n]
        return [r["name"] for r in self._q(
            "SELECT name FROM show_categories WHERE show_id = ? ORDER BY sort_order",
            (show_id,),
        )]

    def category_counts(self, show_id: str, *,
                        include_inactive: bool = False) -> dict[str, int]:
        """Chip -> how many songs carry it, in chip order, zeros included.

        Categories are per show and a show only has the ones that suit it:
        Halloween might be Scary / Spooky / Funny and share no chip at all with
        Christmas. But a vocabulary can also outlive its songs — a chip stays
        after the last song carrying it leaves the playlist, and a new show's
        chips exist before anything is categorised. Rendering "Crooners (0)" to
        a viewer is a dead end they can only tap to see nothing, so the voter
        page asks for non-empty chips and the admin page asks for all of them.

        Counted over active songs only, so a chip disappears from the voter page
        when its last song leaves the playlist and returns when it comes back —
        without touching the vocabulary, which stays curated.
        """
        counts = {name: 0 for name in self.list_categories(show_id)}
        for song in self.list_show_songs(show_id, include_inactive=include_inactive):
            for category in song.categories:
                if category in counts:
                    counts[category] += 1
        return counts

    # --------------------------------------------------------------- songs
    def upsert_song(
        self, key: str, title: str, sequence_name: str, *,
        media_name: str | None = None, duration_seconds: float | None = None,
        artist: str | None = None, year: int | None = None,
    ) -> None:
        """Additive write of one song.

        Parser-owned columns (title, sequence_name, media_name, duration) are
        refreshed. Curated columns are FILLED only when empty:
        COALESCE(songs.artist, excluded.artist) keeps what a human typed and
        accepts the parser's guess only where there was nothing.
        display_override is never touched here at all.

        The WHERE on the upsert means an unchanged row is not rewritten, so
        updated_at stays honest and a re-sync really is a no-op.
        """
        self._q(
            """
            INSERT INTO songs(song_key, title, artist, year, sequence_name,
                              media_name, duration_seconds)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(song_key) DO UPDATE SET
                title            = excluded.title,
                sequence_name    = excluded.sequence_name,
                media_name       = COALESCE(excluded.media_name, songs.media_name),
                duration_seconds = COALESCE(excluded.duration_seconds,
                                            songs.duration_seconds),
                artist           = COALESCE(songs.artist, excluded.artist),
                year             = COALESCE(songs.year, excluded.year),
                updated_at       = datetime('now')
            WHERE songs.title            IS NOT excluded.title
               OR songs.sequence_name    IS NOT excluded.sequence_name
               OR songs.media_name       IS NOT COALESCE(excluded.media_name,
                                                         songs.media_name)
               OR songs.duration_seconds IS NOT COALESCE(excluded.duration_seconds,
                                                         songs.duration_seconds)
               OR songs.artist           IS NOT COALESCE(songs.artist,
                                                         excluded.artist)
               OR songs.year             IS NOT COALESCE(songs.year, excluded.year)
            """,
            (key, title, artist, year, sequence_name, media_name, duration_seconds),
        )

    def resolve_key(self, key: str) -> str:
        """Follow a renamed .fseq's old key to the song it became."""
        row = self._q("SELECT song_key FROM song_aliases WHERE alias_key = ?",
                      (key,)).fetchone()
        return row["song_key"] if row else key

    def get_song(self, key: str) -> Song | None:
        row = self._q("SELECT * FROM songs WHERE song_key = ?",
                      (self.resolve_key(key),)).fetchone()
        return _song(row) if row else None

    def list_songs(self) -> list[Song]:
        return [_song(r) for r in self._q("SELECT * FROM songs ORDER BY title")]

    def set_display_override(self, key: str, text: str | None) -> None:
        """Manual title fix from the admin page. None clears it."""
        self._q(
            "UPDATE songs SET display_override = ?, updated_at = datetime('now') "
            "WHERE song_key = ?",
            (text or None, self.resolve_key(key)),
        )

    def set_song_metadata(self, key: str, *, artist=_UNSET, year=_UNSET) -> None:
        """Overwrite curated metadata. Unlike upsert_song this DOES replace —
        it is a human deliberately correcting a value, including back to None
        ('needs review'), which is why the sentinel is needed to tell an
        omitted argument from an explicit null."""
        fields = {}
        if artist is not _UNSET:
            fields["artist"] = artist
        if year is not _UNSET:
            fields["year"] = year
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._q(
            f"UPDATE songs SET {assignments}, updated_at = datetime('now') "
            f"WHERE song_key = ?",
            (*fields.values(), self.resolve_key(key)),
        )

    def add_alias(self, alias_key: str, song_key: str) -> None:
        """Point an old song_key at the song it became."""
        target = self.resolve_key(song_key)      # never build alias chains
        if alias_key == target:
            raise ValueError("a song cannot alias itself")
        if self._q("SELECT 1 FROM songs WHERE song_key = ?", (alias_key,)).fetchone():
            raise ValueError(
                f"{alias_key!r} is still a real song; merge_songs it first so its "
                f"votes and categories move rather than being orphaned"
            )
        self._q(
            "INSERT INTO song_aliases(alias_key, song_key) VALUES(?, ?) "
            "ON CONFLICT(alias_key) DO UPDATE SET song_key = excluded.song_key",
            (alias_key, target),
        )

    def merge_songs(self, old_key: str, new_key: str) -> None:
        """Fold `old_key` into `new_key` and leave an alias behind.

        This is what happens when a .fseq is renamed: the parser sees a brand
        new song and the old one looks deleted, so votes, categories and play
        history would fork across two rows. Merging moves all of it onto the
        surviving key.

        Categories: the surviving row's win, except where it has none, in which
        case the old row's are adopted. A union would invent combinations no
        one curated.
        """
        old, new = self.resolve_key(old_key), self.resolve_key(new_key)
        if old == new:
            return
        if self._q("SELECT 1 FROM songs WHERE song_key = ?", (new,)).fetchone() is None:
            raise ValueError(f"no such song: {new_key!r}")

        with self.db.transaction():
            for row in self._q("SELECT * FROM show_songs WHERE song_key = ?", (old,)):
                surviving = self._q(
                    "SELECT * FROM show_songs WHERE show_id = ? AND song_key = ?",
                    (row["show_id"], new),
                ).fetchone()
                if surviving is None:
                    self._q(
                        "UPDATE show_songs SET song_key = ? "
                        "WHERE show_id = ? AND song_key = ?",
                        (new, row["show_id"], old),
                    )
                    continue
                categories = surviving["categories"]
                source = surviving["source"]
                if not json.loads(categories):
                    categories, source = row["categories"], row["source"]
                self._q(
                    "UPDATE show_songs SET categories = ?, source = ?, active = ? "
                    "WHERE show_id = ? AND song_key = ?",
                    (categories, source,
                     int(bool(surviving["active"] or row["active"])),
                     row["show_id"], new),
                )
                self._q("DELETE FROM show_songs WHERE show_id = ? AND song_key = ?",
                        (row["show_id"], old))

            self._q("UPDATE votes  SET song_key   = ? WHERE song_key   = ?", (new, old))
            self._q("UPDATE rounds SET song_key   = ? WHERE song_key   = ?", (new, old))
            self._q("UPDATE rounds SET winner_key = ? WHERE winner_key = ?", (new, old))
            # Anything already pointing at the old key follows it forward.
            self._q("UPDATE song_aliases SET song_key = ? WHERE song_key = ?", (new, old))
            self._q("DELETE FROM songs WHERE song_key = ?", (old,))
            self._q("INSERT OR REPLACE INTO song_aliases(alias_key, song_key) "
                    "VALUES(?, ?)", (old, new))

    # --------------------------------------------------------- memberships
    def load_memberships(self) -> dict[tuple[str, str], Membership]:
        """Every show's memberships, in the shape reconcile() expects.

        ALL shows, deliberately — not just the one being reconciled.
        reconcile()'s suggest_from_other_shows scans this dict for the same
        song under a different show_id, which is how 20 of the 26 New Year's
        songs arrive pre-categorised. Filtering by show here would silently
        turn that feature off.
        """
        store: dict[tuple[str, str], Membership] = {}
        for row in self._q("SELECT * FROM show_songs"):
            store[(row["show_id"], row["song_key"])] = Membership(
                show_id=row["show_id"],
                key=row["song_key"],
                categories=json.loads(row["categories"]),
                active=bool(row["active"]),
                source=row["source"],
                playlist_index=row["playlist_index"] or 0,
            )
        return store

    def save_memberships(self, store: Mapping[tuple[str, str], Membership]) -> None:
        """Write a reconciled store back.

        last_seen is set by this method rather than carried on Membership, so
        reconcile() stays free of clock access. It moves on every sync for
        active rows — that column is a "when did we last see this in the
        playlist" fact and is meant to change, so it is the one thing a
        re-sync legitimately rewrites.
        """
        with self.db.transaction():
            for (show_id, key), m in store.items():
                self._q(
                    """
                    INSERT INTO show_songs(show_id, song_key, categories, active,
                                           playlist_index, source, last_seen)
                    VALUES(?,?,?,?,?,?, CASE WHEN ? THEN datetime('now') END)
                    ON CONFLICT(show_id, song_key) DO UPDATE SET
                        categories     = excluded.categories,
                        active         = excluded.active,
                        playlist_index = excluded.playlist_index,
                        source         = excluded.source,
                        last_seen      = CASE WHEN excluded.active = 1
                                              THEN datetime('now')
                                              ELSE show_songs.last_seen END
                    """,
                    (show_id, key, json.dumps(m.categories), int(m.active),
                     m.playlist_index, m.source, int(m.active)),
                )

    def set_categories(self, show_id: str, key: str, categories: Sequence[str],
                       *, source: str = "curated") -> None:
        """Assign categories by hand. The admin page's write path, and the only
        way a membership becomes 'curated'.

        Categories outside the show's vocabulary are refused. Enforcing it here
        is the whole point of having a controlled vocabulary: an unrecognised
        name produces no chip, so the song silently drops out of every filtered
        view while still appearing under "All" — a bug that is invisible until
        someone asks why a song never shows up under Instrumental.

        This is not hypothetical. The curated Christmas data assigns
        "Instrumental" to 300-violin-orchestra, which is a New Year's chip and
        has never existed at Christmas.
        """
        categories = list(categories)
        unknown = [c for c in categories if c not in set(self.list_categories(show_id))]
        if unknown:
            raise ValueError(
                f"{show_id} has no categor{'y' if len(unknown) == 1 else 'ies'} "
                f"{unknown!r}; add it to the show's vocabulary first"
            )
        self._q(
            "UPDATE show_songs SET categories = ?, source = ? "
            "WHERE show_id = ? AND song_key = ?",
            (json.dumps(categories), source, show_id, self.resolve_key(key)),
        )

    def sync_show(
        self, show_id: str, parsed_rows: Iterable[Mapping],
        *, metadata: Mapping[str, tuple[str | None, int | None]] | None = None,
    ) -> Report:
        """Parser output -> database, in one transaction. The stage-1 bridge.

        `parsed_rows` are rows from catalog.parser.parse_playlist. `metadata`
        maps song_key to (artist, year); those are only ever used to fill gaps,
        never to overwrite (see upsert_song).

        One transaction so that a crash halfway cannot leave songs written but
        memberships not — which would look like a catalogue of songs belonging
        to no show.
        """
        rows = list(parsed_rows)
        metadata = metadata or {}
        with self.db.transaction():
            for row in rows:
                artist, year = metadata.get(row["key"], (None, None))
                self.upsert_song(
                    row["key"], row["title"], row["sequence"],
                    media_name=row.get("media"),
                    duration_seconds=_duration_seconds(row.get("length")),
                    artist=artist, year=year,
                )
            store = self.load_memberships()
            report = reconcile(show_id, rows, store, self.list_categories(show_id))
            self.save_memberships(store)
        return report

    # ------------------------------------------------------- voter-facing
    def list_show_songs(self, show_id: str, *,
                        include_inactive: bool = False) -> list[ShowSong]:
        """The song list a voter sees.

        There is deliberately no filter on categories. An uncategorised song is
        still a song someone can vote for, and it must appear under "All" — a
        gap in curation must never hide a song from the people voting.
        """
        sql = """
            SELECT ss.song_key, ss.categories, ss.active, ss.playlist_index, ss.source,
                   s.title, s.display_override, s.artist, s.year
            FROM show_songs ss
            JOIN songs s ON s.song_key = ss.song_key
            WHERE ss.show_id = ?
        """
        if not include_inactive:
            sql += " AND ss.active = 1"
        sql += " ORDER BY ss.playlist_index, s.title"
        return [
            ShowSong(
                key=r["song_key"],
                title=r["display_override"] or r["title"],
                artist=r["artist"], year=r["year"],
                categories=json.loads(r["categories"]),
                active=bool(r["active"]),
                playlist_index=r["playlist_index"],
                source=r["source"],
            )
            for r in self._q(sql, (show_id,))
        ]

    # --------------------------------------------------- live-playlist voting
    # Since 2026-08-25 the voteable set comes from whatever FPP is actually
    # playing, not the per-show curated catalog above (list_show_songs is
    # still used for admin curation, unchanged) -- these two queries are what
    # the follower uses to turn "these sequence keys are in tonight's
    # playlist" into something the voter page can render. See CLAUDE.md.
    def voteable_catalog(self, keys: Iterable[str]) -> dict[str, dict]:
        """For each of `keys` (already resolved through aliases, already
        filtered by the caller to entries FPP reports having real media) that
        exists in `songs`, its display info and the UNION of its categories
        across every show that curates it. A key with no matching song is
        simply omitted — it just isn't voteable yet, the same "a curation gap
        never hides a song, it just has no chips" idea as before, one level
        earlier: here the song has not been reconciled into the catalogue at
        all yet, so there is nothing to show.
        """
        keys = list(dict.fromkeys(keys))       # de-dupe, keep order
        if not keys:
            return {}
        placeholders = ",".join("?" * len(keys))
        songs = {r["song_key"]: r for r in self._q(
            f"SELECT * FROM songs WHERE song_key IN ({placeholders})", keys)}
        if not songs:
            return {}
        cats: dict[str, set[str]] = {k: set() for k in songs}
        for r in self._q(
            f"SELECT song_key, categories FROM show_songs "
            f"WHERE song_key IN ({placeholders})", keys):
            if r["song_key"] in cats:
                cats[r["song_key"]].update(json.loads(r["categories"]))
        return {
            key: {"key": key, "title": song.display_title, "artist": song.artist,
                 "year": song.year, "categories": sorted(cats[key])}
            for key, row in songs.items()
            for song in [_song(row)]
        }

    def show_overlap_counts(self, keys: Iterable[str]) -> dict[str, int]:
        """For each show, how many of `keys` it curates as an active member.
        Used only to guess "what does tonight feel like" for the header text
        and visual theme (Follower.resolve_display_show) — not load-bearing
        for voting, which no longer needs to resolve a show at all."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        placeholders = ",".join("?" * len(keys))
        return {
            r["show_id"]: r["n"] for r in self._q(
                f"SELECT show_id, COUNT(*) AS n FROM show_songs "
                f"WHERE song_key IN ({placeholders}) AND active = 1 "
                f"GROUP BY show_id", keys)
        }

    # -------------------------------------------------------------- rounds
    # Global, not per-show, since 2026-08-25 -- one open round at a time for
    # the whole install, matching votes/tallies. `rounds.show_id` stays
    # NOT NULL in the schema and every round still records one (see
    # Follower.resolve_display_show), but it is informational now: a best
    # guess at "what did this feel like" for history's sake, never something
    # a query filters by.
    def get_round(self, round_id: int) -> Round | None:
        row = self._q("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        return _round(row) if row else None

    def current_round(self) -> Round | None:
        row = self._q(
            "SELECT * FROM rounds WHERE ended_at IS NULL ORDER BY round_id DESC LIMIT 1",
        ).fetchone()
        return _round(row) if row else None

    def ensure_round(self, show_id: str, song_key: str) -> Round:
        """Return the open round, starting a new one only if the song changed.

        Not `open_round`. FPP's status gets re-read constantly and re-reports
        the same song every time; more importantly, when FPP goes unreachable
        and comes back the adapter replays the current song. A method that
        opened a round per call would throw away every vote cast so far,
        mid-song, precisely during the wobble the votes most need to survive.

        Only call this with a real song from a reachable FPP. When the adapter
        reports status='unknown' the correct move is to do nothing and leave
        the round open; when it reports idle, call close_open_round.

        Consequence worth knowing: a song played genuinely back-to-back
        continues the same round instead of starting a second one. With
        cooldown_songs >= 1 a voted-for song cannot repeat, so this only
        affects a manual replay.

        `show_id` is written but not read back for gating — see the section
        comment above.
        """
        key = self.resolve_key(song_key)
        with self.db.transaction():
            current = self.current_round()
            if current is not None and current.song_key == key:
                return current
            if current is not None:
                self._q(
                    "UPDATE rounds SET ended_at = datetime('now') WHERE round_id = ?",
                    (current.round_id,),
                )
            cur = self._q(
                "INSERT INTO rounds(show_id, song_key) VALUES(?, ?)", (show_id, key)
            )
            return self.get_round(cur.lastrowid)

    def close_open_round(self, winner_key: str | None = None) -> None:
        """End the open round — the show stopped, or the night is over."""
        current = self.current_round()
        if current is None:
            return
        self._q(
            "UPDATE rounds SET ended_at = datetime('now'), "
            "winner_key = COALESCE(?, winner_key) WHERE round_id = ?",
            (winner_key and self.resolve_key(winner_key), current.round_id),
        )

    def set_winner(self, round_id: int, winner_key: str | None) -> None:
        """Record what this round's votes chose. It plays in the NEXT round."""
        self._q(
            "UPDATE rounds SET winner_key = ? WHERE round_id = ?",
            (winner_key and self.resolve_key(winner_key), round_id),
        )

    def recent_song_keys(self, limit: int) -> list[str]:
        """Song keys from the last `limit` rounds, most recent first."""
        return [r["song_key"] for r in self._q(
            "SELECT song_key FROM rounds WHERE song_key IS NOT NULL "
            "ORDER BY round_id DESC LIMIT ?",
            (limit,),
        )]

    def locked_keys(self) -> set[str]:
        """What a voter cannot vote for: playing now, plus the cooldown window.

        Derived from `rounds` every time rather than tracked, so it is correct
        after a restart with no state to rebuild.
        """
        return set(self.recent_song_keys(self.cooldown_songs() + 1))

    def last_played_round(self) -> dict[str, int]:
        """song_key -> the most recent round it played in. Drives the tie-break.

        Global since 2026-08-25 (was per-show). Paulin's call, accepting the
        edge case knowingly: a song shared between two former "shows" can
        stay locked into the start of a new season if it played right at the
        end of the last one, where it used to count as never-played. He can
        clear it by hand (a few real rounds push it out of the window) and
        judged that simpler than resurrecting a per-show history split once
        "which show" is no longer a clean boundary — see CLAUDE.md.
        """
        return {r["song_key"]: r["last_round"] for r in self._q(
            "SELECT song_key, MAX(round_id) AS last_round FROM rounds "
            "WHERE song_key IS NOT NULL GROUP BY song_key",
        )}

    # --------------------------------------------------------------- votes
    def cast_vote(
        self, round_id: int, voter_hash: str, song_key: str, *,
        allowance: int | None = None, locked: Iterable[str] | None = None,
        valid_keys: Iterable[str] | None = None,
    ) -> VoteResult:
        """Record one vote. Atomic: check and insert happen in one transaction.

        The unique index on (round_id, voter_hash, song_key) stops an exact
        double-submit, but it cannot stop "allowance 3, four different songs" —
        two taps that both read a count of 2 and both insert. Hence
        BEGIN IMMEDIATE around the whole method.

        At allowance 1 a vote for a different song MOVES the existing one
        rather than being refused: with one vote each and 1-3 people watching,
        refusing the change is just a worse experience for no gain.

        `valid_keys`, if given, is "what's actually voteable right now" —
        computed by the follower from the live FPP playlist's content
        (2026-08-25; see CLAUDE.md). This store no longer decides that from a
        static per-show membership table: a song not currently in the
        playing playlist is excluded by simply not being in this set,
        whether it's from a different occasion or was just deactivated by a
        reconcile. Omitted, the check is skipped — used by tests that exercise
        the allowance/lock/tie-break logic without a live playlist.
        """
        with self.db.transaction():
            rnd = self.get_round(round_id)
            if rnd is None or not rnd.is_open:
                return VoteResult(ROUND_CLOSED, song_key)

            key = self.resolve_key(song_key)
            # show_id comes from the round, never from the caller. votes.show_id
            # is denormalised and this is what stops it drifting from
            # rounds.show_id. Informational only now -- see current_round.
            show_id = rnd.show_id

            exists = self._q("SELECT 1 FROM songs WHERE song_key = ?",
                             (key,)).fetchone()
            if exists is None:
                return VoteResult(UNKNOWN_SONG, key)
            if valid_keys is not None and key not in set(valid_keys):
                return VoteResult(NOT_IN_SHOW, key)

            if allowance is None:
                allowance = self.votes_per_round()
            if locked is None:
                locked = self.locked_keys()

            existing = [r["song_key"] for r in self._q(
                "SELECT song_key FROM votes WHERE round_id = ? AND voter_hash = ? "
                "ORDER BY vote_id",
                (round_id, voter_hash),
            )]

            if key in set(locked):
                return VoteResult(LOCKED, key, votes_used=len(existing),
                                  allowance=allowance)
            if key in existing:
                # Not an error. The voter page treats a second tap as "unvote",
                # which is retract_vote — a store primitive should not silently
                # mean two opposite things depending on state.
                return VoteResult(DUPLICATE, key, votes_used=len(existing),
                                  allowance=allowance)

            removed = None
            if len(existing) >= allowance:
                if allowance == 1:
                    removed = existing[0]
                    self._q(
                        "DELETE FROM votes WHERE round_id = ? AND voter_hash = ?",
                        (round_id, voter_hash),
                    )
                else:
                    return VoteResult(LIMIT_REACHED, key, votes_used=len(existing),
                                      allowance=allowance)

            self._q(
                "INSERT INTO votes(show_id, song_key, round_id, voter_hash) "
                "VALUES(?,?,?,?)",
                (show_id, key, round_id, voter_hash),
            )
            used = len(existing) + 1 if removed is None else 1
            return VoteResult(MOVED if removed else ACCEPTED, key,
                              removed_key=removed, votes_used=used,
                              allowance=allowance)

    def retract_vote(self, round_id: int, voter_hash: str, song_key: str) -> bool:
        """Take a vote back — tapping a song you already voted for. True if one
        was removed."""
        cur = self._q(
            "DELETE FROM votes WHERE round_id = ? AND voter_hash = ? AND song_key = ?",
            (round_id, voter_hash, self.resolve_key(song_key)),
        )
        return cur.rowcount > 0

    def voter_selection(self, round_id: int, voter_hash: str) -> set[str]:
        """Which songs this voter has picked this round — what the page needs
        to render its pips and highlights after a reload."""
        return {r["song_key"] for r in self._q(
            "SELECT song_key FROM votes WHERE round_id = ? AND voter_hash = ?",
            (round_id, voter_hash),
        )}

    def tally(self, round_id: int) -> dict[str, int]:
        """Vote counts for a round. A query, never a counter."""
        return {r["song_key"]: r["n"] for r in self._q(
            "SELECT song_key, COUNT(*) AS n FROM votes WHERE round_id = ? "
            "GROUP BY song_key",
            (round_id,),
        )}

    def winner(self, round_id: int) -> str | None:
        """What won this round, or None if nobody voted.

        Ties break toward the least-recently-played song, and a song that has
        never played counts as least recent of all. At this turnout a
        first-past-the-post tie is common, so the tie-break is what actually
        decides most nights: breaking toward least-recently-played rotates the
        catalogue, where breaking alphabetically would play Auld Lang Syne
        forever. Title is the final, deterministic tiebreak.

        Returning None rather than picking something is deliberate — the
        service lets the playlist carry on by itself.
        """
        row = self._q(
            """
            SELECT v.song_key,
                   COUNT(*) AS votes,
                   COALESCE((SELECT MAX(r.round_id) FROM rounds r
                             WHERE r.song_key = v.song_key), -1)
                       AS last_played,
                   s.title AS title
            FROM votes v
            JOIN songs s ON s.song_key = v.song_key
            WHERE v.round_id = ?
            GROUP BY v.song_key
            ORDER BY votes DESC, last_played ASC, title ASC
            LIMIT 1
            """,
            (round_id,),
        ).fetchone()
        return row["song_key"] if row else None

    # --------------------------------------------------------- voting rules
    def votes_per_round(self) -> int:
        """The vote allowance, global across every show (2026-08-25) — see
        the Show dataclass's own docstring for why this moved off `shows`."""
        return int(self.get_setting(VOTES_PER_ROUND_KEY, "3"))

    def set_votes_per_round(self, n: int) -> None:
        n = int(n)
        if not 1 <= n <= 3:
            # The 1-3 allowance is a product rule (CLAUDE.md section 7), not
            # an arbitrary bound -- caught here for a usable error message.
            raise ValueError("votes_per_round must be between 1 and 3")
        self.set_setting(VOTES_PER_ROUND_KEY, n)

    def cooldown_songs(self) -> int:
        """How many recently-played songs stay locked, global across every
        show (2026-08-25)."""
        return int(self.get_setting(COOLDOWN_SONGS_KEY, "4"))

    def set_cooldown_songs(self, n: int) -> None:
        n = int(n)
        if n < 0:
            raise ValueError("cooldown_songs must not be negative")
        self.set_setting(COOLDOWN_SONGS_KEY, n)

    # ----------------------------------------------------------- admin tally
    # Everything below is deliberately GLOBAL, not scoped by show_id or
    # round_id, unlike tally() above. Paulin's call (2026-08-18): a single
    # persistent tally across every show is more useful to him than one split
    # per show, and it sidesteps a genuine ambiguity -- when a live playlist's
    # songs straddle more than one show, there is no non-arbitrary way to pick
    # whose tally a vote belongs to. `votes` is append-only (see schema.sql),
    # so nothing here is a stored counter; a "reset" moves where counting
    # starts rather than deleting anything.
    def voting_enabled(self) -> bool:
        """Whether the public page currently accepts votes. Defaults on — an
        admin has to deliberately stop voting, never the reverse, so a
        missing setting (a fresh install) never silently blocks a show."""
        return self.get_setting(VOTING_ENABLED_KEY, "1") == "1"

    def set_voting_enabled(self, enabled: bool) -> None:
        self.set_setting(VOTING_ENABLED_KEY, "1" if enabled else "0")

    def cumulative_tally(self) -> dict[str, int]:
        """Vote counts since the last reset (or all time, if never reset)."""
        since = self.get_setting(TALLY_RESET_AT_KEY)
        sql = "SELECT song_key, COUNT(*) AS n FROM votes"
        params: tuple = ()
        if since:
            sql += " WHERE created_at >= ?"
            params = (since,)
        sql += " GROUP BY song_key"
        return {r["song_key"]: r["n"] for r in self._q(sql, params)}

    def todays_tally(self) -> dict[str, int]:
        """Votes cast so far today, in local time. Independent of the reset
        marker — resetting the cumulative total does not change what "today"
        means, and today's count is never itself reset early."""
        return {r["song_key"]: r["n"] for r in self._q(
            "SELECT song_key, COUNT(*) AS n FROM votes "
            "WHERE date(created_at, 'localtime') = date('now', 'localtime') "
            "GROUP BY song_key"
        )}

    def daily_tallies(self, days: int = 8) -> list[dict]:
        """[{'date': 'YYYY-MM-DD', 'counts': {song_key: n}}, ...] for the last
        `days` days, oldest first, local time. `created_at` is stored via
        SQLite's datetime('now'), which is UTC; the 'localtime' modifier
        converts it for display the way a person actually experiences "today"
        rather than wherever UTC midnight happens to fall.
        """
        rows = self._q(
            "SELECT date(created_at, 'localtime') AS day, song_key, COUNT(*) AS n "
            "FROM votes "
            "WHERE date(created_at, 'localtime') >= date('now', 'localtime', ?) "
            "GROUP BY day, song_key",
            (f"-{days - 1} days",),
        )
        by_day: dict[str, dict[str, int]] = {}
        for r in rows:
            by_day.setdefault(r["day"], {})[r["song_key"]] = r["n"]
        return [{"date": d, "counts": c} for d, c in sorted(by_day.items())]

    def tally_reset_at(self) -> str | None:
        """When the cumulative tally last reset, or None if never."""
        return self.get_setting(TALLY_RESET_AT_KEY)

    def reset_tally(self) -> str:
        """Move the cumulative tally's start forward to now and return the new
        marker. Every vote ever cast stays in `votes` — see schema.sql's
        append-only design — this only changes where cumulative_tally starts
        counting from, so a reset is always safe to undo by hand if needed.

        Millisecond precision, not datetime('now')'s default whole seconds:
        votes.created_at only has second resolution, so a reset landing in
        the same second as a vote needs the marker to sort strictly after it
        for cumulative_tally's `created_at >= since` to exclude that vote —
        a second-precision marker in the same second as the vote would be
        string-equal and wrongly keep counting it.
        """
        now = self._q(
            "SELECT strftime('%Y-%m-%d %H:%M:%f', 'now') AS now"
        ).fetchone()["now"]
        self.set_setting(TALLY_RESET_AT_KEY, now)
        return now

    def recent_votes(self, limit: int = 50) -> list[dict]:
        """The most recent votes, newest first — the admin page's activity
        feed. Titles are joined in so the caller needs no second query."""
        return [
            {"created_at": r["created_at"], "song_key": r["song_key"], "title": r["title"]}
            for r in self._q(
                """
                SELECT v.created_at, v.song_key,
                       COALESCE(s.display_override, s.title) AS title
                FROM votes v JOIN songs s ON s.song_key = v.song_key
                ORDER BY v.vote_id DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]
