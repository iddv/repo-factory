"""V5-POLISH, offline: no global cap by default (no stage skipped for lack of global budget;
the reserve rules only under a cap), the README's commands run as written by the readiness
stage (a failing test command is red and repaired; the acceptance suite's README too), the
immediate restart after a stop and after a SIGKILL, and the cost trims (no survivor round,
one claim sample for scoped builds with an honest one-sample calibration, re-verify only
when a round can follow it, a fresh or resumed repair session chosen by how long ago the
build ended)."""

import json
import re
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from repo_factory.events import fold, read_events
from repo_factory.rules.llm import ScriptedLLM
from repo_factory.v5 import accept, commands, pipeline, readiness
from repo_factory.v5.common import Budget, BudgetExceeded
from repo_factory.v5.exam import mounted

from . import test_v5_pipeline as base
from . import v5_ready_fake as fake

POLICY = base.POLICY
SANDBOX = pytest.mark.skipif(not commands.available(), reason="needs bwrap")


def policy_copy(tmp_path, **changes) -> Path:
    """policies/v5 with ``changes`` (dotted keys) applied to rules.yaml."""
    p = tmp_path / "policy"
    shutil.copytree(POLICY, p)
    rules = yaml.safe_load((p / "rules.yaml").read_text())
    for k, v in changes.items():
        d = rules
        *head, last = k.split(".")
        for h in head:
            d = d.setdefault(h, {})
        d[last] = v
    (p / "rules.yaml").write_text(yaml.safe_dump(rules, sort_keys=False))
    return p


