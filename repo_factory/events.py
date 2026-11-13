"""The build event log: ``<den>/events.jsonl``, one JSON object per line.

This is a contract (see ``docs/events.md``): ``repo-factory status``, the terminal renderer and
any UI read it. Writers append; nothing rewrites a line. ``fold`` turns the events of
the latest build into a plain state dict.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

SCHEMA_VERSION = 1
FILENAME = "events.jsonl"
START_TYPES = ("build_started", "revise_started")

_log = logging.getLogger("repo_factory.events")


class EventLog:
    """Appends events for one build to ``path`` and passes each to its subscribers.

    ``emit`` is thread-safe: workers run in parallel and each line is written and
    flushed under a lock, so lines never interleave."""

    def __init__(
        self,
        path: Path | None,
        task: str,
        build: int,
        subscribers: list[Callable[[dict], None]] | None = None,
    ):
        self.path = Path(path) if path else None
        self.task, self.build = task, build
        self.subscribers = list(subscribers or [])
        self.lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, **fields) -> dict:
        ev = {
            "v": SCHEMA_VERSION,
            "ts": round(time.time(), 3),
            "task": self.task,
            "build": self.build,
            "type": type,
            **fields,
        }
        with self.lock:
            if self.path:
                with open(self.path, "a") as f:
                    f.write(json.dumps(ev, default=str) + "\n")
            for sub in self.subscribers:
                try:
                    sub(ev)
                except Exception as e:  # noqa: BLE001 - a broken renderer must never kill a build
                    _log.warning("event %s: subscriber %r failed: %s", type, sub, e)
        return ev

    def __call__(self, type: str, fields: dict) -> None:
        """The ``ForageConfig.on_event`` callback shape: ``(type, fields)``."""
        self.emit(type, **fields)


def log_path(den_root: Path) -> Path:
    return Path(den_root) / FILENAME


def resolve_log(target: str | Path | None) -> Path:
    """A task dir (uses ``<taskdir>/.den``), a den dir, or an ``events.jsonl`` path."""
    p = Path(target or ".")
    if p.is_file():
        return p
    if (p / ".den").is_dir():
        return log_path(p / ".den")
    return log_path(p)


def read_events(path: Path) -> list[dict]:
    """All events in the log; a torn last line (writer still mid-write) is skipped."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and "type" in ev:
            out.append(ev)
    return out


def latest_build(events: list[dict]):
    starts = [e for e in events if e["type"] in START_TYPES]
    return starts[-1]["build"] if starts else None


# -- activity: what is running now (the UI's Activity panel, ``repo-factory status``) -------------
PHASES = (
    "questions",
    "tech selection",
    "planning",
    "plan review",
    "designing",
    "tests",
    "building",
    "closing review",
    "hardening",
    "verifying",
    "repairing",
    "delivering",
    "done",
)
RECENT = 8  # finished items kept in ``activity["recent"]``
COST_CATEGORIES = ("workers", "mining", "judging", "planning", "other")
# A running call of these callers names the phase while the build has not started building.
CALL_PHASE = {"tech": "tech selection", "planner": "planning", "plan_review": "plan review"}
CALLER_CATEGORY = {
    "worker": "workers",
    "miner": "mining",
    "validator": "mining",
    "arbiter": "mining",
    "judge": "judging",
    "pairwise": "judging",
    "planner": "planning",
    "tech": "planning",
    "plan_review": "planning",
    "builder": "planning",
    "controller": "planning",
    "scoper": "planning",
    "design": "planning",
    "design_judge": "planning",
    "rule_walk": "planning",
    "claims": "mining",
    "verifier": "mining",
    "oracle": "judging",
    "pair": "judging",
    "postmortem": "other",
    "scope": "planning",
    "security": "mining",
    "readiness": "mining",
}


def call_label(c: dict) -> str:
    """A model call in plain words: "Writing brief for m3.t2", "Judging 3 candidates for
    m2.t1 (pairwise)", "Controller deciding after m5"..."""
    caller, purpose = c.get("caller") or "other", str(c.get("purpose") or "")
    where = c.get("unit") or c.get("task_id") or c.get("milestone")
    task, mode = (purpose.split(":", 1) + [""])[:2]
    if caller in ("design", "rule_walk", "design_judge") and purpose:
        return purpose  # build v4: "Designing: schema (3 options)"...
    if caller == "scoper":
        return f"Writing brief for {where}" if where else "Writing a brief"
    if caller == "pairwise":
        n = f"{c['n']} candidates" if c.get("n") else "candidates"
        return f"Judging {n} for {c.get('task_id') or where or 'a design task'} (pairwise)"
    if caller == "judge":
        return (
            "Judging requirement coverage"
            if purpose == "requirements"
            else f"Judging: {purpose or 'candidates'}"
        )
    if caller == "miner":
        what = mode or "rules"
        return (
            f"Miner replacing a promoted rule ({what})"
            if task == "replace"
            else f"Miner proposing {what} rules"
        )
    if caller == "validator":
        return "Validating proposed rules"
    if caller == "arbiter":
        return "Arbiter blaming failing rules on code or rule"
    if caller == "controller":
        return f"Controller deciding after {c.get('milestone') or 'the last run'}"
    if caller == "planner":
        return "Replanning after a backtrack" if "backtrack" in purpose else "Planning milestones"
    if caller == "tech":
        return "Choosing the tech stack"
    if caller == "plan_review":
        return "Reviewing the plan against the spec"
    if caller == "builder":
        return {
            "questions": "Drafting clarifying questions",
            "tests": "Generating acceptance tests",
            "checklist": "Building the requirements checklist",
        }.get(purpose, f"Builder: {purpose or 'working'}")
    if caller == "sim_owner":
        return "Simulated owner reviewing" if purpose == "judge" else "Simulated owner answering"
    if caller in V5_CALL_LABELS:  # build v5
        return V5_CALL_LABELS[caller].get(purpose) or V5_CALL_LABELS[caller][None]
    return f"{caller}: {purpose}" if purpose else caller


