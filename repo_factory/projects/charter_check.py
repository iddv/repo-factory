"""The charter, checked on a fix's diff (PJ-PLAN.md §5.3): the model's class is backed by code
at the moment a change would land.

A maintenance fix (``maintain.revise.Fix``, ``maintain.repofix.RepoFix``) of a repo that is a
project with a charter runs ``problems`` in its deliver step, beside the scope check that keeps
workflows, the policy, the licence and the notice out of every fix:

1. **The promises' anchors stay in the README**: a promise's anchor (its first eight words)
   that the README had before the fix and no longer has holds the fix with that sentence.
2. **No new dependency where the charter says standard library only** (pyproject, the
   requirements files, package.json, compared with the default branch's).

A fix held here is not tried again by itself: it waits for the owner (``needs-you``) with the
sentence that failed. No model is called."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

STDLIB = re.compile(
    r"standard library only|stdlib only|no (?:third[- ]party|external|new) "
    r"(?:runtime )?dependenc",
    re.I,
)
DEP_FILES = ("pyproject.toml", "package.json")
REQS = re.compile(r"(^|/)requirements[\w.-]*\.txt$")


def charter_of(home, repo: str) -> dict | None:
    """The charter of the project whose repo is ``repo`` (``owner/name`` or an adopted key)."""
    from ..loop.ledger import Ledger
    from ..settings.store import Config
    from .record import Projects

    db = Config(home).db
    if not Path(db).exists():
        return None
    p = Projects(Ledger(db)).by_repo(repo)
    return (p or {}).get("charter")


def deps(name: str, text: str | None) -> set[str]:
    """The dependency names a pyproject, a requirements file or a package.json declares."""
    if not text:
        return set()
    out: set[str] = set()
    try:
        if name.endswith("pyproject.toml"):
            d = tomllib.loads(text)
            proj = d.get("project") or {}
            lines = list(proj.get("dependencies") or [])
            for v in (proj.get("optional-dependencies") or {}).values():
                lines += list(v or [])
            lines += list(((d.get("tool") or {}).get("poetry") or {}).get("dependencies") or {})
        elif name.endswith("package.json"):
            d = json.loads(text)
            return set(d.get("dependencies") or {}) | set(d.get("peerDependencies") or {})
        else:
            lines = [
                x for x in text.splitlines() if x.strip() and not x.strip().startswith(("#", "-"))
            ]
    except (ValueError, tomllib.TOMLDecodeError):
        return set()
    for line in lines:
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(line))
        if m and m.group(1).lower() != "python":
            out.add(m.group(1).lower().replace("_", "-"))
    return out


def problems(charter: dict | None, root: Path, changed: list[str], base) -> list[str]:
    """What in this fix breaks the charter, each one sentence; [] when nothing does.
    ``base(path)`` is the file's text on the default branch (None when it did not exist)."""
    if not charter:
        return []
    from .drift import anchor_of, anchor_present

    out = []
    if "README.md" in changed:
        before = base("README.md") or ""
        now = (
            (root / "README.md").read_text(errors="replace")
            if (root / "README.md").is_file()
            else ""
        )
        for p in charter.get("promises") or []:
            a = (
                p.get("anchor")
                if isinstance(p, dict) and p.get("anchor")
                else anchor_of(p.get("text") if isinstance(p, dict) else p)
            )
            if a and anchor_present(before, a) and not anchor_present(now, a):
                out.append(f'charter: the promise "{a}…" is no longer in the README')
    rule = next((c for c in charter.get("constraints") or [] if STDLIB.search(str(c))), None)
    if rule:
        for f in changed:
            if Path(f).name in DEP_FILES or REQS.search(f):
                cur = (root / f).read_text(errors="replace") if (root / f).is_file() else None
                new = sorted(deps(f, cur) - deps(f, base(f)))
                if new:
                    out.append(
                        f'charter: {f} adds {", ".join(new)} where the charter says "{rule}"'
                    )
    return out


def for_fix(home, repo: str, root: Path, changed: list[str], base) -> list[str]:
    """``problems`` for the fix of ``repo``, never failing the fix for its own errors."""
    try:
        return problems(charter_of(home, repo), Path(root), changed, base)
    except Exception:  # noqa: BLE001 - the guard is best effort
        return []
