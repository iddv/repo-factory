"""Projects' record (PJ-PLAN.md §2, PJ-1): a published build or a confirmed adopted repo is a
project; a repo maps to one project; the backfill is idempotent; the list and the page read the
ledger and the poll's cache only (never a model, never GitHub)."""

import json
import os
import stat
import time

import pytest

from repo_factory.cli import main
from repo_factory.maintain.github import Cache
from repo_factory.projects import record
from repo_factory.projects.record import Projects, project_id
from repo_factory.settings.store import DEFAULTS, Config
from repo_factory.settings.write import PROJECTS, project_value
from repo_factory.ui import api

from .app_common import call
from .loop_common import make_loop

pytestmark = pytest.mark.usefixtures("projects_on")

LIVE = [
    ("FrontDesk Book", "frontdesk-book"),
    ("Lesson Ledger", "lesson-ledger"),
    ("Plotkeeper", "plotkeeper"),
    ("Receipts", "receipts"),
    ("Driftwatch", "driftwatch"),
    ("Beliefline", "beliefline"),
    ("Nextread", "nextread"),
    ("NoteHatch", "notehatch"),
]


def published(lp, product, name, state="public", owner="iddv"):
    """An idea published as ``owner/name`` (state published or public), as the loop leaves it."""
    idea, _ = lp.ledger.add(f"an idea that became {product}: {name} for people", "cli")
    return lp.ledger.update(
        idea["id"],
        state=state,
        pitch={
            "product": product,
            "one_liner": f"{product} does one thing well.",
            "verdict": "accept",
        },
        publish={
            "repo": f"{owner}/{name}",
            "url": f"https://github.com/{owner}/{name}",
            "version": "0.1.0",
            "visibility": "public" if state == "public" else "private",
        },
    )


@pytest.fixture
def lp(tmp_path):
    return make_loop(tmp_path)


def test_a_published_idea_becomes_one_project(lp):
    idea = published(lp, "Receipts", "receipts")
    ps = Projects(lp.ledger)
    p, made = record.create_for_idea(ps, idea)
    assert made and p["id"] == project_id("iddv/receipts") and p["kind"] == "built"
    assert p["name"] == "Receipts" and p["idea"] == idea["id"] and p["state"] == "active"
    assert p["repos"] == [
        {
            "role": "primary",
            "repo": "iddv/receipts",
            "url": "https://github.com/iddv/receipts",
            "visibility": "public",
            "default_branch": "main",
        }
    ]
    assert p["charter"] is None and p["charter_v"] == 0 and p["topic"] is None
    again, made = record.create_for_idea(ps, lp.ledger.must(idea["id"]))
    assert not made and again["id"] == p["id"]
    assert ps.by_repo("IDDV/Receipts")["id"] == p["id"]  # case does not matter
    assert ps.by_idea(idea["id"])["id"] == p["id"]
    assert ps.by_repo("iddv/nothing") is None and ps.by_idea("i00000000") is None


def test_an_idea_without_a_repo_is_no_project(lp):
    idea, _ = lp.ledger.add("a held build that never published", "cli")
    assert record.create_for_idea(Projects(lp.ledger), idea) == (None, False)


def test_events_are_the_projects_timeline_and_stay_out_of_the_ideas_history(lp):
    idea = published(lp, "Receipts", "receipts")
    ps = Projects(lp.ledger)
    p, _ = record.create_for_idea(ps, idea)
    ps.event(p["id"], "charter", "charter v1 by repo-factory", v=1, by="repo-factory")
    ev = ps.events(p["id"])
    assert [e["data"]["kind"] for e in ev] == ["charter", "created"]
    assert ev[1]["data"] == {"kind": "created", "repo": "iddv/receipts", "idea": idea["id"]}
    assert all(h["idea"] != p["id"] for h in lp.ledger.history(ideas_only=True))
    assert any(h["idea"] == p["id"] for h in lp.ledger.history())
    with pytest.raises(ValueError):
        ps.event(p["id"], "gossip", "not a kind")


def test_the_backfill_makes_the_eight_live_projects_once(lp):
    for product, name in LIVE:
        published(lp, product, name)
    held, _ = lp.ledger.add("Sidedoor: held, no repo", "cli")
    lp.ledger.update(held["id"], state="held")
    closed, _ = lp.ledger.add("Issuefold: closed", "cli")
    lp.ledger.update(
        closed["id"],
        state="closed",
        publish={"repo": "iddv/issuefold", "url": "https://github.com/x"},
    )
    dry = record.backfill(lp, dry_run=True)
    assert len(dry) == 8 and all(r["created"] for r in dry)
    assert Projects(lp.ledger).all() == []  # the dry run wrote nothing
    out = record.backfill(lp)
    assert sorted(r["name"] for r in out) == sorted(p for p, _ in LIVE)
    assert all(r["created"] for r in out)
    again = record.backfill(lp)
    assert len(again) == 8 and not any(r["created"] for r in again)
    ps = Projects(lp.ledger)
    assert len(ps.all()) == 8
    notes = {e["note"] for p in ps.all() for e in ps.events(p["id"])}
    assert notes == {"created (backfill)"}


