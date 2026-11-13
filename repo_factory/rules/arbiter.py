"""Arbitration: who is wrong when an active rule fails, the code or the rule?

This replaces scoring v2's "drop a generated test that fails on every candidate" for
rules: a rule that every candidate fails may be exactly the one that catches a shared,
systematic bug. One batched model call reads the spec, the answers, the interface, each
failing rule with its failure output, and the code, and decides per rule:

- ``code_wrong``: the rule stays active; the failure is queued for a fix.
- ``rule_wrong``: the rule is repaired (the call may return a corrected module, which is
  revalidated) or retired with the reason.
- ``ambiguous``: the spec doesn't settle it. The rule becomes ``disputed`` (not scored)
  and the ambiguity is recorded as a question for the owner.

Verdicts are recorded on the rule (``Rule.verdicts``, ``caught``) and in the store's
history. A rule is arbitrated once per version: a later failure of a rule already judged
``code_wrong`` reuses that verdict.

Build v3 §4 (``controller.design_flaw``, only with the controller on): a fourth verdict,

- ``design_flaw``: the spec requires what the rule asserts, but no local fix is consistent
  with the spec under the current interface, state model or ADR. The verdict names the
  decision at fault ("checkout has no branch parameter, so transit can't be enforced").
  It is validated conservatively (``validate_flaw``): it must quote a spec (or answer) rule
  that is really there and name an element that really is in the interface or the ADR;
  otherwise it becomes ``code_wrong``. The rule stays active (it is right); the controller
  reads the open flaws (``open_flaws``) and may backtrack. A backtrack supersedes them."""

from __future__ import annotations

import json
import re

from ..llm.json import extract_json
from .llm import tag
from .miner import MAX_CODE_CHARS, Context
from .model import DESIGN_FLAW, VERDICTS, Rule
from .validate import norm, quotes, rules_md

MAX_REPAIRS = 2

ARBITER_PROMPT = """{tag}
You arbitrate failing acceptance rules. Each rule below is a pytest scenario traced to the
spec; it fails on the current code. Decide for each one who is wrong, from the spec and the
owner's answers (they are the authority, not the code and not the rule):

- "code_wrong": the spec requires what the rule asserts, and the code doesn't do it.
- "rule_wrong": the rule asserts something the spec doesn't require, sets up state in a
  way the interface doesn't support, misuses the interface (wrong argument order, a name
  the interface doesn't have), or has a bug of its own. If the rule's intent is right and
  only its mechanics are wrong, return a corrected module as a ```python block that starts
  with the line `# repair: <rule id>` (same single test function, same intent).
- "ambiguous": the spec doesn't settle the outcome; reasonable readings differ. Give the
  question you would ask the owner to settle it.

Time rules: where the spec defines behaviour in terms of time ("expires after 3 days",
"for 1 day"), a correct implementation behaves that way whenever an operation happens after
that time, whether or not some maintenance function was called first; how it achieves that
(a background job, checking on access) is an implementation detail, not an ambiguity.

Request (spec.md):
{spec}

Answers from the owner:
{answers}

Public interface:
{interface}

Failing rules:
{rules}

Current code:
{code}

Return a JSON object first, then any repair blocks:
{{"results": {{"<rule id>": {{"verdict": "code_wrong|rule_wrong|ambiguous", "reason": "one or two sentences", "question": "only for ambiguous"}}, ...}}}}"""

DESIGN_FLAW_SUFFIX = """

One more verdict is available, because a rule can fail for a reason neither the code nor the
rule can fix: the design. The factory can go back and change a design decision, at the cost
of rebuilding the work that followed it, so this verdict sends the build to its controller
instead of to a fix run.

<adr>
{adr}
</adr>

- "design_flaw": the spec requires what the rule asserts, but no local fix to the code is
  consistent with the spec under the current public interface, state model or architecture
  decision (the ADR above); the decision itself must change first. Example: "checkout has no
  branch parameter, so the in-transit rule can't be enforced". Give it only with both a quote
  of the spec rule (from the spec or the answers, word for word) and the interface or ADR
  element at fault, written exactly as it appears there; without both, the verdict is
  "code_wrong". Its entry also has:
  "spec_rule": "the quote", "element": "the interface operation, parameter, field or ADR
  decision at fault", "decision": "what is wrong with that decision and why it blocks the
  rule", "fix": "the design change, in one sentence"."""

_REPAIR = re.compile(r"```(?:python|py)?[ \t]*\n#\s*repair:\s*(\S+)[^\n]*\n(.*?)```", re.S)


def failing_md(rules: list[Rule], results: dict[str, dict], tail: int = 1200) -> str:
    out = []
    for r in rules:
        d = (results.get(r.id) or {}).get("detail", "").strip()
        out.append(rules_md([r]) + f"\nFailure output (tail):\n```\n{d[-tail:]}\n```")
    return "\n\n".join(out)


