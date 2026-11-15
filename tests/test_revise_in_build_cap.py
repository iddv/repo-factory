"""Repair rounds (the automatic one, Ask for changes) spend from what the build's cap has left,
unless ``revise.max_usd`` is set: a build at its cap gets no automatic round, and says why."""

from __future__ import annotations

import json
from pathlib import Path

from repo_factory.costs import ledger
from repo_factory.loop.daemon import Loop
from repo_factory.v5 import change


def den(tmp_path: Path, cap, spends: dict[int, float], revises: dict[int, int]) -> Path:
    d = tmp_path / ".den"
    for r in spends:
        st = {"cap": cap} if r not in revises else {"change": {"revises": revises[r]}}
        (d / "v5" / f"r{r}").mkdir(parents=True, exist_ok=True)
        (d / "v5" / f"r{r}" / "state.json").write_text(json.dumps(st))
    p = ledger.ledger_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"build": r, "usd": usd}) + "\n" for r, usd in spends.items()))
    return d


def test_a_revise_round_spends_what_the_build_and_its_rounds_left(tmp_path):
    # round 1 the build ($20 of $25), round 2 a revise of it ($3): $2 left for round 3
    d = den(tmp_path, 25.0, {1: 20.0, 2: 3.0}, {2: 1})
    assert change.build_spend(d, 2) == (25.0, 23.0)
    assert change.build_left(d, 2) == 2.0
    d2 = den(tmp_path / "nocap", None, {1: 40.0}, {})
    assert change.build_left(d2, 1) is None  # no cap: nothing to keep inside


def _loop(revise_cap=None):
    lp = Loop.__new__(Loop)
    lp.cfg = {"build": {"auto_repair": 1}, "revise": {"max_usd": revise_cap}}
    return lp


def test_a_build_at_its_cap_gets_no_automatic_round(tmp_path, monkeypatch):
    d = den(tmp_path, 12.0, {1: 12.0}, {})
    said = []
    monkeypatch.setattr("repo_factory.loop.daemon.log", said.append)
    idea = {"id": "i1", "title": "t", "task_dir": str(d.parent), "build": {}}
    g = {
        "verdict": "held",
        "round": 1,
        "reasons": ["tests: 1 failing"],
        "checks": {"delivered": {"ok": True}, "tests": {"ok": False, "detail": "1 failing"}},
    }
    lp = _loop()
    lp.revise = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no round may start"))
    assert lp._auto_repair(idea, g) is None
    assert said and "no automatic repair: the build's cap is spent" in said[-1]
    # with a revise.max_usd of its own, the round spends from that instead
    assert _loop(5.0)._build_left(idea, g) is None
