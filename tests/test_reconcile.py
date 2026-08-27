"""Reconciliation tracks show membership only, additive and idempotent.

Categories moved off Membership entirely (2026-08-26, see CLAUDE.md) — they
are global, live on `songs`, and Store.sync_show reports on them separately,
not reconcile() itself. What's left here is what reconcile() actually still
owns: whether a song is currently active in a show's playlist, and its
position — and that a re-run never destroys any of it.
"""
from fppvote.catalog.parser import parse_playlist
from fppvote.catalog.reconcile import reconcile
from tests.fixtures.playlists import CHRISTMAS, NYE


def _synced_christmas():
    rows, _ = parse_playlist(CHRISTMAS)
    store = {}
    reconcile("christmas", rows, store)
    return rows, store


def test_cold_start_adds_every_song():
    rows, _ = parse_playlist(CHRISTMAS)
    store = {}
    rep = reconcile("christmas", rows, store)
    assert len(rep.added) == 65
    assert len(store) == 65


def test_rerun_is_idempotent():
    """The nightly sync must not touch membership that hasn't changed."""
    rows, store = _synced_christmas()
    rep = reconcile("christmas", rows, store)
    assert rep.added == [] and rep.deactivated == [] and rep.reactivated == []
    assert rep.unchanged == 65


def test_a_song_can_belong_to_two_shows_independently():
    """The same store dict stands in for the whole show_songs table —
    reconciling a second show must not disturb the first's membership."""
    rows, store = _synced_christmas()
    nrows, _ = parse_playlist(NYE)
    rep = reconcile("nye", nrows, store)
    assert len(rep.added) == 26
    assert store[("christmas", "barbie-girl")].active is True
    assert store[("nye", "barbie-girl")].active is True


def test_removed_song_is_deactivated_not_deleted():
    rows, store = _synced_christmas()
    trimmed = [r for r in rows if r["key"] != "barbie-girl"]
    rep = reconcile("christmas", trimmed, store)
    assert rep.deactivated == ["barbie-girl"]
    assert store[("christmas", "barbie-girl")].active is False


def test_returning_song_is_reactivated():
    rows, store = _synced_christmas()
    trimmed = [r for r in rows if r["key"] != "barbie-girl"]
    reconcile("christmas", trimmed, store)
    rep = reconcile("christmas", rows, store)
    assert rep.reactivated == ["barbie-girl"]
    assert store[("christmas", "barbie-girl")].active is True


def test_playlist_index_tracks_the_current_position():
    rows, store = _synced_christmas()
    barbie = next(r for r in rows if r["key"] == "barbie-girl")
    assert store[("christmas", "barbie-girl")].playlist_index == barbie["playlist_index"]
