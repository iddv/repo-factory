"""Ask for changes on the loop (AR-PLAN.md §2.4): ``Loop.revise`` queues a revise round of a
held or ready build that has no repo, which takes a build slot like any build; ok goes
through the gate, a failure holds it with the gate of the round it revised, a crash resumes
it. And #65: an honesty hold first gets one docs-only repair round. The builds are the fake
``repo-factory build``/``repo-factory revise`` (``fixtures/loop/fake_build.py``); ``gh`` is faked."""

import json
import subprocess
from pathlib import Path

import pytest

from repo_factory.errors import LoopError
from repo_factory.loop import builds

from .loop_common import FAKE_BUILD, FakeModel, make_loop, wait


@pytest.fixture(autouse=True)
def fake_gh(tmp_path, monkeypatch):
    d = tmp_path / "gh"
    monkeypatch.setenv("FAKE_GH_DIR", str(d))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    return d


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


def held(tmp_path, fake=None, **over):
    lp = make_loop(tmp_path, **over)
    idea, _ = lp.add("small clinics still schedule patients on paper")
    task = Path(lp.pitch([idea["id"]])[0]["task_dir"])
    (task / "fake.json").write_text(json.dumps(fake if fake is not None else RED))
    r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = lp.gate(idea["id"])
    return lp, idea["id"], task, out


def finish(lp, iid):
    """Run what start_builds started to its end, then watch it."""
    rec = lp.ledger.get(iid)["build"]
    wait(lambda: not builds.alive(rec["pid"]))
    lp.watch()
    return lp.ledger.get(iid)


def runs(task):
    return [json.loads(x)["argv"] for x in (task / "fake_runs.jsonl").read_text().splitlines()]


def test_the_preconditions(tmp_path):
    lp, iid, task, out = held(tmp_path)
    assert out["state"] == "held"
    with pytest.raises(LoopError, match="20 to 4000"):
        lp.revise(iid, "too short")
    for state, pub, why in (
        ("building", {}, "only a held or ready"),
        ("published", {"url": "https://github.com/o/p"}, "only a held or ready"),
        ("held", {"url": "https://github.com/o/p"}, "Open issue"),
    ):
        lp.ledger.update(iid, state=state, publish=pub)
        with pytest.raises(LoopError, match=why):
            lp.revise(iid, ASK)
    lp.ledger.update(
        iid,
        state="held",
        publish={},
        gate={"verdict": "held", "reasons": ["build stage: Claude login expired"]},
    )
    with pytest.raises(LoopError, match="delivered nothing"):
        lp.revise(iid, ASK)
    lp.ledger.update(iid, gate=out["gate"])
    lp.cfg.data["revise"]["enabled"] = False
    with pytest.raises(LoopError, match="revise is off"):
        lp.revise(iid, ASK)
    lp.cfg.data["revise"]["enabled"] = True
    lp.ledger.update(iid, state="ready")
    assert lp.revise(iid, ASK)["state"] == "queued"


def test_revise_queues_with_its_change_file_and_origin(tmp_path):
    lp, iid, task, _ = held(tmp_path)
    out = lp.revise(iid, ASK, origin="ui")
    assert out["state"] == "queued" and out["gate"]["verdict"] == "held"  # the gate is kept
    ch = out["build"]["change"]
    assert (ch["mode"], ch["origin"]) == ("code", "ui")  # tests hold: code
    assert Path(ch["file"]).read_text().strip() == ASK
    holds = json.loads(Path(ch["file"]).with_suffix(".holds.json").read_text())
    assert holds["failing"] == ["tests"] and "4 of 58" in holds["reasons"][0]
    h = lp.ledger.history(iid)[0]
    assert h["note"].startswith("revise requested (ui): Fix the 4 failing")
    assert h["data"]["origin"] == "ui" and h["data"]["kind"] == "revise"
    assert h["data"]["instructions"] == ASK


def test_the_revise_argv():
    cfg = {"build": {"policy": "/p", "command": None, "revise_command": None}}
    ch = {"file": "/t/.den/changes/k.md", "mode": "docs", "origin": "agent:rc"}
    cmd = builds.command(cfg, Path("/t"), change=ch)
    assert cmd[-8:] == [
        "revise",
        "/t",
        "--note-file",
        "/t/.den/changes/k.md",
        "--mode",
        "docs",
        "--origin",
        "agent:rc",
    ]
    assert builds.command(cfg, Path("/t"), 3, change=ch)[-2:] == ["--resume", "3"]


def test_a_revise_waits_for_a_running_build(tmp_path):
    lp, iid, task, _ = held(tmp_path, concurrency=1)
    other, _ = lp.add("a second idea about dentists booking chairs")
    lp.ledger.update(
        other["id"], state="building", build={"pid": subprocess.Popen(["sleep", "30"]).pid}
    )
    lp.revise(iid, ASK)
    assert lp.start_builds() == [] and lp.ledger.get(iid)["state"] == "queued"
    lp.ledger.update(other["id"], state="closed")
    assert [i["id"] for i in lp.start_builds()] == [iid]


def test_ok_goes_through_the_gate_and_ready_publishes_privately(tmp_path, fake_gh):
    lp, iid, task, _ = held(
        tmp_path, {**RED, "revise": {"readiness": {}}}, publish={"enabled": True}
    )
    lp.revise(iid, ASK, origin="agent:rc")
    [started] = lp.start_builds()
    assert started["build"]["change"] and "started" in lp.ledger.history(iid)[0]["note"]
    out = finish(lp, iid)
    assert out["state"] == "ready" and out["gate"]["round"] == 2, out["reason"]
    assert "change" not in out["build"]
    [c] = out["build"]["changes"]
    assert (c["round"], c["verdict"], c["origin"]) == (2, "ready", "agent:rc")
    assert lp.ledger.history(iid)[0]["note"] == "revised in round 2 (agent:rc): the gate says ready"
    argv = runs(task)[-1]
    assert "--note-file" in argv and argv[argv.index("--origin") + 1] == "agent:rc"
    lp.tick()  # ready: it waits for the press, as any build
    assert lp.ledger.get(iid)["state"] == "ready"
    assert lp.publish(iid)["state"] == "published"


