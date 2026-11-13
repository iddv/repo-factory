"""repo-factory keeps its own fix pull requests mergeable, and merges them on green CI when the repo
opts in (``current.py``, ``automerge.py``; MAINTAIN.md §5.2). A fake GitHub over a real bare
git repository (update-branch, squash merges and conflicts are real git), a scripted session
harness for the refresh: no model, no network, never a live repo."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import pytest

from repo_factory.gh import Gh
from repo_factory.maintain import automerge, current, github, policy, poll, revise
from repo_factory.maintain.github import Cache

from .loop_common import make_loop
from .test_maintain_revise import FILES, Scripted, no_asset, note, pytest_runner
from .test_maintain_triage import FakeGitHub, stamp

pytestmark = pytest.mark.usefixtures("maintenance_on")

REPO = "o/plots"
OWNER = ["-c", "user.name=owner", "-c", "user.email=owner@localhost"]
CONFLICT_TEST = "tests/acceptance/test_empty_named_plot.py"


def g(bare, *args, env=None, check=True):
    p = subprocess.run(
        ["git", *args], cwd=bare, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    if check and p.returncode != 0:
        raise AssertionError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


def who(name, at=None):
    at = int(at or time.time())
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": f"{name}@localhost",
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": f"{name}@localhost",
        "GIT_AUTHOR_DATE": f"{at} +0000",
        "GIT_COMMITTER_DATE": f"{at} +0000",
    }


class GitHub(FakeGitHub):
    """M2's fake (issues, comments, labels) plus pull requests whose branches are real refs in
    a bare repository: their head, mergeability, files, size and commits come from git;
    ``update-branch`` and a squash merge are real merges (``merge-tree``); ``ci`` is the check
    state per commit (``ci_default`` for any other: None means the repo has no CI)."""

    def __init__(self, bare: Path, policy_text: str):
        super().__init__([], policy_text=policy_text)
        self.bare = bare
        self.web_flow: set[str] = set()
        self.squashed: dict[str, int] = {}
        self.ci: dict[str, str | None] = {}
        self.ci_default: str | None = "success"
        self.review_list: dict[int, list] = {}
        self.updates: list[int] = []
        self.merges: list[int] = []
        self.releases: list[dict] = []
        self.put_files: list[dict] = []
        self.refs: dict[str, str] = {}

    # -- what a test does on the "server" ------------------------------------------------------
    def sha(self, ref):
        return g(self.bare, "rev-parse", ref)

    def commit_files(self, parent, files: dict, msg, name="repo-factory", at=None):
        """A commit on top of ``parent`` with ``files`` changed (text, or None to delete)."""
        idx = self.bare / "idx"
        env = {**who(name, at), "GIT_INDEX_FILE": str(idx)}
        g(self.bare, "read-tree", parent, env=env)
        for path, text in files.items():
            if text is None:
                g(self.bare, "rm", "--cached", "-q", path, env=env)
                continue
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=self.bare,
                input=text,
                capture_output=True,
                text=True,
            ).stdout.strip()
            g(self.bare, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = g(self.bare, "write-tree", env=env)
        idx.unlink()
        return g(self.bare, "commit-tree", tree, "-p", parent, "-m", msg, env=env)

    def open_pr(
        self, n, issue, title, files, kind="bug", test=None, created=None, labels=("repo-factory",)
    ):
        branch = f"repo-factory/{issue}-{re.sub(r'[^a-z0-9]+', '-', title.lower())[:30].strip('-')}"
        created = created or time.time() - 600
        sha = self.commit_files(self.sha("main"), files, f"Fix #{issue}: {title}", at=created - 30)
        g(self.bare, "update-ref", f"refs/heads/{branch}", sha)
        meta = (
            f"{revise.META} issue={issue} kind={kind} miss=verify"
            + (f" test={test}" if test else "")
            + " -->"
        )
        self.issues.setdefault(
            issue,
            {
                "number": issue,
                "title": title,
                "body": "It breaks.",
                "user": {"login": "owner"},
                "author_association": "OWNER",
                "labels": [{"name": x} for x in (kind, "go")],
                "state": "open",
                "created_at": stamp(created - 3600),
                "updated_at": stamp(created),
                "html_url": f"https://github.com/{REPO}/issues/{issue}",
            },
        )
        self.issues[n] = {
            "number": n,
            "title": f"Fix #{issue}: {title}",
            "user": {"login": "owner"},
            "author_association": "OWNER",
            "state": "open",
            "draft": False,
            "body": f"Fix #{issue}: {title}\n\nFixes #{issue}\n\n{meta}\n",
            "labels": [{"name": x} for x in (*labels, kind)],
            "pull_request": {},
            "created_at": stamp(created),
            "updated_at": stamp(created),
            "html_url": f"https://github.com/{REPO}/pull/{n}",
            "branch": branch,
        }
        return branch

    def head_of(self, n):
        return self.sha(f"refs/heads/{self.issues[n]['branch']}")

    def mergeable(self, n):
        p = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", "main", self.head_of(n)],
            cwd=self.bare,
            capture_output=True,
            text=True,
        )
        return p.returncode == 0, p.stdout.split("\n")[0]

    def squash(self, n, sha=None):
        """The owner's (or repo-factory's) squash merge of pull request ``n``."""
        ok, tree = self.mergeable(n)
        assert ok, f"#{n} conflicts"
        head = self.head_of(n)
        assert sha is None or sha == head, "the sha guard"
        new = g(
            self.bare,
            "commit-tree",
            tree,
            "-p",
            "main",
            "-m",
            f"{self.issues[n]['title']} (#{n})",
            env=who("owner"),
        )
        g(self.bare, "update-ref", "refs/heads/main", new)
        self.squashed[new] = n
        self.merges.append(n)
        self.issues[n]["state"] = "closed"
        self.issues[n]["merged"] = True
        for m in re.findall(r"Fixes #(\d+)", self.issues[n]["body"]):
            self.issues[int(m)]["state"] = "closed"
        return new

    def owner_push(self, n):
        """The owner pushes a commit of their own to the pull request's branch."""
        sha = self.commit_files(
            self.head_of(n), {"OWNER.md": "mine\n"}, "my own change", name="owner", at=time.time()
        )
        g(self.bare, "update-ref", f"refs/heads/{self.issues[n]['branch']}", sha)
        return sha

    # -- the REST calls ------------------------------------------------------------------------
    def commit_json(self, sha):
        parents = g(self.bare, "rev-list", "--parents", "-n1", sha).split()[1:]
        date = g(self.bare, "log", "-1", "--format=%cI", sha)
        name = g(self.bare, "log", "-1", "--format=%cn", sha)
        login = "web-flow" if sha in self.web_flow else name
        return {
            "sha": sha,
            "parents": [{"sha": p} for p in parents],
            "committer": {"login": login},
            "commit": {
                "committer": {"name": name, "date": date},
                "message": g(self.bare, "log", "-1", "--format=%B", sha),
            },
        }

    def pull_json(self, n):
        i = self.issues[n]
        head = self.head_of(n)
        base = g(self.bare, "merge-base", "main", head)
        nums = [
            ln.split("\t")
            for ln in g(self.bare, "diff", "--numstat", base, head).splitlines()
            if ln
        ]
        ok, _ = self.mergeable(n)
        return {
            **i,
            "head": {"sha": head, "ref": i["branch"], "repo": {"full_name": REPO}},
            "base": {"ref": "main"},
            "mergeable": ok,
            "mergeable_state": "clean" if ok else "dirty",
            "additions": sum(int(a) for a, _, _ in nums),
            "deletions": sum(int(d) for _, d, _ in nums),
            "changed_files": len(nums),
        }

    def __call__(self, args, cwd=None):
        a = list(args)
        if a[:2] == ["repo", "clone"]:
            p = subprocess.run(
                ["git", "clone", "-q", str(self.bare), a[3]], capture_output=True, text=True
            )
            return p.returncode, p.stdout, p.stderr
        method = a[a.index("-X") + 1] if "-X" in a else "GET"
        payload = (
            json.loads(Path(a[a.index("--input") + 1]).read_text()) if "--input" in a else None
        )
        path, _, query = a[-1].partition("?")
        base = f"repos/{REPO}"
        rest = path[len(base) :] if path.startswith(base) else None
        ok = lambda obj: (0, json.dumps(obj), "")  # noqa: E731
        if rest is None:
            return super().__call__(args, cwd)
        rec = lambda: self.calls.append((method, a[-1], payload))  # noqa: E731
        raw = any("application/vnd.github.raw" in x for x in a)
        if rest == "/contents/.github/repo-factory.yml" and method == "PUT":
            rec()
            self.put_files.append(payload)
            return ok({"commit": {"sha": "c" * 40}})
        if rest == "/contents/.github/repo-factory.yml" and not raw:
            rec()
            if (
                "ref=repo-factory%2Fpolicy-merge" in query
                or "ref=repo-factory/policy-merge" in query
            ):
                return ok({"sha": "b" * 40}) if self.put_files else (1, "", "HTTP 404: Not Found")
            return ok({"sha": "a" * 40})
        if rest == "/git/refs" and method == "POST":
            rec()
            if payload["ref"] in self.refs:
                return 1, "", "HTTP 422: Reference already exists"
            self.refs[payload["ref"]] = payload["sha"]
            return ok({"ref": payload["ref"]})
        m = re.fullmatch(r"/git/refs/(heads/.+)", rest)
        if m and method == "PATCH":
            rec()
            self.refs["refs/" + unquote(m.group(1))] = payload["sha"]
            return ok({})
        if rest == "/pulls" and method == "GET" and "head=" in query:
            rec()
            want = unquote(query.split("head=")[1].split("&")[0]).split(":", 1)[1]
            return ok(
                [
                    i
                    for i in self.issues.values()
                    if i.get("branch") == want and i["state"] == "open"
                ]
            )
        if rest == "/pulls" and method == "POST":
            rec()
            n = max(self.issues) + 1 if self.issues else 1
            self.issues[n] = {
                "number": n,
                "title": payload["title"],
                "body": payload["body"],
                "user": {"login": "owner"},
                "author_association": "OWNER",
                "labels": [],
                "state": "open",
                "pull_request": {},
                "created_at": stamp(time.time()),
                "updated_at": stamp(time.time()),
                "html_url": f"https://github.com/{REPO}/pull/{n}",
                "branch": payload["head"],
            }
            return ok(self.issues[n])
        if rest == "/commits/main" and "-i" in a:
            sha = self.sha("main")
            if any(x == f'If-None-Match: "{sha}"' for x in a):
                return 1, "", "HTTP 304"
            return 0, f'HTTP/2.0 200 OK\nEtag: "{sha}"\n\n{sha}', ""
        if rest == "/commits/main":
            rec()
            return ok(self.commit_json(self.sha("main")))
        m = re.fullmatch(r"/commits/([0-9a-f]{40})/pulls", rest)
        if m:
            rec()
            n = self.squashed.get(m.group(1))
            return ok([self.issues[n]] if n else [])
        m = re.fullmatch(r"/commits/([0-9a-f]{40})", rest)
        if m:
            rec()
            return ok(self.commit_json(m.group(1)))
        m = re.fullmatch(r"/compare/(.+)\.\.\.(.+)", rest)
        if m:
            rec()
            b, h = unquote(m.group(1)), unquote(m.group(2))
            cs = g(self.bare, "rev-list", "--reverse", f"{b}..{h}").split()
            return ok(
                {
                    "behind_by": int(g(self.bare, "rev-list", "--count", f"{h}..{b}")),
                    "ahead_by": len(cs),
                    "commits": [{"sha": c, "commit": {"message": "c"}} for c in cs],
                }
            )
        if rest == "/tags":
            rec()
            tags = g(self.bare, "tag", "--list").split()
            return ok([{"name": t, "commit": {"sha": self.sha(t)}} for t in tags])
        if rest == "/releases" and method == "POST":
            rec()
            g(self.bare, "tag", payload["tag_name"], "main")
            self.releases.append(payload)
            return ok({"tag_name": payload["tag_name"], "html_url": "https://github.com/x"})
        m = re.fullmatch(r"/contents/(.+)", rest)
        if m and unquote(m.group(1)) != ".github/repo-factory.yml":
            p = subprocess.run(
                ["git", "show", f"main:{unquote(m.group(1))}"],
                cwd=self.bare,
                capture_output=True,
                text=True,
            )
            return (0, p.stdout, "") if p.returncode == 0 else (1, "", "HTTP 404: Not Found")
        m = re.fullmatch(r"/commits/([^/]+)/check-runs", rest)
        if m:
            rec()
            st = self.ci.get(m.group(1), self.ci_default)
            if st is None:
                return ok({"total_count": 0, "check_runs": []})
            done = st not in ("pending",)
            return ok(
                {
                    "total_count": 1,
                    "check_runs": [
                        {
                            "name": "test",
                            "status": "completed" if done else "in_progress",
                            "conclusion": st if done else None,
                            "completed_at": stamp(time.time()),
                            "html_url": "https://github.com/x",
                        }
                    ],
                }
            )
        m = re.fullmatch(r"/pulls/(\d+)(/.*)?", rest)
        if m:
            n, sub = int(m.group(1)), m.group(2) or ""
            rec()
            if sub == "" and any("application/vnd.github.diff" in x for x in a):
                h = self.head_of(n)
                return 0, g(self.bare, "diff", g(self.bare, "merge-base", "main", h), h), ""
            if sub == "" and method == "GET":
                return ok(self.pull_json(n))
            if sub == "" and method == "PATCH":
                self.issues[n].update(payload)
                return ok(self.issues[n])
            if sub == "/files":
                h = self.head_of(n)
                nums = [
                    ln.split("\t")
                    for ln in g(
                        self.bare, "diff", "--numstat", g(self.bare, "merge-base", "main", h), h
                    ).splitlines()
                    if ln
                ]
                return ok([{"filename": f, "changes": int(a) + int(d)} for a, d, f in nums])
            if sub == "/reviews":
                return ok(self.review_list.get(n, []))
            if sub == "/commits":
                h = self.head_of(n)
                return ok(
                    [
                        self.commit_json(c)
                        for c in g(self.bare, "rev-list", "--reverse", f"main..{h}").split()
                    ]
                )
            if sub == "/update-branch" and method == "PUT":
                head = self.head_of(n)
                if payload.get("expected_head_sha") != head:
                    return 1, "", "HTTP 422: expected head sha didn't match current head ref."
                okm, tree = self.mergeable(n)
                if not okm:
                    return 1, "", "HTTP 422: merge conflict between base and head"
                new = g(
                    self.bare,
                    "commit-tree",
                    tree,
                    "-p",
                    head,
                    "-p",
                    "main",
                    "-m",
                    "Merge branch 'main' into " + self.issues[n]["branch"],
                    env=who("GitHub"),
                )
                g(self.bare, "update-ref", f"refs/heads/{self.issues[n]['branch']}", new)
                self.web_flow.add(new)
                self.updates.append(n)
                return ok({"message": "Updating pull request branch."})
            if sub == "/merge" and method == "PUT":
                if not self.mergeable(n)[0] or payload.get("sha") not in (None, self.head_of(n)):
                    return 1, "", "HTTP 405: Pull Request is not mergeable"
                self.squash(n, payload.get("sha"))
                return ok({"merged": True})
        return super().__call__(args, cwd)