V5_CALL_LABELS = {
    "claims": {"dedupe": "Merging the two claim lists", None: "Listing claims from the spec"},
    "oracle": {"interpret": "Voting on open questions", None: "Oracle panel on failing scenarios"},
    "pair": {None: "Pair panel on a repair conflict"},
    "postmortem": {None: "Post-mortem of hidden failures"},
    "scope": {None: "Writing the v1 product scope"},
}


def worker_label(c: dict, hints: dict | None = None) -> str:
    """ "Worker: m4 · spec_literal", "Worker: m2.t1 · way 2 (event log)"."""
    what = c.get("task_id") or c.get("milestone") or f"candidate {c.get('candidate')}"
    if c.get("hint") is not None:
        h = (hints or {}).get(c.get("task_id")) or []
        text = h[c["hint"]] if isinstance(c["hint"], int) and c["hint"] < len(h) else None
        if text and len(text) > 60:
            text = text[:59].rstrip() + "…"
        how = f"way {c['hint'] + 1}" + (f" ({text})" if text else "")
    elif c.get("kind") == "refine" and not c.get("milestone"):
        how = f"refine of node {c.get('parent')}"
    else:
        how = c.get("strategy") or c.get("kind") or ""
    return f"Worker: {what}" + (f" · {how}" if how else "")


def mining_label(m: dict) -> str:
    """A mining pass: "Mining interactions in the background", "Checking the plan's rules
    after m1", "Mining requirements from the plan"."""
    modes = ", ".join(m.get("modes") or [])
    after = str(m.get("after") or "")
    if m.get("point") == "plan":
        where = "from the plan"
    elif after.startswith("review"):
        where = f"in {after}"
    else:
        where = f"after {after}" if after else ""
    what = (
        f"Mining {modes}"
        if modes
        else ("Mining rules" if m.get("point") == "plan" else "Checking the plan's rules")
    )
    return f"{what} in the background" if m.get("async") else f"{what} {where}".rstrip()


def backtrack_label(b: dict) -> str:
    """ "Backtracked to before m2: add branch_id to checkout"."""
    tgt = b.get("target")
    where = (
        "to the start"
        if tgt == "start"
        else f"to before {b['from_milestone']}"
        if b.get("from_milestone") and tgt in ("milestone", "replan", None)
        else f"to a losing fork (node {b.get('node')})"
        if tgt == "loser"
        else f"to {b.get('label') or tgt}"
    )
    verb = "Replanned" if b.get("kind") == "replan" else "Backtracked"
    if b.get("kind") == "replan":
        where = f"from {b['from_milestone']}" if b.get("from_milestone") else ""
    return f"{verb} {where}".rstrip() + (f": {b['change']}" if b.get("change") else "")


class _Activity:
    """Folds the activity events of one build into a small, bounded dict: only what runs
    now, the last ``RECENT`` finished items, and counts and sums (never one entry per
    call)."""

    def __init__(self):
        self.calls: dict = {}  # call id -> the running call
        self.mining: dict = {}  # point -> the running pass
        self.recent: list = []
        self.n_calls = self.n_done = self.n_failed = 0
        self.by_caller: dict = {}
        self.call_cost = {k: 0.0 for k in COST_CATEGORIES}
        self.fallback = {k: 0.0 for k in COST_CATEGORIES}  # logs without call_* events
        self.phase = 0
        self.started_ts: dict = {}  # candidate -> ts of candidate_started

    def judging(self) -> set:
        """Design tasks whose pairwise judge is running now."""
        return {c.get("task_id") for c in self.calls.values() if c.get("caller") == "pairwise"}

    def to_phase(self, name: str) -> None:
        self.phase = max(self.phase, PHASES.index(name))

    def done(self, item: dict) -> None:
        self.recent.append(item)
        if len(self.recent) > RECENT:
            del self.recent[0]

    def event(self, t: str, e: dict) -> None:
        if t == "call_started":
            self.n_calls += 1
            self.calls[e.get("call")] = {
                "kind": "call",
                "id": e.get("call"),
                "started": e.get("ts"),
                **{
                    k: e.get(k)
                    for k in (
                        "caller",
                        "purpose",
                        "model",
                        "milestone",
                        "unit",
                        "task_id",
                        "n",
                        "background",
                    )
                    if e.get(k) is not None
                },
            }
        elif t == "call_finished":
            self.n_done += 1
            c = self.calls.pop(e.get("call"), None) or {
                k: e.get(k) for k in ("caller", "purpose", "model")
            }
            ok = e.get("ok") is not False
            self.n_failed += 0 if ok else 1
            usd = float(e.get("usd") or 0)
            cat = e.get("category") or CALLER_CATEGORY.get(str(c.get("caller")), "other")
            self.call_cost[cat if cat in self.call_cost else "other"] += usd
            b = self.by_caller.setdefault(str(c.get("caller")), {"calls": 0, "usd": 0.0})
            b["calls"] += 1
            b["usd"] = round(b["usd"] + usd, 4)
            self.done(
                {
                    "kind": "call",
                    "label": call_label(c),
                    "caller": c.get("caller"),
                    "finished": e.get("ts"),
                    "wall_s": e.get("wall_s"),
                    "ok": ok,
                    "usd": e.get("usd"),
                    "error": e.get("error"),
                }
            )
        elif t == "mining_started":
            self.mining[e.get("point")] = {
                "kind": "mining",
                "id": e.get("point"),
                "started": e.get("ts"),
                **{k: e.get(k) for k in ("point", "after", "modes", "async")},
            }
        elif t == "mining_finished":
            m = self.mining.pop(e.get("point"), None) or {
                k: e.get(k) for k in ("point", "after", "modes", "async")
            }
            self.fallback["mining"] += float(e.get("usd") or 0)
            self.done(
                {
                    "kind": "mining",
                    "label": mining_label(m),
                    "finished": e.get("ts"),
                    "wall_s": e.get("wall_s")
                    if e.get("wall_s") is not None
                    else (round(e["ts"] - m["started"], 1) if m.get("started") else None),
                    "ok": not e.get("error"),
                    "usd": e.get("usd"),
                    "mined": e.get("mined"),
                    "error": e.get("error"),
                }
            )
        elif t == "brief" and e.get("usd"):
            self.fallback["planning"] += float(e.get("usd") or 0)

    def state(self, st: dict, hints: dict) -> dict:
        running = st["status"] == "running"
        # A design task's forks report back only after the judge: while its pairwise call
        # runs, they are done building, not running.
        judging = self.judging()
        workers = [
            {
                "kind": "worker",
                "id": c["candidate"],
                "label": worker_label(c, hints),
                "started": c.get("started"),
                **{
                    k: c[k]
                    for k in ("candidate", "milestone", "task_id", "strategy", "hint", "run")
                    if c.get(k) is not None
                },
            }
            for c in st["candidates"]
            if c.get("status") == "running"
            and not (c.get("hint") is not None and c.get("task_id") in judging)
        ]
        mining = [{**m, "label": mining_label(m)} for m in self.mining.values()]
        calls = [
            {
                **c,
                "label": call_label(c)
                + (
                    " · background"
                    if c.get("background") and c.get("caller") in ("miner", "validator", "arbiter")
                    else ""
                ),
            }
            for c in self.calls.values()
        ]
        items = (
            sorted(workers + mining + calls, key=lambda x: x.get("started") or 0) if running else []
        )
        # The phase: the furthest the events got, or what a running planning call says
        # while the build has not reached its workers.
        phase = PHASES[self.phase]
        if running and self.phase < PHASES.index("tests"):
            for c in calls:
                if c.get("caller") in CALL_PHASE and self.phase < PHASES.index("designing"):
                    phase = CALL_PHASE[c["caller"]]
                elif c.get("caller") == "builder" and c.get("purpose") == "tests":
                    phase = "tests"
        if st["status"] in ("error", "interrupted"):
            phase = "failed" if st["status"] == "error" else "stopped"
        # Progress: milestones moved past, and the current milestone's tasks.
        ms = st["milestones"]
        done = sum(1 for m in ms if m.get("status") in ("passed", "failed"))
        cur = next((m for m in reversed(ms) if m.get("status") == "running"), None)
        tasks = (cur or {}).get("tasks") or []
        t_done = sum(1 for t in tasks if t.get("status") in ("passed", "failed"))
        frac = None
        if ms:
            frac = (done + (t_done / len(tasks) if tasks else 0)) / len(ms)
        # Cost so far by category: the build's own breakdown once it has one.
        workers_usd = sum(float(c.get("usd") or 0) for c in st["candidates"])
        if st.get("cost_breakdown"):
            br = {k: float(v or 0) for k, v in st["cost_breakdown"].items()}
        else:
            src = self.call_cost if self.n_done else self.fallback
            br = {**{k: v for k, v in src.items()}, "workers": workers_usd}
        br = {k: round(v, 4) for k, v in br.items()}
        # Build v4: the stage in words ("Designing", "Building (one session)", "Hardening
        # round 2: repairing 3 rules"), while the build runs.
        stage = (st.get("v4") or st.get("v5") or {}).get("stage") if running else None
        return {
            "phase": phase,
            "stage": stage,
            "running": items,
            "recent": list(reversed(self.recent)),
            "calls": {
                "started": self.n_calls,
                "finished": self.n_done,
                "failed": self.n_failed,
                "by_caller": self.by_caller,
            },
            "inferred": self.n_calls == 0,
            "progress": {
                "milestones": [done, len(ms)],
                "milestone": (cur or {}).get("id"),
                "tasks": [t_done, len(tasks)] if tasks else None,
                "fraction": round(frac, 4) if frac is not None else None,
            },
            "cost": {"total": round(sum(br.values()), 4), "breakdown": br},
        }


