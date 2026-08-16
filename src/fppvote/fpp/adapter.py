"""
Every single call into FPP goes through this module. Nothing else in the
codebase may talk to FPP directly.

Why: FPP 10 ships mid-August 2026, and major FPP releases have a track record
of moving things. When that happens we want one file to fix, not a search
across the whole service. The old plugin scattered FPP calls inline through
both its Python and its PHP, which is part of why it is hard to repair.

Three implementations are planned:

  HttpFppAdapter  - polls /api/fppd/status. Always works. The safe default.
  MqttFppAdapter  - subscribes to falcon/player/<host>/... Event driven, so no
                    1 Hz busy loop and a faster reaction on song change.
                    MQTT is optional in FPP settings, so this can never be
                    the only path.
  FakeFppAdapter  - replays a scripted show from a fixture. Lets the whole
                    application be built and tested with no Raspberry Pi.

Contract tests live in tests/test_adapter.py and run the same assertions
against every implementation, including captured real FPP responses. Upgrading
FPP and running the suite tells you in seconds whether anything moved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PlaylistEntry:
    """One item in an FPP playlist."""
    sequence_name: str
    media_name: str | None
    duration_seconds: float
    enabled: bool
    index: int                 # position in the playlist; runtime use only


@dataclass(frozen=True)
class ShowStatus:
    """A snapshot of what FPP is doing right now."""
    status: str                # 'idle' | 'playing' | 'stopping' | 'unknown'
    playlist_name: str | None
    sequence_name: str | None  # maps to song_key via the catalog
    media_title: str | None
    media_artist: str | None   # FPP reads ID3 tags; may fill catalog gaps
    seconds_elapsed: float
    seconds_remaining: float
    seconds_total: float

    @property
    def is_playing(self) -> bool:
        return self.status == "playing"


@runtime_checkable
class FppAdapter(Protocol):
    """The complete surface we depend on. Keep it small."""

    def version(self) -> str:
        """FPP version string, e.g. '9.5'. Used to warn on untested releases."""
        ...

    def get_status(self) -> ShowStatus:
        """Current playback state. Must never raise; return status='unknown'
        if FPP is unreachable so the service degrades instead of dying."""
        ...

    def list_playlists(self) -> list[str]:
        ...

    def get_playlist(self, name: str) -> list[PlaylistEntry]:
        """Read a playlist. Source of truth for which songs are live."""
        ...

    def start_at_item(self, playlist: str, index: int) -> None:
        """Jump to a playlist position — how a vote result gets played."""
        ...


# Versions we have actually tested against. Do not widen this without running
# the contract tests; the old plugin declared open-ended support for versions
# that did not exist yet, and shipped broken to people who trusted it.
TESTED_FPP_VERSIONS = ("8.0", "9.0", "9.5")
