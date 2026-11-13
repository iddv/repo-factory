"""Build v5's stage library, piece by piece (repo_factory/v5/; docs/design/build-v5.md §3)."""

import json
import re
from pathlib import Path

import pytest
import yaml

from repo_factory.rules.llm import ScriptedLLM
from repo_factory.rules.model import Rule
from repo_factory.rules.store import RuleStore
from repo_factory.v5 import claims, oracle, pipeline, report, verify
from repo_factory.v5.common import TYPICAL_USD, Budget, BudgetExceeded, prompt_text, render

POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"


# -- policy data ------------------------------------------------------------------------------


def test_rules_yaml_is_the_design_and_its_stage_list_is_checked(tmp_path):
    r = pipeline.load_rules(POLICY)
    # no global cap by default (the owner's standing rule); the cost trims of V5-POLISH
    assert r["pipeline"] == "v5" and r["max_usd"] is None
    # one claim sample: the second fed only p_hidden's capture-recapture term (AU1 #5)
    assert r["claims"]["samples"] == 1 and "samples_scoped" not in r["claims"]
    assert r["repair"]["resume"] == "auto" and r["repair"]["warm_minutes"] == 30
    # keys nothing read (AU1 #5)
    assert not {"regenerate", "stop"} & set(r) and "rounds" not in r["repair"]
    assert r["stages"] == [
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
    assert "mutants" not in r  # the stage is gone (AU1 #4)
    # spend is uncapped by default: no stage cap and no session max_usd (every one optional)
    assert r["build"]["stage_caps"] == {} and r["build"]["max_usd"] is None
    assert (
        r["verify"]["max_usd"] is None
        and r["repair"]["max_usd"] is None
        and r["repair"]["fixup_usd"] is None
    )
    assert (
        r["readiness"]["security"]["max_usd"] is None
        and r["readiness"]["checks"]["max_usd"] is None
        and r["readiness"]["repair"]["max_usd"] is None
    )
    # what stops a runaway session instead: turns and wall clock, per stage
    assert (r["build"]["max_turns"], r["build"]["timeout_s"]) == (250, 7200)
    assert (r["verify"]["max_turns"], r["verify"]["timeout_s"]) == (60, 1800)
    assert (r["repair"]["max_turns"], r["repair"]["timeout_s"]) == (60, 1800)
    assert r["scope"] == "auto"
    assert r["interpret"] == {"mode": "auto", "max_questions": 3}  # Addendum A: no fork
    assert r["verify"]["held_fraction"] == 0.3
    bad = tmp_path / "p"
    bad.mkdir()
    (bad / "rules.yaml").write_text(yaml.safe_dump({**r, "stages": ["build", "magic", "deliver"]}))
    with pytest.raises(ValueError, match="magic"):
        pipeline.load_rules(bad)
    (bad / "rules.yaml").write_text(yaml.safe_dump({**r, "stages": ["deliver", "claims", "build"]}))
    assert pipeline.load_rules(bad)["stages"] == ["build", "claims", "deliver"]
    # an older policy that still lists the retired mutants stage loads without it
    (bad / "rules.yaml").write_text(
        yaml.safe_dump(
            {
                **r,
                "stages": ["build", "verify", "mutants", "deliver"],
                "mutants": {"n": 14, "model": "claude-sonnet-5", "survivor_round": False},
            }
        )
    )
    assert pipeline.load_rules(bad)["stages"] == ["build", "verify", "deliver"]
    assert yaml.safe_load((POLICY / "lessons.yaml").read_text()) == {
        "version": 0,
        "cap": 20,
        "lessons": [],
    }


PROMPTS = [
    ("claims", None),
    ("claims", "dedupe"),
    ("verifier", None),
    ("reverify", None),
    ("reverify", "settings"),
    ("oracle", None),
    ("pair", None),
    ("repair", None),
    ("repair", "fixup"),
    ("repair", "settings"),
    ("repair", "fresh"),
    ("repair", "undo"),
    ("postmortem", None),
]


@pytest.mark.parametrize("name,section", PROMPTS)
def test_every_prompt_loads_and_its_json_contract_parses(name, section):
    text = prompt_text(POLICY, name, section)
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        json.loads(block)  # the example contract is valid JSON
    assert "<!-- section" not in text
    filled = render(text, **{k: "X" for k in re.findall(r"<<(\w+)>>", text)})
    assert "<<" not in filled


def test_the_verifier_prompt_carries_the_hand_reproduction_rule():
    text = prompt_text(POLICY, "verifier")
    assert "reproduced the behaviour by hand" in text and "errors in setup is your bug" in text
    assert "_verify/findings.json" in text and "# claim:" in text


def test_the_postmortem_prompt_is_section_5_3s_contract():
    text = prompt_text(POLICY, "postmortem")
    obj = json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))
    assert set(obj["summary"]) == {
        "no_claim",
        "claim_wrong_expectation",
        "retired_by_oracle",
        "passed_wrongly",
        "found_not_fixed",
        "repair_regression",
        "interpretation",
    }
    f = obj["failures"][0]
    assert set(f) == {"row", "miss_type", "existing_lesson", "new_lesson", "policy_note"}
    assert "at most three" in text and "240 characters" in text