def build(tmp_path, h, policy=POLICY, files=None, **kw):
    t = tmp_path / "task"
    shutil.copytree(base.FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    sol = tmp_path / "solution"
    (sol / "textstats").mkdir(parents=True)
    (sol / "textstats" / "__init__.py").write_text("")
    (sol / "textstats" / "core.py").write_text(base.BUGGY)
    for rel, text in (files or {}).items():
        (sol / rel).parent.mkdir(parents=True, exist_ok=True)
        (sol / rel).write_text(text)
    h.fake.solution = sol
    out = pipeline.build(
        t, den=t / ".den", out=tmp_path / "out", harness=h, llm=base.llm(), policy=policy, **kw
    )
    return t, out


# -- 1. no global cap by default ---------------------------------------------------------------


def test_the_budget_without_a_global_cap():
    b = Budget(None)
    b.extra["build"] = 50.0
    assert not b.limited and b.cap_text() == "no cap"
    assert b.allows(1e6) and b.left() == float("inf")
    b.check("verify")  # no global cap to hit
    assert b.session_cap("verify", 2.5) == 2.5  # the session's own max_usd
    assert b.session_cap("verify", None) is None  # nothing caps it: no flag at all
    b.extra["verify"] = 25.0
    b.check("verify")  # no stage cap by default either
    staged = Budget(None, stage_caps={"verify": 2.5})  # a stage cap, when one is set
    staged.extra["verify"] = 2.5
    with pytest.raises(BudgetExceeded, match="stage cap"):
        staged.check("verify")
    assert staged.session_cap("verify", 2.5) == 0.0 and staged.session_cap("verify", None) == 0.0
    capped = Budget(4.0)
    capped.extra["build"] = 3.5
    assert capped.limited and not capped.allows(1.0) and capped.cap_text() == "$4.00"


def test_the_cap_comes_from_the_caller_the_task_or_the_default(monkeypatch):
    rules = pipeline.load_rules(POLICY)
    monkeypatch.delenv(pipeline.CAP_ENV, raising=False)
    assert pipeline.build_cap(None, None, rules) is None  # the default: no cap
    assert pipeline.build_cap(3.0, 9.0, rules) == 3.0  # --max-cost
    assert pipeline.build_cap(None, 9.0, rules) == 9.0  # the task's budget_usd
    assert pipeline.build_cap(None, None, rules, vanilla_usd=2.0) == pytest.approx(5.9)
    monkeypatch.setenv(pipeline.CAP_ENV, "6")
    assert pipeline.build_cap(None, None, rules) == 6.0


def test_an_uncapped_build_skips_no_stage_and_keeps_no_reserve(tmp_path):
    """The same build that skips the readiness repair under a $1.50 cap (test_v5_readiness)
    runs every stage without a cap: no repair round, re-verify or readiness step is skipped
    for lack of global budget, and nothing is held in reserve."""
    from .test_v5_readiness import ready_build

    t, h, out = ready_build(tmp_path)
    assert out["cap_usd"] is None
    assert [r[0] for r in h.runs] == [
        "build",
        "verify",
        "repair",
        "reverify",
        "security",
        "checks",
        "ready-repair",
    ]
    evs = read_events(Path(out["events"]))
    skipped = [e for e in evs if e.get("status") == "skipped"]
    assert not [e for e in skipped if re.search(r"cap|reserve|budget", str(e.get("why")))]
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    assert rd["skipped"] is None and rd["round"]["accepted"]
    # medium findings go too: without a cap the full round is always affordable
    assert [f["sent"] for f in rd["security"]["findings"]][:3] == [True, True, True]
    st = json.loads((Path(out["den"]) / "v5" / "r1" / "state.json").read_text())
    assert st["cap"] is None
    rep = (Path(out["out"]) / "REPORT.md").read_text()
    assert "| no cap |" in rep
    # the UI: "no cap" on the cost line; a capped build shows its cap
    from repo_factory.ui.v5view import view

    sf = fold(evs)
    assert "cap_usd" in sf and sf["cap_usd"] is None
    assert view(t, sf)["cost"]["cap"] is None
    capped = fold([{**e, "cap_usd": 4.0} if e["type"] == "build_started" else e for e in evs])
    assert view(t, capped)["cost"]["cap"] == 4.0
    old = fold([{k: v for k, v in e.items() if k != "cap_usd"} for e in evs])
    assert view(t, old)["cost"]["cap"] == "unknown"


def test_the_reserve_rules_apply_only_under_a_cap():
    rules = pipeline.load_rules(POLICY)

    def fake(cap, spent):
        b = Budget(cap)
        b.extra["build"] = spent
        return SimpleNamespace(
            rules=rules,
            budget=b,
            cfg_of=lambda n: rules.get(n) or {},
            readiness_reserve=lambda: pipeline.Run.readiness_reserve(
                SimpleNamespace(rules=rules, budget=b)
            ),
        )

    # under a cap: re-verify needs its own cap + round 2 + the readiness reserve
    why = pipeline.Run.no_further_round(fake(10.0, 4.0), 1)
    assert why.startswith("reserve rule") and "$6.30" in why  # 1.00 + 2.00 + 3.30
    assert pipeline.Run.no_further_round(fake(20.0, 4.0), 1) is None
    # without one only max_rounds stops it
    assert pipeline.Run.no_further_round(fake(None, 400.0), 1) is None
    assert "max_rounds" in pipeline.Run.no_further_round(fake(None, 0.0), 2)
    # readiness: the start and the round's minimum never block without a cap
    run = SimpleNamespace(budget=Budget(None), rules={"readiness": {}})
    run.budget.extra["build"] = 99.0
    assert run.budget.allows(float(readiness.cfg(run, "start_usd")))
    assert run.budget.allows(float(readiness.cfg(run, "repair")["min_usd"]))


# -- 2. re-verify only when a round can follow; its findings get one --------------------------


class ReverifyFinds(base.Scripted):
    """Re-verify finds a new defect (char_count counts a trailing newline); round 2 fixes it."""

    def run(self, prompt, workspace, timeout_s, **opts):
        ws = Path(workspace)
        s = opts.get("session") or {}
        if "The builder has repaired" in prompt:
            self.runs.append(("reverify", opts))
            ((mounted(opts) or ws) / "_verify" / "scenarios" / "test_r2.py").write_text(
                "# rule: char_count ignores the trailing newline\n# claim: explore\n"
                "from textstats.core import char_count\n\n\n"
                "def test_r2():\n    assert char_count('ab\\n') == 2\n"
            )
            return self.result(ws, s, 0.1)
        if "=== Findings ===" in prompt and any(r[0] == "repair" for r in self.runs):
            self.runs.append(("repair", opts))
            core = ws / "textstats" / "core.py"
            core.write_text(
                core.read_text().replace("return len(text)\n", "return len(text.rstrip('\\n'))\n")
            )
            ids = re.findall(r"^### (\w+):", prompt, re.M)
            base.git(ws, "add", "-A")
            base.git(ws, "commit", "-q", "-m", f"fix({','.join(ids)}): no trailing newline")
            return self.result(ws, s, 0.3)
        return super().run(prompt, workspace, timeout_s, **opts)


def test_uncapped_reverify_findings_lead_to_a_second_round(tmp_path):
    h = ReverifyFinds()
    t, out = build(tmp_path, h)
    assert [r[0] for r in h.runs][:5] == ["build", "verify", "repair", "reverify", "repair"]
    assert [r[0] for r in h.runs].count("reverify") == 1  # none after the last round
    evs = read_events(Path(out["events"]))
    done = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"]
    assert [e["round"] for e in done] == [1, 2] and done[1]["fixed"] == 1
    skip = [
        e
        for e in evs
        if e["type"] == "v5_stage" and e["stage"] == "reverify" and e["status"] == "skipped"
    ]
    assert len(skip) == 1 and "max_rounds" in skip[0]["why"]
    assert "rstrip" in (Path(out["out"]) / "textstats" / "core.py").read_text()


def test_capped_reverify_is_skipped_when_no_round_can_follow(tmp_path):
    h = ReverifyFinds()
    t, out = build(tmp_path, h, max_usd=5.0)  # round 1 fits; re-verify + round 2 don't
    runs = [r[0] for r in h.runs]
    assert runs.count("repair") == 1 and "reverify" not in runs
    evs = read_events(Path(out["events"]))
    skip = [
        e
        for e in evs
        if e["type"] == "v5_stage" and e["stage"] == "reverify" and e["status"] == "skipped"
    ]
    assert skip and skip[0]["why"].startswith("reserve rule") and "cap $5.00" in skip[0]["why"]


# -- 3. the other trims ------------------------------------------------------------------------


def test_every_build_takes_one_claim_sample_and_no_dedupe_call(tmp_path):
    calls = []

    def claims_reply(prompt, mode):
        calls.append("claims")
        return json.dumps({"claims": base.CLAIMS, "ambiguities": []})

    def dedupe(prompt, mode):
        calls.append("dedupe")
        return "{}"

    llm = ScriptedLLM({"claims": claims_reply, "dedupe": dedupe})
    rules = pipeline.load_rules(POLICY)
    got = {}
    for scoped in (True, False):
        calls.clear()
        run = SimpleNamespace(
            cfg_of=lambda n: rules.get(n) or {},
            scoped=lambda s=scoped: s,
            llm=llm,
            policy=POLICY,
            spec="Count words.",
            lessons=[],
            budget=Budget(None),
            root=tmp_path,
            emit=lambda *a, **k: None,
        )
        pipeline.Run.do_claims(run)
        got[scoped] = (
            run.claims["samples"],
            list(calls),
            {tuple(c["sets"]) for c in run.claims["claims"]},
        )
    # one sample, no dedupe, scoped or not: the second sample fed only p_hidden (AU1 #5)
    assert got[True] == got[False] == (1, ["claims"], {(1,)})


def test_the_repair_session_resumes_only_while_the_build_is_warm():
    def run(**kw):
        rules = {"repair": {"resume": kw.pop("mode", "auto"), "warm_minutes": 30}}
        return SimpleNamespace(cfg_of=lambda n: rules.get(n) or {}, builder_session=kw.get("bs"))

    now = time.time()
    warm = pipeline.Run.resume_choice(run(bs={"id": "B", "ended": now - 5 * 60}))
    assert warm["mode"] == "resume" and warm["idle_min"] == pytest.approx(5, abs=0.2)
    cold = pipeline.Run.resume_choice(run(bs={"id": "B", "ended": now - 45 * 60}))
    assert cold["mode"] == "fresh" and "expired" in cold["why"]
    # a resume refreshes the cache: "used" counts from then
    used = pipeline.Run.resume_choice(run(bs={"id": "B", "ended": now - 90 * 60, "used": now - 60}))
    assert used["mode"] == "resume"
    assert pipeline.Run.resume_choice(run(bs={"id": "B"}))["mode"] == "fresh"  # unknown
    assert pipeline.Run.resume_choice(run(bs=None))["mode"] == "fresh"
    assert (
        pipeline.Run.resume_choice(run(bs={"id": "B", "ended": now - 999 * 60}, mode=True))["mode"]
        == "resume"
    )
    assert (
        pipeline.Run.resume_choice(run(bs={"id": "B", "ended": now}, mode=False))["mode"] == "fresh"
    )


def test_a_cold_build_gets_a_fresh_repair_session_and_says_so(tmp_path):
    h = base.Scripted()
    t, out = build(tmp_path, h, policy=policy_copy(tmp_path, **{"repair.warm_minutes": 0}))
    rep = dict(h.runs)["repair"]
    assert "resume" not in rep["session"]  # a fresh session
    st = json.loads((Path(out["den"]) / "v5" / "r1" / "state.json").read_text())
    assert st["rounds"][0]["session"]["mode"] == "fresh"
    ev = next(
        e
        for e in read_events(Path(out["events"]))
        if e["type"] == "repair_round" and e["status"] == "done"
    )
    assert ev["session"] == "fresh"
    assert (
        "| 1 | fresh (the builder session was last used"
        in (Path(out["out"]) / "REPORT.md").read_text()
    )


# -- 4. the README's commands, run as written ----------------------------------------------------

README_UNITTEST = """# Textstats

Counts words.

## Install

    pip install .

## Tests

```sh
python -m unittest discover -s tests
```
"""
# A README whose test command names a directory that doesn't exist: no test runs (exit 5)
README_BROKEN = README_UNITTEST.replace("discover -s tests", "discover -s test")
UNIT = (
    "import unittest\n\nfrom textstats.core import word_count\n\n\n"
    "class T(unittest.TestCase):\n    def test_words(self):\n"
    "        self.assertEqual(word_count('a b'), 2)\n"
)


class ReadmeRepair(base.Scripted):
    """The readiness repair fixes the README's test command (and adds the usual files)."""

    BROKEN, FIXED = (
        "python -m unittest discover -s test\n",
        "python -m unittest discover -s tests\n",
    )

    def __init__(self, fix=True):
        super().__init__()
        self.fix, self.saw = fix, {}

    def run(self, prompt, workspace, timeout_s, **opts):
        ws = Path(workspace)
        if fake.REPAIR in prompt and "### tests_green" in prompt:
            self.runs.append(("ready-repair", opts))
            self.saw["brief"] = prompt
            # nothing of repo-factory's is in the workspace to run the command against (EX-PLAN §4.2)
            self.saw["acceptance"] = (ws / "tests" / "acceptance").exists()
            if self.fix:
                r = ws / "README.md"
                r.write_text(r.read_text().replace(self.BROKEN, self.FIXED))
                base.git(ws, "add", "-A")
                base.git(ws, "commit", "-q", "-m", "fix(tests_green): document the pytest run")
            fake.write_ready_files(ws)
            base.git(ws, "add", "-A")
            base.git(ws, "commit", "-q", "-m", "fix(ci,gitignore,readme): CI, ignores, README")
            return self.result(ws, opts.get("session"), 0.05)
        return super().run(prompt, workspace, timeout_s, **opts)


@SANDBOX
def test_unittest_discovery_runs_green_as_written_with_the_exam_outside(tmp_path):
    """Beliefline: ``python3 -m unittest discover -s tests`` collected repo-factory's pytest suite in
    ``tests/acceptance/`` (89 import errors). With the exam outside the tree it runs only the
    product's own tests, green, and nothing goes to the readiness repair for it."""
    h = ReadmeRepair()
    t, out = build(tmp_path, h, files={"README.md": README_UNITTEST, "tests/test_unit.py": UNIT})
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    first = next(
        x for x in rd["commands"]["tests"] if x["cmd"] == "python -m unittest discover -s tests"
    )
    assert first["status"] == "ok" and first["runs"] == ["ok"]
    assert "tests_green" not in {d.get("ref") for d in rd.get("sent") or []}
    card = {i["id"]: i for i in rd["final"]["items"]}
    assert card["tests_green"]["status"] == "green", card["tests_green"]
    assert not (Path(out["out"]) / "tests" / "acceptance").exists()


@SANDBOX
def test_a_readme_test_command_that_fails_as_written_is_red_and_repaired(tmp_path):
    h = ReadmeRepair()
    t, out = build(tmp_path, h, files={"README.md": README_BROKEN, "tests/test_unit.py": UNIT})
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    # the commands step: the documented directory doesn't exist; it fails as written (twice:
    # red, not a flake)
    first = next(
        x for x in rd["commands"]["tests"] if x["cmd"] == "python -m unittest discover -s test"
    )
    assert first["status"] == "failed" and first["rc"] != 0
    assert first["runs"][:2] == ["failed", "failed"]
    sent = {d["ref"]: d for d in rd["sent"]}
    assert "tests_green" in sent and sent["tests_green"]["check"]
    assert readiness.basis(sent["tests_green"]) == "readiness"  # rank 3
    assert accept.rank({"kind": "scenario", "basis": "readiness"}) == 3
    brief = h.saw["brief"]
    assert "`python -m unittest discover -s test` (README.md:" in brief
    assert not h.saw["acceptance"]  # nothing of repo-factory's in the workspace
    # after the round the harness ran it again as written: fixed
    assert rd["round"]["accepted"] and "tests_green" in rd["round"]["extra"]
    card = {i["id"]: i for i in rd["final"]["items"]}
    assert card["tests_green"]["status"] == "green", card["tests_green"]
    assert "run green as written" in card["tests_green"]["why"]
    o = Path(out["out"])
    assert "python -m unittest discover -s tests\n" in (o / "README.md").read_text()


@SANDBOX
def test_an_unrepaired_readme_test_command_stays_red_in_the_scorecard(tmp_path):
    h = ReadmeRepair(fix=False)
    t, out = build(tmp_path, h, files={"README.md": README_BROKEN, "tests/test_unit.py": UNIT})
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    card = {i["id"]: i for i in rd["final"]["items"]}
    tg = card["tests_green"]
    assert tg["status"] == "red" and "python -m unittest discover -s test" in tg["why"]
    ev = Path(out["den"]) / "v5" / "r1" / "exam" / "evidence"
    ready = (ev / "readiness.md").read_text()
    assert "| The documented test commands run green, as written | red |" in ready
    assert "The README's commands, run as written" in (Path(out["out"]) / "REPORT.md").read_text()


def test_readme_commands_are_extracted_by_purpose(tmp_path):
    root = tmp_path / "p"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "app").write_text("#!/bin/sh\n")
    (root / "app.sh").write_text("#!/bin/sh\n")
    (root / "README.md").write_text("""# App

## Install and start

```sh
git clone <this repo> app && cd app && ./install.sh
app create-owner             # prompts for email and password
app create-owner --generate
```

### With Docker instead

```sh
docker compose up -d
```

### Running by hand

```sh
export APP_KEY=$(python3 -c 'print(1)')
bin/app serve
```

## Commands

| Command | What |
|---|---|
| `./app.sh backup [file]` | back up |
| `./app.sh test` | run the tests |

## Backup

Nightly: `./app.sh backup`. Restore: `./app.sh restore <file>`.

## Tests

```console
$ python3 -m pip install pytest
$ python3 -m pytest acceptance/findings
collected 3 items
```

If you have pytest, `python -m pytest tests` runs everything; `pytest` alone works too.
""")
    ex = commands.extract(root)
    assert ex["install_section"] == "Install and start"
    assert [(e["cmd"], e["skip"]) for e in ex["quick"]] == [
        ("./install.sh", None),  # the clone is the fresh copy
        ("app create-owner", "interactive: it prompts for input"),
        ("app create-owner --generate", None),
    ]  # docker, by hand: left out
    assert [e["cmd"] for e in ex["backup"]] == ["./app.sh backup"]  # the runnable one
    assert [e["cmd"] for e in ex["tests"]] == [
        "./app.sh test",
        "python3 -m pytest acceptance/findings",
        "python -m pytest tests",
    ]
    (root / "README.md").write_text(
        (root / "README.md").read_text() + "\nAgain:\n\n    python -m pytest tests\n"
    )
    assert len(commands.extract(root)["tests"]) == 3  # each once
    run = next(e for e in ex["run"] if e["cmd"] == "bin/app serve")
    assert run["setup"] == ["export APP_KEY=$(python3 -c 'print(1)')"]
    recipes = commands.run_recipes(ex, {"start": "bin/app serve --port 9", "port": 9})
    assert recipes[0]["source"] == "the operational checks" and recipes[0]["port"] == 9
    for cmd, why in (
        ("sudo ./install.sh", "root"),
        ("systemctl --user start app", "systemd"),
        ("app backup FILE", "placeholder"),
        ("pip install -e .", "dependencies"),
        ("app restore x.db", "restore"),
    ):
        assert why in commands.skip_reason({"cmd": cmd}), cmd
    from repo_factory.v5.cmdrunner import assignment_only

    assert assignment_only("export A=$(python3 -c 'print(1 2)')") and assignment_only("A=1")
    assert not assignment_only("A=1 python3 app.py")


