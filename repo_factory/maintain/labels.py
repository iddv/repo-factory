"""The label scheme (MAINTAIN.md §2): 22 labels in kind, flow, claim, priority, outcome and
learning groups, one hue per group. Labels are the protocol: only people with triage or write
access can set them, so ``go`` is the owner's signature and issue text never is.

``labels_yaml()`` is the repo's ``.github/labels.yml``; ``apply`` creates or updates labels
through ``gh label create --force``, skipping the ones already right, and deletes nothing unless
``prune`` is asked for (never by CI, never by the backfill)."""

from __future__ import annotations

import json

# (name, colour, group, description)
LABELS: list[tuple[str, str, str, str]] = [
    ("bug", "0052cc", "kind", "Something documented does not work"),
    ("feature", "1d76db", "kind", "Something new or changed"),
    ("question", "5ea1e8", "kind", "A question; no code expected"),
    ("security", "003d99", "kind", "A security weakness (report serious ones privately)"),
    ("dependency", "7aa6e8", "kind", "A dependency or Actions update"),
    ("docs", "a2c4f0", "kind", "README, AGENTS.md or SECURITY text"),
    ("task", "4f86c6", "kind", "Upkeep with no failing behaviour: a test, a fixture, a chore"),
    ("triage", "fef2c0", "flow", "Nobody has classified this yet"),
    ("needs-info", "fbca04", "flow", "Waiting for the reporter to answer a question"),
    ("go", "e99c1b", "flow", "Approved: money may be spent fixing this"),
    ("needs-you", "d93f0b", "flow", "repo-factory tried and could not; the owner must look"),
    (
        "claim:repo-factory",
        "5319e7",
        "claim",
        "repo-factory is working on this (see its lease comment)",
    ),
    ("claim:agent", "8a63d2", "claim", "Another agent is working on this (see its comment)"),
    ("p1", "b60205", "priority", "Now: data loss, security, install broken"),
    ("p2", "e11d21", "priority", "Soon: a documented flow is wrong"),
    ("p3", "f9a5a5", "priority", "Later: cosmetic, nice to have"),
    ("duplicate", "cfd3d7", "outcome", "Same as another issue (a comment names it)"),
    ("wontfix", "bfbfbf", "outcome", "Not planned, by decision"),
    ("cannot-reproduce", "d4d4d4", "outcome", "A failing test could not be written"),
    ("upstream", "c5c5c5", "outcome", "A Python or platform problem"),
    ("lesson", "0e8a16", "learning", "A user-found bug the build's checks missed"),
    ("repo-factory", "006b75", "pr", "A pull request opened by repo-factory"),
]

NAMES = [n for n, _, _, _ in LABELS]
KINDS = [n for n, _, g, _ in LABELS if g == "kind"]
PRIORITIES = [n for n, _, g, _ in LABELS if g == "priority"]

# GitHub's default labels that ours do not replace by name. D13: the backfill PR lists the
# commands to prune them; nothing deletes them automatically.
PRUNE_SUGGESTED = ["enhancement", "invalid", "documentation"]
KEEP_SUGGESTED = ["help wanted", "good first issue"]


def labels_yaml() -> str:
    """``.github/labels.yml``: one JSON object per line, which is valid YAML and lets the
    repo's stdlib-only ``sync_labels.py`` read it without a YAML parser."""
    lines = [
        "# The label scheme repo-factory and any agent follow (AGENTS.md › Maintenance protocol).",
        "# Synced by .github/workflows/labels.yml and `repo-factory maintain labels`: created or",
        "# updated, never deleted. One label per line; edit it here, in a pull request.",
        "labels:",
    ]
    group = None
    for name, color, g, desc in LABELS:
        if g != group:
            lines.append(f"  # {g}")
            group = g
        lines.append("  - " + json.dumps({"name": name, "color": color, "description": desc}))
    return "\n".join(lines) + "\n"


def parse_labels_yaml(text: str) -> list[dict]:
    """The reader ``sync_labels.py`` uses, kept identical so the two cannot disagree."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("- {"):
            out.append(json.loads(s[2:]))
    return out


def current(gh, repo: str) -> dict[str, dict]:
    out = gh("label", "list", "--repo", repo, "--limit", "500", "--json", "name,color,description")
    return {x["name"]: x for x in json.loads(out or "[]")}


def apply(gh, repo: str, prune: bool = False, dry_run: bool = False) -> dict:
    """Make the repo's labels match ``LABELS``. One ``gh label create --force`` per label that
    is missing or differs; nothing when everything is already right. ``prune`` deletes labels
    that are not ours (an explicit choice; never the default)."""
    have = current(gh, repo)
    res = {"created": [], "updated": [], "unchanged": [], "pruned": [], "extra": []}
    for name, color, _, desc in LABELS:
        old = have.get(name)
        if old and old.get("color", "").lower() == color and (old.get("description") or "") == desc:
            res["unchanged"].append(name)
            continue
        if not dry_run:
            gh(
                "label",
                "create",
                name,
                "--repo",
                repo,
                "--color",
                color,
                "--description",
                desc,
                "--force",
            )
        res["updated" if old else "created"].append(name)
    res["extra"] = sorted(n for n in have if n not in NAMES)
    if prune:
        for n in res["extra"]:
            if not dry_run:
                gh("label", "delete", n, "--repo", repo, "--yes")
            res["pruned"].append(n)
    return res


def prune_commands(repo: str, extra: list[str]) -> list[str]:
    """The ``gh label delete`` lines the owner may run (D13), for the suggested defaults that
    the repo still has."""
    return [f'gh label delete "{n}" --repo {repo} --yes' for n in PRUNE_SUGGESTED if n in extra]
