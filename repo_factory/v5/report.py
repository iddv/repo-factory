"""``REPORT.md`` and the evidence's ``assumptions.md`` for a v5 build (docs/design/build-v5.md §3.8).

REPORT.md: what was checked (claims, defects found, fixed and open), the findings, the settings table and the other
decisions (Addendum A), the repair rounds and conflicts, and the cost by stage (with any
stage that hit its cap). ASSUMPTIONS.md is delivered in the repo: the settings (key,
values, default, scope, the spec question each settles), then every complex question with
both readings and the one the build implements."""

from __future__ import annotations

from .common import SIMPLE_STAGES, STAGES

TARGETS = {
    "scope": 0.40,
    "build": 3.70,
    "claims": 0.60,
    "verify": 2.00,
    "oracle": 0.30,
    "repair": 0.90,
    "reverify": 0.40,
    "readiness": 2.10,
    "deliver": 0.0,
    # the simple flow's steps (BF-DESIGN §4.7)
    "plan": 0.35,
    "check": 3.30,
    "fix": 2.20,
}


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


SETTING_STATUS = {
    "exposed": "already in the build",
    "added": "added",
    "open": "not working yet",
    "proposed": "proposed, not added",
}
EXISTS = ("added", "exposed")  # review 7: only these are in the delivered code


def existing(s: dict) -> list[dict]:
    return [it for it in s.get("settings") or [] if it.get("status") in EXISTS]


def missing(s: dict) -> list[dict]:
    return [it for it in s.get("settings") or [] if it.get("status") not in EXISTS]


def settings_table(s: dict, rows: list[dict] | None = None) -> list[str]:
    """Addendum A: the operator settings, one row each: key, values, default, scope, the
    spec question it settles, and where it stands."""
    rows = s.get("settings") or [] if rows is None else rows
    if not rows:
        return []
    L = ["| key | values | default | scope | settles | status |", "|---|---|---|---|---|---|"]
    for it in rows:
        st = it["setting"]
        L.append(
            f"| `{st['key']}` | {', '.join(st['values'])} | `{st['default']}` | "
            f"{st['scope']} | {_cell(it.get('question'))}"
            + (f' (spec: "{_cell(it["quote"])}")' if it.get("quote") else "")
            + " | "
            f"{SETTING_STATUS.get(it.get('status'), it.get('status'))}"
            + (f" ({_cell(it['why'])})" if it.get("why") and it.get("status") != "added" else "")
            + " |"
        )
    return L


def not_implemented(s: dict) -> list[str]:
    """The settings the factory proposed that are **not** in the delivered code (review 7:
    B's ASSUMPTIONS.md documented seven switches that didn't exist). No environment variable
    is named: there is none to set."""
    rows = missing(s)
    if not rows:
        return []
    L = [
        "### Not implemented",
        "",
        "Proposed as operator settings, but **not in the delivered code**: there is no switch "
        "to set. The build behaves one fixed way (`behaves as`, where known).",
        "",
        "| key | values | proposed default | behaves as | settles | why not |",
        "|---|---|---|---|---|---|",
    ]
    for it in rows:
        st = it["setting"]
        L.append(
            f"| `{st['key']}` | {', '.join(st['values'])} | `{st['default']}` | "
            f"{_cell(it.get('current') or '-')} | {_cell(it.get('question'))} | "
            f"{SETTING_STATUS.get(it.get('status'), it.get('status'))}"
            + (f": {_cell(it['why'])}" if it.get("why") else "")
            + " |"
        )
    return L + [""]


def complex_items(s: dict) -> list[dict]:
    return [i for i in s.get("interpretations") or [] if i.get("kind") != "binary"]