V4_TYPES = (
    "design_started",
    "design_finished",
    "build_once_started",
    "build_once_finished",
    "harden_round",
    "backtrack_design",
)


def v4_event(st: dict, act: _Activity, t: str, e: dict) -> None:
    """Build v4's events (docs/events.md) into ``st["v4"]``: the design pack's summary, the
    one-shot build(s), the hardening rounds and design backtracks, and ``stage``, the
    activity line's words for where the build is."""
    v = st["v4"] = st["v4"] or {
        "design": None,
        "builds": [],
        "build": None,
        "rounds": [],
        "backtracks": [],
        "stage": None,
    }
    if t == "design_started":
        act.to_phase("designing")
        v["stage"] = "Designing"
        v["design"] = {"running": True, "search": e.get("search"), "stand_in": e.get("stand_in")}
    elif t == "design_finished":
        v["design"] = {
            "running": False,
            **{
                k: e.get(k)
                for k in (
                    "dimensions",
                    "forked",
                    "chosen",
                    "gaps",
                    "revisions",
                    "usd",
                    "wall_s",
                    "stand_in",
                )
            },
        }
        v["stage"] = "Generating tests against the design"
    elif t == "build_once_started":
        act.to_phase("building")
        n = e.get("epoch") or 0
        v["stage"] = "Building (one session)" + (f", after design backtrack {n}" if n else "")
        v["builds"].append(
            {
                "status": "running",
                "started": e.get("ts"),
                **{k: e.get(k) for k in ("milestone", "epoch", "max_turns", "max_usd", "model")},
            }
        )
    elif t == "build_once_finished":
        b = next(
            (x for x in reversed(v["builds"]) if x.get("milestone") == e.get("milestone")), None
        )
        if b is None:
            b = {"milestone": e.get("milestone")}
            v["builds"].append(b)
        b.update(
            status="done",
            **{
                k: e.get(k)
                for k in (
                    "epoch",
                    "node",
                    "session_id",
                    "usd",
                    "wall_s",
                    "turns",
                    "exit_reason",
                    "valid",
                    "user_tests",
                    "acceptance",
                    "coverage",
                    "held_back",
                    "passed",
                )
            },
        )
        v["build"] = b
    elif t == "harden_round":
        act.to_phase("hardening")
        k, n = e.get("round"), e.get("repaired") or 0
        bt = f" (after design backtrack {e['epoch']})" if e.get("epoch") else ""
        rec = {
            k2: e.get(k2)
            for k2 in (
                "round",
                "epoch",
                "head",
                "node",
                "mined",
                "failing",
                "code_wrong",
                "repaired",
                "accepted",
                "why",
                "stop",
                "before",
                "after",
                "status",
                "design_flaws",
                "design_rules",
            )
        }
        v["rounds"] = [
            r for r in v["rounds"] if (r.get("round"), r.get("epoch")) != (k, e.get("epoch"))
        ] + [rec]
        if e.get("status") == "repairing":
            v["stage"] = f"Hardening round {k}{bt}: repairing {n} rule{'s' if n != 1 else ''}"
        else:
            verdict = (
                "accepted"
                if e.get("accepted")
                else "rejected"
                if e.get("accepted") is False
                else e.get("stop") or "done"
            )
            v["stage"] = f"Hardening round {k}{bt}: {verdict}"
    elif t == "backtrack_design":
        b = next((x for x in v["backtracks"] if x.get("number") == e.get("number")), None)
        if b is None:
            b = {}
            v["backtracks"].append(b)
        b.update(
            {
                k: e[k]
                for k in (
                    "number",
                    "rule",
                    "dimension",
                    "from",
                    "to",
                    "reason",
                    "kept",
                    "why",
                    "rebuild",
                    "final",
                )
                if k in e
            }
        )
        if e.get("status") != "done":
            v["stage"] = (
                f"Design backtrack {e.get('number')}: {e.get('dimension')} -> {e.get('to')}"
            )


