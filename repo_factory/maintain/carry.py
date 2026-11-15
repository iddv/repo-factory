"""How a fix leaves the session's workspace: as files, into a clone the host made itself.

A fix session works in its clone with tools, inside the sandbox, and that clone's ``.git`` is
bound read-write: a session can point ``origin`` at a repository it made (whose hooks would
run on the host at the push) or add a proxy to the config (which would be handed the GitHub
credential). So after the session the host runs **no** network git in that tree. Instead:

1. ``clone``: the host clones the repository afresh, from the address it builds from the
   repository's name (``url_of``), into a folder beside the workspace that no session is ever
   given, and checks out the commit the fix started from.
2. ``carry``: each file the fix changed is copied over as bytes (or removed). A link, a path
   that leaves the tree or goes through a link, or anything that is not a plain file is
   refused: nothing outside the workspace is ever read on the session's behalf.
3. ``push``: one commit in the host's clone, pushed to the address it built, onto a new
   ``repo-factory/`` branch (``taken`` names the ones already there); never forced.

The stand-in GitHub of the tests and of a scratch install is reached the way git itself
offers: ``url.<local folder>.insteadOf=https://github.com/`` in the environment
(``GIT_CONFIG_COUNT``), which a tree's own config can never set (``gitcmd`` refuses it there).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .. import gitcmd

NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class CarryError(RuntimeError):
    """The fix cannot be carried over as it is (the words say why)."""


def url_of(full: str) -> str:
    """The address the host pushes to: built from the repository's name, never read from a
    tree's config."""
    if not NAME.fullmatch(full or "") or ".." in full:
        raise CarryError(f"not a repository name: {full!r}")
    return f"https://github.com/{full}.git"


def _git(*args: str, cwd: Path, env: dict | None = None, check: bool = True) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        sub = next((a for a in args if not a.startswith("-") and "=" not in a), "git")
        raise CarryError(f"git {sub} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def clone(full: str, dest: Path, base: str, auth: list[str], env: dict | None) -> Path:
    """The host's own clone of ``full`` at ``base`` (the commit the fix started from), in
    ``dest`` (made afresh; never inside a session's workspace)."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(*auth, "clone", "-q", "--no-checkout", url_of(full), str(dest), cwd=dest.parent, env=env)
    gitcmd.pin(dest)
    if not re.fullmatch(r"[0-9a-f]{40}", base or ""):
        raise CarryError(f"the fix's starting commit is not a commit name: {base!r}")
    _git("checkout", "-q", "--detach", base, cwd=dest)
    return dest


def _rel(f: str) -> Path:
    p = Path(f)
    if not f or p.is_absolute() or ".." in p.parts or ".git" in p.parts:
        raise CarryError(f"{f}: outside the repository")
    return p


def _through_link(root: Path, rel: Path) -> str | None:
    """The first folder on the way to ``root/rel`` that is a link, or None."""
    at = root
    for part in rel.parts[:-1]:
        at = at / part
        if at.is_symlink():
            return str(at.relative_to(root))
    return None


def carry(ws: Path, dest: Path, files: list[str]) -> list[str]:
    """Copies each of ``files`` (paths relative to the workspace) from ``ws`` to ``dest``, or
    removes it there when the fix removed it; stages them; returns what ``dest`` now changes
    against its checkout. Raises ``CarryError`` for a link, a path out of the tree, or anything
    but a plain file."""
    ws, dest = Path(ws), Path(dest)
    for f in files:
        rel = _rel(f)
        for root in (ws, dest):
            link = _through_link(root, rel)
            if link:
                raise CarryError(f"{f}: the folder {link} is a link; a fix never goes through one")
        src, dst = ws / rel, dest / rel
        if src.is_symlink():
            raise CarryError(f"{f}: a link; a fix never adds or changes one")
        if dst.is_symlink():
            dst.unlink()  # the fix replaced a link of main's with a file (or removed it)
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            os.chmod(dst, 0o755 if os.access(src, os.X_OK) else 0o644)
        elif not src.exists():
            if dst.is_file():
                dst.unlink()
        else:
            raise CarryError(f"{f}: not a plain file")
    _git("add", "-A", cwd=dest)
    return sorted(_git("diff", "--cached", "--name-only", "--no-renames", "HEAD", cwd=dest).split())


def taken(dest: Path, full: str, prefix: str, auth: list[str], env: dict | None) -> set[str]:
    """The branches on the repository whose names start with ``prefix``."""
    out = set()
    for ln in _git(
        *auth, "ls-remote", "--heads", url_of(full), f"{prefix}*", cwd=dest, env=env, check=False
    ).splitlines():
        ref = ln.split()[-1] if ln.split() else ""
        if ref.startswith("refs/heads/"):
            out.add(ref[len("refs/heads/") :])
    return out


def push(dest: Path, full: str, branch: str, auth: list[str], env: dict | None) -> str:
    """The host's clone's HEAD onto a new ``repo-factory/`` branch of ``full``, at the address
    it built; never forced. Returns the commit pushed."""
    if not branch.startswith("repo-factory/") or ".." in branch:
        raise CarryError(f"refusing to push to {branch}: not a repo-factory/ branch")
    head = _git("rev-parse", "HEAD", cwd=dest).strip()
    _git(*auth, "push", "-q", url_of(full), f"HEAD:refs/heads/{branch}", cwd=dest, env=env)
    return head
