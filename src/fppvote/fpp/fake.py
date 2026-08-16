"""
FakeFppAdapter — a scripted show with no Raspberry Pi anywhere.

This is what makes stages 4 to 6 buildable on a laptop. It is not a stub that
returns canned values: it holds a real playlist, advances through it, wraps at
the end, and honours start_at_item, so the service exercises the same code path
it will use against a real FPP.

Time is explicit. tick() advances the show rather than the wall clock, which
means a test can play a whole 65-song night in a millisecond and get exactly
the same sequence of rounds every run. Nothing here sleeps.

go_offline() simulates FPP being unreachable. That path matters more than the
happy one — 'the playlist should just keep playing' is the actual feature, and
this is how it gets tested.
"""
from __future__ import annotations

from .adapter import (
    STATUS_IDLE, STATUS_PLAYING, UNREACHABLE, FppError, PlaylistEntry, ShowStatus,
)


class FakeFppAdapter:
    """A playable in-memory FPP."""

    def __init__(self, playlists: dict[str, list[PlaylistEntry]] | None = None,
                 *, version: str = "9.5", playing: str | None = None):
        self.playlists = playlists or {}
        self._version = version
        self.offline = False
        self.commands: list[tuple[str, int]] = []   # every start_at_item, for asserts

        self.playlist_name: str | None = None
        self.position = 0            # 0-based index into the playlist
        self.elapsed = 0.0
        self.status = STATUS_IDLE

        if playing:
            self.start_at_item(playing, 1)

    # ------------------------------------------------------- test controls
    def go_offline(self) -> None:
        """FPP stops answering. get_status degrades; everything else raises."""
        self.offline = True

    def go_online(self) -> None:
        self.offline = False

    def tick(self, seconds: float) -> None:
        """Advance the show, rolling into the next song as often as needed."""
        if self.status != STATUS_PLAYING:
            return
        remaining = float(seconds)
        while remaining > 0:
            entry = self.current_entry()
            if entry is None:
                return
            left = entry.duration_seconds - self.elapsed
            if remaining < left:
                self.elapsed += remaining
                return
            remaining -= left
            self._advance()

    def play_to_end_of_song(self) -> None:
        """Finish the current song exactly, landing on the next one."""
        entry = self.current_entry()
        if entry is not None:
            self.tick(entry.duration_seconds - self.elapsed)

    def stop(self) -> None:
        self.status = STATUS_IDLE
        self.elapsed = 0.0

    # -------------------------------------------------------------- internals
    def _entries(self) -> list[PlaylistEntry]:
        return [e for e in self.playlists.get(self.playlist_name or "", [])
                if e.enabled]

    def current_entry(self) -> PlaylistEntry | None:
        entries = self._entries()
        if not entries or not 0 <= self.position < len(entries):
            return None
        return entries[self.position]

    def _advance(self) -> None:
        entries = self._entries()
        self.elapsed = 0.0
        # Wraps rather than stopping: an FPP show playlist repeats all evening,
        # and a fake that halted after one pass would never exercise the
        # cooldown window rolling over.
        self.position = (self.position + 1) % len(entries) if entries else 0

    # --------------------------------------------------------------- adapter
    def version(self) -> str:
        if self.offline:
            raise FppError("FPP is offline (fake)")
        return self._version

    def get_status(self) -> ShowStatus:
        if self.offline:
            return UNREACHABLE
        entry = self.current_entry()
        if entry is None or self.status != STATUS_PLAYING:
            return ShowStatus(status=STATUS_IDLE, playlist_name=self.playlist_name,
                              sequence_name=None, media_title=None,
                              media_artist=None, seconds_elapsed=0.0,
                              seconds_remaining=0.0, seconds_total=0.0)
        return ShowStatus(
            status=STATUS_PLAYING,
            playlist_name=self.playlist_name,
            sequence_name=entry.sequence_name,
            media_title=entry.media_name,
            media_artist=None,
            seconds_elapsed=self.elapsed,
            seconds_remaining=max(0.0, entry.duration_seconds - self.elapsed),
            seconds_total=entry.duration_seconds,
        )

    def list_playlists(self) -> list[str]:
        if self.offline:
            raise FppError("FPP is offline (fake)")
        return sorted(self.playlists)

    def get_playlist(self, name: str) -> list[PlaylistEntry]:
        if self.offline:
            raise FppError("FPP is offline (fake)")
        if name not in self.playlists:
            raise FppError(f"no such playlist: {name!r}")
        return list(self.playlists[name])

    def start_at_item(self, playlist: str, index: int) -> None:
        if self.offline:
            raise FppError("FPP is offline (fake)")
        if playlist not in self.playlists:
            raise FppError(f"no such playlist: {playlist!r}")
        self.commands.append((playlist, index))
        self.playlist_name = playlist
        # index is 1-based, matching PlaylistEntry.index and FPP itself.
        self.position = max(0, int(index) - 1)
        self.elapsed = 0.0
        self.status = STATUS_PLAYING


def from_catalog(playlists: dict[str, list[tuple]], **kwargs) -> FakeFppAdapter:
    """Build a fake from (sequence, media, 'mm:ss') tuples — the same shape the
    playlist fixtures already use, so a test show is one call away."""
    def seconds(text):
        total = 0.0
        for part in str(text).split(":"):
            total = total * 60 + float(part or 0)
        return total

    built = {
        name: [PlaylistEntry(sequence_name=seq, media_name=media,
                             duration_seconds=seconds(length), enabled=True,
                             index=i)
               for i, (seq, media, length) in enumerate(entries, start=1)]
        for name, entries in playlists.items()
    }
    return FakeFppAdapter(built, **kwargs)
