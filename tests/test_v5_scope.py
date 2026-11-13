"""The scope stage and depth in verification (SCOPE.REPORT): an idea gets ``SCOPE.md`` before
the build, a spec passes through; flows of the definition of done are walked by the verifier,
and the missing or partial ones are ``incomplete`` findings sent to repair with ranks; the
completeness score; never a red suite at delivery; only existing settings described; the
harness runs scenarios where the verifier did (the lending run's q22); the UI. Offline: the
build and sessions through a scripted harness, every no-tools call through ``ScriptedLLM``."""

import json
import re
import shutil
from pathlib import Path

import pytest

from repo_factory.events import fold, read_events
from repo_factory.rules.llm import ScriptedLLM
from repo_factory.rules.model import Rule
from repo_factory.v5 import accept, pipeline, report, scope
from repo_factory.v5.exam import mounted

from . import test_v5_pipeline as base

FIXTURE = Path(__file__).parent / "fixtures" / "repo_task"
POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"
LENDING = (Path(__file__).parent / "fixtures" / "specs" / "lending.md").read_text()
LIBRARY = (
    "there is an uptick in libraries that need software to manage their inventory. "
    "create something that someone can easily install and start checking out books for "
    "their customers and keep track of their money"
)
FREELANCE = (
    "Freelancers keep losing track of hours and invoices across clients. Build "
    "something they can install and use from the terminal to log time per client and "
    "project, and send clean invoices at month end."
)
WEBHOOKS = (
    "Lots of indie developers need to receive webhooks from many SaaS tools and fan "
    "them out reliably to their own services. Build a self-hostable service for that."
)

SCOPE_MD = """# Scope: Shelfkeeper

A lending desk for a small library: books, members, loans and fees, run by its librarians.

## Users
- Librarian (staff login): runs the desk. The first-run admin is created by the setup command.

## Core flows
### F1: Install and first run
- Who: the operator
- Steps: 1. run the one install command 2. sign in as admin 3. see the demo books

### F2: Check out a book
- Who: librarian
- Steps: 1. find the member 2. scan the book 3. see the due date

### F3: Take a payment
- Who: librarian
- Steps: 1. open the member 2. enter the amount 3. see the new balance

## Features
### Must have (v1)
- M1: Catalogue and copies (F1, F2)
- M2: Fees and payments (F3)
### Later (not in v1)
- Mobile app: the desk is enough for v1

## Domain rules and defaults
- A loan lasts 21 days (default).

## Operator settings
| key | values | default | what it decides |
|---|---|---|---|
| `payment.allow_credit` | allowed, refused | refused | may a member pay more than they owe |
| not a key | a, b | a | prose |

## Money and data integrity
Amounts in integer cents.

## Install and run
One command.

## Non-functional basics
Staff log in.

## Definition of done
- [ ] F1: Install and first run — the admin signs in and sees the demo books
- [ ] F2: Check out a book — the loan shows with its due date
- [ ] F3: Take a payment — the balance drops by the amount
"""


# -- the heuristic -----------------------------------------------------------------------------


def test_the_heuristic_scopes_ideas_and_passes_specs_through():
    for idea in (LIBRARY, FREELANCE, WEBHOOKS):
        h = scope.classify(idea)
        assert h["verdict"] == "idea", h
        assert scope.should_call("auto", h["verdict"])
    h = scope.classify(LENDING)
    assert h["verdict"] == "spec" and h["numbers"] >= 10, h
    assert not scope.should_call("auto", "spec")
    # a short task that names its functions and files is a spec, not an idea
    assert scope.classify((FIXTURE / "spec.md").read_text())["verdict"] == "spec"
    # a longer input with few concrete details: the scope call decides (it may PASS)
    vague = " ".join(["The shop wants a better way to handle its customers and orders."] * 12)
    assert scope.classify(vague)["verdict"] == "borderline"
    assert scope.should_call("auto", "borderline") and scope.should_call("always", "spec")
    assert not scope.should_call("off", "idea")


