"""The readiness stage (docs/design/build-v5.md, Addendum C), offline: the security review
and the operational checks through a scripted harness, findings flowing to the readiness
repair by severity and budget, the scorecard, the security and readiness reports in the
round's evidence (EX-PLAN §4.4: never in the delivered tree), no build-process words in the
product's prose, the scope prompt's breadth and safe defaults, the cap and the UI."""

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from repo_factory.events import fold, read_events
from repo_factory.rules.store import RuleStore
from repo_factory.v5 import accept, acceptance, commands, jargon, pipeline, readiness, report
from repo_factory.v5.common import STAGES, Budget
from repo_factory.v5.exam import PROPOSALS_AT, mounted

from . import test_v5_pipeline as base
from . import v5_ready_fake as fake

# The README's test commands only run inside a bwrap sandbox (commands.available()). Where
# there is none (a hardened CI runner, a machine without bubblewrap), the "tests_green" item
# is amber by design, so the tests that expect it green are skipped there.
needs_sandbox = pytest.mark.skipif(
    not commands.available(), reason="no bwrap sandbox: the README's commands aren't run"
)

POLICY = base.POLICY
SOLUTION = (
    base.BUGGY
    + '''

def demo_default():
    """Whether a first start loads demo data."""
    return True
'''
)
DEMO_SCEN = (
    "import textstats.core as core\n\n\ndef test_c4():\n    assert core.demo_default() is True\n"
)
PROBE_S1 = (
    "# rule: char_count refuses input that isn't text with a clear error\n"
    "# expect: ValueError\n# source: security review\n# claim: security\n"
    "import pytest\nfrom _verify.security_helpers import counted\n\n\n"
    "def test_sec_1():\n    with pytest.raises(ValueError):\n        counted(None)\n"
)
PROBE_S3 = (
    "# rule: top_words refuses a negative n\n# expect: ValueError\n"
    "# source: security review\n# claim: security\n"
    "import pytest\nfrom _verify.driver import top\n\n\n"
    "def test_sec_3():\n    with pytest.raises(ValueError):\n        top('a b', -1)\n"
)
PROBE_S4 = (
    "# rule: word_count refuses bytes\n# expect: ValueError\n"
    "# source: security review\n# claim: security\n"
    "import pytest\nfrom _verify.driver import wc\n\n\n"
    "def test_sec_4():\n    with pytest.raises(ValueError):\n        wc(b'a b')\n"
)
PROBE_S5 = (
    "# rule: word_count of an empty text is zero\n# expect: 0\n"
    "# source: security review\n# claim: security\n"
    "from _verify.driver import wc\n\n\ndef test_sec_5():\n    assert wc('') == 0\n"
)
GUARD = (
    "# rule: HTML in the text is counted as words, never run\n# expect: 2\n"
    "# source: security review\n# claim: security\n"
    "from _verify.driver import wc\n\n\ndef test_sec_ok_1():\n"
    "    assert wc('<b>x</b> y') == 2\n"
)
PROBE_EMPTY = (
    "# rule: a fresh start has no demo data\n# expect: False\n"
    "# source: readiness\n# claim: readiness\n"
    "import textstats.core as core\n\n\n"
    "def test_ready_empty_start():\n    assert core.demo_default() is False\n"
)
FINDINGS = [
    {
        "id": "s1",
        "title": "char_count crashes on input that isn't text",
        "severity": "high",
        "category": "hostile input",
        "where": "textstats/core.py:5",
        "repro": "python -c 'from textstats.core import char_count; char_count(None)'",
        "observed": "TypeError traceback",
        "expected": "ValueError with a clear message",
        "probe": "_verify/scenarios/test_sec_1.py",
    },
    {
        "id": "s2",
        "title": "The module logs every text it counts",
        "severity": "high",
        "category": "secrets",
        "where": "textstats/core.py",
        "repro": "grep -n print core.py",
        "observed": "texts printed",
        "expected": "no texts in logs",
        "probe": None,
        "no_probe": "needs a log capture the product doesn't have",
    },
    {
        "id": "s3",
        "title": "top_words accepts a negative n",
        "severity": "medium",
        "category": "hostile input",
        "repro": "top_words('a b', -1)",
        "observed": "[]",
        "expected": "ValueError",
        "probe": "_verify/scenarios/test_sec_3.py",
    },
    {
        "id": "s4",
        "title": "word_count accepts bytes",
        "severity": "low",
        "repro": "word_count(b'a b')",
        "observed": "2",
        "expected": "ValueError",
        "probe": "_verify/scenarios/test_sec_4.py",
    },
    {
        "id": "s5",
        "title": "word_count of an empty text",
        "severity": "info",
        "repro": "word_count('')",
        "observed": "0?",
        "expected": "0",
        "probe": "_verify/scenarios/test_sec_5.py",
    },
]


