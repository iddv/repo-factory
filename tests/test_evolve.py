"""The evolve run (PJ-PLAN.md §4, §5, PJ-6) with a fake model replaying the experiment's real
replies (tests/fixtures/projects/replies/*.evolve.txt) and a fixed gather (no clone, no
network): the ideas land with their class and state; a second run raises the agreeing count and
adds nothing; a minor change asks after two runs; a non-goal match becomes scope; a declined
idea is skipped; a quiet project makes no call; duplicates of open issues are tracked; the
presets; the scope ask's charter change; the doors answer at once."""

import json
import re
import time
from pathlib import Path

import pytest
import yaml

from repo_factory import registry as R
from repo_factory.projects import changes as X
from repo_factory.projects import evolve, jobs, life, sources
from repo_factory.projects.charter import normalize, parse, save
from repo_factory.projects.record import Projects, create_for_idea
from repo_factory.ui import api
from repo_factory.ui import types as T

from .app_common import call
from .loop_common import FakeModel, make_loop
from .test_projects import published

pytestmark = pytest.mark.usefixtures("projects_on")

REPLIES = Path(__file__).parent / "fixtures" / "projects" / "replies"
EVOLVE = {k: (REPLIES / f"{k}.evolve.txt").read_text() for k in ("receipts", "plotkeeper")}
CHARTER = {k: (REPLIES / f"{k}.charter.txt").read_text() for k in ("receipts", "plotkeeper")}


def reply_with(name: str, edit) -> str:
    """The experiment's reply, its JSON changed by ``edit(obj)``."""
    raw = EVOLVE[name]
    obj = json.loads(re.search(r"```json\s*(\{.*\})\s*```", raw, re.S).group(1))
    edit(obj)
    return "```json\n" + json.dumps(obj) + "\n```"


@pytest.fixture
def world(tmp_path, monkeypatch):
    state = {"any": True, "items": []}

    def fake_gather(loop, p, **kw):
        fp = {
            "at": time.time(),
            "head": "abc",
            "charter_v": p.get("charter_v"),
            "issues": {f"r#{i['number']}": 1 for i in state["items"]},
        }
        return {
            "backlog": {"security": [{"id": "S3", "status": "open"}]},
            "drift": state.get("drift") or [],
            "issues": {"items": state["items"], "text": "no open issues"},
            "observations": [{"id": 440, "title": "fakegreen: focus markers skip tests"}],
            "parent": [],
            "digest": "receipts/detect.py: SKIP_MARKERS = (...)",
            "charter_v": p.get("charter_v"),
            "head": "abc",
            "fingerprint": fp,
            "changed": {"any": state["any"]},
            "errors": [],
        }

    monkeypatch.setattr(sources, "gather", fake_gather)

    def make(name="receipts", keep_chores=True, **over):
        # the replies are the live prompt's, six chores of seven in Receipts': the merge and
        # gate tests keep them all; the maintenance cap has its own tests (keep_chores=False)
        pj = dict(over.pop("projects", {}))
        if keep_chores:
            pj["evolve"] = {"max_maintenance": 20, **(pj.get("evolve") or {})}
        lp = make_loop(tmp_path / name, **({"projects": pj} if pj else {}), **over)
        (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))  # the App's too
        ps = Projects(lp.ledger)
        p, _ = create_for_idea(ps, published(lp, name.capitalize(), name))
        p = save(ps, p["id"], normalize(parse(CHARTER[name])), "repo-factory", "drafted")
        return lp, p

    return make, state


def states(lp, pid):
    return sorted((ch["class"], ch["state"]) for ch in X.all_of(lp.ledger, pid))


