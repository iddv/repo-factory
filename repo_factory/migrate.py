"""Numbered migrations for repo_factory's own SQLite files (CR-PLAN S-7, D-8): the loop's ``loop.db``
(``loop/ledger.py``) and a build's den (``den/store.py``).

A migration is ``(number, SQL, added columns)``. ``apply`` runs, in order, every migration above
the number recorded in the file (``schema_version`` in its key-value table: ``meta`` in
``loop.db``, ``schema_meta`` in a den) and records each one. Migration 1 is each file's schema
as it was when numbering began, written so that it changes nothing on a file that already has
it: an existing file is detected as that baseline and only marked. An added column is added only
when it is missing, for the files that predate it.

**Migrations only add** (tests/test_migrations_additive.py): tables, indexes, triggers, views,
nullable or defaulted columns. An update's rollback reinstalls the old code and keeps the file
the new code migrated, so the old code must start on it; the old code ignores the version row.
"""

from __future__ import annotations

import json
import sqlite3
import time

# (number, SQL script, ((table, column, declaration), ...))
Migration = tuple[int, str, tuple[tuple[str, str, str], ...]]
KEY = "schema_version"


def wal(c: sqlite3.Connection, wait_s: float = 30.0) -> None:
    """``PRAGMA journal_mode=WAL``, waiting while another connection holds the file. SQLite
    answers the switch on a new file with an immediate "database is locked" while another
    connection has it open, without the busy handler the connection's ``timeout`` sets: eight
    ledgers opened at once on a new loop.db failed about one time in ten
    (tests/test_migrate_race.py)."""
    end, pause = time.monotonic() + wait_s, 0.005
    while True:
        try:
            c.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= end:
                raise
        time.sleep(pause)
        pause = min(pause * 2, 0.25)


def current(c: sqlite3.Connection, table: str) -> int:
    """The number of the last migration recorded in the file (0: none yet)."""
    c.execute(f"CREATE TABLE IF NOT EXISTS {table} (k TEXT PRIMARY KEY, v TEXT)")
    row = c.execute(f"SELECT v FROM {table} WHERE k = ?", (KEY,)).fetchone()
    return int(json.loads(row[0])) if row else 0


def apply(c: sqlite3.Connection, migrations: list[Migration], table: str) -> list[int]:
    """Run the migrations the file has not had, in order; returns their numbers."""
    have, done = current(c, table), []
    for n, script, columns in sorted(migrations, key=lambda m: m[0]):
        if n <= have:
            continue
        c.executescript(script)
        for tbl, name, decl in columns:
            if name not in {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")}:
                try:
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError as e:
                    # another connection added it between our look and our ALTER: the page's
                    # first reads open the ledger at once on a new file (PAR: GET /api/state
                    # answered 500 "duplicate column name: kind", and the trace page waited)
                    if "duplicate column name" not in str(e):
                        raise
        c.execute(f"INSERT OR REPLACE INTO {table} (k, v) VALUES (?, ?)", (KEY, json.dumps(n)))
        done.append(n)
    return done
