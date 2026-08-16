"""
Connections, pragmas and migrations. Nothing here knows what a song is.

Why this file exists at all, rather than `sqlite3.connect` wherever it is
needed:

  * `PRAGMA foreign_keys` is PER-CONNECTION. The `PRAGMA foreign_keys = ON` at
    the top of schema.sql applies only to the connection that ran the script;
    every other connection has foreign keys OFF and every FK in the schema is
    decorative. That is a silent, data-losing default. connect() sets it every
    time, and tests assert it on a fresh connection.

  * FastAPI runs sync endpoint functions in a threadpool, so more than one
    thread will touch the database. sqlite3 connection objects are not safe to
    share across threads, so each thread gets its own (see Database). WAL plus
    a busy timeout is what makes concurrent readers and one writer work without
    a lock we would have to remember to take everywhere.

  * A half-applied migration on the Pi in the middle of December is the failure
    mode worth designing against. migrate() has an explicit version ladder and
    refuses outright to open a database newer than the code understands, rather
    than running against a schema it does not know.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Bump only alongside a new branch in the migrate() ladder below.
SCHEMA_VERSION = 1

# Long enough to ride out another writer's transaction, short enough that a
# genuinely stuck lock shows up as an error instead of a hung page.
BUSY_TIMEOUT_MS = 5000


class DatabaseTooNew(RuntimeError):
    """The file on disk was written by a newer version of this code.

    Raised instead of proceeding: an older binary writing to a newer schema
    corrupts data quietly, and a show night is a bad time to find out.
    """


def connect(path: str | Path) -> sqlite3.Connection:
    """Open one connection with every pragma this project depends on."""
    path = str(path)
    # isolation_level=None puts the driver in autocommit mode, which is what
    # lets us issue our own explicit BEGIN IMMEDIATE. Without it the driver
    # decides when a transaction starts and Store.cast_vote cannot guarantee
    # that its count-then-insert is atomic.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        # WAL is a property of the database file, so this is a no-op after the
        # first time. NORMAL is the right durability trade on an SD card: a
        # power cut can lose the last commits but cannot corrupt the file.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Version recorded in the file. 0 means "empty, never migrated"."""
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0                      # schema_meta itself does not exist yet
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database up to SCHEMA_VERSION. Returns the version reached.

    Adding a version means adding a branch here, never editing schema.sql in
    place — schema.sql describes a fresh install, the ladder describes how an
    existing one catches up.
    """
    version = schema_version(conn)

    if version > SCHEMA_VERSION:
        raise DatabaseTooNew(
            f"database is at schema version {version}, this code understands "
            f"{SCHEMA_VERSION}. Upgrade the plugin rather than downgrading the "
            f"database."
        )

    if version == 0:
        conn.executescript(SCHEMA_PATH.read_text())
        version = schema_version(conn)

    # if version < 2:
    #     conn.executescript(...)
    #     conn.execute("UPDATE schema_meta SET value='2' WHERE key='version'")
    #     version = 2

    return version


class Database:
    """A migrated database, plus one connection per thread.

    Thread-local rather than a single shared connection because FastAPI's
    threadpool means we do not control which thread we are on, and rather than
    one global lock because BEGIN IMMEDIATE already gives us the serialisation
    we actually need — at the point where it matters, not everywhere.

    Note that `:memory:` gives each thread its OWN empty database, so it is
    only usable single-threaded. Tests use a file under tmp_path.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        # Eagerly, in the constructing thread: a DatabaseTooNew should surface
        # at startup, not on the first request of the night.
        migrate(self.connection)

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.path)
            self._local.conn = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def transaction(self):
        """Explicit BEGIN IMMEDIATE ... COMMIT, re-entrant.

        IMMEDIATE takes the write lock up front. The alternative — a deferred
        transaction that upgrades on first write — can fail partway through
        with SQLITE_BUSY after the caller has already read the state it is
        deciding on, which is exactly the check-then-insert race in cast_vote.

        Re-entrant so that a method holding a transaction can call another one
        that also wants one; only the outermost commits.
        """
        conn = self.connection
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            conn.execute("BEGIN IMMEDIATE")
        self._local.depth = depth + 1
        try:
            yield conn
        except BaseException:
            self._local.depth = depth
            if depth == 0:
                conn.execute("ROLLBACK")
            raise
        else:
            self._local.depth = depth
            if depth == 0:
                conn.execute("COMMIT")

    def close(self) -> None:
        """Close THIS thread's connection. Other threads keep theirs."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            self._local.depth = 0