# -- the fixture product and its four pull requests -----------------------------------------------
def make_bare(tmp: Path) -> Path:
    src, bare = tmp / "product", tmp / "origin.git"
    for rel, text in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run(["git", *OWNER, "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", *OWNER, "commit", "-q", "-m", "Plots 0.1.0"],
        cwd=src,
        check=True,
        env={**os.environ, **who("owner", time.time() - 86400)},
    )
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    g(bare, "tag", "v0.1.0", "main")
    return bare


APP = FILES["app.py"]
A_404 = APP.replace("        return 500", "        return 404")  # PR #21
C_EMPTY = APP.replace(
    "    return 200 if found else 404",  # PR #23: conflicts
    "    return 200 if found is not None else 404",
)
EMPTY_TEST = (
    '"""A plot with an empty name is still a plot."""\nimport app\n\n\n'
    "def test_empty_named_plot_is_found(monkeypatch):\n"
    '    monkeypatch.setitem(app.PLOTS, 2, "")\n    st = app.status("2")\n'
    '    assert st == 200, f"empty-named plot answered {st}"\n'
)


def ci_green(**over):
    return policy.render({"status": "maintained", **({"merge": over} if over else {})})


@pytest.fixture
def world(tmp_path):
    bare = make_bare(tmp_path)
    fake = GitHub(bare, policy.render({"status": "maintained"}))
    lp = make_loop(tmp_path, maintain={"revise_command": [sys.executable, "-c", "pass"]})
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    return lp, fake


