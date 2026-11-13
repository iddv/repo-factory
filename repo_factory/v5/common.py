"""Plumbing the v5 stages share: prompts from the policy, no-tools JSON calls, the budget,
and running independent calls in parallel."""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import json
import math
import re
from pathlib import Path

from ..costs import ledger

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
# The simple flow's stage library (BF-DESIGN §4: Brief → Plan → Build → Check ⇄ Fix →
# Publish), a policy with ``flow: simple`` (``policies/simple``). Chosen per build
# (``build.flow``), beside today's: the records and events keep their names (D10).
SIMPLE_STAGES = ("plan", "build", "check", "fix", "deliver")
# The lean flow's (track LEAN: Brief → Build → Harden → Check → Fix once → Publish), a policy
# with ``flow: lean`` (``policies/lean``); its stage list is ``stages.LEAN``.
LEAN_STAGES = ("scope", "build", "harden", "check", "fix", "deliver")
# Today's role and stage names onto the lean flow's (a loop.yaml's build.models and
# build.stage_caps): the scope brief uses the builder's model, as today.
LEAN_MAP = {
    "plan": None,
    "claims": None,
    "verify": "check",
    "oracle": None,
    "repair": "fix",
    "reverify": None,
    "readiness": "check",
}
# Today's stage names onto the simple flow's (a loop.yaml's build.models and build.stage_caps
# written for today's flow; BF-DESIGN §7.3): a name mapped to None does nothing there.
SIMPLE_MAP = {
    "scope": "plan",
    "claims": None,
    "verify": "check",
    "oracle": None,
    "repair": "fix",
    "reverify": None,
    "readiness": None,
}
# Where a ledger record tagged with today's name counts in the simple flow (a library call
# that tags its own stage, e.g. the scope call): the step it runs in.
SIMPLE_LEDGER = {
    "scope": "plan",
    "claims": "plan",
    "verify": "check",
    "oracle": "check",
    "readiness": "check",
    "repair": "fix",
    "reverify": "fix",
}
# Stages repo-factory no longer has. An older rules.yaml, or a loop.yaml's build.models or
# build.stage_caps, may still name one: it is dropped as if it were not there. mutants: its
# score decided nothing (AU1 #4).
RETIRED_STAGES = ("mutants",)
# Stages only a maintenance fix runs (MAINTAIN.md §5.2, ``maintain/revise.py``): reproduce
# writes the failing test first. Kept out of STAGES so a build's stage list, report and costs
# stay as they were.
REVISE_STAGES = ("reproduce",)
# Stage caps (USD) are settings, none by default (the owner's rule: spend is uncapped unless
# a cap is set): ``rules.yaml``'s ``build.stage_caps`` and the loop's ``build.stage_caps``
# (passed as ``$REPO_FACTORY_V5_STAGE_CAPS``), a map of stage to dollars. A stage with a cap gives
# its sessions min(their rules.yaml max_usd, what is left of the stage cap, what is left of
# the global cap); a stage without one, and no global cap and no session max_usd, starts its
# sessions with no ``--max-budget-usd`` at all (``max_turns`` and ``timeout_s`` still stop a
# runaway). No-tools calls are checked before each one.
STAGE_CAPS: dict[str, float] = {}
STAGE_CAPS_ENV = "REPO_FACTORY_V5_STAGE_CAPS"
# What a stage typically costs (§3.1's old fixed caps): only the reserve rule reads these,
# and only under a global cap (a second repair round must leave re-verify and readiness
# their share). Never a cap. readiness (Addendum C): security 1.20 + checks 0.60 + repair 1.50.
TYPICAL_USD = {
    "scope": 1.0,
    "build": 6.0,
    "claims": 1.0,
    "verify": 2.5,
    "oracle": 0.6,
    "repair": 2.5,
    "reverify": 1.0,
    "readiness": 3.3,
    "deliver": 0.1,
    "reproduce": 1.0,
}
SECTION = re.compile(r"^<!-- section: (\w+) -->\s*$", re.M)


def usd_or_none(v, what: str = "cap") -> float | None:
    """A cap as configured: a positive number of dollars, or None (``null``, ``none``, 0,
    empty: no cap). Raises ValueError otherwise."""
    if v is None or isinstance(v, bool):
        if isinstance(v, bool):
            raise ValueError(f"{what}: give a number of dollars, or null for no cap")
        return None
    if isinstance(v, str):
        v = v.strip().lstrip("$")
        if v.lower() in ("", "none", "null", "no", "off", "unlimited"):
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: give a number of dollars, or null for no cap") from None
    if f != f or f < 0:
        raise ValueError(f"{what}: give a number of dollars, or null for no cap")
    return f if f > 0 else None


