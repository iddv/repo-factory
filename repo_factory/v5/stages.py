"""A build flow is data: a list of stages (track LEAN; the owner, 2026-10-19: "you should be
able to add a stage to the build").

Each ``Stage`` says what it is called, what it reads and writes (files and folders in the
round folder, ``<den>/v5/r<N>/``), how it runs (a prompt of the policy, run as one Claude Code
session in the build workspace, or a code function of the run), its model role (Settings ›
Models), its own cap and timeout (none by default: the owner's rule), and whether its failure
holds the build. ``FLOWS`` holds the three shipped flows: ``v5`` (today's), ``simple`` (BF) and
``lean`` (Brief → Build → Harden → Check → Fix once → Publish).

The runner (``pipeline.Run.steps``) walks a flow's list in order; a step it has finished is
never run again (the round's ``state.json``), so a resumed round goes on where it stopped.

A policy can add a **prompt stage** of its own in ``rules.yaml``: ``add_stages: [{name: docs,
prompt: docs, after: harden, role: build, holds: false, max_turns: 60, timeout_s: 1200}]``,
with ``prompts/docs.md`` in the policy (``<<spec>>`` and ``<<flows>>`` are filled in). It runs
as one session in the build workspace after the stage it names and its work is committed. It
is first-party only: the policy is the owner's; stages from someone else (cubs) are a later,
separate design (track BSD), and this shape is kept compatible with it.

**What a stage writes for repo-factory goes to the round, never into the product.** A prompt stage
that needs to hand repo-factory a file (the harden stage's map of flows to tests) writes it under
``.repo-factory/`` in the workspace; the runner moves it to the round folder after the session, and
delivery strips every process file (``process.py``) from the delivered tree in every flow."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# The six cells of the build page (BF-0, ``pipe.ts`` ``LEDGER``) a stage lights
CELLS = ("brief", "plan", "build", "check", "fix", "publish")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,23}$")


@dataclass(frozen=True)
class Stage:
    """One stage of a flow. ``prompt``: the policy prompt it runs as one session (a prompt
    stage), or ``code``: the run's method that does it (a code stage); exactly one.
    ``inputs``/``outputs``: paths relative to the round folder (``build/`` is the workspace).
    ``role``: the model role whose model its sessions use (None: no session of its own, or
    the role its code picks per session). ``cap_usd``/``timeout_s``/``max_turns``: the
    stage's own defaults (None: no cap; ``rules.yaml``'s section of the same name goes over
    them). ``holds``: a failure of this stage holds the build (a stage that doesn't hold is
    recorded and the build goes on). ``loop``: the step runs until its function says it is
    over (its state is saved in between). ``record``: the runner records it as one
    ``v5_stage`` (a loop records its own stages inside). ``cell``: the build page's cell."""

    name: str
    prompt: str | None = None
    code: str | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    role: str | None = None
    cap_usd: float | None = None
    timeout_s: int | None = None
    max_turns: int | None = None
    holds: bool = True
    loop: bool = False
    record: bool = True
    cell: str = "build"
    label: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        if not NAME.match(self.name):
            raise ValueError(
                f"stage name {self.name!r}: lower-case letters, digits and _, 2 to 24 characters"
            )
        if (self.prompt is None) == (self.code is None):
            raise ValueError(f"stage {self.name}: a prompt or a code function, exactly one")
        if self.cell not in CELLS:
            raise ValueError(f"stage {self.name}: cell must be one of {', '.join(CELLS)}")

    @property
    def kind(self) -> str:
        return "prompt" if self.prompt else "code"

    @property
    def id(self) -> str:
        """The stage's public id (BSD-DESIGN §3.1: ``brief``, ``build``, ``harden``,
        ``check``, ``publish``, the anchors a cub's stage names); the internal name
        otherwise. The records and events keep the internal name (``v5_stage`` ``stage``)."""
        return PUBLIC.get(self.name, self.name)

    def to_dict(self) -> dict:
        """The stage as plain data (``build_started.flow_stages``, the docs), with BSD's
        fields: ``id``, ``title``, ``owner`` ("core": repo-factory's own or the policy's),
        ``anchor`` (None for a flow's own stage), ``core``, ``on_fail``."""
        d = {k: v for k, v in asdict(self).items() if k != "extra"}
        d.update(
            kind=self.kind,
            inputs=list(self.inputs),
            outputs=list(self.outputs),
            id=self.id,
            title=self.label or self.name,
            owner="core",
            anchor=self.extra.get("anchor"),
            core=not self.extra.get("anchor"),
            on_fail="hold" if self.holds else "warn",
        )
        return d