def four(fake):
    fake.open_pr(21, 11, "A huge id is a 500", {"app.py": A_404})
    fake.open_pr(22, 12, "Document the export", {"docs/export.md": "# Export\n"}, kind="docs")
    fake.open_pr(
        23,
        13,
        "An empty-named plot is a 404",
        {"app.py": C_EMPTY, CONFLICT_TEST: EMPTY_TEST},
        test=CONFLICT_TEST,
    )
    fake.open_pr(
        24,
        14,
        "The changelog has no date",
        {"CHANGELOG.md": FILES["CHANGELOG.md"].replace("## 0.1.0", "## 0.1.0 (today)")},
    )


def tick(lp, at=None):
    return poll.maintain_tick(lp, now=at or time.time(), model=None)


def mine(out):
    return next(x for x in out["repos"] if x["repo"] == REPO)


def comments_with(fake, n, marker):
    return [c for c in fake.comments.get(n, []) if marker in c["body"]]


def refresh_run(lp, issue, pr, harness):
    return revise.Fix(
        lp,
        REPO,
        issue,
        refresh=pr,
        harness=harness,
        tests=pytest_runner,
        check=lambda root: {"ok": True, "summary": "passed: 1 commands, 0 problems", "output": ""},
        venv="/nonexistent",
        exam=no_asset,
    ).run()


