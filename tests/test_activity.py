"""Live activity: ``call_started``/``call_finished`` from ``call_claude`` (thread-safe), the
folded ``activity`` state (what runs now, recently finished, phase, progress, cost so far),
old logs without call events, the fold staying bounded, the terminal's long-call line and
``repo-factory status``, and a fake v3 build with design forks, mining and a backtrack."""

import io
import json
import subprocess
import threading
import time
from types import SimpleNamespace

from repo_factory.costs import ledger
from repo_factory.events import EventLog, fold, load_state, read_events
from repo_factory.progress import Renderer, status_text

from .test_ledger import fake_claude


def of(evs, t):
    return [e for e in evs if e["type"] == t]


def active(tmp_path, build=3):
    log = EventLog(tmp_path / "events.jsonl", "t", build)
    return log, ledger.activate(tmp_path / "ledger.jsonl", build, events=log)


# -- call_* events from call_claude ------------------------------------------------------------


def test_call_claude_emits_started_and_finished(tmp_path, monkeypatch):
    run, _ = fake_claude([0.25, None, None, None])
    monkeypatch.setattr(subprocess, "run", run)
    log, ctx = active(tmp_path)
    with ctx:
        log.emit("build_started", pid=1)
        with ledger.tags(milestone="m2"):
            ledger.call_claude("brief", "sonnet", caller="scoper", purpose="brief")
        try:
            ledger.call_claude("x", caller="judge", purpose="requirements", retries=3)
        except RuntimeError:
            pass
    evs = read_events(tmp_path / "events.jsonl")
    s, f = of(evs, "call_started"), of(evs, "call_finished")
    assert [e["caller"] for e in s] == ["scoper", "judge"] and len(f) == 2
    assert s[0]["call"] == f[0]["call"] and s[0]["call"] != s[1]["call"]
    assert (s[0]["purpose"], s[0]["model"], s[0]["milestone"], s[0]["category"]) == (
        "brief",
        "sonnet",
        "m2",
        "planning",
    )
    assert f[0]["usd"] == 0.25 and f[0]["ok"] is True and f[0]["wall_s"] >= 0
    assert f[1]["ok"] is False and f[1]["attempts"] == 3 and "overloaded" in f[1]["error"]
    assert f[1]["usd"] is None
    assert "background" not in s[0]
    # the ledger still gets every attempt
    assert len(ledger.read(tmp_path)) == 4


def test_no_events_without_an_active_build_log(tmp_path, monkeypatch):
    run, _ = fake_claude([0.1, 0.1])
    monkeypatch.setattr(subprocess, "run", run)
    ledger.call_claude("x", caller="planner")  # no ledger at all
    with ledger.activate(tmp_path / "ledger.jsonl", 1):  # a ledger, no log
        ledger.call_claude("x", caller="planner")
    assert not (tmp_path / "events.jsonl").exists()