# an old round's ``mutation`` event (the retired mutants stage, AU1 #4) and ``prediction``
# event (p_hidden, AU1 #5) are not read: a type fold does not know changes nothing
V5_TYPES = (
    "v5_stage",
    "claims",
    "findings",
    "conflict",
    "repair_round",
    "fix_round",
    "interpretation",
    "scope",
    "security",
    "readiness",
    "prompt",
    "v5_live",
    "capability",
    "study",
)
V5_STAGE_WORDS = {
    "scope": "Scoping the idea (one call)",
    "build": "Building (one session)",
    "claims": "Listing claims from the spec",
    "verify": "Verifying (a fresh session)",
    "oracle": "Oracle panel on the failing scenarios",
    "repair": "Repairing",
    "reverify": "Re-verifying the repair",
    "readiness": "Readiness: security review, operational checks, repair",
    "deliver": "Delivering",
    "reproduce": "Reproducing the issue as a failing test",
    # the simple flow's steps (BF-DESIGN §4)
    "plan": "Planning the product (the plan and the checklist)",
    "check": "Checking it: tests, security and readiness at once",
    "fix": "Fixing what the checks found",
}
V5_PHASE = {
    "reproduce": "planning",
    "scope": "planning",
    "build": "building",
    "claims": "verifying",
    "verify": "verifying",
    "oracle": "verifying",
    "repair": "repairing",
    "reverify": "repairing",
    "readiness": "verifying",
    "deliver": "delivering",
    "plan": "planning",
    "check": "verifying",
    "fix": "repairing",
}
SEVERITIES = ("critical", "high", "medium", "low")


