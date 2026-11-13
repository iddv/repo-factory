"""``merge.factory_patch: ci-green`` (MAINTAIN.md §5.2): repo-factory merges its own fix pull requests
when CI is green, in a repo whose ``.github/repo-factory.yml`` opts in. Off by default (decision D3,
overruled by the owner on 2026-10-15 as an opt-in per repo).

Every poll, per repo, the queue is repo-factory's open fix pull requests (``current.own``), oldest
first. Each is **refused** (it waits for the owner, with the reason on the page) when any of
these holds:

- its kind is not in ``merge.kinds`` (``bug``, ``docs`` by default; the owner may add
  ``security`` and ``dependency``); a ``feature`` never, whatever the file says;
- it is labelled ``hold`` or ``needs-you``, or a review requests changes;
- anyone else pushed a commit to it (``current.known_head``);
- its diff is over ``merge.max_lines`` changed lines (added plus removed) outside its tests: the
  test the protocol demands is not size (Beliefline #11: 20 README lines and a 67-line test);
- it touches ``.github/``, a LICENSE, NOTICE or COPYING file, or a lockfile;
- its head commit has no check run (a repo without CI never auto-merges), or one that is not
  ``success`` (``skipped`` aside; a cancelled job is not green).

It **waits** while it is behind ``main`` or conflicts (``current.py`` brings it up to date),
while GitHub works out whether it merges, and while a check is queued or running. The oldest
one that is not refused is next; when it is ready, it is squash-merged with GitHub's ``sha``
guard (only while that commit is still its head), and a comment names the policy line. One
merge per repo per poll: then ``current.py`` updates the rest and their CI runs again before
the next. The release waits while the queue still has fixes coming (``release_waits``), so a
batch merged over several polls makes one release once the queue is empty."""

from __future__ import annotations

from pathlib import PurePosixPath

from . import current as C
from . import labels as L
from . import triage
from .github import GitHubError, Repo

MERGED = "<!-- repo-factory:merge v1 -->"
NEVER_KINDS = ("feature",)
LOCKFILES = {
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "bun.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "go.sum",
    "composer.lock",
    "mix.lock",
    "flake.lock",
    "packages.lock.json",
    "gradle.lockfile",
}
GREEN = ("success", "skipped")  # a skipped job's `if:` was false; nothing else counts


def may_merge_kind(kind: str | None, merge: dict) -> bool:
    return bool(kind) and kind not in NEVER_KINDS and kind in (merge.get("kinds") or [])


def is_on(pol: dict) -> bool:
    return (
        (pol.get("merge") or {}).get("factory_patch") == "ci-green"
        and pol.get("status") == "maintained"
        and not pol.get("adopted")
    )


def never_path(f: str) -> str | None:
    """Why auto-merge never takes a diff touching ``f``, or None."""
    p = PurePosixPath(f)
    if f.startswith(".github/"):
        return f"`{f}` is under .github/"
    name = p.name.upper()
    if name.startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING")):
        return f"`{f}` is a licence or notice file"
    if p.name in LOCKFILES:
        return f"`{f}` is a lockfile"
    return None


def checks(r: Repo, sha: str) -> dict:
    """The check runs on ``sha``, judged strictly: ``green`` (every one finished ``success``
    or ``skipped``, and at least one ``success``), ``pending``, ``red`` or ``none``."""
    got = r.get(f"repos/{r.full}/commits/{sha}/check-runs?per_page=100") or {}
    runs = got.get("check_runs") or []
    if not runs:
        return {"state": "none", "runs": 0}
    waiting = [x.get("name") for x in runs if x.get("status") != "completed"]
    bad = [
        f"{x.get('name')} ({x.get('conclusion')})"
        for x in runs
        if x.get("status") == "completed" and x.get("conclusion") not in GREEN
    ]
    if bad:
        return {"state": "red", "runs": len(runs), "bad": bad}
    if waiting:
        return {"state": "pending", "runs": len(runs), "waiting": waiting}
    if not any(x.get("conclusion") == "success" for x in runs):
        return {"state": "none", "runs": len(runs)}
    return {"state": "green", "runs": len(runs)}


