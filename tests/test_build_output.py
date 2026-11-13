"""Build output never reaches a published repo (AU2 #6).

NoteHatch went public with ``build/`` and ``notehatch.egg-info/`` and no ``.gitignore``: its
delivered tree had its own ``.gitignore`` naming both, and delivery deleted it (``_strip_scaffold``
took it for repo-factory's scaffold, which v5 never writes) before ``git add -A``. Sidedoor's and Repo
Recall's staged trees carried their egg-info the same way. The replay below is NoteHatch's
round 3 delivery: its file names and its own ``.gitignore``, copied read-only (the contents
are stand-ins)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repo_factory import gitcmd
from repo_factory.loop import tree
from repo_factory.v5 import change, workspace

# NoteHatch's own .gitignore (tasks/notehatch/.den/v5/r3/deliver/.gitignore), as delivered
NOTEHATCH_IGNORE = (
    "__pycache__/\n*.egg-info/\nbuild/\n.pytest_cache/\n*.pyc\n.venv/\nvenv/\n"
    "env/\n.env\ndist/\n.eggs/\n.tox/\n.mypy_cache/\n.ruff_cache/\n.coverage\n"
    "htmlcov/\n.idea/\n.vscode/\n.DS_Store\n"
)
JUNK = [
    "build/lib/notehatch/__init__.py",
    "build/lib/notehatch/cli.py",
    "notehatch.egg-info/PKG-INFO",
    "notehatch.egg-info/SOURCES.txt",
    "notehatch.egg-info/top_level.txt",
    "notehatch/__pycache__/cli.cpython-312.pyc",
]
PRODUCT = [
    "README.md",
    "pyproject.toml",
    "notehatch/__init__.py",
    "notehatch/cli.py",
    "tests/test_cli.py",
]


def delivered(root: Path, ignore: str | None = NOTEHATCH_IGNORE) -> Path:
    for rel in PRODUCT + JUNK:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"# {rel}\n")
    if ignore is not None:
        (root / ".gitignore").write_text(ignore)
    return root


def tracked(out: Path, ref: str = "repo-factory/notehatch") -> list[str]:
    p = gitcmd.run(["ls-tree", "-r", "--name-only", ref], cwd=out, capture_output=True, text=True)
    return sorted(p.stdout.split())


def cfg(tmp_path: Path):
    return SimpleNamespace(
        id="notehatch", seed_dir=tmp_path / "no-seed", user_tests=tmp_path / "no-tests"
    )


def test_notehatch_replayed_delivery_keeps_its_gitignore_and_commits_no_build_output(tmp_path):
    art = delivered(tmp_path / "deliver")
    out = tmp_path / "out"
    workspace.make_branch(cfg(tmp_path), art, out, own_gitignore=True)
    files = tracked(out)
    assert ".gitignore" in files
    assert (out / ".gitignore").read_text() == NOTEHATCH_IGNORE  # the product's, as it was
    assert sorted(PRODUCT) == [f for f in files if f != ".gitignore"]
    assert not any(
        f.startswith(("build/", "notehatch.egg-info/")) or f.endswith(".pyc") for f in files
    )


def test_a_product_without_a_gitignore_gets_the_standard_one(tmp_path):
    art = delivered(tmp_path / "deliver", ignore=None)
    out = tmp_path / "out"
    workspace.make_branch(cfg(tmp_path), art, out, own_gitignore=True)
    assert (out / ".gitignore").read_text().splitlines() == list(workspace.IGNORE)
    assert sorted(PRODUCT + [".gitignore"]) == tracked(out)


def test_the_lab_still_strips_the_gitignore_it_wrote_itself(tmp_path):
    art = delivered(tmp_path / "deliver")
    out = tmp_path / "out"
    workspace.make_branch(cfg(tmp_path), art, out)  # v1-v4: repo-factory's scaffold
    assert ".gitignore" not in tracked(out)


def test_a_revise_round_drops_build_output_an_older_head_tracked(tmp_path):
    # Sidedoor's rounds 4-10 all carried sidedoor.egg-info: a new commit on such a head
    art = delivered(tmp_path / "deliver", ignore=None)
    out = tmp_path / "out"
    workspace.make_branch(cfg(tmp_path), art, out)  # the old head, junk tracked
    assert "notehatch.egg-info/PKG-INFO" in tracked(out)
    (art / ".gitignore").write_text(NOTEHATCH_IGNORE)
    change.add_commit(cfg(tmp_path), art, out, "docs round")
    files = tracked(out)
    assert sorted(PRODUCT + [".gitignore"]) == files


def test_staging_leaves_build_output_out_of_a_commit_that_tracked_it(tmp_path):
    # the published iddv/notehatch: build/ and the egg-info committed, no .gitignore
    art = delivered(tmp_path / "deliver", ignore=None)
    out = tmp_path / "out"
    workspace.make_branch(cfg(tmp_path), art, out)
    stage = tmp_path / "stage" / "notehatch"
    dropped = tree.export(out, "repo-factory/notehatch", stage)
    assert dropped == ["build", "notehatch.egg-info"]  # caches never reach a commit
    left = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    assert left == sorted(PRODUCT)


def test_junk_keeps_a_product_folder_named_build_below_the_root(tmp_path):
    (tmp_path / "src" / "build").mkdir(parents=True)
    (tmp_path / "src" / "build" / "steps.py").write_text("")
    (tmp_path / "src" / "pkg.egg-info").mkdir()
    assert tree.junk(tmp_path) == ["src/pkg.egg-info"]
