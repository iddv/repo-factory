"""A Node command-line product is not held for the README check's own limits: the check writes
a launcher for each ``package.json`` ``bin`` (as it does for Python's console scripts) when it
skips the README's global install; a command not found right after an install step it skipped,
and a test command that needs a download, are not measured (a gap), never a hold. The shape
is a real build's: an install line, then its command; a ``pretest`` that downloads."""

from __future__ import annotations

import json
from pathlib import Path

from repo_factory.loop import gate
from repo_factory.v5 import commands

README = """# notefold

Turns a folder of notes into one page.

## Install

```
npm install -g --install-links .
notefold demo my-demo
```

## Tests

```
npm test
```
"""


def product(root: Path) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "notefold.js").write_text("#!/usr/bin/env node\nconsole.log('ok')\n")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "notefold",
                "version": "0.1.0",
                "bin": {"notefold": "./bin/notefold.js"},
                "scripts": {"pretest": "playwright install chromium", "test": "node --test"},
            }
        )
    )
    (root / "README.md").write_text(README)
    return root


def test_the_global_install_of_the_product_is_known():
    for cmd in ("npm install -g --install-links .", "npm i -g .", "npm link", "yarn global add ."):
        assert commands.installs_product(cmd), cmd
    for cmd in ("npm install", "npm ci", "npm install -g some-tool"):
        assert not commands.installs_product(cmd), cmd


def test_a_launcher_is_written_for_each_package_bin(tmp_path):
    repo = product(tmp_path / "repo")
    assert commands.node_bins(repo) == {"notefold": "bin/notefold.js"}
    names = commands.write_scripts(repo, tmp_path / "bin", "/usr/bin/python3")
    assert names == ["notefold"]
    text = (tmp_path / "bin" / "notefold").read_text()
    assert text.startswith("#!/bin/sh\nexec node ") and "bin/notefold.js" in text
    # a bin that leaves the copy is not linked
    (repo / "package.json").write_text(json.dumps({"name": "x", "bin": "../outside.js"}))
    assert commands.node_bins(repo) == {}


def result(repo: Path, quick_out: str, test_out: str, rc: int = 127) -> dict:
    ex = commands.extract(repo)
    return {
        "ran": True,
        "extract": ex,
        "quick": [
            {
                "cmd": "notefold demo my-demo",
                "where": "README.md:11",
                "status": "failed",
                "rc": rc,
                "output": quick_out,
            }
        ],
        "tests": [
            {
                "cmd": "npm test",
                "where": "README.md:17",
                "status": "failed",
                "rc": 1,
                "output": test_out,
            }
        ],
        "restart": None,
    }


def test_a_foldpage_shaped_build_ends_with_no_false_hold(tmp_path):
    repo = product(tmp_path / "repo")
    res = result(
        repo,
        "sh: 1: notefold: not found",
        "> pretest\n> playwright install chromium\nError: getaddrinfo ENOTFOUND cdn.playwright.dev",
    )
    it = commands.items(res, restart=False)
    assert it["commands"]["status"] != "red", it["commands"]["why"]
    assert "need the install step the check skipped" in it["commands"]["why"]
    t = it["tests_green"]
    assert t["status"] == "amber" and t.get("not_verifiable"), t["why"]
    assert t["why"].startswith("not measured: `npm test` needs a download")
    # the gate: neither is a hold, both are said as gaps
    rd = tmp_path / "round"
    (rd / "exam" / "evidence").mkdir(parents=True)
    for n in ("security.md", "readiness.md"):
        (rd / "exam" / "evidence" / n).write_text("x")
    (rd / "readiness.json").write_text(
        json.dumps(
            {
                "security": {"ran": True, "findings": []},
                "final": {"items": [{**v, "id": k} for k, v in it.items()]},
            }
        )
    )
    checks = gate.record_checks(rd)
    assert checks["tests"]["ok"] and "not measured" in checks["tests"]["gap"]
    assert checks["safe_defaults"]["ok"], checks["safe_defaults"]


def test_a_real_failure_after_the_install_still_holds(tmp_path):
    repo = product(tmp_path / "repo")
    res = result(repo, "TypeError: cannot read properties of undefined", "1 failing test", rc=1)
    it = commands.items(res, restart=False)
    assert it["commands"]["status"] == "red"
    assert it["tests_green"]["status"] == "red" and not it["tests_green"].get("not_verifiable")
