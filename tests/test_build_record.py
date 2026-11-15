"""Builds' head: your own record (how many builds, the median time and cost), from the
builds' own ledgers; the lean flow counted beside v5 and on its own."""

import json
import subprocess
from pathlib import Path

import pytest

from repo_factory.ui import api, record

from .loop_common import FAKE_BUILD, make_loop


def row(**kw):
    return {"idea": "i1", "status": "held", "cost_usd": 10.0, "wall_s": 3600, **kw}


def test_the_record_counts_finished_builds_with_a_cost():
    rows = [
        row(cost_usd=10.0, wall_s=3600, url="https://github.com/o/a", idea_state="published"),
        row(cost_usd=12.0, wall_s=4000, idea_state="held"),
        row(cost_usd=20.0, wall_s=5000, flow="lean", idea_state="held"),
        row(cost_usd=None),  # no cost recorded: left out, and said
        row(status="running", live=True),  # not finished
        row(idea=None),  # a build started by hand
        row(kind="fix"),  # a maintenance fix
    ]
    got = record.of(rows)
    assert got == {
        "builds": 3,
        "median_usd": 12.0,
        "median_s": 4000,
        "total_usd": 42.0,
        "published": 1,
        "held": 2,
        "no_cost": 1,
        "lean": {"builds": 1, "median_usd": 20.0, "median_s": 5000, "total_usd": 20.0},
    }


def test_no_builds_is_an_empty_record():
    got = record.of([])
    assert got["builds"] == 0 and got["median_usd"] is None and got["lean"] is None


@pytest.fixture(autouse=True)
def fake_gh(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_DIR", str(tmp_path / "gh"))


def test_the_head_matches_the_builds_ledgers(tmp_path):
    lp = make_loop(tmp_path, build={"auto_repair": 0})
    tasks = []
    for text in ("small clinics still schedule patients on paper", "a habit tracker for streaks"):
        idea, _ = lp.add(text)
        out = lp.pitch([idea["id"]])[0]
        task = Path(out["task_dir"])
        (task / "fake.json").write_text("{}")
        r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        lp.gate(idea["id"])
        tasks.append(task)
    lp.cfg.tasks_dir.mkdir(parents=True, exist_ok=True)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    snap = app.snapshot()
    rec = snap["record"]
    costs = sorted(r["cost_usd"] for r in snap["builds"] if r.get("idea"))
    assert rec["builds"] == 2 == len(costs)
    # each row's cost is its own build ledger's total
    from repo_factory.costs.ledger import read, summarize

    totals = sorted(round(summarize(read(t / ".den"))["total_usd"], 4) for t in tasks)
    assert totals == pytest.approx(costs, abs=0.01), (totals, costs)
    assert rec["median_usd"] == pytest.approx(round(sum(costs) / 2, 2), abs=0.01)
    assert rec["total_usd"] == pytest.approx(sum(costs), abs=0.01)
    assert json.dumps(rec)  # the page reads it as JSON
