"""A drift finding follows main, not the calendar (track AM2, after Beliefline, 2026-10-13: its
AGENTS.md was merged at 20:10, and Today still said "AGENTS.md is missing" from a check two
days old, because the doctor ran every ``maintain.doctor_days``).

GitHub cannot push to a loopback-only repo-factory, so every poll reads main's head conditionally (a
304 when nothing moved, which costs no rate limit); when it moved, the doctor and the project's
drift run again in that poll. ``doctor_days`` is only the floor for an unchanged repo. Re-check
(registry ``maintain_recheck``, a read: no confirm) asks now, in the background, and the row
says how old its result is. A fake GitHub behind a fake ``gh``: no model, no network."""

import json
import os
import signal
import sys
import time

import pytest

from repo_factory import registry as R
from repo_factory.gh import Gh
from repo_factory.maintain import doctor, github, policy, poll, recheck
from repo_factory.ui import api

from .loop_common import make_loop
from .test_maintain_agents_held import AGENTS, UI, Hub, origin_without_agents
from .test_maintain_revise import REPO, S5

pytestmark = pytest.mark.usefixtures("maintenance_on")

STALE = "`make lint` is in AGENTS.md but not in the README"
CMD = "python -m pip install pytest==8.3.3 && python -m pytest tests/acceptance"
# the merged fix: AGENTS.md that matches the tree and the README, and the maintenance files
FIXED = {
    "AGENTS.md": f"# AGENTS.md — Plots\n\n## Setup, run, test\n\n```sh\n{CMD}\n```\n\n"
    "## Architecture in brief\n\n| Path | What |\n|---|---|\n| `app.py` | the app |\n",
    "README.md": f"# Plots\n\n## Tests\n\n```sh\n{CMD}\n```\n",
    "CLAUDE.md": "See AGENTS.md.\n",
    ".github/repo-factory.yml": "(served from the policy)",
    ".github/labels.yml": "- name: go\n",
}


