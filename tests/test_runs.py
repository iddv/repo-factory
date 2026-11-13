"""A product's Builds (UXD-DESIGN §2, UXI-1; ``repo_factory/ui/runs.py``). Build N is round N of the
event log: a fresh build, a retry and a revision are each a Build; repair rounds inside one are
part of it; a stopped Build that is resumed stays one. The log here has Sidedoor's shape on
15 Oct: two fresh builds, a retry that continues Build 1, a fresh one, six revisions (code,
docs, one asked by the pre-publish check), Build 9 failed in its build stage, Build 10 stopped
by you during packaging; plus a Build that was paused and resumed."""

import json
from pathlib import Path

import pytest

from repo_factory.loop import gate as gate_mod
from repo_factory.ui import api
from repo_factory.ui.runs import run, runs, who

T0 = 1_791_500_000.0
ALL = ["scope", "build", "claims", "verify", "oracle", "repair", "reverify", "readiness", "deliver"]
REV = ["build", "verify", "repair", "reverify", "deliver"]


class Log:
    def __init__(self, d: Path):
        self.d = d
        (d / ".den").mkdir(parents=True, exist_ok=True)
        self.ev = (d / ".den" / "events.jsonl").open("a")
        self.led = (d / ".den" / "ledger.jsonl").open("a")
        self.t = T0

    def emit(self, n, type, **f):
        self.t += 60
        self.ev.write(
            json.dumps({"v": 1, "ts": self.t, "task": "sd", "build": n, "type": type, **f}) + "\n"
        )
        self.ev.flush()

    def spend(self, n, stage, usd, tin, tout):
        self.led.write(
            json.dumps(
                {
                    "build": n,
                    "stage": stage,
                    "usd": usd,
                    "in_tokens": tin,
                    "out_tokens": tout,
                    "ts": self.t,
                }
            )
            + "\n"
        )
        self.led.flush()

    def full(self, n, **start):
        self.emit(n, "build_started", pipeline="v5", stages=ALL, title="Sidedoor", **start)
        for s in ALL:
            self.emit(n, "v5_stage", stage=s, status="started")
            if s == "reverify":
                self.emit(
                    n,
                    "v5_stage",
                    stage=s,
                    status="skipped",
                    why="repair.max_rounds (2) reached: no round could repair it",
                )
                continue
            self.emit(
                n,
                "v5_stage",
                stage=s,
                status="done",
                usd=0.5,
                wall_s=60,
                tokens_in=1000,
                tokens_out=100,
            )
        self.emit(n, "repair_round", round=1, status="repairing", sent=4)
        self.emit(n, "repair_round", round=1, status="done", sent=4, fixed=2)
        self.emit(n, "repair_round", round=2, status="done", sent=2, fixed=0)
        self.spend(n, "build", 5.0, 100_000, 2_000)
        self.emit(n, "build_finished", status="ok", cost_usd=5.0, wall_s=3600)

    def revise(self, n, of, finish=True, error=None, origin="ui", mode="docs"):
        self.emit(
            n,
            "build_started",
            pipeline="v5",
            stages=REV,
            kind="revise",
            revises=of,
            origin=origin,
            mode=mode,
            instructions=f"change {n}",
        )
        for s in ("scope", "claims", "oracle", "readiness"):
            self.emit(n, "v5_stage", stage=s, status="skipped", why=f"carried from round {of}")
        self.emit(n, "v5_stage", stage="build", status="started")
        if error:
            self.emit(n, "v5_stage", stage="build", status="failed", why=error)
            self.emit(
                n,
                "build_finished",
                status="error",
                stage="build",
                failure="error",
                error=f"revise failed in build: {error}",
                cost_usd=0.08,
                wall_s=400,
            )
            return
        self.emit(n, "v5_stage", stage="build", status="done", usd=0.05)
        self.emit(n, "v5_stage", stage="verify", status="started")
        self.emit(n, "v5_stage", stage="verify", status="done")
        for s in ("repair", "reverify"):
            self.emit(n, "v5_stage", stage=s, status="skipped", why="verify found nothing")
        self.emit(n, "v5_stage", stage="deliver", status="started")
        self.spend(n, "build", 0.05, 80_000, 1_000)
        if finish:
            self.emit(n, "v5_stage", stage="deliver", status="done")
            self.emit(n, "build_finished", status="ok", cost_usd=0.05, wall_s=1200)