# Internal names whose public id differs (BSD-DESIGN §3.1, ask 5): the lean flow's Brief is
# today's scope step, its Publish today's delivery
PUBLIC = {"scope": "brief", "deliver": "publish"}


ADD_KEYS = (
    "name",
    "prompt",
    "after",
    "role",
    "holds",
    "max_turns",
    "timeout_s",
    "cap_usd",
    "cell",
    "label",
    "inputs",
    "outputs",
)


def added(spec: dict, policy_prompts: set[str]) -> tuple[Stage, str]:
    """A prompt stage a policy adds (``rules.yaml`` ``add_stages``): ``(stage, after)``.
    Refuses unknown keys, a code stage (only repo-factory's library has code), and a prompt the
    policy doesn't ship."""
    if not isinstance(spec, dict):
        raise ValueError("add_stages: each entry is a map (name, prompt, after, ...)")
    bad = sorted(set(spec) - set(ADD_KEYS))
    if bad:
        raise ValueError(f"add_stages: unknown key(s) {bad} (keys: {', '.join(ADD_KEYS)})")
    name, after = str(spec.get("name") or ""), str(spec.get("after") or "")
    p = str(spec.get("prompt") or name)
    if p not in policy_prompts:
        raise ValueError(f"add_stages {name}: the policy has no prompts/{p}.md")
    if not after:
        raise ValueError(f"add_stages {name}: say which stage it runs after (after:)")
    st = Stage(
        name=name,
        prompt=p,
        role=spec.get("role") or "build",
        holds=bool(spec.get("holds", False)),
        cell=spec.get("cell") or "build",
        max_turns=spec.get("max_turns"),
        timeout_s=spec.get("timeout_s"),
        cap_usd=spec.get("cap_usd"),
        label=str(spec.get("label") or name),
        inputs=tuple(spec.get("inputs") or ("build/",)),
        outputs=tuple(spec.get("outputs") or ("build/",)),
        extra={"anchor": f"after:{after}"},
    )
    return st, after


def with_added(flow: tuple[Stage, ...], adds: list[tuple[Stage, str]]) -> tuple[Stage, ...]:
    """``flow`` with each added stage after the one it names, by its name or its public id
    (``after: brief``), in the order given."""
    out = list(flow)
    for st, after in adds:
        names = [s.name for s in out]
        ids = [s.id for s in out]
        if st.name in names or st.name in ids:
            raise ValueError(f"add_stages: {st.name} is already a stage of this flow")
        at = names.index(after) if after in names else ids.index(after) if after in ids else None
        if at is None:
            raise ValueError(
                f"add_stages {st.name}: no stage {after!r} to run after (stages: {', '.join(ids)})"
            )
        out.insert(at + 1, st)
    return tuple(out)


# -- the shipped flows ----------------------------------------------------------------------
V5 = (
    Stage(
        "scope",
        code="do_scope",
        outputs=("scope.json", "SCOPE.md", "STUDY.md"),
        role="build",
        holds=False,
        cell="plan",
        label="Scope",
    ),
    Stage(
        "build",
        code="do_build",
        inputs=("SCOPE.md",),
        outputs=("build/",),
        role="build",
        cell="build",
        label="Build",
    ),
    Stage(
        "claims",
        code="do_claims",
        inputs=("SCOPE.md",),
        outputs=("claims.json",),
        role="claims",
        cell="check",
        label="Claims",
    ),
    Stage(
        "verify",
        code="do_verify",
        inputs=("build/", "claims.json"),
        outputs=("findings.json", "verify-exam/"),
        role="verify",
        cell="check",
        label="Verify",
    ),
    Stage(
        "oracle",
        code="do_oracle",
        inputs=("findings.json",),
        outputs=("findings.json",),
        role="oracle",
        cell="check",
        label="Oracle",
    ),
    Stage(
        "repairs",
        code="repair_step",
        inputs=("findings.json",),
        outputs=("build/",),
        role="repair",
        loop=True,
        record=False,
        cell="fix",
        label="Repair ⇄ re-verify",
    ),
    Stage(
        "adopt",
        code="adopt_step",
        inputs=("build/",),
        outputs=("build/",),
        role="repair",
        record=False,
        cell="fix",
        label="Own tests",
    ),
    Stage(
        "readiness",
        code="readiness_step",
        inputs=("build/",),
        outputs=("readiness.json", "build/"),
        role="readiness",
        loop=True,
        record=False,
        cell="check",
        label="Readiness",
    ),
    Stage(
        "deliver",
        code="do_deliver",
        inputs=("build/",),
        outputs=("deliver/", "exam/evidence/"),
        cell="publish",
        label="Deliver",
    ),
)

