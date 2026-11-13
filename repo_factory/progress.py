"""Terminal output for ``repo-factory build`` / ``repo-factory revise`` / ``repo-factory status``.

``Renderer`` subscribes to the event log (``repo_factory.events``) and prints one short
line per event to stderr. ``summary`` turns a folded build state into the closing
plain-English summary: what was built, where it is, how to review it, what next.
"""

from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from pathlib import Path

HEARTBEAT_S = 60
LONG_CALL_S = 60  # a model call running this long gets one line


def fmt_pair(p) -> str | None:
    return f"{p[0]}/{p[1]}" if isinstance(p, (list, tuple)) and len(p) == 2 else None


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(round(s))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def short_path(p: str | Path | None) -> str:
    """Relative to the cwd when that is shorter, quoted for the shell."""
    if p is None:
        return "?"
    p = Path(p)
    try:
        rel = os.path.relpath(p)
        if not rel.startswith(".." + os.sep) and len(rel) < len(str(p)):
            p = Path(rel)
    except ValueError:
        pass
    return shlex.quote(str(p))


MARKS = {"passed": "✓", "failed": "✗", "running": "…", "pending": "·"}


def milestone_lines(st: dict, indent: str = "  ") -> list[str]:
    """The milestone tour as a checklist: ✓ passed, ✗ failed (shortfall), … running,
    · pending."""
    out = []
    for m in st.get("milestones") or []:
        status = m.get("status") or "pending"
        line = f"{indent}{MARKS.get(status, '·')} {m.get('n')}. {m.get('title')}"
        extra = []
        if m.get("size"):
            extra.append(m["size"])
        if status in ("passed", "failed"):
            if m.get("coverage") is not None:
                extra.append(f"coverage {float(m['coverage']):.2f}")
            if m.get("attempts"):
                extra.append(f"{m['attempts']} run{'s' if m['attempts'] != 1 else ''}")
            if m.get("usd") is not None:
                extra.append(f"${float(m['usd']):.2f}")
        elif status == "running":
            extra.append("running")
        out.append(line + (f" ({', '.join(extra)})" if extra else ""))
        out += task_lines(m, indent + "    ")
        out += decision_lines(m.get("decisions") or [], indent + "    ")
    return out


def notable(d: dict) -> bool:
    """A controller decision worth a line: anything but a plain "gate passed, continue"."""
    return not (
        d.get("option") == "continue"
        and d.get("by") == "rules"
        and str(d.get("reason") or "").startswith("the task's gate passed")
        and ";" not in str(d.get("reason") or "")
    )


def decision_line(d: dict) -> str:
    by = {"llm": "model", "fallback": "fallback"}.get(d.get("by"), "rules")
    where = f", {d['task_id']}" if d.get("task_id") else ""
    return (
        f"↳ {d.get('label') or d.get('option')} (after node {d.get('at')}, {by}{where}): "
        f"{d.get('reason') or ''}"
    )


def decision_lines(decisions: list[dict], indent: str = "      ") -> list[str]:
    """The controller's decisions after a milestone's runs (build v3 §4), the notable ones."""
    return [indent + decision_line(d) for d in decisions if notable(d)]


def controller_lines(c: dict | None) -> list[str]:
    """``repo-factory status``: the controller's decisions in numbers, then each backtrack."""
    if not c or not c.get("decisions"):
        return []
    ds = c["decisions"]
    by = {k: sum(1 for d in ds if d.get("by") == k) for k in ("rules", "llm", "fallback")}
    opts = {}
    for d in ds:
        opts[d.get("option")] = opts.get(d.get("option"), 0) + 1
    L = [
        f"Controller: {len(ds)} decision(s) ({', '.join(f'{v} {k}' for k, v in opts.items())}; "
        f"{by['rules']} by the rules, {by['llm']} by the model, {by['fallback']} fallback), "
        f"{len(c.get('backtracks') or [])} backtrack(s)"
    ]
    for b in c.get("backtracks") or []:
        rp = b.get("replan") or {}
        L.append(
            f"  ↶ backtrack {b.get('number')}: {b.get('label')} (node {b.get('node')}, "
            f"{b.get('by')}), replanned from {b.get('from_milestone')}"
            + (", interface changed" if rp.get("interface_changed") else "")
            + (f", ADR revision {b['adr_revision']}" if b.get("adr_revision") else "")
            + (f", {len(rp['retired'])} rule(s) retired" if rp.get("retired") else "")
            + f"\n      change: {b.get('change')}\n      why: {b.get('reason')}"
        )
    return L


