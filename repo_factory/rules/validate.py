"""Validation: what a proposed rule must pass before it becomes ``active``.

(a) **Traceable**: its ``source`` quotes the spec or an answer (every quoted fragment is
    found, punctuation- and case-insensitively).
(b) **Independent agreement**: a second model call, which did not write the rule, reads
    the spec and decides on its own whether the outcome the test asserts is required;
    only ``agree`` survives (``disagree`` and ``ambiguous`` are rejected with the reason).
(c) **Deterministic**: two runs on the same code, in opposite file orders, give the same
    outcome.
(d) **Collectable against the interface**: it parses, has a test with an assertion, and
    on the code it neither breaks in its own file (scoring v2's ``broken_reason``:
    NameError/SyntaxError in the rule, missing fixture, ImportError of an installed name)
    nor imports a module or name the interface doesn't have.

A rule that *fails* on the current code is not rejected: that is what rules are for. Its
failure goes to the arbiter (``arbiter.py``). (c) and (d) need code; without it a rule
that passed (a) and (b) stays ``candidate`` until ``validate_runs`` sees code."""

from __future__ import annotations

import ast
import json
import re
import unicodedata

from ..llm.json import extract_json
from .llm import tag
from .miner import Context
from .model import Rule

MIN_FRAGMENT = 8


def _or_dash(v) -> object:
    return "-" if v is None else v


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^\w€$£%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quotes(source: str) -> list[str]:
    """The quoted fragments of a source citation (the whole text if nothing is quoted),
    split at ellipses and ``|``."""
    q = re.findall(r'"([^"]+)"|“([^”]+)”', source or "")
    parts = [a or b for a, b in q] or [source or ""]
    out = []
    for p in parts:
        out += [f for f in re.split(r"\.\.\.|…|\s\|\s", p) if len(norm(f)) >= MIN_FRAGMENT]
    return out


def traceable(rule: Rule, ctx: Context) -> tuple[bool, str]:
    """(a): (ok, where-or-why)."""
    frags = quotes(rule.source)
    if not frags:
        return False, "no source quoted"
    hay = {"spec": norm(ctx.spec)}
    hay.update({f"answer:{q}": norm(a) for q, a in ctx.answers.items() if a})
    where = []
    for f in frags:
        hit = next((k for k, v in hay.items() if norm(f) in v), None)
        if hit is None:
            return False, f"not found in the spec or answers: {f.strip()[:120]!r}"
        where.append(hit)
    return True, ",".join(dict.fromkeys(where))


def structural(rule: Rule) -> str | None:
    """Static part of (d): None if fine, else why not."""
    try:
        tree = ast.parse(rule.code)
    except SyntaxError as e:
        return f"syntax error: {e.msg} (line {e.lineno})"
    tests = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
    ]
    if not tests:
        return "no test function"
    for t in tests:
        src = ast.unparse(t)
        if not any(isinstance(n, ast.Assert) for n in ast.walk(t)) and "raises" not in src:
            return f"{t.name} asserts nothing"
    text = rule.code
    if re.search(r"\bspec\.md\b", text):
        return "reads spec.md"
    if re.search(
        r"^\s*(import|from)\s+(socket|requests|urllib\.request|http\.client)\b", text, re.M
    ):
        return "uses the network"
    return None


def interface_modules(interface: str) -> set[str]:
    """Top-level module names the interface mentions (``library/services/x.py`` and
    dotted forms) plus every identifier in it, for the import check."""
    mods = set()
    for m in re.findall(r"([A-Za-z_][\w/\.]*)\.py\b", interface or ""):
        mods.add(m.replace("/", ".").split(".")[0])
    mods |= {w.lower() for w in re.findall(r"[A-Za-z_]\w+", interface or "")}
    return mods


def import_reason(detail: str, interface: str) -> str | None:
    """An import failure that is the rule's fault: a module or name the interface (and the
    environment) doesn't have."""
    if not interface:
        return None
    names = interface_modules(interface)
    m = re.search(r"No module named '([\w\.]+)'", detail or "")
    if m and m.group(1).split(".")[0].lower() not in names:
        return f"imports {m.group(1)!r}, which is neither in the interface nor installed"
    m = re.search(r"cannot import name '(\w+)' from '([\w\.]+)'", detail or "")
    if m and m.group(1) not in (interface or ""):
        return f"imports {m.group(1)!r} from {m.group(2)!r}, not in the interface"
    return None


CHECK_PROMPT = """{tag}
You check proposed acceptance rules for software, independently of whoever wrote them.
Each rule is a pytest scenario citing the part of the spec it traces to. For each rule,
first read the spec and the owner's answers and decide yourself what a correct
implementation must do in the situation the test sets up; then compare that with what the
test asserts.

- "agree": the spec (or an answer) clearly requires every outcome the test asserts.
- "disagree": the spec requires something else, or the test asserts something the spec
  doesn't require (an invented detail, a stricter or looser rule, a wrong number).
- "ambiguous": the spec doesn't settle the outcome; reasonable readings differ.

Time rules: where the spec defines behaviour in terms of time ("expires after 3 days",
"for 1 day"), a correct implementation behaves that way whenever an operation happens after
that time, whether or not some maintenance function was called first; how it achieves that
(a background job, checking on access) is an implementation detail, not an ambiguity.

Judge the expected behaviour only; don't judge style, and assume the public interface
below is the one the code implements.

Request (spec.md):
{spec}

Answers from the owner:
{answers}

Public interface:
{interface}

Rules:
{rules}

Return JSON only: {{"results": {{"<rule id>": {{"verdict": "agree", "reason": "one sentence"}}, ...}}}}"""


