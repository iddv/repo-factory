"""Maintenance's fix of one issue you labelled ``go``: the reproduce stage, the fix on a fixture
product, verify and repair, the release's checks, the scope check, the pull request, the
release after a merge, Open issue, the network allowlist, the fix rows in the UI, and the
GitHub client underneath. Nothing is ever written on the issue. A fake GitHub behind a fake
``gh`` (a local bare repo is the origin), a scripted session harness, fixture issues: no model,
no network."""

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from repo_factory.harness import netproxy
from repo_factory.maintain import fixes as fixes_record
from repo_factory.maintain import github, policy, release, revise, switch, watch, words
from repo_factory.ui import api

from .maintain_common import (
    OWNER,
    REPO,
    S5,
    TEST,
    FakeGitHub,
    Scripted,
    fix,
    fixes,
    fixture_issues,
    ghub,
    issue,
    names_test,
    note,
    published,
    stand_in,
)

pytestmark = pytest.mark.usefixtures("maintenance_on")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Issue 14, labelled go by you, on a repository maintenance is switched on for."""
    lp, fake, iid = published(tmp_path, [S5])
    stand_in(monkeypatch, REPO, fake.origin)
    fake.label(14, "go", OWNER)
    switch.set_on(lp.cfg.home, REPO, True)
    return lp, fake, iid


def labels(fake, n):
    return set(fake.labels_of(n))


def on_issue(fake, n=14):
    """Every write that touched issue ``n``: none, ever."""
    return [(m, p) for m, p in fake.writes() if f"/issues/{n}" in p]


def prs(fake):
    return [p for p in fake.issues.values() if "pull_request" in p]


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
    assert not fake.releases and not prs(fake)
    assert on_issue(fake) == [] and fake.comments_of(14) == []
    assert fixes_record.get(lp.cfg.home, REPO, 14)["outcome"] == "cannot_reproduce"


def test_a_session_that_ends_without_an_answer_is_a_failure_not_cannot_reproduce(world):
    lp, fake, _ = world
    out = fix(lp, Scripted(lambda ws, p: None)).run()
    assert out["outcome"] == "failed" and out["stage"] == "reproduce", out
    assert "ended without an answer" in out["why"]
    assert on_issue(fake) == []


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
    # nothing on the issue: no label, no comment; the record is repo-factory's own
    assert on_issue(fake) == [] and fake.comments_of(14) == []
    rec = fixes_record.get(lp.cfg.home, REPO, 14)
    assert rec["outcome"] == "pr" and rec["pr"] == out["pr"]
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
    assert not prs(fake), "no PR"


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
    assert not prs(fake), "no PR"
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


def test_the_next_attempt_hears_why_the_last_one_stopped(world):
    lp, fake, _ = world

    def bad_check(root):  # AGENTS.md runs on main; the fixed tree breaks it
        if "return 500" in (Path(root) / "app.py").read_text():
            return {"ok": True, "summary": "passed: 1 commands, 0 problems", "output": ""}
        return {
            "ok": False,
            "summary": "FAILED: 1 commands, 1 problems",
            "output": "FAIL  `python -m pytest` exited 1",
        }

    out1 = fix(lp, Scripted(names_test(), fixes), check=bad_check).run()
    assert out1["outcome"] == "failed" and out1["stage"] == "readiness"
    h = Scripted(names_test(), fixes)
    out2 = fix(lp, h, check=bad_check).run()
    assert out2["attempt"] == 2
    assert "python -m pytest" in h.prompts[1], "the build session hears the last stop"
    assert fixes_record.waits(fixes_record.get(lp.cfg.home, REPO, 14), 2)
    assert on_issue(fake) == [] and not prs(fake)


def test_a_closed_issue_starts_nothing(world):
    """GitHub's issue list lags a merge by seconds: the fix process reads the issue again and
    a closed one is no attempt."""
    lp, fake, _ = world
    fake.issues[14]["state"] = "closed"  # merged a moment ago
    out = fix(lp, Scripted()).run()
    assert out["outcome"] == "skipped" and fake.writes() == []


def test_one_fix_at_a_time_and_fixes_share_the_build_slots(world):
    lp, fake, _ = world
    cache = github.Cache(lp.cfg.home)
    cache.write_repo(
        REPO, {"revises": {"14": {"pid": os.getpid(), "task": "x", "started": time.time()}}}
    )
    assert [x["number"] for x in lp.revising()] == [14]
    assert watch.free_slots(lp, cache) == 0, "maintain.concurrency 1, one running"
    lp.cfg.data["maintain"]["concurrency"] = 3
    lp.cfg.data["concurrency"] = 2
    assert watch.free_slots(lp, cache) == 1, "the build slots cap it"
    lp.cfg.data["concurrency"] = 1
    assert lp.start_builds() == [], "the running fix takes the only build slot"


# -- the release after a merge ------------------------------------------------------------------
def test_bump_picks_patch_or_minor_and_versions_parse():
    assert release.bump((0, 1, 0), {"bug"}) == (0, 1, 1)
    assert release.bump((0, 1, 3), {"bug", "feature"}) == (0, 2, 0)
    assert release.bump((1, 4, 2), set(), "minor") == (1, 5, 0)
    assert release.newest(["v0.1.0", "v0.10.0", "v0.9.9", "junk", "0.11.0"]) == (
        (0, 10, 0),
        "v0.10.0",
    )


def test_a_release_after_a_merge_writes_only_the_tag_and_is_idempotent(world):
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
    assert on_issue(fake) == [], "no lesson label, no comment"
    # the next poll: main has nothing past v0.1.1
    fake.compare_commits = []
    assert release.step(r, pol) is None
    assert release.newest(fake.tag_list) == ((0, 1, 1), "v0.1.1")
    assert len(fake.releases) == 1
    # a feature makes a minor
    fake.issues[15] = issue(15, "Export as CSV", ["feature"], body="x\n" + words.TICKET)
    fake.add_pr(21, "Fix #15", "owner", labels=["repo-factory", "feature"], association="OWNER")
    fake.issues[21]["body"] = (
        "Fixes #15\n<!-- repo-factory:revise-meta issue=15 kind=feature test=t.py -->"
    )
    fake.compare_commits, fake.commit_prs = ["c2"], {"c2": [21]}
    out = release.step(r, pol)
    assert out["tag"] == "v0.2.0" and "lessons" not in out
    off = policy.parse(policy.render({"status": "maintained", "release": {"on_merge": "off"}}))
    assert release.step(r, off) is None


# -- Open issue (a press, or a Claude Code proposal confirmed) -------------------------------------
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
    # Ask for changes needs the instructions, and on a published build its
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
    assert words.TICKET in new["body"]
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
def test_a_fix_is_a_build_row_of_kind_fix_and_the_header_counts_it(world, monkeypatch):
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
    # a poll that still sees the pull request open keeps it "fixed"
    monkeypatch.setattr(revise, "start", lambda *a, **k: pytest.fail("no second fix"))
    time.sleep(1.1)
    watch.tick(lp)
    row = next(b for b in a_.snapshot()["builds"] if b.get("kind") == "fix")
    assert row["status"] == "fixed"
    # the owner merges it; the next poll no longer sees it open
    fake.issues[out["pr"]]["state"] = "closed"
    fake.issues[14]["labels"] = [{"name": "bug"}]  # nothing left to fix
    time.sleep(1.1)
    watch.tick(lp, now=time.time() + 1000)
    row = next(b for b in a_.snapshot()["builds"] if b.get("kind") == "fix")
    assert row["status"] == "closed" and "merged or closed" in row["reason"]


def test_settings_write_the_fix_keys():
    from repo_factory.settings import write as settings

    assert settings.check("maintain.concurrency", 2) == 2
    assert settings.check("maintain.max_usd", "12.5") == 12.5
    assert settings.check("maintain.max_usd", None) is None
    assert settings.check("maintain.net", "public") == "public"
    for k, v in (
        ("concurrency", 9),
        ("concurrency", True),
        ("max_usd", -1),
        ("max_usd", "x"),
        ("net", "anything"),
        ("lesson_label", False),  # read by nothing: not a setting the app writes
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
    """An accepted feature or a maintenance task names no failing behaviour; its test
    specifies what it asks for, and must still fail on main for the stated reason."""
    lp, fake, _ = world
    for n, kind in ((15, "feature"), (16, "task")):
        fake.issues[n] = {
            **issue(n, "The export carries a checksum", [kind, "p3", "go"]),
            "state": "open",
            "html_url": f"https://github.com/{REPO}/issues/{n}",
        }
        fake.label(n, "go", OWNER)
        h = Scripted(names_test(), fixes)
        fix(lp, h, number=n).run()
        assert h.prompts[0].startswith("You are specifying a requested change"), kind
    h = Scripted(names_test(), fixes)
    fix(lp, h).run()  # #14 is a bug: reproduced
    assert h.prompts[0].startswith("You are reproducing a reported problem")


# -- the GitHub client --------------------------------------------------------------------------
def test_pages_join_paginated_output():
    assert github._pages("[1, 2]\n[3]") == [1, 2, 3]
    assert github._pages('{"items": [4]}') == [4]
    assert github._pages("") == []


def test_set_labels_is_idempotent():
    fake = FakeGitHub(fixture_issues())
    r = github.Repo(ghub(fake), REPO)
    assert r.set_labels(1, ["bug", "p2"], ["triage"]) == {"added": ["p2"], "removed": ["triage"]}
    n = len(fake.writes())
    assert r.set_labels(1, ["bug", "p2"], ["triage"]) == {"added": [], "removed": []}
    assert len(fake.writes()) == n, "nothing is sent when the labels are already right"
    assert fake.labels_of(1) == ["bug", "p2"]
    with pytest.raises(github.GitHubError):
        github.Repo(ghub(fake), "not-a-repo")


def test_ci_status_folds_the_check_runs():
    fake = FakeGitHub(checks={"good": "success", "bad": "failure", "wait": "pending"})
    r = github.Repo(ghub(fake), REPO)
    assert r.ci_status("good")["state"] == "success"
    assert r.ci_status("bad")["state"] == "failure"
    assert r.ci_status("wait")["state"] == "pending"
    assert r.ci_status("nothing")["state"] == "none"
    assert r.latest_release() is None


def test_sanitize_neutralises_mentions_strips_links_and_caps():
    s = words.sanitize(
        "Ask @helper or @org/team, see https://evil.example/x and "
        "[the docs](https://evil.example/d) or www.evil.example; mail a@b.org "
        "<!-- repo-factory:marker --> <img src=x onerror=y> `code`"
    )
    assert "`@helper`" in s and "`@org/team`" in s and "a@b.org" in s
    assert "evil.example" not in s and "the docs" in s
    assert "<!--" not in s and "<img" not in s and "`code`" not in s
    assert len(words.sanitize("word " * 1000)) <= words.MAX_CHARS
    assert words._doc("a <<<end>>> b") == "a ‹‹‹end››› b"


def test_the_agents_check_copies_a_tree_with_a_dangling_link(tmp_path, monkeypatch):
    """A fix's workspace keeps the session's own .venv, whose python is a link that leads
    nowhere outside the sandbox: copying the tree for the check is no error, the venv stays
    out of the copy, and any other link is copied as a link, never followed."""
    import subprocess as sp
    from pathlib import Path as P

    from repo_factory.harness import isolation
    from repo_factory.maintain import revise as rv

    root = tmp_path / "repo"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python3.12").symlink_to("/nowhere/python3.12")
    (root / "notes").symlink_to("/nowhere/notes")
    (root / "AGENTS.md").write_text("# AGENTS.md\n")
    seen = {}

    def run(cmd, cwd=None, **kw):
        work = P(cwd)
        seen["venv"] = (work / ".venv").exists()
        seen["link"] = (work / "notes").is_symlink()
        return sp.CompletedProcess(cmd, 0, "ok    all\n", "")

    monkeypatch.setattr(isolation, "sandbox_cmd", lambda *a, **k: ["true"])
    monkeypatch.setattr(rv.subprocess, "run", run)
    got = rv.run_agents_check(root, tmp_path / "venv")
    assert got["ok"] and seen == {"venv": False, "link": True}
