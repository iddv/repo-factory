"""repo-factory keeps its own fix pull requests mergeable (MAINTAIN.md §5.2, *Keeping repo-factory's pull
requests current*), whether or not it may merge them.

When the owner merges one of repo-factory's fixes, ``main`` moves under the others: they fall behind,
or conflict. Every poll, for each open pull request repo-factory opened for a fix (label ``repo-factory``, a
``repo-factory/…`` branch in the repo itself, repo-factory's ``revise-meta`` line in its body):

1. **Hands off** when it is a draft, labelled ``hold`` or ``needs-you``, or when anyone else
   pushed a commit to it. repo-factory knows its own head: the commit it pushed (or, for a pull request
   from before this, every commit made before the pull request was opened), plus the merge
   commits GitHub's *Update branch* makes (committed by ``web-flow``). Any other commit is the
   owner's, and repo-factory never touches that pull request again.
2. **Up to date** (``main`` has nothing it lacks): nothing to do.
3. **Behind, no conflict**: GitHub's *Update branch* (``update-branch``, only while the head is
   the one repo-factory read), and CI runs again.
4. **Behind, with a conflict only in** ``CHANGELOG.md``: git merges ``main`` into the branch
   itself, keeping both sides' lines there (``merge_main``), and pushes the merge with
   ``--force-with-lease``. No model runs, the diff the owner reviewed stays as it was, and no
   refresh is spent: with k open pull requests the last one needs k−1 of these.
5. **Behind, with a conflict in any other file**: a **refresh**: the same fix pipeline and
   stages, readiness included, run again on the new ``main`` (``revise.Fix`` with ``refresh``), pushed with
   ``--force-with-lease`` to the same branch, so the pull request and its review history
   stay. A refresh is not an attempt on the issue. At most ``maintain.refresh_max`` (2) per
   pull request; past that, or when one fails, the pull request is labelled ``needs-you``
   with the reason. When the fix turns out to be on ``main`` already (its reproducing test
   passes there, or its issue was closed), the pull request is closed and says so.

Each pull request carries one short comment, edited in place (the ``repo-factory:current`` marker,
which also records the head repo-factory knows and the refreshes spent: GitHub is the record; the
cache only saves reads). Pushes go only to ``repo-factory/`` branches, by the host, with
maintenance's narrowed identity (``revise.Fix.remote``); no session ever holds a token.

It imports nothing of maintenance's import cycle (the poll hands it the refresh's ``start``), so
it stays a low layer the poll and the fix both use."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from .. import gitcmd, proc
from .github import GitHubError, Repo
from .words import META, SIGNATURE, meta

CURRENT = "<!-- repo-factory:current v1"
MARK = re.compile(r"<!-- repo-factory:current v1 head=(\S+) refreshes=(\d+)")
SLACK_S = 300  # a commit made up to 5 minutes after its pull request opened
WEB_FLOW = "web-flow"  # who commits GitHub's own merges (Update branch)
HANDS_OFF = ("hold", "needs-you")
# files whose lines a merge keeps from both sides (git's union merge): fix pull requests opened
# before 2026-10-16 each added a line under CHANGELOG's Unreleased, so they all conflicted there
UNION = ("CHANGELOG.md",)


def settings(cfg) -> dict:
    return cfg.get("maintain") or {}


def refresh_max(cfg) -> int:
    v = settings(cfg).get("refresh_max", 2)
    return int(v if v is not None else 2)


def own(item: dict) -> bool:
    """One of repo-factory's fix pull requests, from what the issue list says: open, labelled
    ``repo-factory``, with repo-factory's meta line (the branch is checked on the pull request itself)."""
    return (
        bool(item.get("is_pr"))
        and item.get("state", "open") == "open"
        and "repo-factory" in item["labels"]
        and META in (item.get("body") or "")
    )


def issue_of(item: dict) -> int | None:
    m = meta(item.get("body") or "") or {}
    try:
        return int(m["issue"])
    except (KeyError, ValueError):
        return None


def read_mark(comments: list[dict]) -> dict:
    """repo-factory's status comment on the pull request: ``{"id", "head", "refreshes"}`` or ``{}``."""
    for c in comments:
        m = MARK.search(c.get("body") or "")
        if m:
            return {
                "id": c.get("id"),
                "head": None if m.group(1) == "-" else m.group(1),
                "refreshes": int(m.group(2)),
            }
    return {}


