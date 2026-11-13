"""AU1 #2: a build the gate holds gets one automatic code revise round (origin ``auto``) with
the hold as its brief (``build.auto_repair``, default 1; 0: off), instead of waiting for the
owner to type the gate's reasons back. A round that leaves the hold exactly as it found it
stops them with one line. The builds are the fake ``repo-factory build``/``repo-factory revise``."""

import json
import subprocess
from pathlib import Path

import pytest

from repo_factory.errors import LoopError
from repo_factory.loop import builds

from .loop_common import FAKE_BUILD, make_loop, wait

RED = {
    "readiness": {
        "final": {
            "items": [
                {
                    "id": "tests_green",
                    "status": "red",
                    "why": "4 of 58 of the product's own tests fail",
                }
            ]
        }
    }
}
FEWER = {
    "readiness": {
        "final": {
            "items": [
                {
                    "id": "tests_green",
                    "status": "red",
                    "why": "2 of 58 of the product's own tests fail",
                }
            ]
        }
    }
}
LINE = "two rounds did not move it: Publish anyway, or say what to change"


def built(tmp_path, fake, auto=1, **over):
    """A held build, judged by the gate as a finished build is (``Loop.gate``)."""
    lp = make_loop(tmp_path, build={"auto_repair": auto}, **over)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    (task / "fake.json").write_text(json.dumps(fake))
    r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return lp, idea["id"], lp.gate(idea["id"])


def run_round(lp, iid):
    [started] = lp.start_builds()
    wait(lambda: not builds.alive(started["build"]["pid"]))
    lp.watch()
    return lp.ledger.get(iid)


def test_a_product_hold_queues_one_automatic_code_round(tmp_path):
    lp, iid, out = built(tmp_path, RED)
    assert out["state"] == "queued"
    ch = out["build"]["change"]
    assert (ch["origin"], ch["mode"]) == ("auto", "code")
    text = Path(ch["file"]).read_text()
    assert text.startswith(
        "The release checks held this build. Fix the product so each of "
        "these passes, and change nothing else:\n- tests: 4 of 58"
    )
    assert ch["reasons"] == ["tests: 4 of 58 of the product's own tests fail"]
    notes = [h["note"] for h in lp.ledger.history(iid)]
    assert notes[0] == (
        "held by the gate: an automatic repair round runs on the hold (1 of 1, build.auto_repair)"
    )
    # the round leaves the hold as it found it: held with the one line, no second round
    out = run_round(lp, iid)
    assert out["state"] == "held"
    assert out["reason"] == "tests: 4 of 58 of the product's own tests fail; " + LINE
    assert [c["origin"] for c in out["build"]["changes"]] == ["auto"]
    assert lp.start_builds() == []


def test_an_identical_outcome_stops_them_before_the_bound(tmp_path):
    lp, iid, out = built(tmp_path, RED, auto=3)
    out = run_round(lp, iid)
    assert out["state"] == "held" and out["reason"].endswith(LINE)
    assert out["build"]["auto_repairs"] == 1 and lp.start_builds() == []


def test_a_round_that_moves_the_hold_gets_the_next_one(tmp_path):
    lp, iid, out = built(tmp_path, {**RED, "revise": FEWER}, auto=3)
    out = run_round(lp, iid)  # 4 failing -> 2 failing: moved, so again
    assert out["state"] == "queued" and out["build"]["change"]["origin"] == "auto"
    assert out["build"]["change"]["reasons"] == ["tests: 2 of 58 of the product's own tests fail"]
    out = run_round(lp, iid)  # 2 -> 2: two rounds ended on it
    assert out["state"] == "held"
    assert out["reason"] == "tests: 2 of 58 of the product's own tests fail; " + LINE
    assert len(out["build"]["changes"]) == 2


def test_a_cut_off_build_gets_no_repair_round(tmp_path):
    from repo_factory.loop import gate

    lp, iid, out = built(tmp_path, RED, auto=0)
    lp.cfg["build"]["auto_repair"] = 1
    g = {**out["gate"], "reasons": ["completeness: " + gate.NOTHING_MEASURED]}
    assert lp._auto_repair(lp.ledger.get(iid), g) is None
    assert lp.ledger.get(iid)["state"] == "held"


def test_auto_repair_0_does_nothing(tmp_path):
    lp, iid, out = built(tmp_path, RED, auto=0)
    assert out["state"] == "held" and "change" not in out["build"]
    assert lp.start_builds() == []


def test_revise_max_per_build_bounds_it(tmp_path):
    lp, iid, out = built(tmp_path, {**RED, "revise": FEWER}, auto=3, revise={"max_per_build": 1})
    assert out["state"] == "queued"
    out = run_round(lp, iid)  # moved, but the one revise round is spent
    assert out["state"] == "held" and "change" not in out["build"]
    assert out["reason"] == "tests: 2 of 58 of the product's own tests fail"
    lp2, iid2, out2 = built(tmp_path / "b", RED, revise={"enabled": False})
    assert out2["state"] == "held" and "change" not in out2["build"]


def test_an_automatic_round_only_queues_while_the_loop_is_paused(tmp_path):
    lp, iid, out = built(tmp_path, RED)
    lp.pause()
    lp.tick()
    i = lp.ledger.get(iid)
    assert i["state"] == "queued" and not i["build"].get("pid")
    lp.resume()


def test_judging_again_from_the_command_line_starts_no_round(tmp_path):
    lp, iid, out = built(tmp_path, RED, auto=0)
    lp.cfg.data["build"]["auto_repair"] = 1
    assert lp.gate(iid, repair=False)["state"] == "held"


def test_factory_revise_takes_the_origin_and_no_token_acts_as_it():
    from repo_factory.loop.cli import origin_arg
    from repo_factory.ui.api import HttpError, caller

    assert origin_arg("auto") == "auto"
    with pytest.raises(HttpError):
        caller({"origin": "auto"}, {}, "/api/act")


def test_the_refusal_no_longer_says_retry(tmp_path):
    lp, iid, _ = built(tmp_path, RED, auto=0)
    ask = "Fix the 4 failing own tests (see the gate); keep the tests' expectations."
    for _ in range(2):
        lp.revise(iid, ask)
        assert run_round(lp, iid)["state"] == "held"
    with pytest.raises(LoopError) as e:
        lp.revise(iid, ask)
    assert "Retry" not in str(e.value)
    assert str(e.value).endswith("or Publish anyway when the hold is one you accept")
