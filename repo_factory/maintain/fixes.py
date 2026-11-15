"""What repo-factory's fixes did, kept in repo-factory, never on the issue.

Maintenance writes nothing on an issue (no comment, no label): the only thing GitHub sees of
a fix is its pull request. So the attempts, how each ended and why live here, and the
project's page reads them:

``<loop home>/maintain/fixes.json`` (0600): ``{"<owner>/<name>#<n>": {"attempts", "outcome",
"stage", "why", "pr", "head", "branch", "usd", "at", "history": [...]}}``.

- ``attempts``: failed attempts since the last Retry; at ``revise.max_attempts`` the issue
  waits for you (``waits``) and the poll leaves it alone until you press Retry.
- ``outcome``: ``pr`` (a pull request is open: ``pr``, its ``head`` commit and ``branch``),
  ``failed``, ``cannot_reproduce`` (waits for you at once), ``skipped`` (nothing was tried).
- ``history``: the last ``KEEP`` endings, newest last.

A file that cannot be read is read as empty: a fix may then be tried once more than it
should, which costs at most one fix's cap."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

from .. import paths

FILE = "fixes.json"
KEEP = 5
COUNTED = ("failed",)  # what counts as a failed attempt
WAITS = ("cannot_reproduce",)  # what waits for you at once


def key(repo: str, number: int) -> str:
    return f"{repo}#{int(number)}"


def path(home) -> Path:
    return paths.Layout(home).maintain / FILE


def read(home) -> dict[str, dict]:
    try:
        d = json.loads(path(home).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)}


def get(home, repo: str, number: int) -> dict:
    return read(home).get(key(repo, number)) or {}


def of_repo(home, repo: str) -> dict[int, dict]:
    out = {}
    for k, v in read(home).items():
        r, _, n = k.rpartition("#")
        if r == repo and n.isdigit():
            out[int(n)] = v
    return out


def attempts(rec: dict | None) -> int:
    try:
        return max(0, int((rec or {}).get("attempts") or 0))
    except (TypeError, ValueError):
        return 0


def waits(rec: dict | None, max_attempts: int) -> str | None:
    """Why this issue waits for you (the poll starts nothing on it), or None."""
    rec = rec or {}
    if rec.get("outcome") in WAITS:
        return "it could not be reproduced as a failing test: add detail, then press Retry"
    if attempts(rec) >= max(1, int(max_attempts)):
        return (
            f"{attempts(rec)} attempts failed (the last in the {rec.get('stage') or '?'} stage): "
            "press Retry to let it try again, or fix it by hand"
        )
    return None


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


def ended(home, repo: str, number: int, out: dict, now: float | None = None) -> dict:
    """One fix's ending (``revise.Fix.run``'s result, or the poll's word for a process that
    died): the record moved on, and returned."""
    now = float(now or time.time())
    outcome = str(out.get("outcome") or "failed")
    with _locked(home) as d:
        rec = dict(d.get(key(repo, number)) or {})
        n = attempts(rec) + (1 if outcome in COUNTED else 0)
        line = {
            "outcome": outcome,
            "stage": out.get("stage"),
            "why": (str(out.get("why") or "")[:600]) or None,
            "pr": out.get("pr"),
            "usd": round(float(out.get("usd") or 0.0), 4),
            "at": now,
        }
        if outcome == "pr":
            line.update(head=out.get("head"), branch=out.get("branch"), url=out.get("url"))
            # what merging judges the diff by: the kind the fix ran as and its reproducing test
            line.update(
                kind=out.get("kind") if isinstance(out.get("kind"), str) else None,
                test=out.get("test") if isinstance(out.get("test"), str) else None,
            )
        rec.update(line, attempts=n)
        rec["history"] = ([*(rec.get("history") or [])] + [line])[-KEEP:]
        d[key(repo, number)] = rec
        return dict(rec)


def retry(home, repo: str, number: int, by: str = "ui", now: float | None = None) -> dict:
    """Your Retry: the failed attempts are forgotten (the history stays), so the next poll may
    start a fix again while the issue is open and carries your ``go``."""
    with _locked(home) as d:
        rec = dict(d.get(key(repo, number)) or {})
        rec.update(attempts=0, outcome="retry", retry_at=float(now or time.time()), retry_by=by)
        d[key(repo, number)] = rec
        return dict(rec)


def opened(home, repo: str) -> dict[int, dict]:
    """The pull requests repo-factory's fixes opened on ``repo``: ``{pr: record}``, each with
    the commit the fix pushed and its ``repo-factory/`` branch (a line without them is not one
    this install pushed, whatever else says so). The poll counts only these as "the fix of N",
    and the merge step takes only these (only while their head is the commit pushed)."""
    out = {}
    for n, rec in of_repo(home, repo).items():
        for h in rec.get("history") or []:
            if not isinstance(h, dict):
                continue
            if (
                h.get("outcome") == "pr"
                and isinstance(h.get("pr"), int)
                and isinstance(h.get("head"), str)
                and h["head"]
                and str(h.get("branch") or "").startswith("repo-factory/")
            ):
                out[h["pr"]] = {**h, "issue": n}
    return out


def month_usd(home, since: float) -> float:
    """What fixes recorded here cost since ``since`` (a cross-check; the ledger's spend table
    is what the caps read)."""
    total = 0.0
    for rec in read(home).values():
        for h in rec.get("history") or []:
            if float(h.get("at") or 0) >= since:
                total += float(h.get("usd") or 0)
    return round(total, 4)
