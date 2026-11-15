"""Release: a tag and a GitHub release after a merge (with the repository's release switch on).

When ``.github/repo-factory.yml`` › ``release.on_merge`` is not ``off``: the newest version tag
is compared with ``main``; when ``main`` has commits since, the pull requests they came from
decide the bump (``bump``: any ``feature`` → minor, else patch; ``on_merge: minor`` always at
least minor), and ``gh``'s REST call creates the tag and a GitHub release with generated notes
(the pull request titles and their issue links). Idempotent: nothing new on main, or a tag that
exists, is a no-op. Nothing is labelled or commented."""

from __future__ import annotations

import re

from . import labels as L
from .github import GitHubError, Repo


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


def step(r: Repo, pol: dict) -> dict | None:
    """One release check of one repo; None when there is nothing to do."""
    rel = pol.get("release") or {}
    on_merge, prefix = rel.get("on_merge") or "off", rel.get("prefix") or "v"
    if on_merge == "off":
        return None
    tags = [t["name"] for t in r.tags()]
    cur = newest(tags, prefix)
    if cur is None:
        return {"skipped": f"no {prefix}X.Y.Z tag to count from"}
    commits = r.compare(cur[1])
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
    return {
        "tag": tag,
        "from": cur[1],
        "prs": [p["number"] for p in prs],
        "kinds": sorted(kinds),
        **r.create_release(tag),
    }