class Conditional(Hub):
    """Main's head as GitHub answers ``Accept: application/vnd.github.sha`` with ``-i``: its
    ETag, and a 304 (gh exits 1) when ``If-None-Match`` names the head that is still there."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.heads: list[str] = []  # 200 or 304, one per conditional read

    def __call__(self, args, cwd=None):
        a = list(args)
        if a[:2] == ["api", "-i"] and a[-1] == f"repos/{self.repo}/commits/main":
            tag = f'"{self.main_sha}"'
            if any(x == f"If-None-Match: {tag}" for x in a):
                self.heads.append("304")
                return 1, f"HTTP/2.0 304 Not Modified\nEtag: {tag}\n\n", "gh: HTTP 304"
            self.heads.append("200")
            return (
                0,
                f"HTTP/2.0 200 OK\nEtag: {tag}\nX-Ratelimit-Remaining: 4999\n\n{self.main_sha}",
                "",
            )
        return super().__call__(args, cwd)

    def trees(self) -> int:
        return sum(1 for c in self.calls if "/git/trees/" in c[1])


@pytest.fixture
def drifted(tmp_path):
    """Plotkeeper, published and maintained, AGENTS.md on main; the doctor's last result (an
    hour old, so not due by days) was computed at main ``a…`` and says it drifted."""
    fake = Conditional(
        [S5],
        policy_text=policy.render({"status": "maintained"}),
        origin=origin_without_agents(tmp_path),
        files={"AGENTS.md": AGENTS},
    )
    lp = make_loop(
        tmp_path, maintain={"revise_command": [sys.executable, "-c", "pass"], "concurrency": 0}
    )
    lp._gh = Gh(run=fake)
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": REPO, "url": f"https://github.com/{REPO}"},
    )
    github.Cache(lp.cfg.home).write_repo(
        REPO,
        {
            "doctor": {
                "at": time.time() - 3600,
                "problems": [STALE],
                "self_hosted": [],
                "head": "a" * 40,
            }
        },
    )
    return lp, fake


def kinds(lp):
    return [x["kind"] for x in poll.today(lp.cfg.home)["rows"] if x["repo"] == REPO]


# -- the conditional read -------------------------------------------------------------------------
def test_head_reads_the_sha_and_etag_and_a_304_is_not_a_change():
    calls = []

    def run(args, cwd=None):
        calls.append(list(args))
        if any(x.startswith("If-None-Match") for x in args):
            return 1, 'HTTP/2.0 304 Not Modified\r\nEtag: "abc"\r\n\r\n', "gh: HTTP 304"
        return 0, 'HTTP/2.0 200 OK\r\nEtag: "abc"\r\n\r\n' + "c" * 40, ""

    r = github.Repo(Gh(run=run), "o/r")
    assert r.head("main") == {"sha": "c" * 40, "etag": '"abc"', "changed": True}
    assert calls[0][:4] == ["api", "-i", "-H", "Accept: application/vnd.github.sha"]
    assert r.head("main", '"abc"') == {"sha": None, "etag": '"abc"', "changed": False}
    assert 'If-None-Match: "abc"' in calls[1]
    # a fake (or a proxy) that answers the commit as JSON, without headers, still works
    plain = github.Repo(Gh(run=lambda a, c=None: (0, json.dumps({"sha": "d" * 40}), "")), "o/r")
    assert plain.head("main")["sha"] == "d" * 40
    broken = github.Repo(Gh(run=lambda a, c=None: (1, "", "gh: Not Found (HTTP 404)")), "o/r")
    with pytest.raises(github.GitHubError):
        broken.head("main")


def test_the_doctor_is_due_when_main_moved_or_after_a_backfill_and_by_days_otherwise():
    now = time.time()
    rec = {"doctor": {"at": now - 60, "problems": [], "head": "a" * 40}}
    assert not doctor.due(rec, now, 7, "a" * 40)
    assert doctor.due(rec, now, 7, "b" * 40), "main moved"
    assert not doctor.due(rec, now, 7, None), "head unknown: the days decide"
    assert doctor.due({**rec, "backfill_done": now - 30}, now, 7, "a" * 40)
    assert doctor.due({"doctor": {**rec["doctor"], "at": now - 8 * 86400}}, now, 7, "a" * 40)
    assert not doctor.due(rec, now, 0, "b" * 40), "0: never"


# -- the poll: a merged fix clears the row at the next poll ---------------------------------------
def test_a_merged_agents_md_clears_the_drift_row_on_the_next_poll(drifted):
    lp, fake = drifted
    poll.maintain_tick(lp)
    # main is where the result was computed: a 304 later, nothing is read again
    assert kinds(lp) == ["drift"] and fake.trees() == 0
    row = [x for x in poll.today(lp.cfg.home)["rows"] if x["kind"] == "drift"][0]
    assert row["checked"] and row["head"] == "a" * 7 and row["problems"] == [STALE]
    poll.maintain_tick(lp, now=time.time() + 900)
    assert fake.heads == ["200", "304"], "the second poll asked with the ETag: a free 304"
    assert kinds(lp) == ["drift"] and fake.trees() == 0
    # the owner merges the fix (AGENTS.md as the tree and the README say): main moves
    fake.files.update(FIXED)
    fake.main_sha = "b" * 40
    poll.maintain_tick(lp, now=time.time() + 1800)
    assert fake.heads[-1] == "200" and fake.trees() == 1
    assert "drift" not in kinds(lp), "cleared within one poll of the merge, not days"
    rec = github.Cache(lp.cfg.home).repo(REPO)
    assert rec["doctor"]["head"] == "b" * 40 and rec["doctor"]["problems"] == []
    assert rec["head"]["sha"] == "b" * 40 and rec["head"]["moved"]
    assert fake.writes() == [], "reads only"


def test_the_projects_drift_of_a_moved_repo_refreshes_first_whatever_its_age(tmp_path):
    from repo_factory.projects import life

    lp = make_loop(tmp_path)
    p = {
        "id": "p1",
        "repos": [{"repo": REPO}],
        "stats": {"clone_head": "a" * 40, "at": time.time()},
    }
    cache = {"repos": {REPO: {"head": {"sha": "a" * 40}}}}
    assert not life.head_moved(lp.cfg.home, p, cache)
    cache["repos"][REPO]["head"]["sha"] = "b" * 40
    assert life.head_moved(lp.cfg.home, p, cache)
    assert not life.head_moved(lp.cfg.home, {**p, "stats": {}}, cache), "never refreshed: by age"


# -- Re-check -------------------------------------------------------------------------------------
def press(app, repo=REPO, who=UI):
    body = {"kind": "maintain_recheck", "subject": {"kind": "new"}, "fields": {"repo": repo}}
    code, _, data = api.handle(
        "POST", "/api/act", None, json.dumps(body).encode(), app=app, identity=who
    )
    return code, json.loads(data)


def test_recheck_is_a_read_with_no_confirm_and_runs_in_the_background(drifted):
    from repo_factory import proc

    lp, fake = drifted
    poll.maintain_tick(lp)
    lp.cfg.data["maintain"]["recheck_command"] = [
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
    ]
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    app.loop, app.loop_cfg = (lambda: lp), (lambda: lp.cfg)
    a = R.get("maintain_recheck")
    assert a.click == "click" and not R.door_refuses("agent:rc", "maintain_recheck")
    code, out = press(app, repo="someone/else")
    assert code == 409 and "not a repository repo-factory maintains" in out["error"]
    t0 = time.time()
    code, out = press(app)
    assert code == 200 and time.time() - t0 < 5, "no request waits on it"
    assert "in the background" in out["say"]
    st = recheck.job_state(lp.cfg.home, REPO)
    try:
        assert st["state"] == "running" and st["by"] == "ui"
        code, again = press(app)
        assert code == 200 and "being checked already" in again["say"]
        rows = [x for x in poll.today(lp.cfg.home)["rows"] if x["kind"] == "drift"]
        assert rows and rows[0]["recheck"]["state"] == "running", "the row says it at once"
        summary = app.maintain_summary()["repos"][0]
        assert summary["recheck"]["state"] == "running" and summary["doctor"]["head"] == "a" * 7
    finally:
        os.kill(st["pid"], signal.SIGKILL)
    deadline = time.time() + 10
    while time.time() < deadline and proc.running(st["pid"], st["start"]):
        time.sleep(0.1)
    st = recheck.job_state(lp.cfg.home, REPO)
    assert st["state"] == "failed" and "stopped without finishing" in st["why"]
    assert fake.writes() == []


def test_the_recheck_reads_main_now_and_replaces_only_the_doctors_rows(
    drifted, monkeypatch, capsys
):
    from repo_factory.cli import main
    from repo_factory.maintain import cli

    lp, fake = drifted
    poll.maintain_tick(lp)
    rec = github.Cache(lp.cfg.home).repo(REPO)
    rec["rows"] = rec["rows"] + [{"kind": "factory_pr", "number": 3, "title": "PR 3"}]
    github.Cache(lp.cfg.home).write_repo(REPO, rec)
    assert fake.trees() == 0
    fake.files.update(FIXED)  # merged a moment ago; the next poll is far off
    fake.main_sha = "b" * 40
    monkeypatch.setattr(cli, "_loop", lambda args: lp)  # the fake GitHub, never gh
    main(["maintain", "recheck", REPO, "--home", str(lp.cfg.home)])
    said = capsys.readouterr().out
    assert "AGENTS.md: current" in said and f"main at {'b' * 7}" in said
    assert fake.trees() == 1 and fake.heads[-1] == "200", "asked without the ETag"
    assert kinds(lp) == ["factory_pr"], "the drift row is gone; the poll's other rows stay"
    st = recheck.job_state(lp.cfg.home, REPO)
    assert st["state"] == "done" and st["by"] == "cli" and st["finished"]
    assert fake.writes() == []
    # a check that cannot read says why on the row's state
    fake.main_sha, real = "c" * 40, fake.__class__.__call__

    def no_tree(self, args, cwd=None):
        if "/git/trees/" in list(args)[-1]:
            return 1, "", "gh: Server Error (HTTP 502)"
        return real(self, args, cwd)

    monkeypatch.setattr(Conditional, "__call__", no_tree)
    with pytest.raises(SystemExit, match="502"):
        main(["maintain", "recheck", REPO, "--home", str(lp.cfg.home)])
    st = recheck.job_state(lp.cfg.home, REPO)
    assert st["state"] == "failed" and "502" in st["why"]


def test_the_command_line_press_goes_through_the_registry(drifted, monkeypatch):
    from repo_factory.maintain import cli

    seen = []
    monkeypatch.setattr(
        cli,
        "_act",
        lambda args, kind, fields, confirm=False: (
            seen.append((kind, fields, confirm)) or {"say": "ok"}
        ),
    )
    from repo_factory.cli import main

    main(["maintain", "recheck", REPO, "--detach"])
    assert seen == [("maintain_recheck", {"repo": REPO}, False)]


# -- the row says how old its result is ----------------------------------------------------------
def test_the_drift_row_says_its_age_and_carries_recheck():
    rows = doctor.rows(REPO, {"doctor": {"at": 1000.0, "problems": [STALE], "head": "f" * 40}})
    assert rows[0]["checked"] == 1000.0 and rows[0]["head"] == "f" * 7
    # the row's words and Re-check: pressed in pages/settings/maint.test.ts ("says when it was
    # checked and at which commit…", "carries the age in its words and Re-check first…", "is a
    # read: one press asks the registry, no confirm…", "says Checking… while it runs…")