class ReadyScripted(base.Scripted):
    """The build of ``test_v5_pipeline`` with a real readiness stage: findings of every
    severity, probes, a scratch script the reviewer left, red readiness items; the
    readiness repair fixes the high finding, flips the demo default (and updates the
    scenario that encoded the old one), names the probe-less items, adds CI and docs."""

    def __init__(self, repair_ready=True):
        super().__init__()
        self.repair_ready = repair_ready
        self.prompts = {}

    def run(self, prompt, workspace, timeout_s, **opts):
        ws, s = Path(workspace), opts.get("session") or {}
        v = (mounted(opts) or ws) / "_verify"  # the session's exam, /work/exam
        if fake.SECURITY in prompt:
            self.runs.append(("security", opts))
            self.prompts["security"] = prompt
            (v / "security_helpers.py").write_text(
                "from textstats.core import char_count\n\n\ndef counted(x):\n"
                "    return char_count(x)\n"
            )
            (v / "sec_scratch.py").write_text("print('a scratch script')\n")
            for name, code in (
                ("test_sec_1.py", PROBE_S1),
                ("test_sec_3.py", PROBE_S3),
                ("test_sec_4.py", PROBE_S4),
                ("test_sec_5.py", PROBE_S5),
                ("test_sec_ok_1.py", GUARD),
            ):
                (v / "scenarios" / name).write_text(code)
            (v / "security.json").write_text(json.dumps(fake.security_report(FINDINGS)))
            return self.result(ws, s, 0.4)
        if fake.CHECKS_PROMPT in prompt:
            self.runs.append(("checks", opts))
            self.prompts["checks"] = prompt
            (v / "scenarios" / "test_ready_empty_start.py").write_text(PROBE_EMPTY)
            (v / "readiness.json").write_text(
                json.dumps(
                    fake.checks_report(
                        {
                            "empty_start": {
                                "status": "red",
                                "why": "demo data on a default start",
                                "fixable": True,
                                "next": "start empty",
                                "probe": "_verify/scenarios/test_ready_empty_start.py",
                            },
                            "logs": {
                                "status": "red",
                                "why": "no log",
                                "fixable": True,
                                "next": "log errors",
                            },
                            "upgrade": {
                                "status": "amber",
                                "why": "no schema version",
                                "fixable": True,
                                "next": "add one",
                            },
                        }
                    )
                )
            )
            return self.result(ws, s, 0.2)
        if fake.REPAIR in prompt:
            self.runs.append(("ready-repair", opts))
            self.prompts["ready-repair"] = prompt
            if self.repair_ready:
                core = ws / "textstats" / "core.py"
                t = core.read_text().replace(
                    "def char_count(text):\n    return len(text)",
                    "def char_count(text):\n    if not isinstance(text, str):\n"
                    "        raise ValueError('text must be a string')\n    return len(text)",
                )
                t = t.replace(  # the same check fixes the unsent S4
                    "def word_count(text):\n    return len(text.split())",
                    "def word_count(text):\n    if not isinstance(text, str):\n"
                    "        raise ValueError('text must be a string')\n"
                    "    return len(text.split())",
                )
                core.write_text(t)
                rid = re.search(r"^### (\w+) \(security, high\): char_count", prompt, re.M)[1]
                base.git(ws, "add", "-A")
                base.git(ws, "commit", "-q", "-m", f"fix({rid}): char_count validates input")
                core.write_text(
                    core.read_text().replace(
                        "    return True",
                        "    import os\n    return os.environ.get('TEXTSTATS_DEMO') == '1'",
                    )
                )
                # the scenario that encoded the old default asks for the demo explicitly:
                # the exam is read-only, so a new start-up is proposed beside it
                f = next(
                    p
                    for p in (v / "scenarios").glob("test_rule_*.py")
                    if "demo_default() is True" in p.read_text()
                )
                (mounted(opts, PROPOSALS_AT) / f.name).write_text(
                    "import textstats.core as core\n\n\ndef test_c4(monkeypatch):\n"
                    "    monkeypatch.setenv('TEXTSTATS_DEMO', '1')\n"
                    "    assert core.demo_default() is True\n"
                )
                rid = re.search(
                    r"^### (\w+) \(readiness, the safe-defaults rule\): Starts", prompt, re.M
                )[1]
                base.git(ws, "add", "textstats")
                base.git(ws, "commit", "-q", "-m", f"fix({rid}): start empty by default")
                fake.write_ready_files(ws)
                base.git(ws, "add", "-A")
                base.git(
                    ws,
                    "commit",
                    "-q",
                    "-m",
                    "fix(S2,logs,ci,gitignore,readme): no "
                    "logging of texts, error log, CI, .gitignore, README",
                )
            return self.result(ws, s, 0.6)
        if "You are verifying" in prompt:
            res = super().run(prompt, workspace, timeout_s, **opts)
            (v / "scenarios" / "test_c4.py").write_text(
                "# rule: demo data loads on a default start\n# claim: explore\n" + DEMO_SCEN
            )
            return res
        return super().run(prompt, workspace, timeout_s, **opts)


