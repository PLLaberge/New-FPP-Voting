"""
The adapter and the database, driven together over a simulated show.

Not the service — that is stage 4. This is the proof that the two halves
compose, and it pins the one rule that is easy to get wrong when the service
does arrive: what to do with a round when FPP stops answering.

Everything here runs off FakeFppAdapter, so a whole evening plays in a
millisecond and the same night happens every run.
"""
from fppvote.catalog.parser import parse_playlist, slugify
from fppvote.db import Store
from fppvote.fpp import STATUS_IDLE, STATUS_PLAYING, STATUS_UNKNOWN, from_catalog
from tests.conftest import christmas_rows
from tests.fixtures.playlists import CHRISTMAS


def build(store):
    """Wire a fake FPP to the same playlist the database was seeded from."""
    return from_catalog({"Christmas 2025": [(seq, media, length)
                                            for seq, media, length in CHRISTMAS]})


def follow(store, fpp, show_id="christmas"):
    """One iteration of what the service loop will do.

    The whole rule in four lines: open a round on a real song, close it when
    FPP is genuinely idle, and — critically — do NOTHING on 'unknown'.
    """
    status = fpp.get_status()
    if status.status == STATUS_PLAYING and status.sequence_name:
        return store.ensure_round(show_id, slugify(status.sequence_name))
    if status.status == STATUS_IDLE:
        store.close_open_round()
    return store.current_round()          # unknown: leave it alone


def test_a_played_song_becomes_a_round(curated):
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)

    rnd = follow(curated, fpp)
    assert rnd is not None
    assert rnd.song_key == slugify(fpp.get_status().sequence_name)
    assert curated.get_song(rnd.song_key) is not None, \
        "the adapter's sequence name must resolve to a catalogued song"


def test_polling_the_same_song_does_not_start_new_rounds(curated):
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)
    first = follow(curated, fpp)

    for _ in range(30):          # 30 seconds of 1 Hz polling
        fpp.tick(1)
        assert follow(curated, fpp).round_id == first.round_id


def test_votes_survive_fpp_dropping_out_mid_song(curated):
    """The failure this whole design is aimed at.

    FPP goes unreachable for a while and comes back replaying the same song. If
    that were treated as a new song, every vote cast so far would be discarded
    mid-song — the exact moment the votes matter most.
    """
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)
    rnd = follow(curated, fpp)
    curated.cast_vote(rnd.round_id, "alice", "zero", locked=frozenset())
    curated.cast_vote(rnd.round_id, "bob", "zero", locked=frozenset())

    fpp.go_offline()
    for _ in range(20):
        assert fpp.get_status().status == STATUS_UNKNOWN
        assert follow(curated, fpp).round_id == rnd.round_id

    fpp.go_online()
    assert follow(curated, fpp).round_id == rnd.round_id
    assert curated.tally(rnd.round_id) == {"zero": 2}, "votes must survive"


def test_a_song_change_closes_one_round_and_opens_the_next(curated):
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)
    first = follow(curated, fpp)
    curated.cast_vote(first.round_id, "alice", "zero", locked=frozenset())

    fpp.play_to_end_of_song()
    second = follow(curated, fpp)

    assert second.round_id != first.round_id
    assert curated.get_round(first.round_id).is_open is False
    assert curated.tally(second.round_id) == {}, "votes reset every song"
    assert curated.tally(first.round_id) == {"zero": 1}, "history is kept"


def test_a_vote_result_is_played_by_index_not_by_identity(curated):
    """song_key identifies the song; the playlist index is only ever how FPP is
    told where to go. Looked up fresh, never stored as identity."""
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)
    rnd = follow(curated, fpp)
    curated.cast_vote(rnd.round_id, "alice", "barbie-girl", locked=frozenset())

    winner = curated.winner(rnd.round_id)
    assert winner == "barbie-girl"

    entries = fpp.get_playlist("Christmas 2025")
    index = next(e.index for e in entries if slugify(e.sequence_name) == winner)
    fpp.start_at_item("Christmas 2025", index)

    assert slugify(fpp.get_status().sequence_name) == winner
    assert fpp.commands[-1] == ("Christmas 2025", index)


