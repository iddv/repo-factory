"""When a project is born and what the loop's tick does for it.

- **At publish** (``projects.create.on: publish``, private or public; ``public``: when it goes
  public): the published idea becomes a project; the charter drafted at ready
  (``publish.charter_draft``) becomes version 1 by repo-factory.
- **The tick** (with the maintenance poll, never in a request): a project with no charter gets
  one drafted by ``repo-factory projects charter ID`` in a process of its own (one at a time, a failed
  draft tried again after ``RETRY_S``, at most ``MAX_DRAFTS`` times); then the clone, the drift findings and the numbers of a few projects (``stats.refresh``, at
  most ``MAX_REFRESH`` a tick, each at most once per ``maintain.poll_s``).

None of this ever fails the publish or the tick: what went wrong is logged and
kept on the project, and the next tick tries again."""

from __future__ import annotations

import time

from ..errors import LoopError

RETRY_S = 6 * 3600
MAX_DRAFTS = 3
MAX_REFRESH = 4  # projects whose clone, drift and stats refresh in one tick


def settings(cfg) -> dict:
    return cfg.get("projects") or {}


def on_published(loop, idea: dict, public: bool = False) -> dict | None:
    """The project of an idea that was just published (``public``: it was just made public).
    Returns the project, or None when projects are off or wait for ``public``."""
    from . import charter as C
    from .record import Projects, create_for_idea

    s = settings(loop.cfg)
    if not s.get("enabled", False):
        return None
    ps = Projects(loop.ledger)
    have = ps.by_idea(idea["id"])
    if have:
        if public:  # the repo's visibility follows
            repos = [
                {**r, "visibility": "public"} if r.get("role") == "primary" else r
                for r in have["repos"]
            ]
            have = ps.update(have["id"], repos=repos)
        return have
    if (s.get("create") or {}).get("on", "publish") == "public" and not public:
        return None
    p, made = create_for_idea(
        ps, idea, note="created at publish" if not public else "created when it went public"
    )
    if p is None:
        return None
    draft = (idea.get("publish") or {}).get("charter_draft") or {}
    if made and draft.get("charter") and not p.get("charter"):
        p = C.save(
            ps,
            p["id"],
            draft["charter"],
            "repo-factory",
            "drafted when the build was ready",
            sources={
                "model": draft.get("model"),
                "usd": draft.get("usd"),
                "from": "the staged tree",
            },
        )
    return p


def backfill(loop, dry_run: bool = False, charters: bool = True) -> list[dict]:
    """``repo-factory projects backfill``: the projects (``record.backfill``), then, in this
    process (the command line may wait on a model), each missing charter drafted (one call
    each). No evolve run starts: the
    first runs on the loop's cadence, so the owner sees the charters before any ideas. The
    tally is kept (``meta projects_backfill``) for the Projects list's one line about it."""
    from . import charter as C
    from . import record

    out = record.backfill(loop, dry_run=dry_run)
    if dry_run:
        return out
    ps = record.Projects(loop.ledger)
    for r in out:
        p = ps.must(r["id"])
        if charters and not p.get("charter"):
            if loop.charter_model is None:
                r["charter_error"] = "charters are not drafted here (projects.enabled: false)"
            else:
                try:
                    p = C.draft_for_project(loop, p["id"], loop.charter_model)
                except Exception as e:  # noqa: BLE001 - said; the tick tries again
                    r["charter_error"] = f"{type(e).__name__}: {e}"[:300]
        if p.get("charter"):
            r["charter"] = p["charter"].get("pitch")
    loop.ledger.meta(
        "projects_backfill",
        {
            "at": time.time(),
            "projects": len(out),
            "charters": sum(1 for r in out if r.get("charter")),
        },
    )
    return out


def tick(loop, now: float | None = None) -> dict:
    """Charters for projects without one, then a few projects' clones, drift and stats
    (``refresh_stats``)."""
    from . import jobs
    from .record import Projects

    now = now or time.time()
    out: dict = {"drafting": [], "stats": [], "errors": []}
    if not settings(loop.cfg).get("enabled", False):
        return out
    ps = Projects(loop.ledger)
    busy = len(jobs.running(loop.ledger, "charter"))
    for p in ps.all("active"):
        if not p.get("charter"):
            st = jobs.state(loop.ledger, "charter", p["id"])
            tries = int(st.get("tries") or 0)
            if (
                st.get("state") in ("running", "queued")
                or busy >= 1
                or tries >= MAX_DRAFTS
                or (st.get("state") == "failed" and now - float(st.get("finished") or 0) < RETRY_S)
            ):
                continue
            try:
                jobs.spawn(loop.cfg, loop.ledger, "charter", p["id"])
                jobs.set_state(loop.ledger, "charter", p["id"], tries=tries + 1)
                out["drafting"].append(p["id"])
                busy += 1
            except LoopError as e:
                out["errors"].append(f"{p['id']}: {e}")
    out["stats"] = refresh_stats(loop, ps, now, out["errors"])
    out["evolving"] = start_runs(loop, ps, now, out["errors"])
    out["filed"] = follow_all(loop, ps, out["errors"])
    return out