def v4_lines(v: dict | None) -> list[str]:
    """``repo-factory status`` for build v4: the design pack, the one-shot build(s), each hardening
    round with its verdict, and the design backtracks."""
    if not v:
        return []
    L = []
    d = v.get("design") or {}
    if d.get("running"):
        L.append("Design: searching…")
    elif d:
        dims = d.get("dimensions") or []
        L.append(
            f"Design: {len(dims)} dimension(s), {len(d.get('forked') or [])} forked"
            + (" (stand-in pack)" if d.get("stand_in") else "")
            + (f", {len(d['gaps'])} gap(s) in the combined walk" if d.get("gaps") else "")
        )
        for x in dims:
            opts = ", ".join(
                f"{o['id']}{'*' if o['id'] == x.get('chosen') else ''}"
                + (
                    f" ({o['gaps']} gap{'s' if o['gaps'] != 1 else ''})"
                    if o.get("gaps") is not None
                    else ""
                )
                for o in x.get("options") or []
            )
            L.append(f"  {x['name']} ({x.get('door')}, {x.get('ambiguity')}): {opts}")
    for b in v.get("builds") or []:
        tag = f" after design backtrack {b['epoch']}" if b.get("epoch") else ""
        if b.get("status") == "running":
            L.append(
                f"One-shot build{tag}: running (up to {b.get('max_turns')} turns, "
                f"${b.get('max_usd')})"
            )
            continue
        held = fmt_pair(b.get("held_back"))
        L.append(
            f"One-shot build{tag}: node {b.get('node')}, session {b.get('session_id')}, "
            f"{'valid' if b.get('valid') else 'NOT valid'}"
            + (f", coverage {float(b['coverage']):.2f}" if b.get("coverage") is not None else "")
            + (f", held-back {held}" if held else "")
            + f", ${float(b.get('usd') or 0):.2f}, {fmt_duration(b.get('wall_s'))}"
        )
    for r in v.get("rounds") or []:
        tag = f" (after design backtrack {r['epoch']})" if r.get("epoch") else ""
        if r.get("status") == "repairing":
            L.append(
                f"Hardening round {r.get('round')}{tag}: repairing {r.get('repaired')} rule(s)…"
            )
            continue
        verdict = (
            "accepted"
            if r.get("accepted")
            else "rejected"
            if r.get("accepted") is False
            else "no repair"
        )
        line = (
            f"Hardening round {r.get('round')}{tag}: {r.get('repaired') or 0} rule(s) "
            f"repaired, {verdict}; {r.get('mined') or 0} mined, {r.get('failing') or 0} "
            f"failing"
        )
        if r.get("stop"):
            line += f"; stopped: {r['stop']}"
        L.append(line + (f"\n      {r['why']}" if r.get("why") else ""))
    for b in v.get("backtracks") or []:
        L.append(
            f"  ↶ design backtrack {b.get('number')}: {b.get('dimension')} "
            f"{b.get('from')} -> {b.get('to')} for rule {b.get('rule')}"
            + (
                f"; rebuilt line {'kept' if b.get('kept') else 'set aside'}"
                if b.get("kept") is not None
                else ""
            )
        )
    return L


