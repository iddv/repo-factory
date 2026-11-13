"""One bad row never takes a page down: in each list repo-factory builds row by row (the
builds, in the snapshot and in /api/builds; projects; Changes; Today's project asks;
Learning's lessons; the loop's steps in Activity), one row that raises is still listed, as
"Couldn't read this <thing>" with an error id, the rest as before, and the error is kept once
by its id, in the home."""

import json
import logging

import pytest
import yaml

from repo_factory import rowsafe
from repo_factory.ui import api

from .loop_common import make_loop
from .product_fixture import _product


def raises(*_a, **_k):
    raise TypeError("unsupported format string passed to NoneType.__format__")


def check(row: dict, what: str, exc: str = "TypeError") -> str:
    u = row["unreadable"]
    assert u["what"] == what and u["words"] == f"Couldn't read this {what}"
    assert u["exception"] == exc and len(u["error_id"]) == 8
    int(u["error_id"], 16)
    return u["error_id"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(rowsafe, "_seen", {})


@pytest.fixture
def made(tmp_path):
    lp = make_loop(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    lp.cfg.tasks_dir.mkdir(parents=True, exist_ok=True)
    _product(lp.cfg.tasks_dir, "sidedoor")
    _product(lp.cfg.tasks_dir, "brokenone")
    return api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home), lp, None


def test_a_build_that_cannot_be_read_is_listed_in_the_snapshot_and_in_builds(
    made, monkeypatch, caplog
):
    app, _, _ = made
    real_row, real_state = api.App.build_row, api.App.state
    monkeypatch.setattr(
        api.App,
        "build_row",
        lambda self, d, i: raises() if d.name == "brokenone" else real_row(self, d, i),
    )
    monkeypatch.setattr(
        api.App, "state", lambda self, d: raises() if d.name == "brokenone" else real_state(self, d)
    )
    with caplog.at_level(logging.ERROR):
        snap = [app.snapshot() for _ in range(3)][-1]
        builds = app.builds()
    bad = [b for b in snap["builds"] if rowsafe.is_unreadable(b)]
    assert [b["id"] for b in bad] == ["brokenone"] and bad[0]["title"] == "brokenone"
    eid = check(bad[0], "build")
    assert [b["key"] for b in snap["builds"] if not rowsafe.is_unreadable(b)] == ["sidedoor"]
    [b] = [b for b in builds if rowsafe.is_unreadable(b)]
    check(b, "build")
    assert len(builds) == 2
    assert sum(eid in r.getMessage() for r in caplog.records) == 1


def test_projects_changes_asks_and_lessons_contain_a_bad_row(made, monkeypatch):
    from repo_factory.projects import changes as X
    from repo_factory.projects import record

    app, lp, _ = made
    ps = [
        {"id": "p00000001", "name": "Good", "idea": None, "repos": []},
        {"id": "p00000002", "name": "Bad", "idea": None, "repos": []},
    ]
    monkeypatch.setattr(record.Projects, "all", lambda self, states=None: ps)
    monkeypatch.setattr(record, "cache_recs", lambda home, p, cache: [])
    monkeypatch.setattr(
        record,
        "row",
        lambda pss, p, *a, **k: (
            raises()
            if p["name"] == "Bad"
            else {"id": p["id"], "name": p["name"], "health": "ok", "last": 1}
        ),
    )
    out = record.listing(app.loop())["projects"]
    assert [p["id"] for p in out] == ["p00000001", "p00000002"]
    check(out[1], "project")

    chs = [
        {
            "id": "c1",
            "title": "Fine",
            "kind": "k",
            "class": "minor",
            "state": "open",
            "card": {},
            "project": "p00000001",
            "updated": 2,
        },
        {
            "id": "c2",
            "title": "Broken",
            "kind": "k",
            "class": "minor",
            "state": "open",
            "card": None,
            "project": "p00000001",
            "updated": 1,
        },
    ]
    monkeypatch.setattr(X, "all_of", lambda ledger: chs)
    got = app.changes()["changes"]
    assert [c["id"] for c in got] == ["c1", "c2"] and got[1]["title"] == "Broken"
    check(got[1], "change", "AttributeError")
    monkeypatch.setattr(X, "asks", lambda ledger: chs)
    asks = app.project_asks(app.loop())
    assert [a["id"] for a in asks] == ["c1", "c2"]
    check(asks[1], "change", "AttributeError")

    from repo_factory.lessons import postmortem

    monkeypatch.setattr(
        postmortem,
        "issue_lessons",
        lambda data: [
            {
                "id": "l1",
                "text": "t",
                "applies_to": [],
                "status": "active",
                "born": {"issue": "me/good#1"},
            },
            {"id": "l2", "born": {"issue": "me/good#2"}},
        ],
    )
    lessons = app.maintain_lessons()["lessons"]
    assert [x["id"] for x in lessons] == ["l1", "l2"]
    check(lessons[1], "lesson", "KeyError")


def test_a_loop_step_that_cannot_be_read_is_listed_in_activity(made, monkeypatch):
    import time

    from repo_factory.loop import activity as A

    _, lp, _ = made
    now = time.time()
    rows = [
        {
            "run": run,
            "mark": mark,
            "kind": "pitch",
            "title": run,
            "idea": None,
            "ts": now - 10,
            "pid": 0,
            "pstart": 0,
            "state": "ok",
            "words": None,
            "usd": 0.1,
            "error": None,
        }
        for run in ("r1", "r2")
        for mark in ("started", "ended")
    ]

    class Db:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=()):
            return rows

    class Ledger:
        def db(self):
            return Db()

    real = A._run_row
    monkeypatch.setattr(
        A, "_run_row", lambda **kw: raises() if kw["rid"] == "loop:r2" else real(**kw)
    )
    running, recent = A.steps(Ledger(), now)
    assert running == [] and len(recent) == 2
    [bad] = [r for r in recent if rowsafe.is_unreadable(r)]
    assert bad["id"] == "loop:r2" and bad["kind"] == "pitch" and bad["state"] == "failed"
    check(bad, "run")


