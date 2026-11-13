"""The clone repo-factory keeps of each project's repo, for the drift checks and the code digest.

One clone per project repo, ``<loop home>/projects/clones/<owner>-<name>/``, shallow (the last
200 commits, as maintenance clones), refreshed by the maintenance tick: cloned once with the
maintenance ``gh`` (``maintain.poll.gh_for``), then fetched and reset to the default branch.
An adopted repo already has a clone, kept by its onboarding and refreshed when its default
branch moves (``poll.rebaseline``): that one is read as it is, never fetched or reset here,
so a fix or a baseline working in it is never disturbed.

git runs through ``gitcmd`` (no hooks, fsmonitor, filters or the repo's own commands)
because the tree comes from the network. Every git call goes through ``git`` and every gh call through
the ``gh`` object, both parameters, so tests use local folders and never the network."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import gitcmd, paths

DEPTH = "200"


class CloneError(RuntimeError):
    """A clone or fetch failed; the message says which and why."""


def git(*args: str, cwd: Path, env: dict | None = None) -> str:
    try:
        p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True, env=env, timeout=300)
    except OSError as e:
        raise CloneError(f"git {' '.join(args[:2])}: {e}") from None
    if p.returncode != 0:
        raise CloneError(f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def github_repo(repo: str) -> str:
    """``owner/name`` of a project repo entry (an adopted subdir's key is ``owner/name/sub``)."""
    return "/".join(repo.split("/")[:2])


def own_dir(home, repo: str) -> Path:
    return paths.Layout(home).projects / "clones" / github_repo(repo).replace("/", "-")


def work_dir(home, full: str) -> Path:
    """Maintenance's work folder of a repo (``maintain/work/<owner>-<name>/``)."""
    return paths.Layout(home).maintain / "work" / full.replace("/", "-")


def maintenance_dir(home, repo: str) -> Path | None:
    """Maintenance's clone of a repo (``maintain/work/<owner>-<name>/clone``), if any."""
    d = work_dir(home, github_repo(repo)) / "clone"
    return d if (d / ".git").exists() else None


def primary(p: dict) -> dict:
    if not p.get("repos"):
        raise CloneError(f"project {p.get('id')} has no repo")
    return p["repos"][0]


def path_of(home, p: dict) -> Path:
    """Where the project's clone is (or will be): the maintenance clone of an adopted repo
    when it exists, else repo-factory's own."""
    repo = primary(p)["repo"]
    return (maintenance_dir(home, repo) if p.get("kind") == "adopted" else None) or own_dir(
        home, repo
    )


def root_of(home, p: dict) -> Path:
    """The tree the checks read: the clone, or an adopted subdir's folder in it."""
    sub = primary(p).get("subdir")
    d = path_of(home, p)
    return d / sub if sub else d


def _head_and_tags(d: Path, run) -> tuple[str, list[str]]:
    head = run("rev-parse", "HEAD", cwd=d).strip()[:40]
    tags = [t for t in run("tag", "-l", cwd=d).split() if t]
    return head, tags


def refresh(loop, p: dict, gh=None, git_fn=None) -> dict:
    """Clone or fetch the project's repo: ``{"path", "head", "moved", "tags", "shared"}``.
    ``moved``: the head differs from the last one kept (``stats.clone_head``; the caller
    keeps the new one), so a first refresh has moved. ``shared``: the adopted repo's
    maintenance clone was read as it is. Raises CloneError."""
    run = git_fn or git
    home = loop.cfg.home
    rec = primary(p)
    branch = rec.get("default_branch") or "main"
    full = github_repo(rec["repo"])
    shared = maintenance_dir(home, rec["repo"]) if p.get("kind") == "adopted" else None
    if shared:
        d = shared
    else:
        d = own_dir(home, rec["repo"])
        if gh is None:
            from ..maintain.poll import gh_for

            gh = gh_for(loop)
        if (d / ".git").is_dir():
            _fetch(d, branch, gh, run)
        else:
            _clone(d, full, gh)
    head, tags = _head_and_tags(d, run)
    if not head:
        raise CloneError(f"{full}: the clone has no head")
    last = (p.get("stats") or {}).get("clone_head")
    return {
        "path": str(d),
        "head": head,
        "moved": head != last,
        "tags": tags,
        "shared": bool(shared),
    }


def _clone(d: Path, full: str, gh) -> None:
    shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        gh("repo", "clone", full, str(d), "--", "-q", "--depth", DEPTH)
    except Exception as e:  # noqa: BLE001 - said in the project's error
        shutil.rmtree(d, ignore_errors=True)
        raise CloneError(f"gh repo clone {full}: {e}") from None
    if not (d / ".git").is_dir():
        raise CloneError(f"gh repo clone {full} made no clone")


def _fetch(d: Path, branch: str, gh, run) -> None:
    auth = gh.git_auth() if getattr(gh, "credential", None) else []
    env = gh.env() if auth else None
    run(*auth, "fetch", "-q", "--depth", DEPTH, "--tags", "origin", branch, cwd=d, env=env)
    run("checkout", "-q", "-B", branch, f"origin/{branch}", cwd=d)
    run("reset", "-q", "--hard", f"origin/{branch}", cwd=d)
