"""A project's record (PJ-PLAN.md §2): a repo that keeps evolving from the idea it came from.

A project joins what is scattered elsewhere: the idea that started it (a ledger idea; none for
an adopted repo), the repo it produced or the repo that was adopted (``repos``: the primary,
then any fork or mirror), its charter (``charter``: the head of ``charter_versions``, which only
grows), its project topic (``topic``, unused for now), the change ideas its loop proposes
(``changes``), its maintenance (the poll's cache, read by repo and never copied), its numbers
(``stats``, refreshed by the maintenance tick) and its history: ``history`` rows whose ``idea``
is the project's id and whose ``data.kind`` says what happened (``EVENTS``).

The tables are in the loop's ledger (``loop.db``, ``ledger.SCHEMA``). A repo maps to one
project: the id is ``p`` and 8 hex of the repo's name, so creating it twice finds the first.
Nothing here calls a model or GitHub: the list and the page read the ledger and the cache."""

from __future__ import annotations

import hashlib
import json
import time

from ..errors import LoopError
from ..stored import kept
from ..stored import projects as stored_projects

KINDS = ("built", "adopted")  # repo-factory published it; MX adopted it
STATES = ("active", "paused", "archived")
HEALTH = ("ok", "attention", "needs_you", "paused")
JSON_FIELDS = ("repos", "charter", "topic", "settings", "stats")
# each JSON field's contract (``repo_factory.stored.projects``); a charter or topic not set yet is
# None, the rest their empty value
CONTRACTS = {
    "repos": stored_projects.Repos,
    "charter": stored_projects.Charter,
    "topic": stored_projects.ProjectTopic,
    "settings": stored_projects.ProjectSettings,
    "stats": stored_projects.Stats,
}
EVENTS = ("created", "charter", "topic", "evolve", "change", "maintenance", "stats", "state")
# Today's maintenance rows that wait for the owner (maintain.poll.rows, adopted_rows, doctor);
# an AGENTS.md row is advice since FX3 (it holds no fix): attention, never needs-you
NEEDS_YOU_ROWS = (
    "needs_you",
    "factory_pr",
    "pr_ci",
    "confirm_profile",
    "identity_reach",
    "onboarding_failed",
    "pr_blocked",
)
ADVICE_ROWS = ("agents_md",)


def _stored(t, v) -> str:
    """A JSON field's text: the writer's value as it gave it, checked against its contract (a
    value the type cannot read is noted once)."""
    text = json.dumps(v)
    t.parse(text)
    return text


def project_id(repo: str) -> str:
    """``p`` and 8 hex of the repo's name (``owner/name``, or an adopted subdir's key)."""
    return "p" + hashlib.sha256(repo.strip().lower().encode()).hexdigest()[:8]


