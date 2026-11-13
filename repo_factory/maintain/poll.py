"""The maintenance step of the loop's tick: ``maintain_tick(loop)`` (off unless
``maintain.enabled``).

Every ``maintain.poll_s`` seconds the daemon polls each maintained repository: the loop's
published builds (state ``published`` or ``public``).
Per repo, with one read of its open issues and pull requests:

1. its policy, ``.github/repo-factory.yml`` (a missing file is ``as-is``; a broken one is reported in one
   comment on the newest issue and the repo is left alone; ``archived`` gets one comment, then
   nothing);
2. **triage**: every open issue labelled ``triage``, and every ``needs-info`` issue whose reporter
   answered, gets a kind, a priority and one comment (``triage.py``), at most
   ``maintain.triage_max_per_poll`` model calls a poll across all repos;
3. a stranger's pull request is triaged on its title and body only (its code is never fetched);
4. a **Dependabot** pull request is labelled ``dependency`` and, only when ``merge.dependabot``
   allows its level and its CI is green, merged (squash);
5. **repo-factory's own pull requests** (products): with ``merge.factory_patch: ci-green``, the oldest
   one that is ready is merged, at most one a poll (``automerge.py``); then every other one is
   kept mergeable whatever the policy: brought up to date with ``main``, or re-done on it when it
   conflicts (``current.py``);
6. **release** (``release.py``): when ``release.on_merge`` is on and ``main`` moved past the
   newest version tag, a tag and a GitHub release (and the ``lesson`` label on a fixed
   user-reported bug); it waits while the merge queue still has fixes coming, so a batch makes
   one release;
7. **revise** (``revise.py``, ``maintained`` repos only): a fix that ended is forgotten; when a
   slot is free (``free_slots``) the head of the ``go`` queue is claimed and its fix started,
   detached;
8. the **drift doctor** (``doctor.py``): AGENTS.md against the tree and the README, and no
   workflow on a self-hosted runner, whenever main moved (``read_head``: one conditional read a
   poll, a free 304 when nothing moved), and at least every
   ``maintain.doctor_days``;
9. what Today shows (issues with ``needs-you``, repo-factory's pull requests waiting for review,
   Dependabot updates waiting, issues still untriaged after a day, AGENTS.md drift and a
   self-hosted runner) and the counts the UI reads are written to the disposable cache
   (``github.Cache``).

Nothing here runs while the loop is paused or over ``daily_usd`` (the daemon checks before it
calls)."""

from __future__ import annotations

import re
import time

from . import automerge, current, doctor, release, revise, triage
from . import labels as L
from . import policy as P
from .github import BOTS, WRITERS, Cache, GitHubError, Repo

ARCHIVED = "<!-- repo-factory:archived v1 -->"
POLICY_ERROR = "<!-- repo-factory:policy v1 -->"
MERGED = "<!-- repo-factory:merge v1 -->"
STALE_TRIAGE_S = 24 * 3600
LEVELS = {"never": 0, "patch": 1, "minor": 2}


def settings(cfg) -> dict:
    return cfg.get("maintain") or {}


def gh_for(loop, repo: str | None = None, scope: str = "maintain"):
    """Maintenance's ``gh`` (``identity.resolve``): repo-factory's own GitHub App when
    it is installed (a token for ``scope`` on ``repo`` alone), else ``maintain.token_env_file``'s
    token, else the loop's own ``gh``: the owner's login."""
    from .identity import gh_for as _gh_for

    return _gh_for(loop, repo, scope)


def targets(loop, polling: bool = False) -> list[dict]:
    """The repos maintenance looks at: each published build's repo (its policy in its
    ``.github/repo-factory.yml``; with ``polling``, not the paused ones)."""
    from . import pauses

    out, seen = [], set()
    # maintenance paused on a project (pauses.py): skipped by the poll; every other reader
    # sees the pause on the target
    held = pauses.read(loop.cfg.home)
    for i in loop.ledger.all(("published", "public")):
        full = (i.get("publish") or {}).get("repo")
        if full and full not in seen:
            seen.add(full)
            if polling and full in held:
                continue
            p = i.get("pitch") or {}
            out.append(
                {
                    "repo": full,
                    "idea": i["id"],
                    "product": p.get("product") or i["title"],
                    "one_liner": p.get("one_liner") or "",
                    "kind": "product",
                    "paused": held.get(full),
                }
            )
    return out


