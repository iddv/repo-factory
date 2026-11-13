"""A project's numbers and its drift, refreshed by the maintenance tick (PJ-PLAN §2.4, §4.2).

``refresh`` writes ``projects.stats`` from reads that already happen: one ``repos/<r>`` call
through the maintenance ``gh`` (``github``), the poll's cache (counts, Needs-you rows,
the release, CI), the ledger (the build's rounds, the fix tasks, spend and tokens, the change
ideas by state) and the project's clone (refreshed here; the drift checks run on it when its
head or the charter changed since the last run). Each part fails alone: what failed is said in
``stats.error`` and the rest is still written, the old numbers kept where a read failed.

The Timeline gets a line only when something is news: the drift findings set changed (never
on every tick), the first star, the tenth open issue. No model call here."""

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

DRIFT_NOTE = 3  # findings named in the Timeline line
MILESTONES = (("stars", 1, "the first star"), ("issues", 10, "10 open issues"))


def month_start(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


# -- the parts -----------------------------------------------------------------------------------
def github(gh, p: dict) -> dict:
    """Stars, forks, watchers and open issues of the primary repo, from the GitHub API."""
    import json

    from .clones import github_repo, primary

    d = json.loads(gh("api", f"repos/{github_repo(primary(p)['repo'])}") or "{}")
    return {
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "watchers": d.get("subscribers_count"),
        "open_issues": d.get("open_issues_count"),
    }


def from_cache(recs: list[tuple[str, dict]]) -> dict:
    """Counts, Needs-you, the latest release and CI from the poll's cache records."""
    from .record import NEEDS_YOU_ROWS, counts

    rows = [r for _, rec in recs for r in rec.get("rows") or []]
    rel = next((rec["release"] for _, rec in recs if (rec.get("release") or {}).get("tag")), None)
    ci = next((rec["ci"] for _, rec in recs if isinstance(rec.get("ci"), dict)), None)
    return {
        **counts(recs),
        "needs_you": sum(1 for r in rows if r.get("kind") in NEEDS_YOU_ROWS),
        "untriaged": sum(1 for r in rows if r.get("kind") == "untriaged"),
        "release": {k: rel.get(k) for k in ("tag", "url", "at")} if rel else None,
        "ci": ci,
    }


def fix_tasks(tasks_dir, p: dict) -> list[Path]:
    """The maintenance fix folders (``loop.json`` kind ``fix``) of the project's repos."""
    want = {str(r.get("repo") or "").lower() for r in p.get("repos") or []}
    out = []
    root = Path(tasks_dir)
    for d in sorted(root.glob("fix-*")) if root.is_dir() else []:
        try:
            j = json.loads((d / "loop.json").read_text())
        except (OSError, ValueError):
            continue
        if (
            isinstance(j, dict)
            and j.get("kind") == "fix"
            and {str(j.get("key") or "").lower(), str(j.get("repo") or "").lower()} & want
        ):
            out.append(d)
    return out


def builds(idea: dict | None, fixes: list[Path]) -> dict:
    b = (idea or {}).get("build") or {}
    return {"round": b.get("round"), "changes": len(b.get("changes") or []), "fixes": len(fixes)}


def _own(ledger, p: dict, start: float) -> tuple[float, dict | None]:
    """The loop's own calls for the project since ``start``: rows carrying its id, and rows
    of its origin idea written before ``spend.project`` existed or without it (triage)."""
    q = (
        "SELECT COALESCE(SUM(usd), 0), COUNT(in_tokens) + COUNT(out_tokens), "
        "COALESCE(SUM(in_tokens), 0), COALESCE(SUM(out_tokens), 0) FROM spend "
        "WHERE ts >= ? AND (project = ? OR (project IS NULL AND idea = ?))"
    )
    with ledger.db() as c:
        r = c.execute(q, (start, p["id"], p.get("idea") or "")).fetchone()
    return float(r[0]), ({"in": int(r[2]), "out": int(r[3])} if r[1] else None)


def spend(ledger, p: dict, idea: dict | None, fixes: list[Path], now: float) -> tuple[dict, dict]:
    """``({"month", "all"} in dollars, {"month", "all"} in tokens)``: the loop's own calls for
    the project plus the build's and the fixes' cost ledgers. Tokens are None where no row
    carried them (no token data is not zero tokens)."""
    from ..loop.ledger import add_tokens, build_spend, build_tokens

    tasks = [d for d in [(idea or {}).get("task_dir"), *map(str, fixes)] if d]
    usd, tok = {}, {}
    for k, start in (("month", month_start(now)), ("all", 0.0)):
        own, own_tok = _own(ledger, p, start)
        usd[k] = round(own + build_spend(tasks, start), 4)
        tok[k] = add_tokens(own_tok, build_tokens(tasks, start))
    return usd, tok


def ideas(ledger, pid: str) -> dict:
    with ledger.db() as c:
        return {
            r[0]: int(r[1])
            for r in c.execute(
                "SELECT state, COUNT(*) FROM changes WHERE project = ? GROUP BY state", (pid,)
            )
        }


# -- drift ---------------------------------------------------------------------------------------
def finding_key(f: dict) -> str:
    return f"{f.get('check')}|{f.get('where')}|{f.get('what')}"


def drift_due(st: dict, head: str, charter_v: int) -> bool:
    """The checks run after the default branch moved (and the first time), when the charter
    changed (its promises are checked), or when the last run did not finish."""
    d = st.get("drift") or {}
    # a run from before the anchors were kept judged promises the README never had: again
    return (
        d.get("commit") != head
        or int(d.get("charter_v") or 0) != int(charter_v or 0)
        or "anchors" not in d
    )


def run_drift(home, p: dict, clone: dict, *, latest=None, now: float) -> dict:
    """``drift.run`` on the clone: ``{"findings", "at", "commit", "charter_v"}``. The pins'
    latest versions come from ``latest.lookup`` (a day's cache; ``latest`` replaces PyPI)."""
    from . import drift
    from . import latest as L
    from .clones import primary

    root = Path(clone["path"]) / (primary(p).get("subdir") or "")
    names = L.pinned_names(root)
    newest = L.lookup(home, names, fetch=latest, now=now) if names else {}
    # a promise is drift only when the README was seen to say it and no longer does
    seen = set(((p.get("stats") or {}).get("drift") or {}).get("anchors") or [])
    found = drift.run(
        root,
        tags=clone.get("tags"),
        latest=newest,
        charter=p.get("charter"),
        today=datetime.date.fromtimestamp(now),
        seen_anchors=seen,
    )
    return {
        "findings": found,
        "at": now,
        "commit": clone["head"],
        "charter_v": int(p.get("charter_v") or 0),
        "anchors": sorted(seen | set(drift.anchors_in_readme(root, p.get("charter")))),
    }


def drift_note(new: list[dict], old: list[dict] | None) -> str | None:
    """The Timeline line when the findings set changed, else None."""
    keys = {finding_key(f) for f in new}
    if keys == {finding_key(f) for f in old or []}:
        return None
    if not new:
        return f"drift: no findings now (there were {len(old or [])})"
    named = "; ".join(str(f.get("what") or "")[:140] for f in new[:DRIFT_NOTE])
    more = f" (and {len(new) - DRIFT_NOTE} more)" if len(new) > DRIFT_NOTE else ""
    return f"drift: {len(new)} finding{'s' if len(new) != 1 else ''}: {named}{more}"


def milestones(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """``(reached, notes)``: a milestone is said once, when a refresh crosses it; one already
    passed at the first refresh is marked reached without a line."""
    reached = list(old.get("milestones") or [])
    notes = []
    for key, n, what in MILESTONES:
        tag = f"{key}:{n}"
        have = new.get(key)
        if tag in reached or have is None or int(have) < n:
            continue
        reached.append(tag)
        if old.get(key) is not None and int(old[key]) < n:
            notes.append(what)
    return reached, notes


# -- the refresh ---------------------------------------------------------------------------------
def refresh(
    loop,
    p: dict,
    *,
    gh=None,
    now: float | None = None,
    cache: dict | None = None,
    latest=None,
    git_fn=None,
) -> dict:
    """Refresh the clone, the drift findings and the numbers of one project and write them to
    ``projects.stats`` (keys kept: see the module docstring). Returns the stats written.
    ``gh``: the maintenance ``gh`` by default; ``cache``: the poll's cache as read;
    ``latest``: the pins' latest-version fetch (a dict in tests); ``git_fn``: git's runner."""
    from . import clones
    from .record import Projects, cache_recs

    now = now or time.time()
    ps = Projects(loop.ledger)
    p = ps.must(p["id"])
    old = dict(p.get("stats") or {})
    st = dict(old)
    errors = []
    if gh is None:
        from ..maintain.poll import gh_for

        gh = gh_for(loop)
    try:
        c = clones.refresh(loop, p, gh=gh, git_fn=git_fn)
        st.update(clone_head=c["head"], clone_at=now)
        if c["moved"] or drift_due(st, c["head"], p.get("charter_v") or 0):
            st["drift"] = run_drift(loop.cfg.home, p, c, latest=latest, now=now)
            note = drift_note(st["drift"]["findings"], (old.get("drift") or {}).get("findings"))
            if note:
                ps.event(p["id"], "maintenance", note, commit=c["head"][:12])
    except Exception as e:  # noqa: BLE001 - the numbers still refresh
        errors.append(f"clone: {type(e).__name__}: {e}"[:300])
    try:
        st.update(github(gh, p))
    except Exception as e:  # noqa: BLE001 - the last numbers stay
        errors.append(f"github: {type(e).__name__}: {e}"[:300])
    st.update(from_cache(cache_recs(loop.cfg.home, p, cache)))
    idea = loop.ledger.get(p["idea"]) if p.get("idea") else None
    fixes = fix_tasks(loop.cfg.tasks_dir, p)
    st["builds"] = builds(idea, fixes)
    st["spend"], st["tokens"] = spend(loop.ledger, p, idea, fixes, now)
    st["ideas"] = ideas(loop.ledger, p["id"])
    st["milestones"], notes = milestones(old, st)
    st.update(error="; ".join(errors) or None, at=now)
    ps.update(p["id"], stats=st)
    for what in notes:
        ps.event(p["id"], "stats", f"{p['name']} reached {what}", milestone=what)
    return st