def test_the_self_checks_reads_are_every_page_and_nothing_that_leaves(made):
    """A self-check reads these (``api.selfcheck_paths``): each GET that takes nothing, the
    builds' pages and the projects', never what asks GitHub or runs a program."""
    app, _, _ = made
    paths = api.selfcheck_paths(app)
    for p in (
        "/api/state",
        "/api/header",
        "/api/builds",
        "/api/activity",
        "/api/projects",
        "/api/changes",
    ):
        assert p in paths, p
    for gone in api.SELFCHECK_EXCLUDED:
        assert gone not in paths
    builds = [p for p in paths if p.startswith("/api/build?dir=")]
    assert len(builds) == 2 and all("sidedoor" in b or "brokenone" in b for b in builds)
    # each answers below 500 on this loop
    for p in paths:
        path, _, q = p.partition("?")
        code, _, _ = api.handle("GET", path, q, app=app)
        assert code < 500, p


def test_the_id_is_the_signature_and_the_file_is_the_homes(tmp_path):
    rowsafe.configure(tmp_path)
    try:
        out = rowsafe.each(
            [{"id": n} for n in (1, 2, 3)], lambda x: raises() if x["id"] == 2 else x, "build"
        )
        assert [r["id"] for r in out] == [1, 2, 3]
        eid = check(out[1], "build")
        rec = json.loads((tmp_path / "errors.jsonl").read_text().splitlines()[0])
        assert rec["id"] == eid and rec["exception"] == "TypeError"
        assert rec["frames"] and all(f.startswith("repo_factory.") for f in rec["frames"])
        assert rec["signature"].startswith(eid) and len(rec["signature"]) == 16
    finally:
        rowsafe.configure(None)
