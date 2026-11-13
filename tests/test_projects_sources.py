"""The repo's own backlog and the code digest, read from trimmed copies of Receipts and
Plotkeeper (tests/fixtures/projects) with no model and no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_factory.projects import sources

pytestmark = pytest.mark.usefixtures("projects_on")

FIX = Path(__file__).parent / "fixtures" / "projects"
RECEIPTS, PLOTKEEPER = FIX / "receipts", FIX / "plotkeeper"


def test_receipts_backlog():
    b = sources.backlog(RECEIPTS)
    assert [(s["id"], s["status"], s["severity"]) for s in b["security"]] == [
        ("S3", "open", "low"),
        ("S4", "open", "low"),
    ]
    assert b["security"][0]["title"].startswith("`receipts verify` on a deeply nested JSON")
    assert b["security"][0]["line"] == 11
    assert b["readiness"] == []
    assert [k["test"] for k in b["known_open"]] == [
        "test_receipts_verify_hostile_bundle_file_10_000_deep_nested_json.py"
    ]
    assert len(b["unreleased"]) == 1 and b["unreleased"][0].startswith("Maintenance files")
    [x] = b["xfail"]
    assert x["file"].endswith("test_receipts_verify_hostile_bundle_file_10_000_deep_nested_json.py")
    assert x["reason"].startswith("Known security issue (low)")
    assert b["todo"] == {"TODO": 0, "FIXME": 0}


def test_plotkeeper_backlog():
    b = sources.backlog(PLOTKEEPER)
    # the findings table: S1-S3 fixed, S4 and S5 open, S6 "confirm by hand" (not fixed)
    assert [s["id"] for s in b["security"]] == ["S4", "S5", "S6"]
    assert b["security"][2]["status"].endswith("confirm by hand")
    assert [r["item"] for r in b["readiness"]] == [
        "Graceful shutdown",
        "Health endpoint (services)",
    ]
    assert b["readiness"][1]["next_step"].startswith("make /health run a trivial query")
    assert len(b["known_open"]) == 2 and len(b["xfail"]) == 2
    assert {x["file"].rsplit("/", 1)[1] for x in b["xfail"]} == {k["test"] for k in b["known_open"]}


def test_backlog_of_an_empty_tree(tmp_path):
    assert sources.backlog(tmp_path) == {
        "security": [],
        "readiness": [],
        "known_open": [],
        "unreleased": [],
        "xfail": [],
        "todo": {"TODO": 0, "FIXME": 0},
    }


def test_backlog_reads_unittest_marks_and_todos(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n    @unittest.expectedFailure\n"
        "    def test_x(self):\n        pass  # TODO: fix\n"
    )
    (tmp_path / "app.py").write_text("# FIXME one\n# TODO two\n")
    b = sources.backlog(tmp_path)
    assert b["xfail"] == [{"file": "tests/test_a.py", "line": 4, "reason": ""}]
    assert b["todo"] == {"TODO": 2, "FIXME": 1}


def test_receipts_digest_lists_skip_markers():
    d = sources.digest(RECEIPTS, constants=["SKIP_MARKERS"])
    assert len(d) <= 12000
    line = next(x for x in d.splitlines() if x.strip().startswith("SKIP_MARKERS"))
    assert "('pytest skip'" in line and "('RSpec pending/skip'" in line and "…" not in line
    assert "receipts/detect.py (309 lines): Test-run / build detection" in d
    assert "skip_markers_added:" in d
    modules = [x.split(" (")[0] for x in d.splitlines() if not x.startswith("  ")]
    assert modules and all(m.startswith("receipts/") for m in modules)


def test_digest_is_capped_and_keeps_named_constants():
    d = sources.digest(RECEIPTS, max_chars=2500, constants=["SKIP_MARKERS"])
    assert len(d) <= 2500
    assert "SKIP_MARKERS" in d and d.endswith("more modules)")
    assert "receipts/__init__.py" in d


def test_digest_is_deterministic():
    a = sources.digest(PLOTKEEPER)
    assert a == sources.digest(PLOTKEEPER) and a
    paths = [x.split(" (")[0] for x in a.splitlines() if not x.startswith(("  ", "…"))]
    assert paths == sorted(paths)
    assert sources.digest(PLOTKEEPER, max_chars=3000) == sources.digest(PLOTKEEPER, max_chars=3000)
