"""Connections and migrations — the guarantees the rest of the layer assumes."""
import sqlite3

import pytest

from fppvote.db import SCHEMA_VERSION, Database, DatabaseTooNew, connect, schema_version


def test_fresh_database_is_migrated(db_path):
    db = Database(db_path)
    assert schema_version(db.connection) == SCHEMA_VERSION
    db.close()


def test_reopening_changes_nothing(db_path, store):
    store.set_setting("hello", "world")
    store.close()
    again = Database(db_path)
    assert schema_version(again.connection) == SCHEMA_VERSION
    row = again.connection.execute(
        "SELECT value FROM settings WHERE key = 'hello'").fetchone()
    assert row["value"] == "world"
    again.close()


def test_foreign_keys_are_on_for_every_connection(db_path, store):
    """The PRAGMA in schema.sql applies only to the connection that ran it.

    If this ever fails, every foreign key in the schema is decorative on every
    connection but one, and a bad delete silently orphans rows.
    """
    fresh = connect(db_path)
    assert fresh.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        fresh.execute(
            "INSERT INTO show_songs(show_id, song_key) VALUES('nope', 'nope')")
    fresh.close()


def test_wal_is_enabled(store):
    mode = store.db.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_refuses_a_database_from_a_newer_version(db_path, store):
    """Better a refusal at startup than an old binary writing a new schema."""
    store.db.connection.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'version'",
        (str(SCHEMA_VERSION + 1),))
    store.close()
    with pytest.raises(DatabaseTooNew):
        Database(db_path)


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.db.transaction():
            store.set_setting("a", "1")
            raise RuntimeError("boom")
    assert store.get_setting("a") is None


def test_nested_transactions_commit_once(store):
    with store.db.transaction():
        store.set_setting("outer", "1")
        with store.db.transaction():
            store.set_setting("inner", "2")
        # still inside the outer transaction
        assert store.get_setting("inner") == "2"
    assert store.get_setting("outer") == "1"
    assert store.get_setting("inner") == "2"


def test_nested_rollback_discards_everything(store):
    with pytest.raises(RuntimeError):
        with store.db.transaction():
            store.set_setting("outer", "1")
            with store.db.transaction():
                store.set_setting("inner", "2")
            raise RuntimeError("boom")
    assert store.get_setting("outer") is None
    assert store.get_setting("inner") is None


def test_vote_allowance_range_is_structural(store):
    """1-3 is a product rule; the schema should not let anything else in."""
    store.create_show("x", "X", "P")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.connection.execute(
            "UPDATE shows SET votes_per_round = 9 WHERE show_id = 'x'")
    with pytest.raises(ValueError):
        store.update_show("x", votes_per_round=0)


def test_unknown_show_field_is_rejected(store):
    store.create_show("x", "X", "P")
    with pytest.raises(ValueError):
        store.update_show("x", playlist="typo-for-playlist_name")
