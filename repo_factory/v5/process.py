"""repo-factory's process files stay out of the shipped product, in every flow (track LEAN).

EV8's blind judge marked repo-factory's products down for bloat, and part of it was repo-factory's own
paperwork shipped in the product: SCOPE.md, the readiness and security reports, the
assumptions, a generated acceptance suite of about 90 files. Those are records of how the
product was built, not the product: they belong to the round (``<den>/v5/r<N>/``), where the
build page, the gate and the evidence read them.

- ``strip(tree, round_dir)``: at delivery, every process file in the tree moves to
  ``<round>/process/`` (the README's lines that ran a moved suite go too);
- ``collect(ws, round_dir)``: after a prompt stage, what it wrote for repo-factory under ``.repo-factory/``
  moves to the round folder (``stages.py``).

Files the product wrote for its own users stay: a ``SECURITY.md`` with its reporting policy,
``AGENTS.md``, CI, its own tests. repo-factory's versions of those are recognised by their own words
(``exam.legacy_files``)."""

from __future__ import annotations

import shutil
from pathlib import Path

DIR = "process"
# At the tree's root: repo-factory's brief and plan, the study, the round's report, the bench's notes
FILES = ("SCOPE.md", "PLAN.md", "STUDY.md", "REPORT.md", "bench-meta.json")
# Folders repo-factory or a stage's session keep for repo-factory: anywhere in the tree for _verify
DIRS = (".repo-factory", "_verify")
HANDOFF = ".repo-factory"


def found(tree: Path) -> list[str]:
    """The process files in ``tree``, as paths relative to it."""
    from .exam import legacy_files

    tree = Path(tree)
    out = [n for n in FILES if (tree / n).is_file()]
    out += [n for n in DIRS if (tree / n).exists()]
    out += [x for x in legacy_files(tree) if x not in out]
    return out


def strip(tree: Path, round_dir: Path) -> list[str]:
    """Move ``found(tree)`` to ``<round_dir>/process/`` (an earlier copy there is
    replaced), and take the README's commands for a moved acceptance suite out with it.
    Returns what moved."""
    from .exam import ACC_SECTION, suite_commands_out

    tree, dest = Path(tree), Path(round_dir) / DIR
    moved = found(tree)
    for rel in moved:
        src, to = tree / rel, dest / rel
        if to.exists() or to.is_symlink():
            shutil.rmtree(to) if to.is_dir() and not to.is_symlink() else to.unlink()
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(to))
    if "tests/acceptance" in moved:
        for p in sorted(tree.glob("README*")):
            if p.is_file():
                t = p.read_text(errors="replace")
                new = suite_commands_out(ACC_SECTION.sub("", t))
                if new != t:
                    p.write_text(new.rstrip() + "\n")
    return moved


def collect(ws: Path, round_dir: Path, allowed=()) -> list[str]:
    """The files a session wrote under ``<ws>/.repo-factory/`` that its stage declares as outputs
    (``allowed``: plain file names) into the round folder, replacing one of that name; the
    rest is dropped (a session never writes the round's own records), and ``.repo-factory/`` is
    removed from the workspace. Returns the names collected."""
    src = Path(ws) / HANDOFF
    if not src.is_dir():
        if src.exists() or src.is_symlink():
            src.unlink()
        return []
    out = []
    for p in sorted(src.iterdir()):
        if p.is_symlink() or not p.is_file() or p.name not in set(allowed):
            continue
        to = Path(round_dir) / p.name
        if to.is_dir() and not to.is_symlink():
            shutil.rmtree(to)
        elif to.exists() or to.is_symlink():
            to.unlink()
        shutil.copyfile(p, to)
        out.append(p.name)
    shutil.rmtree(src, ignore_errors=True)
    return out
