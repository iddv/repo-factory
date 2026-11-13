"""Run pytest on a candidate under the solver sandbox (#6): network on by default (off with
``$REPO_FACTORY_TEST_NETWORK=0``, task.yaml ``network: false``, or ``network=False`` as the bench
does), and in every case the tests see
only a throwaway copy of the candidate plus a venv (pytest, the task's ``requirements`` and
the candidate's own declared dependencies, see ``candidate_venv``), never the den or the
repo_factory package. Venvs are built once per requirement set, outside the sandbox."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from .isolation import base_python, isolation, sandbox_cmd

TEST_TIMEOUT_S = 120
SKIP = shutil.ignore_patterns(".git", "__pycache__", ".repo_factory", ".pytest_cache")


def venv_root() -> Path:
    return Path(
        os.environ.get("REPO_FACTORY_VENV_ROOT")
        or Path.home() / ".cache" / "repo_factory" / "venvs"
    )


def _venv_key(reqs: list[str]) -> str:
    return hashlib.sha256("\n".join([sys.version, *reqs]).encode()).hexdigest()[:12]


def ensure_venv(requirements: list[str]) -> Path:
    """A venv holding pytest and the task's requirements, keyed by their hash."""
    reqs = sorted({"pytest", *requirements})
    path, err = _build_venv(reqs)
    if path is None:
        raise RuntimeError(f"could not build the test venv: {err}")
    return path


INSTALL_TIMEOUT_S = 600


_venv_locks: dict[str, threading.Lock] = {}
_venv_locks_guard = threading.Lock()


def _build_venv(reqs: list[str]) -> tuple[Path | None, str | None]:
    """Build (or reuse) the venv for ``reqs`` (already sorted), outside the sandbox with
    network (uv, retried ``--offline``). Returns ``(path, None)`` or ``(None, error)``.
    One build per key at a time in this process (background mining scores in parallel
    with the main thread, and the temp dir is per process)."""
    key = _venv_key(reqs)
    with _venv_locks_guard:
        lock = _venv_locks.setdefault(key, threading.Lock())
    with lock:
        return _build_venv_locked(reqs, key)


def relink(path: Path) -> list[str]:
    """Point the venv's scripts (``pip``, ``pytest``) at the venv where it lives. It is built
    under a temporary name and renamed, so their ``#!`` named a directory that is gone: a
    bare ``pip install pytest`` in AGENTS.md exited 127, "pip: not found", in every fix's
    check (driftwatch, 2026-10-17). Returns the scripts it rewrote; a venv built before this
    is repaired when it is next used."""
    tmp = re.compile(re.escape(str(path.with_name(f".tmp-{path.name}-"))).encode() + rb"\d+")
    done = []
    for f in sorted((path / "bin").iterdir()) if (path / "bin").is_dir() else []:
        if f.is_symlink() or not f.is_file():
            continue
        data = f.read_bytes()
        head, n = tmp.subn(str(path).encode(), data[:1024])
        if not data.startswith(b"#!") or not n:
            continue
        new = f.with_name(f".{f.name}.{os.getpid()}.tmp")
        new.write_bytes(head + data[1024:])
        new.chmod(f.stat().st_mode)
        new.replace(f)
        done.append(f.name)
    return done


def _build_venv_locked(reqs: list[str], key: str) -> tuple[Path | None, str | None]:
    path = venv_root() / key
    py = path / "bin" / "python"
    if py.exists() and (path / ".ok").exists():
        relink(path)
        return path, None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{key}-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)

    def run(cmd) -> tuple[int, str]:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
            return p.returncode, (p.stderr or p.stdout or "").strip()
        except subprocess.TimeoutExpired:
            return -1, f"timed out after {INSTALL_TIMEOUT_S}s"
        except OSError as e:
            return -1, str(e)

    uv = shutil.which("uv")
    if uv:
        rc, err = run([uv, "venv", "-q", "--python", base_python(), str(tmp)])
        if rc == 0:
            cmd = [uv, "pip", "install", "-q", "--python", str(tmp / "bin" / "python"), *reqs]
            rc, err = run(cmd)
            if rc != 0:
                rc2, err2 = run(cmd + ["--offline"])
                rc, err = (rc2, err2) if rc2 == 0 else (rc, err)
    else:
        rc, err = run([base_python(), "-m", "venv", str(tmp)])
        if rc == 0:
            rc, err = run([str(tmp / "bin" / "python"), "-m", "pip", "install", "-q", *reqs])
    if rc != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, err[-1500:] or f"exit {rc}"
    (tmp / ".ok").write_text("")
    try:
        tmp.rename(path)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
    relink(path)
    return path, None