def test_a_spec_passes_through_with_no_call_and_off_never_calls():
    llm = ScriptedLLM({"scope": SCOPE_MD})
    res = scope.run(llm, POLICY, LENDING, "auto")
    assert res["decision"] == "pass" and not res["called"] and llm.prompts == []
    assert "concrete details" in res["why"]
    res = scope.run(llm, POLICY, LIBRARY, "off")
    assert res["decision"] == "pass" and res["why"] == "scope: off" and llm.prompts == []


# -- the prompt's contract and the reply -----------------------------------------------------------


def test_the_scope_prompt_contract():
    text = scope.scope_prompt(POLICY, LIBRARY, "auto")
    assert LIBRARY in text and "<<" not in text
    for h in (
        "# Scope:",
        "## Users",
        "## Core flows",
        "### F1:",
        "## Features",
        "### Must have (v1)",
        "### Later (not in v1)",
        "## Domain rules and defaults",
        "## Operator settings",
        "## Money and data integrity",
        "## Install and run",
        "## Non-functional basics",
        "## Definition of done",
        "- [ ] F1:",
    ):
        assert h in text, h
    low = text.lower()
    for must in (
        "end to end",
        "exactly two",
        "(default)",
        "no tech-stack mandates",
        "one command",
        "seed or demo data",
        "admin",
        "authentication",
        "validation",
        "backup",
        "one long session",
        "f1 is always **install and first run**",
    ):
        assert must in low, must
    assert "PASS:" in text  # auto: the model may pass
    assert "PASS:" not in scope.scope_prompt(POLICY, LIBRARY, "always")


def test_the_reply_is_parsed_into_flows_must_haves_settings_and_done():
    r = scope.parse("```markdown\n" + SCOPE_MD + "```")
    assert r["decision"] == "scoped" and r["title"] == "Shelfkeeper"
    assert [f["id"] for f in r["flows"]] == ["F1", "F2", "F3"]
    assert [(f["id"], f["title"]) for f in r["dod"]] == [
        ("F1", "Install and first run"),
        ("F2", "Check out a book"),
        ("F3", "Take a payment"),
    ]
    assert r["dod"][1]["done_when"] == "the loan shows with its due date"
    assert (
        scope.flows_md(r["dod"][:1])
        == "- F1: Install and first run — done when: the admin signs in and sees the demo books"
    )
    assert [m["id"] for m in r["must"]] == ["M1", "M2"] and r["later"]
    assert r["settings"] == [
        {
            "key": "payment.allow_credit",
            "values": ["allowed", "refused"],
            "default": "refused",
            "means": "may a member pay more than they owe",
        }
    ]
    assert scope.parse("PASS: it already states the rules")["decision"] == "pass"
    assert scope.parse("Sure! Here you go.")["decision"] == "malformed"


def test_a_reply_without_a_definition_of_done_is_retried_once():
    replies = iter(["# Scope: X\n\n## Users\n- a\n", SCOPE_MD])
    llm = ScriptedLLM({"scope": lambda p, m: next(replies)})
    res = scope.run(llm, POLICY, LIBRARY, "auto")
    assert res["decision"] == "scoped" and len(llm.prompts) == 2
    assert "previous reply had no definition of done" in llm.prompts[1]
    llm = ScriptedLLM({"scope": "no idea"})
    res = scope.run(llm, POLICY, LIBRARY, "auto")
    assert res["decision"] == "malformed" and len(llm.prompts) == 2 and res["called"]


def test_the_build_prompt_is_the_idea_then_the_scope_then_the_vanilla_suffix():
    p = scope.build_prompt(LIBRARY, SCOPE_MD, pipeline.VANILLA_SUFFIX)
    assert p == (
        LIBRARY
        + "\n\nHere is the product scope for v1:\n\n"
        + SCOPE_MD.strip()
        + "\n\nWork in the current directory.\n"
    )
    # a spec that passed through: the vanilla prompt, byte for byte (§3.2)
    assert (
        scope.build_prompt(LENDING, None, pipeline.VANILLA_SUFFIX)
        == LENDING.rstrip() + "\n\nWork in the current directory.\n"
    )
    assert scope.spec_text(LIBRARY, SCOPE_MD).startswith(LIBRARY + "\n\nHere is the product")
    assert scope.spec_text(LENDING, None) == LENDING