def _play_evening(store, fpp, rounds, choose):
    """Run `rounds` rounds, letting `choose(candidates)` pick what to vote for.

    Returns what actually played, in order.
    """
    fpp.start_at_item("Christmas 2025", 1)
    entries = fpp.get_playlist("Christmas 2025")
    index_of = {slugify(e.sequence_name): e.index for e in entries}
    played = []

    for _ in range(rounds):
        rnd = follow(store, fpp)
        played.append(rnd.song_key)
        locked = store.locked_keys()
        candidates = [s.key for s in store.list_show_songs("christmas")
                      if s.key not in locked]
        for key in choose(candidates):
            store.cast_vote(rnd.round_id, "alice", key)
        winner = store.winner(rnd.round_id)
        store.set_winner(rnd.round_id, winner)
        fpp.start_at_item("Christmas 2025", index_of[winner])
    return played


def test_no_song_repeats_inside_the_cooldown_window(curated):
    """The playing song and the last 4 played are locked, so any five
    consecutive rounds must be five different songs — however the voting goes.

    Checked against the most hostile voter there is: one who always picks the
    earliest song still available. That walks a narrow cycle rather than
    exploring the catalogue, which is fine — the cooldown is what guarantees
    variety at this turnout, not the tie-break.
    """
    fpp = build(curated)
    played = _play_evening(curated, fpp, 20, lambda candidates: candidates[:1])

    window = curated.cooldown_songs() + 1
    for start in range(len(played) - window + 1):
        chunk = played[start:start + window]
        assert len(set(chunk)) == window, f"repeat inside the cooldown: {chunk}"


def test_a_tie_breaks_toward_the_least_recently_played(curated):
    """At 1-3 voters most rounds end in a tie, so this is what really picks the
    song. Voting for two songs at once makes every round a tie on purpose."""
    fpp = build(curated)
    fpp.start_at_item("Christmas 2025", 1)
    rnd = follow(curated, fpp)

    # play two songs so they have history, then tie them against each other
    fpp.play_to_end_of_song()
    older = follow(curated, fpp).song_key
    fpp.play_to_end_of_song()
    newer = follow(curated, fpp).song_key
    fpp.play_to_end_of_song()

    current = follow(curated, fpp)
    curated.cast_vote(current.round_id, "alice", older, locked=frozenset())
    curated.cast_vote(current.round_id, "bob", newer, locked=frozenset())

    assert curated.tally(current.round_id) == {older: 1, newer: 1}
    assert curated.winner(current.round_id) == older


def test_low_turnout_still_reaches_deep_into_the_catalogue(curated):
    """A voter who picks the least-recently-played song they can see should
    walk the catalogue rather than circling a handful of tracks."""
    fpp = build(curated)

    def choose(candidates):
        # the store's own history, not a tally kept on the side
        history = curated.last_played_round()
        return [min(candidates, key=lambda key: history.get(key, -1))]

    played = _play_evening(curated, fpp, 20, choose)
    assert len(set(played)) == 20, "every round played a different song"


def test_the_playlist_is_the_source_of_truth_for_what_is_live(curated):
    """Reconciling from the adapter is the same operation as reconciling from
    the fixtures — the adapter is just where the rows come from at runtime."""
    fpp = build(curated)
    entries = fpp.get_playlist("Christmas 2025")
    rows = [{"key": slugify(e.sequence_name), "title": e.sequence_name,
             "sequence": e.sequence_name, "media": e.media_name,
             "length": e.duration_seconds, "playlist_index": e.index}
            for e in entries if e.enabled]

    report = curated.sync_show("christmas", rows)
    assert report.added == [] and report.deactivated == []
    assert report.unchanged == 65