# -- claims -----------------------------------------------------------------------------------


def C(s, e, basis="spec_implied", lessons=()):
    return {
        "situation": s,
        "action": "act",
        "expect": e,
        "basis": basis,
        "quote": "",
        "kind": "requirement",
        "lessons": list(lessons),
    }


def test_claims_merge_keeps_sets_and_renumbers():
    s1 = {
        "claims": [C("a", "1"), C("b", "2", lessons=["L1"])],
        "ambiguities": [
            {
                "question": "Q?",
                "literal": "x",
                "expected": "y",
                "recommend": "expected",
                "stakes": "high",
            }
        ],
    }
    s2 = {
        "claims": [C("A", "1", basis="spec_explicit"), C("c", "3"), C("bee", "2", lessons=["L2"])],
        "ambiguities": [],
    }
    llm = ScriptedLLM({"dedupe": json.dumps({"claims": [["1:c2", "2:c3"]]})})
    m = claims.merge([s1, s2], llm, POLICY)
    assert m["dedupe"] == "llm"
    got = {c["situation"]: (c["id"], c["sets"], c["basis"], c["lessons"]) for c in m["claims"]}
    assert got == {
        "a": ("c1", [1, 2], "spec_explicit", []),  # same text: merged anyway
        "b": ("c2", [1, 2], "spec_implied", ["L1", "L2"]),
        "c": ("c3", [2], "spec_implied", []),
    }
    assert m["ambiguities"][0]["id"] == "a1" and m["ambiguities"][0]["sets"] == [1]


def test_claims_run_two_samples_and_turn_silent_claims_into_ambiguities():
    out = json.dumps(
        {
            "claims": [C("x", "y"), C("z", "w", basis="silent")],
            "ambiguities": [{"question": "Q?", "stakes": "high"}],
        }
    )
    llm = ScriptedLLM({"claims": out, "dedupe": "{}"})
    m = claims.run(llm, POLICY, "spec", [], {"samples": 2, "max_claims": 60, "max_ambiguities": 8})
    assert [c["sets"] for c in m["claims"]] == [[1, 2]]
    assert len(m["ambiguities"]) == 2 and m["dedupe"] == "text"
    assert sum(1 for p in llm.prompts if p.startswith("[factory-rules task=claims]")) == 2


def test_lessons_born_on_the_pack_are_never_injected(tmp_path):
    p = tmp_path / "lessons.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "lessons": [
                    {
                        "id": "L1",
                        "text": "t1",
                        "status": "active",
                        "born": {"pack": "lending"},
                        "hit_rate": 0.9,
                        "applies_to": "claims",
                        "stats": {"caught": 9, "exposures": 10},
                    },
                    {
                        "id": "L2",
                        "text": "t2",
                        "status": "probation",
                        "born": {"pack": "jobq"},
                        "hit_rate": 0.2,
                        "applies_to": "verifier",
                    },
                    {
                        "id": "L3",
                        "text": "t3",
                        "status": "active",
                        "born": {"pack": "jobq"},
                        "hit_rate": 0.5,
                        "applies_to": "claims",
                        "stats": {"caught": 3, "exposures": 6},
                    },
                    {"id": "L4", "text": "t4", "status": "retired", "born": {"pack": "x"}},
                ],
            }
        )
    )
    got = claims.load_lessons(p, "lending")
    assert [x["id"] for x in got] == ["L3", "L2"]
    assert [x["id"] for x in claims.load_lessons(p, None)] == ["L1", "L3", "L2"]
    assert "L3 (found a defect in 3 of 6 builds): t3" in claims.lessons_md(got, "claims")
    assert claims.lessons_md(got, "verifier").count("- L2") == 1


