"""Maintenance paused on a repository, in repo-factory, at once (the owner, 2026-10-29: "a simple way
to pause maintenance on a project").

A product repo-factory built keeps its policy in its own ``.github/repo-factory.yml``, and until now the only
switch there was ``status:`` (a pull request the owner merges). An adopted repo has its
profile's ``paused`` status, which the poll honours. Pausing maintenance on a project writes
one line here for each of its repositories, products and adopted ones alike, and for an adopted
one also sets its profile's ``paused`` status as **Pause** on Build › Repos does. While a
repository is here, the poll (``poll.targets(polling=True)``) skips it: no fix run, merge,
release, label, comment or model call on it; a fix already running goes on (Stop round on its
row stops it). Resuming removes the line and maintenance picks up from the next poll. The
repo's ``.github/repo-factory.yml`` is never touched.

``<loop home>/maintain/paused.json`` (0600): ``{"<owner>/<name>": {"at", "by", "project",
"profile"}}``; ``profile`` is True when this pause set an adopted repo's profile to paused (so
resuming restores it, and leaves alone a repo paused on its own before)."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

from .. import paths

FILE = "paused.json"


def path(home) -> Path:
    return paths.Layout(home).maintain / FILE


def read(home) -> dict[str, dict]:
    """Every paused repository: ``{repo: {"at", "by", "project", "profile"}}``; a file that
    cannot be read is read as none paused (the poll then runs, as before this file)."""
    try:
        d = json.loads(path(home).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)}


def get(home, repo: str) -> dict | None:
    return read(home).get(repo)


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


def pause(
    home,
    repo: str,
    *,
    by: str,
    project: str | None = None,
    profile: bool = False,
    now: float | None = None,
) -> dict:
    with _locked(home) as d:
        d[repo] = {
            "at": float(now or time.time()),
            "by": str(by or "ui"),
            "project": project,
            "profile": bool(profile),
        }
        return dict(d[repo])


def resume(home, repo: str) -> dict | None:
    """Removes the line; returns what it was (None: it was not paused here)."""
    with _locked(home) as d:
        return d.pop(repo, None)


def words(rec: dict | None) -> str | None:
    """ "Maintenance paused since 29 Oct 10:04, by you" (None: not paused)."""
    if not rec:
        return None
    try:
        at = time.strftime("%-d %b %H:%M", time.localtime(float(rec.get("at") or 0)))
    except (TypeError, ValueError):
        at = "a while"
    by = str(rec.get("by") or "ui")
    who = {"ui": "you", "cli": "you, on the command line", "owner": "you"}.get(
        by, by.replace("console", "the console").replace("mcp", "an assistant", 1)
    )
    return f"Maintenance paused since {at}, by {who}"