# -- ranks, xfail, settings -------------------------------------------------------------------------


def test_incomplete_findings_rank_by_the_scope():
    assert accept.rank({"kind": "scenario", "basis": "scope_must"}) == 2
    assert accept.rank({"kind": "scenario", "basis": "incomplete"}) == 3
    assert accept.rank({"kind": "builder_test"}) == 4
    # a must-have flow outranks the builder's test; the pair panel's verdict is ignored
    assert accept.resolve("y_wins", 2, 4) == "keep_x"


def test_assumptions_and_report_describe_only_settings_that_exist():
    st = {"values": ["a", "b"], "default": "a", "scope": "global"}
    s = {
        "task": "t",
        "branch": "repo-factory/t",
        "costs": {},
        "cap": 11.0,
        "interpretations": [],
        "settings": [
            {
                "id": "s1",
                "status": "added",
                "question": "Q1?",
                "setting": {**st, "key": "fees.waive"},
            },
            {
                "id": "s2",
                "status": "exposed",
                "question": "Q2?",
                "setting": {**st, "key": "hold.claim"},
            },
            {
                "id": "s3",
                "status": "open",
                "question": "Q3?",
                "current": "b",
                "why": "value a fails x1",
                "setting": {**st, "key": "loan.renew"},
            },
            {
                "id": "s4",
                "status": "proposed",
                "question": "Q4?",
                "setting": {**st, "key": "pay.credit"},
            },
        ],
    }
    for text, head in (
        (report.render_assumptions(s), "### Not available yet"),
        (report.render_report(s), "### Not implemented"),
    ):
        have, gone = text.split(head)
        for k in ("fees.waive", "hold.claim"):
            assert k in have and k not in gone
        for k in ("loan.renew", "pay.credit"):
            assert k not in have and k in gone
    a = report.render_assumptions(s)
    assert "`FEES_WAIVE`" in a and "`HOLD_CLAIM`" in a
    assert "LOAN_RENEW" not in a and "PAY_CREDIT" not in a  # no switch to name
    assert "| `loan.renew` | a, b | `a` | Q3? |" in a and "x1" not in a  # plain: no ids
    r = report.render_report(s)
    assert "| `loan.renew` | a, b | `a` | b | Q3? | not working yet: value a fails x1 |" in r


# -- q22: a scenario runs where the verifier ran it --------------------------------------------


def test_a_scenario_finds_the_product_at_its_fixed_path_and_serves_on_loopback(tmp_path):
    # The lending run's q22 passed for the verifier and failed in the harness: it found the
    # repo three directories up from its own file, and the harness ran it one level
    # shallower. Now the product is at /work/product (REPO_FACTORY_PRODUCT_DIR) for the verifier and
    # the harness alike (EX-PLAN §3.1), and a server on 127.0.0.1 is allowed.
    from repo_factory.harness.isolation import isolation
    from repo_factory.v5 import exam

    from .exam_fixture import FakeRun

    if isolation() != "bwrap":
        pytest.skip("needs bubblewrap")
    code = tmp_path / "build"
    shutil.copytree(FIXTURE / "_fake_solution", code)
    run = FakeRun(tmp_path / "run", "stdlib_unittest_lib", skip=("c1", "c2", "x1"))
    r = Rule(
        id="q22",
        title="the start command serves",
        kind="edge",
        source="",
        expect="",
        code="import os, subprocess, sys, time, urllib.request, socket\n\n\n"
        "def test_q22():\n"
        "    root = os.environ['REPO_FACTORY_PRODUCT_DIR']\n"
        "    assert os.path.exists(os.path.join(root, 'textstats', 'core.py'))\n"
        "    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]"
        "; s.close()\n"
        "    p = subprocess.Popen([sys.executable, '-m', 'http.server', str(port), "
        "'--bind', '127.0.0.1'], cwd=root, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "    try:\n"
        "        for _ in range(100):\n"
        "            try:\n"
        "                body = urllib.request.urlopen(f'http://127.0.0.1:{port}/"
        "textstats/core.py').read(); break\n"
        "            except OSError:\n"
        "                time.sleep(0.05)\n"
        "        assert b'def ' in body\n"
        "    finally:\n"
        "        p.terminate(); p.wait()\n",
        source_ref="spec",
        origin={},
        status="active",
    )
    run.store.rules["q22"] = r
    out = exam.run(run, code, rules=[r])["scenarios"]
    assert out["q22"]["status"] == "passed", out["q22"]["detail"]