def ev(out) -> Path:
    """The round's evidence: the reports and the checks, beside the delivered tree."""
    return Path(out["den"]) / "v5" / f"r{out['round']}" / "exam" / "evidence"


def ready_build(tmp_path, h=None, **kw):
    t = tmp_path / "task"
    shutil.copytree(base.FIXTURE, t)
    shutil.rmtree(t / "_fake_solution")
    sol = tmp_path / "solution" / "textstats"
    sol.mkdir(parents=True)
    (sol / "__init__.py").write_text("")
    (sol / "core.py").write_text(SOLUTION)
    h = h or ReadyScripted()
    h.fake.solution = sol.parent
    out = pipeline.build(
        t,
        den=t / ".den",
        out=tmp_path / "out",
        harness=h,
        llm=base.llm(),
        policy=POLICY,
        owner="queue",
        **kw,
    )
    return t, h, out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return ready_build(tmp_path_factory.mktemp("ready"))


# -- the stage, end to end ----------------------------------------------------------------------


def test_the_stage_runs_after_reverify_and_before_delivery(built):
    t, h, out = built
    assert STAGES.index("reverify") < STAGES.index("readiness") < STAGES.index("deliver")
    assert pipeline.load_rules(POLICY)["stages"][-2:] == ["readiness", "deliver"]
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
    started = [e["stage"] for e in evs if e["type"] == "v5_stage" and e["status"] == "started"]
    assert started.index("reverify") < started.index("readiness") < started.index("deliver")
    assert out["done_steps"][-2:] == ["readiness", "deliver"]
    # the harness ran the README's commands between the checks and the repair
    steps = [e["step"] for e in evs if e["type"] == "readiness"]
    assert steps.index("checks") < steps.index("commands") < steps.index("repair")
    # no step has a dollar cap by default (each one's max_usd is a setting); the repair is
    # a fresh session
    o = dict(h.runs)
    assert (o["security"]["max_usd"], o["checks"]["max_usd"], o["ready-repair"]["max_usd"]) == (
        None,
        None,
        None,
    )
    assert "resume" not in o["ready-repair"]["session"]  # not the builder's session
    assert "You didn't write this code" in h.prompts["ready-repair"]
    assert out["cost_by_stage"]["readiness"] == pytest.approx(1.2)