# Build v4 hardening (V4-D): the second reading of the design's known-critical rules
# (``RulesEngine.mine_design``). Their interface came from a design that left exactly these
# rules uncovered, so it mustn't settle what behaviour the spec requires.
DESIGN_CHECK_SUFFIX = """

These rules check behaviour that the design documents behind the public interface were
found not to cover, so the interface may be incomplete exactly where they apply. It fixes
names and signatures, not which behaviour is required: judge what the spec and the answers
require. An interface whose error list or parameters leave out something the spec requires
doesn't make the rule ambiguous; a limit or block the spec places on an action applies to
every operation that performs that action. Return JSON only, in the form above."""


def rules_md(rules: list[Rule], with_code: bool = True) -> str:
    out = []
    for r in rules:
        s = f"### rule {r.id}: {r.title}\nSource: {r.source}\nExpect: {r.expect}"
        if with_code:
            s += f"\n```python\n{r.code.rstrip()}\n```"
        out.append(s)
    return "\n\n".join(out)


def agreement(llm, rules: list[Rule], ctx: Context, note: str = "") -> dict[str, dict]:
    """(b): rule id -> {"verdict": agree|disagree|ambiguous, "reason"}. One batched call.
    A missing or unparsable answer counts as ``ambiguous`` (not agreed). ``note``: text
    appended to the prompt (``DESIGN_CHECK_SUFFIX`` for the design's rules)."""
    if not rules:
        return {}
    text = llm(
        CHECK_PROMPT.format(
            tag=tag("check"),
            spec=ctx.spec,
            answers=ctx.answers_md(),
            interface=ctx.interface or "(none)",
            rules=rules_md(rules),
        )
        + note
    )
    try:
        got = extract_json(text).get("results", {})
    except (ValueError, json.JSONDecodeError, AttributeError):
        got = {}
    out = {}
    for r in rules:
        v = got.get(r.id) if isinstance(got, dict) else None
        verdict = (v or {}).get("verdict") if isinstance(v, dict) else v
        verdict = verdict if verdict in ("agree", "disagree", "ambiguous") else "ambiguous"
        reason = (v or {}).get("reason", "") if isinstance(v, dict) else ""
        out[r.id] = {"verdict": verdict, "reason": str(reason)[:300] or "(no reason given)"}
    return out


def validate(
    store,
    rules: list[Rule],
    ctx: Context,
    llm,
    run=None,
    by: str = "validate",
    agree: bool = True,
    root=None,
    note: str = "",
) -> dict:
    """Run (a)-(d) on ``rules`` (already in ``store`` as candidates). ``run(rules,
    reverse) -> outcomes`` runs rules on the kept candidate's code (``score.run_rules``);
    None leaves (c)/(d) for later. ``agree=False`` skips (b) (repairs: the arbiter that
    wrote the repair already read the spec). ``note``: appended to (b)'s prompt.

    Returns {"active": [ids], "rejected": {id: reason}, "pending": [ids],
    "results": {id: outcome on the code}}."""
    rejected, todo = {}, []
    for r in rules:
        ok, where = traceable(r, ctx)
        r.checks["a"] = where if ok else False
        if ok:
            r.source_ref = where
        why = None if ok else f"(a) untraceable: {where}"
        if why is None and (s := structural(r)):
            r.checks["d"] = False
            why = f"(d) {s}"
        if why:
            rejected[r.id] = why
            store.set_status(r.id, "retired", why, by)
        else:
            todo.append(r)
    if agree and todo:
        verdicts = agreement(llm, todo, ctx, note)
        keep = []
        for r in todo:
            v = verdicts[r.id]
            r.checks["b"] = v["verdict"]
            if v["verdict"] == "agree":
                keep.append(r)
            else:
                why = f"(b) second reading: {v['verdict']}: {v['reason']}"
                rejected[r.id] = why
                store.set_status(r.id, "retired", why, by)
        todo = keep
    store.save()
    out = {"active": [], "rejected": rejected, "pending": [r.id for r in todo], "results": {}}
    if run is not None and todo:
        res = validate_runs(store, todo, ctx, run, by, root)
        out["active"], out["pending"], out["results"] = res["active"], [], res["results"]
        out["rejected"].update(res["rejected"])
    return out


def validate_runs(
    store, rules: list[Rule], ctx: Context, run, by: str = "validate", root=None
) -> dict:
    """(c) and (d) on code: two runs in opposite orders. Passing rules become active.
    ``root``: the code's dir; a third-party module the code itself uses but which is
    missing is the code's fault (``checklist.missing_dependency``), not the rule's."""
    from .tree import missing_dependency

    first = run(rules, False)
    second = run(rules, True)
    active, rejected = [], {}
    for r in rules:
        a, b = first.get(r.id) or {}, second.get(r.id) or {}
        detail = a.get("detail", "")
        code_fault = root is not None and detail and missing_dependency(detail, r.test_file, root)
        broken = (
            a.get("broken")
            or b.get("broken")
            or (None if code_fault else import_reason(detail, ctx.interface))
        )
        if broken:
            r.checks["d"] = False
            why = f"(d) broken against the interface: {broken}"
        elif a.get("status") != b.get("status"):
            r.checks["c"] = False
            why = f"(c) flaky: {a.get('status')} then {b.get('status')}"
        else:
            r.checks["c"] = r.checks["d"] = True
            active.append(r.id)
            store.set_status(
                r.id,
                "active",
                f"validated (a: {r.checks.get('a')}; b: {_or_dash(r.checks.get('b'))}; "
                f"{a.get('status')} on the current code)",
                by,
            )
            continue
        rejected[r.id] = why
        store.set_status(r.id, "retired", why, by)
    store.save()
    return {
        "active": active,
        "rejected": rejected,
        "results": {i: first[i] for i in active if i in first},
    }