def v5_event(st: dict, act: _Activity, t: str, e: dict) -> None:
    """Build v5's events (docs/events.md) into ``st["v5"]``: each stage's status and cost,
    the claims, the latest findings, the repair rounds and their conflicts, the
    interpretations, and ``stage`` (the activity line)."""
    v = st["v5"] = st["v5"] or {
        "stages": {},
        "stage": None,
        "claims": None,
        "findings": None,
        "rounds": [],
        "conflicts": [],
        "interpretations": [],
        "scope": None,
        "security": None,
        "readiness": None,
    }
    if t == "v5_plan":  # build_started: the stages this run will run
        v["planned"] = list(e.get("stages") or [])
    elif t == "v5_stage":
        name = e.get("stage")
        s = v["stages"].setdefault(name, {"stage": name})
        s.update({k: e.get(k) for k in ("status", "why") if k in e})
        for k in ("usd", "wall_s"):  # a stage run more than once: the sum
            if e.get(k) is not None:
                s[k] = round(float(s.get(k) or 0) + float(e[k]), 4)
        for k in ("tokens_in", "tokens_out"):  # absent on older events: left unset
            if e.get(k) is not None:
                s[k] = int(s.get(k) or 0) + int(e[k])
        if isinstance(e.get("prompts"), dict):  # the prompt versions the stage used
            s["prompts"] = {**(s.get("prompts") or {}), **e["prompts"]}
        if isinstance(e.get("tools_by_server"), dict):  # MCP calls per server (CAP-PLAN §3)
            tbs = s.setdefault("tools_by_server", {})
            for k, n in e["tools_by_server"].items():
                tbs[k] = int(tbs.get(k) or 0) + int(n or 0)
        for kind in ("skills", "agents"):  # plugin skills and subagents used (PL)
            if isinstance(e.get(kind), dict):
                acc = s.setdefault(kind, {})
                for k, n in e[kind].items():
                    acc[k] = int(acc.get(k) or 0) + int(n or 0)
        if e.get("capabilities"):  # the capabilities the stage's sessions had
            s["capabilities"] = sorted(set(s.get("capabilities") or []) | set(e["capabilities"]))
        if e.get("status") not in (None, "started"):
            s.pop("live", None)  # the transcript's counts replace the live
        if e.get("status") == "started":
            s["runs"] = s.get("runs", 0) + 1
        if e.get("status") == "started":
            s["started"] = e.get("ts")
            if name in V5_PHASE:
                act.to_phase(V5_PHASE[name])
            v["stage"] = V5_STAGE_WORDS.get(name, name)
    elif t == "v5_live":  # the factory-mod's live counts for a stage
        s = v["stages"].setdefault(e.get("stage"), {"stage": e.get("stage")})
        s["live"] = {
            k: e.get(k)
            for k in ("tool_calls", "by_server", "last_tool", "at", "notes", "evidence", "denied")
        }
    elif t == "study":  # the study before scope (in the scope cell)
        v["study"] = {
            k: e.get(k) for k in ("status", "wall_s", "tools_by_server", "capabilities", "path")
        }
    elif t == "capability":  # a capability this build carries, and why
        caps = v.setdefault("capabilities", [])
        rec = {k: e.get(k) for k in ("name", "origin", "why", "stages", "ref")}
        v["capabilities"] = [c for c in caps if c.get("name") != rec["name"]] + [rec]
    elif t == "prompt":  # custom_prompts: a prompt and its version
        v.setdefault("prompts", {})[str(e.get("id"))] = {
            k: e.get(k)
            for k in ("version", "n", "custom", "template", "additions", "house_rules", "stage")
        }
    elif t == "claims":
        v["claims"] = {k: e.get(k) for k in ("n", "ambiguities", "samples", "dedupe", "lessons")}
    elif t == "scope":
        v["scope"] = {
            k: e.get(k)
            for k in (
                "decision",
                "why",
                "mode",
                "verdict",
                "called",
                "flows",
                "dod",
                "must",
                "settings",
                "words",
                "shared_base",
                "path",
            )
        }
    elif t == "findings":
        v["findings"] = {
            k: e.get(k)
            for k in (
                "stage",
                "confirmed",
                "interpretation",
                "settings",
                "retired",
                "incomplete",
                "environment",
                "completeness",
                "blocked",
                "scenarios",
                "failing",
                "new_failing",
            )
        }
    elif t == "conflict":
        v["conflicts"].append(
            {
                k: e.get(k)
                for k in ("round", "x", "y", "group", "verdict", "action", "x_rank", "y_rank")
            }
        )
    elif t in ("repair_round", "fix_round"):  # the simple flow's round reads the same
        rec = {
            k: e.get(k)
            for k in (
                "round",
                "status",
                "sent",
                "fixed",
                "regressions",
                "conflicts",
                "reverted",
                "accepted",
                "why",
                "settings",
                "session",
            )
        }
        v["rounds"] = [r for r in v["rounds"] if r.get("round") != e.get("round")] + [rec]
        if e.get("status") in ("repairing", "fixing"):
            act.to_phase("repairing")
            v["stage"] = (
                f"Fix round {e.get('round')}: {e.get('sent')} item(s)"
                if t == "fix_round"
                else f"Repair round {e.get('round')}: {e.get('sent')} finding(s)"
            )
    elif t == "interpretation":
        v["interpretations"].append(
            {
                k: e.get(k)
                for k in (
                    "item",
                    "mode",
                    "chosen",
                    "forked",
                    "implements",
                    "stakes",
                    "kind",
                    "setting",
                    "status",
                )
            }
        )
    elif t == "security":  # the readiness stage's security review
        v["security"] = {
            k: e.get(k) for k in (*SEVERITIES, "ran", "confirmed", "unconfirmed", "probes", "audit")
        }
        v["stage"] = (
            "Security review: " + ", ".join(f"{e[s]} {s}" for s in SEVERITIES if e.get(s))
            if any(e.get(s) for s in SEVERITIES)
            else "Security review: no findings"
        )
    elif t == "readiness":  # checks, the repair round, the final scorecard
        r = v.get("readiness") or {}
        step = e.get("step")
        if step in ("checks", "final"):
            r[step] = {k: e.get(k) for k in ("green", "amber", "red", "na", "security") if k in e}
        elif step == "repair":
            r["repair"] = {
                k: e.get(k)
                for k in ("status", "sent", "fixed", "regressions", "accepted", "why", "updated")
            }
            if e.get("status") == "repairing":
                act.to_phase("repairing")
                v["stage"] = f"Readiness repair: {e.get('sent')} item(s)"
        v["readiness"] = r


class _Fold:
    """One build's state while its events are folded: ``st`` (what ``fold`` returns) and the
    working tables the handlers share (questions, candidates, milestones, rule states)."""

    def __init__(self, build):
        self.st: dict = {
            "task": None,
            "build": build,
            "kind": "build",
            "status": "running",
            "started": None,
            "finished": None,
            "spec": None,
            "title": None,
            "policy": None,
            "harness": None,
            "pid": None,
            "taskdir": None,
            "note": None,
            "parent": None,
            "questions": [],
            "tests": None,
            "candidates": [],
            "delivered": None,
            "verdicts": [],
            "cost_usd": 0.0,
            "wall_s": None,
            "error": None,
            "plan": None,
            "milestones": [],
            "rules": None,
            "assumptions": None,
            "adr": None,
            "plan_review": None,
            "controller": None,
            "v4": None,
            "v5": None,
        }
        self.qs: dict = {}
        self.rule_st: dict = {}  # rule id -> {"status", "visibility"} (rule_status events)
        self.act = _Activity()
        self.cands: dict = {}
        self.ms: dict = {}


# -- one handler per event type (or family); each changes the _Fold in place ------------------
def _on_start(f: _Fold, t: str, e: dict) -> None:
    st = f.st
    f.act.to_phase("questions" if t == "build_started" else "building")
    st.update(
        kind="build" if t == "build_started" else "revise",
        started=e["ts"],
        **{
            k: e.get(k)
            for k in (
                "spec",
                "title",
                "policy",
                "harness",
                "pid",
                "taskdir",
                "note",
                "parent",
                "pipeline",
                "cap_usd",
            )
            if k in e
        },
    )
    if e.get("pipeline") == "v5" and e.get("stages"):  # the stage strip's plan
        v5_event(st, f.act, "v5_plan", e)
    if e.get("kind") == "revise":  # a revise round (Ask for changes; AR-PLAN.md §2)
        st["revise"] = {k: e.get(k) for k in ("revises", "origin", "mode", "instructions")}


def _on_revise_outcome(f: _Fold, t: str, e: dict) -> None:
    f.st["revise"] = {
        **(f.st.get("revise") or {}),
        **{k: e.get(k) for k in ("changed", "before", "after", "what")},
    }


def _on_question(f: _Fold, t: str, e: dict) -> None:
    f.act.to_phase("plan review" if e.get("id") == "plan" else "questions")
    f.qs[e["id"]] = {
        "id": e["id"],
        "text": e.get("text"),
        "n": e.get("n"),
        "of": e.get("of"),
        "answered": False,
        "answer": None,
    }
    if e.get("auto_accept_s"):
        f.qs[e["id"]]["auto_accept_s"] = e["auto_accept_s"]


