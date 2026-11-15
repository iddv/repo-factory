"""What merging takes, what a proposal and a fix post, and which branch is meant: merging judges
the diff (a docs fix changes documents only, a bug fix carries its reproducing test, nothing that
runs somewhere, no kind label no merge); a proposal is opened with its kind; releasing is its own
switch, off; titles and proposals are sanitised; the default branch is read once. The stand-in
GitHub of ``test_f3_maintenance``; no model, no network."""

import json
import time

import pytest

from repo_factory.gh import Gh
from repo_factory.maintain import automerge, fixes, labels, proposals, revise, switch, watch
from repo_factory.maintain.github import Repo

from .test_f3_maintenance import OWNER, REPO, own_fix, rows, world  # noqa: F401


@pytest.fixture
def merging(world, monkeypatch):
    lp, fake, iid = world
    switch.set_on(lp.cfg.home, REPO, True)
    switch.set_merge(lp.cfg.home, REPO, True)
    monkeypatch.setattr(revise, "start", lambda *a, **k: {"pid": None, "task": "/nonexistent"})
    fake.checks = {"h1": "success"}
    return lp, fake, iid


def why_refused(lp) -> str:
    q = watch.rows_of(lp.cfg.home, REPO)["queue"]
    return q["items"][0]["why"]


def poll(lp):
    lp.ledger.meta("maintain_last_poll", 0)
    watch.tick(lp, now=time.time() + 1000)


def set_kinds(fake, n, kinds):
    fake.issues[n]["labels"] = [{"name": "go"}, *({"name": k} for k in kinds)]


# -- merging judges the diff ---------------------------------------------------------------------
def test_a_bug_fix_with_its_test_and_a_plain_source_file_merges(merging):
    lp, fake, _ = merging
    own_fix(lp, fake)
    poll(lp)
    assert fake.merged == [40], why_refused(lp)


@pytest.mark.parametrize(
    "files,word",
    [
        ({"app.py": 2}, "reproducing test is not in the diff"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, "pyproject.toml": 1}, "runs somewhere"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, "tests/conftest.py": 3}, "runs somewhere"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, "AGENTS.md": 1}, "runs somewhere"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, ".claude/settings.json": 1}, "folder"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, "requirements.txt": 1}, "runs somewhere"),
        ({"app.py": 2, "tests/test_huge_id.py": 9, ".github/CODEOWNERS": 1}, ".github/"),
    ],
)
def test_a_bug_fix_without_its_test_or_with_a_file_that_runs_never_merges(merging, files, word):
    lp, fake, _ = merging
    own_fix(lp, fake, files=files)
    poll(lp)
    assert fake.merged == [] and word in why_refused(lp)


def test_a_link_in_the_diff_never_merges(merging):
    lp, fake, _ = merging
    own_fix(lp, fake, files={"app.py": 2, "tests/test_huge_id.py": 9, "data.json": 1})
    fake.modes = {"data.json": "120000"}
    poll(lp)
    assert fake.merged == [] and "a link or a submodule" in why_refused(lp)


def test_a_docs_fix_merges_only_when_every_file_is_a_document(merging):
    lp, fake, _ = merging
    set_kinds(fake, 14, ["docs"])
    own_fix(lp, fake, files={"README.md": 3, "docs/usage.rst": 4, "NOTES.txt": 1})
    poll(lp)
    assert fake.merged == [40], why_refused(lp)
    fake.merged.clear()
    own_fix(lp, fake, files={"README.md": 3, "app.py": 1})
    poll(lp)
    assert fake.merged == [] and "not a document" in why_refused(lp)


@pytest.mark.parametrize("kinds", [[], ["feature"], ["task"], ["bug", "docs"]])
def test_no_kind_or_another_kind_on_the_issue_never_merges(merging, kinds):
    lp, fake, _ = merging
    set_kinds(fake, 14, kinds)
    own_fix(lp, fake)
    poll(lp)
    assert fake.merged == []
    why = why_refused(lp)
    assert ("no kind label" in why) if not kinds else ("kind" in why or "never merges" in why)


