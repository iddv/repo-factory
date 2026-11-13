"""repo-factory's own SQLite files migrate by number, and only add. An update's rollback reinstalls the old code
and keeps the file the new code migrated, so an old version must start on a newer schema: a
migration may only create tables, indexes, triggers and views, add nullable or defaulted
columns, and seed the version table. A file made before numbering began is migration 1."""

import json
import re
import sqlite3

import pytest

from repo_factory import migrate
from repo_factory.den import store as den_store
from repo_factory.den.store import Den
from repo_factory.loop import ledger as ledger_mod
from repo_factory.loop.ledger import Ledger

ALLOWED = [
    re.compile(r"^CREATE (VIRTUAL )?TABLE (IF NOT EXISTS )?\w+", re.I),
    re.compile(r"^CREATE (UNIQUE )?INDEX (IF NOT EXISTS )?\w+", re.I),
    re.compile(r"^CREATE TRIGGER (IF NOT EXISTS )?\w+", re.I),
    re.compile(r"^CREATE VIEW (IF NOT EXISTS )?\w+", re.I),
    re.compile(r"^INSERT (OR IGNORE )?INTO (meta|schema_meta)\b", re.I),
]
FILES = {"loop.db": (ledger_mod.MIGRATIONS, "meta"), "den": (den_store.MIGRATIONS, "schema_meta")}


def statements(script: str) -> list[str]:
    out, buf = [], ""
    for line in script.splitlines():
        buf += re.sub(r"--.*$", "", line) + "\n"
        if sqlite3.complete_statement(buf):
            if buf.strip().rstrip(";").strip():
                out.append(" ".join(buf.split()).rstrip(";").strip())
            buf = ""
    if buf.strip():
        out.append(" ".join(buf.split()))
    return out


@pytest.mark.parametrize("name", FILES)
def test_every_migration_only_adds(name):
    migrations, _ = FILES[name]
    numbers = [n for n, _, _ in migrations]
    assert numbers == list(range(1, len(numbers) + 1)), "numbered 1, 2, 3, … with no gap"
    for n, script, columns in migrations:
        bad = [s[:120] for s in statements(script) if not any(p.match(s) for p in ALLOWED)]
        assert not bad, (name, n, bad)
        for table, column, decl in columns:
            assert "NOT NULL" not in decl.upper() or "DEFAULT" in decl.upper(), (
                name,
                n,
                table,
                column,
                "NOT NULL without DEFAULT",
            )


def version(path, table):
    c = sqlite3.connect(path)
    try:
        row = c.execute(f"SELECT v FROM {table} WHERE k = 'schema_version'").fetchone()
        return json.loads(row[0]) if row else None
    finally:
        c.close()


def test_a_new_file_starts_at_the_latest_migration(tmp_path):
    Ledger(tmp_path / "loop.db")
    assert version(tmp_path / "loop.db", "meta") == len(ledger_mod.MIGRATIONS)
    Den(tmp_path / "den")
    assert version(tmp_path / "den" / "den.sqlite", "schema_meta") == len(den_store.MIGRATIONS)


def test_a_file_from_before_numbering_is_the_baseline_and_keeps_its_rows(tmp_path):
    """loop.db as the code before S-7 left it (an older one, without the later spend columns):
    it is marked 1, gains what it lacked and the later migrations (2: empty now; 3: the
    loop's runs as they go, ``activity``), and keeps every row."""
    path = tmp_path / "loop.db"
    c = sqlite3.connect(path)
    c.executescript(ledger_mod.SCHEMA.replace("usd REAL NOT NULL);", "usd REAL NOT NULL);", 1))
    c.execute(
        "INSERT INTO ideas (id, fingerprint, source, text, state, created, updated) "
        "VALUES ('i00000001', 'fp', 'cli', 'an idea', 'new', 1, 1)"
    )
    c.execute("INSERT INTO meta (k, v) VALUES ('last_intake', '5')")
    c.commit()
    c.close()
    lg = Ledger(path)
    latest = ledger_mod.MIGRATIONS[-1][0]
    assert version(path, "meta") == latest == 3
    assert lg.get("i00000001")["text"] == "an idea" and lg.meta("last_intake") == 5
    tables = {
        r[0]
        for r in sqlite3.connect(path).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "activity" in tables and "nominations" not in tables
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(spend)")}
    assert {"in_tokens", "out_tokens", "project"} <= cols
    Ledger(path)  # opening again changes nothing
    assert version(path, "meta") == latest and lg.get("i00000001")["state"] == "new"


def test_a_later_migration_runs_once_and_is_recorded(tmp_path):
    c = sqlite3.connect(tmp_path / "x.db")
    first = [(1, "CREATE TABLE IF NOT EXISTS a (x TEXT);", ())]
    assert migrate.apply(c, first, "meta") == [1]
    later = first + [(2, "CREATE TABLE IF NOT EXISTS b (y TEXT);", (("a", "z", "INTEGER"),))]
    assert migrate.apply(c, later, "meta") == [2]
    assert migrate.apply(c, later, "meta") == []
    assert migrate.current(c, "meta") == 2
    assert {r[1] for r in c.execute("PRAGMA table_info(a)")} == {"x", "z"}


def test_a_read_only_ledger_never_creates_or_writes(tmp_path):
    missing = tmp_path / "none" / "loop.db"
    Ledger(missing, read_only=True)
    assert not missing.parent.exists()
    path = tmp_path / "loop.db"
    Ledger(path).meta("last_intake", 7)
    before = path.read_bytes()
    ro = Ledger(path, read_only=True)
    assert ro.meta("last_intake") == 7 and ro.all() == []
    with pytest.raises(sqlite3.OperationalError):
        ro.meta("last_intake", 8)
    assert path.read_bytes() == before