SIMPLE = (
    Stage(
        "plan",
        code="do_plan",
        outputs=("scope.json", "SCOPE.md", "claims.json"),
        role="plan",
        holds=False,
        cell="plan",
        label="Plan",
    ),
    Stage(
        "build",
        code="do_build",
        inputs=("SCOPE.md",),
        outputs=("build/",),
        role="build",
        cell="build",
        label="Build",
    ),
    Stage(
        "loop",
        code="loop_step",
        inputs=("build/", "claims.json"),
        outputs=("findings.json", "readiness.json", "build/"),
        role="check",
        loop=True,
        record=False,
        cell="check",
        label="Check ⇄ Fix",
    ),
    Stage(
        "deliver",
        code="do_deliver",
        inputs=("build/",),
        outputs=("deliver/", "exam/evidence/"),
        cell="publish",
        label="Deliver",
    ),
)

LEAN = (
    # Brief: today's scope step, its prompt unchanged (SCOPE.md stays in the round)
    Stage(
        "scope",
        code="do_scope",
        outputs=("scope.json", "SCOPE.md", "STUDY.md"),
        role="build",
        holds=False,
        cell="plan",
        label="Brief",
    ),
    # Build: one Claude Code session on the pitch plus SCOPE.md
    Stage(
        "build",
        code="do_build",
        inputs=("SCOPE.md",),
        outputs=("build/",),
        role="build",
        cell="build",
        label="Build",
    ),
    # Harden: one session (EV7's prompt, adapted): robustness, tests, CI, the docs a user
    # needs; no invented features or settings. Its map of flows to tests goes to the round.
    Stage(
        "harden",
        prompt="harden",
        inputs=("SCOPE.md", "build/"),
        outputs=("build/", "flows.json"),
        role="build",
        max_turns=120,
        timeout_s=3600,
        holds=False,
        cell="build",
        label="Harden",
    ),
    # Check: the objective gates only (the security probe, the README's quick start as
    # written, loopback, tests green, completeness on the definition of done, secrets, rea)
    Stage(
        "check",
        code="lean_check",
        inputs=("build/", "flows.json"),
        outputs=("readiness.json", "findings.json", "check.json"),
        role="check",
        cell="check",
        label="Check",
    ),
    # one fix session when a gate failed, then the harness again; what is still red holds
    # at the gate, as today
    Stage(
        "fix",
        code="lean_fix",
        inputs=("check.json", "build/"),
        outputs=("build/", "check.json"),
        role="fix",
        max_turns=120,
        timeout_s=3000,
        holds=False,
        cell="fix",
        label="Fix once",
    ),
    Stage(
        "deliver",
        code="do_deliver",
        inputs=("build/",),
        outputs=("deliver/", "exam/evidence/"),
        cell="publish",
        label="Publish",
    ),
)

FLOWS: dict[str, tuple[Stage, ...]] = {"v5": V5, "simple": SIMPLE, "lean": LEAN}


def flow_of(rules: dict | None) -> str:
    """The flow a policy's rules run: ``flow:`` (``simple``, ``lean``), else ``v5``."""
    f = str((rules or {}).get("flow") or "").strip().lower()
    return f if f in FLOWS else "v5"


def library(flow: str) -> tuple[str, ...]:
    """The stage names a ``rules.yaml`` ``stages:`` list may hold for ``flow``: the
    recorded stages (a loop records its own; v5's ``repairs`` and ``readiness`` steps record
    ``repair``, ``reverify`` and ``readiness``, simple's ``loop`` records ``check``, ``fix``)."""
    from .common import SIMPLE_STAGES, STAGES

    if flow == "simple":
        return SIMPLE_STAGES
    if flow == "lean":
        return tuple(s.name for s in LEAN)
    return STAGES


def describe(flow: str) -> list[dict]:
    """``flow``'s stages as plain data."""
    return [s.to_dict() for s in FLOWS[flow]]
