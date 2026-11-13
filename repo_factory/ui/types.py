"""The shapes of the API's answers the page reads, as TypedDicts: the one source the page's
TypeScript types are generated from (``scripts/gen_ts.py`` writes
``ui/web/src/api/types.gen.ts``; a test fails when it is stale), and checked against the real
answers on the fake app at test time (``tests/test_api_types.py``), so neither side can drift
without a red test.

``OPEN`` names the types whose answers may carry more keys than are written here (the rows
the page still reads untyped, until its page moves); every key written here must still be
there with its type. ``RETURNS`` maps the paths the page reads to what they answer.

Standard library only; nothing here runs on a request. (No ``from __future__ import
annotations``: TypedDict reads ``NotRequired`` only from evaluated annotations.)
"""

import types
import typing
from typing import Any, Literal, NotRequired, TypedDict

# -- the shell's reads -----------------------------------------------------------------------


class Info(TypedDict):
    """``api/info``: the server's root, harness, policies and API version."""

    root: str
    harness: str
    policies: list[str]
    api_version: int
    policy: str


class WhoAmI(TypedDict):
    """``/api/whoami``: who this browser is signed in as (``origin`` None: not signed in)."""

    origin: str | None
    via: str | None
    require_token: bool
    signin: str


class Header(TypedDict):
    """``api/header``: what the header shows."""

    api_version: int
    paused: bool
    running: int
    queued: int
    revising: int
    needs_you: int
    open_ideas: int
    onboarding: list[Any]


class SpendTotals(TypedDict):
    loop: float
    builds: float
    chat: float
    maintain: float
    total: float


class Spend(TypedDict):
    today: SpendTotals
    week: SpendTotals


class LoopStatus(TypedDict):
    """The loop's state (``Loop.status``) with the brief's floor."""

    home: str
    paused: bool
    stop_requested: bool
    daemon_pid: int | None
    concurrency: int
    daily_usd: float | None
    settings_problems: list[Any]
    approval: str
    spend: Spend
    counts: dict[str, int]
    running: list[Any]
    awaiting_approval: list[Any]
    held: list[Any]
    min_buildability: float | None


class Gate(TypedDict):
    verdict: str | None
    reasons: list[Any]
    round: int | None


# -- a product's Builds (UXI-1, ui/runs.py): Build N is round N of the event log --------------


# "in" and "from" are Python keywords: those shapes are written in TypedDict's call form
Tokens = TypedDict("Tokens", {"in": int, "out": int})
RunFrom = TypedDict("RunFrom", {"from": int | None})


class StartedBy(TypedDict):
    by: Literal["you", "repo-factory", "agent", "chat"]
    door: str | None
    name: str | None


class RunGate(TypedDict):
    """The release checks' verdict on one Build, and where it was read from."""

    verdict: str | None
    reasons: list[str]
    source: Literal["record", "idea", "changes", "history"]
    # how many of the release checks passed ("6 of 7"); None when only the verdict is known
    passed: int | None
    total: int


class RunAfter(TypedDict):
    """What happened to a judged Build's result: published, public, kept, or held before the
    push by a pre-push scan (``scan``) or, before 2026-10-16, by the wording checks
    (``wording``)."""

    what: Literal["wording", "scan", "published", "public", "kept"]
    at: float
    why: NotRequired[str]
    url: NotRequired[str | None]


class RunStop(TypedDict):
    at: float | None
    by: str
    resumable: NotRequired[bool]


RunOutcome = Literal[
    "running", "paused", "stopped", "interrupted", "failed", "checks_failed", "ready", "delivered"
]


class Run(RunFrom):
    """One Build: one attempt, immutable once it ends."""

    n: int
    kind: Literal["fresh", "continue", "revision", "fix"]
    mode: str | None
    instructions: str | None
    started_by: StartedBy
    started: float
    ended: float | None
    outcome: RunOutcome
    stage: str | None
    error: str | None
    cost_usd: float | None
    tokens: Tokens | None
    wall_s: int | None
    ran: list[str]
    carried_from: int | None
    stages_n: int
    gate: RunGate | None
    after: RunAfter | None
    stopped: RunStop | None
    resumed: list[float]
    latest: bool


class RunStage(RunFrom):
    """One stage of one Build; ``reason`` says why it did not run (carried over from Build
    ``from``, not needed, the repair rounds' limit)."""

    stage: str
    status: str
    why: str | None
    reason: Literal["carried", "not_needed", "limit", "skipped"] | None
    usd: float | None
    wall_s: float | None
    tokens: Tokens | None
    runs: int | None
    started: float | None


