"""A synthetic events.jsonl with every event type events.fold reads, each carrying the fields it
reads, in an order a build could write them (S-14's characterisation input). Two builds: a v3/v4
style one (plan, candidates, milestones, tasks, controller, rules, v4 events) and a v5 one, so
both branches of fold run. Deterministic."""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
evs = []
ts = [1_760_000_000.0]


def ev(t, build, **kw):
    ts[0] += 1.5
    evs.append({"v": 1, "ts": round(ts[0], 3), "task": "t-every", "build": build, "type": t, **kw})


# -- build 1: the older pipelines (v3/v4 shapes) -----------------------------------------------
b = 1
ev("build_started", b, spec="spec.md", title="Every kind", policy="policies/v4", harness="fake",
   pid=4242, taskdir="/nowhere/task", note="first", parent=None, pipeline="v4", cap_usd=5)
ev("question", b, id="q1", text="Which store?", n=1, of=2, auto_accept_s=30)
ev("question", b, id="plan", text="The plan", n=2, of=2)
ev("answer", b, id="q1", text="sqlite")
ev("answer", b, id="q9", text="an answer to a question never logged")
ev("tests_generated", b, visible=12, held_back=4)
ev("assumptions", b, items=["one user", "local only"])
ev("adr", b, decision="sqlite", stack="python", candidates=["sqlite", "json"])
ev("plan_reviewed", b, gaps=["auth"], applied=1, error=None)
ev("plan", b, version=1, replanned=False, milestones=[
    {"id": "m1", "title": "Core", "size": "s", "tasks": [
        {"id": "t1", "title": "Model", "uncertainty": "design", "size": "s"},
        {"id": "t2", "title": "CLI", "size": "m"}]},
    {"id": "m2", "title": "Polish", "size": "m"}])
ev("call_started", b, call="c1", node="n1", kind="worker", label="w1", caller="build", model="m")
ev("call_finished", b, call="c1", node="n1", kind="worker", usd=0.12, ok=True, caller="build",
   wall_s=3.0, tokens_in=100, tokens_out=50)
ev("milestone_started", b, id="m1", title="Core", n=1, size="s", max_turns=40, max_usd=2)
ev("task_started", b, milestone="m1", task_id="t1", title="Model", kind="design", n=1,
   hints=["approach a", "approach b"])
ev("brief", b, candidate=1, unit="t1", kind="worker", model="m", usd=0.01, cached=False,
   summary="brief", lint=[], text="do it")
ev("candidate_started", b, candidate=1, run="r1", strategy="fresh", kind="worker", parent=None,
   milestone="m1", reason="first", task_id="t1", hint=0, fix_items=["x"])
ev("candidate_started", b, candidate=2, run="r2", strategy="fork", kind="worker", parent=1,
   task_id="t1", hint=1)
ev("candidate_finished", b, candidate=1, node="n1", valid=True, score=0.9, user_tests=10,
   acceptance=0.8, coverage=0.7, dropped_tests=0, usd=0.5, exit_reason="done",
   rules={"active": 2}, model="m", milestone="m1", gate={"passed": True}, reason="ok")
ev("candidate_finished", b, candidate=2, node="n2", valid=False, score=0.1, usd=0.25)
ev("candidates_rescored", b, candidates=[{"node": "n1", "valid": True, "score": 0.95,
                                          "coverage": 0.75, "rules": {"active": 3}}],
   dropped_tests=1)
ev("task_finished", b, milestone="m1", task_id="t1", kind="design", n=1, passed=True,
   winner="n1", reason="best", coverage=0.75, usd=0.75, runs=2, stage="judge",
   candidates=[{"node": "n1"}, {"node": "n2"}])
ev("task_finished", b, milestone="m1", task_id="t3", kind="routine", n=3, passed=False)
ev("controller_decision", b, at="m1", epoch=0, milestone="m1", task_id="t1", option="keep",
   label="Keep going", by="rules", reason="green", ambiguous=False, menu=["keep"], params={})
ev("adr_revised", b, revision=1)
ev("backtrack", b, number=1, kind="design", label="Backtrack", node="n1", target="m1",
   from_milestone="m1", change="store", reason="slow", by="rules", at="m1", discard=["n2"],
   flaws=["slow"])
