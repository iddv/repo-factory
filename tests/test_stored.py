"""The loop's stored JSON through its contracts (RCA 2026-10-23, A1; repo-factory-notes
plans/CTR-DESIGN.md §2): every registered type round-trips and never raises, its odd values
cover its fields, every JSON column of ``loop.db`` has one, and the ledger reads and writes
each idea's fields through them."""

from __future__ import annotations

import json
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings

from repo_factory import stored
from repo_factory.loop.ledger import CONTRACTS, Ledger
from repo_factory.stored import base, kept
from repo_factory.stored.ideas import Build, Gate, HistoryData, Pitch, Urls
from repo_factory.stored.testing import column_text, strategy

REGISTERED = sorted(stored.load_all().values(), key=lambda t: (t.where(), t.__name__))


@pytest.mark.parametrize("cls", REGISTERED, ids=lambda t: t.__name__)
def test_every_registered_contract_round_trips_and_never_raises(cls):
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(strategy(cls))
    def round_trip(v):
        assert cls.parse(v.to_json()) == v

    @settings(max_examples=200)
    @given(column_text)
    def total(raw):
        got = cls.parse(raw)
        assert got is None or isinstance(got, cls)
        assert isinstance(kept(cls, raw), list if cls.LIST_ROOT else dict)

    round_trip()
    total()


@pytest.mark.parametrize("cls", REGISTERED, ids=lambda t: t.__name__)
def test_every_registered_contracts_odd_values_cover_its_fields(cls):
    odd = cls.odd()
    for f in cls._fields():
        values = [getattr(o, f.name) for o in odd]
        if base._strip_optional(f.tp)[1]:
            assert None in values and any(v is not None for v in values), f.name
    for o in odd:
        assert cls.load(o.to_json()) == o


def test_json_columns_reads_the_schema():
    cols = stored.json_columns()
    assert ("ideas", "pitch") in cols and ("history", "data") in cols
    assert ("ideas", "context") not in cols  # the fetched links' text
    assert ("activity", "detail") not in cols


def test_every_json_column_has_a_contract():
    """A migration that adds a JSON column adds its contract in repo_factory.stored, or an OPAQUE
    entry saying why not."""
    missing = [
        f"{t}.{c}"
        for t, c in stored.json_columns()
        if (t, c) not in stored.COLUMNS and (t, c) not in stored.OPAQUE
    ]
    assert not missing, "JSON columns with no contract in repo_factory.stored: " + ", ".join(
        missing
    )
    assert set(stored.OPAQUE) <= set(stored.json_columns())
    # the inventory, corrected: the charter, its versions and the project's topic are JSON;
    # a project's health is computed on read
    cols = set(stored.json_columns())
    assert {
        ("projects", "charter"),
        ("projects", "topic"),
        ("charter_versions", "charter"),
        ("changes", "filed"),
    } <= cols
    assert ("projects", "health") not in cols
    assert {c for t, c in stored.COLUMNS if t == "ideas"} == set(CONTRACTS)


def test_kept_keeps_every_key_and_null_and_types_the_declared_fields():
    raw = json.dumps({"round": None, "pid": "12", "status": "running", "mine": [1, None]})
    assert kept(Build, raw) == {"round": None, "pid": None, "status": "running", "mine": [1, None]}
    for odd in (None, "", "not json", "[1, 2]", "3", "null"):
        assert kept(Build, odd) == {}
    assert kept(Urls, None) == [] and kept(Urls, '["https://a", 3]') == ["https://a"]
    assert kept(Urls, '{"x": 1}') == []


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "loop.db")


def test_the_ledger_reads_each_field_through_its_contract(ledger, tmp_path):
    idea, _ = ledger.add("a tool that sorts photos by place", "cli")
    with sqlite3.connect(tmp_path / "loop.db") as c:  # rows as older or odd writers left them
        c.execute(
            "UPDATE ideas SET pitch = ?, build = ?, gate = ?, publish = ?, urls = ?, "
            "signals = ? WHERE id = ?",
            (
                '{"buildability": "high", "product": "Placer", "sent": true}',
                "[]",
                "not json",
                None,
                '"one"',
                '{"github": 3}',
                idea["id"],
            ),
        )
        c.execute(
            "INSERT INTO history (idea, ts, state, note, data) VALUES (?, 0, 'new', 'x', ?)",
            (idea["id"], "[1]"),
        )
    got = ledger.must(idea["id"])
    assert got["pitch"] == {"buildability": None, "product": "Placer", "sent": True}
    assert got["build"] == {} and got["gate"] == {} and got["publish"] == {}
    assert got["urls"] == [] and got["signals"] == {"github": None}
    assert all(isinstance(h["data"], dict) for h in ledger.history(idea["id"]))


def test_the_ledger_writes_a_dict_whole_and_a_contract_as_its_text(ledger):
    idea, _ = ledger.add("a tool that sorts photos by place", "cli")
    out = ledger.update(
        idea["id"],
        build={"status": "queued", "round": None},
        gate=Gate(verdict="held", reasons=["a check failed"]),
        note="queued",
        history={"origin": "ui", "kind": "round_started"},
    )
    assert out["build"] == {"status": "queued", "round": None}  # the null kept
    assert out["gate"] == {
        "verdict": "held",
        "reasons": ["a check failed"],
        "checks": {},
        "gaps": [],
    }
    assert Pitch.load(out["pitch"]) == Pitch()
    [h] = [h for h in ledger.history(idea["id"]) if h["note"] == "queued"]
    assert HistoryData.load(h["data"]).origin == "ui" and h["data"]["kind"] == "round_started"


def test_a_project_and_a_change_read_through_their_contracts(ledger, tmp_path):
    from repo_factory.projects import changes
    from repo_factory.projects.record import Projects

    ps = Projects(ledger)
    p, _ = ps.create(name="placer", kind="built", repo={"repo": "owner/placer"}, note="made")
    assert p["repos"] == [{"role": "primary", "repo": "owner/placer"}]
    assert p["charter"] is None and p["topic"] is None and p["stats"] == {}
    with sqlite3.connect(tmp_path / "loop.db") as c:
        c.execute(
            "UPDATE projects SET charter = '[1]', stats = 'not json', repos = '{}' WHERE id = ?",
            (p["id"],),
        )
        c.execute(
            "INSERT INTO changes (id, project, fp, title, kind, class, state, card, "
            "filed, created, updated) VALUES ('c1', ?, 'f', 't', 'fix', 'small', 'new', "
            "'\"text\"', 'nope', 0, 0)",
            (p["id"],),
        )
        c.execute("INSERT INTO meta (k, v) VALUES ('odd', 'not json')")
    got = ps.must(p["id"])
    assert got["charter"] is None and got["stats"] == {} and got["repos"] == []
    [ch] = changes.all_of(ledger, p["id"])
    assert ch["card"] == {} and ch["filed"] is None
    assert ledger.meta("odd") is None
