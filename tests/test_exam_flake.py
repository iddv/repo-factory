"""The flake protocol (EX-PLAN §3.3): a failing scenario is run again alone, up to
``exam.reruns`` more times (5 runs in all). Once in five: flaky, repo-factory's own problem, retired
and listed, never a finding. At least ``exam.intermittent_hold_min`` (3) times: it fails. In
between: it passes with an amber "intermittent" row. The same rule for an own test command."""

import json

import pytest

from repo_factory.harness.isolation import isolation
from repo_factory.rules.model import Rule
from repo_factory.v5 import exam

from .exam_fixture import FakeRun, expected, product

pytestmark = pytest.mark.skipif(isolation() != "bwrap", reason="needs bubblewrap")


def counted(fail_on: list[int]) -> str:
    """A scenario that fails on the given runs (1-based), counted in a file under ``$HOME``
    (the sandbox's own home: it lives as long as the one exam run)."""
    return (
        "import os\n\n\ndef test_counted():\n"
        "    p = os.path.expanduser('~/.count')\n"
        "    n = int(open(p).read()) + 1 if os.path.exists(p) else 1\n"
        "    open(p, 'w').write(str(n))\n"
        f"    assert n not in {sorted(fail_on)!r}, f'run {{n}}'\n"
    )


def scenario(run, rid: str, fail_on: list[int]) -> Rule:
    r = Rule(
        id=rid,
        title=rid,
        kind="edge",
        source="",
        code=counted(fail_on),
        status="active",
        origin={"mode": "v5", "scenario": rid},
    )
    run.store.rules[rid] = r
    return r


def test_own_and_exam_are_told_apart_in_a_pytest_product(tmp_path):
    exp = expected("pytest_app")
    run = FakeRun(tmp_path, "pytest_app")
    tree = product("pytest_app", tmp_path / "tree")
    rec = exam.run(run, tree, own=True, readme=True, flake=True)
    assert len(rec["tests"]) == exp["own_tests"]
    assert set(rec["tests"].values()) == {"passed"}  # the product's conftest worked
    assert rec["own"]["green"] and [c["cmd"] for c in rec["own"]["commands"]] == exp["readme_tests"]
    assert {k: v["status"] for k, v in rec["scenarios"].items()} == exp["scenarios"]
    # a consistent failure: decided after three runs, never retired
    a2 = rec["scenarios"]["a2"]
    assert a2["runs"] == ["failed", "failed", "failed"] and a2["class"] == "consistent"


def test_once_in_five_is_flaky_and_retired(tmp_path):
    run = FakeRun(tmp_path, "pytest_app", skip=("a2",))
    tree = product("pytest_app", tmp_path / "tree")
    r = scenario(run, "f1", [1])
    rec = exam.run(run, tree, rules=[r], flake=True)
    o = rec["scenarios"]["f1"]
    assert o["runs"] == ["failed", "passed", "passed", "passed", "passed"]
    assert o["status"] == "passed" and o["class"] == "flaky" and rec["flaky"] == ["f1"]
    assert exam.absorb(run, rec) == ["f1"]
    assert run.store.rules["f1"].status == "retired"
    assert "flaky: failed once in 5 runs" in run.store.rules["f1"].reason
    row = json.loads((run.root / "exam" / "flaky.jsonl").read_text().splitlines()[0])
    assert row["id"] == "f1" and row["runs"][0] == "failed"


def test_three_of_five_fails_and_holds(tmp_path):
    run = FakeRun(tmp_path, "pytest_app", skip=("a2",))
    tree = product("pytest_app", tmp_path / "tree")
    r = scenario(run, "f3", [1, 3, 4])
    rec = exam.run(run, tree, rules=[r], flake=True)
    o = rec["scenarios"]["f3"]
    assert o["runs"] == ["failed", "passed", "failed", "failed"]  # decided at 3 failures
    assert o["status"] == "failed" and o["class"] == "intermittent" and not rec["flaky"]


def test_two_of_five_is_an_amber_row_not_a_failure(tmp_path):
    run = FakeRun(tmp_path, "pytest_app", skip=("a2",))
    tree = product("pytest_app", tmp_path / "tree")
    r = scenario(run, "f2", [1, 4])
    rec = exam.run(run, tree, rules=[r], flake=True)
    o = rec["scenarios"]["f2"]
    assert o["status"] == "passed" and o["intermittent"] and o["rate"] == 0.4
    assert exam.intermittent(rec) == [
        {"kind": "scenario", "id": "f2", "rate": 0.4, "runs": o["runs"]}
    ]


def test_hold_min_one_holds_on_any_intermittent_item(tmp_path):
    run = FakeRun(
        tmp_path,
        "pytest_app",
        skip=("a2",),
        exam_cfg={"intermittent_hold_min": 1, "flaky_retire": False},
    )
    tree = product("pytest_app", tmp_path / "tree")
    r = scenario(run, "f1", [1])
    rec = exam.run(run, tree, rules=[r], flake=True)
    assert rec["scenarios"]["f1"]["status"] == "failed" and not rec["flaky"]


def test_an_own_command_red_twice_holds_and_red_once_is_intermittent(tmp_path):
    run = FakeRun(tmp_path, "pytest_app", skip=("a1", "a2"))
    tree = product("pytest_app", tmp_path / "tree")
    (tree / "README.md").write_text(
        "# Tally\n\n## Tests\n\n    python -m pytest\n    ./flaky test\n"
    )
    (tree / "flaky").write_text(
        "#!/usr/bin/env python3\nimport os, sys\np = os.path.expanduser('~/.n')\n"
        "n = int(open(p).read()) + 1 if os.path.exists(p) else 1\nopen(p, 'w').write(str(n))\n"
        "sys.exit(1 if n == 1 else 0)\n"
    )
    (tree / "flaky").chmod(0o755)
    rec = exam.run(run, tree, rules=[], readme=True, flake=True)
    rows = {c["cmd"]: c for c in rec["own"]["commands"]}
    assert rows["python -m pytest"]["verdict"] == "green"
    assert rows["./flaky test"]["verdict"] == "intermittent"
    assert rows["./flaky test"]["runs"] == ["failed", "ok", "ok", "ok", "ok"]
    assert rec["own"]["green"] and rec["own"]["intermittent"][0]["cmd"] == "./flaky test"
