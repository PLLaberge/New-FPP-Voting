"""
The admin API: settings, category vocabulary, per-song category assignment,
song metadata, and pulling a fresh reconcile from FPP.

All of it is thin — every write here is a Store method that already has its
own tests in test_db.py. What is worth testing at this layer is the wiring:
that the right store call happens for the right route, that validation
errors come back as 400 rather than a 500, and that the admin gate actually
gates once a token is configured.
"""
import pytest
from fastapi.testclient import TestClient

from fppvote.fpp import from_catalog
from fppvote.service import Config, create_app
from tests.fixtures.playlists import CHRISTMAS

PLAYLIST = "All_Xmas_Songs - Alphabetic"


def build_app(store, *, admin_token="", entries=None):
    fpp = from_catalog({PLAYLIST: list(entries if entries is not None else CHRISTMAS)})
    fpp.start_at_item(PLAYLIST, 1)
    store.update_show("christmas", playlist_name=PLAYLIST)
    return create_app(Config(db_path=store.db.path, poll_seconds=3600,
                             admin_token=admin_token),
                      store=store, adapter=fpp)


@pytest.fixture
def client(curated, db_path):
    app = build_app(curated)
    with TestClient(app) as c:
        app.state.follower.tick()
        yield c


# ------------------------------------------------------------------- the gate
def test_admin_routes_are_open_when_no_token_is_configured(client):
    """Every laptop run, by default — nothing else is listening."""
    assert client.get("/api/admin/shows").status_code == 200


def test_admin_routes_refuse_a_missing_or_wrong_token_once_configured(curated, db_path):
    app = build_app(curated, admin_token="letmein")
    with TestClient(app) as c:
        assert c.get("/api/admin/shows").status_code == 401
        assert c.get("/api/admin/shows",
                     headers={"X-Admin-Token": "nope"}).status_code == 401
        assert c.get("/api/admin/shows",
                     headers={"X-Admin-Token": "letmein"}).status_code == 200


def test_the_admin_page_itself_is_always_revalidated(client):
    """Same lesson as the voter page: no Cache-Control means a restarted
    server can keep serving a stale copy to a browser that already has it."""
    response = client.get("/admin")
    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "")


# ------------------------------------------------------------------- shows
def test_shows_listing_includes_every_show_and_a_review_count(client, curated):
    data = client.get("/api/admin/shows").json()
    show = next(s for s in data if s["id"] == "christmas")
    for field in ("id", "name", "playlist_name", "tagline", "note", "theme",
                  "active", "songs_total", "needs_review"):
        assert field in show, f"admin page needs {field} but the payload lacks it"
    assert show["songs_total"] == len(curated.list_show_songs("christmas"))
    assert show["needs_review"] == 0     # `curated` is fully categorised


def test_updating_show_settings_writes_through_the_store(client, curated):
    r = client.patch("/api/admin/shows/christmas", json={"tagline": "New tagline"})
    assert r.status_code == 200
    assert r.json()["tagline"] == "New tagline"
    show = curated.get_show("christmas")
    assert show.tagline == "New tagline"


def test_an_unknown_show_field_is_a_400_not_a_500(client):
    r = client.patch("/api/admin/shows/christmas", json={"nope": 1})
    assert r.status_code == 400


# ------------------------------------------------------------ voting rules
# Global since 2026-08-25 (see CLAUDE.md), so these live under their own
# route rather than a per-show one -- see test_updating_show_settings_writes
# _through_the_store above for what is still per-show.
def test_voting_rules_are_global_not_per_show(client, curated):
    r = client.put("/api/admin/voting-rules",
                   json={"votes_per_round": 1, "cooldown_songs": 2})
    assert r.status_code == 200
    assert r.json() == {"votes_per_round": 1, "cooldown_songs": 2}
    assert curated.votes_per_round() == 1
    assert curated.cooldown_songs() == 2
    assert client.get("/api/admin/voting-rules").json() == \
        {"votes_per_round": 1, "cooldown_songs": 2}


def test_an_out_of_range_vote_allowance_is_a_400(client):
    r = client.put("/api/admin/voting-rules", json={"votes_per_round": 9})
    assert r.status_code == 400


def test_a_missing_show_is_a_404_everywhere(client):
    assert client.get("/api/admin/shows/nope").status_code == 404 \
        or client.get("/api/admin/shows/nope/categories").status_code == 404
    assert client.get("/api/admin/shows/nope/songs").status_code == 404
    assert client.get("/api/admin/shows/nope/categories").status_code == 404
    assert client.patch("/api/admin/shows/nope", json={}).status_code == 404
    assert client.post("/api/admin/shows/nope/reconcile").status_code == 404


# --------------------------------------------------------------- categories
def test_category_vocabulary_round_trips(client, curated):
    before = client.get("/api/admin/categories").json()
    assert before["categories"] == curated.list_categories()
    assert before["counts"]["Traditional"] > 0