def v5_lines(v: dict | None) -> list[str]:
    """``repo-factory status`` for build v5: each stage with its cost, the claims, the findings, each
    repair round with its conflicts, the security review, readiness and the interpretations."""
    if not v:
        return []
    L = []
    st = v.get("stages") or {}
    if st:
        L.append(
            "Stages: "
            + ", ".join(
                f"{k} {s.get('status')}" + (f" ${float(s['usd']):.2f}" if s.get("usd") else "")
                for k, s in st.items()
            )
        )
    sc = v.get("scope")
    if sc:
        L.append(
            f"Scope: {sc.get('decision')} ({sc.get('why')})"
            if sc.get("decision") != "scoped"
            else f"Scope: SCOPE.md written, {sc.get('dod')} flow(s) in the definition of "
            f"done, {sc.get('settings')} setting(s); the build doesn't share the "
            "vanilla base"
        )
    c = v.get("claims")
    if c:
        L.append(
            f"Claims: {c.get('n')} from {c.get('samples')} "
            f"sample{'' if c.get('samples') == 1 else 's'}, "
            f"{c.get('ambiguities')} open question(s)"
        )
    f = v.get("findings")
    if f:
        L.append(
            f"Findings ({f.get('stage')}): {f.get('confirmed')} confirmed defect(s), "
            + (f"{f['incomplete']} incomplete, " if f.get("incomplete") else "")
            + f"{f.get('interpretation')} interpretation(s), {f.get('retired')} retired, "
            f"{f.get('blocked')} blocked; {f.get('failing')} of {f.get('scenarios')} "
            "scenarios failing"
        )
        if f.get("completeness"):
            done, total = f["completeness"][:2]
            L.append(f"Completeness: {done} of {total} flows work end to end")
    for r in v.get("rounds") or []:
        if r.get("status") == "repairing":
            L.append(f"Repair round {r.get('round')}: repairing {r.get('sent')} finding(s)…")
        elif r.get("status") == "skipped":
            L.append(f"Repair round {r.get('round')}: skipped ({r.get('why')})")
        else:
            rv = r.get("reverted") or []
            L.append(
                f"Repair round {r.get('round')}: {r.get('fixed')} of {r.get('sent')} fixed, "
                f"{r.get('regressions')} regression(s), {r.get('conflicts')} conflict(s)"
                + (f", reverted {', '.join(rv)}" if rv else "")
                + f", {'accepted' if r.get('accepted') else 'not accepted'}"
                + (f"\n      {r['why']}" if r.get("why") else "")
            )
    for x in v.get("conflicts") or []:
        L.append(
            f"  ⚖ {', '.join(x.get('x') or [])} (rank {x.get('x_rank')}) vs {x.get('y')} "
            f"(rank {x.get('y_rank')}): {x.get('verdict')} -> {x.get('action')}"
        )
    sec = v.get("security")
    if sec:
        sev = [f"{sec[s]} {s}" for s in ("critical", "high", "medium", "low") if sec.get(s)]
        L.append(
            "Security review: "
            + (", ".join(sev) if sev else "no findings")
            + (
                f" ({sec.get('confirmed')} confirmed, {sec.get('probes')} with a test)"
                if sev
                else ""
            )
            + (f"; dependency audit {sec['audit']}" if sec.get("audit") else "")
        )
    rd = v.get("readiness") or {}
    if rd.get("repair"):
        r = rd["repair"]
        L.append(
            f"Readiness repair: skipped ({r.get('why')})"
            if r.get("status") == "skipped"
            else f"Readiness repair: repairing {r.get('sent')} item(s)…"
            if r.get("status") == "repairing"
            else f"Readiness repair: {r.get('fixed')} of {r.get('sent')} fixed, "
            f"{r.get('regressions')} regression(s), "
            f"{'accepted' if r.get('accepted') else 'not accepted'}"
        )
    card = rd.get("final") or rd.get("checks")
    if card:
        L.append(
            f"Readiness{'' if rd.get('final') else ' (before repair)'}: "
            f"{card.get('green', 0)} green, {card.get('amber', 0)} amber, "
            f"{card.get('red', 0)} red"
        )
    its = [i for i in v.get("interpretations") or [] if i.get("kind") != "binary"]
    sets = [i for i in v.get("interpretations") or [] if i.get("kind") == "binary"]
    if sets:
        L.append(
            f"Settings: {len(sets)} ("
            + ", ".join(f"{i.get('setting')}={i.get('chosen')}" for i in sets[:6])
            + ")"
        )
    if its:
        L.append(
            f"Interpretations: {len(its)} ("
            + ", ".join(
                f"{i.get('item')}={i.get('chosen')}" + (" forked" if i.get("forked") else "")
                for i in its[:6]
            )
            + ")"
        )
    return L


def task_lines(m: dict, indent: str = "      ") -> list[str]:
    """A milestone's tasks (build v3 §3) under it; a design task with its N candidates and
    the winner."""
    out = []
    for t in m.get("tasks") or []:
        status = t.get("status") or "pending"
        kind = t.get("kind") or "routine"
        extra = [kind]
        if kind == "design" and t.get("n"):
            extra[0] = f"design, {t['n']} ways"
        if status in ("passed", "failed") and t.get("winner") is not None:
            extra.append(
                f"winner node {t['winner']}" if kind == "design" else f"node {t['winner']}"
            )
        if status == "failed":
            extra.append("shortfall")
        out.append(
            f"{indent}{MARKS.get(status, '·')} {t.get('id')} {t.get('title') or ''} "
            f"({', '.join(extra)})"
        )
        for c in t.get("candidates") or []:
            win = "★" if c.get("outcome") == "winner" else " "
            rules = fmt_pair(c.get("rules"))
            bits = [
                b
                for b in (
                    f"rules {rules}" if rules else None,
                    f"pairwise {c['points']:g}" if c.get("points") is not None else None,
                )
                if b
            ]
            out.append(
                f"{indent}  {win} node {c.get('node')}: {c.get('hint_text') or ''}"
                + (f" ({', '.join(bits)})" if bits else "")
            )
        if status in ("passed", "failed") and kind == "design" and t.get("reason"):
            out.append(f"{indent}    why: {t['reason']}")
    return out


