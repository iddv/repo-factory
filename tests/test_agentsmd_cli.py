"""AGENTS.md for a command-line product (found by the capabilities acceptance run, 2026-10-11:
the honesty check held NoteHatch, a CLI, on AGENTS.md's "start the service, sign in, submit
forms" and "the Settings page", repo-factory's own template text for a web app). A product whose README
names no 127.0.0.1 address gets the command-line wording; a server keeps its own."""

from __future__ import annotations

from repo_factory.maintain import agentsmd

ASSUMPTIONS = (
    "# Assumptions\n\n## Settings\n\n| Setting | Values | Default |\n|---|---|---|\n"
    "| `export.images` | extract, keep | `extract` |\n"
)


def tree(tmp_path, readme: str):
    (tmp_path / "tests" / "acceptance" / "_helpers").mkdir(parents=True)
    (tmp_path / "README.md").write_text(readme)
    (tmp_path / "ASSUMPTIONS.md").write_text(ASSUMPTIONS)
    (tmp_path / "SCOPE.md").write_text("# Scope: X\n\nA tool.\n")
    return agentsmd.render(agentsmd.values_from_tree(tmp_path, repo="o/x"))


def test_a_cli_gets_cli_wording(tmp_path):
    text = tree(
        tmp_path,
        "# NoteHatch\n\nExports notes to Markdown.\n\n## Install\n\n```\n./install.sh\n```\n",
    )
    assert "Settings page" not in text and "sign in" not in text and "submit forms" not in text
    assert "the config file and flags, the README table" in text
    assert "Set in the config file, a flag or an environment variable" in text
    assert "run the command with its arguments" in text


def test_a_server_keeps_its_wording(tmp_path):
    text = tree(
        tmp_path, "# Lendbook\n\nA library app.\n\nOpen http://127.0.0.1:8080/ after `./run.sh`.\n"
    )
    assert "the Settings page, the README table" in text
    assert "start the service, sign in, submit forms" in text
