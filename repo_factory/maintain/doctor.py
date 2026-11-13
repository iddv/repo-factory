"""The drift doctor on GitHub (MAINTAIN.md §3 "How it stays current", §6.4): each maintained
repo's ``main`` is checked without a model and without a clone whenever main moved (the poll
reads its head conditionally every ``maintain.poll_s``), after a backfill, on the owner's
Re-check (``recheck.py``), and at least every ``maintain.doctor_days`` days; what is wrong
becomes one row on Today, with its age, until it is fixed.

``check_repo`` reads the tree (one call) and the files the check reads (AGENTS.md, the README,
CLAUDE.md, the policy, the label file and each workflow), lays them out in a scratch directory
with every other path as an empty file, and runs ``agentsmd.check`` on it: commands in AGENTS.md
the README lacks, architecture rows that point nowhere, modules the table does not name, a
missing file, and **any workflow whose ``runs-on`` names a self-hosted runner** (product CI
runs on GitHub-hosted runners only; the machine repo-factory runs on never serves a product repo).

The result lives in the poll's disposable cache (``rec["doctor"]``); Today's rows and the build
page's Maintenance box ("AGENTS.md current" or the list) read it from there."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from . import agentsmd
from .github import Repo

READ = ("AGENTS.md", "README.md", "CLAUDE.md", ".github/repo-factory.yml", ".github/labels.yml")
MAX_PATHS = 5000
SELF_HOSTED = "self-hosted runner"


def check_repo(r: Repo, ref: str = "main") -> list[str]:
    """``agentsmd.check`` on the repo's ``ref`` as GitHub has it. ``[]``: current."""
    paths = r.tree(ref)[:MAX_PATHS]
    with tempfile.TemporaryDirectory(prefix="repo-factory-doctor-") as d:
        root = Path(d)
        want = set(READ) | {
            p["path"]
            for p in paths
            if p["type"] == "blob"
            and p["path"].startswith(".github/workflows/")
            and p["path"].endswith((".yml", ".yaml"))
        }
        for p in paths:
            rel = Path(p["path"])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            dest = root / rel
            if p["type"] == "tree":
                dest.mkdir(parents=True, exist_ok=True)
            elif p["type"] == "blob":
                dest.parent.mkdir(parents=True, exist_ok=True)
                text = r.file(p["path"], ref) if p["path"] in want else None
                dest.write_text(text or "")
        return agentsmd.check(root)


def due(rec: dict, now: float, days: float, head: str | None = None) -> bool:
    """Due when main moved since the last check (``head``: main's commit now, from the poll's
    conditional read), when a backfill finished since it (``rec["backfill_done"]``), or every
    ``days`` days for an unchanged repo (the floor for facts outside it). ``days`` 0: never."""
    if not days or days <= 0:
        return False
    d = rec.get("doctor") or {}
    last = d.get("at")
    if last is None or now - last >= days * 86400:
        return True
    if head and d.get("head") != head:
        return True
    return bool(rec.get("backfill_done") and rec["backfill_done"] > last)


def step(
    r: Repo,
    rec: dict,
    now: float | None = None,
    days: float = 7,
    force: bool = False,
    head: str | None = None,
) -> dict | None:
    """Check one repo when it is due (``force``: now); the result goes in ``rec["doctor"]``,
    with the commit of main it was computed at. A failed read keeps the previous result and
    is retried at the next poll."""
    now = now or time.time()
    if not force and not due(rec, now, days, head):
        return None
    problems = check_repo(r)
    rec["doctor"] = {
        "at": now,
        "problems": problems,
        "head": head,
        "self_hosted": [p for p in problems if SELF_HOSTED in p],
    }
    return rec["doctor"]


def rows(full: str, rec: dict) -> list[dict]:
    """Today's rows for one repo: one for a self-hosted runner (the urgent one), one for
    AGENTS.md drift."""
    d = rec.get("doctor") or {}
    probs = d.get("problems") or []
    if not probs:
        return []
    hosted = d.get("self_hosted") or []
    drift = [p for p in probs if p not in hosted]
    base = f"https://github.com/{full}"
    # checked: when the result was computed (the row says its age), at which commit of main
    seen = {"checked": d.get("at"), "head": (d.get("head") or "")[:7] or None}
    out = []
    if hosted:
        out.append(
            {
                "kind": "self_hosted",
                "number": None,
                "title": hosted[0],
                "problems": hosted,
                "url": f"{base}/tree/main/.github/workflows",
                "updated": d.get("at"),
                "created": d.get("at"),
                **seen,
            }
        )
    if drift:
        out.append(
            {
                "kind": "drift",
                "number": None,
                "title": f"AGENTS.md has drifted: {len(drift)} problem"
                + ("s" if len(drift) != 1 else ""),
                "problems": drift[:10],
                "url": f"{base}/blob/main/AGENTS.md",
                "updated": d.get("at"),
                "created": d.get("at"),
                **seen,
            }
        )
    return out


def summary(rec: dict) -> dict | None:
    """For the Maintenance box: ``{"at", "current", "problems", "head"}`` or None (not checked
    yet)."""
    d = rec.get("doctor")
    if not d:
        return None
    return {
        "at": d.get("at"),
        "current": not d.get("problems"),
        "problems": (d.get("problems") or [])[:10],
        "head": (d.get("head") or "")[:7] or None,
    }