# -- the held split ---------------------------------------------------------------------------


def rule(i):
    return Rule(
        id="",
        title=f"r{i}",
        kind="requirement",
        source="",
        code=f"def test_{i}():\n    assert {i} == {i}\n",
        origin={"mode": "v5", "scenario": f"c{i}"},
    )


def test_the_held_split_never_exports_a_held_scenario(tmp_path):
    st = RuleStore(tmp_path / "store")
    ids = [st.add(rule(i), visibility="visible").id for i in range(40)]
    for i in ids:
        st.set_status(i, "active", "validated", "t")
    seen = []
    st.on_change = lambda r, old, new, why: seen.append((r.id, r.visibility))
    held = verify.held_split(st, ids[:30], 0.3)  # only passing ones are eligible
    assert seen == [(i, "held") for i in held]  # rule_status: the fold counts them
    assert 3 <= len(held) <= 18 and set(held) <= set(ids[:30])
    assert all(st.rules[i].visibility == "visible" for i in ids[30:])
    # the exam a repair session sees (exam/visible): never a held scenario
    from repo_factory.v5 import exam

    vis = exam.write_exam(tmp_path / "visible", st.active("visible"), None)
    exported = {p.name for p in (vis / "_verify" / "scenarios").glob("test_*.py")}
    assert len(exported) == 40 - len(held)
    assert not exported & {st.rules[i].test_file for i in held}
    with pytest.raises(ValueError):
        st.export(tmp_path / "x", "visible", [st.rules[held[0]]])
    # a held scenario that regressed is promoted and replaced
    new = verify.replace_held(st, held[0], ids[:30])
    assert st.rules[held[0]].visibility == "visible" and st.rules[new].visibility == "held"
    # the evidence: every active scenario, held too (the build is over)
    full = exam.write_exam(tmp_path / "all", st.active(), None)
    assert len(list((full / "_verify" / "scenarios").glob("test_*.py"))) == 40


def test_scenarios_load_from_the_verifier_files(tmp_path):
    ws = (
        verify.prepare(
            tmp_path / "src",
            tmp_path / "v",
            "spec",
            {
                "claims": [
                    {
                        "id": "c1",
                        "situation": "s",
                        "action": "a",
                        "expect": "e",
                        "quote": "q",
                        "kind": "interaction",
                        "sets": [1, 2],
                        "basis": "spec_explicit",
                    }
                ],
                "ambiguities": [],
            },
        )
        if (tmp_path / "src").mkdir() is None
        else None
    )
    (ws / "_verify" / "scenarios" / "test_c1.py").write_text(
        "# rule: the rule\n# claim: c1\ndef test_c1():\n    assert True\n"
    )
    (ws / "_verify" / "scenarios" / "test_x1.py").write_text(
        "# rule: found by probing\n# claim: explore\ndef test_x1():\n    assert True\n"
    )
    (ws / "_verify" / "findings.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "c1",
                        "file": "_verify/scenarios/test_c1.py",
                        "status": "fail",
                        "claim": "c1",
                        "repro": "curl",
                        "observed": "o",
                        "scope": "local",
                    }
                ],
                "blocked": [],
            }
        )
    )
    f = verify.load_findings(ws)
    st = RuleStore(tmp_path / "store")
    cl = json.loads((ws / "_verify" / "claims.json").read_text())
    rs = verify.import_scenarios(st, ws, f, cl)
    assert [(r.kind, r.origin["claim"], r.origin.get("repro")) for r in rs] == [
        ("interaction", "c1", "curl"),
        ("edge", "explore", None),
    ]
    assert rs[0].origin["sets"] == [1, 2] and rs[0].source == "q" and rs[0].expect == "e"
    assert verify.import_scenarios(st, ws, f, cl) == []  # idempotent
    (ws / "_verify" / "scenarios" / "test_x1.py").write_text(
        "# claim: explore\ndef test_x1():\n    assert 1\n"
    )
    assert [r.version for r in verify.import_scenarios(st, ws, f, cl)] == [2]
    assert verify.load_findings(tmp_path)["scenarios"] == []  # missing: empty, no crash