def test_removing_a_category_still_in_use_reports_it_as_orphaned(client, curated):
    """Songs keep the assignment — see Store.set_category_vocabulary — the
    admin page just needs to be told, since the chip stops rendering
    silently."""
    vocab = curated.list_categories()
    assert "Crooners" in vocab and curated.category_counts()["Crooners"] > 0
    trimmed = [c for c in vocab if c != "Crooners"]
    r = client.put("/api/admin/categories", json={"categories": trimmed})
    assert r.status_code == 200
    assert "Crooners" in r.json()["orphaned"]
    assert curated.list_categories() == trimmed


def test_categories_must_be_a_list_of_strings(client):
    r = client.put("/api/admin/categories", json={"categories": "nope"})
    assert r.status_code == 400
    r = client.put("/api/admin/categories", json={"categories": [1, 2]})
    assert r.status_code == 400


# -------------------------------------------------------------------- songs
def test_song_listing_carries_the_raw_title_and_the_override_separately(client):
    songs = client.get("/api/admin/shows/christmas/songs").json()["songs"]
    song = next(s for s in songs if s["key"] == "mele-kalikimaka")
    assert song["title"] == "Mele Kalikimaka"
    assert song["display_override"] is None
    for field in ("sequence_name", "media_name", "artist", "year", "categories",
                  "active", "playlist_index", "needs_review"):
        assert field in song


def test_setting_a_songs_categories_refuses_anything_outside_the_vocabulary(client):
    r = client.put("/api/admin/shows/christmas/songs/mele-kalikimaka/categories",
                   json={"categories": ["Not A Real Chip"]})
    assert r.status_code == 400


def test_setting_a_songs_categories_writes_through_and_updates_counts(client, curated):
    r = client.put("/api/admin/shows/christmas/songs/mele-kalikimaka/categories",
                   json={"categories": ["Traditional"]})
    assert r.status_code == 200
    body = r.json()
    assert body["song"]["categories"] == ["Traditional"]
    assert body["counts"] == curated.category_counts(include_inactive=True)
    membership = next(s for s in curated.list_show_songs("christmas")
                      if s.key == "mele-kalikimaka")
    assert membership.categories == ["Traditional"]


def test_display_override_can_be_set_and_cleared(client, curated):
    r = client.put("/api/admin/songs/mele-kalikimaka",
                   json={"display_override": "Merry Little Christmas"})
    assert r.status_code == 200
    assert r.json()["display_override"] == "Merry Little Christmas"
    assert curated.get_song("mele-kalikimaka").display_title == "Merry Little Christmas"

    r = client.put("/api/admin/songs/mele-kalikimaka", json={"display_override": None})
    assert r.json()["display_override"] is None
    assert curated.get_song("mele-kalikimaka").display_title == "Mele Kalikimaka"


def test_artist_and_year_can_be_corrected(client, curated):
    r = client.put("/api/admin/songs/mele-kalikimaka",
                   json={"artist": "Bing Crosby", "year": 1950})
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "mele-kalikimaka"
    assert body["artist"] == "Bing Crosby"
    assert body["year"] == 1950
    updated = curated.get_song("mele-kalikimaka")
    assert (updated.artist, updated.year) == ("Bing Crosby", 1950)


def test_year_must_be_an_integer_or_null(client):
    r = client.put("/api/admin/songs/mele-kalikimaka", json={"year": "not a year"})
    assert r.status_code == 400


def test_a_missing_song_is_a_404(client):
    assert client.put("/api/admin/songs/no-such-song", json={"artist": "X"}).status_code == 404


# --------------------------------------------------- excluding and deleting
def test_a_song_can_be_excluded_and_reinstated(client, curated):
    r = client.put("/api/admin/songs/zero", json={"excluded": True})
    assert r.status_code == 200
    assert r.json()["excluded"] is True
    assert curated.get_song("zero").excluded is True

    r = client.put("/api/admin/songs/zero", json={"excluded": False})
    assert r.json()["excluded"] is False
    assert curated.get_song("zero").excluded is False


def test_deleting_a_song_that_is_not_excluded_is_a_400(client):
    r = client.delete("/api/admin/songs/zero")
    assert r.status_code == 400
    assert "excluded" in r.json()["detail"]


def test_deleting_a_song_with_history_is_a_400(client, curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "zero")
    client.put("/api/admin/songs/zero", json={"excluded": True})

    r = client.delete("/api/admin/songs/zero")
    assert r.status_code == 400
    assert curated.get_song("zero") is not None


def test_deleting_an_excluded_unplayed_song_succeeds(client, curated):
    client.put("/api/admin/songs/zero", json={"excluded": True})
    r = client.delete("/api/admin/songs/zero")
    assert r.status_code == 200
    assert r.json()["deleted"] == "zero"
    assert curated.get_song("zero") is None