class Projects:
    """The projects in one loop's ledger."""

    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for k, t in CONTRACTS.items():
            d[k] = kept(t, d.get(k), none=k in ("charter", "topic"))
        return d

    def get(self, pid: str) -> dict | None:
        with self.ledger.db() as c:
            r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return self._row(r) if r else None

    def must(self, pid: str) -> dict:
        p = self.get(pid)
        if p is None:
            raise LoopError(f"no project {pid!r} (see repo-factory projects list)")
        return p

    def all(self, state: str | tuple | None = None) -> list[dict]:
        q, args = "SELECT * FROM projects", ()
        if state:
            states = (state,) if isinstance(state, str) else tuple(state)
            q += f" WHERE state IN ({','.join('?' * len(states))})"
            args = states
        with self.ledger.db() as c:
            return [self._row(r) for r in c.execute(q + " ORDER BY created, id", args)]

    def by_repo(self, repo: str | None) -> dict | None:
        """The project one of whose repos is ``repo`` (``owner/name`` or an adopted key)."""
        if not repo:
            return None
        p = self.get(project_id(repo))
        if p:
            return p
        want = repo.strip().lower()
        return next(
            (
                p
                for p in self.all()
                if any(str(r.get("repo") or "").lower() == want for r in p["repos"])
            ),
            None,
        )

    def by_idea(self, iid: str | None) -> dict | None:
        if not iid:
            return None
        with self.ledger.db() as c:
            r = c.execute(
                "SELECT * FROM projects WHERE idea = ? ORDER BY created LIMIT 1", (iid,)
            ).fetchone()
        return self._row(r) if r else None

    def create(
        self,
        *,
        name: str,
        kind: str,
        repo: dict,
        idea: str | None = None,
        note: str = "created",
        data: dict | None = None,
    ) -> tuple[dict, bool]:
        """``(project, created)``: a repo already in a project returns that project, False."""
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        if not repo.get("repo"):
            raise ValueError("a project needs a repo")
        old = self.by_repo(repo["repo"])
        if old:
            return old, False
        pid, now = project_id(repo["repo"]), time.time()
        with self.ledger.db() as c:
            c.execute(
                "INSERT INTO projects (id, name, kind, idea, repos, state, created, "
                "updated) VALUES (?,?,?,?,?,?,?,?)",
                (
                    pid,
                    name or repo["repo"].split("/")[-1],
                    kind,
                    idea,
                    _stored(stored_projects.Repos, [{"role": "primary", **repo}]),
                    "active",
                    now,
                    now,
                ),
            )
        self.event(pid, "created", note, repo=repo["repo"], idea=idea, **(data or {}))
        return self.must(pid), True

    def update(self, pid: str, **fields) -> dict:
        """Set fields (dicts and lists are stored as JSON)."""
        if "state" in fields and fields["state"] not in STATES:
            raise ValueError(f"state is one of {', '.join(STATES)}")
        sets = {
            k: (_stored(CONTRACTS[k], v) if k in CONTRACTS and v is not None else v)
            for k, v in fields.items()
        }
        sets["updated"] = time.time()
        with self.ledger.db() as c:
            n = c.execute(
                f"UPDATE projects SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                (*sets.values(), pid),
            ).rowcount
        if not n:
            raise KeyError(pid)
        return self.must(pid)

    def event(self, pid: str, kind: str, note: str, **data) -> None:
        """One line of the project's Timeline: a ``history`` row keyed by the project's id."""
        if kind not in EVENTS:
            raise ValueError(f"unknown event kind {kind!r}")
        st = (self.get(pid) or {}).get("state") or "active"
        with self.ledger.db() as c:
            self.ledger._hist(
                c, pid, st, note, {"kind": kind, **{k: v for k, v in data.items() if v is not None}}
            )

    def events(self, pid: str, limit: int | None = 50) -> list[dict]:
        return self.ledger.history(pid, limit=limit)

    def last_event(self, pid: str) -> float | None:
        ev = self.events(pid, limit=1)
        return ev[0]["ts"] if ev else None


# -- birth: from a published idea, from a confirmed adopted repo -------------------------------
def repo_of_idea(idea: dict) -> dict | None:
    """A published idea's repo as a project's ``repos`` entry, else None."""
    pub = idea.get("publish") or {}
    if not pub.get("repo"):
        return None
    vis = pub.get("visibility") or ("public" if idea.get("state") == "public" else "private")
    return {
        "repo": pub["repo"],
        "url": pub.get("url"),
        "visibility": vis,
        "default_branch": pub.get("branch") or "main",
    }


def name_of_idea(idea: dict) -> str:
    return (idea.get("pitch") or {}).get("product") or idea.get("title") or idea["id"]


def create_for_idea(
    projects: Projects, idea: dict, note: str = "created at publish"
) -> tuple[dict | None, bool]:
    """The project of a published idea (``projects.create.on`` is the caller's to read);
    ``(None, False)`` when the idea has no repo."""
    repo = repo_of_idea(idea)
    if repo is None:
        return None, False
    return projects.create(
        name=name_of_idea(idea), kind="built", repo=repo, idea=idea["id"], note=note
    )


def repo_of_profile(prof: dict) -> dict:
    from ..maintain import profile as Pf

    return {
        "repo": Pf.name_of(prof),
        "url": prof.get("url") or f"https://github.com/{prof['repo']}",
        "visibility": prof.get("visibility"),
        "default_branch": prof.get("default_branch") or "main",
        **({"subdir": prof["subdir"]} if prof.get("subdir") else {}),
    }


def create_for_profile(
    projects: Projects, prof: dict, note: str = "created at confirm"
) -> tuple[dict, bool]:
    from ..maintain import profile as Pf

    k = Pf.name_of(prof)
    return projects.create(
        name=k.split("/", 1)[1], kind="adopted", repo=repo_of_profile(prof), note=note
    )


