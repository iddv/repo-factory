"""The front door: the README never installs from git (a git install has no built page),
every relative link in it resolves, and the package metadata describes the product."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


def test_no_install_from_git():
    assert not [x for x in README.splitlines() if "uv tool install" in x and "git+" in x]


def test_every_relative_link_resolves():
    links = re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", README)
    missing = [
        x
        for x in links
        if not x.startswith(("http://", "https://", "mailto:")) and not (ROOT / x).exists()
    ]
    assert missing == []


def test_the_package_says_what_it_is():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert meta["name"] == "repo-factory" and meta["description"]
    assert "POC" not in meta["description"]
    assert meta["license"] == "Apache-2.0" and meta["readme"] == "README.md"
    assert all("email" not in a for a in meta.get("authors", []))
    assert meta["scripts"]["repo-factory"]
