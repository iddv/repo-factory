"""Run pytest on a candidate under the solver sandbox (#6): network on by default (off with
``$REPO_FACTORY_TEST_NETWORK=0``, task.yaml ``network: false``, or ``network=False`` as the bench
does), and in every case the tests see
only a throwaway copy of the candidate plus a venv (pytest, the task's ``requirements`` and
the candidate's own declared dependencies, see ``candidate_venv``), never the den or the
repo_factory package. Venvs are built once per requirement set: the empty venv on the host, every
install inside bubblewrap whose one way out is the index proxy (``install_sandboxed``)."""

from __future__ import annotations

import contextlib
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

from .. import paths
from . import links
from .isolation import base_python, isolation, sandbox_cmd

TEST_TIMEOUT_S = 120
SKIP = shutil.ignore_patterns(".git", "__pycache__", ".repo_factory", ".pytest_cache")


def venv_root() -> Path:
    return Path(os.environ.get("REPO_FACTORY_VENV_ROOT") or paths.cache() / "venvs")


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


# -- installs, inside the sandbox ------------------------------------------------------------
# A requirement set may name packages a model chose. Their install code (a source package's
# build) and their start-up code (a wheel's ``.pth``) run only inside bubblewrap: its own
# network namespace, its one way out the host's index proxy with the ``exam.indexes``
# allow-list (``v5/nodeinstall.py`` does the same for npm). The host makes the empty venv and
# never starts its interpreter.
DEFAULT_INDEXES = ("pypi.org", "files.pythonhosted.org")
EXAM_ENV = "REPO_FACTORY_EXAM"  # the loop's exam settings, as v5/exam.py reads them
PROXY_DIR = "/run/repo_factory"
NO_SANDBOX = (
    "a product's packages install only inside bubblewrap, and bwrap is missing or does not "
    "work here (repo-factory doctor says why)"
)


def indexes() -> list[str]:
    """The names the install may reach: ``exam.indexes`` as the loop passes it
    (``$REPO_FACTORY_EXAM``), else PyPI. An empty list: no network (the cache only)."""
    import json

    try:
        got = json.loads(os.environ.get(EXAM_ENV) or "{}")
    except ValueError:
        got = {}
    v = got.get("indexes") if isinstance(got, dict) else None
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    return list(DEFAULT_INDEXES)


def build_cache() -> Path:
    """The uv cache the sandboxed installs use: their own, beside the venvs."""
    return Path(os.environ.get("REPO_FACTORY_VENV_UV_CACHE") or venv_root().parent / "venv-uv")


def install_sandboxed(
    venv: Path,
    reqs: list[str],
    hosts: list[str],
    run,
    offline: bool = False,
    wheelhouse: Path | None = None,
):
    """``uv pip install reqs`` into ``venv`` inside bubblewrap. ``hosts``: the proxy's
    allow-list (empty: no network at all). ``run(cmd) -> (rc, text)``. The venv is bound at
    its own path (its scripts name it), uv and its cache too; nothing else of the home.
    ``wheelhouse``: a local folder of packages to install from, read-only (the tests)."""
    from . import netproxy

    uv = os.path.realpath(shutil.which("uv") or "uv")
    cache = build_cache()
    cache.mkdir(parents=True, exist_ok=True)
    line = [uv, "pip", "install", "-q", "--no-config", "--python", str(venv / "bin" / "python")]
    line += ["--offline"] if offline or not hosts else []
    if wheelhouse is not None:
        line += ["--find-links", str(wheelhouse)]
    line += list(reqs)
    env = [
        ("HOME", "/home/user"),
        ("PATH", "/usr/local/bin:/usr/bin:/bin"),
        ("LANG", "C.UTF-8"),
        ("UV_CACHE_DIR", str(cache)),
        ("UV_LINK_MODE", "copy"),
        ("UV_NO_CONFIG", "1"),
        ("UV_PYTHON_DOWNLOADS", "never"),
        ("PIP_DISABLE_PIP_VERSION_CHECK", "1"),
    ]
    with (
        tempfile.TemporaryDirectory(prefix="repo-factory-pip-") as tmp,
        contextlib.ExitStack() as st,
    ):
        work, bindir = Path(tmp) / "work", Path(tmp) / "bin"
        work.mkdir()
        bindir.mkdir()
        binds = [(uv, uv, True), (cache, cache, False), (venv, venv, False)]
        if wheelhouse is not None:
            binds.append((wheelhouse, wheelhouse, True))
        tail: list[str] = list(line)
        if hosts:
            px = st.enter_context(netproxy.Proxy(hosts=list(hosts)))
            shutil.copy(Path(netproxy.__file__), bindir / "netproxy.py")
            binds += [(px.dir, PROXY_DIR, False), (bindir, "/work/bin", True)]
            env += [
                (k, "http://127.0.0.1:3128")
                for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
            ]
            tail = ["-I", "/work/bin/netproxy.py", "--bridge", f"{PROXY_DIR}/proxy.sock"]
            tail += ["3128", "--", *line]
        cmd = sandbox_cmd(
            work,
            interp=base_python() if hosts else uv,
            env=tuple(env),
            network=False,
            etc=True,
            home="/home/user",
            work_at="/work/product",
            binds=tuple(binds),
        )
        rc, err = run(cmd + (tail if hosts else line[1:]))
        if rc != 0 and hosts:
            # what the proxy refused or could not reach, so the reason is in the message
            seen = [f"{v} {t}" for v, t in list(px.log) if v != "allowed"][-4:]
            if seen:
                err = f"{err} (the index proxy: {'; '.join(seen)})"
        return rc, err


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
    if not uv:
        return None, "installing a product's packages needs uv, which this machine lacks"
    if isolation() != "bwrap":
        return None, NO_SANDBOX
    # the empty venv on the host (no package code runs), every install inside the sandbox
    rc, err = run([uv, "venv", "-q", "--python", base_python(), str(tmp)])
    if rc == 0:
        rc, err = install_sandboxed(tmp, reqs, indexes(), run)
        if rc != 0:
            rc2, err2 = install_sandboxed(tmp, reqs, [], run, offline=True)
            rc, err = (rc2, err2) if rc2 == 0 else (rc, err)
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
# the product itself (``.``, ``-e .``): its code is on PYTHONPATH, it is never installed
_SELF_REQ = re.compile(r"^(-e\s+|--editable[\s=]+)?\.[/\\]?(\[[\w,.\s-]*\])?$")
_failed: dict[str, str] = {}  # venv key -> install error (this process only)


