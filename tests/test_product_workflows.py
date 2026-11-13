"""A product's own workflow is looked at before the push: a self-hosted label,
``pull_request_target`` or a write permission holds the publish, in words, and Publish anyway
never pushes over it."""

from __future__ import annotations

from repo_factory.loop import gate, precheck, workflows

GOOD = """name: test
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m pytest
"""
BAD = """name: ci
on:
  pull_request_target:
permissions: write-all
jobs:
  build:
    runs-on: [self-hosted, linux]
    permissions:
      contents: write
    steps:
      - run: make
"""


def tree(tmp_path, text, name="ci.yml"):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    return tmp_path


def test_a_plain_linux_workflow_passes(tmp_path):
    assert workflows.scan(tree(tmp_path, GOOD)) == []
    assert workflows.scan(tmp_path / "nothing") == []


def test_what_holds_the_publish_is_said(tmp_path):
    got = workflows.scan(tree(tmp_path, BAD))
    assert got == [
        ".github/workflows/ci.yml: runs on pull_request_target (a stranger's change with secrets)",
        ".github/workflows/ci.yml: asks for a token that may write (write-all)",
        ".github/workflows/ci.yml: job build runs on a self-hosted runner",
        ".github/workflows/ci.yml: job build asks for a token that may write (contents: write)",
    ]
    # a file repo-factory staged itself is its own, not the build's
    assert workflows.scan(tmp_path, skip=[".github/workflows/ci.yml"]) == []


def test_publish_anyway_never_pushes_over_it():
    rec = {
        "blocked": [
            "workflow check: .github/workflows/ci.yml: job build runs on a self-hosted runner"
        ]
    }
    held = precheck.holds(rec)
    assert held == [
        ("workflow", ".github/workflows/ci.yml: job build runs on a self-hosted runner")
    ]
    plan = gate.override_plan({}, [k for k, _ in held], ["tests", "workflow"])
    assert plan["refused"] == [("workflow", gate.NEVER["workflow"])] and not plan["overrides"]
