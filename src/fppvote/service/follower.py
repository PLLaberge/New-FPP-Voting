"""
The loop that watches FPP and drives rounds. All the timing lives here.

Three rules, and the third is the one that costs votes if you get it wrong:

  playing -> ensure_round on the sequence FPP reports
  idle    -> close the open round; the show has stopped
  unknown -> DO NOTHING

'unknown' means we cannot see FPP, which is not the same as FPP being idle.
Closing a round on 'unknown' would discard every vote cast so far every time
the network hiccups, mid-song, which is exactly when the votes matter.

Handover: the winner takes over by jumping ~2s before the current song ends
(config.handover_lead_seconds), so it starts clean at the cost of the outgoing
song's fade. If that window is missed — a skipped poll, a long GC pause — the
late path below jumps as soon as the song changes instead. The winner playing
slightly wrong is acceptable; the winner never playing at all is not.

Nothing here is async. It is called from a thread so its blocking database and
HTTP work stays off the event loop.

2026-08-25 — the show no longer gatekeeps voting (CLAUDE.md). This module's
job changed shape along with it: instead of matching FPP's playlist name to
one configured show and listing that show's whole curated catalogue, it now
reads whatever FPP is actually playing and works out which of those songs are
real (have media, not animation) and already known (exist in the database).
`show_id` is still resolved every tick, but only as a best guess for the
header text and visual theme — see resolve_display_show — never to decide
who can vote for what.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..catalog.parser import clean_title, slugify
from ..db import Store
from ..fpp import (
    STATUS_IDLE, STATUS_PLAYING, STATUS_UNKNOWN, FppError, PlaylistEntry, ShowStatus,
)
from .config import Config

log = logging.getLogger(__name__)


def normalise_playlist_name(name: str | None) -> str:
    """Compare playlist names forgivingly.

    FPP stores playlists as JSON files in ~/media/playlists/ and refers to them
    by filename without the extension — 'NY_Dance_Party.json' on disk is
    'NY_Dance_Party' over the API. Whether a given FPP release includes the
    suffix in a status message is not something to bet a show on, so both forms
    match, and so does a difference in case or surrounding whitespace.

    Nothing else is normalised. Underscores, spaces and hyphens are all
    meaningful — 'All_Xmas_Songs - Alphabetic' is a real name, and collapsing
    those would let two genuinely different playlists collide. Still used to
    de-duplicate the playlist-index cache below; no longer used to pick a show.
    """
    text = (name or "").strip()
    if text.lower().endswith(".json"):
        text = text[:-len(".json")]
    return text.strip().lower()


def voteable_keys_from_entries(entries: list[PlaylistEntry], store: Store) -> list[str]:
    """Which of a live playlist's entries are candidates to vote for.

    Two filters, in order, matching Paulin's spec exactly (2026-08-25):
    1. Only real songs — an entry with no media (an animation, e.g.
       "House- White.fseq") is excluded. FPP simply omits the media field for
       these; there is nothing else to detect.
    2. `leadIn`/`leadOut` are already excluded upstream — parse_playlist only
       ever reads the `mainPlaylist` section of FPP's response, so entries
       is already just the main part.

    Existence in the `songs` table (has this been reconciled at all?) is
    deliberately NOT checked here — that filter belongs to
    Store.voteable_catalog, which the caller applies next. Keeping it there
    means this function stays pure (no DB access) and easy to test in
    isolation.
    """
    keys = []
    for entry in entries:
        if not entry.enabled or not entry.media_name:
            continue
        keys.append(store.resolve_key(slugify(entry.sequence_name)))
    # De-dupe while keeping order — a renamed sequence can alias onto a key
    # already seen under its old name.
    return list(dict.fromkeys(keys))


@dataclass
class FollowerState:
    """What the loop knows. Rebuilt from the database on restart, except for
    the handover bookkeeping, which is per-process and safe to lose."""
    show_id: str | None = None         # best guess only — see resolve_display_show
    round_id: int | None = None
    status: ShowStatus | None = None
    fpp_reachable: bool = False
    fpp_version: str | None = None
    version_warning: str | None = None
    last_error: str | None = None
    handed_over: set[int] = field(default_factory=set)
    # Playlist order, not a set — the voter page can sort songs by playing
    # order, and that ordering has to survive the round trip through here.
    voteable_keys: tuple[str, ...] = ()


class Follower:
    def __init__(self, store: Store, adapter, config: Config,
                 clock=time.monotonic):
        self.store = store
        self.adapter = adapter
        self.config = config
        self.state = FollowerState()
        self._clock = clock
        self._playlist_cache: dict[str, tuple[float, list[PlaylistEntry]]] = {}

    # --------------------------------------------------------- live playlist
    def current_entries(self, playlist_name: str) -> list[PlaylistEntry]:
        """The live playlist's entries, cached briefly. Whatever FPP is
        actually playing, never a fixed configured name."""
        name = normalise_playlist_name(playlist_name)
        cached = self._playlist_cache.get(name)
        now = self._clock()
        if cached and now - cached[0] < self.config.playlist_cache_seconds:
            return cached[1]
        try:
            entries = self.adapter.get_playlist(playlist_name)
        except FppError as exc:
            log.warning("could not read playlist %r: %s", playlist_name, exc)
            return cached[1] if cached else []
        self._playlist_cache[name] = (now, entries)
        return entries

    def playlist_index(self, playlist_name: str) -> dict[str, int]:
        """song_key -> playlist position, for start_at_item. Looked up fresh
        every time it is needed and never stored as identity — it shifts
        whenever a song is added, which is the bug in the old plugin."""
        return {self.store.resolve_key(slugify(e.sequence_name)): e.index
                for e in self.current_entries(playlist_name) if e.enabled}

    def resolve_display_show(self, voteable_keys: list[str]):
        """Best guess at "what does tonight feel like" for the header text
        and visual theme ONLY — never for voting, which does not need a show
        at all any more. Majority vote: whichever show curates the most of
        the currently voteable songs as an active member. Sticky when
        ambiguous (a tie, or nothing recognised yet) so the theme does not
        flicker between polls; falls back to the first active show if
        nothing has ever resolved.
        """
        overlap = self.store.show_overlap_counts(voteable_keys) if voteable_keys else {}
        if overlap:
            best = max(overlap.values())
            leaders = sorted(sid for sid, n in overlap.items() if n == best)
            if len(leaders) == 1:
                return self.store.get_show(leaders[0])
            if self.state.show_id in leaders:
                return self.store.get_show(self.state.show_id)   # keep the tie-break stable
            return self.store.get_show(leaders[0])
        if self.state.show_id:
            return self.store.get_show(self.state.show_id)
        shows = self.store.list_shows(active_only=True)
        return shows[0] if shows else None

    # ----------------------------------------------------------------- tick
    def tick(self) -> FollowerState:
        """One pass. Safe to call as often as you like; never raises."""
        try:
            return self._tick()
        except Exception as exc:                          # noqa: BLE001
            # A bug in here must not stop the show either.
            log.exception("follower tick failed")
            self.state.last_error = str(exc)
            return self.state

    def _tick(self) -> FollowerState:
        status = self.adapter.get_status()
        self.state.status = status
        self.state.fpp_reachable = status.status != STATUS_UNKNOWN

        if status.status == STATUS_UNKNOWN:
            # Deliberately nothing. The round stays open and the votes stand.
            return self.state

        if not self.store.list_shows(active_only=True):
            # Empty database. Says what to do about it, because "waiting for
            # the show" is what a viewer sees either way and gives no hint
            # that the catalogue is simply missing. Still needed: rounds.
            # show_id is NOT NULL, and there has to be at least one show row
            # to reference even though voting no longer cares which.
            self.state.last_error = ("no shows configured in the database - "
                                     "run tools/init_db.py to build it")
            return self.state
        self.state.last_error = None

        if status.status == STATUS_IDLE:
            self.store.close_open_round()
            self.state.round_id = None
            return self.state

        if status.status != STATUS_PLAYING or not status.sequence_name:
            return self.state

        entries = self.current_entries(status.playlist_name or "")
        candidate_keys = voteable_keys_from_entries(entries, self.store)
        catalog = self.store.voteable_catalog(candidate_keys)
        # Playlist order preserved — see FollowerState.voteable_keys.
        voteable_keys = [k for k in candidate_keys if k in catalog]
        self.state.voteable_keys = tuple(voteable_keys)

        display_show = self.resolve_display_show(voteable_keys)
        self.state.show_id = display_show.show_id if display_show else None
        if display_show is None:
            # No show row at all to hang a round on — see the empty-database
            # guard above; list_shows(active_only=True) was non-empty a
            # moment ago, so this only happens if it changed mid-tick.
            return self.state

        key = self.store.resolve_key(slugify(status.sequence_name))
        self._ensure_song_catalogued(key, status)

        previous_id = self.state.round_id
        rnd = self.store.ensure_round(display_show.show_id, key)
        self.state.round_id = rnd.round_id

        if rnd.round_id != previous_id and previous_id is not None:
            self._late_handover(status.playlist_name, previous_id, now_playing=key)

        self._maybe_hand_over(status.playlist_name, rnd, status)
        return self.state

    def _ensure_song_catalogued(self, key: str, status: ShowStatus) -> None:
        """The currently-playing sequence needs a `songs` row to exist before
        ensure_round can reference it (song_key is a real foreign key) — this
        is what makes "accept any playlist" actually work for a song nobody
        has reconciled yet, including a pure animation. Existence only, the
        same thing the parser is trusted to say during a normal reconcile:
        never touches curated fields (artist/year/categories) if the row
        already exists — upsert_song already guarantees that.
        """
        if self.store.get_song(key) is not None:
            return
        title, _feat, _notes = clean_title(status.media_title or "", status.sequence_name or "")
        self.store.upsert_song(key, title or status.sequence_name or key,
                               status.sequence_name or key,
                               media_name=status.media_title)

    # ------------------------------------------------------------- handover
    def _maybe_hand_over(self, playlist_name: str | None, rnd, status: ShowStatus) -> None:
        """The on-time path: jump a beat before the song ends."""
        if rnd.round_id in self.state.handed_over:
            return
        lead = self.config.handover_lead_seconds
        if lead <= 0:
            return                       # configured to use the late path only
        remaining = status.seconds_remaining
        if not (0 < remaining <= lead):
            return

        # Marked before the jump, not after: if start_at_item throws we still
        # must not retry it every 100ms for the rest of the song.
        self.state.handed_over.add(rnd.round_id)
        winner = self.store.winner(rnd.round_id)
        if not winner:
            return                       # nobody voted; let the playlist run on
        self.store.set_winner(rnd.round_id, winner)
        self._play(playlist_name, winner)

    def _late_handover(self, playlist_name: str | None, round_id: int, now_playing: str) -> None:
        """The fallback: the song changed before we got to jump.

        Costs the viewer about a second of whatever FPP chose by itself. That
        is the trade Paulin picked when he chose the early jump — the point is
        that a winner is never silently dropped.
        """
        if round_id in self.state.handed_over:
            return
        self.state.handed_over.add(round_id)
        winner = self.store.winner(round_id)
        if not winner:
            return
        self.store.set_winner(round_id, winner)
        if winner == now_playing:
            return                       # FPP happened to pick it anyway
        log.info("late handover: missed the lead window for round %s, jumping "
                 "to %s now", round_id, winner)
        self._play(playlist_name, winner)

    def _play(self, playlist_name: str | None, song_key: str) -> None:
        if not playlist_name:
            log.warning("winner %r decided but no playlist is currently known; "
                        "leaving the show alone", song_key)
            return
        index = self.playlist_index(playlist_name).get(song_key)
        if index is None:
            log.warning("winner %r is not in playlist %r; leaving the show "
                        "alone", song_key, playlist_name)
            return
        try:
            self.adapter.start_at_item(playlist_name, index)
        except FppError as exc:
            # The vote result is lost, the show is not.
            log.error("could not start %r at item %s: %s",
                      song_key, index, exc)

    # -------------------------------------------------------------- version
    def refresh_version(self) -> None:
        """Read the FPP version once at startup, for the health endpoint."""
        from ..fpp import untested_version_warning
        try:
            self.state.fpp_version = self.adapter.version()
        except FppError as exc:
            log.warning("could not read FPP version: %s", exc)
            self.state.fpp_version = None
        self.state.version_warning = untested_version_warning(self.state.fpp_version)
        if self.state.version_warning:
            log.warning("%s", self.state.version_warning)
