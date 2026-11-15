"""A maintained repo without a working AGENTS.md is a repo-level condition, never a per-issue
failure: readiness compares the fix with main, so what already fails on main is main's, and
the fix goes on. One row on the repo says what is wrong (``revise.agents_state`` and
``agents_rows``, which Re-check reads). A fake GitHub behind a fake ``gh``, a local bare repo as
the origin: no model, no network."""

import json
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote

import pytest

from repo_factory.maintain import fixes as fixes_record
from repo_factory.maintain import github, revise, switch

from .maintain_common import (
    FILES,
    GIT,
    OWNER,
    REPO,
    S5,
    FakeHub,
    Scripted,
    fix,
    fixes,
    make_origin,
    names_test,
    published,
    stand_in,
)

pytestmark = pytest.mark.usefixtures("maintenance_on")

AGENTS = FILES["AGENTS.md"]
UI = {"origin": "ui", "name": "ui", "actions": None}


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
    """The fake, plus the contents API (``contents``: AGENTS.md and the rest), main's commit
    and main's tree (the drift doctor)."""

    def __init__(self, *a, files=None, **kw):
        super().__init__(*a, **kw)
        self.contents, self.main_sha = dict(files or {}), "a" * 40

    def __call__(self, args, cwd=None):
        a = list(args)
        if a and a[0] == "api":
            path = a[-1].split("?")[0]
            base = f"repos/{self.repo}/contents/"
            if path.startswith(base) and not path.endswith(".github/repo-factory.yml"):
                self.calls.append(("GET", a[-1], None))
                name = unquote(path[len(base) :])
                if name not in self.contents and name.startswith(".github/workflows/"):
                    return 0, FILES.get(name, ""), ""  # main's workflow: GitHub's runners
                return (
                    (0, self.contents[name], "")
                    if name in self.contents
                    else (1, "", "gh: Not Found (HTTP 404)")
                )
            if path == f"repos/{self.repo}/commits/main":
                self.calls.append(("GET", a[-1], None))
                return 0, json.dumps({"sha": self.main_sha}), ""
            if path == f"repos/{self.repo}/git/trees/main":  # the drift doctor
                self.calls.append(("GET", a[-1], None))
                names = [p for p in FILES if p != "AGENTS.md"] + list(self.contents)
                return 0, json.dumps({"tree": [{"path": p, "type": "blob"} for p in names]}), ""
        return super().__call__(args, cwd)


def world(tmp_path, monkeypatch, origin):
    lp, fake, _ = published(tmp_path, [S5], hub=Hub)
    fake.origin = origin
    stand_in(monkeypatch, REPO, origin)
    fake.label(14, "go", OWNER)
    switch.set_on(lp.cfg.home, REPO, True)
    return lp, fake


@pytest.fixture
def held(tmp_path, monkeypatch):
    """Plotkeeper published, maintained, issue #14 labelled go by you, and no AGENTS.md on
    main."""
    return world(tmp_path, monkeypatch, origin_without_agents(tmp_path / "o"))


@pytest.fixture
def with_agents(tmp_path, monkeypatch):
    """Plotkeeper with its AGENTS.md on main."""
    lp, fake = world(tmp_path, monkeypatch, make_origin(tmp_path / "o"))
    fake.contents["AGENTS.md"] = AGENTS
    return lp, fake


def state(lp, rec):
    return revise.agents_state(github.Repo(lp.gh, REPO), rec, time.time())


# -- one row says what is wrong ------------------------------------------------------------------
def test_a_repo_without_agents_md_has_one_repo_row(held):
    lp, fake = held
    rec = {"agents_md": state(lp, {})}
    assert rec["agents_md"]["state"] == "missing"
    rows = revise.agents_rows(REPO, rec)
    assert len(rows) == 1 and rows[0]["kind"] == "agents_md" and rows[0]["held"] == []
    assert rows[0]["title"] == (
        "AGENTS.md is missing: fixes go on, but none can show the commands still work"
    )
    fake.contents["AGENTS.md"] = AGENTS
    rec = {"agents_md": state(lp, rec)}
    assert rec["agents_md"]["state"] == "ok" and revise.agents_rows(REPO, rec) == []


def test_a_broken_fence_is_said_on_the_row_and_holds_nothing(held):
    lp, fake = held
    fake.contents["AGENTS.md"] = "# AGENTS.md\n\nNo commands here.\n"
    rec = {"agents_md": state(lp, {})}
    assert rec["agents_md"]["state"] == "broken"
    assert "no shell commands under a setup heading" in rec["agents_md"]["why"]
    row = revise.agents_rows(REPO, rec)[0]
    assert row["title"] == rec["agents_md"]["why"]  # the file is there: never "set up"
    assert row["url"].endswith("/blob/main/AGENTS.md")


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


def test_commands_that_fail_on_main_are_mains_not_the_fixs(with_agents):
    """Driftwatch: "FAILED: 4 commands, 3 problems" on main held every fix for 72 hours. The
    same failures on the fix are main's: the fix opens its PR and the body says so; the repo's
    row says what fails on main once main has not moved since."""
    lp, fake = with_agents
    out = fix(lp, Scripted(names_test(), fixes), check=check_of(DW_FAIL, DW_FAIL)).run()
    assert out["outcome"] == "pr", (out.get("stage"), out.get("why"))
    body = fake.issues[out["pr"]]["body"]
    assert "(2 already on main, not this fix's)" in body
    fake.main_sha = out["agents_main"]["sha"]
    rec = state(lp, {"agents_main": out["agents_main"]})
    assert rec["state"] == "broken" and "do not all run on main" in rec["why"]


def test_a_command_the_fix_breaks_stops_it(with_agents):
    lp, fake = with_agents
    broke = "FAIL  `./plots.sh test` exited 1:\nboom\nFAILED: 3 commands, 1 problems"
    out = fix(
        lp, Scripted(names_test(), fixes), check=check_of("passed: 3 commands, 0 problems", broke)
    ).run()
    assert out["outcome"] == "failed" and out["stage"] == "readiness"
    assert "ran on main fail on the fix: `./plots.sh test`" in out["why"]
    assert fixes_record.attempts(fixes_record.get(lp.cfg.home, REPO, 14)) == 1
    assert fake.comments_of(14) == []


# -- a retry is told why the last attempt stopped; repo-factory's own error is not hidden ---------
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


def test_an_error_in_factory_is_a_failed_attempt_said_on_the_page(held, monkeypatch):
    lp, fake = held

    def boom(self):
        raise RuntimeError("the sandbox could not start")

    monkeypatch.setattr(revise.Fix, "check_main", boom)
    out = fix(lp, Scripted()).run()
    assert out["outcome"] == "failed" and out["stage"] == "error"
    rec = fixes_record.get(lp.cfg.home, REPO, 14)
    assert "the sandbox could not start" in rec["why"]
    assert fake.comments_of(14) == [] and "needs-you" not in fake.labels_of(14)
