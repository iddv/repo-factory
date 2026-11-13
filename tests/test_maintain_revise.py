"""Maintenance's fixing half (MAINTAIN.md §11, M3): the queue, claim and lease, the reproduce
stage, the fix on a fixture product, the scope check, the pull request, failures and
``needs-you``, the release on merge with its lesson label, Cut a ticket, the network
allowlist, and the fix rows in the UI. A fake GitHub behind a fake ``gh`` (a local bare repo
is the origin), a scripted session harness, fixture issues: no model, no network."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from repo_factory.gh import Gh
from repo_factory.harness import netproxy
from repo_factory.harness.base import HarnessResult
from repo_factory.maintain import github, policy, poll, release, revise
from repo_factory.ui import api

from .loop_common import make_loop
from .test_maintain_triage import NOW, FakeGitHub, stamp

pytestmark = pytest.mark.usefixtures("maintenance_on")

REPO = "o/plots"
TEST = "tests/acceptance/test_huge_numeric_id_gets_404.py"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost"]


# -- a fixture product with one known open item -------------------------------------------------
APP = '''"""Plots: the HTTP answer for a plot id."""
PLOTS = {1: "A-01"}


def lookup(id_text):
    n = int(id_text)
    if n > 2 ** 63:
        raise OverflowError("id too large for the store")
    return PLOTS.get(n)


def status(id_text):
    try:
        found = lookup(id_text)
    except OverflowError:
        return 500
    return 200 if found else 404
'''
MARKED = '''"""A huge numeric id in a URL gets a 404, not a server error (SECURITY.md S5)."""
from app import status


def test_huge_id_is_404():
    st = status("9" * 30)
    assert st == 404, f"huge id answered {st}"


# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402
_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason='Known security issue (low): huge id')]
'''
FILES = {
    "app.py": APP,
    "tests/test_app.py": "from app import status\n\n\ndef test_known_plot():\n"
    "    assert status('1') == 200\n",
    "tests/acceptance/conftest.py": "import sys\nfrom pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).parents[2]))\n",
    "tests/acceptance/test_unknown_plot_is_404.py": '"""An unknown plot is a 404."""\nfrom app import status\n\n\n'
    "def test_unknown():\n    assert status('7') == 404\n",
    TEST: MARKED,
    "tests/acceptance/README.md": "# Acceptance tests\n\nRun them with `python -m pytest tests/acceptance`.\n\n"
    "## Known open items\n\nThese tests are marked as expected failures.\n\n"
    "- `test_huge_numeric_id_gets_404.py`: Known security issue (low): huge id\n",
    "AGENTS.md": "# AGENTS.md — Plots\n\n## Setup, run, test\n\n```sh\n"
    "python -m pip install pytest==8.3.3 && python -m pytest tests/acceptance\n```\n",
    "SECURITY.md": "| S5 | low | A huge numeric id in a URL causes a 500 error | open |\n",
    "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 0.1.0\n\n- First release.\n",
    ".github/workflows/ci.yml": "name: ci\n",
    ".gitignore": "__pycache__/\n*.pyc\n",
}


def make_origin(tmp: Path) -> Path:
    src, bare = tmp / "product", tmp / "origin.git"
    for rel, text in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run([*GIT, "add", "-A"], cwd=src, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "Plots 0.1.0"], cwd=src, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


class FakeHub(FakeGitHub):
    """M2's fake GitHub plus what a fix and a release write: pull requests, issues, tags,
    compare, the pulls of a commit, releases; ``repo clone`` clones the local bare repo."""

    def __init__(self, *a, origin=None, **kw):
        super().__init__(*a, **kw)
        self.origin, self.tag_list, self.compare_commits = origin, [], []
        self.commit_prs, self.releases, self.next_number = {}, [], 100

    def __call__(self, args, cwd=None):
        a = list(args)
        if a[:2] == ["repo", "clone"]:
            p = subprocess.run(
                ["git", "clone", "-q", str(self.origin), a[3]], capture_output=True, text=True
            )
            return p.returncode, p.stdout, p.stderr
        method = a[a.index("-X") + 1] if "-X" in a else "GET"
        payload = (
            json.loads(Path(a[a.index("--input") + 1]).read_text()) if "--input" in a else None
        )
        path = a[-1].split("?")[0]
        base = f"repos/{self.repo}"
        ok = lambda obj: (0, json.dumps(obj), "")  # noqa: E731
        if path in (f"{base}/pulls", f"{base}/issues") and method == "POST":
            self.calls.append((method, a[-1], payload))
            self.next_number += 1
            n = self.next_number
            url = f"https://github.com/{self.repo}/{'pull' if 'pulls' in path else 'issues'}/{n}"
            self.issues[n] = {
                "number": n,
                "title": payload["title"],
                "body": payload["body"],
                "user": {"login": "owner"},
                "author_association": "OWNER",
                "labels": [{"name": x} for x in payload.get("labels") or []],
                "state": "open",
                "created_at": stamp(time.time()),
                "updated_at": stamp(time.time()),
                "html_url": url,
                **(
                    {
                        "pull_request": {},
                        "head": {"ref": payload["head"], "sha": "f00"},
                        "draft": payload.get("draft", False),
                    }
                    if "pulls" in path
                    else {}
                ),
            }
            return ok(self.issues[n])
        if path == f"{base}/tags":
            self.calls.append((method, a[-1], None))
            return ok([{"name": t, "commit": {"sha": "t" + t}} for t in self.tag_list])
        m = re.fullmatch(re.escape(base) + r"/compare/(.+)\.\.\.(.+)", path)
        if m:
            self.calls.append((method, a[-1], None))
            return ok(
                {
                    "commits": [
                        {"sha": s, "commit": {"message": f"commit {s}"}}
                        for s in self.compare_commits
                    ]
                }
            )
        m = re.fullmatch(re.escape(base) + r"/commits/(\w+)/pulls", path)
        if m:
            self.calls.append((method, a[-1], None))
            return ok([self.issues[n] for n in self.commit_prs.get(m.group(1), [])])
        if path == f"{base}/releases" and method == "POST":
            self.calls.append((method, a[-1], payload))
            self.releases.append(payload)
            self.tag_list.append(payload["tag_name"])
            return ok(
                {
                    "tag_name": payload["tag_name"],
                    "html_url": f"https://github.com/{self.repo}/releases/tag/{payload['tag_name']}",
                }
            )
        return super().__call__(args, cwd)

    def comments_of(self, n):
        return [c["body"] for c in self.comments.get(n, [])]


def issue(n, title, labels=(), created=None, assignees=(), body="It breaks."):
    return {
        "number": n,
        "title": title,
        "body": body,
        "user": {"login": "reporter"},
        "author_association": "NONE",
        "labels": [{"name": x} for x in labels],
        "created_at": stamp(created or NOW - 86400),
        "updated_at": stamp(NOW - 600),
        "comments": 0,
        "assignees": [{"login": x} for x in assignees],
    }


S5 = issue(14, "A huge numeric id in a URL gives a 500 instead of 404", ["bug", "p2", "go"])


@pytest.fixture
def world(tmp_path):
    fake = FakeHub(
        [S5], policy_text=policy.render({"status": "maintained"}), origin=make_origin(tmp_path)
    )
    lp = make_loop(tmp_path, maintain={"revise_command": [sys.executable, "-c", "pass"]})
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    return lp, fake, i["id"]


# -- the sessions and the test runner -------------------------------------------------------------
class Scripted:
    """A session harness: each call runs the next step on the workspace (``step(ws, prompt)``)
    and costs ``usd``."""

    name = "scripted"

    def __init__(self, *steps, usd=0.4):
        self.steps, self.usd, self.prompts = list(steps), usd, []

    def run(self, prompt, workspace, timeout_s, **kw):
        import uuid

        uuid.UUID(kw["session"]["id"])  # the CLI refuses anything else
        self.prompts.append(prompt)
        ws = Path(workspace)
        step = self.steps.pop(0) if self.steps else (lambda ws, p: None)
        step(ws, prompt)
        return HarnessResult(
            artifact_dir=ws,
            tool_calls=1,
            input_tokens=0,
            output_tokens=0,
            cost_usd=self.usd,
            transcript_path=ws / "transcript.jsonl",
            exit_ok=True,
            session_id=f"s{len(self.prompts)}",
            model="fake",
        )


def note(ws, name, obj):
    (ws / ".repo-factory").mkdir(exist_ok=True)
    (ws / ".repo-factory" / name).write_text(json.dumps(obj))


def names_test(expect="huge id answered 500", test=TEST, existing=True):
    return lambda ws, p: note(
        ws,
        "reproduce.json",
        {"test": test, "existing": existing, "expect": expect, "why": "500, not 404"},
    )


def fixes(ws, p, extra=None):
    app = ws / "app.py"
    app.write_text(app.read_text().replace("        return 500", "        return 404"))
    sec = ws / "SECURITY.md"
    sec.write_text(sec.read_text().replace("| open |", f"| fixed (`{TEST}`) |"))
    ch = ws / "CHANGELOG.md"
    ch.write_text(
        ch.read_text().replace(
            "## Unreleased\n", "## Unreleased\n\n- A huge id is a 404, not a server error.\n"
        )
    )
    note(
        ws,
        "fix.json",
        {
            "what": "status() answers 404 for an id too large for the store.",
            "why": "lookup() raises OverflowError, which became a 500.",
            "agents_md": "unchanged",
            "miss": "security",
            "flow": "F2",
        },
    )
    if extra:
        extra(ws)


def pytest_runner(root, targets):
    """``run_pytest``'s result shape, from plain pytest on a copy (the real one runs in the
    sandbox with a venv; here: this interpreter, no network either way)."""
    from repo_factory.harness.venv import parse_junit

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "a"
        shutil.copytree(
            root, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".repo-factory")
        )
        junit = work / ".j.xml"
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--rootdir",
                str(work),
                f"--junitxml={junit}",
                "-o",
                "junit_family=xunit1",
                *targets,
            ],
            cwd=work,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        tests, errors, details = parse_junit(junit, work) if junit.exists() else ({}, [], {})
    return {"tests": tests, "errors": errors, "details": details, "returncode": p.returncode}


def no_asset(tree):
    return {"why": "release v0.1.0 has no checks asset", "tag": "v0.1.0", "ran": False}


def fix(lp, harness, check=None, number=14, exam=no_asset):
    return revise.Fix(
        lp,
        REPO,
        number,
        harness=harness,
        tests=pytest_runner,
        check=check
        or (lambda root: {"ok": True, "summary": "passed: 1 commands, 0 problems", "output": ""}),
        venv="/nonexistent",
        exam=exam,
    )


def labels(fake, n):
    return set(fake.labels_of(n))


# -- the queue and the claim -----------------------------------------------------------------------
def item(n, labs, created=0.0, assignees=(), is_pr=False, body=""):
    return {
        "number": n,
        "title": f"t{n}",
        "labels": list(labs),
        "assignees": list(assignees),
        "is_pr": is_pr,
        "created": created,
        "body": body,
        "state": "open",
    }


def test_the_queue_orders_p1_first_and_skips_what_is_taken():
    pol = policy.parse(policy.render({"status": "maintained"}))
    items = [
        item(1, ["bug", "go", "p3"], 1),
        item(2, ["bug", "go", "p1"], 5),
        item(3, ["bug", "go", "p2"], 2),
        item(4, ["bug", "go"], 0),
        item(5, ["bug", "go", "p1"], 9, assignees=["owner"]),  # a person's claim
        item(6, ["bug", "go", "p1", "needs-you"], 0),
        item(7, ["bug", "go", "p1", "claim:agent"], 0),  # live lease
        item(8, ["bug", "go", "p1", "claim:agent"], 3),  # expired lease
        item(9, ["bug", "go", "p1"], 0),  # an open PR fixes it
        item(10, ["feature", "go", "p1"], 0),  # the owner's go
        item(13, ["question", "go", "p1"], 0),  # a decision
        item(11, ["bug", "p1"], 0),  # no go
        item(12, ["bug", "go", "p1", "cannot-reproduce"], 0),
        item(40, ["repo-factory"], is_pr=True, body="Fixes #9"),
    ]
    leases = {7: {"until": NOW + 3600}, 8: {"until": NOW - 60}}
    got = [i["number"] for i in revise.queue(items, pol, NOW, lambda n: leases.get(n))]
    assert got == [10, 8, 2, 3, 1, 4]
    assert revise.queue(items, policy.parse(policy.render({"status": "as-is"})), NOW) == []
    assert revise.queue(items, policy.MISSING, NOW) == [], "a missing policy fixes nothing"


def test_claim_posts_the_lease_and_renew_edits_it(world):
    lp, fake, _ = world
    r = github.Repo(lp.gh, REPO)
    fake.comments[14] = [
        {
            "id": 5,
            "body": "<!-- repo-factory:claim lease_until=2026-01-01T00:00Z "
            "branch=agent/14-x -->  Working on this.",
            "user": {"login": "a"},
            "created_at": stamp(NOW - 9000),
            "updated_at": stamp(NOW - 9000),
        }
    ]
    got = revise.claim(r, r.issue(14), "repo-factory/14-huge", NOW, 6)
    assert "claim:repo-factory" in labels(fake, 14)
    mine = [c for c in fake.comments[14] if revise.MINE in c["body"]]
    assert len(mine) == 1 and "branch=repo-factory/14-huge" in mine[0]["body"]
    assert "previous claim's lease expired" in mine[0]["body"], "a take-over says so"
    assert fake.comments[14][0]["body"].endswith("Working on this."), (
        "another agent's comment is left alone"
    )
    ls = revise.lease_of(r.comments(14))
    assert ls["mine"] and abs(ls["until"] - got["until"]) < 61
    revise.renew(r, 14, "repo-factory/14-huge", NOW + 3600, 6)
    mine = [c for c in fake.comments[14] if revise.MINE in c["body"]]
    assert len(mine) == 1 and revise.lease_of(r.comments(14))["until"] > got["until"]


# -- reproduce --------------------------------------------------------------------------------------
def test_reproduce_accepts_a_test_that_fails_for_the_stated_reason(world):
    lp, fake, _ = world
    f = fix(lp, Scripted(names_test(), fixes))
    out = f.run()
    assert out["outcome"] == "pr", out
    assert f.test == TEST and "huge id answered 500" in f.failure


def test_reproduce_rejects_a_passing_test_and_another_reason_then_cannot_reproduce(world):
    lp, fake, _ = world
    passing = names_test(test="tests/acceptance/test_unknown_plot_is_404.py", existing=False)
    other = names_test(expect="database is locked")
    h = Scripted(passing, other)
    out = fix(lp, h).run()
    assert out["outcome"] == "cannot_reproduce" and out["stage"] == "reproduce", out
    assert "not with the stated expectation" in out["why"]
    assert "already exists" in h.prompts[1] or "passes on the current code" in h.prompts[1]
    assert "cannot-reproduce" in labels(fake, 14) and "claim:repo-factory" not in labels(fake, 14)
    assert any("result=cannot-reproduce" in b for b in fake.comments_of(14))
    assert not fake.releases and not [p for p in fake.issues.values() if "pull_request" in p]
    # a cannot-reproduce issue leaves the queue until the owner removes the label
    pol = policy.parse(fake.policy_text)
    items = github.Repo(lp.gh, REPO).issues()
    assert revise.queue(items, pol, time.time()) == []


def test_a_session_that_ends_without_an_answer_is_a_failure_not_cannot_reproduce(world):
    lp, fake, _ = world
    out = fix(lp, Scripted(lambda ws, p: None)).run()
    assert out["outcome"] == "failed" and out["stage"] == "reproduce", out
    assert "ended without an answer" in out["why"]
    assert "cannot-reproduce" not in labels(fake, 14)
    assert "repo-factory will try once more" in fake.comments_of(14)[-1]


def test_a_new_test_that_fails_for_the_reason_is_kept_and_other_edits_reverted(world):
    lp, fake, _ = world
    new = "tests/acceptance/test_a_long_id_is_not_found.py"

    def writes(ws, p):
        (ws / new).write_text(
            '"""A long id is not found."""\nfrom app import status\n\n\n'
            "def test_long():\n    st = status('1' * 25)\n"
            "    assert st == 404, f'long id answered {st}'\n"
        )
        (ws / "app.py").write_text("broken = True\n")  # not the session's job
        note(
            ws, "reproduce.json", {"test": new, "existing": False, "expect": "long id answered 500"}
        )

    f = fix(lp, Scripted(writes, fixes))
    out = f.run()
    assert out["outcome"] == "pr", out
    pr = fake.issues[out["pr"]]
    assert f"`{new}` (failed on main: long id answered 500)" in pr["body"]


# -- the fix, verify, deliver ---------------------------------------------------------------------------
def test_the_fix_reaches_deliver_unmarks_the_open_item_and_opens_the_pr(world):
    lp, fake, iid = world
    out = fix(lp, Scripted(names_test(), fixes)).run()
    assert out["outcome"] == "pr" and out["usd"] == pytest.approx(0.8), out
    pr = fake.issues[out["pr"]]
    body = pr["body"]
    assert pr["title"] == f"Fix #14: {S5['title']}" and "Fixes #14" in body
    assert f"**Reproduced by:** `{TEST}` (failed on main: huge id answered 500)" in body
    assert "**Documents:** AGENTS.md unchanged on purpose · CHANGELOG.md · SECURITY.md" in body
    assert "**Cost:** $0.80" in body and "2 model sessions" in body
    assert "<!-- repo-factory:revise-meta issue=14 kind=bug miss=security flow=F2 test=" in body
    assert {x["name"] for x in pr["labels"]} == {"repo-factory", "bug"}
    assert pr["head"]["ref"] == "repo-factory/14-a-huge-numeric-id-in-a-url-gives-a-500"
    # the pushed branch: the fix, the mark and the README line gone, nothing else
    clone = Path(tempfile.mkdtemp())
    subprocess.run(
        ["git", "clone", "-q", "-b", pr["head"]["ref"], str(fake.origin), str(clone / "c")],
        check=True,
    )
    c = clone / "c"
    assert "Known open item" not in (c / TEST).read_text()
    assert "test_huge_numeric_id_gets_404.py" not in (c / "tests/acceptance/README.md").read_text()
    assert "Every test passes on this version." in (c / "tests/acceptance/README.md").read_text()
    assert "return 404" in (c / "app.py").read_text() and not (c / ".repo-factory").exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=c, capture_output=True, text=True
    ).stdout
    assert log.startswith("Fix #14: A huge") and "Fixes #14" in log
    assert "Claude" not in log and "Co-Authored" not in log
    # on the issue: the claim gone, one comment pointing at the PR
    assert "claim:repo-factory" not in labels(fake, 14)
    assert any("result=pr n=" in b and f"#{out['pr']}" in b for b in fake.comments_of(14))
    lease = [b for b in fake.comments_of(14) if revise.MINE in b]
    assert len(lease) == 1 and f"it proposed a fix in #{out['pr']}." in lease[0]
    assert revise.lease_of(github.Repo(lp.gh, REPO).comments(14))["until"] <= time.time() + 60
    assert "**Verified:** 3 tests passed (the product's own and the acceptance suite" in body
    # spend: the loop's ledger (maintenance) and the task's event log (the build page)
    assert lp.spend()["today"]["maintain"] == pytest.approx(0.8)
    task = revise.task_dir(lp.cfg.tasks_dir, REPO, 14)
    st = __import__("repo_factory.events", fromlist=["load_state"]).load_state(task)
    assert st["status"] == "ok" and st["v5"]["planned"][0] == "reproduce"
    assert st["v5"]["stages"]["repair"]["status"] == "skipped"
    assert revise.result(task)["outcome"] == "pr"


def released_checks(seen):
    """maintain.exam's runner as the fix sees it: the release's checks on a tree. ``q1``
    passes on any tree; ``q2`` expects the old answer (500) and so fails on the fix."""

    def run(tree):
        app = (Path(tree) / "app.py").read_text()
        seen.append("return 404" in app)
        return {
            "ran": True,
            "tag": "v0.1.0",
            "scenarios": {
                "q1": {"status": "passed"},
                "q2": {
                    "status": "passed" if "return 500" in app else "failed",
                    "detail": "assert 404 == 500",
                },
            },
        }

    return run


def test_the_release_checks_run_on_main_and_on_the_fix_and_the_pr_reports_them(world):
    lp, fake, _ = world
    seen = []
    f = fix(lp, Scripted(names_test(), fixes), exam=released_checks(seen))
    assert f.exam_mode == "report"  # maintain.exam's default
    out = f.run()
    assert out["outcome"] == "pr", out
    assert seen == [False, True], "main first, then the fixed tree"
    body = fake.issues[out["pr"]]["body"]
    assert (
        "**repo-factory's checks from v0.1.0:** 1 of 2 pass on this branch; passed on main and "
        "fail now: `q2`"
        in body
        and "the owner decides" in body
    )


def test_require_stops_a_fix_that_breaks_a_released_check(world):
    lp, fake, _ = world
    f = fix(lp, Scripted(names_test(), fixes), exam=released_checks([]))
    f.exam_mode = "require"
    out = f.run()
    assert out["outcome"] == "failed" and out["stage"] == "readiness", out
    assert "passed on main and fail on the fix: q2" in out["why"]
    assert not [p for p in fake.issues.values() if "pull_request" in p], "no PR"


def test_a_release_without_the_checks_asset_says_so_in_the_pr(world):
    lp, fake, _ = world
    out = fix(lp, Scripted(names_test(), fixes)).run()
    body = fake.issues[out["pr"]]["body"]
    assert "**repo-factory's checks:** not run: release v0.1.0 has no checks asset" in body


def test_the_checks_asset_unpacks_into_a_rule_set(tmp_path):
    from repo_factory.maintain import checks
    from repo_factory.v5 import exam

    ev = tmp_path / "ev"
    (ev / "exam" / "_verify" / "scenarios").mkdir(parents=True)
    (ev / "exam" / "_verify" / "__init__.py").write_text("")
    (ev / "exam" / "_verify" / "scenarios" / "test_rule_q1.py").write_text("def test(): pass\n")
    (ev / "manifest.json").write_text('{"factory_version": "0.9.0"}')
    tar = exam.evidence_tarball(ev, tmp_path / "x-0.1.0-checks.tar.gz", "x-0.1.0")
    folder = checks.unpack(tar, tmp_path / "asset")
    assert folder.name == "x-0.1.0-checks"
    a = checks.Asset(folder, tmp_path / "run")
    assert [r.id for r in a.store.active()] == ["q1"] and a.manifest["factory_version"] == "0.9.0"
    d, h = exam.exam_for(a, a.store.active())
    assert (d / "_verify" / "scenarios" / "test_rule_q1.py").is_file() and h
    assert checks.compare(
        {"scenarios": {"q1": {"status": "failed"}}},
        {"ran": True, "scenarios": {"q1": {"status": "passed"}}},
    ) == {
        "ran": True,
        "checks": 1,
        "passed": 1,
        "failed_on_main": ["q1"],
        "regressions": [],
        "details": {},
    }
    assert checks.mode({"exam": "nonsense"}) == "report" and checks.mode({"exam": "off"}) == "off"


def test_the_scope_check_holds_a_diff_touching_a_workflow(world):
    lp, fake, _ = world
    sneaky = lambda ws: (ws / ".github/workflows/x.yml").write_text("on: push\n")  # noqa: E731
    h = Scripted(
        names_test(), lambda ws, p: fixes(ws, p, sneaky), lambda ws, p: fixes(ws, p, sneaky)
    )  # the repair keeps it
    out = fix(lp, h).run()
    assert out["outcome"] == "failed" and out["stage"] == "reverify", out
    assert "a fix never changes this file" in out["why"]
    assert "out of scope" in h.prompts[2], "the repair round was told"
    assert not [p for p in fake.issues.values() if "pull_request" in p], "no PR"
    assert revise.scope_violations(
        [
            ".github/workflows/x.yml",
            ".github/repo-factory.yml",
            "LICENSE",
            "NOTICE",
            "../x",
            ".env",
            "app.py",
            ".env.example",
        ]
    ) == [
        ".github/workflows/x.yml: a fix never changes this file",
        ".github/repo-factory.yml: a fix never changes this file",
        "LICENSE: a fix never changes this file",
        "NOTICE: a fix never changes this file",
        "../x: outside the repository",
        ".env: a env file",
    ]


def test_a_regression_goes_to_one_repair_round_then_passes(world):
    lp, fake, _ = world

    def breaks(ws, p):
        fixes(ws, p)
        app = ws / "app.py"
        app.write_text(app.read_text().replace("return 200 if found else 404", "return 404"))

    def repairs(ws, p):
        app = ws / "app.py"
        head, _, tail = app.read_text().rpartition("\n    return 404\n")
        app.write_text(head + "\n    return 200 if found else 404\n" + tail)

    h = Scripted(names_test(), breaks, repairs)
    out = fix(lp, h).run()
    assert out["outcome"] == "pr", (out.get("stage"), out.get("why"))
    assert "regression: `tests/test_app.py::test_known_plot` passed on main" in h.prompts[2]
    assert out["sessions"] == 3


def test_failures_count_up_to_needs_you_and_removing_it_resets(world):
    lp, fake, _ = world

    def bad_check(root):  # AGENTS.md runs on main; the fixed tree breaks it
        if "return 500" in (Path(root) / "app.py").read_text():
            return {"ok": True, "summary": "passed: 1 commands, 0 problems", "output": ""}
        return {
            "ok": False,
            "summary": "FAILED: 1 commands, 1 problems",
            "output": "FAIL  `python -m pytest` exited 1",
        }

    r = github.Repo(lp.gh, REPO)
    out1 = fix(lp, Scripted(names_test(), fixes), check=bad_check).run()
    assert out1["outcome"] == "failed" and out1["stage"] == "readiness"
    assert "needs-you" not in labels(fake, 14) and "claim:repo-factory" not in labels(fake, 14)
    assert "repo-factory will try once more" in fake.comments_of(14)[-1]
    assert "-attempt1`" in fake.comments_of(14)[-1], "the partial work is pushed"
    out2 = fix(lp, Scripted(names_test(), fixes), check=bad_check).run()
    assert out2["attempt"] == 2 and "needs-you" in labels(fake, 14)
    from repo_factory.events import load_state

    st = load_state(revise.task_dir(lp.cfg.tasks_dir, REPO, 14))
    assert st["build"] == 2 and st["v5"]["stages"]["build"]["runs"] == 1, "each run its own"
    assert "labels it `needs-you`" in fake.comments_of(14)[-1]
    assert revise.attempts(r.comments(14)) == 2
    pol = policy.parse(fake.policy_text)
    assert revise.queue(r.issues(), pol, time.time()) == [], "needs-you leaves the queue"
    # the owner removes needs-you (Try again): the next poll posts the reset and starts over
    r.set_labels(14, remove=["needs-you"])
    rec, slots = {}, [1]
    got = revise.step(lp, r, poll.targets(lp)[0], r.issues(), pol, rec, time.time(), slots)
    assert got["started"] == [14] and slots == [0]
    assert any(revise.RESET in b for b in fake.comments_of(14))
    assert revise.attempts(r.comments(14)) == 0
    assert "claim:repo-factory" in labels(fake, 14) and rec["revises"]["14"]["pid"]


def test_a_fix_process_that_dies_without_a_word_counts_as_a_failed_attempt(world):
    lp, fake, _ = world
    r = github.Repo(lp.gh, REPO)
    pol = policy.parse(fake.policy_text)
    rec, slots = {}, [1]
    got = revise.step(lp, r, poll.targets(lp)[0], r.issues(), pol, rec, time.time(), slots)
    assert got["started"] == [14]
    task = Path(rec["revises"]["14"]["task"])
    assert json.loads((task / "loop.json").read_text())["kind"] == "fix"
    assert "Fix #14 in Plotkeeper" in (task / "spec.md").read_text()
    pid = rec["revises"]["14"]["pid"]
    for _ in range(100):  # the fake fix process (python -c pass) exits
        if not revise.alive(pid):
            break
        time.sleep(0.05)
    keep = dict(rec["revises"])
    got = revise.step(lp, r, poll.targets(lp)[0], r.issues(), pol, rec, time.time(), [0])
    assert got["ended"][0]["outcome"] == "failed" and rec["revises"] == {}
    assert "fix process stopped" in fake.comments_of(14)[-1]
    assert "claim:repo-factory" not in labels(fake, 14)
    # one that died after opening its PR (before it wrote its result): the PR says it is done
    n = len(fake.comments_of(14))
    fake.add_pr(30, "Fix #14: huge id", "owner", labels=["repo-factory"], association="OWNER")
    fake.issues[30]["body"] = "Fixes #14"
    rec["revises"] = keep
    got = revise.step(lp, r, poll.targets(lp)[0], r.issues(), pol, rec, time.time(), [0])
    assert got["ended"][0] == {"number": 14, "outcome": "pr", "pr": 30, "why": None}
    assert len(fake.comments_of(14)) == n, "no failure comment"


def test_a_stale_issue_list_after_a_merge_starts_nothing(world):
    """GitHub's issue list lags a merge by seconds: it still says open and go. The poll reads
    the one issue fresh before it claims, the fix process checks again, and a fix that died
    on an issue closed meanwhile is no attempt."""
    lp, fake, _ = world
    r = github.Repo(lp.gh, REPO)
    pol = policy.parse(fake.policy_text)
    stale = r.issues()
    fake.issues[14]["state"] = "closed"  # merged a moment ago
    got = revise.step(lp, r, poll.targets(lp)[0], stale, pol, {}, time.time(), [1])
    assert got["started"] == [] and "claim:repo-factory" not in labels(fake, 14)
    out = fix(lp, Scripted()).run()
    assert out["outcome"] == "skipped" and fake.comments_of(14) == [], "not even a lease"
    fake.issues[14]["labels"].append({"name": "claim:repo-factory"})
    revise.abandon(r, 14, {}, pol)
    assert "claim:repo-factory" not in labels(fake, 14)
    assert not any("result=failed" in b for b in fake.comments_of(14))


def test_one_fix_at_a_time_and_fixes_share_the_build_slots(world):
    lp, fake, _ = world
    cache = github.Cache(lp.cfg.home)
    cache.write_repo(
        REPO, {"revises": {"14": {"pid": os.getpid(), "task": "x", "started": time.time()}}}
    )
    assert [x["number"] for x in lp.revising()] == [14]
    assert poll.free_slots(lp, cache) == 0, "maintain.concurrency 1, one running"
    lp.cfg.data["maintain"]["concurrency"] = 3
    lp.cfg.data["concurrency"] = 2
    assert poll.free_slots(lp, cache) == 1, "the build slots cap it"
    lp.cfg.data["concurrency"] = 1
    assert lp.start_builds() == [], "the running fix takes the only build slot"


def test_the_tick_starts_a_fix_and_never_while_paused(world):
    lp, fake, _ = world
    lp.cfg.pause_file.write_text("x")
    assert "maintain" not in lp.tick()
    assert "claim:repo-factory" not in labels(fake, 14)
    lp.cfg.pause_file.unlink()
    out = lp.tick()
    rv = out["maintain"]["repos"][0]["revise"]
    assert rv["started"] == [14] and "claim:repo-factory" in labels(fake, 14)


# -- release and the lesson label -------------------------------------------------------------------
def test_bump_picks_patch_or_minor_and_versions_parse():
    assert release.bump((0, 1, 0), {"bug"}) == (0, 1, 1)
    assert release.bump((0, 1, 3), {"bug", "feature"}) == (0, 2, 0)
    assert release.bump((1, 4, 2), set(), "minor") == (1, 5, 0)
    assert release.newest(["v0.1.0", "v0.10.0", "v0.9.9", "junk", "0.11.0"]) == (
        (0, 10, 0),
        "v0.10.0",
    )


def test_release_after_a_merge_labels_the_lesson_and_is_idempotent(world):
    lp, fake, _ = world
    fake.tag_list = ["v0.1.0"]
    fake.add_pr(
        20, "Fix #14: huge id", "owner", labels=["repo-factory", "bug"], association="OWNER"
    )
    fake.issues[20].update(
        state="closed",
        body="Fixes #14\n\n<!-- repo-factory:revise-meta issue=14 "
        "kind=bug miss=security flow=F4 test=" + TEST + " -->",
    )
    fake.compare_commits, fake.commit_prs = ["c1"], {"c1": [20]}
    r = github.Repo(lp.gh, REPO)
    pol = policy.parse(fake.policy_text)
    out = release.step(r, pol)
    assert out["tag"] == "v0.1.1" and out["kinds"] == ["bug"], out
    assert fake.releases == [
        {
            "tag_name": "v0.1.1",
            "name": "v0.1.1",
            "target_commitish": "main",
            "generate_release_notes": True,
        }
    ]
    assert "lesson" in labels(fake, 14)
    c = [b for b in fake.comments_of(14) if release.LESSON in b]
    assert len(c) == 1 and f"miss=security flow=F4 test={TEST}" in c[0]
    # the next poll: main has nothing past v0.1.1
    fake.compare_commits = []
    assert release.step(r, pol) is None
    assert release.newest(fake.tag_list) == ((0, 1, 1), "v0.1.1")
    assert len(fake.releases) == 1
    # a feature makes a minor; a ticket repo-factory opened is no lesson
    fake.issues[15] = issue(15, "Export as CSV", ["feature"], body="x\n" + revise.TICKET)
    fake.add_pr(21, "Fix #15", "owner", labels=["repo-factory", "feature"], association="OWNER")
    fake.issues[21]["body"] = (
        "Fixes #15\n<!-- repo-factory:revise-meta issue=15 kind=feature test=t.py -->"
    )
    fake.compare_commits, fake.commit_prs = ["c2"], {"c2": [21]}
    out = release.step(r, pol)
    assert out["tag"] == "v0.2.0" and out["lessons"] == []
    off = policy.parse(policy.render({"status": "maintained", "release": {"on_merge": "off"}}))
    assert release.step(r, off) is None


def test_the_poll_releases_and_the_box_shows_it(world):
    lp, fake, _ = world
    fake.tag_list, fake.compare_commits = ["v0.1.0"], ["c1"]
    fake.add_pr(20, "Docs", "owner", labels=["docs"], association="OWNER")
    fake.commit_prs = {"c1": [20]}
    fake.issues[14]["labels"] = [{"name": "bug"}]  # nothing to fix this time
    out = poll.maintain_tick(lp)
    assert out["repos"][0]["release"]["tag"] == "v0.1.1"
    assert github.Cache(lp.cfg.home).repo(REPO)["release"]["tag"] == "v0.1.1"


# -- Cut a ticket (a press, or a Claude Code proposal confirmed) -------------------------------------
def test_cut_ticket_validates_checks_and_runs_through_the_fake_gh(world):
    from repo_factory import registry as R

    lp, fake, iid = world
    fields = R.validate(
        "cut_ticket",
        {
            "issue": "Huge numeric id returns 500",
            "text": "Open /plots/999999999999999999999 and see a 500.",
            "labels": ["bug", "p2", "go"],
        },
    )
    assert fields["labels"] == ["bug", "p2", "go"] and R.get("cut_ticket").title == "Open issue"
    for bad in (
        {"labels": ["bug", "wontfix"]},
        {"labels": ["bug", "feature"]},
        {"issue": ""},
        {"text": ""},
    ):
        with pytest.raises(R.ActionError):
            R.validate("cut_ticket", {"issue": "x", "text": "y", "labels": ["bug"], **bad})
    # Ask for changes (AR-PLAN.md §2.5) needs the instructions, and on a published build its
    # check sends the owner to an issue
    with pytest.raises(R.ActionError, match="needs instructions"):
        R.validate("revise", {})
    pub = {
        "key": "k",
        "idea": "i12345678",
        "idea_state": "published",
        "status": "ready",
        "repo": "o/p",
        "url": "https://github.com/o/p",
    }
    assert "Open issue" in R.check("revise", R.Subject("build", None, pub))
    row = {"repo": REPO, "idea_state": "published", "idea": iid}
    assert R.check("cut_ticket", R.Subject("build", None, row)) is None
    assert R.check("cut_ticket", R.Subject("build", None, {"idea_state": "ready"}))
    a_ = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a_.loop = lambda: lp
    press = lambda: R.run(
        R.Ctx(a_, "ui", confirm=True),
        "cut_ticket",
        fields,  # noqa: E731
        R.Subject("build", None, row),
        record=False,
    )
    out = press()
    n = out["result"]["id"]
    assert out["say"].startswith(f"Opened #{n} on {REPO}")
    new = fake.issues[n]
    assert new["title"] == "Huge numeric id returns 500" and labels(fake, n) == {"bug", "p2", "go"}
    assert revise.TICKET in new["body"]
    # a repo whose policy fixes nothing gets the issue without go, and the card says so
    fake.policy_text = policy.render({"status": "as-is"})
    a_._maint = None
    out = press()
    assert "go" not in labels(fake, out["result"]["id"]) and "without go" in out["say"]


# -- the network allowlist --------------------------------------------------------------------------
def test_the_allowlist_matches_names_and_subdomains_only():
    ok = netproxy.allowlist(["github.com", "PyPI.org."])
    assert ok("github.com") and ok("api.github.com") and ok("pypi.org") and ok("PYPI.ORG")
    assert not ok("evilgithub.com") and not ok("github.com.evil.example")
    assert not ok("140.82.112.3") and not ok("") and not ok("[::1]")


def test_the_proxy_refuses_a_host_not_listed_before_resolving_it():
    with netproxy.Proxy(hosts=["api.anthropic.com"]) as px:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(str(px.sock))
        s.sendall(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example\r\n\r\n")
        got = s.recv(4096).decode()
        s.close()
    assert got.startswith("HTTP/1.1 403") and "host not on the allowlist" in got
    assert px.log == [("denied", "evil.example:443")]


def test_the_sandbox_reads_the_allowlist_from_the_environment(monkeypatch):
    from repo_factory.harness import sandbox

    monkeypatch.delenv(sandbox.NET_HOSTS_ENV, raising=False)
    assert sandbox.net_hosts() is None, "builds: any public destination"
    monkeypatch.setenv(sandbox.NET_HOSTS_ENV, "api.anthropic.com, pypi.org")
    assert sandbox.net_hosts() == ["api.anthropic.com", "pypi.org"]


# -- the UI -------------------------------------------------------------------------------------------
def test_a_fix_is_a_build_row_of_kind_fix_and_the_header_counts_it(world):
    lp, fake, _ = world
    out = fix(lp, Scripted(names_test(), fixes)).run()
    a_ = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a_.loop = lambda: lp
    a_.loop_cfg = lambda: lp.cfg
    row = next(b for b in a_.snapshot()["builds"] if b.get("kind") == "fix")
    assert row["status"] == "fixed" and row["source"] == "fix"
    assert row["title"] == f"Fix #14: {S5['title']}" and row["desc"] == "Plotkeeper"
    assert row["pr"]["number"] == out["pr"] and row["issue"]["number"] == 14
    assert row["reason"] == f"PR #{out['pr']} waits for your review on GitHub"
    h = a_.header()
    assert h["revising"] == 0 and h["needs_you"] == 0, "a PR waits on GitHub, not as a build"
    page = a_.build_page(Path(row["dir"]))
    stages = [s["stage"] for s in page["state"]["v5"]["stages"]]
    assert stages == ["reproduce", "build", "verify", "repair", "reverify", "readiness", "deliver"]
    # the owner merges it; the next poll no longer sees it open
    fake.issues[out["pr"]]["state"] = "closed"
    time.sleep(1.1)
    poll.maintain_tick(lp)
    row = next(b for b in a_.snapshot()["builds"] if b.get("kind") == "fix")
    assert row["status"] == "closed" and "merged or closed" in row["reason"]


def test_settings_write_the_fix_keys():
    from repo_factory.settings import write as settings

    assert settings.check("maintain.concurrency", 2) == 2
    assert settings.check("maintain.max_usd", "12.5") == 12.5
    assert settings.check("maintain.max_usd", None) is None
    assert settings.check("maintain.net", "public") == "public"
    assert settings.check("maintain.lesson_label", False) is False
    for k, v in (
        ("concurrency", 9),
        ("concurrency", True),
        ("max_usd", -1),
        ("max_usd", "x"),
        ("net", "anything"),
        ("lesson_label", "yes"),
    ):
        with pytest.raises(ValueError):
            settings.check(f"maintain.{k}", v)


def test_no_attribution_in_what_factory_posts():
    for p in (
        Path(revise.__file__),
        Path(release.__file__),
        revise.POLICY / "prompts" / "fix.md",
        revise.POLICY / "prompts" / "reproduce.md",
        revise.POLICY / "prompts" / "specify.md",
    ):
        t = p.read_text()
        assert "Claude" not in t.replace("Claude Code", "") and "Co-Authored" not in t, p


def test_a_feature_or_task_is_specified_not_reproduced(world):
    """AU3 findings 5 and 7: an accepted feature (Beliefline #8) or a maintenance task
    (Beliefline #6) names no failing behaviour; its test specifies what it asks for, and must
    still fail on main for the stated reason."""
    lp, fake, _ = world
    for n, kind in ((15, "feature"), (16, "task")):
        fake.issues[n] = {
            **issue(n, "The export carries a checksum", [kind, "p3", "go"]),
            "state": "open",
            "html_url": f"https://github.com/{REPO}/issues/{n}",
        }
        h = Scripted(names_test(), fixes)
        fix(lp, h, number=n).run()
        assert h.prompts[0].startswith("You are specifying a requested change"), kind
    h = Scripted(names_test(), fixes)
    fix(lp, h).run()  # #14 is a bug: reproduced
    assert h.prompts[0].startswith("You are reproducing a reported problem")
