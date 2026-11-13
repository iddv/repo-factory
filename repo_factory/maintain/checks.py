"""``maintain.exam`` (EX-PLAN §9, §10): a fix PR checked against the checks repo-factory published
with the product's latest release (``<name>-<version>-checks.tar.gz``, ``loop/publish.py``).

The product's repository no longer carries repo-factory's checks, so the maintenance lane fetches the
asset, reads its scenarios as a rule set (``Asset``: ``exam.run``'s view of it, as
``v5/rounds.Finished`` is for a stored round) and runs them, sandboxed and network off, on main
before the fix and on the fixed tree after it. A check that passed on main and fails on the
fix is a regression. ``report`` (the default) puts the comparison in the PR body; ``require``
stops the fix instead of opening the PR; ``off`` does nothing. A fix that legitimately changes
behaviour breaks a check written against the old one: that is why the default only reports.

A release without the asset (published before the checks moved out, or with
``exam.evidence_asset: false``) has nothing to run; the PR says so."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

from ..resources import POLICIES

MODES = ("report", "require", "off")
RULE_FILE = re.compile(r"^test_rule_(\w+)\.py$")


def mode(settings: dict) -> str:
    m = str((settings or {}).get("exam") or "report")
    return m if m in MODES else "report"


class AssetStore:
    """The asset's scenarios as active rules (``RuleStore``'s ``active`` and ``key``)."""

    def __init__(self, scenarios: Path):
        from ..rules.model import Rule

        self.rules = []
        for p in sorted(Path(scenarios).glob("test_*.py")):
            m = RULE_FILE.match(p.name)
            self.rules.append(
                Rule(
                    id=m.group(1) if m else p.stem,
                    title=p.stem,
                    kind="regression",
                    source="",
                    code=p.read_text(),
                    status="active",
                )
            )

    def active(self) -> list:
        return list(self.rules)

    def key(self, rules: list) -> str:
        return hashlib.sha256(
            json.dumps(sorted((r.id, r.version, r.digest()) for r in rules)).encode()
        ).hexdigest()[:16]


class Asset:
    """An unpacked checks asset as the exam reads a round: ``root`` its work folder,
    ``helpers`` the driver the scenarios share (``exam/_verify``)."""

    def __init__(self, unpacked: Path, work: Path):
        from ..v5.exam import SCENARIOS, VERIFY
        from ..v5.pipeline import load_rules

        self.src = Path(unpacked)
        self.root = Path(work)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = AssetStore(self.src / "exam" / SCENARIOS)
        self.helpers = self.src / "exam" / VERIFY
        self.layout = "exam"
        self.policy = POLICIES / "v5"
        self.rules = load_rules(self.policy)
        self.requirements: list = []
        self.den = type("Den", (), {"root": self.root})()
        self._venv = None
        self._tree = None
        self.events: list = []
        try:
            self.manifest = json.loads((self.src / "manifest.json").read_text())
        except (OSError, ValueError):
            self.manifest = {}

    def use_tree(self, tree: Path) -> None:
        self._tree = Path(tree)

    @property
    def venv(self) -> Path:
        if self._venv is None:
            from ..harness.venv import candidate_venv

            self._venv = candidate_venv(self._tree, self.requirements)[0]
        return self._venv

    def emit(self, type: str, **fields) -> None:
        self.events.append((type, fields))


def unpack(tarball: Path, dest: Path) -> Path | None:
    """The asset's single top folder (``<name>-checks/``), unpacked under ``dest`` with
    ``tarfile``'s data filter (no links out, no device files, no absolute paths)."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tarball) as t:
        t.extractall(dest, filter="data")
    tops = [p for p in dest.iterdir() if p.is_dir()]
    return tops[0] if len(tops) == 1 else None


def _gh(gh, *args: str) -> str:
    from .github import GitHubError

    code, out, err = gh._run(list(args), None)
    if code != 0:
        raise GitHubError(f"gh {args[0]} {args[1]}: {(err or out or '').strip()[:200]}")
    return out


def fetch(gh, full: str, dest: Path) -> tuple[Path | None, str | None, str]:
    """The latest release's checks asset, unpacked: ``(folder or None, tag, why)``."""
    from .github import GitHubError

    dest = Path(dest)
    try:
        tag = json.loads(_gh(gh, "release", "view", "-R", full, "--json", "tagName,assets") or "{}")
    except (GitHubError, ValueError) as e:
        return None, None, f"no release to take the checks from ({str(e)[:120]})"
    names = [
        a.get("name")
        for a in tag.get("assets") or []
        if str(a.get("name") or "").endswith("-checks.tar.gz")
    ]
    if not names:
        return (
            None,
            tag.get("tagName"),
            (
                f"release {tag.get('tagName')} has no checks asset "
                "(published before the checks moved out)"
            ),
        )
    dl = dest / "download"
    if dl.exists():
        shutil.rmtree(dl)
    dl.mkdir(parents=True)
    try:
        _gh(
            gh,
            "release",
            "download",
            tag["tagName"],
            "-R",
            full,
            "--pattern",
            names[0],
            "-D",
            str(dl),
        )
        folder = unpack(dl / names[0], dest / "asset")
    except (GitHubError, OSError, tarfile.TarError, KeyError) as e:
        return None, tag.get("tagName"), f"could not fetch {names[0]} ({str(e)[:160]})"
    from ..v5.exam import SCENARIOS

    if folder is None or not (folder / "exam" / SCENARIOS).is_dir():
        return None, tag.get("tagName"), f"{names[0]} holds no scenarios"
    return folder, tag.get("tagName"), ""


def statuses(rec: dict) -> dict[str, str]:
    return {k: v.get("status") or "error" for k, v in (rec.get("scenarios") or {}).items()}


def run_on(asset: Asset, tree: Path) -> dict:
    """The asset's scenarios on ``tree`` (the flake protocol on: a check that fails once in
    five runs is not a regression)."""
    from ..v5 import exam

    asset.use_tree(tree)
    return exam.run(asset, tree, flake=True)


def compare(before: dict, after: dict) -> dict:
    """``before`` on main, ``after`` on the fix: the counts and the regressions (passed
    before, not after)."""
    b, a = statuses(before), statuses(after)
    reg = sorted(k for k, v in b.items() if v == "passed" and a.get(k) != "passed")
    return {
        "ran": bool(after.get("ran")),
        "checks": len(a),
        "passed": sum(1 for v in a.values() if v == "passed"),
        "failed_on_main": sorted(k for k, v in b.items() if v != "passed"),
        "regressions": reg,
        "details": {
            k: str((after.get("scenarios") or {}).get(k, {}).get("detail") or "")[-300:]
            for k in reg
        },
    }


def line(res: dict | None) -> str:
    """The PR body's line."""
    if not res:
        return ""
    if res.get("why") and not res.get("ran"):
        return f"**repo-factory's checks:** not run: {res['why']}"
    tag = f" from {res['tag']}" if res.get("tag") else ""
    out = f"**repo-factory's checks{tag}:** {res['passed']} of {res['checks']} pass on this branch"
    if res.get("failed_on_main"):
        out += f"; {len(res['failed_on_main'])} already failed on main"
    if res.get("regressions"):
        out += (
            "; passed on main and fail now: "
            + ", ".join(f"`{k}`" for k in res["regressions"][:10])
            + " (a fix that changes the behaviour on purpose breaks a check written "
            "against the old one: the owner decides)"
        )
    else:
        out += "; nothing that passed on main fails"
    return out
