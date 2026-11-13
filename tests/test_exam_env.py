"""The exam's environment is a contract (EX-PLAN §5): ``/etc`` read-only, a private HOME,
loopback only while product code runs, package indexes only during the install phase and only
through the index proxy, the product at /work/product. Row by row, through scenarios that
look at the sandbox from inside. No model, no network: the install phase runs offline (a
lockfile with no packages) and the proxy's refusal is checked against itself."""

import json
import shutil
import socket
import subprocess

import pytest

from repo_factory.harness import netproxy
from repo_factory.harness.isolation import isolation
from repo_factory.rules.model import Rule
from repo_factory.v5 import exam, nodeinstall, readiness

from .exam_fixture import FakeRun, expected, product

pytestmark = pytest.mark.skipif(isolation() != "bwrap", reason="needs bubblewrap")
needs_npm = pytest.mark.skipif(not shutil.which("npm"), reason="needs node and npm")

INSIDE = """import getpass, os, socket, json


def test_contract():
    # /etc: read-only, as on a user's machine (getpass, localhost, TLS roots)
    assert os.path.exists("/etc/passwd") and getpass.getuser()
    assert socket.gethostbyname("localhost") == "127.0.0.1"
    assert os.path.isdir("/etc/ssl")
    try:
        open("/etc/factory-test", "w")
        wrote = True
    except OSError:
        wrote = False
    assert not wrote
    # a private, writable HOME
    assert os.environ["HOME"] == "/home/user"
    os.makedirs(os.path.expanduser("~/.cache/x"), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.local/bin"), exist_ok=True)
    # the environment of a fresh user's CI
    for k, v in (("CI", "1"), ("LANG", "C.UTF-8"), ("TERM", "dumb"), ("USER", "user"),
                 ("REPO_FACTORY_PRODUCT_DIR", "/work/product")):
        assert os.environ.get(k) == v, k
    assert "/work/product" in os.environ["PYTHONPATH"] and "/work/exam" in os.environ["PYTHONPATH"]
    # loopback only: one interface, no proxy socket, nothing outside reachable
    ifaces = [ln.split(":")[0].strip() for ln in open("/proc/net/dev").read().splitlines()[2:]]
    assert ifaces == ["lo"], ifaces
    mounts = open("/proc/self/mounts").read()
    assert "/run/repo_factory" not in mounts and not os.path.exists("/run/repo_factory")
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        assert k not in os.environ, k
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("1.1.1.1", 443))
        reached = True
    except OSError:
        reached = False
    finally:
        s.close()
    assert not reached
"""


def listeners() -> set:
    """The host's listening addresses beyond loopback (other tests may open loopback ports
    while this one runs; what matters is that nothing listens on the network)."""
    p = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True)
    out = {ln.split()[3] for ln in p.stdout.splitlines() if ln.strip()}
    return {a for a in out if not a.startswith(("127.", "[::1]", "localhost"))}


