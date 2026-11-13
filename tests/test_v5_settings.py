"""Addendum A (docs/design/build-v5.md): binary business-rule ambiguities become operator
settings; complex ones are the implementation's call. Nobody votes, asks or forks.

The motivating case is from Track D's real check on review6/A. The spec-only panel voted 3/3
that a lapsed hold's pickup window chains from the previous expiry. The repair did that and
regressed hidden topic T15, whose author held the other reading ("a fresh window from
processing"). Here that case is a binary policy. It becomes the setting
`hold.window_after_idle: chained|fresh`, defaulting to the build's reading, never a
confirmed defect."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from repo_factory.events import fold, read_events
from repo_factory.harness.base import HarnessResult
from repo_factory.harness.fake import FakeHarness
from repo_factory.rules.llm import ScriptedLLM
from repo_factory.v5 import claims, oracle, pipeline, settings
from repo_factory.v5.common import prompt_text
from repo_factory.v5.exam import mounted

from . import v5_ready_fake

FIXTURE = Path(__file__).parent / "fixtures" / "repo_task"
POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"


# -- the claims contract ----------------------------------------------------------------------


def test_the_claims_prompt_asks_for_kind_and_a_setting_and_its_example_parses():
    text = prompt_text(POLICY, "claims")
    obj = json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))
    a1, a2 = obj["ambiguities"]
    assert a1["kind"] == "binary" and set(a1["setting"]) >= {"key", "values", "default", "scope"}
    assert a1["setting"]["default"] in a1["setting"]["values"] and a2["kind"] == "complex"
    assert "setting" not in a2
    # the cleaned example keeps both
    out = claims.clean(obj, 60, 8)["ambiguities"]
    assert out[0]["kind"] == "binary" and out[0]["setting"]["scope"] == "pair"
    assert out[1]["kind"] == "complex" and "setting" not in out[1]
    for words in ("`binary`", "`complex`", "operator", "literal reading's value first"):
        assert words in text
    # the motivating case must not leak into the prompt (it is the known-answer test)
    assert "window" not in text and "renewal" not in text


def B(key, values, default=None, scope=None, recommend="expected", **kw):
    return {
        "question": f"{key}?",
        "literal": "l",
        "expected": "e",
        "recommend": recommend,
        "kind": "binary",
        "setting": {"key": key, "values": values, "default": default, "scope": scope},
        **kw,
    }


def test_binary_ambiguities_are_normalised_and_bad_ones_are_complex():
    raw = {
        "ambiguities": [
            B("Checkout.Cross Branch", ["allowed", "refused"], "refused", "per branch pair"),
            B("payment.allow_credit", [True, False], None, None, recommend="literal"),
            B("fees.count", ["started", "full", "calendar"]),  # three values: complex
            {"question": "q?", "kind": "binary"},  # no setting: complex
            {"question": "old style, no kind"},
        ]
    }
    a = claims.clean(raw, 60, 8)["ambiguities"]
    assert a[0]["setting"] == {
        "key": "checkout.cross_branch",
        "values": ["allowed", "refused"],
        "default": "refused",
        "scope": "pair",
    }
    # no default given: the recommended reading's value (values are [literal, expected])
    assert a[1]["setting"]["default"] == "true" and a[1]["setting"]["scope"] == "global"
    assert [x["kind"] for x in a] == ["binary", "binary", "complex", "complex", "complex"]
    # two samples, one saw a switch: the merged item is binary
    s1 = {"claims": [], "ambiguities": [{**a[3], "question": "same?"}]}
    s2 = {"claims": [], "ambiguities": [{**a[0], "question": "same?"}]}
    m = claims.merge([s1, s2])["ambiguities"]
    assert len(m) == 1 and m[0]["kind"] == "binary" and m[0]["sets"] == [1, 2]


def test_setting_names_pins_and_env():
    assert settings.env_name("hold.window_after_idle") == "HOLD_WINDOW_AFTER_IDLE"
    code = "# rule: r\n# claim: c2\ndef test_c2():\n    assert True\n"
    p = settings.pin_code(code, {"hold.window_after_idle": "chained"})
    assert settings.pinned(p) == {"hold.window_after_idle": "chained"}
    assert "monkeypatch.setenv('HOLD_WINDOW_AFTER_IDLE', 'chained')" in p
    assert settings.pin_code(p, {"hold.window_after_idle": "chained"}) == p  # idempotent
    assert settings.unpin(p) == code and settings.pin_code(p, {}) == code
    s = {"values": ["today", "due_date"]}
    assert settings.reading_value(s, "literal") == "today"
    assert settings.reading_value(s, "neither") is None and settings.other(s, "today") == "due_date"


# -- the panel ------------------------------------------------------------------------------


def votes(*replies):
    it = iter(replies)
    return ScriptedLLM({"oracle": lambda p, m: json.dumps({"results": next(it)})})


POL = {
    "key": "hold.window_after_idle",
    "values": ["chained", "fresh"],
    "claim_value": "chained",
    "scope": "global",
    "question": "After an idle gap, does the next window chain from the last expiry?",
}


def test_a_binary_policy_is_a_setting_even_when_the_panel_votes_required_3_of_3():
    R = {"vote": "required", "quote": "passes to the next person"}
    llm = votes(
        {"c2": {**R, "policy": POL}, "c9": R, "c7": {**R, "policy": POL}},
        {"c2": {**R, "policy": POL}, "c9": R, "c7": {**R, "policy": POL}},
        {"c2": R, "c9": {"vote": "silent", "policy": POL}, "c7": {**R, "policy": POL}},
    )
    items = {
        "c2": {
            "situation": "a queue, 7 idle days",
            "expect": "C's window chains",
            "basis": "spec_implied",
        },
        "c9": {"situation": "x", "expect": "y", "basis": "spec_implied"},
        "c7": {"situation": "the spec states it", "expect": "z", "basis": "spec_explicit"},
    }
    out = oracle.panel(llm, POLICY, "spec", items, known=[{**POL, "question": "window?"}])
    assert out["c2"]["class"] == "setting" and out["c2"]["unanimous"]
    assert out["c2"]["setting"]["key"] == "hold.window_after_idle"
    assert out["c2"]["setting"]["claim_value"] == "chained"
    assert out["c9"]["class"] == "defect"  # one policy vote isn't enough
    assert out["c7"]["class"] == "defect"  # rank 2: the spec states it
    assert "`hold.window_after_idle`: values chained, fresh (window?)" in llm.prompts[0]


def test_a_switch_the_build_already_exposes_needs_no_repair_unless_a_value_fails(tmp_path):
    from repo_factory.rules.model import Rule
    from repo_factory.rules.store import RuleStore

    run = pipeline.Run.__new__(pipeline.Run)
    run.settings, run.status = [], {}
    run.store = RuleStore(tmp_path / "rules")
    run.claims = {
        "ambiguities": claims.clean(
            {
                "ambiguities": [
                    B("checkout.cross_branch", ["allowed", "refused"], "refused", "pair"),
                    B("payment.allow_credit", ["true", "false"], "false"),
                ]
            },
            60,
            8,
        )["ambiguities"]
    }
    for i, a in enumerate(run.claims["ambiguities"], 1):
        a["id"] = f"a{i}"
    run.findings = {
        "ambiguities": [
            {
                "id": "a1",
                "implements": "expected",
                "has_setting": True,
                "how": "CROSS=1",
                "values": {"allowed": "pass", "refused": "pass"},
            },
            {
                "id": "a2",
                "implements": "expected",
                "has_setting": True,
                "values": {"true": "pass", "false": "pass"},
            },
        ]
    }
    # the verifier's both-values scenario for payment.allow_credit=true fails on the build
    r = run.store.add(
        Rule(
            id="",
            title="credit kept",
            kind="edge",
            source="",
            expect="",
            code="# setting: payment.allow_credit=true\n# claim: a2\n"
            "def test_x():\n    assert False\n",
            origin={"mode": "v5", "scenario": "a2__true", "pins": {"payment.allow_credit": "true"}},
        ),
        by="t",
        visibility="visible",
    )
    run.store.set_status(r.id, "active", "ok", "t")
    run.status[r.id] = "failed"
    run.claim_settings()
    s1, s2 = run.settings
    assert (s1["key"], s1["status"], s1["has_setting"], s1["how"]) == (
        "checkout.cross_branch",
        "exposed",
        True,
        "CROSS=1",
    )
    # the harness's run, not the verifier's word: the value fails, so the item is open
    assert (s2["status"], s2["values"]["true"], s2["fix_value"]) == ("open", "fail", "true")
    assert s2["scenarios"] == [r.id] and [s["id"] for s in run.open_settings()] == ["s2"]
    assert "doesn't behave as described" in settings.item_md(s2)


# -- the motivating case, offline --------------------------------------------------------------

HOLDS = '''"""Holds."""


def window_start(prev_expiry, processed_at):
    """When a lapsed hold passes on, the next member's 3-day window starts."""
    return processed_at