def is_simple(rules: dict | None) -> bool:
    """Whether a policy's rules are the simple flow's (``flow: simple``)."""
    return str((rules or {}).get("flow") or "").strip().lower() == "simple"


def is_lean(rules: dict | None) -> bool:
    """Whether a policy's rules are the lean flow's (``flow: lean``)."""
    return str((rules or {}).get("flow") or "").strip().lower() == "lean"


def to_lean(names: dict) -> dict:
    """``to_simple`` for the lean flow (``LEAN_MAP``); a key already in its names wins."""
    out = {}
    for k, v in names.items():
        if k in LEAN_MAP:
            if LEAN_MAP[k] is not None and LEAN_MAP[k] not in names:
                out[LEAN_MAP[k]] = v
        else:
            out[k] = v
    return out


def to_simple(names: dict) -> dict:
    """A map keyed by today's stage names (``build.models``, ``build.stage_caps``) in the
    simple flow's names: ``verify``→``check``, ``repair``→``fix``, ``scope``→``plan``; the
    stages the simple flow has no counterpart for are dropped (``repo-factory doctor`` says so).
    A key already in the simple flow's names wins over a mapped one."""
    out = {}
    for k, v in names.items():
        if k in SIMPLE_MAP:
            if SIMPLE_MAP[k] is not None and SIMPLE_MAP[k] not in names:
                out[SIMPLE_MAP[k]] = v
        else:
            out[k] = v
    return out


def stage_caps(rules: dict, overlay: str | dict | None = None) -> dict[str, float]:
    """The build's stage caps: ``rules.yaml``'s ``build.stage_caps`` with ``overlay`` (the
    loop's ``build.stage_caps``, as JSON in ``$REPO_FACTORY_V5_STAGE_CAPS``) over it. Only the
    stages with a cap appear; a null in the overlay removes the policy's cap. Unknown stages
    are refused; a retired one (``RETIRED_STAGES``) is dropped. For the simple flow, today's
    names are mapped onto its own (``to_simple``)."""
    base = ((rules or {}).get("build") or {}).get("stage_caps") or {}
    over = json.loads(overlay) if isinstance(overlay, str) and overlay.strip() else overlay
    if over is not None and not isinstance(over, dict):
        raise ValueError(f"{STAGE_CAPS_ENV}: expected a JSON object of stage to dollars")
    if not isinstance(base, dict):
        raise ValueError("rules.yaml build.stage_caps: expected a map of stage to dollars")
    out: dict[str, float] = {}
    simple, lean = is_simple(rules), is_lean(rules)
    lib = SIMPLE_STAGES if simple else LEAN_STAGES if lean else STAGES
    known = set(lib) | set(REVISE_STAGES)
    for src in (base, over or {}):
        src = {k: v for k, v in src.items() if k not in RETIRED_STAGES}
        if simple:
            src = to_simple(src)
        elif lean:
            src = to_lean(src)
        bad = sorted(set(src) - known)
        if bad:
            raise ValueError(f"stage_caps: unknown stage(s) {bad} (stages: {list(lib)})")
        for k, v in src.items():
            cap = usd_or_none(v, f"stage_caps.{k}")
            if cap is None:
                out.pop(k, None)
            else:
                out[k] = cap
    return out


# -- prompts ---------------------------------------------------------------------------------
def prompt_home(policy: Path, name: str) -> Path:
    """The policy dir ``<name>.md`` is served from: ``policy`` itself when it has the
    prompt, else the policy its ``rules.yaml`` names in ``inherits`` (the simple flow shares
    today's plan, build, exam, security and checks prompts byte for byte, and the owner's
    edits to them in Settings › Prompts, with ``policies/v5``)."""
    p = Path(policy)
    if (p / "prompts" / f"{name}.md").is_file():
        return p
    try:
        import yaml

        parent = (yaml.safe_load((p / "rules.yaml").read_text()) or {}).get("inherits")
    except (OSError, ValueError):
        return p
    if not parent:
        return p
    from ..resources import POLICIES

    return prompt_home(POLICIES / str(parent), name)