def test_the_contract_from_inside(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib", skip=("c1", "c2", "x1"))
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    r = Rule(id="env", title="env", kind="edge", source="", code=INSIDE, status="active")
    run.store.rules["env"] = r
    before = listeners()
    rec = exam.run(run, tree, rules=[r])
    assert rec["scenarios"]["env"]["status"] == "passed", rec["scenarios"]["env"]["detail"]
    assert rec["env"]["isolation"] == "bwrap"
    assert listeners() <= before  # nothing new listens on the network


@needs_npm
def test_a_node_cli_runs_its_own_tests_and_the_exam_drives_it(tmp_path):
    exp = expected("node_cli")
    run = FakeRun(tmp_path, "node_cli")
    tree = product("node_cli", tmp_path / "tree")
    rec = exam.run(run, tree, readme=True, flake=True)
    assert [c["cmd"] for c in rec["own"]["commands"]] == exp["readme_tests"]
    assert rec["own"]["green"], rec["own"]
    assert {k: v["status"] for k, v in rec["scenarios"].items()} == exp["scenarios"]
    assert rec["install"]["node"] == exp["install"]


@needs_npm
def test_the_install_phase_runs_npm_through_the_bridge_and_caches_by_lockfile(tmp_path):
    """Offline: a lockfile with no packages, so npm needs no index; it runs in its own
    sandbox, exec'd by the proxy bridge, and only package.json and the lockfile are there."""
    tree = product("node_cli", tmp_path / "tree")
    (tree / "app.js").write_text("// product code: never in the install phase\n")
    dest = tmp_path / "cache" / nodeinstall.key(tree)
    res = nodeinstall.run_install(tree, dest, ["registry.npmjs.org"], "package-lock.json")
    assert res["ok"], res
    assert res["cmd"] == "npm ci --ignore-scripts --no-audit --no-fund --loglevel=error"
    assert (dest / "node_modules").is_dir() and (dest / ".ok").is_file()
    assert json.loads((dest / ".ok").read_text())["requests"] == []  # nothing was fetched


WHEELS_INSIDE = """import os


def test_pip_installs_from_the_wheelhouse():
    assert os.environ["PIP_FIND_LINKS"] == "/work/deps/wheels"
    assert os.environ["PIP_NO_INDEX"] == "1"
    assert os.listdir("/work/deps/wheels") == ["flit_core-3.9.0-py3-none-any.whl"]
    try:
        open("/work/deps/wheels/x", "w")
        wrote = True
    except OSError:
        wrote = False
    assert not wrote
    assert not os.path.lexists("/work/product/wheels")         # never in the product's tree
"""


def test_a_python_package_gets_its_build_backend_as_wheels(tmp_path, monkeypatch):
    """pip install . in the exam (no network) builds from wheels fetched before the run: the
    product's [build-system] requires plus pip, setuptools and wheel (the fetch is faked
    here; it never runs in tests)."""
    from repo_factory.v5 import pyinstall

    run = FakeRun(tmp_path, "stdlib_unittest_lib", skip=("c1", "c2", "x1"))
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    (tree / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["flit_core>=3.2"]\nbuild-backend = "flit_core.buildapi"\n'
    )
    asked = []

    def fake(reqs, dest, venv):
        asked.append(reqs)
        (dest / "wheels").mkdir(parents=True)
        (dest / "wheels" / "flit_core-3.9.0-py3-none-any.whl").write_bytes(b"PK")
        (dest / ".ok").write_text("{}")
        return {"ok": True, "why": None, "files": ["flit_core-3.9.0-py3-none-any.whl"]}

    monkeypatch.delenv(pyinstall.OFF_ENV, raising=False)
    monkeypatch.setattr(pyinstall, "download", fake)
    r = Rule(id="wh", title="wh", kind="edge", source="", code=WHEELS_INSIDE, status="active")
    run.store.rules["wh"] = r
    rec = exam.run(run, tree, rules=[r])
    assert rec["scenarios"]["wh"]["status"] == "passed", rec["scenarios"]["wh"]["detail"]
    assert asked == [["flit_core>=3.2", "pip", "setuptools", "wheel"]]
    assert rec["install"]["python"].startswith("build backends fetched: flit_core")
    # no PyPI in exam.indexes: nothing is fetched
    assert pyinstall.install(run, tree, {"indexes": ["registry.npmjs.org"]})["wheels"] is None


def test_what_a_python_package_builds_with(tmp_path):
    from repo_factory.v5 import pyinstall

    (tmp_path / "a").mkdir()
    assert pyinstall.needs(tmp_path / "a") is None  # not a package
    (tmp_path / "a" / "setup.py").write_text("")
    assert pyinstall.needs(tmp_path / "a") == ["setuptools>=40.8.0", "wheel"]
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "pyproject.toml").write_text('[project]\nname = "b"\n')
    assert pyinstall.needs(tmp_path / "b") == ["setuptools>=40.8.0", "wheel"]
    (tmp_path / "b" / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\n[project]\nname = "b"\n'
    )
    assert pyinstall.needs(tmp_path / "b") == ["hatchling"]
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "pyproject.toml").write_text("[tool.ruff]\nline-length = 99\n")
    assert pyinstall.needs(tmp_path / "c") is None  # only tool config


def test_the_index_proxy_refuses_a_host_not_on_its_list():
    with netproxy.Proxy(hosts=["registry.npmjs.org", "pypi.org"]) as px:
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(px.sock))
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        reply = s.recv(4096).decode()
        s.close()
    assert reply.startswith("HTTP/1.1 403") and "not on the allowlist" in reply
    assert px.log == [("denied", "example.com:443")]


def test_systemd_steps_are_not_verifiable_here_never_red(tmp_path):
    exp = expected("loopback_server")
    run = FakeRun(tmp_path, "loopback_server")
    tree = product("loopback_server", tmp_path / "tree")
    rec = exam.run(run, tree, readme=True)
    assert (
        rec["own"]["green"]
        and {k: v["status"] for k, v in rec["scenarios"].items()} == exp["scenarios"]
    )
    from repo_factory.v5 import commands

    ex = commands.extract(tree)
    skipped = {e["cmd"]: e["skip"] for e in ex["skipped"] + ex["quick"] if e.get("skip")}
    res = {"extract": commands._brief(ex)}
    nv = readiness.not_verifiable(res, {})
    assert "needs root" in nv or "systemd" in nv, (nv, skipped)


def test_canary_secrets_made_at_runtime_are_clean_and_a_committed_key_is_caught(tmp_path):
    from repo_factory.loop import secrets

    tree = product("canary_security", tmp_path / "tree")
    assert secrets.scan(tree) == []
    run = FakeRun(tmp_path, "canary_security")
    rec = exam.run(run, tree, readme=True)
    assert rec["scenarios"]["sec_1"]["status"] == "passed" and rec["own"]["green"]
    sib = product("canary_security", tmp_path / "sibling")
    key = "sk_" + "live_" + "".join("0123456789abcdef"[i % 16] for i in range(32))
    (sib / "vault" / "config.py").write_text(f'API_KEY = "{key}"\n')
    hits = secrets.scan(sib)
    assert hits and hits[0]["file"] == "vault/config.py"
