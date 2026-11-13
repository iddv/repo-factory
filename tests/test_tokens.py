"""Tokens next to dollars (the UI shows tokens per thing; dollars stay for Settings > Spend):
the loop's spend table gains token columns by an add-only migration, the den ledger, the
v5_stage events and their fold, the v5 view, the build rows, triage, the pitch, the context,
revise, the status' today/week and the maintenance spend. Old records without token data
read as None, never as zero and never from dollars. Offline: scripted models and harnesses,
the fake claude."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from repo_factory.events import EventLog, fold, read_events
from repo_factory.loop import ledger as loop_ledger
from repo_factory.loop.models import Costed, split
from repo_factory.maintain import github, revise, triage
from repo_factory.ui import api, server

from .loop_common import FakeModel, make_loop
from .test_maintain_revise import REPO, Scripted, fix, fixes, names_test
from .test_maintain_revise import world as world  # noqa: F401  (fixture)
from .test_maintain_triage import FakeGitHub, fixture_issues, maintained, scripted

# -- the spend table ---------------------------------------------------------------------------
OLD_SCHEMA = """
CREATE TABLE ideas (
  id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
  source_ref TEXT, title TEXT, text TEXT NOT NULL, urls TEXT, context TEXT, card TEXT,
  state TEXT NOT NULL, reason TEXT, pitch TEXT, task_dir TEXT, build TEXT, gate TEXT,
  publish TEXT, signals TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea TEXT NOT NULL, ts REAL NOT NULL,
  state TEXT NOT NULL, note TEXT, data TEXT);
CREATE TABLE spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea TEXT, ts REAL NOT NULL, kind TEXT NOT NULL,
  usd REAL NOT NULL);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def test_an_old_loop_db_gains_token_columns_in_place(tmp_path):
    db = tmp_path / "loop.db"
    c = sqlite3.connect(db)
    c.executescript(OLD_SCHEMA)
    c.execute(
        "INSERT INTO spend (idea, ts, kind, usd) VALUES (?,?,?,?)",
        ("i1", time.time(), "pitch", 0.25),
    )
    c.commit()
    c.close()
    led = loop_ledger.Ledger(db)
    with led.db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(spend)")}
        rows = [dict(r) for r in c.execute("SELECT * FROM spend")]
    assert {"in_tokens", "out_tokens"} <= cols
    assert rows == [{**rows[0], "in_tokens": None, "out_tokens": None}] and rows[0]["usd"] == 0.25
    assert led.own_spend(0) == pytest.approx(0.25)
    assert led.own_tokens(0) is None, "an old row has no token data: None, not zero"
    led.spend("triage", 0.01, "i1", in_tokens=1200, out_tokens=80)
    led.spend("pitch", 0.02, "i1")  # a caller that knows no tokens
    assert led.own_tokens(0) == {"in": 1200, "out": 80}
    assert led.own_tokens(0, kind="pitch") is None
    loop_ledger.Ledger(db)  # opening again changes nothing
    with led.db() as c:
        assert c.execute("SELECT COUNT(*) FROM spend").fetchone()[0] == 3


