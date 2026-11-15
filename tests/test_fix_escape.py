"""In the sandbox lane: what a fix session writes into its clone's ``.git``
from inside bubblewrap never makes the host run a command or hand over a credential.

The planting runs in real bubblewrap with the workspace bound read-write, as a session's tools
do; then the host delivers the fix. With the old delivery (a push from the workspace's own
clone, to ``origin``) the planted repository's ``pre-receive`` hook ran on the host. Now the
host carries the fix into its own clone and pushes to the address it built, and nothing planted
runs. Real git, a real hook, a local bare repository as the stand-in GitHub; no network."""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from repo_factory.harness import sandbox
from repo_factory.harness.isolation import bwrap_cmd
from repo_factory.maintain import switch

from .test_f3_maintenance import REPO, quiet, world  # noqa: F401
from .test_maintain_revise import Scripted, names_test
from .test_maintain_revise import fixes as fixes_step

PLANT = r"""
import pathlib, subprocess, sys
ws, marker = pathlib.Path(sys.argv[1]), sys.argv[2]
evil = ws / ".repo-factory" / "e.git"
subprocess.run(["git", "init", "-q", "--bare", str(evil)], check=True)
hook = evil / "hooks" / "pre-receive"
hook.write_text("#!/bin/sh\necho hook ran > " + marker + "\n")
hook.chmod(0o755)
subprocess.run(["git", "-C", str(ws), "config", "remote.origin.url", str(evil)], check=True)
subprocess.run(["git", "-C", str(ws), "config", "remote.origin.pushurl", str(evil)], check=True)
for extra in sys.argv[3:]:
    k, v = extra.split("=", 1)
    subprocess.run(["git", "-C", str(ws), "config", k, v], check=True)
"""


def in_bwrap(ws: Path, marker: Path, *extra: str) -> None:
    """What a session's tool can do: run code inside bubblewrap with the workspace bound."""
    if not sandbox.available():
        pytest.skip("bubblewrap cannot make a sandbox here")
    cmd = bwrap_cmd(shutil.which("bwrap"), ws)  # up to the sandbox's own python
    p = subprocess.run(
        [*cmd, "-I", "-c", PLANT, str(ws), str(marker), *extra], capture_output=True, text=True
    )
    assert p.returncode == 0, p.stderr


def branches(origin: Path) -> list[str]:
    out = subprocess.run(
        ["git", "--git-dir", str(origin), "branch", "--list", "repo-factory/*"],
        capture_output=True,
        text=True,
    ).stdout
    return [b.strip() for b in out.splitlines() if b.strip()]


def test_a_hook_planted_from_the_sandbox_never_runs_on_the_hosts_push(world, tmp_path):
    lp, fake, _ = world
    switch.set_on(lp.cfg.home, REPO, True)
    marker = tmp_path / "RAN_ON_HOST.txt"
    planted: list[Path] = []

    def plant_then_fix(ws, p):
        in_bwrap(ws, marker)
        planted.append(ws / ".repo-factory" / "e.git")
        assert (planted[0] / "hooks" / "pre-receive").is_file()
        fixes_step(ws, p)

    res = quiet(lp, Scripted(names_test(), plant_then_fix)).run()
    assert planted, "the session step ran"
    assert not marker.exists(), "the planted hook ran on the host"
    assert res["outcome"] == "pr", res
    # the push went to the address the host built (the stand-in), not the planted repository
    assert branches(fake.origin) == [res["branch"]]


def test_a_proxy_planted_from_the_sandbox_sees_nothing(world, tmp_path):
    lp, fake, _ = world
    switch.set_on(lp.cfg.home, REPO, True)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(0.2)
    seen: list[bytes] = []
    stop = threading.Event()

    def listen():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            seen.append(c.recv(4096))
            c.close()

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    port = srv.getsockname()[1]
    marker = tmp_path / "RAN_ON_HOST.txt"

    def plant_then_fix(ws, p):
        in_bwrap(ws, marker, f"http.proxy=http://127.0.0.1:{port}", "http.sslVerify=false")
        fixes_step(ws, p)

    try:
        res = quiet(lp, Scripted(names_test(), plant_then_fix)).run()
    finally:
        stop.set()
        t.join(2)
        srv.close()
    assert seen == [], "the planted proxy was reached"
    assert not marker.exists()
    # the host's git refuses the tree, with words, and nothing is pushed anywhere
    assert res["outcome"] == "failed" and "http" in (res.get("why") or ""), res
    assert branches(fake.origin) == [] and fake.writes() == []
