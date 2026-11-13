"""The v5 view of one build for ``repo-factory ui`` (docs/design/build-v5.md §3.8, Addendum A).

``view(taskdir, st)`` turns the folded state (``st["v5"]``, from ``events.fold``) plus the
run's own files under ``<den>/v5/r<round>/`` (``findings.json``, ``claims.json``) and the
ledger into plain JSON for the page: the stage strip, the findings and their counts, the
operator settings, repair rounds, conflicts, cost by stage
against the vanilla reference, and where ``REPORT.md`` and ``ASSUMPTIONS.md`` are.
Everything here is data; the page renders it with ``textContent`` only.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import gitcmd

STAGES = (
    "scope",
    "build",
    "claims",
    "verify",
    "oracle",
    "repair",
    "reverify",
    "readiness",
    "deliver",
)
OPTIONAL = ("scope", "readiness")  # shown only for runs that have them (older logs don't)
# The simple flow's steps (BF-DESIGN §4): a round of it shows these, never today's
SIMPLE = ("plan", "build", "check", "fix", "deliver")
# The lean flow's (track LEAN): its round lists them in build_started
LEAN = ("scope", "build", "harden", "check", "fix", "deliver")
# A retired stage an old round ran keeps its place in that round's strip: the stage it came
# after. mutants: removed (AU1 #4).
RETIRED = {"mutants": "verify"}
SEVERITIES = ("critical", "high", "medium", "low")
VANILLA_LIMIT = 3.0  # §0: v5 may cost up to 3x the vanilla build


def _json(p: Path) -> dict:
    try:
        out = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def run_dir(taskdir: Path, st: dict) -> Path:
    return Path(taskdir) / ".den" / "v5" / f"r{st.get('build')}"


def _ledger_by_stage(taskdir: Path, rid) -> dict[str, float]:
    """Spend so far per stage, from the ledger (live, before a stage's closing event)."""
    from ..costs import ledger

    out: dict[str, float] = {}
    for r in ledger.read(Path(taskdir) / ".den", rid):
        s = r.get("stage")
        if s:
            out[s] = out.get(s, 0.0) + float(r.get("usd") or 0.0)
    return out


def _ledger_tokens(taskdir: Path, rid) -> tuple[dict[str, dict], dict | None]:
    """Tokens from the ledger: ``(per stage, whole build)``, each ``{"in", "out"}``. A
    stage (or the build) is present only when one of its lines carries token fields: no
    token data is not zero tokens."""
    from ..costs import ledger
    from ..loop.ledger import add_tokens, tokens_of

    by: dict[str, dict] = {}
    total = None
    for r in ledger.read(Path(taskdir) / ".den", rid):
        t = tokens_of(r)
        if t is None:
            continue
        total = add_tokens(total, t)
        if r.get("stage"):
            by[r["stage"]] = add_tokens(by.get(r["stage"]), t)
    return by, total


def _event_tokens(s: dict) -> dict | None:
    """A folded stage's tokens (``tokens_in``/``tokens_out``), None for events from before
    tokens were recorded."""
    if s.get("tokens_in") is None and s.get("tokens_out") is None:
        return None
    return {"in": int(s.get("tokens_in") or 0), "out": int(s.get("tokens_out") or 0)}


def _claim_text(c: dict) -> str:
    bits = [c.get("situation"), c.get("action")]
    text = "; ".join(str(b).rstrip(".") for b in bits if b)
    if c.get("expect"):
        text += (" → " if text else "") + str(c["expect"])
    return text


def stages(
    v: dict, live: bool, spent: dict[str, float], toks: dict[str, dict] | None = None
) -> list[dict]:
    """The stage strip. ``tokens`` per stage: the stage's events' sum, else (a running stage,
    or events without token fields) the ledger's (``toks``), else None."""
    toks = toks or {}
    # the scope and readiness stages only where the run has them (logs from before don't);
    # a live run whose build_started lists its stages shows them all from the start
    planned = set(v.get("planned") or [])
    order = [k for k in STAGES if k not in OPTIONAL or k in v["stages"] or k in planned]
    for k, after in RETIRED.items():
        if k in v["stages"] and after in order:
            order.insert(order.index(after) + 1, k)
    order += [k for k in v["stages"] if k not in order]
    if (planned | set(v["stages"])) & {"plan", "check", "fix"}:  # a simple-flow round
        order = list(SIMPLE) + [k for k in v["stages"] if k not in SIMPLE]
    if "harden" in planned or "harden" in v["stages"]:  # a lean round: the stages it lists
        order = list(v.get("planned") or LEAN) + [
            k for k in v["stages"] if k not in (v.get("planned") or LEAN)
        ]
    if "reproduce" in planned:  # a maintenance fix: exactly the stages it runs
        order = list(v["planned"]) + [k for k in v["stages"] if k not in planned]
    skipped = next(
        (r.get("why") for r in v.get("rounds") or [] if r.get("status") == "skipped"), None
    )
    out = []
    for name in order:
        s = v["stages"].get(name) or {}
        status = s.get("status") or ("pending" if live else "not_run")
        if status == "not_run" and name in ("repair", "reverify", "fix") and skipped:
            s = {**s, "why": skipped}  # the reserve rule, say
        if status == "started":
            status = "running" if live else "stopped"
        usd = s.get("usd")
        if status == "running" or usd is None:
            usd = round(spent[name], 4) if spent.get(name) else usd
        tokens = _event_tokens(s)
        if status == "running" or tokens is None:
            tokens = dict(toks[name]) if toks.get(name) else tokens
        out.append(
            {
                "stage": name,
                "status": status,
                "usd": usd,
                "tokens": tokens,
                "wall_s": s.get("wall_s"),
                "started": s.get("started"),
                "runs": s.get("runs"),
                "why": s.get("why"),
                # CAP-PLAN §3: MCP calls per server, the stage's capabilities, live counts
                "tools_by_server": s.get("tools_by_server"),
                "capabilities": s.get("capabilities"),
                "skills": s.get("skills"),
                "agents": s.get("agents"),
                "live": s.get("live") if status == "running" else None,
                # the study runs inside the scope cell: "study + scope"
                "study": v.get("study") if name == "scope" else None,
            }
        )
    return out


def jsonl_rows(p: Path, limit: int = 200) -> list[dict]:
    """The last ``limit`` JSON rows of a file the factory-mod's collector wrote."""
    try:
        lines = p.read_text(errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def notes_view(root: Path) -> tuple[list[dict], list[dict]]:
    """``(notes, studied)``: the session's notes and the evidence it recorded about a studied
    target (``notes.jsonl``, ``evidence.jsonl`` in the round's folder)."""
    notes = [
        {k: r.get(k) for k in ("text", "stage", "at")} for r in jsonl_rows(root / "notes.jsonl")
    ]
    studied = [
        {k: r.get(k) for k in ("target", "version", "sha256", "behaviour", "source", "stage", "at")}
        for r in jsonl_rows(root / "evidence.jsonl")
    ]
    return notes, studied


def settings_rows(v: dict, findings: dict) -> list[dict]:
    """One row per operator setting: the ``interpretation`` events (kind binary) in order,
    filled in from ``findings.json`` (values, scope, the latest status, both-values results);
    a key a repair round reported as added is ``added``."""
    rows: dict[str, dict] = {}
    for i in v.get("interpretations") or []:
        if i.get("kind") == "binary" and i.get("setting"):
            rows[str(i["setting"])] = {
                "key": i["setting"],
                "id": i.get("item"),
                "default": i.get("chosen"),
                "status": i.get("status"),
                "current": i.get("implements"),
                "values": [],
                "scope": None,
                "tested": None,
                "source": None,
                "question": None,
            }
    for s in findings.get("settings") or []:
        spec = s.get("setting") or {}
        key = str(s.get("key") or spec.get("key") or "")
        if not key:
            continue
        r = rows.setdefault(
            key,
            {
                "key": key,
                "id": s.get("id"),
                "default": None,
                "status": None,
                "current": None,
                "values": [],
                "scope": None,
                "tested": None,
                "source": None,
                "question": None,
            },
        )
        r.update(
            {
                "values": [str(x) for x in spec.get("values") or []],
                "default": spec.get("default", r["default"]),
                "scope": spec.get("scope"),
                "status": s.get("status") or r["status"],
                "current": s.get("current") or r["current"],
                "source": s.get("source"),
                "question": s.get("question"),
                "tested": s.get("values") if isinstance(s.get("values"), dict) else None,
            }
        )
    added = {k for r in v.get("rounds") or [] if r.get("accepted") for k in r.get("settings") or []}
    for k in added:
        if k in rows and rows[k]["status"] in (None, "proposed", "open"):
            rows[k]["status"] = "added"
    return list(rows.values())


def defects(findings: dict, claims: dict) -> list[dict]:
    """The confirmed defects: the claim in prose, observed against expected, and whether the
    delivered head (the harness's latest run) now passes it."""
    by_claim = {str(c.get("id")): c for c in claims.get("claims") or []}
    by_rule = {s.get("rule"): s for s in findings.get("scenarios") or [] if s.get("rule")}
    out = []
    for rid in dict.fromkeys(findings.get("defects") or []):
        s = by_rule.get(rid) or {}
        c = by_claim.get(str(s.get("claim"))) or {}
        hs = s.get("harness_status")
        out.append(
            {
                "id": s.get("id") or rid,
                "rule": rid,
                "claim": s.get("claim"),
                "class": "incomplete" if s.get("class") == "incomplete" else "defect",
                "text": _claim_text(c)
                or (
                    "found by exploring (no spec claim)"
                    if s.get("claim") == "explore"
                    else str(s.get("claim") or "")
                ),
                "flows": s.get("flows") or [],
                "quote": c.get("quote"),
                "observed": s.get("observed"),
                "expected": s.get("expected"),
                "repro": s.get("repro"),
                "scope": s.get("scope"),
                "status": "retired"
                if s.get("store_status") == "retired"
                else "fixed"
                if hs == "passed"
                else "open",
            }
        )
    return out


def label(st: dict, v: dict, rows: list[dict]) -> str | None:
    """The phase pill's words while a v5 build runs: "Verifying: 23 scenarios",
    "Repairing: 4 defects, 2 settings"."""
    cur = next(
        (s for s in reversed(list(v["stages"].values())) if s.get("status") == "started"), None
    )
    if cur is None:
        return v.get("stage")
    name = cur.get("stage")
    f = v.get("findings") or {}
    rules = st.get("rules") or {}
    n_scen = f.get("scenarios") or sum(
        int(rules.get(k) or 0) for k in ("active", "retired", "disputed", "candidate")
    )

    def n(x, word):
        return f"{x} {word}{'' if x == 1 else 's'}"

    if name == "build":
        return "Building: one Claude Code session writes the code, its tests and its README"
    if name == "claims":
        return "Listing claims from the spec"
    if name in ("verify", "reverify"):
        verb = "Verifying" if name == "verify" else "Re-verifying"
        return f"{verb}: {n(n_scen, 'scenario')}" if n_scen else f"{verb}: a fresh session"
    if name == "oracle":
        return (
            f"Three votes on {n(f['failing'], 'failing scenario')}"
            if f.get("failing")
            else "Three votes on each failing scenario"
        )
    if name == "repair":
        r = next(
            (r for r in reversed(v.get("rounds") or []) if r.get("status") == "repairing"), None
        )
        if r is None:
            return "Repairing"
        n_set = sum(1 for s in rows if s.get("status") in ("proposed", "open"))
        n_def = max(int(r.get("sent") or 0) - n_set, 0)
        parts = [n(n_def, "defect")] + ([n(n_set, "setting")] if n_set else [])
        return f"Repairing: {', '.join(parts)}"
    if name == "readiness":
        return v.get("stage") or "Readiness: security review and operational checks"
    if name == "deliver":
        return "Delivering: report, assumptions, branch"
    return v.get("stage")


def is_repo(out: Path) -> bool:
    """``out`` holds a git repository. A folder this process may not read (an old
    absolute path from another machine, or a hidden /home) counts as no repository."""
    try:
        return (Path(out) / ".git").is_dir()
    except OSError:
        return False


EVIDENCE = {
    "ASSUMPTIONS.md": "assumptions.md",
    "SECURITY.md": "security.md",
    "READINESS.md": "readiness.md",
}


def evidence_file(taskdir: Path, st: dict, name: str) -> Path | None:
    """A report in the round's evidence (``exam/evidence/``, EX-PLAN §4.4): where the
    security and readiness reports and the assumptions are since the exam moved out of the
    product; None for a round from before."""
    p = run_dir(taskdir, st) / "exam" / "evidence" / EVIDENCE.get(name, name)
    return p if p.is_file() else None


def assumptions_text(taskdir: Path, st: dict) -> str | None:
    """The build's assumptions: the evidence's, else (an older round) the delivery copy, else
    the branch's."""
    ev = evidence_file(taskdir, st, "ASSUMPTIONS.md")
    if ev is not None:
        return ev.read_text(errors="replace")
    p = run_dir(taskdir, st) / "deliver" / "ASSUMPTIONS.md"
    if p.exists():
        return p.read_text(errors="replace")
    dl = st.get("delivered") or {}
    out = Path(dl.get("out") or Path(taskdir) / "out")
    if is_repo(out) and dl.get("branch"):
        r = gitcmd.run(
            ["show", f"{dl['branch']}:ASSUMPTIONS.md"], cwd=out, capture_output=True, text=True
        )
        if r.returncode == 0:
            return r.stdout
    return None


def scope_text(taskdir: Path, st: dict) -> str | None:
    """``SCOPE.md``: the run's copy, else the delivered branch's."""
    p = run_dir(taskdir, st) / "SCOPE.md"
    if p.exists():
        return p.read_text(errors="replace")
    return delivered_text(taskdir, st, "SCOPE.md")


def delivered_text(taskdir: Path, st: dict, name: str) -> str | None:
    """A delivered file (``SECURITY.md``, ``READINESS.md``, ...): the round's evidence for
    the reports, else the run's delivery copy, else the branch's."""
    ev = evidence_file(taskdir, st, name)
    if ev is not None:
        return ev.read_text(errors="replace")
    p = run_dir(taskdir, st) / "deliver" / name
    if p.exists():
        return p.read_text(errors="replace")
    dl = st.get("delivered") or {}
    out = Path(dl.get("out") or Path(taskdir) / "out")
    if is_repo(out) and dl.get("branch"):
        r = gitcmd.run(["show", f"{dl['branch']}:{name}"], cwd=out, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
    return None


def readiness_view(v: dict, root: Path) -> dict | None:
    """The readiness stage for the page: the security findings by severity (each with its
    status after the repair) and the scorecard (the final one, else the one before the
    repair), from ``readiness.json``; the events' counts while it runs."""
    rec = _json(root / "readiness.json")
    ev_sec, ev = v.get("security") or {}, v.get("readiness") or {}
    if not rec and not ev_sec and not ev:
        return None
    sec = rec.get("security") or {}
    found = [
        {
            "id": f.get("id"),
            "severity": f.get("severity"),
            "title": f.get("title"),
            "status": f.get("status"),
            "sent": bool(f.get("sent")),
            "where": f.get("where"),
            "repro": f.get("repro"),
            "test": bool(f.get("rule")),
        }
        for f in sec.get("findings") or []
        if isinstance(f, dict)
    ]
    counts = (
        {s: sum(1 for f in found if f["severity"] == s) for s in SEVERITIES}
        if found
        else {s: int(ev_sec.get(s) or 0) for s in SEVERITIES}
    )
    fin = rec.get("final") or {}
    items = fin.get("items") or list(((rec.get("checks") or {}).get("items") or {}).values())
    card = [
        {
            "id": i.get("id"),
            "title": i.get("title"),
            "status": i.get("status"),
            "why": i.get("why"),
            "next": i.get("next"),
        }
        for i in items
        if isinstance(i, dict)
    ]
    r = rec.get("round") or {}
    return {
        "ran": bool(sec.get("ran") or ev_sec.get("ran")),
        "findings": found,
        "counts": counts,
        "audit": (sec.get("audit") or {}).get("status") or ev_sec.get("audit"),
        "scorecard": card,
        "final": bool(fin),
        "tally": {
            s: sum(1 for c in card if c["status"] == s) for s in ("green", "amber", "red", "n/a")
        },
        "repair": {
            "sent": len(rec.get("sent") or []),
            "accepted": r.get("accepted"),
            "why": r.get("why"),
        }
        if r
        else (ev.get("repair") or None),
        "skipped": rec.get("skipped"),
    }


def scope_view(v: dict, root: Path, findings: dict) -> dict | None:
    """The scope stage for the page: the decision, the flows of the definition of done
    with the harness's status each (``findings.json`` ``completeness``), and whether
    ``SCOPE.md`` exists."""
    rec = {**(v.get("scope") or {}), **_json(root / "scope.json")}
    if not rec:
        return None
    comp = findings.get("completeness") if isinstance(findings.get("completeness"), dict) else None
    dod = rec.get("dod") if isinstance(rec.get("dod"), list) else []  # the events: a count
    flows = (comp or {}).get("flows") or [
        {"id": f.get("id"), "title": f.get("title"), "status": "pending"}
        for f in dod
        if isinstance(f, dict)
    ]
    return {
        "decision": rec.get("decision"),
        "why": rec.get("why"),
        "mode": rec.get("mode"),
        "verdict": (rec.get("heuristic") or {}).get("verdict") or rec.get("verdict"),
        "shared_base": rec.get("shared_base"),
        "flows": flows,
        "settings": len(rec["settings"])
        if isinstance(rec.get("settings"), list)
        else rec.get("settings"),
        "file": (root / "SCOPE.md").exists(),
    }


def view(taskdir: Path, st: dict | None) -> dict | None:
    if not st or not st.get("v5"):
        return None
    v = st["v5"]
    root = run_dir(taskdir, st)
    findings, claims = _json(root / "findings.json"), _json(root / "claims.json")
    live = st.get("status") == "running"
    spent = _ledger_by_stage(taskdir, st.get("build"))
    stage_toks, ledger_toks = _ledger_tokens(taskdir, st.get("build"))
    strip = stages(v, live, spent, stage_toks)
    rows = settings_rows(v, findings)
    dfx = defects(findings, claims)
    scen = findings.get("scenarios") or []
    f = v.get("findings") or {}
    interp = v.get("interpretations") or []
    comp = findings.get("completeness") if isinstance(findings.get("completeness"), dict) else None
    if comp is None and f.get("completeness"):
        comp = {"done": f["completeness"][0], "total": f["completeness"][1], "flows": []}
    n_inc = sum(1 for d in dfx if d["class"] == "incomplete")
    counts = {
        "confirmed": len(dfx) - n_inc
        if findings.get("defects") is not None
        else f.get("confirmed"),
        "incomplete": n_inc if findings.get("defects") is not None else f.get("incomplete"),
        "environment": len(findings.get("environment") or []) or f.get("environment"),
        "settings": len(rows),
        "retired": sum(1 for s in scen if s.get("class") == "retired")
        if scen
        else f.get("retired"),
        "blocked": len(findings["blocked"])
        if findings.get("blocked") is not None
        else f.get("blocked"),
        "complex": sum(1 for i in interp if i.get("kind") != "binary"),
        "scenarios": f.get("scenarios"),
        "failing": f.get("failing"),
    }
    cost = {s["stage"]: s["usd"] for s in strip if s["usd"] is not None}
    vanilla = cost.get("build") if (cost.get("build") or 0) >= 0.01 else None  # a real build
    total = round(sum(cost.values()), 4)
    if not live and st.get("cost_usd"):
        total = max(total, float(st["cost_usd"]))
    from ..loop.ledger import add_tokens

    tok_total = None
    for s in strip:
        tok_total = add_tokens(tok_total, s["tokens"])
    if (
        not live
        and ledger_toks
        and (
            tok_total is None
            or ledger_toks["in"] + ledger_toks["out"] > tok_total["in"] + tok_total["out"]
        )
    ):
        tok_total = dict(ledger_toks)  # calls outside a stage count too, once finished
    dl = st.get("delivered") or {}
    notes, studied = notes_view(root)
    return {
        "stages": strip,
        "label": label(st, v, rows) if live else None,
        "notes": notes,
        "studied": studied,
        "capabilities": v.get("capabilities") or [],
        "claims": v.get("claims"),
        "counts": counts,
        "defects": dfx,
        "settings": rows,
        "scope": scope_view(v, root, findings),
        "completeness": {"done": comp["done"], "total": comp["total"]} if comp else None,
        "incomplete": [d for d in dfx if d["class"] == "incomplete"],
        "complex": [i for i in interp if i.get("kind") != "binary"],
        "rounds": v.get("rounds") or [],
        "conflicts": v.get("conflicts") or [],
        "cost": {
            "by_stage": cost,
            "total": total,
            "vanilla": vanilla,
            "multiple": round(total / vanilla, 2) if vanilla else None,
            "limit": VANILLA_LIMIT,
            # tokens over the stages ({"in", "out"}), None when no token data exists
            "tokens": tok_total,
            # the build's global cap: a number, None (no cap), or "unknown" (an old log)
            "cap": st["cap_usd"] if "cap_usd" in st else "unknown",
        },
        "readiness": readiness_view(v, root),
        "report": bool(dl.get("report")) and Path(dl["report"]).exists(),
        "assumptions": (root / "exam" / "evidence" / "assumptions.md").exists()
        or (root / "deliver" / "ASSUMPTIONS.md").exists()
        or bool(dl),
        "security_md": (root / "exam" / "evidence" / "security.md").exists()
        or (root / "deliver" / "SECURITY.md").exists(),
        "readiness_md": (root / "exam" / "evidence" / "readiness.md").exists()
        or (root / "deliver" / "READINESS.md").exists(),
        "evidence": str(root / "exam" / "evidence")
        if (root / "exam" / "evidence").is_dir()
        else None,
        "branch": dl.get("branch"),
        "out": dl.get("out"),
    }