def test_a_failed_revise_holds_with_the_gate_it_revised(tmp_path):
    lp, iid, task, before = held(
        tmp_path,
        {
            **RED,
            "revise": {
                "outcome": "error",
                "error": "revise failed in reverify: 4 own tests still fail",
            },
        },
    )
    lp.revise(iid, ASK)
    lp.start_builds()
    out = finish(lp, iid)
    assert out["state"] == "held"
    assert out["reason"] == (
        "revise failed in reverify: 4 own tests still fail; the build is as it was"
    )
    assert out["gate"]["round"] == 1 and out["gate"]["verdict"] == before["gate"]["verdict"]
    assert out["build"]["change"] and out["build"]["round"] == 2  # Retry resumes it


def test_an_interrupted_revise_resumes_as_a_revise(tmp_path):
    lp, iid, task, _ = held(tmp_path, {**RED, "revise": {"outcome": "die"}})
    lp.revise(iid, ASK)
    lp.start_builds()
    out = finish(lp, iid)
    assert out["state"] == "queued" and out["build"]["resume_round"] == 2
    (task / "fake.json").write_text(json.dumps({**RED, "revise": {"readiness": {}}}))
    lp.start_builds()
    out = finish(lp, iid)
    assert out["state"] == "ready"
    argv = runs(task)[-1]
    assert "--note-file" in argv and argv[argv.index("--resume") + 1] == "2"


BAD_README = json.dumps(
    {
        "ok": False,
        "issues": [
            {
                "file": "README.md",
                "quote": "Book patients in.",
                "problem": "overclaims",
                "replacement": "Book patients in, on your own machine.",
            }
        ],
    }
)


def test_a_wording_issue_never_starts_a_round_and_never_holds(tmp_path, fake_gh):
    """The owner's decision (2026-10-16): the review's replacement is applied, the build
    publishes, and no docs repair round runs (there are none any more)."""
    lp, iid, task, _ = held(
        tmp_path,
        {"revise": {"readiness": {}}},
        honesty=FakeModel(BAD_README),
        publish={"enabled": True},
    )
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    assert "change" not in out["build"] and not (task / ".den" / "changes").exists()
    assert [r["now"] for r in out["publish"]["described"]["reworded"]] == [
        "Book patients in, on your own machine."
    ]


def test_an_issue_about_notice_is_none_its_text_is_fixed(tmp_path, fake_gh):
    """EX-PLAN §4.5–4.6: NOTICE is repo-factory's fixed text, covered by a fixture test, never shown
    to the honesty check; an issue the model raises about it neither holds the push nor
    makes a "repo-factory problem" (there is no such hold)."""
    notice = json.dumps(
        {
            "ok": False,
            "issues": [
                {
                    "file": "NOTICE",
                    "quote": "None of them made, reviewed or endorsed it.",
                    "problem": "odd",
                    "replacement": "",
                }
            ],
        }
    )
    lp, iid, task, _ = held(tmp_path, {}, honesty=FakeModel(notice), publish={"enabled": True})
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    assert "repo-factory problem" not in json.dumps(out)


def test_factory_words_in_the_products_own_text_never_start_a_round(tmp_path, fake_gh):
    """A provenance hit is listed and handed to the review, never a hold and never a round."""
    lp, iid, task, _ = held(
        tmp_path,
        {"files": {"AGENTS.md": "# Agents\n\nBuilt by brb\n"}, "revise": {"readiness": {}}},
        publish={"enabled": True},
    )
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    assert "change" not in out["build"]
    assert [(h["file"], h["line"]) for h in out["publish"]["jargon"]] == [("AGENTS.md", 3)]


def test_the_check_never_quotes_its_own_sandbox_paths(tmp_path):
    from repo_factory.v5 import commands

    work = tmp_path / "readme-check-x"
    got = {
        "quick": [
            {"cmd": "./install.sh", "output": f"no package at '{work}/repo' in {work}/home/.local"}
        ]
    }
    out = commands.scrub(got, work)["quick"][0]["output"]
    assert out == "no package at '.' in <sandbox>/home/.local" and str(tmp_path) not in out


def test_a_secret_always_holds_without_a_repair(tmp_path, fake_gh):
    import random
    import string

    key = "ghp_" + "".join(random.Random(9).choices(string.ascii_letters + string.digits, k=40))
    lp, iid, task, _ = held(
        tmp_path,
        {"files": {"config.py": f'TOKEN = "{key}"\n'}},
        honesty=FakeModel(BAD_README),
        publish={"enabled": True},
    )
    out = lp.publish(iid)
    assert out["state"] == "held" and "secrets scan" in out["reason"]
    assert "change" not in out["build"]


def test_cli_revise(tmp_path, capsys):
    import yaml

    from repo_factory.cli import main

    lp, iid, task, _ = held(tmp_path)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    main(
        [
            "loop",
            "--home",
            str(lp.cfg.home),
            "revise",
            iid,
            "--text",
            ASK,
            "--mode",
            "code",
            "--origin",
            "agent:rc",
        ]
    )
    assert "Queued" in capsys.readouterr().out
    i = lp.ledger.get(iid)
    assert i["state"] == "queued" and i["build"]["change"]["origin"] == "agent:rc"