def arbitrate(
    llm,
    rules: list[Rule],
    results: dict[str, dict],
    ctx: Context,
    design_flaw: bool = False,
    adr: str | None = None,
) -> dict:
    """Rule id -> {"verdict", "reason", "question"?, "repair"?}. One call for all.
    ``design_flaw`` (build v3 §4): offer the fourth verdict, validated by ``validate_flaw``
    against the interface and ``adr``; an entry that fails validation is ``code_wrong``."""
    if not rules:
        return {}
    code = ctx.code or "(not available)"
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n... (truncated)"
    prompt = ARBITER_PROMPT.format(
        tag=tag("arbitrate"),
        spec=ctx.spec,
        answers=ctx.answers_md(),
        interface=ctx.interface or "(none)",
        rules=failing_md(rules, results),
        code=code,
    )
    if design_flaw:
        prompt += DESIGN_FLAW_SUFFIX.format(adr=(adr or "(none)").strip())
    text = llm(prompt)
    head = _REPAIR.split(text)[0] if _REPAIR.search(text) else text
    try:
        got = extract_json(head).get("results", {})
    except (ValueError, json.JSONDecodeError, AttributeError):
        try:
            got = extract_json(text).get("results", {})
        except (ValueError, json.JSONDecodeError, AttributeError):
            got = {}
    repairs = {rid: code.strip() + "\n" for rid, code in _REPAIR.findall(text)}
    out = {}
    allowed = VERDICTS + ((DESIGN_FLAW,) if design_flaw else ())
    for r in rules:
        v = got.get(r.id) if isinstance(got, dict) else None
        v = v if isinstance(v, dict) else {"verdict": v}
        verdict = v.get("verdict") if v.get("verdict") in allowed else None
        rec = {
            "verdict": verdict or "unjudged",
            "reason": str(v.get("reason") or "")[:400] or "(no reason given)",
        }
        if verdict == "ambiguous":
            rec["question"] = str(v.get("question") or v.get("reason") or "")[:400]
        if verdict == "rule_wrong" and r.id in repairs:
            rec["repair"] = repairs[r.id]
        if verdict == DESIGN_FLAW:
            flaw = {
                k: " ".join(str(v.get(k) or "").split())[:400]
                for k in ("spec_rule", "element", "decision", "fix")
            }
            ok, why = validate_flaw(flaw, ctx, adr)
            if ok:
                rec.update(flaw)
                rec["decision"] = rec["decision"] or rec["reason"]
            else:
                # Conservative: a design flaw we can't verify is treated as a code bug.
                rec = {
                    "verdict": "code_wrong",
                    "reason": f"{rec['reason']} (design_flaw not accepted: {why})"[:500],
                }
        out[r.id] = rec
    return out


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
ELEMENT_STOP = {
    "the",
    "and",
    "has",
    "have",
    "with",
    "without",
    "parameter",
    "param",
    "field",
    "argument",
    "function",
    "method",
    "operation",
    "endpoint",
    "str",
    "int",
    "float",
    "bool",
    "dict",
    "list",
    "none",
    "self",
    "adr",
    "interface",
    "decision",
}


def element_names(element: str) -> list[str]:
    """The identifiers an element names, most specific first (``checkout(member_id,
    copy_id)`` -> ``checkout``, ``member_id``, ``copy_id``); generic words dropped."""
    return [w for w in dict.fromkeys(IDENT.findall(element or "")) if w.lower() not in ELEMENT_STOP]


def validate_flaw(flaw: dict, ctx: Context, adr: str | None = None) -> tuple[bool, str]:
    """A ``design_flaw`` entry must (1) quote a spec rule found word for word (punctuation
    and case aside) in the spec or the owner's answers, and (2) name an element whose main
    identifier appears in the public interface or the ADR. Returns (ok, why not)."""
    quote, element = flaw.get("spec_rule") or "", flaw.get("element") or ""
    if not quote.strip():
        return False, "no spec rule quoted"
    if not element.strip():
        return False, "no interface or ADR element named"
    hay = norm(" ".join([ctx.spec or "", *[str(a) for a in (ctx.answers or {}).values() if a]]))
    frags = quotes(quote)
    if not frags or any(norm(f) not in hay for f in frags):
        return False, f"the quoted rule is not in the spec or the answers: {quote[:120]!r}"
    names = element_names(element)
    if not names:
        return False, f"the element names nothing: {element[:120]!r}"
    where = {w.lower() for w in IDENT.findall((ctx.interface or "") + "\n" + (adr or ""))}
    if names[0].lower() not in where:
        return False, f"{names[0]!r} is not in the interface or the ADR"
    return True, ""


