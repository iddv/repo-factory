"""A maintained repo without a working AGENTS.md is a repo-level condition, never a per-issue
failure (track AM, after Beliefline, 2026-10-13: every fix stopped at readiness with "AGENTS.md
is missing", spent both attempts on each issue and labelled it needs-you).

Since AU3 finding 4 (2026-10-16) it holds nothing: readiness compares the fix with main, so what
already fails on main is main's. Driftwatch's queue had been held 72 hours by such a hold,
including the docs fix that would have repaired it. One row on the repo says what is wrong, with
the press that fixes it (the backfill PR, registry ``maintain_backfill``); an issue labelled
needs-you only for a missing AGENTS.md is told the cause is gone. A fake GitHub behind a fake ``gh``, a local bare repo as the origin: no model,
no network."""

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import pytest

from repo_factory.gh import Gh
from repo_factory.maintain import github, policy, poll, revise
from repo_factory.ui import api

from .loop_common import make_loop
from .test_maintain_revise import (
    FILES,
    GIT,
    REPO,
    S5,
    FakeHub,
    Scripted,
    fix,
    fixes,
    issue,
    names_test,
)

pytestmark = pytest.mark.usefixtures("maintenance_on")

AGENTS = FILES["AGENTS.md"]


def origin_without_agents(tmp: Path) -> Path:
    src, bare = tmp / "product", tmp / "origin.git"
    for rel, text in FILES.items():
        if rel != "AGENTS.md":
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run([*GIT, "add", "-A"], cwd=src, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "Plots 0.1.0"], cwd=src, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


class Hub(FakeHub):
    """The revise fake, plus the contents API for AGENTS.md and main's commit."""

    def __init__(self, *a, files=None, **kw):
        super().__init__(*a, **kw)
        self.files, self.main_sha = dict(files or {}), "a" * 40

    def __call__(self, args, cwd=None):
        a = list(args)
        if a and a[0] == "api":
            path = a[-1].split("?")[0]
            base = f"repos/{self.repo}/contents/"
            if path.startswith(base) and not path.endswith(".github/repo-factory.yml"):
                self.calls.append(("GET", a[-1], None))
                name = unquote(path[len(base) :])
                return (
                    (0, self.files[name], "")
                    if name in self.files
                    else (1, "", "gh: Not Found (HTTP 404)")
                )
            if path == f"repos/{self.repo}/commits/main":
                self.calls.append(("GET", a[-1], None))
                return 0, json.dumps({"sha": self.main_sha}), ""
            if path == f"repos/{self.repo}/git/trees/main":  # the weekly drift doctor
                self.calls.append(("GET", a[-1], None))
                names = [p for p in FILES if p != "AGENTS.md"] + list(self.files)
                return 0, json.dumps({"tree": [{"path": p, "type": "blob"} for p in names]}), ""
        return super().__call__(args, cwd)


@pytest.fixture
def held(tmp_path):
    """Plotkeeper as Beliefline was: published, maintained, issue #14 labelled go, and no
    AGENTS.md on main."""
    fake = Hub(
        [S5],
        policy_text=policy.render({"status": "maintained"}),
        origin=origin_without_agents(tmp_path),
    )
    lp = make_loop(
        tmp_path,
        maintain={
            "revise_command": [sys.executable, "-c", "pass"],
            "backfill_command": [sys.executable, "-c", "import time; time.sleep(30)"],
        },
    )
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    return lp, fake


def step(lp, rec, slots=None, now=None):
    r = github.Repo(lp.gh, REPO)
    pol = policy.load(lp.gh, REPO)
    return revise.step(
        lp,
        r,
        poll.targets(lp)[0],
        r.issues(),
        pol,
        rec,
        now or time.time(),
        slots if slots is not None else [1],
    )


def labels(fake, n):
    return fake.labels_of(n)


# -- the poll: the fix starts, one row says what is wrong --------------------------------------
def test_a_repo_without_agents_md_still_gets_its_fix_and_one_repo_row(held):
    lp, fake = held
    rec, slots = {}, [1]
    got = step(lp, rec, slots)
    assert got["started"] == [14] and slots == [0] and "held" not in got
    assert "claim:repo-factory" in labels(fake, 14)
    assert rec["agents_md"]["state"] == "missing"
    rows = revise.agents_rows(REPO, rec)
    assert len(rows) == 1 and rows[0]["kind"] == "agents_md" and rows[0]["held"] == []
    assert rows[0]["title"] == (
        "AGENTS.md is missing: fixes go on, but none can show the commands still work"
    )


