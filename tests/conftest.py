"""Shared fixtures for the database tests.

Everything runs against a real SQLite file under tmp_path, never ':memory:'.
Connections are thread-local, and each thread connecting to ':memory:' gets its
own separate empty database — which would make the concurrency tests pass for
entirely the wrong reason.
"""
import pytest

from fppvote.catalog.metadata import CHRISTMAS_CATS, META, NYE_CATS, SHOW_DEFS
from fppvote.catalog.parser import parse_playlist
from fppvote.db import Store
from tests.fixtures.playlists import CHRISTMAS, NYE

# Taken from SHOW_DEFS rather than restated, so the fixtures cannot drift from
# the vocabulary the seed script actually installs.
XCATS = SHOW_DEFS["christmas"]["categories"]
NCATS = SHOW_DEFS["nye"]["categories"]


def christmas_rows(exclude=()):
    """Parsed Christmas playlist, optionally minus some keys — how a song
    leaving the playlist is simulated."""
    rows, _ = parse_playlist(CHRISTMAS)
    return [r for r in rows if r["key"] not in exclude]


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "fppvote.db"


@pytest.fixture
def store(db_path):
    s = Store.open(db_path)
    yield s
    s.close()


@pytest.fixture
def christmas(store):
    """A seeded but empty Christmas show."""
    store.create_show("christmas", "Christmas 2025", "Christmas 2025",
                      tagline="Tap any song. The winner plays next.")
    store.set_show_categories("christmas", XCATS)
    return store


@pytest.fixture
def synced(christmas):
    """Christmas, with the real 65-song playlist synced in."""
    rows, _ = parse_playlist(CHRISTMAS)
    christmas.sync_show("christmas", rows, metadata=META)
    return christmas


def _curate(store, show_id, assignments):
    """Apply curated categories, skipping any outside the show's vocabulary.

    The skip exists because the real Christmas data assigns "Instrumental" to
    300-violin-orchestra and that is a New Year's chip — see
    test_a_category_outside_the_vocabulary_is_refused. These fixtures model a
    VALID curated state, so they filter rather than carry the error in.
    """
    valid = set(store.list_categories(show_id))
    for key, cats in assignments.items():
        store.set_categories(show_id, key, [c for c in cats if c in valid])


@pytest.fixture
def curated(synced):
    """Christmas, synced and fully categorised — the steady state."""
    _curate(synced, "christmas", CHRISTMAS_CATS)
    return synced


def sync_nye(store):
    """Add the New Year's show and sync its playlist."""
    store.create_show("nye", "New Year's Eve 2026", "New Years 2026",
                      note="Dec 29 - Jan 3", theme="nye")
    store.set_show_categories("nye", NCATS)
    rows, _ = parse_playlist(NYE)
    return store.sync_show("nye", rows, metadata=META)


def curate_nye(store):
    _curate(store, "nye", NYE_CATS)
