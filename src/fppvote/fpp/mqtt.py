"""
MqttFppAdapter — event-driven status, layered ON TOP of HTTP rather than
instead of it.

MQTT is optional in FPP's settings, so it can never be the only path (decision
6). This adapter therefore does not replace HttpFppAdapter, it wraps one:

  * get_status() returns the MQTT view while messages are arriving, and falls
    straight back to HTTP polling the moment they stop. No reconnect logic to
    get wrong, no state to rebuild — if MQTT goes quiet the show does not
    notice.
  * list_playlists / get_playlist / version go to HTTP always. MQTT publishes
    what is playing, not what could play.
  * start_at_item prefers MQTT's set/playlist/<name>/startPosition and falls
    back to HTTP if publishing fails, because a vote result that never reaches
    FPP is the one failure a viewer actually sees.

'Falling back' is decided by a timestamp, not by a connection callback:
`stale_after` seconds without a message and we stop trusting the cache. A
broker that accepts a connection and then says nothing is a real failure mode,
and it looks identical to a healthy one from the connection's point of view.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from .adapter import (
    STATUS_IDLE, STATUS_PLAYING, STATUS_STOPPING, FppError, PlaylistEntry,
    ShowStatus,
)
from .http import HttpFppAdapter, _seconds

log = logging.getLogger(__name__)

# FPP publishes under falcon/player/<hostname>/. Everything we read is a suffix
# of that prefix.
DEFAULT_PREFIX = "falcon/player"


class MqttFppAdapter:
    """Status over MQTT, everything else over HTTP."""

    def __init__(self, http: HttpFppAdapter, *, host: str = "localhost",
                 port: int = 1883, hostname: str = "FPP",
                 prefix: str = DEFAULT_PREFIX, stale_after: float = 15.0,
                 client=None, clock=time.monotonic):
        self.http = http
        self.host = host
        self.port = port
        self.topic_root = f"{prefix}/{hostname}"
        self.stale_after = stale_after
        self._clock = clock
        self._client = client
        self._lock = threading.Lock()

        self._last_message_at: float | None = None
        self._sequence: str | None = None
        self._playlist: str | None = None
        self._title: str | None = None
        self._artist: str | None = None
        self._status: str | None = None
        self._elapsed = 0.0
        self._remaining = 0.0
        self._total = 0.0

    # ------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        """Subscribe in the background. Failure here is survivable by design —
        we log it and every call quietly uses HTTP instead."""
        if self._client is None:
            try:
                import paho.mqtt.client as paho
            except ImportError:
                log.warning("paho-mqtt not installed; using HTTP polling only")
                return
            self._client = paho.Client()

        try:
            self._client.on_message = self._on_message
            self._client.connect(self.host, self.port)
            self._client.subscribe(f"{self.topic_root}/#")
            self._client.loop_start()
            log.info("MQTT subscribed to %s/#", self.topic_root)
        except Exception as exc:                      # noqa: BLE001
            log.warning("MQTT unavailable (%s); using HTTP polling only", exc)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:                         # noqa: BLE001
                pass

    # -------------------------------------------------------------- ingest
    def _on_message(self, _client, _userdata, message) -> None:
        payload = message.payload
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        self.handle(str(message.topic), payload)

    def handle(self, topic: str, payload: str) -> None:
        """Route one message. Public so the contract tests can feed messages in
        without a broker — the parsing is the part worth testing."""
        suffix = topic[len(self.topic_root):].lstrip("/") \
            if topic.startswith(self.topic_root) else topic
        payload = (payload or "").strip()

        with self._lock:
            self._last_message_at = self._clock()

            if suffix == "playlist/sequence/status":
                self._sequence = payload or None
            elif suffix == "playlist/media/title":
                self._title = payload or None
            elif suffix == "playlist/media/artist":
                self._artist = payload or None
            elif suffix in ("playlist/name/status", "playlist/name"):
                self._playlist = payload or None
            elif suffix == "status":
                self._apply_status_word(payload)
            elif suffix == "playlist_details":
                self._apply_details(payload)

    def _apply_status_word(self, payload: str) -> None:
        word = payload.lower()
        if "play" in word:
            self._status = STATUS_PLAYING
        elif "stopping" in word:
            self._status = STATUS_STOPPING
        elif "idle" in word or "stop" in word:
            self._status = STATUS_IDLE

    def _apply_details(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        self._remaining = _seconds(data.get("secondsRemaining",
                                            data.get("seconds_remaining")))
        self._elapsed = _seconds(data.get("secondsElapsed",
                                          data.get("secondsPlayed", 0)))
        self._total = _seconds(data.get("secondsTotal", 0)) or \
            (self._elapsed + self._remaining)
        for key in ("playlist", "name"):
            if data.get(key):
                self._playlist = str(data[key])
                break
        if data.get("sequenceName"):
            self._sequence = str(data["sequenceName"])
        # A details message with a song in it means something is playing, even
        # if the status topic has not been seen this session.
        if self._status is None and self._sequence:
            self._status = STATUS_PLAYING

    # --------------------------------------------------------------- state
    @property
    def fresh(self) -> bool:
        """Is the MQTT view recent enough to trust?"""
        with self._lock:
            if self._last_message_at is None or self._sequence is None:
                return False
            return (self._clock() - self._last_message_at) <= self.stale_after

    # -------------------------------------------------------------- adapter
    def version(self) -> str:
        return self.http.version()

    def get_status(self) -> ShowStatus:
        """Never raises. MQTT when fresh, HTTP when not."""
        if not self.fresh:
            return self.http.get_status()
        with self._lock:
            return ShowStatus(
                status=self._status or STATUS_PLAYING,
                playlist_name=self._playlist,
                sequence_name=self._sequence,
                media_title=self._title,
                media_artist=self._artist,
                seconds_elapsed=self._elapsed,
                seconds_remaining=self._remaining,
                seconds_total=self._total or (self._elapsed + self._remaining),
            )

    def list_playlists(self) -> list[str]:
        return self.http.list_playlists()

    def get_playlist(self, name: str) -> list[PlaylistEntry]:
        return self.http.get_playlist(name)

    def start_at_item(self, playlist: str, index: int) -> None:
        """Publish, then fall back to HTTP. A vote result that never reaches
        FPP is the one failure a viewer actually notices."""
        if self._client is not None:
            try:
                topic = f"{self.topic_root}/set/playlist/{playlist}/startPosition"
                result = self._client.publish(topic, str(int(index)))
                rc = getattr(result, "rc", 0)
                if rc == 0:
                    return
                log.warning("MQTT publish returned rc=%s; falling back to HTTP", rc)
            except Exception as exc:                  # noqa: BLE001
                log.warning("MQTT publish failed (%s); falling back to HTTP", exc)
        try:
            self.http.start_at_item(playlist, index)
        except FppError:
            raise
