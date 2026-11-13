"""The lean flow (track LEAN: Brief → Build → Harden → Check → Fix once → Publish) and the flow
as data (``stages.py``), offline: the build through ``FakeHarness``, the harden, security and
fix sessions scripted here; the README's commands, the product's tests and the probes really
run. Plus repo-factory's process files kept out of the delivered tree (``process.py``)."""

import json
import re
import shutil
from pathlib import Path

import pytest

from repo_factory.events import read_events
from repo_factory.loop import builds as loop_builds
from repo_factory.loop import gate
from repo_factory.resources import POLICIES
from repo_factory.v5 import lean, pipeline, process, stages
from repo_factory.v5.exam import mounted

from . import test_v5_pipeline as base
from . import v5_ready_fake as fake

POLICY = Path(__file__).resolve().parents[1] / "policies" / "lean"
HARDEN = "Last, write `.repo-factory/flows.json`"
FIX = "Its release checks found the\nproblems below"
FLOW_TESTS = (
    "from textstats.core import top_words, word_count\n\n\n"
    "def test_word_count():\n    assert word_count('a b c') == 3\n\n\n"
    "def test_top_words():\n    assert top_words('a a b c', 1) == ['a']\n"
)
PROBE = (
    "# rule: char_count refuses input that isn't text\n# expect: ValueError\n"
    "# source: security review\n# claim: security\nimport pytest\n"
    "from textstats.core import char_count\n\n\ndef test_sec_1():\n"
    "    with pytest.raises(ValueError):\n        char_count(None)\n"
)
FINDING = {
    "id": "s1",
    "title": "char_count crashes on input that isn't text",
    "severity": "high",
    "category": "hostile input",
    "where": "textstats/core.py:5",
    "repro": "char_count(None)",
    "observed": "TypeError",
    "expected": "ValueError",
    "probe": "_verify/scenarios/test_sec_1.py",
}


class Scripted(base.Scripted):
    """The lean sessions: harden (tests per flow, the docs, CI, its map of flows), the
    security probe (one high finding with a probe), the one fix (``fix``: what it does)."""

    def __init__(self, fix="all"):
        super().__init__()
        self.fix = fix
        self.prompts = {}

    def run(self, prompt, workspace, timeout_s, **opts):
        ws, s = Path(workspace), opts.get("session") or {}
        if HARDEN in prompt:
            self.runs.append(("harden", opts))
            self.prompts["harden"] = prompt
            (ws / "tests").mkdir(exist_ok=True)
            (ws / "tests" / "test_flows.py").write_text(FLOW_TESTS)
            fake.write_ready_files(ws)
            (ws / ".repo-factory").mkdir()
            (ws / ".repo-factory" / "flows.json").write_text(
                json.dumps(
                    {
                        "F1": ["tests/test_flows.py::test_word_count"],
                        "F2": ["tests/test_flows.py::test_top_words"],
                    }
                )
            )
            (ws / ".repo-factory" / "state.json").write_text("{}")  # never a round record
            return self.result(ws, s, 0.3)
        if fake.SECURITY in prompt:
            self.runs.append(("security", opts))
            v = (mounted(opts) or ws) / "_verify"
            (v / "scenarios" / "test_sec_1.py").write_text(PROBE)
            (v / "security.json").write_text(json.dumps(fake.security_report([FINDING])))
            return self.result(ws, s, 0.2)
        if FIX in prompt:
            self.runs.append(("fix", opts))
            self.prompts["fix"] = prompt
            core = ws / "textstats" / "core.py"
            t = core.read_text()
            if self.fix == "all":
                t = t.replace("[:n + 1]", "[:n]").replace(
                    "def char_count(text):\n    return len(text)",
                    "def char_count(text):\n    if not isinstance(text, str):\n"
                    "        raise ValueError('text must be a string')\n    return len(text)",
                )
            else:  # a careless fix: word_count off by one
                t = t.replace("return len(text.split())", "return len(text.split()) + 1")
            core.write_text(t)
            return self.result(ws, s, 0.4)
        if prompt.rstrip().endswith("Work in the current directory."):
            assert "What ships with the product" not in prompt  # harden's job, not build's
            assert not (ws / "SCOPE.md").exists()
            self.prompts.setdefault("build", prompt)
        return super().run(prompt, workspace, timeout_s, **opts)


