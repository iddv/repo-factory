"""The own-suite run measures a Node product too (``exam.py``, ``examrunner.own_node``): a tree
with an ``npm test`` script has it run in the exam sandbox with Node's junit reporter, per
test (``file::name``) beside pytest's, so the lean flow's completeness (each flow's own
tests, by the harden step's map) is measured instead of "unchecked".

The real case (track LEAN): Plotkeeper is a Node product (``node --test``); its harden step
mapped every flow to ``test/*.test.js`` tests, and all of them were "unchecked" because the
own-suite run was pytest only."""

import json
import shutil

import pytest

from repo_factory.harness.isolation import isolation
from repo_factory.v5 import exam, lean

from .exam_fixture import FakeRun

bwrap = pytest.mark.skipif(
    isolation() != "bwrap" or not shutil.which("npm"), reason="needs bubblewrap and Node"
)

A = """const test = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
test('F1: install and first run: setup, empty dashboard', () => {
  // a test that runs the product's own command, as an e2e test does
  const out = execFileSync(process.execPath, ['-e', 'console.log("ready")']).toString();
  assert.equal(out.trim(), 'ready');
});
test.describe('offers', () => {
  test('F4: offer a freed plot to the next person', () => { assert.equal(1, 2); });
  test('later', { skip: true }, () => {});
});
"""
B = """const test = require('node:test');
test('F2: manage plots', () => {});
"""


def node_tree(root, script="node --test --test-concurrency=1 test/*.test.js"):
    (root / "test").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": "pk", "version": "1.0.0", "scripts": {"test": script}})
    )
    (root / "test" / "e2e.test.js").write_text(A)
    (root / "test" / "plots.test.js").write_text(B)
    return root


def test_which_trees_have_a_node_suite(tmp_path):
    assert exam.node_suite(node_tree(tmp_path / "a"))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
    )
    assert not exam.node_suite(tmp_path / "b")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "package.json").write_text("{not json")
    assert not exam.node_suite(tmp_path / "c") and not exam.node_suite(tmp_path / "none")


def test_a_node_flow_map_names_tests_by_their_own_words():
    tests = {
        "test/e2e.test.js::F1: install and first run: setup, empty dashboard": "passed",
        "test/e2e.test.js::F4: offer a freed plot to the next person": "failed",
        "test/plots.test.js::F2: manage plots": "passed",
        "tests/test_app.py::test_add": "passed",
    }
    assert lean.tests_of(
        ["test/e2e.test.js::F1: install and first run: setup, empty dashboard"], tests
    ) == ["test/e2e.test.js::F1: install and first run: setup, empty dashboard"]
    assert lean.tests_of(["test/plots.test.js"], tests) == ["test/plots.test.js::F2: manage plots"]
    # pytest's readings are unchanged: a class is the file, a test name is that test
    assert lean.tests_of(["tests/test_app.py::TestApp"], tests) == ["tests/test_app.py::test_add"]
    assert lean.tests_of(["tests/test_app.py::TestApp::test_add"], tests) == [
        "tests/test_app.py::test_add"
    ]
    assert lean.tests_of(["tests/test_app.py::test_gone"], tests) == []


@bwrap
def test_npm_test_is_run_per_test_beside_pytest(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    tree = node_tree(tmp_path / "tree")
    rec = exam.run(run, tree, rules=[], own=True)
    assert rec["ran"], rec.get("why")
    assert rec["tests"] == {
        "test/e2e.test.js::F1: install and first run: setup, empty dashboard": "passed",
        "test/e2e.test.js::F4: offer a freed plot to the next person": "failed",
        "test/e2e.test.js::later": "skipped",
        "test/plots.test.js::F2: manage plots": "passed",
    }, rec.get("own_node")
    assert rec["own_node"]["returncode"] != 0
    assert "2" in rec["details"]["test/e2e.test.js::F4: offer a freed plot to the next person"]
    # the lean flow's completeness from it, by a Plotkeeper-shaped map
    dod = [{"id": "F1"}, {"id": "F2"}, {"id": "F4"}]
    mapping = {
        "F1": ["test/e2e.test.js::F1: install and first run: setup, empty dashboard"],
        "F2": ["test/plots.test.js"],
        "F4": ["test/e2e.test.js::F4: offer a freed plot to the next person"],
    }
    st = {k: v["status"] for k, v in lean.flow_statuses(dod, mapping, rec["tests"]).items()}
    assert st == {"F1": "works", "F2": "works", "F4": "broken"}


@bwrap
def test_a_node_runner_without_a_report_gives_its_exit_only(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    tree = node_tree(tmp_path / "tree", script='node -e "process.exit(3)"')
    rec = exam.run(run, tree, rules=[], own=True)
    assert rec["ran"] and rec["tests"] == {} and rec["own_node"]["returncode"] == 3