def test_findings_flow_to_the_repair_by_severity_and_are_judged_by_the_harness(built):
    t, h, out = built
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    s1, s2, s3, s4, s5 = rd["security"]["findings"]  # by severity, then order
    assert [x["id"] for x in (s1, s2, s3, s4, s5)] == ["S1", "S2", "S3", "S4", "S5"]
    assert [x["title"].split()[0] for x in (s1, s2, s3, s4, s5)] == [
        "char_count",
        "The",
        "top_words",
        "word_count",
        "word_count",
    ]
    assert s5["severity"] == "low"  # "info" reads as low
    # sent: high always, medium because the full round was affordable; low never
    assert (s1["sent"], s2["sent"], s3["sent"], s4["sent"], s5["sent"]) == (
        True,
        True,
        True,
        False,
        False,
    )
    # the harness is the record: S1's probe passes after the repair, S3's still fails;
    # S2 has no test (its commit names it); S4 wasn't sent, but S1's fix made its test pass
    # on the delivered tree: "passes", not "fixed" (a changed default can void a test's
    # premise); S5's probe passed on the build: unconfirmed
    assert (s1["status"], s2["status"], s3["status"], s4["status"], s5["status"]) == (
        "fixed",
        "addressed",
        "open",
        "passes",
        "unconfirmed",
    )
    sec = (ev(out) / "security.md").read_text()
    assert (
        "| S4 | low | word_count accepts bytes | its test passes after other fixes; "
        "confirm by hand |"
        in sec
        and "Reproduce it by hand before closing it" in sec
    )
    assert s2["confirmed_by"] == "hand" and s1["confirmed_by"] == "test"
    brief = h.prompts["ready-repair"]
    assert (
        brief.index("(security, high)")
        < brief.index("(security, medium)")
        < brief.index("(readiness")
    )
    assert "word_count accepts bytes" not in brief and "commit each" in brief
    # ranks: security 2, readiness 3, the owner's safe-defaults rule 1
    assert accept.rank({"kind": "scenario", "basis": "security"}) == 2
    assert accept.rank({"kind": "scenario", "basis": "readiness"}) == 3
    assert accept.rank({"kind": "scenario", "basis": "owner_rule"}) == 1
    # the round: accepted, nothing broken, the scenario that encoded the old default was
    # updated (not counted as a regression)
    r = rd["round"]
    assert r["accepted"] and r["regressions"] == []
    assert [x["title"] for x in rd["scenarios_updated"]] == ["demo data loads on a default start"]
    store = RuleStore(next((Path(out["den"]) / "cache" / "rules").glob("*/r1")))
    upd = store.rules[rd["scenarios_updated"][0]["id"]]
    assert upd.status == "active" and "TEXTSTATS_DEMO" in upd.code
    # the reviewer's scratch script never joins the helpers; the helper its probe imports does
    helpers = Path(out["den"]) / "v5" / "r1" / "helpers"
    assert (helpers / "security_helpers.py").exists() and not (helpers / "sec_scratch.py").exists()


@needs_sandbox
def test_the_scorecard_and_the_deliverables(built):
    t, h, out = built
    o = Path(out["out"])
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    card = {i["id"]: i for i in rd["final"]["items"]}
    # every item, in order (intermittent and not-verifiable rows only when there are some)
    assert list(card) == [k for k in readiness.CHECKS if k not in ("intermittent", "verifiable")]
    assert card["empty_start"]["status"] == "green"  # its probe passes now
    assert card["empty_start"]["why"].startswith("fixed in this version")
    assert card["logs"]["status"] == "amber" and "not re-checked" in card["logs"]["why"]
    assert card["upgrade"]["status"] == "amber"  # amber session items: as found
    for k in ("ci", "gitignore", "readme", "lockfile", "tests_green", "docs_plain"):
        assert card[k]["status"] == "green", (k, card[k])
    assert card["health"]["status"] == "n/a"
    # the README's commands, run as written on the delivered tree: a library whose quick
    # start only installs it, not a service; its test commands run green
    assert card["commands"]["status"] == "n/a" and card["restart_now"]["status"] == "n/a"
    assert "run green as written" in card["tests_green"]["why"]
    # the reports: in the evidence beside the tree (never in it), plain words
    for gone in ("SECURITY.md", "READINESS.md", "ASSUMPTIONS.md", "tests/acceptance"):
        assert not (o / gone).exists(), gone
    sec, ready = (ev(out) / "security.md").read_text(), (ev(out) / "readiness.md").read_text()
    for s in (
        "## Threat model",
        "| entry point | who can reach it |",
        "## What was checked",
        "## Findings",
        "| S1 | high | char_count crashes",
        "fixed (a test checks it)",
        "### S3 (medium): top_words accepts a negative n",
        "## Dependency audit",
        "## Known limits",
        "## Reporting a security issue",
    ):
        assert s in sec, s
    assert "### S1 " not in sec  # fixed: no open detail
    assert "| Starts empty; demo data only on an explicit flag or command | green |" in ready
    # a fix that came without a test of the product's own: amber (exam.adopt: fixes)
    assert card["adopted"]["status"] == "amber" and "without a test" in card["adopted"]["why"]
    assert "**12 green, 3 amber, 0 red**, 6 not applicable" in ready
    assert "Known security issue (medium): Top_words refuses a negative n" in ready
    assert jargon.scan_run(SimpleNamespace(root=ev(out).parent.parent), o) == []
    rep = (o / "REPORT.md").read_text()
    assert "## Readiness" in rep and "Scorecard: 12 green" in rep
    assert "| readiness | 1.2000 | 2.10 | none |" in rep