def build(tmp, fix="all"):
    t = tmp / "task"
    shutil.copytree(base.FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    sol = tmp / "solution" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(base.BUGGY)
    h = Scripted(fix)
    h.fake.solution = sol.parent
    out = pipeline.build(
        t, den=t / ".den", out=tmp / "out", harness=h, llm=base.llm(), policy=POLICY
    )
    return h, out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("lean"))


def test_three_flows_are_stage_lists_and_lean_is_the_brief_build_harden_check_fix_publish():
    assert set(stages.FLOWS) == {"v5", "simple", "lean"}
    assert [s.name for s in stages.LEAN] == ["scope", "build", "harden", "check", "fix", "deliver"]
    h = next(s for s in stages.LEAN if s.name == "harden")
    assert h.kind == "prompt" and h.role == "build" and not h.holds and "flows.json" in h.outputs
    for flow in stages.FLOWS.values():
        for s in flow:
            d = s.to_dict()
            assert {
                "name",
                "inputs",
                "outputs",
                "role",
                "cap_usd",
                "timeout_s",
                "holds",
                "kind",
            } <= set(d)
            json.dumps(d)
    rules = pipeline.load_rules(POLICY)
    assert rules["stages"] == list(stages.library("lean")) and rules["pipeline"] == "v5"
    assert rules["readiness"]["security"]["max_turns"] == 60 and rules["harden"]["max_turns"] == 120
    from repo_factory.v5.common import prompt_home

    for name in ("scope", "build", "security", "study"):
        assert prompt_home(POLICY, name).resolve() == (POLICIES / "v5").resolve(), name
    for name in ("harden", "fix"):
        assert prompt_home(POLICY, name).resolve() == POLICY.resolve(), name


def test_a_stage_is_checked():
    with pytest.raises(ValueError, match="exactly one"):
        stages.Stage("x1", prompt="a", code="b")
    with pytest.raises(ValueError, match="lower-case"):
        stages.Stage("Bad Name", code="b")
    with pytest.raises(ValueError, match="cell"):
        stages.Stage("ok", code="b", cell="nowhere")


def test_a_whole_lean_build_offline(built):
    h, out = built
    assert [r[0] for r in h.runs] == ["build", "harden", "security", "fix"]
    assert out["failed"] is None and out["finished"]
    rd = Path(out["den"]) / "v5" / f"r{out['round']}"
    evs = list(read_events(Path(out["events"])))
    started = next(e for e in evs if e["type"] == "build_started")
    assert started["flow"] == "lean" and started["stages"] == list(stages.library("lean"))
    assert [s["name"] for s in started["flow_stages"]] == [s.name for s in stages.LEAN]
    done = [e["stage"] for e in evs if e["type"] == "v5_stage" and e["status"] == "done"]
    assert done == ["scope", "build", "harden", "check", "fix", "deliver"]
    # the harden prompt carries the definition of done; its map went to the round, and
    # nothing else of what it wrote under .repo-factory/ did
    assert "- F1:" in h.prompts["harden"] and "- F2:" in h.prompts["harden"]
    assert json.loads((rd / "flows.json").read_text())["F2"]
    assert json.loads((rd / "state.json").read_text())["flow"] == "lean"
    # Check held on a high finding, a failing flow and its red test command; one fix fixed all
    chk = json.loads((rd / lean.CHECK_FILE).read_text())
    first = {x["id"] for x in chk["measures"][0]["holds"]}
    assert {"security:S1", "flow:F2", "check:tests_green"} <= first
    assert chk["measures"][0]["flows"]["F1"]["status"] == "works"
    assert chk["fix"]["accepted"] and chk["fix"]["open"] == [] and chk["holds"] == []
    assert "security:S1" in h.prompts["fix"] or "char_count crashes" in h.prompts["fix"]
    fr = [e for e in evs if e["type"] == "fix_round" and e["status"] == "done"]
    assert len(fr) == 1 and fr[0]["accepted"]
    # what the gate reads: no open high, the tests green, every flow works
    checks = gate.record_checks(rd)
    assert checks["security"]["ok"] and checks["tests"]["ok"], checks
    assert checks["completeness"]["ok"] and "gap" not in checks["completeness"], checks
    assert checks["documents"]["ok"], checks
    assert out["completeness"]["done"] == out["completeness"]["total"] == 2


