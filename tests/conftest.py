"""Shared fixtures for the database tests.

Everything runs against a real SQLite file under tmp_path, never ':memory:'.
Connections are thread-local, and each thread connecting to ':memory:' gets its
own separate empty database — which would make the concurrency tests pass for
entirely the wrong reason.
"""
import pytest

from fppvote.catalog.metadata import CATEGORIES, META, SONG_CATEGORIES
from fppvote.catalog.parser import parse_playlist
from fppvote.db import Store
from tests.fixtures.playlists import CHRISTMAS, NYE


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
    """A seeded but empty Christmas show. Also seeds the global category
    vocabulary (2026-08-26, see CLAUDE.md) — categories are no longer per
    show, so this is where every fixture built on top of `christmas` gets a
    vocabulary to assign against."""
    store.create_show("christmas", "Christmas 2025", "Christmas 2025",
                      tagline="Tap any song. The winner plays next.")
    store.set_category_vocabulary(CATEGORIES)
    return store


@pytest.fixture
def synced(christmas):
    """Christmas, with the real 65-song playlist synced in."""
    rows, _ = parse_playlist(CHRISTMAS)
    christmas.sync_show("christmas", rows, metadata=META)
    return christmas


def _curate(store, assignments):
    """Apply curated categories, skipping any outside the vocabulary.

    Global since 2026-08-26 — no show_id. The skip exists because a song not
    yet synced (e.g. an NYE-only key when only Christmas has been synced)
    simply has no row to update; set_categories no-ops rather than erroring.
    """
    valid = set(store.list_categories())
    for key, cats in assignments.items():
        if store.get_song(key) is None:
            continue
        store.set_categories(key, [c for c in cats if c in valid])


@pytest.fixture
def curated(synced):
    """Christmas, synced and fully categorised — the steady state."""
    _curate(synced, SONG_CATEGORIES)
    return synced


def sync_nye(store):
    """Add the New Year's show and sync its playlist."""
    store.create_show("nye", "New Year's Eve 2026", "New Years 2026",
                      note="Dec 29 - Jan 3", theme="nye")
    rows, _ = parse_playlist(NYE)
    return store.sync_show("nye", rows, metadata=META)


def curate_nye(store):
    _curate(store, SONG_CATEGORIES)
