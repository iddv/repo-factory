"""The maintenance poll: what the loop's tick does every ``maintain.poll_s`` for each
repository you switched maintenance on for (``switch.py``), and nothing else.

Nothing here runs while the loop is paused (the daemon checks first), and a repository that is
paused (``pauses.py``) or switched off is not read at all. Per repository, with one read of its
open issues and pull requests:

1. **Its policy** (``.github/repo-factory.yml``). Fixes start only once the setup pull request
   (``setup.py``: labels, the policy file, AGENTS.md and CI) is merged and the file says
   ``status: maintained``. A file that cannot be read is a row on the page, never a comment.
2. **Your word**: an open issue is fixed only when its ``go`` label was put on by you, the login
   maintenance acts for, read from the issue's events. Anyone else's issue, a ``go`` someone
   else put on, and anyone else's pull request are "Waiting for you" rows: no model call, no
   label, no comment, nothing else.
3. **Fixes** (``revise.Fix``, quiet): when a slot is free, nothing of this repo is running,
   maintenance's money is not spent (``spend.py``) and the issue does not wait for you
   (``fixes.waits``), the oldest of your ``go`` issues without an open fix gets one, detached.
   A fix ends as a pull request or as a line on the project's page; never a comment.
4. **Merging** (``merge_step``), only with the repo's second switch on (off by default): one of
   repo-factory's own fix pull requests a poll, bugs and docs only, green CI, nothing under
   ``.github/``, only while its head is the commit the fix pushed and no workflow on ``main``
   names a self-hosted runner; then a patch release when the policy file allows it.
5. **The doctor** (reads only): AGENTS.md against the tree, and any workflow on a self-hosted
   runner, which the page shows as a warning (and which stops merging).

What the page shows is written to the poll's disposable cache (``github.Cache``)."""

from __future__ import annotations

import time

from . import automerge, doctor, fixes, pauses, release, revise, spend, switch
from . import policy as P
from .github import BOTS, Cache, GitHubError, Repo
from .poll import free_slots, gh_for, read_head, summary_of
from .poll import targets as published

SETUP_BRANCH = "repo-factory/maintenance"
MAX_GO_READS = 30  # issues whose events one poll reads (the rest: the next poll)
MERGE_KINDS = ("bug", "docs")  # what merging its own fixes may ever take


def targets(loop) -> list[dict]:
    """The published repositories maintenance is switched on for and not paused."""
    home = loop.cfg.home
    on, held = set(switch.on_repos(home)), pauses.read(home)
    return [t for t in published(loop) if t["repo"] in on and t["repo"] not in held]


def due(loop, now: float | None = None) -> bool:
    if not targets(loop):
        return False
    last = loop.ledger.meta("maintain_last_poll") or 0
    every = float((loop.cfg.get("maintain") or {}).get("poll_s") or 900)
    return (now or time.time()) - last >= every


def tick(loop, now: float | None = None) -> dict:
    """One poll of every repository switched on. One repo's failure is in its result and the
    cache, and the next repo goes on."""
    from .identity import login_of

    now = now or time.time()
    out: dict = {"repos": [], "at": now}
    ts = targets(loop)
    if not ts:
        out["off"] = True
        # nothing polled: a repo switched on next is polled at the next tick, not poll_s later
        return out
    cache = Cache(loop.cfg.home)
    stop = spend.reached(loop, now)
    out["spend"] = stop
    slots = [0 if stop else free_slots(loop, cache)]
    me = login_of(loop.gh)
    for t in ts:
        try:
            gh = gh_for(loop, t["repo"])
            t["login"] = me
            t["bot"] = gh.login_name if getattr(gh, "credential", None) == "app" else None
            out["repos"].append(poll_repo(loop, gh, t, cache, now, slots, stop))
        except Exception as e:  # noqa: BLE001 - the next repo still runs
            out["repos"].append({"repo": t["repo"], "errors": [f"{type(e).__name__}: {e}"]})
    loop.ledger.meta("maintain_last_poll", now)
    return out


def row(kind: str, item: dict | None, title: str, why: str, **more) -> dict:
    i = item or {}
    return {
        "kind": kind,
        "number": i.get("number"),
        "title": title,
        "why": why,
        "url": i.get("url") or more.pop("url", None),
        "author": i.get("author"),
        "updated": i.get("updated"),
        "created": i.get("created"),
        "comments": i.get("comments"),
        **more,
    }