def body(text: str, head: str | None, refreshes: int) -> str:
    return f"{CURRENT} head={head or '-'} refreshes={int(refreshes)} -->\n{text}\n\n_{SIGNATURE}_\n"


def say(r: Repo, n: int, b: dict, text: str | None = None) -> None:
    """The one status comment, edited in place (posted the first time)."""
    if text is not None:
        b["say"] = text
    if not b.get("say"):
        return
    r.comment_with_marker(n, CURRENT, body(b["say"], b.get("head"), b.get("refreshes") or 0))


def short(sha: str | None) -> str:
    return (sha or "")[:7]


class Moved:
    """Why ``main`` moved, read once a poll: "after #12 merged" or "after main moved to
    `abc1234`"."""

    def __init__(self, r: Repo):
        self.r, self._said = r, None

    def __str__(self) -> str:
        if self._said is None:
            self._said = "after `main` moved"
            try:
                sha = (self.r.get(f"repos/{self.r.full}/commits/main") or {}).get("sha")
                prs = self.r.commit_pulls(sha) if sha else []
                self._said = (
                    f"after #{prs[0]['number']} merged"
                    if prs
                    else f"after `main` moved to `{short(sha)}`"
                )
            except GitHubError:
                pass
        return self._said


def known_head(r: Repo, pull: dict, b: dict) -> bool:
    """Whether the pull request's head is repo-factory's own (``b["head"]`` is moved on when it is):
    the head repo-factory knows, a merge GitHub's *Update branch* made on top of it, or, for one
    repo-factory knows nothing of yet, a branch whose every commit predates the pull request (or is
    such a merge). Anything else: someone else pushed, and repo-factory keeps its hands off."""
    head, mine = pull["head"], b.get("head")
    if not head:
        return False
    if head == mine:
        return True
    if mine:
        c = r.commit(head)
        if len(c["parents"]) == 2 and c["parents"][0] == mine and c["committer"] == WEB_FLOW:
            b["head"] = head
            return True
        return False
    opened = pull.get("created") or 0
    for c in r.pull_commits(pull["number"]):
        merge = len(c["parents"]) == 2 and c["committer"] == WEB_FLOW
        if not merge and (c["date"] or 0) > opened + SLACK_S:
            return False
    b["head"] = head
    return True


