"""Reconciliation must never destroy hand-assigned categories."""
from fppvote.catalog.parser import parse_playlist
from fppvote.catalog.reconcile import reconcile
from fppvote.catalog.metadata import CHRISTMAS_CATS, NYE_CATS
from tests.fixtures.playlists import CHRISTMAS, NYE

XCATS = ["New this year", "Traditional", "Contemporary", "Spiritual", "Crooners",
         "Rock & Roll", "Sing-Along", "Kids & Movies", "Not-So-Christmasy"]
NCATS = ["New this year", "Countdown", "Dance Tunes", "Pop", "Rock",
         "Kids & Movies", "Throwback", "Instrumental"]


def _curated_christmas():
    rows, _ = parse_playlist(CHRISTMAS)
    store = {}
    reconcile("christmas", rows, store, XCATS)
    for (sid, key), m in store.items():
        if key in CHRISTMAS_CATS:
            m.categories = CHRISTMAS_CATS[key]
            m.source = "curated"
    return rows, store


def test_cold_start_flags_everything_for_review():
    rows, _ = parse_playlist(CHRISTMAS)
    store = {}
    rep = reconcile("christmas", rows, store, XCATS)
    assert len(rep.added) == 65
    assert len(rep.needs_review) == 65


def test_rerun_is_idempotent():
    """The nightly sync must not undo your curation."""
    rows, store = _curated_christmas()
    rep = reconcile("christmas", rows, store, XCATS)
    assert rep.added == [] and rep.deactivated == [] and rep.needs_review == []
    assert rep.unchanged == 65


def test_categories_carry_across_shows():
    rows, store = _curated_christmas()
    nrows, _ = parse_playlist(NYE)
    rep = reconcile("nye", nrows, store, NCATS)
    assert len(rep.suggested) == 20
    assert len(rep.needs_review) == 6
    for _, cats, _ in rep.suggested:
        assert all(c in NCATS for c in cats)


def test_removed_song_is_deactivated_not_deleted():
    rows, store = _curated_christmas()
    trimmed = [r for r in rows if r["key"] != "barbie-girl"]
    rep = reconcile("christmas", trimmed, store, XCATS)
    assert rep.deactivated == ["barbie-girl"]
    m = store[("christmas", "barbie-girl")]
    assert m.active is False
    assert m.categories, "categories must survive removal"


def test_returning_song_is_reactivated_with_categories():
    rows, store = _curated_christmas()
    trimmed = [r for r in rows if r["key"] != "barbie-girl"]
    reconcile("christmas", trimmed, store, XCATS)
    rep = reconcile("christmas", rows, store, XCATS)
    assert rep.reactivated == ["barbie-girl"]
    assert store[("christmas", "barbie-girl")].categories
