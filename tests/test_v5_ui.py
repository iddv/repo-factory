"""``repo-factory ui`` and build v5 (docs/design/build-v5.md §3.8, Addendum A): the policy choice, the
v5 view (stages, findings, settings, rounds, cost, delivered files), the phase
labels, and that v3/v4 views are unchanged. Offline: v5 dens come from Track D's scripted
builds (``FakeHarness`` plus ``ScriptedLLM``), never from a real session."""

import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from repo_factory.events import EventLog, fold, read_events
from repo_factory.ui import api, server
from repo_factory.ui.server import HttpError, make_server
from repo_factory.v5 import pipeline
from repo_factory.v5.common import STAGES

from . import test_v5_pipeline as defect_case
from . import test_v5_settings as settings_case
from .auth_common import ui_bearer

FIXTURE = Path(__file__).parent / "fixtures" / "repo_task"
V4_LOG = Path(__file__).parent / "fixtures" / "v5_run10" / "events.jsonl"
REPO = Path(__file__).resolve().parents[1]


def serve(root, harness="fake"):
    srv = make_server(root, port=0, harness=harness)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def get(base, path, **q):
    url = base + path + ("?" + urllib.parse.urlencode(q) if q else "")
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode()
        return (json.loads(body) if "json" in r.headers["Content-Type"] else body), r.headers[
            "Content-Type"
        ]


