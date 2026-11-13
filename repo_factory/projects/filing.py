"""An accepted change idea becomes a change to the existing repo through the machinery that
exists (PJ-PLAN.md §4.7): no new pipeline, and the merge stays the owner's.

- **A product** (repo-factory published it; its policy is ``.github/repo-factory.yml``) and **an adopted repo
  the owner owns with repo-factory's labels**: an issue on the repo, with the kind label, a priority,
  and ``go``. The owner's own Accept or Build it is the go, whatever the kind (AU3 finding 5: an
  accepted feature used to sit there with no way to be built); an idea repo-factory accepted by itself
  gets ``go`` only when the repo's policy lets repo-factory fix that kind (``revise.kinds``). A
  decision ("Decide …") is a ``question`` and never gets ``go``: it is the owner's to answer.
  The maintenance poll's fix (``Fix``, ``RepoFix``) then writes the test (a failing one that
  reproduces a bug, or one that specifies the requested behaviour), makes it pass and opens a
  pull request.
- **An adopted repo repo-factory may not label, or one the owner does not own**: a factory-side request
  (the profile's ``requests``, as *Ask repo-factory to change this repo* keeps them); no issue is
  filed on a stranger's repo unless ``projects.changes.file_issues_outside`` is on.

The idea keeps only what repo-factory decided (``queued`` once accepted) and ``closed`` once its issue
is closed (``follow``). Where it is in between (a fix running, a pull request waiting, a fix that
gave up) is read from the facts when the page asks (``steps.step``: the fix's task folder and
the last poll), never copied here: a copy that only some events moved said "repo-factory is fixing
#3" three days after repo-factory gave up on it (AU3 finding 6).
Build it can be undone for ``build.undo_s`` seconds: the issue is closed again, unworked."""

from __future__ import annotations

import time

from ..errors import LoopError
from . import changes as X

MARKER = "<!-- repo-factory:project-change {cid} -->"
FOLLOW_MAX = 10  # filed ideas whose issue is read per tick


DECIDE = ("decide", "choose")  # a title that asks for a decision, not code
BROKEN = ("renamed", "broken", "integration", "silently")  # a maintenance item that fails now


def kind_label(ch: dict) -> str:
    """The issue's kind label (MAINTAIN.md's scheme) for a change idea. ``bug`` only for what
    someone reported or what is broken now; any other maintenance item names no failing
    behaviour, and the fixer would find none to reproduce (Beliefline #3 and #6, AU3 finding
    7): it is a ``task``, whose test specifies what it asks for."""
    c, title = ch["card"], ch["title"].lower()
    blob = " ".join([title, c.get("what") or "", " ".join(c.get("evidence") or [])]).lower()
    if title.startswith(DECIDE):
        return "question"
    if ch["kind"] == "feature" or ch["class"] in ("minor", "scope"):
        return "feature"
    if "security" in blob or "security.md" in blob:
        return "security"
    if any(w in title for w in ("upgrade", "bump", "pin", "dependency", "dependencies")):
        return "dependency"
    if any(w in title for w in ("readme", "docs", "documentation", "changelog", "typo")):
        return "docs"
    return "bug" if ch["kind"] == "customer_ask" or any(w in title for w in BROKEN) else "task"


def by_owner(ch: dict) -> bool:
    """The owner accepted it (Accept, Build it), not repo-factory's own gate."""
    return (ch["card"].get("accepted") or {}).get("by") not in (None, "repo-factory")


def priority(cfg: dict, p: dict, ch: dict, label: str) -> str:
    pri = X.setting(cfg, p, "changes.priority", {}) or {}
    broken = any(w in ch["title"].lower() for w in BROKEN)
    if label == "security":
        return pri.get("security", "p2")
    if broken:
        return pri.get("broken_integration", "p2")
    return pri.get("default", "p3")


def body(p: dict, ch: dict) -> str:
    c = ch["card"]
    ev = "\n".join(f"- {e}" for e in c.get("evidence") or [])
    test = c.get("test") or "a test that fails before the change and passes after it"
    return (
        f"{c.get('what') or ch['title']}\n\n**Why now.** {c.get('why_now') or ''}\n\n"
        f"**Evidence**\n{ev}\n\n**The test to write.** {test}\n\n"
        f"_Proposed by repo-factory from this project's sources ({ch['kind'].replace('_', ' ')}, "
        f"{ch['class']}); charter v{c.get('charter_v') or p.get('charter_v') or 0}._\n\n"
        f"{MARKER.format(cid=ch['id'])}\n"
    )