# -- the budget --------------------------------------------------------------------------------


def test_budget_caps_stages_and_the_reserve_rule():
    # stage caps set (the old fixed ones, now a setting): they hold as before
    b = Budget(11.0, stage_caps=TYPICAL_USD)
    b.extra = {"build": 3.7, "claims": 0.6, "verify": 2.4}
    assert b.spent() == pytest.approx(6.7)
    assert b.allows(2.5) and not b.allows(4.5)  # stage 6's reserve
    assert b.session_cap("verify", 2.5) == pytest.approx(0.1)  # what the stage has left
    assert b.session_cap("repair", 2.5) == 2.5
    b.extra["claims"] = 1.0
    with pytest.raises(BudgetExceeded, match="stage cap"):
        b.check("claims")
    b.extra["build"] = 7.7
    with pytest.raises(BudgetExceeded, match="build's cap"):
        b.check("oracle")
    assert b.hit == ["claims", "oracle"]
    assert b.session_cap("repair", 2.5) == 0.0


def test_no_tools_calls_stop_at_the_cap():
    b = Budget(1.0)
    b.extra = {"build": 1.0}
    llm = ScriptedLLM({"oracle": "{}"})
    with pytest.raises(BudgetExceeded):
        oracle.panel(llm, POLICY, "spec", {"q1": {"situation": "s", "expect": "e"}}, budget=b)
    assert llm.calls == 0


# -- the panel ---------------------------------------------------------------------------------


def votes(*vs):
    it = iter(vs)
    return ScriptedLLM(
        {
            "oracle": lambda p, m: json.dumps({"results": next(it)}),
            "pair": lambda p, m: json.dumps(next(it)),
            "interpret": lambda p, m: json.dumps({"results": next(it)}),
        }
    )


def test_panel_classes():
    R, N, S = ({"vote": v} for v in ("required", "not_required", "silent"))
    llm = votes(
        {"a": R, "b": N, "c": R, "d": R}, {"a": R, "b": N, "c": S, "d": N}, {"a": R, "b": R, "c": N}
    )  # d: one vote missing
    out = oracle.panel(llm, POLICY, "spec", {k: {"situation": k, "expect": "e"} for k in "abcd"})
    assert {k: v["class"] for k, v in out.items()} == {
        "a": "defect",
        "b": "retired",
        "c": "interpretation",
        "d": "interpretation",
    }
    assert out["a"]["unanimous"] and out["d"]["votes"] == ["required", "not_required", "silent"]
    # the panel sees prose, never code
    p = llm.prompts[0]
    assert "### a\nSituation: a\nExpected: e" in p and "def test" not in p


def test_pair_majority_and_its_binary_policy():
    llm = votes({"verdict": "compatible"}, {"verdict": "x_wins"}, {"verdict": "compatible"})
    out = oracle.pair(llm, POLICY, "s", {"prose": "X"}, {"prose": "Y"})
    assert out["verdict"] == "compatible" and out["setting"] is None
    pol = {"key": "Fees.Count Mode", "values": ["started", "full"], "claim_value": "started"}
    llm = votes(
        {"verdict": "compatible"},
        {"verdict": "x_wins", "policy": pol},
        {"verdict": "nonsense", "policy": pol},
    )
    out = oracle.pair(llm, POLICY, "s", {"prose": "X"}, {"prose": "Y"})
    assert out["verdict"] == "undecidable"
    assert (out["setting"]["key"], out["setting"]["claim_value"]) == ("fees.count_mode", "started")
    assert not hasattr(oracle, "interpret")  # Addendum A: no reading vote


# -- report ------------------------------------------------------------------------------------


