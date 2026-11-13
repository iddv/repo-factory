"""REPORT.md's rule-set section, the delivered code's rule pass status, and the learning
record hibernate reads (which mining strategies found real bugs)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .score import summarize


def delivered_status(eng, code_dir: Path, digest: str | None) -> dict:
    """The delivered code on every active rule, visible and held: ``{"visible": [p, t],
    "held": [p, t], "results": {id: outcome}}``."""
    rules = eng.store.active()
    res = eng.runner(code_dir, digest)(rules) if rules else {}
    out = {"results": res}
    for vis in ("visible", "held"):
        s = summarize(res, [r for r in rules if r.visibility == vis])
        out[vis] = [s["passed"], s["total"]]
    return out


def _cell(text: str, n: int = 90) -> str:
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= n else text[: n - 1] + "…"


def render(eng, status: dict | None, rounds: list[dict] | None = None) -> str:
    st = eng.store
    rules = list(st.rules.values())
    if not rules:
        return "\n## Rule set\n\nNo rules were mined.\n"
    summ = st.summary()
    res = (status or {}).get("results") or {}
    act = [r for r in rules if r.status == "active"]
    L = [
        "",
        "## Rule set",
        "",
        f"The factory's own executable rules (docs/design/rules.md): {len(rules)} proposed, "
        f"{summ['active']} active ({summ['held']} held back from workers), "
        f"{summ['retired']} retired, {summ['disputed']} disputed, {summ['candidate']} "
        f"never validated. The arbiter blamed the code for {summ['caught']} rule(s); "
        f"{sum(1 for r in rules if r.fixed)} of those were fixed later.",
    ]
    if status:
        v, h = status["visible"], status["held"]
        L.append(f"Delivered code: visible rules {v[0]}/{v[1]}, held-back rules {h[0]}/{h[1]}.")
    by_mode = defaultdict(lambda: [0, 0, 0])
    for r in rules:
        m = by_mode[(r.origin or {}).get("mode", "?")]
        m[0] += 1
        m[1] += r.status == "active"
        m[2] += bool(r.caught)
    L += ["", "| mining mode | proposed | active | caught a code bug |", "|---|---|---|---|"]
    L += [f"| {k} | {a} | {b} | {c} |" for k, (a, b, c) in sorted(by_mode.items())]
    if rounds:
        L += [
            "",
            "Closing review loop:",
            "",
            "| round | rules | passing | blamed on code | new | fixed | stop |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rounds:
            L.append(
                f"| {r.get('round')} | {r.get('rules')} | {r.get('passed')} | "
                f"{r.get('code_wrong')} | {r.get('new')} | "
                f"{'yes' if r.get('fixed') else ('-' if r.get('fixed') is None else 'no')}"
                f" | {r.get('stop') or ''} |"
            )
    if act:
        L += [
            "",
            "### Active rules",
            "",
            "| id | kind | milestones | shown | delivered | rule | source |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in sorted(act, key=lambda r: r.id):
            out = (res.get(r.id) or {}).get("status", "-")
            mark = "pass" if out == "passed" else f"**{out}**"
            L.append(
                f"| {r.id} | {r.kind} | {','.join(r.milestones) or '-'} | "
                f"{'yes' if r.visibility == 'visible' else 'held'} | {mark} | "
                f"{_cell(r.title, 70)} | {_cell(r.source, 90)} |"
            )
    promoted = [h for h in st.history() if h.get("to_visibility") == "visible"]
    if promoted:
        L += [
            "",
            "### Held rules promoted to visible",
            "",
            "These held-back rules caught a real bug (the arbiter blamed the code), so they "
            "were shown to the fix loop; a held replacement was mined in the same area.",
            "",
        ]
        repl = {
            (r.origin or {}).get("replaces"): r.id
            for r in rules
            if (r.origin or {}).get("replaces")
        }
        L += [
            f"- {h['id']}: {_cell(h.get('reason'), 200)}"
            + (f" (replacement: {repl[h['id']]})" if h["id"] in repl else "")
            for h in promoted
        ]
    gone = [r for r in rules if r.status in ("retired", "disputed")]
    if gone:
        L += ["", "### Retired and disputed rules", ""]
        L += [
            f"- {r.id} ({r.status}) {_cell(r.title, 70)}: {_cell(r.reason, 200)}"
            for r in sorted(gone, key=lambda r: r.id)
        ]
    if st.questions:
        L += ["", "### Questions the rules raised for the owner", ""]
        L += [f"- ({q['rule']}) {_cell(q['text'], 300)}" for q in st.questions]
    return "\n".join(L) + "\n"


def learning(cache_dir: Path) -> dict:
    """Per mining mode and prompt version over every rule store in a den's cache: rules
    proposed, activated, and those that caught a code bug (and saw it fixed). Hibernate
    reports it; a later policy can learn which mining strategies find real bugs."""
    out: dict = {}
    for p in sorted(Path(cache_dir).glob("rules/*/*/rules.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for d in data:
            o = d.get("origin") or {}
            k = f"{o.get('mode', '?')}@{o.get('prompt', '?')}"
            b = out.setdefault(k, {"proposed": 0, "active": 0, "caught": 0, "fixed": 0})
            b["proposed"] += 1
            b["active"] += d.get("status") == "active"
            b["caught"] += bool(d.get("caught"))
            b["fixed"] += bool(d.get("fixed"))
    return dict(sorted(out.items()))