def where(loop, p: dict) -> dict:
    """``{"how": "issue"|"request", "repo", "owned", "policy" | "profile"}`` for the
    project's primary repo."""
    repo = p["repos"][0]["repo"]
    if p["kind"] != "adopted":
        return {"how": "issue", "repo": repo, "owned": True}
    from ..maintain import profile as Pf

    prof = Pf.load(loop.cfg.home, repo) or {}
    owned = bool((prof.get("access") or {}).get("owned"))
    label = (prof.get("labels") or {}).get("scheme") == "repo-factory"
    outside = bool(X.setting(loop.cfg.data, p, "changes.file_issues_outside", False))
    how = "issue" if label and (owned or outside) else "request"
    return {"how": how, "repo": repo, "owned": owned, "profile": prof}


def _gh(loop, repo: str | None = None):
    """Maintenance's identity, narrowed to filing and reading issues on ``repo`` (repo-factory's own
    app: a token with issues write and metadata read on that one repository)."""
    from ..maintain.poll import gh_for

    return gh_for(loop, repo, "issues")


def go_allowed(
    loop, p: dict, w: dict, label: str, gh=None, owner: bool = False
) -> tuple[bool, str | None]:
    """Whether ``go`` goes on a ``label`` issue: ``(ok, why not)``. Never on a decision; the
    repo's policy must let repo-factory fix issues at all; its ``revise.kinds`` bounds only what repo-factory
    files by itself, never the owner's press (``owner``)."""
    if label == "question":
        return False, "a decision is the owner's to make: repo-factory writes no code for it"
    from ..maintain import policy as P
    from ..maintain.github import GitHubError

    try:
        pol = P.load(gh or _gh(loop), w["repo"])
    except (GitHubError, P.PolicyError) as e:
        return False, f"its policy could not be read ({e})"
    rv = pol.get("revise") or {}
    if pol.get("status") != "maintained" or rv.get("trigger") == "off":
        return (
            False,
            f"its policy does not let repo-factory fix issues (status {pol.get('status')})",
        )
    if not owner and label not in (rv.get("kinds") or []):
        return (
            False,
            f"its policy lets repo-factory fix {', '.join(rv.get('kinds') or []) or 'nothing'}"
            f", not {label} (.github/repo-factory.yml › revise.kinds)",
        )
    return True, None


def file(loop, ch: dict, gh=None) -> dict:
    """File a ``queued`` change idea (see the module's doc); a GitHub error leaves it queued
    with why, and the tick files it again."""
    from ..maintain.github import GitHubError, Repo
    from .record import Projects

    if ch["state"] != "queued" or ch.get("filed"):
        return ch
    ps = Projects(loop.ledger)
    p = ps.must(ch["project"])
    w = where(loop, p)
    now = time.time()
    if w["how"] == "request":
        rid = add_request(loop.cfg.home, w["repo"], f"{ch['title']}\n\n{body(p, ch)}")
        filed = {
            "how": "request",
            "repo": w["repo"],
            "id": rid,
            "go": True,
            "at": now,
            "where": f"as request {rid} in repo-factory for {w['repo']}",
        }
    else:
        gh = gh or _gh(loop, w["repo"])
        label = kind_label(ch)
        labels = [label, priority(loop.cfg.data, p, ch, label)]
        ok, held = go_allowed(loop, p, w, label, gh, owner=by_owner(ch))
        if ok:
            labels.append("go")
        try:
            r = Repo(gh, w["repo"]).create_issue(ch["title"], body(p, ch), labels)
        except GitHubError as e:
            card = {**ch["card"], "file_error": {"at": now, "error": str(e)[:300]}}
            return X.update(loop.ledger, ch["id"], card=card)
        filed = {
            "how": "issue",
            "repo": w["repo"],
            "number": r.get("number"),
            "url": r.get("url"),
            "go": ok,
            "go_held": held,
            "labels": labels,
            "at": now,
            "where": f"as #{r.get('number')} on {w['repo']}",
        }
    card = {k: v for k, v in ch["card"].items() if k != "file_error"}
    undo_s = float(((loop.cfg.get("build") or {}).get("undo_s")) or 10)
    if by_owner(ch):
        card["undo"] = {"until": now + undo_s}
    ch = X.update(loop.ledger, ch["id"], filed=filed, card=card)
    ps.event(
        p["id"],
        "change",
        f"filed {filed['where']}"
        + (" with go" if filed.get("how") == "issue" and filed.get("go") else "")
        + (f"; go held back: {filed['go_held']}" if filed.get("go_held") else "")
        + f": {ch['title']}",
        change=ch["id"],
        url=filed.get("url"),
    )
    return ch