def test_the_tree_is_the_products_and_the_checks_are_in_the_evidence(built):
    """EX-PLAN §4: the delivered tree holds what the build agent wrote; repo-factory's scenarios,
    the reviewers' probes and helpers, the results and the reports are the evidence."""
    t, h, out = built
    o = Path(out["out"])
    assert not (o / "tests" / "acceptance").exists() and not (o / "tests" / "verification").exists()
    assert "Acceptance tests" not in (o / "README.md").read_text()
    e = ev(out)
    scen = e / "exam" / "_verify" / "scenarios"
    names = sorted(p.name for p in scen.glob("test_*.py"))
    assert names and all(re.fullmatch(r"test_rule_\w+\.py", n) for n in names)
    assert (e / "exam" / "_verify" / "security_helpers.py").exists()
    assert not (e / "exam" / "_verify" / "sec_scratch.py").exists()
    res = json.loads((e / "results.json").read_text())
    # S3's probe still fails on the delivered tree: an open item in the evidence, no mark
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    s3 = next(f for f in rd["security"]["findings"] if f["id"] == "S3")
    assert res["scenarios"][s3["rule"]]["status"] == "failed"
    code = (scen / f"test_rule_{s3['rule']}.py").read_text()
    assert "xfail" not in code and "Known open item" not in code
    assert "These end-to-end checks" in (e / "readiness.md").read_text()
    assert (
        "Known security issue (medium): Top_words refuses a negative n"
        in (e / "readiness.md").read_text()
    )
    readme = (e / "README.md").read_text()
    assert "REPO_FACTORY_PRODUCT_DIR=/path/to/checkout" in readme


@needs_sandbox
def test_events_status_and_the_ui_show_the_readiness_stage(built, tmp_path):
    t, h, out = built
    evs = read_events(Path(out["events"]))
    sec = next(e for e in evs if e["type"] == "security")
    assert (sec["high"], sec["medium"], sec["low"], sec["probes"]) == (2, 1, 2, 4)
    st = fold(evs)
    assert st["v5"]["readiness"]["final"]["green"] == 12
    assert st["v5"]["readiness"]["repair"]["accepted"] is True
    from repo_factory.progress import status_text

    text = status_text(st)
    assert "Security review: 2 high, 1 medium, 2 low" in text
    assert "Readiness: 12 green, 3 amber, 0 red" in text and "Readiness repair:" in text
    from repo_factory.ui import server
    from repo_factory.ui.v5view import view

    v = view(t, st)
    assert [x["stage"] for x in v["stages"]][-2:] == ["readiness", "deliver"]
    rd = v["readiness"]
    assert rd["counts"] == {"critical": 0, "high": 2, "medium": 1, "low": 2}
    assert [(f["id"], f["status"]) for f in rd["findings"]][:3] == [
        ("S1", "fixed"),
        ("S2", "addressed"),
        ("S3", "open"),
    ]
    assert rd["final"] and rd["tally"]["green"] == 12 and len(rd["scorecard"]) == 21
    assert v["security_md"] and v["readiness_md"]
    app = server.App(t.parent, "claude-code")
    assert app.text(t, "security").startswith("# Security")
    assert app.text(t, "readiness").startswith("# Readiness")
    # an older log without a readiness stage shows none
    st2 = json.loads(json.dumps(st))
    st2["v5"]["stages"].pop("readiness")
    st2["v5"]["planned"].remove("readiness")
    assert "readiness" not in [x["stage"] for x in view(t, st2)["stages"]]


# -- pieces ------------------------------------------------------------------------------------


