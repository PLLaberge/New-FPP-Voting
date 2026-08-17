"""
The voter page, checked without a browser.

There is no npm on the Pi and no build step, so there is no JS test runner
either. These are the checks that are still worth making statically: that every
element the script reaches for exists, that every URL it calls is a real route,
and that the simulation it used to run on is genuinely gone rather than merely
unreferenced.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fppvote.fpp import from_catalog
from fppvote.service import Config, create_app
from tests.fixtures.playlists import CHRISTMAS

PAGE = (Path(__file__).resolve().parents[1] / "src" / "fppvote" / "web" /
        "static" / "vote.html")
HTML = PAGE.read_text()
SCRIPT = HTML[HTML.index("<script>"):HTML.index("</script>")]
PLAYLIST = "All_Xmas_Songs - Alphabetic"


@pytest.fixture
def client(curated, db_path):
    curated.update_show("christmas", playlist_name=PLAYLIST)
    fpp = from_catalog({PLAYLIST: list(CHRISTMAS)})
    fpp.start_at_item(PLAYLIST, 1)
    app = create_app(Config(db_path=db_path, poll_seconds=3600),
                     store=curated, adapter=fpp)
    with TestClient(app) as c:
        app.state.follower.tick()
        yield c


# --------------------------------------------------------------- the wiring
def test_every_element_the_script_reaches_for_exists():
    """A renamed or deleted id fails silently in a browser — the page just
    stops doing one thing. Cheap to catch here instead."""
    defined = set(re.findall(r'id="([^"]+)"', HTML))
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', SCRIPT))
    used |= set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', SCRIPT))
    assert used - defined == set(), f"script uses missing ids: {used - defined}"


def test_every_url_the_page_calls_is_a_real_route(client):
    routes = {getattr(r, "path", None) for r in client.app.routes}
    called = set(re.findall(r'(?:fetch|")(/api/[a-z]+)', SCRIPT))
    assert called, "expected the page to call the API"
    assert called <= routes, f"page calls routes that do not exist: {called - routes}"
    assert "/ws" in routes


def test_the_simulation_is_gone():
    """The page used to carry its own copy of the catalogue and invent votes.
    One source of truth now — which is why chips change here without anyone
    editing this file."""
    for ghost in ("const CATALOG", "const SHOWS", "function nextSong",
                  "function startSong", "showSeg", "voteSeg", "Math.random()*9"):
        assert ghost not in SCRIPT, f"leftover from the prototype: {ghost}"


def test_the_page_no_longer_lets_a_viewer_pick_the_show_or_the_allowance():
    """Both follow the server now — the show from whatever playlist FPP is
    running, the allowance from the show's settings."""
    assert "Prototype controls" not in HTML
    assert 'data-max="3"' not in HTML


# ------------------------------------------------------------- transparency
def test_the_page_says_what_it_stores_and_offers_to_clear_it():
    """Paulin's condition when he approved the token: it has to be transparent
    to the viewer."""
    assert "resetId" in HTML
    assert "Reset my voting ID" in HTML
    body = HTML.lower()
    assert "no ip address" in body
    assert "random id" in body


def test_the_token_lives_in_localstorage_so_it_can_be_inspected():
    assert 'localStorage.getItem(TOKEN_KEY)' in SCRIPT
    assert 'localStorage.removeItem(TOKEN_KEY)' in SCRIPT
    # and the page survives private mode, where localStorage throws
    assert "catch(e){ return \"\"; }" in SCRIPT


# ---------------------------------------------------------------- degrading
def test_the_page_has_a_message_for_every_way_it_can_lose_the_show():
    for phrase in ("Connecting to the show",      # first load
                   "Can’t see the show",          # FPP unreachable
                   "Waiting for the show",        # no playlist running
                   "Live updates disconnected"):  # websocket dropped
        assert phrase in SCRIPT, f"no banner copy for: {phrase}"


def test_polling_backs_up_the_websocket():
    """The socket is an optimisation, never the only way to be right — the same
    shape as MQTT sitting on top of HTTP in the adapter."""
    assert "if(!state.connected) refresh()" in SCRIPT
    assert "scheduleReconnect" in SCRIPT


def test_missing_artist_and_year_are_spelled_out_not_printed_as_null():
    """6 songs have no artist and 23 no year — real gaps in the catalogue."""
    assert "Artist to confirm" in SCRIPT
    assert "Year TBD" in SCRIPT
    assert "Year to confirm" in SCRIPT      # the heading when sorting by year


# --------------------------------------------------------------- end to end
def test_the_service_serves_the_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Vote for the next song" in response.text


def test_the_payload_carries_what_the_page_renders(client):
    state = client.get("/api/state").json()
    song = state["songs"][0]
    for field in ("key", "title", "artist", "year", "categories", "index",
                  "votes", "locked", "last_played"):
        assert field in song, f"page renders {field} but the payload lacks it"
    assert set(state) >= {"show", "categories", "now_playing", "round_id",
                          "songs", "fpp", "you"}
    assert "_selections" not in state, "internal field must not leak to the page"