def _on_answer(f: _Fold, t: str, e: dict) -> None:
    q = f.qs.setdefault(e["id"], {"id": e["id"], "text": None, "n": None, "of": None})
    q.update(answered=True, answer=e.get("text"))


def _on_tests_generated(f: _Fold, t: str, e: dict) -> None:
    f.act.to_phase("tests")
    f.st["tests"] = {"visible": e.get("visible"), "held_back": e.get("held_back")}


def _on_brief(f: _Fold, t: str, e: dict) -> None:
    f.act.event(t, e)
    # Build v3 §7b: emitted just before its candidate starts (``core.briefs``).
    if e.get("candidate") is not None:
        f.cands.setdefault(e["candidate"], {"candidate": e["candidate"]})["brief"] = {
            k: e.get(k)
            for k in ("unit", "kind", "model", "usd", "cached", "summary", "lint", "text")
        }


def _on_candidate_started(f: _Fold, t: str, e: dict) -> None:
    f.act.to_phase("building")
    brief = (f.cands.get(e["candidate"]) or {}).get("brief")
    c = f.cands[e["candidate"]] = {
        "candidate": e["candidate"],
        "run": e.get("run"),
        "node": None,
        "strategy": e.get("strategy"),
        "kind": e.get("kind"),
        "parent": e.get("parent"),
        "status": "running",
        "started": e.get("ts"),
    }
    if brief:
        c["brief"] = brief
    if e.get("milestone"):
        c["milestone"] = e["milestone"]
        c["reason"] = e.get("reason")
    for k in ("task_id", "hint"):
        if e.get(k) is not None:
            c[k] = e[k]
    if e.get("fix_items"):
        c["fix_items"] = e["fix_items"]


def _on_plan(f: _Fold, t: str, e: dict) -> None:
    st = f.st
    f.act.to_phase("planning")
    st["plan"] = {
        "version": e.get("version"),
        "replanned": e.get("replanned"),
        "milestones": e.get("milestones") or [],
    }
    if e.get("backtrack"):
        st["plan"]["backtrack"] = e["backtrack"]
    # After a backtrack's replan (build v3 §4) the milestones it kept keep their state.
    before = {k: v for k, v in f.ms.items() if k in set(e.get("kept") or [])}
    f.ms = {
        m["id"]: {
            "id": m["id"],
            "title": m.get("title"),
            "size": m.get("size"),
            "n": i,
            "of": len(st["plan"]["milestones"]),
            "status": "pending",
            "node": None,
            "passed": None,
            "coverage": None,
            "usd": None,
            "attempts": 0,
            **(
                {
                    "tasks": [
                        {
                            "id": tk.get("id"),
                            "title": tk.get("title"),
                            "kind": tk.get("uncertainty") or "routine",
                            "size": tk.get("size"),
                            "status": "pending",
                            "n": None,
                            "winner": None,
                            "candidates": [],
                        }
                        for tk in m["tasks"]
                    ]
                }
                if m.get("tasks")
                else {}
            ),
        }
        for i, m in enumerate(st["plan"]["milestones"], 1)
    }
    for k, v in before.items():
        if k in f.ms:
            f.ms[k] = {**v, "n": f.ms[k]["n"], "of": f.ms[k]["of"]}


def _on_controller(f: _Fold, t: str, e: dict) -> None:
    c = f.st["controller"] = f.st["controller"] or {"decisions": [], "backtracks": []}
    if t == "controller_decision":
        c["decisions"].append(
            {
                k: e.get(k)
                for k in (
                    "at",
                    "epoch",
                    "milestone",
                    "task_id",
                    "option",
                    "label",
                    "by",
                    "reason",
                    "ambiguous",
                    "menu",
                    "params",
                )
            }
        )
    elif t == "backtrack":
        c["backtracks"].append(
            {
                k: e.get(k)
                for k in (
                    "number",
                    "kind",
                    "label",
                    "node",
                    "target",
                    "from_milestone",
                    "change",
                    "reason",
                    "by",
                    "at",
                    "discard",
                    "flaws",
                )
            }
        )
    elif c["backtracks"]:
        if t == "replan":
            c["backtracks"][-1]["replan"] = {
                k: e.get(k) for k in ("version", "interface_changed", "kept", "retired", "summary")
            }
        else:
            c["backtracks"][-1]["adr_revision"] = e.get("revision")


def _on_task(f: _Fold, t: str, e: dict) -> None:
    m = f.ms.setdefault(e.get("milestone"), {"id": e.get("milestone"), "n": None})
    tasks = m.setdefault("tasks", [])
    tk = next((x for x in tasks if x["id"] == e.get("task_id")), None)
    if tk is None:
        tk = {"id": e.get("task_id"), "title": e.get("title"), "candidates": []}
        tasks.append(tk)
    tk.update(kind=e.get("kind") or tk.get("kind"), n=e.get("n"))
    if t == "task_started":
        tk.update(status="running", hints=e.get("hints") or [])
    else:
        tk.update(
            status="passed" if e.get("passed") else "failed",
            **{k: e.get(k) for k in ("winner", "reason", "coverage", "usd", "runs", "stage")},
        )
        if e.get("candidates"):
            tk["candidates"] = e["candidates"]


def _on_v4(f: _Fold, t: str, e: dict) -> None:
    v4_event(f.st, f.act, t, e)


def _on_v5(f: _Fold, t: str, e: dict) -> None:
    v5_event(f.st, f.act, t, e)


def _on_budget_warning(f: _Fold, t: str, e: dict) -> None:
    f.st.setdefault("warnings", []).append(
        {
            k: e.get(k)
            for k in ("milestone", "task_id", "n", "spent_usd", "committed_usd", "budget_usd")
        }
    )


def _on_assumptions(f: _Fold, t: str, e: dict) -> None:
    f.st["assumptions"] = list(e.get("items") or [])


def _on_adr(f: _Fold, t: str, e: dict) -> None:
    f.st["adr"] = {k: e.get(k) for k in ("decision", "stack", "candidates")}


def _on_plan_reviewed(f: _Fold, t: str, e: dict) -> None:
    f.st["plan_review"] = {k: e.get(k) for k in ("gaps", "applied", "error")}