def poll_repo(loop, gh, t: dict, cache: Cache, now: float, slots: list[int], stop=None) -> dict:
    full, home = t["repo"], loop.cfg.home
    rec = cache.repo(full)
    out: dict = {"repo": full, "waiting": 0, "started": [], "ended": [], "merged": [], "errors": []}
    r = Repo(gh, full)
    try:
        items = r.issues(state="open")
    except GitHubError as e:
        rec.update(error=str(e), last_try=now)
        cache.write_repo(full, rec)
        out["errors"].append(str(e))
        return out
    rows: list[dict] = []
    pol = None
    try:
        pol = P.load(gh, full)
        rec.update(policy=summary_of(pol), error=None)
    except P.PolicyError as e:
        rec.update(policy=None, error=str(e))
        rows.append(
            row(
                "policy",
                None,
                ".github/repo-factory.yml cannot be read",
                f"{e}; maintenance leaves this repository alone until it is fixed",
                url=f"https://github.com/{full}/blob/main/.github/repo-factory.yml",
            )
        )
    rec.update(last_try=now, idea=t["idea"], product=t["product"])
    read_head(r, rec, "main", now, out)
    me = {x for x in (t.get("login"), t.get("bot")) if x}
    ended(loop, r, rec, items, now, out)
    own_prs = fixes.opened(home, full)
    mine, go_reads = [], 0
    issues = [i for i in items if not i["is_pr"]]
    for i in sorted(issues, key=lambda i: i["number"]):
        if "go" not in i["labels"]:
            rows.append(
                row(
                    "waiting",
                    i,
                    i["title"],
                    f"opened by {i['author'] or 'someone'}; nothing happens until you label it go",
                )
            )
            continue
        if go_reads >= MAX_GO_READS:
            rows.append(row("waiting", i, i["title"], "labelled go; read at the next poll"))
            continue
        go_reads += 1
        try:
            from ..loop.from_issue import labelled_by

            who = labelled_by(r.events(i["number"]), "go")
        except GitHubError as e:
            out["errors"].append(f"#{i['number']}: {e}")
            continue
        if who and who in me:
            mine.append(i)
        else:
            rows.append(
                row(
                    "waiting",
                    i,
                    i["title"],
                    f"labelled go by {who or 'someone'}, not by you: only your own go starts a fix",
                )
            )
    for p in [i for i in items if i["is_pr"]]:
        if p["number"] in own_prs:
            continue  # repo-factory's own fix: the fix rows below say it
        if p["author"] in me:
            from .backfill import TITLE

            if p["title"] == TITLE and "repo-factory" in p["labels"]:
                rows.append(
                    row(
                        "setup",
                        p,
                        p["title"],
                        "merge it to let maintenance fix issues you label go",
                    )
                )
            continue
        who = "Dependabot" if p["author"] in BOTS else (p["author"] or "someone")
        rows.append(
            row(
                "waiting",
                p,
                p["title"],
                f"a pull request by {who}; repo-factory never checks it out, runs or merges it",
            )
        )
    # the doctor: reads only; a self-hosted runner stops merging and is a warning
    try:
        doctor.step(
            r,
            rec,
            now,
            float((loop.cfg.get("maintain") or {}).get("doctor_days", 7) or 0),
            head=(rec.get("head") or {}).get("sha"),
        )
    except (GitHubError, OSError) as e:
        out["errors"].append(f"doctor: {e}")
    hosted = (rec.get("doctor") or {}).get("self_hosted") or []
    if hosted:
        rows.append(
            row(
                "self_hosted",
                None,
                "a workflow on main names a self-hosted runner",
                "; ".join(hosted)[:400]
                + ": repo-factory never merges here; read the workflow before you merge anything",
                url=f"https://github.com/{full}/tree/main/.github/workflows",
            )
        )
    maintained = (
        pol is not None and pol["status"] == "maintained" and pol["revise"]["trigger"] != "off"
    )
    if pol and not maintained and not any(x["kind"] == "setup" for x in rows):
        rows.append(
            row(
                "setup",
                None,
                "maintenance waits for its setup",
                f"`.github/repo-factory.yml` says status {pol['status']}: merge the setup pull "
                "request (labels, policy, AGENTS.md, CI) to let it fix issues you label go",
                url=f"https://github.com/{full}/pulls",
            )
        )
    if pol is not None and maintained and switch.merges(home, full):
        try:
            got = merge_step(loop, r, items, pol, rec, now, own_prs, hosted)
        except GitHubError as e:
            got = {"why": f"GitHub did not answer: {e}"}
        rec["queue"] = got
        if got.get("merged"):
            out["merged"].append(got["merged"])
            try:
                rel = release.step(r, pol, lessons=False)
                if rel and rel.get("tag"):
                    out["release"] = rel
                    rec["release"] = {"tag": rel["tag"], "url": rel.get("url"), "at": now}
            except GitHubError as e:
                out["errors"].append(f"release: {e}")
    else:
        rec.pop("queue", None)
    # fixes of your go issues
    if pol is not None and maintained:
        start_fix(loop, r, t, items, mine, pol, rec, now, slots, stop, out)
    rows += fix_rows(home, full, items, mine, pol, rec, own_prs)
    rec.update(
        last_poll=now,
        rows=rows,
        counts={
            "issues": len(issues),
            "prs": sum(1 for i in items if i["is_pr"]),
            "go_yours": len(mine),
            "waiting": sum(1 for x in rows if x["kind"] == "waiting"),
        },
        errors=out["errors"][-5:] or None,
    )
    out["waiting"] = rec["counts"]["waiting"]
    cache.write_repo(full, rec)
    return out