def test_the_receipts_run_lands_seven_ideas_with_their_class_and_state(world):
    make, _ = world
    lp, p = make()
    model = FakeModel(EVOLVE["receipts"], usd=0.17)
    out = evolve.run(lp, p["id"], model)
    assert out["proposed"] == 7 and out["new"] == 7 and len(model.prompts) == 1
    prompt = model.prompts[0]
    assert "# Promises" in prompt
    assert "SKIP_MARKERS" in prompt
    assert "Nothing from outside the repository was gathered" in prompt
    assert "No ideas have been proposed for this project yet." in prompt
    # quiet: the six patches above the confidence line are filed on their own; the minor
    # feature asks at once (AU3 finding 8: a second run proposing the same title word for word
    # a week later almost never came; Driftwatch's three sat at `agreeing: 1` since 10-13)
    assert states(lp, p["id"]) == [("minor", "new")] + [("patch", "queued")] * 6
    minor = next(ch for ch in X.all_of(lp.ledger, p["id"]) if ch["class"] == "minor")
    assert (
        minor["card"]["gate"]["why"] == "waits for your answer"
    )  # never "a minor change" twice in a row (QA B17)
    assert minor["card"].get("ask")
    with lp.ledger.db() as c:
        r = c.execute("SELECT kind, usd, project FROM spend").fetchall()
    assert [tuple(x) for x in r] == [("evolve", 0.17, p["id"])]
    ev = Projects(lp.ledger).events(p["id"], limit=1)[0]
    assert ev["note"] == "checked for ideas: 7 new, 0 proposed again"


def test_a_second_run_agrees_adds_nothing_and_the_minor_change_asks(world):
    make, _ = world
    lp, p = make()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    out = evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]), force=True)
    assert out["new"] == 0 and out["agreed"] == 7
    rows = X.all_of(lp.ledger, p["id"])
    assert len(rows) == 7 and all(ch["card"]["runs"]["agreeing"] == 2 for ch in rows)
    asks = X.asks(lp.ledger, p["id"])
    assert [a["class"] for a in asks] == ["minor"]
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    code, snap = call(app, "GET", "/api/state")
    assert [a["id"] for a in snap["project_asks"]] == [asks[0]["id"]]
    assert T.problems(snap, T.Snapshot) == [], "the ask has the shape types.py says"
    code, lst = call(app, "GET", "/api/projects")
    row = lst["projects"][0]
    assert row["asks"] == 1 and row["health"] == "needs_you"


def test_a_scope_change_asks_with_the_charter_diff_and_accept_writes_a_version(world):
    make, _ = world
    lp, p = make("plotkeeper")

    def edit(o):
        for i in o["ideas"]:
            if i["class"] == "scope":
                i["charter_change"] = {
                    "field": "promises",
                    "from": "Installs with one command, needs only Python 3.9+, and starts "
                    "empty with no default password.",
                    "to": "Installs with one command, needs only Python 3.10+, and starts "
                    "empty with no default password.",
                }

    evolve.run(lp, p["id"], FakeModel(reply_with("plotkeeper", edit)))
    scope = [ch for ch in X.all_of(lp.ledger, p["id"]) if ch["class"] == "scope"]
    assert len(scope) == 1 and scope[0]["card"]["ask"] and scope[0]["state"] == "new"
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    code, snap = call(app, "GET", "/api/state")
    ask = next(a for a in snap["project_asks"] if a["class"] == "scope")
    assert T.problems(snap, T.Snapshot) == [], "the scope ask and its diff are typed"
    assert {
        "op": "-",
        "line": "- Installs with one command, needs only Python 3.9+, and "
        "starts empty with no default password.",
    } in ask["diff"]
    assert any(d["op"] == "+" and "3.10+" in d["line"] for d in ask["diff"])
    body = {
        "kind": "charter_change",
        "subject": {"kind": "new"},
        "origin": "ui",
        "fields": {"change": scope[0]["id"], "answer": "accept"},
    }
    assert call(app, "POST", "/api/act", None, body)[0] == 409  # confirm class
    code, out = call(app, "POST", "/api/act", None, {**body, "confirm": True})
    assert code == 200, out
    p2 = Projects(lp.ledger).must(p["id"])
    assert p2["charter_v"] == 2 and any("3.10+" in x["text"] for x in p2["charter"]["promises"])
    assert X.must(lp.ledger, scope[0]["id"])["state"] == "queued"