def test_status_sums_tokens_today_and_this_week(tmp_path):
    lp = make_loop(tmp_path)
    assert lp.status()["tokens"] == {"today": None, "week": None}
    i, _ = lp.ledger.add("clinic booking", "typed")
    task = tmp_path / "tasks" / "clinic"
    (task / ".den").mkdir(parents=True)
    now = time.time()
    lines = [
        {"ts": now, "usd": 0.5, "in_tokens": 1000, "out_tokens": 100},
        {"ts": now, "usd": 0.2},  # no token fields
        {"ts": now - 40 * 86400, "usd": 9.0, "in_tokens": 9, "out_tokens": 9},
    ]
    (task / ".den" / "ledger.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    lp.ledger.update(i["id"], task_dir=str(task))
    lp.ledger.spend("chat", 0.03, i["id"], in_tokens=10, out_tokens=20)
    st = lp.status()
    assert st["tokens"]["today"] == {"in": 1010, "out": 120}
    assert st["tokens"]["week"] == {"in": 1010, "out": 120}
    assert st["spend"]["today"]["builds"] == pytest.approx(0.7), "dollars unchanged"


# -- the model callable, triage and the pitch ---------------------------------------------------
class TokenModel(FakeModel):
    def __call__(self, prompt):
        text, usd = super().__call__(prompt)
        return Costed(text, usd, {"in": 300, "out": 40})


def test_a_reply_carries_tokens_and_still_unpacks_as_two():
    text, usd = Costed("hi", 0.1, {"in": 1, "out": 2})
    assert (text, usd) == ("hi", 0.1)
    assert split(Costed("hi", 0.1, {"in": 1, "out": 2})) == ("hi", 0.1, {"in": 1, "out": 2})
    assert split(("hi", 0.1)) == ("hi", 0.1, None), "a plain 2-tuple has no tokens"
    assert split(("hi", 0.1, (5, 6))) == ("hi", 0.1, {"in": 5, "out": 6})


def test_triage_counts_the_tokens_of_both_tries_and_records_them():
    it = github.issue(fixture_issues()[0])
    bad = TokenModel(
        ["not json", json.dumps({"kind": "bug", "prio": "p1", "summary": "x"})], usd=0.01
    )
    out = triage.classify(it, bad, product="P")
    res, usd = out
    assert res["kind"] == "bug" and usd == pytest.approx(0.02)
    assert out.tokens == {"in": 600, "out": 80}
    plain = triage.classify(
        it, FakeModel(json.dumps({"kind": "bug", "prio": "p1", "summary": "x"})), product="P"
    )
    assert plain.tokens is None


def test_the_poll_records_triage_tokens_in_spend(tmp_path, maintenance_on):
    from repo_factory.gh import Gh

    lp = make_loop(tmp_path)
    lp._gh = Gh(run=FakeGitHub(fixture_issues(), policy_text=maintained()))
    base = scripted()
    lp._triage = lambda p: Costed(*base(p), {"in": 500, "out": 50})
    i, _ = lp.ledger.add("allotment plots", "typed", title="Plotkeeper")
    lp.ledger.update(
        i["id"],
        state="published",
        pitch={"product": "Plotkeeper", "one_liner": "Allotment plots."},
        publish={"repo": "o/plots", "url": "https://github.com/o/plots"},
    )
    lp.maintain()
    assert lp.ledger.own_tokens(0, kind="triage") == {"in": 6 * 500, "out": 6 * 50}
    a = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    a.loop = lambda: lp
    ms = a.maint_spend(i["id"], 0)
    assert ms["tokens"] == {"in": 3000, "out": 300}
    assert ms["by_kind"]["triage"]["tokens"] == {"in": 3000, "out": 300}
    assert ms["usd"] == pytest.approx(6 * 0.02)
    lp.ledger.spend("lesson", 0.5, i["id"])  # no token data for this kind
    ms = a.maint_spend(i["id"], 0)
    assert ms["by_kind"]["lesson"]["tokens"] is None and ms["tokens"] == {"in": 3000, "out": 300}
    assert a.maint_spend("nobody", 0)["tokens"] is None


def test_the_pitch_records_its_tokens(tmp_path):
    from repo_factory.loop.pitch import pitch_idea

    from .loop_common import pitch_json

    lp = make_loop(tmp_path, model=TokenModel(pitch_json()))
    i, _ = lp.ledger.add("clinic booking for small clinics", "typed")
    pitch_idea(lp.ledger, i, lp.model, lp.cfg)
    assert lp.ledger.own_tokens(0, kind="pitch") == {"in": 300, "out": 40}


# -- the den ledger, the events and the v5 view -------------------------------------------------
def test_v5_stage_events_fold_tokens_and_old_events_have_none():
    base = {"task": "t", "build": 1}
    evs = [
        {**base, "type": "build_started", "ts": 1.0, "pipeline": "v5"},
        {**base, "type": "v5_stage", "ts": 2.0, "stage": "build", "status": "started"},
        {
            **base,
            "type": "v5_stage",
            "ts": 3.0,
            "stage": "build",
            "status": "done",
            "usd": 0.5,
            "tokens_in": 1000,
            "tokens_out": 200,
        },
        {**base, "type": "v5_stage", "ts": 4.0, "stage": "repair", "status": "started"},
        {
            **base,
            "type": "v5_stage",
            "ts": 5.0,
            "stage": "repair",
            "status": "done",
            "usd": 0.1,
            "tokens_in": 10,
            "tokens_out": 1,
        },
        {**base, "type": "v5_stage", "ts": 6.0, "stage": "repair", "status": "started"},
        {
            **base,
            "type": "v5_stage",
            "ts": 7.0,
            "stage": "repair",
            "status": "done",
            "usd": 0.1,
            "tokens_in": 30,
            "tokens_out": 3,
        },
        {**base, "type": "v5_stage", "ts": 8.0, "stage": "verify", "status": "done", "usd": 0.2},
    ]  # an older event
    s = fold(evs)["v5"]["stages"]
    assert (s["build"]["tokens_in"], s["build"]["tokens_out"]) == (1000, 200)
    assert (s["repair"]["tokens_in"], s["repair"]["tokens_out"]) == (40, 4)
    assert "tokens_in" not in s["verify"]


def task_dir(root: Path, name: str) -> Path:
    d = root / name
    (d / ".den").mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    return d


def test_the_v5_view_and_the_build_row_show_tokens_or_none(tmp_path):
    root = tmp_path / "root"
    new, old = task_dir(root, "new"), task_dir(root, "old")
    for d, tok in ((new, True), (old, False)):
        log = EventLog(d / ".den" / "events.jsonl", d.name, 1)
        log.emit("build_started", pid=None, pipeline="v5", policy="v5.0")
        for s, n in (("build", 1000), ("verify", 500)):
            log.emit("v5_stage", stage=s, status="started")
            log.emit(
                "v5_stage",
                stage=s,
                status="done",
                usd=0.4,
                wall_s=10,
                **({"tokens_in": n, "tokens_out": n // 10} if tok else {}),
            )
        log.emit("build_finished", status="ok", cost_usd=0.8, wall_s=20)
    recs = [
        {"build": 1, "stage": "build", "usd": 0.4, "in_tokens": 1000, "out_tokens": 100},
        {"build": 1, "stage": "verify", "usd": 0.4, "in_tokens": 500, "out_tokens": 50},
        {"build": 1, "caller": "judge", "usd": 0.01, "in_tokens": 7, "out_tokens": 3},
    ]
    (new / ".den" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (old / ".den" / "ledger.jsonl").write_text(
        json.dumps({"build": 1, "stage": "build", "usd": 0.8}) + "\n"
    )
    app = server.App(root, "claude-code")
    v = app.state(new)["v5"]
    strip = {x["stage"]: x for x in v["stages"]}
    assert strip["build"]["tokens"] == {"in": 1000, "out": 100}
    assert strip["verify"]["tokens"] == {"in": 500, "out": 50}
    assert strip["oracle"]["tokens"] is None, "a stage that never ran has no tokens"
    # the total: the ledger's whole build (a call outside the stages counts once finished)
    assert v["cost"]["tokens"] == {"in": 1507, "out": 153}
    assert v["cost"]["total"] == pytest.approx(0.8), "dollars still there"
    vo = app.state(old)["v5"]
    assert all(x["tokens"] is None for x in vo["stages"]) and vo["cost"]["tokens"] is None
    rn, ro = app.build_row(new, None), app.build_row(old, None)
    assert rn["tokens"] == {"in": 1507, "out": 153} and rn["cost_usd"] == pytest.approx(0.8)
    assert ro["tokens"] is None and ro["last_tokens"] is None
    assert ro["cost_usd"] == pytest.approx(0.8)


def test_a_running_stage_takes_its_tokens_from_the_ledger():
    from repo_factory.ui.v5view import stages

    v = {
        "stages": {
            "build": {
                "stage": "build",
                "status": "done",
                "usd": 0.3,
                "tokens_in": 5,
                "tokens_out": 1,
            },
            "verify": {"stage": "verify", "status": "started"},
        }
    }
    live = {x["stage"]: x for x in stages(v, True, {}, {"verify": {"in": 70, "out": 7}})}
    assert live["verify"]["tokens"] == {"in": 70, "out": 7}
    assert live["build"]["tokens"] == {"in": 5, "out": 1}
    assert live["claims"]["tokens"] is None
    assert {x["stage"]: x for x in stages(v, False, {})}["verify"]["tokens"] is None


def test_the_build_row_has_the_last_rounds_tokens(tmp_path):
    root = tmp_path / "root"
    d = task_dir(root, "two")
    log = EventLog(d / ".den" / "events.jsonl", "two", 2)
    log.emit("build_started", pid=None)
    log.emit("build_finished", status="ok", cost_usd=0.3, wall_s=5)
    recs = [
        {"build": 1, "usd": 0.2, "in_tokens": 100, "out_tokens": 10},
        {"build": 2, "usd": 0.3, "in_tokens": 300, "out_tokens": 30},
    ]
    (d / ".den" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    app = server.App(root, "claude-code")
    idea = {
        "id": "i1",
        "state": "held",
        "source": "typed",
        "build": {"round": 2, "cost_usd": 0.3},
        "updated": 1.0,
        "pitch": {},
    }
    row = app.build_row(d, idea)
    assert row["tokens"] == {"in": 400, "out": 40}
    assert row["last_tokens"] == {"in": 300, "out": 30}
    assert row["last_cost_usd"] == 0.3


# -- the context a session reads --------------------------------------------------------------


# -- revise (a fix) -------------------------------------------------------------------------------
class TokenScripted(Scripted):
    def run(self, prompt, workspace, timeout_s, **kw):
        res = super().run(prompt, workspace, timeout_s, **kw)
        res.input_tokens, res.output_tokens = 2000, 150
        return res


def test_a_fix_records_its_tokens(world):  # noqa: F811
    from repo_factory.costs import ledger

    lp, fake, iid = world
    out = fix(lp, TokenScripted(names_test(), fixes)).run()
    assert out["outcome"] == "pr", out
    assert out["tokens"] == {"in": 4000, "out": 300}
    task = revise.task_dir(lp.cfg.tasks_dir, REPO, 14)
    recs = [r for r in ledger.read(task / ".den") if r.get("caller") == "worker"]
    assert [(r["in_tokens"], r["out_tokens"]) for r in recs] == [(2000, 150), (2000, 150)]
    assert lp.ledger.own_tokens(0, kind="revise") == {"in": 4000, "out": 300}
    st = fold(read_events(task / ".den" / "events.jsonl"))
    s = st["v5"]["stages"]
    assert (s["reproduce"]["tokens_in"], s["build"]["tokens_in"]) == (2000, 2000)
    assert revise.result(task)["tokens"] == {"in": 4000, "out": 300}