def ended(loop, r: Repo, rec: dict, items: list[dict], now: float, out: dict) -> None:
    """Fix processes that are gone: one that wrote its result recorded itself
    (``fixes.ended``); one that died without a word is recorded here as a failed attempt,
    unless its pull request is open (GitHub then says it got that far)."""
    from pathlib import Path

    revs = dict(rec.get("revises") or {})
    prs_for = revise.linked(items)
    for n, x in list(revs.items()):
        if revise.alive(x.get("pid"), start=x.get("start")):
            continue
        res = revise.result(Path(x["task"]))
        if res is None or (res.get("finished") or 0) < (x.get("started") or 0) - 5:
            if prs_for.get(int(n)):
                res = {"outcome": "pr", "pr": prs_for[int(n)][0], "why": None}
            else:
                res = {
                    "outcome": "failed",
                    "stage": "process",
                    "why": "the fix process stopped without finishing (a restart or a crash)",
                }
            fixes.ended(loop.cfg.home, r.full, int(n), res, now)
        out["ended"].append({"number": int(n), **{k: res.get(k) for k in ("outcome", "pr", "why")}})
        del revs[n]
    rec["revises"] = revs


def start_fix(loop, r, t, items, mine, pol, rec, now, slots, stop, out) -> None:
    if stop:
        out["spend"] = stop
        return
    if rec.get("revises") or slots[0] <= 0:
        return
    home, linked = loop.cfg.home, revise.linked(items)
    max_att = int(pol["revise"]["max_attempts"])
    for item in sorted(mine, key=lambda i: (i.get("created") or 0, i["number"])):
        n = item["number"]
        if linked.get(n) or item["assignees"] or fixes.waits(fixes.get(home, r.full, n), max_att):
            continue
        fresh = r.issue(n)  # the list lags a merge by seconds: read it once more
        if fresh["state"] != "open" or "go" not in fresh["labels"]:
            continue
        branch = revise.branch_name(n, fresh["title"])
        task = revise.write_task(loop.cfg, r.full, fresh, t["product"], branch)
        x = revise.start(loop.cfg, r.full, n, task)
        rec.setdefault("revises", {})[str(n)] = x
        out["started"].append(n)
        slots[0] -= 1
        return


