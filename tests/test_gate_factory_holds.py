"""AU1 #1: the gate holds only on the product. What repo-factory itself could not measure (a flow
the harness's sandbox fails on both copies, a flow no scenario checked, a security review
that did not run) is a gap, stated, never a hold. The cases are real rounds' records,
trimmed (``tests/fixtures/factory_holds``)."""

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
        # NoteHatch r1-r2: the scenario runner had no HOME, all six flows `environment`
        (
            "notehatch-r1",
            "completeness: F1 Install and first run, F2 Preview what will be exported",
        ),
        ("notehatch-r2", "not measured: the sandbox fails them in both copies"),
    ],
)
def test_a_factory_gap_never_holds_the_build(name, gap):
    v = judge(name)
    assert v["verdict"] == "ready", v["reasons"]
    assert any(gap in g for g in v["gaps"]), v["gaps"]
    assert v["checks"]["completeness"]["ok"]


def test_a_cut_off_build_with_nothing_measured_holds_overridably():
    # Sidedoor r1: cut off, no scenario for any flow and no security review: nothing to judge
    v = judge("sidedoor-r1")
    assert v["verdict"] == "held"
    assert v["reasons"] == ["completeness: " + gate.NOTHING_MEASURED]
    assert v["checks"]["security"]["ok"]
    assert "security: the security review did not run" in v["gaps"]
    plan = gate.override_plan(v["checks"], [])
    assert [k for k, _ in plan["overrides"]] == ["completeness"] and not plan["refused"]


def test_a_security_review_that_did_not_run_is_a_gap_and_overridable():
    # Sidedoor r2: the same gaps, and a product hold beside them (no README: no test command)
    v2 = judge("sidedoor-r2")
    assert v2["verdict"] == "held"
    assert [r.split(":")[0] for r in v2["reasons"]] == ["tests", "completeness"]
    assert "security: the security review did not run" in v2["gaps"]
    plan = gate.override_plan(v2["checks"], [])
    assert [k for k, _ in plan["overrides"]] == ["tests", "completeness"] and not plan["refused"]


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
    v = judge("sidedoor-r1")
    gate.record(tmp_path, v)
    import json

    rec = json.loads((tmp_path / ".den" / "v5" / "r1" / "gate.json").read_text())
    assert rec["gaps"] == v["gaps"] and rec["checks"]["security"]["gap"]
    line = publish.gaps_line(v)
    assert line == (
        "Not every check could run before this release: the security review did not run."
    )
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
    assert c["ok"] and c["detail"] == "1/6 flows work end to end; 5 not measured"
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
