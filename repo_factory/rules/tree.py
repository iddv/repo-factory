"""Reading a candidate's tree: its files as prompt text (``artifact_text``,
``source_text``), and whose fault a failing test is (``broken_reason``,
``missing_dependency``). Moved from ``problems/repo/checklist.py`` (S-5b): the rule
set and v5 use them, the research checklist too."""

from __future__ import annotations

import re
from pathlib import Path

SKIP_DIRS = {
    "acceptance",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".repo_factory",
    ".den",
    "node_modules",
    ".venv",
    "venv",
}


_LOC = re.compile(r"^(\S+?\.py):\d+:(?: in |\s*\w*$)", re.M)
_EXC = re.compile(r"^E\s+(\w+(?:Error|Exception))\b", re.M)
_HEAD = re.compile(r"^(\w+(?:Error|Exception)):")
_LOCAL_BROKEN = {"NameError", "SyntaxError", "IndentationError", "UnboundLocalError"}


def broken_reason(text: str, test_file: str) -> str | None:
    """Why a failure is the test's own fault, or None. Criterion (a): a NameError or
    SyntaxError raised in the test file itself, a missing fixture, or an ImportError of a
    name from an installed/stdlib module (``cannot import name 'x' from 'y' (.../lib/
    python...)``). A missing module the task should create is the candidate's fault."""
    if not text:
        return None
    if re.search(r"fixture '[^']+' not found", text):
        return "missing fixture"
    m = _HEAD.match(text)
    excs = _EXC.findall(text)
    exc = excs[-1] if excs else (m.group(1) if m else None)
    locs = _LOC.findall(text)
    origin = Path(locs[-1]).name if locs else None
    if exc is None and "SyntaxError" in text:
        exc = "SyntaxError"
    base = Path(test_file).name
    if exc in _LOCAL_BROKEN and (origin == base or (exc != "NameError" and base in text)):
        return f"{exc} in the test file"
    if (
        exc == "ImportError"
        and re.search(r"cannot import name .* from .*/lib/python", text)
        and origin == base
    ):
        return "ImportError from an installed module"
    return None


_MISSING = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")


def _local_top(root: Path, name: str) -> bool:
    """``name`` is a top-level module or package of the candidate itself."""
    return any(
        (d / f"{name}.py").is_file() or (d / name).is_dir()
        for d in (Path(root), Path(root) / "src")
    )


def _imports(root: Path, name: str) -> bool:
    """The candidate's own code (not tests, not generated tests) imports ``name``."""
    pat = re.compile(rf"^\s*(from\s+{re.escape(name)}(\.|\s)|import\s+{re.escape(name)}\b)", re.M)
    for p in Path(root).rglob("*.py"):
        rel = p.relative_to(root)
        if set(rel.parts[:-1]) & (SKIP_DIRS | {"tests"}) or p.name.startswith("test_"):
            continue
        try:
            if pat.search(p.read_text(errors="replace")):
                return True
        except OSError:
            continue
    return False


def missing_dependency(text: str, test_file: str, root: Path) -> str | None:
    """The third-party module whose absence made a test fail, when that is the candidate's
    fault, else None. It is when the import fails inside the candidate's code (or inside a
    library it uses), or in the test file for a module the candidate's own code imports: the
    candidate used a package it did not declare, or whose install failed. A missing module
    that the task itself must create (or a stdlib one) is not: criterion (b) handles a
    test that guessed a wrong name."""
    import sys

    m = _MISSING.search(text or "")
    if not m:
        return None
    mod = m.group(1).split(".")[0]
    if mod in sys.stdlib_module_names or _local_top(root, mod):
        return None
    locs = _LOC.findall(text)
    origin = Path(locs[-1]).name if locs else None
    if origin is not None and origin != Path(test_file).name:
        return mod
    return mod if _imports(root, mod) else None


def artifact_text(root: Path, limit: int = 150_000) -> str:
    """The candidate's files as prompt text for the judge (generated tests and the spec
    excluded; truncated)."""
    out, n = [], 0
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (
            not p.is_file()
            or set(rel.parts) & SKIP_DIRS
            or str(rel) == "spec.md"
            or p.suffix in (".pyc", ".db", ".sqlite", ".png", ".jpg", ".ico", ".lock")
        ):
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        chunk = f"--- {rel}\n{text}\n"
        if n + len(chunk) > limit:
            out.append(f"--- {rel} (truncated: {len(text)} chars)")
            continue
        n += len(chunk)
        out.append(chunk)
    return "\n".join(out) or "(empty)"


def is_source(rel: Path) -> bool:
    """A file the miner reads code from: not a test, not documentation."""
    name = rel.name.lower()
    if set(p.lower() for p in rel.parts[:-1]) & {"tests", "test", "docs"}:
        return False
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return False
    if rel.suffix.lower() in (".md", ".rst") or name in ("license", ".gitignore"):
        return False
    return not (rel.suffix.lower() == ".txt" and not name.startswith("requirements"))


def source_text(root: Path, first=(), limit: int = 60_000) -> str:
    """The candidate's source files as prompt text for the miner (build v3 §7c,
    ``mining.lean_prompt``): ``artifact_text`` without tests and docs, the files in
    ``first`` (the milestones the pass is about) ahead of the rest, so a truncation never
    cuts them; the others still appear by name."""
    root = Path(root)
    first = [str(f) for f in first]
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (
            not p.is_file()
            or set(rel.parts) & SKIP_DIRS
            or str(rel) == "spec.md"
            or p.suffix in (".pyc", ".db", ".sqlite", ".png", ".jpg", ".ico", ".lock")
            or not is_source(rel)
        ):
            continue
        files.append((0 if str(rel) in first else 1, str(rel), p))
    out, n = [], 0
    for _, rel, p in sorted(files):
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        chunk = f"--- {rel}\n{text}\n"
        if n + len(chunk) > limit:
            out.append(f"--- {rel} (not shown: {len(text)} chars)")
            continue
        n += len(chunk)
        out.append(chunk)
    return "\n".join(out) or "(empty)"
