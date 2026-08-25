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
import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import (Body, Cookie, Depends, FastAPI, Header, HTTPException,
                     Query, Response, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..catalog.parser import parse_playlist, slugify
from ..db import Store
from ..fpp import FakeFppAdapter, FppError, HttpFppAdapter, MqttFppAdapter, PlaylistEntry
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
        # _selections maps every voter's hash to what they picked. It exists so
        # a broadcast is one query rather than one per connection, and it must
        # never leave the server: sending it would hand every viewer everyone
        # else's votes.
        payload.pop("_selections", None)
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
    voting_enabled = store.voting_enabled()
    show = store.get_show(state.show_id) if state.show_id else None
    if show is None:
        return {"show": None, "fpp": _fpp_block(state), "songs": [],
                "categories": [], "now_playing": None, "round_id": None,
                "voting_enabled": voting_enabled, "_selections": {}}

    tally = store.tally(state.round_id) if state.round_id else {}
    locked = store.locked_keys(show.show_id)
    # last_played goes to the page so its "Top" ordering breaks ties the same
    # way winner() does. Without it the row at the top of the list can differ
    # from the song that actually plays next, which reads as the vote lying.
    history = store.last_played_round(show.show_id)
    songs = [
        {"key": s.key, "title": s.title, "artist": s.artist, "year": s.year,
         "categories": s.categories, "index": s.playlist_index,
         "votes": tally.get(s.key, 0), "locked": s.key in locked,
         "last_played": history.get(s.key, -1)}
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
        "voting_enabled": voting_enabled,
        "_selections": selections,
    }


def _fpp_block(state) -> dict:
    return {"reachable": state.fpp_reachable, "version": state.fpp_version,
            "warning": state.version_warning, "error": state.last_error}


# ------------------------------------------------------------------- admin
def _admin_show_block(store: Store, show) -> dict:
    """A show as the admin page wants it: settings plus enough of a summary
    to show whether it needs attention, without shipping every song."""
    songs = store.list_show_songs(show.show_id, include_inactive=True)
    return {
        "id": show.show_id, "name": show.name, "playlist_name": show.playlist_name,
        "tagline": show.tagline, "note": show.note, "theme": show.theme,
        "votes_per_round": show.votes_per_round, "cooldown_songs": show.cooldown_songs,
        "active": show.active,
        "categories": store.list_categories(show.show_id),
        # include_inactive=True: a deactivated song still carries a category
        # assignment, and the vocabulary editor needs to know that before
        # letting a chip be dropped — same population set_show_categories
        # itself scans for orphans.
        "category_counts": store.category_counts(show.show_id, include_inactive=True),
        "songs_total": sum(1 for s in songs if s.active),
        "needs_review": sum(1 for s in songs if s.active and s.needs_review),
    }


def _song_block(song) -> dict:
    """The song fields that are true regardless of show — see CLAUDE.md
    section 3. Shared by the per-show song listing and the plain song editor
    so the two never describe the same columns differently."""
    return {
        "key": song.key, "title": song.title, "display_override": song.display_override,
        "sequence_name": song.sequence_name, "media_name": song.media_name,
        "artist": song.artist, "year": song.year,
    }


def _admin_song_block(store: Store, membership) -> dict:
    """One song as the admin page edits it: the raw parsed fields plus the
    curated ones, so 'what the parser saw' and 'what a human overrode' are
    both visible rather than pre-blended into one title."""
    block = _song_block(store.get_song(membership.key))
    block.update(categories=membership.categories, active=membership.active,
                 playlist_index=membership.playlist_index, source=membership.source,
                 needs_review=membership.needs_review)
    return block


def _find_show_song(store: Store, show_id: str, key: str):
    """No single-row lookup exists in Store — list_show_songs is the only
    query, and reading ~100 rows to find one is cheap at this scale. Adding a
    second SQL path for one caller was not worth it."""
    for s in store.list_show_songs(show_id, include_inactive=True):
        if s.key == key:
            return s
    return None


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

    def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
        """Gate /api/admin/*. An empty admin_token (every laptop run, by
        default) leaves it open — there is nothing else listening. Once a
        token is set, a wrong or missing header is refused outright rather
        than warned about: unlike a bad FPP version, there is no safe
        degraded mode for "anyone with the tunnel URL can edit categories"."""
        if config.admin_token and x_admin_token != config.admin_token:
            raise HTTPException(status_code=401,
                                detail="missing or incorrect X-Admin-Token")

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

        if not store.voting_enabled():
            return {"outcome": "voting_stopped", "message":
                    "Sorry, no voting for songs at this time. Try again later."}

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
        shows = store.list_shows(active_only=True)
        return {
            "ok": state.fpp_reachable and bool(shows),
            "fpp": _fpp_block(state),
            "shows_configured": len(shows),
            "show": show.show_id if show else None,
            "round_id": state.round_id,
            "playing": state.status.sequence_name if state.status else None,
            "status": state.status.status if state.status else "unknown",
            "votes_this_round": sum(store.tally(state.round_id).values())
                                if state.round_id else 0,
            "websockets": len(app.state.hub.connections),
        }

    # --------------------------------------------------------- admin: shows
    @app.get("/api/admin/shows")
    def admin_list_shows(_: None = Depends(require_admin)):
        return [_admin_show_block(store, s) for s in store.list_shows(active_only=False)]

    @app.patch("/api/admin/shows/{show_id}")
    def admin_update_show(show_id: str, body: dict = Body(...),
                          _: None = Depends(require_admin)):
        if store.get_show(show_id) is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        try:
            store.update_show(show_id, **body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return _admin_show_block(store, store.get_show(show_id))

    # ----------------------------------------------------- admin: categories
    @app.get("/api/admin/shows/{show_id}/categories")
    def admin_get_categories(show_id: str, _: None = Depends(require_admin)):
        if store.get_show(show_id) is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        return {"categories": store.list_categories(show_id),
                "counts": store.category_counts(show_id, include_inactive=True)}

    @app.put("/api/admin/shows/{show_id}/categories")
    def admin_set_categories(show_id: str, body: dict = Body(...),
                             _: None = Depends(require_admin)):
        if store.get_show(show_id) is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        names = body.get("categories")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise HTTPException(400, "categories must be a list of strings")
        orphaned = store.set_show_categories(show_id, names)
        return {"categories": store.list_categories(show_id),
                "counts": store.category_counts(show_id, include_inactive=True),
                "orphaned": orphaned}

    # ---------------------------------------------------------- admin: songs
    @app.get("/api/admin/shows/{show_id}/songs")
    def admin_list_songs(show_id: str, _: None = Depends(require_admin)):
        if store.get_show(show_id) is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        songs = store.list_show_songs(show_id, include_inactive=True)
        return {"songs": [_admin_song_block(store, s) for s in songs]}

    @app.put("/api/admin/shows/{show_id}/songs/{song_key}/categories")
    def admin_set_song_categories(show_id: str, song_key: str, body: dict = Body(...),
                                  _: None = Depends(require_admin)):
        if store.get_show(show_id) is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        cats = body.get("categories")
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise HTTPException(400, "categories must be a list of strings")
        try:
            store.set_categories(show_id, song_key, cats)
        except ValueError as e:
            raise HTTPException(400, str(e))
        row = _find_show_song(store, show_id, song_key)
        if row is None:
            raise HTTPException(404, f"no such song in {show_id!r}: {song_key!r}")
        return {"song": _admin_song_block(store, row),
                "counts": store.category_counts(show_id, include_inactive=True)}

    @app.put("/api/admin/songs/{song_key}")
    def admin_update_song(song_key: str, body: dict = Body(...),
                          _: None = Depends(require_admin)):
        """Display-name override and curated artist/year — the fields the
        parser cannot get right or cannot know at all."""
        song = store.get_song(song_key)
        if song is None:
            raise HTTPException(404, f"no such song: {song_key!r}")
        if "display_override" in body:
            store.set_display_override(song_key, body["display_override"])
        meta: dict = {}
        if "artist" in body:
            meta["artist"] = body["artist"]
        if "year" in body:
            year = body["year"]
            if year is not None:
                try:
                    year = int(year)
                except (TypeError, ValueError):
                    raise HTTPException(400, "year must be an integer or null")
            meta["year"] = year
        if meta:
            store.set_song_metadata(song_key, **meta)
        return _song_block(store.get_song(song_key))

    # ------------------------------------------------------ admin: reconcile
    @app.post("/api/admin/shows/{show_id}/reconcile")
    def admin_reconcile(show_id: str, _: None = Depends(require_admin)):
        """Pull the show's playlist straight from FPP and run it through the
        same reconcile() every seed script uses: additive, idempotent, never
        touching a category a human already set. This is what picks up a
        song added or removed on the Pi without anyone running init_db.py."""
        show = store.get_show(show_id)
        if show is None:
            raise HTTPException(404, f"no such show: {show_id!r}")
        try:
            entries = adapter.get_playlist(show.playlist_name)
        except FppError as e:
            raise HTTPException(
                502, f"could not read playlist {show.playlist_name!r} from FPP: {e}")
        tuples = [(e.sequence_name, e.media_name, str(e.duration_seconds))
                 for e in entries if e.enabled]
        rows, issues = parse_playlist(tuples)
        report = store.sync_show(show_id, rows)
        return {
            "summary": report.summary(),
            "added": report.added, "reactivated": report.reactivated,
            "deactivated": report.deactivated, "unchanged": report.unchanged,
            "suggested": [{"key": k, "categories": c, "borrowed_from": b}
                         for k, c, b in report.suggested],
            "needs_review": report.needs_review,
            "parser_issues": issues,
        }

    # ---------------------------------------------------------- admin: tally
    @app.get("/api/admin/tally")
    def admin_get_tally(_: None = Depends(require_admin)):
        """Cumulative (since the last reset, or all-time) and today's vote
        counts, global across every show — see Store's own comment on why
        this is not split per show. Only songs with at least one vote appear;
        the full catalogue is what /api/admin/shows/{id}/songs is for."""
        cumulative = store.cumulative_tally()
        today = store.todays_tally()
        songs = []
        for key in set(cumulative) | set(today):
            song = store.get_song(key)
            songs.append({"key": key, "title": song.display_title if song else key,
                          "cumulative": cumulative.get(key, 0), "today": today.get(key, 0)})
        songs.sort(key=lambda s: (-s["cumulative"], s["title"]))
        return {
            "reset_at": store.tally_reset_at(),
            "voting_enabled": store.voting_enabled(),
            "viewers": len(app.state.hub.connections),
            "songs": songs,
            "daily": store.daily_tallies(),
        }

    @app.get("/api/admin/tally/export")
    def admin_export_tally(_: None = Depends(require_admin)):
        """A CSV of the cumulative and today's counts — for keeping records
        across seasons, independent of resetting the on-page total."""
        cumulative = store.cumulative_tally()
        today = store.todays_tally()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["song_key", "title", "cumulative_votes", "today_votes"])
        for key in sorted(set(cumulative) | set(today)):
            song = store.get_song(key)
            writer.writerow([key, song.display_title if song else key,
                             cumulative.get(key, 0), today.get(key, 0)])
        return Response(
            content=buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition":
                    'attachment; filename="fppvote-tally.csv"'})

    @app.post("/api/admin/tally/reset")
    def admin_reset_tally(_: None = Depends(require_admin)):
        """Move the cumulative total's starting point to now. Nothing is
        deleted — see Store.reset_tally — so this is safe to undo by hand."""
        return {"reset_at": store.reset_tally()}

    @app.put("/api/admin/voting")
    def admin_set_voting(body: dict = Body(...), _: None = Depends(require_admin)):
        """The Start/Stop Voting control. Stopping does not touch the
        follower or rounds — FPP keeps playing and rounds keep opening and
        closing underneath — it only refuses new votes and tells the voter
        page to show a stopped message instead of the song list."""
        enabled = bool(body.get("enabled"))
        store.set_voting_enabled(enabled)
        return {"voting_enabled": enabled}

    @app.get("/api/admin/activity")
    def admin_get_activity(_: None = Depends(require_admin)):
        """The most recent votes, for an at-a-glance is-this-working check
        during a show — separate from the aggregate tally."""
        return {"votes": store.recent_votes(limit=50)}

    @app.get("/api/admin/backup")
    def admin_backup(_: None = Depends(require_admin)):
        """Download the raw database file — vote history and every curated
        category, in one file. The Cloudflare tunnel credentials are a
        separate, unrelated piece of infrastructure this app has no reason to
        know about; back those up by hand — see docs/DEPLOY.md."""
        return FileResponse(store.db.path, media_type="application/octet-stream",
                            filename="fppvote-backup.db")

    @app.websocket("/ws")
    async def websocket(socket: WebSocket, token: str | None = Query(default=None)):
        """The page's live feed.

        The token arrives as a query parameter because a browser cannot set
        headers on a WebSocket handshake; the cookie is the fallback. It is the
        same opaque browser-held token the REST calls use, and only its HMAC is
        ever stored.
        """
        await socket.accept()
        token = (token or socket.cookies.get(VOTER_COOKIE) or "").strip() \
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

    @app.middleware("http")
    async def always_revalidate_the_page(request, call_next):
        """Make the browser check whether the page changed, every load.

        Without a Cache-Control header browsers fall back to *heuristic*
        caching — roughly a tenth of the file's age — and will happily serve a
        stale copy without asking. That cost an evening: the server was
        restarted with fixed code and the phone kept running the old page.

        'no-cache' does not mean "do not cache", it means "revalidate before
        using". The ETag is still sent, so an unchanged page costs a 304 with
        no body — cheap enough at this scale, and it means a fix reaches a
        viewer's phone without anyone being told to clear anything.
        """
        response = await call_next(request)
        if (request.url.path in ("/", "/admin")
                or request.url.path.startswith("/static")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        def index():
            return FileResponse(STATIC / "vote.html")

        @app.get("/admin")
        def admin_page():
            return FileResponse(STATIC / "admin.html")

    return app