class RepairRound(TypedDict):
    round: int | None
    status: str | None
    sent: int | None
    fixed: Any
    regressions: Any
    why: str | None


class RunsBrief(TypedDict):
    """A Builds row's Builds in brief: how many, and the latest."""

    n: int
    latest: Run


class BuildRuns(TypedDict):
    """``api/build/runs?dir=``: every Build of a product, newest first."""

    key: str
    runs: list[Run]


class Neighbours(TypedDict):
    prev: int | None
    next: int | None


class BuildRun(TypedDict):
    """``api/run?dir=&n=``: one Build, its stages in order, its repair rounds, the v5 view of
    its round (the same as ``api/build``'s ``state.v5`` for the latest)."""

    key: str
    run: Run
    stages: list[RunStage]
    repairs: list[RepairRound]
    v5: dict[str, Any] | None
    of: int
    neighbours: Neighbours


class BuildRow(TypedDict):
    """One build in ``api/state``' ``builds`` (and ``api/build``'s ``row``)."""

    key: str
    dir: str
    status: str
    # "revise can't fix this: …": a held build's hold is repo-factory's own sandbox (ASKF), else None
    cant_revise: str | None
    state: str | None
    title: str
    desc: str
    updated: float | None
    live: bool
    idea: str | None
    idea_state: str | None
    cost_usd: float | None
    repo: str | None
    url: str | None
    gate: Gate | None
    kind: NotRequired[str]
    builds: NotRequired[RunsBrief | None]


class Pitch(TypedDict):
    verdict: str | None
    product: str | None
    one_liner: str | None


class IdeaRow(TypedDict):
    """One task in ``api/state``' ``ideas`` (the ledger's rows): ``state`` ``new`` with no
    ``build`` is the Brief stage."""

    id: str
    status: str
    state: str | None
    title: str
    source: str
    text: str
    reason: str | None
    created: float | None
    updated: float | None
    sent: bool
    pitch: Pitch | None
    build: str | None


class HistoryRow(TypedDict):
    id: int
    idea: str
    title: str | None
    build: str | None
    ts: float
    state: str
    note: str


class ProjectLink(TypedDict):
    """The project a row belongs to (``App.project_links``)."""

    id: str
    name: str


class MaintainRow(TypedDict):
    """One row of the last maintenance poll (``maintain.poll.today``): a repo's issue, PR or
    repo-factory's own ask of it. Its other keys depend on its ``kind``."""

    kind: str
    repo: str
    title: str
    product: str
    idea: str | None
    build: str | None
    project: ProjectLink | None
    number: NotRequired[int | None]
    url: NotRequired[str | None]
    updated: NotRequired[float | None]
    problems: NotRequired[list[str]]


class MaintainToday(TypedDict):
    rows: list[MaintainRow]
    polled: float | None
    onboarding: list[Any]


class AskProject(TypedDict):
    """The project a change belongs to (both None when its record is gone)."""

    id: str | None
    name: str | None


class DiffLine(TypedDict):
    op: Literal["-", "+"]
    line: str


# What a project asks the owner (``App.project_asks``): a change to build, or a scope change
# with its charter ``diff``. "class" is a Python keyword: written in TypedDict's call form.
ProjectAsk = TypedDict(
    "ProjectAsk",
    {
        "id": str,
        "project": AskProject,
        "class": Literal["patch", "minor", "scope"],
        "kind": str,
        "title": str,
        "what": str | None,
        "why": str | None,
        "since": float | None,
        "runs": int | None,
        "evidence": int,
        "diff": list[DiffLine] | None,
        "stale": bool,
    },
)


class Snapshot(TypedDict):
    """``api/state``: everything the pages derive from, in one read."""

    api_version: int
    now: float
    root: str
    harness: str
    status: LoopStatus
    builds: list[BuildRow]
    ideas: list[IdeaRow]
    history: list[HistoryRow]
    maintain: MaintainToday
    project_asks: list[ProjectAsk]


class ActionField(TypedDict):
    name: str
    kind: Literal["text", "line", "enum", "list", "int", "bool"]
    required: bool
    max: int


class ActionOffer(TypedDict):
    """One action ``api/actions`` offers for a subject: enabled, or why not."""

    kind: str
    title: str
    says: str
    click: Literal["click", "confirm"]
    key: str | None
    fields: list[ActionField]
    enabled: bool
    why: str | None
    page: bool


class Subject(TypedDict):
    kind: str
    id: str | None


class ActionList(TypedDict):
    """``api/actions?kind=&id=``."""

    subject: Subject
    actions: list[ActionOffer]


