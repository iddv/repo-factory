"""The exam's install phase for a Python product's packaging (EX-PLAN §3.1, §5.1).

The exam drives the product as a user would, and a user installs it: ``pip install .`` or
``pipx install .`` builds the package in an isolated environment, and that fetches the build
backend (``[build-system] requires``: setuptools, hatchling, flit-core...) from the package
index. The exam's run has no network, so an install flow failed there for a reason no user
meets (Repo Recall's and Nextread's "F1 Install and first run").

Like the venv the product's tests run in (``sandbox.candidate_venv``: built on the host with
uv, cached by requirement set), the wheels are fetched on the host before the exam runs:
``pip download --only-binary=:all:`` (wheels only, so no package code runs) of pip, setuptools,
wheel and the product's declared build requirements, into
``<den>/exam-cache/py/<key>/wheels``. The exam's run gets that folder read-only at
``/work/deps/wheels`` with ``PIP_FIND_LINKS`` and ``PIP_NO_INDEX``: pip installs from it and
never tries an index, which it could not reach anyway. Only when ``exam.indexes`` allows PyPI;
a failure is noted, never red (the install flow then fails as before)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BASE = ("pip", "setuptools", "wheel")
LEGACY = ("setuptools>=40.8.0", "wheel")  # pip's default for a project without [build-system]
PYPI = ("pypi.org", "files.pythonhosted.org")
TIMEOUT_S = 300
AT = "/work/deps/wheels"
OFF_ENV = "REPO_FACTORY_EXAM_WHEELS"  # "0": never fetch (the test suite: no network)


def needs(tree: Path) -> list[str] | None:
    """The product's build requirements, or None when it is no Python package."""
    tree = Path(tree)
    pp = tree / "pyproject.toml"
    if pp.is_file():
        try:
            import tomllib

            data = tomllib.loads(pp.read_text(errors="replace"))
        except (ValueError, OSError):
            return list(LEGACY)
        reqs = (data.get("build-system") or {}).get("requires")
        if isinstance(reqs, list) and all(isinstance(r, str) for r in reqs):
            return [r.strip() for r in reqs if r.strip()]
        return list(LEGACY) if (data.get("project") or (tree / "setup.py").is_file()) else None
    if (tree / "setup.py").is_file() or (tree / "setup.cfg").is_file():
        return list(LEGACY)
    return None


def python_tag(venv: Path) -> str:
    cfg = Path(venv) / "pyvenv.cfg"
    try:
        m = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+)", cfg.read_text(), re.M)
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def key(reqs: list[str], tag: str) -> str:
    return hashlib.sha256(json.dumps([sorted(set(reqs) | set(BASE)), tag]).encode()).hexdigest()[
        :20
    ]


def cache_root(run) -> Path:
    return Path(run.den.root) / "exam-cache" / "py"


def install(run, tree: Path, cfg: dict) -> dict:
    """``{"wheels": host dir or None, "python": what happened}``."""
    reqs = needs(tree)
    if reqs is None:
        return {"wheels": None}
    if os.environ.get(OFF_ENV) == "0":
        return {"wheels": None, "python": f"no wheels: ${OFF_ENV}=0"}
    if not any(h in (cfg.get("indexes") or []) for h in PYPI):
        return {"wheels": None, "python": "no wheels: exam.indexes does not allow PyPI"}
    venv = Path(run.venv)
    tag = python_tag(venv)
    k = key(reqs, tag)
    dest = cache_root(run) / k
    if (dest / ".ok").is_file():
        return {"wheels": dest / "wheels", "python": f"build backends cached ({k})"}
    res = download(sorted(set(reqs) | set(BASE)), dest, venv)
    if res["ok"]:
        return {
            "wheels": dest / "wheels",
            "python": f"build backends fetched: {', '.join(res['files'][:8])} ({k})",
        }
    return {"wheels": None, "python": f"could not fetch the build backends: {res['why']}"}


def download(reqs: list[str], dest: Path, venv: Path) -> dict:
    """``pip download --only-binary=:all:`` of ``reqs`` for ``venv``'s Python, on the host, into
    ``dest/wheels`` (then ``dest/.ok``)."""
    uv = shutil.which("uv")
    if not uv:
        return {"ok": False, "why": "needs uv, which this machine lacks", "files": []}
    with tempfile.TemporaryDirectory(prefix="repo-factory-wheels-") as tmp:
        tools, out = Path(tmp) / "pip", Path(tmp) / "wheels"

        def run(cmd):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
                return p.returncode, (p.stderr or p.stdout or "").strip()[-600:]
            except subprocess.TimeoutExpired:
                return -1, f"timed out after {TIMEOUT_S} s"

        rc, err = run(
            [uv, "venv", "-q", "--seed", "--python", str(venv / "bin" / "python"), str(tools)]
        )
        if rc == 0:
            rc, err = run(
                [
                    str(tools / "bin" / "python"),
                    "-m",
                    "pip",
                    "download",
                    "-q",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "--dest",
                    str(out),
                    *reqs,
                ]
            )
        if rc != 0:
            return {"ok": False, "why": err or f"exit {rc}", "files": []}
        files = sorted(p.name for p in out.glob("*.whl"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged = dest.with_name(dest.name + ".tmp")
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir()
        shutil.move(str(out), staged / "wheels")
        (staged / ".ok").write_text(json.dumps({"requires": reqs, "files": files}))
        shutil.rmtree(dest, ignore_errors=True)
        staged.rename(dest)
    return {"ok": True, "why": None, "files": files}


def env(at: str) -> dict:
    """pip's environment in the exam: the wheelhouse, never an index."""
    return {"PIP_FIND_LINKS": at, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
