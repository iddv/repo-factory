"""The build loop's runs as they go: a tick's step that did work is a run with its marks, and a
run whose process died is cut off. Nothing touches a model or the network."""

from __future__ import annotations

import time

from repo_factory.loop import activity as A

from .loop_common import FakeModel, make_loop


def test_a_failed_pitch_is_a_failed_run_with_the_verb(tmp_path):
    def boom(prompt):
        raise RuntimeError("the model answered 529")

    lp = make_loop(tmp_path, model=FakeModel(boom))
    lp.add("a booking tool for small clinics")
    lp.tick()
    [r] = A.steps(lp.ledger)[1]
    assert r["state"] == "failed" and "529" in r["error"] and r["verb"] == "Write the brief again"


def test_a_step_whose_process_died_is_cut_off_not_running(tmp_path):
    lp = make_loop(tmp_path)
    A._write(
        lp.ledger,
        {
            "run": "loop.pitch-0123456789ab",
            "kind": "loop.pitch",
            "ts": time.time(),
            "mark": "started",
            "pid": 999999,
            "pstart": 1,
            "title": "Nextread",
        },
    )
    running, recent = A.steps(lp.ledger)
    assert running == [] and recent[0]["state"] == "stopped"
    assert "cut off" in recent[0]["words"]