class Outcome(TypedDict, total=False):
    """One press on a subject (``registry.outcomes``): ok, refused or error; why when it did
    not happen."""

    kind: str
    title: str
    origin: str | None
    at: str
    outcome: Literal["ok", "refused", "error"]
    refusal: str | None
    why: str | None
    what: str | None
    idea: str | None  # its subject, in the last presses on anything (PAR)
    build: str | None


class OutcomeSubject(TypedDict):
    kind: str
    id: str | None
    build: str | None
    title: str | None


class Rejudged(TypedDict):
    """Publish anyway's holds as judged today, beside the stored verdict's (ASKF)."""

    was: list[str]
    now: list[str]
    version: str | None


class OutcomeNow(TypedDict, total=False):
    """A press that did not happen, as it would meet the subject now."""

    kind: str
    title: str
    enabled: bool
    why: str | None
    holds: list[str]
    rejudged: Rejudged | None


class OutcomeList(TypedDict):
    """``api/outcomes?kind=&id=``: the subject's last presses, newest first (ASKF), and
    ``now``: each kind that did not happen, as it would meet the subject today."""

    subject: OutcomeSubject
    state: str | None
    reason: str | None
    outcomes: list[Outcome]
    now: list[OutcomeNow]


# -- agent traces (TRC-DESIGN §8.1): every string redacted on read -----------------------------

RunState = Literal["running", "waiting", "ok", "degraded", "failed", "skipped", "stopped"]


class RunTrigger(TypedDict):
    origin: str
    by: str | None
    at: str | None


class RunSubject(TypedDict):
    kind: str
    id: str | None
    name: str | None


class RunProgress(TypedDict):
    step: int | None
    steps: int | None


class RunNeeds(TypedDict):
    """Where a waiting run's press is: the verb and the route (``cub/radar?list=proposals``)."""

    verb: NotRequired[str]
    href: NotRequired[str]
    words: NotRequired[str]


class ActivityRun(TypedDict):
    """One run, in words: the loop's (``loop:<id>``), a build
    (``build:<key>``), a detached job (``pid:<n>``), a session (``session:<tid>``)."""

    id: str
    uuid: str
    kind: str
    title: str
    words: str | None
    state: RunState
    trigger: RunTrigger
    subject: RunSubject | None
    feature: str
    started: str | None
    finished: str | None
    elapsed_s: float | None
    usd: float
    calls: int | None
    tokens: dict[str, int] | None
    progress: RunProgress | None
    trace: str | None
    stoppable: bool
    stopping: bool
    needs: RunNeeds | None
    error: str | None
    why: str | None
    verb: str | None
    glyph: NotRequired[str]


class ActivityLast(TypedDict):
    state: str
    at: str | None
    why: str | None


class ActivityNext(TypedDict):
    """A scheduled kind that is on: when it runs next, how often, its last run."""

    kind: str
    title: str
    at: str
    every_words: str
    last: ActivityLast | None
    failing_since: str | None
    failures_in_a_row: int


class ActivityFold(TypedDict):
    """The hourly skipped re-checks and the usage reports, one line a kind."""

    kind: str
    state: str
    n: int
    first: str | None
    last: str | None
    why: str | None
    title: str
    line: str


class ActivityTry(TypedDict):
    uuid: str
    at: str | None
    error: str | None
    trace: str


class ActivityFail(TypedDict):
    """A Fix row: a kind failing twice in a row or for two days, until its next good run."""

    id: str
    kind: str
    title: str
    since: str | None
    last: str | None
    tries: int
    error: str
    verb: str
    trace: str
    runs: list[ActivityTry]
    # the failure kept by its id (rowsafe.note_failure)
    error_id: NotRequired[str]


class IncidentKind(TypedDict):
    kind: str
    title: str
    noun: str
    since: str | None
    tries: int


class ActivityIncident(TypedDict):
    """A blocking incident: a provider's model key refused, from its first failure, folding
    every kind it stops; the verb renews the key."""

    id: str
    provider: str
    name: str
    cause: str
    blocking: bool
    title: str
    words: str
    since: str | None
    last: str | None
    tries: int
    kinds: list[IncidentKind]
    error: str
    verb: str
    href: str
    trace: str
    error_id: NotRequired[str]
    # a blocked state's short words and the band's tone; a key incident has neither
    short: NotRequired[str]
    tone: NotRequired[str]
    # whether it raises the band: repo-factory's own (a refused model key, ``auth:``; its core,
    # ``core:``) does; absent: decided by the id's class
    band: NotRequired[bool]
    # you may put any incident away (POST api/activity/dismiss {incident}); what makes it go, in
    # words; the failed runs that hold it (a later good call to the provider or a passing key test
    # clears it). Absent: no Dismiss, no words, no runs (each reader says nothing)
    dismiss: NotRequired[bool]
    clears: NotRequired[str]
    runs: NotRequired[list["IncidentRun"]]