def merge_main(r: Repo, pull: dict) -> str | None:
    """``main`` merged into repo-factory's branch by git, ``UNION`` files keeping both sides' lines, and
    pushed only while the branch's head is still ``pull["head"]``: the new head, or None when
    another file conflicts (or anything else fails; the refresh is the fallback). The host's
    hardened git, maintenance's narrowed identity, as ``revise.Fix`` pushes."""
    from . import identity

    g = identity.narrow(r.gh, "push")
    auth = g.git_auth()
    branch, head = pull["head_ref"], pull["head"]
    with tempfile.TemporaryDirectory(prefix="repo-factory-merge-") as tmp:
        repo = Path(tmp) / "repo"

        def git(*args: str, env: dict | None = None) -> str:
            return gitcmd.git(*args, cwd=repo, env=env)

        try:
            r.gh("repo", "clone", r.full, str(repo), "--", "-q")
            git("checkout", "-q", "-B", branch, f"origin/{branch}")
            if git("rev-parse", "HEAD").strip() != head:
                return None
            (repo / ".git" / "info").mkdir(exist_ok=True)
            (repo / ".git" / "info" / "attributes").write_text(
                "".join(f"/{f} merge=union\n" for f in UNION)
            )
            who = identity.git_author(r.gh) or [
                "-c",
                "user.name=repo-factory",
                "-c",
                "user.email=repo-factory@localhost",
            ]
            p = gitcmd.run(
                [
                    *who,
                    "merge",
                    "-q",
                    "--no-edit",
                    "-m",
                    f"Merge main into {branch}",
                    "origin/main",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if p.returncode != 0:
                return None
            git(
                *auth,
                "push",
                "-q",
                f"--force-with-lease=refs/heads/{branch}:{head}",
                "origin",
                f"HEAD:refs/heads/{branch}",
                env=g.env() if auth else None,
            )
            return git("rev-parse", "HEAD").strip()
        except Exception:  # noqa: BLE001 - the refresh is the fallback
            return None


def result(task: Path) -> dict | None:
    """How a fix process ended (``revise.RESULT`` in its task folder), or None."""
    try:
        return json.loads((Path(task) / ".den" / "revise.json").read_text())
    except (OSError, ValueError):
        return None


def ended(loop, r: Repo, book: dict, revs: dict) -> list[dict]:
    """Refreshes whose process ended: their outcome into the book (the process itself already
    said it on the pull request). A process that died without a word leaves the pull request
    as it was; the next poll looks at it afresh (the refresh it spent stays spent)."""
    out = []
    for n, x in list(revs.items()):
        if not x.get("refresh") or proc.running(x.get("pid"), x.get("start")):
            continue
        res = result(Path(x["task"]))
        pr = str(x["refresh"])
        b = book.setdefault(pr, {})
        if res is None or (res.get("finished") or 0) < (x.get("started") or 0) - 5:
            res = {"outcome": "died", "why": "the refresh stopped without finishing"}
        if res.get("outcome") == "refreshed" and res.get("head"):
            b.update(head=res["head"], state="refreshed", base=None)
        else:
            b.update(state=res.get("outcome") or "failed", why=res.get("why"))
        out.append(
            {"pr": int(pr), "issue": int(n), "outcome": res.get("outcome"), "why": res.get("why")}
        )
        del revs[n]
    return out


def step(
    loop,
    r: Repo,
    t: dict,
    items: list[dict],
    pol: dict,
    rec: dict,
    now: float,
    slots: list[int],
    moved: bool = False,
    start=None,
) -> dict:
    """One poll's keeping-current of one repo's repo-factory pull requests. ``moved``: something
    merged earlier in this poll, so ``rec["head"]`` is stale and every one is compared.
    ``start(issue, pr)``: starts a refresh, detached (``revise.start_refresh``), and returns
    its process record."""
    out: dict = {"updated": [], "refreshing": [], "ended": [], "needs_you": [], "left": []}
    book: dict = {k: dict(v) for k, v in (rec.get("prs") or {}).items()}
    revs = dict(rec.get("revises") or {})
    out["ended"] = ended(loop, r, book, revs)
    main = None if moved else (rec.get("head") or {}).get("sha")
    why = Moved(r)
    cap = refresh_max(loop.cfg)
    mine = sorted((i for i in items if own(i)), key=lambda i: i["number"])
    seen = set()
    for item in mine:
        n = item["number"]
        seen.add(str(n))
        b = book.setdefault(str(n), {})
        b.update(
            number=n,
            title=item["title"],
            url=item.get("url"),
            issue=issue_of(item),
            labels=[x for x in item["labels"]],
        )
        try:
            if item.get("draft") or set(item["labels"]) & set(HANDS_OFF):
                b["state"] = next((x for x in HANDS_OFF if x in item["labels"]), "draft")
                continue
            pull = r.pull(n)
            if b.get("state") == "touched" and b.get("touched") == pull["head"]:
                continue  # said once; the owner's from here
            if not pull["head_ref"].startswith("repo-factory/") or pull["head_repo"] != r.full:
                b["state"] = "not-repo-factory"
                continue
            if "head" not in b or "refreshes" not in b:
                got = read_mark(r.comments(n))
                b.setdefault("head", got.get("head"))
                b["refreshes"] = max(int(b.get("refreshes") or 0), got.get("refreshes") or 0)
            if not known_head(r, pull, b):
                b.update(state="touched", touched=pull["head"])
                out["left"].append(n)
                continue
            if any(x.get("refresh") == n for x in revs.values()):
                b["state"] = "refreshing"
                continue
            if main and b.get("base") == main and b.get("state") in ("current", "updating"):
                continue
            behind = r.behind(pull["head"], "main")
            if behind == 0:
                b.update(state="current", base=main, why=None)
                if b.get("say") and b.get("said_head") != b["head"]:
                    say(r, n, b)  # the marker keeps the head repo-factory knows
                    b["said_head"] = b["head"]
                continue
            b["behind"] = behind
            if pull["mergeable"] is None:
                b["state"] = "checking"  # GitHub is still working it out
                continue
            if pull["mergeable"] and pull["mergeable_state"] != "dirty":
                try:
                    r.update_branch(n, pull["head"])
                except GitHubError as e:
                    if "422" not in str(e) and "conflict" not in str(e).lower():
                        raise
                else:
                    b.update(state="updating", base=None, why=None)
                    say(
                        r,
                        n,
                        b,
                        f"Updated onto `main` {why} (GitHub's *Update branch*: `main` "
                        "merged into this branch). CI runs again on it.",
                    )
                    b["said_head"] = b["head"]
                    out["updated"].append(n)
                    continue
            # a conflict: git merges main when only CHANGELOG.md conflicts (no model, the
            # reviewed diff stays); else re-do the fix on the new main, or hand it to the owner
            new = merge_main(r, pull)
            if new:
                b.update(state="updating", base=None, why=None, head=new)
                say(
                    r,
                    n,
                    b,
                    f"Brought onto `main` {why}: git merged `main` into this branch, "
                    "keeping both sides' lines in CHANGELOG.md. The change itself is "
                    "as you reviewed it; CI runs again on it.",
                )
                b["said_head"] = b["head"]
                out["updated"].append(n)
                continue
            if pol.get("status") != "maintained":
                b["state"] = "conflict"
                continue
            k = int(b.get("refreshes") or 0)
            if k >= cap:
                give_up(
                    r,
                    n,
                    b,
                    f"This conflicts with `main` {why}, and repo-factory has already "
                    f"re-done it {k} time{'s' if k != 1 else ''} (the most it "
                    f"does, `maintain.refresh_max: {cap}`).",
                )
                out["needs_you"].append(n)
                continue
            b["state"] = "conflict"
            if slots[0] <= 0 or revs:
                if b.get("said") != "conflict":  # one fix process per repo; it waits its turn
                    say(
                        r,
                        n,
                        b,
                        f"This no longer applies cleanly to `main` {why}. repo-factory re-does "
                        "the fix on the new `main` when a fix slot is free (one at a time "
                        "per repository; not counted as an attempt) and pushes it to "
                        "this branch.",
                    )
                    b["said"] = "conflict"
                continue
            issue = b.get("issue")
            if not issue:
                give_up(
                    r,
                    n,
                    b,
                    f"This conflicts with `main` {why}, and its body no longer "
                    "says which issue it fixes, so repo-factory cannot re-do it.",
                )
                out["needs_you"].append(n)
                continue
            b["refreshes"] = k + 1
            b["said"] = "refreshing"
            say(
                r,
                n,
                b,
                f"This no longer applies cleanly to `main` {why}, so repo-factory is re-doing "
                f"the fix on the new `main` (refresh {k + 1} of {cap}; not counted as "
                "an attempt). Same stages, readiness included; it is pushed to this "
                "branch, so this pull request and its review history stay.",
            )
            x = start(issue, n)
            revs[str(issue)] = x
            slots[0] -= 1
            b["state"] = "refreshing"
            out["refreshing"].append(n)
        except GitHubError as e:
            b["error"] = str(e)[:300]
            out.setdefault("errors", []).append(f"PR #{n}: {e}")
    rec["prs"] = {k: v for k, v in book.items() if k in seen}
    rec["revises"] = revs
    return out


def give_up(r: Repo, n: int, b: dict, why: str) -> None:
    """The pull request is the owner's to resolve: ``needs-you`` and the reason."""
    r.set_labels(n, ["needs-you"], have=b.get("labels") or [])
    b.update(state="needs-you", why=why)
    say(
        r,
        n,
        b,
        f"{why} It is labelled `needs-you`: resolve the conflict by hand (AGENTS.md "
        "says how to run everything), or close this pull request and repo-factory fixes "
        f"#{b.get('issue') or '?'} again from `main`. repo-factory leaves it alone from here.",
    )


def summary(rec: dict) -> dict:
    """What the page shows of a repo's repo-factory pull requests, from the cache."""
    return {
        k: {
            f: v.get(f)
            for f in ("number", "title", "url", "state", "why", "refreshes", "issue", "behind")
        }
        for k, v in (rec.get("prs") or {}).items()
    }
