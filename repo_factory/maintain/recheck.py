"""Re-check (registry ``maintain_recheck``): read one maintained repo again now, rather than at
the next poll or the doctor's next due date.

GitHub cannot push to repo-factory (it binds to loopback only, so there is no address for a webhook),
so what repo-factory knows of a repo comes from its poll: main's head read conditionally every
``maintain.poll_s`` (``poll.read_head``), with the doctor and a project's drift computed again
when it moved. This press covers the rest, for whoever doubts a row: main's head (without the
ETag), the drift doctor and the AGENTS.md row (a product's), and the project's clone, drift
and numbers. It reads
only: no comment, label or pull request, and no model.

It runs in the background, one at a time per repo (``repo-factory maintain recheck REPO``, detached); its
state is a small file the rows read (``overlay``), so the press answers at once and the row says
"Checking…", then the result's new age, or why it stopped."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .. import paths


class RecheckError(RuntimeError):
    pass


def state_path(home, key: str) -> Path:
    return paths.Layout(home).maintain / "recheck" / (re.sub(r"[^\w.-]+", "__", key) + ".json")


def read_state(home, key: str) -> dict | None:
    try:
        d = json.loads(state_path(home, key).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("repo") == key else None


def write_state(home, key: str, **fields) -> dict:
    p = state_path(home, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {**(read_state(home, key) or {}), **fields, "repo": key}
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)
    return d


def job_state(home, key: str) -> dict | None:
    """``{"state": running|done|failed, "since", "finished", "why", "by"}`` or None (never
    pressed). A running check whose process is gone without a word is ``failed``."""
    from .. import proc

    d = read_state(home, key)
    if d and d.get("state") == "running" and not proc.running(d.get("pid"), d.get("start")):
        d = {**d, "state": "failed", "why": "the check stopped without finishing"}
    return d


def target(loop, key: str) -> dict:
    from .poll import targets

    for t in targets(loop):
        if (t.get("key") or t["repo"]) == key:
            return t
    raise RecheckError(f"{key or 'that'} is not a repository repo-factory maintains")


def command(cfg, key: str) -> list[str]:
    m = cfg.get("maintain") or {}
    base = list(
        m.get("recheck_command")
        or [sys.executable, "-m", "repo_factory.cli", "maintain", "recheck"]
    )
    return base + [key, "--home", str(cfg.home)]


def start(loop, key: str, by: str = "ui", now: float | None = None) -> dict:
    """Start the Re-check of ``key`` in the background; returns at once. One already running
    is not started twice (``{"started": False, "since"}``)."""
    from .. import proc
    from ..models import build_env

    t = target(loop, key)
    key = t.get("key") or t["repo"]
    st = job_state(loop.cfg.home, key)
    if st and st.get("state") == "running":
        return {"started": False, "repo": key, "since": st.get("since")}
    cmd = command(loop.cfg, key)
    log = state_path(loop.cfg.home, key).with_suffix(".log")
    p = proc.spawn_detached(
        cmd, cwd=log.parent, log=log, env=build_env(loop.cfg), banner=" ".join(cmd)
    )
    write_state(
        loop.cfg.home,
        key,
        state="running",
        pid=p.pid,
        start=proc.start_time(p.pid),
        since=now or time.time(),
        finished=None,
        by=by,
        why=None,
    )
    return {"started": True, "repo": key, "pid": p.pid}


def run(loop, key: str, now: float | None = None, gh=None) -> dict:
    """The check itself (in the detached process, or the command line's foreground): main's
    head, the doctor (a product's), then the project's stats. Only the keys it computed are
    written back, over the cache as it is now (a poll may have written meanwhile)."""
    from . import doctor, revise
    from .github import Cache, Repo
    from .poll import gh_for, read_head

    now = now or time.time()
    t = target(loop, key)
    key = t.get("key") or t["repo"]
    adopted = t.get("kind") == "adopted"
    prof = t.get("profile") or {}
    if gh is None:
        gh = gh_for(loop, t["repo"])  # repositories it built (adopting left)
    cache = Cache(loop.cfg.home)
    rec = cache.repo(key)
    r = Repo(gh, t["repo"])
    out: dict = {"repo": key, "errors": []}
    out["head"] = read_head(r, rec, prof.get("default_branch") or "main", now, out, fresh=True)
    if not adopted:
        doctor.step(r, rec, now, force=True, head=out["head"])
        out["problems"] = rec["doctor"]["problems"]
    cur = cache.repo(key)  # the rows as they are now: only the doctor's go
    patch = {"head": rec.get("head")}
    if not adopted:
        # AGENTS.md's row too (reads only: the poll's agents_state, never its comments)
        ag = revise.agents_state(r, rec, now) or rec.get("agents_md")
        rows = [
            x
            for x in cur.get("rows") or []
            if x.get("kind") not in ("drift", "self_hosted", "agents_md")
        ]
        maintained = (rec.get("policy") or {}).get("status") == "maintained"
        held = revise.agents_rows(key, {**rec, "agents_md": ag}) if maintained else []
        fresh = doctor.rows(key, rec)
        if held:  # one row says it (as the poll)
            fresh = held + [x for x in fresh if x["kind"] != "drift"]
        patch.update(doctor=rec["doctor"], rows=rows + fresh)
        if ag:
            patch["agents_md"] = ag
    cache.write_repo(key, {**cur, **patch})
    out["projects"] = projects(loop, t, now, out["errors"])
    return out


def projects(loop, t: dict, now: float, errors: list[str]) -> list[str]:
    """The clone, drift and numbers of the project(s) whose repo this is, refreshed now."""
    from ..projects import stats
    from ..projects.record import Projects

    if not ((loop.cfg.get("projects") or {}).get("enabled", True)):
        return []
    sub = (t.get("profile") or {}).get("subdir")
    done = []
    for p in Projects(loop.ledger).all("active"):
        first = (p.get("repos") or [{}])[0]
        if first.get("repo") != t["repo"] or (first.get("subdir") or None) != (sub or None):
            continue
        try:
            st = stats.refresh(loop, p, now=now)
            done.append(p["id"])
            if st.get("error"):
                errors.append(f"{p['id']}: {st['error']}")
        except Exception as e:  # noqa: BLE001 - said on the row
            errors.append(f"{p['id']}: {type(e).__name__}: {e}"[:300])
    return done


def overlay(home, rows: list[dict]) -> list[dict]:
    """The Re-check's state on each row it refreshes (drift, a self-hosted runner, AGENTS.md),
    read now (the press shows at once)."""
    for r in rows:
        if r.get("kind") in ("drift", "self_hosted", "agents_md") and r.get("repo"):
            r["recheck"] = job_state(home, r["repo"])
    return rows