class IncidentRun(TypedDict):
    uuid: str
    kind: str
    at: str | None
    error: str
    trace: str


class ActivityCounts(TypedDict):
    running: int
    waiting: int
    failed_recent: int
    failing: int
    building: int
    incidents: NotRequired[int]


class ActivityHealth(TypedDict):
    """repo-factory's health word and one line why; ``trace`` opens the run it is about."""

    word: Literal["Healthy", "Degraded", "Paused"]
    tone: Literal["green", "amber"]
    reason: str | None
    at: str | None
    run: str | None
    trace: str | None


class Activity(TypedDict):
    """``api/activity``: what runs, what waits on you, what comes next, what ran."""

    seq: int
    running: list[ActivityRun]
    waiting: list[ActivityRun]
    next: list[ActivityNext]
    recent: list[ActivityRun]
    folded: list[ActivityFold]
    failing: list[ActivityFail]
    incidents: list[ActivityIncident]
    counts: ActivityCounts
    quiet: str | None
    scheduler: bool
    paused: bool
    now: float | None
    # the status bar's word and why; None when nothing is said
    health: NotRequired[ActivityHealth | None]


# -- the event stream (/api/stream): invalidations, never payloads ------------------------------

ChangeKind = Literal["activity", "builds", "ideas", "jobs", "loop", "projects", "maintain"]


class StreamChange(TypedDict):
    """``event: change``: refetch what these kinds show (``keys``: which, when known)."""

    kinds: list[ChangeKind]
    keys: list[str]
    seq: int


class StreamHost(TypedDict):
    """``event: host``: the server's version; a new one means reload."""

    version: str


OPEN = frozenset(
    {
        "LoopStatus",
        "Gate",
        "BuildRow",
        "Pitch",
        "IdeaRow",
        "MaintainToday",
        "MaintainRow",
        "ActionField",
        "ActionOffer",
    }
)

# what the page reads, by the path it asks for (relative; the sign-in check absolute)
RETURNS: dict[str, type] = {
    "api/info": Info,
    "api/header": Header,
    "api/state": Snapshot,
    "api/actions": ActionList,
    "api/outcomes": OutcomeList,
    "api/build/runs": BuildRuns,
    "api/run": BuildRun,
    "api/activity": Activity,
    "/api/whoami": WhoAmI,
}


# -- the check ---------------------------------------------------------------------------------


def problems(value: Any, tp: Any, where: str = "$") -> list[str]:
    """Where ``value`` (a decoded JSON answer) does not have the shape ``tp`` says, in words;
    empty when it does."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is Any:
        return []
    if tp is None or tp is type(None):
        return [] if value is None else [f"{where}: {value!r:.40} is not null"]
    if origin is Literal:
        return [] if value in args else [f"{where}: {value!r:.40} is not one of {args}"]
    if origin in (typing.Union, types.UnionType):
        tried = [problems(value, a, where) for a in args]
        return [] if any(not t for t in tried) else min(tried, key=len)
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: {type(value).__name__} is not a list"]
        return [p for i, v in enumerate(value) for p in problems(v, args[0], f"{where}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: {type(value).__name__} is not an object"]
        return [p for k, v in value.items() for p in problems(v, args[1], f"{where}.{k}")]
    if typing.is_typeddict(tp):
        if not isinstance(value, dict):
            return [f"{where}: {type(value).__name__} is not an object ({tp.__name__})"]
        out = [
            f"{where}: no {k!r} ({tp.__name__})"
            for k in sorted(tp.__required_keys__)
            if k not in value
        ]
        hints = typing.get_type_hints(tp)
        for k, v in value.items():
            if k in hints:
                out += problems(v, hints[k], f"{where}.{k}")
            elif tp.__name__ not in OPEN:
                out.append(f"{where}: {k!r} is not in {tp.__name__}")
        return out
    if tp is bool:
        return [] if isinstance(value, bool) else [f"{where}: {value!r:.40} is not a boolean"]
    if tp is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{where}: {value!r:.40} is not an integer"]
    if tp is float:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return [] if ok else [f"{where}: {value!r:.40} is not a number"]
    if tp is str:
        return [] if isinstance(value, str) else [f"{where}: {value!r:.40} is not a string"]
    raise TypeError(f"problems: no rule for {tp!r} at {where}")
