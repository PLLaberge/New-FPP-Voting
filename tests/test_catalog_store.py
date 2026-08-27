"""The catalogue side of the store.

The theme of every test here is the same: a re-sync must never undo a human.
"""
import pytest

from fppvote.catalog.metadata import META, SONG_CATEGORIES
from fppvote.catalog.reconcile import Membership
from tests.conftest import christmas_rows as _rows, sync_nye


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
    song = curated.get_song("zero")
    assert song.categories == SONG_CATEGORIES["zero"]


def test_a_songs_categories_are_the_same_in_every_show_it_appears_in(curated):
    """Global since 2026-08-26 (see CLAUDE.md) — no per-show suggestion
    machinery is needed any more. A song curated once keeps the same
    categories under every show that reconciles it in."""
    report = sync_nye(curated)
    assert len(report.added) == 26
    solo = curated.get_song("barbie-girl").categories
    assert solo == SONG_CATEGORIES["barbie-girl"]
    xmas_row = next(s for s in curated.list_show_songs("christmas") if s.key == "barbie-girl")
    nye_row = next(s for s in curated.list_show_songs("nye") if s.key == "barbie-girl")
    assert xmas_row.categories == nye_row.categories == solo


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
    assert kept.categories == SONG_CATEGORIES["barbie-girl"], "categories must survive"
    assert curated.tally(rnd.round_id) == {"barbie-girl": 1}, "votes must survive"


def test_returning_song_is_reactivated_with_its_categories(curated):
    curated.sync_show("christmas", _rows(exclude={"barbie-girl"}), metadata=META)
    report = curated.sync_show("christmas", _rows(), metadata=META)
    assert report.reactivated == ["barbie-girl"]
    songs = {s.key: s for s in curated.list_show_songs("christmas")}
    assert songs["barbie-girl"].categories == SONG_CATEGORIES["barbie-girl"]


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
    orphaned = curated.set_category_vocabulary(
        [c for c in curated.list_categories() if c != "Crooners"])
    assert orphaned == ["Crooners"]


def test_the_category_vocabulary_is_global_not_per_show(curated):
    """Since 2026-08-26 (see CLAUDE.md) there is one vocabulary for the whole
    install — a show no longer has "its own" chips, and list_categories
    takes no show_id at all."""
    sync_nye(curated)
    before = curated.list_categories()
    curated.set_category_vocabulary(before + ["Spooky"])
    assert "Spooky" in curated.list_categories()


def test_the_voter_page_only_sees_chips_that_have_songs(curated):
    """A chip with nothing behind it is a dead end for a viewer, but the admin
    page still needs to see it to curate against."""
    counts = curated.category_counts()
    assert counts["Crooners"] == 6

    crooners = [s.key for s in curated.list_show_songs("christmas")
                if "Crooners" in s.categories]
    curated.sync_show("christmas", _rows(exclude=set(crooners)), metadata=META)

    assert curated.category_counts()["Crooners"] == 0
    assert "Crooners" not in curated.list_categories(non_empty=True)
    assert "Crooners" in curated.list_categories(), \
        "the vocabulary itself is curated and must survive"

    # and the chip comes back with its songs
    curated.sync_show("christmas", _rows(), metadata=META)
    assert "Crooners" in curated.list_categories(non_empty=True)


def test_a_fresh_vocabulary_offers_no_chips_until_songs_are_curated(store):
    store.set_category_vocabulary(["Scary", "Spooky", "Funny"])
    assert store.list_categories(non_empty=True) == []
    assert store.category_counts() == {"Scary": 0, "Spooky": 0, "Funny": 0}


def test_a_category_outside_the_vocabulary_is_refused(curated):
    """An unrecognised category renders no chip, so the song silently drops out
    of every filtered view while still showing under "All" — invisible until
    someone goes looking for it."""
    with pytest.raises(ValueError, match="Rock and Roll"):
        curated.set_categories("zero", ["Rock and Roll"])


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
    assert curated.category_counts()["Instrumental"] == 6


