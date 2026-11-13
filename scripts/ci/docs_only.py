#!/usr/bin/env python3
"""docs_only.py BASE HEAD: print ``code=true`` when the change touches anything but
documentation, else ``code=false`` (CI's `changes` job: a docs-only pull request runs `lint`
and `gitleaks` only). Documentation is Markdown outside the package, the root papers, the
issue forms and images under ``docs/``. Standard library only."""

from __future__ import annotations

import subprocess
import sys

DOC_SUFFIXES = (".md", ".png", ".jpg", ".jpeg", ".gif", ".webp")
DOC_FILES = {"LICENSE", "NOTICE", ".github/CODEOWNERS", ".github/pull_request_template.md"}


def is_doc(path: str) -> bool:
    if path in DOC_FILES or path.startswith(".github/ISSUE_TEMPLATE/"):
        return True
    if path.startswith("repo_factory/"):  # the page's help is code: the page shows it
        return False
    return path.endswith(DOC_SUFFIXES)


def main(argv: list[str]) -> int:
    base, head = argv
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    code = any(not is_doc(p) for p in out) or not out
    print(f"code={'true' if code else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
