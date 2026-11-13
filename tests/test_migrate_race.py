"""Two connections migrating one new file at once (PAR: CI's flaky dark trace test). The page's
first reads open the loop's ledger at the same moment on a new loop.db; both saw an added column
missing, the second ALTER found it there, and GET /api/state answered 500 "duplicate column
name: kind", so the trace page never drew."""

import json
import sqlite3
import threading

from repo_factory import migrate
from repo_factory.loop import ledger as ledger_mod
from repo_factory.loop.ledger import Ledger


def test_a_column_another_connection_added_meanwhile_is_no_error(tmp_path):
    """The race made exact: the column is already there when this connection, which looked
    before the other added it, runs its ALTER."""
    db = tmp_path / "loop.db"
    Ledger(db)  # migrated: every column there
    added = [m for m in ledger_mod.MIGRATIONS if m[2]]
    assert added, "a loop.db migration that adds a column"
    n = added[-1][0]
    c = sqlite3.connect(db)
    c.execute("UPDATE meta SET v = ? WHERE k = ?", (json.dumps(n - 1), migrate.KEY))
    c.commit()

    class LookedEarlier:
        """A connection whose look at the table came before the other's ALTER."""

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA table_info"):
                return iter([(0, "id")])
            return c.execute(sql, *a)

        def executescript(self, script):
            return c.executescript(script)

    assert migrate.apply(LookedEarlier(), ledger_mod.MIGRATIONS, "meta") == sorted(
        m[0] for m in ledger_mod.MIGRATIONS if m[0] >= n
    )


def test_another_error_of_an_alter_still_raises(tmp_path):
    c = sqlite3.connect(tmp_path / "x.db")
    c.execute("CREATE TABLE t (id INTEGER)")
    bad = [(1, "", (("t", "y", "NOT A TYPE ((("),))]
    try:
        migrate.apply(c, bad, "meta")
    except sqlite3.OperationalError as e:
        assert "duplicate column" not in str(e)
    else:
        raise AssertionError("a broken ALTER was swallowed")


def test_ledgers_opened_at_once_on_a_new_file_all_open(tmp_path):
    db, errors = tmp_path / "fresh" / "loop.db", []
    db.parent.mkdir()

    def one():
        try:
            Ledger(db)
        except Exception as e:  # noqa: BLE001 - what the page's 500 was
            errors.append(repr(e))

    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_the_wal_switch_waits_while_another_connection_holds_the_file():
    """What the race came down to: the switch to WAL answered "database is locked" at once,
    without the connection's busy timeout; ``migrate.wal`` waits and tries again."""
    calls = []

    class Busy:
        def execute(self, sql, *a):
            calls.append(sql)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")

    migrate.wal(Busy())
    assert calls == ["PRAGMA journal_mode=WAL"] * 3

    class Broken:
        def execute(self, sql, *a):
            raise sqlite3.OperationalError("disk I/O error")

    try:
        migrate.wal(Broken())
    except sqlite3.OperationalError as e:
        assert "disk" in str(e)
    else:
        raise AssertionError("another error was swallowed")