def test_a_step_redone_after_an_interruption_reuses_the_command_run(tmp_path, monkeypatch):
    calls = []

    def fake_run(tree, venv, phases, recipe, timeouts, wheels=None):
        calls.append(tuple(phases))
        return {"ran": True, "phases": list(phases), "extract": {}, "quick": [], "tests": []}

    monkeypatch.setattr(commands, "run", fake_run)
    from repo_factory.v5 import exam

    monkeypatch.setattr(
        exam,
        "run",
        lambda *a, **k: {"key": "k", "own": {"commands": [], "green": True, "ran": True}},
    )
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "README.md").write_text("# P\n")

    def run():
        return SimpleNamespace(
            root=tmp_path / "r1", rules={}, ready={"checks": {}}, venv=tmp_path / "venv"
        )

    first = readiness.run_commands(run(), tree, ("quick", "tests"), deliver=True)
    again = readiness.run_commands(run(), tree, ("quick", "tests"), deliver=True)  # a new process
    assert calls == [("quick",)] and again == first  # the tests: the exam record's
    (tree / "README.md").write_text("# P, changed\n")
    readiness.run_commands(run(), tree, ("quick", "tests"), deliver=True)
    assert len(calls) == 2  # a different tree runs again


def test_command_results_become_items():
    def res(quick=(), tests=(), restart=None, **ex):
        return {
            "ran": True,
            "quick": list(quick),
            "tests": list(tests),
            "restart": restart,
            "extract": {
                "readme": "README.md",
                "install_section": "Install",
                "quick": [],
                "skipped": [],
                "tests": [{"cmd": t["cmd"], "where": t["where"]} for t in tests],
                **ex,
            },
        }

    ok = {"cmd": "./install.sh", "where": "README.md:5", "status": "ok", "rc": 0}
    bad_test = {
        "cmd": "./app.sh test",
        "where": "README.md:9",
        "status": "failed",
        "rc": 1,
        "output": "ModuleNotFoundError: No module named '_helpers'\nFAILED (errors=3)",
    }
    good_test = {"cmd": "python -m pytest", "where": "README.md:10", "status": "ok", "rc": 0}
    it = commands.items(res([ok], [bad_test, good_test]), restart=False)
    assert it["commands"]["status"] == "green"
    assert it["tests_green"]["status"] == "red" and it["tests_green"]["fixable"]
    assert "`./app.sh test` (README.md:9) exits 1" in it["tests_green"]["why"]
    assert "_helpers" in it["tests_green"]["detail"]
    assert commands.items(res([ok], [good_test]), restart=False)["tests_green"]["status"] == "green"
    # a quick-start command that fails is red; one that only wanted input isn't
    failing = {**ok, "status": "failed", "rc": 2, "output": "install.sh: line 3: oops"}
    assert commands.items(res([failing], [good_test]))["commands"]["status"] == "red"
    asks = {
        "cmd": "app create-owner --generate",
        "where": "README.md:6",
        "status": "failed",
        "rc": 1,
        "output": "EOFError: EOF when reading a line",
    }
    assert commands.items(res([ok, asks], [good_test]))["commands"]["status"] == "amber"
    # not run at all: amber, never green, never sent
    no = commands.items({"ran": False, "why": "no bwrap"})
    assert {k: v["status"] for k, v in no.items()} == {
        "commands": "amber",
        "tests_green": "amber",
        "restart_now": "amber",
    }
    assert not any(v["fixable"] for v in no.values())


