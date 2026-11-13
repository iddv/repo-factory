"""Track E (build v5 §5, §6.1): the lessons file, lint, injection, post-mortem, credit,
promotion and history. Offline: every model call and v5 run is a fake."""

import json
from pathlib import Path

import pytest
import yaml

from repo_factory.lessons import credit as cmod
from repo_factory.lessons import lint as lmod
from repo_factory.lessons import postmortem as pmod
from repo_factory.lessons import store

FIX = Path(__file__).parent / "fixtures" / "hsuite"
PROFILE = {
    "name": "full-v1",
    "hash": "h1",
    "cli": "2.1",
    "model": "claude-opus-5-5",
    "tools": ["Bash"],
    "mcp_servers": [],
    "permission_mode": "dontAsk",
    "net": "proxy",
    "config": "clean",
}
GOOD = "When a state expires by time, test a long gap with no calls at all: the next party in line must still get a full, fresh turn."


def _w(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


@pytest.fixture
def packs(tmp_path, monkeypatch):
    """Three packs with their own domain terms; shiftboard is held-out."""
    root = tmp_path / "packs"
    for pid, split, terms in (
        ("lending", "dev", ["member", "loan", "hold", "copy", "branch", "fee", "shelf"]),
        ("jobq", "dev", ["job", "lease", "worker", "dead letter"]),
        ("shiftboard", "heldout", ["shift", "staff", "swap"]),
    ):
        _w(
            root / pid / "pack.yaml",
            yaml.safe_dump({"id": pid, "version": 1, "split": split, "domain_terms": terms}),
        )
        _w(root / pid / "spec.md", f"# Build {pid}\n")
    monkeypatch.setenv("REPO_FACTORY_PACKS", str(root))
    return root


def lesson(
    lid, pack, status="probation", exposures=0, caught=0, false_alarms=0, text=None, at="claims"
):
    return store.normalise(
        {
            "id": lid,
            "text": text or f"Test the edge of every limit, case {lid.lower()}.",
            "applies_to": at,
            "status": status,
            "born": {"pack": pack, "run": "r", "row": "x", "date": "2026-10-01"},
            "stats": {
                "exposures": exposures,
                "caught": caught,
                "false_alarms": false_alarms,
                "derived": 0,
                "packs_caught_on": [],
            },
        }
    )


# -- lint ------------------------------------------------------------------------------------
LINT_CASES = [
    (GOOD, []),
    (
        "For every request a party can make about an item, test making it while that party already has the item.",
        [],
    ),
    ("Test both sides of every limit, then the same pair after a long idle gap.", []),
    (
        "When an item is held for a party, check that nobody else can take it.",
        ["domain word 'held'"],
    ),
    ("Check every member limit twice.", ["domain word 'member'"]),
    ("Probe copies of every record.", ["domain word 'copies'"]),
    (
        "Retry each job after its lease ends.",
        ["domain word 'job'", "domain word 'lease'"],
    ),  # jobq's terms
    ("Swap two parties' turns and compare.", ["domain word 'swap'"]),  # held-out pack's terms
    (
        "Requeue anything sent to the dead letter store and test it again.",
        ["domain word 'dead letter'"],
    ),
    ("Test a gap of three days with no calls.", []),
    ("Test a gap of 3 days with no calls.", ["contains a number"]),
    ("Call reset_state() before each probe.", ["identifier-like tokens"]),
    ("Read app/service.py first.", ["identifier-like tokens"]),
    ("The verifier should test long gaps.", ["not imperative"]),
    ("Test gaps. Test limits. Test orders.", ["3 sentences"]),
    ("Test " + "every limit and " * 20 + "gap.", ["characters"]),
]


@pytest.mark.parametrize("text,expect", LINT_CASES)
def test_lint_table(packs, text, expect):
    probs = lmod.lint(text)
    if not expect:
        assert probs == []
    for e in expect:
        assert any(e in p for p in probs), (text, probs)


def test_lint_reads_domain_terms_of_every_pack(packs):
    terms = lmod.domain_terms()
    assert (
        terms["member"] == ["lending"]
        and terms["job"] == ["jobq"]
        and terms["shift"] == ["shiftboard"]
    )
    assert "lease" in terms and "dead letter" in terms


def test_lint_rejects_quotes_of_hidden_rows_and_duplicates(packs):
    hidden = ["the next party must still get a full fresh turn after the gap"]
    assert any("quotes a hidden row" in p for p in lmod.lint(GOOD, hidden=hidden))
    assert any("duplicates" in p for p in lmod.lint(GOOD, existing=[GOOD.upper()]))


def test_lint_file_cap_and_live_texts(packs):
    data = {
        "version": 1,
        "cap": 2,
        "lessons": [
            lesson("L001", "jobq"),
            lesson("L002", "jobq"),
            lesson("L003", "jobq", text="Check each member."),
            lesson("L004", "jobq", status="retired"),
        ],
    }
    probs = lmod.lint_file(data)
    assert any("3 probation/active lessons (cap 2)" in p for p in probs)
    assert any(p.startswith("L003: domain word 'member'") for p in probs)


# -- store: injection, cap, eviction ---------------------------------------------------------
def test_for_build_never_returns_a_lesson_born_on_the_pack(tmp_path):
    p = tmp_path / "lessons.yaml"
    store.save(
        p,
        {
            "version": 3,
            "cap": 20,
            "lessons": [
                lesson("L001", "lending", "active", exposures=4, caught=3),
                lesson("L002", "jobq", "probation", exposures=4, caught=1),
                lesson("L003", "jobq", "active", exposures=4, caught=2),
                lesson("L004", "mdsite", "retired", exposures=8, caught=0),
            ],
        },
    )
    assert [x["id"] for x in store.for_build("lending", p)] == ["L003", "L002"]
    assert [x["id"] for x in store.for_build("jobq", p)] == ["L001"]
    assert [x["id"] for x in store.for_build(None, p)] == [
        "L001",
        "L003",
        "L002",
    ]  # a new spec: all live
    for pack in ("lending", "jobq", "mdsite", "shiftboard"):
        assert all(x["born"]["pack"] != pack for x in store.for_build(pack, p))
    assert "found a defect in 3 of 4 builds" in store.render(store.for_build(None, p))


def test_save_load_round_trip_keeps_the_51_shape(tmp_path):
    p = tmp_path / "lessons.yaml"
    data = store.empty()
    x, _ = store.add(
        data,
        GOOD,
        "claims",
        {"pack": "lending", "run": "runs/e3/lending/s1/v5", "row": "t15/x"},
        today="2026-10-10",
    )
    store.save(p, data)
    raw = yaml.safe_load(p.read_text())
    assert set(raw) == {"version", "cap", "lessons"}
    assert list(raw["lessons"][0]) == [
        "id",
        "text",
        "applies_to",
        "status",
        "born",
        "stats",
        "hit_rate",
    ]
    assert raw["lessons"][0]["born"] == {
        "date": "2026-10-10",
        "pack": "lending",
        "run": "runs/e3/lending/s1/v5",
        "row": "t15/x",
    }
    assert store.load(p)["lessons"][0]["id"] == "L001" and x["status"] == "probation"


def test_cap_and_eviction_order():
    data = {
        "version": 1,
        "cap": 4,
        "lessons": [
            lesson("L001", "a", "active", exposures=10, caught=1),  # 0.10, active
            lesson("L002", "a", "probation", exposures=4, caught=2),  # 0.50
            lesson("L003", "a", "probation", exposures=4, caught=1),  # 0.25  <- evicted first
            lesson("L004", "a", "active", exposures=2, caught=2),
        ],
    }  # 1.00
    x, ev = store.add(data, "Test it.", "claims", {"pack": "b"}, today="2026-10-11")
    assert ev["id"] == "L003" and ev["status"] == "retired" and "evicted" in ev["retired"]["why"]
    assert x["id"] == "L005" and len(store.live(data)) == 4
    _, ev = store.add(data, "Test that.", "verifier", {"pack": "b"})
    assert ev["id"] == "L005"  # probation first: 0.0 beats L002's 0.50
    for lid in ("L002", "L006"):
        store.get(data, lid)["status"] = "active"
    _, ev = store.add(data, "Test this.", "claims", {"pack": "b"})
    assert ev["id"] == "L006"  # no probation left: lowest active (0.0)
    with pytest.raises(store.LessonsError):
        store.add(data, "Test.", "builder", {"pack": "b"})


# -- credit ----------------------------------------------------------------------------------
CLAIMS = {
    "claims": [
        {"id": "c1", "lessons": ["L001"]},
        {"id": "c2", "lessons": ["L001", "L002"]},
        {"id": "c3", "lessons": []},
        {"id": "c4", "lessons": ["L002"]},
    ]
}
FINDINGS = {
    "scenarios": [
        {"id": "c1", "claim": "c1", "status": "fail", "lessons": ["L001"], "class": "defect"},
        {"id": "c2", "claim": "c2", "status": "fail", "class": "defect"},  # lessons via its claim
        {"id": "c4", "claim": "c4", "status": "fail", "lessons": ["L002"], "class": "retired"},
        {
            "id": "c4b",
            "claim": "c4",
            "status": "fail",
            "lessons": ["L002"],
            "class": "interpretation",
        },
        {"id": "c3", "claim": "c3", "status": "pass", "lessons": ["L003"], "class": None},
    ]
}


def test_credit_counters_from_fixture_findings():
    data = {
        "version": 2,
        "cap": 20,
        "lessons": [
            lesson("L001", "lending"),
            lesson("L002", "lending"),
            lesson("L003", "lending"),
            lesson("L004", "jobq"),
        ],
    }
    inj = cmod.injected_ids(claims=CLAIMS, run={}, data=data, pack="jobq")
    assert inj == ["L001", "L002", "L003"]  # L004 was born on jobq: not shown
    res = cmod.credit(
        data, pack="jobq", claims=CLAIMS, findings=FINDINGS, injected=inj, run_ref="run-1"
    )
    s = {x["id"]: x["stats"] for x in data["lessons"]}
    assert s["L001"] == {
        "exposures": 1,
        "derived": 2,
        "caught": 1,
        "false_alarms": 0,
        "packs_caught_on": ["jobq"],
    }
    assert s["L002"] == {
        "exposures": 1,
        "derived": 2,
        "caught": 1,
        "false_alarms": 1,
        "packs_caught_on": ["jobq"],
    }
    assert s["L003"]["exposures"] == 1 and s["L003"]["caught"] == 0
    assert s["L004"]["exposures"] == 0
    assert store.get(data, "L001")["hit_rate"] == 1.0
    assert res["lessons"]["L001"] == {"exposures": 1, "derived": 2, "caught": 1}
    again = cmod.credit(
        data, pack="jobq", claims=CLAIMS, findings=FINDINGS, injected=inj, run_ref="run-1"
    )
    assert "skipped" in again and s["L001"]["exposures"] == 1  # a run is credited once
    assert cmod.injected_ids(
        claims={"lessons_injected": ["L009"]}, run={}, data=data, pack="x"
    ) == ["L009"]


def test_oracle_json_classes_when_findings_lack_them():
    data = {"version": 1, "cap": 20, "lessons": [lesson("L001", "a")]}
    f = {"scenarios": [{"id": "s1", "claim": "c1", "status": "fail", "lessons": ["L001"]}]}
    cmod.credit(data, pack="b", claims=None, findings=f, injected=["L001"], oracle={"s1": "defect"})
    assert data["lessons"][0]["stats"]["caught"] == 1


def test_probation_and_retirement_rules():
    data = {
        "version": 1,
        "cap": 20,
        "lessons": [
            lesson("L001", "a", exposures=3, caught=2),  # caught on one pack only
            lesson("L002", "a", exposures=3, caught=2),
            lesson("L003", "a", exposures=4, caught=0),  # retire: never caught
            lesson("L004", "a", exposures=3, caught=0, false_alarms=2),
            lesson("L005", "a", "active", exposures=9, caught=2, false_alarms=3),  # retire: 3 > 2
            lesson("L006", "a", "active", exposures=9, caught=3, false_alarms=3),
        ],
    }  # 3 == 3: kept
    store.get(data, "L001")["stats"]["packs_caught_on"] = ["jobq"]
    store.get(data, "L002")["stats"]["packs_caught_on"] = ["jobq", "mdsite"]
    moves = {m["id"]: m["to"] for m in cmod.apply_rules(data, today="2026-10-12")}
    assert moves == {"L002": "active", "L003": "retired", "L005": "retired"}
    assert (
        store.get(data, "L001")["status"] == "probation"
        and store.get(data, "L004")["status"] == "probation"
    )
    store.get(data, "L004")["stats"]["false_alarms"] = 3
    assert {m["id"] for m in cmod.apply_rules(data)} == {"L004"}


# -- post-mortem -----------------------------------------------------------------------------
def toy_score():
    from repo_factory.lessons.pack import Pack

    pack = Pack.load(FIX / "toy")
    rows = {r["id"]: "pass" for r in pack.manifest}
    rows.update(
        {
            "s_count/value_is_1": "fail",
            "s_count/limit_holds_at_3": "fail",
            "s_count/unruled_row": "fail",
            "s_count/either_row": "fail",
            "s_crash/after_crash": "fail",
        }
    )
    return pack, {
        "rows": rows,
        "why": {"s_crash/after_crash": "missing: RuntimeError('boom')"},
        "evidence": {"s_count/value_is_1": "2"},
    }


def toy_traces(d: Path) -> Path:
    t = d / "traces"
    _w(
        t / "s_count.jsonl",
        "\n".join(
            json.dumps(x)
            for x in [
                {
                    "i": 1,
                    "op": "inc",
                    "args": [],
                    "kwargs": {},
                    "result": {
                        "ok": True,
                        "status": 200,
                        "body": {"value": 1},
                        "error": None,
                        "raw": {"v": 1},
                    },
                },
                {"check": "first_inc_ok", "ok": True, "evidence": "''"},
                {
                    "i": 1,
                    "op": "inc",
                    "args": [],
                    "kwargs": {},
                    "result": {
                        "ok": True,
                        "status": 200,
                        "body": {},
                        "error": None,
                        "raw": {"v": 2},
                    },
                },
                {"check": "value_is_1", "ok": False, "evidence": "2"},
                {
                    "i": 1,
                    "op": "inc",
                    "args": [5],
                    "kwargs": {"by": 2},
                    "exception": "ValueError('x')",
                },
                {"check": "limit_holds_at_3", "ok": False, "evidence": "4"},
            ]
        )
        + "\n",
    )
    return t


def test_failing_rows_core_and_interp_with_trace_excerpt(tmp_path):
    pack, score = toy_score()
    rows = pmod.failing_rows(score, pack, toy_traces(tmp_path))
    assert [r["row"] for r in rows] == [
        "s_count/value_is_1",
        "s_count/limit_holds_at_3",
        "s_crash/after_crash",
    ]
    assert [r["scored_as"] for r in rows] == ["core", "interp", "core"]  # unruled/either left out
    r0 = rows[0]
    assert r0["expected"] == "goes up by one" and r0["observed"] == "evidence 2"
    assert r0["trace"][-1].startswith("CHECK value_is_1: ok=False")
    assert len(r0["trace"]) == 3 and '"raw": {"v": 2}' in r0["trace"][1]  # empty body: raw shown
    assert 'inc(5, by=2) -> {"raised": "exception: ValueError' in rows[1]["trace"][-2]
    assert rows[2]["trace"] == [] and "missing" in rows[2]["observed"]


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts, self.usd = reply, [], 0.0

    def __call__(self, prompt):
        self.prompts.append(prompt)
        self.usd += 0.21
        return "Here you go:\n" + json.dumps(self.reply)


def test_postmortem_without_claims_is_all_no_claim_and_keeps_three_clean_lessons(tmp_path, packs):
    pack, score = toy_score()
    lp = _w(
        tmp_path / "lessons.yaml",
        yaml.safe_dump(
            {
                "version": 2,
                "cap": 20,
                "lessons": [dict(lesson("L001", "jobq", "active"), text=GOOD)],
            }
        ),
    )
    reply = {
        "failures": [
            {
                "row": "s_count/value_is_1",
                "miss_type": "claim_wrong_expectation",
                "existing_lesson": None,
                "new_lesson": {
                    "text": "Check the value after each single step, not only at the end.",
                    "applies_to": "verifier",
                },
                "policy_note": "x",
            },
            {
                "row": "s_count/limit_holds_at_3",
                "miss_type": "interpretation",
                "existing_lesson": "L001",
                "new_lesson": {
                    "text": "Test the step that would cross a limit.",
                    "applies_to": "claims",
                },
            },
            {
                "row": "s_crash/after_crash",
                "miss_type": "no_claim",
                "new_lesson": {"text": "Check every member twice.", "applies_to": "claims"},
            },
            {"row": "not/a_row", "miss_type": "no_claim"},
        ],
        "summary": {"no_claim": 1},
    }
    llm = FakeLLM(reply)
    pm = pmod.run(
        score,
        pack,
        traces=toy_traces(tmp_path),
        lessons_path=lp,
        llm=llm,
        run_ref="scratch/toy",
        terms=lmod.domain_terms(),
    )
    f = {x["row"]: x for x in pm["failures"]}
    assert set(f) == {"s_count/value_is_1", "s_count/limit_holds_at_3", "s_crash/after_crash"}
    assert all(x["miss_type"] == "no_claim" for x in pm["failures"])
    assert f["s_count/value_is_1"]["coerced_from"] == "claim_wrong_expectation"
    assert f["s_count/limit_holds_at_3"]["existing_lesson"] == "L001"
    assert f["s_count/limit_holds_at_3"]["new_lesson"] is None  # existing lesson: none new
    assert f["s_count/value_is_1"]["new_lesson"]["applies_to"] == "verifier"
    assert [d["row"] for d in pm["dropped"]] == ["s_count/limit_holds_at_3", "s_crash/after_crash"]
    assert any("domain word 'member'" in p for p in pm["dropped"][1]["problems"])
    assert pm["summary"]["no_claim"] == 3 and pm["usd"] == 0.21
    assert pm["inputs"] == {
        "claims": False,
        "findings": False,
        "rows": 3,
        "lessons_version": 2,
        "note": pm["inputs"]["note"],
    }
    p = llm.prompts[0]
    assert "<<" not in p and "goes up by one" in p and "(absent: this run has no claims.json" in p
    assert "- L001 [active, claims]" in p and "member" in p.split("must not contain")[1]
    # applying: one new probation lesson in the candidate, born on the toy pack, once per run
    data = store.load(lp)
    added = pmod.apply(pm, data, terms=lmod.domain_terms(), today="2026-10-12")
    assert [x["id"] for x in added] == ["L002"] and added[0]["status"] == "probation"
    assert added[0]["born"] == {
        "date": "2026-10-12",
        "pack": "toy",
        "run": "scratch/toy",
        "row": "s_count/value_is_1",
    }
    assert pmod.apply(pm, data) == []


def test_postmortem_limits_new_lessons_to_three_and_one_per_failure(tmp_path, packs):
    from repo_factory.lessons.pack import Pack

    pack = Pack.load(FIX / "toy")
    score = {"rows": {r["id"]: "fail" for r in pack.manifest}, "why": {}, "evidence": {}}
    rows = pmod.failing_rows(score, pack, None)
    texts = [
        "Test the first step.",
        "Test the last step.",
        "Test a repeated step.",
        "Test a step out of order.",
        "Test a step after a long pause.",
    ]
    reply = {
        "failures": [
            {
                "row": r["row"],
                "miss_type": "found_not_fixed",
                "new_lesson": {"text": t, "applies_to": "claims"},
            }
            for r, t in zip(rows, texts)
        ]
    }
    out = pmod.check(
        reply,
        rows,
        claims={"claims": []},
        findings={"scenarios": []},
        lessons=[],
        terms={},
        hidden=[],
    )
    assert sum(1 for f in out["failures"] if f["new_lesson"]) == 3
    assert any("over the limit of 3" in p for d in out["dropped"] for p in d["problems"])
    assert len(rows) == 7 and out["summary"]["found_not_fixed"] == 5  # allowed with findings
    assert [f["row"] for f in out["failures"] if f.get("defaulted")] == [r["row"] for r in rows[5:]]
    assert out["summary"]["no_claim"] == 2


def test_postmortem_refuses_held_out_packs(packs):
    from repo_factory.lessons.pack import Pack

    with pytest.raises(pmod.PostmortemError, match="held-out"):
        pmod.run({"rows": {}}, Pack.load("shiftboard"), traces=None, llm=FakeLLM({}))


def test_postmortem_prompt_from_policy_has_every_placeholder():
    text = (store.POLICY / "prompts" / "postmortem.md").read_text()
    for k in ("spec", "failures", "claims", "findings", "lessons"):
        assert f"<<{k}>>" in text
    for t in pmod.MISS_TYPES:
        assert f"`{t}`" in text
    assert store.load(store.default_path())["lessons"] == []  # version 0: empty


def test_cli_postmortem_score_dir_applies_to_candidate(tmp_path, packs, monkeypatch, capsys):
    from repo_factory.cli import main

    pack, score = toy_score()
    sd = tmp_path / "score"
    _w(sd / "score.json", score)
    toy_traces(sd)
    reply = {
        "failures": [
            {
                "row": "s_count/value_is_1",
                "miss_type": "no_claim",
                "new_lesson": {
                    "text": "Check the value after each single step.",
                    "applies_to": "claims",
                },
            }
        ]
    }
    fake = FakeLLM(reply)
    monkeypatch.setattr(pmod, "ClaudeCall", lambda model=None: fake)
    cand = tmp_path / "cand.yaml"
    main(
        [
            "lessons",
            "postmortem",
            "--score-dir",
            str(sd),
            "--pack",
            str(FIX / "toy"),
            "--lessons",
            str(tmp_path / "none.yaml"),
            "--candidate",
            str(cand),
        ]
    )
    out = capsys.readouterr().out
    assert "s_count/value_is_1 [core]: no_claim" in out and "added L001" in out
    pm = json.loads((sd / "postmortem.json").read_text())
    assert pm["applied"]["version"] == 1 and store.load(cand)["lessons"][0]["status"] == "probation"
    main(["lessons", "list", "--file", str(cand), "--lint"])
    assert "L001 [probation]" in capsys.readouterr().out


# -- promotion -------------------------------------------------------------------------------
def bench_with_runs(root: Path, v5_fail: dict) -> Path:
    for pack in ("lending", "jobq", "shiftboard"):
        u = root / "runs" / "e3" / pack / "s1"
        _w(u / "base" / "run.json", {"usd": {"total": 3.0}, "profile": PROFILE})
        _w(u / "vanilla" / "score.json", {"core": {"passed": 40, "total": 50}})
        _w(
            u / "v5" / "run.json",
            {
                "usd": {"base": 3.0, "arm": 4.0, "total": 7.0},
                "profile": PROFILE,
                "lessons_version": 0,
                "policy": "v5.0",
            },
        )
        _w(u / "v5" / "score.json", {"core": {"passed": 50 - v5_fail[pack], "total": 50}})
    return root


def policy_with_candidate(d: Path) -> Path:
    store.save(d / "lessons.yaml", store.empty())
    data, cand = store.open_candidate(policy=d)
    store.add(data, GOOD, "claims", {"pack": "lending", "run": "x", "row": "y"}, today="2026-10-12")
    store.save(cand, data)
    return d


def fakes(new_fail: dict, usd=4.1):
    calls = []

    def run(unit, spec, *, lessons, arm, max_usd, bench):
        calls.append((unit.parent.name, arm, Path(lessons).name))
        rj = {
            "usd": {"base": 3.0, "arm": usd, "total": 3.0 + usd},
            "profile": PROFILE,
            "policy": "v5.0",
        }
        _w(unit / arm / "run.json", rj)
        return rj

    def score(unit, arm):
        s = {"core": {"passed": 50 - new_fail[unit.parent.name], "total": 50}, "valid": True}
        _w(unit / arm / "score.json", s)
        return s

    return run, score, calls


# -- history ---------------------------------------------------------------------------------
def history_bench(root: Path) -> Path:
    for pack, seed, vf, rf, v5f, v4f in (
        ("lending", 1, 6, 4, 2, 3),
        ("lending", 2, 5, 5, 3, None),
        ("shiftboard", 1, 7, 6, 5, None),
    ):
        u = root / "runs" / "e3" / pack / f"s{seed}"
        _w(
            u / "base" / "run.json",
            {
                "usd": {"base": 3.0, "arm": 0, "total": 3.0},
                "profile": PROFILE,
                "wall_s": {"base": 1000},
            },
        )
        _w(
            u / "vanilla" / "score.json",
            {
                "core": {"passed": 50 - vf, "total": 50},
                "interp": {"passed": 3},
                "measurement_usd": 1.0,
                "scored_at": "2026-10-10T10:00:00+00:00",
            },
        )
        _w(
            u / "self-review" / "r1" / "run.json",
            {"usd": {"base": 3.0, "arm": 4.0, "total": 7.0}, "profile": PROFILE},
        )
        _w(u / "self-review" / "r1" / "score.json", {"core": {"passed": 50 - rf, "total": 50}})
        _w(
            u / "self-review" / "r2" / "run.json",
            {"usd": {"base": 3.0, "arm": 9.0, "total": 12.0}, "profile": PROFILE},
        )
        _w(u / "self-review" / "r2" / "score.json", {"core": {"passed": 50, "total": 50}})
        _w(
            u / "v5" / "run.json",
            {
                "usd": {"base": 3.0, "arm": 4.5, "total": 7.5},
                "profile": PROFILE,
                "policy": "v5.0",
                "lessons_version": 0,
                "wall_s": {"base": 1000, "arm": 2000},
            },
        )
        _w(
            u / "v5" / "score.json",
            {"core": {"passed": 50 - v5f, "total": 50}, "measurement_usd": 0.5},
        )
        _w(u / "v5" / "prediction.json", {"p_hidden": 0.9})
        _w(
            u / "v5" / "postmortem.json",
            {"usd": 0.2, "failures": [{"miss_type": "no_claim"}] * v5f},
        )
        if v4f is not None:
            _w(u / "v4" / "run.json", {"usd": 12.0, "policy": "v4", "profile": PROFILE})
            _w(u / "v4" / "score.json", {"core": {"passed": 50 - v4f, "total": 50}})
    return root
