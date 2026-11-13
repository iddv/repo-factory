"""``v5.pipeline.build``: the vanilla build, then post-build search (docs/design/build-v5.md §3).

Stages (``rules.yaml`` ``stages:``): scope -> build -> claims -> verify -> oracle
-> repair -> reverify -> readiness (``readiness.py``: security review, operational checks, one
repair round) -> deliver. Nothing is decided before code exists; nothing weaker than the
builder decides what is correct; a repair is never thrown away because two of the factory's
own checks disagree (the conflict protocol, ``repair.run_round``).

Files, under ``<den>/v5/r<round>/``: ``build/`` (the builder's workspace, a git repo with one
commit per finding after repairs), ``verify/`` (the verifier's copy with ``_verify/``),
``claims.json``, ``findings.json`` (the verifier's, plus the harness's statuses and the
panel's classes), ``transcripts/``. Scenarios live in the
den's ``RuleStore`` (``cache/rules/<task>/r<round>/``). Cost: ``<den>/ledger.jsonl``, every
record tagged with its ``stage``."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import yaml

from .. import custom_prompts
from ..costs import ledger
from ..harness import links
from ..models import V5_MODELS_ENV
from ..resources import POLICIES
from . import acceptance, exam, oracle, readiness, repair, report, verify
from . import claims as claims_mod
from . import scope as scope_mod
from . import settings as settings_mod
from .common import (
    LEAN_STAGES,
    RETIRED_STAGES,
    STAGE_CAPS_ENV,
    STAGES,
    Budget,
    BudgetExceeded,
    is_lean,
    is_simple,
    prompt,
    prompt_home,
    stage_caps,
    to_lean,
    usd_or_none,
)
from .launch import launch
from .types import Prebuilt

VANILLA_SUFFIX = "Work in the current directory."  # bench/contestants.VANILLA_SUFFIX
DEFAULT_CAP = None  # no global cap unless one is set
CAP_ENV = "REPO_FACTORY_V5_MAX_USD"  # overrides rules.yaml max_usd
NO_CAP = ("none", "null", "unlimited", "off", "no")
MODELS_ENV = V5_MODELS_ENV  # stage models over rules.yaml's
# A model role -> the rules.yaml sections whose ``model`` it sets. ``build`` also writes the
# scope brief; ``verify`` also runs re-verify (it copies its caps).
MODEL_SECTIONS = {
    "build": [("build",)],
    "claims": [("claims",)],
    "verify": [("verify",)],
    "oracle": [("oracle",)],
    "repair": [("repair",)],
    "readiness": [("readiness", "security"), ("readiness", "checks"), ("readiness", "repair")],
}
# The lean flow's three roles: Build (the brief, the build and the harden session), Check
# (the security probe), Fix (the one fix session)
LEAN_MODEL_SECTIONS = {
    "build": [("build",), ("harden",)],
    "check": [("check", "security")],
    "fix": [("fix",)],
}
VANILLA_MULTIPLE = 2.95
SECOND_ROUND_USD = 2.0  # §3.1's reserve for repair round 2
REVERIFY_TIMEOUT_S = 400  # re-verify 4 min, with a margin
AUTH_REASON = (
    "Claude login expired: run `claude` once on this machine, or set up a "
    "long-lived token (`claude setup-token`, then CLAUDE_CODE_OAUTH_TOKEN in the "
    "service's environment)"
)
# A build session that stopped on one of these may have left a usable tree (a partial build)
STOPS = {
    "max_budget": "reached its dollar cap",
    "max_turns": "ran out of turns (max_turns)",
    "timeout": "ran out of time (timeout_s)",
}
# Not the product: the factory's and the session's own files in a build workspace
NOT_PRODUCT = {"SCOPE.md", "transcript.jsonl", "stderr.txt", ".gitignore"}


class StageFailed(RuntimeError):
    """A stage that can't go on: the build ends ``error`` with the stage and the reason, the
    stages after it don't run, and the step is not done (a retry resumes it). ``kind``:
    ``auth`` (the Claude login), ``stopped`` (turns, time or a cap, with no usable tree) or
    ``error``."""

    def __init__(self, stage: str, reason: str, kind: str = "error"):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason, self.kind = stage, reason, kind


def product_files(ws: Path, base: list[Path] | None = None) -> list[str]:
    """The files in a build workspace that are the product: everything but git, the
    environments, caches, ``NOT_PRODUCT`` and files identical to those in ``base`` (the seed
    and the user's tests, copied in before the session)."""
    from ..den.artifacts import ENV_DIRS

    ws = Path(ws)
    skip = {".git", "__pycache__", ".pytest_cache", "venv", "node_modules", *ENV_DIRS}
    out = []
    if not ws.is_dir():
        return out
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        if not p.is_file() or any(part in skip for part in rel.parts) or str(rel) in NOT_PRODUCT:
            continue
        same = False
        for b in base or []:
            q = Path(b) / rel
            try:
                same = same or (q.is_file() and q.read_bytes() == p.read_bytes())
            except OSError:
                pass
            q = (
                Path(b) / Path(*rel.parts[1:])
                if rel.parts[0] == "tests" and len(rel.parts) > 1
                else None
            )
            try:
                same = same or (q is not None and q.is_file() and q.read_bytes() == p.read_bytes())
            except OSError:
                pass
        if not same:
            out.append(str(rel))
    return out


def carry_from(den_root: Path, rid: int) -> Path | None:
    """For a fresh round: the round whose stopped build this one continues (Retry keeps the
    work). Rounds before ``rid``, newest first (a round the owner stopped with Stop round is
    passed over): one whose build stage stopped early (``cut_off``
    or a failed build stage) and left product files is the one; a round whose build stage
    finished means the owner asked for a new build, so nothing is carried; a stopped round
    with no product files (an auth failure at the start) is passed over."""
    for r in range(int(rid) - 1, 0, -1):
        root = Path(den_root) / "v5" / f"r{r}"
        if (root / "stopped.json").exists():  # the owner stopped it: only Resume continues it
            continue
        try:
            st = json.loads((root / "state.json").read_text())
        except (OSError, ValueError):
            continue
        stopped = bool(st.get("cut_off")) or (st.get("failed") or {}).get("stage") == "build"
        if not stopped:
            if "build" in (st.get("done") or []):
                return None
            continue
        if (root / "build" / ".git").is_dir() and product_files(root / "build"):
            return root
    return None


BASE_SUBJECTS = ("v5: before repair 1", "v5: before readiness", "v5: the base build")


def base_commit(round_dir: Path) -> str:
    """The round's build before any repair: the base its first repair round measured
    (``state.json``), else the newest commit from before its build stage ended (its
    ``v5_stage`` event), else the first of its ``BASE_SUBJECTS`` commits, else its head."""
    root = Path(round_dir)
    try:
        st = json.loads((root / "state.json").read_text())
    except (OSError, ValueError):
        st = {}
    for r in st.get("rounds") or []:
        if isinstance(r, dict) and r.get("base"):
            return str(r["base"])
    ws = root / "build"
    from ..events import read_events

    ended = None
    try:
        for e in read_events(root.parent.parent / "events.jsonl"):
            if (
                e.get("type") == "v5_stage"
                and e.get("stage") == "build"
                and e.get("status") == "done"
                and str(e.get("build")) == root.name[1:]
            ):
                ended = float(e.get("ts") or 0) or None
    except (OSError, ValueError):
        ended = None
    if ended:
        for ln in repair.git(ws, "log", "--format=%H %ct").splitlines():
            sha, _, ct = ln.partition(" ")
            if ct.isdigit() and int(ct) <= ended + 5:
                return sha
    log = [
        ln.split(" ", 1) for ln in repair.git(ws, "log", "--format=%H %s").splitlines() if " " in ln
    ]
    for subject in BASE_SUBJECTS:
        for sha, s in log:
            if s.startswith(subject):
                return sha
    return repair.head(ws)


def strip_envs(ws: Path) -> None:
    """Remove the environments a session left in a workspace (``.venv`` and friends: never
    part of the artifact, and a sandboxed ``uv sync`` leaves symlinks that don't resolve
    outside the sandbox, which breaks every later copy of the workspace)."""
    from ..den.artifacts import ENV_DIRS

    for name in (*ENV_DIRS, "venv"):
        p = Path(ws) / name
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def load_rules(policy: Path) -> dict:
    """``rules.yaml``, with the stage list checked against the fixed stage library (§5.7,
    tier 3: the list is data, the stages are code). A retired stage an older policy still
    lists (``RETIRED_STAGES``) is dropped. A policy's own prompt stages (``add_stages``,
    ``stages.added``) join the flow after the stage each names; they are kept, checked, in
    ``rules["added"]``."""
    from . import stages as stages_mod

    rules = yaml.safe_load((Path(policy) / "rules.yaml").read_text()) or {}
    flow = stages_mod.flow_of(rules)
    lib = stages_mod.library(flow)
    stages = [s for s in rules.get("stages") or lib if s not in RETIRED_STAGES]
    bad = [s for s in stages if s not in lib]
    if bad:
        raise ValueError(f"rules.yaml stages: unknown stage(s) {bad} (library: {list(lib)})")
    if "build" not in stages or "deliver" not in stages:
        raise ValueError("rules.yaml stages must include build and deliver")
    rules["stages"] = [s for s in lib if s in stages]  # the library's order
    have = {p.stem for p in (Path(policy) / "prompts").glob("*.md")}
    adds = [stages_mod.added(a, have) for a in rules.get("add_stages") or []]
    stages_mod.with_added(stages_mod.FLOWS[flow], adds)  # checked here, once
    rules["added"] = [{**st.to_dict(), "after": after} for st, after in adds]
    rules["stages"] += [st.name for st, _ in adds]
    rules = with_models(rules, os.environ.get(MODELS_ENV))
    if is_simple(rules):
        raise ValueError(
            "the simple flow is not part of repo-factory: use policies/v5 or policies/lean"
        )
    if is_lean(rules):
        from . import lean

        rules = lean.normalize(rules)
    return rules


def with_models(rules: dict, overlay: str | dict | None) -> dict:
    """``rules`` with the stage models the operator chose (Settings › Building › Models, the loop's
    ``build.models``, passed to the build as ``$REPO_FACTORY_V5_MODELS``, a JSON object of role
    to model): each role sets ``model`` in its sections. A role left out keeps the policy's
    model; ``null`` means the Claude Code CLI's default. Unknown roles are refused; a retired
    stage's role (a loop.yaml from before) is dropped."""
    if not overlay:
        return rules
    models = json.loads(overlay) if isinstance(overlay, str) else overlay
    if not isinstance(models, dict):
        raise ValueError(f"{MODELS_ENV}: expected a JSON object of role to model")
    models = {k: v for k, v in models.items() if k not in RETIRED_STAGES}
    sections = MODEL_SECTIONS
    if is_lean(rules):  # verify and readiness→check, repair→fix
        sections = LEAN_MODEL_SECTIONS
        models = to_lean(models)
    bad = sorted(set(models) - set(sections))
    if bad:
        raise ValueError(f"{MODELS_ENV}: unknown role(s) {bad} (roles: {list(sections)})")
    for role, model in models.items():
        if model is not None and not isinstance(model, str):
            raise ValueError(f"{MODELS_ENV}: {role} must be a model name or null")
        for path in sections[role]:
            node = rules
            for key in path:
                node = node.setdefault(key, {})
            node["model"] = model or None
    return rules


def default_cap(rules: dict) -> float | None:
    """The build's global cap when the caller gives none (no ``--max-cost``, no task
    ``budget_usd``): ``$REPO_FACTORY_V5_MAX_USD`` if set (a positive number; ``none`` for no
    cap), else ``rules.yaml``'s ``max_usd`` (``null``: no cap, the default), else
    ``DEFAULT_CAP`` (None). None means unlimited: the stage and session caps still hold."""
    v = os.environ.get(CAP_ENV, "").strip().lstrip("$")
    if v:
        if v.lower() in NO_CAP:
            return None
        try:
            if float(v) > 0:
                return float(v)
        except ValueError:
            raise ValueError(f"{CAP_ENV}={v!r}: give a number of dollars, or none") from None
    m = rules.get("max_usd", DEFAULT_CAP)
    if m is None or str(m).strip().lower() in NO_CAP or float(m) <= 0:
        return None
    return float(m)


def build_cap(max_usd, task_budget, rules: dict, vanilla_usd=None) -> float | None:
    """The build's global cap: ``--max-cost``, else the task's ``budget_usd``, else
    ``default_cap``; the bench's 2.95x vanilla caps it further. None: no cap."""
    cap = float(max_usd) if max_usd else float(task_budget) if task_budget else default_cap(rules)
    if vanilla_usd:
        v = VANILLA_MULTIPLE * float(vanilla_usd)
        cap = v if cap is None else min(cap, v)
    return cap


def product_name(root: Path, scope_md: str | None, fallback: str) -> str:
    """The product's name for the user-facing files: SCOPE.md's ``# Scope: <name>``, else
    the README's first heading (up to a dash or colon), else ``fallback``."""
    m = re.search(r"^#\s*Scope:\s*(.+)$", scope_md or "", re.M)
    if m:
        return m.group(1).strip().split(" — ")[0].strip()
    for p in sorted(Path(root).glob("README*")):
        if p.is_file():
            m = re.search(r"^#\s+(.+)$", p.read_text(errors="replace"), re.M)
            if m:
                return re.split(r"\s+[—–-]\s+|:\s", m.group(1).strip())[0].strip("` ")
    return fallback


def is_v5(policy_dir: Path) -> bool:
    p = Path(policy_dir) / "rules.yaml"
    try:
        return p.exists() and (yaml.safe_load(p.read_text()) or {}).get("pipeline") == "v5"
    except yaml.YAMLError:
        return False


def build(
    taskdir: Path,
    *,
    den: Path | None = None,
    owner: str = "none",  # none | human | queue | sim
    policy: Path = POLICIES / "v5",
    max_usd: float | None = None,
    out: Path | None = None,
    prebuilt: Prebuilt | None = None,
    lessons: Path | None = None,
    exclude_lessons_born_on: str | None = None,
    subscribers: list | None = None,
    harness=None,
    llm=None,
    vanilla_usd: float | None = None,
    resume: int | None = None,
    steps: int | None = None,
    carry: bool = True,
    from_round: Path | None = None,
) -> dict:
    """One v5 build. ``harness``/``llm``: test doubles (a FakeHarness-like
    ``run(prompt, workspace, timeout_s, **opts)``; ``rules.llm`` backends). ``vanilla_usd``:
    the pack's vanilla mean (the bench), which caps the build at 2.95x.

    Resumable: the run's state is saved after every step (a stage, or one repair round or
    re-verify) to ``<den>/v5/r<round>/state.json``. ``steps``: run at most that many steps,
    then return (``finished: False``); ``resume``: continue round ``resume`` from its state
    (``prebuilt``, ``max_usd`` and ``out`` then come from the state).

    A stage that can't go on (``StageFailed``: the Claude login failed, or the build stage
    stopped without a usable tree) ends the build ``error`` with the stage and the reason;
    nothing after it runs and nothing is delivered. Resuming the round runs that stage again.
    A fresh round continues an earlier round's stopped build (``carry_from``: its scope and
    its committed tree) unless ``carry`` is False (``repo-factory build --fresh``).

    ``from_round``: an earlier round's folder (``<den>/v5/r<N>``, of either flow, this task's
    or a copy of another's): its plan and its build before any repair are this round's, and
    the round starts after the build (``repo-factory build --prebuilt``: two flows
    compared on one plan and one build). The policy's ``flow: lean`` runs the lean flow
    (``lean.LeanRun``)."""
    from ..den.store import Den
    from ..events import EventLog, log_path
    from ..tasks import load_task
    from . import stages as stages_mod
    from .workspace import lifecycle, spec_title

    taskdir = Path(taskdir).resolve()
    task, cfg = load_task(taskdir)
    rules = load_rules(Path(policy))
    d = Den(den or taskdir / ".den")
    d.add_task(task)
    saved = None
    if resume is not None:
        rid = int(resume)
        saved = json.loads((Path(d.root) / "v5" / f"r{rid}" / "state.json").read_text())
        was = saved.get("flow") or "v5"
        if was != stages_mod.flow_of(rules):
            # a round resumes in the flow it started in, whatever build.flow says now
            policy = POLICIES / was
            rules = load_rules(Path(policy))
        out = Path(saved["out"])
        cap = float(saved["cap"]) if saved.get("cap") is not None else None
    else:
        rid = d.new_round(f"v{rules.get('version', '5.0')}", [task.id], "build")
        out = Path(out).resolve() if out else taskdir / "out"
        cap = build_cap(max_usd, getattr(cfg, "budget_usd", None), rules, vanilla_usd)
    log = EventLog(log_path(d.root), task.id, rid, subscribers)
    caps = stage_caps(rules, os.environ.get(STAGE_CAPS_ENV))
    flow = stages_mod.flow_of(rules)
    if flow == "lean":
        from .lean import LeanRun as cls
    else:
        cls = Run
    lessons_at = lessons or prompt_home(Path(policy), "claims") / "lessons.yaml"
    run = cls(
        taskdir,
        task,
        cfg,
        d,
        rid,
        log,
        rules,
        Path(policy),
        Budget(cap, d.root, rid, stage_caps=caps, stages=LEAN_STAGES if flow == "lean" else STAGES),
        owner,
        out,
        prebuilt,
        harness,
        llm,
        claims_mod.load_lessons(lessons_at, exclude_lessons_born_on),
        fresh=saved is None,
    )
    if saved is not None:
        run.load(saved)
    elif from_round is not None:
        run.adopt_round(Path(from_round))
    elif prebuilt is None and carry:
        # Retry keeps the work: a fresh round after a stopped build continues its tree
        carried = carry_from(d.root, rid)
        run.carried = str(carried) if carried else None
    t0 = time.time() - float((saved or {}).get("wall_s") or 0.0)
    with (
        ledger.activate(ledger.ledger_path(d.root), rid, events=log),
        lifecycle(d, log, t0, run.budget.spent),
    ):
        if saved is None:
            log.emit(
                "build_started",
                spec=str(taskdir / "spec.md"),
                taskdir=str(taskdir),
                title=spec_title(task.spec),
                policy=f"v{rules.get('version', '5.0')}",
                harness=getattr(harness, "name", "claude-code"),
                owner=owner,
                out=str(out),
                pid=os.getpid(),
                pipeline="v5",
                cap_usd=cap,
                stage_caps=run.budget.stage_caps,
                prebuilt=prebuilt is not None or from_round is not None,
                stages=rules["stages"],
                flow=flow,
                flow_stages=[s.to_dict() for s in run.flow_stages()],
                **({"from_round": str(run.adopted)} if run.adopted else {}),
                prompts=custom_prompts.build_versions(Path(policy)),
                factory_version=exam.factory_version(),
                **({"continues": Path(run.carried).name} if run.carried else {}),
            )
        else:  # the round runs again, in this process (docs/events.md)
            log.emit(
                "build_resumed",
                pid=os.getpid(),
                done=list(run.done),
                **({"retries": run.failed["stage"]} if run.failed else {}),
            )
        finished = run.go(steps, t0)
        wall = round(time.time() - t0, 1)
        s = ledger.summarize(ledger.read(d.root, rid))
        cost = run.budget.spent()
        totals = dict(
            cost_usd=round(cost, 4),
            wall_s=wall,
            worker_cost_usd=round(run.worker_usd, 4),
            cost_breakdown=s["breakdown"],
            cost_by_stage=run.budget.by_stage(),
            **({"worker_models": sorted(run.models)} if run.models else {}),
        )
        if finished and run.failed:
            # a stage that could not go on: failed, not delivered (the loop holds it with
            # Retry, which resumes this round at that stage)
            f = run.failed
            log.emit(
                "build_finished",
                status="error",
                stage=f["stage"],
                failure=f["kind"],
                error=f"{f['stage']} stage: {f['reason']}",
                **totals,
            )
        elif finished:
            log.emit("build_finished", status="ok", **totals)
    return {
        "task": task.id,
        "round": rid,
        "delivered": run.delivered,
        "branch": run.branch,
        "out": str(out),
        "den": str(d.root),
        "cost_usd": round(cost, 4),
        "events": str(log.path),
        "wall_s": wall,
        "pipeline": "v5",
        "finished": finished,
        "done_steps": list(run.done),
        "cost_by_stage": run.budget.by_stage(),
        "cost_breakdown": s["breakdown"],
        "cap_usd": cap,
        "checked": run.checked,
        "alt_branches": run.alt_branches,
        "claims": str(run.root / "claims.json"),
        "findings": str(run.root / "findings.json"),
        "stop": run.stop,
        "scope": run.scope,
        "completeness": run.completeness(),
        "readiness": run.readiness_summary(),
        "failed": run.failed,
        "partial": run.partial,
    }


class Run:
    """One v5 build's state; also the context ``repair.run_round`` works through."""

    def __init__(
        self,
        taskdir,
        task,
        cfg,
        den,
        rid,
        log,
        rules,
        policy,
        budget,
        owner,
        out,
        prebuilt,
        harness,
        llm,
        lessons,
        fresh: bool = True,
    ):
        from ..rules.llm import ClaudeLLM
        from ..rules.store import RuleStore, store_root

        self.taskdir, self.task, self.cfg, self.den, self.rid = taskdir, task, cfg, den, rid
        self.log, self.rules, self.policy, self.budget = log, rules, policy, budget
        self.owner, self.out, self.prebuilt, self.harness = owner, out, prebuilt, harness
        self.llm = llm or ClaudeLLM((rules.get("claims") or {}).get("model"))
        self.panel_llm = llm or ClaudeLLM((rules.get("oracle") or {}).get("model"))
        # the scope call uses the builder's model (null: the CLI default)
        self.scope_llm = llm or ClaudeLLM((rules.get("build") or {}).get("model"), timeout_s=900)
        self.lessons = lessons
        self.idea = task.spec  # the owner's input, as given
        self.spec = task.spec  # what every stage reads: + SCOPE.md
        self.scope: dict = {}  # the scope stage's record (scope.json)
        self.root = Path(den.root) / "v5" / f"r{rid}"
        if fresh and self.root.exists():
            shutil.rmtree(self.root)
        (self.root / "transcripts").mkdir(parents=True, exist_ok=True)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir(exist_ok=True)
        self.ws = self.root / "build"
        self.vws = self.root / "verify"
        # EX-PLAN §3: the exam lives outside the product. ``exam``: the verifier writes its
        # driver and scenarios into ``verify-exam/_verify`` (bound at /work/exam) and every
        # run lays the product at /work/product. ``inside``: a round from before (its
        # scenarios find the repository from their own path inside it); its runs keep that
        # layout, outside the delivered tree all the same.
        self.layout = "exam"
        self._exam_mount: tuple | None = None  # (host dir, read_only) for a repair
        self._final: dict | None = None  # the delivered tree's exam record
        self.store = RuleStore(
            store_root(den.cache, task.id, rid),
            held_fraction=float(self.cfg_of("verify").get("held_fraction", 0.3)),
        )
        self.store.on_change = self._rule_event
        self.helpers: Path | None = None
        self._venv = None
        self.claims: dict = {"claims": [], "ambiguities": []}
        self.findings: dict = {
            "scenarios": [],
            "blocked": [],
            "ambiguities": [],
            "self_estimate": {},
        }
        self.status: dict[str, str] = {}  # rule id -> status on the current head
        self.panel: dict[str, dict] = {}
        self.defects: list[str] = []  # confirmed defect rule ids, in order
        self.interpretations: list[dict] = []
        self.interp_repairs: list[dict] = []
        self.settings: list[dict] = []  # Addendum A: binary items as settings
        self.alt_branches: list[dict] = []  # the fork is retired: always empty
        self.rounds: list[dict] = []
        self.checked: dict | None = None  # what delivery counted (checked_counts)
        self.answers: dict = {}
        self.builder_session: dict | None = None  # {"id", "cost"}
        self.repair_session: dict | None = None
        self.verifier_session: dict | None = None
        self.candidates = 0
        self.delivered: int | None = None
        self.branch = f"repo-factory/{cfg.id}"
        self.cut_off = False  # the build session stopped early
        self.failed: dict | None = None  # {"stage", "reason", "kind"}: not delivered
        self.partial: dict | None = None  # a stopped build kept: {"exit_reason", ...}
        self.build_failures = 0  # failed build attempts in this round
        self.carried: str | None = None  # the earlier round this one continues
        self.adopted: str | None = None  # repo-factory build --prebuilt: its round
        self.stop: str | None = None
        self.models: set[str] = set()
        self.worker_usd = 0.0
        # a fix-up or undo session's own cap; None (the default): none
        self.fixup_usd = usd_or_none(self.cfg_of("repair").get("fixup_usd"), "repair.fixup_usd")
        self._prebuilt_flag = prebuilt is not None
        self.done: list[str] = []  # finished steps (resumable runs)
        self.rs: dict = {"next": None}  # the repair loop's state
        self.ready: dict = readiness.new_record()  # the readiness stage's record
        self._measures: dict = {}
        self._stage_tools: dict[str, dict] = {}  # stage -> MCP calls per server
        self._stage_skills: dict[str, dict] = {}  # stage -> {"skills", "agents"} called
        self._stage_why: dict[str, str] = {}  # stage -> a note on a stage that ran
        self.session_choice: dict | None = None  # the last repair round's resume choice

    # -- state (a resumable run) -------------------------------------------------------------
    STATE = (
        "claims",
        "findings",
        "status",
        "panel",
        "defects",
        "interpretations",
        "interp_repairs",
        "settings",
        "rounds",
        "checked",
        "answers",
        "builder_session",
        "repair_session",
        "verifier_session",
        "candidates",
        "delivered",
        "cut_off",
        "stop",
        "worker_usd",
        "done",
        "rs",
        "scope",
        "ready",
        "failed",
        "partial",
        "build_failures",
        "carried",
        "layout",
        "adopted",
    )

    def save(self, wall_s: float = 0.0) -> None:
        st = {k: getattr(self, k) for k in self.STATE}
        st.update(
            flow=self.FLOW,
            models=sorted(self.models),
            hit=self.budget.hit,
            cap=self.budget.cap,
            windows=self.budget.windows,
            out=str(self.out),
            wall_s=round(wall_s, 1),
            prebuilt=self.prebuilt is not None,
            helpers=str(self.helpers) if self.helpers else None,
            measures=[[k[0], k[1], v] for k, v in self._measures.items()],
        )
        tmp = self.root / "state.json.tmp"
        tmp.write_text(json.dumps(st, default=str))
        tmp.replace(self.root / "state.json")

    def load(self, st: dict) -> None:
        for k in self.STATE:
            if k in st:
                setattr(self, k, st[k])
        if not st.get("layout"):
            # a round saved before the exam moved out: its scenarios keep their layout,
            # unless it has none yet (it resumes before its verifier ran)
            self.layout = (
                "inside"
                if (st.get("findings") or {}).get("scenarios") or "verify" in (st.get("done") or [])
                else "exam"
            )
        self.models = set(st.get("models") or [])
        self.budget.hit = list(st.get("hit") or [])
        self.budget.windows = dict(st.get("windows") or {})
        self.helpers = Path(st["helpers"]) if st.get("helpers") else None
        self._measures = {(a, b): v for a, b, v in st.get("measures") or []}
        self._prebuilt_flag = bool(st.get("prebuilt"))
        self.scope = self.scope or {}
        self.ready = {**readiness.new_record(), **(self.ready or {})}
        self.spec = scope_mod.spec_text(self.idea, self.scope_md())

    def scoped(self) -> bool:
        return self.scope.get("decision") == "scoped"

    def scope_md(self) -> str | None:
        p = self.root / scope_mod.FILE
        return p.read_text() if self.scoped() and p.exists() else None

    # -- plumbing --------------------------------------------------------------------------
    def cfg_of(self, name: str) -> dict:
        return self.rules.get(name) or {}

    def emit(self, type: str, **fields) -> None:
        self.log.emit(type, **fields)

    def _rule_event(self, r, old, new, reason) -> None:
        self.emit(
            "rule_status",
            id=r.id,
            kind=r.kind,
            title=r.title,
            milestones=[],
            visibility=r.visibility,
            version=r.version,
            frm=old,
            to=new,
            reason=str(reason)[:300],
            scenario=r.origin.get("scenario"),
        )

    @property
    def venv(self) -> Path:
        if self._venv is None:
            from ..harness.venv import candidate_venv

            self._venv = candidate_venv(self.ws, list(self.cfg.requirements))[0]
        return self._venv

    @property
    def vex(self) -> Path:
        """The verifier's exam workspace (bound at /work/exam): ``verify-exam``; for a round
        of the ``inside`` layout, its copy of the build (``_verify/`` inside it)."""
        return self.vws if self.layout == "inside" else self.root / "verify-exam"

    def runner(self, code_dir: Path, flake: bool = False):
        """``run(rules, reverse) -> outcomes`` on ``code_dir``: the exam's record of that
        tree (``exam.run``, one sandbox run per tree hash and rule set). ``flake``: the
        rerun protocol; the flaky scenarios it finds are retired (``exam.absorb``)."""
        code_dir = Path(code_dir)

        def go(rules, reverse=False):
            rec = exam.run(self, code_dir, rules=list(rules), reverse=reverse, flake=flake)
            if flake:
                exam.absorb(self, rec)
            return exam.outcomes_of(rec)

        return go

    def stage(self, name: str, fn) -> None:
        if name not in self.rules["stages"]:
            self.emit(
                "v5_stage",
                stage=name,
                status="skipped",
                usd=0.0,
                wall_s=0.0,
                tokens_in=0,
                tokens_out=0,
            )
            return
        t0, usd0 = time.time(), self.budget.by_stage().get(name, 0.0)
        tok0 = self.budget.tokens_by_stage().get(name, (0, 0))
        self.emit("v5_stage", stage=name, status="started")
        status, why, failed = "done", None, None
        try:
            with ledger.tags(stage=name), custom_prompts.collect() as used:
                fn()
        except BudgetExceeded as e:
            status, why = "capped", str(e)
        except StageFailed as e:
            status, why, failed = "failed", e.reason, e
        why = why or self._stage_why.pop(name, None)
        tok1 = self.budget.tokens_by_stage().get(name, (0, 0))
        tbs = self._stage_tools.pop(name, None)
        ran = self._stage_skills.pop(name, None) or {}
        self.emit(
            "v5_stage",
            stage=name,
            status=status,
            usd=round(self.budget.by_stage().get(name, 0.0) - usd0, 4),
            tokens_in=tok1[0] - tok0[0],
            tokens_out=tok1[1] - tok0[1],
            wall_s=round(time.time() - t0, 1),
            **({"why": why} if why else {}),
            **({"prompts": used} if used else {}),
            **({"tools_by_server": tbs} if tbs else {}),
            **{k: v for k, v in ran.items() if v},
        )
        if failed is not None:
            raise failed

    def session(
        self,
        role: str,
        text: str,
        ws: Path,
        stage: str,
        caps: dict,
        purpose: str,
        resume: dict | None = None,
        caller: str = "worker",
        exam_mount: tuple | None = None,
    ):
        """One Claude Code session through ``launch``; its cost is recorded in the ledger
        under ``stage``. Returns ``(result, {"id", "cost"})``. ``exam_mount``: ``(host dir,
        read_only)`` bound at /work/exam (and, read-write, ``(dir, False, proposals dir)``);
        a read-only exam is hashed before and after (``exam.check_unchanged``)."""
        done = self.finished_session(stage, purpose)
        if done is not None:  # a resumed step whose session already ran
            return done
        # None: nothing caps this session (no max_usd, stage cap or global cap), so it runs
        # without --max-budget-usd; max_turns and timeout_s stop a runaway
        cap = self.budget.session_cap(stage, usd_or_none(caps.get("max_usd"), f"{stage}.max_usd"))
        if cap is not None and cap < 0.05:
            self.budget._hit(stage)
            raise BudgetExceeded(f"{stage}: no budget left for a {role} session")
        sid = str(uuid.uuid4())
        sess = {"id": sid}
        if resume and resume.get("id"):
            sess.update(resume=resume["id"], base_cost_usd=resume.get("cost") or 0.0)
        t0 = time.time()
        lc = self.launch_for(stage, purpose, exam_mount)
        ro_exam = Path(exam_mount[0]) if exam_mount and exam_mount[1] else None
        before = exam.digest(ro_exam) if ro_exam is not None else None
        timeout_s = int(caps.get("timeout_s") or 1800)
        res = launch(
            text,
            ws,
            config_dir=self.root / "cfg" / role,
            max_turns=int(caps.get("max_turns") or 60),
            max_usd=cap,
            timeout_s=timeout_s,
            session=sess,
            model=caps.get("model"),
            harness=self.harness,
            spec=lc,
        )
        if getattr(res, "resume_failed", False) and resume:
            res = launch(
                text,
                ws,
                config_dir=self.root / "cfg" / role,
                max_turns=int(caps.get("max_turns") or 60),
                max_usd=cap,
                timeout_s=timeout_s,
                session={"id": sid},
                model=caps.get("model"),
                harness=self.harness,
                spec=lc,
            )
        tbs = getattr(res, "tools_by_server", None) or {}
        acc = self._stage_tools.setdefault(stage, {})
        for k, n in tbs.items():
            acc[k] = acc.get(k, 0) + int(n or 0)
        ran = self._stage_skills.setdefault(stage, {"skills": {}, "agents": {}})
        for kind in ("skills", "agents"):
            for k, n in (getattr(res, kind, None) or {}).items():
                ran[kind][k] = ran[kind].get(k, 0) + int(n or 0)
        for name in ("transcript.jsonl", "stderr.txt"):
            p = Path(ws) / name
            if p.exists():
                shutil.move(str(p), self.root / "transcripts" / f"{purpose}.{name}")
        if Path(ws) != self.vws:
            strip_envs(Path(ws))
        usd = float(res.cost_usd or 0.0)
        # tokens: the harness's ``usage`` is this run's own (also for a resumed session; only
        # the dollars are cumulative, harness.claude_code.parse_stream), so no base to subtract
        ledger.record(
            caller,
            stage=stage,
            purpose=purpose,
            usd=res.cost_usd,
            model=res.model,
            in_tokens=res.input_tokens,
            out_tokens=res.output_tokens,
            session_usd=res.session_cost_usd,
            wall_s=round(time.time() - t0, 3),
            ok=bool(res.exit_ok),
            exit_reason=res.exit_reason,
            turns=res.num_turns,
            session=res.session_id or sid,
            **({"base_usd": sess["base_cost_usd"]} if "resume" in sess else {}),
            **({"mcp_calls": dict(tbs)} if tbs else {}),
            **{k: getattr(res, k) for k in ("skills", "agents") if getattr(res, k, None)},
        )
        if res.model:
            self.models.add(res.model)
        if caller == "worker":
            self.worker_usd += usd
        if res.exit_reason == "max_budget":
            self.budget._hit(stage)
        if getattr(res, "auth_failed", False):
            # the launch already re-copied the login and retried once (profile.launch)
            raise StageFailed(stage, AUTH_REASON, "auth")
        if ro_exam is not None:
            try:
                exam.check_unchanged(ro_exam, before, stage)
            except StageFailed:
                self.emit(
                    "exam_tampered", stage=stage, purpose=purpose, fingerprint="exam/tampered"
                )
                raise
        return res, {
            "id": res.session_id or sid,
            "cost": res.session_cost_usd if res.session_cost_usd is not None else usd,
        }

    def traits(self) -> list | None:
        """The product's traits (web, cli, library, api) from the scope stage, None before
        it ran. A round whose scope did not record them reads them from its scope now."""
        if "traits" in (self.scope or {}):
            return list(self.scope.get("traits") or [])
        if "scope" not in self.done and not self.scope:
            return None
        return scope_mod.traits(self.idea, self.scope_md())["traits"]

    def launch_for(self, stage: str, purpose: str, exam_mount: tuple | None = None):
        """The session's ``Launch``: the workspace at /work/product; ``exam_mount`` (see
        ``session``) at /work/exam."""
        from ..harness.launch import Launch

        lc = Launch(workdir=exam.PRODUCT_AT, env={"REPO_FACTORY_PRODUCT_DIR": exam.PRODUCT_AT})
        if exam_mount is not None:
            proposals = exam_mount[2] if len(exam_mount) > 2 else None
            lc.mounts = [
                *lc.mounts,
                *exam.session_mounts(exam_mount[0], not exam_mount[1], proposals),
            ]
            lc.env["REPO_FACTORY_EXAM_DIR"] = exam.EXAM_AT
            if exam_mount[1]:  # the exam is read-only
                lc.env["REPO_FACTORY_EXAM_RO"] = "1"
        return lc

    def finished_session(self, stage: str, purpose: str):
        """On a resumed run, a session this step already ran (its ledger record and
        transcript exist: the step died after it, e.g. in the harness's own run). Its
        result is rebuilt from the record, so a crash never pays for a session twice."""
        recs = [
            r
            for r in ledger.read(self.den.root, self.rid)
            if r.get("stage") == stage and r.get("purpose") == purpose and r.get("session")
        ]
        t = self.root / "transcripts" / f"{purpose}.transcript.jsonl"
        if not recs or not t.exists() or recs[-1].get("exit_reason") == "auth":
            return None  # (a login failure: the retry runs it again)
        from ..harness.base import HarnessResult

        r = recs[-1]
        res = HarnessResult(
            artifact_dir=Path(self.ws),
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            transcript_path=t,
            exit_ok=bool(r.get("ok")),
            exit_reason=r.get("exit_reason") or "ok",
            session_id=r["session"],
            session_cost_usd=r.get("session_usd"),
            model=r.get("model"),
            num_turns=r.get("turns"),
        )
        cost = r.get("session_usd") if r.get("session_usd") is not None else r.get("usd")
        return res, {"id": r["session"], "cost": cost}

    # -- the stages ------------------------------------------------------------------------
    FLOW = "v5"  # the flow's stage list (stages.FLOWS)

    def flow_stages(self) -> tuple:
        """This build's stage list: the flow's (``stages.FLOWS``) with the policy's own
        prompt stages (``rules.yaml`` ``add_stages``) after the stages they name."""
        import dataclasses

        from . import stages as stages_mod

        fields = {f.name for f in dataclasses.fields(stages_mod.Stage)} - {"extra"}
        adds = [
            (
                stages_mod.Stage(
                    **{
                        k: (tuple(v) if k in ("inputs", "outputs") else v)
                        for k, v in a.items()
                        if k in fields
                    },
                    extra={"anchor": a.get("anchor")},
                ),
                a["after"],
            )
            for a in self.rules.get("added") or []
        ]
        return stages_mod.with_added(stages_mod.FLOWS[self.FLOW], adds)

    @property
    def LOOPS(self) -> tuple:  # noqa: N802 - read as the old constant
        """The steps that run several times, saved in between (``Stage.loop``)."""
        return tuple(s.name for s in self.flow_stages() if s.loop)

    def steps(self) -> list[tuple]:
        """The flow's steps in order, from its stage list: ``(name, fn)``; a step in
        ``LOOPS`` runs until its ``fn`` returns True. A recorded stage runs inside
        ``stage`` (one ``v5_stage`` record; skipped when the policy's ``stages`` leaves it
        out); a prompt stage is one session (``prompt_stage``)."""
        out = []
        for s in self.flow_stages():
            fn = getattr(self, s.code) if s.code else (lambda s=s: self.prompt_stage(s))
            out.append((s.name, (lambda s=s, fn=fn: self.stage(s.name, fn)) if s.record else fn))
        return out

    def readiness_step(self) -> bool:
        return readiness.step(self)

    def prompt_stage(self, s) -> None:
        """A prompt stage (``stages.Stage.prompt``): one Claude Code session in the build
        workspace on ``prompts/<prompt>.md`` (``<<spec>>``: the idea and SCOPE.md;
        ``<<flows>>``: the definition of done, one line a flow), with the stage's caps
        (``rules.yaml``'s section of its name over the stage's own) and its role's model.
        Its work is committed; what it wrote under ``.repo-factory/`` moves to the round folder
        (``process.collect``). A stage that doesn't hold (``holds: false``) and stops
        without finishing is recorded and the build goes on with the tree as it is."""
        from . import process

        own = self.cfg_of(s.name)
        caps = {
            "max_turns": s.max_turns or 60,
            "timeout_s": s.timeout_s or 1800,
            "max_usd": s.cap_usd,
            "model": self.cfg_of(s.role or "build").get("model"),
            **{k: v for k, v in own.items() if v is not None or k == "max_usd"},
        }
        text = prompt(self.policy, s.prompt, spec=self.spec.strip(), flows=self.dod_lines())
        repair.ensure_repo(self.ws, f"v5: before {s.name}")
        try:
            res, _ = self.session("builder", text, self.ws, s.name, caps, s.name)
        finally:
            strip_envs(self.ws)
            process.collect(self.ws, self.root, [o for o in s.outputs if "/" not in o])
        repair.ensure_repo(self.ws, f"v5: {s.name}")
        ok = res.exit_ok and res.exit_reason == "ok"
        if not ok:
            why = f"the {s.name} session {STOPS.get(res.exit_reason, 'ended with an error')}"
            if s.holds:
                raise StageFailed(s.name, why, "stopped" if res.exit_reason in STOPS else "error")
            self._stage_why[s.name] = why + "; the build goes on with the tree as it is"

    def dod_lines(self) -> str:
        """The definition of done, one line a flow (``- F1: title``), or a line saying
        there is none."""
        dod = (self.scope or {}).get("dod") or []
        return (
            "\n".join(f"- {f['id']}: {f['title']}" for f in dod)
            or "(no definition of done: take the flows the spec above describes)"
        )

    def go(self, steps: int | None = None, t0: float | None = None) -> bool:
        """Run the steps not done yet (at most ``steps``), saving the state after each.
        Returns True when the build is over: delivered, or ``failed`` (a ``StageFailed``: the
        step stays not done, so a resumed round (Retry) runs it again)."""
        t0 = time.time() if t0 is None else t0
        self._t0 = t0
        if "build" in self.done and self.PLAN_STAGE not in self.done:
            self.done.insert(0, self.PLAN_STAGE)  # a run saved before the scope stage
        self.failed = None  # a retry: the failed step runs again
        plan = self.steps()
        loops = self.LOOPS
        n = 0
        for name, fn in plan:
            if name in self.done:
                continue
            if (
                self.cut_off
                and self.partial is None
                and "build" in self.done
                and name not in ("deliver",)
            ):
                # a round saved before failed build stages (cut off and delivered as it was)
                self.stop = "the base build stopped early (cap, turns or error)"
                self.done.append(name)
                continue
            while True:
                if steps is not None and n >= steps:
                    self.save(time.time() - t0)
                    return False
                try:
                    over = fn()
                except StageFailed as e:
                    self.failed = {"stage": e.stage, "reason": e.reason, "kind": e.kind}
                    if e.stage == "build":
                        self.build_failures += 1
                    self.stop = f"the {e.stage} stage failed: {e.reason}"
                    self.save(time.time() - t0)
                    return True
                n += 1
                if name not in loops or over:
                    break
                self.save(time.time() - t0)
            self.done.append(name)
            self.save(time.time() - t0)
        return True

    def do_scope(self) -> None:
        """Stage 0 (``scope.py``): an idea gets ``SCOPE.md``, a v1 product brief, from one
        no-tools call with the builder's model; a spec passes through unchanged. A scoped
        build can't share the vanilla base build (its prompt isn't the vanilla prompt), so a
        prebuilt base is used only when the input passes through (``scope: always`` builds
        afresh instead)."""
        mode = str(self.rules.get("scope") or "auto")
        prior = Path(self.carried) if self.carried else None
        if self.adopted:
            res = self.adopt_plan()
        elif prior is not None and (prior / "scope.json").exists():
            # continuing an earlier round's stopped build: its scope, which its tree was
            # built to (a new scope call could describe another product)
            res = json.loads((prior / "scope.json").read_text())
            md = prior / scope_mod.FILE
            res["md"] = md.read_text() if res.get("decision") == "scoped" and md.exists() else None
            res["why"] = f"continues round {prior.name[1:]}'s stopped build: its scope"
        elif self.prebuilt is not None and mode != "always":
            res = {
                "mode": mode,
                "decision": "pass",
                "called": False,
                "md": None,
                "heuristic": scope_mod.classify(self.idea),
                "why": "a shared (prebuilt) base build: the vanilla prompt",
            }
        elif not (prompt_home(self.policy, "scope") / "prompts" / "scope.md").exists():
            res = {
                "mode": mode,
                "decision": "pass",
                "called": False,
                "md": None,
                "heuristic": scope_mod.classify(self.idea),
                "why": "this policy has no prompts/scope.md",
            }
        else:
            res = scope_mod.run(
                self.scope_llm,
                self.policy,
                self.idea,
                mode,
                self.budget,
                size=self.rules.get("scope_size") or None,
                extra=self.scope_rules(),
            )
        scoped = res["decision"] == "scoped"
        if not scoped and not res.get("dod"):
            # EV7: a spec passed through unchanged gets its definition of done from its own
            # text, so completeness is scored; with nothing to derive it is a gap (gate.py)
            dod = scope_mod.spec_dod(self.idea)
            if dod:
                res.update(dod=dod, dod_from="spec")
        res["shared_base"] = not scoped
        if "traits" not in res:  # what kind of product it is
            t = scope_mod.traits(self.idea, res.get("md") if scoped else None)
            res.update(traits=t["traits"], traits_from=t["from"])
        scope_mod.write(self.root, res)
        self.scope = scope_mod.record(res)
        self.spec = scope_mod.spec_text(self.idea, res.get("md") if scoped else None)
        h = res.get("heuristic") or {}
        self.emit(
            "scope",
            decision=res["decision"],
            why=res.get("why"),
            mode=res["mode"],
            verdict=h.get("verdict"),
            words=h.get("words"),
            called=res.get("called"),
            flows=len(res.get("flows") or []),
            dod=len(res.get("dod") or []),
            must=len(res.get("must") or []),
            settings=len(res.get("settings") or []),
            shared_base=not scoped,
            traits=res.get("traits"),
            size=res.get("size"),
            path=str(self.root / scope_mod.FILE) if scoped else None,
        )

    # -- repo-factory build --prebuilt: an earlier round's plan and build ------------------------------
    def adopt_round(self, src: Path) -> None:
        """This round takes ``src``'s plan and build (``build(from_round=...)``): checked
        here, copied by the plan and build steps (``adopt_plan``, ``adopt_build``)."""
        src = Path(src).resolve()
        if not (src / "build" / ".git").is_dir() or not (src / "scope.json").is_file():
            raise ValueError(f"--prebuilt {src}: not a round folder (no build/.git or scope.json)")
        self.adopted = str(src)

    def adopted_usd(self, stages: tuple) -> float:
        """What the adopted round's ``stages`` cost (its den's ledger)."""
        src = Path(self.adopted)
        try:
            recs = ledger.read(src.parent.parent, int(src.name[1:]))
        except (OSError, ValueError):
            return 0.0
        return round(sum(float(r.get("usd") or 0.0) for r in recs if r.get("stage") in stages), 4)

    def adopt_plan(self) -> dict:
        """The adopted round's scope (``scope.json``, ``SCOPE.md``, ``STUDY.md``), its cost
        recorded once as prebuilt."""
        src = Path(self.adopted)
        res = json.loads((src / "scope.json").read_text())
        md = src / scope_mod.FILE
        res["md"] = md.read_text() if res.get("decision") == "scoped" and md.exists() else None
        res["why"] = f"the plan of {src.parent.parent.parent.name} round {src.name[1:]} (prebuilt)"
        if (src / "STUDY.md").is_file():
            shutil.copy2(src / "STUDY.md", self.root / "STUDY.md")
        ledger.record(
            "worker",
            stage=self.PLAN_STAGE,
            purpose="plan (prebuilt)",
            usd=self.adopted_usd(("scope", "plan")),
            ok=True,
            prebuilt=True,
        )
        return res

    PLAN_STAGE = "scope"

    def adopted_claims(self) -> dict | None:
        """The adopted round's claim list (``claims.json``), or None."""
        p = Path(self.adopted) / "claims.json"
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        return c if isinstance(c, dict) and c.get("claims") else None

    def adopt_build(self) -> None:
        """The adopted round's build workspace at its last commit before any repair
        (``base_commit``), as this round's build; the build's cost recorded once."""
        src = Path(self.adopted)
        sha = base_commit(src)
        if self.ws.exists():
            shutil.rmtree(self.ws)
        repair.checkout_copy(src / "build", sha, self.ws)
        repair.git(self.ws, "checkout", "-q", "-B", "build")
        usd = self.adopted_usd(("build",))
        ledger.record(
            "worker", stage="build", purpose="build (prebuilt)", usd=usd, ok=True, prebuilt=True
        )
        self.worker_usd += usd
        self.emit(
            "candidate_started",
            candidate=1,
            run="build",
            strategy="prebuilt",
            kind="branch",
            parent=None,
            milestone="build",
            reason=f"the build of {src.parent.parent.parent.name} round {src.name[1:]} "
            f"at {sha[:10]} (prebuilt)",
        )
        self.emit(
            "candidate_finished",
            candidate=1,
            run="build",
            node=1,
            kind="branch",
            strategy="prebuilt",
            parent=None,
            valid=True,
            usd=usd,
            exit_reason="ok",
            milestone="build",
            reason="prebuilt",
        )
        self._prebuilt_flag = True

    def do_build(self) -> None:
        self.candidates = 1
        caps = self.cfg_of("build")
        if self.adopted:
            self.adopt_build()
            self.delivered = 1
            repair.ensure_repo(self.ws, "v5: the base build")
            self.partial = None
            _ = self.venv
            return
        if self.prebuilt is not None and self.scoped():
            self._prebuilt_flag = False  # scope: always: built afresh
        if self.prebuilt is not None and not self.scoped():
            pb = self.prebuilt
            links.copytree(
                pb.workspace,
                self.ws,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    "venv",
                    "node_modules",
                    "__pycache__",
                    ".pytest_cache",
                    "transcript.jsonl",
                    "stderr.txt",
                ),
            )
            self.emit(
                "candidate_started",
                candidate=1,
                run="base",
                strategy="vanilla",
                kind="branch",
                parent=None,
                milestone="build",
                reason="shared base build",
            )
            ledger.record(
                "worker",
                stage="build",
                purpose="base build (prebuilt)",
                usd=float(pb.usd or 0.0),
                wall_s=pb.wall_s,
                ok=True,
                prebuilt=True,
                session=pb.session_id or None,
            )
            self.worker_usd += float(pb.usd or 0.0)
            if pb.session_id:
                t = Path(pb.transcript) if getattr(pb, "transcript", None) else None
                self.builder_session = {
                    "id": pb.session_id,
                    "cost": float(pb.usd or 0.0),
                    **({"ended": t.stat().st_mtime} if t is not None and t.exists() else {}),
                }
            self.emit(
                "candidate_finished",
                candidate=1,
                run="base",
                node=1,
                kind="branch",
                strategy="vanilla",
                parent=None,
                valid=True,
                usd=float(pb.usd or 0.0),
                exit_reason="ok",
                milestone="build",
                reason="shared base build",
            )
        else:
            res = None
            prior = Path(self.carried) if self.carried else None
            if prior is not None and not self.ws.exists():
                # Retry keeps the work: the earlier round's committed tree, as it stopped
                links.copytree(
                    prior / "build",
                    self.ws,
                    ignore=shutil.ignore_patterns(
                        ".venv",
                        "venv",
                        "node_modules",
                        "__pycache__",
                        ".pytest_cache",
                        "transcript.jsonl",
                        "stderr.txt",
                    ),
                )
            md = self.scope_md()
            # an earlier attempt's tree (this round's, or the carried one): continued, never
            # thrown away
            have = self.product()
            self.ws.mkdir(parents=True, exist_ok=True)
            if not have:
                if self.cfg.seed_dir.is_dir():
                    shutil.copytree(self.cfg.seed_dir, self.ws, dirs_exist_ok=True)
                if self.cfg.has_user_tests():
                    shutil.copytree(self.cfg.user_tests, self.ws / "tests", dirs_exist_ok=True)
            if md and self.SCOPE_IN_WORKSPACE:
                # the builder reads it beside the code; it never ships (process.strip)
                (self.ws / scope_mod.FILE).write_text(md)
            text = self.build_text(md)
            if have:
                repair.ensure_repo(self.ws, "v5: the stopped build, as it was")
                text += "\n" + self.continue_brief(have)
                why = "continues the stopped build's committed tree"
            else:
                why = "the scoped build: the idea plus SCOPE.md" if md else "the vanilla build"
            # one purpose per attempt (a crashed attempt's session is found again by it)
            purpose = "build" if not self.build_failures else f"build{self.build_failures + 1}"
            self.emit(
                "candidate_started",
                candidate=1,
                run="build",
                strategy="scoped" if md else "vanilla",
                kind="branch",
                parent=None,
                milestone="build",
                reason=why,
            )
            res, self.builder_session = self.session(
                "builder", text, self.ws, "build", caps, purpose
            )
            self.builder_session["ended"] = time.time()  # repair rounds measure from here
            ok = res.exit_ok and res.exit_reason == "ok"
            self.cut_off = not ok
            self.emit(
                "candidate_finished",
                candidate=1,
                run="build",
                node=1,
                kind="branch",
                strategy="scoped" if md else "vanilla",
                parent=None,
                valid=ok,
                usd=res.cost_usd,
                exit_reason=res.exit_reason,
                milestone="build",
                model=res.model,
                reason=why,
            )
        self.delivered = 1
        repair.ensure_repo(self.ws, "v5: the base build")
        if self.cut_off:
            self.settle_stopped(res)  # a partial build goes on, or StageFailed
        else:
            self.partial = None
            _ = self.venv  # the build's declared dependencies, installed once

    SCOPE_IN_WORKSPACE = True  # SCOPE.md beside the code while it is built

    def build_text(self, md: str | None) -> str:
        """The build session's prompt: the idea, SCOPE.md, the owner's house rules and what
        a product carries (``scope.build_prompt``)."""
        return scope_mod.build_prompt(self.idea, md, VANILLA_SUFFIX, self.policy)

    def scope_rules(self) -> str:
        """A flow's own rules for its brief, added to the scope prompt; none here (v5's
        scope prompt is sent as it is)."""
        return ""

    def product(self) -> list[str]:
        """The product files in the build workspace (``product_files``, less the seed and
        the user's tests)."""
        base = [
            p
            for p in (self.cfg.seed_dir, self.cfg.user_tests if self.cfg.has_user_tests() else None)
            if p is not None and Path(p).is_dir()
        ]
        return product_files(self.ws, base)

    def continue_brief(self, files: list[str]) -> str:
        """What a session continuing a stopped build is told: its tree is the earlier
        session's committed work."""
        try:
            log = repair.git(self.ws, "log", "--oneline", "-n", "15").strip()
        except Exception:  # noqa: BLE001 - the brief only
            log = ""
        shown = "\n".join(f"- {f}" for f in files[:60]) + (
            f"\n- ... and {len(files) - 60} more" if len(files) > 60 else ""
        )
        return (
            "\n## Continue the build already in this directory\n\n"
            "An earlier session started this build here and stopped before it finished "
            "(it ran out of turns, time or budget, or its login expired). Its work is "
            "committed in this directory's git history:\n\n"
            f"```\n{log}\n```\n\nFiles so far:\n\n{shown}\n\n"
            "Do not start over. Read what is there, find what is missing or broken against "
            "the brief above, and finish the product: complete the features, make the "
            "tests pass, and make the README's commands work. Keep what already works.\n"
        )

    def tree_check(self) -> dict:
        """Whether a stopped build's tree builds and its tests run (a partial build is kept
        only then): ``{"ok", "why", "tests"}``. Its declared dependencies install into the
        build's venv; the tree's own tests run once, network off (failing tests are fine;
        they must run)."""
        from ..harness.venv import candidate_venv, run_pytest

        venv, install = candidate_venv(self.ws, list(self.cfg.requirements))
        if install.get("status") == "failed":
            return {"ok": False, "why": f"doesn't install ({install.get('detail')})"}
        self._venv = venv
        t = run_pytest(self.ws, {}, [], venv, timeout_s=600, network=False)
        n = len(t.get("tests") or {})
        if t.get("returncode") not in (0, 1) or not n:
            return {
                "ok": False,
                "why": "has no tests that run"
                if not n
                else f"tests don't run (pytest exit {t.get('returncode')})",
            }
        passed = sum(1 for v in (t.get("tests") or {}).values() if v == "passed")
        return {
            "ok": True,
            "why": f"builds; {passed} of {n} tests pass",
            "tests": n,
            "passed": passed,
        }

    def settle_stopped(self, res) -> None:
        """The build session stopped early. A stop on turns, time or a cap that left a tree
        that builds and whose tests run is a *partial* build: the stages go on with that
        tree (verify finds what is missing, repair fixes it) and REPORT.md says so. Anything
        else (an error, or a stop with nothing usable) raises ``StageFailed``: the build
        fails with the reason, and a retry continues this tree."""
        reason = getattr(res, "exit_reason", None) or "error"
        files = self.product()
        if reason in STOPS and files:
            check = self.tree_check()
            if check["ok"]:
                self.partial = {
                    "exit_reason": reason,
                    "why": f"the build session {STOPS[reason]}; its tree {check['why']}",
                    "files": len(files),
                }
                self.stop = None
                self.emit(
                    "build_partial",
                    stage="build",
                    exit_reason=reason,
                    why=self.partial["why"],
                    files=len(files),
                )
                return
            raise StageFailed(
                "build", f"the build session {STOPS[reason]} and its tree {check['why']}", "stopped"
            )
        if reason in STOPS:
            raise StageFailed(
                "build",
                f"the build session {STOPS[reason]} before it wrote any of the product",
                "stopped",
            )
        raise StageFailed(
            "build",
            "the build session ended with an error before it finished" + self.session_error(),
            "error",
        )

    def session_error(self) -> str:
        """The build session's own last words, when its transcript has them (``: ...``)."""
        p = (
            self.root
            / "transcripts"
            / (
                ("build" if self.build_failures == 0 else f"build{self.build_failures + 1}")
                + ".transcript.jsonl"
            )
        )
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        for line in reversed(lines):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                txt = str(ev.get("result") or ev.get("subtype") or "").strip()
                return f" ({txt[:200]})" if txt else ""
        return ""

    def do_claims(self) -> None:
        cfg = dict(self.cfg_of("claims"))
        got = self.adopted_claims() if getattr(self, "adopted", None) else None
        self.claims = got or claims_mod.run(
            self.llm, self.policy, self.spec, self.lessons, cfg, self.budget
        )
        claims_mod.write(self.root / "claims.json", self.claims)
        self.emit(
            "claims",
            n=len(self.claims["claims"]),
            ambiguities=len(self.claims["ambiguities"]),
            samples=self.claims.get("samples"),
            dedupe=self.claims.get("dedupe"),
            lessons=len(self.lessons),
        )

    def do_verify(self) -> None:
        caps = self.cfg_of("verify")
        verify.prepare(
            self.ws,
            self.vws,
            self.spec,
            self.claims,
            exam_ws=None if self.layout == "inside" else self.vex,
        )
        text = prompt(
            self.policy,
            "verifier",
            spec=self.spec.strip(),
            explore_pct=str(round(100 * float(caps.get("explore_share", 0.3)))),
            lessons=claims_mod.lessons_md(self.lessons, "verifier"),
            scope=self.flows_section("verifier", "scope"),
        )
        try:
            _, self.verifier_session = self.session(
                "verifier",
                text,
                self.vws,
                "verify",
                caps,
                "verify",
                caller="verifier",
                exam_mount=(self.vex, False),
            )
        finally:
            self.ingest("verifier")

    def flows_section(self, name: str, section: str) -> str:
        """The prompt section that has the verifier walk ``SCOPE.md``'s flows, or for a spec
        that passed through, the flows drawn from the spec (``spec_<section>``, falling back
        to ``section`` when the prompt has no such section). Empty with no definition of
        done."""
        dod = self.scope.get("dod") or []
        if not dod:
            return ""
        names = ([f"spec_{section}"] if self.scope.get("dod_from") == "spec" else []) + [section]
        for s in names:
            try:
                return prompt(self.policy, name, s, flows=scope_mod.flows_md(dod))
            except KeyError:  # an older policy without the section
                continue
        return ""

    def ingest(self, by: str) -> list[str]:
        """The verifier's scenarios into the store, validated by the harness's own two
        runs on the build (not the verifier's copy); the passing share split held. Returns
        the ids that became active."""
        self.findings = verify.load_findings(self.vex)
        self.helpers = verify.helpers_dir(self.vex, self.root / "helpers")
        verify.import_scenarios(self.store, self.vex, self.findings, self.claims, by=by)
        # Every scenario not yet validated (also ones an interrupted step imported).
        new = self.store.select("candidate")
        res = verify.validate(self.store, new, self.spec, self.runner(self.ws))
        for i, v in res["results"].items():
            self.status[i] = v.get("status")
        passing = [i for i in res["active"] if self.status.get(i) == "passed"]
        verify.held_split(
            self.store, passing, float(self.cfg_of("verify").get("held_fraction", 0.3))
        )
        self.write_findings()
        return res["active"]

    def failing(self) -> list[str]:
        return [r.id for r in self.store.active() if self.status.get(r.id) not in (None, "passed")]

    def passing_rules(self) -> list:
        return [r for r in self.store.active() if self.status.get(r.id) == "passed"]

    # -- the panel and interpretations ------------------------------------------------------
    def claim_of(self, rule) -> dict:
        cid = rule.origin.get("claim")
        c = next((c for c in self.claims["claims"] if c["id"] == cid), None)
        if c:
            return c
        return {
            "situation": rule.title,
            "expect": rule.expect or rule.origin.get("expected"),
            "quote": rule.source,
        }

    def judge(self, ids: list[str]) -> dict:
        """The panel on failing scenarios ``ids``; records each class. Scenarios pinned to
        a setting value test a setting, not the spec: they never go to the panel
        (``attach_pinned``)."""
        ids = [i for i in ids if i not in self.panel and not self.store.rules[i].origin.get("pins")]
        if not ids:
            return {}
        cfg = self.cfg_of("oracle")
        items = {i: self.claim_of(self.store.rules[i]) for i in ids}
        known = [{**s["setting"], "question": s.get("question")} for s in self.settings]
        res = oracle.panel(
            self.panel_llm,
            self.policy,
            self.spec,
            items,
            self.answers,
            int(cfg.get("votes", 3)),
            int(cfg.get("defect_min", 2)),
            self.budget,
            known=known,
        )
        for i, v in res.items():
            self.panel[i] = v
            r = self.store.rules[i]
            r.origin["panel"] = v
            if v["class"] == "defect":
                self.defects.append(i)
                self.store.record_verdict(
                    i, {"verdict": "code_wrong", "by": "v5-panel", "votes": v["votes"]}
                )
            elif v["class"] == "retired":
                self.store.set_status(i, "retired", "panel: not required by the spec", "v5-panel")
        self.store.save()
        self.write_findings()
        return res

    # -- before the panel: incomplete and environment ----------------------------------------
    def triage(self, ids: list[str], new: set | None = None) -> dict:
        """Failing scenarios that don't go to the panel. **environment**: the verifier ran it
        green, and the harness fails it on the build *and* on the verifier's own copy: the
        harness's environment, not the code (recorded, never repaired; delivered xfail).
        Only scenarios first run now (``new``; all when None) qualify: a scenario the
        harness saw pass that fails later is a regression. **incomplete**: a flow of
        ``SCOPE.md`` that doesn't work end to end, or what the verifier marked incomplete
        (a stub, a dead control, placeholder data): sent to repair like a defect, rank 2
        for a flow of the definition of done (all must-have), else rank 3. The panel isn't
        asked: the scope is the factory's v1 contract, not a reading of the spec. Returns
        ``{id: class}``."""
        ids = [i for i in ids if i not in self.panel and not self.store.rules[i].origin.get("pins")]
        env = self.environment([i for i in ids if new is None or i in new])
        must = {f["id"] for f in self.scope.get("dod") or []}
        out = {}
        for i in ids:
            r = self.store.rules[i]
            if i in env:
                cls, why = (
                    "environment",
                    ("passes in the verifier's run, fails only in the harness's sandbox"),
                )
            elif r.origin.get("incomplete") or r.origin.get("flows"):
                fl = r.origin.get("flows") or []
                r.origin["basis"] = "scope_must" if set(fl) & must else "incomplete"
                cls = "incomplete"
                why = (
                    f"flow {', '.join(fl)} of SCOPE.md doesn't work end to end"
                    if fl
                    else "missing or partial (the verifier's finding)"
                )
                self.defects.append(i)
                self.store.record_verdict(
                    i, {"verdict": "code_wrong", "by": "v5-incomplete", "votes": []}
                )
            else:
                continue
            v = {
                "votes": [],
                "required": 0,
                "not_required": 0,
                "silent": 0,
                "class": cls,
                "unanimous": False,
                "quotes": [why],
                "setting": None,
            }
            self.panel[i] = v
            r.origin["panel"] = v
            out[i] = cls
        if out:
            self.store.save()
        return out

    def environment(self, ids: list[str]) -> set[str]:
        """Of ``ids`` (failing in the harness's run on the build), those the verifier
        reported passing that also fail when the harness runs them on the verifier's own
        copy. (One that passes there points at a difference between the copy and the build,
        which is the build's business: the panel judges it.)"""
        cand = [
            i
            for i in ids
            if str(self.store.rules[i].origin.get("verifier_status") or "").lower()
            in ("pass", "passed")
        ]
        if not cand or not self.vws.is_dir():
            return set()
        res = self.runner(self.vws)([self.store.rules[i] for i in cand])
        return {i for i in cand if (res.get(i) or {}).get("status") != "passed"}

    def completeness(self) -> dict | None:
        """Flows of ``SCOPE.md``'s definition of done that work end to end: every active
        scenario of the flow passes in the harness's latest run (``works``); ``broken``
        when one fails, ``unchecked`` when the verifier wrote none. ``verifier``: its own
        report (``works|partial|missing``)."""
        dod = self.scope.get("dod") or []
        if not dod:
            return None
        rep = {
            str(f.get("id") or "").upper(): f
            for f in self.findings.get("flows") or []
            if isinstance(f, dict)
        }
        rows = []
        for f in dod:
            rs = [r for r in self.store.active() if f["id"] in (r.origin.get("flows") or [])]
            # a scenario only the harness's environment fails says nothing about the flow
            env = [
                r
                for r in rs
                if self.status.get(r.id) != "passed"
                and (self.panel.get(r.id) or {}).get("class") == "environment"
            ]
            st = [self.status.get(r.id) for r in rs if r not in env]
            status = (
                "unchecked"
                if not rs
                else "environment"
                if not st
                else "works"
                if all(s == "passed" for s in st)
                else "broken"
            )
            rows.append(
                {
                    "id": f["id"],
                    "title": f["title"],
                    "status": status,
                    "scenarios": [r.id for r in rs],
                    "verifier": (rep.get(f["id"]) or {}).get("status"),
                }
            )
        return {
            "done": sum(1 for x in rows if x["status"] == "works"),
            "total": len(rows),
            "flows": rows,
        }

    def findings_event(self, stage: str, res: dict, tri: dict, **extra) -> None:
        comp = self.completeness()
        self.emit(
            "findings",
            stage=stage,
            confirmed=sum(1 for v in res.values() if v["class"] == "defect"),
            incomplete=sum(1 for c in tri.values() if c == "incomplete"),
            environment=sum(1 for c in tri.values() if c == "environment"),
            interpretation=sum(1 for v in res.values() if v["class"] == "interpretation"),
            retired=sum(1 for v in res.values() if v["class"] == "retired"),
            settings=sum(1 for v in res.values() if v["class"] == "setting"),
            blocked=len(self.findings.get("blocked") or []),
            scenarios=len(self.store.active()),
            failing=len(self.failing()),
            **({"completeness": [comp["done"], comp["total"]]} if comp else {}),
            **extra,
        )

    def do_oracle(self) -> None:
        self.claim_settings()  # first: the panel reuses their keys
        tri = self.triage(self.failing())
        res = self.judge(self.failing())
        self.findings_event("oracle", res, tri)
        self.do_interpretations(res)
        if self.open_defects() or self.interp_repairs or self.open_settings():
            # The pre-repair measure (scenarios plus the build's own suite), now: the repair
            # step then holds only the session and the after-measure.
            repair.measure_at(RoundView(self), repair.ensure_repo(self.ws, "v5: before repair 1"))

    # -- Addendum A: binary items become settings, complex ones stay as built ---------------
    def setting_for(self, st: dict, **fields) -> dict:
        """The setting item for ``st["key"]``, made if new (``fields`` fill a new one)."""
        it = next((s for s in self.settings if s["key"] == st["key"]), None)
        if it is None:
            it = {
                "id": f"s{len(self.settings) + 1}",
                "class": "setting",
                "key": st["key"],
                "setting": {
                    k: st[k] for k in ("key", "values", "default", "scope", "means") if k in st
                },
                "scenarios": [],
                "values": {},
                "status": "proposed",
                "has_setting": False,
                "current": None,
                "round": None,
                "why": None,
                **fields,
            }
            self.settings.append(it)
        return it

    def open_settings(self) -> list[dict]:
        """Setting items a repair still has to add or fix."""
        return [s for s in self.settings if s["status"] in ("proposed", "open")]

    def claim_settings(self) -> None:
        """The claims' ``binary`` ambiguities as setting items (once). Default: the
        expected reading (the claims' recommendation). The verifier said whether the build
        already exposes the switch (``has_setting``) and how each value behaved; an exposed
        switch whose values both pass needs no repair."""
        if any(s.get("source") == "claims" for s in self.settings):
            return
        impl = {str(a.get("id")): a for a in self.findings.get("ambiguities") or []}
        for a in self.claims.get("ambiguities") or []:
            if a.get("kind") != "binary" or not a.get("setting"):
                continue
            st, f = a["setting"], impl.get(a["id"]) or {}
            vals = (
                {
                    settings_mod.norm_value(k): str(v).lower()
                    for k, v in (f.get("values") or {}).items()
                }
                if isinstance(f.get("values"), dict)
                else {}
            )
            it = self.setting_for(
                st,
                source="claims",
                ambiguity=a["id"],
                question=a.get("question"),
                literal=a.get("literal"),
                expected=a.get("expected"),
            )
            it.update(
                current=settings_mod.reading_value(st, f.get("implements")),
                implements=f.get("implements") or "unknown",
                has_setting=bool(f.get("has_setting")),
                how=f.get("how"),
                values={
                    k: ("pass" if v.startswith("pass") else "fail")
                    for k, v in vals.items()
                    if k in st["values"]
                },
            )
            if it["has_setting"]:
                bad = [v for v in st["values"] if it["values"].get(v) != "pass"]
                if not bad:
                    it.update(
                        status="exposed",
                        why="the build already exposes it; both values behave as described",
                    )
                else:
                    it["fix_value"] = bad[0]
        self.attach_pinned()

    def attach_pinned(self) -> None:
        """Scenarios pinned to a setting value (``# setting: key=value``: the verifier's
        both-values scenarios, or ones the factory pinned) belong to that setting's item;
        a failing one means that value doesn't behave as described."""
        seen: dict[tuple, list] = {}
        for r in self.store.active():
            for key, val in (r.origin.get("pins") or {}).items():
                it = next((s for s in self.settings if s["key"] == key), None)
                if it is None:
                    continue
                if r.id not in it["scenarios"]:
                    it["scenarios"].append(r.id)
                seen.setdefault((it["id"], val), []).append(r)
        for (sid, val), rules in seen.items():
            it = next(s for s in self.settings if s["id"] == sid)
            bad = [r for r in rules if self.status.get(r.id) != "passed"]
            it["values"][val] = "fail" if bad else "pass"
            if bad and it["status"] in ("exposed", "added"):
                it.update(
                    status="open",
                    fix_value=val,
                    why=f"value {val} fails {bad[0].origin.get('scenario') or bad[0].id}",
                )

    def scenario_setting(self, i: str, prop: dict, source: str) -> dict | None:
        """A failing scenario that the panel (or an undecidable pair) found to be one side
        of a binary policy becomes part of a setting item, never a defect. A new item
        defaults to the build's current behaviour, the other side of the scenario's
        (§3.5's authority order: the builder's reading, rank 4, outranks one the factory
        assumed, rank 5); an item the claims proposed keeps their default (the expected
        reading). The scenario is pinned to its value unless that is the default."""
        cv = prop.get("claim_value") or prop["values"][0]
        r = self.store.rules[i]
        c = self.claim_of(r)
        it = self.setting_for(
            {**prop, "default": settings_mod.other(prop, cv)},
            source=source,
            question=prop.get("question") or oracle.prose(c).replace("\n", "; "),
            quote=c.get("quote"),
            current=settings_mod.other(prop, cv),
        )
        if cv not in it["setting"]["values"]:
            self.store.set_status(
                i,
                "retired",
                f"binary policy {it['key']}: its value {cv!r} isn't one of the setting's",
                "v5-setting",
            )
            return it
        if i not in it["scenarios"]:
            it["scenarios"].append(i)
        if cv != it["setting"]["default"]:
            verify.pin(
                self.store,
                i,
                {**(r.origin.get("pins") or {}), it["key"]: cv},
                f"{it['id']}: tests the non-default value",
            )
        if it["status"] in ("exposed", "added"):
            it.update(status="open", why=f"{i} fails")
        return it

    def do_interpretations(self, panel: dict) -> None:
        """Addendum A. Binary items are settings (``claim_settings``; panel ``setting``
        classes join or make one); nobody votes on them, asks about them or forks them.
        Complex items (the claims' ``complex`` ambiguities, panel splits that are no binary
        policy) keep the build's reading, recorded in ``ASSUMPTIONS.md``; no repair. Only
        ``interpret: ask`` with a reachable owner asks, and only about complex items."""
        cfg = self.cfg_of("interpret")
        impl = {str(a.get("id")): a for a in self.findings.get("ambiguities") or []}
        for i, v in panel.items():
            if v["class"] == "setting":
                self.scenario_setting(i, v["setting"], "panel")
        items: dict[str, dict] = {}
        for a in self.claims.get("ambiguities") or []:
            if a.get("kind") == "binary" and a.get("setting"):
                continue
            items[a["id"]] = {
                **a,
                "kind": "complex",
                "source": "claims",
                "implements": (impl.get(a["id"]) or {}).get("implements") or "unknown",
            }
        for i, v in panel.items():
            if v["class"] != "interpretation":
                continue
            r = self.store.rules[i]
            c = self.claim_of(r)
            items[f"i-{i}"] = {
                "id": f"i-{i}",
                "question": oracle.prose(c).replace("\n", "; "),
                "literal": "as built: "
                + str(r.origin.get("observed") or "the build's current behaviour"),
                "expected": str(c.get("expect") or r.expect),
                "stakes": "low",
                "implements": "literal",
                "source": "panel",
                "scenario": i,
                "kind": "complex",
            }
        chosen: dict[str, dict] = {}
        if cfg.get("mode") == "ask" and self.owner not in (None, "none") and items:
            chosen.update(self.ask_owner(items, int(cfg.get("max_questions", 3))))
        for k, it in items.items():
            c = chosen.get(k) or {}
            reading, impl_r = c.get("reading"), it.get("implements")
            rec = {
                "id": k,
                "kind": "complex",
                "question": it.get("question"),
                "literal": it.get("literal"),
                "expected": it.get("expected"),
                "implements": impl_r,
                "stakes": it.get("stakes"),
                "mode": "ask" if c else "auto",
                "votes": c.get("votes"),
                "chosen": reading or impl_r or "as built",
                "source": it.get("source"),
            }
            if reading and impl_r not in (reading, "unknown", "either", None):
                rec["how"] = (
                    f"the owner chose {reading}; the build implemented {impl_r}: repair queued"
                )
                if it.get("scenario") and reading == "expected":
                    self.defects.append(it["scenario"])
                elif it.get("scenario"):
                    self.store.set_status(
                        it["scenario"],
                        "retired",
                        "the owner chose the build's reading",
                        "v5-interp",
                    )
                else:
                    self.interp_repairs.append(
                        {
                            "id": k,
                            "class": "interpretation",
                            "question": it.get("question"),
                            "chosen_text": it.get(reading),
                            "chosen_by": "owner",
                            "reading": reading,
                        }
                    )
            elif reading:
                rec["how"] = (
                    f"the owner chose {reading}; the build already implements it"
                    if impl_r == reading
                    else f"the owner chose {reading}; the build's reading unknown: left as built"
                )
            else:
                rec["how"] = "complex: the implementation decides; the build's reading is kept"
                if it.get("scenario"):
                    self.store.set_status(
                        it["scenario"],
                        "retired",
                        "complex item: the build's reading kept",
                        "v5-interp",
                    )
            self.interpretations.append(rec)
            self.emit(
                "interpretation",
                item=k,
                mode=rec["mode"],
                chosen=rec["chosen"],
                forked=False,
                implements=impl_r,
                stakes=it.get("stakes"),
                kind="complex",
            )
        for s in self.settings:
            if not s.get("announced"):
                s["announced"] = True
                self.emit(
                    "interpretation",
                    item=s["id"],
                    mode="setting",
                    chosen=s["setting"]["default"],
                    forked=False,
                    implements=s.get("current"),
                    stakes="low",
                    kind="binary",
                    setting=s["key"],
                    status=s["status"],
                    source=s.get("source"),
                )
        self.store.save()
        self.write_findings()

    def ask_owner(self, items: dict, n: int) -> dict:
        """``interpret: ask``: the top ``n`` items by stakes, through the existing oracle
        (``--owner human|queue|sim``). The answer is rank 1."""
        from types import SimpleNamespace

        from .workspace import make_oracle

        top = sorted(items.values(), key=lambda it: it.get("stakes") != "high")[:n]
        orc = make_oracle(self.owner, self.taskdir, self.den, False, len(top))
        out = {}
        for j, it in enumerate(top, 1):
            text = (
                f"{it['question']}\n  A) {it.get('literal') or '-'}\n"
                f"  B) {it.get('expected') or '-'}\nAnswer A or B, or say what you want."
            )
            self.emit("question", id=it["id"], text=text, n=j, of=len(top))
            ans = orc.answer(self.task, SimpleNamespace(id=it["id"], text=text))
            self.emit("answer", id=it["id"], text=ans)
            if not ans:
                continue
            self.answers[it["id"]] = ans
            a = ans.strip().lower()
            reading = (
                "literal"
                if a in ("a", "a)") or a.startswith(("a)", "a ", "literal"))
                else "expected"
                if a in ("b", "b)") or a.startswith(("b)", "b ", "expected"))
                else None
            )
            out[it["id"]] = {"reading": reading or "expected", "by": "owner", "votes": [ans]}
            if reading is None:
                it["expected"] = ans  # the owner's own words are the reading
        return out

    # -- repair and re-verify ---------------------------------------------------------------
    def open_defects(self) -> list[str]:
        return [
            i
            for i in dict.fromkeys(self.defects)
            if self.store.rules[i].status == "active" and self.status.get(i) != "passed"
        ]

    def repair_step(self) -> bool:
        """One step of the repair loop: a repair round, the re-verify after it (which then
        decides on another round, §3.6-3.7). True when the loop is over. (The state
        ``forks`` is the loop's last step; the fork itself is retired, Addendum A.)
        The loop's state is ``self.rs`` (saved with the run)."""
        rs, caps = self.rs, self.cfg_of("repair")
        if rs.get("next") is None:
            if "repair" not in self.rules["stages"]:
                self.emit("v5_stage", stage="repair", status="skipped", usd=0.0, wall_s=0.0)
                rs["next"] = "done"
                return True
            send = (
                [self.defect_item(i) for i in self.open_defects()]
                + list(self.interp_repairs)
                + self.setting_items()
            )
            if not send:
                self.stop = "no confirmed defect"
            rs.update(k=0, send=send, next="repair" if send else "forks")
        if rs["next"] == "repair":
            k = rs["k"] + 1
            want = usd_or_none(caps.get("max_usd"), "repair.max_usd")
            need = (want or self.budget.typical("repair")) if k == 1 else SECOND_ROUND_USD
            if not self.budget.allows(need):
                self.stop = (
                    f"reserve rule: ${self.budget.spent():.2f} spent + ${need:.2f} "
                    f"for repair round {k} > cap ${self.budget.cap:.2f}"
                )
                self.emit(
                    "repair_round", round=k, status="skipped", sent=len(rs["send"]), why=self.stop
                )
                rs["next"] = "forks"
                return False
            r = {}
            # this round's own cap, fix-ups included: the repair max_usd (round 1) or what the
            # reserve kept for round 2, when a cap applies; else the stage cap; else none
            capped = want is not None or self.budget.limited or "repair" in self.budget.stage_caps
            self.budget.window("repair", (want if k == 1 else SECOND_ROUND_USD) if capped else None)
            self.stage("repair", lambda: r.update(self.repair_start(k, rs["send"])))
            rs["k"] = k
            if not r:
                self.stop = "the repair stage hit its cap"
                rs["next"] = "forks"
            else:
                rs.update(partial=r, next="accept")
            return False
        if rs["next"] == "accept":  # the round's second step
            r = {}
            self.stage(
                "repair", lambda: r.update(self.repair_round(rs["k"], rs["send"], rs["partial"]))
            )
            rs.pop("partial", None)
            if not r:
                self.stop = "the repair stage hit its cap"
                rs["next"] = "forks"
            else:
                rs["next"] = "reverify" if "reverify" in self.rules["stages"] else "decide"
                rs["new"] = []
            return False
        if rs["next"] == "reverify" and not (self.rounds and self.rounds[-1].get("accepted")):
            # the round was undone: the head is the one already verified, nothing to probe
            self.emit(
                "v5_stage",
                stage="reverify",
                status="skipped",
                usd=0.0,
                wall_s=0.0,
                why="the repair round was undone; the head is unchanged",
            )
            rs.update(next="decide", new=[])
            return False
        if rs["next"] == "reverify" and (why := self.no_further_round(rs["k"])):
            # Its findings would have nowhere to go but REPORT.md (both h2h runs where it
            # found one): skipped. Without a global cap only max_rounds can stop it.
            self.emit("v5_stage", stage="reverify", status="skipped", usd=0.0, wall_s=0.0, why=why)
            self.stop = self.stop or why
            rs.update(next="decide", new=[])
            return False
        if rs["next"] == "reverify":
            rv = {}
            self.budget.window("reverify")
            self.stage("reverify", lambda: rv.update(new=self.reverify(self.rounds[-1])))
            rs["new"] = rv.get("new") or []
            if not rs["new"]:
                self.stop = "re-verify found no new confirmed defect"
            rs["next"] = "decide"
            return False
        if rs["next"] == "decide":
            rs["next"] = "forks"
            new, k = rs.get("new") or [], rs["k"]
            remaining = self.open_defects()
            max_rounds = int(caps.get("max_rounds", 2))
            if k >= max_rounds:
                self.stop = self.stop or f"repair.max_rounds ({max_rounds}) reached"
                return False
            # §3.6: another round only for defects a repair can fix (local or cross-cutting,
            # or new); §3.7: stop when nothing new was confirmed and nothing fixable is left.
            todo = [
                i
                for i in remaining
                if self.store.rules[i].origin.get("scope") != "structural" or i in new
            ]
            if not todo and not self.open_settings():
                self.stop = self.stop or "no defect left that a repair round can fix"
                return False
            if not self.budget.allows(SECOND_ROUND_USD + self.readiness_reserve()):
                self.stop = "reserve rule: no budget for another repair round" + (
                    f" (keeping ${self.readiness_reserve():.2f} for the readiness stage)"
                    if self.readiness_reserve()
                    else ""
                )
                self.emit(
                    "repair_round", round=k + 1, status="skipped", sent=len(todo), why=self.stop
                )
                return False
            self.stop = None
            rs.update(
                send=[self.defect_item(i) for i in todo] + self.setting_items(), next="repair"
            )
            return False
        if rs["next"] == "forks":  # the fork is retired (Addendum A)
            rs["next"] = "done"
        return True

    ADOPT_PROMPT = (
        "The independent checks below pass or fail against this product from the outside; "
        "they are not part of it and never will be. Write one test of your own, in your own "
        "test suite and the runner the README documents, for each behaviour below, through "
        "the product's real interface. Don't change the product's code, and don't copy the "
        "checks' code. Run your suite, and commit with a message that starts with `test:`.\n\n"
        "=== Behaviours ===\n{items}\n"
    )

    def adopt_step(self) -> bool:
        """``exam.adopt: all`` (EX-PLAN §4.3): one short session after the repairs asks the
        builder for a test of the product's own for every active scenario's behaviour; the
        exam then runs again on the new head as usual. ``fixes`` (the default) and ``off``:
        nothing here (``fixes`` is checked from git at readiness, ``check_adopted``)."""
        if exam.settings(self)["adopt"] != "all" or "repair" not in self.rules["stages"]:
            return True
        rules = self.store.active()
        if not rules:
            return True
        items = "\n".join(
            f"- {acceptance.rule_title(r).rstrip('.')}"
            + (f": expected {r.expect}" if r.expect else "")
            for r in rules
        )

        def go():
            caps = {**self.cfg_of("repair"), "max_turns": 40}
            text = (
                prompt(self.policy, "repair", "fresh")
                + "\n"
                + self.ADOPT_PROMPT.format(items=items)
            )
            self.session(
                "builder",
                text,
                self.ws,
                "repair",
                caps,
                "adopt",
                exam_mount=(exam.visible(self), True),
            )
            repair.ensure_repo(self.ws, "test: the product's own tests for the checks")

        self.stage("repair", go)
        return True

    def no_further_round(self, k: int) -> str | None:
        """Why no repair round can follow round ``k`` (then re-verify is skipped), or None.
        ``repair.max_rounds`` always; under a global cap also the reserve rule, counting
        re-verify's own cap, so that what it finds can still be repaired. Without a cap a
        new confirmed defect always gets the next round."""
        max_rounds = int(self.cfg_of("repair").get("max_rounds", 2))
        if k >= max_rounds:
            return (
                f"repair.max_rounds ({max_rounds}) reached: no round could repair what "
                "re-verify finds"
            )
        need = SECOND_ROUND_USD + self.readiness_reserve() + self.budget.typical("reverify")
        if not self.budget.allows(need):
            return (
                f"reserve rule: ${self.budget.spent():.2f} spent + ${need:.2f} for "
                f"re-verify and repair round {k + 1} > cap {self.budget.cap_text()}"
            )
        return None

    def readiness_reserve(self) -> float:
        """What a second repair round leaves for the readiness stage (its three caps): the
        optional round never takes the stage's money (Addendum C). Only under a global
        cap: without one nothing needs reserving."""
        if "readiness" not in self.rules["stages"] or not self.budget.limited:
            return 0.0
        return round(sum(readiness.step_usd(self, p) for p in ("security", "checks", "repair")), 2)

    def has_ready(self) -> bool:
        """Whether this flow has the readiness checks (today's ``readiness`` stage; the
        simple flow's Check)."""
        return "readiness" in self.rules["stages"]

    def readiness_summary(self) -> dict | None:
        rd = self.ready or {}
        if not self.has_ready():
            return None
        found = (rd.get("security") or {}).get("findings") or []
        return {
            "security": {
                s: sum(1 for f in found if f["severity"] == s) for s in readiness.SEVERITIES
            },
            "open": sum(1 for f in found if f.get("status") not in ("fixed",)),
            "scorecard": (rd.get("final") or {}).get("counts"),
            "skipped": rd.get("skipped"),
        }

    def setting_items(self) -> list[dict]:
        """The open setting items for a repair brief, each with ``pins`` (visible scenario
        file -> the value it runs with)."""
        out = []
        for s in self.open_settings():
            pins = {}
            for i in s["scenarios"]:
                r = self.store.rules.get(i)
                if r is not None and r.status == "active" and r.visibility == "visible":
                    v = (r.origin.get("pins") or {}).get(s["key"])
                    if v and v != s["setting"]["default"]:
                        pins[r.test_file] = v
            out.append(
                {
                    **s,
                    "scenarios": [
                        i
                        for i in s["scenarios"]
                        if self.store.rules.get(i) is not None
                        and self.store.rules[i].status == "active"
                    ],
                    "pins": pins,
                }
            )
        return out

    def defect_item(self, i: str) -> dict:
        r = self.store.rules[i]
        cls = "incomplete" if (self.panel.get(i) or {}).get("class") == "incomplete" else "defect"
        return {
            "id": i,
            "class": cls,
            "title": r.title,
            "expect": r.expect,
            "source": r.source,
            "origin": r.origin,
            "test_file": r.test_file,
            "quotes": (self.panel.get(i) or {}).get("quotes"),
            "detail": None,
        }

    def repair_start(self, k: int, send: list[dict]) -> dict:
        """Round ``k``'s first step: the repair session (``repair.start_round``)."""
        self.emit("repair_round", round=k, status="repairing", sent=len(send))
        strip_envs(self.ws)
        self.session_choice = None
        r = repair.start_round(RoundView(self), k, send)
        if self.session_choice:  # resumed or fresh, and why
            r["session"] = self.session_choice
        return r

    def repair_round(self, k: int, send: list[dict], partial: dict) -> dict:
        """Round ``k``'s second step: acceptance (``repair.finish_round``)."""
        strip_envs(self.ws)
        r = repair.finish_round(RoundView(self), k, send, partial)
        self.rounds.append(r)
        now = r.get("now") or r.get("after")  # the measure of the head kept
        if now:
            for g in ("scenarios", "held"):
                self.status.update(now.get(g) or {})
        if r.get("accepted"):
            self.delivered = self.candidates
        reverted = {
            x for v in (r.get("reverted") or []) + (r.get("dropped") or []) for x in v["findings"]
        }
        for res in r.get("settings") or []:
            it = next((s for s in self.settings if s["id"] == res["id"]), None)
            if it is None:
                continue
            ok = r.get("accepted") and res["fixed"] and res["id"] not in reverted
            it.update(status="added" if ok else "open", round=k, why=res["why"])
            if ok:
                it.pop("fix_value", None)
        self.emit(
            "repair_round",
            round=k,
            status="done",
            sent=len(r["sent"]),
            session=(r.get("session") or {}).get("mode"),
            fixed=len(r.get("fixed") or []),
            regressions=len(r.get("regressions") or []),
            conflicts=len(r.get("pairs") or []),
            reverted=sorted(reverted),
            accepted=bool(r.get("accepted")),
            why=r.get("why"),
            undo={v["sha"][:10]: v.get("how") for v in r.get("reverted") or []},
            settings=[s["key"] for s in r.get("settings") or [] if s["fixed"]],
        )
        self.interpretations += [
            {
                **i,
                "mode": "auto",
                "chosen": (
                    f"setting {i['setting']}"
                    if i.get("kind") == "binary"
                    else "the earlier behaviour (Y)"
                ),
            }
            for i in r.get("interpretations") or []
        ]
        self.write_findings()
        return r

    def reverify(self, r: dict) -> list[str]:
        """The verifier, resumed on the repaired code: reruns all, probes the fixes. New
        failing scenarios go to the panel. Returns the newly confirmed defects."""
        if not self.verifier_session:
            return []
        strip_envs(self.ws)
        if self.layout == "inside":  # its _verify/ lives in the copy
            keep = self.vws / verify.VERIFY_DIR
            tmp = self.scratch / "verify-keep"
            if tmp.exists():
                shutil.rmtree(tmp)
            links.copytree(keep, tmp)
            verify.copy_repo(self.ws, self.vws)
            links.copytree(tmp, keep, dirs_exist_ok=True)
        else:  # the exam is outside: a fresh copy
            verify.copy_repo(self.ws, self.vws)
        fixes = (
            "\n".join(
                f"- {i}: {self.store.rules[i].title} "
                f"(scenario {self.store.rules[i].origin.get('file')})"
                for i in r.get("fixed") or []
            )
            or "(none were fixed)"
        )
        added = [s for s in self.settings if s["status"] == "added" and s["round"] == r["round"]]
        sets = (
            prompt(
                self.policy,
                "reverify",
                "settings",
                items="\n\n".join(self.setting_md(s) for s in added),
            )
            if added
            else ""
        )
        caps = {
            **self.cfg_of("verify"),
            "max_turns": 40,
            "timeout_s": min(
                int(self.cfg_of("verify").get("timeout_s") or 1800), REVERIFY_TIMEOUT_S
            ),
        }
        known_failing = set(self.failing())
        added: list[str] = []
        try:
            _, self.verifier_session = self.session(
                "verifier",
                prompt(
                    self.policy,
                    "reverify",
                    fixes=fixes,
                    settings=sets,
                    flows=self.flows_section("reverify", "flows"),
                ),
                self.vws,
                "reverify",
                caps,
                f"reverify{r['round']}",
                resume=self.verifier_session,
                caller="verifier",
                exam_mount=(self.vex, False),
            )
        finally:
            added = self.ingest("reverify")
        res = self.runner(self.ws, flake=True)(self.store.active())
        for i, v in res.items():
            self.status[i] = v.get("status")
        fresh = [
            i
            for i in self.failing()
            if i not in known_failing
            and i not in self.panel
            and not self.store.rules[i].origin.get("pins")
        ]
        before = set(self.defects)
        tri = self.triage(fresh, new=set(added))
        res = self.judge(fresh)
        for i, v in res.items():
            if v["class"] == "setting":
                self.scenario_setting(i, v["setting"], "panel")
        self.attach_pinned()  # the verifier's both-values scenarios
        new = [i for i in self.defects if i not in before]
        self.findings_event("reverify", res, tri, new_failing=len(fresh))
        self.write_findings()
        return new

    # -- repair.run_round's context ------------------------------------------------------------
    def measure(self, code_dir: Path) -> dict:
        """The harness's record of one head: every active scenario (visible and held), the
        builder's own tests and the user's tests, network off: one exam run (``exam.run``,
        the flake protocol on; flaky scenarios leave the exam)."""
        rec = exam.run(self, code_dir, rules=self.store.active(), own=True, flake=True)
        exam.absorb(self, rec)
        rules = self.store.active()
        res = exam.outcomes_of(rec)
        scen = {
            r.id: (res.get(r.id) or {}).get("status", "error")
            for r in rules
            if r.visibility == "visible"
        }
        held = {
            r.id: (res.get(r.id) or {}).get("status", "error")
            for r in rules
            if r.visibility == "held"
        }
        t = exam.suite_of(rec)
        user_files = set()
        if self.cfg.has_user_tests():
            user_files = {
                f"tests/{p.relative_to(self.cfg.user_tests)}"
                for p in self.cfg.user_tests.rglob("test*.py")
            }
        tests, user = {}, {}
        for tid, st in (t.get("tests") or {}).items():
            (user if tid.split("::")[0] in user_files else tests)[tid] = st
        for f in t.get("errors") or []:
            (user if f in user_files else tests)[f] = "error"
        valid = t.get("returncode") != -1 and all(v == "passed" for v in user.values())
        return {"valid": valid, "scenarios": scen, "held": held, "tests": tests, "user": user}

    def session_for_repair(
        self,
        text: str,
        purpose: str,
        stage: str,
        caps: dict | None = None,
        prefix: str = "",
        fresh: bool = False,
    ):
        """A repair session: the builder's session resumed (a fix-up or an undo resumes the
        last repair session). ``caps``/``prefix``: the readiness round's own caps and
        ``ready-`` purposes; ``fresh``: a new session instead of the builder's (the readiness
        round: forking a build whose prompt cache has expired re-caches its whole
        transcript, $1.00-1.36 before the first edit in the real check)."""
        follow_up = purpose.startswith(("fixup", "undo"))  # capped at repair.fixup_usd
        if self.repair_session and follow_up:
            resume = self.repair_session
        else:
            choice = (
                {"mode": "fresh", "why": "the readiness round: always fresh"}
                if fresh
                else self.resume_choice()
            )
            self.session_choice = choice
            resume = self.builder_session if choice["mode"] == "resume" else None
        if not resume:
            text = prompt(self.policy, "repair", "fresh") + "\n" + text
        purpose = prefix + purpose
        caps = caps or self.cfg_of("repair")
        self.candidates += 1
        self.emit(
            "candidate_started",
            candidate=self.candidates,
            run=purpose,
            strategy="vanilla",
            kind="refine",
            parent=self.delivered or 1,
            milestone=purpose,
            reason=purpose.replace("repair", "repair round ")
            .replace("fixup", "fix-up after round ")
            .replace("undo", "undo in round "),
        )
        try:
            res, sess = self.session(
                "builder",
                text,
                self.ws,
                stage,
                caps if not follow_up else {**caps, "max_usd": self.fixup_usd},
                purpose,
                resume=resume,
                exam_mount=self._exam_mount or (exam.visible(self), True),
            )
        except BudgetExceeded:
            self.emit(
                "candidate_finished",
                candidate=self.candidates,
                run=purpose,
                node=self.candidates,
                kind="refine",
                parent=self.delivered or 1,
                valid=False,
                usd=0.0,
                exit_reason="cost_cap",
                milestone=purpose,
            )
            return None
        self.repair_session = sess
        if resume is not None and resume is self.builder_session:
            self.builder_session["used"] = time.time()  # its prompt cache is warm again
        self.emit(
            "candidate_finished",
            candidate=self.candidates,
            run=purpose,
            node=self.candidates,
            kind="refine",
            parent=self.delivered or 1,
            valid=bool(res.exit_ok),
            usd=res.cost_usd,
            exit_reason=res.exit_reason,
            milestone=purpose,
            model=res.model,
        )
        return res

    def resume_choice(self) -> dict:
        """A repair round's session: the builder's resumed (forked) or a fresh one. Measured,
        then chosen: resuming pays off only while the builder transcript's prompt cache is
        warm; a cold resume re-caches the whole transcript ($1.00-1.36 before the first edit
        in the readiness real check). ``repair.resume``: ``auto`` (the default) resumes only
        when the builder session was last used less than ``repair.warm_minutes`` (30) ago;
        ``true``/``false`` force it. Returns ``{"mode": "resume" | "fresh", "why",
        "idle_min"}``; the round records it."""
        c = self.cfg_of("repair")
        mode = c.get("resume", "auto")
        warm = float(c.get("warm_minutes", 30))
        bs = self.builder_session or {}
        last = bs.get("used") or bs.get("ended")
        idle = round((time.time() - float(last)) / 60, 1) if last else None
        if not bs.get("id"):
            return {"mode": "fresh", "why": "no builder session to resume", "idle_min": idle}
        if mode is True or str(mode).lower() in ("true", "always", "yes"):
            return {"mode": "resume", "why": "repair.resume: true", "idle_min": idle}
        if mode is False or str(mode).lower() in ("false", "never", "no"):
            return {"mode": "fresh", "why": "repair.resume: false", "idle_min": idle}
        if idle is None:
            return {
                "mode": "fresh",
                "why": "when the builder session ended is unknown",
                "idle_min": None,
            }
        if idle < warm:
            return {
                "mode": "resume",
                "why": f"the builder session was last used {idle:g} min "
                f"ago (< {warm:g}): its cache is likely warm",
                "idle_min": idle,
            }
        return {
            "mode": "fresh",
            "why": f"the builder session was last used {idle:g} min ago "
            f"(>= {warm:g}): its cache has likely expired",
            "idle_min": idle,
        }

    # The names ``repair.run_round`` calls.
    def setting_md(self, s: dict) -> str:
        return settings_mod.item_md(s)

    def brief(self, defects: list[dict]) -> str:
        sets = [d for d in defects if d.get("class") == "setting"]
        rest = [d for d in defects if d.get("class") != "setting"]
        n = sum(1 for d in rest if d.get("class") == "defect")
        inc = sum(1 for d in rest if d.get("class") == "incomplete")
        parts = [f"{n} defect(s)"] + ([f"{inc} incomplete flow(s) or feature(s)"] if inc else [])
        if len(rest) > n + inc:
            parts.append(f"{len(rest) - n - inc} interpretation change(s)")
        if sets:
            parts.append(f"{len(sets)} setting(s) to add")
        ctx = " and ".join(parts) + " below."
        return prompt(
            self.policy,
            "repair",
            context=ctx,
            spec=self.spec.strip(),
            findings="\n\n".join(repair.finding_md(d) for d in rest) or "(none)",
            settings=prompt(
                self.policy,
                "repair",
                "settings",
                items="\n\n".join(self.setting_md(s) for s in sets),
            )
            if sets
            else "",
        )

    def rescue(self, regs: list[dict], items: list[dict]) -> list[str]:
        """Scenarios a *changed* default broke (a setting whose default isn't the build's
        old behaviour): each regressed scenario is run again on the head pinned to the old
        value; one that passes then tests that value, so it is pinned for good. Returns the
        rescued ids."""
        from ..rules.model import Rule

        changed = [
            it
            for it in items
            if it.get("current") in it["setting"]["values"]
            and it["current"] != it["setting"]["default"]
        ]
        cand = [
            g for g in regs if g["group"] in ("scenarios", "held") and g["id"] in self.store.rules
        ]
        if not changed or not cand:
            return []
        d = repair.checkout_copy(self.ws, repair.head(self.ws), self.scratch / "rescue")
        out = []
        try:
            run = self.runner(d)
            for g in cand:
                r = self.store.rules[g["id"]]
                for it in changed:
                    pins = {**(r.origin.get("pins") or {}), it["key"]: it["current"]}
                    probe = Rule.from_dict(
                        {**r.to_dict(), "code": settings_mod.pin_code(r.code, pins)}
                    )
                    if (run([probe]).get(r.id) or {}).get("status") == "passed":
                        verify.pin(
                            self.store,
                            r.id,
                            pins,
                            f"{it['id']} changed the default; it tests the old value",
                        )
                        live = next(s for s in self.settings if s["id"] == it["id"])
                        if r.id not in live["scenarios"]:
                            live["scenarios"].append(r.id)
                        out.append(r.id)
                        break
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return out

    def pair_setting(self, xs: list[str], reg: dict, prop: dict) -> str:
        """An undecidable pair the votes call a binary policy: a setting item (default the
        earlier behaviour, Y's side) holding X's scenarios pinned to X's value; a later
        round adds it."""
        it = None
        for x in xs:
            if x in self.store.rules:
                it = self.scenario_setting(x, prop, "pair")
        if it is None:
            it = self.setting_for(
                {
                    **prop,
                    "default": settings_mod.other(
                        prop, prop.get("claim_value") or prop["values"][0]
                    ),
                },
                source="pair",
                question=f"{', '.join(xs)} against {reg['id']}",
                current=settings_mod.other(prop, prop.get("claim_value") or prop["values"][0]),
            )
        if x_open := [x for x in xs if x in self.defects]:
            self.defects = [d for d in self.defects if d not in x_open]
        return it["id"]

    def fixup_brief(self, pairs: list[dict]) -> str:
        return prompt(
            self.policy, "repair", "fixup", pairs=repair.pairs_md(pairs), spec=self.spec.strip()
        )

    def undo_brief(self, commits: list[dict]) -> str:
        return prompt(self.policy, "repair", "undo", commits=repair.undo_md(self.ws, commits))

    def export(self, ids: list[str]) -> list[str]:
        """The repair session's view of the exam: ``exam/visible`` (every visible scenario
        and the driver), mounted read-only at /work/exam for the next session. Nothing goes
        into the workspace."""
        d = exam.visible(self)
        self._exam_mount = (d, True)
        return sorted(p.name for p in (d / verify.SCENARIOS).glob("test_*.py"))

    def pair(self, x: dict, y: dict) -> dict:
        return oracle.pair(
            self.panel_llm,
            self.policy,
            self.spec,
            x,
            y,
            self.answers,
            int(self.cfg_of("oracle").get("votes", 3)),
            self.budget,
        )

    def item(self, i: str) -> dict:
        r = self.store.rules.get(i)
        if r is None:
            it = next((x for x in self.interp_repairs if x["id"] == i), None)
            if it:
                return {
                    "kind": "interpretation",
                    "chosen_by": it.get("chosen_by"),
                    "prose": f"{it.get('question')}: {it.get('chosen_text')}",
                }
            s = next((x for x in self.settings if x["id"] == i), None)
            if s:
                st = s["setting"]
                return {
                    "kind": "interpretation",
                    "prose": f"An operator setting `{st['key']}` ({' or '.join(st['values'])}, "
                    f"default {st['default']}) settles: {s.get('question')}",
                }
            return readiness.item_for(self, i) or {"kind": "scenario", "prose": i}
        v = self.panel.get(i) or {}
        c = self.claim_of(r)
        return {
            "kind": "scenario",
            "basis": r.origin.get("basis"),
            "required": v.get("required"),
            "votes": len(v.get("votes") or []) or 3,
            "source_ref": r.source_ref,
            "situation": c.get("situation"),
            "action": c.get("action"),
            "expect": c.get("expect") or r.expect,
            "quote": c.get("quote"),
        }

    def reg_item(self, reg: dict) -> dict:
        if reg["group"] == "user":
            return {"kind": "user_test", "prose": f"The owner's own test `{reg['id']}` passes."}
        if reg["group"] == "tests":
            name = reg["id"].split("::")[-1].replace("_", " ")
            return {
                "kind": "builder_test",
                "prose": f"The builder's own test `{reg['id']}` passes ({name}).",
            }
        return self.item(reg["id"])

    def retire(self, i: str, why: str) -> None:
        if i in self.store.rules:
            self.store.set_status(i, "retired", why, "v5-pair")

    def promote(self, i: str) -> None:
        verify.replace_held(self.store, i, [r.id for r in self.passing_rules()])

    def allows(self, usd: float) -> bool:
        return self.budget.allows(usd)

    # -- delivery ----------------------------------------------------------------------------
    def write_findings(self) -> None:
        """``findings.json``: the verifier's file, each scenario with the harness's status,
        its store id and the panel's class (track E's credit assignment reads it)."""
        by_scen = {
            r.origin.get("scenario"): r
            for r in self.store.rules.values()
            if r.origin.get("mode") == "v5"
        }
        scen = []
        for s in self.findings.get("scenarios") or []:
            r = by_scen.get(str(s.get("id")))
            extra = {}
            if r is not None:
                v = self.panel.get(r.id) or {}
                extra = {
                    "rule": r.id,
                    "store_status": r.status,
                    "visibility": r.visibility,
                    "harness_status": self.status.get(r.id),
                    "class": v.get("class"),
                    "votes": v.get("votes"),
                    **({"flows": r.origin["flows"]} if r.origin.get("flows") else {}),
                }
            scen.append({**s, **extra})
        known = {str(s.get("id")) for s in scen}
        for sid, r in by_scen.items():
            if sid not in known:
                v = self.panel.get(r.id) or {}
                scen.append(
                    {
                        "id": sid,
                        "file": r.origin.get("file"),
                        "claim": r.origin.get("claim"),
                        "sets": r.origin.get("sets"),
                        "lessons": r.origin.get("lessons"),
                        "rule": r.id,
                        "status": "fail"
                        if self.status.get(r.id) not in (None, "passed")
                        else "pass",
                        "store_status": r.status,
                        "visibility": r.visibility,
                        "harness_status": self.status.get(r.id),
                        "class": v.get("class"),
                        "votes": v.get("votes"),
                        **({"flows": r.origin["flows"]} if r.origin.get("flows") else {}),
                    }
                )
        kinds = {a["id"]: a for a in self.claims.get("ambiguities") or []}
        amb = []
        for a in self.findings.get("ambiguities") or []:
            c = kinds.get(str(a.get("id"))) or {}
            rec = {**a, "kind": c.get("kind", "complex")}
            if rec["kind"] == "binary":
                rec.setdefault("implements", "unknown")
                rec["has_setting"] = bool(a.get("has_setting"))
                rec["setting"] = (c.get("setting") or {}).get("key")
            amb.append(rec)
        out = {
            **self.findings,
            "scenarios": scen,
            "ambiguities": amb,
            "defects": list(dict.fromkeys(self.defects)),
            "completeness": self.completeness(),
            "environment": [i for i, v in self.panel.items() if v.get("class") == "environment"],
            "interpretations": self.interpretations,
            "settings": [
                {
                    k: s.get(k)
                    for k in (
                        "id",
                        "key",
                        "setting",
                        "source",
                        "ambiguity",
                        "question",
                        "current",
                        "has_setting",
                        "status",
                        "scenarios",
                        "values",
                        "round",
                        "why",
                    )
                }
                for s in self.settings
            ],
        }
        (self.root / "findings.json").write_text(json.dumps(out, indent=1, default=str))

    def publish_branch(self, art: Path) -> None:
        """``out``'s ``repo-factory/<id>`` becomes the delivered tree (a revise round adds a commit
        instead: ``change.ChangeRun``)."""
        from .workspace import make_branch

        make_branch(self.cfg, art, self.out, own_gitignore=True)

    def do_deliver(self) -> None:
        self.attach_pinned()
        defects = list(dict.fromkeys(self.defects))
        reverted = {x for r in self.rounds for v in r.get("reverted") or [] for x in v["findings"]}
        rows = []
        for i in defects:
            r = self.store.rules[i]
            st = self.status.get(i)
            if r.status == "retired":
                status = "retired"
            elif st == "passed":
                status = "fixed"
            elif i in reverted:
                status = "reverted"
            else:
                status = "open"
            cls = (self.panel.get(i) or {}).get("class")
            rows.append(
                {
                    "id": i,
                    "title": r.title,
                    "status": status,
                    "sets": r.origin.get("sets") or [],
                    "scope": r.origin.get("scope"),
                    "class": "incomplete" if cls == "incomplete" else "defect",
                    "flows": r.origin.get("flows") or [],
                    "via": {"defect": "panel", "incomplete": "verifier (incomplete)"}.get(
                        cls, "interpretation vote"
                    ),
                }
            )
        self.checked = self.checked_counts(rows)
        self.write_findings()
        # The delivered tree: the head and nothing of repo-factory's (EX-PLAN §4). The exam, its
        # results and the reports go to <round>/exam/evidence/, the release asset.
        art = verify.copy_repo(self.ws, self.root / "deliver")
        shutil.rmtree(art / "acceptance" / "findings", ignore_errors=True)
        if (art / "acceptance").is_dir() and not any((art / "acceptance").iterdir()):
            (art / "acceptance").rmdir()
        shutil.rmtree(art / verify.VERIFY_DIR, ignore_errors=True)
        # repo-factory's process files (SCOPE.md, its reports, a generated suite, .repo-factory/) move to
        # the round, in every flow: the delivered tree is the product (track LEAN)
        from . import process

        moved = process.strip(art, self.root)
        if moved:
            self.emit("process_files", moved=moved, to=str(self.root / process.DIR))
        if self.helpers is None and self.vex.is_dir():
            self.helpers = verify.helpers_dir(self.vex, self.root / "helpers")
        suite = self.delivered_suite(art)
        rec = self._final
        product = product_name(art, self.scope_md(), self.task.id)
        summary = self.summary(rows)
        summary.update(
            suite=suite,
            open_scenarios=[
                {
                    "id": o["id"],
                    "class": o["class"],
                    "title": self.store.rules[o["id"]].title,
                    "file": o["file"],
                }
                for o in suite["exam"]["open"]
            ],
        )
        from . import jargon

        hits = jargon.scan_run(self, art)
        docs = {"assumptions.md": report.render_assumptions(summary)}
        if self.has_ready():
            readiness.settle(self, rec)
            readiness.finalize(self, art, rec, hits)
            files = {
                r.id: f"exam/{verify.SCENARIOS}/{r.test_file}" for r in self.store.rules.values()
            }
            docs["security.md"] = readiness.render_security(product, self.ready, files)
            docs["readiness.md"] = readiness.render_readiness(
                product, self.ready, suite["exam"]["open"]
            )
            summary["readiness"] = self.ready
        summary["jargon"] = hits
        ev = exam.evidence(self, rec, docs, product)
        self.publish_branch(art)
        exam.set_commit(self, self.delivered_commit())
        (self.out / "REPORT.md").write_text(report.render_report(summary))
        self.emit(
            "delivered",
            node=self.delivered,
            branch=self.branch,
            out=str(self.out),
            report=str(self.out / "REPORT.md"),
            changed=True,
            alt_branches=[a["branch"] for a in self.alt_branches],
            rules={"visible": self._count("visible"), "held": self._count("held")},
            evidence=str(ev),
            exam=rec.get("key"),
        )

    def delivered_commit(self) -> str | None:
        """The head of ``repo-factory/<id>`` in ``out`` (the evidence names the commit it checked)."""
        try:
            return repair.git(self.out, "rev-parse", self.branch).strip() or None
        except Exception:  # noqa: BLE001 - no repo: nothing to name
            return None

    def delivered_suite(self, art: Path) -> dict:
        """The delivered tree's exam record (``exam.final``: the product's own tests, the
        README's test commands as written and every active scenario, one sandbox run, the
        flake protocol on). Every scenario's status becomes its status on the delivered head.
        Nothing is marked in the tree: what still fails is an open item in the evidence.
        Returns ``{"ran", "why", "key", "own": {"passed", "failed", "total"}, "readme",
        "exam": {"passed", "open": [{"id", "file", "reason", "class"}], "intermittent",
        "flaky"}, "reused"}``."""
        rec = exam.final(self, art, parent=self.parent_exam())
        self._final = rec
        scen = rec.get("scenarios") or {}
        flows = {f["id"]: f["title"] for f in self.scope.get("dod") or []}
        opens, passed = [], 0
        for r in self.store.active():
            st = (scen.get(r.id) or {}).get("status")
            if st is None:
                continue
            self.status[r.id] = st
            if st == "passed":
                passed += 1
                continue
            cls = (self.panel.get(r.id) or {}).get("class") or "open"
            opens.append(
                {
                    "id": r.id,
                    "file": f"exam/{verify.SCENARIOS}/{r.test_file}",
                    "reason": acceptance.reason(r, cls, flows),
                    "class": cls,
                }
            )
        tests = rec.get("tests") or {}
        failed = [t for t, s in tests.items() if s in ("failed", "error")] + list(
            rec.get("errors") or []
        )
        self.write_findings()
        return {
            "ran": bool(rec.get("ran")),
            "why": rec.get("why"),
            "key": rec.get("key"),
            "own": {
                "passed": sum(1 for s in tests.values() if s == "passed"),
                "failed": failed,
                "total": len(tests) + len(rec.get("errors") or []),
            },
            "readme": rec.get("own"),
            "exam": {
                "passed": passed,
                "open": opens,
                "intermittent": exam.intermittent(rec),
                "flaky": list(rec.get("flaky") or []),
            },
            "reused": exam.reused_words(rec),
        }

    def parent_exam(self) -> tuple[int, dict] | None:
        """The record ``exam.final`` may reuse (a docs revise round's parent's); a build has
        none."""
        return None

    def _count(self, vis: str) -> list[int]:
        rs = [r for r in self.store.active() if r.visibility == vis]
        return [sum(1 for r in rs if self.status.get(r.id) == "passed"), len(rs)]

    def checked_counts(self, rows: list[dict] | None = None) -> dict:
        """What the round checked, as counts (REPORT.md's first section): the spec's claims,
        the confirmed defects (explore ones included), how many are fixed on the delivered
        head and how many open, the claims the verifier could not check, and what it trusts
        least (its own words). Facts, not a prediction (AU1 #5)."""
        rows = [x for x in rows or [] if x.get("status") != "retired"]
        fixed = sum(1 for x in rows if x.get("status") == "fixed")
        se = self.findings.get("self_estimate") or {}
        return {
            "claims": len(self.claims.get("claims") or []),
            "found": len(rows),
            "fixed": fixed,
            "open": len(rows) - fixed,
            "blocked": len(self.findings.get("blocked") or []),
            "least_trusted": list(se.get("least_trusted") or [])[:5],
        }

    def summary(self, rows: list[dict]) -> dict:
        retired = [
            {"id": i, "title": self.store.rules[i].title}
            for i, v in self.panel.items()
            if v["class"] == "retired"
        ]
        return {
            "task": self.task.id,
            "title": self.idea.strip().splitlines()[0].lstrip("# ") if self.idea.strip() else "",
            "branch": self.branch,
            "settings": self.settings,
            "scope": self.scope,
            "completeness": self.completeness(),
            "environment": [
                {"id": i, "title": self.store.rules[i].title}
                for i, v in self.panel.items()
                if v.get("class") == "environment" and i in self.store.rules
            ],
            "checked": self.checked,
            "defects": rows,
            "retired": retired,
            "interpretations": self.interpretations,
            "rounds": self.rounds,
            "stop": self.stop,
            "costs": self.budget.by_stage(),
            "caps_hit": self.budget.hit,
            "stage_caps": dict(self.budget.stage_caps),
            "partial": self.partial,
            "continues": Path(self.carried).name if self.carried else None,
            "cap": self.budget.cap,
            "prebuilt": self._prebuilt_flag,
            "scenarios": {
                "total": len(self.store.rules),
                "active": len(self.store.active()),
                "held": len([r for r in self.store.active() if r.visibility == "held"]),
            },
        }


# ``repair.run_round`` calls ``ctx.session(text, purpose, stage)``; ``Run.session`` is the
# general launcher, so the round gets a thin view with the repair flavour.
class RoundView:
    def __init__(self, run: Run):
        self.__dict__["_run"] = run

    def __getattr__(self, name):
        if name == "session":
            return self._run.session_for_repair
        return getattr(self._run, name)

    def __setattr__(self, name, value):
        setattr(self._run, name, value)
