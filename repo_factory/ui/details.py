"""Details: the record behind a row or a dialog.

The screen shows what decides; the record is one tap away (the Details drawer). A record is its
title, the page it opens on, and short groups of facts (What · Who, when · The rules it meets ·
Evidence · The run · Log), each a list of [label, value] rows.

``sel`` names the object as a row's ``data-sel`` does: ``b<build key>`` or ``i<task id>``.
Every stored value is read for what it is (a missing key, a null, a list or a dict): a record
never fails on a shape it did not expect, it says less.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .http import HttpError

ORDER = ("What", "Who, when", "The rules it meets", "Evidence", "Asked of you", "The run", "Log")
LONG = 600


def _words(k: str) -> str:
    """a key in words: created_at → created at"""
    return str(k).replace("_", " ").strip()


def _when(v) -> str | None:
    """a time in words: an epoch or an ISO string as "2026-10-29 18:54 UTC"; else None"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and 1e9 < v < 1e11:
        return datetime.fromtimestamp(v, UTC).strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(v, str) and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d", v):
        return v[:16].replace("T", " ") + " UTC"
    return None


def _value(v) -> str | None:
    """a stored value as words, whatever its shape; None for nothing"""
    if v is None or v == "" or v == [] or v == {}:
        return None
    w = _when(v)
    if w:
        return w
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:g}" if isinstance(v, float) else str(v)
    if isinstance(v, list):
        parts = [
            p for p in (_value(x) if not isinstance(x, dict) else _brief(x) for x in v[:8]) if p
        ]
        more = f" and {len(v) - 8} more" if len(v) > 8 else ""
        return ("; ".join(parts) + more) or None
    if isinstance(v, dict):
        return _brief(v)
    s = str(v)
    return s if len(s) <= LONG else s[:LONG] + "…"


def _brief(d: dict) -> str | None:
    """a small dict in one line: its title or name, else its first few facts"""
    for k in ("title", "name", "label", "words", "text"):
        if isinstance(d.get(k), str) and d[k]:
            return _value(d[k])
    parts = [
        f"{_words(k)} {_value(v)}"
        for k, v in list(d.items())[:4]
        if _value(v) and not isinstance(v, (dict, list))
    ]
    return ", ".join(parts) or None


def _rows(d: dict, keys=None, skip=()) -> list[list[str]]:
    out = []
    for k in keys if keys is not None else list(d):
        if k in skip or k not in d:
            continue
        v = _value(d.get(k))
        if v is not None:
            out.append([_words(k), v])
    return out


def _record(sel: str, kind: str, title: str, page: str | None, groups: dict[str, list]) -> dict:
    return {
        "sel": sel,
        "kind": kind,
        "title": title or sel,
        "page": page,
        "groups": [{"h": h, "rows": groups[h]} for h in ORDER if groups.get(h)],
        "tool": f"api/details?sel={sel}",
    }


def _build(a, key: str, sel: str) -> dict:
    b = next(
        (
            x
            for x in a.builds()
            if isinstance(x, dict)
            and (x.get("key") == key or str(x.get("key", "")).endswith("/" + key))
        ),
        None,
    )
    if b is None:
        raise HttpError(404, f"no build {key!r}")
    k = b.get("key") or key
    raw = b.get("fail")
    fail: dict = raw if isinstance(raw, dict) else {}
    return _record(
        sel,
        "build",
        b.get("title") or k,
        f"#/builds/{k}",
        {
            "What": _rows(b, ("status", "title", "desc", "reason", "repo", "url"))
            + (
                [["failed at", f"{fail.get('stage') or 'a stage'}: {fail.get('line')}"]]
                if fail.get("line")
                else []
            ),
            "Who, when": _rows(b, ("idea", "updated", "created")),
            "The rules it meets": _rows(b, ("gate", "state")),
            "The run": _rows(b, ("tokens", "cost_usd", "wall_s")),
        },
    )


def _idea(a, iid: str, sel: str) -> dict:
    # a row names a task "i" + its id, and a task's id starts with "i" itself: either is found
    idea = a.loop_idea(iid) or a.loop_idea("i" + iid)
    base = idea if isinstance(idea, dict) else {}
    if not base:
        raise HttpError(404, f"no task {iid!r}")
    return _record(
        sel,
        "task",
        base.get("title") or iid,
        "#/builds",
        {
            "What": _rows(base, ("state", "title", "source", "text")),
            "Who, when": _rows(base, ("created", "updated", "id")),
            "The run": _rows(base, ("build", "pitch", "sent")),
        },
    )


def record(a, q: dict) -> dict:
    """GET /api/details?sel=…: the record of the object a row or a dialog names."""
    sel = str((q or {}).get("sel") or "").strip()
    if not sel or len(sel) > 200:
        raise HttpError(400, "sel names the object: b<build key> or i<task id>")
    if sel.startswith("b") and len(sel) > 1:
        return _build(a, sel[1:], sel)
    if sel.startswith("i") and len(sel) > 1:
        return _idea(a, sel[1:], sel)
    raise HttpError(404, f"Details does not know {sel!r}: b<build key> or i<task id>")


def said(rec: dict) -> str:
    """the record in one line, the answer first"""
    first: list = next((g["rows"] for g in rec.get("groups") or [] if g.get("rows")), [])
    facts = "; ".join(f"{k}: {v}" for k, v in first[:3])
    return (
        f"{rec.get('title')}{' (' + rec['page'] + ')' if rec.get('page') else ''}: {facts}."
        if facts
        else str(rec.get("title"))
    )
