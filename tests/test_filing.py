"""Filing changes (PJ-PLAN.md §4.7, PJ-7) on an in-process fake GitHub (no network): a patch
under quiet files an issue with go; a minor change filed after the owner's click gets no go
because the product's policy excludes features (the policy wins, and the idea says so); an
adopted repo the owner does not own gets a factory-side request and no issue; hands-on files
nothing by itself; the idea follows its issue to closed; Build it can be undone."""

import json
import time

import pytest
import yaml

from repo_factory.gh import Gh
from repo_factory.projects import changes as X
from repo_factory.projects import evolve, filing, life, sources
from repo_factory.projects.charter import normalize, parse, save
from repo_factory.projects.record import Projects, create_for_idea, create_for_profile
from repo_factory.ui import api

from .app_common import call
from .loop_common import FakeModel, make_loop
from .test_evolve import CHARTER, EVOLVE
from .test_projects import published

pytestmark = pytest.mark.usefixtures("projects_on")

POLICY = "status: maintained\nrevise:\n  trigger: go\n  kinds: [bug, security, docs, dependency]\n"


class Hub:
    """``gh api`` for issues and the policy file; every write recorded."""

    def __init__(self, policy=POLICY):
        self.policy, self.issues, self.calls = policy, {}, []

    def __call__(self, args, cwd=None):
        a = list(args)
        method = a[a.index("-X") + 1] if "-X" in a else "GET"
        body = json.load(open(a[a.index("--input") + 1])) if "--input" in a else None
        path = a[-1]
        self.calls.append((method, path, body))
        ok = lambda o: (0, json.dumps(o), "")  # noqa: E731
        if path.endswith("/contents/.github/repo-factory.yml"):
            return (0, self.policy, "") if self.policy else (1, "", "HTTP 404: Not Found")
        if method == "POST" and path.endswith("/issues"):
            n = len(self.issues) + 1
            repo = path.split("/")[1] + "/" + path.split("/")[2]
            self.issues[n] = {
                "number": n,
                "title": body["title"],
                "body": body["body"],
                "labels": [{"name": x} for x in body["labels"]],
                "state": "open",
                "html_url": f"https://github.com/{repo}/issues/{n}",
            }
            return ok(self.issues[n])
        if "/issues/" in path:
            n = int(path.rsplit("/", 1)[1])
            if method == "PATCH":
                self.issues[n].update(state=body["state"])
            return ok(self.issues[n])
        return 1, "", "gh: Not Found (HTTP 404)"

    def posted(self):
        return [b for m, p, b in self.calls if m == "POST"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sources,
        "gather",
        lambda loop, p, **kw: {
            "backlog": {},
            "drift": [],
            "issues": {"items": [], "text": "none"},
            "observations": [],
            "parent": [],
            "digest": "",
            "charter_v": p.get("charter_v"),
            "head": "abc",
            "fingerprint": {"at": time.time()},
            "changed": {"any": True},
            "errors": [],
        },
    )

    def make(**over):
        # Receipts' reply is the live prompt's: six maintenance patches, which these filing
        # tests file; evolve.max_maintenance (default 1) is tested in test_evolve
        pj = dict(over.pop("projects", {}))
        pj["evolve"] = {"max_maintenance": 20, **(pj.get("evolve") or {})}
        lp = make_loop(tmp_path, projects=pj, **over)
        (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))  # the App's too
        hub = Hub()
        lp._gh = Gh(run=hub)
        ps = Projects(lp.ledger)
        p, _ = create_for_idea(ps, published(lp, "Receipts", "receipts"))
        p = save(ps, p["id"], normalize(parse(CHARTER["receipts"])), "repo-factory", "drafted")
        return lp, p, hub

    return make


def test_a_patch_under_quiet_files_an_issue_with_go(world):
    lp, p, hub = world()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    filed = [ch for ch in X.all_of(lp.ledger, p["id"]) if ch.get("filed")]
    assert len(filed) == 6 and len(hub.posted()) == 6
    s3 = next(ch for ch in filed if "RecursionError" in ch["title"])
    assert s3["filed"]["go"] and s3["filed"]["labels"] == ["security", "p2", "go"]
    issue = hub.issues[s3["filed"]["number"]]
    assert f"<!-- repo-factory:project-change {s3['id']} -->" in issue["body"]
    assert "**Evidence**" in issue["body"] and "charter v1" in issue["body"]
    codex = next(ch for ch in filed if "Codex" in ch["title"])
    assert codex["filed"]["labels"][:2] == ["bug", "p2"]  # a broken integration
    assert not s3["card"].get("undo")  # repo-factory filed it: no Undo
    ev = [e["note"] for e in Projects(lp.ledger).events(p["id"])]
    assert any(
        n.startswith(f"filed as #{s3['filed']['number']} on iddv/receipts with go") for n in ev
    )


