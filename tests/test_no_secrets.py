"""The repo itself passes the loop's pre-push secrets scan: no token shapes, no random values
assigned to secret names, no key, env or database files. Test secrets are built at runtime
(GitGuardian watches the repo; a fake key is still a literal it flags).

Every tracked file is checked, including tests/fixtures/, scripts/ and repo_factory/lab/;
untracked files (a local .env, dens, workspaces) are not the repo's."""

import shutil
import subprocess
from pathlib import Path

import pytest

from repo_factory.loop import secrets

ROOT = Path(__file__).resolve().parents[1]


def tracked() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def test_no_key_env_or_database_files_are_tracked():
    bad = [
        rel
        for rel in tracked()
        for kind, pat, ok in secrets.BAD_FILES
        if pat.search(Path(rel).name) and not (ok and ok.search(Path(rel).name))
    ]
    assert bad == []


def test_tracked_files_have_no_secret_shaped_values():
    hits = []
    for rel in tracked():
        p = ROOT / rel
        if (
            not p.is_file()
            or p.is_symlink()
            or p.name in secrets.LOCKFILES
            or p.stat().st_size > secrets.MAX_BYTES
        ):
            continue
        raw = p.read_bytes()
        if b"\0" in raw[:8192]:
            continue  # binary
        hits += [
            f"{rel}:{n} {kind} {m}"
            for n, kind, m in secrets.scan_text(raw.decode("utf-8", errors="replace"))
        ]
    assert hits == [], "\n".join(hits)


def test_the_maintenance_package_and_its_templates_are_clean():
    """``repo_factory/maintain`` writes its templates into every published product: they carry no
    secret-shaped value, no file a scan refuses, and nothing private (home paths, the host,
    an e-mail address). Checked whether or not the files are tracked yet."""
    pkg = ROOT / "repo_factory" / "maintain"
    files = [p for p in pkg.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    # what publishing writes into a new repo
    files += [ROOT / "repo_factory" / "loop" / "publish.py"]
    revise = ROOT / "policies" / "v5-revise"
    files += [p for p in revise.rglob("*") if p.is_file()]
    assert any(p.parent.name == "templates" for p in files) and len(files) > 20
    # what repo-factory posts on a product's GitHub: the templates, the poll's, a fix's and a
    # release's pull requests and issues (and the prompts that read the issue), and what
    # publishing writes
    posted = {
        "watch.py",
        "words.py",
        "proposals.py",
        "revise.py",
        "release.py",
        "fix.md",
        "reproduce.md",
        "rules.yaml",
        "publish.py",
        "doctor.py",
        "identity.py",
    }
    hits = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        hits += [
            f"{rel}: bad file {kind}"
            for kind, pat, ok in secrets.BAD_FILES
            if pat.search(p.name) and not (ok and ok.search(p.name))
        ]
        text = p.read_text(errors="replace")
        hits += [f"{rel}:{n} {kind} {m}" for n, kind, m in secrets.scan_text(text)]
        if p.parent.name == "templates" or p.name in posted:
            words = _public_words()
            hits += [f"{rel}: {k}" for ln in text.splitlines() for k in words.line_hits(ln)]
    assert hits == [], "\n".join(hits)


def _public_words():
    """The repository's own scanner of private names and paths (scripts/ci/public_words.py)."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "public_words", root / "scripts" / "ci" / "public_words.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
