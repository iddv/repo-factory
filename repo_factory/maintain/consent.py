"""Whose word starts a fix: your own ``go``, on an issue whose text nobody else changed since.

The fix session is given the issue's title and body as its task. Your ``go`` label says you
read that text; so a fix starts only when

- the ``go`` that stands now was put on by you (the issue's events: the last ``labeled`` or
  ``unlabeled`` of ``go`` wins), and
- when someone else opened the issue: neither its title (a ``renamed`` event) nor its body
  (GitHub's ``lastEditedAt``) was changed by anyone but you after that ``go``.

Read at the poll, at the fix's start and again before its push (``watch.py``,
``revise.Fix.owners_go``). An issue that fails either is a "Waiting for you" row with the words
here; nothing is written on it. When the edit time cannot be read, it waits too (the safe side).
"""

from __future__ import annotations

from .github import GitHubError, epoch

EDITED = "edited after you labelled it: take go off and put it on again"


def _login(x) -> str | None:
    return x.get("login") if isinstance(x, dict) and isinstance(x.get("login"), str) else None


def last_go(events, label: str = "go") -> dict | None:
    """The ``labeled`` event of ``label`` that stands now (``{"by", "at"}``), or None when the
    label was removed after, or never added (``from_issue.labelled_by``'s rule)."""
    got = None
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("label"), dict):
            continue
        if e["label"].get("name") != label:
            continue
        if e.get("event") == "labeled":
            got = {"by": _login(e.get("actor")), "at": epoch(e.get("created_at"))}
        elif e.get("event") == "unlabeled":
            got = None
    return got


def renamed(events, me: set[str]) -> float | None:
    """When the title was last changed by someone other than ``me`` (None: never)."""
    at = None
    for e in events if isinstance(events, list) else []:
        if isinstance(e, dict) and e.get("event") == "renamed":
            if _login(e.get("actor")) in me:
                continue
            t = epoch(e.get("created_at"))
            at = max(at or 0.0, t or float("inf"))  # a rename with no time counts as late
    return at


def why_not(r, item: dict, me: set[str], events: list | None = None) -> str | None:
    """Why ``item`` is not your word now, or None. ``r``: the ``github.Repo``; ``events``:
    the issue's events when already read."""
    me = {x for x in me if x}
    events = r.events(item["number"]) if events is None else events
    go = last_go(events)
    if not go or go["by"] not in me:
        who = (go or {}).get("by")
        return f"labelled go by {who or 'nobody now'}, not by you: only your own go starts a fix"
    if item.get("author") in me:
        return None  # your own issue: its text is yours
    try:
        body = r.last_edit(item["number"])
    except GitHubError as e:
        return f"could not read when it was last edited ({str(e)[:120]}): it waits"
    title_at = renamed(events, me)
    body_at = body.get("at") if body.get("by") not in me else None
    edits: list[float] = [float(x) for x in (title_at, body_at) if x is not None]
    if not edits:
        return None
    if go["at"] is None or max(edits) >= go["at"]:
        return EDITED
    return None
