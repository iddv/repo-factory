"""A product's Builds (UXD-DESIGN §2, UXI-1): one record per attempt, derived from what exists.

Every attempt to make a product is a numbered round in the task's event log (``build: N``,
folders ``.den/v5/r<N>/``); the page calls round N **Build N**. A fresh build, a retry and a
revision (Ask for changes) are each a new Build; a repair round inside the pipeline is part of
one; a stopped Build that is resumed stays the same Build.

``runs(taskdir, idea, history)`` reads the event log (start, finish, resume, the stages each
round ran and why the others did not), the cost ledger (cost and tokens per round), the
gate's verdict per round (``.den/v5/r<N>/gate.json``, written at judge time from 0.3.58 on;
older rounds from the idea's ``build.changes`` and its history notes) and the idea's history
(who started it, what happened to the result after: published, held by the pre-publish check,
stopped by you). ``run(taskdir, n, ...)`` is one Build's folded state, as ``api/build``'s
``state`` is for the latest.

Read-only and deterministic: no model, no network, nothing written (``loop.gate.record`` writes
``gate.json`` when the loop judges a round).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..events import START_TYPES, alive, fold, log_path, read_events
from ..loop.gate import GATE_FILE

# mutants: retired (AU1 #4); only an old round that ran it lists it, in the place it ran
# The simple flow's steps (BF-DESIGN: plan, check, fix) take their places among today's; a round
# lists only its own flow's
STAGE_ORDER = (
    "reproduce",
    "scope",
    "plan",
    "build",
    "claims",
    "verify",
    "mutants",
    "oracle",
    "check",
    "repair",
    "reverify",
    "fix",
    "readiness",
    "deliver",
)
RAN = ("done", "failed", "stopped", "started", "running", "held", "capped", "passed")
CARRIED = re.compile(r"carried from round (\d+)")


def run_dir(taskdir: Path, n: int) -> Path:
    return Path(taskdir) / ".den" / "v5" / f"r{n}"


def _read_gate(taskdir: Path, n: int) -> dict | None:
    try:
        g = json.loads((run_dir(taskdir, n) / GATE_FILE).read_text())
    except (OSError, ValueError):
        return None
    return g if isinstance(g, dict) and g.get("verdict") else None


def round_gate(taskdir: Path, n: int, idea: dict | None = None) -> tuple[dict, str] | None:
    """Build N's gate as it was kept, with where: the round's ``gate.json`` (``record``), else
    the idea record's gate when it is this round's (``idea``: a revision writes no
    ``gate.json``). The page's gate and the trace's links read the same one."""
    g = _read_gate(taskdir, n)
    if g:
        return g, "record"
    ig = (idea or {}).get("gate")
    if isinstance(ig, dict) and ig and ig.get("round") == n:
        return ig, "idea"
    return None


# -- who started a Build ----------------------------------------------------------------------
def who(origin: str | None, note: str = "") -> dict:
    """``{by: you|repo-factory|agent|chat, door, name}``: a press is yours whatever the door (the page,
    the command line, repo-factory mcp); the pre-publish check and the loop's own retries are repo-factory's."""
    o = str(origin or "")
    if o in ("ui", "cli", "mcp"):
        return {"by": "you", "door": o, "name": None}
    if o == "chat":
        return {"by": "chat", "door": "chat", "name": None}
    if o.startswith("agent:"):
        return {"by": "agent", "door": "mcp", "name": o[len("agent:") :]}
    if o in ("honesty", "auto"):  # the wording check's, a hold's repair (AU1 #2)
        return {"by": "repo-factory", "door": o, "name": None}
    if not o and re.search(r"by the owner|^retry", note or ""):
        return {"by": "you", "door": None, "name": None}
    return {"by": "repo-factory", "door": None, "name": None}


def _data(h: dict) -> dict:
    d = h.get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = None
    return d if isinstance(d, dict) else {}


# -- the stages one Build ran --------------------------------------------------------------------
def stage_rows(st: dict) -> list[dict]:
    """The stages of one Build in order, each ``{stage, status, why, reason, from, usd,
    wall_s, tokens, runs}``. ``reason`` says why a stage did not run, in one of three words:
    ``carried`` (from Build ``from``), ``not_needed`` (verify found nothing), ``limit``
    (the repair rounds' limit); else None."""
    v = st.get("v5") or {}
    stages = v.get("stages") or {}
    planned = list(v.get("planned") or [])
    names = [k for k in STAGE_ORDER if k in stages or k in planned] + [
        k for k in stages if k not in STAGE_ORDER
    ]
    out = []
    live = st.get("status") == "running"
    for name in names:
        s = stages.get(name) or {}
        status = s.get("status") or ("pending" if live else "not_run")
        if status == "started":
            status = "running" if live else "stopped"
        why = s.get("why")
        reason, frm = None, None
        if status == "skipped":
            m = CARRIED.search(str(why or ""))
            if m:
                reason, frm = "carried", int(m.group(1))
            elif "found nothing" in str(why or ""):
                reason = "not_needed"
            elif "max_rounds" in str(why or "") or "limit" in str(why or ""):
                reason = "limit"
            else:
                reason = "skipped"
        tok = None
        if s.get("tokens_in") is not None or s.get("tokens_out") is not None:
            tok = {"in": int(s.get("tokens_in") or 0), "out": int(s.get("tokens_out") or 0)}
        out.append(
            {
                "stage": name,
                "status": status,
                "why": why,
                "reason": reason,
                "from": frm,
                "usd": s.get("usd"),
                "wall_s": s.get("wall_s"),
                "tokens": tok,
                "runs": s.get("runs"),
                "started": s.get("started"),
            }
        )
    return out


def repair_rounds(st: dict) -> list[dict]:
    """The repair rounds inside one Build (part of it, never Builds of their own); the simple
    flow's fix rounds alike."""
    v = st.get("v5") or {}
    return [
        {k: r.get(k) for k in ("round", "status", "sent", "fixed", "regressions", "why")}
        for r in v.get("rounds") or []
    ]


# -- the gate and what happened after, from the history notes ------------------------------------
GATE_NOTE = re.compile(
    r"^(?:built(?: \(\$[\d.]+\))?; (?:the gate passed|held by the gate: (.*))"
    r"|revised in round (\d+) \([^)]*\): the gate says (ready|held)(?:; (.*))?)$",
    re.S,
)


CHECKS = ("delivered", "documents", "security", "tests", "safe_defaults", "completeness")
"""The release checks (loop/gate.py), six: what "5 of 6 passed" counts. A round judged before
the ``p_hidden`` check went (AU1 #5) kept its checks, seven, in its ``gate.json``."""
REASON_START = re.compile(r"(?:^|; )(?=(?:" + "|".join(CHECKS) + r"): )")


def _gate(verdict, reasons, source: str, checks: dict | None = None) -> dict:
    """A Build's gate: the verdict, each failing check's reason, and how many of the checks
    passed (from the checks when they were kept, else the failing reasons' count)."""
    reasons = [str(r) for r in reasons or []]
    total = len(checks) if checks else len(CHECKS)
    failed = (
        sum(1 for c in checks.values() if not (c or {}).get("ok"))
        if checks
        else (0 if verdict == "ready" else len(reasons) or None)
    )
    return {
        "verdict": verdict,
        "reasons": reasons,
        "source": source,
        "passed": None if failed is None else max(0, total - failed),
        "total": total,
    }


def _gate_from_note(note: str) -> dict | None:
    m = GATE_NOTE.match(note or "")
    if not m:
        return None
    if m.group(3):
        verdict, text = m.group(3), m.group(4)
    elif note.startswith("built") and "the gate passed" in note:
        verdict, text = "ready", None
    else:
        verdict, text = "held", m.group(1)
    # "tests: …; completeness: 0/7 flows …": one reason per failing check
    reasons = [x for x in REASON_START.split(text) if x] if text else []
    return _gate(verdict, reasons, "history")


def _after(rows: list[dict]) -> dict | None:
    """What happened to a Build's result once it was judged, from the history rows in its
    window: published privately, made public, kept private, or held before the push: by a
    pre-push scan (``scan``: a secret, what was taken from a studied program, a study left
    unrecorded), or, before 2026-10-16, by the wording checks alone (``wording``: the honesty
    check, the jargon scan; never a hold since). The last one wins."""
    out = None
    for h in rows:
        note = str(h.get("note") or "")
        if note.startswith("held: not pushed: "):
            why = note[len("held: not pushed: ") :]
            facts = re.search(r"(^|; )(secrets scan|rea scan|studied record): ", why)
            out = {"what": "scan" if facts else "wording", "why": why, "at": h["ts"]}
        elif note.startswith("published privately"):
            m = re.search(r"https://\S+", note)
            out = {"what": "published", "url": m.group(0) if m else None, "at": h["ts"]}
        elif h.get("state") == "public" and note.startswith("approved"):
            out = {"what": "public", "at": h["ts"]}
        elif note.startswith("kept private"):
            out = {"what": "kept", "at": h["ts"]}
    return out


# -- all of them --------------------------------------------------------------------------------
def runs(
    taskdir: Path,
    idea: dict | None = None,
    history: list[dict] | None = None,
    events: list[dict] | None = None,
) -> list[dict]:
    """Every Build of the product at ``taskdir``, oldest first. ``idea``: the loop's record
    (its stop, pause and gate); ``history``: the idea's history rows (who started each
    Build, the gate's older verdicts, what happened after), oldest first."""
    from ..costs import ledger as led
    from ..loop.ledger import add_tokens, tokens_of

    taskdir = Path(taskdir)
    evs = events if events is not None else read_events(log_path(taskdir / ".den"))
    starts: dict[int, dict] = {}
    finished: dict[int, dict] = {}
    resumed: dict[int, list] = {}
    last_ts: dict[int, float] = {}
    for e in evs:
        n = e.get("build")
        if not isinstance(n, int):
            continue
        last_ts[n] = max(last_ts.get(n, 0), float(e.get("ts") or 0))
        t = e.get("type")
        if t in START_TYPES and n not in starts:
            starts[n] = e
        elif t == "build_finished":
            finished[n] = e
        elif t == "build_resumed":
            resumed.setdefault(n, []).append(e.get("ts"))
    cost: dict[int, float] = {}
    toks: dict[int, dict] = {}
    for r in led.read(taskdir / ".den"):
        n = r.get("build")
        if not isinstance(n, int):
            continue
        cost[n] = cost.get(n, 0.0) + float(r.get("usd") or 0.0)
        t = tokens_of(r)
        if t is not None:
            toks[n] = add_tokens(toks.get(n), t)
    rec = (idea or {}).get("build") or {}
    changes = {c.get("round"): c for c in rec.get("changes") or [] if c.get("round")}
    hist = sorted(history or [], key=lambda h: h.get("ts") or 0)
    order = sorted(starts)
    out = []
    for i, n in enumerate(order):
        s = starts[n]
        st = fold(evs, build=n) or {}
        t0 = float(s.get("ts") or 0)
        t1 = float(starts[order[i + 1]].get("ts") or 0) if i + 1 < len(order) else None
        # the history rows of this Build's window: from the press that queued it (just before
        # its start) to the next Build's start
        prev_end = float((finished.get(order[i - 1]) or {}).get("ts") or 0) if i else 0
        win = [
            h
            for h in hist
            if (h.get("ts") or 0) >= t0 - 1 and (t1 is None or (h.get("ts") or 0) < t1 - 1)
        ]
        asks = [
            h
            for h in hist
            if prev_end <= (h.get("ts") or 0) < t0 + 1
            and h.get("state") in ("new", "queued", "building")
        ]
        kind = "fresh"
        frm = None
        if s.get("kind") == "revise":
            kind, frm = "revision", s.get("revises")
        elif s.get("continues"):
            m = re.match(r"r(\d+)", str(s["continues"]))
            kind, frm = "continue", int(m.group(1)) if m else None
        if "reproduce" in (s.get("stages") or []):
            kind = "fix"
        # who pressed for it: the start's own origin (a revision's), else the newest row of
        # the press before it that names one ("retry: …", "sent to build by the owner")
        origin, note = s.get("origin"), ""
        for h in reversed(asks):
            d = _data(h)
            said = str(h.get("note") or "")
            if origin or note:
                break
            if d.get("origin") and not d.get("auto"):
                origin = d["origin"]
            elif re.search(r"by the owner|^retry", said):
                note = said
        by = (
            who(origin, note)
            if (origin or note or not idea)
            else {"by": "repo-factory", "door": None, "name": None}
        )
        if not idea and not origin:
            by = {"by": "you", "door": None, "name": None}  # a build started by hand
        fin = finished.get(n)
        stopped = None
        st_rec = rec.get("stopped") or {}
        for h in win:
            d = _data(h)
            if (
                d.get("kind") == "stop_round"
                and d.get("round") == n
                and h.get("state") != "building"
            ):
                stopped = {"at": h["ts"], "by": who(d.get("origin"))["by"]}
        if st_rec.get("round") == n:
            stopped = {
                "at": st_rec.get("at"),
                "by": "you",
                "resumable": bool(st_rec.get("resumable")),
            }
        paused = (idea or {}).get("state") == "paused" and rec.get("resume_round") == n
        # the outcome, one word: what the Build came to
        if fin is None:
            running = (
                (idea or {}).get("state") == "building" and n == order[-1]
                if idea
                else alive(st.get("pid"))
            )
            if running and not (stopped and n != order[-1]):
                outcome = "running"
            elif paused:
                outcome = "paused"
            elif stopped:
                outcome = "stopped"
            else:
                outcome = "interrupted"
        elif fin.get("status") == "error":
            outcome = "failed"
        else:
            outcome = "delivered"
        gate = None
        kept = round_gate(taskdir, n, idea)
        if kept:
            g, src = kept
            gate = _gate(g.get("verdict"), g.get("reasons"), src, g.get("checks"))
        elif n in changes and changes[n].get("verdict"):
            gate = _gate(changes[n]["verdict"], [], "changes")
        if outcome == "delivered" and (gate is None or gate["source"] == "changes"):
            # the history note says the verdict and, when held, every failing check
            hg = next(
                (x for x in (_gate_from_note(str(h.get("note") or "")) for h in win) if x), None
            )
            if hg and (gate is None or hg["verdict"] == gate["verdict"]):
                gate = hg
        if outcome == "delivered" and gate:
            outcome = "ready" if gate["verdict"] == "ready" else "checks_failed"
        if outcome == "failed" and kind == "revision":
            gate = None  # the checks were not run on a failed revision
        ended = (
            (fin or {}).get("ts")
            or (stopped or {}).get("at")
            or (None if outcome in ("running", "paused") else last_ts.get(n))
        )
        wall = (fin or {}).get("wall_s")
        if wall is None and ended:
            wall = float(ended) - t0
        usd = (fin or {}).get("cost_usd")
        if usd is None:
            usd = round(cost[n], 4) if n in cost else None
        rows = stage_rows(st)
        ran = [x["stage"] for x in rows if x["status"] in RAN]
        carried = sorted({x["from"] for x in rows if x["reason"] == "carried" and x["from"]})
        out.append(
            {
                "n": n,
                "kind": kind,
                "from": frm,
                "mode": s.get("mode"),
                "instructions": (s.get("instructions") or None),
                "started_by": by,
                "started": t0,
                "ended": ended,
                "outcome": outcome,
                "stage": (fin or {}).get("stage")
                or next((x["stage"] for x in rows if x["status"] in ("stopped", "running")), None),
                "error": (fin or {}).get("error"),
                "cost_usd": round(float(usd), 4) if usd is not None else None,
                "tokens": toks.get(n),
                "wall_s": round(wall) if wall else None,
                "ran": ran,
                "carried_from": carried[-1] if carried else None,
                "stages_n": len(rows),
                "gate": gate,
                "after": _after(win) if outcome in ("ready", "checks_failed") else None,
                "stopped": stopped,
                "resumed": [x for x in resumed.get(n, []) if x],
                "latest": n == order[-1],
            }
        )
    return out


def run(taskdir: Path, n: int, idea: dict | None = None, history: list[dict] | None = None) -> dict:
    """One Build: its record (as in ``runs``), its stages in order with why each did not run,
    its repair rounds, and the v5 view of its round (findings, readiness, cost by stage). A
    Build that does not exist is a LookupError."""
    from .v5view import view

    taskdir = Path(taskdir)
    evs = read_events(log_path(taskdir / ".den"))
    rs = runs(taskdir, idea, history, events=evs)
    rec = next((r for r in rs if r["n"] == n), None)
    if rec is None:
        raise LookupError(f"no Build {n}")
    st = fold(evs, build=n) or {}
    if rec["outcome"] == "running" and st.get("status") == "running":
        pass
    elif st.get("status") == "running":
        st["status"] = "interrupted"  # its process is gone: no stage is running
    v = view(taskdir, st)
    return {
        "run": rec,
        "stages": stage_rows(st),
        "repairs": repair_rounds(st),
        "v5": v,
        "of": len(rs),
        "neighbours": {
            "prev": next((r["n"] for r in reversed(rs) if r["n"] < n), None),
            "next": next((r["n"] for r in rs if r["n"] > n), None),
        },
    }