# -- a whole scoped build, offline --------------------------------------------------------------------

FLOW_SCEN = {
    "flow_F1": (
        "F1",
        "pass",
        "from _verify.driver import wc\n\n\ndef test_f1():\n    assert wc('a b') == 2\n",
    ),
    "flow_F2": (
        "F2",
        "fail",
        "from _verify.driver import top\n\n\ndef test_f2():\n"
        "    assert top('a a b c', 1) == ['a']\n",
    ),
    "flow_F3": (
        "F3",
        "pass",
        "from _verify.driver import wc\n\n\ndef test_f3():\n    assert wc('') == 0\n",
    ),
    "x1": (
        "none",
        "fail",
        "import textstats.core as core\n\n\ndef test_x1():\n"
        "    assert hasattr(core, 'char_histogram')\n",
    ),
    "env1": (
        "F3",
        "pass",
        "import os\n\n\ndef test_env1():\n"
        "    assert os.environ.get('ONLY_IN_THE_VERIFIERS_SHELL')\n",
    ),
}


class ScopedScripted(base.Scripted):
    """The verifier walks the flows: F1 and F3 work, F2 doesn't (the build's top_words bug,
    which the repair fixes), x1 is a missing feature on no flow (never fixed), env1 passes
    only in the verifier's own shell (the harness fails it everywhere)."""

    def run(self, prompt, workspace, timeout_s, **opts):
        ws = Path(workspace)
        s = opts.get("session") or {}
        if prompt.rstrip().endswith("Work in the current directory."):
            assert "Here is the product scope for v1:" in prompt and "### F2:" in prompt
            assert (ws / "SCOPE.md").read_text() == SCOPE_MD  # ships in the repo
            return super().run(prompt, workspace, timeout_s, **opts)
        if "You are verifying" in prompt:
            self.runs.append(("verify", opts))
            assert "Walk every flow below end to end" in prompt
            assert "- F2: Check out a book — done when:" in prompt
            assert (
                "Here is the product scope for v1:"
                in ((mounted(opts) or ws) / "_verify" / "spec.md").read_text()
            )
            v = (mounted(opts) or ws) / "_verify"
            (v / "driver.py").write_text(base.DRIVER)
            out = []
            for sid, (flow, status, code) in FLOW_SCEN.items():
                (v / "scenarios" / f"test_{sid}.py").write_text(
                    f"# rule: {sid}\n# claim: flow\n# flow: {flow}\n{code}"
                )
                out.append(
                    {
                        "id": sid,
                        "file": f"_verify/scenarios/test_{sid}.py",
                        "claim": "flow",
                        "status": status,
                        "scope": "local",
                        **({"class": "incomplete"} if status == "fail" else {}),
                    }
                )
            (v / "findings.json").write_text(
                json.dumps(
                    {
                        "scenarios": out,
                        "flows": [
                            {"id": "F1", "status": "works"},
                            {"id": "F2", "status": "partial"},
                            {"id": "F3", "status": "works"},
                        ],
                        "self_estimate": {"p_hidden": 0.8},
                    }
                )
            )
            return self.result(ws, s, 0.5)
        if "=== Findings ===" in prompt:
            first = not any(r[0] == "repair" for r in self.runs)
            self.runs.append(("repair", opts))
            assert "a stub, a dead control or placeholder data" in prompt  # x1, each round
            assert "env1" not in prompt  # not repaired
            core = ws / "textstats" / "core.py"
            if first:
                assert "Incomplete: flow F2 of SCOPE.md's definition of done" in prompt
                assert "2 incomplete flow(s) or feature(s)" in prompt
                ids = re.findall(r"^### (\w+):", prompt, re.M)
                core.write_text(core.read_text().replace("[:n + 1]", "[:n]"))
                base.git(ws, "add", "-A")
                base.git(ws, "commit", "-q", "-m", f"fix({','.join(ids)}): top_words returns n")
            return self.result(ws, s, 0.3)
        if "The builder has repaired" in prompt:
            self.runs.append(("reverify", opts))
            assert "Walk every flow of `SCOPE.md`'s definition of done again" in prompt
            return self.result(ws, s, 0.1)
        return super().run(prompt, workspace, timeout_s, **opts)  # the readiness stage