def fake_run(cap=20.0, spent=0.0, medium="if_budget", findings=(), checks=()):
    b = Budget(cap)
    b.extra["build"] = spent
    rules = {"readiness": {"medium": medium, "repair": {"max_usd": 1.5, "min_usd": 0.5}}}
    return SimpleNamespace(
        budget=b,
        rules=rules,
        store=SimpleNamespace(rules={}),
        ready={
            "security": {"findings": [dict(f) for f in findings]},
            "checks": {"items": {c["id"]: dict(c) for c in checks}},
        },
    )


SEV = [
    {"id": f"S{n}", "title": s, "severity": s, "status": "open"}
    for n, s in enumerate(("critical", "high", "medium", "low"), 1)
] + [{"id": "S5", "title": "x", "severity": "high", "status": "unconfirmed"}]
CHK = [
    {"id": "ci", "title": "CI", "status": "red", "fixable": True},
    {"id": "gitignore", "title": "g", "status": "amber", "fixable": True},
    {"id": "upgrade", "title": "u", "status": "amber", "fixable": True},
    {"id": "backup_restore", "title": "b", "status": "red", "fixable": False},
    {"id": "bind_localhost", "title": "bind", "status": "red", "fixable": True},
]


@pytest.mark.parametrize(
    "cap,medium,want",
    [
        (20.0, "if_budget", ["S1", "S2", "S3"]),  # the full round fits: medium goes too
        (10.0 + 1.0, "if_budget", ["S1", "S2"]),  # only a smaller round fits: no medium
        (20.0, "never", ["S1", "S2"]),
        (11.0, "always", ["S1", "S2", "S3"]),
    ],
)
def test_plan_sends_by_severity_and_budget(cap, medium, want):
    run = fake_run(cap=cap, spent=10.0, medium=medium, findings=SEV, checks=CHK)
    items = readiness.plan(run)
    assert [d["id"] for d in items if d["class"] == "security"] == want
    # readiness: fixable red ones, and amber ones the harness checks itself
    # most severe first: the owner's safe-defaults rule (rank 1), then the rest (rank 3)
    assert [d["id"] for d in items if d["class"] == "readiness"] == [
        "bind_localhost",
        "ci",
        "gitignore",
    ]
    assert next(d for d in items if d["id"] == "bind_localhost")["owner_rule"]
    assert readiness.basis(next(d for d in items if d["id"] == "bind_localhost")) == "owner_rule"
    assert all(d["check"] for d in items)  # none has a test here
    assert not any(
        f.get("sent") for f in run.ready["security"]["findings"] if f["id"] in ("S4", "S5")
    )


def test_the_round_is_skipped_below_its_minimum_and_the_stage_below_its_start(tmp_path):
    # $0.55 spent before the stage (no main repair at this cap); security $0.40 and checks
    # $0.20 fit, and then $1.15 + the round's $0.50 minimum doesn't
    t, h, out = ready_build(tmp_path, max_usd=1.5)
    assert [r[0] for r in h.runs][-2:] == ["security", "checks"]
    rd = json.loads((Path(out["den"]) / "v5" / "r1" / "readiness.json").read_text())
    assert "a repair round needs $0.50" in rd["skipped"]
    assert not any(f.get("sent") for f in rd["security"]["findings"])
    o = Path(out["out"])
    assert "Repair round" not in (ev(out) / "security.md").read_text()
    assert (
        "| S1 | high | char_count crashes on input that isn't text | open |"
        in (ev(out) / "security.md").read_text()
    )
    assert "Readiness repair: not run (the build's cap" in (o / "REPORT.md").read_text()
    # the review's money is kept aside from the first session on: at $0.80 the stages before
    # it stop short, in words, and the review still runs ($0.55 + $0.30 > $0.80 skipped it)
    t1, h1, out1 = ready_build(tmp_path / "a", max_usd=0.8)
    assert "security" in [r[0] for r in h1.runs]
    before = [
        e
        for e in read_events(Path(out1["events"]))
        if e["type"] == "v5_stage" and e.get("status") == "capped" and e["stage"] != "readiness"
    ]
    assert before and all("$0.30 kept for the security review" in e["why"] for e in before)
    sec1 = json.loads((Path(out1["den"]) / "v5" / "r1" / "readiness.json").read_text())
    assert sec1["security"]["ran"]
    # with less than start_usd under the cap at all, the stage doesn't start; SECURITY.md says so
    t2, h2, out2 = ready_build(tmp_path / "b", max_usd=0.1)
    assert "security" not in [r[0] for r in h2.runs]
    sec = (ev(out2) / "security.md").read_text()
    assert "**This version has not had a security review**" in sec
    assert "## Reporting a security issue" in sec
    ready = (ev(out2) / "readiness.md").read_text()
    assert "| CI workflow installs the project and runs the tests | red |" in ready


