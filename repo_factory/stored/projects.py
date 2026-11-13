"""A project's record, its change ideas and the weekly editor's runs (CTR-DESIGN §2.4):
``projects.repos``, ``.charter``, ``.topic``, ``.settings``, ``.stats``; ``changes.card``,
``.filed``; ``nominations.bundle``, ``.reply``, ``.nominee``, ``.research``, ``.posted``,
``.verdict_data``.

Each is an object its writer builds (``projects/record.py``, ``projects/changes.py``,
``loop/editor.py``) and its readers index by key; the types keep every key as written and
name the few a reader formats. ``projects.health`` is computed on read (``record.health``),
never stored. A column whose NULL means "none yet" (a charter not drafted, a change not filed,
an editor run that has not replied) is read with ``kept(..., none=True)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Stored


@dataclass(frozen=True)
class Repos(Stored):
    COLUMN = ("projects", "repos")
    LIST_ROOT = "items"
    items: list[dict[str, Any]] = field(default_factory=list)  # {role, repo, ...}, primary first


@dataclass(frozen=True)
class Charter(Stored):
    COLUMN = ("projects", "charter")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CharterVersion(Charter):
    """A charter as one of its versions keeps it (``charter_versions``, append-only)."""

    COLUMN = ("charter_versions", "charter")


@dataclass(frozen=True)
class ProjectTopic(Stored):
    """The topic the project gathers for (``projects/topic.py``; unused for now)."""

    COLUMN = ("projects", "topic")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectSettings(Stored):
    COLUMN = ("projects", "settings")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Stats(Stored):
    COLUMN = ("projects", "stats")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeCard(Stored):
    COLUMN = ("changes", "card")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Filed(Stored):
    """Where a change idea was filed (an issue, a PR), or None before it is."""

    COLUMN = ("changes", "filed")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NominationBundle(Stored):
    """What the editor read: counts, the cards' and dispatches' ids, the prompt's record."""

    COLUMN = ("nominations", "bundle")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NominationReply(Stored):
    COLUMN = ("nominations", "reply")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Nominee(Stored):
    COLUMN = ("nominations", "nominee")
    title: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Research(Stored):
    """The research desk's dossier, or ``{error}``."""

    COLUMN = ("nominations", "research")
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Posted(Stored):
    """The card verdicts posted elsewhere (``post_verdicts``; unused for now)."""

    COLUMN = ("nominations", "posted")
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerdictData(Stored):
    """The owner's verdict's facts (reasons, a note, the days of a Later, when)."""

    COLUMN = ("nominations", "verdict_data")
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
