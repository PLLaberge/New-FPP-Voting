"""
The admin page, checked without a browser — same approach as
test_vote_page.py: every id the script reaches for must exist, and the page
must actually be reachable from a running service.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fppvote.fpp import from_catalog
from fppvote.service import Config, create_app
from tests.fixtures.playlists import CHRISTMAS

PAGE = (Path(__file__).resolve().parents[1] / "src" / "fppvote" / "web" /
        "static" / "admin.html")
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


def test_every_element_the_script_reaches_for_exists():
    defined = set(re.findall(r'id="([^"]+)"', HTML))
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', SCRIPT))
    assert used - defined == set(), f"script uses missing ids: {used - defined}"


def test_the_page_has_a_favicon():
    assert 'rel="icon"' in HTML


def test_the_service_serves_the_admin_page(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Admin" in response.text


def test_the_page_is_always_revalidated(client):
    response = client.get("/admin")
    assert "no-cache" in response.headers.get("cache-control", "")


# ------------------------------------------------------------------- the gate
def test_the_admin_token_is_kept_in_localstorage_not_a_cookie():
    """A shared secret set once via prompt(), not a login system — see
    require_admin in server.py. Kept in localStorage so it survives a reload
    without asking again."""
    assert 'localStorage.getItem(TOKEN_KEY)' in SCRIPT
    assert 'localStorage.setItem(TOKEN_KEY' in SCRIPT
    assert 'X-Admin-Token' in SCRIPT


def test_a_401_prompts_for_the_token_and_retries_once():
    """The page has no separate 'log in' screen — the first request that
    needs a token asks for it inline and retries, rather than the whole page
    refusing to load."""
    body = SCRIPT[SCRIPT.index("async function api("):SCRIPT.index("/* ---------- state")]
    assert "401" in body
    assert "return api(path, opts)" in body


# --------------------------------------------------------------- title editing
def test_the_song_title_shows_the_parsed_title_and_the_override_separately():
    """Store._song_block's `title` is always the raw parsed title, never
    blended with the override — this is what lets the admin page show both,
    unlike the voter page which only ever needs the blended one."""
    assert "parsed as: " in SCRIPT
    assert "song.display_override || song.title" in SCRIPT


def test_typing_back_the_parsed_title_clears_the_override_rather_than_setting_one():
    assert "value === song.title" in SCRIPT


# ----------------------------------------------------------- reconcile safety
def test_reconcile_never_sends_categories_the_server_would_have_to_invent():
    """The reconcile button posts with no body — categories are never guessed
    client-side, only by Store.sync_show's suggest_from_other_shows."""
    body = SCRIPT[SCRIPT.index('$("#reconcileBtn").onclick'):
                  SCRIPT.index('/* ---------- songs')]
    assert 'method:"POST"' in body
    assert '"categories"' not in body