def refused(req: str) -> bool:
    """A requirement installed only by name from an allowed index: one with ``@`` (a direct
    reference), a URL or a path is refused (and named), never fetched."""
    r = req.strip()
    if _SELF_REQ.match(r):
        return False
    return "@" in r or "://" in r or bool(_LOCAL_REQ.match(r)) or r.startswith(("git+", "hg+"))


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
        if _SELF_REQ.match(line):
            continue  # the product itself: its code is on PYTHONPATH
        if line.startswith("-") and not line.startswith(("-e", "--editable")):
            continue  # options (an index, constraints): the install uses its own
        out.append(line)
    return out


def declared_dependencies(root: Path | None) -> list[str]:
    """Requirement strings the candidate declares and the install takes: its requirements
    files, and from ``pyproject.toml`` the project dependencies plus the test/dev extras and
    dependency groups. The product itself (``.``, ``-e .``) is skipped (its code is on
    PYTHONPATH when tests run); a direct reference, URL or path is left out (``refused``,
    named by ``refused_dependencies``)."""
    return [d for d in _declared(root) if not refused(d)]


def refused_dependencies(root: Path | None) -> list[str]:
    """What the candidate declares that the install refuses (``refused``)."""
    return [d for d in _declared(root) if refused(d)]


def _declared(root: Path | None) -> list[str]:
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
    return sorted({d.strip() for d in out if d.strip() and not _SELF_REQ.match(d.strip())})


def candidate_venv(root: Path | None, requirements: list[str]) -> tuple[Path, dict]:
    """The venv a candidate's tests run in: pytest, the task's ``requirements`` and the
    candidate's own declared dependencies (``declared_dependencies``), installed inside the
    sandbox (``install_sandboxed``) and cached by the hash of that requirement set
    (candidates that declare the same dependencies share one venv; one that declares none
    uses the task venv).

    Returns ``(venv, install)``; ``install`` is ``{"status": "none" | "ok" | "failed",
    "deps": [...], "refused": [...], "detail": str}``. If the install fails, the task venv is
    returned (the tests then fail at import, which is the candidate's fault) and ``status``
    says why. ``refused``: declared requirements with ``@``, a URL or a path, never
    installed; ``detail`` names them."""
    deps = declared_dependencies(root)
    no = refused_dependencies(root)
    said = (
        f"; not installed: {', '.join(no)} (a direct reference, URL or path: name the "
        "package and its version instead)"
        if no
        else ""
    )
    if not deps:
        return ensure_venv(requirements), {
            "status": "none",
            "deps": [],
            "refused": no,
            "detail": said[2:],
        }
    reqs = sorted({"pytest", *requirements, *deps})
    key = _venv_key(reqs)
    err = _failed.get(key)
    path = None
    if err is None:
        path, err = _build_venv(reqs)
    if path is not None:
        return path, {
            "status": "ok",
            "deps": deps,
            "refused": no,
            "detail": f"{len(deps)} declared{said}",
        }
    _failed[key] = err
    return ensure_venv(requirements), {
        "status": "failed",
        "deps": deps,
        "refused": no,
        "detail": f"installing {', '.join(deps)} failed: {err}{said}",
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
            links.copytree(src, work, ignore=SKIP)
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
                links.copytree(source, dest, ignore=SKIP)
                if Path(source).is_dir()
                else links.copy_file(source, dest)
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
