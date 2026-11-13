"""A revise round of a delivered v5 build (AR-PLAN.md §2), offline: the round it revises is a
whole v5 build through the scripted harness (``test_v5_pipeline``), built once for the module
and restored before each test; the change and repair sessions are scripted here. The suite
runs for real (sandboxed, network off)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from repo_factory.events import read_events
from repo_factory.loop import gate
from repo_factory.v5 import change, commands, pipeline

from . import test_v5_pipeline as base

OWN = """from textstats.core import char_count, word_count


def test_words():
    assert word_count("a b") == 2


def test_chars():
    assert char_count("ab") == 2


def test_chars_trimmed():
    assert char_count(" ab ") == 2
"""
FULL = {"done": 3, "total": 3, "flows": []}


def full(self):  # every flow works: the gate reads tests
    return dict(FULL)


def head(out: Path, ref: str = "repo-factory/textstats") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=out, capture_output=True, text=True
    ).stdout.strip()


def sh(ws: Path, rel: str, old: str, new: str) -> None:
    p = ws / rel
    p.write_text(p.read_text().replace(old, new))


def note(ws: Path, what: str) -> None:
    (ws / ".repo-factory").mkdir(exist_ok=True)
    (ws / ".repo-factory" / "change.json").write_text(
        json.dumps({"what": what, "why": "asked", "documents": []})
    )


class Changer(base.Scripted):
    """The revise round's sessions: ``edit(ws)`` is the change session, ``fix(ws)`` the
    repair session; the round being revised never runs a session here."""

    def __init__(self, edit, fix=None):
        super().__init__()
        self.edit, self.fix = edit, fix

    def run(self, prompt, workspace, timeout_s, **opts):
        ws, s = Path(workspace), opts.get("session") or {}
        if "You are changing" in prompt:
            self.runs.append(("change", opts))
            self.edit(ws)
            note(ws, "the change")
            return self.result(ws, s, 0.2)
        if "The checks after your change found problems" in prompt:
            self.runs.append(("change-repair", opts))
            if self.fix:
                self.fix(ws)
            return self.result(ws, s, 0.1)
        return super().run(prompt, workspace, timeout_s, **opts)


@pytest.fixture(scope="module")
def delivered(tmp_path_factory):
    root = tmp_path_factory.mktemp("change") / "w"
    root.mkdir()
    task = root / "task"
    shutil.copytree(base.FIXTURE, task)
    shutil.rmtree(task / "_fake_solution")
    sol = root / "solution"
    (sol / "textstats").mkdir(parents=True)
    (sol / "textstats" / "__init__.py").write_text("")
    (sol / "textstats" / "core.py").write_text(base.BUGGY)
    (sol / "tests").mkdir()
    (sol / "tests" / "test_own.py").write_text(OWN)
    h = base.Scripted()
    h.fake.solution = sol
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline.Run, "completeness", full)
        out = pipeline.build(task, out=root / "out", harness=h, llm=base.llm(), policy=base.POLICY)
    assert out["failed"] is None and out["round"] == 1
    snap = root.parent / "snap"
    shutil.copytree(root, snap, symlinks=True)
    return root, snap


@pytest.fixture
def world(delivered, monkeypatch):
    root, snap = delivered
    monkeypatch.setattr(pipeline.Run, "completeness", full)
    yield root / "task", root / "task" / ".den", root / "out"
    shutil.rmtree(root)
    shutil.copytree(snap, root, symlinks=True)


def revise(
    task,
    den,
    h,
    note_="Fix the failing own test: char_count must ignore the spaces around the text.",
    **kw,
):
    return change.revise(task, note_, den=den, harness=h, base_policy=base.POLICY, **kw)


def round_events(res, rnd):
    return [e for e in read_events(Path(res["events"])) if e.get("build") == rnd]


@pytest.mark.skipif(
    not commands.available(),
    reason="needs bwrap: the gate's tests check is "
    "the README's commands, which run only in the sandbox",
)
def test_a_sidedoor_shaped_revise_makes_the_own_tests_green(world):
    task, den, out = world
    g1 = gate.evaluate(task)
    assert not g1["checks"]["tests"]["ok"] and g1["checks"]["tests"]["detail"].startswith(
        "a documented test command fails as written"
    )
    old = head(out)
    h = Changer(
        lambda ws: sh(ws, "textstats/core.py", "return len(text)", "return len(text.strip())")
    )
    res = revise(task, den, h, mode="code")
    assert res["finished"] and res["failed"] is None, res
    assert (res["round"], res["revises"], res["mode"]) == (2, 1, "code")
    assert [r[0] for r in h.runs] == ["change"]
    g2 = gate.evaluate(task)
    assert g2["round"] == 2
    if "no bwrap sandbox" not in g2["checks"]["tests"]["detail"]:  # a CI runner without
        assert g2["checks"]["tests"]["ok"], g2["checks"]["tests"]  # bwrap can't run the
        assert g2["verdict"] == "ready", g2["reasons"]  # README's commands
    # (g) the delivered branch gets a new commit; the old head is its parent
    assert head(out, "repo-factory/textstats^") == old and head(out) != old
    assert "len(text.strip())" in (out / "textstats" / "core.py").read_text()
    assert not (out / ".repo-factory").exists()
    evs = round_events(res, 2)
    start = evs[0]
    assert start["type"] == "build_started" and start["kind"] == "revise"
    assert (start["revises"], start["origin"], start["mode"]) == (1, "cli", "code")
    skipped = {e["stage"] for e in evs if e["type"] == "v5_stage" and e["status"] == "skipped"}
    assert {"scope", "claims", "oracle", "repair", "reverify"} <= skipped
    done = {e["stage"] for e in evs if e["type"] == "v5_stage" and e["status"] == "done"}
    assert {"build", "verify", "deliver"} <= done
    oc = next(e for e in evs if e["type"] == "revise_outcome")
    assert oc["before"]["own_failed"] == 1 and oc["after"]["own_failed"] == 0
    assert oc["before"]["total"] == oc["after"]["total"] >= 3
    assert "textstats/core.py" in oc["changed"]
    assert evs[-1]["type"] == "build_finished" and evs[-1]["status"] == "ok"
    # round 1's record is as it was
    assert json.loads((den / "v5" / "r1" / "readiness.json").read_text()) != json.loads(
        (den / "v5" / "r2" / "readiness.json").read_text()
    )
    assert change.base_round(den) == 2


def test_b_driftwatch_shaped_docs_mode_changes_markdown_only(world):
    task, den, out = world
    old = head(out)
    code = (out / "textstats" / "core.py").read_text()
    h = Changer(
        lambda ws: sh(
            ws,
            "README.md",
            "ASCII words only.",
            "Words are split on spaces; other scripts are not handled.",
        )
    )
    res = revise(
        task,
        den,
        h,
        mode="docs",
        note_="README line 'ASCII words only': say words are split on spaces.",
    )
    assert res["finished"] and res["failed"] is None, res
    assert "Words are split on spaces" in (out / "README.md").read_text()
    assert (out / "textstats" / "core.py").read_text() == code
    diff = subprocess.run(
        ["git", "diff", "--name-only", old, "repo-factory/textstats"],
        cwd=out,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "README.md" in diff and not [
        f for f in diff if not f.endswith(".md") and not f.startswith("tests/acceptance/")
    ]
    prompt = h.runs[0][1]
    assert h.runs[0][0] == "change" and prompt["max_turns"] == 30  # the docs caps
    # AU1 #8: no baseline, and the delivered tree is not measured again (only Markdown changed)
    evs = round_events(res, 2)
    assert not [e for e in evs if e["type"] == "exam_run"]
    deliver = next(
        e
        for e in evs
        if e["type"] == "v5_stage" and e["stage"] == "deliver" and e["status"] == "done"
    )
    assert deliver["why"] == "Exam reused from round 1: only documentation changed"
    assert next(e for e in evs if e["type"] == "revise_outcome")["exam_reused"] == 1
    assert "Exam reused from round 1" in (out / "REPORT.md").read_text()
    assert gate.evaluate(task)["round"] == 2  # the gate judges as usual


def test_c_docs_mode_that_edits_code_fails_after_one_repair_and_leaves_out_alone(world):
    task, den, out = world
    old = head(out)
    h = Changer(lambda ws: sh(ws, "textstats/core.py", "return len(text)", "return 0"))
    res = revise(task, den, h, mode="docs", note_="README: say it handles ASCII words only.")
    assert res["finished"] and res["failed"]["stage"] == "reverify"
    assert [r[0] for r in h.runs] == ["change", "change-repair"]
    assert head(out) == old and "return len(text)" in (out / "textstats" / "core.py").read_text()
    evs = round_events(res, 2)
    assert evs[-1]["type"] == "build_finished" and evs[-1]["status"] == "error"
    assert evs[-1]["error"].startswith("revise failed in reverify: ")
    assert "docs mode changes Markdown only" in evs[-1]["error"]
    assert change.base_round(den) == 1  # the failed round is not a base


def test_d_a_regression_is_repaired(world):
    task, den, out = world

    def edit(ws):  # fixes chars, breaks words
        sh(ws, "textstats/core.py", "return len(text)", "return len(text.strip())")
        sh(ws, "textstats/core.py", "return len(text.split())", "return len(text.split()) + 1")

    h = Changer(
        edit,
        fix=lambda ws: sh(ws, "textstats/core.py", "len(text.split()) + 1", "len(text.split())"),
    )
    res = revise(task, den, h, mode="code")
    assert res["finished"] and res["failed"] is None, res
    assert [r[0] for r in h.runs] == ["change", "change-repair"]
    assert h.runs[1][1]["session"]["resume"]  # the repair resumes the change session
    found = res["change"]["found"]
    assert not found  # re-verified clean
    evs = round_events(res, 2)
    first = next(e for e in evs if e["type"] == "findings")
    assert first["confirmed"] >= 1


def test_e_what_a_revise_may_never_change():
    bad = change.violations(
        [
            "tests/acceptance/test_x.py",
            "LICENSE",
            "NOTICE",
            ".github/workflows/ci.yml",
            ".github/repo-factory.yml",
            "README.md",
            "app.py",
        ],
        "code",
    )
    for f in ("LICENSE", "NOTICE", ".github/workflows/ci.yml", ".github/repo-factory.yml"):
        assert any(b.startswith(f + ":") for b in bad), f
    # repo-factory's exam is not in the tree (EX-PLAN §3.4): a test there is the product's own
    assert not any(b.startswith(("README.md", "app.py", "tests/acceptance")) for b in bad)
    assert any(b.startswith("app.py:") for b in change.violations(["app.py"], "docs"))
    assert not change.violations(["README.md", "docs/guide.md"], "docs")


def test_f_a_round_killed_after_build_resumes_at_verify(world):
    task, den, out = world
    edit = lambda ws: sh(
        ws,
        "textstats/core.py",
        "return len(text)",  # noqa: E731
        "return len(text.strip())",
    )
    h = Changer(edit)
    first = revise(task, den, h, mode="code", steps=1)
    assert not first["finished"] and first["done_steps"] == ["build"]
    assert change.base_round(den) == 1
    h2 = Changer(lambda ws: pytest.fail("the change session ran twice"))
    res = change.revise(
        task, "", den=den, harness=h2, base_policy=base.POLICY, resume=first["round"]
    )
    assert res["finished"] and res["failed"] is None, res
    assert h2.runs == [] and [r[0] for r in h.runs] == ["change"]
    evs = round_events(res, first["round"])
    assert [e["type"] for e in evs].count("build_started") == 1
    assert any(e["type"] == "build_resumed" for e in evs)


def test_pick_mode_is_docs_only_for_wording_holds():
    ready = {"verdict": "ready", "checks": {"tests": {"ok": True}}}
    held = {"verdict": "held", "checks": {"tests": {"ok": False}}}
    assert change.pick_mode(ready, "not pushed: honesty check: 'x': y") == "docs"
    assert change.pick_mode(ready, {"honesty": {"ok": False}, "jargon": [{"file": "R"}]}) == "docs"
    assert change.pick_mode(ready, {"honesty": {"ok": False}, "secrets": [{"file": "c"}]}) == "code"
    assert change.pick_mode(held, "not pushed: honesty check") == "code"
    assert change.pick_mode(ready, None) == "code"
