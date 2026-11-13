"""A build's workspace and lifecycle, as v5 uses them: the delivery branch, the scaffold it
strips, the owner who answers questions, the build's pid file and its ``build_finished`` on any
exit. Moved from ``problems/repo/build.py`` (S-5a), which v1-v4 still import them from."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path

from .. import gitcmd
from ..den.artifacts import _files
from .repair import IDENTITY

PID_FILE = "build.pid"


def _git(cwd: Path, *args: str) -> str:
    p = gitcmd.run([*IDENTITY, *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def _copy_tree(src: Path, dest: Path) -> None:
    for rel in _files(src):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dest / rel)


# What a product's repo never holds (AU2 #6): written as its .gitignore when it has none, so
# delivery's ``git add -A`` leaves build output out
IGNORE = (
    "__pycache__/",
    "*.pyc",
    "*.egg-info/",
    "build/",
    "dist/",
    ".pytest_cache/",
    "node_modules/",
    ".venv/",
    "venv/",
)


def _strip_scaffold(cfg, out: Path, own_gitignore: bool = False) -> None:
    """Undo what repo_factory put in the workspace (spec.md, the generated acceptance/ tests,
    .gitignore): the workspace overwrote these names, so restore the owner's own copy from the
    seed if there is one, else remove ours. Generated tests don't belong in the owner's repo.
    ``own_gitignore`` (v5, which never writes one): the workspace's .gitignore is the
    product's and stays; a product without one gets ``IGNORE`` (NoteHatch's own listed
    ``build/`` and ``*.egg-info/``, was removed here, and both went public)."""
    seed = cfg.seed_dir if cfg.seed_dir.is_dir() else None
    for name in (
        ("spec.md", "acceptance") if own_gitignore else ("spec.md", ".gitignore", "acceptance")
    ):
        p = out / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        src = seed / name if seed is not None else None
        if src is not None and src.is_dir():
            shutil.copytree(src, p)
        elif src is not None and src.exists():
            shutil.copy2(src, p)
    if own_gitignore and not (out / ".gitignore").exists():
        (out / ".gitignore").write_text("\n".join(IGNORE) + "\n")


def make_branch(cfg, artifact: Path | None, out: Path, own_gitignore: bool = False) -> str:
    """``out`` becomes a git repo: ``main`` = seed repo + user tests, ``repo-factory/<id>`` = the
    delivered candidate. Returns the diff between them. ``own_gitignore``: see
    ``_strip_scaffold``."""
    if out.exists():
        if any(out.iterdir()) and not (out / "REPORT.md").exists():
            raise SystemExit(f"{out} exists and is not a previous repo-factory build output")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    _git(out, "init", "-q", "-b", "main")
    if cfg.seed_dir.is_dir():
        _copy_tree(cfg.seed_dir, out)
    if cfg.user_tests.is_dir():
        _copy_tree(cfg.user_tests, out / "tests")
    _git(out, "add", "-A")
    _git(out, "commit", "-q", "--allow-empty", "-m", "seed")
    branch = f"repo-factory/{cfg.id}"
    _git(out, "checkout", "-q", "-b", branch)
    if artifact is not None:
        for rel in _files(out):
            (out / rel).unlink()
        _copy_tree(artifact, out)
        _strip_scaffold(cfg, out, own_gitignore)
    _git(out, "add", "-A")
    _git(out, "commit", "-q", "--allow-empty", "-m", f"repo-factory: deliver {cfg.id}")
    return _git(out, "diff", "main", branch)


def make_oracle(owner: str, taskdir: Path, den, fake: bool, total: int | None = None):
    if owner == "human":
        from ..owner import StdinOracle

        return StdinOracle(total=total)
    if owner == "queue":
        from ..owner import HumanOracle

        return HumanOracle(den.root / "human")
    raise SystemExit(f"unknown owner {owner!r}")


def spec_title(spec: str, n: int = 100) -> str:
    """A one-line name for the spec: its first Markdown heading if it starts with one,
    else the first sentence of its first paragraph (hard wraps joined)."""
    import re

    lines = [ln.strip() for ln in spec.strip().splitlines()]
    if lines and lines[0].startswith("#"):
        text = lines[0].lstrip("#").strip()
    else:
        para = " ".join(ln for ln in spec.strip().split("\n\n")[0].splitlines() if ln.strip())
        text = re.split(r"(?<=[.!?])\s", para, maxsplit=1)[0]
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rsplit(" ", 1)[0] + "..."


def pid_path(den_root: Path) -> Path:
    return Path(den_root) / PID_FILE


@contextlib.contextmanager
def lifecycle(den, log, t0: float, cost_fn):
    """``<den>/build.pid`` while the build runs; any exception (Ctrl-C included) still
    emits ``build_finished`` with status ``error`` before it propagates."""
    pid = pid_path(den.root)
    pid.write_text(f"{os.getpid()}\n")
    try:
        yield
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            msg = "interrupted (Ctrl-C)"
        elif isinstance(e, SystemExit):
            msg = str(e.code)
        else:
            msg = f"{type(e).__name__}: {e}"
        try:
            cost = cost_fn()
        except Exception:
            cost = None
        log.emit(
            "build_finished",
            status="error",
            error=msg,
            cost_usd=cost,
            wall_s=round(time.time() - t0, 1),
        )
        raise
    finally:
        pid.unlink(missing_ok=True)