def ok(c: dict) -> bool:
    """A candidate's mark: valid, and (milestone runs) its gate passed."""
    return bool(c.get("valid")) and (c.get("gate") or {}).get("passed", True) is not False


def candidate_label(c: dict, numbers: dict | None = None) -> str:
    numbers = numbers or {}
    if c.get("milestone"):
        what = c["milestone"]
        if c.get("task_id"):
            what = c["task_id"] + (f" way {c['hint'] + 1}" if c.get("hint") is not None else "")
        par = c.get("parent")
        if c.get("kind") == "refine" and par is not None:
            what += f", from {numbers.get(par, f'node {par}')}"
        elif c.get("strategy"):
            what += f", {c['strategy']}"
        return f"candidate {c['candidate']} ({what})"
    if c.get("kind") == "refine":
        par = c.get("parent")
        what = f"refine of {numbers.get(par, f'node {par}')}" if par is not None else "refine"
        if c.get("strategy"):
            what += f", {c['strategy']}"
        return f"candidate {c['candidate']} ({what})"
    return f"candidate {c['candidate']} ({c.get('strategy') or c.get('kind')})"


def candidate_result(c: dict) -> str:
    parts = []
    if u := fmt_pair(c.get("user_tests")):
        parts.append(f"user tests {u}")
    if a := fmt_pair(c.get("acceptance")):
        parts.append(f"acceptance {a}")
    if c.get("coverage") is not None:
        parts.append(f"coverage {c['coverage']:.2f}")
    if r := fmt_pair(c.get("rules")):
        parts.append(f"rules {r}")
    if (score := c.get("score")) is not None:
        parts.append(f"score {score:.2f}")
    if c.get("usd") is not None:
        parts.append(f"${float(c['usd']):.2f}")
    if c.get("exit_reason") not in (None, "ok"):
        parts.append(f"worker stopped: {c['exit_reason']}")
    g = c.get("gate")
    if g:
        sl = fmt_pair(g.get("slice"))
        parts.append(
            ("gate passed" if g.get("passed") else "gate failed")
            + (f" ({sl} requirements)" if sl else "")
        )
    return ", ".join(parts)


