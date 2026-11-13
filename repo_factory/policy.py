"""Which build policy a build runs: ``--policy``, else ``$REPO_FACTORY_POLICY``, else v5, the product
pipeline (decision O-2). Moved from ``problems/repo/build.py`` (S-5a)."""

from __future__ import annotations

import sys
from pathlib import Path

from .resources import POLICIES

DEFAULT_POLICY = "v5"  # the product pipeline (decision O-2); the research policies
# (v1-v4, repo_factory/lab/policies) only by path


def resolve_policy(path: str | None) -> Path:
    import os

    path = path or os.environ.get("REPO_FACTORY_POLICY") or None  # e.g. for repo-factory ui builds
    if path and Path(path).is_dir():
        return Path(path)
    default = POLICIES / DEFAULT_POLICY
    if not path and default.is_dir():
        return default
    print(
        f"warning: policy {path or default} not found; using the built-in repo fallback "
        "policy (v1 hardcodes TSP strategies)",
        file=sys.stderr,
    )
    return Path(__file__).parent / "lab" / "problems" / "repo" / "policy"
