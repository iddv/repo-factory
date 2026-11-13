"""Release (MAINTAIN.md §5.3) and the lesson label (§9).

After any merge to ``main`` (repo-factory's pull request, the owner's, a Dependabot one), when
``.github/repo-factory.yml`` › ``release.on_merge`` is not ``off``: the newest version tag is compared
with ``main``; when ``main`` has commits since, the pull requests they came from decide the
bump (``bump``: any ``feature`` → minor, else patch; ``on_merge: minor`` always at least
minor), and ``gh``'s REST call creates the tag and a GitHub release with generated notes (the
pull request titles and their issue links). Idempotent: nothing new on main, or a tag that
exists, is a no-op; two merges in one poll make one release.

A merged fix of repo-factory's that a user's report started is recorded as a lesson candidate on
GitHub, never in repo-factory: the issue it fixed gets the ``lesson`` label and one comment with the
``<!-- repo-factory:lesson v1 ... -->`` marker, when the issue is a ``bug`` or ``security`` report,
the fix reproduced it with a test that failed on main, and repo-factory did not open the issue
itself. The nightly learning job reads the label (track M4)."""

from __future__ import annotations

import re

from . import labels as L
from .github import GitHubError, Repo
from .revise import SIGNATURE, TICKET
from .words import META_FIELDS, meta  # noqa: F401 - release.meta, as before

LESSON = "<!-- repo-factory:lesson v1"


def version(tag: str, prefix: str = "v") -> tuple[int, int, int] | None:
    m = re.fullmatch(re.escape(prefix) + r"(\d+)\.(\d+)\.(\d+)", tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def newest(tags: list[str], prefix: str = "v") -> tuple[tuple[int, int, int], str] | None:
    vs = [(v, t) for t in tags if (v := version(t, prefix))]
    return max(vs) if vs else None


def bump(v: tuple[int, int, int], kinds: set[str], on_merge: str = "patch") -> tuple:
    """The next version: minor for a ``feature`` (or ``on_merge: minor``), else patch."""
    if on_merge == "minor" or "feature" in kinds:
        return (v[0], v[1] + 1, 0)
    return (v[0], v[1], v[2] + 1)


def fixed_issues(pr: dict) -> list[int]:
    from .revise import FIXES

    return sorted({int(x) for x in FIXES.findall(f"{pr.get('title')}\n{pr.get('body') or ''}")})


def kinds_of(r: Repo, prs: list[dict]) -> set[str]:
    """The kinds of what the merged pull requests did: their own kind labels and those of the
    issues they fixed."""
    out = set()
    for p in prs:
        out |= {x for x in p["labels"] if x in L.KINDS}
        for n in fixed_issues(p):
            try:
                out |= {x for x in r.issue(n)["labels"] if x in L.KINDS}
            except GitHubError:
                pass
    return out


def lesson(r: Repo, pr: dict) -> dict | None:
    """The lesson candidate of one merged repo-factory fix, when §9's three conditions hold."""
    m = meta(pr.get("body") or "")
    if not m or "repo-factory" not in pr["labels"] or not m.get("test") or not m.get("issue"):
        return None
    n = int(m["issue"])
    issue = r.issue(n)
    if not {"bug", "security"} & set(issue["labels"]) or TICKET in (issue.get("body") or ""):
        return None
    kind = "security" if "security" in issue["labels"] else "bug"
    fields = (
        f"kind={kind} miss={m.get('miss') or 'verify'}"
        + (f" flow={m['flow']}" if m.get("flow") else "")
        + f" test={m['test']} pr={pr['number']}"
    )
    flow = f" ({m['flow']})" if m.get("flow") else ""
    r.set_labels(n, ["lesson"], have=issue["labels"])
    r.comment_with_marker(
        n,
        LESSON,
        (
            f"{LESSON} {fields} -->\nRecorded as a lesson candidate: a documented behaviour{flow} "
            f"failed in a way the build's own checks did not catch, and `{m['test']}` now covers "
            "it. The nightly learning job reads this label; remove it if the bug was not the "
            f"build's fault.\n\n_{SIGNATURE}_\n"
        ),
        edit=False,
    )
    return {"issue": n, "pr": pr["number"], "kind": kind, "miss": m.get("miss")}


def step(r: Repo, pol: dict, *, lessons: bool = True) -> dict | None:
    """One release check of one repo; None when there is nothing to do."""
    rel = pol.get("release") or {}
    on_merge, prefix = rel.get("on_merge") or "off", rel.get("prefix") or "v"
    if on_merge == "off":
        return None
    tags = [t["name"] for t in r.tags()]
    cur = newest(tags, prefix)
    if cur is None:
        return {"skipped": f"no {prefix}X.Y.Z tag to count from"}
    commits = r.compare(cur[1], "main")
    if not commits:
        return None
    prs, seen = [], set()
    for c in commits:
        for p in r.commit_pulls(c["sha"]):
            if p["number"] not in seen:
                seen.add(p["number"])
                prs.append(p)
    kinds = kinds_of(r, prs)
    tag = prefix + ".".join(map(str, bump(cur[0], kinds, on_merge)))
    if tag in tags:
        return {"exists": tag}
    out = {
        "tag": tag,
        "from": cur[1],
        "prs": [p["number"] for p in prs],
        "kinds": sorted(kinds),
        **r.create_release(tag),
    }
    if lessons:
        out["lessons"] = [
            x for x in (lesson(r, p) for p in prs if "repo-factory" in p["labels"]) if x
        ]
    return out