def test_the_probes_files_are_refused_by_merging_and_the_agent_files_by_the_fix():
    """Every line of the review's list of files that run somewhere: merging refuses each; a
    fix refuses .github/ whole and the agent and editor files."""
    files = [
        ".github/workflows/ci.yml",
        ".github/scripts/check_agents_md.py",
        ".github/actions/setup/action.yml",
        ".github/dependabot.yml",
        ".github/CODEOWNERS",
        "AGENTS.md",
        "CLAUDE.md",
        ".claude/settings.json",
        ".vscode/tasks.json",
        ".devcontainer/devcontainer.json",
        ".pre-commit-config.yaml",
        "Makefile",
        "pyproject.toml",
        "setup.py",
        "package.json",
        "conftest.py",
        "tests/conftest.py",
        "Dockerfile",
        ".gitattributes",
        ".gitmodules",
        ".envrc",
        "requirements.txt",
        "uv.lock",
        "LICENSE",
    ]
    assert [f for f in files if not automerge.never_path(f)] == []
    for f in ("app.py", "tests/test_app.py", "README.md", "docs/x.md"):
        assert automerge.never_path(f) is None, f
    agents = [
        f for f in files if f.startswith((".github/", ".claude/", ".vscode/", ".devcontainer/"))
    ]
    agents += ["AGENTS.md", "CLAUDE.md"]
    assert [f for f in agents if not revise.scope_violations([f])] == []
    assert revise.scope_violations(["app.py", "tests/test_app.py", "README.md"]) == []


def test_documents_are_documents_and_nothing_that_runs_is_one():
    for f in ("README.md", "docs/guide.html", "CHANGES.rst", "notes.txt"):
        assert automerge.is_document(f), f
    for f in ("AGENTS.md", "CLAUDE.md", "requirements.txt", "app.py", ".github/README.md"):
        assert not automerge.is_document(f), f


def test_a_fix_record_without_kind_or_test_never_merges(merging):
    """A line kept before merging judged the diff has no test: it waits for you."""
    lp, fake, _ = merging
    own_fix(lp, fake)
    d = json.loads(fixes.path(lp.cfg.home).read_text())
    for h in d[f"{REPO}#14"]["history"]:
        h.pop("test", None)
        h["kind"] = None
    fixes.path(lp.cfg.home).write_text(json.dumps(d))
    poll(lp)
    assert fake.merged == [] and "reproducing test" in why_refused(lp)


# -- releasing is its own switch, off --------------------------------------------------------------
def test_a_merge_is_not_released_until_the_third_switch_is_on(merging):
    lp, fake, _ = merging
    fake.policy_text = fake.policy_text  # the default policy: release.on_merge patch
    own_fix(lp, fake)
    poll(lp)
    assert fake.merged == [40] and fake.releases == []
    assert switch.get(lp.cfg.home, REPO)["release"] is False
    with pytest.raises(ValueError, match="merging is off"):
        switch.set_merge(lp.cfg.home, REPO, False)
        switch.set_release(lp.cfg.home, REPO, True)


def test_the_release_switch_reads_a_stored_null_or_text_as_off(world):
    lp, _, _ = world
    p = switch.path(lp.cfg.home)
    p.parent.mkdir(parents=True, exist_ok=True)
    for v in (None, "yes", 1):
        p.write_text(json.dumps({REPO: {"on": True, "merge": True, "release": v}}))
        assert switch.releases(lp.cfg.home, REPO) is False
    p.write_text(json.dumps({REPO: {"on": True, "merge": True}}))
    assert switch.releases(lp.cfg.home, REPO) is False
    switch.set_merge(lp.cfg.home, REPO, True)
    switch.set_release(lp.cfg.home, REPO, True)
    assert switch.releases(lp.cfg.home, REPO)
    switch.set_merge(lp.cfg.home, REPO, False)
    switch.set_merge(lp.cfg.home, REPO, True)
    assert not switch.releases(lp.cfg.home, REPO)  # never comes back by itself


def test_the_page_asks_for_the_release_switch_only_with_merging_on(world):
    from repo_factory.ui import api

    lp, _, _ = world
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop = lambda: lp
    switch.set_on(lp.cfg.home, REPO, True)
    with pytest.raises(api.HttpError, match="merging is off"):
        a.maintenance_release({"repo": REPO, "on": True})
    a.maintenance_merge({"repo": REPO, "on": True})
    out = a.maintenance_release({"repo": REPO, "on": True})
    assert out["repos"][0]["switch"]["release"] is True


# -- a proposal carries its kind, sanitised --------------------------------------------------------
def test_a_proposal_is_opened_with_its_kind_and_sanitised(world):
    lp, fake, _ = world
    switch.set_on(lp.cfg.home, REPO, True)
    text = json.dumps(
        {
            "proposals": [
                {
                    "title": "Add CSV export @everyone",
                    "why": "See [here](https://evil.example/x)",
                    "kind": "feature",
                },
                {"title": "Tidy", "why": "w", "kind": "nonsense"},
            ]
        }
    )
    got = proposals.parse(text)
    assert got[0]["title"] == "Add CSV export `@everyone`" and "evil.example" not in got[0]["why"]
    assert got[1]["kind"] == "task"
    d = proposals.path(lp.cfg.home)
    d.parent.mkdir(parents=True, exist_ok=True)
    # a list kept before the rule, unsanitised: shown and posted sanitised
    raw = {**got[0], "title": "Ping @someone", "why": "<!-- x --> https://evil.example"}
    d.write_text(json.dumps({REPO: {"items": [raw]}}))
    shown = proposals.of(lp.cfg.home, REPO)["items"][0]
    assert shown["title"] == "Ping `@someone`" and "evil.example" not in shown["why"]
    issue = proposals.build(lp, REPO, raw["id"])
    new = fake.issues[issue["number"]]
    assert [x["name"] for x in new["labels"]] == ["go", "feature"]
    assert new["title"] == "Ping `@someone`"


