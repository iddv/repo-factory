"""Mining: one model call proposes new rules (executable scenarios with source citations).

Inputs: the spec, the owner's answers, the plan and its fixed public interface, the rules
the store already has, and the current code of the kept candidate. The code is there to
aim the scenarios at where the code is probably wrong and to show how state is set up
through the interface; the expected outcome must come from the spec and answers only
(validation (b) checks that independently).

Modes (``model.MODES``):

- ``requirements``: one scenario per concrete behaviour the spec states.
- ``interactions``: after a milestone, how its behaviour combines with the earlier
  milestones' (state that one feature leaves behind and another relies on).
- ``edges``: boundaries, invalid input, repeated operations, concurrency, time.

Build v4 hardening adds ``mine_design`` (``DESIGN_FOCUS``): one scenario per rule of the
design pack, the open gaps of its final rule walk first, each with the walk's why.

Every prompt asks for scenarios that a *plausible but subtly wrong* implementation fails."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field

from .llm import tag
from .model import MODES, Rule

MAX_CODE_CHARS = 120_000
DEFAULT_N = {"requirements": 12, "interactions": 14, "edges": 10}


@dataclass
class Context:
    spec: str
    answers: dict = field(default_factory=dict)  # qid -> answer text (None: skipped)
    questions: dict = field(default_factory=dict)  # qid -> question text
    interface: str = ""
    milestones: list = field(default_factory=list)  # [{"id", "title", "build"}]
    code: str = ""  # the kept candidate's files as text
    milestone: str | None = None  # the milestone just built
    built: list = field(default_factory=list)  # milestones built so far

    def answers_md(self) -> str:
        rows = [
            f"- {qid}: {self.questions.get(qid, qid)}\n  answer: {a}"
            for qid, a in self.answers.items()
            if a
        ]
        return "\n".join(rows) or "(none)"

    def plan_md(self) -> str:
        return (
            "\n".join(
                f"- {m['id']}: {m.get('title', '')}: {m.get('build', '')}"
                + ("  (built)" if m["id"] in self.built else "")
                for m in self.milestones
            )
            or "(no plan)"
        )


FOCUS = {
    "requirements": """Focus: REQUIREMENTS. One scenario per concrete, checkable behaviour
the spec or the answers state (limits, periods, amounts, orderings, error cases). Prefer the
exact numbers the spec gives and test both sides of every limit.""",
    "interactions": """Focus: INTERACTIONS. The milestone just built is {milestone}. Write
scenarios about how its behaviour combines with the behaviour of the other milestones
built so far, not about either feature alone. Each scenario should drive the system
through a sequence of operations that crosses at least two features. For every pair of
features (the new one with each earlier one), ask what the first does to state the second
relies on. Look for:
- state one feature accumulates (balances, penalties, counters, flags) that an operation
  of another feature must keep, respect or update rather than reset or ignore;
- parameters that recur across operations (a location, owner, account, channel): are they
  validated and applied consistently by every operation that takes them, including the
  newest ones?
- state transitions one feature must trigger in another, whatever order things happened
  in (a resource becoming free must serve whoever is waiting, also when the wait started
  before, or while, the resource was free);
- time: deadlines that must take effect once time has passed, whether or not any
  maintenance function was called, and how every other operation sees a record whose
  deadline has passed but which nothing has processed yet;
- an operation that must be refused because of state another feature owns, and the
  converse: refused only while that state is really still in force.""",
    "edges": """Focus: EDGES. Boundaries (exactly at a limit, one over, one under; day
boundaries for time rules; caps), invalid input (unknown ids, wrong owner/location,
operations in the wrong state, repeated or out-of-order operations), concurrency (two
actors racing for the last unit) and time (the injectable clock moved past every
deadline the spec mentions, with and without the explicit maintenance call). Prefer edges
where features meet.""",
}

MINE_PROMPT = """{tag}
You are the build factory's own reviewer. The factory checks every candidate
implementation against a growing set of executable rules: pytest scenarios, each traced
to a sentence of the spec or an answer from the owner. Your job is to propose NEW rules
that a plausible but subtly wrong implementation would fail, while a correct
implementation passes.

{focus}

Request (spec.md):
{spec}

Answers from the owner to clarifying questions:
{answers}

Build plan (milestones):
{plan}

Public interface (fixed by the plan; the code implements exactly these names):
{interface}