def test_last_played_lets_the_page_match_the_servers_tie_break(client, curated):
    """The top row must be the song that would actually win. If the page sorted
    ties differently, the vote would look like it was lying."""
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    a, b = [s for s in state["songs"] if not s["locked"]][:2]
    client.post("/api/vote", json={"song_key": a["key"]},
                headers={"X-Voter-Token": token})
    client.post("/api/vote", json={"song_key": b["key"]},
                headers={"X-Voter-Token": token})

    fresh = client.get("/api/state").json()
    tied = [s for s in fresh["songs"] if s["votes"] == 1]
    assert len(tied) == 2
    # the page's sort: votes desc, then last_played asc, then title
    page_pick = sorted(tied, key=lambda s: (-s["votes"], s["last_played"],
                                            s["title"]))[0]["key"]
    assert page_pick == curated.winner(fresh["round_id"])


def test_the_websocket_accepts_the_token_as_a_query_parameter(client):
    """A browser cannot set headers on a WebSocket handshake."""
    with client.websocket_connect("/ws?token=alice-token") as socket:
        payload = socket.receive_json()
        assert payload["show"]["id"] == "christmas"
        assert payload["you"]["votes_left"] == 3


# ------------------------------------------------------- rendering, not flashing
def test_the_song_list_is_updated_in_place_not_rebuilt():
    """The list used to be wiped and recreated on every push, once a second.
    `.song` carries `animation:rise`, so all 65 rows re-ran their fade-in every
    second and the page visibly flashed.

    Rows are now created once, cached by key, and only written to when a value
    actually changed.
    """
    assert "const rowCache" in SCRIPT
    assert "function reconcile(" in SCRIPT
    assert "if(n._sig === sig) return n;" in SCRIPT, "rows must skip unchanged writes"
    assert "function setText(" in SCRIPT


def test_nothing_clears_the_list_on_the_hot_path():
    """innerHTML='' is fine for the empty states, which are rare. It must not
    appear in the path that runs on every update."""
    body = SCRIPT[SCRIPT.index("const playingKey = state.nowPlaying"):]
    assert 'innerHTML=""' not in body


def test_chips_and_meter_skip_rebuilds_when_unchanged():
    for guard in ("if(sig === chipSig) return;", "if(sig === meterSig) return;"):
        assert guard in SCRIPT


def test_the_entrance_animation_is_removed_after_it_plays():
    """Re-inserting a node restarts its CSS animations, so a row moved to a new
    sort position would fade in again."""
    assert 'n.style.animation="none"' in SCRIPT


def test_the_socket_waits_until_we_know_who_we_are():
    """If localStorage is blocked the browser has no token and the server issues
    a cookie. Connecting before that arrives gets the socket a throwaway
    identity, and every push then claims the viewer has used no votes."""
    assert "await refresh(); connect();" in SCRIPT
    assert "function adoptToken(" in SCRIPT


def test_a_failed_update_cannot_freeze_the_page():
    """The websocket handler used to be `catch(e){}`. One fault from any cause
    left the socket open, every later push hitting the same fault, and the page
    silently frozen until the viewer thought to reload — it looked fine and was
    simply out of date.
    """
    code = re.sub(r"/\*.*?\*/", "", SCRIPT, flags=re.S)      # drop comments
    for allowed in ('try{ localStorage.removeItem(TOKEN_KEY); }catch(e){}',
                    'try{ socket.close(); }catch(e){}'):
        code = code.replace(allowed, "")   # nothing to recover from in these
    assert "catch(e){}" not in code, "a silent catch remains in the update path"
    assert "function applySafely(" in SCRIPT
    assert "function hardRender(" in SCRIPT
    assert "console.error" in SCRIPT, "a swallowed error is worse than a loud one"


def test_the_viewer_is_told_when_the_page_gives_up():
    assert "Something went wrong on this page" in SCRIPT
    assert "state.broken" in SCRIPT


def test_a_reconciling_poll_backs_up_the_socket():
    """A WebSocket can stop delivering without closing — proxies and Cloudflare
    Tunnel both drop idle connections quietly."""
    assert "setInterval(refresh, 20000)" in SCRIPT


def test_the_all_chip_tracks_the_filter_even_when_chips_do_not_rebuild():
    """The chip list is only rebuilt when its contents change, so the All
    button's pressed state has to be set outside that guard."""
    chips = SCRIPT[SCRIPT.index("function renderChips("):SCRIPT.index("function updateChipNav(")]
    assert "chipAll" not in chips
    assert '$("#chipAll").setAttribute' in SCRIPT


# -------------------------------------------------------------- cache control
def test_the_page_is_always_revalidated(client):
    """Without a Cache-Control header browsers fall back to heuristic caching
    and serve a stale page without asking. That cost an evening: the server was
    restarted with fixed code and the browser kept running the old page, so a
    fix that was live looked like a fix that had not worked.

    'no-cache' means "revalidate before using", not "do not cache".
    """
    response = client.get("/")
    assert "no-cache" in response.headers.get("cache-control", "")


def test_the_page_announces_itself_in_the_console():
    """So 'which copy am I running?' is answerable in five seconds rather than
    by guessing."""
    assert 'console.info("fppvote: live page ready' in SCRIPT