def redo_empty(ws, p):
    """The refresh's sessions: the reproducing test, then the fix, on the new main."""
    t = ws / CONFLICT_TEST
    if not t.exists():
        t.write_text(EMPTY_TEST)
        note(
            ws,
            "reproduce.json",
            {
                "test": CONFLICT_TEST,
                "existing": False,
                "expect": "empty-named plot answered 404",
                "why": "x",
            },
        )
        return
    app = ws / "app.py"
    app.write_text(
        app.read_text().replace(
            "    return 200 if found else 404", "    return 200 if found is not None else 404"
        )
    )
    note(
        ws,
        "fix.json",
        {
            "what": "status() finds a plot whose name is empty.",
            "why": "an empty name is falsy",
            "miss": "verify",
        },
    )


# -- 1. kept current, with or without auto-merge ----------------------------------------------------
def test_four_prs_the_owner_merges_one_the_rest_are_brought_onto_main(world):
    lp, fake = world
    four(fake)
    out = tick(lp)
    assert not mine(out).get("current"), "nothing moved yet: nothing to do"
    assert fake.updates == [] and not fake.comments

    fake.squash(21)  # the owner merges one
    out = mine(tick(lp))
    cur = out["current"]
    assert sorted(cur["updated"]) == [22, 24], cur  # GitHub's update-branch, no conflict
    assert cur["refreshing"] == [23], cur  # the conflict: re-done on the new main
    for n in (22, 24):
        said = comments_with(fake, n, current.CURRENT)
        assert len(said) == 1 and "Updated onto `main` after #21 merged" in said[0]["body"]
        assert g(fake.bare, "rev-list", "--count", f"{fake.head_of(n)}..main") == "0"
    said = comments_with(fake, 23, current.CURRENT)
    assert len(said) == 1 and "refresh 1 of 2; not counted as an attempt" in said[0]["body"]
    rec = Cache(lp.cfg.home).repo(REPO)
    assert rec["revises"]["13"]["refresh"] == 23
    assert "--refresh" in revise.command(lp.cfg, REPO, 13, 23)

    # the refresh itself (the poll started it detached; here it runs in-process)
    before = fake.head_of(23)
    h = Scripted(redo_empty, redo_empty)
    res = refresh_run(lp, 13, 23, h)
    assert res["outcome"] == "refreshed", (res.get("stage"), res.get("why"))
    assert res["head"] == fake.head_of(23) != before, "pushed to the same branch"
    assert fake.mergeable(23)[0], "it applies to the new main now"
    assert "pull request #23" in h.prompts[1] and "<<<document" in h.prompts[1]
    assert fake.issues[23]["state"] == "open" and fake.issues[23]["number"] == 23
    assert (
        not comments_with(fake, 13, "result=failed")
        and revise.attempts(fake.comments.get(13, [])) == 0
    ), "a refresh is not an attempt"
    assert "claim:repo-factory" not in fake.labels_of(13)
    said = comments_with(fake, 23, current.CURRENT)
    assert len(said) == 1, "edited in place, never a second comment"
    assert "Re-done on the new `main` (refresh 1 of 2" in said[0]["body"]

    out = mine(tick(lp))
    assert [e["outcome"] for e in out["current"]["ended"]] == ["refreshed"]
    book = Cache(lp.cfg.home).repo(REPO)["prs"]
    assert {k: v["state"] for k, v in book.items()} == {
        "22": "current",
        "23": "current",
        "24": "current",
    }
    for n in (22, 23, 24):  # all go green
        assert automerge.checks(github.Repo(lp.gh, REPO), fake.head_of(n))["state"] == "green"
        assert fake.mergeable(n)[0]
        assert g(fake.bare, "rev-list", "--count", f"{fake.head_of(n)}..main") == "0"
    assert fake.merges == [21], "auto-merge is off: repo-factory merged nothing"
    assert tick(lp) and fake.updates == [22, 24], "an up-to-date one is left alone"