def test_deterministic_checks(tmp_path):
    r = tmp_path / "r"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg.egg-info").mkdir()
    (r / "pkg.egg-info" / "PKG-INFO").write_text("x")
    c = readiness.deterministic(r)
    assert c["ci"]["status"] == "red" and c["ci"]["fixable"]
    assert c["lockfile"]["status"] == "green" and "standard library" in c["lockfile"]["why"]
    assert c["gitignore"]["status"] == "red" and "pkg.egg-info" in c["gitignore"]["why"]
    assert c["readme"]["status"] == "red"
    (r / "pyproject.toml").write_text('[project]\nname = "p"\ndependencies = ["requests"]\n')
    assert readiness.check_lockfile(r)["status"] == "red"
    (r / "uv.lock").write_text("")
    assert readiness.check_lockfile(r)["status"] == "green"
    (r / ".gitignore").write_text("__pycache__/\n")
    g = readiness.check_gitignore(r)
    assert g["status"] == "amber" and "virtual environments" in g["why"]
    (r / ".github" / "workflows").mkdir(parents=True)
    (r / ".github" / "workflows" / "test.yml").write_text("name: test\non: push\njobs: {}\n")
    assert readiness.check_ci(r)["status"] == "amber"  # runs nothing
    fake.write_ready_files(r)
    assert readiness.check_ci(r)["status"] == "green"
    assert readiness.check_readme(r)["status"] == "green"
    (r / "README.md").write_text((r / "README.md").read_text() + "\nSee tests/verification.\n")
    rd = readiness.check_readme(r)
    assert rd["status"] == "amber" and "jargon" in rd["why"]


def test_the_reports_carry_no_build_process_words():
    assert acceptance.plain_title("x3 - (neighbour of fix e44) ids") == "Ids"
    assert acceptance.plain_title("F3: Check out a book") == "Check out a book"
    rd = {
        "security": {
            "ran": True,
            "threat_model": {
                "summary": "s",
                "entry_points": [{"name": "GET /", "reach": "public", "auth": "none"}],
                "assets": [],
            },
            "checks": [],
            "findings": [
                {
                    "id": "S1",
                    "title": "t",
                    "severity": "high",
                    "status": "open",
                    "rule": "e66",
                    "repro": "curl x",
                }
            ],
            "audit": {"tool": "pip-audit", "status": "clean"},
            "limits": ["TLS untested"],
        },
        "final": {"items": [], "counts": {}},
    }
    sec = readiness.render_security("P", rd, {"e66": "exam/_verify/scenarios/test_rule_e66.py"})
    ready = readiness.render_readiness("P", rd, [])
    assert "e66 " not in sec and "`exam/_verify/scenarios/test_rule_e66.py`" in sec
    assert "(still fails on this version)" in sec and "`security.md`" in ready


def test_the_scope_prompt_covers_named_breadth_and_safe_defaults():
    from repo_factory.v5.common import prompt_text

    p = prompt_text(POLICY, "scope")
    assert "Breadth the idea names is must-have" in p and "many SaaS tools" in p
    for s in ("Standard Webhooks/Svix", "Slack", "Paddle", "not a token one or two"):
        assert s in p, s
    install = p.split("## Install and run")[1].split("## Non-functional")[0]
    for s in (
        "listens on localhost (127.0.0.1)",
        "starts **empty**",
        "never a fixed default password",
        "explicit flag or command",
        "every option documented",
        "versioned schema or migrations",
    ):
        assert s in install, s
    assert "with the demo data loaded" not in p
    sec = prompt_text(POLICY, "security")
    for s in (
        "Threat model",
        "Content-Length",
        "deeply nested JSON",
        "CSRF",
        "path traversal",
        "open redirects",
        "lockout",
        "pip-audit",
        "default credentials",
        "reproduce it by hand",
        "test_sec_<n>.py",
        "critical",
        "unavailable",
    ):
        assert s in sec, s
    chk = prompt_text(POLICY, "readiness")
    for k, (_, how) in readiness.CHECKS.items():
        if how == "session":
            assert f"`{k}`" in chk, k


