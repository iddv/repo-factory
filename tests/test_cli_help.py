"""`repo-factory --help` says what it is and every listed command says what it does; the
commands kept off (maintain, projects, lessons, github) are not listed; `--version` answers;
`doctor` says one line per check."""

import pytest

from repo_factory import __version__, doctor
from repo_factory.cli import main

LISTED = {"doctor", "costs", "build", "revise", "new", "status", "answer", "verdict", "ui", "loop"}
UNLISTED = {"maintain", "projects", "lessons", "github"}


def test_help_lists_the_commands_and_not_the_ones_kept_off(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "repo-factory" in out
    listed = set()
    for line in out.splitlines():
        word = line.strip().split(" ", 1)[0]
        if word in LISTED | UNLISTED and line.startswith("    "):
            listed.add(word)
    assert listed >= LISTED, LISTED - listed
    assert not listed & UNLISTED, listed & UNLISTED


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"repo-factory {__version__}"


def test_doctor_says_four_lines_and_fails_without_claude(monkeypatch):
    said = []
    monkeypatch.setattr(doctor, "claude_line", lambda: (False, "Claude Code is not installed"))
    monkeypatch.setattr(doctor, "bwrap_line", lambda: (True, "bubblewrap can make a sandbox"))
    monkeypatch.setattr(doctor, "git_line", lambda: (True, "git"))
    monkeypatch.setattr(doctor, "gh_line", lambda: (False, "gh is not installed"))
    assert doctor.run(said.append) == 1
    assert len(said) == 4 and said[1].startswith("ok ") and said[0].startswith("missing: ")


def test_doctor_passes_without_gh(monkeypatch):
    said = []
    monkeypatch.setattr(doctor, "claude_line", lambda: (True, "Claude Code, signed in"))
    monkeypatch.setattr(doctor, "bwrap_line", lambda: (True, "bubblewrap"))
    monkeypatch.setattr(doctor, "git_line", lambda: (True, "git"))
    monkeypatch.setattr(doctor, "gh_line", lambda: (False, "gh is not installed"))
    assert doctor.run(said.append) == 0
