"""The maintenance poll: what the loop's tick does every ``maintain.poll_s`` for each
repository you switched maintenance on for (``switch.py``), and nothing else.

Nothing here runs while the loop is paused (the daemon checks first), and a repository that is
paused (``pauses.py``) or switched off is not read at all. Per repository, with one read of its
open issues and pull requests:

1. **Its policy** (``.github/repo-factory.yml``). Fixes start only while the file says
   ``status: maintained``: a product repo-factory published carries it from its first commit;
   any other repository gets it from the setup pull request (``maintain setup``: labels, the
   policy file, AGENTS.md and CI). A file that cannot be read is a row on the page, never a
   comment.
2. **Your word** (``consent.py``): an open issue is fixed only when its ``go`` label was put on
   by you, the login maintenance acts for, read from the issue's events, and (when someone else
   opened it) its title and body were not changed by anyone else after that ``go``. Anyone
   else's issue, a ``go`` someone else put on, an issue edited after your ``go``, and anyone
   else's pull request are "Waiting for you" rows: no model call, no label, no comment,
   nothing else.
3. **Fixes** (``revise.Fix``, quiet): when a slot is free, nothing of this repo is running,
   maintenance's money is not spent (``spend.py``) and the issue does not wait for you
   (``fixes.waits``), the oldest of your ``go`` issues without an open fix gets one, detached.
   A fix ends as a pull request or as a line on the project's page; never a comment.
4. **Merging** (``merge_step``), only with the repo's second switch on (off by default): one of
   repo-factory's own fix pull requests a poll, bugs and docs only, green CI, nothing under
   ``.github/``, only while its head is the commit the fix pushed and no workflow on ``main``
   may run off GitHub's own runners; then a patch release when the policy file allows it.
5. **The doctor** (reads only): AGENTS.md against the tree, and any workflow that may run off
   GitHub's own runners (``runners.py``), which the page shows and which stops fixes and
   merging. While sessions would sign in with Claude Code's own sign-in file
   (``signin.py``), no fix starts either.

What the page shows is written to the poll's disposable cache (``github.Cache``)."""

from __future__ import annotations

import time

from . import automerge, consent, doctor, fixes, pauses, release, revise, signin, spend, switch
from . import policy as P
from .github import BOTS, Cache, GitHubError, Repo

SETUP_BRANCH = "repo-factory/maintenance"
MAX_GO_READS = 30  # issues whose events one poll reads (the rest: the next poll)
MERGE_KINDS = ("bug", "docs")  # what merging its own fixes may ever take


def settings(cfg) -> dict:
    return cfg.get("maintain") or {}


def gh_for(loop, repo: str | None = None, scope: str = "maintain"):
    """Maintenance's ``gh`` (``identity.resolve``): repo-factory's own GitHub App when
    it is installed (a token for ``scope`` on ``repo`` alone), else ``maintain.token_env_file``'s
    token, else the loop's own ``gh``: the owner's login."""
    from .identity import gh_for as _gh_for

    return _gh_for(loop, repo, scope)


def published(loop) -> list[dict]:
    """Every published build's repository (state ``published`` or ``public``), once each, with
    its pause (``pauses.py``) on it: what the pages list and a request may name."""
    out, seen = [], set()
    held = pauses.read(loop.cfg.home)
    for i in loop.ledger.all(("published", "public")):
        full = (i.get("publish") or {}).get("repo")
        if full and full not in seen:
            seen.add(full)
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


def read_head(
    r: Repo, rec: dict, branch: str, now: float, out: dict | None = None, fresh: bool = False
) -> str | None:
    """The branch's commit now, into ``rec["head"]`` (``{"sha", "etag", "branch", "at",
    "moved"}``): one conditional read a poll, a 304 when nothing moved (no rate limit spent).
    What is derived from the repo (the doctor) runs again when it differs from the commit it was
    computed at. ``fresh``: ask without the ETag (a Re-check). A failed read keeps the last
    answer; the doctor then falls back to its days."""
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


def summary_of(pol: dict) -> dict:
    """The policy file in the few words the page shows."""
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