def test_a_non_goal_match_becomes_scope_and_a_feature_is_never_a_patch(world):
    make, _ = world
    lp, p = make()

    def edit(o):
        o["ideas"] = [
            {
                **o["ideas"][0],
                "title": "Add a local web viewer for receipts",
                "kind": "feature",
                "class": "minor",
                "what": "A web viewer.",
                "evidence": ["obs:1"],
                "duplicate_of": None,
            },
            {
                **o["ideas"][1],
                "title": "Flag focus markers like .only",
                "kind": "feature",
                "class": "patch",
                "evidence": ["obs:440"],
                "duplicate_of": None,
            },
        ]

    evolve.run(lp, p["id"], FakeModel(reply_with("receipts", edit)))
    by = {ch["title"]: ch for ch in X.all_of(lp.ledger, p["id"])}
    viewer = by["Add a local web viewer for receipts"]
    assert viewer["class"] == "scope" and viewer["card"]["raised"].startswith(
        "it matches a non-goal: A local web viewer"
    )
    assert by["Flag focus markers like .only"]["class"] == "minor"


def test_declined_ideas_are_counted_not_shown_and_not_now_sleeps(world):
    make, _ = world
    lp, p = make()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    minor = next(ch for ch in X.all_of(lp.ledger, p["id"]) if ch["class"] == "minor")
    X.decline(lp, minor["id"], "never", "skips are a later release")
    out = evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]), force=True)
    assert out["declined"] == 1 and out["agreed"] == 6
    assert X.must(lp.ledger, minor["id"])["state"] == "closed"
    model = FakeModel(EVOLVE["receipts"])
    evolve.run(lp, p["id"], model, force=True)
    assert "declined for good: skips are a later release" in model.prompts[0]
    lp2, p2 = make("plotkeeper")
    evolve.run(lp2, p2["id"], FakeModel(EVOLVE["plotkeeper"]))
    m2 = next(ch for ch in X.all_of(lp2.ledger, p2["id"]) if ch["class"] == "minor")
    X.decline(lp2, m2["id"], "not_now")
    ch = X.must(lp2.ledger, m2["id"])
    assert ch["state"] == "later" and ch["later_until"] > time.time() + 29 * 86400
    evolve.gate(lp2, p2, now=ch["later_until"] + 1)
    assert X.must(lp2.ledger, m2["id"])["state"] == "new"  # its time came


def test_a_quiet_project_makes_no_call(world):
    make, state = world
    lp, p = make()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    state["any"] = False
    model = FakeModel(EVOLVE["receipts"])
    out = evolve.run(lp, p["id"], model)
    assert (
        out == {"skipped": "nothing changed", "since": evolve.last_run(lp.ledger, p["id"])["at"]}
        and model.prompts == []
    )
    assert evolve.run(lp, p["id"], model, force=True)["proposed"] == 7  # Check now: always


def test_maintenance_is_capped_in_code_and_drift_never_reaches_the_prompt(world):
    """PE2 §6.8: the drift checks are maintenance code files; the model never sees them, and
    a run keeps at most ``evolve.max_maintenance`` maintenance proposals (default 1)."""
    make, state = world
    state["drift"] = [
        {
            "check": "version_tag",
            "severity": "low",
            "what": "v0.3.1 untagged",
            "where": "pyproject.toml",
        }
    ]
    lp, p = make(keep_chores=False)
    p = Projects(lp.ledger).must(p["id"])
    assert X.setting(lp.cfg.data, p, "evolve.max_maintenance") == 1  # the default
    model = FakeModel(EVOLVE["receipts"])
    out = evolve.run(lp, p["id"], model)
    assert "v0.3.1 untagged" not in model.prompts[0] and "Drift checks" not in model.prompts[0]
    # Receipts' reply: one feature and six chores; the first chore is kept, five dropped
    kinds = sorted(ch["card"]["kind"] for ch in X.all_of(lp.ledger, p["id"]))
    assert kinds == ["feature", "maintenance"] and out["proposed"] == 2
    assert len(out["maintenance_dropped"]) == 5
    # 0: no maintenance proposal at all
    lp2, p2 = make("plotkeeper", keep_chores=False, projects={"evolve": {"max_maintenance": 0}})
    out2 = evolve.run(lp2, p2["id"], FakeModel(EVOLVE["plotkeeper"]))
    assert {ch["card"]["kind"] for ch in X.all_of(lp2.ledger, p2["id"])} == {"feature"}
    assert out2["proposed"] == 3 and len(out2["maintenance_dropped"]) == 4