# Beliefline's four open fix PRs on 2026-10-16 (#11, #13, #14, #15): each added its line at the top
# of CHANGELOG's Unreleased, so each merge left the others conflicting in that one file, and each
# conflict cost a whole model re-do, capped at maintain.refresh_max (AU3 finding 1).
BELIEFLINE_LINES = {
    11: "- README: health endpoint, how to stop the service, `BELIEFLINE_ACCESS_LOG`, "
    "Troubleshooting and Limitations.",
    13: "- Owner endpoints return 400 with field and detail, not 500, for an out-of-range time.",
    14: "- `/v1/health` now reads the database and reports `schema_version`.",
    15: "- The package version now matches the newest release, 0.1.1.",
}


def with_line(line):
    return FILES["CHANGELOG.md"].replace("## Unreleased\n", f"## Unreleased\n\n{line}\n")


def test_changelog_only_conflicts_are_merged_by_git_not_redone(world):
    lp, fake = world
    for k, (n, line) in enumerate(BELIEFLINE_LINES.items()):
        fake.open_pr(
            30 + k, n, f"Fix {n}", {"CHANGELOG.md": with_line(line), f"src/fix{n}.py": f"N = {n}\n"}
        )
    tick(lp)
    prs = [30, 31, 32, 33]
    for merged in prs[:-1]:  # the owner merges them one by one
        fake.squash(merged)
        open_ = [n for n in prs if n > merged]
        assert all(not fake.mergeable(n)[0] for n in open_), "they conflict in CHANGELOG.md"
        out = mine(tick(lp))
        cur = out["current"]
        assert sorted(cur["updated"]) == open_ and not cur["refreshing"], cur
        assert all(fake.mergeable(n)[0] for n in open_)
        rec = Cache(lp.cfg.home).repo(REPO)
        assert not rec["revises"], "no model re-do"
        assert all(rec["prs"][str(n)]["refreshes"] == 0 for n in open_)
        assert all("needs-you" not in fake.labels_of(n) for n in open_)
    fake.squash(prs[-1])
    log = g(fake.bare, "show", "main:CHANGELOG.md")
    assert all(line in log for line in BELIEFLINE_LINES.values()), log
    assert "<<<<<<<" not in log
    said = comments_with(fake, 33, current.CURRENT)
    assert len(said) == 1 and "keeping both sides' lines in CHANGELOG.md" in said[0]["body"]
    # repo-factory's own merge commits are its known head: the next poll leaves the PR alone
    assert mine(tick(lp)).get("current", {}).get("left") in (None, [])


def test_a_code_conflict_is_never_merged_by_git(world):
    """CHANGELOG.md is the only file git resolves; a conflict in code still goes to a refresh,
    and the branch is left exactly as it was."""
    lp, fake = world
    four(fake)
    tick(lp)
    before = fake.head_of(23)
    fake.squash(21)
    out = mine(tick(lp))
    assert out["current"]["refreshing"] == [23] and fake.head_of(23) == before


def test_fix_prs_are_never_asked_for_a_changelog_line():
    from repo_factory.maintain import TEMPLATES

    for p in (
        TEMPLATES / "AGENTS.md.tmpl",
        TEMPLATES / "pull_request_template.md",
        TEMPLATES / "CHANGELOG.md.tmpl",
        revise.POLICY / "prompts" / "fix.md",
    ):
        text = p.read_text()
        assert "line under *Unreleased*" not in text and "under `## Unreleased`" not in text, p


def test_hold_needs_you_and_an_owner_commit_are_never_touched(world):
    lp, fake = world
    four(fake)
    fake.issues[22]["labels"].append({"name": "hold"})
    fake.issues[23]["labels"].append({"name": "needs-you"})
    tick(lp)  # repo-factory learns its own heads
    fake.owner_push(24)
    fake.squash(21)
    out = mine(tick(lp))
    assert fake.updates == [] and not out.get("current", {}).get("refreshing")
    assert out["current"]["left"] == [24]
    assert not fake.comments.get(22) and not fake.comments.get(23) and not fake.comments.get(24)
    book = Cache(lp.cfg.home).repo(REPO)["prs"]
    assert (book["22"]["state"], book["23"]["state"], book["24"]["state"]) == (
        "hold",
        "needs-you",
        "touched",
    )


def test_an_older_pr_with_a_later_commit_is_the_owners(world):
    """A pull request repo-factory knows nothing of yet: a commit made after it was opened is not
    repo-factory's (repo-factory pushes before it opens one)."""
    lp, fake = world
    four(fake)
    fake.owner_push(22)
    fake.squash(21)
    tick(lp)
    assert 22 not in fake.updates and 24 in fake.updates


def test_the_refresh_cap_then_needs_you(world):
    lp, fake = world
    lp.cfg.data["maintain"]["refresh_max"] = 1
    four(fake)
    tick(lp)
    fake.squash(21)
    tick(lp)  # refresh 1 of 1 starts
    refresh_run(lp, 13, 23, Scripted(redo_empty, redo_empty))
    tick(lp)
    # main moves again under it, with another conflict on the same line
    fake.open_pr(
        25,
        15,
        "Another change there",
        {
            "app.py": A_404.replace(
                "    return 200 if found else 404", "    return 200 if found != None else 404"
            )
        },
    )
    fake.squash(25)
    out = mine(tick(lp))
    assert out["current"]["needs_you"] == [23]
    assert "needs-you" in fake.labels_of(23)
    said = comments_with(fake, 23, current.CURRENT)
    assert (
        len(said) == 1
        and "re-done it 1 time" in said[0]["body"]
        and "`maintain.refresh_max: 1`" in said[0]["body"]
    )
    assert tick(lp) and fake.labels_of(23).count("needs-you") == 1