def test_a_minor_change_after_the_owners_click_gets_go_the_press_is_the_go(world):
    """Beliefline #8 (10-13): the owner accepted "Add `verify --anchor`", it was filed as a
    feature with no go (revise.kinds has no feature), and nothing could ever build it. The
    owner's own Accept is the go; revise.kinds bounds what repo-factory files by itself."""
    lp, p, hub = world()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    minor = next(ch for ch in X.all_of(lp.ledger, p["id"]) if ch["class"] == "minor")
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    lp_app = app.loop()
    lp_app._gh = lp._gh
    app.loop = lambda: lp_app
    code, out = call(
        app,
        "POST",
        "/api/act",
        None,
        {
            "kind": "accept_change",
            "subject": {"kind": "new"},
            "fields": {"change": minor["id"]},
            "origin": "ui",
        },
    )
    assert code == 200 and out["undo"]["until"] > time.time(), out
    ch = X.must(lp.ledger, minor["id"])
    assert ch["filed"]["labels"] == ["feature", "p3", "go"] and ch["filed"]["go"]
    assert ch["filed"]["go_held"] is None
    # Undo within the window: the issue is closed unworked and the idea is proposed again
    code, out = call(
        app,
        "POST",
        "/api/act",
        None,
        {
            "kind": "undo_change",
            "subject": {"kind": "new"},
            "fields": {"change": minor["id"]},
            "origin": "ui",
        },
    )
    assert code == 200 and X.must(lp.ledger, minor["id"])["state"] == "new"
    assert hub.issues[ch["filed"]["number"]]["state"] == "closed"


def test_undo_on_an_ask_asks_again(world):
    """Build it on Today, then Undo: the minor change is back on Today as it was."""
    lp, p, hub = world()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]), force=True)
    [ask] = X.asks(lp.ledger, p["id"])
    X.accept(lp, ask["id"])
    assert not X.asks(lp.ledger, p["id"])
    filing.undo(lp, ask["id"])
    assert [a["id"] for a in X.asks(lp.ledger, p["id"])] == [ask["id"]]


def test_an_adopted_repo_not_yours_gets_a_request_and_no_issue(world, tmp_path):
    lp, _, hub = world()
    from repo_factory.maintain import profile as Pf

    prof = {
        "repo": "pallets/tool",
        "confirmed": True,
        "status": "maintained",
        "access": {"level": "admin", "owned": False},
        "labels": {"scheme": "none"},
    }
    k = "pallets/tool"
    Pf.root(lp.cfg.home).mkdir(parents=True, exist_ok=True)
    Pf.path(lp.cfg.home, k).write_text(Pf.dump(Pf.merge(Pf.DEFAULTS, prof)))
    p, _ = create_for_profile(Projects(lp.ledger), Pf.load(lp.cfg.home, k))
    card = {
        "title": "Return 404 for unknown ids",
        "kind": "maintenance",
        "class": "patch",
        "evidence": ["repo:SECURITY.md S5"],
        "confidence": 0.9,
        "runs": {"agreeing": 1},
        "what": "x",
    }
    ch = X.add(lp.ledger, p["id"], card)
    assert X.decide(lp.cfg.data, p, ch["card"], owned=evolve.owned(lp, p))[0] == "ask"
    ch = X.accept(lp, ch["id"])
    assert ch["filed"]["how"] == "request" and ch["filed"]["id"] == "r1"
    assert hub.posted() == []  # nothing on their repo
    req = Pf.load(lp.cfg.home, k)["requests"][0]
    assert req["text"].startswith("Return 404 for unknown ids") and req["by"] == "repo-factory"


def test_hands_on_files_nothing_by_itself(world):
    lp, p, hub = world(projects={"autonomy": {"preset": "hands-on"}})
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    assert hub.posted() == [] and len(X.asks(lp.ledger, p["id"])) == 7


def test_the_idea_follows_its_issue_to_closed(world):
    lp, p, hub = world()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    ch = next(ch for ch in X.all_of(lp.ledger, p["id"]) if ch.get("filed"))
    n = ch["filed"]["number"]
    hub.issues[n]["labels"].append({"name": "needs-you"})
    filing.follow(lp, p)
    assert X.must(lp.ledger, ch["id"])["state"] == "queued", "no copy of GitHub's labels"
    assert not [e for e in Projects(lp.ledger).events(p["id"]) if "needs you" in e["note"]]
    hub.issues[n]["state"] = "closed"
    out = life.follow_all(lp, Projects(lp.ledger), [])
    assert any(f"#{n} is closed" in w for w in out)
    assert X.must(lp.ledger, ch["id"])["state"] == "closed"


