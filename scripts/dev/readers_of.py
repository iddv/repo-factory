#!/usr/bin/env python3
"""Who reads a stored field (RCA 2026-10-23, A6; repo-factory-notes plans/CTR-DESIGN.md §5).

A pull request that adds a value or shape to a stored field (null, a new state, a new key) lists
the field's readers and shows each handles it. This prints them:

  readers_of.py sources.validation              every reader of the column
  readers_of.py sources.validation median_similarity
                                                the readers of one key: the lines that name it

Two lists. **Through the contract**: the files that import the column's contract family (the
module in the package's ``stored/`` that declares
``COLUMN = (table, column)``) and name the contract, a subclass of it, or a type it nests (a
``Card``'s ``Evidence``); when the type's field changes (``float`` to ``float | None``), mypy's
strict modules and the stored-shape lint fail on each reader that does not handle it, so CI red
is the list. **By the table's name**: the other files that query the table and name the column
(or the key), or index a row's dict by the column's name (``idea["pitch"]``, ``.get("pitch")``:
repo_factory's ledger hands its rows out as dicts): the readers still on the raw dict, to read by
hand. A reader in a template, a shell script or the UI's TypeScript is in neither: name it in
the pull request (the UI gets the API type's change through ``afk.gen.ts``).

Standard library only; it reads the source, it runs nothing.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "repo_factory"
SKIP = {"__pycache__", "examples"}


def py_files(root: Path = SRC) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if not SKIP & set(p.parts))


def is_contract_module(f: Path) -> bool:
    return "stored" in f.parts[:-1] or f.name == "stored.py"


@dataclass
class Family:
    """A column's contract and its family: the module, the contract's name, and the names a
    reader may use for it (the contract, its subclasses, the types its fields nest)."""

    module: Path
    contract: str
    names: set[str] = field(default_factory=set)

    def dotted(self) -> str:
        """The module's import path (``repo_factory.stored.ideas``)."""
        rel = self.module.relative_to(SRC.parent).with_suffix("")
        return ".".join(rel.parts)


def _column(cls: ast.ClassDef) -> tuple[str, str] | None:
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "COLUMN"
            and isinstance(stmt.value, ast.Tuple)
        ):
            vals = [e.value for e in stmt.value.elts if isinstance(e, ast.Constant)]
            if len(vals) == 2:
                return str(vals[0]), str(vals[1])
    return None


def families(files: list[Path]) -> dict[tuple[str, str], Family]:
    """(table, column) -> its Family, from every ``COLUMN = (...)`` in a contract module."""
    out: dict[tuple[str, str], Family] = {}
    for f in files:
        if not is_contract_module(f):
            continue
        tree = ast.parse(f.read_text(errors="replace"))
        classes = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
        bases = {n: {b.id for b in c.bases if isinstance(b, ast.Name)} for n, c in classes.items()}
        for name, cls in classes.items():
            col = _column(cls)
            if col is None:
                continue
            fam = Family(f, name, {name})
            # its subclasses in the module (InputIds -> SynthesisInputIds) and its bases
            fam.names |= {n for n, bs in bases.items() if name in bs} | (bases[name] - {"Stored"})
            # the module's types its fields name (Card -> Evidence, Rating, Demand ...)
            for node in ast.walk(cls):
                if isinstance(node, ast.Name) and node.id in classes and node.id != name:
                    fam.names.add(node.id)
            out[col] = fam
    return out


def lines_naming(text: str, word: str) -> list[int]:
    pat = re.compile(rf"""(["'.]){re.escape(word)}\b""")
    return [i for i, ln in enumerate(text.splitlines(), 1) if pat.search(ln)]


def imports_family(text: str, fam: Family) -> bool:
    """Whether a file imports the family's module (``from ..stored.cards import``, ``from
    repo_factory.stored import ideas``, ``from .stored import ...`` in the feature's own folder)."""
    dotted = fam.dotted()
    last = dotted.rsplit(".", 1)[-1]
    parent = dotted.rsplit(".", 1)[0]
    pats = [
        rf"\b{re.escape(dotted)}\b",
        rf"from\s+\.+stored\.{re.escape(last)}\s+import",
        rf"from\s+\.+{re.escape(last)}\s+import",
        rf"from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(last)}\b",
        rf"from\s+\.+stored\s+import\s+[^\n]*\b{re.escape(last)}\b",
    ]
    if fam.module.name == "stored.py":  # a feature's: imported as .stored
        pats.append(r"from\s+\.stored\s+import")
    return any(re.search(p, text) for p in pats)


def main(argv: list[str]) -> int:
    if not argv or "." not in argv[0]:
        print(__doc__)
        return 2
    table, column = argv[0].split(".", 1)
    key = argv[1] if len(argv) > 1 else None
    files = py_files()
    fam = families(files).get((table, column))
    t, c = re.escape(table), re.escape(column)
    table_pat = re.compile(rf"""(?:FROM|JOIN|INTO|UPDATE)\s+{t}\b|["']{t}["']""")
    indexed = re.compile(rf"""\[["']{c}["']\]|\.get\(["']{c}["']""")
    typed, raw = [], []
    for f in files:
        text = f.read_text(errors="replace")
        rel = f.relative_to(ROOT)
        if (
            fam
            and f != fam.module
            and imports_family(text, fam)
            and any(re.search(rf"\b{n}\b", text) for n in fam.names)
        ):
            typed.append((rel, lines_naming(text, key) if key else []))
        elif fam and f == fam.module:
            continue  # the contract itself
        elif (table_pat.search(text) and re.search(rf"\b{c}\b", text)) or indexed.search(text):
            hits = lines_naming(text, key) if key else lines_naming(text, column)
            if hits or not key:
                raw.append((rel, hits))
    what = f"{table}.{column}" + (f" key {key!r}" if key else "")
    if fam:
        print(
            f"{what}: contract {fam.contract} ({fam.module.relative_to(ROOT)}; its family: "
            f"{', '.join(sorted(fam.names))})"
        )
        print(f"\nThrough the contract ({len(typed)} files; mypy and the lint check these):")
        for rel, hits in typed:
            print(f"  {rel}" + (f"  lines {', '.join(map(str, hits))}" if hits else ""))
    else:
        print(f"{what}: no contract type yet (stored/); the readers by name only")
    print(f"\nBy the table's name ({len(raw)} files; read each by hand):")
    for rel, hits in raw:
        print(
            f"  {rel}"
            + (
                f"  lines {', '.join(map(str, hits[:12]))}" + (" ..." if len(hits) > 12 else "")
                if hits
                else ""
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
