"""No request the page, the command line or an agent's token serves waits on a model call. A model-backed step is done before its button is shown (the pre-push checks:
``loop/precheck.py``) or runs in the background with a state the page shows.

Here a fake ``claude`` that records each call and then blocks for two minutes is first on
PATH, so a request that reached a model (in this process or in the ``repo-factory loop`` verb it runs)
would hang past the bound, and the record would show it. Repo Recall's case (2026-10-11): the
owner pressed Publish anyway, the request ran the honesty model for 64 s, then the push held
again on checks the dialog never showed, and the log said "published over a hold"."""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from repo_factory.ui import api

from .app_common import call
from .loop_common import FAKE_BUILD, FakeModel, make_loop, pitch_json

BOUND_S = 30  # a `repo-factory loop` verb starts a Python process; a model call blocks for 120 s
RED_TESTS = {
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


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_DIR", str(tmp_path / "gh"))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    called = tmp_path / "model-called"
    shim = bin_dir / "claude"
    shim.write_text(f"#!/bin/sh\necho \"$*\" >> '{called}'\nsleep 120\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-config"))
    lp = make_loop(
        tmp_path,
        model=FakeModel([pitch_json(product="Clinicbook"), pitch_json(product="Dentbook")]),
        publish={"enabled": True},
    )
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    ids = {}
    for name, text, fake in (
        ("held", "small clinics still schedule patients on paper", RED_TESTS),
        ("ready", "dentists lose track of recall appointments", {}),
    ):
        idea, _ = lp.add(text)
        out = lp.pitch([idea["id"]])[0]
        task = Path(out["task_dir"])
        (task / "fake.json").write_text(json.dumps(fake))
        r = subprocess.run(
            [sys.executable, str(FAKE_BUILD), str(task)], capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr
        assert lp.gate(idea["id"])["state"] == name
        ids[name] = (idea["id"], task.name)
    return lp, api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home), ids, called


def timed(app, method, path, body=None, query=None):
    t0 = time.time()
    code, r = call(app, method, path, query, body)
    return code, r, time.time() - t0


def anyway(key, origin="ui", **fields):
    return {
        "kind": "publish_anyway",
        "subject": {"kind": "build", "id": key},
        "origin": origin,
        "confirm": True,
        "fields": {"reason": "the four failures are flaky clock tests", **fields},
    }


def test_no_press_waits_on_a_model(world):
    lp, app, ids, called = world
    (held, hkey), (ready, rkey) = ids["held"], ids["ready"]
    # before the pre-push checks ran: every door refuses at once, and says why
    for path, body, code_ in (
        ("/api/act", anyway(hkey), 409),
        ("/api/act", anyway(hkey, origin="cli"), 409),
        (
            "/api/loop/publish-anyway",
            {"id": held, "reason": "the four failures are flaky", "yes": True},
            400,
        ),
        ("/api/loop/approve", {"id": ready}, 400),
    ):
        code, r, s = timed(app, "POST", path, body)
        assert code == code_ and "pre-push checks" in r["error"], (path, r)
        assert s < BOUND_S, (path, s)
    assert not called.exists(), called.read_text()
    assert lp.ledger.get(held)["state"] == "held" and lp.ledger.get(ready)["state"] == "ready"
    # the page shows the dialog with the gate's hold, waiting for the checks
    code, r = call(app, "GET", "/api/actions", {"kind": "build", "id": hkey})
    pa = next(a for a in r["actions"] if a["kind"] == "publish_anyway")
    assert not pa["enabled"] and pa["plan"]["checking"] and not pa["plan"]["ok"]
    # the background step: the loop's tick runs them (in this process, with a fake model); the
    # ready build waits for the owner's Publish, which then runs no model, in the bound
    lp.tick()
    assert lp.ledger.get(ready)["state"] == "ready"
    code, r, s = timed(
        app,
        "POST",
        "/api/act",
        {
            "kind": "publish",
            "subject": {"kind": "build", "id": rkey},
            "origin": "ui",
            "confirm": True,
            "fields": {},
        },
    )
    assert code == 200 and s < BOUND_S and r["outcome"] == "published", r
    assert lp.ledger.get(ready)["state"] == "published"
    assert lp.ledger.get(held)["publish"]["precheck"]["state"] == "done"
    # now the dialog lists every hold and the press is deterministic: no model, in the bound
    code, r = call(app, "GET", "/api/actions", {"kind": "build", "id": hkey})
    pa = next(a for a in r["actions"] if a["kind"] == "publish_anyway")
    assert pa["enabled"] and pa["plan"]["ok"]
    assert [(x["check"], x["may"]) for x in pa["plan"]["holds"]] == [("tests", True)]
    code, r, s = timed(app, "POST", "/api/act", anyway(hkey, holds=["tests"]))
    assert code == 200 and s < BOUND_S, r
    assert r["outcome"] == "published" and "https://github.com/owner/clinicbook" in r["say"]
    assert not called.exists(), called.read_text()
    # the action log tells the truth: published, with the repo
    log = [json.loads(x) for x in (lp.cfg.home / "actions.jsonl").read_text().splitlines()]
    assert log[-1]["what"].startswith(
        "published privately over a hold: https://github.com/owner/clinicbook"
    )
    assert log[-1]["url"] == "https://github.com/owner/clinicbook"