# -- 5. the immediate restart -----------------------------------------------------------------

SERVER = """import os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.lock")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class S(HTTPServer):
    allow_reuse_address = REUSE


if STALE_LOCK and os.path.exists(LOCK):
    sys.exit("another instance is running (app.lock)")
open(LOCK, "w").write(str(os.getpid()))
srv = S(("127.0.0.1", 8765), H)


def stop(*a):
    os.unlink(LOCK)
    sys.exit(0)


signal.signal(signal.SIGTERM, stop)
srv.serve_forever()
"""


def service(tmp_path, reuse=True, stale_lock=False) -> Path:
    root = tmp_path / ("p" + str(reuse) + str(stale_lock))
    root.mkdir()
    (root / "server.py").write_text(
        SERVER.replace("REUSE", str(reuse)).replace("STALE_LOCK", str(stale_lock))
    )
    (root / "README.md").write_text(
        "# App\n\n## Install and start\n\n```sh\npython3 server.py\n```\n"
    )
    return root


@SANDBOX
@pytest.mark.parametrize(
    "reuse,stale,status,which",
    [
        (True, False, "green", None),
        (False, False, "red", "after a graceful stop"),  # the round-2 webhook relay's bug
        (True, True, "red", "after a crash (SIGKILL)"),  # a lock a crash leaves behind
    ],
)
def test_the_restart_check_starts_again_at_once_after_a_stop_and_a_crash(
    tmp_path, reuse, stale, status, which
):
    from repo_factory.harness.venv import ensure_venv

    # ``python3 server.py`` is no start command by name: a server once its step's time is up
    res = commands.run(
        service(tmp_path, reuse, stale),
        ensure_venv([]),
        ("quick", "restart"),
        timeouts={"step_s": 4},
    )
    assert res["ran"] and res["quick"][0]["status"] == "serving"
    assert res["quick"][0]["ports"] == [8765]  # inside the sandbox only
    rs = res["restart"]
    assert rs["ran"] and rs["port"] == 8765 and rs["graceful"]["exited"]
    it = commands.items(res, True, tests=False)["restart_now"]
    assert it["status"] == status, it
    if which:
        assert which in it["why"] and it["fixable"]
        assert "SIGTERM" in it["detail"]
    else:
        assert rs["restart"]["ok"] and rs["crash"]["ok"]
        assert rs["restart"]["started_after_s"] < 2 and rs["crash"]["started_after_s"] < 2