def scoped_llm():
    llm = base.llm()
    llm.handlers.update(scope=SCOPE_MD)
    return llm


def scoped_task(tmp_path):
    t = tmp_path / "task"
    shutil.copytree(FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    (t / "spec.md").write_text(LIBRARY + "\n")
    return t


def test_a_whole_scoped_build_offline(tmp_path):
    t = scoped_task(tmp_path)
    h = ScopedScripted()
    llm = scoped_llm()
    sol = tmp_path / "solution" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(base.BUGGY)
    h.fake.solution = sol.parent
    out = pipeline.build(
        t, den=tmp_path / "den", out=tmp_path / "out", harness=h, llm=llm, policy=POLICY
    )
    assert out["finished"]
    # the scope stage: one call, recorded; the build doesn't share the vanilla base
    sc = out["scope"]
    assert sc["decision"] == "scoped" and sc["called"] and sc["shared_base"] is False
    assert sc["heuristic"]["verdict"] == "idea" and len(sc["dod"]) == 3
    assert "dod_from" not in sc  # the scope's own, not drawn from the input
    assert sum(1 for p in llm.prompts if "task=scope" in p) == 1
    root = Path(out["den"]) / "v5" / "r1"
    assert (root / "SCOPE.md").read_text() == SCOPE_MD
    assert json.loads((root / "scope.json").read_text())["decision"] == "scoped"
    evs = read_events(Path(out["events"]))
    e = next(e for e in evs if e["type"] == "scope")
    assert (e["decision"], e["dod"], e["settings"], e["shared_base"]) == ("scoped", 3, 1, False)
    # claims read the idea plus SCOPE.md as the spec
    claims_prompt = next(p for p in llm.prompts if "task=claims" in p)
    assert LIBRARY in claims_prompt and "### F3: Take a payment" in claims_prompt
    # incomplete findings skip the panel and go to repair; environment goes nowhere
    assert not any("task=oracle" in p for p in llm.prompts)
    f = next(e for e in evs if e["type"] == "findings" and e["stage"] == "oracle")
    assert (f["incomplete"], f["environment"], f["confirmed"]) == (2, 1, 0)
    assert f["completeness"] == [2, 3]  # F2 broken; F3's env1 doesn't count against it
    fnd = json.loads(Path(out["findings"]).read_text())
    by = {s["id"]: s for s in fnd["scenarios"]}
    assert by["flow_F2"]["class"] == "incomplete" and by["x1"]["class"] == "incomplete"
    assert by["env1"]["class"] == "environment" and fnd["environment"] == [by["env1"]["rule"]]
    assert by["env1"]["rule"] not in fnd["defects"]
    # ranks: a definition-of-done flow is must-have (2); x1 is on no flow (3)
    from repo_factory.rules.store import RuleStore

    store = RuleStore(next((Path(out["den"]) / "cache" / "rules").glob("*/r1")))
    assert store.rules[by["flow_F2"]["rule"]].origin["basis"] == "scope_must"
    assert store.rules[by["x1"]["rule"]].origin["basis"] == "incomplete"
    # the repair fixed F2: completeness 3/3 on the delivered head; x1 stays open
    rounds = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"]
    assert rounds[0]["accepted"] and rounds[0]["fixed"] == 1
    assert out["completeness"]["done"] == 3 and out["completeness"]["total"] == 3
    assert [x["status"] for x in out["completeness"]["flows"]] == ["works"] * 3
    assert fold(evs)["v5"]["scope"]["decision"] == "scoped"
    # delivery: nothing of repo-factory's in the repo, SCOPE.md included (track LEAN: it stays in the
    # round, beside the copy moved out of the tree); the two still failing scenarios are open
    # items in the evidence (never marks in the tree)
    o = Path(out["out"])
    assert not (o / "SCOPE.md").exists()
    rd = Path(out["den"]) / "v5" / "r1"
    assert (rd / "SCOPE.md").read_text() == SCOPE_MD
    assert (rd / "process" / "SCOPE.md").is_file()
    assert any(e["type"] == "process_files" and "SCOPE.md" in e["moved"] for e in evs)
    assert not (o / "tests" / "acceptance").exists()
    ev = Path(out["den"]) / "v5" / "r1" / "exam" / "evidence"
    res = json.loads((ev / "results.json").read_text())
    failing = {k for k, v in res["scenarios"].items() if v["status"] != "passed"}
    assert failing == {by["x1"]["rule"], by["env1"]["rule"]}
    ready = (ev / "readiness.md").read_text()
    assert "Fails only in an isolated test sandbox" in ready and "Not complete yet: X1" in ready
    rep = (o / "REPORT.md").read_text()
    assert "**Completeness: 3 of 3 flows**" in rep and "| F2 | Check out a book | works |" in rep
    assert "[incomplete: F2]" in rep and "### Environment (not repaired)" in rep
    assert "## Tests at delivery" in rep and "2 open" in rep
    assert f"- {by['x1']['rule']} (incomplete): x1" in rep
    assert "Warning: the build's own test suite fails" not in rep
    assert "does not share the vanilla base build" in rep
    assert "SCOPE.md" in (ev / "assumptions.md").read_text()
    from repo_factory.progress import status_text

    text = status_text(fold(evs))
    assert "Scope: SCOPE.md written, 3 flow(s)" in text and "Completeness: 3 of 3" in text


def test_the_ui_shows_the_scope_stage_completeness_and_incomplete_findings(tmp_path):
    from repo_factory.ui import server
    from repo_factory.ui.v5view import view

    t = scoped_task(tmp_path)
    h = ScopedScripted()
    sol = tmp_path / "solution" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(base.BUGGY)
    h.fake.solution = sol.parent
    pipeline.build(
        t, den=t / ".den", out=t / "out", harness=h, llm=scoped_llm(), policy=POLICY, owner="queue"
    )
    st = fold(read_events(t / ".den" / "events.jsonl"))
    v = view(t, st)
    assert [x["stage"] for x in v["stages"]][:2] == ["scope", "build"]
    assert v["stages"][0]["status"] == "done"
    assert v["scope"]["decision"] == "scoped" and v["scope"]["file"]
    assert [(f["id"], f["status"]) for f in v["scope"]["flows"]] == [
        ("F1", "works"),
        ("F2", "works"),
        ("F3", "works"),
    ]
    assert v["completeness"] == {"done": 3, "total": 3}
    assert v["counts"]["incomplete"] == 2 and v["counts"]["environment"] == 1
    assert {d["id"] for d in v["incomplete"]} == {"flow_F2", "x1"}
    assert all(d["class"] == "incomplete" for d in v["incomplete"])
    app = server.App(tmp_path, "claude-code")
    assert app.text(t, "scope") == SCOPE_MD
    # a run without a scope stage (an older log) shows no scope column
    st2 = json.loads(json.dumps(st))
    st2["v5"]["stages"].pop("scope")
    st2["v5"]["planned"].remove("scope")
    assert [x["stage"] for x in view(t, st2)["stages"]][0] == "build"


def test_a_prebuilt_base_is_shared_only_when_the_input_passes_through(tmp_path):
    t = scoped_task(tmp_path)
    h = ScopedScripted()
    llm = scoped_llm()
    pb_ws = tmp_path / "pb"
    shutil.copytree(FIXTURE / "_fake_solution", pb_ws)
    from repo_factory.v5.types import Prebuilt

    pb = Prebuilt(
        workspace=pb_ws,
        session_id="S0",
        config_dir=tmp_path / "cfg",
        usd=1.0,
        wall_s=1.0,
        transcript=tmp_path / "t.jsonl",
    )
    out = pipeline.build(
        t,
        den=tmp_path / "den",
        out=tmp_path / "out",
        harness=h,
        llm=llm,
        policy=POLICY,
        prebuilt=pb,
        steps=2,
    )
    assert out["scope"]["decision"] == "pass" and "prebuilt" in out["scope"]["why"]
    assert out["scope"]["shared_base"] is True
    assert not any("task=scope" in p for p in llm.prompts) and h.runs == []


def test_a_run_saved_before_the_scope_stage_resumes_without_scoping(tmp_path):
    task = tmp_path / "task"
    shutil.copytree(FIXTURE, task)
    shutil.rmtree(task / "_fake_solution")
    h = base.Scripted()
    first = base.run_build(task, tmp_path, h, steps=3)
    st_path = Path(first["den"]) / "v5" / "r1" / "state.json"
    st = json.loads(st_path.read_text())
    st["done"] = [d for d in st["done"] if d != "scope"]  # as an older run saved it
    st.pop("scope", None)
    st_path.write_text(json.dumps(st))
    llm = ScriptedLLM({**base.llm().handlers, "scope": SCOPE_MD})
    out = pipeline.build(
        task, den=tmp_path / "den", harness=h, llm=llm, policy=POLICY, resume=1, steps=1
    )
    assert out["done_steps"][:3] == ["scope", "build", "claims"]
    assert not any("task=scope" in p for p in llm.prompts)


# -- EV7: a spec that passes through gets its definition of done from its own text -------------
PROSE_SPEC = (
    "A command-line tool that counts words, for people who edit text. It runs as "
    "`python3 -m wc FILE [--top N]` on Python 3.11 with the standard library only. "
    "`--top N` prints the N most common words, one per line; `--json` prints one JSON object "
    "instead. A missing FILE is an error that names it and exits with status 2. `--help` "
    "explains every option, and the README shows an example that runs as written.\n"
)
API_SPEC = (
    "A small HTTP service. `POST /items` creates an item and answers 201; "
    "`GET /items` lists the items; `DELETE /items/<id>` answers 204 or 404.\n"
)


def test_a_prose_spec_gets_one_flow_per_requirement_in_its_own_words():
    assert scope.classify(PROSE_SPEC)["verdict"] == "spec"
    dod = scope.spec_dod(PROSE_SPEC)
    assert [f["id"] for f in dod] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert dod[4]["done_when"] == (
        "A missing FILE is an error that names it and exits with status 2."
    )
    # "...; ..." splits only when each part names its own command
    assert dod[2]["done_when"].startswith("`--top N` prints") and dod[3]["done_when"].startswith(
        "`--json` prints"
    )
    assert dod[1]["title"] == "It runs as python3 -m wc FILE [--top N] …"
    # endpoints, one flow each
    api = scope.spec_dod(API_SPEC)
    assert [f["done_when"].split()[0] for f in api[1:]] == ["`POST", "`GET", "`DELETE"]


def test_numbered_requirements_or_acceptance_lines_win_over_prose():
    numbered = (
        "# tool\n\nIt must do these things:\n\n1. `add X` stores X.\n"
        "2. `list` prints every X, oldest first.\n3. `rm X` removes X or exits 1.\n"
    )
    assert [f["done_when"] for f in scope.spec_dod(numbered)] == [
        "`add X` stores X.",
        "`list` prints every X, oldest first.",
        "`rm X` removes X or exits 1.",
    ]
    accept = numbered + "\nAcceptance: `list` after `add a` prints `a`.\n"
    assert [f["done_when"] for f in scope.spec_dod(accept)] == ["`list` after `add a` prints `a`."]


def test_many_requirements_share_flows_and_nothing_to_draw_gives_none():
    many = "\n".join(f"{i}. `cmd{i}` prints line {i}." for i in range(1, 31))
    dod = scope.spec_dod(many)
    assert len(dod) == scope.SPEC_FLOWS
    assert "`cmd1` prints" in dod[0]["done_when"] and "`cmd30` prints" in dod[-1]["done_when"]
    assert scope.spec_dod("") == [] and scope.spec_dod("# Title\n\nHi there.\n") == []


class SpecScripted(base.Scripted):
    """``test_v5_pipeline``'s sessions; the verifier's prompt is kept, and it walks F1."""

    def run(self, prompt, workspace, timeout_s, **opts):
        out = super().run(prompt, workspace, timeout_s, **opts)
        if "You are verifying" in prompt:
            self.verifier_prompt = prompt
            (mounted(opts) / "_verify" / "scenarios" / "test_flow_F1.py").write_text(
                "# rule: word_count counts words\n# claim: flow\n# flow: F1\n"
                "from _verify.driver import wc\n\n\ndef test_flow_f1():\n"
                "    assert wc('a b') == 2\n"
            )
        return out


def test_a_spec_build_walks_its_own_requirements_and_is_not_held_for_completeness(tmp_path):
    from repo_factory.loop import gate

    task = tmp_path / "task"
    shutil.copytree(FIXTURE, task)
    shutil.rmtree(task / "_fake_solution")
    h = SpecScripted()
    out = base.run_build(task, tmp_path, h)
    sc = out["scope"]
    assert sc["decision"] == "pass" and not sc["called"] and sc["shared_base"] is True
    assert sc["dod_from"] == "spec" and [f["id"] for f in sc["dod"]] == ["F1", "F2"]
    assert "word_count(text)" in sc["dod"][0]["done_when"]
    # the verifier walks them, with the spec's section (not SCOPE.md's)
    assert "made from a spec, used as written" in h.verifier_prompt
    assert "SCOPE.md" not in h.verifier_prompt.split("=== The specification ===")[1]
    assert "- F2: Also add top_words(text, n) returning the n" in h.verifier_prompt
    comp = out["completeness"]
    assert (comp["done"], comp["total"]) == (1, 2)
    assert [x["status"] for x in comp["flows"]] == ["works", "unchecked"]
    root = Path(out["den"]) / "v5" / "r1"
    chk = gate.record_checks(root)["completeness"]
    assert chk["ok"] and "1/2 flows" in chk["detail"] and "F2" in chk["gap"]
    assert "no definition of done" not in json.dumps(gate.record_checks(root))
    assert "the spec's own 2 requirements" in (Path(out["out"]) / "REPORT.md").read_text()


def test_no_definition_of_done_is_a_gap_unless_nothing_was_measured(tmp_path):
    from repo_factory.loop import gate

    (tmp_path / "findings.json").write_text(json.dumps({}))
    (tmp_path / "readiness.json").write_text(
        json.dumps({"security": {"ran": True, "findings": []}})
    )
    c = gate.record_checks(tmp_path)["completeness"]
    assert c["ok"] and c["gap"] == gate.NO_DOD
    assert gate.gaps_of({"completeness": c}) == [f"completeness: {gate.NO_DOD}"]
    (tmp_path / "readiness.json").write_text(json.dumps({"security": {"ran": False}}))
    c = gate.record_checks(tmp_path)["completeness"]
    assert not c["ok"] and c["detail"] == gate.NOTHING_MEASURED


def test_an_underspecified_pitch_still_gets_the_scopes_definition_of_done():
    res = scope.run(scoped_llm(), POLICY, LIBRARY, "auto")
    assert res["decision"] == "scoped" and len(res["dod"]) == 3 and "dod_from" not in res
