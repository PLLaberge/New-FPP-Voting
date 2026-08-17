"""
The service: state payload, voting, handover timing, degradation.

Runs against FakeFppAdapter throughout — no Pi, no network, no sleeping. The
follower is driven by calling tick() directly rather than waiting for the
background task, so a whole evening's worth of transitions happens instantly
and identically every run.
"""
import pytest
from fastapi.testclient import TestClient

from fppvote.catalog.parser import slugify
from fppvote.fpp import from_catalog
from fppvote.service import Config, Follower, build_state, create_app
from fppvote.service.follower import normalise_playlist_name
from tests.fixtures.playlists import CHRISTMAS

PLAYLIST = "Christmas 2025"


@pytest.fixture
def fpp():
    """Just the adapter. Deliberately independent of which store fixture the
    test wants — an earlier version pulled in `curated`, which quietly curated
    the store out from under a test that had asked for an uncurated one."""
    return from_catalog({PLAYLIST: list(CHRISTMAS)})


@pytest.fixture
def config(db_path):
    # A long poll interval so the background task ticks once at startup and
    # then sleeps: the tests drive the follower explicitly, which is what makes
    # a whole evening's transitions deterministic.
    return Config(db_path=db_path, handover_lead_seconds=2.0, poll_seconds=3600)


@pytest.fixture
def follower(curated, fpp, config):
    curated.update_show("christmas", playlist_name=PLAYLIST)
    fpp.start_at_item(PLAYLIST, 1)
    return Follower(curated, fpp, config)


@pytest.fixture
def client(curated, fpp, config):
    curated.update_show("christmas", playlist_name=PLAYLIST)
    fpp.start_at_item(PLAYLIST, 1)
    app = create_app(config, store=curated, adapter=fpp)
    with TestClient(app) as c:
        app.state.follower.tick()      # don't race the background task
        yield c


def playing_key(fpp):
    return slugify(fpp.get_status().sequence_name)


# ------------------------------------------------------------ show resolution
def test_the_show_is_derived_from_the_playlist_fpp_is_playing(follower, curated):
    """No admin toggle and no date rule — it is right after a restart and right
    when the playlist changes mid-evening."""
    state = follower.tick()
    assert state.show_id == "christmas"
    assert state.round_id is not None


def test_an_unrecognised_playlist_falls_back_when_there_is_only_one_show(
        curated, fpp, config):
    """The playlist names were guesses until someone checked them against the
    Pi. With a single show there is nothing to be ambiguous about, so run and
    warn rather than refusing — a name mismatch must not mean a dead page on
    the first cold night."""
    curated.update_show("christmas", playlist_name="Something Else")
    follower = Follower(curated, fpp, config)
    fpp.start_at_item(PLAYLIST, 1)
    assert follower.tick().show_id == "christmas"


def test_an_unrecognised_playlist_is_reported_when_it_is_ambiguous(
        curated, fpp, config):
    """Two shows and neither matches: say so instead of picking one."""
    curated.update_show("christmas", playlist_name="Something Else")
    curated.create_show("nye", "New Year's", "Another Thing")
    follower = Follower(curated, fpp, config)
    fpp.start_at_item(PLAYLIST, 1)
    state = follower.tick()
    assert state.show_id is None
    assert "matches no configured show" in state.last_error


@pytest.mark.parametrize("stored,reported", [
    ("NY_Dance_Party", "NY_Dance_Party"),
    ("NY_Dance_Party", "NY_Dance_Party.json"),          # FPP included the suffix
    ("NY_Dance_Party.json", "NY_Dance_Party"),          # someone pasted the filename
    ("All_Xmas_Songs - Alphabetic", "All_Xmas_Songs - Alphabetic"),
    ("All_Xmas_Songs - Alphabetic", "all_xmas_songs - alphabetic"),
    ("All_Xmas_Songs - Alphabetic", "  All_Xmas_Songs - Alphabetic  "),
])
def test_real_playlist_names_match_in_either_form(curated, config, stored, reported):
    """FPP keeps playlists as ~/media/playlists/<name>.json and refers to them
    without the suffix. Both forms match, so pasting the filename works too."""
    curated.update_show("christmas", playlist_name=stored)
    fpp = from_catalog({reported.strip(): list(CHRISTMAS)})
    fpp.start_at_item(reported.strip(), 1)
    follower = Follower(curated, fpp, config)
    assert follower.tick().show_id == "christmas"


def test_similar_playlist_names_are_not_conflated(curated, config):
    """Underscores, spaces and hyphens are meaningful; only .json and case are
    forgiven. Two shows, so there is no single-show fallback to mask this."""
    curated.update_show("christmas", playlist_name="All_Xmas_Songs - Alphabetic")
    curated.create_show("nye", "New Year's", "NY_Dance_Party")
    fpp = from_catalog({"All Xmas Songs Alphabetic": list(CHRISTMAS)})
    fpp.start_at_item("All Xmas Songs Alphabetic", 1)
    follower = Follower(curated, fpp, config)
    assert follower.tick().show_id is None