def prompt_text(policy: Path, name: str, section: str | None = None) -> str:
    """``<policy>/prompts/<name>.md``: the part before the first ``<!-- section: x -->``
    marker, or the named section. A prompt of the shipped policy comes with the owner's
    house rules, additions or override (``custom_prompts``). A policy that ``inherits``
    another is served the other's prompts it doesn't have (``prompt_home``)."""
    from ..custom_prompts import policy_text

    text = policy_text(prompt_home(policy, name), name)
    parts = SECTION.split(text)
    if section is None:
        return parts[0].strip() + "\n"
    for i in range(1, len(parts) - 1, 2):
        if parts[i] == section:
            return parts[i + 1].strip() + "\n"
    raise KeyError(f"prompts/{name}.md has no section {section!r}")


def render(template: str, **vals) -> str:
    """Fill ``<<name>>`` placeholders (JSON braces in the prompt stay as they are)."""
    for k, v in vals.items():
        template = template.replace(f"<<{k}>>", v if isinstance(v, str) else json.dumps(v))
    return template


def prompt(policy: Path, name: str, section: str | None = None, **vals) -> str:
    return render(prompt_text(policy, name, section), **vals)


# -- no-tools calls --------------------------------------------------------------------------
def parse_json(text: str):
    from ..llm.json import extract_json

    return extract_json(text or "")


def ask_json(
    llm,
    task: str,
    text: str,
    budget: Budget | None = None,
    stage: str | None = None,
    retries: int = 1,
) -> dict:
    """One no-tools call through ``llm`` (a ``rules.llm`` backend: ``ClaudeLLM`` records it
    in the ledger) with the ``[factory-rules task=<task>]`` tag the fakes dispatch on; the reply's
    JSON object. Unparsable output is retried ``retries`` times, then ``{}``. The budget is
    checked before each attempt (``BudgetExceeded``)."""
    from ..rules.llm import tag

    body = f"{tag(task)}\n{text}"
    for attempt in range(retries + 1):
        if budget is not None and stage is not None:
            budget.check(stage)
        with ledger.tags(stage=stage):
            out = llm(
                body
                if attempt == 0
                else body + "\n\nYour previous reply had no parsable JSON object. Return only "
                "the JSON object."
            )
        try:
            obj = parse_json(out)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def parallel(fns: list, workers: int = 4) -> list:
    """Run independent calls at once (each in a copy of the caller's context, so ledger tags
    and the active span stay); results in order."""
    if len(fns) <= 1:
        return [f() for f in fns]
    with cf.ThreadPoolExecutor(max_workers=min(workers, len(fns))) as ex:
        futs = [ex.submit(contextvars.copy_context().run, f) for f in fns]
        return [f.result() for f in futs]