class Renderer:
    """One human line per event on ``out``. ``interactive``: the stdin oracle prints the
    questions itself, so question/answer events are not echoed. A heartbeat line every
    ``heartbeat_s`` seconds while candidates run keeps long builds from looking hung."""

    def __init__(self, out=None, interactive: bool = False, heartbeat_s: float = HEARTBEAT_S):
        self.out = out or sys.stderr
        self.interactive = interactive
        self.heartbeat_s = heartbeat_s
        self.numbers: dict = {}  # node id -> "candidate N"
        self.running: set = set()
        self.calls: dict = {}  # call id -> its call_started event (running)
        self.long_call_s = LONG_CALL_S
        self.told: set = set()  # calls already given their "still working" line
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ticker: threading.Thread | None = None
        self.failed = False  # printed a build_finished error line

    def p(self, line: str) -> None:
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()

    def __call__(self, ev: dict) -> None:
        line = self.line(ev)
        if line:
            self.p(line)

    def line(self, ev: dict) -> str | None:
        t = ev["type"]
        if t == "build_started":
            self.t0 = ev["ts"]
            self._start_ticker()
            title = f": {ev['title']}" if ev.get("title") else ""
            return (
                f"repo-factory build {ev['task']}{title}\n"
                f"  policy {ev.get('policy')}, harness {ev.get('harness')}, "
                f"builder {ev.get('builder')}"
            )
        if t == "revise_started":
            self.t0 = ev["ts"]
            if ev.get("parent") is not None:
                self.numbers[ev["parent"]] = f"the delivered code (node {ev['parent']})"
            self._start_ticker()
            return (
                f"repo-factory revise {ev['task']}: one more round from node {ev.get('parent')} "
                f'with your note:\n  "{ev.get("note")}"'
            )
        if t == "question":
            return (
                None
                if self.interactive
                else f"? Question {ev.get('n')} of {ev.get('of')}: {ev.get('text')}"
            )
        if t == "answer":
            if self.interactive:
                return None
            return f"  -> {ev['text']}" if ev.get("text") is not None else "  -> (no answer)"
        if t == "plan":
            ms = ev.get("milestones") or []
            head = (
                f"✓ replanned after backtrack {ev['backtrack']}"
                if ev.get("backtrack")
                else "✓ replanned with your correction"
                if ev.get("replanned")
                else f"✓ plan: {len(ms)} milestones"
            )
            return head + "".join(
                f"\n  {i}. {m.get('title')} ({m.get('size')})" for i, m in enumerate(ms, 1)
            )
        if t == "milestone_started":
            lim = ""
            if ev.get("max_turns") or ev.get("max_usd"):
                lim = f" (up to {ev.get('max_turns')} turns, ${ev.get('max_usd')} per run)"
            return f"▸ milestone {ev.get('n')} of {ev.get('of')}: {ev.get('title')}{lim}"
        if t == "milestone_finished":
            mark = "✓" if ev.get("passed") else "✗"
            cov = ev.get("coverage")
            cov = f"coverage {float(cov):.2f}, " if cov is not None else ""
            short = "" if ev.get("passed") else " (shortfall recorded; moving on)"
            return (
                f"{mark} milestone {ev.get('n')} of {ev.get('of')}: {ev.get('title')}: "
                f"{cov}{ev.get('attempts')} run(s), ${float(ev.get('usd') or 0):.2f}{short}"
            )
        if t == "task_started":
            if ev.get("kind") == "design":
                return (
                    f"▸ task {ev.get('task_id')}: {ev.get('title')} (design: {ev.get('n')} "
                    "ways in parallel)"
                    + "".join(f"\n    way {i}: {h}" for i, h in enumerate(ev.get("hints") or [], 1))
                )
            return f"▸ task {ev.get('task_id')}: {ev.get('title')}"
        if t == "task_finished":
            mark = "✓" if ev.get("passed") else "✗"
            if ev.get("kind") == "design":
                return (
                    f"{mark} task {ev.get('task_id')}: winner node {ev.get('winner')} of "
                    f"{ev.get('n')} ({ev.get('reason')})"
                )
            return f"{mark} task {ev.get('task_id')}: node {ev.get('winner')} ({ev.get('reason')})"
        if t == "controller_decision":
            return ("◆ controller " + decision_line(ev).removeprefix("↳ ")) if notable(ev) else None
        if t == "backtrack":
            return (
                f"↶ backtrack {ev.get('number')}: {ev.get('label')} (node {ev.get('node')}); "
                f"{len(ev.get('discard') or [])} run(s) set aside; design change: "
                f"{ev.get('change')}"
            )
        if t == "replan":
            return (
                f"✓ replan after backtrack {ev.get('number')} from {ev.get('from_milestone')}"
                + (": interface changed" if ev.get("interface_changed") else "")
                + (f"; {len(ev['retired'])} rule(s) retired" if ev.get("retired") else "")
            )
        if t == "adr_revised":
            return f"✓ ADR revision {ev.get('revision')}: {ev.get('change')}"
        if t == "design_started":
            return "▸ designing: dimensions, options, rule walks"
        if t == "design_finished":
            dims = ev.get("dimensions") or []
            return (
                f"✓ design: {len(dims)} dimension(s), {len(ev.get('forked') or [])} forked"
                + "".join(
                    f"\n  {x['name']}: {x.get('chosen')} ({x.get('reason') or ''})" for x in dims
                )
            )
        if t == "build_once_started":
            return (
                f"▸ one-shot build{' after design backtrack ' + str(ev['epoch']) if ev.get('epoch') else ''}"
                f" (one session, up to {ev.get('max_turns')} turns, ${ev.get('max_usd')})"
            )
        if t == "build_once_finished":
            return (
                f"{'✓' if ev.get('valid') else '✗'} one-shot build: node {ev.get('node')}, "
                f"${float(ev.get('usd') or 0):.2f}, {fmt_duration(ev.get('wall_s'))}"
            )
        if t == "harden_round":
            if ev.get("status") == "repairing":
                return (
                    f"▸ hardening round {ev.get('round')}: repairing {ev.get('repaired')} rule(s)"
                )
            verdict = (
                "accepted"
                if ev.get("accepted")
                else "rejected"
                if ev.get("accepted") is False
                else "no repair"
            )
            return (
                f"{'✓' if ev.get('accepted') is not False else '✗'} hardening round "
                f"{ev.get('round')}: {verdict}"
                + (f"; stopped: {ev['stop']}" if ev.get("stop") else "")
                + (f" ({ev['why']})" if ev.get("why") else "")
            )
        if t == "backtrack_design":
            if ev.get("status") == "done":
                return (
                    f"✓ design backtrack {ev.get('number')}: rebuilt line "
                    f"{'kept' if ev.get('kept') else 'set aside'} ({ev.get('why')})"
                )
            return (
                f"↶ design backtrack {ev.get('number')}: {ev.get('dimension')} "
                f"{ev.get('from')} -> {ev.get('to')} (rule {ev.get('rule')}); rebuilding"
            )
        if t == "budget_warning":
            return (
                f"! budget:{ev.get('n')} forks of task {ev.get('task_id')} could take the "
                f"build to ${float(ev.get('committed_usd') or 0):.2f}, over its "
                f"${float(ev.get('budget_usd') or 0):.2f} budget"
            )
        if t == "tests_generated":
            return (
                f"✓ acceptance tests: {ev.get('visible')} visible to workers, "
                f"{ev.get('held_back')} held back to check the result"
            )
        if t == "brief":
            if ev.get("error"):
                return f"  · no brief for {ev.get('unit')}: {ev['error']}"
            lint = ev.get("lint") or {}
            cost = "cached" if ev.get("cached") else f"${float(ev.get('usd') or 0):.2f}"
            return (
                f"  · brief for {ev.get('unit')}: {lint.get('cited')}/{lint.get('criteria')} "
                f"criteria cited, {cost}"
            )
        if t == "call_started":
            self.calls[ev.get("call")] = ev
            return None
        if t == "call_finished":
            self.calls.pop(ev.get("call"), None)
            self.told.discard(ev.get("call"))
            return None
        if t == "candidate_started":
            self.running.add(ev["candidate"])
            return f"… {candidate_label(ev, self.numbers)} started"
        if t == "candidate_finished":
            self.running.discard(ev["candidate"])
            if ev.get("node") is not None:
                self.numbers[ev["node"]] = f"candidate {ev['candidate']}"
            mark = "✓" if ok(ev) else "✗"
            bad = "" if ev.get("valid") else "not valid: "
            return f"{mark} {candidate_label(ev, self.numbers)}: {bad}{candidate_result(ev)}"
        if t == "candidates_rescored":
            n = len(ev.get("dropped_tests") or [])
            return (
                f"= {n} broken generated test(s) dropped; rescored "
                f"{len(ev.get('candidates') or [])} earlier candidate(s)"
            )
        if t == "delivered":
            if ev.get("node") is None:
                return "✗ no valid candidate; the branch holds your seed code only"
            if ev.get("changed") is False:
                return f"= no new candidate beat the delivered one; {ev.get('branch')} unchanged"
            who = self.numbers.get(ev["node"], f"node {ev['node']}")
            return f"✓ delivered {who} on branch {ev.get('branch')}"
        if t == "mining_started":
            return "· rules: " + mining_line({**ev, "running": True})
        if t == "mining_finished":
            fix = ev.get("fix_items") or []
            tail = f"; {len(fix)} fix item(s) for the next worker" if fix else ""
            err = f" (error: {ev['error']})" if ev.get("error") else ""
            return "· rules: " + mining_line({**ev, "running": False}) + tail + err
        if t == "rule_promoted":
            return (
                f"↑ held rule {ev.get('id')} caught a real bug; shown to the fix loop, "
                "a held replacement will be mined"
            )
        if t == "verdict":
            note = f": {ev['note']}" if ev.get("note") else ""
            return f"✓ verdict {ev.get('verdict')} ({ev.get('source')}){note}"
        if t == "build_finished":
            self.stop.set()
            if ev.get("status") == "error":
                self.failed = True
                return f"✗ build failed after {fmt_duration(ev.get('wall_s'))}: {ev.get('error')}"
            return None
        return None

    def _start_ticker(self) -> None:
        if self.ticker is not None or not self.heartbeat_s:
            return

        def tick():
            last = time.time()
            step = min(self.heartbeat_s, max(1.0, self.long_call_s / 4))
            while not self.stop.wait(step):
                for line in self.long_calls():
                    self.p(line)
                if time.time() - last < self.heartbeat_s:
                    continue
                last = time.time()
                if self.running:
                    n = len(self.running)
                    self.p(
                        f"  · {n} candidate{'s' if n > 1 else ''} still running "
                        f"({fmt_duration(time.time() - self.t0)} elapsed)"
                    )

        self.ticker = threading.Thread(target=tick, daemon=True)
        self.ticker.start()

    def long_calls(self, now: float | None = None) -> list[str]:
        """One line per model call that has run longer than ``long_call_s``, once each."""
        from .events import call_label

        now = time.time() if now is None else now
        out = []
        for ev in list(self.calls.values()):
            if (
                ev.get("call") not in self.told
                and now - float(ev.get("ts") or now) >= self.long_call_s
            ):
                self.told.add(ev.get("call"))
                out.append(
                    f"  · still working ({fmt_duration(now - ev['ts'])}): "
                    f"{call_label(ev)} [{ev.get('caller')}]"
                )
        return out


