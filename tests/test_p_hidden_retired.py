"""The gate's p_hidden check is gone (AU1 #5). It was ``1 - (unfound + unfixed + 0.5 blocked) /
max(1, claims)`` clamped to [0.5, 0.99]: 0.958-0.99 in all 36 recorded rounds, never a hold,
and 0.99 when nothing was checked. The real case: Sidedoor r1 had 0 claims and 0 scenarios and
"passed 0.99 of a hidden suite" (its records, trimmed, in ``fixtures/sidedoor_r1_gate``). The
gate on those records now has no p_hidden check and the same verdict and reasons otherwise;
and an old ``gate.json`` that lists the check still displays. Offline."""

import json
import shutil
from pathlib import Path

from repo_factory import registry
from repo_factory.loop import gate
from repo_factory.ui import runs

FIX = Path(__file__).parent / "fixtures" / "sidedoor_r1_gate"

# The gate's checks on Sidedoor r1's records before this change (origin/main's
# gate.record_checks on the same fixture): held by documents, security and completeness, while
# p_hidden passed at 0.99 with 0 claims checked.
BEFORE = {
    "documents": (False, "no evidence for the delivered tree (the security and readiness reports)"),
    "security": (False, "the security review did not run"),
    "tests": (True, None),
    "safe_defaults": (True, None),
    "completeness": (
        False,
        "0/7 flows work end to end; not yet: F1 Install and first run, "
        "F2 Manage agent profiles, F3 Run a trial, F4 Inspect a run, "
        "F5 Manage doors and tasks, F6 Run history, F7 Report and export",
    ),
    "p_hidden": (True, "p_hidden 0.99 (minimum 0.85)"),
}


def test_sidedoor_r1_has_no_p_hidden_check_and_the_same_verdict_otherwise(tmp_path):
    rd = tmp_path / "r1"
    shutil.copytree(FIX, rd)
    pred = json.loads((rd / "prediction.json").read_text())
    assert pred["p_hidden"] == 0.99 and pred["features"]["claims"] == 0  # the old number
    checks = gate.record_checks(rd)
    assert "p_hidden" not in checks
    assert set(checks) == set(BEFORE) - {"p_hidden"}
    for k, c in checks.items():
        if k in ("security", "completeness"):  # AU1 #1 reworded these: below
            continue
        ok, detail = BEFORE[k]
        assert c["ok"] is ok, k
        if detail is not None:
            assert c["detail"] == detail, k
    # AU1 #1: the review that did not run is a gap; with nothing measured completeness holds
    assert checks["security"]["ok"] and checks["security"]["gap"]
    assert checks["completeness"] == {"ok": False, "detail": gate.NOTHING_MEASURED}
    v = gate._verdict(checks, 1, None, None, None)
    assert v["verdict"] == "held" and "p_hidden" not in v
    assert [r.split(":")[0] for r in v["reasons"]] == ["documents", "completeness"]
    # what p_hidden hid is a fact on its own: nothing was checked
    assert json.loads((rd / "findings.json").read_text())["scenarios"] == []


def test_an_old_gate_json_with_the_p_hidden_check_still_displays():
    g = json.loads((FIX / "gate_r10.json").read_text())  # Sidedoor r10, 0.3.72
    assert g["checks"]["p_hidden"]["ok"]
    shown = runs._gate(g["verdict"], g["reasons"], "record", g["checks"])
    assert (shown["verdict"], shown["passed"], shown["total"]) == ("ready", 7, 7)
    # a held record over p_hidden (none exists, but the dialog must not crash on one): listed
    # by its key, overridable only when the owner's may_override still names it
    old = {**g["checks"], "p_hidden": {"ok": False, "detail": "p_hidden 0.8 (minimum 0.85)"}}
    plan = gate.override_plan(old, [], ["tests", "p_hidden"])
    assert plan["overrides"] == [("p_hidden", "p_hidden 0.8 (minimum 0.85)")]
    view = registry.plan_view({**plan, "details": {}})
    assert view["holds"][0]["label"] == "p_hidden"
    assert gate.override_plan(old, [])["refused"][0][0] == "p_hidden"  # the default list
