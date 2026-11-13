import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

# -- Isolation, before any repo_factory import ----------------------------------------------------
# No test may open the developer's home (~/.repo-factory), token store, gh login or a model: HOME is a temp dir for the whole session (each xdist worker has its own), the
# caches stay on the real ones (they only hold venvs and wheels), the network is loopback only,
# and `claude` and `gh` on PATH are stand-ins that fail loudly.
_REAL_HOME = Path.home()
os.environ.setdefault(
    "REPO_FACTORY_VENV_ROOT", str(_REAL_HOME / ".cache" / "repo_factory" / "venvs")
)
os.environ.setdefault(
    "REPO_FACTORY_WORKER_UV_CACHE", str(_REAL_HOME / ".cache" / "repo_factory" / "worker-uv")
)
os.environ.setdefault(
    "REPO_FACTORY_WORKER_VENV_ROOT", str(_REAL_HOME / ".cache" / "repo_factory" / "worker-venvs")
)
os.environ.setdefault("UV_CACHE_DIR", str(_REAL_HOME / ".cache" / "uv"))
TEST_HOME = Path(tempfile.mkdtemp(prefix="repo-factory-home-")).resolve()
os.environ["HOME"] = str(TEST_HOME)
for _k in (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "REPO_FACTORY_UNIT",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "REPO_FACTORY_DOTENV_VARS",
    "REPO_FACTORY_CLAUDE_DIR",
):
    os.environ.pop(_k, None)
# the one home is inside the temp HOME, whatever the developer's environment says
os.environ["REPO_FACTORY_HOME"] = str(TEST_HOME / "repo-factory-home")
SHIMS = Path(__file__).resolve().parent / "_shims"
os.environ["PATH"] = f"{SHIMS}{os.pathsep}{os.environ.get('PATH', '')}"

_LOOPBACK = {"localhost", "", "0.0.0.0", "::", "::1"}


def _loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    h = str(host).strip("[]").lower()
    return h in _LOOPBACK or h.startswith("127.") or h.endswith(".localhost")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _guard(sock, address):
    if sock.family == getattr(socket, "AF_UNIX", None):
        return
    host = address[0] if isinstance(address, tuple) else address
    if not _loopback(host):
        raise OSError(
            101, f"tests never touch the network: connect to {host!r} refused (tests/conftest.py)"
        )


