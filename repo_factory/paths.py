"""Where repo-factory keeps its files: one home, and what is inside it. The one module that
knows these names.

The **home** (``home()``: ``--home``, else ``$REPO_FACTORY_HOME``, else ``~/.repo-factory``)
holds the settings, the ledger, the daemon's control files, the sign-in tokens, the owner's
prompt versions, by default the tasks folder (``Layout``), and the venvs and caches builds
use (``cache()``). Nothing is written outside the home, the tasks folder and a published
repository (an install from before keeps its old ``~/.cache/repo_factory``).

Standard library only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME_ENV = "REPO_FACTORY_HOME"
DEFAULT_HOME = "~/.repo-factory"


def home(explicit: str | Path | None = None) -> Path:
    """``explicit`` (``--home``), else ``$REPO_FACTORY_HOME``, else ``~/.repo-factory``,
    resolved."""
    return Path(explicit or os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser().resolve()


def cache() -> Path:
    """Where the venvs, the uv caches and the shell prefix go: ``<home>/cache``. An install
    from before this kept them in ``~/.cache/repo_factory``: when that folder is there it is
    still used as it is, never moved (delete it to start over in the home)."""
    old = Path(os.environ.get("HOME") or Path.home()) / ".cache" / "repo_factory"
    return old if old.is_dir() else home() / "cache"


@dataclass(frozen=True)
class Layout:
    """The home's entries."""

    root: Path

    settings = property(lambda self: self.root / "loop.yaml")  # the settings store
    db = property(lambda self: self.root / "loop.db")  # the ledger
    lock = property(lambda self: self.root / "daemon.lock")  # held while the loop ticks
    pid = property(lambda self: self.root / "daemon.pid")
    log = property(lambda self: self.root / "daemon.log")
    stop = property(lambda self: self.root / "STOP")  # ask the loop to stop
    pause = property(lambda self: self.root / "PAUSE")  # no new work while present
    actions = property(lambda self: self.root / "actions.jsonl")  # every registry action run
    maintain = property(lambda self: self.root / "maintain")
    projects = property(lambda self: self.root / "projects")
    publish = property(lambda self: self.root / "publish")
    tokens = property(lambda self: self.root / "tokens")  # the page's sign-in tokens
    claude = property(lambda self: self.root / "claude")  # the kept Claude login
    github = property(lambda self: self.root / "github")  # the GitHub App's keys
    prompts = property(lambda self: self.root / "prompts")  # the owner's prompt versions
    tasks = property(lambda self: self.root / "tasks")  # the default tasks folder