ev("replan", b, version=2, interface_changed=True, kept=["m1"], retired=["m2"], summary="s")
ev("adr_revised", b, revision=2)
ev("plan", b, version=2, replanned=True, backtrack=1, kept=["m1"], milestones=[
    {"id": "m1", "title": "Core", "size": "s"}, {"id": "m3", "title": "New", "size": "s"}])
ev("milestone_finished", b, id="m1", title="Core", n=1, node="n1", passed=True, coverage=0.8,
   slice="all", usd=1.0, attempts=1, attempt_reasons=[])
ev("milestone_started", b, id="review1", title="Review", n=3)
ev("milestone_finished", b, id="m3", title="New", n=2, passed=False)
ev("budget_warning", b, milestone="m3", task_id="t9", n=1, spent_usd=4, committed_usd=1,
   budget_usd=5)
ev("design_started", b, search="wide", stand_in=False)
ev("design_finished", b, dimensions=3, forked=1, chosen="a", gaps=0, revisions=1, usd=0.2,
   wall_s=10, stand_in=False)
ev("build_once_started", b, milestone="m1", epoch=0, max_turns=50, max_usd=3, model="m")
ev("build_once_finished", b, milestone="m1", epoch=0, node="n3", session_id="s1", usd=1.1,
   wall_s=60, turns=20, exit_reason="done", valid=True, user_tests=12, acceptance=0.9,
   coverage=0.8, held_back=4, passed=True)
ev("build_once_finished", b, milestone="m9", epoch=1, node="n9")
ev("build_once_started", b, milestone="m1", epoch=1)
ev("mining_started", b, point="harden1.bt1", after="n3", modes=["a"], **{"async": False},
   active=2)
ev("mining_finished", b, point="harden1.bt1", caught=1, mined=3, fix_items=["f"], usd=0.3,
   error=None)
ev("rules_mined", b, proposed=3, error=None)
ev("rules_mined", b, proposed=1, error="parse")
ev("rule_status", b, id="r1", to="active", visibility="held")
ev("rule_status", b, id="r2", to="active", visibility="visible")
ev("rule_status", b, id="r3", to="retired")
ev("rule_status", b, id="r4", to="disputed")
ev("rule_status", b, id="r5", to="candidate")
ev("rule_promoted", b, id="r5")
ev("review_round", b, round=1, rules=5, passed=4, code_wrong=1, fixed=1, stop=None)
ev("harden_round", b, round=1, epoch=1, head="n3", node="n4", mined=3, failing=1,
   code_wrong=1, repaired=1, status="repairing", design_flaws=[], design_rules=[])
ev("harden_round", b, round=1, epoch=1, accepted=True, status="done", before=1, after=0)
ev("harden_round", b, round=2, epoch=None, accepted=False, status="done")
ev("harden_round", b, round=3, stop="budget", status="done")
ev("backtrack_design", b, number=1, rule="r1", dimension="store", **{"from": "a"}, to="b",
   reason="slow", kept=["x"], why="w", rebuild=True, final=False, status="running")
ev("backtrack_design", b, number=1, status="done", final=True)
ev("delivered", b, node="n4", branch="repo-factory/every", out="/nowhere/out", commit="abc",
   report="REPORT.md", held_back=4, changed=["a.py"], rules={"active": 2})
ev("verdict", b, verdict="accept", note="good", source="ui", node="n4")
ev("build_finished", b, status="ok", wall_s=300, error=None, stage=None, failure=None,
   cost_usd=3.21, cost_breakdown={"workers": 2}, worker_cost_usd=2.0)

# -- build 2: v5 -----------------------------------------------------------------------------
b = 2
ev("revise_started", b, spec="spec.md", title="Every kind", policy="policies/v5", harness="claude",
   pid=4343, pipeline="v5", cap_usd=8, kind="revise", revises=1, origin="ui", mode="docs",
   instructions="tighten", stages=["scope", "build", "verify", "repair", "deliver"])
ev("revise_outcome", b, changed=True, before="a", after="b", what="docs")
ev("study", b, status="done", wall_s=4, tools_by_server={"rea": 2}, capabilities=["rea"],
   path="study.md")
