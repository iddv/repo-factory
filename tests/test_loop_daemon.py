"""The daemon: concurrency, pause and resume, crash-resume, retry then hold, stop. Builds
are ``tests/fixtures/loop/fake_build.py`` (real processes, no model calls)."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from repo_factory.loop import builds

from .loop_common import FakeModel, make_loop, pitch_json, wait


def queue(lp, n, fake=None):
    ids = []
    for k in range(n):
        lp._model = FakeModel(pitch_json(product=f"Product {k}"))
        idea, _ = lp.add(f"idea number {k} about something different {k * 7}")
        out = lp.pitch([idea["id"]])[0]
        assert out["state"] == "queued"
        if fake is not None:
            (Path(out["task_dir"]) / "fake.json").write_text(json.dumps(fake))
        ids.append(idea["id"])
    return ids


def states(lp, ids):
    return [lp.ledger.get(i)["state"] for i in ids]


def test_concurrency_limit_then_gate(tmp_path):
    lp = make_loop(tmp_path, concurrency=2)
    ids = queue(lp, 3, fake={"sleep": 1.5})
    started = lp.start_builds()
    assert len(started) == 2 and states(lp, ids).count("building") == 2
    assert lp.start_builds() == []  # no free slot
    rec = lp.ledger.get(ids[0])["build"]
    assert builds.alive(rec["pid"])
    runs = tmp_path / "tasks" / "product-0" / "fake_runs.jsonl"
    wait(runs.exists)
    argv = " ".join(json.loads(runs.read_text().splitlines()[0])["argv"])
    assert "--owner none --harness claude-code" in argv
    wait(lambda: states(lp, ids)[:2] == ["ready", "ready"], tick=lp.tick, timeout=40)
    # the third started once a slot was free, and the round was recorded while it ran
    wait(lambda: states(lp, ids)[2] == "ready", tick=lp.tick, timeout=40)
    g = lp.ledger.get(ids[0])["gate"]
    assert g["verdict"] == "ready" and all(c["ok"] for c in g["checks"].values())
    assert lp.ledger.get(ids[0])["build"]["round"] == 1
    assert lp.spend()["today"]["builds"] >= 4.5  # 3 x $1.50 from the tasks' ledgers


def test_pause_stops_new_builds_resume_restarts(tmp_path):
    lp = make_loop(tmp_path)
    ids = queue(lp, 1)
    lp.pause()
    out = lp.tick()
    assert out["paused"] and states(lp, ids) == ["queued"]
    lp.add("a new idea while paused")
    lp.tick()
    assert len(lp.ledger.all("new")) == 1  # no pitch while paused either
    lp.resume()
    lp.tick()
    assert states(lp, ids) == ["building"]
    wait(lambda: states(lp, ids) == ["ready"], tick=lp.tick)


def test_crash_resume_continues_the_round(tmp_path):
    lp = make_loop(tmp_path)
    [iid] = queue(lp, 1, fake={"sleep": 60})
    lp.start_builds()
    idea = lp.ledger.get(iid)
    pid = idea["build"]["pid"]
    wait(lambda: lp.watch() is not None and lp.ledger.get(iid)["build"].get("round") == 1)
    os.killpg(pid, signal.SIGKILL)  # a power loss: no build_finished
    wait(lambda: not builds.alive(pid))
    # a fresh daemon (the old one "crashed" too) picks it up
    lp2 = make_loop(tmp_path)
    (tmp_path / "tasks" / "product-0" / "fake.json").write_text(json.dumps({"sleep": 0}))
    lp2.watch()
    idea = lp2.ledger.get(iid)
    assert idea["state"] == "queued" and idea["build"]["resume_round"] == 1
    assert idea["build"]["resumes"] == 1
    lp2.start_builds()
    wait(lambda: lp2.ledger.get(iid)["state"] == "ready", tick=lp2.tick)
    runs = (tmp_path / "tasks" / "product-0" / "fake_runs.jsonl").read_text().splitlines()
    assert "--resume" in json.loads(runs[1])["argv"]
    from repo_factory.events import load_state

    st = load_state(tmp_path / "tasks" / "product-0")
    assert st["status"] == "ok" and st["build"] == 1


def test_a_failed_build_is_retried_once_then_held(tmp_path):
    lp = make_loop(tmp_path)
    [iid] = queue(lp, 1, fake={"outcome": "error"})
    lp.start_builds()
    wait(lambda: lp.ledger.get(iid)["state"] == "held", tick=lp.tick)
    idea = lp.ledger.get(iid)
    assert idea["build"]["failures"] == 2 and "failed 2 time(s)" in idea["reason"]
    runs = (tmp_path / "tasks" / "product-0" / "fake_runs.jsonl").read_text().splitlines()
    assert len(runs) == 2
    assert "--resume" in json.loads(runs[1])["argv"]  # v5 saved its state: resumed
    # repo-factory loop retry: goes again
    (tmp_path / "tasks" / "product-0" / "fake.json").write_text(json.dumps({"outcome": "ok"}))
    out = lp.retry(iid)
    assert out["state"] == "queued"
    wait(lambda: lp.ledger.get(iid)["state"] == "ready", tick=lp.tick)


def test_a_login_failure_is_held_at_once_with_its_reason_and_retry_resumes(tmp_path):
    lp = make_loop(tmp_path)
    [iid] = queue(lp, 1, fake={"outcome": ["auth", "ok"]})
    lp.start_builds()
    wait(lambda: lp.ledger.get(iid)["state"] == "held", tick=lp.tick)
    idea = lp.ledger.get(iid)
    # not retried on its own (it would fail the same way): held, saying what to do
    runs = (tmp_path / "tasks" / "product-0" / "fake_runs.jsonl").read_text().splitlines()
    assert len(runs) == 1
    assert idea["reason"].startswith("build stage: Claude login expired: run `claude` once")
    assert idea["build"]["failed_stage"] == "build"
    assert any(h["reason"] == idea["reason"] for h in lp.status()["held"])  # Needs you
    out = lp.retry(iid)  # after the owner logged in again
    assert out["state"] == "queued" and out["build"]["resume_round"] == 1
    wait(lambda: lp.ledger.get(iid)["state"] == "ready", tick=lp.tick)
    runs = (tmp_path / "tasks" / "product-0" / "fake_runs.jsonl").read_text().splitlines()
    assert "--resume" in json.loads(runs[1])["argv"]


def test_a_rebuild_starts_fresh_and_a_plain_new_round_may_continue(tmp_path):
    cfg = make_loop(tmp_path).cfg
    assert "--fresh" in builds.command(cfg, tmp_path / "t", fresh=True)
    assert "--fresh" not in builds.command(cfg, tmp_path / "t")
    assert "--fresh" not in builds.command(cfg, tmp_path / "t", resume=2, fresh=True)


def test_stop_stops_builds_and_they_resume(tmp_path):
    lp = make_loop(tmp_path)
    [iid] = queue(lp, 1, fake={"sleep": 60})
    lp.start_builds()
    pid = lp.ledger.get(iid)["build"]["pid"]
    wait(lambda: lp.watch() is not None and lp.ledger.get(iid)["build"].get("round") == 1)
    stopped = lp.stop(grace_s=5)
    assert stopped == [iid] and not builds.alive(pid)
    idea = lp.ledger.get(iid)
    assert idea["state"] == "queued" and idea["build"]["resume_round"] == 1
    assert lp.cfg.stop_file.exists()
    assert lp.tick()["paused"]  # a stop also holds new work
    lp.cfg.stop_file.unlink()
    (tmp_path / "tasks" / "product-0" / "fake.json").write_text(json.dumps({"sleep": 0}))
    wait(lambda: lp.ledger.get(iid)["state"] == "ready", tick=lp.tick)


def test_daily_cap_holds_new_builds(tmp_path):
    lp = make_loop(tmp_path, daily_usd=0.01)
    ids = queue(lp, 1)  # the pitch itself spent $0.05
    out = lp.tick()
    assert out.get("over_cap") and states(lp, ids) == ["queued"]


def test_run_until_idle_and_lock(tmp_path):
    lp = make_loop(tmp_path)
    ids = queue(lp, 1)
    t0 = time.time()
    lp.run(until_idle=True)
    assert states(lp, ids) == ["ready"] and time.time() - t0 < 30
    assert not (lp.cfg.home / "daemon.pid").exists()


def test_cli_run_once_and_status(tmp_path):
    lp = make_loop(tmp_path)
    queue(lp, 1)
    import yaml

    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    env = {**os.environ}
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "repo_factory.cli",
            "loop",
            "--home",
            str(lp.cfg.home),
            "run",
            "--once",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "build started" in r.stdout
    r = subprocess.run(
        [sys.executable, "-m", "repo_factory.cli", "loop", "--home", str(lp.cfg.home), "status"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "building 1" in r.stdout or "ready 1" in r.stdout
    wait(lambda: lp.ledger.all("ready"), tick=lp.tick)


def test_build_resumed_folds_as_running_with_the_new_pid():
    from repo_factory.events import fold

    evs = [
        {"v": 1, "ts": 1.0, "task": "t", "build": 1, "type": "build_started", "pid": 1},
        {
            "v": 1,
            "ts": 2.0,
            "task": "t",
            "build": 1,
            "type": "build_finished",
            "status": "error",
            "error": "interrupted (Ctrl-C)",
        },
        {"v": 1, "ts": 3.0, "task": "t", "build": 1, "type": "build_resumed", "pid": 42},
    ]
    st = fold(evs)
    assert st["status"] == "running" and st["pid"] == 42 and st["error"] is None
    assert st["activity"]["phase"] == "building"


def test_factory_build_resume_is_v5_only(tmp_path):
    import pytest

    from repo_factory.cli import main

    task = tmp_path / "t"
    main(["new", "x y z", "--dir", str(task)])
    old = tmp_path / "old-policy"
    old.mkdir()
    (old / "rules.yaml").write_text("pipeline: v3\n")
    with pytest.raises(SystemExit, match="is not a v5 policy"):
        main(["build", str(task), "--resume", "1", "--policy", str(old)])
