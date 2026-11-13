"""LoopError (CR-PLAN S-13, CR-REVIEW C-4): a loop step that says no raises LoopError. The
command line still prints the words and exits 1, the page answers 400, the host's in-process
command groups still see SystemExit, and a safety net (``except Exception``) catches it."""

import argparse
import json
import subprocess
import sys

import pytest

from repo_factory import errors
from repo_factory.errors import LoopError


def test_the_command_line_prints_the_words_and_exits_1(tmp_path):
    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text("{}\n")
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "repo_factory.cli",
            "loop",
            "--home",
            str(home),
            "close",
            "i00000000",
            "--reason",
            "not this one",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p.returncode == 1, p.stderr
    assert "no idea 'i00000000'" in p.stderr and "Traceback" not in p.stderr


def test_the_page_answers_400_with_the_words(monkeypatch):
    from repo_factory.ui import api

    def refuse(app, path, query):
        raise LoopError("i1 is closed; there is nothing to close")

    monkeypatch.setattr(api, "_get", refuse)
    status, _, body = api.handle("GET", "/api/anything", app=object())
    assert status == 400
    assert json.loads(body) == {"error": "i1 is closed; there is nothing to close"}


def test_a_host_command_group_says_no_with_systemexit():
    def no(args):
        raise LoopError("the words")

    @errors.host_group
    def add_group(sub):
        g = sub.add_parser("thing").add_subparsers(dest="c", required=True)
        g.add_parser("do").set_defaults(fn=no)

    p = argparse.ArgumentParser()
    add_group(p.add_subparsers(dest="cmd", required=True))
    ns = p.parse_args(["thing", "do"])
    with pytest.raises(SystemExit) as e:
        ns.fn(ns)
    assert e.value.code == "the words"  # printed, exit 1


def test_the_hosts_groups_are_wrapped():
    from repo_factory.github_cli import add_github_group
    from repo_factory.maintain.cli import add_maintain_group

    for add in (add_maintain_group, add_github_group):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd", required=True)
        add(sub)
        seen = []

        def walk(parser):
            if parser._defaults.get("fn"):
                seen.append(getattr(parser._defaults["fn"], "_as_exit", False))
            for a in parser._actions:
                if isinstance(a, argparse._SubParsersAction):
                    for sp in a.choices.values():
                        walk(sp)

        walk(p)
        assert seen and all(seen), add.__name__


def test_a_safety_net_catches_it():
    try:
        raise LoopError("no")
    except Exception as e:  # noqa: BLE001 - the point of C-4
        assert str(e) == "no" and e.code == 1