def follow_all(loop, ps, errors: list[str]) -> list[str]:
    """File the accepted ideas not filed yet and let the filed ones follow their issue."""
    from . import changes as X
    from . import filing

    said = []
    for p in ps.all("active"):
        if not X.all_of(loop.ledger, p["id"], ("queued", "building", "held")):
            continue
        try:
            said += [f"{p['id']}: {w}" for w in filing.follow(loop, p)]
        except Exception as e:  # noqa: BLE001 - the next project goes on
            errors.append(f"{p['id']} filing: {type(e).__name__}: {e}"[:300])
    return said


def start_runs(loop, ps, now: float, errors: list[str]) -> list[str]:
    """Start the evolve runs that are due (``evolve.due``), each ``repo-factory projects evolve ID`` in a
    process of its own, at most ``evolve.concurrency`` at once; a "not now" idea whose time
    came is woken by the gate. Returns the projects started."""
    from . import evolve, jobs

    limit = int(((settings(loop.cfg).get("evolve") or {}).get("concurrency")) or 0)
    busy, started = len(jobs.running(loop.ledger, "evolve")), []
    for p in ps.all("active"):
        evolve.gate(loop, p, now)
        if busy >= limit:
            continue
        why = evolve.due(loop, p, now)
        if not why or jobs.state(loop.ledger, "evolve", p["id"]).get("state") in (
            "running",
            "queued",
        ):
            continue
        try:
            jobs.spawn(loop.cfg, loop.ledger, "evolve", p["id"], by="repo-factory")
            jobs.set_state(loop.ledger, "evolve", p["id"], why=why)
            started.append(p["id"])
            busy += 1
        except LoopError as e:
            errors.append(f"{p['id']} evolve: {e}")
    return started


def head_moved(home, p: dict, cache: dict | None = None) -> bool:
    """The project's repo's main, as the maintenance poll last read it, is not the commit its
    clone (and so its drift) was refreshed at."""
    from .record import cache_recs

    if not p.get("repos"):
        return False
    recs = cache_recs(home, {"repos": p["repos"][:1]}, cache)
    sha = (recs[0][1].get("head") or {}).get("sha") if recs else None
    have = (p.get("stats") or {}).get("clone_head")
    return bool(sha and have and sha != have)


def refresh_stats(loop, ps, now: float, errors: list[str]) -> list[str]:
    """The clone, drift and numbers of the active projects whose stats are older than
    ``maintain.poll_s``: at most ``MAX_REFRESH`` a tick, one after the other; one project's
    failure is kept on it (``stats.error``) and the next goes on. A project whose repo's main
    moved (the maintenance poll's head, read just before this) comes first, whatever its age,
    so its drift follows a merge within one poll; then the stalest."""
    from ..maintain.github import Cache
    from . import stats

    every = float(((loop.cfg.get("maintain") or {}).get("poll_s")) or 900)
    active, cache = ps.all("active"), Cache(loop.cfg.home).read()
    moved = {p["id"] for p in active if head_moved(loop.cfg.home, p, cache)}
    due = [
        p
        for p in active
        if p["id"] in moved or now - float((p.get("stats") or {}).get("at") or 0) >= every
    ]
    due.sort(key=lambda p: (p["id"] not in moved, float((p.get("stats") or {}).get("at") or 0)))
    done = []
    for p in due[:MAX_REFRESH]:
        try:
            st = stats.refresh(loop, p, now=now)
            done.append(p["id"])
            if st.get("error"):
                errors.append(f"{p['id']} stats: {st['error']}")
        except Exception as e:  # noqa: BLE001 - the next project goes on
            msg = f"{type(e).__name__}: {e}"[:300]
            errors.append(f"{p['id']} stats: {msg}")
            try:
                ps.update(p["id"], stats={**(p.get("stats") or {}), "error": msg, "at": now})
            except Exception:  # noqa: BLE001 - said above
                pass
    return done
