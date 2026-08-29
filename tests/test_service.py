"""
The service: state payload, voting, handover timing, degradation.

Runs against FakeFppAdapter throughout — no Pi, no network, no sleeping. The
follower is driven by calling tick() directly rather than waiting for the
background task, so a whole evening's worth of transitions happens instantly
and identically every run.
"""
import json

import pytest
from fastapi.testclient import TestClient

from fppvote.catalog.parser import slugify
from fppvote.fpp import from_catalog
from fppvote.service import Config, Follower, build_state, create_app
from fppvote.service.follower import normalise_playlist_name
from tests.conftest import curate_nye, sync_nye
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
# 2026-08-25: the app accepts any playlist FPP hands it (CLAUDE.md). There is
# no more name-matching gate between a playlist and a show — `show_id` is now
# only a best guess at "what does tonight feel like", resolved from which
# show's catalogue the currently-playing songs mostly belong to.
def test_the_show_is_derived_from_the_playlist_fpp_is_playing(follower, curated):
    """No admin toggle and no date rule — it is right after a restart and right
    when the playlist changes mid-evening."""
    state = follower.tick()
    assert state.show_id == "christmas"
    assert state.round_id is not None


def test_any_playlist_name_works_regardless_of_the_configured_name(curated, config):
    """The whole point of this redesign: nothing gates voting on a playlist's
    name matching a show any more. As long as the songs it contains have
    already been reconciled into the catalogue, they are voteable under a
    playlist called anything at all."""
    curated.update_show("christmas", playlist_name="Something Else Entirely")
    fpp = from_catalog({"A Totally Unconfigured Name": list(CHRISTMAS)})
    fpp.start_at_item("A Totally Unconfigured Name", 1)
    follower = Follower(curated, fpp, config)
    state = follower.tick()
    assert state.round_id is not None
    assert len(state.voteable_keys) == 65
    assert state.show_id == "christmas"      # still resolved, by content


def test_display_show_follows_whichever_catalogue_dominates(curated, fpp, config):
    """Two shows sharing songs: the display show (header text and theme only —
    never gating, see CLAUDE.md) follows whichever show's active catalogue
    covers the most of what's actually playing tonight."""
    sync_nye(curated)
    curate_nye(curated)
    follower = Follower(curated, fpp, config)
    fpp.start_at_item(PLAYLIST, 1)      # the full 65-song Christmas catalogue
    state = follower.tick()
    assert state.show_id == "christmas"


def test_display_show_is_sticky_when_nothing_is_recognised(curated, config):
    """A resolved display show does not reset to nothing just because one poll
    hits an unfamiliar playlist — it stays put until something else takes the
    lead, so the header does not flicker."""
    follower = Follower(curated, from_catalog({PLAYLIST: list(CHRISTMAS)}), config)
    follower.adapter.start_at_item(PLAYLIST, 1)
    assert follower.tick().show_id == "christmas"

    unknown = from_catalog({"Mystery Playlist": [
        ("totally-unknown-sequence", "Totally Unknown Song", "0:30"),
    ]})
    unknown.start_at_item("Mystery Playlist", 1)
    follower.adapter = unknown
    assert follower.tick().show_id == "christmas"


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
    assert state["votes_per_round"] == 3
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


def test_an_excluded_song_is_not_voteable_even_while_live(follower, curated):
    """excluded (2026-08-27) is checked directly, unlike show_songs.active —
    it works even on a song still sitting in tonight's live FPP playlist."""
    curated.set_excluded("zero", True)
    state = follower.tick()
    assert "zero" not in state.voteable_keys

    rnd = curated.current_round()
    result = curated.cast_vote(rnd.round_id, "voter", "zero",
                               valid_keys=state.voteable_keys)
    assert result.outcome == "not_in_show"


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


def test_the_leader_endpoint_tracks_the_front_runner(client):
    state = client.get("/api/state").json()
    token = state["you"]["token"]

    # Round open, nobody has voted: round_id is real, but there is no leader.
    empty = client.get("/api/leader").json()
    assert empty["round_id"] == state["round_id"]
    assert empty["title"] is None
    assert empty["votes"] == 0

    unlocked = next(s for s in state["songs"] if not s["locked"])
    client.post("/api/vote", json={"song_key": unlocked["key"]},
                headers={"X-Voter-Token": token})

    lead = client.get("/api/leader").json()
    assert lead["title"] == unlocked["title"]
    assert lead["votes"] == 1
    assert lead["round_id"] == state["round_id"]


def test_the_leader_endpoint_is_all_nulls_with_no_open_round(curated, fpp, config):
    # Built without the `client` fixture on purpose: that one runs the follower
    # loop as a live background task, which would race this test's own tick over
    # whether the round is closed. Here nothing ever starts on the fake, so FPP
    # is idle from the first tick and the round is unambiguously closed.
    curated.update_show("christmas", playlist_name=PLAYLIST)
    app = create_app(config, store=curated, adapter=fpp)
    app.state.follower.tick()
    assert app.state.follower.state.round_id is None
    body = TestClient(app).get("/api/leader").json()
    assert body == {"title": None, "votes": 0, "round_id": None}


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
    rnd = curated.current_round()
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys())
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
    rnd = curated.current_round()
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys())
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
    rnd = curated.current_round()
    winner = next(s.key for s in curated.list_show_songs("christmas")
                  if s.key not in curated.locked_keys())
    curated.cast_vote(rnd.round_id, "alice", winner)

    # jump clean over the lead window, as a skipped poll would
    fpp.play_to_end_of_song()
    follower.tick()

    assert playing_key(fpp) == winner
    assert curated.get_round(rnd.round_id).winner_key == winner


def test_votes_survive_fpp_vanishing_mid_song(follower, curated, fpp):
    follower.tick()
    rnd = curated.current_round()
    key = next(s.key for s in curated.list_show_songs("christmas")
               if s.key not in curated.locked_keys())
    curated.cast_vote(rnd.round_id, "alice", key)

    fpp.go_offline()
    for _ in range(20):
        assert follower.tick().round_id == rnd.round_id
    fpp.go_online()

    assert follower.tick().round_id == rnd.round_id
    assert curated.tally(rnd.round_id) == {key: 1}


def test_an_idle_fpp_closes_the_round(follower, curated, fpp):
    follower.tick()
    rnd = curated.current_round()
    fpp.stop()
    follower.tick()
    assert curated.get_round(rnd.round_id).is_open is False
    assert curated.current_round() is None


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


def test_a_websocket_never_receives_other_voters_selections(client, curated):
    """_selections maps every voter's hash to their picks. It is a broadcast
    optimisation and must not leave the server — it leaked here once, past an
    assertion written loosely enough to pass either way."""
    state = client.get("/api/state").json()
    key = next(s["key"] for s in state["songs"] if not s["locked"])
    client.post("/api/vote", json={"song_key": key},
                headers={"X-Voter-Token": "someone-else"})

    with client.websocket_connect("/ws?token=me") as socket:
        payload = socket.receive_json()
        assert "_selections" not in payload
        assert curated.voter_hash("someone-else") not in json.dumps(payload)
        assert payload["you"]["selection"] == [], "only my own picks"