def test_a_failed_restart_goes_to_repair_at_rank_2():
    run = SimpleNamespace(
        budget=Budget(None),
        rules={},
        store=SimpleNamespace(rules={}),
        ready={
            "security": {"findings": []},
            "checks": {
                "items": {
                    "ci": {"id": "ci", "title": "CI", "status": "red", "fixable": True},
                    "restart_now": {
                        "id": "restart_now",
                        "title": "Restart",
                        "status": "red",
                        "fixable": True,
                        "why": "exited with 1",
                        "detail": "- SIGTERM",
                    },
                    "bind_localhost": {
                        "id": "bind_localhost",
                        "title": "bind",
                        "status": "red",
                        "fixable": True,
                    },
                }
            },
        },
    )
    items = readiness.plan(run)
    assert [d["ref"] for d in items] == ["bind_localhost", "restart_now", "ci"]  # by rank
    d = items[1]
    assert readiness.basis(d) == "reliability"
    assert accept.rank({"kind": "scenario", "basis": "reliability"}) == 2
    md = readiness.item_md(d)
    assert "(readiness, reliability)" in md and "- SIGTERM" in md and "SIGKILL" in md
    assert (
        readiness.item_for(SimpleNamespace(ready={"sent": items}), "restart_now")["basis"]
        == "reliability"
    )
    # a session probe for the restart check is rank 2 as well
    assert "restart" in readiness.RELIABILITY


def test_the_checks_prompt_asks_for_the_immediate_restart_and_the_run_recipe():
    from repo_factory.v5.common import prompt_text

    p = prompt_text(POLICY, "readiness")
    for s in ("at once", "SIGKILL", "TIME_WAIT", '"run":', "in the foreground", "127.0.0.1"):
        assert s in p, s
    rep = prompt_text(POLICY, "readiness", "repair")
    assert "Every command the README documents must work exactly as written" in rep
    got = readiness.norm_checks({"run": {"start": "bin/app serve", "port": 8080}, "checks": []})
    assert got["run"]["start"] == "bin/app serve"
