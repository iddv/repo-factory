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


def test_doctor_says_five_lines_and_fails_without_claude(monkeypatch):
    said = []
    monkeypatch.setattr(doctor, "claude_line", lambda: (False, "Claude Code is not installed"))
    monkeypatch.setattr(doctor, "bwrap_line", lambda: (True, "bubblewrap can make a sandbox"))
    monkeypatch.setattr(doctor, "git_line", lambda: (True, "git"))
    monkeypatch.setattr(doctor, "gh_line", lambda: (False, "gh is not installed"))
    assert doctor.run(said.append) == 1
    assert len(said) == 5 and said[1].startswith("ok ") and said[0].startswith("missing: ")


def test_doctor_passes_without_gh(monkeypatch):
    said = []
    monkeypatch.setattr(doctor, "claude_line", lambda: (True, "Claude Code, signed in"))
    monkeypatch.setattr(doctor, "bwrap_line", lambda: (True, "bubblewrap"))
    monkeypatch.setattr(doctor, "git_line", lambda: (True, "git"))
    monkeypatch.setattr(doctor, "gh_line", lambda: (False, "gh is not installed"))
    assert doctor.run(said.append) == 0


def _claude_here(monkeypatch, on):
    from repo_factory.harness import handoff

    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(doctor, "_run", lambda cmd, timeout=20: (0, "2.1.300 (Claude Code)\n"))
    monkeypatch.setattr(
        handoff, "build_login", lambda **k: {"on": on, "words": "the way builds sign in"}
    )


def test_doctor_says_when_builds_copy_the_sign_in_file(monkeypatch):
    _claude_here(monkeypatch, "signin")
    ok, line = doctor.claude_line()
    assert ok and "copy this sign-in file into each session" in line
    assert "claude setup-token" in line and "Settings › Claude" in line


def test_doctor_says_nothing_of_a_copy_with_a_login_token(monkeypatch):
    _claude_here(monkeypatch, "login")
    ok, line = doctor.claude_line()
    assert ok and "copy" not in line


def test_doctor_warns_under_five_gb(tmp_path):
    m = tmp_path / "meminfo"
    m.write_text("MemTotal:       16000000 kB\nMemAvailable:    3145728 kB\n")
    assert doctor.mem_available(str(m)) == 3.0
    m.write_text("MemAvailable:    8388608 kB\n")
    assert doctor.mem_available(str(m)) == 8.0
    assert doctor.mem_available(str(tmp_path / "none")) is None