def candidates(loop) -> list[dict]:
    """What should be a project: every idea published (private or public) with a repo, and
    every confirmed adopted repo that is not detached. ``[{"name", "kind", "repo", "idea",
    "profile"}]``."""
    from ..maintain import profile as Pf

    out = []
    for i in loop.ledger.all(("published", "public")):
        repo = repo_of_idea(i)
        if repo:
            out.append(
                {
                    "name": name_of_idea(i),
                    "kind": "built",
                    "repo": repo["repo"],
                    "idea": i,
                    "profile": None,
                }
            )
    for prof in Pf.every(loop.cfg.home):
        if prof.get("confirmed") and prof.get("status") != "detached":
            out.append(
                {
                    "name": Pf.name_of(prof).split("/", 1)[1],
                    "kind": "adopted",
                    "repo": Pf.name_of(prof),
                    "idea": None,
                    "profile": prof,
                }
            )
    return out


def backfill(loop, dry_run: bool = False) -> list[dict]:
    """Make the projects that should exist and do not (PJ-PLAN.md §2.5), idempotently:
    ``[{"id", "name", "kind", "repo", "created"}]`` (``created`` False: it was there already;
    with ``dry_run``: would be created, nothing written)."""
    ps = Projects(loop.ledger)
    out = []
    for c in candidates(loop):
        have = ps.by_repo(c["repo"])
        if have or dry_run:
            out.append(
                {
                    "id": (have or {}).get("id") or project_id(c["repo"]),
                    "name": c["name"],
                    "kind": c["kind"],
                    "repo": c["repo"],
                    "created": not have,
                }
            )
            continue
        if c["idea"]:
            p, made = create_for_idea(ps, c["idea"], note="created (backfill)")
        else:
            p, made = create_for_profile(ps, c["profile"], note="created (backfill)")
        if p is None:
            continue
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "kind": p["kind"],
                "repo": c["repo"],
                "created": made,
            }
        )
    return out


# -- what the list and the page show -----------------------------------------------------------
def cache_recs(home, p: dict, cache: dict | None = None) -> list[tuple[str, dict]]:
    """The poll's cache record of each of the project's repos: ``[(repo, rec)]``."""
    from ..maintain.github import Cache

    d = cache if cache is not None else Cache(home).read()
    return [(r["repo"], dict((d.get("repos") or {}).get(r["repo"]) or {})) for r in p["repos"]]


def maintenance_rows(recs: list[tuple[str, dict]]) -> list[dict]:
    return [{**row, "repo": full} for full, rec in recs for row in rec.get("rows") or []]


def health(
    p: dict,
    recs: list[tuple[str, dict]],
    cfg: dict | None = None,
    asks: int = 0,
    now: float | None = None,
    paused: dict | None = None,
) -> tuple[str, str]:
    """``(word, why)``: one word, never a score (PJ-PLAN.md §2.4). ``needs_you`` when a
    Needs-you row or an ask belongs to the project; ``attention`` when CI is red, a Dependabot
    update waits past ``health.stale_pr_days``, the repo's last poll failed, AGENTS.md has
    advice or a drift finding is open; ``paused`` when the project is paused or archived;
    ``maintenance_paused`` when maintenance is paused on its repo (``paused``: that repo's
    ``maintain.pauses`` line; "Maintenance paused since 29 Oct 10:04, by you"); else ``ok``."""
    now = now or time.time()
    h = ((cfg or {}).get("projects") or {}).get("health") or {}
    if p.get("state") in ("paused", "archived"):
        return "paused", f"the project is {p['state']}"
    if paused:
        from ..maintain.pauses import words

        return "maintenance_paused", words(paused) or "Maintenance paused"
    rows = maintenance_rows(recs)
    mine = [r for r in rows if r.get("kind") in NEEDS_YOU_ROWS]
    if asks or mine:
        bits = ([f"{asks} idea{'s' if asks != 1 else ''} to answer"] if asks else []) + [
            f"{r['kind'].replace('_', ' ')}: {r.get('title') or ''}".strip() for r in mine[:2]
        ]
        return "needs_you", "; ".join(bits)
    stale = float(h.get("stale_pr_days") or 14) * 86400
    for full, rec in recs:
        ci = rec.get("ci") or {}
        if isinstance(ci, dict) and ci.get("state") in ("failure", "red"):
            return "attention", f"CI is red on {full}'s default branch"
        old = [
            r
            for r in rec.get("rows") or []
            if r.get("kind") == "dependabot" and now - float(r.get("created") or now) > stale
        ]
        if old:
            return "attention", f"a dependency update has waited over {int(stale // 86400)} days"
        if rec.get("error"):
            return "attention", f"the last poll of {full} failed: {str(rec['error'])[:120]}"
    advice = [r for r in rows if r.get("kind") in ADVICE_ROWS]
    if advice:
        return "attention", str(advice[0].get("title") or "AGENTS.md")[:160]
    # a low finding (a tag ahead of the changelog) is listed, not a reason for the word
    drift = [
        f
        for f in ((p.get("stats") or {}).get("drift") or {}).get("findings") or []
        if f.get("severity") in ("medium", "high")
    ]
    if drift:
        return (
            "attention",
            f"{len(drift)} drift finding{'s' if len(drift) != 1 else ''}: {drift[0].get('what')}",
        )
    return "ok", "nothing waits for you"