def render_report(s: dict) -> str:
    L = [f"# repo-factory build (v5): {s['task']}", ""]
    if s.get("title"):
        L += [f"What was asked: {s['title']}", ""]
    L += suite_warning(s)
    L += scope_section(s)
    c = s.get("checked") or {}
    if c:
        L += [
            "## What was checked",
            "",
            f"{c.get('claims')} claims from the spec: {c.get('found')} defects confirmed, "
            f"{c.get('fixed')} fixed, {c.get('open')} open; {c.get('blocked')} claim(s) "
            "could not be checked.",
        ]
        if c.get("least_trusted"):
            L += ["", "The verifier trusts these least: " + "; ".join(c["least_trusted"]) + "."]
        L.append("")
    defects = s.get("defects") or []
    fixed = [d for d in defects if d.get("status") == "fixed"]
    open_ = [d for d in defects if d.get("status") != "fixed"]
    by_vote = sum(1 for d in defects if d.get("via") == "interpretation vote")
    inc = sum(1 for d in defects if d.get("class") == "incomplete")
    L += [
        "## Findings",
        "",
        f"The verifier wrote {s.get('scenarios', {}).get('total', 0)} scenarios "
        f"({s.get('scenarios', {}).get('active', 0)} kept after two runs in opposite orders, "
        f"{s.get('scenarios', {}).get('held', 0)} held back from the repairer); "
        f"{len(defects) - by_vote - inc} failed and were confirmed as defects by the panel"
        + (
            f", {inc} showed something missing or partial (incomplete: sent to repair "
            "without the panel)"
            if inc
            else ""
        )
        + (f", {by_vote} more by the interpretation vote after a split panel" if by_vote else "")
        + f"; {len(s.get('retired') or [])} were retired by the panel.",
        "",
    ]

    def tag(d):
        return (
            " [incomplete" + (f": {', '.join(d['flows'])}" if d.get("flows") else "") + "]"
            if d.get("class") == "incomplete"
            else ""
        )

    if fixed:
        L += ["### Fixed", ""] + [f"- {d['id']}{tag(d)}: {d.get('title')}" for d in fixed] + [""]
    if open_:
        L += (
            ["### Open", ""]
            + [
                f"- {d['id']}{tag(d)}: {d.get('title')} ({d.get('status')})"
                + (f": {d['why']}" if d.get("why") else "")
                for d in open_
            ]
            + [""]
        )
    if s.get("environment"):
        L += [
            "### Environment (not repaired)",
            "",
            "These passed in the verifier's own run and fail only in the factory's sandbox, "
            "on the build and on the verifier's copy alike: a problem of the check's "
            "environment, not of the code. They are not sent to repair.",
            "",
        ]
        L += [f"- {r['id']}: {r.get('title')}" for r in s["environment"]] + [""]
    if s.get("retired"):
        L += ["### Retired by the panel (not required by the spec)", ""]
        L += [f"- {r['id']}: {r.get('title')}" for r in s["retired"]] + [""]
    L += suite_section(s)
    from .readiness import report_section

    L += report_section(s.get("readiness") or {})
    if s.get("settings"):
        have = existing(s)
        L += ["## Settings", ""]
        if have:
            L += (
                [
                    "Business rules the spec leaves open between two reasonable policies are "
                    "operator settings, defaulting to the expected reading (documented in the "
                    "README and `ASSUMPTIONS.md`). Each one below exists in the delivered code.",
                    "",
                ]
                + settings_table(s, have)
                + [""]
            )
        else:
            L += ["None of the proposed settings is in the delivered code.", ""]
        L += not_implemented(s)
    its = complex_items(s)
    if its:
        L += [
            "## Decisions",
            "",
            "Other open questions, decided by the implementation (all in `ASSUMPTIONS.md`).",
            "",
        ]
        for i in its:
            L.append(
                f"- {i['id']}: {i['question']} -> **{i.get('chosen') or 'as built'}** "
                f"({i.get('how') or i.get('mode')})"
            )
        L.append("")
    if s.get("rounds"):
        L += [
            "## Repair rounds",
            "",
            "| round | session | sent | fixed | regressions | "
            "conflicts | reverted | accepted | why |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in s["rounds"]:
            ses = r.get("session") or {}
            L.append(
                f"| {r['round']} | "
                + (f"{ses['mode']} ({_cell(ses.get('why'))})" if ses.get("mode") else "-")
                + f" | {len(r.get('sent') or [])} | {len(r.get('fixed') or [])} | "
                f"{len(r.get('regressions') or [])} | {len(r.get('pairs') or [])} | "
                f"{', '.join(x for v in r.get('reverted') or [] for x in v['findings']) or '-'} | "
                f"{'yes' if r.get('accepted') else 'no'} | {_cell(r.get('why'))} |"
            )
        for r in s["rounds"]:
            for p in r.get("pairs") or []:
                L.append(
                    f"\n- Round {r['round']} conflict: {', '.join(p['x'])} (rank "
                    f"{p['x_rank']}) vs `{p['y']['id']}` (rank {p['y_rank']}): panel "
                    f"`{p['verdict']}`, action `{p['action']}`."
                )
        L.append("")
    if s.get("stop"):
        L += [f"Stopped: {s['stop']}.", ""]
    if s.get("partial"):
        L += [
            f"**A partial build:** {s['partial']['why']}. The stages after it verified and "
            "repaired that tree as it was.",
            "",
        ]
    if s.get("continues"):
        L += [
            f"This round continued round {s['continues'][1:]}'s stopped build (its scope and "
            "its committed tree).",
            "",
        ]
    costs = s.get("costs") or {}
    L += ["## Cost by stage", "", "| stage | USD | target | cap |", "|---|---|---|---|"]
    caps = s.get("stage_caps") or {}
    order = [k for k in (*STAGES, *SIMPLE_STAGES) if k in costs] or list(STAGES)
    for k in order:
        cap = f"{float(caps[k]):.2f}" if caps.get(k) is not None else "none"
        L.append(
            f"| {k} | {float(costs.get(k) or 0):.4f} | {TARGETS[k]:.2f} | "
            f"{cap}{' (hit)' if k in (s.get('caps_hit') or []) else ''} |"
        )
    L += [
        f"| **total** | **{sum(float(v or 0) for v in costs.values()):.4f}** | "
        f"{sum(TARGETS[k] for k in order):.2f} | "
        + (f"{float(s['cap']):.2f} |" if s.get("cap") is not None else "no cap |"),
        "",
    ]
    if s.get("prebuilt"):
        L += ["The build stage is a shared base build (its cost is the base's).", ""]
    L += [
        "## Delivery",
        "",
        f"Branch `{s['branch']}`: the product as the build wrote it, "
        "nothing of repo-factory's in it. The exam (the verifier's scenarios and the readiness "
        "probes), its results and the reports are the round's evidence, `exam/evidence/` in "
        "the den, published with the release as its checks asset: `assumptions.md`"
        + (", `security.md` and `readiness.md`" if s.get("readiness") else "")
        + "."
        + (
            " The product scope the build worked from is `SCOPE.md`."
            if (s.get("scope") or {}).get("decision") == "scoped"
            else ""
        ),
        "",
    ]
    if s.get("jargon"):
        L += (
            [
                "Build-process words in the product's own prose (the provenance scan; not "
                "rewritten automatically):",
                "",
            ]
            + [
                f"- `{h['file']}:{h['line']}` ({h['kind']}): {_cell(h['text'])}"
                for h in s["jargon"][:15]
            ]
            + [""]
        )
    return "\n".join(L)


def suite_warning(s: dict) -> list[str]:
    """At the top of REPORT.md: the build's own tests fail on the delivered tree."""
    own = (s.get("suite") or {}).get("own") or {}
    if not own.get("failed"):
        return []
    L = [
        f"> **Warning: the build's own test suite fails: {len(own['failed'])} of "
        f"{own.get('total')} tests** (on the delivered tree, network off):",
        ">",
    ]
    L += [f"> - `{t}`" for t in own["failed"][:20]]
    if len(own["failed"]) > 20:
        L.append(f"> - ... and {len(own['failed']) - 20} more")
    return L + [""]


def scope_section(s: dict) -> list[str]:
    """The scope stage: what the input was taken for, and the flows that work end to end."""
    sc = s.get("scope") or {}
    if not sc:
        return []
    comp = s.get("completeness") or {}
    if sc.get("decision") != "scoped":
        L = ["## Scope", "", f"The input was used as the spec, unchanged ({sc.get('why')}).", ""]
        if comp:
            L[-1:] = [f"Its definition of done is the spec's own {comp['total']} requirements.", ""]
            L += completeness_table(comp)
        return L
    L = [
        "## Scope and completeness",
        "",
        "The input was an idea, so the factory first wrote a v1 product scope, `SCOPE.md` "
        f"(in the repository): {len(sc.get('flows') or [])} core flows, "
        f"{len(sc.get('must') or [])} must-have features, "
        f"{len(sc.get('settings') or [])} operator settings. The build, the verifier and the "
        "repair worked from the idea plus `SCOPE.md`; this build does not share the vanilla "
        "base build.",
        "",
    ]
    if comp:
        L += completeness_table(comp)
    return L


def completeness_table(comp: dict) -> list[str]:
    L = [
        f"**Completeness: {comp['done']} of {comp['total']} flows** of the definition of "
        "done work end to end through the real interface (the harness's last run of each "
        "flow's scenarios).",
        "",
        "| flow | | status | verifier said |",
        "|---|---|---|---|",
    ]
    for f in comp.get("flows") or []:
        L.append(
            f"| {f['id']} | {_cell(f.get('title'))} | {f['status']} | {f.get('verifier') or '-'} |"
        )
    return L + [""]


def suite_section(s: dict) -> list[str]:
    """The delivered tree's exam record: the build's own tests, its README's test commands,
    and repo-factory's scenarios still open (open items in the evidence; nothing is marked in the
    tree)."""
    su = s.get("suite") or {}
    opens = s.get("open_scenarios") or []
    if not su and not opens:
        return []
    L = ["## Tests at delivery", ""]
    own, ex, rd = su.get("own") or {}, su.get("exam") or {}, su.get("readme") or {}
    if su.get("ran"):
        L.append(
            f"- The build's own tests: {own.get('passed', 0)} of {own.get('total', 0)} pass"
            + (
                f", **{len(own.get('failed') or [])} fail** (listed at the top)"
                if own.get("failed")
                else ""
            )
            + "."
        )
        cmds = rd.get("commands") or []
        if cmds:
            L.append(
                "- The README's test commands, as written: "
                + "; ".join(f"`{c['cmd']}` {c.get('verdict') or c.get('status')}" for c in cmds)
                + "."
            )
        L.append(
            f"- repo-factory's scenarios (outside the tree, in the evidence): "
            f"{ex.get('passed', 0)} pass, {len(opens)} open"
            + (f", {len(ex['intermittent'])} intermittent" if ex.get("intermittent") else "")
            + (f", {len(ex['flaky'])} retired as flaky" if ex.get("flaky") else "")
            + f" (exam record `{su.get('key')}`)."
        )
        if su.get("reused"):
            L.append(f"- {su['reused']}.")
    elif su:
        L.append(f"- The delivered tree was not run ({su.get('why') or 'unknown'}).")
    if opens:
        L += ["", "Open (they fail on the delivered tree; each passes once it is fixed):", ""]
        L += [f"- {o['id']} ({o['class']}): {o.get('title')} (`{o.get('file')}`)" for o in opens]
    return L + [""]


def plain_table(rows: list[dict], proposed: bool) -> list[str]:
    """The settings for ASSUMPTIONS.md, in an operator's words (no ids or statuses)."""
    if proposed:
        L = ["| key | values | proposed default | settles |", "|---|---|---|---|"]
    else:
        L = ["| key | values | default | scope | settles |", "|---|---|---|---|---|"]
    for it in rows:
        st = it["setting"]
        q = _cell(it.get("question")) + (
            f' (spec: "{_cell(it["quote"])}")' if it.get("quote") else ""
        )
        L.append(
            f"| `{st['key']}` | {', '.join(st['values'])} | `{st['default']}` | "
            + ("" if proposed else f"{st['scope']} | ")
            + f"{q} |"
        )
    return L


def chosen_text(i: dict) -> str:
    """What this version does about a complex question, in plain words."""
    c = str(i.get("chosen") or "").strip()
    if c == "literal" and i.get("literal"):
        return str(i["literal"])
    if c == "expected" and i.get("expected"):
        return str(i["expected"])
    if c in ("", "as built", "unknown", "either", "neither", "literal", "expected") or c.startswith(
        "the earlier behaviour"
    ):
        return "it keeps the behaviour it was built with"
    return c


def render_assumptions(s: dict) -> str:
    """ASSUMPTIONS.md: the open questions and what this version does, for an operator (no
    process words: no ids, votes or readings "as first written")."""
    scoped = (s.get("scope") or {}).get("decision") == "scoped"
    L = [
        "# Assumptions",
        "",
        ("The idea this product was built from" if scoped else "The specification")
        + " leaves these questions open. Each section says what this version does.",
        "",
    ]
    if scoped:
        L += [
            "The rules and numbers the idea left open are set as defaults in the product "
            "scope, `SCOPE.md` (each marked *(default)*).",
            "",
        ]
    sets = existing(s)
    if s.get("settings"):
        L += ["## Settings", ""]
    if sets:
        from .settings import env_name

        L += (
            [
                "Where reasonable operators differ between two business policies, the operator "
                "chooses. Each setting below exists in this version; the README's configuration "
                "section says how to change it, and the environment variable named below "
                "overrides it everywhere.",
                "",
            ]
            + plain_table(sets, False)
            + [""]
        )
        for it in sets:
            st = it["setting"]
            means = st.get("means") or {}
            L.append(
                f"- `{st['key']}` (`{env_name(st['key'])}`): "
                + "; ".join(
                    f"`{v}`" + (f": {means[v]}" if means.get(v) else "") for v in st["values"]
                )
                + "."
            )
        L.append("")
    gone = missing(s)
    if gone:
        L += (
            [
                "### Not available yet",
                "",
                "These were planned as settings but are **not in this version**: there is no "
                "switch to set, and the product behaves one fixed way.",
                "",
            ]
            + plain_table(gone, True)
            + [""]
        )
    its = complex_items(s)
    L += [
        "## Decisions",
        "",
        "Questions that aren't a simple switch. For each: the two ways to read it, and what "
        "this version does.",
        "",
    ]
    if not its:
        L.append("None: no other open question was found that changes the behaviour.")
    for i in its:
        L += [
            f"### {_cell(i['question'])}",
            "",
            f"- One reading: {_cell(i.get('literal')) or '-'}",
            f"- The other: {_cell(i.get('expected')) or '-'}",
            f"- **This version: {_cell(chosen_text(i))}.**",
            "",
        ]
    return "\n".join(L)
