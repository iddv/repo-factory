"""AU1 #3, the case it was written for: Repo Recall, held 105 h on F5 (a flow its sandbox could
not measure) and still held after 0.3.76 stopped making that hold, because a Re-check compared
the installed version's run with the installed version's reading of the record (no
difference) instead of with the verdict the build is held under. And its README quick start was
red only by the old harness (exit 127: the install step skipped, nothing in its place), which a
Re-check never measured again. Its records, trimmed: ``fixtures/recheck_verdict/repo-recall``
(round 4's flows and scorecard, round 5's quick-start item). The exam and the quick start are
given, not run."""

import json
import shutil
from pathlib import Path

from repo_factory.loop import gate, recheck

FIX = Path(__file__).parent / "fixtures" / "recheck_verdict" / "repo-recall"
DELIVERED = {"SECURITY.md", "READINESS.md"}
# what the ledger holds it under: 0.3.x's gate on the same records
HELD = {
    "verdict": "held",
    "round": 4,
    "branch": "repo-factory/repo-recall",
    "reasons": ["completeness: 6/7 flows work end to end; not yet: F5 Detect drift"],
    "checks": {
        "delivered": {"ok": True, "detail": "build ok, branch repo-factory/repo-recall"},
        "documents": {"ok": True, "detail": "SECURITY.md and READINESS.md delivered"},
        "security": {"ok": True, "detail": "no open critical or high finding"},
        "tests": {"ok": True, "detail": "the README's test commands run green"},
        "safe_defaults": {"ok": True, "detail": "safe defaults and install: install green"},
        "completeness": {
            "ok": False,
            "detail": "6/7 flows work end to end; not yet: F5 Detect drift",
        },
    },
}
# the exam on the delivered tree: every scenario passes but F5's, which the sandbox still fails
EXAM = {
    "scenarios": {f"e{n}": {"status": "failed" if n == 32 else "passed"} for n in range(28, 35)}
}
GREEN = {
    "id": "commands",
    "status": "green",
    "source": "harness",
    "title": "The README's quick-start commands work as written",
    "why": "all ran",
}


def held(tmp_path, monkeypatch):
    root = tmp_path / ".den" / "v5" / "r4"
    shutil.copytree(FIX, root)
    monkeypatch.setattr(
        gate, "evaluate", lambda task, build=None: {"checks": gate.record_checks(root, DELIVERED)}
    )
    idea = {"id": "i1", "task_dir": str(tmp_path), "gate": {**HELD, "out": str(tmp_path / "out")}}
    return idea, root


def test_repo_recall_a_recheck_clears_the_hold_the_installed_factory_no_longer_makes(
    tmp_path, monkeypatch
):
    idea, root = held(tmp_path, monkeypatch)
    # the record as the installed repo-factory reads it: F5 is a gap, but the old harness's red
    # quick start (exit 127) is in it; judged against itself nothing would differ
    rec_checks = gate.record_checks(root, DELIVERED)
    assert rec_checks["completeness"]["ok"] and not rec_checks["safe_defaults"]["ok"]
    was, new = recheck.judge(idea, EXAM, GREEN, root, tmp_path / "rd")
    d = recheck.compare(was, new)
    assert [(x["check"], x["was"], x["now"]) for x in d] == [("completeness", False, True)]
    assert "F5 Detect drift not measured" in new["completeness"]["gap"]
    out = recheck.clears(
        idea["gate"], {"state": "done", "differs": d, "checks": new, "version": "0.3.79", "at": 1.0}
    )
    assert out["verdict"] == "ready" and out["judged_by"] == "0.3.79", out["reasons"]
    assert out["gaps"] == [
        "completeness: F5 Detect drift not measured: the sandbox fails it in both copies"
    ]


def test_a_quick_start_still_red_now_is_a_new_failure_and_changes_nothing(tmp_path, monkeypatch):
    idea, root = held(tmp_path, monkeypatch)
    red = [
        i
        for i in json.loads((root / "readiness.json").read_text())["final"]["items"]
        if i["id"] == "commands"
    ][0]
    was, new = recheck.judge(idea, EXAM, red, root, tmp_path / "rd")
    d = recheck.compare(was, new)
    assert ("safe_defaults", True, False) in [(x["check"], x["was"], x["now"]) for x in d]
    assert (
        recheck.clears(
            idea["gate"],
            {"state": "done", "differs": d, "checks": new, "version": "0.3.79", "at": 1.0},
        )
        is None
    )
