"""The product's largest import cycle stays small (CR-PLAN S-10). Every import counts, the ones
inside a function too: a lazy import still ties the two modules together for a reader and for
a refactor. When a change breaks more of a cycle, lower ``LARGEST``; never raise it."""

import ast
import sys
from collections import defaultdict
from pathlib import Path

import repo_factory

LARGEST = 18  # 2026-10-14: 73 before S-10 (maintain and projects still need each other)
PKG = Path(repo_factory.__file__).parent


def _modname(p: Path) -> str:
    parts = list(p.relative_to(PKG.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _graph() -> dict[str, set[str]]:
    files = [
        p
        for p in PKG.rglob("*.py")
        if "__pycache__" not in p.parts and "lab" not in p.relative_to(PKG).parts[:1]
    ]
    mods = {_modname(p): p for p in files}
    edges: dict[str, set[str]] = defaultdict(set)
    for m, p in mods.items():
        is_pkg = p.name == "__init__.py"
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = m.split(".") if is_pkg else m.split(".")[:-1]
                    base = base[: len(base) - (node.level - 1)]
                    t = ".".join(base + ([node.module] if node.module else []))
                else:
                    t = node.module or ""
                targets = [t] + [f"{t}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            else:
                continue
            for t in targets:
                while t and t not in mods:
                    t = t.rpartition(".")[0]
                if t and t != m:
                    edges[m].add(t)
    return {m: edges[m] for m in mods}


def _largest_cycle(adj: dict[str, set[str]]) -> list[str]:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    best: list[str] = []

    def visit(v):
        nonlocal best
        index[v] = low[v] = len(index)
        stack.append(v)
        on.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > len(best):
                best = comp

    for v in sorted(adj):
        if v not in index:
            visit(v)
    return sorted(best)


def test_the_largest_import_cycle_does_not_grow():
    big = _largest_cycle(_graph())
    assert len(big) <= LARGEST, f"{len(big)} modules import each other: {big}"


def test_the_low_layers_stay_out_of_every_cycle():
    """gh, the delivered tree, the honesty check, proc and paths import nothing that imports
    them back."""
    adj = _graph()
    for low in (
        "repo_factory.gh",
        "repo_factory.loop.tree",
        "repo_factory.loop.honesty",
        "repo_factory.proc",
        "repo_factory.paths",
    ):
        seen, todo = set(), list(adj[low])
        while todo:
            m = todo.pop()
            if m not in seen:
                seen.add(m)
                todo += adj.get(m, ())
        assert low not in seen, f"{low} reaches itself again"
