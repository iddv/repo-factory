"""The lessons file (build v5 §5.1) and injection (§5.2).

``policies/v5/lessons.yaml`` is the promoted default. Everything the loop changes (new
probation lessons from post-mortems, counters from credit, retirements, evictions) goes to the
*candidate* next to it, ``lessons.next.yaml`` (version N+1); only ``repo-factory lessons promote`` makes
a candidate the default (§5.5).

Lessons are plain dicts, exactly the §5.1 YAML shape, so Track D's pipeline can use them as
they are::

    {"id": "L007", "text": "...", "applies_to": "claims", "status": "probation",
     "born": {"pack", "run", "row", "date"},
     "stats": {"exposures", "derived", "caught", "false_alarms", "packs_caught_on"},
     "hit_rate": 0.5}
"""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path

import yaml

from ..resources import POLICIES  # noqa: F401 - ROOT: promote's git commits

POLICY = POLICIES / "v5"
CAP = 20
STATUSES = ("probation", "active", "retired")
LIVE = ("probation", "active")
APPLIES_TO = ("claims", "verifier")  # builder: tier 2, later
STATS = ("exposures", "derived", "caught", "false_alarms")
CANDIDATE = "lessons.next.yaml"


class LessonsError(Exception):
    pass


def default_path(policy: Path | None = None) -> Path:
    return Path(policy or POLICY) / "lessons.yaml"


def candidate_path(policy: Path | None = None) -> Path:
    return Path(policy or POLICY) / CANDIDATE


def empty() -> dict:
    return {"version": 0, "cap": CAP, "lessons": []}


def load(path: Path | None = None) -> dict:
    """The file as a dict with ``version``, ``cap`` and ``lessons`` (each lesson normalised:
    every stat present, ``hit_rate`` recomputed). A missing file is version 0, empty."""
    path = Path(path) if path else default_path()
    data = (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}
    if not isinstance(data, dict):
        raise LessonsError(f"{path}: not a mapping")
    out = {**empty(), **data}
    out["version"] = int(out.get("version") or 0)
    out["cap"] = int(out.get("cap") or CAP)
    out["lessons"] = [normalise(x) for x in (out.get("lessons") or [])]
    return out


def save(path: Path, data: dict) -> Path:
    """Write ``data`` keeping the §5.1 key order; a header comment says what the file is."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": data["version"], "cap": data.get("cap", CAP)}
    for k, v in data.items():
        if k not in ("version", "cap", "lessons"):
            body[k] = v
    body["lessons"] = [_ordered(x) for x in data["lessons"]]
    head = (
        "# The lessons file (docs/design/build-v5.md §5.1). Written by the post-mortem and "
        "promoted by\n# `repo-factory lessons promote` (track E).\n"
    )
    path.write_text(head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100))
    return path


def _ordered(x: dict) -> dict:
    keys = ("id", "text", "applies_to", "status", "born", "stats", "hit_rate")
    return {**{k: x[k] for k in keys if k in x}, **{k: v for k, v in x.items() if k not in keys}}


def normalise(x: dict) -> dict:
    x = dict(x)
    s = {k: int((x.get("stats") or {}).get(k) or 0) for k in STATS}
    s["packs_caught_on"] = list((x.get("stats") or {}).get("packs_caught_on") or [])
    x["stats"] = s
    x.setdefault("applies_to", "claims")
    x.setdefault("status", "probation")
    x["born"] = dict(x.get("born") or {})
    x["hit_rate"] = hit_rate(x)
    return x


def hit_rate(lesson: dict) -> float:
    """caught / exposures (§5.1): the share of builds it was shown to where it found a defect."""
    s = lesson.get("stats") or {}
    n = int(s.get("exposures") or 0)
    return round(int(s.get("caught") or 0) / n, 4) if n else 0.0


def live(data: dict) -> list[dict]:
    return [x for x in data["lessons"] if x.get("status") in LIVE]


def get(data: dict, lid: str) -> dict | None:
    return next((x for x in data["lessons"] if x.get("id") == lid), None)


# -- injection (§5.2) -------------------------------------------------------------------------
def for_build(pack_id: str | None, path: Path | None = None) -> list[dict]:
    """The lessons a build of ``pack_id`` may see: status ``active`` or ``probation`` and
    **not born on ``pack_id``**, best hit rate first (ties: older first). ``pack_id=None`` is a
    new spec (production, held-out): every live lesson applies.

    This is what makes every dev score leave-one-pack-out by construction."""
    data = load(path)
    out = [x for x in live(data) if pack_id is None or x["born"].get("pack") != pack_id]
    return sorted(out, key=lambda x: (-x["hit_rate"], _num(x.get("id"))))


def render(lessons: list[dict], applies_to: str | None = None) -> str:
    """One line per lesson with its rate, for a prompt."""
    lines = []
    for x in lessons:
        if applies_to and x.get("applies_to", "claims") != applies_to:
            continue
        s = x["stats"]
        rate = (
            f"found a defect in {s['caught']} of {s['exposures']} builds"
            if s["exposures"]
            else "not tried yet"
        )
        lines.append(f"- {x['id']} ({rate}): {x['text']}")
    return "\n".join(lines)


# -- changing the file ------------------------------------------------------------------------
def _num(lid) -> int:
    m = re.fullmatch(r"L(\d+)", str(lid or ""))
    return int(m.group(1)) if m else 10**9


def next_id(data: dict) -> str:
    n = max((_num(x.get("id")) for x in data["lessons"] if _num(x.get("id")) < 10**9), default=0)
    return f"L{n + 1:03d}"


def evict_one(data: dict, today: str | None = None) -> dict | None:
    """When the file is full: retire the live lesson with the lowest hit rate, probation
    before active (§5.4); ties go to the oldest. Returns the evicted lesson."""
    pool = [x for x in live(data) if x["status"] == "probation"] or live(data)
    if not pool:
        return None
    x = min(pool, key=lambda x: (x["hit_rate"], _num(x.get("id"))))
    retire(x, "evicted: the file was full and it had the lowest hit rate", today)
    return x


def retire(lesson: dict, why: str, today: str | None = None) -> None:
    lesson["status"] = "retired"
    lesson["retired"] = {"why": why, "date": today or date.today().isoformat()}


def add(
    data: dict, text: str, applies_to: str, born: dict, today: str | None = None
) -> tuple[dict, dict | None]:
    """Append a new ``probation`` lesson; evict first if the live set is at the cap. Returns
    (the lesson, the evicted lesson or None). The caller lints the text first."""
    if applies_to not in APPLIES_TO:
        raise LessonsError(f"applies_to must be one of {APPLIES_TO}, got {applies_to!r}")
    evicted = None
    if len(live(data)) >= int(data.get("cap") or CAP):
        evicted = evict_one(data, today)
    x = normalise(
        {
            "id": next_id(data),
            "text": text.strip(),
            "applies_to": applies_to,
            "status": "probation",
            "born": {"date": today or date.today().isoformat(), **born},
        }
    )
    data["lessons"].append(x)
    return x, evicted


def open_candidate(
    policy: Path | None = None, current: Path | None = None, candidate: Path | None = None
) -> tuple[dict, Path]:
    """The candidate (version N+1) to change: the existing ``lessons.next.yaml``, or a fresh
    copy of the default at N+1."""
    cand = Path(candidate) if candidate else candidate_path(policy)
    if cand.is_file():
        return load(cand), cand
    data = copy.deepcopy(load(current or default_path(policy)))
    data["version"] += 1
    data["based_on"] = data["version"] - 1
    return data, cand