def test_harden_documents_every_interface_and_closes_the_reviews_holds(built):
    """LEAN3: the harden prompt asks for each interface in the README (LEAN2's FrontDesk
    documented its screens, not its API: 4/13 from the README alone), for what the security
    review held releases for (LEAN and LEAN2: unbounded bodies, open connections, failed
    sign-in rows), and for its working habits, with no turn cap."""
    h, _ = built
    p = h.prompts["harden"]
    for words in (
        "Document every interface the product exposes",
        "every HTTP endpoint",
        "`curl` example",
        "every command and its options",
        "every screen",
        "every file it reads or writes",
        "a limit on every request body",
        "a timeout on every connection",
        "someone who is not signed in",
        "Run the whole suite once at the end",
    ):
        assert words in p, words
    assert not re.search(r"\bturns?\b", p, re.I)


def test_the_delivered_tree_is_the_product_only(built):
    _, out = built
    import subprocess

    files = set(
        subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", out["branch"]],
            cwd=out["out"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )
    assert "tests/test_flows.py" in files and "README.md" in files
    assert not any(f.startswith((".repo-factory", "_verify")) or f in process.FILES for f in files)
    assert not any("acceptance" in f for f in files)


def test_a_fix_that_breaks_a_passing_check_is_undone(tmp_path):
    h, out = build(tmp_path, fix="careless")
    rd = Path(out["den"]) / "v5" / f"r{out['round']}"
    chk = json.loads((rd / lean.CHECK_FILE).read_text())
    assert not chk["fix"]["accepted"] and "flow:F1" in chk["fix"]["why"]
    # the tree went back: word_count is right, and the holds are the first check's
    core = (rd / "build" / "textstats" / "core.py").read_text()
    assert "len(text.split()) + 1" not in core
    assert {x["id"] for x in chk["holds"]} == {x["id"] for x in chk["measures"][0]["holds"]}
    assert not gate.record_checks(rd)["security"]["ok"]  # it holds at the gate


def test_a_policy_adds_a_prompt_stage_after_one_it_names(tmp_path):
    pol = tmp_path / "mine"
    shutil.copytree(POLICY, pol)
    (pol / "prompts" / "docs.md").write_text("<<spec>>\n\nWrite docs/USAGE.md.\n")
    rules = (
        (pol / "rules.yaml")
        .read_text()
        .replace(
            "inherits: v5",
            "inherits: v5\nadd_stages:\n  - {name: docs, prompt: docs, after: harden}",
        )
    )
    (pol / "rules.yaml").write_text(rules)
    import yaml

    r = yaml.safe_load(rules)
    assert r["add_stages"][0]["after"] == "harden"
    # the rules.yaml's inherits needs repo_factory's own v5: point at it
    got = pipeline.load_rules(pol)
    assert got["stages"][-1] == "docs" and got["added"][0]["after"] == "harden"
    fake_run = type("X", (), {"rules": got, "FLOW": "lean"})()
    flow = pipeline.Run.flow_stages(fake_run)
    assert [s.name for s in flow] == ["scope", "build", "harden", "docs", "check", "fix", "deliver"]
    # BSD's record fields: the public ids, an added stage's anchor and owner
    d = {s.name: s.to_dict() for s in flow}
    assert [x["id"] for x in d.values()] == [
        "brief",
        "build",
        "harden",
        "docs",
        "check",
        "fix",
        "publish",
    ]
    assert d["docs"]["anchor"] == "after:harden" and not d["docs"]["core"]
    assert d["docs"]["on_fail"] == "warn" and d["check"]["on_fail"] == "hold"
    assert d["scope"]["owner"] == "core" and d["scope"]["anchor"] is None
    # after: takes a public id too
    first = stages.added({"name": "notes", "after": "brief"}, {"notes"})
    assert [s.name for s in stages.with_added(stages.LEAN, [first])][:2] == ["scope", "notes"]
    for bad, msg in (
        ({"name": "docs", "prompt": "nope", "after": "harden"}, "no prompts"),
        ({"name": "docs", "after": "nowhere"}, "no stage"),
        ({"name": "docs", "code": "do_build", "after": "build"}, "unknown key"),
    ):
        (pol / "rules.yaml").write_text(
            rules.split("add_stages:")[0] + "add_stages:\n  - " + json.dumps(bad) + "\n"
        )
        with pytest.raises(ValueError, match=msg):
            pipeline.load_rules(pol)


# -- process files ---------------------------------------------------------------------------
def test_process_files_move_to_the_round_and_the_products_own_stay(tmp_path):
    t = tmp_path / "tree"
    (t / "tests" / "acceptance" / "_helpers").mkdir(parents=True)
    (t / "tests" / "acceptance" / "conftest.py").write_text(
        "# run: python -m pytest tests/acceptance\n"
    )
    (t / "tests" / "acceptance" / "test_one.py").write_text("def test_x(): pass\n")
    (t / "tests" / "test_own.py").write_text("def test_y(): pass\n")
    (t / "SCOPE.md").write_text("# Scope: X\n")
    (t / "READINESS.md").write_text("# Readiness\n\nX on the way to production.\n")
    (t / "SECURITY.md").write_text("# Security policy\n\nReport problems privately.\n")
    (t / ".repo-factory").mkdir()
    (t / ".repo-factory" / "flows.json").write_text("{}")
    (t / "README.md").write_text(
        "# X\n\n```\npython -m pytest tests/acceptance\npython -m pytest tests\n```\n"
    )
    moved = process.strip(t, tmp_path / "round")
    assert set(moved) == {"SCOPE.md", ".repo-factory", "tests/acceptance", "READINESS.md"}
    assert (t / "SECURITY.md").is_file() and (t / "tests" / "test_own.py").is_file()
    assert (tmp_path / "round" / "process" / "tests" / "acceptance" / "test_one.py").is_file()
    assert "tests/acceptance" not in (t / "README.md").read_text()
    assert "pytest tests" in (t / "README.md").read_text()
    assert process.found(t) == []


def test_collect_takes_only_the_declared_outputs(tmp_path):
    ws, rd = tmp_path / "ws", tmp_path / "round"
    (ws / ".repo-factory").mkdir(parents=True)
    rd.mkdir()
    (rd / "state.json").write_text('{"keep": 1}')
    (ws / ".repo-factory" / "flows.json").write_text("{}")
    (ws / ".repo-factory" / "state.json").write_text("{}")
    assert process.collect(ws, rd, ["flows.json"]) == ["flows.json"]
    assert json.loads((rd / "state.json").read_text()) == {"keep": 1}
    assert not (ws / ".repo-factory").exists()


# -- the checks' pieces ------------------------------------------------------------------------
def test_bind_item_reads_the_quick_starts_addresses():
    def res(*addrs):
        return {"quick": [{"status": "serving", "ports": [8080], "addrs": {"8080": list(addrs)}}]}

    assert lean.bind_item(res("127.0.0.1"))["status"] == "green"
    assert lean.bind_item(res("::1", "127.0.0.1"))["status"] == "green"
    red = lean.bind_item(res("0.0.0.0"))
    assert red["status"] == "red" and "0.0.0.0" in red["why"]
    assert lean.bind_item({"quick": [{"status": "ok"}]}) is None  # a CLI
    assert lean.bind_item({"quick": [{"status": "serving", "ports": [1]}]}) is None


def test_proc_net_addresses_decode():
    from repo_factory.v5.cmdrunner import _addr

    assert _addr("0100007F") == "127.0.0.1" and _addr("00000000") == "0.0.0.0"
    assert _addr("00000000000000000000000001000000") == "::1"
    assert _addr("00000000000000000000000000000000") == "::"


def test_flow_statuses_from_the_map_and_the_own_tests():
    tests = {
        "tests/test_a.py::test_one": "passed",
        "tests/test_a.py::test_two": "failed",
        "tests/test_b.py::TestX::test_three": "passed",
    }
    dod = [
        {"id": "F1", "title": "a"},
        {"id": "F2", "title": "b"},
        {"id": "F3", "title": "c"},
        {"id": "F4", "title": "d"},
    ]
    got = lean.flow_statuses(
        dod,
        {"F1": ["tests/test_a.py::test_one"], "F2": ["tests/test_a.py"], "F3": ["test_three"]},
        tests,
    )
    assert {k: v["status"] for k, v in got.items()} == {
        "F1": "works",
        "F2": "broken",
        "F3": "works",
        "F4": "unchecked",
    }
    # unittest classes: the harness records file::test (no class); parameters too
    tests = {
        "tests/test_e2e.py::test_first_run": "passed",
        "tests/test_e2e.py::test_add[a]": "passed",
        "tests/test_e2e.py::test_add[b]": "passed",
        "tests/test_unit.py::test_init": "failed",
        "tests/test_other.py::test_first_run": "failed",
    }
    got = lean.flow_statuses(
        dod[:3],
        {
            "F1": ["tests/test_e2e.py::F1FirstRun::test_first_run"],
            "F2": ["./tests/test_e2e.py::AddTests::test_add"],
            "F3": ["tests/test_unit.py::InitTests"],
        },
        tests,
    )
    assert got["F1"] == {"status": "works", "tests": ["tests/test_e2e.py::test_first_run"]}
    assert got["F2"]["status"] == "works" and len(got["F2"]["tests"]) == 2
    assert got["F3"] == {"status": "broken", "tests": ["tests/test_unit.py::test_init"]}


# -- the loop and the settings ---------------------------------------------------------------
def test_build_flow_lean_picks_the_lean_policy(tmp_path):
    b = {"policy": str(POLICIES / "v5"), "flow": "lean"}
    assert loop_builds.policy_of(b) == POLICIES / "lean"
    assert loop_builds.flow_of(["brb", "build", "t", "--policy", str(POLICIES / "lean")]) == "lean"
    mine = {"policy": str(tmp_path), "flow": "lean"}
    assert loop_builds.policy_of(mine) == tmp_path  # a policy set by hand wins
    from repo_factory.settings import schema, write

    assert "lean" in write.RULES["build.flow"][1]
    idle = schema.inert({"build": {"flow": "lean", "auto_repair": 3, "models": {"claims": "x"}}})
    assert {p["key"] for p in idle} == {"build.auto_repair", "build.models.claims"}
    assert "build.flow: lean" in schema.describe(idle)


def test_lean_model_roles_map_todays(monkeypatch):
    monkeypatch.setenv(
        pipeline.MODELS_ENV,
        json.dumps({"build": "m-build", "verify": "m-check", "repair": "m-fix", "claims": "m-x"}),
    )
    got = pipeline.load_rules(POLICY)
    assert got["build"]["model"] == got["harden"]["model"] == "m-build"
    assert got["readiness"]["security"]["model"] == "m-check" and got["fix"]["model"] == "m-fix"


# -- the brief's two rules and the build's stopping rule (LEAN2) -------------------------------
def test_the_lean_brief_adds_ev8_s_two_rules_and_v5_s_prompt_is_unchanged():
    from types import SimpleNamespace

    from repo_factory.v5 import scope

    idea = "Build something to keep track of every plot and who has it."
    v5 = POLICIES / "v5"
    # v5: the run adds nothing, so its scope prompt is the same bytes as before
    assert pipeline.Run.scope_rules(None) == ""
    assert scope.scope_prompt(v5, idea, "auto", "") == scope.scope_prompt(v5, idea, "auto")
    rules = lean.LeanRun.scope_rules(
        SimpleNamespace(
            policy=POLICY,
            own_prompt=lambda n: lean.LeanRun.own_prompt(SimpleNamespace(policy=POLICY), n),
        )
    )
    assert "own words are the definition of done" in rules
    assert "Mint nothing the pitch didn't ask for" in rules
    text = scope.scope_prompt(POLICY, idea, "auto", rules)
    # the template is v5's; the rules come after its own rules, before the reply line
    base = scope.scope_prompt(v5, idea, "auto")
    assert text.replace(rules.strip() + "\n\n", "") == base
    assert (
        text.index("=== Rules ===")
        < text.index("own words are the definition")
        < text.index(scope.REPLY)
    )


def test_a_scoped_lean_build_gets_its_stopping_rule_and_a_spec_the_vanilla_prompt(built):
    from types import SimpleNamespace

    ns = SimpleNamespace(idea="Build a garden plot tracker.", policy=POLICY)
    ns.own_prompt = lambda n: lean.LeanRun.own_prompt(ns, n)
    md = "# Scope: Plots\n\n## Definition of done\n- [ ] F1: Install and first run — it starts\n"
    text = lean.LeanRun.build_text(ns, md)
    assert "When to stop: the Definition of done above is the finish line" in text
    assert (
        text.index("# Scope: Plots")
        < text.index("When to stop")
        < text.rindex(pipeline.VANILLA_SUFFIX)
    )
    # a spec that passes through (this fixture) is built from the vanilla prompt, no rule:
    # no turn cap either, the build's own max_turns stays as it was
    h, _ = built
    assert "When to stop" not in h.prompts["build"]
    assert pipeline.load_rules(POLICY)["build"]["max_turns"] == 250
