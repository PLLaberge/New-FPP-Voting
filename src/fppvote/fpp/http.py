"""
HttpFppAdapter — polls FPP's REST API. Always available, so it is the default
and the fallback under MQTT.

Field names are read DEFENSIVELY, through _first(), which tries several keys
and takes whichever exists. That is not sloppiness, it is the point of this
file: FPP has renamed fields across releases, and a KeyError at 7pm on a show
night is the failure this whole module exists to prevent. An unrecognised field
degrades to None and the show carries on.

The precise key names below come from FPP's documented API and are the most
likely thing in this project to be wrong. Run scripts/capture_fpp.py against
the real Pi and drop the result into tests/fixtures/captured/ — the contract
tests then check this parsing against what FPP actually said, which is the only
verification that counts.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from .adapter import (
    STATUS_IDLE, STATUS_PLAYING, STATUS_STOPPING, STATUS_UNKNOWN, UNREACHABLE,
    FppError, PlaylistEntry, ShowStatus,
)

log = logging.getLogger(__name__)

# FPP's numeric status codes. 4 is 'paused', which we report as playing on
# purpose: the current song is still the current song, and reporting idle would
# make the service close the round and discard everyone's votes over a pause.
_NUMERIC_STATUS = {
    0: STATUS_IDLE,
    1: STATUS_PLAYING,
    2: STATUS_STOPPING,
    3: STATUS_STOPPING,
    4: STATUS_PLAYING,
}


def _first(payload: dict, *names, default=None):
    """First key present with a non-empty value. Order is preference order."""
    for name in names:
        if name in payload:
            value = payload[name]
            if value not in (None, "", []):
                return value
    return default


def _seconds(value) -> float:
    """Accept 214, '214.5', or FPP's 'mm:ss' / 'hh:mm:ss' strings."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        if ":" in text:
            total = 0.0
            for part in text.split(":"):
                total = total * 60 + float(part or 0)
            return total
        return float(text)
    except ValueError:
        return 0.0


def parse_status(payload: dict) -> ShowStatus:
    """Turn an /api/fppd/status body into a ShowStatus. Pure, so the contract
    tests can run it straight against a captured response."""
    raw = _first(payload, "status", default=None)
    name = str(_first(payload, "status_name", default="") or "").lower()

    if isinstance(raw, int) and raw in _NUMERIC_STATUS:
        status = _NUMERIC_STATUS[raw]
    elif "play" in name:
        status = STATUS_PLAYING
    elif "stopping" in name:
        status = STATUS_STOPPING
    elif "paus" in name:
        status = STATUS_PLAYING          # see _NUMERIC_STATUS
    elif "idle" in name or "stop" in name:
        status = STATUS_IDLE
    else:
        status = STATUS_UNKNOWN

    playlist = _first(payload, "current_playlist_name", "current_playlist")
    if isinstance(playlist, dict):
        playlist = _first(playlist, "playlist", "name")

    elapsed = _seconds(_first(payload, "seconds_played", "seconds_elapsed",
                              "time_elapsed"))
    remaining = _seconds(_first(payload, "seconds_remaining", "time_remaining"))

    return ShowStatus(
        status=status,
        playlist_name=playlist or None,
        sequence_name=_first(payload, "current_sequence", "sequence_filename",
                             "current_sequence_filename"),
        media_title=_first(payload, "current_song_title", "media_title", "title"),
        media_artist=_first(payload, "current_song_artist", "media_artist",
                            "artist"),
        seconds_elapsed=elapsed,
        seconds_remaining=remaining,
        seconds_total=elapsed + remaining,
    )


def parse_playlist(payload) -> list[PlaylistEntry]:
    """Turn an /api/playlist/<name> body into entries.

    Disabled entries are kept, not dropped: `enabled` is reported so the
    reconciler can deactivate a song rather than have it silently vanish, which
    would take its categories and vote history with it.
    """
    if isinstance(payload, dict):
        items = _first(payload, "mainPlaylist", "playlist", "entries", default=[])
    else:
        items = payload or []

    entries = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        sequence = _first(item, "sequenceName", "sequence_name", "sequence")
        if not sequence:
            continue          # a media-only entry is not something to vote for
        entries.append(PlaylistEntry(
            sequence_name=sequence,
            media_name=_first(item, "mediaName", "media_name", "media"),
            duration_seconds=_seconds(_first(item, "duration", "durationSeconds",
                                             "length")),
            enabled=bool(_first(item, "enabled", default=1)),
            index=index,
        ))
    return entries


class HttpFppAdapter:
    """Talks to FPP over its REST API."""

    def __init__(self, base_url: str = "http://localhost", *,
                 client: httpx.Client | None = None, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Injected for tests (httpx.MockTransport) and reused otherwise so a
        # 1 Hz poll is not opening a fresh TCP connection every second.
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- calls
    def _get(self, path: str):
        try:
            response = self._client.get(f"{self.base_url}{path}",
                                        timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:                      # noqa: BLE001
            raise FppError(f"GET {path} failed: {exc}") from exc

    def version(self) -> str:
        for path, keys in (("/api/system/info", ("Version", "version",
                                                 "fppd_version")),
                           ("/api/fppd/status", ("version", "fppd_version"))):
            try:
                payload = self._get(path)
            except FppError:
                continue
            if isinstance(payload, dict):
                found = _first(payload, *keys)
                if found:
                    return str(found)
        raise FppError("FPP did not report a version")

    def get_status(self) -> ShowStatus:
        """Never raises — an unreachable FPP degrades to status='unknown'.

        This is the one call that runs constantly, and the whole reliability
        story rests on it: the playlist keeps playing whether or not we can see
        it, so a failure here is logged and shrugged off rather than raised.
        """
        try:
            payload = self._get("/api/fppd/status")
        except FppError as exc:
            log.warning("FPP status unavailable, degrading to unknown: %s", exc)
            return UNREACHABLE
        try:
            return parse_status(payload if isinstance(payload, dict) else {})
        except Exception:                             # noqa: BLE001
            log.exception("FPP status could not be parsed; payload keys: %s",
                          sorted(payload) if isinstance(payload, dict) else type(payload))
            return UNREACHABLE

    def list_playlists(self) -> list[str]:
        payload = self._get("/api/playlists")
        names = []
        for item in payload or []:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                found = _first(item, "name", "playlist")
                if found:
                    names.append(str(found))
        return names

    def get_playlist(self, name: str) -> list[PlaylistEntry]:
        # Playlist names contain spaces and apostrophes ("New Year's 2026").
        return parse_playlist(self._get(f"/api/playlist/{quote(name, safe='')}"))

    def start_at_item(self, playlist: str, index: int) -> None:
        """Jump to a playlist position — how a vote result gets played.

        The only write path in the whole plugin, and the least verified thing
        in this file: FPP's command names are stable but not something we can
        confirm without a Pi. If votes tally correctly but nothing ever changes
        song, start here.
        """
        try:
            response = self._client.post(
                f"{self.base_url}/api/command",
                json={"command": "Start Playlist At Item",
                      "args": [playlist, int(index)]},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:                      # noqa: BLE001
            raise FppError(
                f"could not start {playlist!r} at item {index}: {exc}") from exc
