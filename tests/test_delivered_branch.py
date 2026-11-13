"""A revise round works on the branch its build was delivered on."""

import subprocess

from repo_factory.v5.change import delivered_branch


def _repo(path, *branches):
    path.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "start",
    )
    for b in branches:
        run("branch", b)
    return path


def test_the_current_name_first(tmp_path):
    out = _repo(tmp_path / "out", "repo-factory/notes", "older/notes")
    assert delivered_branch(out, "notes") == "repo-factory/notes"


def test_a_build_delivered_under_an_earlier_name(tmp_path):
    out = _repo(tmp_path / "out", "older/notes")
    assert delivered_branch(out, "notes") == "older/notes"


def test_no_repository_yet_or_no_match(tmp_path):
    assert delivered_branch(tmp_path / "none", "notes") == "repo-factory/notes"
    out = _repo(tmp_path / "out", "a/other")
    assert delivered_branch(out, "notes") == "repo-factory/notes"