def add_request(home, k: str, text: str, by: str = "repo-factory") -> str:
    """A factory-side request on an adopted repo (``r<n>``), as *Ask repo-factory to change this
    repo…* keeps it; the fix treats it like an issue's report."""
    from ..maintain import profile as Pf

    out: dict = {}

    def fn(x):
        n = 1 + max([int(str(r.get("id", "r0"))[1:] or 0) for r in x.get("requests") or []] or [0])
        out["id"] = f"r{n}"
        x["requests"] = [
            *(x.get("requests") or []),
            {"id": f"r{n}", "text": text[:4000], "at": time.time(), "by": by, "state": "open"},
        ]

    Pf.update(home, k, fn)
    return out["id"]


def undo(loop, cid: str, gh=None, origin: str = "ui") -> dict:
    """Undo a Build it within its window: the issue is closed unworked (or the request
    withdrawn) and the idea is new again."""
    from ..maintain.github import GitHubError, Repo
    from .record import Projects

    ch = X.must(loop.ledger, cid)
    u = ch["card"].get("undo") or {}
    if (not ch.get("filed") and ch["state"] != "queued") or time.time() > float(
        u.get("until") or 0
    ):
        raise LoopError("too late to undo: it was more than a few seconds ago")
    f = ch.get("filed") or {"how": None}  # not filed yet: nothing to close
    if f["how"] == "issue":
        try:
            Repo(gh or _gh(loop, f["repo"]), f["repo"]).close(int(f["number"]), "not_planned")
        except GitHubError as e:
            raise LoopError(f"the issue could not be closed: {e}") from None
    elif f["how"] == "request":
        from ..maintain import profile as Pf

        def fn(x):
            x["requests"] = [r for r in x.get("requests") or [] if r.get("id") != f["id"]]

        Pf.update(loop.cfg.home, f["repo"], fn)
    card = {k: v for k, v in ch["card"].items() if k not in ("undo", "accepted", "file_error")}
    if (card.get("gate") or {}).get("verdict") == "ask":  # it asked you: it asks again, as it was
        card["ask"] = {"since": time.time()}
    ch = X.update(loop.ledger, cid, state="new", filed=None, card=card)
    Projects(loop.ledger).event(
        ch["project"], "change", f"undone ({origin}): {ch['title']}", change=cid
    )
    return ch


def follow(loop, p: dict, gh=None, now: float | None = None) -> list[str]:
    """Files the queued ideas not filed yet, and closes those whose issue is closed. Returns
    what changed, in words."""
    from ..maintain.github import GitHubError, Repo
    from .record import Projects

    out = []
    ps = Projects(loop.ledger)
    for ch in X.all_of(loop.ledger, p["id"], ("queued",)):
        if not ch.get("filed"):
            got = file(loop, ch, gh)
            if got.get("filed"):
                out.append(f"filed {got['filed']['where']}")
    # "building" and "held" are older rows' states: they are read as queued
    live = [
        ch
        for ch in X.all_of(loop.ledger, p["id"], ("queued", "building", "held"))
        if (ch.get("filed") or {}).get("how") == "issue"
    ][:FOLLOW_MAX]
    for ch in live:
        f = ch["filed"]
        try:
            it = Repo(gh or _gh(loop, f["repo"]), f["repo"]).issue(int(f["number"]))
        except GitHubError:
            continue
        if it.get("state") != "closed":
            continue
        card = {
            **ch["card"],
            "closed": {"at": now or time.time(), "reason": it.get("state_reason")},
        }
        X.update(loop.ledger, ch["id"], state="closed", card=card)
        word = f"#{f['number']} is closed"
        ps.event(p["id"], "change", f"{word}: {ch['title']}", change=ch["id"], url=f.get("url"))
        out.append(word)
    return out