def bump_level(title: str) -> str | None:
    """A Dependabot title's level: ``patch``, ``minor``, ``major``, or None when it does not
    say. A pinned Actions SHA bump (``from 3d3c42e to 5fda3b9``) is ``patch``."""
    m = re.search(r"\bfrom v?([\w.+-]+) to v?([\w.+-]+)", title or "")
    if not m:
        return None
    if all(
        re.fullmatch(r"[0-9a-f]{7,40}", x) and not re.fullmatch(r"[\d.]+", x) for x in m.groups()
    ):
        return "patch"  # a pinned SHA moves to the next one
    nums = [re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", x) for x in m.groups()]
    if not all(nums):
        return None
    a, b = [[int(g or 0) for g in n.groups()] for n in nums]
    return "major" if a[0] != b[0] else "minor" if a[1] != b[1] else "patch"


def counts(items: list[dict]) -> dict:
    issues = [i for i in items if not i["is_pr"]]
    prs = [i for i in items if i["is_pr"]]
    by = {
        k: sum(1 for i in issues if k in i["labels"])
        for k in ("triage", "needs-info", "go", "needs-you", "claim:repo-factory", "claim:agent")
    }
    by["claimed"] = sum(
        1 for i in issues if i["assignees"] or any(x.startswith("claim:") for x in i["labels"])
    )
    return {
        "issues": len(issues),
        "prs": len(prs),
        **by,
        "factory_prs": sum(1 for p in prs if "repo-factory" in p["labels"]),
        "dependabot": sum(1 for p in prs if p["author"] in BOTS),
    }


def rows(items: list[dict], now: float, waiting_bots: list[dict]) -> list[dict]:
    """Today's rows for one repo, from GitHub's own labels."""
    out = []
    pick = ("number", "title", "url", "updated", "created")
    for i in items:
        if i["is_pr"]:
            if "repo-factory" in i["labels"] and not i["draft"]:
                out.append({"kind": "factory_pr", **{k: i[k] for k in pick}})
        elif "needs-you" in i["labels"]:
            out.append({"kind": "needs_you", **{k: i[k] for k in pick}})
        elif "triage" in i["labels"] and i["created"] and now - i["created"] > STALE_TRIAGE_S:
            out.append({"kind": "untriaged", **{k: i[k] for k in pick}})
    out += [{"kind": "dependabot", **{k: p[k] for k in pick}} for p in waiting_bots]
    return out


class Budget:
    def __init__(self, n: int):
        self.left = int(n)
        self.capped = 0


def _newest_issue(items: list[dict]) -> dict | None:
    issues = [i for i in items if not i["is_pr"]]
    return max(issues, key=lambda i: i["number"]) if issues else None


def poll_repo(
    loop,
    gh,
    t: dict,
    cache: Cache,
    budget: Budget,
    model,
    now: float,
    slots: list[int] | None = None,
) -> dict:
    full = t["repo"]
    rec = cache.repo(full)
    out: dict = {"repo": full, "triaged": [], "merged": [], "errors": []}
    r = Repo(gh, full)
    try:
        items = r.issues(state="open")
    except GitHubError as e:
        rec.update(error=str(e), last_try=now)
        cache.write_repo(full, rec)
        out["errors"].append(str(e))
        return out
    try:
        pol = P.load(gh, full)
    except P.PolicyError as e:
        msg = str(e)
        rec.update(
            error=msg, last_try=now, last_poll=now, policy=None, counts=counts(items), rows=[]
        )
        newest = _newest_issue(items)
        if newest and not msg.startswith("could not read"):
            body = (
                f"{POLICY_ERROR}\nbears could not read `.github/repo-factory.yml`, so it leaves this "
                f"repository alone until it is fixed: {triage.sanitize(msg, 600)}\n\n"
                f"_{triage.SIGNATURE}_\n"
            )
            try:
                r.comment_with_marker(newest["number"], POLICY_ERROR, body)
            except GitHubError as ce:
                out["errors"].append(str(ce))
        cache.write_repo(full, rec)
        out["errors"].append(msg)
        return out
    summary = summary_of(pol)
    rec.update(policy=summary, error=None, last_try=now, idea=t["idea"], product=t["product"])
    if pol["status"] == "archived":
        if not rec.get("archived_notice"):
            newest = _newest_issue(items)
            if newest:
                r.comment_with_marker(
                    newest["number"],
                    ARCHIVED,
                    (
                        f"{ARCHIVED}\nThis repository is archived (`.github/repo-factory.yml` › `status: "
                        "archived`): repo-factory no longer triages or fixes anything here.\n\n"
                        f"_{triage.SIGNATURE}_\n"
                    ),
                    edit=False,
                )
            rec["archived_notice"] = now
        rec.update(last_poll=now, counts=counts(items), rows=[])
        cache.write_repo(full, rec)
        out["archived"] = True
        return out
    read_head(r, rec, "main", now, out)
    return _work(loop, r, t, items, pol, rec, out, cache, budget, model, now, slots)


def read_head(
    r: Repo, rec: dict, branch: str, now: float, out: dict | None = None, fresh: bool = False
) -> str | None:
    """Main's commit now, into ``rec["head"]`` (``{"sha", "etag", "branch", "at", "moved"}``):
    one conditional read a poll, a 304 when nothing moved (no rate limit spent). What is
    derived from the repo (the doctor here, a project's drift in ``projects.life``) runs again
    when it differs from the commit it was computed at. ``fresh``: ask without the ETag (a
    Re-check). A failed read keeps the last answer; the doctor then falls back to its days."""
    h = dict(rec.get("head") or {})
    same = h.get("branch") == branch and h.get("sha")
    try:
        got = r.head(branch, None if fresh or not same else h.get("etag"))
    except GitHubError as e:
        if out is not None:
            out["errors"].append(f"head: {e}")
        return h.get("sha") if same else None
    if got["changed"]:
        if got["sha"] != h.get("sha"):
            h["moved"] = now
        h.update(sha=got["sha"], etag=got["etag"], branch=branch)
    h["at"] = now
    rec["head"] = h
    return h.get("sha")


def _work(
    loop,
    r,
    t: dict,
    items: list[dict],
    pol: dict,
    rec: dict,
    out: dict,
    cache: Cache,
    budget: Budget,
    model,
    now: float,
    slots: list[int] | None,
) -> dict:
    """Triage, Dependabot, release, revise and the doctor for one repo whose policy is read
    (its ``.github/repo-factory.yml``)."""
    full = t.get("key") or t["repo"]
    opened = [i for i in items if not i["is_pr"]]
    doctor_was = (rec.get("doctor") or {}).get("at") or 0
    readme: list[str] = []

    def read_me() -> str:
        if not readme:
            readme.append(r.readme())
        return readme[0]

    writers = None

    def classify(item, comments=None):
        nonlocal writers
        if budget.left <= 0:
            budget.capped += 1
            return None
        budget.left -= 1
        from ..loop.models import spend_kw, split

        res, usd, tokens = split(
            triage.classify(
                item,
                model,
                product=t["product"],
                one_liner=t["one_liner"],
                open_issues=opened,
                readme=read_me(),
                comments=comments,
            )
        )
        loop.ledger.spend("triage", usd, t["idea"], **spend_kw(tokens))
        out.setdefault("usd", 0.0)
        out["usd"] = round(out["usd"] + usd, 6)
        if res is None:
            out["errors"].append(
                f"#{item['number']}: the triage reply was unusable twice; it stays in triage"
            )
            return None
        if pol["revise"]["auto_go"] == "patch" and writers is None:
            writers = r.collaborators()
        return res

    def done(item, res, comments=None):
        got = triage.apply(
            r,
            item,
            res,
            pol,
            writers=writers,
            comments=comments,
            owner=t.get("login"),
            bot=t.get("bot"),
        )
        item["labels"] = [x for x in item["labels"] if x not in got["labels"]["removed"]] + got[
            "labels"
        ]["added"]
        out["triaged"].append(got)

    if pol["triage"]["enabled"]:
        for item in sorted(opened, key=lambda i: i["number"]):
            try:
                if "triage" in item["labels"]:
                    res = classify(item)
                    if res:
                        done(item, res)
                elif "needs-info" in item["labels"]:
                    comments = r.comments(item["number"])
                    mine = r.find_marker(item["number"], triage.MARKER, comments)
                    since = (mine or {}).get("updated") or 0
                    answers = [
                        c
                        for c in comments
                        if c["author"] == item["author"]
                        and "<!-- repo-factory:" not in c["body"]
                        and (c["created"] or 0) > since
                    ]
                    if mine and answers:
                        res = classify(item, comments=answers)
                        if res:
                            done(item, res, comments)
            except GitHubError as e:
                out["errors"].append(f"#{item['number']}: {e}")
        for pr in [i for i in items if i["is_pr"]]:
            if (
                pr["author"] in BOTS
                or "repo-factory" in pr["labels"]
                or pr["association"] in WRITERS
                or (t.get("bot") and pr["author"] == t["bot"])
                or any(x in L.KINDS for x in pr["labels"])
            ):
                continue
            try:
                res = classify(pr)  # title and body only; never its diff
                if res:
                    done(pr, res)
            except GitHubError as e:
                out["errors"].append(f"PR #{pr['number']}: {e}")

    waiting = []
    for pr in [i for i in items if i["is_pr"] and i["author"] in BOTS]:
        try:
            if "dependency" not in pr["labels"]:
                r.set_labels(pr["number"], ["dependency"], have=pr["labels"])
                pr["labels"].append("dependency")
            level = bump_level(pr["title"])
            allowed = LEVELS.get(pol["merge"]["dependabot"], 0)
            if allowed and level in LEVELS and LEVELS[level] <= allowed:
                head = r.get(f"repos/{full}/pulls/{pr['number']}") or {}
                sha = (head.get("head") or {}).get("sha")
                ci = r.ci_status(sha) if sha else {"state": "none"}
                if ci["state"] == "success" and head.get("mergeable") is not False:
                    r.merge(pr["number"])
                    r.comment_with_marker(
                        pr["number"],
                        MERGED,
                        (
                            f"{MERGED}\nMerged by repo-factory: a {level} update with CI green, as "
                            f"`.github/repo-factory.yml` › `merge.dependabot: {pol['merge']['dependabot']}` "
                            f"allows.\n\n_{triage.SIGNATURE}_\n"
                        ),
                        edit=False,
                    )
                    out["merged"].append(pr["number"])
                    pr["state"] = "merged"
                    continue
            waiting.append(pr)
        except GitHubError as e:
            out["errors"].append(f"PR #{pr['number']}: {e}")
            waiting.append(pr)

    queue: dict = {}
    if pol["status"] in ("maintained", "as-is"):
        try:
            queue = automerge.step(r, items, pol, rec, now, may_merge=not out["merged"])
        except GitHubError as e:
            out["errors"].append(f"merge queue: {e}")
        if queue.get("merged"):
            out["merged"].append(queue["merged"])
            for i in items:
                if i["number"] == queue["merged"]:
                    i["state"] = "merged"
        try:
            kept = current.step(
                loop,
                r,
                t,
                items,
                pol,
                rec,
                now,
                slots if slots is not None else [0],
                moved=bool(out["merged"]),
                start=lambda issue, pr: revise.start_refresh(loop, r, t, issue, pr),
            )
            if any(kept.get(k) for k in ("updated", "refreshing", "ended", "needs_you", "left")):
                out["current"] = kept
            out["errors"] += kept.get("errors") or []
        except (GitHubError, OSError) as e:
            out["errors"].append(f"keeping pull requests current: {e}")
        rec["queue"] = queue
    left = [i for i in items if i.get("state") != "merged"]
    if pol["release"]["on_merge"] != "off" and automerge.release_waits(queue, rec, now):
        out["release"] = {
            "waits": f"{len(automerge.waiting(queue))} fix(es) still in the "
            "merge queue: one release once it is empty"
        }
    elif pol["release"]["on_merge"] != "off":
        try:
            rel = release.step(r, pol, lessons=bool(settings(loop.cfg).get("lesson_label", True)))
            if rel:
                out["release"] = rel
                if rel.get("tag") and rel.get("url"):
                    rec["release"] = {"tag": rel["tag"], "url": rel["url"], "at": now}
        except GitHubError as e:
            out["errors"].append(f"release: {e}")
    if pol["status"] == "maintained" and slots is not None:
        try:
            out["revise"] = revise.step(loop, r, t, left, pol, rec, now, slots)
        except (GitHubError, revise.ReviseError, OSError) as e:
            out["errors"].append(f"revise: {e}")
    # AGENTS.md drift: repo-factory's own files
    disk = cache.repo(full).get("doctor") or {}  # a Re-check that ended meanwhile
    if (disk.get("at") or 0) > doctor_was:
        rec["doctor"] = disk
    try:
        if doctor.step(
            r,
            rec,
            now,
            float(settings(loop.cfg).get("doctor_days", 7) or 0),
            head=(rec.get("head") or {}).get("sha"),
        ):
            out["doctor"] = rec["doctor"]["problems"]
    except (GitHubError, OSError) as e:
        out["errors"].append(f"doctor: {e}")
    extra = doctor.rows(full, rec)
    held = revise.agents_rows(full, rec) if pol["status"] == "maintained" else []
    if held:  # one row says it, with its remedy (the drift is part of it)
        extra = held + [x for x in extra if x["kind"] != "drift"]
    base = [annotate(x, rec) for x in rows(left, now, waiting)]
    rec.update(
        last_poll=now,
        counts=counts(left),
        rows=base + extra,
        triaged=len(out["triaged"]),
        capped=budget.capped,
        errors=out["errors"][-5:] or None,
    )
    cache.write_repo(full, rec)
    return out


def annotate(row: dict, rec: dict) -> dict:
    """A repo-factory pull request's row, with what keeping it current and the merge queue say."""
    if row["kind"] != "factory_pr":
        return row
    b = (rec.get("prs") or {}).get(str(row["number"])) or {}
    q = next(
        (x for x in (rec.get("queue") or {}).get("items") or [] if x["number"] == row["number"]),
        None,
    )
    return {
        **row,
        **({"current": b.get("state"), "current_why": b.get("why")} if b else {}),
        **({"merge": q["verdict"], "merge_why": q.get("why")} if q else {}),
    }


def summary_of(pol: dict) -> dict:
    return {
        "status": pol["status"],
        "trigger": pol["revise"]["trigger"],
        "auto_go": pol["revise"]["auto_go"],
        "factory_patch": pol["merge"]["factory_patch"],
        "merge_kinds": list(pol["merge"].get("kinds") or []),
        "max_lines": pol["merge"].get("max_lines"),
        "dependabot": pol["merge"]["dependabot"],
        "on_merge": pol["release"]["on_merge"],
        "triage": pol["triage"]["enabled"],
        "note": pol["spend"]["note"],
    }


def free_slots(loop, cache: Cache) -> int:
    """How many fixes may start now: ``maintain.concurrency`` less the fixes running, and
    never more than the build slots left (a fix shares ``concurrency`` with builds)."""
    n = len(revise.running(cache))
    mine = int(settings(loop.cfg).get("concurrency", 1) or 0) - n
    shared = int(loop.cfg["concurrency"]) - len(loop.building()) - n
    return max(0, min(mine, shared))


def maintain_tick(loop, now: float | None = None, model=None) -> dict:
    """One poll of every maintained repo. Never raises for one repo's failure: that repo's
    error is in the result and the cache, and the next repo goes on."""
    now = now or time.time()
    s = settings(loop.cfg)
    out = {"repos": [], "at": now}
    if not s.get("enabled", False):
        out["disabled"] = True
        return out
    from .identity import login_of

    cache = Cache(loop.cfg.home)
    model = model or loop.triage_model
    budget = Budget(int(s.get("triage_max_per_poll") or 20))
    slots = [free_slots(loop, cache)]
    logins: dict = {}

    def login(g):
        k = id(g)
        if k not in logins:
            logins[k] = login_of(g)
        return logins[k]

    for t in targets(loop, polling=True):
        try:
            t["login"] = login(loop.gh) or t["repo"].split("/")[0]
            # one identity per repo: the app's token reaches that repository alone
            gh = gh_for(loop, t["repo"])
            t["bot"] = gh.login_name if getattr(gh, "credential", None) == "app" else None
            out["repos"].append(poll_repo(loop, gh, t, cache, budget, model, now, slots))
        except Exception as e:  # noqa: BLE001 - the next repo still runs
            out["repos"].append(
                {"repo": t.get("key") or t["repo"], "errors": [f"{type(e).__name__}: {e}"]}
            )
    loop.ledger.meta("maintain_last_poll", now)
    return out


def today(home) -> dict:
    """Today's maintenance rows from the cache: never a GitHub call, so the page stays fast
    whatever GitHub does. ``{"rows": [...], "polled": ts}``."""
    from .rows import overlay

    d = Cache(home).read()
    out, polled = [], None
    for full, rec in sorted(d["repos"].items()):
        for r in overlay(home, [{**x, "repo": full} for x in rec.get("rows") or []]):
            out.append(
                {**r, "product": rec.get("product") or full.split("/")[1], "idea": rec.get("idea")}
            )
        if rec.get("capped"):
            out.append(
                {
                    "kind": "capped",
                    "repo": full,
                    "number": None,
                    "product": rec.get("product") or full.split("/")[1],
                    "title": f"{rec['capped']} issue(s) wait for triage",
                    "url": f"https://github.com/{full}/issues?q=is%3Aopen+label%3Atriage",
                    "idea": rec.get("idea"),
                }
            )
        polled = max(polled or 0, rec.get("last_poll") or 0) or polled
    return {"rows": out, "polled": polled}
