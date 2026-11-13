"""Pause maintenance on one project (the owner, 2026-10-29: "a simple way to pause maintenance
on a project"; ``maintain/pauses.py``). A product repo-factory built had no switch in repo-factory: only
``status:`` in its ``.github/repo-factory.yml``, a pull request the owner merges. The verb
``pause_maintenance`` writes the pause in repo-factory at once; while it holds, the poll makes no fix
run, merge, release, label, comment or model call on that repo; resuming picks up from the next
poll. On the fake GitHub of the triage tests (``o/plots``, six new issues)."""

import pytest

from repo_factory import registry as R
from repo_factory.maintain import pauses, poll
from repo_factory.projects import record
from repo_factory.projects.record import Projects
from repo_factory.ui import api

from .test_maintain_triage import REPO, app, world  # noqa: F401 - fixtures

pytestmark = pytest.mark.usefixtures("maintenance_on")


def _project(lp, iid):
    p, _ = record.create_for_idea(Projects(lp.ledger), lp.ledger.get(iid))
    return p["id"]


def _act(a, kind, fields, origin="ui"):
    return R.run(
        R.Ctx(app=a, origin=origin, confirm=True), kind, fields, R.Subject("new"), record=False
    )


def test_a_paused_products_poll_does_nothing_and_resume_restores_it(app):  # noqa: F811
    a, lp, fake, iid = app
    pid = _project(lp, iid)
    out = _act(a, "pause_maintenance", {"project": pid, "state": "paused"})
    assert out["say"].startswith("Maintenance paused on Plotkeeper: no fix, merge, release")
    assert pauses.get(lp.cfg.home, REPO)["by"] == "ui"
    # the poll: nothing on the paused repo; no write and no model call anywhere. (The
    # project's own stats, a clone and its star count, still read: that is the project's life,
    # which Pause project governs, not maintenance.)
    got = lp.maintain()
    assert got["repos"] == []
    assert fake.writes() == [] and lp._triage.prompts == []
    assert not [c for c in fake.calls if REPO in str(c[1]) and "issues" in str(c[1])]
    assert all(not fake.comments.get(n) for n in range(1, 7))
    assert REPO not in [t["repo"] for t in poll.targets(lp, polling=True)]
    # every other reader sees the pause
    t = next(t for t in poll.targets(lp) if t["repo"] == REPO)
    assert t["paused"]["by"] == "ui"
    row = record.listing(lp)["projects"][0]
    assert row["health"] == "maintenance_paused"
    assert row["why"].startswith("Maintenance paused since ") and row["why"].endswith(", by you")
    assert row["maintenance_paused"]["by"] == "ui"
    assert a.maintain_summary()["repos"][0]["paused"].startswith("Maintenance paused since ")
    # twice is refused in words
    with pytest.raises(api.HttpError, match="already paused"):
        _act(a, "pause_maintenance", {"project": pid, "state": "paused"})
    # resume: the next poll triages as before
    out = _act(a, "pause_maintenance", {"project": pid, "state": "on"})
    assert out["say"] == "Maintenance resumed on Plotkeeper: it picks up from the next poll."
    assert pauses.get(lp.cfg.home, REPO) is None
    got = lp.maintain()
    assert sorted(t["number"] for t in got["repos"][0]["triaged"]) == [1, 2, 3, 4, 5, 6]
    assert record.listing(lp)["projects"][0]["health"] != "maintenance_paused"
    ev = [e["note"] for e in Projects(lp.ledger).events(pid)]
    assert "maintenance resumed (ui)" in ev and "maintenance paused (ui)" in ev


def test_the_repos_policy_file_is_never_touched(app):  # noqa: F811
    a, lp, fake, iid = app
    pid = _project(lp, iid)
    _act(a, "pause_maintenance", {"project": pid, "state": "paused"})
    _act(a, "pause_maintenance", {"project": pid, "state": "on"})
    assert not [c for c in fake.calls if c[0] != "GET"], "no pull request, no write on GitHub"


def test_a_pause_file_that_cannot_be_read_pauses_nothing(tmp_path):
    from .loop_common import make_loop

    lp = make_loop(tmp_path)
    pauses.path(lp.cfg.home).parent.mkdir(parents=True, exist_ok=True)
    for text in ("not json", "[1, 2]", '{"o/x": null, "o/y": 3}', "null"):
        pauses.path(lp.cfg.home).write_text(text)
        assert pauses.read(lp.cfg.home) == {}
    assert pauses.words(None) is None


def test_pause_project_says_maintenance_goes_on():
    a = R.ACTIONS["pause_project"]
    assert "Maintenance of its repo goes on: Pause maintenance stops that." in a.says
    v = R.ACTIONS["pause_maintenance"]
    assert v.title == "Pause maintenance" and v.click == "click"
