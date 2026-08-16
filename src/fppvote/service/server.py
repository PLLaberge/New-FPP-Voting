"""
The FastAPI service.

Endpoint handlers are sync `def`, so FastAPI runs them in its threadpool and
the store's thread-local connections do exactly the job they were built for.
The follower is an async task that offloads each pass with asyncio.to_thread —
blocking database and HTTP work never touches the event loop, and broadcasting
stays in async code where the WebSockets live.

Voter identity: an opaque token the browser holds. The server issues one on
first contact and sets it as a cookie; the page may also send it as
X-Voter-Token, which is what lets it live in localStorage where a viewer can
see and clear it. Only an HMAC of it is ever stored. Never an IP — behind
Cloudflare Tunnel every viewer shares the edge address.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Body, Cookie, FastAPI, Header, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..catalog.parser import slugify
from ..db import Store
from ..fpp import FakeFppAdapter, HttpFppAdapter, MqttFppAdapter, PlaylistEntry
from .config import Config
from .follower import Follower

log = logging.getLogger(__name__)

VOTER_COOKIE = "fppvote_voter"
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


def build_adapter(config: Config, store: Store):
    """Real FPP, or a fake seeded from the database so a laptop can run a show."""
    if config.fake_fpp:
        playlists = {}
        for show in store.list_shows(active_only=True):
            entries = [
                PlaylistEntry(sequence_name=song.key + ".fseq", media_name=song.title,
                              duration_seconds=180.0, enabled=True, index=i)
                for i, song in enumerate(store.list_show_songs(show.show_id), start=1)
            ]
            if entries:
                playlists[show.playlist_name] = entries
        fake = FakeFppAdapter(playlists)
        first = next(iter(playlists), None)
        if first:
            fake.start_at_item(first, 1)
        return fake

    http = HttpFppAdapter(config.fpp_url)
    if not config.mqtt_host:
        return http
    mqtt = MqttFppAdapter(http, host=config.mqtt_host, port=config.mqtt_port,
                          hostname=config.mqtt_hostname)
    mqtt.connect()
    return mqtt


class Hub:
    """Connected WebSockets, and the last state pushed to them."""

    def __init__(self):
        self.connections: dict[WebSocket, str] = {}     # socket -> voter_hash
        self.shared: dict | None = None

    async def join(self, socket: WebSocket, voter_hash: str):
        self.connections[socket] = voter_hash
        if self.shared is not None:
            await self._send(socket, voter_hash)

    def leave(self, socket: WebSocket):
        self.connections.pop(socket, None)

    async def _send(self, socket: WebSocket, voter_hash: str):
        payload = dict(self.shared or {})
        payload["you"] = personal_block(payload, voter_hash)
        with suppress(Exception):
            await socket.send_json(payload)

    async def broadcast(self, shared: dict):
        self.shared = shared
        for socket, voter_hash in list(self.connections.items()):
            try:
                await self._send(socket, voter_hash)
            except Exception:                            # noqa: BLE001
                self.leave(socket)


def personal_block(shared: dict, voter_hash: str) -> dict:
    """The per-viewer slice. Computed from the shared payload's precomputed
    per-voter map so a broadcast is one query, not one per connection."""
    selection = (shared.get("_selections") or {}).get(voter_hash, [])
    allowance = (shared.get("show") or {}).get("votes_per_round", 3)
    return {"selection": selection,
            "votes_used": len(selection),
            "votes_left": max(0, allowance - len(selection))}


def build_state(store: Store, follower: Follower) -> dict:
    """Everything the voter page needs, in one payload. Blocking; call in a thread."""
    state = follower.state
    show = store.get_show(state.show_id) if state.show_id else None
    if show is None:
        return {"show": None, "fpp": _fpp_block(state), "songs": [],
                "categories": [], "now_playing": None, "round_id": None,
                "_selections": {}}

    tally = store.tally(state.round_id) if state.round_id else {}
    locked = store.locked_keys(show.show_id)
    songs = [
        {"key": s.key, "title": s.title, "artist": s.artist, "year": s.year,
         "categories": s.categories, "index": s.playlist_index,
         "votes": tally.get(s.key, 0), "locked": s.key in locked}
        for s in store.list_show_songs(show.show_id)
    ]

    status = state.status
    playing_key = None
    if status and status.sequence_name:
        playing_key = store.resolve_key(slugify(status.sequence_name))
    playing_song = store.get_song(playing_key) if playing_key else None

    selections: dict[str, list[str]] = {}
    if state.round_id:
        for row in store.db.connection.execute(
                "SELECT voter_hash, song_key FROM votes WHERE round_id = ?",
                (state.round_id,)):
            selections.setdefault(row["voter_hash"], []).append(row["song_key"])

    return {
        "show": {"id": show.show_id, "name": show.name, "tagline": show.tagline,
                 "note": show.note, "theme": show.theme,
                 "votes_per_round": show.votes_per_round,
                 "cooldown_songs": show.cooldown_songs},
        "categories": store.list_categories(show.show_id, non_empty=True),
        "now_playing": {
            "key": playing_key,
            "title": playing_song.display_title if playing_song else
                     (status.media_title if status else None),
            "artist": (playing_song.artist if playing_song else None)
                      or (status.media_artist if status else None),
            "seconds_elapsed": status.seconds_elapsed if status else 0,
            "seconds_remaining": status.seconds_remaining if status else 0,
            "seconds_total": status.seconds_total if status else 0,
        } if status and status.is_playing else None,
        "round_id": state.round_id,
        "songs": songs,
        "fpp": _fpp_block(state),
        "_selections": selections,
    }


def _fpp_block(state) -> dict:
    return {"reachable": state.fpp_reachable, "version": state.fpp_version,
            "warning": state.version_warning, "error": state.last_error}


def create_app(config: Config | None = None, *, store: Store | None = None,
               adapter=None) -> FastAPI:
    config = config or Config.from_env()
    store = store or Store.open(config.db_path)
    adapter = adapter if adapter is not None else build_adapter(config, store)
    follower = Follower(store, adapter, config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(follower.refresh_version)
        task = asyncio.create_task(_run(app))
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(app: FastAPI):
        """Poll, then push. One pass per config.poll_seconds."""
        while True:
            try:
                await asyncio.to_thread(follower.tick)
                shared = await asyncio.to_thread(build_state, store, follower)
                await app.state.hub.broadcast(shared)
            except asyncio.CancelledError:
                raise
            except Exception:                            # noqa: BLE001
                log.exception("follower loop error")     # and keep going
            await asyncio.sleep(config.poll_seconds)

    app = FastAPI(title="FPP Voting", lifespan=lifespan)
    app.state.config = config
    app.state.store = store
    app.state.adapter = adapter
    app.state.follower = follower
    app.state.hub = Hub()

    # ------------------------------------------------------------- identity
    def voter_token(response: Response | None,
                    header: str | None, cookie: str | None) -> str:
        """The browser's token, issued on first contact.

        Header first so the page can keep it in localStorage, where a viewer
        can actually see and clear it; the cookie is the fallback that makes it
        work before any JavaScript has run.
        """
        token = (header or cookie or "").strip()
        if not token:
            token = secrets.token_urlsafe(24)
            if response is not None:
                response.set_cookie(VOTER_COOKIE, token, max_age=60 * 60 * 24 * 90,
                                    httponly=False, samesite="lax")
        return token

    # ------------------------------------------------------------- endpoints
    @app.get("/api/state")
    def get_state(response: Response,
                  x_voter_token: str | None = Header(default=None),
                  fppvote_voter: str | None = Cookie(default=None)):
        token = voter_token(response, x_voter_token, fppvote_voter)
        payload = build_state(store, follower)
        voter = store.voter_hash(token)
        payload["you"] = personal_block(payload, voter)
        payload["you"]["token"] = token
        payload.pop("_selections", None)
        return payload

    @app.post("/api/vote")
    def post_vote(response: Response, body: dict = Body(...),
                  x_voter_token: str | None = Header(default=None),
                  fppvote_voter: str | None = Cookie(default=None)):
        """Cast or retract one vote.

        `retract` is explicit rather than cast_vote toggling on a repeat tap: a
        store primitive whose meaning flips with state is how you end up
        deleting a vote you meant to add.
        """
        token = voter_token(response, x_voter_token, fppvote_voter)
        voter = store.voter_hash(token)
        song_key = str(body.get("song_key") or "")
        round_id = follower.state.round_id

        if round_id is None:
            return {"outcome": "no_round", "message":
                    "Nothing is playing right now — voting opens with the show."}

        if body.get("retract"):
            removed = store.retract_vote(round_id, voter, song_key)
            outcome = "retracted" if removed else "not_voted"
        else:
            result = store.cast_vote(round_id, voter, song_key)
            outcome = result.outcome

        payload = build_state(store, follower)
        payload["you"] = personal_block(payload, voter)
        payload.pop("_selections", None)
        payload["outcome"] = outcome
        return payload

    @app.get("/api/health")
    def health():
        """Boring and observable, per CLAUDE.md. This is what you curl at 6pm
        on the first cold night to find out whether it is the plugin or FPP."""
        state = follower.state
        show = store.get_show(state.show_id) if state.show_id else None
        return {
            "ok": state.fpp_reachable,
            "fpp": _fpp_block(state),
            "show": show.show_id if show else None,
            "round_id": state.round_id,
            "playing": state.status.sequence_name if state.status else None,
            "status": state.status.status if state.status else "unknown",
            "votes_this_round": sum(store.tally(state.round_id).values())
                                if state.round_id else 0,
            "websockets": len(app.state.hub.connections),
        }

    @app.websocket("/ws")
    async def websocket(socket: WebSocket, x_voter_token: str | None = Header(default=None)):
        await socket.accept()
        token = (x_voter_token or socket.cookies.get(VOTER_COOKIE) or "").strip() \
            or secrets.token_urlsafe(24)
        voter = await asyncio.to_thread(store.voter_hash, token)
        await app.state.hub.join(socket, voter)
        try:
            while True:
                await socket.receive_text()      # kept open; page sends heartbeats
        except WebSocketDisconnect:
            pass
        finally:
            app.state.hub.leave(socket)

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        def index():
            return FileResponse(STATIC / "vote.html")

    return app
