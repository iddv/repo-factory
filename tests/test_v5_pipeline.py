"""A whole v5 build offline (docs/design/build-v5.md §3): the build through ``FakeHarness``,
the verifier, the repair and the re-verify through a scripted harness, every no-tools call
through ``ScriptedLLM``; scenarios really run (``run_rules`` in the sandbox). Then ``brb
status`` and the fold render the log."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from repo_factory.costs import ledger
from repo_factory.events import fold, read_events
from repo_factory.harness.base import HarnessResult
from repo_factory.harness.fake import FakeHarness
from repo_factory.rules.llm import ScriptedLLM
from repo_factory.v5 import pipeline
from repo_factory.v5.common import STAGE_CAPS_ENV, TYPICAL_USD
from repo_factory.v5.exam import mounted

from . import v5_ready_fake
from .test_worker_profiles import box  # noqa: F401  (fixture)

READY = ["security", "checks", "ready-repair"]  # the quiet readiness stage's sessions

FIXTURE = Path(__file__).parent / "fixtures" / "repo_task"
POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"

BUGGY = '''"""Text statistics."""
from collections import Counter


def char_count(text):
    return len(text)


def word_count(text):
    return len(text.split())


def top_words(text, n):
    counts = Counter(w.lower() for w in text.split())
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n + 1]]
'''

DRIVER = (
    "from textstats.core import top_words, word_count\n\n\ndef wc(t):\n    "
    "return word_count(t)\n\n\ndef top(t, n):\n    return top_words(t, n)\n"
)
SCEN = {
    "c1": (
        "word_count counts words",
        "from _verify.driver import wc\n\n\ndef test_c1():\n    assert wc('a b c') == 3\n",
    ),
    "c2": (
        "top_words returns n words",
        "from _verify.driver import top\n\n\n"
        "def test_c2():\n    assert top('a a b c', 1) == ['a']\n",
    ),
    "c3": (
        "hyphenated words count twice",
        "from _verify.driver import wc\n\n\ndef test_c3():\n    assert wc('well-known') == 2\n",
    ),
}
CLAIMS = [
    {
        "situation": "a text of three words",
        "action": "count its words",
        "expect": "3",
        "basis": "spec_explicit",
        "quote": "returns the number of words in a string",
        "kind": "requirement",
    },
    {
        "situation": "a text where one word is most common",
        "action": "ask for the top 1",
        "expect": "exactly that one word",
        "basis": "spec_explicit",
        "quote": "returning the n most common words",
        "kind": "requirement",
    },
    {
        "situation": "a hyphenated word",
        "action": "count its words",
        "expect": "it counts as two",
        "basis": "convention",
        "quote": "",
        "kind": "edge",
    },
]


def git(ws, *a):
    subprocess.run([*pipeline.repair.GIT, *a], cwd=ws, check=True, capture_output=True)


class Scripted:
    """The build is ``FakeHarness`` (it copies the task's ``_fake_solution``); the verifier,
    survivor round, repair and re-verify sessions are scripted here."""

    name = "fake"

    def __init__(self, repair_fixes=True, repair_usd=0.3):
        self.fake = FakeHarness(failure_rate=0, solution=None)
        self.runs = []
        self.repair_fixes, self.repair_usd = repair_fixes, repair_usd

    def result(self, ws, session, usd=0.01):
        t = Path(ws) / "transcript.jsonl"
        t.write_text(json.dumps({"type": "fake"}) + "\n")
        return HarnessResult(
            Path(ws),
            1,
            10,
            10,
            usd,
            t,
            True,
            0.1,
            session_id=(session or {}).get("id"),
            session_cost_usd=usd,
            model="fake-model",
        )

    def run(self, prompt, workspace, timeout_s, **opts):
        ws = Path(workspace)
        s = opts.get("session") or {}
        kind = v5_ready_fake.quiet(prompt, ws, opts)  # the readiness stage, quietly
        if kind:
            self.runs.append((kind, opts))
            return self.result(ws, s, 0.05)
        if prompt.rstrip().endswith("Work in the current directory."):
            self.runs.append(("build", opts))
            (ws / "meta.json").write_text(json.dumps({"problem": "repo"}))
            res = self.fake.run(prompt, ws, timeout_s, **opts)
            (ws / "meta.json").unlink()
            return res
        if "You are verifying" in prompt:
            self.runs.append(("verify", opts))
            v = mounted(opts) / "_verify"  # the exam, at /work/exam
            (v / "driver.py").write_text(DRIVER)
            out = []
            for cid, (rule, code) in SCEN.items():
                (v / "scenarios" / f"test_{cid}.py").write_text(
                    f"# rule: {rule}\n# claim: {cid}\n{code}"
                )
                out.append(
                    {
                        "id": cid,
                        "file": f"_verify/scenarios/test_{cid}.py",
                        "claim": cid,
                        "status": "pass" if cid == "c1" else "fail",
                        "repro": f"python -c 'from textstats.core import *' # {cid}",
                        "observed": "wrong",
                        "expected": "right",
                        "scope": "local",
                    }
                )
            (v / "findings.json").write_text(
                json.dumps(
                    {
                        "scenarios": out,
                        "blocked": [{"claim": "c9", "why": "no hook"}],
                        "ambiguities": [{"id": "a1", "implements": "literal"}],
                        "self_estimate": {"p_hidden": 0.9, "least_trusted": ["unicode"]},
                    }
                )
            )
            return self.result(ws, s, 0.5)
        if "=== Findings ===" in prompt:
            self.runs.append(("repair", opts))
            ids = re.findall(r"^### (\w+):", prompt, re.M)
            ex = mounted(opts)  # the visible exam, read-only
            assert (ex / "_verify" / "driver.py").exists()
            assert not (ws / "acceptance").exists() and not (ws / "_verify").exists()
            if self.repair_fixes:
                core = ws / "textstats" / "core.py"
                core.write_text(core.read_text().replace("[:n + 1]", "[:n]"))
                git(ws, "add", "-A")
                git(ws, "commit", "-q", "-m", f"fix({','.join(ids)}): top_words returns n words")
            return self.result(ws, s, self.repair_usd)
        if "The builder has repaired" in prompt:
            self.runs.append(("reverify", opts))
            assert "top_words" in (ws / "textstats" / "core.py").read_text()
            (mounted(opts) / "_verify" / "scenarios" / "test_r1.py").write_text(
                "# rule: probing the fix\n# claim: explore\nfrom _verify.driver import top\n\n\n"
                "def test_r1():\n    assert top('b a b', 1) == ['b']\n"
            )
            return self.result(ws, s, 0.1)
        raise AssertionError(f"unexpected session: {prompt[:200]}")


def llm():
    calls = {"claims": 0}

    def claims(prompt, mode):
        calls["claims"] += 1
        return json.dumps(
            {
                "claims": CLAIMS if calls["claims"] == 1 else CLAIMS[:2],
                "ambiguities": [
                    {
                        "question": "Is punctuation part of a word?",
                        "literal": "yes",
                        "expected": "no",
                        "recommend": "expected",
                        "stakes": "low",
                    }
                ],
            }
        )

    def oracle(prompt, mode):
        out = {}
        for i, body in re.findall(r"^### (\S+)\n(.*?)(?=^### |\Z)", prompt, re.M | re.S):
            vote = "not_required" if "hyphenated" in body else "required"
            out[i] = {"vote": vote, "quote": "the n most common words"}
        return json.dumps({"results": out})

    def interpret(prompt, mode):
        return json.dumps(
            {
                "results": {
                    i: {"reading": "literal"} for i in re.findall(r"^### (\S+)$", prompt, re.M)
                }
            }
        )

    return ScriptedLLM(
        {
            "claims": claims,
            "dedupe": "{}",
            "oracle": oracle,
            "interpret": interpret,
            "pair": json.dumps({"verdict": "compatible"}),
        }
    )


@pytest.fixture
def task(tmp_path):
    t = tmp_path / "task"
    shutil.copytree(FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    return t


def run_build(task, tmp_path, harness, **kw):
    sol = tmp_path / "solution" / "textstats"  # what the fake builder writes
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(BUGGY)
    harness.fake.solution = sol.parent
    return pipeline.build(
        task,
        den=tmp_path / "den",
        out=tmp_path / "out",
        harness=harness,
        llm=llm(),
        policy=POLICY,
        **kw,
    )


def test_a_whole_v5_build_offline(task, tmp_path):
    h = Scripted()
    out = run_build(task, tmp_path, h)
    assert [r[0] for r in h.runs] == ["build", "verify", "repair", "reverify", *READY]
    # no cap anywhere by default: no session gets a dollar allowance (--max-budget-usd);
    # repair round 1 resumes (forks) the builder's session: the build ended moments ago, so
    # its prompt cache is warm
    assert all(o["max_usd"] is None for _, o in h.runs), [(n, o["max_usd"]) for n, o in h.runs]
    assert dict(h.runs)["verify"]["tools"] == "full"
    assert out["failed"] is None and out["partial"] is None
    build_sid = dict(h.runs)["build"]["session"]["id"]
    assert dict(h.runs)["repair"]["session"]["resume"] == build_sid
    assert out["pipeline"] == "v5" and out["branch"] == "repo-factory/textstats"
    for k in ("task", "round", "delivered", "branch", "out", "den", "cost_usd", "events", "wall_s"):
        assert k in out
    evs = read_events(Path(out["events"]))
    types = [e["type"] for e in evs]
    for t in (
        "build_started",
        "v5_stage",
        "claims",
        "findings",
        "repair_round",
        "interpretation",
        "delivered",
        "build_finished",
        "rule_status",
    ):
        assert t in types, t
    assert "prediction" not in types  # no p_hidden any more (AU1 #5)
    c = next(e for e in evs if e["type"] == "claims")
    assert (c["n"], c["ambiguities"], c["samples"]) == (3, 1, 1)
    claims = json.loads(Path(out["claims"]).read_text())
    assert [x["sets"] for x in claims["claims"]] == [[1], [1], [1]]
    f = next(e for e in evs if e["type"] == "findings" and e["stage"] == "oracle")
    assert (f["confirmed"], f["retired"], f["blocked"]) == (1, 1, 1)
    assert "mutation" not in types  # the mutants stage is gone (AU1 #4)
    assert not any(e["type"] == "v5_stage" and e["stage"] == "mutants" for e in evs)
    r = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"][0]
    assert r["accepted"] and r["fixed"] == 1 and r["regressions"] == 0
    # the harness's record: the fixed scenario passes on the delivered head
    fnd = json.loads(Path(out["findings"]).read_text())
    by = {s["id"]: s for s in fnd["scenarios"]}
    assert by["c2"]["class"] == "defect" and by["c2"]["harness_status"] == "passed"
    assert by["c3"]["class"] == "retired" and by["c3"]["store_status"] == "retired"
    # cost: every ledger record carries its stage; sessions are recorded by caller
    recs = ledger.read(Path(out["den"]), out["round"])
    assert {r["stage"] for r in recs} >= {"build", "verify", "repair", "reverify"}
    assert {r["caller"] for r in recs if r["stage"] == "verify"} == {"verifier"}
    # tokens: each session's own usage (the scripted verifier reports 10 in, 10 out)
    ver = [r for r in recs if r["stage"] == "verify"]
    assert [(r["in_tokens"], r["out_tokens"]) for r in ver] == [(10, 10)]
    assert all("in_tokens" in r for r in recs if r.get("session"))
    assert out["cost_by_stage"]["verify"] == pytest.approx(0.5)
    # delivery (EX-PLAN §4): the branch has the fix and nothing of repo-factory's; the exam, its
    # results and the reports are the round's evidence
    o = Path(out["out"])
    assert "[:n]" in (o / "textstats" / "core.py").read_text()
    for gone in (
        "tests/acceptance",
        "tests/verification",
        "ASSUMPTIONS.md",
        "SECURITY.md",
        "READINESS.md",
        "_verify",
        "acceptance",
    ):
        assert not (o / gone).exists(), gone
    ev = Path(out["den"]) / "v5" / f"r{out['round']}" / "exam" / "evidence"
    assert sorted(
        p.name for p in (ev / "exam" / "_verify" / "scenarios").glob("test_*.py")
    ) == sorted(f"test_rule_{i}.py" for i in by_rule(fnd, ("c1", "c2", "r1")))
    assert "Is punctuation part of a word?" in (ev / "assumptions.md").read_text()
    for name in ("README.md", "results.json", "readiness.md", "security.md", "manifest.json"):
        assert (ev / name).is_file(), name
    man = json.loads((ev / "manifest.json").read_text())
    assert man["commit"] and man["tree"] and man["exam_hash"]
    # one sandbox run per distinct tree hash and rule set (EX-PLAN §3.2): no record twice
    runs = [json.loads(x) for x in (ev.parent / "runs.jsonl").read_text().splitlines()]
    assert runs and len({r["key"] for r in runs}) == len(runs)
    assert sum(1 for e in read_events(Path(out["events"])) if e["type"] == "exam_run") == len(runs)
    rep = (o / "REPORT.md").read_text()
    assert (
        rep.index("## What was checked") < rep.index("## Findings") < rep.index("## Cost by stage")
    )
    # the round says which session it used, and why; no global cap by default
    assert "| 1 | resume (the builder session was last used " in rep
    assert out["cap_usd"] is None and "| no cap |" in rep
    assert out["checked"] == {
        "claims": 3,
        "found": 1,
        "fixed": 1,
        "open": 0,
        "blocked": 1,
        "least_trusted": ["unicode"],
    }
    assert "3 claims from the spec: 1 defects confirmed, 1 fixed, 0 open" in rep
    assert not (Path(out["den"]) / "v5" / f"r{out['round']}" / "prediction.json").exists()
    # the delivered tree's exam record: every remaining scenario passes on it
    res = json.loads((ev / "results.json").read_text())
    assert res["scenarios"] and {v["status"] for v in res["scenarios"].values()} == {"passed"}

    # repo-factory status and the UI fold render the v5 log
    st = fold(evs)
    assert st["status"] == "ok" and "prediction" not in st["v5"]
    assert st["v5"]["stages"]["verify"]["status"] == "done"
    assert (
        st["v5"]["stages"]["verify"]["tokens_in"],
        st["v5"]["stages"]["verify"]["tokens_out"],
    ) == (10, 10)
    assert st["activity"]["phase"] == "done"
    from repo_factory.progress import status_text, summary

    text = status_text(st)
    assert "Stages: scope done, build done" in text and "Repair round 1: 1 of 1 fixed" in text
    assert "Scope: pass" in text
    assert "Prediction" not in text and "hidden" not in text
    assert "Claims: 3 from 1 sample," in text
    assert "repo-factory/textstats" in summary(st)


def test_a_build_resumes_step_by_step_from_its_saved_state(task, tmp_path):
    h = Scripted()
    first = run_build(task, tmp_path, h, steps=3)
    assert not first["finished"] and first["done_steps"] == ["scope", "build", "claims"]
    state = json.loads((Path(first["den"]) / "v5" / "r1" / "state.json").read_text())
    assert state["builder_session"]["id"] and state["claims"]["claims"]
    seen = []
    while True:  # one step per call, as the real check
        out = pipeline.build(
            task,
            den=tmp_path / "den",
            harness=h,
            llm=llm(),
            policy=POLICY,
            resume=first["round"],
            steps=1,
        )
        seen.append(out["done_steps"][-1] if out["done_steps"] else None)
        if out["finished"]:
            break
        assert len(seen) < 16  # one more: the adopt step
    assert [r[0] for r in h.runs] == ["build", "verify", "repair", "reverify", *READY]
    evs = read_events(Path(out["events"]))
    assert [e["type"] for e in evs].count("build_started") == 1
    assert [e["type"] for e in evs].count("build_finished") == 1
    r = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"][0]
    assert r["accepted"] and out["checked"]["fixed"] == 1
    assert "[:n]" in (Path(out["out"]) / "textstats" / "core.py").read_text()


def test_a_step_that_dies_after_its_session_resumes_without_paying_twice(
    task, tmp_path, monkeypatch
):
    from repo_factory.v5 import verify as V

    h = Scripted()
    real, calls = V.validate, []

    def flaky(store, rules, spec, run):
        calls.append([r.id for r in rules])
        if len(calls) == 2:  # the re-verify's validation: crash once
            raise OSError("the harness's run died")
        return real(store, rules, spec, run)

    monkeypatch.setattr(V, "validate", flaky)
    with pytest.raises(OSError):
        run_build(task, tmp_path, h)
    assert [r[0] for r in h.runs][-1] == "reverify"
    out = pipeline.build(task, den=tmp_path / "den", harness=h, llm=llm(), policy=POLICY, resume=1)
    assert out["finished"]
    assert [r[0] for r in h.runs].count("reverify") == 1  # the session was reused
    recs = ledger.read(Path(out["den"]), 1)
    assert sum(1 for r in recs if r.get("purpose") == "reverify1") == 1
    st = json.loads((Path(out["den"]) / "v5" / "r1" / "findings.json").read_text())
    r1 = next(s for s in st["scenarios"] if s["id"] == "r1")
    assert r1["store_status"] == "active" and r1["harness_status"] == "passed"


def test_a_round_that_dies_in_acceptance_resumes_without_a_second_repair(
    task, tmp_path, monkeypatch
):
    # The repair session and the acceptance are separate steps: a run stopped (or killed)
    # after the session keeps its commits and its base, and doesn't pay for the repair again.
    from repo_factory.v5 import accept as A

    h = Scripted()
    real, calls = A.decide, []

    def flaky(before, after, sent):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("killed during the measures")
        return real(before, after, sent)

    monkeypatch.setattr(A, "decide", flaky)
    with pytest.raises(OSError):
        run_build(task, tmp_path, h)
    assert [r[0] for r in h.runs][-1] == "repair"
    out = pipeline.build(task, den=tmp_path / "den", harness=h, llm=llm(), policy=POLICY, resume=1)
    assert out["finished"] and [r[0] for r in h.runs].count("repair") == 1
    r = [
        e
        for e in read_events(Path(out["events"]))
        if e["type"] == "repair_round" and e["status"] == "done"
    ][0]
    assert r["accepted"] and r["fixed"] == 1
    assert "[:n]" in (Path(out["out"]) / "textstats" / "core.py").read_text()


def test_the_activity_line_names_the_v5_stage():
    base = {"task": "t", "build": 1, "v": 1}
    evs = [
        {**base, "type": "build_started", "ts": 1.0, "pipeline": "v5"},
        {**base, "type": "v5_stage", "ts": 2.0, "stage": "verify", "status": "started"},
    ]
    a = fold(evs)["activity"]
    assert (a["phase"], a["stage"]) == ("verifying", "Verifying (a fresh session)")
    evs.append(
        {**base, "type": "repair_round", "ts": 3.0, "round": 1, "status": "repairing", "sent": 2}
    )
    a = fold(evs)["activity"]
    assert (a["phase"], a["stage"]) == ("repairing", "Repair round 1: 2 finding(s)")


def test_the_budget_reserve_blocks_the_repair_stage_at_the_cap(task, tmp_path):
    h = Scripted()
    out = run_build(task, tmp_path, h, max_usd=2.0)  # repair needs $2.50 in reserve
    assert "repair" not in [r[0] for r in h.runs]
    evs = read_events(Path(out["events"]))
    skip = [e for e in evs if e["type"] == "repair_round"]
    assert skip and skip[0]["status"] == "skipped" and "reserve rule" in skip[0]["why"]
    assert out["stop"].startswith("reserve rule") and out["cap_usd"] == 2.0
    rep = (Path(out["out"]) / "REPORT.md").read_text()
    assert "### Open" in rep and "Stopped: reserve rule" in rep
    assert "[:n + 1]" in (Path(out["out"]) / "textstats" / "core.py").read_text()


def test_a_repair_that_fixes_nothing_is_undone(task, tmp_path):
    h = Scripted(repair_fixes=False)
    out = run_build(task, tmp_path, h)
    evs = read_events(Path(out["events"]))
    r = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"][0]
    assert not r["accepted"]
    # the delivered head is the base build plus the readiness round only (candidate 4: the
    # build, two undone repair rounds, the readiness repair)
    assert (
        out["delivered"] == 4
        and "[:n + 1]" in (Path(out["out"]) / "textstats" / "core.py").read_text()
    )


def test_repair_round_2_gets_its_own_allowance_after_round_1_spent_the_stage_cap(
    task, tmp_path, monkeypatch
):
    # The lending run: round 1 and its fix-up spent $2.44 of the repair stage's $2.50, so
    # round 2's resumed session was launched with --max-budget-usd 0.06 and stopped after one
    # turn. Each round now has its own window: §3.1's $2.00 for a second round. (Stage caps
    # are a setting now, none by default: the old ones, set, through the loop's variable.)
    monkeypatch.setenv(STAGE_CAPS_ENV, json.dumps(TYPICAL_USD))
    h = Scripted(repair_fixes=False, repair_usd=2.44)
    out = run_build(task, tmp_path, h)
    reps = [o for name, o in h.runs if name == "repair"]
    assert len(reps) == 2
    assert reps[0]["max_usd"] == 2.5 and reps[1]["max_usd"] == 2.0
    build_sid = dict(h.runs)["build"]["session"]["id"]
    assert all(o["session"]["resume"] == build_sid for o in reps)  # both fork the builder
    assert not any("repair" in s for s in out.get("caps_hit") or [])
    # both rounds were undone: no re-verify of an unchanged head
    assert "reverify" not in [name for name, _ in h.runs]
    skips = [
        e
        for e in read_events(Path(out["events"]))
        if e["type"] == "v5_stage" and e["stage"] == "reverify" and e["status"] == "skipped"
    ]
    assert len(skips) == 2 and out["stop"].startswith("repair.max_rounds")
    st = json.loads((Path(out["den"]) / "v5" / "r1" / "state.json").read_text())
    assert st["windows"]["repair"] == [pytest.approx(2.44), 2.0]  # saved: resumable


def test_a_resumed_sessions_budget_flag_is_its_own_allowance(box):  # noqa: F811
    # Measured on CLI 2.1.287: --max-budget-usd on a resumed (forked) session counts only
    # the new run's spend, though its total_cost_usd is cumulative. So the flag is the
    # allowance, never the prior cost plus it (that would let a fork overspend by the prior).
    from repo_factory.harness import claude_code, profile

    home, ws = box
    sess = {"id": "S2", "resume": "S1", "base_cost_usd": 2.18}
    cmd = profile.command(ws, config_dir=home / "cfg", max_turns=60, max_usd=2.0, session=sess)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.00"
    assert cmd[cmd.index("--resume") + 1] == "S1" and "--fork-session" in cmd
    assert cmd[cmd.index("--session-id") + 1] == "S2"
    # the run's own cost is still the reported cumulative total minus the prior
    assert claude_code.run_cost(4.2618, sess) == pytest.approx(2.0818)
    # the pipeline's budget hands a resumed session the allowance, not the session's total
    from repo_factory.v5.common import Budget

    b = Budget(11.0, stage_caps=TYPICAL_USD)
    b.extra["repair"] = 2.44
    assert b.session_cap("repair", 2.5) == pytest.approx(0.06)  # the old, shared cap
    b.window("repair", 2.0)
    assert b.session_cap("repair", 2.5) == 2.0


def test_cmd_build_dispatches_on_pipeline_v5(tmp_path, monkeypatch):
    import argparse

    from repo_factory import cli

    seen = {}

    def fake_build(taskdir, **kw):
        seen.update(kw, taskdir=taskdir)
        return {"events": str(tmp_path / "events.jsonl"), "round": 1}

    monkeypatch.setattr(pipeline, "build", fake_build)
    monkeypatch.setattr(cli, "_finish", lambda args, out: seen.setdefault("finished", out))
    args = argparse.Namespace(
        den="den",
        owner="human",
        policy=str(POLICY),
        max_cost=7.5,
        out=None,
        taskdir=str(tmp_path),
        json=True,
        harness="claude-code",
        builder=None,
        builder_model=None,
        keep_workspaces=False,
        worker_model=None,
    )
    cli.cmd_build(args)
    assert seen["policy"] == POLICY and seen["max_usd"] == 7.5 and seen["finished"]["round"] == 1
    assert pipeline.is_v5(POLICY) and not pipeline.is_v5(POLICY.parent / "v4")


def by_rule(fnd: dict, scenarios) -> list[str]:
    """The store ids of the given scenario ids (findings.json's ``rule``)."""
    return [
        s["rule"]
        for s in fnd["scenarios"]
        if s["id"] in scenarios and s.get("rule") and s.get("store_status") == "active"
    ]