def post(base, path, body):
    req = urllib.request.Request(
        base + path,
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Origin": base, **ui_bearer()},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def new_task(root, name, budget=None):
    d = root / name
    shutil.copytree(FIXTURE, d, ignore=shutil.ignore_patterns(".den", "out", "_fake_solution"))
    y = (d / "task.yaml").read_text().replace("id: textstats", f"id: {name}")
    (d / "task.yaml").write_text(y + (f"budget_usd: {budget}\n" if budget is not None else ""))
    return d


# -- the policy choice --------------------------------------------------------------------------


def test_start_passes_the_chosen_policy(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = new_task(root, "textstats", budget=4.5)
    seen = []
    for harness in ("fake", "claude-code"):
        app = server.App(root, harness)
        monkeypatch.setattr(app, "spawn", lambda d, args: seen.append(args) or {"ok": True})
        if harness == "fake":
            app.start(d)  # no policy: repo-factory build's default
            assert seen[-1] == ["build", str(d), "--owner", "queue", "--harness", "fake"]
            with pytest.raises(HttpError) as e:  # v5 would spend under "fake"
                app.start(d, "v5")
            assert e.value.code == 400 and "fake harness" in str(e.value)
        else:
            app.start(d, "v5")
            # interpret: auto (the policy's default): nobody is asked; the budget is the cap
            assert seen[-1] == [
                "build",
                str(d),
                "--policy",
                str(REPO / "policies" / "v5"),
                "--owner",
                "none",
                "--max-cost",
                "4.5",
                "--harness",
                "claude-code",
            ]
        for bad in ("v2", "v3", "v4", "../v5", "policies/v5"):  # v5 only (O-2)
            with pytest.raises(HttpError) as e:
                app.start(d, bad)
            assert e.value.code == 400


def test_a_v5_policy_with_interpret_ask_asks_through_the_queue(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = new_task(root, "textstats")
    pol = tmp_path / "repo" / "policies" / "v5"
    shutil.copytree(REPO / "policies" / "v5", pol)
    rules = (pol / "rules.yaml").read_text()
    (pol / "rules.yaml").write_text(
        rules.replace("interpret: {mode: auto", "interpret: {mode: ask")
    )
    monkeypatch.setattr(api, "REPO", tmp_path / "repo")
    app = server.App(root, "claude-code")
    assert app.policy_args(d, "v5") == ["--policy", str(pol), "--owner", "queue"]


def test_the_page_offers_v5_only_and_never_spends_under_fake(tmp_path, monkeypatch):
    """O-2: v5 is the product pipeline; the page offers nothing else, and under the fake
    harness (test mode) it refuses v5, which would run real sessions."""
    monkeypatch.setenv("REPO_FACTORY_WORK_ROOT", str(tmp_path / "work"))
    root = tmp_path / "root"
    d = new_task(root, "textstats")
    srv, base = serve(root)
    try:
        info, _ = get(base, "/api/info")
        assert info["policies"] == ["v5"] and info["policy"] == "v5"
        for policy, why in (("v5", "fake harness"), ("v3", "policy must be one of v5")):
            with pytest.raises(urllib.error.HTTPError) as e:
                post(base, "/api/start", {"dir": str(d), "policy": policy})
            assert e.value.code == 400 and why in json.loads(e.value.read())["error"]
        assert not (d / ".den" / "build.log").exists()
    finally:
        srv.shutdown()
        srv.server_close()
        for p, _ in srv.app.procs.values():
            if p.poll() is None:
                p.kill()


def test_cli_refuses_the_fake_harness_and_a_policy_that_is_not_v5(tmp_path):
    d = new_task(tmp_path, "textstats")
    env = {**os.environ, "REPO_FACTORY_WORK_ROOT": str(tmp_path / "work")}
    old = tmp_path / "old-policy"
    old.mkdir()
    (old / "rules.yaml").write_text("pipeline: v3\n")
    for args, msg in (
        (
            ["--policy", str(REPO / "policies" / "v5"), "--harness", "fake"],
            "a build has no fake harness",
        ),
        (["--policy", str(old)], "is not a v5 policy"),
    ):
        p = subprocess.run(
            [sys.executable, "-m", "repo_factory.cli", "build", str(d), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert p.returncode != 0 and msg in p.stderr
    assert not (d / ".den" / "events.jsonl").exists()


# -- the v5 view of a finished build ------------------------------------------------------------


def build_settings_case(root):
    """Track D's T15 case (Addendum A): two settings, one complex item, no defect."""
    d = new_task(root, "holds")
    sol = root.parent / "sol-holds" / "holds"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(settings_case.HOLDS)
    h = settings_case.Scripted()
    h.fake.solution = sol.parent
    pipeline.build(
        d,
        den=d / ".den",
        out=d / "out",
        harness=h,
        llm=settings_case.llm(),
        policy=REPO / "policies" / "v5",
        owner="queue",
    )
    return d


def build_defect_case(root):
    """Track D's whole offline build: one confirmed defect, fixed by the repair."""
    d = new_task(root, "textstats")
    sol = root.parent / "sol-text" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(defect_case.BUGGY)
    h = defect_case.Scripted()
    h.fake.solution = sol.parent
    pipeline.build(
        d,
        den=d / ".den",
        out=d / "out",
        harness=h,
        llm=defect_case.llm(),
        policy=REPO / "policies" / "v5",
        owner="queue",
    )
    return d


@pytest.fixture(scope="module")
def v5root(tmp_path_factory):
    root = tmp_path_factory.mktemp("v5ui") / "root"
    root.mkdir()
    return root, build_settings_case(root), build_defect_case(root)


def test_the_fold_then_the_api_render_a_v5_build(v5root):
    root, holds, text = v5root
    srv, base = serve(root, "claude-code")  # nothing is started here
    try:
        r, _ = get(base, "/api/events", dir=str(holds))
        s = r["state"]
        # a v5 build from the page waits on nobody: owner "queue" and interpret: auto
        assert s["state"] == "delivered" and not s["build"]["questions"]
        assert not [e for e in r["events"] if e["type"] == "question"]
        v = s["v5"]
        assert [x["stage"] for x in v["stages"]][:9] == [
            "scope",
            "build",
            "claims",
            "verify",
            "oracle",
            "repair",
            "reverify",
            "readiness",
            "deliver",
        ]
        assert all(x["status"] in ("done", "skipped") for x in v["stages"])
        assert v["stages"][3]["usd"] == pytest.approx(0.5)  # the verifier session
        # the fixture's spec names functions and a file: a spec, passed through, no call;
        # its two sentences are its definition of done (EV7), which no scenario walked
        assert v["scope"]["decision"] == "pass" and v["scope"]["verdict"] == "spec"
        assert v["stages"][0]["usd"] in (None, 0, 0.0)
        assert v["completeness"] == {"done": 0, "total": 2}
        assert v["label"] is None  # not running
        sets = {x["key"]: x for x in v["settings"]}
        assert set(sets) == {"hold.window_after_idle", "renewal.base"}
        w, rb = sets["hold.window_after_idle"], sets["renewal.base"]
        assert (w["values"], w["default"], w["scope"], w["status"]) == (
            ["chained", "fresh"],
            "fresh",
            "global",
            "added",
        )
        assert w["tested"] == {"chained": "pass", "fresh": "pass"}
        assert (rb["values"], rb["default"], rb["status"]) == (
            ["today", "due_date"],
            "due_date",
            "added",
        )
        assert v["counts"]["settings"] == 2 and v["counts"]["confirmed"] == 0
        assert v["counts"]["complex"] >= 1
        assert [c["item"] for c in v["complex"]] and v["defects"] == []
        assert v["rounds"][0]["accepted"] and v["rounds"][0]["status"] == "done"
        assert "prediction" not in v  # no p_hidden (AU1 #5): the counts stay
        assert v["claims"]["n"] and v["counts"]["blocked"] is not None
        k = v["cost"]
        # the scripted build stage costs under a cent: no vanilla multiple from it
        assert k["total"] > 0 and k["vanilla"] is None and k["multiple"] is None
        assert set(k["by_stage"]) <= set(STAGES)
        assert v["report"] and v["assumptions"] and v["branch"] == "repo-factory/holds"
        # the delivered files, as the page's links fetch them
        rep, ctype = get(base, "/api/text", dir=str(holds), what="report")
        assert ctype.startswith("text/plain") and "## What was checked" in rep
        asm, ctype = get(base, "/api/text", dir=str(holds), what="assumptions")
        assert ctype.startswith("text/plain") and "`renewal.base`" in asm
        # the defect case: a confirmed defect, its claim in prose, observed vs expected
        s2 = get(base, "/api/events", dir=str(text))[0]["state"]
        [f] = s2["v5"]["defects"]
        assert f["id"] == "c2" and f["status"] == "fixed" and f["claim"] == "c2"
        assert "top 1" in f["text"] and (f["observed"], f["expected"]) == ("wrong", "right")
        c = s2["v5"]["counts"]
        assert (c["confirmed"], c["retired"], c["blocked"]) == (1, 1, 1)
        # the builds list
        rows = {Path(b["dir"]).name: b for b in get(base, "/api/builds")[0]}
        assert rows["holds"]["state"] == "delivered" and rows["holds"]["open_questions"] == 0
    finally:
        srv.shutdown()
        srv.server_close()
    # the JSON is the fold's own numbers: the API adds a view, it changes nothing
    st = fold(read_events(holds / ".den" / "events.jsonl"))
    assert st["v5"]["claims"] == v["claims"]


def test_v5_text_is_never_html(v5root, tmp_path):
    """Everything from a build reaches the page as JSON strings: a hostile setting key
    arrives verbatim (the page has no HTML sink: test_unified_ui)."""
    root, holds, _ = v5root
    evil = '<img src=x onerror="alert(1)">'
    from repo_factory.ui.v5view import view

    st = fold(read_events(holds / ".den" / "events.jsonl"))
    st["v5"]["interpretations"].append(
        {"kind": "binary", "setting": evil, "chosen": evil, "item": "s9", "status": "proposed"}
    )
    v = view(holds, st)
    assert evil in [x["key"] for x in v["settings"]]


# -- running: the phase pill and the activity line ----------------------------------------------


def test_running_v5_build_shows_stage_labels(tmp_path):
    root = tmp_path / "root"
    d = root / "busy"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("# busy\n")
    log = EventLog(d / ".den" / "events.jsonl", "busy", 1)
    log.emit("build_started", pid=os.getpid(), pipeline="v5", policy="v5.0")
    for s in ("build", "claims"):
        log.emit("v5_stage", stage=s, status="started")
        log.emit("v5_stage", stage=s, status="done", usd=0.4, wall_s=10)
    log.emit("v5_stage", stage="verify", status="started")
    for i in range(23):
        log.emit("rule_status", id=f"r{i}", to="active", visibility="visible", scenario=f"c{i}")
    app = server.App(root, "claude-code")
    s = app.state(d)
    assert s["state"] == "running" and s["v5"]["label"] == "Verifying: 23 scenarios"
    assert s["build"]["activity"]["stage"] == "Verifying: 23 scenarios"
    assert s["build"]["activity"]["phase"] == "verifying"
    strip = {x["stage"]: x for x in s["v5"]["stages"]}
    assert strip["verify"]["status"] == "running" and strip["oracle"]["status"] == "pending"
    assert strip["build"]["usd"] == 0.4
    # cost against the vanilla reference: stage 1 is the vanilla build
    k = s["v5"]["cost"]
    assert (k["vanilla"], k["total"], k["multiple"], k["limit"]) == (0.4, 0.8, 2.0, 3.0)
    log.emit("v5_stage", stage="verify", status="done", usd=1.0, wall_s=60)
    log.emit("v5_stage", stage="oracle", status="started")
    log.emit("v5_stage", stage="oracle", status="done", usd=0.1, wall_s=5)
    for k, n in (("a.b", "s1"), ("c.d", "s2")):
        log.emit(
            "interpretation",
            item=n,
            mode="setting",
            chosen="x",
            kind="binary",
            setting=k,
            status="proposed",
            forked=False,
        )
    log.emit("v5_stage", stage="repair", status="started")
    log.emit("repair_round", round=1, status="repairing", sent=6)
    s = app.state(d)
    assert s["v5"]["label"] == "Repairing: 4 defects, 2 settings"
    assert s["build"]["activity"]["phase"] == "repairing"
    [row] = app.builds()
    assert row["state"] == "running" and row["phase"] == "repairing"
    # a stage's spend shows live from the ledger, before its closing event
    (d / ".den" / "ledger.jsonl").write_text(
        json.dumps({"build": 1, "stage": "repair", "usd": 0.25, "caller": "worker"}) + "\n"
    )
    strip = {x["stage"]: x for x in app.state(d)["v5"]["stages"]}
    assert strip["repair"]["usd"] == pytest.approx(0.25)


# -- v3 and v4 views are unchanged --------------------------------------------------------------


def test_v3_and_v4_views_have_no_v5_part(tmp_path):
    root = tmp_path / "root"
    d = root / "run10"
    (d / ".den").mkdir(parents=True)
    (d / "spec.md").write_text("# run10\n")
    evs = read_events(V4_LOG)
    started = {**evs[0], "type": "build_started", "ts": evs[0]["ts"] - 1, "pid": None}
    (d / ".den" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [started, *evs]))
    app = server.App(root, "claude-code")
    s = app.state(d)
    assert "v5" not in s and s["build"]["v5"] is None
    st = fold(read_events(d / ".den" / "events.jsonl"))
    assert s["build"]["activity"]["stage"] == st["activity"]["stage"]
    assert app.text(d, "assumptions") == "(no ASSUMPTIONS.md yet)"


def test_stages_a_finished_build_never_ran_say_so():
    from repo_factory.ui.v5view import stages

    why = "reserve rule: $2.03 spent + $2.50 for repair round 1 > cap $4.50"
    v = {
        "stages": {"build": {"stage": "build", "status": "done", "usd": 0.33}},
        "rounds": [{"round": 1, "status": "skipped", "why": why}],
    }
    done = {x["stage"]: x for x in stages(v, False, {})}
    assert done["repair"]["status"] == "not_run" and done["repair"]["why"] == why
    assert done["claims"]["status"] == "not_run" and done["claims"]["why"] is None
    live = {x["stage"]: x for x in stages(v, True, {})}
    assert live["repair"]["status"] == "pending"


def test_an_unreadable_delivery_folder_counts_as_no_repository(tmp_path, monkeypatch):
    from repo_factory.ui.v5view import is_repo

    (tmp_path / ".git").mkdir()
    assert is_repo(tmp_path)

    def denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_dir", denied)
    assert not is_repo(tmp_path)  # e.g. a hidden /home on a CI runner