def test_the_tick_never_waits_on_the_checks(world, monkeypatch):
    """The loop lane is no request, but it must tick within seconds too: repo-factory update's health
    check waits 60 s for it (v0.3.4 rolled back on 2026-10-11 because the first tick after the
    restart ran two builds' honesty checks inline). Live, the tick starts ``repo-factory loop precheck``
    in a process of its own, one at a time; a ready build then waits for the owner's press."""
    from repo_factory.loop import precheck

    lp, app, ids, called = world
    (held, _), (ready, _) = ids["held"], ids["ready"]
    monkeypatch.setenv(precheck.INLINE_ENV, "0")

    def no_model(prompt):
        raise AssertionError("the tick called the honesty model")

    lp._honesty = no_model
    started = []
    monkeypatch.setattr(precheck, "spawn", lambda cfg, iid: started.append(iid) or os.getpid())
    t0 = time.time()
    out = lp.tick()
    assert time.time() - t0 < 10
    assert out["prechecks"] == ["started", "queued"] and started == [ready]
    assert lp.ledger.get(ready)["state"] == "ready"  # not published yet
    lp.tick()
    assert started == [ready]  # one at a time: still running
    # the background process ran them (here in-process, with a fake model): the next tick
    # starts the held one's; the ready build waits for the owner's press, which runs no model
    lp._honesty = FakeModel(json.dumps({"ok": True, "issues": []}))
    lp._spawned.clear()
    lp.precheck(ready)
    lp._honesty = no_model
    out = lp.tick()
    assert "published" not in out and started == [ready, held]
    assert lp.ledger.get(ready)["state"] == "ready"
    assert lp.publish(ready)["state"] == "published"
    # a ready build whose checks keep failing on its tree is held, with why
    idea, _ = lp.add("vets lose track of vaccination reminders")
    lp.ledger.update(
        idea["id"],
        state="ready",
        gate=lp.ledger.get(held)["gate"],
        task_dir=lp.ledger.get(held)["task_dir"],
        pitch=lp.ledger.get(held)["pitch"],
    )
    i = lp.ledger.get(idea["id"])
    lp._set_precheck(
        i["id"],
        {
            "key": precheck.tree_key(i, lp.cfg),
            "state": "failed",
            "attempts": precheck.MAX_ATTEMPTS,
            "finished": time.time(),
            "error": "gh: not logged in",
        },
    )
    lp._spawned.clear()
    lp.tick()
    i = lp.ledger.get(idea["id"])
    assert (
        i["state"] == "held" and "pre-push checks failed 3 times: gh: not logged in" in i["reason"]
    )
    assert not called.exists()


def test_the_checks_cover_one_commit_even_when_the_branch_moves_meanwhile(world):
    """Seen on a copy of the live ledger: a revise round committed to the delivered branch while
    the checks ran. The record covers the commit it staged; the newer one makes it stale."""
    from repo_factory.loop import precheck

    lp, app, ids, called = world
    held, _ = ids["held"]
    g = lp.ledger.get(held)["gate"]
    out, branch = Path(g["out"]), g["branch"]
    before = precheck.tree_sha(lp.ledger.get(held))

    def commit_meanwhile(prompt):
        if (out / "LATER.md").exists():  # the review: the commit is already made
            return json.dumps({"ok": True, "issues": []}), 0.0
        subprocess.run(["git", "checkout", "-q", branch], cwd=out, check=True)
        (out / "LATER.md").write_text("written while the checks ran\n")
        subprocess.run(["git", "add", "LATER.md"], cwd=out, check=True)
        subprocess.run(["git", "commit", "-qm", "later"], cwd=out, check=True)
        return json.dumps({"ok": True, "issues": []}), 0.0

    lp._honesty = commit_meanwhile
    rec = lp.precheck(held)
    assert rec["state"] == "done"
    assert rec["key"] == precheck.tree_key(lp.ledger.get(held), lp.cfg, before)
    assert not (Path(rec["stage"]) / "LATER.md").exists()  # it staged `before`
    assert precheck.status(lp.ledger.get(held), lp.cfg)["state"] == "stale"


