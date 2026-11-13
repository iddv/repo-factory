"""The charter on a fix's diff (PJ-PLAN.md §5.3, PJ-8): a fix that removes a promise's
sentence from the README is held with that sentence; one that adds a dependency where the
charter says standard library only is held; a docs fix that keeps the promises passes; a held
fix waits for the owner at once (needs-you), not after more attempts."""

from pathlib import Path

import pytest

from repo_factory.projects import charter_check as K
from repo_factory.projects.charter import normalize, parse, save
from repo_factory.projects.record import Projects, create_for_idea

from .loop_common import make_loop
from .test_evolve import CHARTER
from .test_projects import published

pytestmark = pytest.mark.usefixtures("projects_on")

README = (
    "# Receipts\n\nRecords shell commands run through a captured shell, with exit codes, "
    "and files created, changed or deleted in the workspace.\n\nRuns no network services "
    "and makes no network calls.\n"
)
PYPROJECT = '[project]\nname = "receipts"\nrequires-python = ">=3.11"\ndependencies = []\n'


def charter():
    return normalize(parse(CHARTER["receipts"]))


def tree(tmp: Path, readme=README, pyproject=PYPROJECT) -> Path:
    root = tmp / "repo"
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text(readme)
    (root / "pyproject.toml").write_text(pyproject)
    return root


BASE = {"README.md": README, "pyproject.toml": PYPROJECT}


def test_a_fix_that_drops_a_promise_sentence_is_held_with_it(tmp_path):
    root = tree(
        tmp_path, readme="# Receipts\n\nRuns no network services and makes no network calls.\n"
    )
    got = K.problems(charter(), root, ["README.md"], BASE.get)
    assert got == [
        'charter: the promise "Records shell commands run through a captured '
        'shell,…" is no longer in the README'
    ]


def test_a_new_dependency_on_a_stdlib_only_charter_is_held(tmp_path):
    root = tree(tmp_path, pyproject=PYPROJECT.replace("[]", '["requests>=2", "rich"]'))
    got = K.problems(charter(), root, ["pyproject.toml"], BASE.get)
    assert len(got) == 1 and got[0].startswith(
        "charter: pyproject.toml adds requests, rich where the charter says"
    )
    assert "standard library only" in got[0]
    root2 = tmp_path / "r2"
    root2.mkdir()
    (root2 / "requirements-dev.txt").write_text("pytest==9.1.1\n")
    got = K.problems(charter(), root2, ["requirements-dev.txt"], lambda f: "pytest==8.3.3\n")
    assert got == []  # a version bump adds nothing


def test_a_docs_fix_that_keeps_the_promises_passes(tmp_path):
    root = tree(tmp_path, readme=README + "\n## Install\n\nOne command.\n")
    assert K.problems(charter(), root, ["README.md", "CHANGELOG.md"], BASE.get) == []
    assert K.problems(None, root, ["README.md"], BASE.get) == []  # no charter: no guard


def test_the_guard_reads_the_projects_charter_by_repo(tmp_path):
    lp = make_loop(tmp_path)
    ps = Projects(lp.ledger)
    p, _ = create_for_idea(ps, published(lp, "Receipts", "receipts"))
    save(ps, p["id"], charter(), "repo-factory", "drafted")
    root = tree(tmp_path, readme="# Receipts\n")
    got = K.for_fix(lp.cfg.home, "iddv/receipts", root, ["README.md"], BASE.get)
    assert len(got) == 2  # both promise sentences gone
    assert K.for_fix(lp.cfg.home, "iddv/other", root, ["README.md"], BASE.get) == []
