"""One build's controls (RC): Stop round (Pause build went into it, AU1 #9), Resume build,
Cancel build and Restart with new instructions, on the loop (``Loop``), the registry and api/act.

The builds are the fake ``repo-factory build``/``repo-factory revise`` (``fixtures/loop/fake_build.py``), each
with a worker in a session of its own (as a Claude session is), so a kill has to find the
whole build and nothing else. No press waits: the kill is SIGTERM now and SIGKILL after
``build.kill_grace_s`` by a detached reaper; the loop's watch makes the change once the build
is gone."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from repo_factory import registry as R
from repo_factory.errors import LoopError
from repo_factory.loop import builds
from repo_factory.loop.daemon import Loop
from repo_factory.settings.store import Config
from repo_factory.ui import api

from .loop_common import FAKE_BUILD, FakeModel, make_loop, pitch_json, wait

RED = {
    "readiness": {
        "final": {
            "items": [
                {
                    "id": "tests_green",
                    "status": "red",
                    "why": "4 of 58 of the product's own tests fail",
                }
            ]
        }
    }
}
ASK = "Fix the 4 failing own tests (see the gate); keep the tests' expectations."
LONG = {"sleep": 60, "worker": True}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_DIR", str(tmp_path / "gh"))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    yield
    for p in tmp_path.rglob("worker.pid"):  # never leave a fake session behind
        try:
            os.kill(int(p.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def alive(pid):
    st = builds._stat(int(pid))
    return st is not None and st[3] != "Z"


def held(tmp_path, text="small clinics still schedule patients on paper", lp=None, **over):
    """A loop idea whose round 1 delivered and was held by the gate (red tests)."""
    lp = lp or make_loop(tmp_path, concurrency=4, build={"kill_grace_s": 2}, **over)
    idea, _ = lp.add(text)
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    (task / "fake.json").write_text(json.dumps(RED))
    r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return lp, lp.gate(idea["id"])["id"], task


def running(lp, iid, task, fake):
    """Retry (a new round) with ``fake`` steering it, started now; waits for its worker."""
    (task / "fake.json").write_text(json.dumps({**RED, **fake}))
    (task / "worker.pid").unlink(missing_ok=True)
    started = [i["id"] for i in lp.start_builds()]
    assert iid in started
    rec = lp.ledger.get(iid)["build"]
    if fake.get("worker") or (fake.get("revise") or {}).get("worker"):
        wait(lambda: (task / "worker.pid").exists())
    return rec["pid"], (
        int((task / "worker.pid").read_text()) if (task / "worker.pid").exists() else None
    )


def pressed(out, what, done):
    """A press returns at once: still building with the kill under way, or (when the build
    went at the SIGTERM) already ``done``."""
    assert out["state"] in ("building", done), out["state"]
    if out["state"] == "building":
        assert out["build"]["kill"]["what"] == what


def settle(lp, iid, timeout=30):
    """The loop's watch until the idea leaves building (what the daemon's tick does)."""
    wait(lambda: lp.ledger.get(iid)["state"] != "building", timeout=timeout, tick=lp.watch)
    return lp.ledger.get(iid)


def test_the_round_newest_at_the_spawn_is_never_the_new_builds():
    """A round that began a moment before a new build's spawn (a fast fake round, then an
    immediate retry) is not that build's, even inside the 5 s clock slack: under load the
    watch once took round 1 for the retry's round and attributed it there."""
    now = time.time()
    rec = {"started": now, "prior_started": now - 1}
    old = {"build": 1, "started": now - 1, "status": "ok"}
    new = {"build": 2, "started": now + 0.5, "status": "running"}
    assert not builds.is_fresh(old, rec) and builds.is_fresh(new, rec)
    assert not builds.is_fresh(None, rec)
    assert builds.is_fresh(old, {"started": now})  # a record from before: as it was
    assert builds.is_fresh(old, {**rec, "round": 1})  # a resumed round is its own


