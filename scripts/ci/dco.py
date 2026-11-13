#!/usr/bin/env python3
"""Every commit a pull request brings carries a Developer Certificate of Origin sign-off
(https://developercertificate.org/): a ``Signed-off-by: Name <email>`` line whose address is
the commit author's. ``git commit -s`` writes it.

    dco.py RANGE        e.g. BASE..HEAD

Merge commits are skipped. Prints one line per commit without a matching sign-off and exits
1; exits 0 when every commit has one. Standard library only.
"""

from __future__ import annotations

import re
import subprocess
import sys

SIGNOFF = re.compile(r"^Signed-off-by:\s*(.+?)\s*<([^>]+)>\s*$", re.MULTILINE)


def missing(rev: str, cwd: str | None = None) -> tuple[int, list[str]]:
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=%h%x00%ae%x00%s%x00%B%x01", rev],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    bad, n = [], 0
    for rec in out.split("\x01"):
        parts = rec.strip("\n").split("\x00")
        if len(parts) != 4:
            continue
        n += 1
        sha, email, subject, body = parts
        emails = {m.group(2).strip().lower() for m in SIGNOFF.finditer(body)}
        if email.strip().lower() not in emails:
            bad.append(f"{sha} {subject}")
    return n, bad


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    n, bad = missing(argv[0])
    for line in bad:
        print(f"no DCO sign-off matching its author: {line}")
    if bad:
        print(
            "Sign off each commit (git commit -s, or git rebase --signoff BASE), then push "
            "again. CONTRIBUTING.md says what the sign-off means."
        )
        return 1
    print(f"DCO: {n} commit(s) signed off")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