def renew(due, today, period=14):
    return today + period
'''

REPAIRED = '''"""Holds."""
import os


def window_start(prev_expiry, processed_at):
    mode = os.environ.get("HOLD_WINDOW_AFTER_IDLE", "fresh")
    return prev_expiry if mode == "chained" else processed_at


def renew(due, today, period=14):
    base = os.environ.get("RENEWAL_BASE", "due_date")
    return (today if base == "today" else due) + period
'''

README = """# Holds

## Configuration

| setting | values | default | env |
|---|---|---|---|
| `hold.window_after_idle` | chained, fresh | fresh | `HOLD_WINDOW_AFTER_IDLE` |
| `renewal.base` | today, due_date | due_date | `RENEWAL_BASE` |
"""

DRIVER = "from holds.core import renew, window_start\n"
SCEN = {  # id -> (rule, body); c1 and c3 pass on the build, c2 fails (the T15 case)
    "c1": (
        "a lapse processed late gives the next member a fresh window",
        "def test_c1():\n    assert window_start(10, 12) == 12\n",
    ),
    "c2": (
        "after an idle gap the next window chains from the previous expiry",
        "def test_c2():\n    assert window_start(10, 12) == 10\n",
    ),
    "c3": (
        "a renewal on day 15 of a loan due on day 20 runs to day 29",
        "def test_c3():\n    assert renew(20, 15) == 29\n",
    ),
}
CLAIMS = [
    {
        "situation": s,
        "action": "act",
        "expect": s,
        "basis": "spec_implied",
        "quote": "has 3 days to collect it before the hold expires and passes to the next person",
        "kind": "edge",
    }
    for _, (s, _) in SCEN.items()
]
RENEWAL = {
    "question": "Does a renewal extend from today or from the due date?",
    "literal": "from today",
    "expected": "from the current due date",
    "recommend": "expected",
    "stakes": "low",
    "kind": "binary",
    "setting": {
        "key": "renewal.base",
        "values": ["today", "due_date"],
        "default": "due_date",
        "scope": "global",
    },
}
FEES = {
    "question": "How are late days counted?",
    "literal": "started days",
    "expected": "whole days",
    "recommend": "expected",
    "stakes": "low",
    "kind": "complex",
}


def git(ws, *a):
    subprocess.run([*pipeline.repair.GIT, *a], cwd=ws, check=True, capture_output=True)


class Scripted:
    name = "fake"

    def __init__(self):
        self.fake = FakeHarness(failure_rate=0, solution=None)
        self.runs, self.prompts = [], {}

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
        ws, s = Path(workspace), opts.get("session") or {}
        kind = v5_ready_fake.quiet(prompt, ws, opts)  # the readiness stage, quietly
        if kind:
            self.runs.append(kind)
            return self.result(ws, s, 0.05)
        if prompt.rstrip().endswith("Work in the current directory."):
            self.runs.append("build")
            (ws / "meta.json").write_text(json.dumps({"problem": "repo"}))
            res = self.fake.run(prompt, ws, timeout_s, **opts)
            (ws / "meta.json").unlink()
            return res
        if "You are verifying" in prompt:
            self.runs.append("verify")
            self.prompts["verify"] = prompt
            v = (mounted(opts) or ws) / "_verify"
            (v / "driver.py").write_text(DRIVER)
            out = []
            for cid, (rule, body) in SCEN.items():
                (v / "scenarios" / f"test_{cid}.py").write_text(
                    f"# rule: {rule}\n# claim: {cid}\nfrom _verify.driver import *\n\n\n{body}"
                )
                out.append(
                    {
                        "id": cid,
                        "file": f"_verify/scenarios/test_{cid}.py",
                        "claim": cid,
                        "status": "fail" if cid == "c2" else "pass",
                        "scope": "local",
                    }
                )
            (v / "findings.json").write_text(
                json.dumps(
                    {
                        "scenarios": out,
                        "blocked": [],
                        "ambiguities": [
                            {
                                "id": "a1",
                                "implements": "literal",
                                "has_setting": False,
                                "evidence": "renew(20, 15) -> 29",
                            },
                            {"id": "a2", "implements": "literal"},
                        ],
                        "self_estimate": {"p_hidden": 0.9},
                    }
                )
            )
            return self.result(ws, s, 0.5)
        if "=== Findings ===" in prompt:
            self.runs.append("repair")
            self.prompts["repair"] = prompt
            core = ws / "holds" / "core.py"
            core.write_text(REPAIRED)
            git(ws, "add", "-A")
            git(ws, "commit", "-q", "-m", "fix(s2): hold.window_after_idle setting")
            (ws / "README.md").write_text(README)
            git(ws, "add", "-A")
            git(ws, "commit", "-q", "-m", "fix(s1): renewal.base setting, documented")
            return self.result(ws, s, 0.3)
        if "The builder has repaired" in prompt:
            self.runs.append("reverify")
            self.prompts["reverify"] = prompt
            for val, want in (("chained", 10), ("fresh", 12)):
                ((mounted(opts) or ws) / "_verify" / "scenarios" / f"test_s2__{val}.py").write_text(
                    f"# rule: window under {val}\n# setting: hold.window_after_idle={val}\n"
                    f"# claim: s2\nfrom _verify.driver import window_start\n\n\n"
                    f"def test_s2_{val}(monkeypatch):\n"
                    f"    monkeypatch.setenv('HOLD_WINDOW_AFTER_IDLE', '{val}')\n"
                    f"    assert window_start(10, 12) == {want}\n"
                )
            return self.result(ws, s, 0.1)
        raise AssertionError(f"unexpected session: {prompt[:200]}")


def llm():
    n = {"claims": 0}

    def claims_call(prompt, mode):
        n["claims"] += 1
        return json.dumps({"claims": CLAIMS, "ambiguities": [RENEWAL, FEES]})

    def panel(prompt, mode):
        out = {}
        for i, body in re.findall(r"^### (\S+)\n(.*?)(?=^### |\Z)", prompt, re.M | re.S):
            out[i] = {
                "vote": "required",
                "quote": "passes to the next person",
                "policy": POL if "chains" in body else None,
            }
        return json.dumps({"results": out})

    return ScriptedLLM(
        {
            "claims": claims_call,
            "dedupe": "{}",
            "oracle": panel,
            "pair": json.dumps({"verdict": "compatible"}),
        }
    )


@pytest.fixture
def task(tmp_path):
    t = tmp_path / "task"
    shutil.copytree(FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    return t


def test_the_t15_case_becomes_a_setting_not_a_defect(task, tmp_path):
    sol = tmp_path / "solution" / "holds"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(HOLDS)
    h = Scripted()
    h.fake.solution = sol.parent
    out = pipeline.build(
        task, den=tmp_path / "den", out=tmp_path / "out", harness=h, llm=llm(), policy=POLICY
    )
    assert h.runs == ["build", "verify", "repair", "reverify", "security", "checks", "ready-repair"]
    fnd = json.loads(Path(out["findings"]).read_text())
    by = {s["id"]: s for s in fnd["scenarios"]}
    # The panel said "required" 3 of 3, and the claim is still not a confirmed defect.
    assert by["c2"]["class"] == "setting" and by["c2"]["votes"] == ["required"] * 3
    assert fnd["defects"] == []
    sets = {s["key"]: s for s in fnd["settings"]}
    w, r = sets["hold.window_after_idle"], sets["renewal.base"]
    # T15: the default is the build's reading (fresh); chained is behind the setting
    assert w["setting"]["default"] == "fresh" and w["current"] == "fresh"
    assert w["source"] == "panel" and w["status"] == "added"
    assert w["values"] == {"chained": "pass", "fresh": "pass"}  # the re-verify's both values
    # a claims binary: the default is the expected reading, even though the build differs
    assert (r["setting"]["default"], r["current"], r["source"], r["status"]) == (
        "due_date",
        "today",
        "claims",
        "added",
    )
    amb = {a["id"]: a for a in fnd["ambiguities"]}
    assert amb["a1"]["kind"] == "binary" and amb["a1"]["has_setting"] is False
    assert amb["a1"]["implements"] == "literal" and amb["a2"]["kind"] == "complex"
    # c2 is pinned to chained and passes; c1 (fresh) still passes under the default
    from repo_factory.rules.store import RuleStore, store_root

    rs = RuleStore(store_root(Path(out["den"]) / "cache", "textstats", out["round"]))
    rules = {x.origin.get("scenario"): x for x in rs.rules.values()}
    assert settings.pinned(rules["c2"].code) == {"hold.window_after_idle": "chained"}
    assert settings.pinned(rules["c1"].code) == {}
    # c3 asserted the old renewal behaviour; the changed default broke it, and the rescue
    # pinned it to the old value instead of calling it a regression
    assert settings.pinned(rules["c3"].code) == {"renewal.base": "today"}
    assert by["c3"]["harness_status"] == "passed"
    evs = read_events(Path(out["events"]))
    rr = [e for e in evs if e["type"] == "repair_round" and e["status"] == "done"][0]
    assert rr["accepted"] and rr["regressions"] == 0
    assert sorted(rr["settings"]) == ["hold.window_after_idle", "renewal.base"]
    assert not [e for e in evs if e["type"] == "conflict"]
    # no reading vote, no question, no fork
    assert not [e for e in evs if e["type"] == "question"] and not out["alt_branches"]
    kinds = {e["item"]: e for e in evs if e["type"] == "interpretation"}
    assert kinds["a2"]["kind"] == "complex" and kinds["a2"]["chosen"] == "literal"
    assert kinds["s2"]["kind"] == "binary" and kinds["s2"]["chosen"] == "fresh"
    # the briefs: settings get their own section; the re-verify tests both values
    rep = h.prompts["repair"]
    assert "=== Settings to add ===" in rep and "### s2: setting `hold.window_after_idle`" in rep
    assert "**Default: `fresh`.**" in rep and "`HOLD_WINDOW_AFTER_IDLE=chained`" in rep
    assert rep.index("=== Findings ===") < rep.index("=== Settings to add ===")
    assert "(keep that behaviour as the default)" in rep.split("### s2")[1]
    assert "test_s2__<value>" not in rep and "under both values" in h.prompts["reverify"]
    assert "has_setting" in h.prompts["verify"]
    # delivery: REPORT.md and ASSUMPTIONS.md carry the settings table; the delivered
    # verification scenarios pass (the pinned ones force their value)
    o = Path(out["out"])
    ev = Path(out["den"]) / "v5" / f"r{out['round']}" / "exam" / "evidence"
    report_md, assumptions = (o / "REPORT.md").read_text(), (ev / "assumptions.md").read_text()
    for text in (report_md, assumptions):
        assert (
            "| `hold.window_after_idle` | chained, fresh | `fresh` | global | After an idle "
            'gap, does the next window chain from the last expiry? (spec: "has 3 days to '
            "collect it" in text
        )
        assert "| `renewal.base` | today, due_date | `due_date` | global |" in text
    assert "### How are late days counted?" in assumptions
    assert "## Decisions" in report_md and "Fork" not in report_md
    # the settings the main repair documented survive the readiness repair's README work
    assert "renewal.base" in (o / "README.md").read_text()
    res = json.loads((ev / "results.json").read_text())
    assert res["scenarios"] and {v["status"] for v in res["scenarios"].values()} == {"passed"}
    st = fold(evs)
    from repo_factory.progress import status_text

    assert "Settings: 2 (renewal.base=due_date, hold.window_after_idle=fresh)" in status_text(st)