# -- an issue's title, neutralised where it is copied ---------------------------------------------
@pytest.mark.parametrize(
    "title",
    [
        "Crash on save, closes #9",
        "fixes #12 and resolves GH-3",
        "see https://github.com/o/r/issues/9 @ann",
    ],
)
def test_a_title_closes_nothing_and_pings_nobody(title):
    t = revise.safe_title(title)
    assert not revise.FIXES.search(t), t
    assert "https://" not in t and "@ann" not in t.replace("`@ann`", "")
    assert not revise.linked(
        [{"is_pr": True, "number": 1, "title": f"Fix #2: {t}", "body": ""}]
    ).get(9)


def test_a_fix_copies_the_title_neutralised(world):
    from .test_f3_maintenance import quiet
    from .test_maintain_revise import Scripted, names_test
    from .test_maintain_revise import fixes as fixes_step

    lp, fake, _ = world
    switch.set_on(lp.cfg.home, REPO, True)
    fake.issues[14]["title"] = "A huge id gives a 500, closes #9"
    res = quiet(lp, Scripted(names_test(), fixes_step)).run()
    assert res["outcome"] == "pr", res
    pr = fake.issues[res["pr"]]
    copied = pr["title"].split(": ", 1)[1]  # after repo-factory's own "Fix #14: "
    assert "closes # 9" in copied and not revise.FIXES.search(copied)
    # its own "Fix #14" and "Fixes #14" link #14 only, never #9
    assert revise.linked([{**pr, "is_pr": True}]) == {14: [res["pr"]]}


# -- the default branch, read once a poll ---------------------------------------------------------
def test_the_default_branch_is_read_and_used(world, monkeypatch):
    lp, fake, _ = world
    fake.branch = "trunk"
    r = Repo(Gh(run=fake), REPO)
    assert r.default_branch() == "trunk" and r.base == "trunk"
    switch.set_on(lp.cfg.home, REPO, True)
    monkeypatch.setattr(revise, "start", lambda *a, **k: {"pid": None, "task": "/nonexistent"})
    watch.tick(lp)
    heads = [p for m, p, _ in fake.calls if "/commits/" in p and "check-runs" not in p]
    assert heads and all("/commits/trunk" in p for p in heads), heads
    trees = [p for m, p, _ in fake.calls if "/git/trees/" in p]
    assert trees and all("/git/trees/trunk" in p for p in trees), trees


def test_a_default_branch_answer_without_a_name_is_main():
    class G:
        def _run(self, args, cwd):
            return 0, json.dumps({"full_name": "o/p"}), ""

    assert Repo(G(), "o/p").default_branch() == "main"


# -- the small ones -------------------------------------------------------------------------------
def test_the_setup_creates_missing_labels_only():
    made = []

    def gh(*a):
        if a[:2] == ("label", "list"):
            return json.dumps([{"name": "bug", "color": "d73a4a", "description": "theirs"}])
        made.append(a)
        return ""

    res = labels.apply(gh, REPO, missing_only=True)
    assert "bug" in res["differs"] and not any(a[2] == "bug" for a in made)
    assert len(made) == len(labels.LABELS) - 1


def test_check_runs_are_read_on_every_page():
    class R:
        full = REPO

        def get(self, path, paginate=False):
            assert paginate
            ok = {"name": "a", "status": "completed", "conclusion": "success"}
            bad = {"name": "b", "status": "completed", "conclusion": "failure"}
            return [{"check_runs": [ok] * 100}, {"check_runs": [bad]}]

    got = automerge.checks(R(), "h1")
    assert got["state"] == "red" and got["runs"] == 101


def test_you_includes_the_token_files_login(world, tmp_path):
    lp, fake, _ = world

    class TokenGh:
        env_file = tmp_path / "token.env"
        login_name = None

        def _run(self, args, cwd):
            return (0, "factory-bot\n", "") if args[:2] == ["api", "user"] else fake(args, cwd)

    seen: dict = {}
    assert watch.acting_login(TokenGh(), seen) == "factory-bot"
    assert seen == {str(TokenGh.env_file): "factory-bot"}
    assert watch.acting_login(Gh(run=fake)) is None