# -- the budget ------------------------------------------------------------------------------
class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """The build's money: the ledger of this build (``ledger.read(den, build)``) is the
    record; ``extra`` adds spend the ledger doesn't hold (tests). ``cap``: the global cap;
    None is no global cap (the owner's default): then the reserve rule (``allows``) always
    passes. ``stage_caps``: the configured stage caps (``stage_caps()``); a stage without one
    has no cap. With neither, nothing here limits a session (``session_cap`` is None)."""

    def __init__(
        self,
        cap: float | None,
        den_root: Path | None = None,
        build: int | None = None,
        stage_caps: dict | None = None,
        stages: tuple = STAGES,
    ):
        self.stages = tuple(stages)  # the flow's stage library (SIMPLE_STAGES)
        self.alias = SIMPLE_LEDGER if self.stages == SIMPLE_STAGES else {}
        self.cap = float(cap) if cap is not None else None
        self.den_root, self.build = den_root, build
        self.stage_caps = {
            **STAGE_CAPS,
            **{k: float(v) for k, v in (stage_caps or {}).items() if v is not None},
        }
        self.extra: dict[str, float] = {}
        self.hit: list[str] = []  # stages that ran into their cap
        # stage -> [spent in the stage when its window opened, the window's cap]: a stage that
        # runs once per repair round (repair, reverify) gets a cap per round, not one cap
        # shared by every round (round 2 once got $0.06 because round 1 had used the rest)
        self.windows: dict[str, list[float]] = {}

    def window(self, stage: str, cap: float | None = None) -> None:
        """Open a new window for ``stage``: from now on its cap (``cap``, default the
        stage's) counts only what is spent from here. Without either there is no cap, and
        an earlier window of the stage is closed."""
        cap = cap if cap is not None else self.stage_caps.get(stage)
        if cap is None:
            self.windows.pop(stage, None)
            return
        self.windows[stage] = [self.by_stage().get(stage, 0.0), float(cap)]

    def typical(self, stage: str) -> float:
        """What ``stage`` may need, for the reserve rule: its cap if it has one, else
        ``TYPICAL_USD``."""
        return float(self.stage_caps.get(stage, TYPICAL_USD.get(stage, 1.0)))

    def _records(self) -> list[dict]:
        if self.den_root is None:
            return []
        return ledger.read(self.den_root, self.build)

    def by_stage(self) -> dict[str, float]:
        out = {s: 0.0 for s in self.stages}
        for r in self._records():
            if r.get("stage") in REVISE_STAGES:
                out.setdefault(r["stage"], 0.0)
            s = r.get("stage")
            s = s if s in out else self.alias.get(s, "deliver")
            out[s] += float(r.get("usd") or 0.0)
        for s, v in self.extra.items():
            s = s if s in out else self.alias.get(s, s)
            out[s] = out.get(s, 0.0) + v
        return {k: round(v, 4) for k, v in out.items()}

    def tokens_by_stage(self) -> dict[str, tuple[int, int]]:
        """``by_stage``'s tokens: stage -> ``(in, out)`` summed over the ledger records that
        carry token fields (records without them add nothing)."""
        out: dict[str, tuple[int, int]] = {}
        for r in self._records():
            s = r.get("stage")
            s = s if s in self.stages or s in REVISE_STAGES else self.alias.get(s, "deliver")
            tin, tout = out.get(s, (0, 0))
            out[s] = (tin + int(r.get("in_tokens") or 0), tout + int(r.get("out_tokens") or 0))
        return out

    def spent(self) -> float:
        return round(sum(self.by_stage().values()), 4)

    @property
    def limited(self) -> bool:
        """Whether the build has a global cap."""
        return self.cap is not None

    def left(self) -> float:
        if self.cap is None:
            return math.inf
        return round(self.cap - self.spent(), 4)

    def stage_left(self, stage: str) -> float:
        if stage in self.windows:
            base, cap = self.windows[stage]
            return round(cap - (self.by_stage().get(stage, 0.0) - base), 4)
        cap = self.stage_caps.get(stage, self.cap)
        if cap is None:
            return math.inf
        return round(cap - self.by_stage().get(stage, 0.0), 4)

    def check(self, stage: str, need: float = 0.01) -> None:
        """Before a call: raises ``BudgetExceeded`` unless ``need`` (default: a cent, any
        call costs something) fits under the build's cap and the stage has money left."""
        if self.cap is not None and self.spent() + need > self.cap:
            self._hit(stage)
            raise BudgetExceeded(
                f"{stage}: the build's cap ${self.cap:.2f} is reached (spent ${self.spent():.2f})"
            )
        if self.stage_left(stage) <= 0:
            self._hit(stage)
            cap = (
                self.windows[stage][1]
                if stage in self.windows
                else self.stage_caps.get(stage, self.cap)
            )
            raise BudgetExceeded(f"{stage}: the stage cap ${cap:.2f} is reached")

    def allows(self, need: float | None) -> bool:
        """The reserve rule (§3.1): ``spent + need <= cap``; always true without a cap.
        ``need`` None (an uncapped session): a dollar, as a floor."""
        need = 1.0 if need is None else float(need)
        return self.cap is None or self.spent() + need <= self.cap + 1e-9

    def cap_text(self) -> str:
        return f"${self.cap:.2f}" if self.cap is not None else "no cap"

    def session_cap(self, stage: str, want: float | None) -> float | None:
        """A session's own ``max_usd``: never more than the stage or the build has left.
        ``want``: the session's configured max_usd (None: none). None when nothing caps it
        (no session max_usd, no stage cap, no global cap): the session then runs without
        ``--max-budget-usd``. This is the session's **own** allowance also when it resumes
        (forks) an earlier session: Claude Code 2.1.287 reports ``total_cost_usd``
        cumulatively over the session's history, but ``--max-budget-usd`` counts only the
        resumed run's own spend (measured; ``profile.launch``'s doc)."""
        lim = min(math.inf if want is None else float(want), self.stage_left(stage), self.left())
        if lim == math.inf:
            return None
        return round(max(0.0, lim), 2)

    def _hit(self, stage: str) -> None:
        if stage not in self.hit:
            self.hit.append(stage)
