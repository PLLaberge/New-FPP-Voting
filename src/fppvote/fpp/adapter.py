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


class FppError(RuntimeError):
    """FPP could not be reached, or answered with something unusable.

    get_status() never raises this — it degrades to status='unknown' instead,
    because a status read failing must not stop the show. The other calls do
    raise it: they are triggered by an admin action or a vote result, where
    silently doing nothing would be worse than an error the caller can log.
    """


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

# Status values a ShowStatus may carry. 'unknown' means we could not reach FPP,
# which is different from knowing it is idle — the service must not close an
# open round on 'unknown', or a momentary network blip discards live votes.
STATUS_IDLE = "idle"
STATUS_PLAYING = "playing"
STATUS_STOPPING = "stopping"
STATUS_UNKNOWN = "unknown"

UNREACHABLE = ShowStatus(
    status=STATUS_UNKNOWN, playlist_name=None, sequence_name=None,
    media_title=None, media_artist=None,
    seconds_elapsed=0.0, seconds_remaining=0.0, seconds_total=0.0,
)


def major_minor(version: str) -> str:
    """'9.5.1' -> '9.5'. FPP ships patch releases that change nothing we use."""
    parts = str(version).strip().lstrip("vV").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")


def untested_version_warning(version: str | None) -> str | None:
    """A sentence for the log and the health endpoint, or None if it is fine.

    A warning, never a refusal: telling Paulin his FPP is untested is useful,
    but refusing to run on the strength of a version string would take the show
    down over a number. FPP 10 ships mid-August 2026 and this is how we find
    out — followed by running the contract tests, which is the actual check.
    """
    if not version:
        return ("FPP version unknown — cannot tell whether this release is "
                "one the contract tests have run against.")
    if major_minor(version) in TESTED_FPP_VERSIONS:
        return None
    return (f"FPP {version} has not been tested against this plugin "
            f"(tested: {', '.join(TESTED_FPP_VERSIONS)}). Run the contract "
            f"tests in tests/test_adapter.py against a capture from this Pi "
            f"before trusting it for a show.")
