"""Rounds, votes and the tie-break.

These are the rules from CLAUDE.md section 7, asserted against the database
rather than against a comment.
"""
from concurrent.futures import ThreadPoolExecutor

from fppvote.db import (
    ACCEPTED, DUPLICATE, LIMIT_REACHED, LOCKED, MOVED, NOT_IN_SHOW,
    ROUND_CLOSED, Store, UNKNOWN_SONG,
)
from tests.conftest import christmas_rows, sync_nye

FREE = frozenset()   # "ignore the cooldown", for tests about something else


# ------------------------------------------------------------------ rounds
def test_same_song_reported_twice_continues_the_same_round(curated):
    """FPP's status is re-read constantly and replays the current song after
    every reconnect. Opening a round per report would bin the votes mid-song."""
    first = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(first.round_id, "voter", "zero")
    again = curated.ensure_round("christmas", "hallelujah")
    assert again.round_id == first.round_id
    assert curated.tally(first.round_id) == {"zero": 1}


def test_a_new_song_closes_the_old_round_and_opens_one(curated):
    first = curated.ensure_round("christmas", "hallelujah")
    second = curated.ensure_round("christmas", "believer")
    assert second.round_id != first.round_id
    assert curated.get_round(first.round_id).is_open is False
    assert curated.current_round("christmas").round_id == second.round_id


def test_votes_reset_every_song(curated):
    """Not a DELETE — a consequence of votes belonging to a round."""
    first = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(first.round_id, "voter", "zero")
    second = curated.ensure_round("christmas", "believer")
    assert curated.tally(second.round_id) == {}
    assert curated.voter_selection(second.round_id, "voter") == set()
    # and the same person may vote again
    assert curated.cast_vote(second.round_id, "voter", "zero").accepted


def test_a_closed_round_takes_no_more_votes(curated):
    first = curated.ensure_round("christmas", "hallelujah")
    curated.ensure_round("christmas", "believer")
    assert curated.cast_vote(first.round_id, "voter", "zero").outcome == ROUND_CLOSED


def test_winner_is_recorded_against_the_round_that_voted(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "zero", locked=FREE)
    curated.set_winner(rnd.round_id, curated.winner(rnd.round_id))
    assert curated.get_round(rnd.round_id).winner_key == "zero"


# ----------------------------------------------------------------- allowance
def test_allowance_is_enforced(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    for key in ("zero", "believer", "barbie-girl"):
        assert curated.cast_vote(rnd.round_id, "voter", key).accepted
    spent = curated.cast_vote(rnd.round_id, "voter", "feliz-navidad")
    assert spent.outcome == LIMIT_REACHED
    assert len(curated.voter_selection(rnd.round_id, "voter")) == 3


def test_voting_twice_for_one_song_is_refused_but_can_be_retracted(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "zero")
    assert curated.cast_vote(rnd.round_id, "voter", "zero").outcome == DUPLICATE
    assert curated.tally(rnd.round_id) == {"zero": 1}
    assert curated.retract_vote(rnd.round_id, "voter", "zero") is True
    assert curated.tally(rnd.round_id) == {}


def test_at_allowance_one_a_new_tap_moves_the_vote(curated):
    curated.update_show("christmas", votes_per_round=1)
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "zero")
    moved = curated.cast_vote(rnd.round_id, "voter", "believer")
    assert moved.outcome == MOVED
    assert moved.removed_key == "zero"
    assert curated.tally(rnd.round_id) == {"believer": 1}


def test_voters_are_independent(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "alice", "zero")
    curated.cast_vote(rnd.round_id, "bob", "zero")
    assert curated.tally(rnd.round_id) == {"zero": 2}


def test_concurrent_taps_cannot_beat_the_allowance(curated):
    """Two taps that both read a count of 2 and both insert would make four
    votes out of an allowance of three. The unique index does not catch it —
    the songs differ — so the transaction has to."""
    rnd = curated.ensure_round("christmas", "hallelujah")
    keys = [s.key for s in curated.list_show_songs("christmas")
            if s.key != "hallelujah"][:12]

    shared = Store(curated.db)   # thread-local connections underneath

    def tap(key):
        return shared.cast_vote(rnd.round_id, "voter", key).outcome

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(tap, keys))

    assert outcomes.count(ACCEPTED) == 3
    assert outcomes.count(LIMIT_REACHED) == 9
    assert len(curated.voter_selection(rnd.round_id, "voter")) == 3


