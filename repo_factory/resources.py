"""Where the build policies are.

In a checkout they are the repo's ``policies/``. An installed wheel (``uv tool install repo-factory``)
has no repo, so the build copies them into the package as ``repo_factory/_data/`` (``pyproject.toml``,
``force-include``); this module finds whichever exists, the checkout first. (The research's v3
prompt pack is the lab's own: ``repo_factory/lab/prompts/``.)
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent
BUNDLED = PACKAGE / "_data"


def resource(name: str) -> Path:
    """``policies``: the checkout's folder, else the wheel's copy."""
    here = REPO / name
    return here if here.is_dir() or not (BUNDLED / name).is_dir() else BUNDLED / name


POLICIES = resource("policies")