def counts(recs: list[tuple[str, dict]]) -> dict:
    out = {"issues": 0, "prs": 0, "factory_prs": 0, "dependabot": 0}
    for _, rec in recs:
        for k in out:
            out[k] += int((rec.get("counts") or {}).get(k) or 0)
    return out


def version_of(p: dict, idea: dict | None, recs: list[tuple[str, dict]]) -> str | None:
    """The latest release: the poll's, else the publish's first release."""
    for _, rec in recs:
        tag = (rec.get("release") or {}).get("tag")
        if tag:
            return tag
    v = ((idea or {}).get("publish") or {}).get("version")
    return f"v{v}" if v else None


def pitch_line(p: dict, idea: dict | None) -> str | None:
    """The charter's pitch; before a charter is drafted, the idea's one-liner."""
    return (
        (p.get("charter") or {}).get("pitch")
        or ((idea or {}).get("pitch") or {}).get("one_liner")
        or None
    )


def stars_of(p: dict, idea: dict | None) -> int | None:
    s = (p.get("stats") or {}).get("stars")
    if s is not None:
        return s
    return (((idea or {}).get("signals") or {}).get("github") or {}).get("stars")


def row(
    ps: Projects,
    p: dict,
    recs: list[tuple[str, dict]],
    idea: dict | None,
    cfg: dict | None = None,
    asks: int = 0,
    new_ideas: int = 0,
    held: dict | None = None,
) -> dict:
    """One row of Desk › Projects (PJ-PLAN.md §6.2). ``held``: ``maintain.pauses.read``."""
    paused = next(
        (held[r["repo"]] for r in p.get("repos") or [] if held and r.get("repo") in held), None
    )
    word, why = health(p, recs, cfg, asks, paused=paused)
    c = counts(recs)
    ci = next(
        ((rec.get("ci") or {}).get("state") for _, rec in recs if isinstance(rec.get("ci"), dict)),
        None,
    )
    return {
        "id": p["id"],
        "name": p["name"],
        "kind": p["kind"],
        "state": p["state"],
        "health": word,
        "why": why,
        "pitch": pitch_line(p, idea),
        "charter_v": p.get("charter_v") or 0,
        "idea": p.get("idea"),
        "repo": (p["repos"][0] if p["repos"] else {}).get("repo"),
        "url": (p["repos"][0] if p["repos"] else {}).get("url"),
        "version": version_of(p, idea, recs),
        "issues": c["issues"],
        "prs": c["prs"],
        "ci": ci,
        "stars": stars_of(p, idea),
        "new_ideas": new_ideas,
        "asks": asks,
        "maintenance_paused": {"at": paused.get("at"), "by": paused.get("by")} if paused else None,
        "last": ps.last_event(p["id"]) or p["updated"],
        "created": p["created"],
    }


ORDER = {"needs_you": 0, "attention": 1, "ok": 2, "maintenance_paused": 3, "paused": 4}