def test_a_stale_missing_finding_is_checked_again_at_once(held):
    """receipts (live, 2026-10-13): the weekly doctor still said "AGENTS.md is missing" days
    after it was merged. The poll, which reads AGENTS.md anyway, sees it there and drops the
    stale finding, so the doctor runs again this poll."""
    lp, fake = held
    fake.files["AGENTS.md"] = AGENTS
    rec = {"doctor": {"at": time.time() - 86400, "problems": ["AGENTS.md is missing"]}}
    step(lp, rec, [0])
    assert "doctor" not in rec and rec["agents_md"]["state"] == "ok"


def test_a_broken_fence_is_said_on_the_row_and_holds_nothing(held):
    lp, fake = held
    fake.files["AGENTS.md"] = "# AGENTS.md\n\nNo commands here.\n"
    rec = {}
    assert step(lp, rec)["started"] == [14]
    assert rec["agents_md"]["state"] == "broken"
    assert "no shell commands under a setup heading" in rec["agents_md"]["why"]
    row = revise.agents_rows(REPO, rec)[0]
    assert row["title"] == rec["agents_md"]["why"]  # the file is there: never "set up"
    assert row["url"].endswith("/blob/main/AGENTS.md")


# -- needs-you only for this reason: told the cause is gone, labels untouched --------------------
def test_needs_you_only_for_a_missing_agents_md_gets_one_cause_gone_comment(held):
    lp, fake = held
    why = "AGENTS.md's commands did not all run as written: AGENTS.md is missing"
    for n, stage, text in ((15, "readiness", why), (16, "verify", "regression: a test")):
        fake.issues[n] = issue(n, f"issue {n}", ["bug", "go", "needs-you"])
        fake.issues[n].setdefault("state", "open")
        fake.issues[n]["html_url"] = f"https://github.com/{REPO}/issues/{n}"
        for k in (1, 2):
            fake.comments.setdefault(n, []).append(
                {
                    "id": 1000 + n * 10 + k,
                    "body": revise.fail_comment(k, stage, text, 0.6, 2, None, k),
                    "user": {"login": "owner"},
                    "created_at": github.iso(time.time()),
                    "updated_at": github.iso(time.time()),
                }
            )
    rec = {}
    step(lp, rec)  # still missing: nothing said
    assert not any(revise.AGENTS_OK in b for b in fake.comments_of(15))
    fake.files["AGENTS.md"] = AGENTS
    got = step(lp, rec, [0])
    assert got["cause_gone"] == [15]
    said = fake.comments_of(15)[-1]
    assert said.startswith(revise.AGENTS_OK) and "Remove `needs-you`" in said
    assert not any(revise.AGENTS_OK in b for b in fake.comments_of(16)), "another reason"
    assert "needs-you" in labels(fake, 15) and "needs-you" in labels(fake, 16), "labels stay"
    n = len(fake.comments_of(15))
    step(lp, rec, [0])
    assert len(fake.comments_of(15)) == n, "once"


# -- the fix itself: readiness is no worse than main --------------------------------------------
DW_FAIL = (
    "FAIL  `python -m pytest tests/acceptance` exited 1:\n4 failed\n"
    "FAIL  `./driftwatch.sh check` exited 2:\nusage\n"
    "FAILED: 4 commands, 2 problems"
)  # Driftwatch's main, 2026-10-13 to 10-16


def check_of(main_output, fix_output):
    """The readiness check: ``main_output`` on the first call (main), ``fix_output`` after."""
    calls = []

    def run(root):
        out = main_output if not calls else fix_output
        calls.append(root)
        bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
        return {"ok": not bad, "output": out, "summary": (out.splitlines() or ["passed"])[-1]}

    return run


def test_a_fix_on_a_main_without_agents_md_goes_on(held):
    lp, fake = held
    h = Scripted(names_test(), fixes)
    out = fix(lp, h, check=check_of("AGENTS.md is missing", "AGENTS.md is missing")).run()
    assert out["outcome"] == "pr", (out.get("stage"), out.get("why"))
    assert out["agents_main"]["ok"] is False and out["agents_main"]["summary"] == revise.MISSING
    assert not [c for c in fake.comments_of(14) if "result=held" in c]


@pytest.fixture
def with_agents(tmp_path):
    """Plotkeeper with its AGENTS.md on main."""
    from .test_maintain_revise import make_origin

    fake = Hub(
        [S5],
        policy_text=policy.render({"status": "maintained"}),
        origin=make_origin(tmp_path),
        files={"AGENTS.md": AGENTS},
    )
    lp = make_loop(tmp_path, maintain={"revise_command": [sys.executable, "-c", "pass"]})
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper"},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    return lp, fake