def _connect(self, address):
    _guard(self, address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _guard(self, address)
    return _real_connect_ex(self, address)


def _getaddrinfo(host, *args, **kwargs):
    if host is not None and not _loopback(host):
        raise socket.gaierror(
            socket.EAI_NONAME,
            f"tests never touch the network: lookup of {host!r} refused (tests/conftest.py)",
        )
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _getaddrinfo

ROOT = Path(__file__).resolve().parents[1]

# The owner's prompt store (custom_prompts) is never the machine's own repo-factory home in tests.
os.environ.setdefault("REPO_FACTORY_PROMPTS_HOME", tempfile.mkdtemp(prefix="repo-factory-prompts-"))
# A press never starts the pre-push checks in a process of their own in tests (that process
# would build a real honesty model): tests run them in-process with a fake (Loop.precheck).
os.environ.setdefault("REPO_FACTORY_PRECHECK_SPAWN", "0")
# ... and the tick runs them itself (live, in a process of their own:
# test_the_tick_never_waits_on_the_checks turns this off)
os.environ.setdefault("REPO_FACTORY_PRECHECK_INLINE", "1")
# The token store is never the developer's: one per test session.
os.environ["REPO_FACTORY_TOKENS_DIR"] = tempfile.mkdtemp(prefix="repo-factory-tokens-")
os.environ.setdefault("REPO_FACTORY_RECHECK_SPAWN", "0")  # a re-check press spawns nothing
os.environ.setdefault("REPO_FACTORY_EXAM_WHEELS", "0")  # the exam fetches no wheels
os.environ.setdefault("REPO_FACTORY_CHARTER", "0")  # no charter drafted unless a
#                                                           test gives the loop a fake model
os.environ.setdefault("REPO_FACTORY_PROJECTS_SPAWN", "0")  # a press starts no job process
# Without bubblewrap (CI's runner has none) the suite's own fixtures still run, as before;
# tests of the refusal unset it.
os.environ.setdefault("REPO_FACTORY_ALLOW_UNSANDBOXED", "1")
# No test acts on GitHub as the developer: a real `gh` reached by mistake (an app whose loop
# home has no loop.yaml uses the default gh) finds no login and no token, and fails.
os.environ["GH_CONFIG_DIR"] = tempfile.mkdtemp(prefix="repo-factory-gh-")
for _k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
    os.environ.pop(_k, None)


@pytest.fixture(autouse=True)
def _own_github_app_store(tmp_path_factory, monkeypatch):
    """Each test has its own store for repo-factory's GitHub App (``$REPO_FACTORY_GITHUB_DIR``): an app one
    test created and installed never answers for a later test in the same worker."""
    monkeypatch.setenv("REPO_FACTORY_GITHUB_DIR", str(tmp_path_factory.mktemp("github")))
    # and for the Claude login token repo-factory keeps: none unless a test keeps one; the names
    # a test's .env added are that test's
    monkeypatch.setenv("REPO_FACTORY_CLAUDE_DIR", str(tmp_path_factory.mktemp("claude")))
    monkeypatch.delenv("REPO_FACTORY_DOTENV_VARS", raising=False)


def _turn_on(monkeypatch, section: str) -> None:
    from repo_factory.settings import store

    monkeypatch.setitem(store.DEFAULTS[section], "enabled", True)
    monkeypatch.setattr(store, "_parsed", {})  # no settings merged with the old default


@pytest.fixture
def maintenance_on(monkeypatch):
    """Maintenance is off by default (a later release turns it on); its tests turn it on, as
    ``maintain.enabled: true`` in loop.yaml does."""
    _turn_on(monkeypatch, "maintain")


@pytest.fixture
def projects_on(monkeypatch):
    """Projects are off by default, as maintenance; their tests turn them on."""
    _turn_on(monkeypatch, "projects")


@pytest.fixture(scope="session")
def _standin_build(tmp_path_factory):
    """A build with an empty module: what the page links when no real build is there."""
    from repo_factory.ui.routes import static

    d = tmp_path_factory.mktemp("app")
    (d / ".vite").mkdir()
    (d / "app-standin.js").write_text("export {};\n")
    (d / ".vite" / "manifest.json").write_text(
        json.dumps({static.ENTRY: {"file": "app-standin.js", "isEntry": True, "src": static.ENTRY}})
    )
    return d


@pytest.fixture(autouse=True)
def _a_built_page(_standin_build, monkeypatch):
    """The page is the built UI, and without a build "/" answers one line saying how to
    get one. The fast and slow lanes build nothing, so where there is no real build the tests
    see a stand-in (a manifest and an empty module) and "/" is the page a build serves. A real
    build (CI's browser job) is used as it is; a test of the notice points APP_DIR elsewhere."""
    from repo_factory.ui.routes import static

    if not (static.APP_DIR / ".vite" / "manifest.json").is_file():
        monkeypatch.setattr(static, "APP_DIR", _standin_build)


def pytest_sessionstart(session):
    """The scoring venv (pytest in a venv of its own, which many build and exam tests run) is
    built once, here, instead of inside whichever test first needs it. Without a cached copy
    and without PyPI that fails, and the run stops with one message instead of ~180 failures."""
    from repo_factory.harness.venv import _build_venv

    path, err = _build_venv(["pytest"])
    if path is None:
        pytest.exit(
            "the test venv needs pytest from PyPI once: run `uv sync` online (or set "
            f"UV_OFFLINE=0), then run the tests again. uv said: {err}",
            returncode=3,
        )


# -- The lanes --------------------------------------------------------------------------------
# CI runs `-m "not slow"` and `-m slow` as two jobs side by side. Slow: every test that took 5 s
# or more in .test_durations (regenerate it with `pytest --store-durations -n 8`, pytest-split's
# file).
SLOW_S = 5.0
DURATIONS = ROOT / ".test_durations"


def pytest_collection_modifyitems(config, items):
    try:
        took = json.loads(DURATIONS.read_text())
    except (OSError, ValueError):
        took = {}
    for item in items:
        if took.get(item.nodeid, 0) >= SLOW_S:
            item.add_marker(pytest.mark.slow)
        if Path(str(item.fspath)).name in SANDBOX_FILES:
            item.add_marker(pytest.mark.sandbox)


# The sandbox's own tests: bubblewrap around a session and around a run of model-written code.
# CI's `test` job runs them again with REPO_FACTORY_BWRAP_REQUIRED=1, where a skip is a failure.
SANDBOX_FILES = {
    "test_isolation.py",
    "test_sandbox_home.py",
    "test_worker_profiles.py",
    "test_harness_read_profile.py",
    "test_exam_integrity.py",
    "test_exam_runner.py",
    "test_product_installs.py",
}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """With REPO_FACTORY_BWRAP_REQUIRED=1, a sandbox test that skipped did not test the sandbox:
    it fails instead."""
    outcome = yield
    rep = outcome.get_result()
    if (
        rep.skipped
        and os.environ.get("REPO_FACTORY_BWRAP_REQUIRED") == "1"
        and item.get_closest_marker("sandbox") is not None
    ):
        rep.outcome = "failed"
        rep.longrepr = f"a sandbox test skipped where bubblewrap is required: {rep.longrepr}"


# a missing import CI's jobs may skip, and why (every other one fails there)
IMPORT_SKIP_OK: dict[str, str] = {}


def import_skip(longrepr) -> str | None:
    """A skip's reason when it is ``pytest.importorskip``'s "could not import …" of a module
    CI must have (not one of ``IMPORT_SKIP_OK``), else None."""
    reason = str(longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) == 3 else longrepr)
    if "could not import" not in reason:
        return None
    module = reason.split("could not import '", 1)[-1].split("'", 1)[0].split(".", 1)[0]
    return None if module in IMPORT_SKIP_OK else reason


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """With ``REPO_FACTORY_NO_IMPORT_SKIPS=1`` (CI's fast and slow jobs), a test skipped because an
    import failed fails instead: a skip of that kind can hide a whole file for days."""
    outcome = yield
    rep = outcome.get_result()
    if rep.skipped and os.environ.get("REPO_FACTORY_NO_IMPORT_SKIPS") == "1":
        why = import_skip(rep.longrepr)
        if why:
            rep.outcome = "failed"
            rep.longrepr = f"skipped because an import failed, which CI does not allow: {why}"


def pytest_sessionfinish(session, exitstatus):
    """The session's HOME was the temp dir throughout: nothing reached the developer's."""
    if Path.home().resolve() != TEST_HOME:
        print(
            f"\nERROR: a test left HOME at {Path.home()} (expected {TEST_HOME}); tests "
            "change HOME only through monkeypatch",
            file=sys.stderr,
        )
        session.exitstatus = 1
        return
    shutil.rmtree(TEST_HOME, ignore_errors=True)