def hrow(ts, state, note, **data):
    return {"ts": ts, "state": state, "note": note, "data": data or {}}


@pytest.fixture
def sidedoor(tmp_path):
    d = tmp_path / "sidedoor"
    log = Log(d)
    hist = [
        hrow(T0 - 30, "new", "sent to build by the owner; the pitch runs on the loop's next tick")
    ]
    log.full(1)
    hist.append(
        hrow(log.t + 1, "held", "built; held by the gate: completeness: 0/7 flows work end to end")
    )
    hist.append(hrow(log.t + 2, "queued", "retry: a fresh build"))
    log.full(2)
    hist.append(
        hrow(
            log.t + 1,
            "held",
            "built; held by the gate: tests: the README documents no test command",
        )
    )
    hist.append(hrow(log.t + 2, "queued", "retry: a new round, continuing round 1's stopped build"))
    log.full(3, continues="r1")
    hist.append(
        hrow(
            log.t + 1,
            "held",
            "built; held by the gate: tests: 44 of 70 of the product's own tests fail",
        )
    )
    hist.append(hrow(log.t + 2, "queued", "retry: a fresh build"))
    log.full(4)
    hist.append(
        hrow(
            log.t + 1,
            "held",
            "built; held by the gate: tests: 4 of 58 of the product's own tests fail",
        )
    )
    log.revise(5, 4, mode="code")
    hist.append(
        hrow(
            log.t + 1,
            "held",
            "revised in round 5 (ui): the gate says held; tests: "
            "a documented test command fails as written",
            origin="ui",
            kind="revise",
            round=5,
            verdict="held",
        )
    )
    log.revise(6, 5, mode="code")
    hist.append(
        hrow(
            log.t + 1,
            "ready",
            "revised in round 6 (ui): the gate says ready",
            origin="ui",
            kind="revise",
            round=6,
            verdict="ready",
        )
    )
    log.revise(7, 6, origin="honesty")
    hist.append(
        hrow(
            log.t + 1,
            "ready",
            "revised in round 7 (honesty): the gate says ready",
            origin="honesty",
            kind="revise",
            round=7,
            verdict="ready",
        )
    )
    hist.append(hrow(log.t + 2, "held", "held: not pushed: jargon scan: READINESS.md:10 brb"))
    log.revise(8, 7)
    hist.append(
        hrow(
            log.t + 1,
            "ready",
            "revised in round 8 (ui): the gate says ready",
            origin="ui",
            kind="revise",
            round=8,
            verdict="ready",
        )
    )
    hist.append(
        hrow(
            log.t + 2,
            "held",
            "held: not pushed: honesty check: 'records every credential': overstates",
        )
    )
    log.revise(9, 8, error="the change session changed nothing (ok)")
    log.revise(10, 8, finish=False)
    stop_at = log.t + 5
    hist.append(
        hrow(
            stop_at,
            "held",
            "round 10 stopped by the owner (ui); back to held",
            origin="ui",
            kind="stop_round",
            round=10,
        )
    )
    idea = {
        "id": "i1",
        "state": "held",
        "gate": {"verdict": "ready", "reasons": [], "round": 8},
        "build": {
            "round": 9,
            "stopped": {"round": 10, "at": stop_at, "resumable": True},
            "changes": [
                {"round": r, "verdict": v}
                for r, v in ((5, "held"), (6, "ready"), (7, "ready"), (8, "ready"))
            ],
        },
    }
    return d, idea, hist


