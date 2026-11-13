"""The loop ledger's ideas and their history (CTR-DESIGN §2.4): ``ideas.urls``, ``.card``,
``.pitch``, ``.build``, ``.gate``, ``.publish``, ``.signals`` and ``history.data``.

Each type names the fields its writers store and readers format or compare, with the type
the writer stores; everything else stays in ``extra``, as written. ``ideas.context`` is the
fetched links' text (``intake.enrich``), not JSON.

- **pitch**: the model's reply as ``pitch.normalise`` leaves it, then ``pitch_idea``'s record
  (``usd``, ``prompt``, ``with``, ``by``), or a failed pitch's ``{errors, last, problems}``, or
  the owner's send mark (``sent``: a time, or True on a draft).
- **build**: the daemon's round record (``status``, ``pid``, ``started``, ``round``, ``runs``...).
- **gate**: ``gate.evaluate``'s verdict, or a hold's ``{verdict: "held", reasons}``.
- **publish**: the publish record (``url``, ``repo``, ``owner``, ``kept``, ``attempts``...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Stored


@dataclass(frozen=True)
class Urls(Stored):
    COLUMN = ("ideas", "urls")
    LIST_ROOT = "items"
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IdeaCard(Stored):
    """An idea card as the loop keeps it (rows from before typed tasks), with what the loop adds (``accepted``,
    ``declined``, ``undo``...)."""

    COLUMN = ("ideas", "card")
    title: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Pitch(Stored):
    COLUMN = ("ideas", "pitch")
    verdict: str | None = None
    product: str | None = None
    product_type: str | None = None
    one_liner: str | None = None
    who: str | None = None
    why_now: str | None = None
    brief: str | None = None
    reject_reason: str | None = None
    buildability: float | None = None
    grounding: list[str] = field(default_factory=list)
    evidence_used: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    errors: int | None = None
    problems: list[str] = field(default_factory=list)
    last: dict[str, Any] | None = None
    sent: float | bool | None = None
    usd: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Build(Stored):
    COLUMN = ("ideas", "build")
    status: str | None = None
    pid: int | None = None
    started: float | None = None
    round: int | None = None
    runs: int | None = None
    stopping: bool | None = None
    heavy: bool | None = None
    capabilities: list[str] = field(default_factory=list)
    started_by: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Gate(Stored):
    COLUMN = ("ideas", "gate")
    verdict: str | None = None
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    round: int | None = None
    branch: str | None = None
    cost_usd: float | None = None
    before_build: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Publish(Stored):
    COLUMN = ("ideas", "publish")
    url: str | None = None
    repo: str | None = None
    owner: str | None = None
    version: str | None = None
    error: str | None = None
    kept: float | None = None
    attempts: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Signals(Stored):
    """What the loop collected about a published repo (``signals.collect``), by source."""

    COLUMN = ("ideas", "signals")
    github: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HistoryData(Stored):
    """A history row's facts (the source taken in from, a pitch's links, the door an owner's
    action came through)."""

    COLUMN = ("history", "data")
    origin: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