def test_cap_maintenance_keeps_the_first_ones_in_order():
    cards = [
        {"kind": k, "title": t}
        for k, t in (
            ("maintenance", "a"),
            ("feature", "b"),
            ("maintenance", "c"),
            ("maintenance", "d"),
        )
    ]
    keep, over = evolve.cap_maintenance(cards, 2)
    assert [c["title"] for c in keep] == ["a", "b", "c"] and over == ["d"]
    assert evolve.cap_maintenance(cards, None) == (cards, [])
    assert [c["title"] for c in evolve.cap_maintenance(cards, "x")[0]] == ["a", "b"]


def test_an_open_issue_marks_the_proposal_already_tracked(world):
    make, state = world
    lp, p = make()
    state["items"] = [
        {
            "repo": "iddv/receipts",
            "number": 4,
            "kind": "needs_you",
            "title": "Write exports and bundles with mode 0600",
            "url": "u4",
        }
    ]
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    tr = [ch for ch in X.all_of(lp.ledger, p["id"]) if ch["state"] == "skipped"]
    assert len(tr) == 1 and tr[0]["card"]["duplicate_of"] == "issue #4"
    # a later run that agrees keeps the issue it tracks (the page links it under Already tracked)
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]), force=True)
    again = X.must(lp.ledger, tr[0]["id"])
    assert again["card"]["duplicate_of"] == "issue #4" and again["card"]["duplicate_url"] == "u4"
    assert again["card"]["runs"]["agreeing"] == 2


@pytest.mark.parametrize(
    "preset, want",
    [
        ("hands-on", [("minor", "new")] + [("patch", "new")] * 6),
        ("off", [("minor", "new")] + [("patch", "new")] * 6),
    ],
)
def test_the_presets_decide_who_decides(world, preset, want):
    make, _ = world
    lp, p = make(projects={"autonomy": {"preset": preset}})
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    assert states(lp, p["id"]) == want
    asked = len(X.asks(lp.ledger, p["id"]))
    assert asked == (7 if preset == "hands-on" else 0)


def test_the_gate_reads_its_settings():
    cfg = {
        "projects": {
            "autonomy": {"preset": "quiet", "minor": "auto", "scope": "auto"},
            "gate": {
                "min_confidence": 0.5,
                "min_agree_runs": {"patch": 1, "minor": 2},
                "require_evidence_kinds": [],
            },
        }
    }
    assert X.autonomy(cfg) == {"patch": "auto", "minor": "auto", "scope": "ask"}  # never auto
    card = {"class": "patch", "kind": "maintenance", "confidence": 0.4, "runs": {"agreeing": 1}}
    assert X.decide(cfg, {}, card) == ("list", "confidence 0.4 is under 0.5")
    card["confidence"] = 0.9
    assert X.decide(cfg, {}, card)[0] == "file"
    assert X.decide(cfg, {}, card, owned=False) == (
        "ask",
        "the repository is not yours: repo-factory asks first",
    )
    p = {"settings": {"autonomy.patch": "list"}}  # the project's own override
    assert X.decide(cfg, p, card)[0] == "list"
    cfg["projects"]["gate"]["require_evidence_kinds"] = ["customer_ask"]
    assert X.decide(cfg, {}, card)[0] == "list"
    card["evidence"] = ["issue:#3"]
    assert X.decide(cfg, {}, card)[0] == "file"


def test_the_tick_starts_due_runs_in_the_background(world):
    make, _ = world
    lp, p = make()
    assert evolve.due(lp, p) is None  # a week after it was made
    later = time.time() + 7 * 86400 + 60
    assert evolve.due(lp, p, later) == "the weekly check"
    ps = Projects(lp.ledger)
    assert life.start_runs(lp, ps, later, []) == [p["id"]]
    assert jobs.state(lp.ledger, "evolve", p["id"])["state"] == "queued"  # spawns nothing here
    assert life.start_runs(lp, ps, later, []) == []


