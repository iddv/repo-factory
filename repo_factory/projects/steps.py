"""Where an accepted change is, in what is true now (UXD-DESIGN §2, the stepper; UXI).

A change's own state (``changes.state``) says what repo-factory decided: ``queued`` when it was
accepted, ``closed`` once its issue is closed (``building`` and ``held`` are older rows' copies
of GitHub, read as ``queued``: AU3 finding 6). On its own it would not know that its pull
request waits for you, or that its fix gave up ("cannot reproduce"). ``step`` reads the rest: the issue it was filed
as, the fix's run in its task folder (``fix-<owner>-<repo>-<issue>``: running, a PR opened,
could not fix) and the last poll (is that PR still open), and says where the change is, as the
stepper draws it:

    Accepted › Issue #4 filed › repo-factory is fixing it › Pull request #11 waits for you › Merged

Each step is ``done``, ``now``, ``fail`` or ``todo``; ``at`` names the current one and
``words`` says it in one sentence. Read-only: no GitHub call, no model.
"""

from __future__ import annotations

STEP_KEYS = ("accepted", "filed", "fixing", "pr", "merged")


def _steps(at: str, fail: bool = False, words: dict | None = None) -> list[dict]:
    words = words or {}
    base = {
        "accepted": "Accepted",
        "filed": "Issue filed",
        "fixing": "repo-factory fixes it",
        "pr": "Pull request",
        "merged": "Merged",
    }
    i = STEP_KEYS.index(at)
    out = []
    for n, k in enumerate(STEP_KEYS):
        state = "done" if n < i else ("fail" if fail else "now") if n == i else "todo"
        if at == "merged" and n == i:
            state = "done"
        out.append({"k": k, "word": words.get(k, base[k]), "state": state})
    return out


def step(ch: dict, fix: dict | None = None) -> dict:
    """Where the change ``ch`` (a ``changes`` row as the project page lists it: ``state``,
    ``filed``, ``declined``, ``later_until``) is. ``fix``: the build row of its fix's task folder
    (``status`` running, fixed, closed or tried; ``reason``; ``pr``; ``key``), when one exists.
    Returns ``{at, words, steps, tone, running, needs_you, pr, issue, fix}``."""
    st = ch.get("state")
    f = ch.get("filed") or {}
    issue = (
        {"number": f.get("number"), "url": f.get("url"), "repo": f.get("repo")}
        if f.get("number")
        else None
    )
    out = {
        "at": st,
        "words": "",
        "steps": [],
        "tone": "grey",
        "running": False,
        "needs_you": False,
        "pr": None,
        "issue": issue,
        "fix": (fix or {}).get("key"),
    }
    if st == "new":
        return {
            **out,
            "at": "proposed",
            "words": "Proposed: accept it and repo-factory files it",
            "tone": "blue",
        }
    if st == "skipped":
        return {**out, "at": "tracked", "words": "Already tracked on GitHub"}
    if st == "later":
        return {**out, "at": "later", "words": "Not now"}
    if st == "closed" and (ch.get("declined") or not ch.get("accepted")):
        return {**out, "at": "declined", "words": "Declined"}
    words = {"filed": f"Issue #{issue['number']} filed" if issue else "Issue filed"}
    if not issue and st in ("queued", "building", "held"):
        return {
            **out,
            "at": "accepted",
            "tone": "blue",
            "running": True,
            "words": "Accepted: repo-factory files the issue on its next tick",
            "steps": _steps("filed", words={"filed": "Filing the issue…"}),
        }
    if issue and not f.get("go"):
        why = f.get("go_held") or "the repo's policy does not let repo-factory fix this kind"
        return {
            **out,
            "at": "filed",
            "tone": "grey",
            "words": f"Issue #{issue['number']} filed; repo-factory does not fix it: {why}",
            "steps": _steps("fixing", fail=True, words={**words, "fixing": "yours to build"}),
        }
    fs = (fix or {}).get("status")
    pr = (fix or {}).get("pr") or None
    if pr:
        out["pr"] = pr
        words["pr"] = f"Pull request #{pr.get('number')}"
    if fs == "running":
        return {
            **out,
            "at": "fixing",
            "tone": "blue",
            "running": True,
            "words": "repo-factory is fixing it"
            + (f": {fix.get('reason')}" if (fix or {}).get("reason") else ""),
            "steps": _steps("fixing", words={**words, "fixing": "repo-factory is fixing it"}),
        }
    if fs == "fixed":
        return {
            **out,
            "at": "pr",
            "tone": "amber",
            "needs_you": True,
            "words": f"Pull request #{(pr or {}).get('number')} waits for your review",
            "steps": _steps(
                "pr",
                words={**words, "pr": f"Pull request #{(pr or {}).get('number')} waits for you"},
            ),
        }
    if fs == "closed" and pr:
        return {
            **out,
            "at": "merged",
            "tone": "green",
            "words": f"Pull request #{pr.get('number')} is no longer open: merged or "
            "closed on GitHub",
            "steps": _steps("merged", words={**words, "merged": "Merged or closed"}),
        }
    if fs == "closed":
        return {
            **out,
            "at": "done",
            "tone": "grey",
            "words": (fix or {}).get("reason") or "Nothing to do",
            "steps": _steps("fixing", fail=True, words={**words, "fixing": "not started"}),
        }
    if fs == "tried":
        why = str((fix or {}).get("reason") or "")
        could = (
            "Could not reproduce it"
            if why.startswith("Could not reproduce")
            else "Could not fix it"
        )
        return {
            **out,
            "at": "could_not",
            "tone": "red",
            "needs_you": True,
            "words": why or could,
            "steps": _steps("fixing", fail=True, words={**words, "fixing": could}),
        }
    if st == "closed":
        return {
            **out,
            "at": "done",
            "tone": "green",
            "words": "Done",
            "steps": _steps("merged", words=words),
        }
    # filed with go and no fix folder yet: the next poll picks it up
    return {
        **out,
        "at": "filed",
        "tone": "blue",
        "words": f"Issue #{(issue or {}).get('number')} filed: repo-factory starts the fix on its "
        "next poll",
        "steps": _steps("fixing", words={**words, "fixing": "repo-factory starts soon"}),
    }