def fix_rows(home, full, items, mine, pol, rec, own_prs) -> list[dict]:
    """What repo-factory's fixes did, from its own record: a pull request waiting for your
    review (or for the merge queue), an issue that waits for you, a fix running now."""
    out = []
    open_prs = {i["number"]: i for i in items if i["is_pr"]}
    by_num = {i["number"]: i for i in items if not i["is_pr"]}
    max_att = int((pol or P.DEFAULTS)["revise"]["max_attempts"])
    queue = {x["number"]: x for x in (rec.get("queue") or {}).get("items") or []}
    for n, x in sorted((rec.get("revises") or {}).items()):
        i = by_num.get(int(n)) or {"number": int(n)}
        out.append(row("fixing", i, i.get("title") or f"#{n}", "a fix is running now"))
    for pr, f in sorted(own_prs.items()):
        p = open_prs.get(pr)
        if not p:
            continue
        q = queue.get(pr)
        why = (
            f"merge queue: {q['why']}"
            if q
            else "a fix by repo-factory: it waits for your review; nothing merges by itself"
        )
        out.append(row("fix_pr", p, p["title"], why, issue=f.get("issue"), usd=f.get("usd")))
    for i in mine:
        w = fixes.waits(fixes.get(home, full, i["number"]), max_att)
        if w:
            out.append(row("needs_you", i, i["title"], w))
    return out


def merge_step(loop, r, items, pol, rec, now, own_prs, hosted) -> dict:
    """One poll's merge of repo-factory's own fixes (the repo's merge switch is on): at most
    one, through ``automerge.judge`` (draft, ``hold``, kinds, size, ``.github/``, licence and
    lock files, changes requested, behind or conflicting, CI not green: each refuses or
    waits), with three more rules: only a pull request this install's fix opened, only while
    its head is the commit that fix pushed, and never while a workflow on ``main`` names a
    self-hosted runner. Bugs and docs only, whatever the policy file says."""
    m = dict(pol["merge"])
    kinds = [k for k in (m.get("kinds") or []) if k in MERGE_KINDS]
    judged = {**pol, "merge": {**m, "kinds": kinds}}
    out: dict = {"on": True, "kinds": kinds, "items": [], "merged": None, "at": now}
    if hosted:
        out["why"] = "a workflow on main names a self-hosted runner: repo-factory never merges here"
        return out
    if not kinds:
        out["why"] = "the policy file's merge.kinds leaves neither bug nor docs"
        return out
    merged = False
    for item in sorted((i for i in items if i["is_pr"]), key=lambda i: i["number"]):
        n = item["number"]
        f = own_prs.get(n)
        if not f:
            continue
        book = {"head": f.get("head")} if f.get("head") else {}
        try:
            pull = r.pull(n)
            if not f.get("head") or pull["head"] != f["head"]:
                j = {"verdict": "refused", "why": "its head is not the commit repo-factory pushed"}
            else:
                j = automerge.judge(r, item, judged, book)
        except GitHubError as e:
            j = {"verdict": "wait", "why": f"GitHub did not answer: {str(e)[:120]}"}
        if j.get("step") == "update" and ";" in j["why"]:  # nothing here updates a branch
            j = {
                **j,
                "why": f"{j['why'].split(';')[0]}: press Update branch on GitHub, or merge it "
                "yourself",
            }
        line = {"number": n, "title": item["title"], "url": item.get("url"), **j}
        if j["verdict"] == "ready" and not merged:
            try:
                r.merge(n, title=f"{item['title']} (#{n})", sha=j["sha"])
            except GitHubError as e:
                line.update(verdict="wait", why=f"the merge was refused: {str(e)[:160]}")
            else:
                merged = True
                line.update(verdict="merged", why=f"merged at `{j['sha'][:7]}`")
                out["merged"] = n
        out["items"].append(line)
    return out


def rows_of(home, repo: str) -> dict:
    """A repository's maintenance as the page reads it, from the cache and the local files:
    never a GitHub call."""
    rec = Cache(home).repo(repo)
    return {
        "repo": repo,
        "switch": switch.get(home, repo),
        "paused": pauses.words(pauses.get(home, repo)),
        "polled": rec.get("last_poll"),
        "policy": rec.get("policy"),
        "error": rec.get("error"),
        "rows": rec.get("rows") or [],
        "counts": rec.get("counts") or {},
        "queue": rec.get("queue"),
        "release": rec.get("release"),
        "doctor": (rec.get("doctor") or {}).get("problems"),
        "fixes": [
            {
                "issue": n,
                **{k: v for k, v in f.items() if k != "history"},
                "history": f.get("history"),
            }
            for n, f in sorted(fixes.of_repo(home, repo).items())
        ],
    }