def _on_milestone_started(f: _Fold, t: str, e: dict) -> None:
    f.act.to_phase("closing review" if str(e["id"]).startswith("review") else "building")
    m = f.ms.setdefault(e["id"], {"id": e["id"], "title": e.get("title"), "n": e.get("n")})
    m.update(
        status="running", size=e.get("size"), max_turns=e.get("max_turns"), max_usd=e.get("max_usd")
    )


def _on_milestone_finished(f: _Fold, t: str, e: dict) -> None:
    m = f.ms.setdefault(e["id"], {"id": e["id"], "title": e.get("title"), "n": e.get("n")})
    m.update(
        {
            k: e.get(k)
            for k in ("node", "passed", "coverage", "slice", "usd", "attempts", "attempt_reasons")
        }
    )
    m["status"] = "passed" if e.get("passed") else "failed"


def _on_candidate_finished(f: _Fold, t: str, e: dict) -> None:
    c = f.cands.setdefault(e["candidate"], {"candidate": e["candidate"]})
    c.update(
        {
            k: e.get(k)
            for k in (
                "node",
                "valid",
                "score",
                "user_tests",
                "acceptance",
                "coverage",
                "dropped_tests",
                "usd",
                "exit_reason",
            )
        }
    )
    if e.get("rules") is not None:
        c["rules"] = e["rules"]
    if e.get("model"):
        c["model"] = e["model"]
    if "milestone" in e:
        c.update(milestone=e["milestone"], gate=e.get("gate"), reason=e.get("reason"))
    c["status"] = "done"
    hints = {tk["id"]: tk.get("hints") for m in f.ms.values() for tk in m.get("tasks") or []}
    f.act.done(
        {
            "kind": "worker",
            "label": worker_label(c, hints),
            "finished": e["ts"],
            "wall_s": round(e["ts"] - c["started"], 1) if c.get("started") else None,
            "ok": bool(c.get("valid")) and (c.get("gate") or {}).get("passed", True) is not False,
            "usd": e.get("usd"),
        }
    )
    f.st["cost_usd"] = round(f.st["cost_usd"] + float(e.get("usd") or 0), 4)


def _on_candidates_rescored(f: _Fold, t: str, e: dict) -> None:
    by_node = {c.get("node"): c for c in f.cands.values()}
    for r in e.get("candidates") or []:
        if (c := by_node.get(r.get("node"))) is not None:
            c.update({k: r.get(k) for k in ("valid", "score", "coverage") if k in r})
            if r.get("rules") is not None:
                c["rules"] = r["rules"]
            c["dropped_tests"] = e.get("dropped_tests")


def _on_delivered(f: _Fold, t: str, e: dict) -> None:
    f.act.to_phase("delivering")
    f.st["delivered"] = {
        k: e.get(k)
        for k in ("node", "branch", "out", "commit", "report", "held_back", "changed", "rules")
    }


def _on_rules(f: _Fold, t: str, e: dict) -> None:
    st, act = f.st, f.act
    r = st["rules"] = st["rules"] or {
        "active": 0,
        "held": 0,
        "retired": 0,
        "disputed": 0,
        "candidate": 0,
        "mined": 0,
        "review_rounds": 0,
        "errors": 0,
    }
    if t in ("mining_started", "mining_finished"):
        act.event(t, e)
        pt = str(e.get("point") or "")
        if t == "mining_started" and pt.startswith("harden") and st.get("v4"):
            k, _, bt = pt.removeprefix("harden").partition(".bt")
            act.to_phase("hardening")
            st["v4"]["stage"] = (
                f"Hardening round {k}"
                + (f" (after design backtrack {bt})" if bt else "")
                + ": mining and checking rules"
            )
        # The pass under way (the UI's "Rules" line), or the last one.
        r["mining"] = {
            "running": t == "mining_started",
            **{
                k: e.get(k)
                for k in (
                    "point",
                    "after",
                    "modes",
                    "async",
                    "active",
                    "caught",
                    "mined",
                    "fix_items",
                    "usd",
                    "error",
                )
                if k in e
            },
        }
        r["caught"] = e.get("caught", r.get("caught"))
        if t == "mining_finished":
            r["mining_usd"] = round(r.get("mining_usd", 0.0) + float(e.get("usd") or 0), 4)
    elif t == "rule_promoted":
        r.setdefault("promoted", []).append(e.get("id"))
    elif t == "rule_status":
        f.rule_st[e.get("id")] = {"status": e.get("to"), "visibility": e.get("visibility")}
        for k in ("active", "retired", "disputed", "candidate"):
            r[k] = sum(1 for v in f.rule_st.values() if v["status"] == k)
        r["held"] = sum(
            1 for v in f.rule_st.values() if v["status"] == "active" and v["visibility"] == "held"
        )
    elif t == "rules_mined":
        r["mined"] += int(e.get("proposed") or 0)
        r["errors"] += 1 if e.get("error") else 0
    else:
        act.to_phase("closing review")
        r["review_rounds"] += 1
        r["last_round"] = {
            k: e.get(k) for k in ("round", "rules", "passed", "code_wrong", "fixed", "stop")
        }


def _on_build_resumed(f: _Fold, t: str, e: dict) -> None:
    """A resumed v5 round: running again, in a new process."""
    f.st.update(
        status="running",
        finished=None,
        error=None,
        pid=e.get("pid"),
        failed_stage=None,
        failure=None,
    )
    f.act.phase = min(f.act.phase, PHASES.index("building"))


def _on_verdict(f: _Fold, t: str, e: dict) -> None:
    f.st["verdicts"].append({k: e.get(k) for k in ("verdict", "note", "source", "node")})


def _on_build_finished(f: _Fold, t: str, e: dict) -> None:
    st = f.st
    f.act.to_phase("done")
    st.update(
        status=e.get("status", "ok"),
        finished=e["ts"],
        wall_s=e.get("wall_s"),
        error=e.get("error"),
        # v5: the stage that failed and how (auth | stopped | error)
        failed_stage=e.get("stage"),
        failure=e.get("failure"),
    )
    if e.get("cost_usd") is not None:
        st["cost_usd"] = e["cost_usd"]
    for k in ("cost_breakdown", "worker_cost_usd"):
        if e.get(k) is not None:
            st[k] = e[k]


