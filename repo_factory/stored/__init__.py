"""The contracts for what the loop stores as JSON: one frozen dataclass per column of
``loop.db``, in this package, so a reader never assumes a stored value's shape.

``base.py`` holds the contract machinery; ``testing.py`` the generated test data for it.

``COLUMNS`` fills as each type is defined; importing this package imports every family.
``json_columns()`` lists the loop ledger's JSON columns from its migrated schema;
``tests/test_stored.py`` fails when one has no contract and is not ``OPAQUE``.

The ledger hands its readers dicts, as it always has: :func:`kept` reads a column through its
contract and gives back the object as stored, every key it had (a null kept null), each
declared field of its declared type (a wrong type reads as the field's default, noted once),
unknown keys untouched. Code that wants the type reads ``T.load(idea["pitch"])``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ideas, projects
from .base import COLUMNS, Noter, Problem, Stored, note, problems_seen

__all__ = [
    "COLUMNS",
    "JSON_NAMES",
    "NOT_JSON",
    "PENDING",
    "Noter",
    "Problem",
    "Stored",
    "OPAQUE",
    "contract_for",
    "ideas",
    "json_columns",
    "kept",
    "load_all",
    "note",
    "problems_seen",
    "projects",
]

#: TEXT columns that hold JSON, by name (the inventory of CTR-DESIGN §1.6, checked against the
#: writers).
JSON_NAMES = frozenset(
    {
        "urls",
        "card",
        "pitch",
        "build",
        "gate",
        "publish",
        "signals",
        "data",
        "repos",
        "settings",
        "stats",
        "health",
        "bundle",
        "nominee",
        "research",
        "posted",
        "verdict_data",
        "v",
        "detail",
        "charter",
        "topic",
        "filed",
        "reply",
    }
)

#: (table, column) pairs whose name is in JSON_NAMES but that hold plain text, or none.
NOT_JSON = frozenset(
    {
        ("activity", "detail"),  # no writer stores it and no reader reads it yet
        ("projects", "health"),  # computed on read (projects/record.health), never stored
    }
)

#: JSON columns with no one contract type, and why.
OPAQUE: dict[tuple[str, str], str] = {
    ("meta", "v"): "one value per key, of the key's own shape (a scoping draft, the lane's "
    "marks, a counter): read with Ledger.meta, which never raises",
}


def load_all() -> dict[tuple[str, str], type[Stored]]:
    return COLUMNS


def contract_for(table: str, column: str) -> type[Stored] | None:
    return COLUMNS.get((table, column))


def json_columns(conn: sqlite3.Connection | None = None) -> list[tuple[str, str]]:
    """Every JSON column of ``loop.db`` as the migrations leave it: a TEXT column named in
    JSON_NAMES and not in NOT_JSON. ``conn``: a migrated ledger (a fresh one when None)."""
    if conn is None:
        from .. import migrate
        from ..loop.ledger import MIGRATIONS

        conn = sqlite3.connect(":memory:")
        migrate.apply(conn, MIGRATIONS, "meta")
    out = []
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    for t in tables:
        for _, name, kind, *_ in conn.execute(f'PRAGMA table_info("{t}")'):
            if (kind or "").upper() == "TEXT" and name in JSON_NAMES and (t, name) not in NOT_JSON:
                out.append((t, name))
    return out


def kept(cls: type[Stored], raw: object, *, none: bool = False) -> Any:
    """The column as the ledger's readers get it: ``raw`` (text, a dict or a list, or None) read
    through ``cls``, as a dict with every key ``raw`` had (a list for a list-rooted type), each
    declared field of its type. NULL, empty or unreadable: the type's empty value, or None with
    ``none`` (a column whose readers tell "none" from an empty object)."""
    if isinstance(raw, list) and cls.LIST_ROOT:
        raw = {cls.LIST_ROOT: raw}
    got = cls.parse(raw if isinstance(raw, (str, bytes, dict)) else None)
    if got is None and none:
        return None
    value = got if got is not None else cls.empty()
    if cls.LIST_ROOT:
        return value.to_dict(nulls=True)[cls.LIST_ROOT]
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    full = value.to_dict(nulls=True)
    return {k: full.get(k) for k in raw} if isinstance(raw, dict) else {}