def test_normalise_playlist_name_only_strips_json_and_case():
    assert normalise_playlist_name("NY_Dance_Party.json") == "ny_dance_party"
    assert normalise_playlist_name("NY_Dance_Party.JSON") == "ny_dance_party"
    assert normalise_playlist_name(None) == ""
    # a name that merely contains 'json' keeps it
    assert normalise_playlist_name("json_party") == "json_party"


def test_an_fpp_blip_does_not_switch_shows(follower, fpp):
    follower.tick()
    fpp.go_offline()
    assert follower.tick().show_id == "christmas"


# -------------------------------------------------------------- state payload
def test_the_state_payload_has_what_the_page_needs(follower, curated):
    follower.tick()
    state = build_state(curated, follower)

    assert state["show"]["name"] == "Christmas 2025"
    assert state["show"]["votes_per_round"] == 3
    assert len(state["songs"]) == 65
    assert state["now_playing"]["key"] == "christmas-vacation"
    assert state["now_playing"]["seconds_total"] > 0
    assert state["categories"] and "Instrumental" in state["categories"]
    assert state["fpp"]["reachable"] is True


def test_the_playing_song_is_locked_in_the_payload(follower, curated):
    follower.tick()
    state = build_state(curated, follower)
    playing = next(s for s in state["songs"] if s["key"] == state["now_playing"]["key"])
    assert playing["locked"] is True


def test_uncategorised_songs_are_still_listed(synced, fpp, config):
    """A curation gap must never hide a song from the people voting."""
    synced.update_show("christmas", playlist_name=PLAYLIST)
    follower = Follower(synced, fpp, config)
    fpp.start_at_item(PLAYLIST, 1)
    follower.tick()
    state = build_state(synced, follower)
    assert len(state["songs"]) == 65
    assert state["categories"] == [], "no chips, but every song is still there"


# --------------------------------------------------------------------- voting
def test_a_vote_round_trips_through_the_api(client):
    first = client.get("/api/state").json()
    token = first["you"]["token"]
    assert first["you"]["votes_left"] == 3

    unlocked = next(s for s in first["songs"] if not s["locked"])
    reply = client.post("/api/vote", json={"song_key": unlocked["key"]},
                        headers={"X-Voter-Token": token}).json()

    assert reply["outcome"] == "accepted"
    assert reply["you"]["votes_used"] == 1
    assert reply["you"]["selection"] == [unlocked["key"]]
    voted = next(s for s in reply["songs"] if s["key"] == unlocked["key"])
    assert voted["votes"] == 1


def test_the_allowance_is_enforced_over_the_api(client):
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    unlocked = [s["key"] for s in state["songs"] if not s["locked"]][:4]

    outcomes = [client.post("/api/vote", json={"song_key": key},
                            headers={"X-Voter-Token": token}).json()["outcome"]
                for key in unlocked]
    assert outcomes == ["accepted", "accepted", "accepted", "limit_reached"]


def test_a_vote_can_be_retracted(client):
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    key = next(s["key"] for s in state["songs"] if not s["locked"])

    client.post("/api/vote", json={"song_key": key}, headers={"X-Voter-Token": token})
    reply = client.post("/api/vote", json={"song_key": key, "retract": True},
                        headers={"X-Voter-Token": token}).json()
    assert reply["outcome"] == "retracted"
    assert reply["you"]["votes_used"] == 0


def test_the_playing_song_cannot_be_voted_for(client):
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    reply = client.post("/api/vote", json={"song_key": state["now_playing"]["key"]},
                        headers={"X-Voter-Token": token}).json()
    assert reply["outcome"] == "locked"


def test_two_voters_are_independent(client):
    state = client.get("/api/state").json()
    key = next(s["key"] for s in state["songs"] if not s["locked"])
    for token in ("alice-token", "bob-token"):
        client.post("/api/vote", json={"song_key": key},
                    headers={"X-Voter-Token": token})
    final = client.get("/api/state", headers={"X-Voter-Token": "alice-token"}).json()
    assert next(s for s in final["songs"] if s["key"] == key)["votes"] == 2
    assert final["you"]["votes_used"] == 1


# ----------------------------------------------------------- voter identity
def test_a_token_is_issued_on_first_contact_and_reused_after(client):
    first = client.get("/api/state").json()
    token = first["you"]["token"]
    assert token and len(token) > 16
    assert client.cookies.get("fppvote_voter") == token

    again = client.get("/api/state", headers={"X-Voter-Token": token}).json()
    assert again["you"]["token"] == token


