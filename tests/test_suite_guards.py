"""The suite's own guards (tests/conftest.py): no test reaches the developer's homes, the
network, a model or GitHub."""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _REAL_HOME, SHIMS, TEST_HOME


def test_home_is_a_temp_dir_and_the_loop_home_is_inside_it():
    assert Path.home().resolve() == TEST_HOME != _REAL_HOME
    assert Path(os.environ["REPO_FACTORY_HOME"]).resolve().is_relative_to(TEST_HOME)
    for k in (
        "XDG_CONFIG_HOME",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        assert k not in os.environ


def test_the_loop_config_resolves_inside_the_temp_home():
    from repo_factory.settings import store as config

    assert Path(config.home()).resolve().is_relative_to(TEST_HOME)


def test_a_connection_off_loopback_is_refused_before_it_is_made():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match="never touch the network"):
            s.connect(("192.0.2.1", 443))  # TEST-NET-1: never routable anyway
    finally:
        s.close()
    with pytest.raises(socket.gaierror, match="never touch the network"):
        socket.getaddrinfo("example.com", 443)


def test_loopback_still_works():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        c = socket.create_connection(srv.getsockname(), timeout=5)
        c.close()
    finally:
        srv.close()


@pytest.mark.parametrize("name", ["claude", "gh"])
def test_claude_and_gh_are_stand_ins_that_fail(name):
    exe = shutil.which(name)
    assert exe and Path(exe).parent == SHIMS
    r = subprocess.run([name, "auth", "status"], capture_output=True, text=True)
    assert r.returncode == 97 and "called from a test" in r.stderr
