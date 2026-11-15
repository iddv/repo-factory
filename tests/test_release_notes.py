"""The release's notes: CHANGELOG.md's section for the tag's version, with links to the
repository's files made absolute (a relative link leads nowhere on the Releases page), then the
install in three commands with that release's wheel."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_notes", ROOT / "scripts" / "ci" / "release_notes.py"
)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

LOG = """# Changelog

## Unreleased

Nothing yet.

## 0.2.0

- New: see [SECURITY.md](SECURITY.md), [the steps](docs/RELEASING.md#the-tag),
  [a site](https://example.org/x) and [this part](#top).

## 0.1.0 — the first release

- The first one.
"""


def test_the_section_of_the_tags_version_and_no_other():
    assert notes.section(LOG, "0.2.0").startswith("- New: see")
    assert "The first one." not in notes.section(LOG, "0.2.0")
    assert notes.section(LOG, "0.1.0") == "- The first one."
    assert notes.section(LOG, "9.9.9") == "See CHANGELOG.md."


def test_links_to_the_repositorys_files_point_at_the_tag():
    body = notes.absolute(notes.section(LOG, "0.2.0"), "owner/name", "v0.2.0")
    assert "](https://github.com/owner/name/blob/v0.2.0/SECURITY.md)" in body
    assert "](https://github.com/owner/name/blob/v0.2.0/docs/RELEASING.md#the-tag)" in body
    assert "](https://example.org/x)" in body and "](#top)" in body


def test_the_readme_installs_the_newest_released_version():
    """The README's first install line is the wheel of the newest version in CHANGELOG.md, at
    the address the release workflow uploads it to (a release PR moves both together)."""
    newest = re.search(r"^## \[?(\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert newest
    v = newest.group(1)
    readme = (ROOT / "README.md").read_text()
    want = f"releases/download/v{v}/repo_factory-{v}-py3-none-any.whl"
    first = next(x for x in readme.splitlines() if x.startswith("uv tool install"))
    assert want in first, first


def test_the_changelog_has_a_section_for_the_version_the_steps_name(capsys, monkeypatch):
    steps = (ROOT / "docs" / "RELEASING.md").read_text()
    tag = re.search(r"\b(v\d+\.\d+\.\d+)\b", steps)
    assert tag, "docs/RELEASING.md names the tag"
    version = tag.group(1).removeprefix("v")
    assert notes.section((ROOT / "CHANGELOG.md").read_text(), version) != "See CHANGELOG.md."
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    assert notes.main([tag.group(1)]) == 0
    out = capsys.readouterr().out
    wheel = f"repo_factory-{version}-py3-none-any.whl"
    assert f"releases/download/{tag.group(1)}/{wheel}" in out
    assert "](docs/" not in out and "](SECURITY.md)" not in out