FOLD: dict[str, Callable[[_Fold, str, dict], None]] = {
    **dict.fromkeys(START_TYPES, _on_start),
    "revise_outcome": _on_revise_outcome,
    "question": _on_question,
    "answer": _on_answer,
    "tests_generated": _on_tests_generated,
    "brief": _on_brief,
    "candidate_started": _on_candidate_started,
    "plan": _on_plan,
    **dict.fromkeys(("controller_decision", "backtrack", "replan", "adr_revised"), _on_controller),
    **dict.fromkeys(("task_started", "task_finished"), _on_task),
    **dict.fromkeys(V4_TYPES, _on_v4),
    **dict.fromkeys(V5_TYPES, _on_v5),
    "budget_warning": _on_budget_warning,
    "assumptions": _on_assumptions,
    "adr": _on_adr,
    "plan_reviewed": _on_plan_reviewed,
    "milestone_started": _on_milestone_started,
    "milestone_finished": _on_milestone_finished,
    "candidate_finished": _on_candidate_finished,
    "candidates_rescored": _on_candidates_rescored,
    "delivered": _on_delivered,
    **dict.fromkeys(
        (
            "rule_status",
            "rules_mined",
            "review_round",
            "mining_started",
            "mining_finished",
            "rule_promoted",
        ),
        _on_rules,
    ),
    "build_resumed": _on_build_resumed,
    "verdict": _on_verdict,
    "build_finished": _on_build_finished,
}
"""Event type -> its handler (CR-PLAN S-14). A type not here changes nothing."""


def fold(events: list[dict], build=None) -> dict | None:
    """The state of one build (default: the latest) from its events, or None.

    ``status`` is ``running`` until ``build_finished``, then ``ok`` or ``error``.
    A crashed process leaves ``running``; ``alive`` tells the two apart."""
    build = latest_build(events) if build is None else build
    if build is None:
        return None
    f = _Fold(build)
    st, act = f.st, f.act
    for e in events:
        if e.get("build") != build:
            continue
        t = e["type"]
        if t in ("call_started", "call_finished"):  # the bulk of a log: fast path
            act.event(t, e)
            continue
        st["task"] = st["task"] or e.get("task")
        handler = FOLD.get(t)
        if handler is not None:
            handler(f, t, e)
    st["questions"] = sorted(f.qs.values(), key=lambda q: (q.get("n") or 0, q["id"]))
    st["candidates"] = [f.cands[k] for k in sorted(f.cands)]
    st["milestones"] = sorted(f.ms.values(), key=lambda m: (m.get("n") or 0, m["id"]))
    if st["controller"]:
        # Each milestone box lists the controller's decisions after its runs (build v3 §4).
        for m in st["milestones"]:
            m["decisions"] = [
                d for d in st["controller"]["decisions"] if d.get("milestone") == m["id"]
            ]
    st["verdict"] = st["verdicts"][-1] if st["verdicts"] else None
    if st["controller"]:
        for b in st["controller"]["backtracks"]:
            b["text"] = backtrack_label(b)
    hints = {tk["id"]: tk.get("hints") for m in st["milestones"] for tk in m.get("tasks") or []}
    st["activity"] = act.state(st, hints)
    st["activity"]["forks"] = forks(st, act.judging() if st["status"] == "running" else set())
    return st


def forks(st: dict, judging: set = frozenset()) -> dict | None:
    """The latest design task's parallel forks (build v3 §3): one entry per approach hint,
    its latest run, and the judge's outcome once the task is judged."""
    seen = [
        (tk, m)
        for m in st["milestones"]
        for tk in m.get("tasks") or []
        if tk.get("kind") == "design" and tk.get("status") in ("running", "passed", "failed")
    ]
    if not seen:
        return None
    task, ms = next((x for x in seen if x[0].get("status") == "running"), seen[-1])
    runs: dict = {}
    for c in st["candidates"]:
        if c.get("task_id") == task["id"] and c.get("hint") is not None:
            runs[c["hint"]] = c  # the latest run of each way
    outcome = {x.get("node"): x for x in task.get("candidates") or [] if isinstance(x, dict)}
    hints = task.get("hints") or [
        x.get("hint_text") for x in task.get("candidates") or [] if isinstance(x, dict)
    ]
    judged_now = task["id"] in judging
    out = []
    for i in range(max(len(hints), max(runs, default=-1) + 1)):
        c = runs.get(i) or {}
        o = outcome.get(c.get("node")) if c.get("node") is not None else None
        status = c.get("status") or "pending"
        if judged_now and status == "running":
            status = "built"  # waiting for the judge's verdict
        out.append(
            {
                "hint": i,
                "hint_text": hints[i] if i < len(hints) else None,
                "candidate": c.get("candidate"),
                "node": c.get("node"),
                "status": status,
                "started": c.get("started"),
                "valid": c.get("valid"),
                "usd": c.get("usd"),
                "rules": c.get("rules"),
                "outcome": (o or {}).get("outcome"),
                "chosen": c.get("node") is not None
                and c.get("node") == task.get("winner")
                and task.get("status") != "running",
                "points": (o or {}).get("points"),
            }
        )
    running = task.get("status") == "running"
    judging = running and bool(out) and (judged_now or all(f["status"] == "done" for f in out))
    return {
        "task_id": task["id"],
        "title": task.get("title"),
        "milestone": ms.get("id"),
        "status": task.get("status"),
        "judging": judging,
        "winner": task.get("winner"),
        "forks": out,
    }


def alive(pid: int | None) -> bool:
    """The build's process runs (a zombie does not): ``proc.alive``."""
    from .proc import alive as _alive

    return bool(pid) and _alive(int(pid))


def load_state(target: str | Path | None, build=None) -> dict | None:
    """Fold the log at ``target`` (see ``resolve_log``). A ``running`` build whose
    process is gone is reported as ``interrupted``."""
    st = fold(read_events(resolve_log(target)), build)
    if st and st["status"] == "running" and st.get("pid") and not alive(st["pid"]):
        st["status"] = "interrupted"
        st["activity"].update(running=[], phase="stopped")
    return st
