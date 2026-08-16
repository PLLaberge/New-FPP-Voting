"""The catalogue side of the store.

The theme of every test here is the same: a re-sync must never undo a human.
"""
import pytest

from fppvote.catalog.metadata import CHRISTMAS_CATS, META
from fppvote.catalog.reconcile import Membership
from tests.conftest import christmas_rows as _rows, curate_nye, sync_nye


# ------------------------------------------------------------------- sync
def test_cold_start_adds_everything_for_review(christmas):
    report = christmas.sync_show("christmas", _rows(), metadata=META)
    assert len(report.added) == 65
    assert len(report.needs_review) == 65
    assert len(christmas.list_show_songs("christmas")) == 65


def test_resync_is_a_noop(curated):
    report = curated.sync_show("christmas", _rows(), metadata=META)
    assert report.added == [] and report.deactivated == [] and report.needs_review == []
    assert report.unchanged == 65


def test_resync_does_not_rewrite_unchanged_songs(synced):
    """updated_at is a sentinel here because datetime('now') only has second
    resolution — a rewrite inside the same second would look identical."""
    synced.db.connection.execute(
        "UPDATE songs SET updated_at = '2000-01-01 00:00:00'")
    synced.sync_show("christmas", _rows(), metadata=META)
    stale = synced.db.connection.execute(
        "SELECT COUNT(*) FROM songs WHERE updated_at != '2000-01-01 00:00:00'"
    ).fetchone()[0]
    assert stale == 0


def test_curation_survives_a_resync(curated):
    curated.sync_show("christmas", _rows(), metadata=META)
    songs = {s.key: s for s in curated.list_show_songs("christmas")}
    assert songs["zero"].categories == CHRISTMAS_CATS["zero"]
    assert songs["zero"].source == "curated"


def test_categories_carry_across_shows_through_the_database(curated):
    """20 of the 26 New Year's songs also play at Christmas. The suggestion
    only works if load_memberships hands reconcile EVERY show, not one."""
    report = sync_nye(curated)
    assert len(report.suggested) == 20
    assert len(report.needs_review) == 6
    valid = set(curated.list_categories("nye"))
    for _, cats, _ in report.suggested:
        assert cats and set(cats) <= valid


# ----------------------------------------------------- leaving and returning
def test_removed_song_is_deactivated_and_keeps_its_history(curated):
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "barbie-girl")

    report = curated.sync_show("christmas", _rows(exclude={"barbie-girl"}),
                               metadata=META)
    assert report.deactivated == ["barbie-girl"]

    visible = {s.key for s in curated.list_show_songs("christmas")}
    assert "barbie-girl" not in visible

    kept = {s.key: s for s in
            curated.list_show_songs("christmas", include_inactive=True)}["barbie-girl"]
    assert kept.active is False
    assert kept.categories == CHRISTMAS_CATS["barbie-girl"], "categories must survive"
    assert curated.tally(rnd.round_id) == {"barbie-girl": 1}, "votes must survive"


def test_returning_song_is_reactivated_with_its_categories(curated):
    curated.sync_show("christmas", _rows(exclude={"barbie-girl"}), metadata=META)
    report = curated.sync_show("christmas", _rows(), metadata=META)
    assert report.reactivated == ["barbie-girl"]
    songs = {s.key: s for s in curated.list_show_songs("christmas")}
    assert songs["barbie-girl"].categories == CHRISTMAS_CATS["barbie-girl"]


# -------------------------------------------------------------- songs table
def test_sync_fills_missing_metadata_but_never_overwrites(synced):
    synced.set_song_metadata("christmas-vacation", artist="Corrected By Hand",
                             year=1989)
    synced.sync_show("christmas", _rows(), metadata=META)
    song = synced.get_song("christmas-vacation")
    assert song.artist == "Corrected By Hand"
    assert song.year == 1989
    # and a gap the sync could legitimately fill is still filled
    assert synced.get_song("zero").artist == "Imagine Dragons"


def test_display_override_survives_a_resync(synced):
    synced.set_display_override("carol-of-the-bells", "Carol of the Bells (Danielle)")
    synced.sync_show("christmas", _rows(), metadata=META)
    songs = {s.key: s for s in synced.list_show_songs("christmas")}
    assert songs["carol-of-the-bells"].title == "Carol of the Bells (Danielle)"


def test_duration_is_stored_in_seconds(synced):
    # "03:25" in the playlist fixture
    assert synced.get_song("christmas-sarajevo-12-24-instrumental").duration_seconds == 205


# ------------------------------------------------------------ voter-facing
def test_uncategorised_songs_are_still_offered_to_voters(synced):
    """A gap in curation must never hide a song from the people voting."""
    songs = synced.list_show_songs("christmas")
    assert len(songs) == 65
    assert all(s.categories == [] for s in songs)
    assert all(s.needs_review for s in songs)


def test_dropping_a_category_reports_orphaned_assignments(curated):
    orphaned = curated.set_show_categories(
        "christmas", [c for c in curated.list_categories("christmas")
                      if c != "Crooners"])
    assert orphaned == ["Crooners"]


def test_each_show_has_its_own_chips(curated):
    """Categories are per show. A chip belongs to the show it suits, and two
    shows need share none at all."""
    sync_nye(curated)
    curate_nye(curated)
    xmas = set(curated.list_categories("christmas"))
    nye = set(curated.list_categories("nye"))
    assert "Crooners" in xmas and "Crooners" not in nye
    assert "Countdown" in nye and "Countdown" not in xmas
    # a chip may appear in two shows; the vocabularies are still independent
    assert "Instrumental" in xmas and "Instrumental" in nye

    # a Halloween vocabulary that overlaps neither
    curated.create_show("halloween", "Halloween 2026", "Halloween 2026",
                        theme="halloween")
    curated.set_show_categories("halloween", ["Scary", "Spooky", "Funny"])
    assert curated.list_categories("halloween") == ["Scary", "Spooky", "Funny"]
    assert not set(curated.list_categories("halloween")) & (xmas | nye)