def test_a_song_cannot_disagree_with_itself_about_being_instrumental(curated):
    """The historical bug this test used to guard against — a song was
    Instrumental under one show and not the other — is now structurally
    impossible: categories are global, so there is only one list to agree or
    disagree with."""
    sync_nye(curated)
    xmas = {s.key: s.categories for s in curated.list_show_songs("christmas")}
    nye = {s.key: s.categories for s in curated.list_show_songs("nye")}
    shared = xmas.keys() & nye.keys()
    assert shared        # sanity: there is real overlap to check
    for key in shared:
        assert xmas[key] == nye[key]


# ------------------------------------------------------------------ shows
def test_seeding_again_does_not_reset_global_voting_rules(christmas):
    """votes_per_round/cooldown_songs are global settings (2026-08-25), not
    columns on `shows` -- create_show can never touch them, seed or reseed."""
    christmas.set_votes_per_round(1)
    christmas.set_cooldown_songs(7)
    assert christmas.create_show("christmas", "Christmas 2025", "Christmas 2025") is False
    assert christmas.votes_per_round() == 1 and christmas.cooldown_songs() == 7


# ------------------------------------------------------------- renamed fseq
def test_merging_a_renamed_sequence_moves_votes_and_categories(curated):
    """Renaming a .fseq creates a new song_key. Merging keeps the history."""
    rnd = curated.ensure_round("christmas", "hallelujah")
    curated.cast_vote(rnd.round_id, "voter", "believer")

    curated.upsert_song("believer-2026", "Believer", "Believer 2026.fseq")
    curated.save_memberships({
        ("christmas", "believer-2026"): Membership("christmas", "believer-2026")
    })

    curated.merge_songs("believer", "believer-2026")

    assert curated.tally(rnd.round_id) == {"believer-2026": 1}
    songs = {s.key: s for s in curated.list_show_songs("christmas")}
    assert "believer" not in songs
    assert songs["believer-2026"].categories == SONG_CATEGORIES["believer"]
    # the old key still resolves, so a stale link or open phone keeps working
    assert curated.resolve_key("believer") == "believer-2026"
    assert curated.get_song("believer").key == "believer-2026"


def test_categories_are_shared_between_christmas_and_nye(curated):
    """The old per-show independence (CLAUDE.md's original "Zero is Rock &
    Roll at Christmas, Dance Tunes at New Year's" example) no longer applies —
    categories are global since 2026-08-26."""
    sync_nye(curated)
    curated.sync_show("christmas", _rows(), metadata=META)
    xmas = {s.key: s for s in curated.list_show_songs("christmas")}
    nye = {s.key: s for s in curated.list_show_songs("nye")}
    assert xmas["zero"].categories == nye["zero"].categories == SONG_CATEGORIES["zero"]


def test_seeding_refreshes_the_playlist_name_but_not_global_voting_rules(christmas):
    """Editing SHOW_DEFS has to actually reach an existing database.

    An insert-only seed would mean changing the playlist name in the file,
    re-running init_db.py, and silently getting nothing — an evening of
    debugging for a one-line change. Descriptive fields refresh; voting rules
    are global settings define_show never touches at all (2026-08-25).
    """
    christmas.set_votes_per_round(1)
    christmas.set_cooldown_songs(7)

    assert christmas.define_show("christmas", "Christmas 2025",
                                 "All_Xmas_Songs - Alphabetic") == "updated"
    show = christmas.get_show("christmas")
    assert show.playlist_name == "All_Xmas_Songs - Alphabetic"
    assert christmas.votes_per_round() == 1 and christmas.cooldown_songs() == 7

    assert christmas.define_show("christmas", "Christmas 2025",
                                 "All_Xmas_Songs - Alphabetic") == "unchanged"


def test_define_show_creates_when_missing(store):
    assert store.define_show("nye", "New Year's Eve 2026", "NY_Dance_Party") == "created"
    assert store.get_show("nye").playlist_name == "NY_Dance_Party"