def today(home) -> dict:
    """Every repository's rows from the poll's cache, for Today: never a GitHub call, so the
    page stays fast whatever GitHub does. ``{"rows": [...], "polled": ts}``."""
    from .rows import overlay

    d = Cache(home).read()
    out, polled = [], None
    for full, rec in sorted(d["repos"].items()):
        for r in overlay(home, [{**x, "repo": full} for x in rec.get("rows") or []]):
            out.append(
                {**r, "product": rec.get("product") or full.split("/")[1], "idea": rec.get("idea")}
            )
        polled = max(polled or 0, rec.get("last_poll") or 0) or polled
    return {"rows": out, "polled": polled}


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
    token_logins: dict[str, str | None] = {}  # "you" includes the token file's login, read once
    for t in ts:
        try:
            gh = gh_for(loop, t["repo"])
            t["login"] = me
            t["bot"] = gh.login_name if getattr(gh, "credential", None) == "app" else None
            t["token"] = acting_login(gh, token_logins)
            out["repos"].append(poll_repo(loop, gh, t, cache, now, slots, stop))
        except Exception as e:  # noqa: BLE001 - the next repo still runs
            out["repos"].append({"repo": t["repo"], "errors": [f"{type(e).__name__}: {e}"]})
    loop.ledger.meta("maintain_last_poll", now)
    return out


def acting_login(gh, seen: dict | None = None) -> str | None:
    """The login of the token file maintenance acts with (``maintain.token_env_file``), or
    None without one: "you" is every identity this install acts as (the signed-in login, its
    App's bot, this token's user), so an issue that token labelled ``go`` (Build this
    proposal) counts as yours. Read once per file (``seen``)."""
    from .identity import login_of

    f = getattr(gh, "env_file", None)
    if not f:
        return None
    key = str(f)
    if seen is not None and key in seen:
        return seen[key]
    got = login_of(gh)
    if seen is not None:
        seen[key] = got
    return got


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
        r.default_branch()  # once a poll: every read and write below means this branch
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
                url=f"https://github.com/{full}/blob/{r.base}/.github/repo-factory.yml",
            )
        )
    rec.update(last_try=now, idea=t["idea"], product=t["product"])
    rec["base"] = r.base
    read_head(r, rec, r.base, now, out)
    me = {x for x in (t.get("login"), t.get("bot"), t.get("token")) if x}
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
            why = consent.why_not(r, i, me)
        except GitHubError as e:
            out["errors"].append(f"#{i['number']}: {e}")
            continue
        if why is None:
            mine.append(i)
        else:
            rows.append(row("waiting", i, i["title"], why))
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
                        "the setup pull request (labels, AGENTS.md, CI): read it, then merge it",
                    )
                )
            continue
        who = "Dependabot" if p["author"] in BOTS else (p["author"] or "someone")
        claims = sorted(revise.linked([p]))  # the issues its title or body says it fixes
        says = (
            f" says it fixes {', '.join(f'#{n}' for n in claims)}; it is not repo-factory's fix"
            " and holds none of repo-factory's"
            if claims
            else ""
        )
        rows.append(
            row(
                "waiting",
                p,
                p["title"],
                f"a pull request by {who}{says}; repo-factory never checks it out, runs or "
                "merges it",
            )
        )
    # the doctor: reads only; a workflow off GitHub's own runners stops fixes and merging
    try:
        doctor.step(
            r,
            rec,
            now,
            float((loop.cfg.get("maintain") or {}).get("doctor_days", 7) or 0),
            head=(rec.get("head") or {}).get("sha"),
            force=not rec.get("doctor"),  # where the workflows run is read once at least
        )
    except (GitHubError, OSError) as e:
        out["errors"].append(f"doctor: {e}")
    hosted = (rec.get("doctor") or {}).get("self_hosted") or []
    if not rec.get("doctor"):  # never read: where main's workflows run cannot be told yet
        hosted = ["where main's workflows run has not been read yet"]
    if hosted:
        rows.append(
            row(
                "self_hosted",
                None,
                "a workflow on main may run off GitHub's own runners",
                "; ".join(hosted)[:400]
                + ": repo-factory starts no fix here (its pull request would run the fix's code "
                "on that machine) and never merges; read the workflow",
                url=f"https://github.com/{full}/tree/{r.base}/.github/workflows",
            )
        )
    held = signin.blocks(loop.cfg)
    if held:
        rows.append(row("signin", None, "fixes wait for a Claude login token", held))
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
        # a release after the merge only with the repository's third switch on (off by
        # default, whatever the policy file says)
        if got.get("merged") and not switch.releases(home, full):
            out["merged"].append(got["merged"])
        elif got.get("merged"):
            out["merged"].append(got["merged"])
            try:
                rel = release.step(r, pol)
                if rel and rel.get("tag"):
                    out["release"] = rel
                    rec["release"] = {"tag": rel["tag"], "url": rel.get("url"), "at": now}
            except GitHubError as e:
                out["errors"].append(f"release: {e}")
    else:
        rec.pop("queue", None)
    # fixes of your go issues: never while a workflow may run off GitHub's own runners, or
    # while sessions would sign in with Claude Code's own sign-in file
    if pol is not None and maintained and not hosted and not held:
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
    for n, x in list(revs.items()):
        if revise.alive(x.get("pid"), start=x.get("start")):
            continue
        res = revise.result(Path(x["task"]))
        if res is None or (res.get("finished") or 0) < (x.get("started") or 0) - 5:
            # never anyone's "Fixes #N" taken for this fix's own pull request
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
    home = loop.cfg.home
    # only a pull request this install's fix opened is "the fix of N": anyone
    # else's "Fixes #N" is a row and never holds the fix
    open_prs = {i["number"] for i in items if i["is_pr"]}
    ours = {f["issue"] for pr, f in fixes.opened(home, r.full).items() if pr in open_prs}
    max_att = int(pol["revise"]["max_attempts"])
    for item in sorted(mine, key=lambda i: (i.get("created") or 0, i["number"])):
        n = item["number"]
        if n in ours or item["assignees"] or fixes.waits(fixes.get(home, r.full, n), max_att):
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
    its head is the commit that fix pushed, and never while a workflow on ``main`` may run off
    GitHub's own runners. Bugs and docs only, whatever the policy file says."""
    m = dict(pol["merge"])
    kinds = [k for k in (m.get("kinds") or []) if k in MERGE_KINDS]
    judged = {**pol, "merge": {**m, "kinds": kinds}}
    out: dict = {"on": True, "kinds": kinds, "items": [], "merged": None, "at": now}
    if hosted:
        out["why"] = (
            "a workflow on main may run off GitHub's own runners: repo-factory never merges here"
        )
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
            elif why := diff_refuses(r, n, f):
                j = {"verdict": "refused", "why": why}
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


