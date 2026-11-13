"""``python -m repo_factory.rules <code_dir> --spec spec.md ...``: mine, validate,
score and arbitrate rules against existing code, outside a build (a real check of the
miner on delivered code). Prints a JSON summary; the store is left in ``--store``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import RulesEngine
from .llm import ClaudeLLM
from .miner import Context
from .store import RuleStore


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m repo_factory.rules")
    ap.add_argument("code_dir")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--interface", help="file with the plan's public interface")
    ap.add_argument("--plan", help="JSON list of milestones [{id, title, build}]")
    ap.add_argument("--answers", help='JSON {"q1": {"q": question, "a": answer}}')
    ap.add_argument("--modes", default="interactions,edges")
    ap.add_argument("--store", required=True)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--requirements", default="", help="comma-separated task requirements")
    ap.add_argument("--held-fraction", type=float, default=0.25)
    ap.add_argument("--no-arbitrate", action="store_true")
    ap.add_argument(
        "--after",
        nargs="*",
        default=None,
        help="mine once per milestone id given, focused on it (as a build does "
        "after each milestone); default: once, after the last milestone",
    )
    ap.add_argument("--n", type=int, default=None, help="rules per mining call")
    a = ap.parse_args(argv)
    code = Path(a.code_dir).resolve()
    answers, questions = {}, {}
    if a.answers:
        for qid, v in json.loads(Path(a.answers).read_text()).items():
            answers[qid], questions[qid] = v.get("a"), v.get("q", qid)
    plan = json.loads(Path(a.plan).read_text()) if a.plan else []
    ctx = Context(
        spec=Path(a.spec).read_text(),
        answers=answers,
        questions=questions,
        interface=Path(a.interface).read_text() if a.interface else "",
        milestones=plan,
    )
    llm = ClaudeLLM(a.model)
    events = []
    store = RuleStore(Path(a.store), a.held_fraction)
    eng = RulesEngine(
        store,
        llm,
        ctx,
        [r for r in a.requirements.split(",") if r],
        cfg={"n": {m: a.n for m in ("requirements", "interactions", "edges")}} if a.n else None,
        emit=lambda t, **f: events.append({"type": t, **f}),
    )
    built = [m["id"] for m in plan] or None
    last = plan[-1]["id"] if plan else None
    mined = [
        eng.mine(m.strip(), code, None, after, built or ())
        for after in (a.after or [last])
        for m in a.modes.split(",")
        if m.strip()
    ]
    results = eng.score(code, None, built)
    arbitration = {} if a.no_arbitrate else eng.arbitrate(code, results, None, built)
    rules = []
    for r in store.rules.values():
        rules.append(
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind,
                "status": r.status,
                "visibility": r.visibility,
                "milestones": r.milestones,
                "reason": r.reason,
                "source": r.source,
                "expect": r.expect,
                "on_code": (results.get(r.id) or {}).get("status"),
                "verdict": (r.verdicts[-1]["verdict"] if r.verdicts else None),
                "verdict_reason": (r.verdicts[-1].get("reason") if r.verdicts else None),
            }
        )
    out = {
        "mined": mined,
        "score": eng.summary(results, built),
        "arbitration": arbitration,
        "store": store.summary(),
        "llm_calls": llm.calls,
        "usd": round(llm.usd, 4),
        "llm_log": llm.log,
        "rules": rules,
    }
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
