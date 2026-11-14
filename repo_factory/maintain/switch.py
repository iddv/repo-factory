"""Maintenance switched on, per repository, in repo-factory: off until you switch it on.

``<loop home>/maintain/repos.json`` (0600): ``{"<owner>/<name>": {"on", "at", "by", "merge",
"merge_at"}}``. A repository that is not here, or is here with ``on: false``, is not polled at
all: no GitHub read, no model call, no pull request. ``merge`` (off by default) lets the poll
merge repo-factory's own fixes of that repository when CI is green (bugs and docs only,
``watch.merge_step``) and cut a release after; it means nothing while ``on`` is false.

Pausing is ``pauses.py`` (a repository) and the loop's ``PAUSE`` file (everything). Both are
files, so a pause survives a restart, and the poll reads them before it does anything.

A file that cannot be read is read as nothing switched on: the safe side."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
from pathlib import Path

from .. import paths

FILE = "repos.json"
REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")


def path(home) -> Path:
    return paths.Layout(home).maintain / FILE


def valid(repo: str) -> bool:
    return isinstance(repo, str) and bool(REPO.fullmatch(repo)) and ".." not in repo


def read(home) -> dict[str, dict]:
    """Every repository's switches: ``{repo: {"on": bool, "merge": bool, ...}}``. A value
    stored as null or a wrong type is read as off (never as on)."""
    try:
        d = json.loads(path(home).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if valid(k) and isinstance(v, dict):
            out[k] = {**v, "on": v.get("on") is True, "merge": v.get("merge") is True}
    return out


def get(home, repo: str) -> dict:
    return read(home).get(repo) or {"on": False, "merge": False}


def is_on(home, repo: str) -> bool:
    return get(home, repo)["on"]


def merges(home, repo: str) -> bool:
    """Merging its own fixes: only while maintenance itself is on."""
    s = get(home, repo)
    return s["on"] and s["merge"]


def on_repos(home) -> list[str]:
    return sorted(k for k, v in read(home).items() if v["on"])


@contextlib.contextmanager
def _locked(home):
    p = path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            d = read(home)
            yield d
            tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(d, f, indent=1, sort_keys=True)
            os.replace(tmp, p)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def set_on(home, repo: str, on: bool, *, by: str = "ui", now: float | None = None) -> dict:
    """Maintenance on or off for ``repo``. Off also turns merging off, so switching it on
    again never brings back a merge switch nobody looked at since."""
    if not valid(repo):
        raise ValueError(f"not a repository name: {repo!r}")
    with _locked(home) as d:
        rec = dict(d.get(repo) or {})
        rec.update(on=bool(on), at=float(now or time.time()), by=str(by or "ui"))
        if not on:
            rec["merge"] = False
        rec.setdefault("merge", False)
        d[repo] = rec
        return dict(rec)


def set_merge(home, repo: str, on: bool, *, by: str = "ui", now: float | None = None) -> dict:
    """Merging its own fixes on or off; refused while maintenance is off for ``repo``."""
    if not valid(repo):
        raise ValueError(f"not a repository name: {repo!r}")
    with _locked(home) as d:
        rec = dict(d.get(repo) or {})
        if on and rec.get("on") is not True:
            raise ValueError(f"maintenance is off for {repo}: switch it on first")
        rec.update(merge=bool(on), merge_at=float(now or time.time()), merge_by=str(by or "ui"))
        rec.setdefault("on", False)
        d[repo] = rec
        return dict(rec)