def diff_refuses(r, pr: int, f: dict) -> str | None:
    """Why merging refuses repo-factory's fix pull request ``pr`` by what its diff holds, or
    None (the diff is judged, not the label). Its issue must carry exactly one kind
    label, ``bug`` or ``docs`` (none: no merge). ``docs``: every changed file is a document
    (``*.md``, ``*.rst``, ``*.txt``, ``docs/``). ``bug``: the fix's reproducing test is in the
    diff. Either: no changed file runs somewhere (manifests, lock files, build and test
    settings, ``.github/``, agents' and editors' files, git's own) and none is a link or a
    submodule. ``f``: the fix's line in ``fixes.json``."""
    from . import labels as L

    n = f.get("issue")
    if not isinstance(n, int):
        return "its issue is not known"
    kinds = sorted({x for x in r.issue(n)["labels"] if x in L.KINDS})
    if not kinds:
        return f"issue #{n} has no kind label: only a bug or docs fix whose issue says so merges"
    if len(kinds) > 1:
        return f"issue #{n} carries more than one kind ({', '.join(kinds)}): no merge"
    kind = kinds[0]
    if kind not in MERGE_KINDS:
        return f"a `{kind}`: repo-factory never merges one; read it and merge it yourself"
    files = r.pull_changes(pr)
    if not files:
        return "its diff could not be read"
    for x in files:
        why = automerge.never_path(x)
        if why:
            return f"{why}: merging never takes that"
    modes = r.modes(str(f.get("head")))
    odd = [x for x in files if modes.get(x) in ("120000", "160000")]
    if odd:
        return f"`{odd[0]}` is a link or a submodule: merging never takes one"
    if kind == "docs":
        other = [x for x in files if not automerge.is_document(x)]
        if other:
            return f"a docs fix that changes `{other[0]}`, which is not a document"
    if kind == "bug":
        test = f.get("test")
        if not isinstance(test, str) or test not in files:
            return "its reproducing test is not in the diff"
    return None


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
