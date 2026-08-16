"""The schema must load, and its key guarantees must actually hold."""
import sqlite3
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "fppvote" / "db" / "schema.sql"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO shows(show_id,name,playlist_name) VALUES('christmas','X','P')")
    conn.execute("INSERT INTO songs(song_key,title,sequence_name) VALUES('zero','Zero','Zero.fseq')")
    conn.execute("INSERT INTO rounds(round_id,show_id) VALUES(1,'christmas')")
    conn.commit()
    yield conn
    conn.close()


def test_schema_loads(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"songs", "shows", "show_songs", "show_categories", "rounds", "votes"} <= tables


def test_one_vote_per_person_per_song_per_round(db):
    db.execute("INSERT INTO votes(show_id,song_key,round_id,voter_hash) "
               "VALUES('christmas','zero',1,'abc')")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO votes(show_id,song_key,round_id,voter_hash) "
                   "VALUES('christmas','zero',1,'abc')")


def test_same_person_may_vote_again_next_round(db):
    db.execute("INSERT INTO votes(show_id,song_key,round_id,voter_hash) "
               "VALUES('christmas','zero',1,'abc')")
    db.execute("INSERT INTO rounds(round_id,show_id) VALUES(2,'christmas')")
    db.execute("INSERT INTO votes(show_id,song_key,round_id,voter_hash) "
               "VALUES('christmas','zero',2,'abc')")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 2


def test_categories_default_to_empty_json(db):
    db.execute("INSERT INTO show_songs(show_id,song_key) VALUES('christmas','zero')")
    db.commit()
    row = db.execute("SELECT categories, source FROM show_songs").fetchone()
    assert row[0] == "[]" and row[1] == "needs_review"