def test_call_events_are_thread_safe(tmp_path, monkeypatch):
    def run(cmd, input=None, timeout=None, **kw):
        time.sleep(0.001)
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"result": "ok", "total_cost_usd": 0.01, "usage": {}}),
        )

    monkeypatch.setattr(subprocess, "run", run)
    log, ctx = active(tmp_path)
    errors = []

    def worker(k):
        try:
            for _ in range(25):
                ledger.call_claude("p", caller="miner", purpose=f"mine:m{k}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    with ctx:
        log.emit("build_started", pid=1)
        ths = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
    assert not errors
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    evs = [json.loads(x) for x in lines]  # every line is whole
    s, f = of(evs, "call_started"), of(evs, "call_finished")
    assert len(s) == len(f) == 200
    assert len({e["call"] for e in s}) == 200 and {e["call"] for e in f} == {e["call"] for e in s}
    assert all(e.get("background") for e in s)
    st = fold(evs)
    a = st["activity"]
    assert a["running"] == [] and a["calls"]["finished"] == 200
    assert a["calls"]["by_caller"]["miner"]["calls"] == 200
    assert abs(a["cost"]["breakdown"]["mining"] - 2.0) < 1e-6


def test_a_nested_same_caller_call_folds_into_its_span(tmp_path, monkeypatch):
    from repo_factory.rules.llm import ClaudeLLM

    run, _ = fake_claude([0.3, 0.2])
    monkeypatch.setattr(subprocess, "run", run)
    log, ctx = active(tmp_path)
    with ctx:
        ClaudeLLM()("[factory-rules task=mine mode=edges]\nmine")  # one row, not two
        with ledger.track("planner", "plan"):  # planner + tech rows
            ledger.call_claude("adr", caller="tech", purpose="adr")
    f = of(read_events(tmp_path / "events.jsonl"), "call_finished")
    assert [(e["caller"], e["purpose"], e["usd"]) for e in f] == [
        ("miner", "mine:edges", 0.3),
        ("tech", "adr", 0.2),
        ("planner", "plan", None),
    ]


# -- the activity fold --------------------------------------------------------------------------


def ev(t, ts, **f):
    return {"v": 1, "ts": ts, "task": "t", "build": 1, "type": t, **f}


PLAN = [
    {
        "id": "m1",
        "title": "One",
        "tasks": [
            {"id": "m1.t1", "title": "Model", "uncertainty": "design", "hints": ["A", "B"]},
            {"id": "m1.t2", "title": "Glue"},
        ],
    },
    {"id": "m2", "title": "Two"},
]


def test_activity_running_then_finished():
    evs = [
        ev("build_started", 100, pid=1),
        ev("plan", 101, version=1, milestones=PLAN),
        ev("milestone_started", 102, id="m1", n=1),
        ev(
            "task_started",
            102,
            milestone="m1",
            task_id="m1.t1",
            kind="design",
            n=2,
            hints=["A", "B"],
        ),
        ev("call_started", 103, call="c1", caller="scoper", purpose="brief", unit="m1.t1"),
        ev(
            "call_finished",
            104,
            call="c1",
            caller="scoper",
            usd=0.05,
            ok=True,
            wall_s=1.0,
            category="planning",
        ),
        ev("candidate_started", 105, candidate=1, milestone="m1", task_id="m1.t1", hint=0),
        ev("candidate_started", 105, candidate=2, milestone="m1", task_id="m1.t1", hint=1),
        ev(
            "mining_started",
            106,
            point="after:m0",
            after="m0",
            modes=["interactions"],
            **{"async": True},
        ),
    ]
    st = fold(evs)
    a = st["activity"]
    labels = [x["label"] for x in a["running"]]
    assert labels == [
        "Worker: m1.t1 · way 1 (A)",
        "Worker: m1.t1 · way 2 (B)",
        "Mining interactions in the background",
    ]
    assert a["recent"][0]["label"] == "Writing brief for m1.t1" and a["recent"][0]["ok"]
    assert a["phase"] == "building" and not a["inferred"]
    assert a["progress"] == {
        "milestones": [0, 2],
        "milestone": "m1",
        "tasks": [0, 2],
        "fraction": 0.0,
    }
    f = a["forks"]
    assert f["task_id"] == "m1.t1" and [x["status"] for x in f["forks"]] == ["running"] * 2
    assert not f["judging"]

    evs += [
        ev("candidate_finished", 110, candidate=1, node=7, valid=True, usd=0.4),
        ev("candidate_finished", 111, candidate=2, node=8, valid=True, usd=0.6),
        ev("call_started", 112, call="c2", caller="pairwise", task_id="m1.t1", n=2, milestone="m1"),
    ]
    a = fold(evs)["activity"]
    assert a["forks"]["judging"]
    assert [x["label"] for x in a["running"] if x["kind"] == "call"] == [
        "Judging 2 candidates for m1.t1 (pairwise)"
    ]
    evs += [
        ev(
            "call_finished", 113, call="c2", caller="pairwise", usd=0.1, ok=True, category="judging"
        ),
        ev(
            "task_finished",
            114,
            milestone="m1",
            task_id="m1.t1",
            kind="design",
            passed=True,
            winner=8,
            candidates=[
                {"node": 7, "outcome": "loser"},
                {"node": 8, "outcome": "winner", "points": 1.5},
            ],
        ),
        ev("mining_finished", 115, point="after:m0", usd=0.2, mined=3),
        ev("controller_decision", 116, milestone="m1", option="backtrack"),
        ev(
            "backtrack",
            116,
            number=1,
            kind="backtrack",
            target="milestone",
            from_milestone="m2",
            change="add branch_id to checkout",
        ),
    ]
    st = fold(evs)
    a = st["activity"]
    assert [x["outcome"] for x in a["forks"]["forks"]] == ["loser", "winner"]
    assert a["running"] == [] and len(a["recent"]) == 5
    assert a["recent"][0]["kind"] == "mining" and a["recent"][0]["wall_s"] == 9
    assert a["progress"]["tasks"] == [1, 2] and a["progress"]["fraction"] == 0.25
    assert a["cost"]["breakdown"] == {
        "workers": 1.0,
        "mining": 0.0,
        "judging": 0.1,
        "planning": 0.05,
        "other": 0.0,
    }
    assert (
        st["controller"]["backtracks"][0]["text"]
        == "Backtracked to before m2: add branch_id to checkout"
    )
    evs.append(
        ev(
            "build_finished",
            120,
            status="ok",
            cost_usd=2.0,
            cost_breakdown={
                "workers": 1.0,
                "mining": 0.2,
                "judging": 0.1,
                "planning": 0.7,
                "other": 0.0,
            },
        )
    )
    a = fold(evs)["activity"]
    assert a["phase"] == "done" and a["cost"]["total"] == 2.0


def test_phases_from_running_calls():
    base = [
        ev("build_started", 1, pid=1),
        ev("question", 2, id="q1", text="?"),
        ev("answer", 3, id="q1", text="x"),
    ]
    assert fold(base)["activity"]["phase"] == "questions"
    for caller, phase in (
        ("tech", "tech selection"),
        ("planner", "planning"),
        ("plan_review", "plan review"),
    ):
        st = fold(base + [ev("call_started", 4, call="c", caller=caller)])
        assert st["activity"]["phase"] == phase
    st = fold(base + [ev("call_started", 4, call="c", caller="builder", purpose="tests")])
    assert st["activity"]["phase"] == "tests"
    st = fold(
        base + [ev("plan", 5, version=1, milestones=PLAN), ev("milestone_started", 6, id="review1")]
    )
    assert st["activity"]["phase"] == "closing review"


def test_old_logs_infer_running_from_start_without_finish(tmp_path):
    evs = [
        ev("build_started", 1, pid=1),
        ev("candidate_started", 2, candidate=1, strategy="spec_literal", milestone="m4"),
        ev("candidate_started", 3, candidate=2, strategy="minimal"),
        ev("candidate_finished", 4, candidate=2, node=5, valid=False, usd=0.3),
        ev("mining_started", 5, point="after:m3", after="m3", modes=["edges"]),
        ev("brief", 1.5, candidate=1, unit="m4", usd=0.02),
    ]
    a = fold(evs)["activity"]
    assert a["inferred"] is True
    assert [x["label"] for x in a["running"]] == [
        "Worker: m4 · spec_literal",
        "Mining edges after m3",
    ]
    assert a["recent"][0]["ok"] is False and a["recent"][0]["wall_s"] == 1
    assert a["cost"]["breakdown"]["planning"] == 0.02 and a["cost"]["breakdown"]["workers"] == 0.3
    # a dead process: nothing shows as running
    p = tmp_path / "events.jsonl"
    p.write_text(
        "\n".join(
            json.dumps({**e, "pid": 2**22 + 99} if e["type"] == "build_started" else e) for e in evs
        )
        + "\n"
    )
    st = load_state(p)
    assert st["status"] == "interrupted" and st["activity"]["running"] == []


def test_fold_stays_bounded_with_many_call_events():
    evs = [ev("build_started", 0, pid=1)]
    for i in range(30000):
        evs.append(ev("call_started", i, call=f"c{i}", caller="scoper", unit=f"m{i % 9}"))
        evs.append(
            ev(
                "call_finished",
                i + 0.5,
                call=f"c{i}",
                caller="scoper",
                usd=0.001,
                ok=i % 100 != 0,
                wall_s=0.5,
                category="planning",
            )
        )
    evs.append(ev("call_started", 40000, call="live", caller="controller", milestone="m5"))
    t0 = time.time()
    st = fold(evs)
    took = time.time() - t0
    a = st["activity"]
    assert took < 3, took
    assert [x["label"] for x in a["running"]] == ["Controller deciding after m5"]
    assert len(a["recent"]) == 8
    assert a["calls"]["started"] == 30001 and a["calls"]["finished"] == 30000
    assert a["calls"]["failed"] == 300
    assert len(json.dumps(st)) < 6000  # no per-call entries anywhere


# -- terminal parity ----------------------------------------------------------------------------


def test_renderer_prints_one_line_per_long_call():
    out = io.StringIO()
    r = Renderer(out, heartbeat_s=0)
    now = time.time()
    r(ev("call_started", now - 61, call="a", caller="scoper", unit="m3.t2"))
    r(ev("call_started", now - 5, call="b", caller="judge", purpose="requirements"))
    assert out.getvalue() == ""  # call events print nothing
    lines = r.long_calls(now)
    assert lines == ["  · still working (1m01s): Writing brief for m3.t2 [scoper]"]
    assert r.long_calls(now + 30) == []  # once per call
    assert len(r.long_calls(now + 60)) == 1  # b crossed 60s
    r(ev("call_finished", now, call="a"))
    assert "a" not in r.calls


def test_status_shows_what_is_running_now():
    now = time.time()
    st = fold(
        [
            ev("build_started", now - 300, pid=1),
            ev("plan", now - 250, version=1, milestones=PLAN),
            ev("milestone_started", now - 200, id="m1", n=1),
            ev("candidate_started", now - 90, candidate=1, milestone="m1", strategy="spec_literal"),
            ev(
                "call_started",
                now - 30,
                call="c",
                caller="miner",
                purpose="mine:edges",
                background=True,
            ),
        ]
    )
    text = status_text(st)
    assert "Now: phase: building · milestones 0/2" in text and "elapsed 5m00s" in text
    assert "Running now:" in text and "… Worker: m1 · spec_literal (1m30s)" in text
    assert "Miner proposing edges rules · background (30s)" in text


# -- a fake v3 build: design forks, mining, the controller and a backtrack -----------------------