# -- a candidate's own declared dependencies ----------------------------------------------
TEST_GROUPS = ("test", "tests", "testing", "dev")
_REQ_FILE = re.compile(r"^(requirements([-_.][\w.-]+)?|[\w-]+[-_]requirements)\.txt$", re.I)
_LOCAL_REQ = re.compile(r"^(\.|/|~|file:|-e\b|--editable\b)")
_failed: dict[str, str] = {}  # venv key -> install error (this process only)


def dependency_files(root: Path | None) -> list[str]:
    """The candidate's dependency declarations at its root: ``requirements*.txt`` (also
    ``dev-requirements.txt`` etc.) and ``pyproject.toml``."""
    if root is None or not Path(root).is_dir():
        return []
    return sorted(
        p.name
        for p in Path(root).iterdir()
        if p.is_file() and (_REQ_FILE.match(p.name) or p.name == "pyproject.toml")
    )


def _requirement_lines(path: Path, root: Path, seen: set) -> list[str]:
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ", "-r", "--requirement=")):
            ref = re.sub(r"^(-r|--requirement)[ =]?", "", line).strip()
            inc = (path.parent / ref).resolve()
            if inc.is_relative_to(Path(root).resolve()):
                out += _requirement_lines(inc, root, seen)
            continue
        if line.startswith("-") or _LOCAL_REQ.match(line):
            continue  # options, constraints, editable/local installs: the code is on PYTHONPATH
        out.append(line)
    return out


def declared_dependencies(root: Path | None) -> list[str]:
    """Requirement strings the candidate declares: its requirements files, and from
    ``pyproject.toml`` the project dependencies plus the test/dev extras and dependency
    groups. Local and editable entries are skipped (the candidate's code is on
    PYTHONPATH when tests run, it is never installed)."""
    import tomllib

    out: list[str] = []
    files = dependency_files(root)
    seen: set = set()
    for name in files:
        if name != "pyproject.toml":
            out += _requirement_lines(Path(root) / name, Path(root), seen)
    if "pyproject.toml" in files:
        try:
            pp = tomllib.loads((Path(root) / "pyproject.toml").read_text())
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            pp = {}
        proj = pp.get("project") or {}
        out += [d for d in proj.get("dependencies") or [] if isinstance(d, str)]
        for k, v in (proj.get("optional-dependencies") or {}).items():
            if k in TEST_GROUPS:
                out += [d for d in v or [] if isinstance(d, str)]
        for k, v in (pp.get("dependency-groups") or {}).items():
            if k in TEST_GROUPS:
                out += [d for d in v or [] if isinstance(d, str)]
    return sorted({d.strip() for d in out if d.strip() and not _LOCAL_REQ.match(d.strip())})