def test_a_confirmed_adopted_repo_is_a_candidate(lp, monkeypatch):
    from repo_factory.maintain import profile as Pf

    profs = [
        {
            "repo": "acme/tool",
            "confirmed": True,
            "status": "maintained",
            "visibility": "public",
            "default_branch": "trunk",
        },
        {"repo": "acme/raw", "confirmed": False, "status": "watch"},
        {"repo": "acme/gone", "confirmed": True, "status": "detached"},
    ]
    monkeypatch.setattr(Pf, "every", lambda home, detached=False: profs)
    c = record.candidates(lp)
    assert [x["repo"] for x in c] == ["acme/tool"]
    p, made = record.create_for_profile(Projects(lp.ledger), profs[0])
    assert made and p["kind"] == "adopted" and p["idea"] is None and p["name"] == "tool"
    assert p["repos"][0]["default_branch"] == "trunk"


def test_health_is_one_word_with_why(lp):
    idea = published(lp, "Receipts", "receipts")
    ps = Projects(lp.ledger)
    p, _ = record.create_for_idea(ps, idea)
    now = time.time()
    rec = {"counts": {"issues": 2, "prs": 1}, "rows": []}
    assert record.health(p, [("iddv/receipts", rec)]) == ("ok", "nothing waits for you")
    word, why = record.health(
        p,
        [
            (
                "iddv/receipts",
                {"rows": [{"kind": "factory_pr", "number": 5, "title": "Fix the Codex events"}]},
            )
        ],
    )
    assert word == "needs_you" and "Fix the Codex events" in why
    assert record.health(p, [("r", {"rows": []})], asks=1)[0] == "needs_you"
    old = {"rows": [{"kind": "dependabot", "created": now - 30 * 86400, "title": "bump"}]}
    assert record.health(p, [("r", old)], now=now)[0] == "attention"
    assert record.health(p, [("r", {"ci": {"state": "failure"}})])[0] == "attention"
    drift = {
        **p,
        "stats": {"drift": {"findings": [{"what": "pyproject says 1.0.0", "severity": "medium"}]}},
    }
    low = {**p, "stats": {"drift": {"findings": [{"what": "a tag ahead", "severity": "low"}]}}}
    assert record.health(low, [("r", {})])[0] == "ok"  # listed, not the word
    assert record.health(drift, [("r", {})]) == (
        "attention",
        "1 drift finding: pyproject says 1.0.0",
    )
    assert record.health({**p, "state": "paused"}, [("r", old)])[0] == "paused"


def test_the_list_and_the_page_read_the_ledger_and_the_cache(lp):
    a = published(lp, "Receipts", "receipts")
    b = published(lp, "Driftwatch", "driftwatch")
    record.backfill(lp)
    Cache(lp.cfg.home).write_repo(
        "iddv/driftwatch",
        {
            "counts": {"issues": 0, "prs": 1, "factory_prs": 1},
            "release": {"tag": "v0.1.2"},
            "rows": [{"kind": "factory_pr", "number": 1, "title": "Clean up", "url": "u"}],
        },
    )
    out = record.listing(lp)
    names = [r["name"] for r in out["projects"]]
    assert names == ["Driftwatch", "Receipts"]  # needs you first
    dw = out["projects"][0]
    assert dw["health"] == "needs_you" and dw["version"] == "v0.1.2" and dw["prs"] == 1
    assert dw["pitch"] == "Driftwatch does one thing well."  # no charter yet: the one-liner
    assert out["counts"]["needs_you"] == 1 and out["counts"]["ok"] == 1
    rc = next(r for r in out["projects"] if r["name"] == "Receipts")
    assert rc["version"] == "v0.1.0" and rc["idea"] == a["id"]
    pg = record.page(lp, dw["id"])
    assert pg["project"]["name"] == "Driftwatch" and pg["idea"]["id"] == b["id"]
    assert pg["maintenance"] == [
        {
            "kind": "factory_pr",
            "number": 1,
            "title": "Clean up",
            "url": "u",
            "repo": "iddv/driftwatch",
        }
    ]
    assert pg["repos"][0]["release"] == "v0.1.2"
    assert [e["kind"] for e in pg["timeline"]] == ["created"]
    with pytest.raises(LookupError):
        record.page(lp, "p00000000")


def test_the_list_carries_the_backfill_tally_and_the_owners_versions(lp):
    from repo_factory.projects import charter as C

    published(lp, "Receipts", "receipts")
    record.backfill(lp)
    out = record.listing(lp)
    assert out["backfill"] is None and out["charters_by_you"] == 0
    pid = out["projects"][0]["id"]
    ps = Projects(lp.ledger)
    C.save(ps, pid, {"pitch": "Receipts records what an agent ran."}, "repo-factory", "drafted")
    lp.ledger.meta(
        "projects_backfill",
        {"at": time.time(), "projects": 1, "charters": 1, "topics": 0, "proposed": 0},
    )
    out = record.listing(lp)
    assert out["backfill"]["charters"] == 1 and out["charters_by_you"] == 0
    C.save(ps, pid, {"pitch": "Receipts records what a coding agent ran."}, "owner", "mine")
    assert record.listing(lp)["charters_by_you"] == 1


