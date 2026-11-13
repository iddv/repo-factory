"""What repo-factory writes on its own pull requests and comments, and reads back: its signature and the
``revise-meta`` line of a fix pull request's body. A leaf module (it imports nothing of repo-factory),
so the steps that keep repo-factory's pull requests current and merge them (``current.py``,
``automerge.py``) stay out of maintenance's import cycle."""

from __future__ import annotations

import re

SIGNATURE = (
    "repo-factory (automated; the owner reviews everything, and nothing merges unless "
    "`.github/repo-factory.yml` says so)"
)
META = "<!-- repo-factory:revise-meta"
META_FIELDS = re.compile(r"(\w+)=(\S+)")


def meta(body: str) -> dict | None:
    """The fields of repo-factory's ``<!-- repo-factory:revise-meta ... -->`` line in a pull request body."""
    for line in (body or "").splitlines():
        if line.strip().startswith(META):
            return dict(META_FIELDS.findall(line.strip()[len(META) :].rstrip("->").strip()))
    return None
