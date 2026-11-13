"""A build is judged by the repo-factory version that built it (EX-PLAN §7, PR 5): the gate records
it; "Re-check with current repo-factory" runs the installed version's checks on the same delivered
tree and says where they differ (one that only turns holds into passes is the verdict, AU1
#3: ``test_recheck_clears``); at publish the press runs every scan again with the installed
version; a "Suspect this check" files a factory issue labelled check, never go."""

import json
import time

import pytest

from repo_factory import registry as R
from repo_factory.harness.isolation import isolation
from repo_factory.loop import gate, publish, recheck

from .test_loop_gate_publish import built

TESTED = {
    "files": {
        "README.md": "# Clinicbook\n\nBook patients in.\n\n## Install\n\n./run.sh\n\n"
        "## Tests\n\n    python3 -m unittest discover -s tests\n",
        "tests/test_app.py": "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertTrue(True)\n",
    }
}


def test_the_gate_records_the_version_that_built_the_round(tmp_path):
    _, _, task = built(tmp_path, TESTED)
    root = task / ".den" / "v5" / "r1"
    assert gate.evaluate(task)["judged_by"] is None  # no version recorded: older
    (root / "exam").mkdir(exist_ok=True)
    (root / "exam" / "manifest.json").write_text(json.dumps({"factory_version": "0.3.3"}))
    assert gate.evaluate(task)["judged_by"] == "0.3.3"
    assert recheck.judged_line({"judged_by": "0.3.3"}) == "judged by repo-factory 0.3.3"
    assert recheck.judged_line({"judged_by": recheck.installed()}) is None


@pytest.mark.skipif(isolation() != "bwrap", reason="needs bubblewrap")
def test_a_recheck_says_what_differs_and_one_that_only_passes_is_the_verdict(tmp_path):
    lp, iid, task = built(
        tmp_path,
        {
            **TESTED,
            "readiness": {
                "final": {
                    "items": [
                        {"id": k, "status": "green"}
                        for k in ("install", "bind_localhost", "empty_start", "first_admin")
                    ]
                    + [{"id": "tests_green", "status": "red", "why": "the suite collided"}]
                }
            },
        },
    )
    lp.gate(iid)
    before = lp.ledger.must(iid)
    assert before["state"] == "held" and "tests" in before["reason"]
    out = lp.recheck(iid)  # the command line: it runs here
    rc = out["gate"]["recheck"]
    assert rc["state"] == "done" and rc["version"] == recheck.installed()
    assert [(d["check"], d["was"], d["now"]) for d in rc["differs"]] == [("tests", False, True)]
    assert recheck.words(rc) == f"re-checked with {rc['version']}: tests passes now"
    # AU1 #3: it only turned the tests hold into a pass: that is the verdict now
    after = lp.ledger.must(iid)
    assert after["state"] == "ready" and after["reason"] is None
    assert after["gate"]["judged_by"] == rc["version"]
    root = task / ".den" / "v5" / "r1"
    assert (root / f"recheck-{rc['version']}.json").is_file()
    assert list((root / "exam" / "results").glob(f"*.recheck-{rc['version']}.json"))
    assert lp.ledger.history(iid)[0]["note"].startswith("re-checked with")


def test_a_press_from_the_page_never_waits(tmp_path, monkeypatch):
    lp, iid, task = built(tmp_path, TESTED)
    lp.gate(iid)
    monkeypatch.setattr(
        recheck,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("a press must not run the re-check in the request")
        ),
    )
    lp.ledger.origin = "ui"
    t0 = time.time()
    out = lp.recheck(iid)
    assert time.time() - t0 < 5 and out["gate"]["recheck"]["state"] == "running"


def test_the_spawned_recheck_runs_although_the_press_recorded_it_running(tmp_path):
    """The press records ``running`` with the spawned process's pid; that process finds its
    own pid there and runs (it returned at once, and nothing was ever re-checked: v0.3.8)."""
    import os

    lp, iid, task = built(tmp_path, TESTED)
    lp.gate(iid)
    g = dict(lp.ledger.must(iid)["gate"])
    g["recheck"] = {"state": "running", "pid": os.getpid(), "started": time.time()}
    lp.ledger.update(iid, gate=g)
    out = lp.recheck(iid)
    assert out["gate"]["recheck"]["state"] == "done"


def test_recheck_is_a_click_never_automatic_and_offered_on_held_and_ready():
    a = R.ACTIONS["recheck"]
    assert a.click == "click" and "recheck" not in R.CONFIRM_CLASS
    ok = {"verdict": "held", "reasons": ["tests: x"], "checks": {"delivered": {"ok": True}}}
    for st in ("held", "ready"):
        row = {"idea": "i1", "idea_state": st, "status": st, "gate": ok}
        assert a.when(R.Subject("build", None, row)) is None
    row = {
        "idea": "i1",
        "idea_state": "held",
        "status": "held",
        "gate": {**ok, "recheck": {"state": "running"}},
    }
    assert a.when(R.Subject("build", None, row)) == "a re-check is running"
    row = {
        "idea": "i1",
        "idea_state": "public",
        "status": "public",
        "gate": ok,
        "url": "https://github.com/o/p",
    }
    assert a.when(R.Subject("build", None, row))


def test_the_press_runs_every_scan_again_whatever_the_record_kept(tmp_path):
    # AU2 #7: no "pinned" scans: a record from the builder's version is never reused
    stage = tmp_path / "p"
    stage.mkdir()
    (stage / "README.md").write_text("# P\n\nRun it with brb.\n")
    prep = {
        "stage": str(stage),
        "jargon": [],
        "rea_scan": [],
        "factory_version": "0.4.0",
        "built_by": "0.4.0",
    }
    out = publish.finish(prep, {"publish": {}})
    assert [h["kind"] for h in out["jargon"]] == ["repo-factory's words"]
    assert not hasattr(publish, "pinned")
