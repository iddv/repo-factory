"""Settings › Publishing › Projects (PJ-PLAN.md §5.4, PJ-9): every setting readable with its check, written
factory-wide to loop.yaml or per project, refused in words; the autonomy preview counts what would
flow with one setting changed (no write, no model); Spend's per-project rows."""

import time

import pytest
import yaml

from repo_factory.projects import changes as X
from repo_factory.projects.record import Projects, create_for_idea
from repo_factory.ui import api

from .app_common import call
from .loop_common import make_loop
from .test_projects import published

pytestmark = pytest.mark.usefixtures("projects_on")


def world(tmp_path):
    lp = make_loop(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump({"concurrency": 1}))
    p, _ = create_for_idea(Projects(lp.ledger), published(lp, "Receipts", "receipts"))
    for i, (cls, conf) in enumerate((("patch", 0.9), ("patch", 0.3), ("minor", 0.8))):
        X.add(
            lp.ledger,
            p["id"],
            {
                "title": f"idea {i} about receipts",
                "kind": "maintenance" if cls == "patch" else "feature",
                "class": cls,
                "confidence": conf,
                "evidence": ["repo:x"],
                "runs": {"agreeing": 1},
            },
        )
    return lp, p, api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)


def test_the_settings_read_and_write_with_their_checks(tmp_path):
    lp, p, app = world(tmp_path)
    code, s = call(app, "GET", "/api/projects/settings")
    assert code == 200 and s["settings"]["autonomy.preset"]["value"] == "quiet"
    assert s["settings"]["autonomy.scope"]["choices"] == ["ask", "list", None]
    assert s["autonomy"] == {"patch": "auto", "minor": "ask", "scope": "ask"}
    code, s = call(
        app, "POST", "/api/projects/settings", None, {"key": "evolve.every_days", "value": 14}
    )
    assert code == 200 and s["settings"]["evolve.every_days"]["value"] == 14
    assert (
        yaml.safe_load((lp.cfg.home / "loop.yaml").read_text())["projects"]["evolve"]["every_days"]
        == 14
    )
    code, err = call(
        app, "POST", "/api/projects/settings", None, {"key": "autonomy.scope", "value": "auto"}
    )
    assert code == 400 and "autonomy.scope is one of" in err["error"]
    code, pg = call(
        app,
        "POST",
        "/api/projects/settings",
        None,
        {"key": "autonomy.preset", "value": "hands-on", "project": p["id"]},
    )
    assert code == 200 and pg["autonomy"] == {"patch": "ask", "minor": "ask", "scope": "ask"}
    assert Projects(lp.ledger).must(p["id"])["settings"] == {"autonomy.preset": "hands-on"}
    code, pg = call(
        app,
        "POST",
        "/api/projects/settings",
        None,
        {"key": "autonomy.preset", "value": None, "project": p["id"]},
    )
    assert code == 200 and Projects(lp.ledger).must(p["id"])["settings"] == {}
    code, err = call(
        app,
        "POST",
        "/api/projects/settings",
        None,
        {"key": "enabled", "value": False, "project": p["id"]},
    )
    assert code == 400 and "factory-wide" in err["error"]


def test_the_preview_counts_what_would_flow(tmp_path):
    lp, p, app = world(tmp_path)
    code, now = call(app, "GET", "/api/projects/preview")
    assert now == {"file": 1, "ask": 1, "list": 1, "total": 3}
    code, more = call(
        app, "GET", "/api/projects/preview", {"key": "gate.min_confidence", "value": "0.2"}
    )
    assert more["file"] == 2
    code, hands = call(
        app, "GET", "/api/projects/preview", {"key": "autonomy.preset", "value": "hands-on"}
    )
    assert hands["file"] == 0 and hands["ask"] == 2
    assert yaml.safe_load((lp.cfg.home / "loop.yaml").read_text()) == {"concurrency": 1}


def test_spend_has_a_row_per_project(tmp_path):
    lp, p, app = world(tmp_path)
    lp.ledger.spend("evolve", 0.17, None, project=p["id"])
    lp.ledger.spend("charter", 0.13, None, project=p["id"])
    code, out = call(app, "GET", "/api/spend/breakdown")
    row = next(r for r in out["projects"] if r["id"] == p["id"])
    assert row["kinds"] == {"charter": 0.13, "evolve": 0.17} and row["all_usd"] == 0.3
    assert row["month_usd"] == 0.3 and time.time() > 0


def test_a_projects_own_preview_and_its_agreeing_runs(tmp_path):
    """The pane's preview counts with the project's own override, as Save would write it; a
    project's gate.min_agree_runs.<class> is read under that whole key."""
    lp, p, app = world(tmp_path)
    code, base = call(app, "GET", "/api/projects/preview", {"project": p["id"]})
    assert base == {"file": 1, "ask": 1, "list": 1, "total": 3}, "a minor idea asks after one run"
    code, two = call(
        app,
        "GET",
        "/api/projects/preview",
        {"key": "gate.min_agree_runs.minor", "value": "2", "project": p["id"]},
    )
    assert code == 200 and two["ask"] == 0, "the owner may still ask for two"
    Projects(lp.ledger).update(p["id"], settings={"gate.min_agree_runs.minor": 2})
    card = {"class": "minor", "confidence": 0.8, "runs": {"agreeing": 1}, "kind": "feature"}
    assert X.decide(lp.cfg.data, Projects(lp.ledger).must(p["id"]), card)[0] == "list"
    assert X.decide(lp.cfg.data, {**p, "settings": {}}, card)[0] == "ask"


def test_every_change_idea_is_listed_with_its_project(tmp_path):
    """Ideas' Changes chip reads GET api/changes: every change idea, its project's id and name."""
    lp, p, app = world(tmp_path)
    code, out = call(app, "GET", "/api/changes")
    assert code == 200 and len(out["changes"]) == 3
    x = out["changes"][0]
    assert x["project"] == {"id": p["id"], "name": "Receipts"}
    assert {"id", "title", "kind", "class", "state", "ask", "updated"} <= set(x)
    assert {c["class"] for c in out["changes"]} == {"patch", "minor"}


def test_pause_resume_and_archive_a_project(tmp_path):
    from repo_factory.projects import evolve

    lp, p, app = world(tmp_path)
    body = {
        "kind": "pause_project",
        "subject": {"kind": "new"},
        "origin": "ui",
        "fields": {"project": p["id"], "state": "paused"},
    }
    code, out = call(app, "POST", "/api/act", None, body)
    assert code == 200 and out["say"].startswith("Paused: Receipts.")
    q = Projects(lp.ledger).must(p["id"])
    assert q["state"] == "paused" and evolve.due(lp, q, time.time() + 30 * 86400) is None
    assert call(app, "POST", "/api/act", None, body)[0] == 409  # already paused
    code, lst = call(app, "GET", "/api/projects")
    assert lst["projects"][0]["health"] == "paused"
    body["fields"]["state"] = "archived"
    assert call(app, "POST", "/api/act", None, body)[0] == 200
    code, lst = call(app, "GET", "/api/projects")
    assert lst["projects"] == [] and lst["archived"] == 1
    notes = [e["note"] for e in Projects(lp.ledger).events(p["id"], limit=2)]
    assert notes == ["archived (ui)", "paused (ui)"]
