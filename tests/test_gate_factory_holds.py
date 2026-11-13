"""AU1 #1: the gate holds only on the product. What repo-factory itself could not measure (a flow
the harness's sandbox fails on both copies, a flow no scenario checked) is a gap, stated, never
a hold. The one exception: a security review that did not run holds the build, and Publish
anyway never overrides it. The cases are real rounds' records, trimmed
(``tests/fixtures/factory_holds``)."""

from pathlib import Path

import pytest

from repo_factory.loop import gate, publish

FIX = Path(__file__).parent / "fixtures" / "factory_holds"
DELIVERED = {"SECURITY.md", "READINESS.md"}  # these rounds delivered their reports


def judge(name: str) -> dict:
    checks = gate.record_checks(FIX / name, DELIVERED)
    return gate._verdict(checks, 1, "repo-factory/x", "/out", None)


@pytest.mark.parametrize(
    "name, gap",
    [
        # Repo Recall, held 105 h on F5: the sandbox fails it on the build and the verifier's copy
        (
            "repo-recall-r4",
            "completeness: F5 Detect drift not measured: the sandbox fails it in both copies",
        ),
    ],
)
def test_a_factory_gap_never_holds_the_build(name, gap):
    v = judge(name)
    assert v["verdict"] == "ready", v["reasons"]
    assert any(gap in g for g in v["gaps"]), v["gaps"]
    assert v["checks"]["completeness"]["ok"]


@pytest.mark.parametrize("name", ["notehatch-r1", "notehatch-r2"])
def test_nothing_measured_is_not_complete(name):
    # NoteHatch r1-r2: the scenario runner had no HOME, all six flows `environment`: the gap
    # is still said, and completeness holds (Publish anyway may override it)
    v = judge(name)
    assert v["verdict"] == "held"
    assert [r.split(":")[0] for r in v["reasons"]] == ["completeness"]
    assert "only 0 of 6 flows could be measured, too few to call it complete" in v["reasons"][0]
    assert any("not measured: the sandbox fails them in both copies" in g for g in v["gaps"])
    plan = gate.override_plan(v["checks"], [])
    assert [k for k, _ in plan["overrides"]] == ["completeness"] and not plan["refused"]


def test_fewer_than_half_measured_is_not_complete_and_half_is():
    rows = [("F1", "A", "works"), ("F2", "B", "environment"), ("F3", "C", "unchecked")]
    c = gate.completeness_check(rows)
    assert not c["ok"] and c["detail"].endswith(
        "only 1 of 3 flows could be measured, too few to call it complete"
    )
    c = gate.completeness_check(rows[:2])
    assert c["ok"] and "too few" not in c["detail"]


def test_a_cut_off_build_with_nothing_measured_holds_and_is_never_published():
    # Sidedoor r1: cut off, no scenario for any flow and no security review: nothing to judge
    v = judge("sidedoor-r1")
    assert v["verdict"] == "held"
    assert [r.split(":")[0] for r in v["reasons"]] == ["security", "completeness"]
    assert v["reasons"][1] == "completeness: " + gate.NOTHING_MEASURED
    assert not any(g.startswith("security") for g in v["gaps"])
    plan = gate.override_plan(v["checks"], [])
    assert [k for k, _ in plan["overrides"]] == ["completeness"]
    assert plan["refused"] == [("security", gate.NEVER_UNREVIEWED)]


def test_a_security_review_that_did_not_run_holds_whatever_may_override_says():
    # Sidedoor r2: no review, and a product hold beside it (no README: no test command)
    v2 = judge("sidedoor-r2")
    assert v2["verdict"] == "held"
    assert [r.split(":")[0] for r in v2["reasons"]] == ["security", "tests", "completeness"]
    said = v2["reasons"][0]
    assert said.startswith("security: the security review did not run (")
    assert said.endswith("Retry builds it again with the review") or "Settings › Spend" in said
    for allowed in (None, ["tests", "completeness", "security"]):
        plan = gate.override_plan(v2["checks"], [], allowed)
        assert plan["refused"] == [("security", gate.NEVER_UNREVIEWED)]
    # a revise round carries the review it revises: no automatic round, and it says why
    v2["round"] = 2
    assert gate.cant_revise(v2, "/no/task") == "revise can't fix this: " + said[10:]


def test_the_hold_says_why_the_review_did_not_run_and_what_to_press():
    capped = "the build's cap: $11.79 spent of $12.00, the stage needs $0.30 to start"
    assert gate.unreviewed(capped) == (
        f"the security review did not run ({capped}); raise the cap in Settings › Spend, then Retry"
    )
    assert gate.unreviewed(None) == (
        "the security review did not run (no reason was recorded); Retry builds it again "
        "with the review"
    )
    # a verdict stored before this change (gate.json keeps ok and detail) is known too
    assert gate.is_unreviewed({"ok": False, "detail": gate.unreviewed("x")})
    assert not gate.is_unreviewed({"ok": False, "detail": "open: high: x"})


def test_a_broken_flow_still_holds():
    # Nextread r1: F1's scenario failed in the harness on the build: the product's defect
    v = judge("nextread-r1")
    assert v["verdict"] == "held"
    assert v["reasons"] == [
        "completeness: 6/7 flows work end to end; not yet: F1 Install and first run"
    ]
    assert v["gaps"] == []


def test_an_open_finding_is_never_overridden():
    checks = gate.record_checks(FIX / "repo-recall-r4", DELIVERED)
    checks["security"] = {"ok": False, "detail": "open: high: x"}
    plan = gate.override_plan(checks, [])
    assert plan["refused"] == [
        ("security", "an open critical or high security finding is never overridden")
    ]


def test_the_gap_is_kept_with_the_verdict_and_said_in_the_release_notes(tmp_path):
    v = judge("notehatch-r1")
    gate.record(tmp_path, v)
    import json

    rec = json.loads((tmp_path / ".den" / "v5" / "r1" / "gate.json").read_text())
    assert rec["gaps"] == v["gaps"] and rec["checks"]["completeness"]["gap"]
    # a review that did not run is a hold now, never a line in the release notes
    assert publish.gaps_line(judge("sidedoor-r1")) is None
    line = publish.gaps_line(judge("notehatch-r1"))
    assert line.startswith(
        "Not every check could run before this release: F1 Install and first run, F2 "
    )
    assert line.endswith("not measured: the sandbox fails them in both copies.")
    assert publish.gaps_line(judge("nextread-r1")) is None


def test_a_recheck_keeps_an_unmeasured_flow_a_gap():
    from repo_factory.loop.recheck import recompute

    checks = gate.record_checks(FIX / "notehatch-r1", DELIVERED)
    new = recompute(
        checks,
        {"scenarios": {"e27": {"status": "passed"}, "e28": {"status": "failed"}}},
        FIX / "notehatch-r1",
    )
    c = new["completeness"]
    assert not c["ok"] and c["detail"] == (
        "1/6 flows work end to end; 5 not measured; only 1 of 6 flows could be measured, "
        "too few to call it complete"
    )
    assert c["gap"].startswith("F2 Preview what will be exported, F3 ")


def test_completeness_check_words():
    c = gate.completeness_check(
        [
            ("F1", "A", "works"),
            ("F2", "B", "environment"),
            ("F3", "C", "unchecked"),
            ("F4", "D", "broken"),
        ]
    )
    assert not c["ok"]
    assert c["detail"] == "1/4 flows work end to end; not yet: F4 D; 2 not measured"
    assert c["gap"] == (
        "F2 B not measured: the sandbox fails it in both copies; "
        "F3 C not measured: no scenario checked it"
    )
