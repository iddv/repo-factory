"""A change's state says what is true (UXI; ``repo_factory/projects/steps.py``). The owner saw
Projects call changes "building" while their pull request waited, after their fix said it could
not reproduce, and after the PR merged, with nothing running. The step reads the issue, the
fix's run and the poll, so each of those reads as what it is."""

import pytest

from repo_factory.projects.steps import step

pytestmark = pytest.mark.usefixtures("projects_on")

FILED = {
    "how": "issue",
    "repo": "o/beliefline",
    "number": 4,
    "url": "https://github.com/o/b/4",
    "go": True,
    "go_held": None,
}


def ch(state, **kw):
    return {"state": state, "filed": FILED, "accepted": {"at": 1}, **kw}


def states(s):
    return [(x["k"], x["state"]) for x in s["steps"]]


def test_building_with_its_pr_open_waits_for_you():
    s = step(
        ch("building"),
        {
            "status": "fixed",
            "key": "fix-o-beliefline-4",
            "pr": {"number": 11, "url": "https://github.com/o/b/pull/11"},
        },
    )
    assert s["at"] == "pr" and s["needs_you"] and not s["running"]
    assert s["words"] == "Pull request #11 waits for your review"
    assert states(s) == [
        ("accepted", "done"),
        ("filed", "done"),
        ("fixing", "done"),
        ("pr", "now"),
        ("merged", "todo"),
    ]
    assert s["steps"][1]["word"] == "Issue #4 filed" and s["fix"] == "fix-o-beliefline-4"


def test_building_whose_fix_could_not_reproduce_says_so():
    s = step(
        ch("building"),
        {"status": "tried", "reason": "Could not reproduce it: the upgrade path already works"},
    )
    assert s["at"] == "could_not" and not s["running"] and s["tone"] == "red"
    assert s["words"].startswith("Could not reproduce it")
    assert ("fixing", "fail") in states(s) and s["steps"][2]["word"] == "Could not reproduce it"


def test_building_whose_pr_is_no_longer_open_is_done():
    s = step(ch("building"), {"status": "closed", "pr": {"number": 12, "url": None}})
    assert s["at"] == "merged" and not s["running"] and not s["needs_you"]
    assert all(x["state"] == "done" for x in s["steps"])


def test_only_a_running_fix_reads_as_fixing():
    s = step(ch("building"), {"status": "running", "reason": "Verifying: 4 scenarios"})
    assert s["at"] == "fixing" and s["running"] and "repo-factory is fixing it" in s["words"]
    s = step(ch("building"), None)
    assert s["at"] == "filed" and not s["running"] and "next poll" in s["words"]


def test_the_states_before_and_beside_a_fix():
    assert step({"state": "new"})["at"] == "proposed"
    assert step({"state": "skipped"})["at"] == "tracked"
    assert step({"state": "later"})["at"] == "later"
    assert step({"state": "closed", "declined": {"how": "never"}})["at"] == "declined"
    s = step({"state": "queued", "accepted": {"at": 1}})
    assert s["at"] == "accepted" and s["steps"][1]["state"] == "now"
    s = step(ch("queued", filed={**FILED, "go": False, "go_held": "a feature: you build it"}))
    assert s["at"] == "filed" and "a feature: you build it" in s["words"]
    # an older row's copy of GitHub ("held") is read as what it was decided: filed with go
    assert step(ch("held"))["at"] == "filed"
    assert step(ch("closed"))["at"] == "done"


def test_beliefline_s_six_building_rows_read_as_what_github_says():
    """Live, 2026-10-16: 6 of Beliefline's 8 rows said `building` (#2-#7), last moved 10-13.
    #3 and #6 were cannot-reproduce; #2, #4, #5 and #7 had open pull requests."""
    tried = {"status": "tried", "reason": "Could not reproduce it: no failing behaviour"}
    facts = {
        2: {"status": "fixed", "pr": {"number": 13}},
        3: tried,
        4: {"status": "fixed", "pr": {"number": 11}},
        5: {"status": "fixed", "pr": {"number": 14}},
        6: tried,
        7: {"status": "fixed", "pr": {"number": 15}},
    }
    got = {n: step(ch("building", filed={**FILED, "number": n}), f)["at"] for n, f in facts.items()}
    assert got == {2: "pr", 3: "could_not", 4: "pr", 5: "pr", 6: "could_not", 7: "pr"}
    assert not any("fixing" in step(ch("building"), f)["words"] for f in facts.values())