def test_a_refresh_that_fails_says_why_and_hands_it_over(world):
    lp, fake = world
    four(fake)
    tick(lp)
    fake.squash(21)
    tick(lp)
    res = refresh_run(
        lp,
        13,
        23,
        Scripted(
            lambda ws, p: note(
                ws,
                "reproduce.json",
                {"test": None, "why": "it no longer happens the way the report says"},
            )
        ),
    )
    assert res["outcome"] == "cannot_reproduce"
    assert "needs-you" in fake.labels_of(23) and fake.issues[23]["state"] == "open"
    said = comments_with(fake, 23, current.CURRENT)
    assert len(said) == 1 and "**reproduce** stage" in said[0]["body"]
    assert "cannot-reproduce" not in fake.labels_of(13), "the issue is left as it was"
    assert revise.attempts(fake.comments.get(13, [])) == 0


def test_a_fix_already_on_main_closes_its_pr(world):
    lp, fake = world
    four(fake)
    tick(lp)
    # the owner fixed the empty name by hand, conflicting with #23 but doing the same thing
    fake.open_pr(
        26,
        16,
        "Empty names",
        {
            "app.py": APP.replace(
                "    return 200 if found else 404", "    return 404 if found is None else 200"
            )
        },
    )
    fake.squash(26)
    tick(lp)
    h = Scripted()
    res = refresh_run(lp, 13, 23, h)
    assert res["outcome"] == "already" and not h.prompts, "nothing is spent"
    assert fake.issues[23]["state"] == "closed" and not fake.issues[23].get("merged")
    assert fake.issues[13]["state"] == "closed"
    said = comments_with(fake, 23, current.CURRENT)
    assert "Closed by repo-factory: this fix is on `main` already" in said[0]["body"]
    assert any("Fixed on `main` already" in c["body"] for c in fake.comments[13])


# -- 2. merge.factory_patch: ci-green ---------------------------------------------------------------
def test_ci_green_merges_one_per_poll_oldest_first_with_one_release(world):
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green")
    fake.open_pr(22, 12, "Document the export", {"docs/export.md": "# Export\n"}, kind="docs")
    fake.open_pr(
        23,
        13,
        "The changelog has no date",
        {"CHANGELOG.md": FILES["CHANGELOG.md"].replace("## 0.1.0", "## 0.1.0 (today)")},
    )
    fake.open_pr(24, 14, "A huge id is a 500", {"app.py": A_404})
    seen = []
    for k in range(4):
        out = mine(tick(lp))
        seen.append(fake.merges[-1] if fake.merges else None)
        if k < 2:
            assert out["release"].get("waits"), out.get("release")
    assert fake.merges == [22, 23, 24], "oldest first, one per poll"
    assert seen[:3] == [22, 23, 24]
    assert len(fake.releases) == 1 and fake.releases[0]["tag_name"] == "v0.1.1"
    assert 23 in fake.updates and 24 in fake.updates, "the rest were brought up to date"
    said = comments_with(fake, 22, automerge.MERGED)
    assert (
        len(said) == 1
        and "`merge.factory_patch: ci-green`" in said[0]["body"]
        and "`merge.kinds: [bug, docs]`" in said[0]["body"]
    )
    q = Cache(lp.cfg.home).repo(REPO)["queue"]
    assert q["on"] and q["line"] == "no fix waiting"


def test_the_queue_line_names_the_next_merge(world):
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green")
    four(fake)
    fake.ci_default = "pending"
    tick(lp)
    q = Cache(lp.cfg.home).repo(REPO)["queue"]
    assert q["line"] == "4 fixes waiting for CI · next merge after #21 is green"
    assert not fake.merges