def listing(loop) -> dict:
    """Desk › Projects: every project not archived, by health then the last event."""
    from ..maintain.github import Cache

    ps = Projects(loop.ledger)
    cache = Cache(loop.cfg.home).read()
    ideas = {i["id"]: i for i in loop.ledger.all(("published", "public"))}
    from .. import rowsafe
    from ..maintain.pauses import read as paused_repos
    from .changes import counts as idea_counts

    held = paused_repos(loop.cfg.home)
    rows: list[dict] = []
    for p in ps.all(("active", "paused")):
        # one project that cannot be read is listed as such (RCA A3)
        with rowsafe.row(rows, "project", p.get("id"), name=p.get("name"), health=None, last=None):
            idea = ideas.get(p.get("idea")) or (
                loop.ledger.get(p["idea"]) if p.get("idea") else None
            )
            n = idea_counts(loop.ledger, p["id"])
            rows.append(
                row(
                    ps,
                    p,
                    cache_recs(loop.cfg.home, p, cache),
                    idea,
                    loop.cfg.data,
                    asks=n["asks"],
                    new_ideas=n["new"],
                    held=held,
                )
            )
    rows.sort(key=lambda r: (ORDER.get(r.get("health") or "", 9), -(r.get("last") or 0)))
    with loop.ledger.db() as c:
        mine = c.execute("SELECT COUNT(*) FROM charter_versions WHERE by = 'owner'").fetchone()[0]
    # the backfill's tally (life.backfill) and how many versions are the owner's: the list says
    # "8 charters drafted by repo-factory" in one line until the owner has edited one
    return {
        "projects": rows,
        "counts": {k: sum(1 for r in rows if r.get("health") == k) for k in ORDER},
        "archived": len(ps.all("archived")),
        "backfill": loop.ledger.meta("projects_backfill"),
        "charters_by_you": mine,
    }


def _preset(cfg: dict, p: dict) -> str:
    from .changes import setting

    return setting(cfg, p, "autonomy.preset", "quiet")


def page(loop, pid: str) -> dict:
    """A project's page (PJ-PLAN.md §6.3): the row, the charter and its versions, the repos,
    the maintenance rows of its repos, the stats, the topic and the timeline."""
    from ..maintain.github import Cache
    from ..maintain.rows import overlay

    ps = Projects(loop.ledger)
    p = ps.get(pid)
    if p is None:
        raise LookupError(f"no project {pid}")
    idea = loop.ledger.get(p["idea"]) if p.get("idea") else None
    recs = cache_recs(loop.cfg.home, p, Cache(loop.cfg.home).read())
    with loop.ledger.db() as c:
        nver = c.execute(
            "SELECT COUNT(*) FROM charter_versions WHERE project = ?", (pid,)
        ).fetchone()[0]
    from ..maintain import current

    repos = []
    for r, (_, rec) in zip(p["repos"], recs):
        pol = rec.get("policy") or {}
        repos.append(
            {
                **r,
                "policy": pol.get("status"),
                "release": (rec.get("release") or {}).get("tag"),
                "counts": rec.get("counts") or {},
                "last_poll": rec.get("last_poll"),
                "ci": rec.get("ci"),
                "error": rec.get("error"),
                # repo-factory's own fix PRs (MQ): the merge policy, the queue, each one's state
                "merge": {
                    "factory_patch": pol.get("factory_patch"),
                    "kinds": pol.get("merge_kinds"),
                    "max_lines": pol.get("max_lines"),
                    "product": p.get("kind") == "built",
                    "queue": rec.get("queue"),
                    "prs": current.summary(rec),
                },
            }
        )
    timeline = [
        {
            "ts": e["ts"],
            "note": e["note"],
            "kind": (e.get("data") or {}).get("kind"),
            "origin": (e.get("data") or {}).get("origin"),
        }
        for e in ps.events(pid, limit=100)
        if e.get("note")
    ]
    from .changes import counts as idea_counts

    n = idea_counts(loop.ledger, pid)
    from ..maintain.pauses import read as paused_repos

    return {
        "project": row(
            ps,
            p,
            recs,
            idea,
            loop.cfg.data,
            asks=n["asks"],
            new_ideas=n["new"],
            held=paused_repos(loop.cfg.home),
        ),
        "charter": p.get("charter"),
        "charter_v": p.get("charter_v") or 0,
        "versions": nver,
        "repos": repos,
        "maintenance": overlay(loop.cfg.home, maintenance_rows(recs)),
        "stats": p.get("stats") or {},
        "topic": p.get("topic"),
        "settings": p.get("settings") or {},
        "preset": _preset(loop.cfg.data, p),
        "idea": {
            "id": idea["id"],
            "title": idea.get("title"),
            "state": idea["state"],
            "task_dir": idea.get("task_dir"),
        }
        if idea
        else None,
        "timeline": timeline,
    }