def mining_line(m: dict) -> str:
    """The rule set's mining pass in words: "mining interactions after m3… 52 active, 3
    caught" while it runs (the UI shows the same line)."""
    modes = ", ".join(m.get("modes") or []) or "rules"
    after = m.get("after")
    where = (
        "from the plan"
        if m.get("point") == "plan"
        else f"in {after}"
        if str(after or "").startswith("review")
        else f"after {after}"
    )
    if m.get("point") != "plan" and not m.get("modes"):
        modes = "checking the plan's rules"
    bg = " (in the background)" if m.get("async") else ""
    head = f"mining {modes} {where}{bg}…" if m.get("running") else f"mined {modes} {where}{bg}:"
    return f"{head} {m.get('active', 0)} active, {m.get('caught', 0)} caught"


def cost_line(st: dict) -> str:
    """``$12.10 total ($4.44 workers, $7.20 mining, ...)`` from ``build_finished``."""
    b = st.get("cost_breakdown") or {}
    parts = ", ".join(f"${float(v):.2f} {k}" for k, v in b.items() if v)
    return f"${float(st.get('cost_usd') or 0):.2f} total" + (f" ({parts})" if parts else "")


def candidate_numbers(st: dict) -> dict:
    """node id -> how to name it in a folded state's lines."""
    numbers = {
        c["node"]: f"candidate {c['candidate']}"
        for c in st.get("candidates", [])
        if c.get("node") is not None
    }
    if st.get("parent") is not None:
        numbers[st["parent"]] = f"the delivered code (node {st['parent']})"
    return numbers