def test_the_doors_answer_at_once(world):
    make, _ = world
    lp, p = make()
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    t0 = time.time()
    code, out = call(
        app,
        "POST",
        "/api/act",
        None,
        {
            "kind": "evolve_now",
            "subject": {"kind": "new"},
            "fields": {"project": p["id"]},
            "origin": "cli",
        },
    )
    assert code == 200 and "in the background" in out["say"] and time.time() - t0 < 5
    assert (
        call(
            app,
            "POST",
            "/api/act",
            None,
            {
                "kind": "evolve_now",
                "subject": {"kind": "new"},
                "fields": {"project": p["id"]},
                "origin": "ui",
            },
        )[0]
        == 409
    )
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    minor = next(ch for ch in X.all_of(lp.ledger, p["id"]) if ch["class"] == "minor")
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
    assert code == 200 and X.must(lp.ledger, minor["id"])["state"] == "queued"
    code, pg = call(app, "GET", "/api/project", {"id": p["id"]})
    assert len(pg["ideas"]) == 7 and pg["evolve"]["proposed"] == 7 and pg["evolve"]["next"]
    assert pg["autonomy"] == {"patch": "auto", "minor": "ask", "scope": "ask"}


def test_the_project_actions_are_in_the_registry_and_an_agent_may_run_them():
    for k in ("evolve_now", "accept_change", "decline_change", "charter_change"):
        assert k in R.ACTIONS and k in R.agent_default_actions() and k in R.PROJECT_KINDS
    assert R.ACTIONS["charter_change"].click == "confirm"
    assert "confirm" in R.schema("charter_change")["required"]


# -- Needs you by rule (GRD-DESIGN §7.3, GRD-7) ----------------------------------------------------
def test_the_loop_stops_asking_while_the_asks_wait(world):
    """projects.asks_open_max: with that many minor asks unanswered, a run proposes nothing new and
    makes no call (it says why); Check for ideas now still runs."""
    make, _ = world
    lp, p = make(projects={"asks_open_max": 1})
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    assert len([a for a in X.asks(lp.ledger, p["id"]) if a["class"] != "scope"]) == 1
    model = FakeModel(EVOLVE["receipts"])
    out = evolve.run(lp, p["id"], model)
    assert out == {"skipped": "1 asks open; not proposing more"} and model.prompts == []
    assert evolve.last_run(lp.ledger, p["id"])["skipped"] == "1 asks open; not proposing more"
    assert "proposed" in evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]), force=True)


def test_a_minor_ask_ages_out_of_needs_you_and_stays_on_its_page(world):
    """projects.ask_days: a minor ask older than it is stale (out of Needs you's count, on the
    project's page still); the header counts one row a project."""
    make, _ = world
    lp, p = make()
    evolve.run(lp, p["id"], FakeModel(EVOLVE["receipts"]))
    ask = X.asks(lp.ledger, p["id"])[0]
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    snap = call(app, "GET", "/api/state")[1]
    assert [a["stale"] for a in snap["project_asks"]] == [False]
    assert api.needs_rows([], snap["project_asks"]) == 1
    old = {**ask["card"]["ask"], "since": time.time() - 15 * 86400}
    X.update(lp.ledger, ask["id"], card={**ask["card"], "ask": old})
    snap = call(app, "GET", "/api/state")[1]
    assert [a["stale"] for a in snap["project_asks"]] == [True]
    assert T.problems(snap, T.Snapshot) == []
    assert api.needs_rows([], snap["project_asks"]) == 0
    assert X.asks(lp.ledger, p["id"]), "it stays an ask on the project's page"


def test_needs_rows_folds_drift_and_leaves_factory_prs_out():
    asks = [
        {"project": {"id": "p1"}, "stale": False},
        {"project": {"id": "p1"}, "stale": False},
        {"project": {"id": "p2"}, "stale": True},
    ]
    maint = [
        {"kind": "factory_pr", "project": {"id": "p1"}},
        {"kind": "drift", "project": {"id": "p1"}},
        {"kind": "drift", "project": {"id": "p2"}},
        {"kind": "needs_you", "project": None},
    ]
    assert api.needs_rows(maint, asks) == 1 + 2  # p1's row; p2's drift and the issue
