"""The README's quick start, run as written (``v5/commands.py``): an install step the check
skips (no network) still puts the product's commands on PATH for the steps after it, an
install script gets the build backends its pip or pipx builds with, and a command that only
opens a port on the way (a demo) is waited out rather than left running as a server.

The real case: Repo Recall r1–r5 and Nextread r1–r2 were red in every scorecard with
``recall: command not found`` (exit 127) after ``pipx install .``, which the check skips."""

import json
from pathlib import Path

import pytest

from repo_factory.v5 import commands

FIX = Path(__file__).parent / "fixtures" / "quickstart"
SANDBOX = pytest.mark.skipif(not commands.available(), reason="needs bwrap")


def venv():
    from repo_factory.harness.venv import ensure_venv

    return ensure_venv([])


def project(root: Path, readme: str, files: dict | None = None) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text(readme)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "hello"\nversion = "0.1"\n\n[project.scripts]\n'
        'hello = "hello.cli:main"\n'
    )
    (root / "hello").mkdir()
    (root / "hello" / "__init__.py").write_text("")
    (root / "hello" / "cli.py").write_text(
        "import sys\n\n\ndef main():\n    print('hello', *sys.argv[1:])\n    return 0\n"
    )
    for rel, text in (files or {}).items():
        (root / rel).write_text(text)
        (root / rel).chmod(0o755)
    return root


def test_which_skipped_install_steps_install_the_product_itself():
    for cmd in (
        "pipx install .",
        "pip install -e .",
        "python3 -m pip install --user .",
        "uv tool install .",
        "uv pip install '.[dev]'",
        "uv sync",
        "poetry install",
        "pip install ./",
    ):
        assert commands.installs_product(cmd), cmd
    for cmd in (
        "pip install -r requirements.txt",
        "pip install pytest",
        "npm ci",
        "python3 -m venv .venv",
        "./install.sh",
        "recall --version",
    ):
        assert not commands.installs_product(cmd), cmd


def test_a_launcher_per_console_script_runs_it_from_the_copy(tmp_path):
    import subprocess
    import sys

    root = project(tmp_path / "p", "# Hello\n")
    names = commands.write_scripts(root, tmp_path / "bin", sys.executable)
    assert names == ["hello"]
    p = subprocess.run(
        [str(tmp_path / "bin" / "hello"), "world"], capture_output=True, text=True, cwd=tmp_path
    )
    assert p.returncode == 0 and p.stdout.strip() == "hello world"
    # a src layout, and a broken or missing pyproject: no launchers, no error
    src = FIX / "repo-recall"
    assert commands.entry_points(src) == {"recall": "recall.cli:main"}
    (tmp_path / "q").mkdir()
    (tmp_path / "q" / "pyproject.toml").write_text("[project\n")
    assert commands.write_scripts(tmp_path / "q", tmp_path / "bin2", sys.executable) == []


QUICK = """# Hello

## Install

```sh
pip install -e .
hello greet
```
"""


@SANDBOX
def test_the_product_s_commands_are_on_path_after_its_skipped_install(tmp_path):
    res = commands.run(project(tmp_path / "p", QUICK), venv(), ("quick",))
    assert res["ran"]
    assert [(r["cmd"], r["status"]) for r in res["quick"]] == [
        ("pip install -e .", "skipped"),
        ("hello greet", "ok"),
    ]
    it = commands.items(res, tests=False, restart=False)["commands"]
    assert it["status"] == "green", it
    # no install step in the quick start: the README is wrong, and the check says so
    bare = QUICK.replace("pip install -e .\n", "")
    res = commands.run(project(tmp_path / "q", bare), venv(), ("quick",))
    assert res["quick"][0]["rc"] == 127
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "red"


@SANDBOX
def test_repo_recall_r5_s_quick_start_replayed_now_works_as_written(tmp_path):
    was = json.loads((FIX / "repo-recall-r5.commands_final.json").read_text())
    assert was["item"]["status"] == "red" and was["quick"][-1]["rc"] == 127
    ex = commands.extract(FIX / "repo-recall")
    assert [(e["cmd"], bool(e["skip"])) for e in ex["quick"]] == [
        (e["cmd"], bool(e["skip"])) for e in was["extract_quick"]
    ]
    res = commands.run(FIX / "repo-recall", venv(), ("quick",))
    got = {r["cmd"]: r for r in res["quick"]}
    assert got["recall --version"]["status"] == "ok", got
    assert "recall 1.0.0" in got["recall --version"]["output"]
    assert commands.items(res, tests=False, restart=False)["commands"]["status"] == "green"


@SANDBOX
def test_an_install_script_gets_the_wheelhouse_and_no_index(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    check = '#!/bin/sh\n[ "$PIP_NO_INDEX" = 1 ] && [ "$PIP_FIND_LINKS" = "%s" ]\n' % wheels
    root = project(
        tmp_path / "p", "# Hello\n\n## Install\n\n```sh\n./install.sh\n```\n", {"install.sh": check}
    )
    assert commands.run(root, venv(), ("quick",), wheels=wheels)["quick"][0]["status"] == "ok"
    assert commands.run(root, venv(), ("quick",))["quick"][0]["status"] == "failed"


DEMO = """import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
time.sleep(float(sys.argv[1]))
"""


@SANDBOX
def test_a_command_that_listens_on_the_way_is_waited_out(tmp_path):
    # Sidedoor r10: ``sidedoor demo`` runs trials on a short-lived broker; taken for a server
    # and left running, it made the backup after it refuse ("A trial is running")
    readme = (
        "# Demo\n\n## Install\n\n```sh\npython3 demo.py 2 && touch done\n"
        "test -f done\npython3 demo.py 600\n```\n"
    )
    root = project(tmp_path / "p", readme, {"demo.py": DEMO})
    res = commands.run(root, venv(), ("quick",), timeouts={"step_s": 6})
    st = [(r["cmd"], r["status"]) for r in res["quick"]]
    assert st[0][1] == "ok" and st[1][1] == "ok", st
    assert st[2][1] == "serving"  # still listening when its time is up: a server
    # a start command is a server as soon as it listens, as before
    readme = "# Demo\n\n## Install\n\n```sh\npython3 demo.py 600 serve\n```\n"
    res = commands.run(project(tmp_path / "q", readme, {"demo.py": DEMO}), venv(), ("quick",))
    assert res["quick"][0]["status"] == "serving" and res["quick"][0]["wall_s"] < 6