def test_sidedoor_has_ten_builds_with_their_kinds_and_outcomes(sidedoor):
    d, idea, hist = sidedoor
    rs = runs(d, idea, hist)
    assert [r["n"] for r in rs] == list(range(1, 11))
    assert [r["kind"] for r in rs] == ["fresh", "fresh", "continue", "fresh"] + ["revision"] * 6
    assert [r["from"] for r in rs] == [None, None, 1, None, 4, 5, 6, 7, 8, 8]
    assert [r["outcome"] for r in rs] == ["checks_failed"] * 5 + ["ready"] * 3 + [
        "failed",
        "stopped",
    ]
    # who started each: you, except Build 7, which the pre-publish check asked for
    assert [r["started_by"]["by"] for r in rs] == ["you"] * 6 + ["repo-factory"] + ["you"] * 3
    # a reader can say which Build passed the checks (6, 7, 8) without the word "round"
    assert [r["n"] for r in rs if (r["gate"] or {}).get("verdict") == "ready"] == [6, 7, 8]
    assert rs[2]["gate"]["reasons"] == ["tests: 44 of 70 of the product's own tests fail"]
    # from the history note (no checks kept): six checks since p_hidden went (AU1 #5)
    assert (rs[2]["gate"]["passed"], rs[2]["gate"]["total"]) == (5, 6)
    assert rs[7]["gate"]["passed"] == 6 and rs[4]["gate"]["passed"] == 5
    assert rs[7]["gate"]["source"] == "idea" and rs[5]["gate"]["source"] in ("changes", "history")
    # what happened after: 7 and 8 were held by the pre-publish check
    assert rs[6]["after"]["what"] == "wording" and "jargon" in rs[6]["after"]["why"]
    assert rs[7]["after"]["what"] == "wording"
    # Build 9 failed in its build stage, with its own error; no checks ran on it
    assert rs[8]["stage"] == "build" and "changed nothing" in rs[8]["error"]
    assert rs[8]["gate"] is None
    # Build 10: stopped by you during packaging, resumable, its own cost and tokens only
    b10 = rs[9]
    assert b10["stopped"]["by"] == "you" and b10["stopped"]["resumable"]
    assert b10["stage"] == "deliver" and b10["latest"]
    assert b10["tokens"] == {"in": 80_000, "out": 1_000} and b10["cost_usd"] == 0.05
    assert b10["carried_from"] == 8 and b10["ran"] == ["build", "verify", "deliver"]


def test_one_build_says_why_each_stage_did_not_run(sidedoor):
    d, idea, hist = sidedoor
    b10 = run(d, 10, idea, hist)
    by = {s["stage"]: s for s in b10["stages"]}
    assert [s["stage"] for s in b10["stages"]] == ALL
    assert {k for k, s in by.items() if s["reason"] == "carried"} == {
        "scope",
        "claims",
        "oracle",
        "readiness",
    }
    assert all(by[k]["from"] == 8 for k in ("scope", "claims"))
    assert by["repair"]["reason"] == by["reverify"]["reason"] == "not_needed"
    assert by["deliver"]["status"] == "stopped"  # its process is gone: not running
    assert b10["neighbours"] == {"prev": 9, "next": None} and b10["of"] == 10
    b3 = run(d, 3, idea, hist)
    assert [r["round"] for r in b3["repairs"]] == [1, 2]  # repair rounds are part of Build 3
    assert {s["stage"]: s["reason"] for s in b3["stages"]}["reverify"] == "limit"
    assert all(s["reason"] is None for s in b3["stages"] if s["stage"] != "reverify")
    with pytest.raises(LookupError):
        run(d, 11, idea, hist)


def test_a_stopped_and_resumed_build_stays_one(tmp_path):
    d = tmp_path / "beliefline"
    log = Log(d)
    log.full(1)
    log.emit(
        2,
        "build_started",
        pipeline="v5",
        stages=REV,
        kind="revise",
        revises=1,
        origin="ui",
        mode="code",
    )
    log.emit(2, "v5_stage", stage="build", status="started")
    log.emit(2, "build_resumed", pid=1)
    log.emit(2, "build_resumed", pid=2)
    log.emit(2, "v5_stage", stage="build", status="done")
    log.emit(2, "build_finished", status="ok", cost_usd=0.1, wall_s=900)
    hist = [
        hrow(
            log.t + 1,
            "ready",
            "revised in round 2 (ui): the gate says ready",
            origin="ui",
            kind="revise",
            round=2,
            verdict="ready",
        ),
        hrow(
            log.t + 2, "published", "published privately: https://github.com/o/b (release v0.1.0)"
        ),
        hrow(log.t + 3, "public", "approved (by the owner): https://github.com/o/b is public"),
    ]
    rs = runs(d, {"id": "i2", "state": "public", "build": {"round": 2}}, hist)
    assert [r["n"] for r in rs] == [1, 2] and len(rs[1]["resumed"]) == 2
    assert rs[1]["outcome"] == "ready" and rs[1]["after"]["what"] == "public"