Rules the factory already has (don't repeat them; find what they miss):
{existing}

Rules proposed before and rejected, with the reason (don't repeat one unless your version
removes the reason):
{rejected}

Current code of the kept candidate (read it to find where it is likely wrong and how to
set up state through the interface; but take every EXPECTED outcome from the spec and
answers only, never from the code):
{code}

Write {n} rules. Each rule is one self-contained pytest module:
- Use only the public interface above (and the standard library and pytest). Set up all
  state through it, in `tmp_path` or in memory; no network; never read spec.md.
- Use the injectable clock (or explicit `now` arguments) for anything time-dependent;
  never sleep.
- Exactly one `def test_<snake_case_name>(...)` per module, with plain asserts (or
  `pytest.raises` with the interface's error types). Assert the behaviour the spec
  requires, not incidental details (message texts, ids, ordering the spec doesn't fix).
- Where the spec is silent or ambiguous about the outcome, don't write that rule.
- Where the spec doesn't settle what happens exactly at a limit (the very instant a
  deadline passes, a value equal to a threshold the spec words loosely), test clearly on
  either side of it instead.

Return each rule as its own ```python block that starts with these comment lines:
# rule: <short title>
# kind: {kind}
# milestones: <ids of the milestones whose behaviour the scenario exercises, e.g. m2, m4>
# source: "<the spec sentence or answer it traces to, copied word for word from the spec or an answer (not from the interface or the code)>"
# expect: <the expected behaviour in one sentence>
"""

PROMPT_VERSION = hashlib.sha256(
    (MINE_PROMPT + json.dumps(FOCUS, sort_keys=True)).encode()
).hexdigest()[:12]

# Build v3 (``mining.schedule: planned``): the pass before any code exists.
PLAN_FOCUS = {
    "requirements": """Focus: REQUIREMENTS, from the plan. No code exists yet: the
scenarios are written before building, against the public interface the plan fixes, and
run on every milestone's code from then on. One scenario per concrete, checkable behaviour
the spec or the answers state (limits, periods, amounts, orderings, error cases). Prefer
the exact numbers the spec gives and test both sides of every limit. Tag each scenario
with the milestones whose behaviour it needs, so it runs once they are built.""",
    "interactions": """Focus: INTERACTIONS, from the plan. No code exists yet: read the
plan's milestones and write scenarios about how their behaviours combine, each driving the
system through a sequence of operations that crosses at least two planned features. For
every pair of features ask what the first does to state the second relies on: state one
feature accumulates (balances, penalties, counters, flags) that another must keep rather
than reset; parameters that recur across operations (a location, owner, account) and must
be validated by every operation that takes them; transitions one feature must trigger in
another whatever the order; deadlines that take effect once time has passed, with or
without a maintenance call; refusals that apply only while the blocking state is still in
force. Tag each scenario with every milestone it crosses.""",
}
PLAN_PROMPT_VERSION = hashlib.sha256(
    (MINE_PROMPT + json.dumps(PLAN_FOCUS, sort_keys=True)).encode()
).hexdigest()[:12]

# A held rule caught a real bug and was promoted to visible; its held replacement.
REPLACE_FOCUS = """

This call replaces one rule. The factory keeps some rules back from the engineers to check
their work independently; this one caught a real bug and is now shown to them, so the
factory needs a NEW held-back rule in the same area: the same features and the same part of
the spec, but a different scenario (other values, another order of operations, another
actor), not a copy. The rule being replaced:
{rule}

Write exactly 1 rule."""
KIND_OF = {"requirements": "requirement", "interactions": "interaction", "edges": "edge"}

# Build v4 hardening (V4-D): the design's known-critical rules, one scenario each. The
# items are the gaps the final combined design walk still lists (with the walk's why) and
# every rule the design was walked against (``core.oneshot.design_items``).
DESIGN_FOCUS = """Focus: KNOWN-CRITICAL RULES, from the design walk. Before any code was
written, the factory walked every rule of the request through the design documents this
code was built from. The rules below are what that design had to express. Those marked GAP
are rules the final design documents still didn't cover, with the reason the walk gave:
the code is most likely wrong exactly there, because an engineer building from those
documents may have treated the gap as an accepted limitation. It is not one: the request
requires the rule. Write one scenario per rule below, in the order given, that checks the
rule's behaviour through the public interface. For a GAP, aim the scenario squarely at
what the walk says is missing (the state the documents never model, the parameter an
operation lacks, the step a hand-off skips) and set up the situation where it matters.
Where the request says an operation is refused but not with which error, accept any of the
interface's errors that fit (`pytest.raises` with a tuple) and assert the state is
unchanged. Skip a rule only where the request doesn't settle the outcome. After the
`# expect:` line add one more header line: `# design: <the rule's id from the list>`.

Rules:
{items}"""
DESIGN_PROMPT_VERSION = hashlib.sha256((MINE_PROMPT + DESIGN_FOCUS).encode()).hexdigest()[:12]

_BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)
_HEAD = re.compile(r"^#\s*(rule|kind|milestones|source|expect|design)\s*:\s*(.*)$", re.I)


def parse_rules(text: str, mode: str) -> list[Rule]:
    """Rules from the model's fenced python blocks (header comments carry the metadata)."""
    out = []
    for block in _BLOCK.findall(text or ""):
        meta, body = {}, []
        for line in block.splitlines():
            m = _HEAD.match(line.strip())
            if m and not body:
                meta[m.group(1).lower()] = m.group(2).strip()
            elif line.strip() or body:
                body.append(line)
        code = "\n".join(body).strip() + "\n"
        if "def test" not in code:
            continue
        ms = [x.strip() for x in re.split(r"[,\s]+", meta.get("milestones", "")) if x.strip()]
        kind = meta.get("kind", KIND_OF.get(mode, "edge")).lower()
        out.append(
            Rule(
                id="",
                title=meta.get("rule", "")[:200] or "(untitled)",
                kind=kind
                if kind in KIND_OF.values() or kind == "regression"
                else KIND_OF.get(mode, "edge"),
                source=meta.get("source", "").strip(),
                code=code,
                milestones=ms,
                expect=meta.get("expect", ""),
                origin={"design": meta["design"]} if meta.get("design") else {},
            )
        )
    return out


def design_items_md(items: list[dict]) -> str:
    """The design items for ``DESIGN_FOCUS``: ``- R11 (GAP; spec): rule`` plus the walk's
    why for a gap and the operation the rule map gives the rule."""
    out = []
    for it in items:
        tags = "; ".join(x for x in ("GAP" if it.get("gap") else "", it.get("source") or "") if x)
        line = f"- {it['id']}" + (f" ({tags})" if tags else "") + f": {it['rule']}"
        if it.get("operation"):
            line += f" [enforced by: {it['operation']}]"
        if it.get("why"):
            line += f"\n  the design walk: {it['why']}"
        out.append(line)
    return "\n".join(out) or "(none)"


def existing_md(rules: list[Rule], lean: bool = False) -> str:
    """The rules the store has, for the prompt; ``lean``: titles only (build v3 §7c)."""
    return (
        "\n".join(f"- {r.brief()}" if lean else f"- {r.brief()}: {r.expect}" for r in rules)
        or "(none yet)"
    )


# One call for several modes (``mining.combine``): the focus texts one after the other,
# then how many rules of each kind.
COMBINED = """

This call covers {modes} together. Write {split}; mark each rule's kind as the kind of
scenario it is."""
MODE_OF = {v: k for k, v in KIND_OF.items()}


def _format(
    tag_line: str,
    focus: str,
    ctx: Context,
    existing: list[Rule],
    lean: bool,
    code: str,
    n: int,
    kind: str,
) -> str:
    return MINE_PROMPT.format(
        tag=tag_line,
        focus=focus,
        spec=ctx.spec,
        answers=ctx.answers_md(),
        plan=ctx.plan_md(),
        interface=ctx.interface or "(none fixed)",
        existing=existing_md([r for r in existing if r.status in ("active", "candidate")], lean),
        rejected="\n".join(
            f"- {r.title}: {r.reason[:160]}"
            for r in existing
            if r.status in ("retired", "disputed")
        )
        or "(none)",
        code=code,
        n=n,
        kind=kind,
    )


def mode_tag(modes) -> str:
    """The tag/ledger mode of a call: ``edges`` or, combined, ``interactions_edges``."""
    return modes if isinstance(modes, str) else "_".join(modes)


class Miner:
    def __init__(self, llm, n: dict | None = None):
        self.llm = llm
        self.n = {**DEFAULT_N, **(n or {})}
        self.passes = 0
        self.lock = threading.Lock()

    @staticmethod
    def at_plan(mode: str, ctx: Context) -> bool:
        """The pass before building (no code, no milestone built yet)."""
        return not ctx.code and ctx.milestone is None and mode in PLAN_FOCUS

    def counts(self, modes: list[str], n: int | dict | None) -> dict[str, int]:
        """Rules to write per mode: the miner's defaults, or the pass's allotment."""
        if n is None:
            return {m: self.n[m] for m in modes}
        if isinstance(n, dict):
            return {m: int(n.get(m, 0)) for m in modes}
        return (
            {modes[0]: int(n)}
            if len(modes) == 1
            else {
                m: int(n) // len(modes) + (1 if i < int(n) % len(modes) else 0)
                for i, m in enumerate(modes)
            }
        )

    def prompt(
        self,
        mode,
        ctx: Context,
        existing: list[Rule],
        replaces: Rule | None = None,
        n: int | dict | None = None,
        lean: bool = False,
    ) -> str:
        """``mode``: one mode, or a list of code modes for one combined call. ``n``: rules
        to ask for (an int, or by mode); default ``self.n``. ``lean``: existing rules as
        titles only (the code digest is scoped by the caller, ``RulesEngine.context``)."""
        modes = [mode] if isinstance(mode, str) else list(mode)
        if not modes or any(m not in MODES for m in modes):
            raise ValueError(f"unknown mining mode {mode!r}")
        code = ctx.code or "(no code yet)"
        if len(code) > MAX_CODE_CHARS:
            code = code[:MAX_CODE_CHARS] + "\n... (truncated)"
        counts = self.counts(modes, n)
        if len(modes) == 1:
            m = modes[0]
            focus = (
                PLAN_FOCUS[m]
                if self.at_plan(m, ctx)
                else FOCUS[m].format(milestone=ctx.milestone or "-")
            )
        else:  # code passes only (callers' business)
            focus = "\n\n".join(FOCUS[m].format(milestone=ctx.milestone or "-") for m in modes)
            focus += COMBINED.format(
                modes=" and ".join(modes),
                split=" and ".join(
                    f"{counts[m]} {KIND_OF[m]} rule(s) (kind: {KIND_OF[m]})"
                    for m in modes
                    if counts[m]
                ),
            )
        if replaces is not None:
            focus += REPLACE_FOCUS.format(
                rule=f"{replaces.brief()}\nSource: {replaces.source}\nExpect: {replaces.expect}"
            )
        return _format(
            tag("replace" if replaces is not None else "mine", mode_tag(mode)),
            focus,
            ctx,
            existing,
            lean,
            code,
            1 if replaces is not None else sum(counts.values()),
            " or ".join(KIND_OF[m] for m in modes),
        )

    def design_prompt(
        self, items: list[dict], ctx: Context, existing: list[Rule], lean: bool = False
    ) -> str:
        code = ctx.code or "(no code yet)"
        if len(code) > MAX_CODE_CHARS:
            code = code[:MAX_CODE_CHARS] + "\n... (truncated)"
        return _format(
            tag("mine", "design"),
            DESIGN_FOCUS.format(items=design_items_md(items)),
            ctx,
            existing,
            lean,
            code,
            len(items),
            "requirement",
        )

    def mine_design(
        self, items: list[dict], ctx: Context, existing: list[Rule], lean: bool = False
    ) -> list[Rule]:
        """One call: a requirement rule for each design item (``DESIGN_FOCUS``), at most
        one per item. ``origin``: mode ``design`` and ``design``, the item's id (when the
        rule names one of the items)."""
        with self.lock:
            self.passes += 1
            n_pass = self.passes
        rules = parse_rules(
            self.llm(self.design_prompt(items, ctx, existing, lean)), "requirements"
        )[: len(items)]
        ids = {str(it["id"]).lower(): it["id"] for it in items}
        for i, r in enumerate(rules, 1):
            ref = str(r.origin.get("design") or "").strip().lower()
            r.kind = "requirement"
            r.origin = {
                "mode": "design",
                "prompt": DESIGN_PROMPT_VERSION + ("+lean" if lean else ""),
                "pass": n_pass,
                "after": ctx.milestone,
                "sample": i,
                "design": ids.get(ref),
            }
        return rules

    def mine(
        self,
        mode,
        ctx: Context,
        existing: list[Rule],
        replaces: Rule | None = None,
        n: int | dict | None = None,
        lean: bool = False,
    ) -> list[Rule]:
        """Proposed rules (not yet in a store), each with its ``origin`` recorded.
        ``replaces``: mine one rule in the same area as that (promoted) rule. ``mode``: a
        list for one combined call (each rule's ``origin.mode`` follows its kind)."""
        with self.lock:
            self.passes += 1
            n_pass = self.passes
        modes = [mode] if isinstance(mode, str) else list(mode)
        text = self.llm(self.prompt(mode, ctx, existing, replaces, n, lean))
        rules = parse_rules(text, modes[0])
        if replaces is not None:
            rules = rules[:1]
        elif n is not None:  # a budgeted pass: never more than allotted
            rules = rules[: sum(self.counts(modes, n).values())]
        plan = len(modes) == 1 and self.at_plan(modes[0], ctx)
        version = PLAN_PROMPT_VERSION if plan else PROMPT_VERSION
        if lean:
            version += "+lean"
        for i, r in enumerate(rules, 1):
            m = modes[0] if len(modes) == 1 else MODE_OF.get(r.kind, modes[0])
            r.origin = {
                "mode": m,
                "prompt": version,
                "pass": n_pass,
                "after": ctx.milestone,
                "sample": i,
            }
            if len(modes) > 1:
                r.origin["combined"] = list(modes)
            if plan:
                r.origin["at"] = "plan"
            if replaces is not None:
                r.origin["replaces"] = replaces.id
        return rules