def summary(st: dict, taskdir: str | Path | None = None) -> str:
    """Plain-English closing summary of a folded build state (``events.fold``)."""
    taskdir = short_path(taskdir or st.get("taskdir"))
    kind = "Revision" if st.get("kind") == "revise" else "Build"
    status = st.get("status")
    cands = [c for c in st.get("candidates", []) if c.get("status") == "done"]
    L = []
    if status == "running":
        L.append(
            f"{kind} of {st['task']} is still running ({len(cands)} candidates finished so far)."
        )
    elif status == "interrupted":
        L.append(f"{kind} of {st['task']} was interrupted (its process is gone).")
    elif status == "error":
        L.append(f"{kind} of {st['task']} failed: {st.get('error')}")
    elif st.get("cost_breakdown"):
        L.append(
            f"{kind} of {st['task']} finished in {fmt_duration(st.get('wall_s'))}: "
            f"{len(cands)} candidate runs, {cost_line(st)}."
        )
    else:
        L.append(
            f"{kind} of {st['task']} finished in {fmt_duration(st.get('wall_s'))}: "
            f"{len(cands)} candidate runs, ${float(st.get('cost_usd') or 0):.2f} "
            "worker cost."
        )
    if st.get("title"):
        L += ["", f"What was asked: {st['title']}"]
    if st.get("note"):
        L += [f'Your note: "{st["note"]}"']
    if st.get("milestones"):
        done = sum(1 for m in st["milestones"] if m.get("status") == "passed")
        L += ["", f"Milestones: {done} of {len(st['milestones'])} passed their gate"]
        L += milestone_lines(st)
    d = st.get("delivered")
    if not d:
        return "\n".join(L)
    out, branch = short_path(d.get("out")), d.get("branch")
    if d.get("node") is None:
        L += [
            "",
            "No candidate passed your tests, so nothing was delivered: the branch "
            f"{branch} in {out} holds your seed code only.",
            f"  Why:              less {short_path(d.get('report'))}",
            f"  Then edit {taskdir}/spec.md (or your tests) and run: repo-factory build {taskdir}",
        ]
        return "\n".join(L)
    best = next((c for c in cands if c.get("node") == d["node"]), None)
    L.append("")
    if d.get("changed") is False:
        L.append("No new candidate passed your tests, so the delivery is unchanged.")
    elif best:
        h = d.get("held_back")
        held = f", held-back tests {h['passed']}/{h['total']}" if h else ""
        L.append(
            f"Delivered {candidate_label(best, candidate_numbers(st))}: "
            f"{candidate_result(best)}{held}."
        )
    L += [
        "",
        f"Your code is on branch {branch} in {out}",
        f"  See the change:   git -C {out} diff main..{branch}",
    ]
    if st.get("kind") == "revise" and d.get("commit"):
        L.append(f"  This revision:    git -C {out} show {d['commit'][:10]}")
    L += [
        f"  Read the report:  less {short_path(d.get('report'))}",
        "",
        "Next:",
        f"  Happy with it:    repo-factory verdict --task {taskdir} accept",
        f'  Want changes:     repo-factory revise {taskdir} "what to change"',
        f'  Not usable:       repo-factory verdict --task {taskdir} reject "why"',
    ]
    v = st.get("verdict")
    if v:
        L += [
            "",
            f"Verdict so far: {v['verdict']} ({v['source']})"
            + (f": {v['note']}" if v.get("note") else ""),
        ]
    return "\n".join(L)