ev("capability", b, name="rea", origin="owner", why="asked", stages=["build"], ref="v1")
ev("capability", b, name="rea", origin="owner", why="again", stages=["build", "verify"], ref="v2")
ev("prompt", b, id="build", version=3, n=2, custom=True, template="t", additions=1,
   house_rules=True, stage="build")
ev("v5_stage", b, stage="scope", status="started")
ev("scope", b, decision="go", why="small", mode="auto", verdict="ok", called=True, flows=2,
   dod=["x"], must=["y"], settings={}, words=100, shared_base=False, path="scope.json")
ev("v5_stage", b, stage="scope", status="done", usd=0.1, wall_s=5, tokens_in=10,
   tokens_out=5, prompts={"scope": 1})
ev("v5_stage", b, stage="build", status="started")
ev("v5_live", b, stage="build", tool_calls=10, by_server={"rea": 1}, last_tool="Edit", at=1,
   notes=[], evidence=[], denied=0)
ev("v5_stage", b, stage="build", status="done", usd=1.0, wall_s=100, tokens_in=1000,
   tokens_out=500, tools_by_server={"rea": 3}, skills={"s": 1}, agents={"a": 2},
   capabilities=["rea"])
ev("v5_stage", b, stage="build", status="started", why="again")
ev("v5_stage", b, stage="build", status="done", usd=0.5, wall_s=50, tools_by_server={"rea": 1},
   skills={"s": 2})
ev("claims", b, n=5, ambiguities=1, samples=["c"], dedupe=0, lessons=[])
ev("interpretation", b, item="i1", mode="ask", chosen="a", forked=False, implements="x",
   stakes="low", kind="setting", setting="s", status="chosen")
ev("v5_stage", b, stage="verify", status="started")
ev("findings", b, stage="verify", confirmed=2, interpretation=0, settings=0, retired=0,
   incomplete=0, environment=0, completeness=1, blocked=0, scenarios=10, failing=2,
   new_failing=2)
# the retired mutants stage's event: an old log has it; fold reads it no more (AU1 #4)
ev("mutation", b, score=0.8, survivors=1, applied=5, killed=4, new_failing=0)
ev("v5_stage", b, stage="unknown_stage", status="started")
ev("repair_round", b, round=1, status="repairing", sent=2, fixed=0, regressions=0,
   conflicts=0, reverted=0, accepted=None, why=None, settings={}, session="s")
ev("conflict", b, round=1, x="a", y="b", group="g", verdict="keep", action="revert",
   x_rank=1, y_rank=2)
ev("repair_round", b, round=1, status="done", sent=2, fixed=2, accepted=True)
# the retired p_hidden prediction: an old log has it; fold reads it no more (AU1 #5)
ev("prediction", b, p_hidden=0.9, interval80=[0.8, 0.95], expected_hidden_failures_per_100=3,
   features={"a": 1})
ev("security", b, critical=0, high=1, medium=0, low=2, ran=True, confirmed=1, unconfirmed=0,
   probes=3, audit="a")
ev("security", b, ran=True)
ev("readiness", b, step="checks", green=5, amber=1, red=1, na=0, security="ok")
ev("readiness", b, step="repair", status="repairing", sent=2, fixed=0)
ev("readiness", b, step="repair", status="done", sent=2, fixed=2, regressions=0,
   accepted=True, why=None, updated=True)
ev("readiness", b, step="final", green=7, amber=0, red=0, na=0)
ev("build_finished", b, status="error", wall_s=200, error="stopped", stage="readiness",
   failure="stopped")
ev("build_resumed", b, pid=4444)
ev("v5_stage", b, stage="deliver", status="started")
ev("delivered", b, node="n9", branch="repo-factory/every", out="/nowhere/out", commit="def")
ev("build_finished", b, status="ok", wall_s=20)

# -- build 3: still running, a question open -----------------------------------------------------
b = 3
ev("build_started", b, title="Running", pipeline="v5", stages=["build"])
ev("question", b, id="q2", text="Ok?", n=1, of=1)
ev("call_started", b, call="c9", node="n9", kind="worker", label="w9", caller="build")

out.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in evs))
print(len(evs), "events,", len({e['type'] for e in evs}), "types")