def test_the_voter_page_only_sees_chips_that_have_songs(curated):
    """A chip with nothing behind it is a dead end for a viewer, but the admin
    page still needs to see it to curate against."""
    counts = curated.category_counts("christmas")
    assert counts["Crooners"] == 6

    crooners = [s.key for s in curated.list_show_songs("christmas")
                if "Crooners" in s.categories]
    curated.sync_show("christmas", _rows(exclude=set(crooners)), metadata=META)

    assert curated.category_counts("christmas")["Crooners"] == 0
    assert "Crooners" not in curated.list_categories("christmas", non_empty=True)
    assert "Crooners" in curated.list_categories("christmas"), \
        "the vocabulary itself is curated and must survive"

    # and the chip comes back with its songs
    curated.sync_show("christmas", _rows(), metadata=META)
    assert "Crooners" in curated.list_categories("christmas", non_empty=True)


def test_a_brand_new_show_offers_no_chips_until_songs_are_curated(store):
    store.create_show("halloween", "Halloween 2026", "Halloween 2026")
    store.set_show_categories("halloween", ["Scary", "Spooky", "Funny"])
    assert store.list_categories("halloween", non_empty=True) == []
    assert store.category_counts("halloween") == {"Scary": 0, "Spooky": 0, "Funny": 0}


def test_a_category_outside_the_vocabulary_is_refused(curated):
    """An unrecognised category renders no chip, so the song silently drops out
    of every filtered view while still showing under "All" — invisible until
    someone goes looking for it.

    This caught a real error on its first run: CHRISTMAS_CATS gave
    300-violin-orchestra the category "Instrumental" when that was a New Year's
    chip only. Christmas has since gained its own Instrumental chip and all
    five of its instrumental tracks carry it, so the two cases below are a typo
    and a chip borrowed from another show.
    """
    sync_nye(curated)
    with pytest.raises(ValueError, match="Rock and Roll"):
        curated.set_categories("christmas", "zero", ["Rock and Roll"])
    with pytest.raises(ValueError, match="Crooners"):
        curated.set_categories("nye", "zero", ["Crooners"])


def test_christmas_instrumentals_carry_the_chip(curated):
    """The tagging that came with the new chip, asserted rather than assumed."""
    tagged = {s.key for s in curated.list_show_songs("christmas")
              if "Instrumental" in s.categories}
    assert tagged == {
        "300-violin-orchestra",
        "christmas-sarajevo-12-24-instrumental",
        "first-snow-instrumental",
        "carol-of-the-bells-foster-instrumental",
        "wizards-in-winter-instrumental",
        "music-box-dancer-radio-version",
    }
    assert curated.category_counts("christmas")["Instrumental"] == 6


def test_songs_in_both_shows_agree_on_being_instrumental(curated):
    """Categories are per show, but "is this an instrumental?" is a fact about
    the recording, not about the night. Where a song plays at both shows and
    both have the chip, disagreeing is an oversight rather than a choice —
    which is exactly what music-box-dancer-radio-version was.
    """
    sync_nye(curated)
    curate_nye(curated)
    xmas = {s.key: s.categories for s in curated.list_show_songs("christmas")}
    nye = {s.key: s.categories for s in curated.list_show_songs("nye")}
    disagree = {key for key in xmas.keys() & nye.keys()
                if ("Instrumental" in xmas[key]) != ("Instrumental" in nye[key])}
    assert disagree == set()


# ------------------------------------------------------------------ shows
def test_seeding_again_does_not_reset_admin_settings(christmas):
    christmas.update_show("christmas", votes_per_round=1, cooldown_songs=7)
    assert christmas.create_show("christmas", "Christmas 2025", "Christmas 2025") is False
    show = christmas.get_show("christmas")
    assert show.votes_per_round == 1 and show.cooldown_songs == 7


# ------------------------------------------------------------- renamed fseq
def test_merging_a_renamed_sequence_moves_votes_and_categories(curated):
    """Renaming a .fseq creates a new song_key. Merging keeps the history."""
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "believer")

    curated.upsert_song("believer-2026", "Believer", "Believer 2026.fseq")
    curated.save_memberships({
        ("christmas", "believer-2026"):
            Membership("christmas", "believer-2026", [], source="needs_review")
    })

    curated.merge_songs("believer", "believer-2026")

    assert curated.tally(rnd.round_id) == {"believer-2026": 1}
    songs = {s.key: s for s in curated.list_show_songs("christmas")}
    assert "believer" not in songs
    assert songs["believer-2026"].categories == CHRISTMAS_CATS["believer"]
    # the old key still resolves, so a stale link or open phone keeps working
    assert curated.resolve_key("believer") == "believer-2026"
    assert curated.get_song("believer").key == "believer-2026"


def test_nye_curation_is_independent_of_christmas(curated):
    sync_nye(curated)
    curate_nye(curated)
    curated.sync_show("christmas", _rows(), metadata=META)
    xmas = {s.key: s for s in curated.list_show_songs("christmas")}
    nye = {s.key: s for s in curated.list_show_songs("nye")}
    assert xmas["zero"].categories == ["Rock & Roll", "Not-So-Christmasy"]
    assert nye["zero"].categories == ["Rock", "Pop"]