def activity_lines(st: dict, now: float | None = None) -> list[str]:
    """``repo-factory status``: the phase, progress and cost so far, then what is running now."""
    a = st.get("activity") or {}
    if not a:
        return []
    now = time.time() if now is None else now
    pr = a.get("progress") or {}
    bits = [f"phase: {a.get('phase')}" + (f" ({a['stage']})" if a.get("stage") else "")]
    m = pr.get("milestones") or [0, 0]
    if m[1]:
        bits.append(f"milestones {m[0]}/{m[1]}")
    if pr.get("tasks"):
        bits.append(f"{pr.get('milestone')} tasks {pr['tasks'][0]}/{pr['tasks'][1]}")
    cost = a.get("cost") or {}
    parts = ", ".join(f"${v:.2f} {k}" for k, v in (cost.get("breakdown") or {}).items() if v)
    bits.append(
        f"cost{' so far' if st.get('status') == 'running' else ''} "
        f"${float(cost.get('total') or 0):.2f}" + (f" ({parts})" if parts else "")
    )
    if st.get("started"):
        end = st.get("finished") or now
        bits.append(
            f"{'took' if st.get('finished') else 'elapsed'} {fmt_duration(end - st['started'])}"
        )
    L = ["Now: " + " · ".join(bits)]
    if a.get("running"):
        L.append("Running now:")
        for x in a["running"]:
            age = fmt_duration(now - x["started"]) if x.get("started") else "?"
            L.append(f"  … {x.get('label')} ({age})")
    return L


def status_text(st: dict | None, taskdir: str | Path | None = None) -> str:
    """``repo-factory status``: questions, candidates and delivery of the latest build."""
    if st is None:
        return "No builds recorded yet. Start one with: repo-factory build <taskdir>"
    words = {"ok": "finished", "error": "failed"}
    L = [f"{st['task']}: {st['kind']} {st['build']} — {words.get(st['status'], st['status'])}"]
    L += activity_lines(st)
    if st.get("questions"):
        L.append("Questions:")
        for q in st["questions"]:
            a = (
                q["answer"]
                if q.get("answered") and q["answer"] is not None
                else ("(no answer)" if q.get("answered") else "(waiting for an answer)")
            )
            text = q.get("text")
            if q["id"] == "plan" and text:  # the plan review: its first line only
                text = "the build plan (see Milestones)"
            L.append(f"  {q['id']}: {text}\n      -> {a}")
    if st.get("tests"):
        L.append(
            f"Acceptance tests: {st['tests']['visible']} visible, "
            f"{st['tests']['held_back']} held back"
        )
    if st.get("rules"):
        r = st["rules"]
        line = (
            f"Rules: {r['active']} active ({r['held']} held back), {r['retired']} retired, "
            f"{r['disputed']} disputed"
        )
        if r.get("review_rounds"):
            lr = r.get("last_round") or {}
            line += f"; review loop: {r['review_rounds']} round(s)" + (
                f", stopped: {lr['stop']}" if lr.get("stop") else ""
            )
        d = (st.get("delivered") or {}).get("rules")
        if d:
            line += (
                f"; delivered code passes {d['visible'][0]}/{d['visible'][1]} visible, "
                f"{d['held'][0]}/{d['held'][1]} held-back rules"
            )
        L.append(line)
        if (r.get("mining") or {}).get("running"):
            L.append("  " + mining_line(r["mining"]))
        if r.get("promoted"):
            L.append(f"  held rules promoted to visible: {', '.join(r['promoted'])}")
    L += controller_lines(st.get("controller"))
    L += v4_lines(st.get("v4"))
    L += v5_lines(st.get("v5"))
    for w in st.get("warnings") or []:
        L.append(
            f"Budget warning: {w.get('n')} forks of task {w.get('task_id')} could take the "
            f"build to ${float(w.get('committed_usd') or 0):.2f} "
            f"(budget ${float(w.get('budget_usd') or 0):.2f})"
        )
    if st.get("candidates"):
        L.append("Candidates:")
        numbers = candidate_numbers(st)
        for c in st["candidates"]:
            if c.get("status") == "running":
                L.append(f"  … {candidate_label(c, numbers)}: running")
            else:
                mark = "✓" if ok(c) else "✗"
                L.append(f"  {mark} {candidate_label(c, numbers)}: {candidate_result(c)}")
    L += ["", summary(st, taskdir)]
    return "\n".join(L)