# ------------------------------------------------------------------- locks
def test_the_playing_song_and_the_last_four_are_locked(curated):
    played = ["zero", "believer", "barbie-girl", "feliz-navidad", "hallelujah"]
    for key in played:
        curated.ensure_round("christmas", key)
    assert curated.locked_keys("christmas") == set(played)

    curated.ensure_round("christmas", "my-favorite-things")
    # six songs played, cooldown 4 + the one playing = five locked
    assert curated.locked_keys("christmas") == set(played[1:] + ["my-favorite-things"])
    assert "zero" not in curated.locked_keys("christmas")


def test_a_locked_song_cannot_be_voted_for(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    assert curated.cast_vote(rnd.round_id, "voter", "hallelujah").outcome == LOCKED
    assert curated.tally(rnd.round_id) == {}


def test_cooldown_length_follows_the_show_setting(curated):
    curated.update_show("christmas", cooldown_songs=0)
    for key in ("zero", "believer"):
        curated.ensure_round("christmas", key)
    assert curated.locked_keys("christmas") == {"believer"}


# ------------------------------------------------------------- unknown songs
def test_a_song_from_another_show_is_refused(curated):
    sync_nye(curated)
    rnd = curated.ensure_round("christmas", "hallelujah")
    # NYE-only; never in the Christmas playlist
    assert curated.cast_vote(rnd.round_id, "voter", "auld-lang-syne").outcome == NOT_IN_SHOW


def test_a_song_that_does_not_exist_is_refused(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    assert curated.cast_vote(rnd.round_id, "voter", "no-such-song").outcome == UNKNOWN_SONG


def test_a_deactivated_song_is_refused(curated):
    curated.sync_show("christmas", christmas_rows(exclude={"barbie-girl"}))
    rnd = curated.ensure_round("christmas", "hallelujah")
    assert curated.cast_vote(rnd.round_id, "voter", "barbie-girl").outcome == NOT_IN_SHOW


def test_show_id_on_a_vote_comes_from_the_round(curated):
    """votes.show_id is denormalised; it must never be able to disagree with
    rounds.show_id."""
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "zero", locked=FREE)
    row = curated.db.connection.execute(
        "SELECT v.show_id AS v, r.show_id AS r FROM votes v "
        "JOIN rounds r ON r.round_id = v.round_id").fetchone()
    assert row["v"] == row["r"] == "christmas"


# ---------------------------------------------------------------- tie-break
def test_no_votes_means_no_winner(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    assert curated.winner(rnd.round_id) is None


def test_the_most_votes_wins(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "alice", "zero", locked=FREE)
    curated.cast_vote(rnd.round_id, "bob", "zero", locked=FREE)
    curated.cast_vote(rnd.round_id, "carol", "believer", locked=FREE)
    assert curated.winner(rnd.round_id) == "zero"


def test_a_tie_breaks_toward_the_least_recently_played(curated):
    """At 1-3 voters a tie is the normal case, so this is what actually picks
    the song most nights. Breaking toward least-recently-played rotates the
    catalogue instead of favouring whatever sorts first."""
    curated.ensure_round("christmas", "believer")      # believer played first
    curated.ensure_round("christmas", "zero")          # zero played more recently
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "alice", "zero", locked=FREE)
    curated.cast_vote(rnd.round_id, "bob", "believer", locked=FREE)
    assert curated.winner(rnd.round_id) == "believer"


def test_a_never_played_song_wins_a_tie(curated):
    curated.ensure_round("christmas", "believer")
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "alice", "believer", locked=FREE)
    curated.cast_vote(rnd.round_id, "bob", "barbie-girl", locked=FREE)
    assert curated.winner(rnd.round_id) == "barbie-girl"


def test_play_history_is_per_show(curated):
    """A song played to death at Christmas starts New Year's fresh."""
    for key in ("zero", "believer"):
        curated.ensure_round("christmas", key)
    assert curated.last_played_round("nye") == {}
    assert set(curated.last_played_round("christmas")) == {"zero", "believer"}


# ---------------------------------------------------------- voter identity
def test_voter_hash_is_stable_salted_and_one_way(store):
    token = "b3f1c2d4-random-browser-token"
    assert store.voter_hash(token) == store.voter_hash(token)
    assert store.voter_hash(token) != store.voter_hash("someone-else")
    assert token not in store.voter_hash(token)


def test_the_salt_is_generated_once_and_persists(db_path, store):
    salt = store.voter_salt()
    assert len(salt) == 64
    hashed = store.voter_hash("token")
    store.close()

    reopened = Store.open(db_path)
    assert reopened.voter_salt() == salt, "a new salt would reset every allowance"
    assert reopened.voter_hash("token") == hashed
    reopened.close()