def test_a_slow_starting_round_is_not_taken_for_the_round_before(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    (ta / "fake.json").write_text(json.dumps({**RED, "start_delay": 1.5}))
    [s] = lp.start_builds()
    lp.watch()  # alive, and its round has not begun: no round yet
    assert lp.ledger.get(a)["build"].get("round") is None
    out = settle(lp, a)
    assert out["build"]["round"] == 2 and "1" not in out["build"].get("rounds_by", {})


def test_stop_round_kills_only_its_build_and_puts_the_idea_back(tmp_path):
    lp, a, ta = held(tmp_path)
    lp2_idea, _ = lp.add("a second idea about dentists booking chairs")
    b_task = Path(lp.pitch([lp2_idea["id"]])[0]["task_dir"])
    b = lp2_idea["id"]
    before = lp.ledger.get(a)
    lp.retry(a)
    (b_task / "fake.json").write_text(json.dumps(LONG))
    (ta / "fake.json").write_text(json.dumps({**RED, **LONG}))
    started = {i["id"] for i in lp.start_builds()}
    assert started == {a, b}
    wait(lambda: (ta / "worker.pid").exists() and (b_task / "worker.pid").exists())
    pa, wa = lp.ledger.get(a)["build"]["pid"], int((ta / "worker.pid").read_text())
    pb, wb = lp.ledger.get(b)["build"]["pid"], int((b_task / "worker.pid").read_text())
    assert os.getpgid(wa) != os.getpgid(pa)  # the worker leads a group of its own

    t0 = time.time()
    out = lp.stop_round(a, origin="ui")
    assert time.time() - t0 < 3  # never waits for the build to go
    pressed(out, "stop", "held")
    wait(lambda: not alive(pa) and not alive(wa))
    assert alive(pb) and alive(wb)  # the other build is untouched
    out = settle(lp, a)
    assert out["state"] == "held"
    for k in ("reason", "gate", "publish"):  # as it was before the round
        assert out[k] == before[k], k
    assert out["build"]["stopped"]["round"] == 2 and out["build"]["round"] == 1
    assert (ta / ".den" / "v5" / "r2" / builds.STOPPED).exists()
    assert not builds.resumable(ta, 2) and builds.resumable(ta, 2, stopped_ok=True)
    h = lp.ledger.history(a)[0]
    assert "round 2 stopped by the owner (ui)" in h["note"] and h["data"]["kind"] == "stop_round"
    # Retry, Ask for changes and Publish anyway are there again; Retry does not resume it
    assert lp.ledger.get(b)["state"] == "building" and alive(pb)
    q = lp.retry(a)
    assert q["build"]["resume_round"] is None and "fresh build" in lp.ledger.history(a)[0]["note"]
    lp.close(b, "done with this test")
    wait(lambda: not alive(pb) and not alive(wb))


def test_a_build_that_ignores_sigterm_is_killed_after_the_grace(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    pa, wa = running(lp, a, ta, {**LONG, "ignore_term": True})
    lp.stop_round(a)
    time.sleep(0.5)
    assert alive(pa)  # SIGTERM ignored: still there
    assert settle(lp, a, timeout=20)["state"] == "held"  # the reaper's SIGKILL, then watch
    assert not alive(pa) and not alive(wa)


def paused_as_before(lp, a, ta):
    """An idea paused before Pause build went into Stop round (AU1 #9), as an older ledger
    holds it: ``paused``, its round 2 kept (no stopped.json), resumable."""
    lp.retry(a)
    running(lp, a, ta, LONG)
    rec = dict(lp.ledger.get(a)["build"])
    lp.stop_round(a)
    settle(lp, a)
    (ta / ".den" / "v5" / "r2" / builds.STOPPED).unlink()
    rec.update(
        pid=None,
        stopping=False,
        status="paused",
        resume_round=2,
        round=2,
        paused={"at": time.time(), "origin": "ui", "round": 2},
    )
    return lp.ledger.update(a, state="paused", build=rec, note="paused by the owner (ui)")


def test_pause_build_is_gone_stop_round_is_the_one_verb(tmp_path):
    lp, a, ta = held(tmp_path)
    assert not hasattr(lp, "pause_build") and "pause_build" not in R.ACTIONS
    lp.retry(a)
    running(lp, a, ta, LONG)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    with pytest.raises(api.HttpError) as e:  # no such verb
        app.loop_act("pause-build", {"id": a})
    assert e.value.code == 404
    assert lp.ledger.get(a)["state"] == "building"  # nothing was stopped
    lp.stop_round(a)
    assert settle(lp, a)["state"] == "held"


def test_a_paused_idea_from_an_older_ledger_is_shown_resumed_and_never_restarted(tmp_path):
    lp, a, ta = held(tmp_path)
    paused_as_before(lp, a, ta)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    s = R.subject_of(app, "idea", a)
    assert s.row["status"] == "paused"
    assert R.check("resume_build", s) is None and R.check("stop_round", s) is None
    for _ in range(3):  # ticks: never started again
        lp.tick()
    # repo-factory.service restarts: a new loop over the same home keeps it paused
    lp2 = Loop(
        Config(lp.cfg.home, overrides=lp.cfg.data),
        model=FakeModel(pitch_json()),
        summary_model=FakeModel("x"),
        honesty_model=FakeModel("{}"),
    )
    lp2.tick()
    lp2.watch()
    assert lp2.ledger.get(a)["state"] == "paused" and lp2.start_builds() == []
    # Resume: queued, then it continues round 2
    q = lp2.resume_build(a, origin="ui")
    assert q["state"] == "queued" and q["build"]["resume_round"] == 2
    (ta / "fake.json").write_text(json.dumps(RED))
    runs = len((ta / "fake_runs.jsonl").read_text().splitlines())
    [s] = lp2.start_builds()
    assert s["id"] == a and s["state"] == "building"
    wait(lambda: len((ta / "fake_runs.jsonl").read_text().splitlines()) > runs)
    argv = json.loads((ta / "fake_runs.jsonl").read_text().splitlines()[-1])["argv"]
    assert argv[argv.index("--resume") + 1] == "2"


def test_stop_round_on_an_older_paused_idea_puts_it_back(tmp_path):
    lp, a, ta = held(tmp_path)
    before = lp.ledger.get(a)
    paused_as_before(lp, a, ta)
    out = lp.stop_round(a, origin="ui")  # nothing runs: at once
    assert out["state"] == "held" and out["reason"] == before["reason"]
    assert out["build"]["stopped"]["round"] == 2 and out["build"]["stopped"]["resumable"]
    assert lp.resume_build(a)["build"]["resume_round"] == 2


def test_an_older_pause_under_way_finishes_as_a_stop(tmp_path):
    """A ``pause`` kill recorded before the upgrade, still under way: the watch makes it a
    stop (back as before the round; the round kept for Resume)."""
    lp, a, ta = held(tmp_path)
    before = lp.ledger.get(a)
    lp.retry(a)
    pa, wa = running(lp, a, ta, LONG)
    rec = dict(lp.ledger.get(a)["build"])
    rec["kill"] = {"what": "pause", "at": time.time(), "origin": "ui"}
    lp.ledger.update(a, build=rec)
    builds.kill(pa, ta, 2)
    out = settle(lp, a)
    assert out["state"] == "held" and out["reason"] == before["reason"]
    assert out["build"]["stopped"]["round"] == 2 and "kill" not in out["build"]
    assert lp.resume_build(a)["build"]["resume_round"] == 2


def test_resume_a_stopped_round(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    running(lp, a, ta, LONG)
    lp.stop_round(a)
    assert settle(lp, a)["state"] == "held"
    # the page offers Resume build, and Retry and Ask for changes again (the round's log has
    # no end, but the loop says it does not run)
    s = R.subject_of(api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home), "idea", a)
    assert not s.row["live"] and s.row["stopped"]["resumable"]
    for k in ("resume_build", "retry", "revise"):
        assert R.check(k, s) is None, (k, R.check(k, s))
    q = lp.resume_build(a)
    assert q["state"] == "queued" and q["build"]["resume_round"] == 2
    assert not (ta / ".den" / "v5" / "r2" / builds.STOPPED).exists()
    assert "stopped" not in q["build"] and q["build"]["before"]["state"] == "held"


def test_cancel_a_queued_revise_goes_back_as_it_was(tmp_path):
    lp, a, ta = held(tmp_path)
    before = lp.ledger.get(a)
    q = lp.revise(a, ASK, origin="ui")
    assert q["state"] == "queued" and q["build"]["change"]
    out = lp.cancel(a, origin="ui")
    assert out["state"] == "held" and "change" not in out["build"]
    for k in ("reason", "gate", "publish"):
        assert out[k] == before[k], k
    assert (
        "cancelled by the owner (ui) before the revise round started"
        in lp.ledger.history(a)[0]["note"]
    )
    assert lp.start_builds() == []  # nothing runs
    assert lp.revise(a, ASK)["state"] == "queued"  # Ask for changes is there again


def test_cancel_a_new_build_goes_back_to_ideas_and_is_not_pitched_again(tmp_path):
    lp = make_loop(tmp_path)  # intake by rule: every new idea pitched
    idea, _ = lp.add("small clinics still schedule patients on paper")
    q = lp.pitch([idea["id"]])[0]
    task = Path(q["task_dir"])
    assert q["state"] == "queued"
    out = lp.cancel(idea["id"])
    assert out["state"] == "new" and out["task_dir"] is None and not task.exists()
    assert list((Path(lp.cfg.tasks_dir) / ".cancelled").iterdir())[0].name.startswith(task.name)
    pitched = len(lp.model.prompts)
    lp.tick()
    assert lp.ledger.get(idea["id"])["state"] == "new" and len(lp.model.prompts) == pitched
    assert lp.send(idea["id"])["state"] == "new"  # Build this sends it again
    lp.tick()  # pitched and started in one tick
    assert lp.ledger.get(idea["id"])["state"] in ("queued", "building")


def test_restart_a_running_revise_with_new_instructions(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.revise(a, ASK, origin="ui")
    (ta / "fake.json").write_text(json.dumps({**RED, "revise": LONG}))
    [s] = lp.start_builds()
    wait(lambda: (ta / "worker.pid").exists())
    pa, wa = s["build"]["pid"], int((ta / "worker.pid").read_text())
    with pytest.raises(LoopError, match="20 to 4000"):
        lp.restart_revise(a, "too short")
    new = "Only fix test_restart: it must wait for the port to close before starting again."
    out = lp.restart_revise(a, new, mode="code", origin="ui")
    pressed(out, "restart", "queued")
    out = settle(lp, a)
    assert not alive(pa) and not alive(wa)
    assert (
        out["state"] == "queued" and Path(out["build"]["change"]["file"]).read_text().strip() == new
    )
    assert out["build"]["before"]["state"] == "held"
    notes = [h["note"] for h in lp.ledger.history(a)[:3]]
    assert notes[0].startswith("restarted with new instructions (ui): Only fix test_restart")
    assert any("round 2 stopped by the owner (ui)" in n for n in notes)
    assert (ta / ".den" / "v5" / "r2" / builds.STOPPED).exists()


def test_factory_loop_stop_still_stops_every_build(tmp_path):
    lp, a, ta = held(tmp_path)
    other, _ = lp.add("a second idea about dentists booking chairs")
    tb = Path(lp.pitch([other["id"]])[0]["task_dir"])
    (tb / "fake.json").write_text(json.dumps({"sleep": 60}))
    lp.retry(a)
    (ta / "fake.json").write_text(json.dumps({**RED, "sleep": 60}))
    assert len(lp.start_builds()) == 2
    wait(lambda: all(builds.current_round(t) for t in (ta, tb)))
    stopped = lp.stop(grace_s=5)
    assert set(stopped) == {a, other["id"]}
    for i in (a, other["id"]):
        x = lp.ledger.get(i)
        assert x["state"] == "queued" and x["build"]["resume_round"], x["build"]


def test_the_registry_and_api_act(tmp_path):
    lp, a, ta = held(tmp_path)
    lp.retry(a)
    running(lp, a, ta, LONG)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    s = R.subject_of(app, "idea", a)
    assert R.check("stop_round", s) is None
    assert (
        R.check("cancel", s) == "only a queued build is cancelled; Stop round stops a running one"
    )
    with pytest.raises(api.HttpError) as e:  # confirm class: 409 without confirm
        app.act({"kind": "stop_round", "subject": {"kind": "idea", "id": a}, "origin": "cli"})
    assert e.value.code == 409
    with pytest.raises(api.HttpError):  # Pause build is gone (AU1 #9)
        app.act(
            {
                "kind": "pause_build",
                "subject": {"kind": "idea", "id": a},
                "origin": "ui",
                "confirm": True,
            }
        )
    t0 = time.time()
    r = app.act(
        {
            "kind": "stop_round",
            "subject": {"kind": "idea", "id": a},
            "origin": "ui",
            "confirm": True,
        }
    )
    assert time.time() - t0 < 10 and r["outcome"] in ("stopping", "held")
    s = R.subject_of(app, "idea", a)
    if lp.ledger.get(a)["state"] == "building":  # stopping…: the row says so
        assert R.check("stop_round", s) == "it is stopping now"
        assert s.row["reason"].startswith("Stopping…")
    out = settle(lp, a)
    assert out["state"] == "held" and out["build"]["stopped"]["resumable"]
    r = app.act({"kind": "resume_build", "subject": {"kind": "idea", "id": a}, "origin": "ui"})
    assert r["outcome"] == "queued" and lp.ledger.get(a)["state"] == "queued"
    acts = [json.loads(x) for x in (lp.cfg.home / "actions.jsonl").read_text().splitlines()]
    assert [(x["kind"], x["origin"]) for x in acts] == [
        ("stop_round", "ui"),
        ("resume_build", "ui"),
    ]


def press(app, kind, key, **fields):
    """What the page's button sends: ``POST api/act`` on the build's row key."""
    body = {
        "kind": kind,
        "subject": {"kind": "build", "id": key},
        "origin": "ui",
        "confirm": True,
        "fields": fields,
    }
    code, _, data = api.handle("POST", "/api/act", None, json.dumps(body).encode(), app=app)
    return code, json.loads(data)


def test_a_loop_builds_stop_and_ask_for_changes_reach_it_through_api_act(tmp_path):
    """The page's Stop and Ask for changes on a loop build's row (its key, the task folder's
    name) reach the build through ``api/act``; neither falls through to a build-by-hand
    route ("no such build under ROOT")."""
    lp, a, ta = held(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    lp.retry(a)
    running(lp, a, ta, LONG)
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    key = R.subject_of(app, "idea", a).row["key"]
    assert key == ta.name
    code, out = press(app, "stop_round", key)
    assert code == 200 and out["outcome"] in ("stopping", "held"), out
    assert settle(lp, a)["state"] == "held"
    code, out = press(app, "revise", key, instructions=ASK, mode="code")
    assert code == 200, out
    i = lp.ledger.get(a)
    assert i["state"] == "queued" and i["build"]["change"]["origin"] == "ui"
