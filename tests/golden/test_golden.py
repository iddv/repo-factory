"""The golden build: the whole cycle through the real command line, with no model.

A typed task goes in (`repo-factory loop add`); the loop writes its brief, builds it in the
sandbox, verifies it, hardens it and gates it (`repo-factory loop run --until-idle`). The build
waits at "ready" and nothing reaches GitHub until the publish press (`repo-factory loop
publish`), which creates a private repository with a release. Claude Code and GitHub are the
stand-ins in ``tests/golden/fake/``: `claude` plays recorded answers (the build "writes"
``fake/product/``), `gh` keeps repositories as bare git repositories in a folder.

This is the test that keeps the experiment in working order: if a change breaks any stage of
the cycle, it fails here. It needs bubblewrap (every session runs in the sandbox); CI's `test`
job requires it, elsewhere it is skipped when bubblewrap cannot make a sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

FAKE = Path(__file__).resolve().parent / "fake"
TASK = "a command-line tool that counts the words in text files"
LIMIT_S = 120


def _bwrap_works() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False
    p = subprocess.run(
        [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "true",
        ],
        capture_output=True,
    )
    return p.returncode == 0


pytestmark = pytest.mark.skipif(
    not _bwrap_works() and os.environ.get("REPO_FACTORY_BWRAP_REQUIRED") != "1",
    reason="bubblewrap cannot make a sandbox here (CI's `test` job requires it)",
)


@pytest.fixture
def factory(tmp_path):
    home, github = tmp_path / "home", tmp_path / "github"
    home.mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Octo Owner\n\temail = owner@example.com\n[init]\n\tdefaultBranch = main\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GH_TOKEN", "GITHUB_TOKEN", "REPO_FACTORY_ALLOW_UNSANDBOXED")
    }
    env.update(
        REPO_FACTORY_HOME=str(home),
        PATH=f"{FAKE}{os.pathsep}{env['PATH']}",
        FAKE_GH_ROOT=str(github),
        GH_CONFIG_DIR=str(tmp_path / "gh"),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        # a Claude login for the stand-in (it is piped to `claude`, which ignores it)
        CLAUDE_CODE_OAUTH_TOKEN="golden-" + secrets.token_hex(8),
        # a GitHub login the host has and no session may see
        GH_TOKEN="gh" + "o_" + secrets.token_hex(18),
    )

    def run(*args: str, timeout: int = LIMIT_S) -> subprocess.CompletedProcess:
        p = subprocess.run(
            [sys.executable, "-m", "repo_factory.cli", *args],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        assert p.returncode == 0, f"repo-factory {' '.join(args)}:\n{p.stdout}\n{p.stderr}"
        return p

    def show(task: str) -> dict:
        return json.loads(run("loop", "show", task).stdout)

    run.home, run.github, run.show, run.env = home, github, show, env
    return run


@pytest.fixture
def escapes(factory, tmp_path):
    """What the stand-in tries from inside its sessions: files of the home it must not see
    (beside the factory's own folder, and in the home the run gives it), a listener on the
    host's loopback, and the run's tokens (as hashes). Yields a function that reads the
    sessions' reports."""
    files = [
        Path(os.environ["HOME"]) / ".config" / "golden-canary.txt",
        factory.home / "golden-canary.txt",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("canary\n")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.5)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                srv.accept()[0].close()
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    tokens = [factory.env["CLAUDE_CODE_OAUTH_TOKEN"], factory.env["GH_TOKEN"]]
    probe = FAKE / "probe.json"
    probe.write_text(
        json.dumps(
            {
                "files": [str(f) for f in files],
                "port": srv.getsockname()[1],
                "hashes": [hashlib.sha256(t.encode()).hexdigest() for t in tokens],
            }
        )
    )

    def reports() -> dict:
        return {p.stem: json.loads(p.read_text()) for p in tmp_path.rglob("probe-*.json")}

    try:
        yield reports
    finally:
        probe.unlink(missing_ok=True)
        stop.set()
        srv.close()


def test_a_typed_task_becomes_a_verified_private_repository_on_the_press(factory, escapes):
    t0 = time.monotonic()
    added = factory("loop", "add", TASK).stdout
    task = added.split()[1]

    # brief, build, verify, harden, gate: then it waits
    factory("loop", "run", "--until-idle")
    row = factory.show(task)
    assert row["state"] == "ready", row.get("reason")
    assert row["pitch"]["product"] == "wordtally"
    gate = row["gate"]
    assert gate["verdict"] == "ready" and gate["reasons"] == []
    assert gate["checks"]["completeness"]["detail"] == "3/3 flows work end to end"
    assert gate["checks"]["security"]["ok"] and gate["checks"]["tests"]["ok"]
    assert row["build"]["status"] == "ok" and row["build"]["cost_usd"] > 0

    # from inside the build session and a check session, as claude and as a tool command:
    # no file of the home read, the host's loopback not reached, no token found
    got = escapes()
    assert {"probe-build", "probe-security"} <= set(got), got
    for where, r in got.items():
        assert r["claude"] == {"read": [], "connected": [], "tokens": []}, (where, r)
        # a tool command runs in the shell's nested namespace; where the machine cannot make
        # one (it said so), the command did not run and reached nothing
        cmd = r["command"]
        if "error" in cmd:
            assert "the session's namespace did not start" in cmd["error"], (where, cmd)
            continue
        assert cmd == {"read": [], "connected": [], "tokens": []}, (where, cmd)

    # publishing asks first: nothing has reached GitHub
    assert not (factory.github / "octo-owner").exists()

    # the press
    factory("loop", "publish", task)
    row = factory.show(task)
    assert row["state"] == "published"
    repo = factory.github / "octo-owner" / "wordtally"
    meta = json.loads(repo.with_suffix(".json").read_text())
    assert meta["visibility"] == "private"
    files = subprocess.run(
        ["git", "-C", str(repo.with_suffix(".git")), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for f in (
        "wordtally/cli.py",
        "tests/test_cli.py",
        "README.md",
        "LICENSE",
        ".github/repo-factory.yml",
    ):
        assert f in files
    release = repo.parent / "wordtally.releases" / "v0.1.0"
    assets = json.loads((release / "release.json").read_text())["assets"]
    assert "wordtally-0.1.0.tar.gz" in assets and "wordtally-0.1.0-checks.tar.gz" in assets

    # nothing it did made the repository public
    calls = [
        json.loads(x)["argv"] for x in (factory.github / "calls.jsonl").read_text().splitlines()
    ]
    assert not any("visibility=public" in a or "--public" in a for c in calls for a in c)
    assert time.monotonic() - t0 < LIMIT_S


def test_a_build_stopped_by_the_spend_cap_says_so_first(factory):
    (factory.home / "loop.yaml").write_text("build:\n  max_usd: 0.03\n")
    task = factory("loop", "add", TASK).stdout.split()[1]
    factory("loop", "run", "--until-idle")
    row = factory.show(task)
    assert row["state"] == "held"
    first = row["gate"]["reasons"][0]
    assert first.startswith("spend: the spend cap stopped it before the build stage"), first
    assert "Settings › Spend" in first
    assert not (factory.github / "octo-owner").exists()