REFUSALS = {
    "no CI": (lambda f: setattr(f, "ci_default", None), {}, "no CI ran on it"),
    "red": (lambda f: setattr(f, "ci_default", "failure"), {}, "CI is not green: test (failure)"),
    "cancelled": (lambda f: setattr(f, "ci_default", "cancelled"), {}, "test (cancelled)"),
    "over max_lines": (None, {"max_lines": 1}, "over `merge.max_lines: 1`"),
    "feature": (
        lambda f: f.issues[22]["labels"].append({"name": "feature"}),
        {},
        "a `feature`: repo-factory never merges one",
    ),
    "security not in kinds": (
        lambda f: f.issues[22]["labels"].append({"name": "security"}),
        {},
        "a `security` fix, and `merge.kinds` is bug, docs",
    ),
    "hold": (lambda f: f.issues[22]["labels"].append({"name": "hold"}), {}, "labelled `hold`"),
    "owner commit": (lambda f: f.owner_push(22), {}, "someone else pushed commits to it"),
    "changes requested": (
        lambda f: f.review_list.__setitem__(
            22,
            [
                {
                    "user": {"login": "owner"},
                    "state": "CHANGES_REQUESTED",
                    "submitted_at": stamp(time.time()),
                }
            ],
        ),
        {},
        "changes requested by owner",
    ),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_each_refusal_waits_for_the_owner(world, case):
    lp, fake = world
    setup, over, why = REFUSALS[case]
    fake.policy_text = ci_green(factory_patch="ci-green", **over)
    fake.open_pr(22, 12, "A huge id is a 500", {"app.py": A_404})
    if setup:
        setup(fake)
    tick(lp)
    assert fake.merges == [], case
    item = Cache(lp.cfg.home).repo(REPO)["queue"]["items"][0]
    assert item["verdict"] == "refused" and why in item["why"], item


def test_github_paths_and_lockfiles_are_never_merged(world):
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green")
    fake.open_pr(22, 12, "CI", {".github/workflows/ci.yml": "name: ci2\n"})
    fake.open_pr(23, 13, "Lock", {"uv.lock": "version = 1\n"})
    fake.open_pr(24, 14, "Licence", {"LICENSE": "MIT\n"})
    tick(lp)
    items = Cache(lp.cfg.home).repo(REPO)["queue"]["items"]
    assert [x["verdict"] for x in items] == ["refused"] * 3 and not fake.merges
    assert (
        "under .github/" in items[0]["why"]
        and "a lockfile" in items[1]["why"]
        and "licence or notice" in items[2]["why"]
    )


def test_security_merges_once_the_owner_adds_it_and_pending_waits(world):
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green", kinds=["bug", "docs", "security"])
    fake.open_pr(22, 12, "A huge id is a 500", {"app.py": A_404}, kind="security")
    fake.ci_default = "pending"
    tick(lp)
    assert not fake.merges
    assert Cache(lp.cfg.home).repo(REPO)["queue"]["items"][0]["why"] == "waiting for CI"
    fake.ci_default = "success"
    tick(lp)
    assert fake.merges == [22]


def test_off_by_default(world):
    lp, fake = world
    fake.open_pr(22, 12, "A huge id is a 500", {"app.py": A_404})
    tick(lp)
    assert not fake.merges and Cache(lp.cfg.home).repo(REPO)["queue"] == {
        "on": False,
        "kinds": ["bug", "docs"],
        "max_lines": 80,
        "items": [],
        "merged": None,
        "at": Cache(lp.cfg.home).repo(REPO)["queue"]["at"],
    }


# -- the policy ----------------------------------------------------------------------------------
def test_merge_kinds_in_the_policy():
    p = policy.parse(policy.render({}))
    assert p["merge"]["kinds"] == ["bug", "docs"] and p["merge"]["factory_patch"] == "never"
    p = policy.parse(
        policy.render(
            {
                "merge": {
                    "factory_patch": "ci-green",
                    "kinds": ["bug", "docs", "security", "dependency"],
                }
            }
        )
    )
    assert p["merge"]["kinds"] == ["bug", "docs", "security", "dependency"]
    with pytest.raises(policy.PolicyError, match="never merges a feature"):
        policy.parse("merge:\n  kinds: [bug, feature]\n")
    with pytest.raises(policy.PolicyError, match="merge.kinds must be a list"):
        policy.parse("merge:\n  kinds: [chore]\n")
    text = policy.render({})
    assert "Never merged by repo-factory, whatever this says: a" in text and "lockfile" in text


def test_the_fix_comment_says_who_merges(world):
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green")
    fake.open_pr(22, 12, "x", {"docs/x.md": "x\n"}, kind="docs")
    f = revise.Fix(lp, REPO, 12)
    assert "repo-factory merges it once every check on it is green" in f.who_merges()
    f.kind = "feature"
    assert "nothing merges by itself" in f.who_merges()
    fake.policy_text = ci_green()
    assert "nothing merges by itself" in revise.Fix(lp, REPO, 12).who_merges()


# -- 3. the owner's switch: one policy PR on .github/repo-factory.yml -------------------------------------
OLD_FILE = """# .github/repo-factory.yml (an older template, without merge.kinds)
repo-factory: 1
status: maintained

merge:
  factory_patch: never          # never | ci-green: repo-factory merges its own PR for a `bug`/`docs`
                              #   fix when CI is green and the diff is under merge.max_lines
  max_lines: 80
  dependabot: patch           # mine

release:
  on_merge: patch
"""


def test_the_policy_edit_changes_only_the_merge_lines():
    from repo_factory.maintain import mergepolicy as M

    new = M.proposed(OLD_FILE, True, ["bug", "docs", "security"])
    p = policy.parse(new)
    assert p["merge"]["factory_patch"] == "ci-green"
    assert p["merge"]["kinds"] == ["bug", "docs", "security"]
    assert p["merge"]["dependabot"] == "patch" and p["release"]["on_merge"] == "patch"
    old, now = OLD_FILE.splitlines(), new.splitlines()
    kinds_line = "  kinds: [bug, docs, security]         " + M.KIND_NOTE
    assert [x for x in now if x not in old] == [
        "  factory_patch: ci-green          # never | ci-green: repo-factory merges its own PR for a "
        "`bug`/`docs`",
        kinds_line,
    ]
    assert [x for x in old if x not in now] == [old[5]], "one line changed, one added"
    assert now.index(kinds_line) == 7, "after factory_patch and its comment lines"
    # the current template, and back off again
    tpl = policy.render({"status": "maintained"})
    on = M.proposed(tpl, True, ["bug", "docs"])
    assert policy.parse(on)["merge"]["factory_patch"] == "ci-green"
    assert len(on.splitlines()) == len(tpl.splitlines()), "both lines rewritten in place"
    assert policy.parse(M.proposed(on, False, ["bug", "docs"]))["merge"]["factory_patch"] == "never"
    with pytest.raises(M.PolicyPRError, match="never merges a feature"):
        M.kinds_of(["bug", "feature"])
    with pytest.raises(M.PolicyPRError, match="backfill PR first"):
        M.proposed(None, True, ["bug"])
    with pytest.raises(M.PolicyPRError, match="does not parse"):
        M.proposed("merge:\n  factory_patch: sometimes\n", True, ["bug"])


def test_the_policy_pr_opens_once_on_factory_branch_and_says_what_never_merges(world):
    import base64

    from repo_factory.maintain import mergepolicy as M

    lp, fake = world
    fake.policy_text = OLD_FILE
    res = M.run(lp, REPO, True, ["docs", "bug"])
    assert res["changed"] and res["kinds"] == ["bug", "docs"]
    pr = fake.issues[res["pr"]["number"]]
    assert pr["branch"] == M.BRANCH == "repo-factory/policy-merge"
    assert pr["title"] == "Policy: repo-factory merges its own fixes when CI is green (bug, docs)"
    assert "**repo-factory never merges, whatever this file says:** a `feature`" in pr["body"]
    assert "repo-factory never merges this pull request: you do" in pr["body"]
    assert fake.refs == {"refs/heads/repo-factory/policy-merge": fake.sha("main")}
    put = fake.put_files[-1]
    assert put["branch"] == "repo-factory/policy-merge" and "sha" not in put
    text = base64.b64decode(put["content"]).decode()
    assert policy.parse(text)["merge"]["factory_patch"] == "ci-green"
    assert "repo-factory" in fake.labels_of(pr["number"])
    # pressed again: the same PR, brought up to date (the branch moved, the file's sha given)
    again = M.run(lp, REPO, True, ["bug"])
    assert again["pr"]["number"] == pr["number"] and fake.put_files[-1]["sha"] == "b" * 40
    assert sum(1 for i in fake.issues.values() if i.get("branch") == M.BRANCH) == 1
    for method, path, payload in [c for c in fake.calls if c[0] != "GET"]:
        assert (
            "repo-factory/policy-merge" in json.dumps(payload) + path
            or "/pulls" in path
            or "/issues/" in path
        ), ("it writes only to its own branch", method, path)
    # what main says already: nothing to propose
    fake.policy_text = M.proposed(OLD_FILE, True, ["bug"])
    assert M.run(lp, REPO, True, ["bug"])["changed"] is False


def test_the_switch_is_a_confirm_class_owner_action(world):
    from repo_factory import registry as R
    from repo_factory.maintain import mergepolicy as M
    from repo_factory.ui import api

    lp, fake = world
    a = R.ACTIONS["maintain_merge_policy"]
    assert a.click == "confirm" and "maintain_merge_policy" in R.OWNER_KINDS
    assert "Never a feature" in a.says
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    app.loop = lambda: lp
    with pytest.raises(api.HttpError, match="never merges a feature"):
        app.maintain_merge_policy(REPO, True, ["feature"])
    with pytest.raises(api.HttpError, match="not a maintained repository"):
        app.maintain_merge_policy("o/other", True, ["bug"])
    lp.cfg.data["maintain"]["policy_command"] = [sys.executable, "-c", "pass"]
    r = app.maintain_merge_policy(REPO, True, ["bug", "docs"])
    assert "repo-factory never merges it itself" in r["say"]
    st = M.read_state(lp.cfg.home, REPO)
    assert st["state"] == "running" and st["on"] and st["kinds"] == ["bug", "docs"]
    assert M.command(lp.cfg, REPO, True, ["bug"])[-5:-2] == ["--on", "--kinds", "bug"]


def test_hands_off_is_gone_and_a_saved_one_reads_as_quiet():
    """AU3 finding 9: hands-off decided nothing quiet did not; it only showed a button."""
    from repo_factory.projects import changes
    from repo_factory.settings import write

    assert "hands-off" not in changes.PRESETS
    assert "hands-off" not in write.PROJECTS["autonomy.preset"][1]
    cfg = {"projects": {"autonomy": {"preset": "hands-off"}}}
    assert changes.autonomy(cfg) == changes.PRESETS["quiet"] == changes.autonomy({})


def test_max_lines_counts_what_is_not_a_test(world):
    """Beliefline #11: a docs fix of 20 README lines and the 67-line test the protocol demands
    waited for the owner under max_lines 80."""
    lp, fake = world
    fake.policy_text = ci_green(factory_patch="ci-green")
    readme = FILES.get("README.md", "# Plots\n") + "".join(f"- line {i}\n" for i in range(20))
    test = '"""The README documents the stop command."""\n' + "".join(
        f"def test_{i}():\n    assert True\n\n\n" for i in range(17)
    )
    fake.open_pr(
        22,
        12,
        "Document the stop command",
        {"README.md": readme, "tests/acceptance/test_stop.py": test},
        kind="docs",
    )
    assert sum(1 for _ in test.splitlines()) > 60
    tick(lp)
    assert fake.merges == [22], Cache(lp.cfg.home).repo(REPO)["queue"]


def test_the_release_waits_for_the_queue_with_no_clock():
    q = {"on": True, "merged": 21, "items": [{"verdict": "wait", "number": 22}]}
    rec = {}
    assert automerge.release_waits(q, rec, 1000.0)
    q2 = {"on": True, "merged": None, "items": [{"verdict": "wait", "number": 22}]}
    assert automerge.release_waits(q2, rec, 1000.0 + 7 * 3600), "no 6-hour cut any more"
    assert not automerge.release_waits({"on": True, "items": []}, rec, 1000.0 + 8 * 3600)
    assert "release_wait" not in rec


def test_two_conflicts_one_refresh_at_a_time_the_other_says_it_waits(world):
    lp, fake = world
    fake.open_pr(22, 12, "Empty names", {"app.py": C_EMPTY}, test=CONFLICT_TEST)
    fake.open_pr(
        23,
        13,
        "Other empty names",
        {
            "app.py": APP.replace(
                "    return 200 if found else 404", "    return 200 if found != None else 404"
            )
        },
    )
    fake.open_pr(21, 11, "A huge id is a 500", {"app.py": A_404})
    tick(lp)
    fake.squash(21)
    out = mine(tick(lp))
    assert out["current"]["refreshing"] == [22], "oldest first"
    said = comments_with(fake, 23, current.CURRENT)
    assert (
        len(said) == 1
        and "after #21 merged" in said[0]["body"]
        and "when a fix slot is free" in said[0]["body"]
    )
    tick(lp)
    assert len(comments_with(fake, 23, current.CURRENT)) == 1, "said once, not every poll"
    assert comments_with(fake, 23, current.CURRENT)[0]["body"] == said[0]["body"]