def candidate_venv(root: Path | None, requirements: list[str]) -> tuple[Path, dict]:
    """The venv a candidate's tests run in: pytest, the task's ``requirements`` and the
    candidate's own declared dependencies (``declared_dependencies``), installed outside the
    sandbox with network and cached by the hash of that requirement set (candidates that
    declare the same dependencies share one venv; one that declares none uses the task venv).

    Returns ``(venv, install)``; ``install`` is ``{"status": "none" | "ok" | "failed",
    "deps": [...], "detail": str}``. If the install fails, the task venv is returned (the
    tests then fail at import, which is the candidate's fault) and ``status`` says why."""
    deps = declared_dependencies(root)
    if not deps:
        return ensure_venv(requirements), {"status": "none", "deps": [], "detail": ""}
    reqs = sorted({"pytest", *requirements, *deps})
    key = _venv_key(reqs)
    err = _failed.get(key)
    path = None
    if err is None:
        path, err = _build_venv(reqs)
    if path is not None:
        return path, {"status": "ok", "deps": deps, "detail": f"{len(deps)} declared"}
    _failed[key] = err
    return ensure_venv(requirements), {
        "status": "failed",
        "deps": deps,
        "detail": f"installing {', '.join(deps)} failed: {err}",
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m repo_factory.harness.venv <dir> [task requirement ...]``: the
    worker-facing ``check.py`` asks for its venv this way (so it uses the same one as
    scoring). Prints one JSON line: ``{"python", "status", "detail"}``."""
    import json

    argv = sys.argv[1:] if argv is None else argv
    venv, info = candidate_venv(Path(argv[0]) if argv else Path("."), argv[1:])
    print(
        json.dumps(
            {
                "python": str(venv / "bin" / "python"),
                "status": info["status"],
                "detail": info["detail"],
            }
        )
    )
    return 0


def default_network() -> bool:
    """Factory test runs get network unless ``$REPO_FACTORY_TEST_NETWORK=0`` (owner's call,
    2026-10-03): the filesystem isolation is what protects the judge and the owner's secrets;
    network-off only bought determinism, which the bench keeps by passing network=False."""
    return os.environ.get("REPO_FACTORY_TEST_NETWORK", "1") != "0"


def run_pytest(
    src: Path,
    overlays: dict[str, Path | None],
    targets: list[str],
    venv: Path,
    timeout_s: int = TEST_TIMEOUT_S,
    network: bool | None = None,
    collect: str | None = None,
) -> dict:
    """Copy ``src`` to a scratch dir, apply ``overlays`` (relative dest -> source dir/file,
    None deletes), and run pytest on ``targets`` sandboxed. Returns
    ``{"tests": {test_id: "passed"|"failed"|"error"|"skipped"}, "errors": [file, ...],
    "details": {test_id or file: failure text}, "returncode": int, "isolation": str}``;
    files that failed to collect are listed in ``errors`` (all their tests count as failed by
    the caller). ``details`` (tail of each failure's traceback) feeds the test-quality filter.
    ``collect`` (a directory relative to the copy, e.g. the hidden suite's output dir) adds
    ``"collected": {relative path: text}`` with every file the run left there."""
    with tempfile.TemporaryDirectory(prefix="repo_factory-tests-") as tmp:
        work = Path(tmp) / "a"
        if src is not None and Path(src).is_dir():
            shutil.copytree(src, work, ignore=SKIP, symlinks=False)
        work.mkdir(exist_ok=True)
        for rel, source in overlays.items():
            dest = work / rel
            if dest.is_dir():
                shutil.rmtree(dest)
            elif dest.exists():
                dest.unlink()
            if source is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            (
                shutil.copytree(source, dest, ignore=SKIP)
                if Path(source).is_dir()
                else shutil.copy(source, dest)
            )
        junit = work / ".brb-junit.xml"
        py = str(venv / "bin" / "python")
        # HOME: a real user's shell has one; a product that resolves it (Node's os.homedir(),
        # Path.home()) must not fail here only because the sandbox cleared it. /tmp is the
        # sandbox's own private tmpfs.
        env = (("PYTHONPATH", str(work)), ("PYTHONDONTWRITEBYTECODE", "1"), ("HOME", "/tmp"))
        net = default_network() if network is None else network
        cmd = sandbox_cmd(work, ro=(venv,), interp=py, env=env, network=net) + [
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--continue-on-collection-errors",
            "--rootdir",
            str(work),
            f"--junitxml={junit}",
            "-o",
            "junit_family=xunit1",
            *targets,
        ]
        penv = (
            None
            if isolation() == "bwrap"
            else {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(work),
                "PYTHONDONTWRITEBYTECODE": "1",
                "HOME": tmp,
            }
        )
        extra = {}
        try:
            p = subprocess.run(
                cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s, env=penv
            )
            rc = p.returncode
        except subprocess.TimeoutExpired:
            if collect is not None:
                extra["collected"] = _collect(work / collect)
            return {
                "tests": {},
                "errors": list(targets),
                "details": {},
                "returncode": -1,
                "isolation": isolation(),
                "network": net,
                **extra,
            }
        tests, errors, details = (
            parse_junit(junit, work) if junit.exists() else ({}, list(targets), {})
        )
        if collect is not None:
            extra = {
                "collected": _collect(work / collect),
                "output": ((p.stdout or "") + (p.stderr or ""))[-DETAIL_CHARS * 4 :],
            }
    return {
        "tests": tests,
        "errors": errors,
        "details": details,
        "returncode": rc,
        "isolation": isolation(),
        "network": net,
        **extra,
    }


def _collect(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text(errors="replace")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


DETAIL_CHARS = 1500


def _detail(el, work: Path | None) -> str:
    text = ((el.get("message") or "") + "\n" + (el.text or "")).strip()
    if work is not None:  # scratch paths differ per run; keep it stable
        text = text.replace(str(work) + "/", "")
    return text[-DETAIL_CHARS:]


def parse_junit(path: Path, work: Path | None = None) -> tuple[dict, list, dict]:
    tests: dict[str, str] = {}
    errors: list[str] = []
    details: dict[str, str] = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        err = tc.find("error")
        if err is not None and "collection failure" in (err.get("message") or ""):
            f = name.replace(".", "/") + ".py"
            errors.append(f)
            details[f] = _detail(err, work)
            continue
        file = tc.get("file") or (cls.replace(".", "/") + ".py")
        tid = f"{file}::{name}"
        fail = tc.find("failure")
        if fail is not None:
            tests[tid] = "failed"
            details[tid] = _detail(fail, work)
        elif err is not None:
            tests[tid] = "error"
            details[tid] = _detail(err, work)
        elif tc.find("skipped") is not None:
            tests[tid] = "skipped"
        else:
            tests[tid] = "passed"
    return tests, errors, details


if __name__ == "__main__":
    sys.exit(main())
