"""The exam runner (EX-PLAN §3.2, PR 1): one sandbox run per tree hash, the product's own
tests and the exam told apart, the product at /work/product and the exam at /work/exam."""

import json

import pytest

from repo_factory.harness.isolation import isolation
from repo_factory.v5 import exam

from .exam_fixture import FakeRun, expected, product

bwrap = pytest.mark.skipif(isolation() != "bwrap", reason="needs bubblewrap")


def runs(run) -> list[dict]:
    p = run.root / "exam" / "runs.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@bwrap
def test_stdlib_unittest_stays_green_with_the_exam_outside(tmp_path):
    exp = expected("stdlib_unittest_lib")
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    rec = exam.run(run, tree, own=True, readme=True)
    assert rec["ran"] and rec["layout"] == "exam"
    # the README's own command, exactly as written, in its own runner: green
    own = rec["own"]
    assert [c["cmd"] for c in own["commands"]] == exp["readme_tests"]
    assert own["green"] is exp["own_green"] and own["commands"][0]["status"] == "ok"
    assert "Ran 2 tests" in own["commands"][0]["output"]
    # the product's own tests per test id, and the exam's scenarios, told apart
    assert len(rec["tests"]) == exp["own_tests"]
    assert all(t.startswith("tests/") for t in rec["tests"])
    assert {k: v["status"] for k, v in rec["scenarios"].items()} == exp["scenarios"]
    # nothing of the exam was ever in the tree
    assert not (tree / "_verify").exists() and not (tree / "tests" / "acceptance").exists()


@bwrap
def test_one_sandbox_run_per_tree_hash(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    a = exam.run(run, tree, own=True)
    b = exam.run(run, tree, own=True)  # in memory
    run.__dict__.pop("_exam_runs")
    c = exam.run(run, tree, own=True)  # from results/<key>.json
    assert a["key"] == b["key"] == c["key"] and len(runs(run)) == 1
    assert (run.root / "exam" / "results" / f"{a['key']}.json").is_file()
    (tree / "wordlib" / "__init__.py").write_text(
        "def count(text):\n    return len(text.replace('-', ' ').split())\n"
    )
    d = exam.run(run, tree, own=True)  # a new tree hash: a new run
    assert d["tree"] != a["tree"] and len(runs(run)) == 2
    assert d["scenarios"]["x1"]["status"] == "passed"
    assert [e for e, _ in run.events].count("exam_run") == 2


@bwrap
def test_the_product_is_at_work_product_and_the_exam_read_only_at_work_exam(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    probe = (
        "import os, pathlib\n\n\ndef test_where():\n"
        "    assert os.getcwd() == '/work/product'\n"
        "    assert os.environ['REPO_FACTORY_PRODUCT_DIR'] == '/work/product'\n"
        "    assert pathlib.Path('/work/product/wordlib/__init__.py').is_file()\n"
        "    assert not pathlib.Path('/work/product/_verify').exists()\n"
        "    try:\n"
        "        open('/work/exam/_verify/x.txt', 'w')\n"
        "        wrote = True\n"
        "    except OSError:\n"
        "        wrote = False\n"
        "    assert not wrote\n"
    )
    from repo_factory.rules.model import Rule

    r = Rule(id="w1", title="where", kind="edge", source="", code=probe, status="active")
    run.store.rules["w1"] = r
    rec = exam.run(run, tree, rules=[r])
    assert rec["scenarios"]["w1"]["status"] == "passed", rec["scenarios"]["w1"]


@bwrap
def test_the_old_layout_runs_its_scenarios_inside_the_copy(tmp_path):
    """A round from before the exam moved out (its scenarios find the repository from their
    own path) keeps that layout: ``_verify/`` laid inside the product's copy, never the
    tree."""
    run = FakeRun(tmp_path, "stdlib_unittest_lib", layout="inside")
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    from repo_factory.rules.model import Rule

    code = (
        "from pathlib import Path\n\n\ndef test_root():\n"
        "    root = Path(__file__).resolve().parents[2]\n"
        "    assert (root / 'wordlib' / '__init__.py').is_file()\n"
    )
    r = Rule(id="o1", title="old", kind="edge", source="", code=code, status="active")
    run.store.rules["o1"] = r
    rec = exam.run(run, tree, rules=[r])
    assert rec["layout"] == "inside" and rec["scenarios"]["o1"]["status"] == "passed"
    assert not (tree / "_verify").exists()


def test_materialise_writes_the_exam_and_its_visible_part_with_hashes(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    run.store.rules["x1"].visibility = "held"
    man = exam.materialise(run)
    root = run.root / "exam"
    assert sorted(p.name for p in (root / "_verify" / "scenarios").glob("*.py")) == [
        "test_rule_c1.py",
        "test_rule_c2.py",
        "test_rule_x1.py",
    ]
    assert sorted(p.name for p in (root / "visible" / "_verify" / "scenarios").glob("*.py")) == [
        "test_rule_c1.py",
        "test_rule_c2.py",
    ]
    assert (root / "_verify" / "driver.py").is_file()
    assert man["exam_hash"] == exam.digest(root / "_verify")
    assert man["visible_hash"] != man["exam_hash"] and len(man["scenarios"]) == 3
    assert json.loads((root / "manifest.json").read_text())["factory_version"]