def test_a_press_over_a_stale_tree_waits_and_one_that_holds_again_says_so(world):
    lp, app, ids, called = world
    held, hkey = ids["held"]
    lp.precheck(held)
    # the tree changed since the checks ran (a commit on the delivered branch): checking again
    out = Path(lp.ledger.get(held)["gate"]["out"])
    branch = lp.ledger.get(held)["gate"]["branch"]
    subprocess.run(["git", "checkout", "-q", branch], cwd=out, check=True)
    (out / "README.md").write_text("# Clinicbook\n\nBuilt by brb, see REPORT.md\n")
    subprocess.run(["git", "commit", "-qam", "words"], cwd=out, check=True)
    code, r, s = timed(app, "POST", "/api/act", anyway(hkey, holds=["tests"]))
    assert code == 409 and "pre-push checks" in r["error"] and s < BOUND_S
    lp.precheck(held)  # the background run on the new tree
    code, r = call(app, "GET", "/api/actions", {"kind": "build", "id": hkey})
    pa = next(a for a in r["actions"] if a["kind"] == "publish_anyway")
    assert [x["check"] for x in pa["plan"]["holds"]] == ["tests"]  # wording never holds
    # pressing with a list that is no longer the plan is refused: the holds changed
    code, r, s = timed(app, "POST", "/api/act", anyway(hkey, holds=["tests", "jargon"]))
    assert code == 409 and "holds changed" in r["error"]
    # a secret appearing in the staged tree after the checks: the press scans again, holds
    # again, and the log, the history and the flash say so
    stage = Path(lp.ledger.get(held)["publish"]["precheck"]["stage"])
    (stage / "config.py").write_text('TOKEN = "ghp_' + "Ab1" * 14 + '"\n')
    code, r, s = timed(app, "POST", "/api/act", anyway(hkey, holds=["tests"]))
    assert code == 200 and r["outcome"] == "held" and s < BOUND_S, r
    assert r["say"].startswith("Not published: held again by secrets scan")
    log = [json.loads(x) for x in (lp.cfg.home / "actions.jsonl").read_text().splitlines()]
    assert log[-1]["what"].startswith("publish anyway held again: secrets scan")
    notes = [h["note"] for h in lp.ledger.history(held)]
    assert not any(n.startswith("published") for n in notes)
    assert lp.ledger.get(held)["state"] == "held" and not called.exists()
    # nor does the page: no "Published over a hold by you" on a build that was not published
    pub = lp.ledger.get(held)["publish"]
    assert "override" not in pub and pub["anyway_tried"]["outcome"] == "held"
    code, s = call(app, "GET", "/api/state")
    assert next(b for b in s["builds"] if b.get("idea") == held)["override"] is None
    # a record written before 0.3.4 kept the override on a hold: the row drops it too
    lp.ledger.update(held, publish={**pub, "override": pub["anyway_tried"]})
    code, s = call(app, "GET", "/api/state")
    assert next(b for b in s["builds"] if b.get("idea") == held)["override"] is None


def test_the_claims_presses_never_wait_on_a_model(world, monkeypatch):
    """NG-PLAN §7: Save release text answers at once from every door and needs no model (the
    owner's text is used as it is). Accept and Edit interpretation belong to build start: on a
    built build they are refused at once. Check the wording again is gone (2026-10-16)."""
    from repo_factory.loop import precheck

    lp, app, ids, called = world
    held, hkey = ids["held"]
    monkeypatch.setenv(precheck.SPAWN_ENV, "0")  # the background run: not here
    p = lp.ledger.get(held)["pitch"]
    lp.ledger.update(
        held,
        pitch={
            **p,
            "claims": [
                {
                    "id": "k1",
                    "claim": "books every patient instantly",
                    "interpreted": "books a patient in one step",
                    "setting": None,
                    "spirit": {"keeps": True, "why": "w"},
                    "source": "pitch",
                    "status": "standing",
                }
            ],
        },
    )
    for kind, fields, origin, ok in (
        (
            "edit_release_text",
            {"text": "Appointment booking for small clinics, on your own machine."},
            "ui",
            True,
        ),
        ("accept_claim", {"claim": "k1"}, "ui", False),
        ("edit_claim", {"claim": "k1", "text": "books a patient in a few steps"}, "cli", False),
        ("check_text", {}, "cli", False),
    ):
        body = {
            "kind": kind,
            "subject": {"kind": "build", "id": hkey},
            "origin": origin,
            "fields": fields,
            "confirm": kind == "accept_claim",
        }
        code, r, s = timed(app, "POST", "/api/act", body)
        assert (code == 200) == ok and s < BOUND_S, (kind, code, r)
    assert not called.exists(), called.read_text()
    i = lp.ledger.get(held)
    assert i["pitch"]["release"]["text"].startswith("Appointment booking for small clinics")
    assert i["pitch"]["claims"][0]["status"] == "standing"
    # the page's row carries the Claims section
    code, s = call(app, "GET", "/api/state")
    row = next(b for b in s["builds"] if b.get("idea") == held)
    assert row["claims"]["claims"][0]["interpreted"] == "books a patient in one step"
    assert row["claims"]["release"]["by"] == "owner"
