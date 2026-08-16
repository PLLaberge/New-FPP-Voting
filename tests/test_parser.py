"""Parser tests — every one of these is a real bug we found in Paulin's 2025
playlists, locked in so it cannot come back."""
import pytest

from fppvote.catalog.parser import (
    parse_playlist, clean_title, slugify, strip_track_number, titlecase_smallwords,
)
from tests.fixtures.playlists import CHRISTMAS, NYE


@pytest.fixture(scope="module")
def christmas():
    rows, issues = parse_playlist(CHRISTMAS)
    return {r["key"]: r for r in rows}, issues


@pytest.fixture(scope="module")
def nye():
    rows, issues = parse_playlist(NYE)
    return {r["key"]: r for r in rows}, issues


# ------------------------------------------------------------------ identity
def test_key_comes_from_sequence_not_index(christmas):
    rows, _ = christmas
    assert "zero" in rows
    assert all(k.replace("-", "").replace("'", "").isalnum() for k in rows)


def test_keys_are_unique(christmas):
    rows, _ = christmas
    assert len(rows) == len(set(rows))


def test_shared_media_still_produces_distinct_songs():
    """Two sequences pointing at one mp3 must stay two songs. Keying on media
    would silently merge them and destroy one song's stats."""
    entries = [
        ("300 Violin Orchestra.fseq", "01 - Santa Won't You (Bring Me Love).mp3", "02:47"),
        ("Santa Won't You (Bring Me Love).fseq", "01 - Santa Won't You (Bring Me Love).mp3", "02:47"),
    ]
    rows, issues = parse_playlist(entries)
    assert len(rows) == 2
    assert any(i["type"] == "shared_media" for i in issues)


def test_duplicate_sequence_is_deduped_and_flagged():
    entries = [
        ("Believer.fseq", "04 Believer.mp3", "03:24"),
        ("Believer.fseq", "04 Believer.mp3", "03:24"),
    ]
    rows, issues = parse_playlist(entries)
    assert len(rows) == 1
    assert any(i["type"] == "duplicate_entry" for i in issues)


# ------------------------------------------------------------------ titles
@pytest.mark.parametrize("media,expected", [
    ("01 Believer.mp3", "Believer"),
    ("01-04- Linus And Lucy.mp3", "Linus and Lucy"),
    ("205 - Nutcracker Trepak (Russian Dance).mp3", "Nutcracker Trepak (Russian Dance)"),
    ("3-15 What a Wonderful World (Single.mp3", "What a Wonderful World"),
])
def test_track_numbers_stripped(media, expected):
    title, _, _ = clean_title(media, "X.fseq")
    assert title == expected


def test_three_digit_title_is_not_eaten_as_track_number():
    """'300 Violin Orchestra' must survive — 3 digits with no separator is
    part of the name, not a track number."""
    title, _, _ = clean_title("13 300 Violin Orchestra.mp3", "300 Violin Orchestra.fseq")
    assert title == "300 Violin Orchestra"


def test_feat_moves_to_artist(christmas):
    rows, _ = christmas
    row = rows["silent-night-feat-reba-mcentire"]
    assert row["title"] == "Silent Night"
    assert row["feat"] == "Reba McEntire"


def test_unusable_media_falls_back_to_sequence(christmas):
    """taylorshow2a.mp3 tells a viewer nothing."""
    rows, _ = christmas
    assert rows["taylor-swift-show"]["title"] == "Taylor Swift Show"


def test_single_word_media_is_not_treated_as_unusable(christmas):
    rows, _ = christmas
    assert rows["believer"]["title"] == "Believer"


def test_small_words_lowercased():
    assert titlecase_smallwords("Life Is A Highway") == "Life Is a Highway"
    assert titlecase_smallwords("A Star To Follow") == "A Star to Follow"


# ------------------------------------------------------------------ truncation
def test_truncated_artist_auto_completed(christmas):
    rows, issues = christmas
    assert rows["we-three-kings"]["feat"] == "Casey Abrams"
    assert any("auto-corrected truncated artist" in i["detail"] for i in issues)


def test_truncated_qualifier_auto_completed(nye):
    rows, issues = nye
    assert rows["toccata-and-fugue-paranormal-remix"]["title"] == "Toccata and Fugue"
    assert any("Paranormal Remix" in i["detail"] for i in issues)


def test_unrecognised_truncation_is_flagged_not_guessed():
    _, _, notes = clean_title("01 Some Song (Zzzq Unknown Frag.mp3", "Some Song.fseq")
    assert any("please confirm" in n for n in notes)


# ------------------------------------------------------------------ real data
def test_christmas_playlist_shape(christmas):
    rows, issues = christmas
    assert len(rows) == 65
    assert not [i for i in issues if i["type"] == "duplicate_entry"]


def test_nye_playlist_shape(nye):
    rows, _ = nye
    assert len(rows) == 26


def test_shared_songs_use_identical_keys(christmas, nye):
    """20 songs appear in both shows. Same song, one catalog row."""
    xrows, _ = christmas
    nrows, _ = nye
    assert len(set(xrows) & set(nrows)) == 20


def test_ambiguous_display_titles_are_flagged(christmas):
    """Two Carol of the Bells sequences shorten to the same name."""
    _, issues = christmas
    assert any(i["type"] == "ambiguous_title" for i in issues)