def test_commands_that_fail_on_main_are_mains_not_the_fixs(with_agents):
    """Driftwatch: "FAILED: 4 commands, 3 problems" on main held every fix for 72 hours. The
    same failures on the fix are main's: the fix opens its PR and the body says so."""
    lp, fake = with_agents
    out = fix(lp, Scripted(names_test(), fixes), check=check_of(DW_FAIL, DW_FAIL)).run()
    assert out["outcome"] == "pr", (out.get("stage"), out.get("why"))
    body = fake.issues[out["pr"]]["body"]
    assert "(2 already on main, not this fix's)" in body
    # the poll learns main's state from the ended fix: the row says it, the queue goes on
    fake.main_sha = out["agents_main"]["sha"]
    task = revise.task_dir(lp.cfg.tasks_dir, REPO, 14)
    rec = {"revises": {"14": {"pid": None, "task": str(task), "started": time.time() - 60}}}
    step(lp, rec)
    assert rec["agents_md"]["state"] == "broken" and "held" not in rec["agents_md"]


def test_a_command_the_fix_breaks_stops_it(with_agents):
    lp, fake = with_agents
    broke = "FAIL  `./plots.sh test` exited 1:\nboom\nFAILED: 3 commands, 1 problems"
    out = fix(
        lp, Scripted(names_test(), fixes), check=check_of("passed: 3 commands, 0 problems", broke)
    ).run()
    assert out["outcome"] == "failed" and out["stage"] == "readiness"
    assert "ran on main fail on the fix: `./plots.sh test`" in out["why"]
    r = github.Repo(lp.gh, REPO)
    assert revise.attempts(r.comments(14)) == 1


# -- a retry is told why the last attempt stopped; repo-factory's own error is not retried -------------
def test_the_second_attempt_hears_why_the_first_stopped(with_agents):
    lp, fake = with_agents
    broke = "FAIL  `./plots.sh test` exited 1:\nboom\nFAILED: 3 commands, 1 problems"
    fix(
        lp, Scripted(names_test(), fixes), check=check_of("passed: 3 commands, 0 problems", broke)
    ).run()
    h = Scripted(names_test(), fixes)
    out = fix(lp, h).run()
    assert out["outcome"] == "pr" and out["attempt"] == 2
    build = h.prompts[1]
    assert "An earlier attempt at this issue stopped in the **readiness** stage" in build
    assert "./plots.sh test" in build and '<<<document name="the last attempt">>>' in build
    assert "earlier attempt" not in h.prompts[0], "the reproduce session is unchanged"


def test_an_error_in_factory_goes_to_the_owner_at_once(held, monkeypatch):
    lp, fake = held
    fake.files["AGENTS.md"] = AGENTS

    def boom(self):
        raise RuntimeError("the sandbox could not start")

    monkeypatch.setattr(revise.Fix, "check_main", boom)
    out = fix(lp, Scripted()).run()
    assert out["outcome"] == "failed" and out["stage"] == "error"
    assert "needs-you" in labels(fake, 14)
    last = fake.comments_of(14)[-1]
    assert "stage=error" in last and "labels it `needs-you`" in last


def test_previous_failure_reads_factory_own_comment_since_the_reset():
    c = revise.fail_comment(
        1, "verify", "regression: `t.py::a` passed on main", 0.3, 2, "repo-factory/1-x-attempt1", 1
    )
    got = revise.previous_failure([{"body": c}])
    assert got["stage"] == "verify" and got["why"].startswith("regression:")
    assert "t.py::a" in got["why"] and "attempt1" not in got["why"]
    assert revise.previous_failure([{"body": c}, {"body": f"{revise.RESET} n=1 -->"}]) is None
    assert revise.previous_failure([]) is None


# -- the press: registry maintain_backfill -------------------------------------------------------
UI = {"origin": "ui", "name": "ui", "actions": None}
CLI = {"origin": "cli", "name": "cli", "actions": None}
MCP = {"origin": "mcp", "name": "mcp", "actions": None}
AGENT = {"origin": "agent:RC", "name": "RC", "actions": None}


def press(app, who=UI, confirm=True, repo=REPO):
    body = {
        "kind": "maintain_backfill",
        "subject": {"kind": "new"},
        "fields": {"repo": repo},
        "confirm": confirm,
    }
    code, _, data = api.handle(
        "POST", "/api/act", None, json.dumps(body).encode(), app=app, identity=who
    )
    return code, json.loads(data)
