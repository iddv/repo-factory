"""The DCO check: every commit signed off by its author; Dependabot's commits (and only its
own) pass with Dependabot's own sign-off. The commits are made up here, in a scratch repo."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dco", ROOT / "scripts" / "ci" / "dco.py")
dco = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dco)

BOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
BOT_SIGNOFF = "Signed-off-by: dependabot[bot] <support@github.com>"


def commit(repo: Path, author: tuple[str, str], body: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0],
        "GIT_COMMITTER_EMAIL": author[1],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    (repo / "f").write_text(body)
    subprocess.run(["git", "add", "f"], cwd=repo, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--no-verify", "-m", body], cwd=repo, env=env, check=True
    )


def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    commit(r, ("A Person", "a@example.com"), "base\n\nSigned-off-by: A Person <a@example.com>")
    return r


def test_dependabots_own_commits_pass_with_its_sign_off(tmp_path):
    r = repo(tmp_path)
    commit(r, BOT, f"Bump ruff from 0.17.3 to 0.17.4\n\n{BOT_SIGNOFF}")
    n, bad = dco.missing("HEAD~1..HEAD", cwd=str(r))
    assert (n, bad) == (1, [])


def test_nobody_else_passes_with_it(tmp_path):
    r = repo(tmp_path)
    commit(r, ("Someone", "s@example.com"), f"Change\n\n{BOT_SIGNOFF}")
    commit(r, (BOT[0], "other@example.com"), f"Bump\n\n{BOT_SIGNOFF}")
    n, bad = dco.missing("HEAD~2..HEAD", cwd=str(r))
    assert n == 2 and len(bad) == 2


def test_an_author_sign_off_still_passes_and_none_fails(tmp_path):
    r = repo(tmp_path)
    commit(r, ("B Person", "b@example.com"), "Fix\n\nSigned-off-by: B Person <b@example.com>")
    commit(r, ("B Person", "b@example.com"), "Unsigned")
    n, bad = dco.missing("HEAD~2..HEAD", cwd=str(r))
    assert n == 2 and [b.split(" ", 1)[1] for b in bad] == ["Unsigned"]