def changes_requested(reviews: list[dict]) -> list[str]:
    """Who asks for changes now (each reviewer's latest word that counts)."""
    last: dict[str, str] = {}
    for x in sorted(reviews, key=lambda x: x.get("at") or 0):
        if x["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[x["user"]] = x["state"]
    return sorted(u for u, s in last.items() if s == "CHANGES_REQUESTED")


def kinds_of(r: Repo, item: dict) -> set[str]:
    """The pull request's kind labels and its issue's (the owner may have relabelled it)."""
    out = {x for x in item["labels"] if x in L.KINDS}
    n = C.issue_of(item)
    if n:
        try:
            out |= {x for x in r.issue(n)["labels"] if x in L.KINDS}
        except GitHubError:
            pass
    return out


def judge(r: Repo, item: dict, pol: dict, b: dict) -> dict:
    """One pull request's place in the queue: ``{"verdict": ready|wait|refused, "why", ...}``.
    The cheap facts first, so a refused one costs few reads."""
    m = pol["merge"]
    n = item["number"]

    def no(why):
        return {"verdict": "refused", "why": why}

    if item.get("draft"):
        return no("a draft")
    for lab in ("hold", "needs-you"):
        if lab in item["labels"]:
            return no(f"labelled `{lab}`")
    kinds = kinds_of(r, item)
    if not kinds:
        return no("it has no kind label")
    never = sorted(kinds & set(NEVER_KINDS))
    if never:
        return no(f"a `{never[0]}`: repo-factory never merges one")
    off = sorted(k for k in kinds if k not in (m.get("kinds") or []))
    if off:
        return no(
            f"a `{off[0]}` fix, and `merge.kinds` is {', '.join(m.get('kinds') or []) or 'empty'}"
        )
    pull = r.pull(n)
    if not pull["head_ref"].startswith("repo-factory/") or pull["head_repo"] != r.full:
        return no("not on a repo-factory/ branch of this repository")
    if not C.known_head(r, pull, dict(b)):
        return no("someone else pushed commits to it")
    from ..v5.exam import is_test_path

    files = r.pull_changes(n)
    lines = sum(c for f, c in files.items() if not is_test_path(f))
    if lines > int(m["max_lines"]):
        return no(
            f"{lines} changed lines outside its tests, over `merge.max_lines: {m['max_lines']}`"
        )
    for f in files:
        why = never_path(f)
        if why:
            return no(f"{why}: auto-merge never takes that")
    who = changes_requested(r.reviews(n))
    if who:
        return no(f"changes requested by {', '.join(who)}")
    base = {"sha": pull["head"], "lines": lines, "kind": sorted(kinds)[0], "title": pull["title"]}
    if r.behind(pull["head"], "main"):
        return {
            **base,
            "verdict": "wait",
            "why": "behind `main`; repo-factory brings it up to date",
            "step": "update",
        }
    if pull["mergeable"] is None:
        return {
            **base,
            "verdict": "wait",
            "why": "GitHub is still checking that it merges",
            "step": "update",
        }
    if not pull["mergeable"] or pull["mergeable_state"] == "dirty":
        return {
            **base,
            "verdict": "wait",
            "why": "it conflicts with `main`; repo-factory re-does it",
            "step": "update",
        }
    ci = checks(r, pull["head"])
    if ci["state"] == "none":
        return no("no CI ran on it: a repo without checks never auto-merges")
    if ci["state"] == "red":
        return no("CI is not green: " + ", ".join(ci["bad"][:3]))
    if ci["state"] == "pending":
        return {**base, "verdict": "wait", "why": "waiting for CI", "step": "ci"}
    return {**base, "verdict": "ready", "why": f"every check green on `{C.short(pull['head'])}`"}


def merge_comment(pol: dict, j: dict) -> str:
    m = pol["merge"]
    return (
        f"{MERGED}\nMerged by repo-factory: a `{j['kind']}` fix of {j['lines']} changed lines, "
        f"with every check green on `{C.short(j['sha'])}`, as `.github/repo-factory.yml` › "
        f"`merge.factory_patch: ci-green` allows (`merge.kinds: "
        f"[{', '.join(m.get('kinds') or [])}]`, `merge.max_lines: {m['max_lines']}`). "
        "Label a fix `hold` to keep it for yourself.\n\n"
        f"_{triage.SIGNATURE}_\n"
    )


def step(
    r: Repo, items: list[dict], pol: dict, rec: dict, now: float, may_merge: bool = True
) -> dict:
    """One poll's merge queue of one repo: at most one merge. ``may_merge``: False when
    something else merged in this poll already (main moved; the rest wait a poll)."""
    m = pol["merge"]
    out: dict = {
        "on": is_on(pol),
        "kinds": list(m.get("kinds") or []),
        "max_lines": m.get("max_lines"),
        "items": [],
        "merged": None,
        "at": now,
    }
    if not out["on"]:
        return out
    book = rec.get("prs") or {}
    merged = False
    for item in sorted((i for i in items if C.own(i)), key=lambda i: i["number"]):
        n = item["number"]
        try:
            j = judge(r, item, pol, book.get(str(n)) or {})
        except GitHubError as e:
            j = {"verdict": "wait", "why": f"GitHub did not answer: {str(e)[:120]}"}
        row = {"number": n, "title": item["title"], "url": item.get("url"), **j}
        head = not any(x["verdict"] != "refused" for x in out["items"])
        if j["verdict"] == "ready" and head and may_merge and not merged:
            try:
                r.merge(n, title=f"{item['title']} (#{n})", sha=j["sha"])
            except GitHubError as e:
                row.update(verdict="wait", why=f"the merge was refused: {str(e)[:160]}")
            else:
                merged = True
                row.update(verdict="merged", why=f"merged at `{C.short(j['sha'])}`")
                out["merged"] = n
                try:
                    r.comment_with_marker(n, MERGED, merge_comment(pol, j), edit=False)
                except GitHubError:
                    pass  # merged is merged; the comment is a courtesy
        out["items"].append(row)
    out["line"] = line(out["items"])
    return out


def waiting(q: dict) -> list[dict]:
    return [x for x in q.get("items") or [] if x["verdict"] in ("wait", "ready")]


def line(items: list[dict]) -> str:
    """The queue in one line: "3 fixes waiting for CI · next merge after #13 is green"."""
    w = [x for x in items if x["verdict"] in ("wait", "ready")]
    done = [x for x in items if x["verdict"] == "merged"]
    bits = []
    if done:
        bits.append(f"merged #{done[0]['number']}")
    if not w:
        return " · ".join(bits) or "no fix waiting"
    ci = sum(1 for x in w if x.get("step") == "ci")
    n = len(w)
    bits.append(f"{n} fix{'es' if n != 1 else ''} waiting" + (" for CI" if ci == n else ""))
    nxt = w[0]
    if nxt["verdict"] == "ready":
        bits.append(f"#{nxt['number']} merges at the next poll")
    elif nxt.get("step") == "ci":
        bits.append(f"next merge after #{nxt['number']} is green")
    else:
        bits.append(f"next merge after #{nxt['number']} is up to date and green")
    return " · ".join(bits)


def release_waits(q: dict, rec: dict, now: float) -> bool:
    """Whether this poll's release waits for the queue: something of it merged, and fixes are
    still coming. It is cut once the queue is empty (no clock: the 6 hours it used to wait at
    most existed only because a release was cut per merge)."""
    if not q.get("on") or not waiting(q):
        rec.pop("release_wait", None)
        return False
    if not (q.get("merged") or rec.get("release_wait")):
        return False  # nothing of the queue merged yet: nothing to batch
    rec.setdefault("release_wait", now)
    return True
