"""Hidden ground truth must be unreachable from the orchestrator, harness and scoring."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "repo_factory"
FORBIDDEN = ("hidden_table", ".hidden", "replay.metrics")
GUARDED = ["harness", "owner.py", "v5", "loop"]


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            yield ("." * node.level) + (node.module or "")
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name


def test_guarded_modules_do_not_import_hidden():
    files = []
    for g in GUARDED:
        p = PKG / g
        files += sorted(p.rglob("*.py")) if p.is_dir() else [p]
    assert files
    for f in files:
        for mod in _imports(f):
            assert not any(x in mod for x in FORBIDDEN), f"{f} imports {mod}"
