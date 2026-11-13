"""Characterisation of events.fold (CR-PLAN S-14, before it becomes a dispatch table): the
state of every build in two logs, pinned whole.

- ``fixtures/events/every_kind.jsonl``: every event type fold reads, each with the fields it
  reads, in three builds (the older pipelines' shapes, a v5 revise round, one still running);
  ``make_every_kind.py`` wrote it.
- ``fixtures/v5_run10/events.jsonl``: a real v5 build's log.

The folds are in ``tests/snapshots/fold_*.json``; a change shows up as their diff. Regenerate
on purpose only: ``REPO_FACTORY_SNAPSHOT=1 pytest tests/test_events_fold_snapshot.py``."""

import json
import os
from pathlib import Path

import pytest

from repo_factory import events

FIX = Path(__file__).parent / "fixtures"
SNAP = Path(__file__).parent / "snapshots"
LOGS = {
    "every_kind": FIX / "events" / "every_kind.jsonl",
    "v5_run10": FIX / "v5_run10" / "events.jsonl",
}


def _folds(log: Path) -> dict:
    evs = events.read_events(log)
    return {str(b): events.fold(evs, b) for b in sorted({e["build"] for e in evs})}


@pytest.mark.parametrize("name", sorted(LOGS))
def test_the_fold_of_every_build_is_what_it_was(name):
    got = json.loads(json.dumps(_folds(LOGS[name]), sort_keys=True, default=str))
    snap = SNAP / f"fold_{name}.json"
    if os.environ.get("REPO_FACTORY_SNAPSHOT") == "1" or not snap.exists():
        snap.parent.mkdir(exist_ok=True)
        snap.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip("snapshot written")
    want = json.loads(snap.read_text())
    assert sorted(got) == sorted(want)
    for build in sorted(got):
        for key in sorted(set(got[build]) | set(want[build])):
            assert got[build].get(key) == want[build].get(key), (name, build, key)


def test_the_synthetic_log_carries_every_type_fold_reads():
    import ast
    import inspect

    src = inspect.getsource(events)
    read = set()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "t"
        ):
            for c in node.comparators:
                for x in ast.walk(c):
                    if isinstance(x, ast.Constant) and isinstance(x.value, str):
                        read.add(x.value)
    read |= set(events.START_TYPES) | set(events.V4_TYPES) | set(events.V5_TYPES)
    read |= set(getattr(events, "FOLD", {}))  # the dispatch table (S-14)
    read.discard("v5_plan")  # fold's own: build_started's stage plan
    have = {e["type"] for e in events.read_events(LOGS["every_kind"])}
    assert read - have == set()
