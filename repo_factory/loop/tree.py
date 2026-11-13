"""A delivered tree on disk: ``export`` (a branch of a build's ``out`` repo, as a plain
folder, without repo-factory's own working files ``DROP``), ``readme_path``, and ``git`` (a git call
that raises ``PublishError`` with git's own words). Publishing, the pre-push checks, the
re-check and the corpus all start from such a tree."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from .. import gitcmd
from ..gh import PublishError

DROP = ("REPORT.md", "bench-meta.json")
# Build output and environments, wherever they are (``*.egg-info``, caches), and ``build/`` and
# ``dist/`` at the root: never published, even from a commit that tracked them (AU2 #6:
# NoteHatch's build/ and egg-info; Sidedoor's and Repo Recall's egg-info)
JUNK_DIRS = ("__pycache__", ".pytest_cache", "node_modules", ".venv", "venv")
JUNK_ROOT = ("build", "dist")


def git(*args: str, cwd: Path) -> str:
    p = gitcmd.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise PublishError(
            f"git {' '.join(args[:2])} failed: {(p.stderr or p.stdout).strip()[:300]}"
        )
    return p.stdout


def junk(root: Path) -> list[str]:
    """The build output in ``root`` (``JUNK_DIRS``, ``*.egg-info``, ``JUNK_ROOT``; ``*.pyc``),
    as relative paths, folders first."""
    root = Path(root)
    out: list[str] = []
    for p in sorted(root.rglob("*")):  # a folder before what is in it
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(o + "/") for o in out):
            continue
        if (
            p.is_dir()
            and (p.name in JUNK_DIRS or p.name.endswith(".egg-info") or rel in JUNK_ROOT)
            or p.is_file()
            and p.suffix == ".pyc"
        ):
            out.append(rel)
    return out


def export(out: Path, branch: str, dest: Path) -> list[str]:
    """``branch`` of ``out`` as a plain folder at ``dest``, without ``DROP`` and the build
    output (``junk``); returns the build output it left out."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    tar = dest.parent / f".{dest.name}.tar"
    git("archive", "--format=tar", "-o", str(tar), branch, cwd=Path(out))
    with tarfile.open(tar) as t:
        t.extractall(dest, filter="data")
    tar.unlink()
    for n in DROP:
        (dest / n).unlink(missing_ok=True)
    dropped = junk(dest)
    for rel in dropped:
        p = dest / rel
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    return dropped


def readme_path(root: Path) -> Path:
    for n in ("README.md", "README.rst", "README.txt", "README"):
        if (root / n).is_file():
            return root / n
    return root / "README.md"
