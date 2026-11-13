"""A docs revise round reuses its parent's exam record (AU1 #8): when only Markdown changed,
the delivered tree is not measured again; a changed README test command runs alone; any
other change runs everything. The sandbox is replaced by a counting fake."""

import json
from pathlib import Path

import pytest

from repo_factory.v5 import exam

from .exam_fixture import FakeRun, product

NAME = "stdlib_unittest_lib"
SIDEDOOR = Path(__file__).parent / "fixtures" / "au1" / "sidedoor_docs_rounds.json"


@pytest.fixture
def calls(monkeypatch):
    """Every sandbox run ``exam.run`` would start: what it measured."""
    seen = []

    def fake(run, tree, tk, rules, exam_dir, ehash, cmds, own, reverse, flake):
        seen.append({"own": bool(own), "readme": [c["cmd"] for c in cmds], "rules": len(rules)})
        return {
            "tree": tk,
            "exam_hash": ehash,
            "at": 0.0,
            "layout": exam.layout(run),
            "ran": True,
            "why": None,
            "flaky": [],
            "wall_s": 1.0,
            "scenarios": {r.id: {"status": "passed"} for r in rules},
            "own": exam.own_summary(
                [{"cmd": c["cmd"], "status": "ok", "runs": ["ok"]} for c in cmds], flake
            )
            if cmds
            else None,
            "tests": {"tests/test_wordlib.py::test_count": "passed"} if own else None,
            "errors": [],
            "details": {},
        }

    monkeypatch.setattr(exam, "_invoke", fake)
    return seen


def parent(tmp_path) -> tuple[Path, dict]:
    """Round 1 delivered: its tree and its final record."""
    tree = product(NAME, tmp_path / "t1")
    rec = exam.final(FakeRun(tmp_path / "r1", NAME), tree)
    return tree, rec


def child(tmp_path, tree: Path) -> tuple[FakeRun, Path]:
    """Round 2: the same exam (its rule store is round 1's), a copy of the tree."""
    import shutil

    t2 = tmp_path / "t2"
    shutil.copytree(tree, t2)
    return FakeRun(tmp_path / "r2", NAME), t2


def edit(p: Path, old: str, new: str) -> None:
    p.write_text(p.read_text().replace(old, new))


def test_a_readme_only_round_reuses_the_parent_record(tmp_path, calls):
    tree, prev = parent(tmp_path)
    assert len(calls) == 1 and prev["code"]
    run, t2 = child(tmp_path, tree)
    edit(t2 / "README.md", "Standard library only.", "It needs only the standard library.")
    rec = exam.final(run, t2, parent=(1, prev))
    assert len(calls) == 1  # nothing ran again
    assert rec["reused"] == {"round": 1, "key": prev["key"], "readme": "reused"}
    assert rec["scenarios"] == prev["scenarios"] and rec["tests"] == prev["tests"]
    assert rec["tree"] == exam.tree_key(t2) != prev["tree"]
    assert (run.root / "exam" / "results" / f"{rec['key']}.json").is_file()
    assert exam.reused_words(rec) == "Exam reused from round 1: only documentation changed"
    assert ("exam_reused", {"tree": rec["tree"], "round": 1, "readme": "reused"}) in run.events


def test_a_changed_readme_test_command_runs_only_the_commands(tmp_path, calls):
    tree, prev = parent(tmp_path)
    run, t2 = child(tmp_path, tree)
    edit(t2 / "README.md", "discover -s tests -v", "discover -s tests")
    rec = exam.final(run, t2, parent=(1, prev))
    assert calls[1:] == [
        {"own": False, "readme": ["python3 -m unittest discover -s tests"], "rules": 0}
    ]
    assert rec["reused"]["readme"] == "run again"
    assert [c["cmd"] for c in rec["own"]["commands"]] == ["python3 -m unittest discover -s tests"]
    assert rec["scenarios"] == prev["scenarios"]
    assert exam.reused_words(rec).endswith("; the README's test commands ran again")


def test_a_docs_round_that_also_touched_code_runs_everything(tmp_path, calls):
    tree, prev = parent(tmp_path)
    run, t2 = child(tmp_path, tree)
    edit(t2 / "README.md", "Standard library only.", "Only the standard library.")
    (t2 / "wordlib" / "__init__.py").write_text(
        (t2 / "wordlib" / "__init__.py").read_text() + "\n# a stray change\n"
    )
    rec = exam.final(run, t2, parent=(1, prev))
    assert calls[1:] == [
        {"own": True, "readme": ["python3 -m unittest discover -s tests -v"], "rules": 3}
    ]
    assert "reused" not in rec


def test_an_older_parent_record_without_a_code_key_is_not_reused(tmp_path, calls):
    tree, prev = parent(tmp_path)
    run, t2 = child(tmp_path, tree)
    old = {k: v for k, v in prev.items() if k != "code"}
    exam.final(run, t2, parent=(1, old))
    assert len(calls) == 2


@pytest.mark.parametrize(
    "rnd", json.loads(SIDEDOOR.read_text())["rounds"], ids=lambda r: f"r{r['round']}"
)
def test_sidedoors_docs_rounds_replayed(tmp_path, calls, rnd):
    """The files Sidedoor's docs rounds changed, applied to a delivered tree: rounds 7 and 8
    (Markdown only, ~800 s of deliver each) now reuse the record; round 10, which also lost
    repo-factory's old acceptance suite (.py files), runs everything again."""
    tree, prev = parent(tmp_path)
    run, t2 = child(tmp_path, tree)
    for f in rnd["changed"]:
        p = t2 / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text() if p.is_file() else "") + "\nOne more plain sentence.\n")
    rec = exam.final(run, t2, parent=(rnd["revises"], prev))
    assert bool(rec.get("reused")) is rnd["reuse"]
    assert len(calls) == (1 if rnd["reuse"] else 2)