def test_the_global_cap_default_is_configurable(monkeypatch):
    rules = pipeline.load_rules(POLICY)
    monkeypatch.delenv(pipeline.CAP_ENV, raising=False)
    assert pipeline.default_cap(rules) is None  # no cap: the owner's rule
    monkeypatch.setenv(pipeline.CAP_ENV, "$20")
    assert pipeline.default_cap(rules) == 20.0
    monkeypatch.setenv(pipeline.CAP_ENV, "none")
    assert pipeline.default_cap({**rules, "max_usd": 9.0}) is None
    monkeypatch.setenv(pipeline.CAP_ENV, "lots")
    with pytest.raises(ValueError, match="REPO_FACTORY_V5_MAX_USD"):
        pipeline.default_cap(rules)
    monkeypatch.setenv(pipeline.CAP_ENV, "")
    assert pipeline.default_cap({**rules, "max_usd": 14}) == 14.0
    assert pipeline.default_cap({k: v for k, v in rules.items() if k != "max_usd"}) is None
    # a second repair round keeps the readiness stage's three caps in reserve, under a cap only
    run = SimpleNamespace(rules=rules, budget=Budget(14.0))
    assert pipeline.Run.readiness_reserve(run) == pytest.approx(3.3)
    assert pipeline.Run.readiness_reserve(SimpleNamespace(rules=rules, budget=Budget(None))) == 0
    assert (
        pipeline.Run.readiness_reserve(
            SimpleNamespace(
                budget=Budget(14.0),
                rules={**rules, "stages": [s for s in rules["stages"] if s != "readiness"]},
            )
        )
        == 0.0
    )
    y = yaml.safe_load((POLICY / "rules.yaml").read_text())
    assert y["readiness"]["repair"]["resume"] is False


def test_assumptions_are_plain():
    s = {
        "task": "t",
        "scope": {"decision": "scoped"},
        "settings": [],
        "interpretations": [
            {
                "id": "a3",
                "kind": "complex",
                "question": "How are days counted?",
                "literal": "started days",
                "expected": "whole days",
                "chosen": "unknown",
                "implements": "unknown",
                "how": "complex: kept",
                "votes": ["x"],
            },
            {
                "id": "i-q12",
                "kind": "complex",
                "question": "Q?",
                "literal": "as built: 5",
                "expected": "6",
                "chosen": "expected",
            },
        ],
    }
    a = report.render_assumptions(s)
    assert "### How are days counted?" in a and "it keeps the behaviour it was built with" in a
    assert "**This version: 6.**" in a
    assert not jargon.scan_text(a, jargon.patterns({}))
    for w in ("a3", "i-q12", "Votes", "unknown", "factory"):
        assert w not in a, w


def test_security_md_puts_the_sessions_words_plainly():
    """The review session cites its own finding refs and workspace paths in free text; the
    delivered SECURITY.md has neither (the loop's pre-push jargon scan held a real build on
    exactly these)."""
    rd = {
        "security": {
            "ran": True,
            "checks": [
                {
                    "area": "hostile HTTP",
                    "what": "negative Content-Length",
                    "result": "fail",
                    "evidence": "reads until EOF (finding s1)",
                },
                {
                    "area": "authZ",
                    "what": "receptionist POST",
                    "result": "pass",
                    "evidence": "403 on each; probe _verify/scenarios/test_sec_ok_1.py",
                },
            ],
            "findings": [
                {
                    "ref": "s1",
                    "id": "S1",
                    "severity": "high",
                    "status": "open",
                    "title": "Unbounded body read",
                    "observed": "see finding s1",
                }
            ],
            "limits": ["slowloris not tested (finding s9)"],
        }
    }
    md = readiness.render_security("X", rd, {})
    assert jargon.scan_text(md, jargon.patterns({"finding": {"s1", "S1", "s9"}})) == []
    assert "reads until EOF (S1, below)" in md
    assert "403 on each; checked by an automated test" in md
    assert "- What happens: see S1, below" in md
