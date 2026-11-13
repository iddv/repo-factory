#!/usr/bin/env python3
"""release_notes.py vX.Y.Z: the release's notes: CHANGELOG.md's section for that version, then
the install in three commands with this release's wheel address. Standard library only."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def section(changelog: str, version: str) -> str:
    m = re.search(rf"^## \[?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    return m.group(1).strip() if m else "See CHANGELOG.md."


def main(argv: list[str]) -> int:
    tag = argv[0]
    version = tag.removeprefix("v")
    repo = os.environ.get("GITHUB_REPOSITORY", "iddv/repo-factory")
    wheel = f"repo_factory-{version}-py3-none-any.whl"
    url = f"https://github.com/{repo}/releases/download/{tag}/{wheel}"
    body = section((ROOT / "CHANGELOG.md").read_text(), version)
    print(f"""{body}

## Install

```
uv tool install --python 3.12 {url}
repo-factory doctor
repo-factory ui
```

`SHA256SUMS` lists the wheel's checksum. repo-factory is an experiment: read the README first.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
