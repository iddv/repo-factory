"""Content-addressed artifact snapshots under ``den/artifacts/<sha256>/``.

Also the workspace's last-valid copy: ``check.py`` saves the artifact to
``.repo_factory/last-valid/`` whenever it passes, so a worker that runs out of turns
mid-edit can have its last passing version restored.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

# A worker with a shell (the ``full`` profile) makes environments and caches in the repo:
# never part of the artifact (restricted workers can't create them, so their hashes stay).
ENV_DIRS = {".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_DIRS = {"task", ".git", "__pycache__", ".claude", ".repo_factory", *ENV_DIRS}
LAST_VALID = ".repo_factory/last-valid"
# What the factory writes at the workspace's root (the worker-facing ``check.py``, a session's
# transcript and prompt): never part of the artifact. Only at the root: a product module named
# ``src/<pkg>/check.py`` is the product's (Repo Recall's was dropped from every delivery).
EXCLUDE_FILES = {"check.py", "transcript.jsonl", "prompt.md", "meta.json"}


def _files(src: Path) -> list[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if (
            p.is_file()
            and not (set(rel.parts[:-1]) & EXCLUDE_DIRS)
            and rel.parts[0] not in EXCLUDE_DIRS
            and not (len(rel.parts) == 1 and rel.name in EXCLUDE_FILES)
            and p.suffix != ".pyc"
        ):
            out.append(rel)
    return out


def content_hash(src: Path) -> str:
    h = hashlib.sha256()
    for rel in _files(src):
        h.update(str(rel).encode() + b"\0" + (src / rel).read_bytes() + b"\0")
    return h.hexdigest()


def snapshot(src: Path, artifacts_root: Path) -> str:
    """Copy the worker's output into the store; returns the content hash."""
    src = Path(src)
    digest = content_hash(src)
    dest = artifacts_root / digest
    if not dest.exists():
        tmp = artifacts_root / f".tmp-{digest}-{uuid.uuid4().hex[:8]}"
        shutil.rmtree(tmp, ignore_errors=True)
        for rel in _files(src):
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, tmp / rel)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            tmp.rename(dest)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return digest


NOISE_FILES = {"stderr.txt"}  # harness byproducts, not the worker's code
NOISE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pyc"}
DIFF_SKIP_DIRS = {"acceptance", "task"}  # generated tests, task scaffold


def _code_files(root: Path | None) -> dict[str, Path]:
    if root is None or not Path(root).is_dir():
        return {}
    return {
        str(rel): Path(root) / rel
        for rel in _files(Path(root))
        if rel.name not in NOISE_FILES
        and rel.suffix not in NOISE_SUFFIXES
        and rel.parts[0] not in DIFF_SKIP_DIRS
        and str(rel) != "spec.md"
    }


def changed_files(old: Path | None, new: Path | None) -> list[str]:
    """Files (code, docs, config; not databases, logs or harness output) added, removed or
    changed between two artifact directories."""
    a, b = _code_files(old), _code_files(new)
    return sorted(
        k
        for k in set(a) | set(b)
        if k not in a or k not in b or a[k].read_bytes() != b[k].read_bytes()
    )


def artifact_diff(old: Path | None, new: Path | None, limit: int = 12000) -> str:
    """A unified diff from ``old`` to ``new`` (``changed_files``), truncated to ``limit``
    characters; binary files are named only."""
    import difflib

    a, b = _code_files(old), _code_files(new)
    out, n = [], 0
    for rel in changed_files(old, new):
        try:
            x = a[rel].read_text().splitlines(keepends=True) if rel in a else []
            y = b[rel].read_text().splitlines(keepends=True) if rel in b else []
            chunk = "".join(difflib.unified_diff(x, y, f"a/{rel}", f"b/{rel}"))
        except UnicodeDecodeError:
            chunk = f"Binary file {rel} changed\n"
        if n + len(chunk) > limit:
            out.append(f"... (diff truncated; also changed: {rel})\n")
            n += 60
            if n > limit + 2000:
                break
            continue
        out.append(chunk if chunk.endswith("\n") else chunk + "\n")
        n += len(chunk)
    return "".join(out)


def save_last_valid(workspace: Path) -> None:
    """Replace the workspace's last-valid copy with its current artifact files."""
    workspace = Path(workspace)
    dest = workspace / LAST_VALID
    tmp = dest.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
    for rel in _files(workspace):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, tmp / rel)
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)


def last_valid(workspace: Path) -> Path | None:
    """The workspace's last-valid copy, if there is one with a ``solver.py``. Symlinks
    anywhere in it disqualify it: a worker could plant one pointing outside the
    workspace (the caller still re-checks validity before restoring)."""
    src = Path(workspace) / LAST_VALID
    if src.parent.is_symlink() or src.is_symlink() or not (src / "solver.py").is_file():
        return None
    if any(p.is_symlink() for p in src.rglob("*")):
        return None
    return src


def restore_last_valid(workspace: Path) -> None:
    """Swap the workspace's artifact files for its last-valid copy."""
    workspace = Path(workspace)
    src = workspace / LAST_VALID
    for rel in _files(workspace):
        (workspace / rel).unlink()
    for rel in _files(src):
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, workspace / rel)


def artifact_path(artifacts_root: Path, digest: str) -> Path:
    return artifacts_root / digest