def test_the_gate_record_wins_and_is_written_beside_its_round(sidedoor):
    d, idea, hist = sidedoor
    gate_mod.record(
        d,
        {
            "verdict": "held",
            "reasons": ["security: open high"],
            "round": 6,
            "checks": {"security": {"ok": False, "detail": "open high"}},
        },
    )
    rec = json.loads((d / ".den" / "v5" / "r6" / "gate.json").read_text())
    assert rec["verdict"] == "held" and rec["checks"]["security"]["ok"] is False
    r6 = runs(d, idea, hist)[5]
    assert r6["gate"] == {
        "verdict": "held",
        "reasons": ["security: open high"],
        "source": "record",
        "passed": 0,
        "total": 1,
    }
    assert r6["outcome"] == "checks_failed"
    gate_mod.record(d, {"verdict": "ready"})  # no round: nothing written, no error
    assert not (d / ".den" / "v5" / "rNone").exists()


def test_a_hand_build_and_a_fix_and_no_log(tmp_path):
    d = tmp_path / "hand"
    log = Log(d)
    log.full(1)
    r = runs(d)[0]
    assert r["started_by"]["by"] == "you" and r["outcome"] == "delivered" and r["gate"] is None
    f = tmp_path / "fix-o-r-3"
    log = Log(f)
    log.emit(
        1,
        "build_started",
        pipeline="v5",
        stages=["reproduce", "build", "verify", "repair", "reverify", "readiness", "deliver"],
    )
    log.emit(1, "v5_stage", stage="reproduce", status="failed", why="already works")
    log.emit(1, "build_finished", status="error", stage="reproduce", error="already works")
    r = runs(f)[0]
    assert r["kind"] == "fix" and r["outcome"] == "failed" and r["ran"] == ["reproduce"]
    assert runs(tmp_path / "nothing") == []


def test_who_started_it_in_words():
    assert who("ui")["by"] == who("cli")["by"] == who("mcp")["by"] == "you"
    assert who("honesty") == {"by": "repo-factory", "door": "honesty", "name": None}
    assert who("agent:EI") == {"by": "agent", "door": "mcp", "name": "EI"}
    assert who(None, "retry: a fresh build")["by"] == "you"
    assert who(None, "accepted as X")["by"] == "repo-factory"


def test_the_runs_routes_answer(sidedoor, tmp_path):
    d, _, _ = sidedoor
    app = api.App(tmp_path, "fake", tmp_path / "loop-home")
    code, _, body = api.handle("GET", "/api/build/runs", {"dir": str(d)}, app=app)
    got = json.loads(body)
    assert code == 200 and [r["n"] for r in got["runs"]][:2] == [10, 9]  # newest first
    code, _, body = api.handle("GET", "/api/run", {"dir": str(d), "n": "9"}, app=app)
    assert code == 200 and json.loads(body)["run"]["outcome"] == "failed"
    assert api.handle("GET", "/api/run", {"dir": str(d), "n": "x"}, app=app)[0] == 400
    assert api.handle("GET", "/api/run", {"dir": str(d), "n": "99"}, app=app)[0] == 404


def test_a_row_never_quotes_an_earlier_builds_failure(sidedoor, tmp_path):
    """Build 9 failed and Build 10 was stopped by you: build.log's last failure line is Build
    9's, so the row (Today, Builds) must not show it as the product's failure (N1)."""
    d, idea, _ = sidedoor
    (d / ".den" / "build.log").write_text(
        "starting\n✗ build failed after 6m52s: revise failed in "
        "build: the change session changed nothing (ok)\n"
    )
    app = api.App(tmp_path, "fake", tmp_path / "loop-home")
    app.loop = lambda: None  # no ledger: the row's own record is enough here
    app.precheck_of = app.claims_of = lambda i: None
    held = {**idea, "build": {**idea["build"], "failures": 1}, "pitch": {"product": "Sidedoor"}}
    row = app.build_row(d, held)
    assert row["builds"]["latest"]["n"] == 10 and row["fail"] is None