def judged(rule: Rule) -> dict | None:
    """The verdict already given for the rule's current version, if any (a design flaw a
    backtrack superseded no longer counts: the rule is judged afresh on the new design)."""
    return next(
        (
            v
            for v in reversed(rule.verdicts)
            if v.get("version") == rule.version and not v.get("superseded")
        ),
        None,
    )


def open_flaws(store) -> list[dict]:
    """Active rules whose standing verdict is a ``design_flaw`` not yet superseded by a
    backtrack: ``{"rule", "title", "milestones", "spec_rule", "element", "decision", "fix",
    "reason"}`` each, in rule-id order."""
    out = []
    with store.lock:
        rules = sorted(store.rules.values(), key=lambda r: r.id)
    for r in rules:
        j = judged(r)
        if r.status == "active" and j and j.get("verdict") == DESIGN_FLAW:
            out.append(
                {
                    "rule": r.id,
                    "title": r.title,
                    "milestones": list(r.milestones),
                    **{
                        k: j.get(k, "")
                        for k in ("spec_rule", "element", "decision", "fix", "reason")
                    },
                }
            )
    return out


def supersede_flaws(store, rule_ids, reason: str) -> list[str]:
    """Mark the rules' standing ``design_flaw`` verdicts as superseded (a backtrack changed
    the design they blame); the rules are arbitrated afresh if they fail again."""
    done = []
    with store.lock:
        for rid in rule_ids:
            r = store.rules.get(rid)
            j = judged(r) if r is not None else None
            if j and j.get("verdict") == DESIGN_FLAW:
                j["superseded"] = reason[:300]
                store._log(
                    id=rid,
                    version=r.version,
                    by="controller",
                    superseded="design_flaw",
                    reason=reason[:300],
                )
                done.append(rid)
        if done:
            store.save()
    return done


def apply(store, verdicts: dict, ctx: Context, revalidate=None, by: str = "arbiter") -> dict:
    """Apply verdicts to the store. ``revalidate(rule_ids)`` re-runs validation on repaired
    rules (``validate.validate`` with ``agree=False``). Returns
    {"code_wrong": [ids], "repaired": [ids], "retired": [ids], "disputed": [ids],
    "unjudged": [ids], "design_flaw": [ids]}. A design flaw leaves the rule active."""
    out = {
        "code_wrong": [],
        "repaired": [],
        "retired": [],
        "disputed": [],
        "unjudged": [],
        "design_flaw": [],
    }
    for rid, v in verdicts.items():
        r = store.rules[rid]
        if v["verdict"] == "unjudged":
            out["unjudged"].append(rid)
            continue
        store.record_verdict(
            rid,
            {
                k: v[k]
                for k in (
                    "verdict",
                    "reason",
                    "question",
                    "spec_rule",
                    "element",
                    "decision",
                    "fix",
                )
                if k in v
            },
        )
        if v["verdict"] == DESIGN_FLAW:
            out["design_flaw"].append(rid)
        elif v["verdict"] == "code_wrong":
            out["code_wrong"].append(rid)
        elif v["verdict"] == "rule_wrong":
            repairs = sum(
                1
                for h in store.history(rid)
                if h.get("to") == "candidate" and str(h.get("reason", "")).startswith("repaired")
            )
            if v.get("repair") and repairs < MAX_REPAIRS:
                store.repair(rid, v["repair"], v["reason"], by)
                out["repaired"].append(rid)
            else:
                store.set_status(rid, "retired", f"arbiter: rule wrong: {v['reason']}", by)
                out["retired"].append(rid)
        else:
            store.set_status(rid, "disputed", f"arbiter: ambiguous spec: {v['reason']}", by)
            store.add_question(rid, v.get("question") or v["reason"])
            out["disputed"].append(rid)
    if out["repaired"] and revalidate is not None:
        revalidate(out["repaired"])
    return out


def run(
    store,
    llm,
    failing: list[Rule],
    results: dict,
    ctx: Context,
    revalidate=None,
    design_flaw: bool = False,
    adr: str | None = None,
) -> dict:
    """Arbitrate the failing rules not yet judged at their current version, apply the
    verdicts, and return ``apply``'s summary plus ``reused`` (rules whose earlier
    ``code_wrong`` verdict still stands). ``design_flaw``/``adr``: see ``arbitrate``."""
    fresh = [r for r in failing if judged(r) is None]
    reused = [r.id for r in failing if (j := judged(r)) and j.get("verdict") == "code_wrong"]
    out = apply(
        store,
        arbitrate(llm, fresh, results, ctx, design_flaw, adr)
        if design_flaw
        else arbitrate(llm, fresh, results, ctx),
        ctx,
        revalidate,
    )
    out["reused"] = reused
    out["code_wrong"] = out["code_wrong"] + reused
    return out