def test_a_github_error_keeps_it_queued_and_the_tick_files_it(world):
    lp, p, hub = world()
    real = hub.__call__
    broken = {"on": True}

    def flaky(args, cwd=None):
        if broken["on"] and "-X" in args and "POST" in args:
            return 1, "", "HTTP 502: Bad Gateway"
        return real(args, cwd)

    lp._gh = Gh(run=flaky)
    card = {
        "title": "Write exports with mode 0600",
        "kind": "maintenance",
        "class": "patch",
        "evidence": ["repo:SECURITY.md S4"],
        "confidence": 0.9,
        "runs": {"agreeing": 1},
    }
    ch = X.accept(lp, X.add(lp.ledger, p["id"], card)["id"])
    assert ch["state"] == "queued" and not ch.get("filed") and ch["card"]["file_error"]
    broken["on"] = False
    filing.follow(lp, p)
    assert X.must(lp.ledger, ch["id"])["filed"]["number"] == 1


def test_accept_filed_on_a_later_tick_still_offers_undo(world):
    """QA B6: Accept: repo-factory fixes it while GitHub refused the filing said "It is filed on the
    loop's next tick" and offered no Undo, though help.md promises one. The press is undoable
    as a filed one is, and undoing it has nothing on GitHub to close."""
    lp, p, hub = world()
    lp._gh = Gh(run=lambda args, cwd=None: (1, "", "HTTP 502: Bad Gateway"))
    card = {
        "title": "Write exports with mode 0600",
        "kind": "maintenance",
        "class": "patch",
        "evidence": ["repo:SECURITY.md S4"],
        "confidence": 0.9,
        "runs": {"agreeing": 1},
    }
    cid = X.add(lp.ledger, p["id"], card)["id"]
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    lp_app = app.loop()
    lp_app._gh = lp._gh
    app.loop = lambda: lp_app
    code, out = call(
        app,
        "POST",
        "/api/act",
        None,
        {
            "kind": "accept_change",
            "subject": {"kind": "new"},
            "fields": {"change": cid},
            "origin": "ui",
        },
    )
    assert code == 200 and "next tick" in out["say"] and out["undo"]["until"] > time.time(), out
    code, out = call(
        app,
        "POST",
        "/api/act",
        None,
        {
            "kind": "undo_change",
            "subject": {"kind": "new"},
            "fields": {"change": cid},
            "origin": "ui",
        },
    )
    ch = X.must(lp.ledger, cid)
    assert code == 200 and ch["state"] == "new" and not ch.get("filed"), out
    assert "file_error" not in ch["card"] and hub.posted() == []
    filing.follow(lp, p)  # the tick files nothing now
    assert not X.must(lp.ledger, cid).get("filed")


def test_no_test_can_act_on_github_as_the_developer():
    """A real gh reached by mistake has no login and no token in tests (conftest)."""
    import os
    from pathlib import Path

    d = Path(os.environ["GH_CONFIG_DIR"])
    assert d.is_dir() and not (d / "hosts.yml").exists()
    assert not any(os.environ.get(k) for k in ("GH_TOKEN", "GITHUB_TOKEN"))


def test_tasks_are_not_bugs_and_a_decision_is_never_built():
    """Beliefline #3 and #6 (10-13) were maintenance items filed as `bug`, so the fixer looked
    for a failing behaviour, found none, and labelled both cannot-reproduce."""

    def ch(title, kind="maintenance", cls="patch"):
        return {"title": title, "kind": kind, "class": cls, "card": {"what": "", "evidence": []}}

    assert (
        filing.kind_label(ch("Decide the Python floor: raise to 3.10+ or test 3.9 in CI"))
        == "question"
    )
    assert (
        filing.kind_label(ch("Add a tested v1-to-v2 migration fixture before any schema change"))
        == "task"
    )
    assert (
        filing.kind_label(ch("Make /v1/health check the database and report schema version"))
        == "task"
    )
    assert (
        filing.kind_label(ch("Search returns nothing for accented names", kind="customer_ask"))
        == "bug"
    )
    ok, why = filing.go_allowed(None, {}, {"profile": {}}, "question", owner=True)
    assert not ok and "decision" in why
