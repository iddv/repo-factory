"""The provenance scan (EX-PLAN §4.6): does the product's own prose carry repo-factory's process?

It replaces the jargon regexes of the old ``acceptance.scan`` (shapes such as
``(finding|claim|rule) [a-z]{1,2}\\d+``, which also matched a product's own words, "state
item s9"). Two sources, both exact:

1. **The ids this build minted**, read from the round's records (``findings.json``: every
   scenario id and rule id; ``claims.json``: claims and ambiguities; the settings;
   ``readiness.json``: the security findings and the review's own refs). A hit is the exact
   id as a word **next to its own kind word** (``finding S2``, ``scenario x12``, ``claim
   c17``, ``setting s3``, ``rule x12``), or a file name repo-factory gave it (``test_rule_x12``,
   ``test_x12.py``).
2. **repo-factory's own vocabulary**: literal strings repo-factory puts in prompts and its own documents and
   nowhere else (``_verify``, ``acceptance/findings``, ``repo_factory``, ``the verifier``...),
   whole words.

Only the product's prose is read: README*, AGENTS.md, SCOPE.md, SECURITY.md and
``docs/**/*.md``. Code and tests never are (a product's own test may say "scenario" all it
likes)."""

from __future__ import annotations

import json
import re
from pathlib import Path

# Literals only repo-factory writes. Not here: ``tests/acceptance`` (the published repos keep the
# suite they were delivered with as their own, EX-PLAN §8.1, and a product may choose that
# directory for its tests), "the verifier" and "the factory" (plain English an AI-tooling or
# a web product says about itself: the ai_tooling fixture).
VOCABULARY = (
    "_verify",
    "acceptance/findings",
    "tests/verification",
    "REPORT.md",
    "repo_factory",
    "brb",
    "oracle panel",
    "marked xfail at delivery",
    "/work/exam",
    "/work/product",
    "<sandbox>",
)
KINDS = {
    "scenario": ("scenario", "finding", "test", "check"),
    "rule": ("rule", "scenario", "finding", "defect", "fix"),
    "claim": ("claim", "ambiguity"),
    "setting": ("setting",),
    "finding": ("finding", "fix", "defect", "issue"),
}
SKIP_IDS = {"explore", "flow", "security", "readiness", "own", "readme", ""}


def _json(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def minted(round_dir: Path) -> dict[str, set[str]]:
    """``{kind: ids}`` from a round's records (``<den>/v5/r<N>/``)."""
    rd = Path(round_dir)
    out: dict[str, set[str]] = {k: set() for k in KINDS}
    files: set[str] = set()
    f = _json(rd / "findings.json")
    for s in f.get("scenarios") or []:
        if not isinstance(s, dict):
            continue
        sid, rid = str(s.get("id") or ""), str(s.get("rule") or "")
        if sid not in SKIP_IDS:
            out["scenario"].add(sid)
            files |= {f"test_{sid}.py", f"test_{sid}"}
        if rid:
            out["rule"].add(rid)
            files |= {f"test_rule_{rid}.py", f"test_rule_{rid}"}
        if s.get("file"):
            files.add(Path(str(s["file"])).name)
    for s in f.get("settings") or []:
        if isinstance(s, dict) and s.get("id"):
            out["setting"].add(str(s["id"]))
    c = _json(rd / "claims.json")
    for k in ("claims", "ambiguities"):
        for x in c.get(k) or []:
            if isinstance(x, dict) and x.get("id"):
                out["claim"].add(str(x["id"]))
    sec = _json(rd / "readiness.json").get("security") or {}
    for x in sec.get("findings") or []:
        if isinstance(x, dict):
            out["finding"] |= {str(v) for v in (x.get("id"), x.get("ref")) if v}
    out["files"] = {x for x in files if len(x) > 6}
    return {k: {i for i in v if i and i not in SKIP_IDS} for k, v in out.items()}


def product_files(root: Path) -> list[Path]:
    """The product's prose: README*, AGENTS.md, SCOPE.md, SECURITY.md, docs/**/*.md."""
    root = Path(root)
    out = [p for p in sorted(root.glob("README*")) if p.is_file()]
    out += [root / n for n in ("AGENTS.md", "SCOPE.md", "SECURITY.md") if (root / n).is_file()]
    if (root / "docs").is_dir():
        out += [p for p in sorted((root / "docs").rglob("*.md")) if p.is_file()]
    return out


def patterns(ids: dict[str, set[str]]) -> list[tuple[str, re.Pattern]]:
    out = []
    for kind, words in KINDS.items():
        have = sorted(ids.get(kind) or (), key=len, reverse=True)
        if not have:
            continue
        alt = "|".join(re.escape(i) for i in have)
        kw = "|".join(words)
        out.append(
            (f"{kind} id", re.compile(rf"\b(?:{kw})s?\s+(?:id\s+)?`?(?:{alt})`?(?![\w-])", re.I))
        )
    names = sorted(ids.get("files") or (), key=len, reverse=True)
    if names:
        out.append(
            (
                "file name",
                re.compile(
                    r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w-])"
                ),
            )
        )
    for word in VOCABULARY:
        out.append(
            (
                "repo-factory's words",
                re.compile(
                    r"(?<![\w/-])" + re.escape(word) + r"(?![\w-])", re.I if " " in word else 0
                ),
            )
        )
    return out


def scan_text(text: str, pats) -> list[tuple[int, str, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, pat in pats:
            if pat.search(line):
                out.append((n, kind, line.strip()[:160]))
                break
    return out


def scan(
    root: Path, ids: dict[str, set[str]] | None = None, files: list[Path] | None = None
) -> list[dict]:
    """``[{"file", "line", "kind", "text"}]`` in the product's prose of ``root``."""
    root = Path(root)
    pats = patterns(ids or {})
    hits = []
    for p in files if files is not None else product_files(root):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for n, kind, line in scan_text(text, pats):
            hits.append(
                {"file": p.relative_to(root).as_posix(), "line": n, "kind": kind, "text": line}
            )
    return hits


def scan_round(round_dir: Path | None, root: Path, files: list[Path] | None = None) -> list[dict]:
    return scan(root, minted(round_dir) if round_dir else {}, files)


def scan_run(run, root: Path, files: list[Path] | None = None) -> list[dict]:
    """The scan with a running build's own ids (its records are on disk)."""
    return scan_round(getattr(run, "root", None), root, files)