def test_the_raw_token_is_never_stored(client, curated):
    state = client.get("/api/state").json()
    token = state["you"]["token"]
    key = next(s["key"] for s in state["songs"] if not s["locked"])
    client.post("/api/vote", json={"song_key": key},
                headers={"X-Voter-Token": token})

    stored = [r["voter_hash"] for r in
              curated.db.connection.execute("SELECT voter_hash FROM votes")]
    assert stored and token not in stored
    assert stored[0] == curated.voter_hash(token)


# -------------------------------------------------------------- the handover
def test_the_winner_takes_over_a_beat_before_the_song_ends(follower, curated, fpp):
    """Paulin's choice: clip the outgoing song's fade rather than cut away from
    a second of the wrong song."""
    follower.tick()
    rnd = curated.current_round("christmas")
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys("christmas"))
    curated.cast_vote(rnd.round_id, "alice", winner)

    # not yet — plenty of song left
    follower.tick()
    assert fpp.commands[-1] == (PLAYLIST, 1)

    entry = fpp.current_entry()
    fpp.tick(entry.duration_seconds - fpp.elapsed - 1.0)   # 1s remaining
    follower.tick()

    assert playing_key(fpp) == winner
    assert curated.get_round(rnd.round_id).winner_key == winner


def test_the_handover_fires_once_not_every_poll(follower, curated, fpp):
    follower.tick()
    rnd = curated.current_round("christmas")
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys("christmas"))
    curated.cast_vote(rnd.round_id, "alice", winner)

    entry = fpp.current_entry()
    fpp.tick(entry.duration_seconds - fpp.elapsed - 1.0)
    for _ in range(5):
        follower.tick()
    assert len([c for c in fpp.commands if c[1] != 1]) == 1


def test_nobody_voting_leaves_the_playlist_alone(follower, fpp):
    """'The playlist should just keep playing.'"""
    follower.tick()
    entry = fpp.current_entry()
    fpp.tick(entry.duration_seconds - fpp.elapsed - 1.0)
    follower.tick()
    assert fpp.commands == [(PLAYLIST, 1)], "no jump was issued"


def test_a_missed_window_still_plays_the_winner(curated, fpp, config):
    """The degradation Paulin accepted: if the lead window is missed the jump
    happens late rather than the winner being silently dropped."""
    follower = Follower(curated, fpp, config)
    fpp.start_at_item(PLAYLIST, 1)
    follower.tick()
    rnd = curated.current_round("christmas")
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys("christmas"))
    curated.cast_vote(rnd.round_id, "alice", winner)

    # jump clean over the lead window, as a skipped poll would
    fpp.play_to_end_of_song()
    follower.tick()

    assert playing_key(fpp) == winner
    assert curated.get_round(rnd.round_id).winner_key == winner


def test_votes_survive_fpp_vanishing_mid_song(follower, curated, fpp):
    follower.tick()
    rnd = curated.current_round("christmas")
    key = next(s.key for s in curated.list_show_songs("christmas")
               if s.key not in curated.locked_keys("christmas"))
    curated.cast_vote(rnd.round_id, "alice", key)

    fpp.go_offline()
    for _ in range(20):
        assert follower.tick().round_id == rnd.round_id
    fpp.go_online()

    assert follower.tick().round_id == rnd.round_id
    assert curated.tally(rnd.round_id) == {key: 1}


def test_an_idle_fpp_closes_the_round(follower, curated, fpp):
    follower.tick()
    rnd = curated.current_round("christmas")
    fpp.stop()
    follower.tick()
    assert curated.get_round(rnd.round_id).is_open is False
    assert curated.current_round("christmas") is None


# --------------------------------------------------------------------- health
def test_the_health_endpoint_answers_the_6pm_question(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["show"] == "christmas"
    assert body["status"] == "playing"
    assert body["playing"]
    assert body["votes_this_round"] == 0


def test_health_reports_an_unreachable_fpp_without_falling_over(client, fpp):
    fpp.go_offline()
    client.app.state.follower.tick()
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert body["status"] == "unknown"


def test_the_page_still_serves_when_fpp_is_down(client, fpp):
    """Degrade, never disappear — the viewers already like the old broken app;
    the win is that this one keeps working."""
    fpp.go_offline()
    client.app.state.follower.tick()
    state = client.get("/api/state").json()
    assert state["fpp"]["reachable"] is False
    assert len(state["songs"]) == 65, "the song list is still browsable"


# ------------------------------------------------------------------ websocket
def test_a_websocket_receives_state_on_connect(client):
    with client.websocket_connect("/ws") as socket:
        payload = socket.receive_json()
        assert payload["show"]["id"] == "christmas"
        assert len(payload["songs"]) == 65
        assert payload["you"]["votes_left"] == 3
        assert "_selections" not in payload or isinstance(payload["_selections"], dict)