def test_report_puts_what_was_checked_first_and_lists_cost_by_stage():
    s = {
        "task": "t",
        "branch": "repo-factory/t",
        "title": "T",
        "cap": 11.0,
        "stop": "done",
        "checked": {
            "claims": 10,
            "found": 2,
            "fixed": 1,
            "open": 1,
            "blocked": 0,
            "least_trusted": ["idle gaps"],
        },
        "defects": [
            {"id": "q1", "title": "fixed one", "status": "fixed"},
            {"id": "q2", "title": "open one", "status": "reverted"},
        ],
        "retired": [{"id": "q3", "title": "not required"}],
        "interpretations": [
            {
                "id": "a1",
                "question": "Q?",
                "literal": "x",
                "expected": "y",
                "chosen": "literal",
                "how": "complex: kept",
                "kind": "complex",
            },
            {"id": "s1", "question": "W?", "kind": "binary", "chosen": "fresh"},
        ],
        "settings": [
            {
                "id": "s1",
                "key": "hold.window_after_idle",
                "question": "W?",
                "status": "added",
                "current": "fresh",
                "setting": {
                    "key": "hold.window_after_idle",
                    "values": ["chained", "fresh"],
                    "default": "fresh",
                    "scope": "global",
                    "means": {"fresh": "3 days from now"},
                },
            },
            {
                "id": "s2",
                "key": "checkout.cross_branch",
                "question": "X?",
                "status": "proposed",
                "why": "reserve rule",
                "setting": {
                    "key": "checkout.cross_branch",
                    "values": ["allowed", "refused"],
                    "default": "refused",
                    "scope": "pair",
                },
            },
        ],
        "rounds": [
            {
                "round": 1,
                "sent": ["q1", "q2"],
                "fixed": ["q1"],
                "regressions": [{}],
                "pairs": [
                    {
                        "x": ["q2"],
                        "x_rank": 5,
                        "y": {"id": "t::a"},
                        "y_rank": 4,
                        "verdict": "y_wins",
                        "action": "revert_x",
                    }
                ],
                "reverted": [{"findings": ["q2"]}],
                "accepted": True,
                "why": "ok",
            }
        ],
        "costs": {"build": 3.5, "verify": 2.5},
        "caps_hit": ["verify"],
        "stage_caps": {"verify": 2.5},
        "scenarios": {"total": 5, "active": 4, "held": 1},
    }
    r = report.render_report(s)
    assert (
        r.index("## What was checked")
        < r.index("## Findings")
        < r.index("## Settings")
        < r.index("## Decisions")
        < r.index("## Cost by stage")
        and "Fork" not in r
    )
    # the facts, not a prediction (AU1 #5)
    assert (
        "10 claims from the spec: 2 defects confirmed, 1 fixed, 1 open; 0 claim(s) "
        "could not be checked."
        in r
        and "idle gaps" in r
    )
    assert "hidden" not in r and "Prediction" not in r
    assert "| verify | 2.5000 | 2.00 | 2.50 (hit) |" in r and "| **total** | **6.0000**" in r
    assert "| build | 3.5000 | 3.70 | none |" in r  # no cap set: none
    assert "q2: open one (reverted)" in r and "panel `y_wins`, action `revert_x`" in r
    assert "| `hold.window_after_idle` | chained, fresh | `fresh` | global | W? | added |" in r
    # review 7: a setting not in the delivered code is only under "Not implemented"
    have, missing = r.split("### Not implemented")
    assert "checkout.cross_branch" not in have.split("## Settings")[1]
    assert (
        "| `checkout.cross_branch` | allowed, refused | `refused` | - | X? | proposed, "
        "not added: reserve rule |" in missing
    )
    assert "- s1:" not in r.split("## Decisions")[1]  # settings aren't decisions
    a = report.render_assumptions(s)
    # ASSUMPTIONS.md is for the operator: plain words, no ids, votes or process notes
    assert a.index("## Settings") < a.index("## Decisions") < a.index("### Q?")
    assert "**This version: x.**" in a and "`HOLD_WINDOW_AFTER_IDLE`" in a
    assert "`fresh`: 3 days from now" in a
    for jargon in (
        "a1:",
        "Delivered:",
        "first written",
        "Tell the factory",
        "Votes",
        "proposed, not added",
    ):
        assert jargon not in a, jargon
    # ASSUMPTIONS.md names no switch that doesn't exist: no env var for the proposed one
    assert "CHECKOUT_CROSS_BRANCH" not in a
    assert (
        "checkout.cross_branch" in a.split("### Not available yet")[1]
        and "checkout.cross_branch" not in a.split("### Not available yet")[0]
    )
