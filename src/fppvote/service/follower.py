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
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..catalog.parser import slugify
from ..db import Store
from ..fpp import (
    STATUS_IDLE, STATUS_PLAYING, STATUS_UNKNOWN, FppError, ShowStatus,
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
    those would let two genuinely different playlists collide.
    """
    text = (name or "").strip()
    if text.lower().endswith(".json"):
        text = text[:-len(".json")]
    return text.strip().lower()


@dataclass
class FollowerState:
    """What the loop knows. Rebuilt from the database on restart, except for
    the handover bookkeeping, which is per-process and safe to lose."""
    show_id: str | None = None
    round_id: int | None = None
    status: ShowStatus | None = None
    fpp_reachable: bool = False
    fpp_version: str | None = None
    version_warning: str | None = None
    last_error: str | None = None
    handed_over: set[int] = field(default_factory=set)


class Follower:
    def __init__(self, store: Store, adapter, config: Config,
                 clock=time.monotonic):
        self.store = store
        self.adapter = adapter
        self.config = config
        self.state = FollowerState()
        self._clock = clock
        self._playlist_cache: dict[str, tuple[float, dict[str, int]]] = {}

    # ------------------------------------------------------------ show lookup
    def resolve_show(self, status: ShowStatus):
        """Which show is running, from the playlist FPP says it is playing.

        Deriving it beats an admin toggle or a date rule: it is right after a
        restart, right when Paulin switches playlists mid-evening, and it needs
        nobody to remember anything. The cost is that shows.playlist_name must
        match FPP exactly — which is why init_db.py nags about it.
        """
        name = normalise_playlist_name(status.playlist_name)
        shows = self.store.list_shows(active_only=True)
        if name:
            for show in shows:
                if normalise_playlist_name(show.playlist_name) == name:
                    return show

        # Unrecognised or absent playlist: stay on the show we were already on
        # rather than guessing, so an FPP blip does not switch shows.
        if self.state.show_id:
            return self.store.get_show(self.state.show_id)

        # One active show and nothing to disambiguate: use it, but say so. The
        # playlist names were guesses until someone checked them against the
        # Pi, and refusing to run because of a name mismatch would be a worse
        # first-night experience than running with a warning.
        if len(shows) == 1:
            log.warning("FPP is playing %r, which matches no show's "
                        "playlist_name; falling back to the only active show "
                        "(%s)", status.playlist_name, shows[0].show_id)
            return shows[0]
        return None

    def playlist_index(self, show) -> dict[str, int]:
        """song_key -> playlist position, cached briefly.

        The index is looked up fresh every time it is needed and never stored
        as identity. It shifts whenever a song is added, which is the bug in
        the old plugin.
        """
        cached = self._playlist_cache.get(show.playlist_name)
        now = self._clock()
        if cached and now - cached[0] < self.config.playlist_cache_seconds:
            return cached[1]
        try:
            entries = self.adapter.get_playlist(show.playlist_name)
        except FppError as exc:
            log.warning("could not read playlist %r: %s", show.playlist_name, exc)
            return cached[1] if cached else {}
        index = {slugify(e.sequence_name): e.index for e in entries if e.enabled}
        self._playlist_cache[show.playlist_name] = (now, index)
        return index

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

        show = self.resolve_show(status)
        if show is None:
            self.state.last_error = (
                f"FPP is playing {status.playlist_name!r}, which matches no "
                f"configured show. Check shows.playlist_name.")
            return self.state
        self.state.show_id = show.show_id
        self.state.last_error = None

        if status.status == STATUS_IDLE:
            self.store.close_open_round(show.show_id)
            self.state.round_id = None
            return self.state

        if status.status != STATUS_PLAYING or not status.sequence_name:
            return self.state

        key = self.store.resolve_key(slugify(status.sequence_name))
        previous_id = self.state.round_id
        rnd = self.store.ensure_round(show.show_id, key)
        self.state.round_id = rnd.round_id

        if rnd.round_id != previous_id and previous_id is not None:
            self._late_handover(show, previous_id, now_playing=key)

        self._maybe_hand_over(show, rnd, status)
        return self.state

    # ------------------------------------------------------------- handover
    def _maybe_hand_over(self, show, rnd, status: ShowStatus) -> None:
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
        self._play(show, winner)

    def _late_handover(self, show, round_id: int, now_playing: str) -> None:
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
        self._play(show, winner)

    def _play(self, show, song_key: str) -> None:
        index = self.playlist_index(show).get(song_key)
        if index is None:
            log.warning("winner %r is not in playlist %r; leaving the show "
                        "alone", song_key, show.playlist_name)
            return
        try:
            self.adapter.start_at_item(show.playlist_name, index)
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
