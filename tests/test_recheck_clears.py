"""AU1 #3: a Re-check that only turns holds into passes is the build's verdict. Nextread r1 was
held on completeness (F1 broken under 0.3.8); "Re-check with current repo-factory" with 0.3.9 passed
every flow, and then a full rebuild ($7.28) was needed just to get that verdict on the record.
Replayed from trimmed copies of its round records and of the re-check's exam record; the exam
itself is not run (``recheck.run`` is replaced by ``recompute`` over the recorded results)."""

import json
import shutil
import time
from pathlib import Path

from repo_factory.loop import gate, recheck

from .loop_common import make_loop

FIX = Path(__file__).parent / "fixtures"
ROUND = FIX / "factory_holds" / "nextread-r1"
EXAM = FIX / "recheck_clears" / "nextread-r1.exam-0.3.9.json"


def held_nextread(tmp_path, monkeypatch, exam: dict):
    """A loop with Nextread's round 1 held as 0.3.8 judged it; ``recheck.run`` replays
    ``exam`` (the re-check's exam record) through ``recompute``."""
    lp = make_loop(tmp_path)
    idea, _ = lp.add("a reading list that says what to read next")
    iid = idea["id"]
    task = tmp_path / "nextread"
    root = task / ".den" / "v5" / "r1"
    shutil.copytree(ROUND, root)
    old = {
        "delivered": {"ok": True, "detail": "build ok, branch repo-factory/nextread"},
        **gate.record_checks(root, {"SECURITY.md", "READINESS.md"}),
    }
    g = {
        **gate._verdict(old, 1, "repo-factory/nextread", str(task / "out"), None),
        "judged_by": "0.3.8",
    }
    lp.ledger.update(iid, state="held", task_dir=str(task), gate=g, reason="; ".join(g["reasons"]))

    def run(idea, cfg, *a, **k):
        new = recheck.recompute(old, exam, root)
        d = recheck.compare(old, new)
        return {
            "version": "0.3.9",
            "judged_by": "0.3.8",
            "at": time.time(),
            "round": 1,
            "checks": new,
            "differs": d,
            "same": not d,
            "state": "done",
            "verdict": "held" if any(not c["ok"] for c in new.values()) else "ready",
        }

    monkeypatch.setattr(recheck, "run", run)
    return lp, iid, root, g


def test_nextread_a_recheck_that_only_passes_is_the_verdict(tmp_path, monkeypatch):
    lp, iid, root, before = held_nextread(tmp_path, monkeypatch, json.loads(EXAM.read_text()))
    assert before["reasons"] == [
        "completeness: 6/7 flows work end to end; not yet: F1 Install and first run"
    ]
    out = lp.recheck(iid)  # the command line: it runs here
    assert out["state"] == "ready" and out["reason"] is None
    g = out["gate"]
    assert (g["verdict"], g["reasons"], g["judged_by"]) == ("ready", [], "0.3.9")
    assert g["checks"]["completeness"] == {"ok": True, "detail": "7/7 flows work end to end"}
    assert g["recheck"]["state"] == "done" and g["round"] == 1
    rec = json.loads((root / gate.GATE_FILE).read_text())
    assert (rec["verdict"], rec["judged_by"]) == ("ready", "0.3.9")
    assert lp.ledger.history(iid)[0]["note"] == (
        "re-checked with 0.3.9: completeness passes now; the verdict is recorded, judged by "
        "repo-factory 0.3.9: ready to publish"
    )


def test_a_recheck_that_finds_a_new_failure_changes_nothing(tmp_path, monkeypatch):
    exam = json.loads(EXAM.read_text())
    exam["own"]["commands"][0].update(verdict="red", status="failed")
    lp, iid, root, before = held_nextread(tmp_path, monkeypatch, exam)
    out = lp.recheck(iid)
    assert out["state"] == "held" and out["reason"] == "; ".join(before["reasons"])
    assert out["gate"]["verdict"] == "held" and out["gate"]["judged_by"] == "0.3.8"
    assert sorted((d["check"], d["now"]) for d in out["gate"]["recheck"]["differs"]) == [
        ("completeness", True),
        ("tests", False),
    ]
    assert not (root / gate.GATE_FILE).exists()


def test_clears_only_a_held_gate_whose_differences_are_all_passes():
    held = {
        "verdict": "held",
        "reasons": ["tests: x"],
        "checks": {"tests": {"ok": False, "detail": "x"}, "security": {"ok": True}},
    }
    done = {
        "state": "done",
        "version": "0.5.0",
        "at": 1.0,
        "checks": {
            "tests": {"ok": True, "detail": "green"},
            "security": {"ok": True},
            "secrets": {"ok": True, "detail": "clean"},
        },
        "differs": [{"check": "tests", "was": False, "now": True}],
    }
    g = recheck.clears(held, done)
    assert (g["verdict"], g["judged_by"], set(g["checks"])) == (
        "ready",
        "0.5.0",
        {"tests", "security"},
    )
    assert recheck.clears({**held, "verdict": "ready"}, done) is None
    assert recheck.clears(held, {**done, "differs": []}) is None
    assert recheck.clears(held, {**done, "state": "failed"}) is None
    # only a pre-push scan passes now: not the gate's verdict to re-record
    assert recheck.clears(held, {**done, "differs": [{"check": "secrets", "now": True}]}) is None