def blocking_claude(tmp, monkeypatch):
    """A ``claude`` first on PATH that records its call and blocks: a route that reached a
    model would hang and leave a record."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    called = tmp / "model-called"
    shim = bin_dir / "claude"
    shim.write_text(f"#!/bin/sh\necho \"$*\" >> '{called}'\nsleep 120\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return called


def test_the_routes_answer_at_once_and_never_call_a_model(lp, tmp_path, monkeypatch):
    called = blocking_claude(tmp_path, monkeypatch)
    idea = published(lp, "Receipts", "receipts")
    record.backfill(lp)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    t0 = time.time()
    code, out = call(app, "GET", "/api/projects")
    assert code == 200 and [r["name"] for r in out["projects"]] == ["Receipts"]
    pid = out["projects"][0]["id"]
    code, pg = call(app, "GET", "/api/project", {"id": pid})
    assert code == 200 and pg["project"]["id"] == pid and pg["idea"]["id"] == idea["id"]
    assert call(app, "GET", "/api/project", {"id": "p00000000"})[0] == 404
    assert call(app, "GET", "/api/project", {"id": "../etc"})[0] == 404
    code, snap = call(app, "GET", "/api/state")
    assert code == 200
    assert time.time() - t0 < 20 and not called.exists()


def test_rows_carry_their_project_by_idea_or_by_repo(lp):
    idea = published(lp, "Receipts", "receipts")
    p, _ = record.create_for_idea(Projects(lp.ledger), idea)
    rows = [
        {"idea": idea["id"], "repo": None},
        {"idea": None, "repo": "iddv/Receipts"},
        {"idea": None, "repo": "iddv/other"},
    ]
    maint = [{"kind": "needs_you", "repo": "iddv/receipts", "idea": idea["id"]}]
    api.App.project_links(lp, rows, maint)
    want = {"id": p["id"], "name": "Receipts"}
    assert [r["project"] for r in rows] == [want, want, None]
    assert maint[0]["project"] == want


def test_spend_carries_the_project(lp):
    lp.ledger.spend("evolve", 0.17, None, project="p1234abcd", in_tokens=10, out_tokens=5)
    with lp.ledger.db() as c:
        r = c.execute("SELECT idea, kind, usd, project FROM spend").fetchone()
    assert tuple(r) == (None, "evolve", 0.17, "p1234abcd")


def test_every_projects_setting_has_a_default_and_a_check():
    d = DEFAULTS["projects"]
    for key in PROJECTS:
        node = d
        for k in key.split("."):
            assert k in node, key
            node = node[k]
    assert d["autonomy"]["preset"] == "quiet" and d["evolve"]["model"] == "claude-sonnet-5-5"
    assert d["budget"]["monthly_usd"] is None  # uncapped
    assert "max_usd" not in d["evolve"] and "tools" not in d["evolve"]  # read by nothing
    assert project_value("evolve.every_days", 14) == 14
    assert project_value("autonomy.minor", "") is None  # back to the preset's
    assert project_value("gate.require_evidence_kinds", ["customer_ask"]) == ["customer_ask"]
    for key, bad in (
        ("autonomy.scope", "auto"),
        ("evolve.every_days", 0),
        ("evolve.tools", "read"),
        ("gate.min_confidence", 2),
        ("enabled", "yes"),
        ("nothing.here", 1),
        ("evolve.model", "a b"),
    ):
        with pytest.raises(ValueError):
            project_value(key, bad)


def test_an_unknown_projects_key_from_a_newer_factory_is_ignored(tmp_path):
    (tmp_path / "loop.yaml").write_text(
        "projects:\n  evolve:\n    every_days: 3\n    a_newer_key: 1\n  newer: {x: 1}\n"
    )
    cfg = Config(tmp_path)
    assert cfg["projects"]["evolve"]["every_days"] == 3
    assert cfg["projects"]["evolve"]["model"] == DEFAULTS["projects"]["evolve"]["model"]
    assert "a_newer_key" not in DEFAULTS["projects"]["evolve"]


def test_factory_projects_backfill_list_and_show(lp, capsys):
    published(lp, "Receipts", "receipts")
    home = str(lp.cfg.home)
    main(["projects", "backfill", "--home", home, "--dry-run"])
    assert "would create" in capsys.readouterr().out and not Projects(lp.ledger).all()
    main(["projects", "backfill", "--home", home, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in out] == ["Receipts"] and out[0]["created"]
    main(["projects", "list", "--home", home])
    assert "Receipts" in capsys.readouterr().out
    main(["projects", "show", out[0]["id"], "--home", home])
    assert "created (backfill)" in capsys.readouterr().out
    main(["projects", "create", lp.ledger.all()[0]["id"], "--home", home])
    assert "already a project" in capsys.readouterr().out