def test_deleting_a_missing_song_is_a_404(client):
    assert client.delete("/api/admin/songs/no-such-song").status_code == 404


# --------------------------------------------------------------- reconcile
def test_reconcile_pulls_the_live_playlist_and_reports_what_changed(curated, db_path):
    """A song dropped from the FPP playlist and a new one added — the same
    thing that happens for real when Paulin edits the show on the Pi."""
    modified = [e for e in CHRISTMAS if not e[0].startswith("Mele Kalikimaka")]
    modified.append(("Totally New Song.fseq", "01 Totally New Song.mp3", "02:00"))
    app = build_app(curated, entries=modified)
    with TestClient(app) as c:
        c.app.state.follower.tick()
        r = c.post("/api/admin/shows/christmas/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert "totally-new-song" in body["added"]
    assert "mele-kalikimaka" in body["deactivated"]
    assert body["summary"]

    songs = {s.key: s for s in curated.list_show_songs("christmas", include_inactive=True)}
    assert songs["mele-kalikimaka"].active is False
    assert songs["totally-new-song"].active is True
    # Categories are editorial and untouched by a reconcile — the song that
    # was already curated must still carry its categories, not be reset.
    assert songs["mele-kalikimaka"].categories


def test_reconcile_reports_when_fpp_cannot_be_reached(curated, db_path):
    app = build_app(curated)
    with TestClient(app) as c:
        c.app.state.adapter.go_offline()
        r = c.post("/api/admin/shows/christmas/reconcile")
    assert r.status_code == 502


# ----------------------------------------------------------------- tally
def cast(client, song_key):
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    client.post("/api/vote", json={"song_key": song_key},
               headers={"X-Voter-Token": token})
    return token


def test_tally_reflects_cast_votes(client):
    cast(client, "mele-kalikimaka")
    r = client.get("/api/admin/tally")
    assert r.status_code == 200
    body = r.json()
    row = next(s for s in body["songs"] if s["key"] == "mele-kalikimaka")
    assert row["cumulative"] == 1
    assert row["today"] == 1
    assert body["voting_enabled"] is True
    assert body["reset_at"] is None
    assert "viewers" in body
    assert "daily" in body


def test_tally_export_is_a_csv(client):
    cast(client, "mele-kalikimaka")
    r = client.get("/api/admin/tally/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "mele-kalikimaka" in r.text
    assert "song_key,title,cumulative_votes,today_votes" in r.text


def test_tally_reset_returns_a_marker_and_clears_cumulative(client):
    cast(client, "mele-kalikimaka")
    r = client.post("/api/admin/tally/reset")
    assert r.status_code == 200
    assert r.json()["reset_at"]
    body = client.get("/api/admin/tally").json()
    row = next(s for s in body["songs"] if s["key"] == "mele-kalikimaka")
    assert row["cumulative"] == 0, "cumulative resets"
    assert row["today"] == 1, "today's count is independent of the reset marker"


def test_voting_can_be_stopped_and_started_via_admin(client):
    r = client.put("/api/admin/voting", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"voting_enabled": False}
    assert client.get("/api/admin/tally").json()["voting_enabled"] is False

    r = client.put("/api/admin/voting", json={"enabled": True})
    assert r.json() == {"voting_enabled": True}


def test_stopped_voting_refuses_new_votes_with_a_clear_message(client):
    client.put("/api/admin/voting", json={"enabled": False})
    state = client.get("/api/state").json()
    assert state["voting_enabled"] is False
    token = state["you"]["token"]
    r = client.post("/api/vote", json={"song_key": "mele-kalikimaka"},
                    headers={"X-Voter-Token": token})
    body = r.json()
    assert body["outcome"] == "voting_stopped"
    assert "no voting" in body["message"].lower()


def test_activity_feed_lists_recent_votes_newest_first(client):
    cast(client, "mele-kalikimaka")
    cast(client, "frosty-the-snowman")
    r = client.get("/api/admin/activity")
    assert r.status_code == 200
    votes = r.json()["votes"]
    assert votes[0]["song_key"] == "frosty-the-snowman"
    assert votes[0]["title"]


def test_backup_downloads_the_database_file(client):
    r = client.get("/api/admin/backup")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "fppvote-backup.db" in r.headers["content-disposition"]
    assert r.content[:16] == b"SQLite format 3\x00"


def test_tally_endpoints_require_the_admin_token_when_configured(curated, db_path):
    app = build_app(curated, admin_token="secret")
    with TestClient(app) as c:
        assert c.get("/api/admin/tally").status_code == 401
        assert c.get("/api/admin/tally/export").status_code == 401
        assert c.post("/api/admin/tally/reset").status_code == 401
        assert c.put("/api/admin/voting", json={"enabled": False}).status_code == 401
        assert c.get("/api/admin/activity").status_code == 401
        assert c.get("/api/admin/backup").status_code == 401
