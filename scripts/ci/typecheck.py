#!/usr/bin/env python3
"""The type check, with a ratchet (RCA 2026-10-23, A2; repo-factory-notes plans/CTR-DESIGN.md §3.2).

Runs mypy with ``pyproject.toml``'s ``[tool.mypy]`` table and compares each file's error count
with ``scripts/ci/typecheck_baseline.json`` (the counts on the day the check landed, lowered
since):

- a file whose count **rose** above its baseline (a file not in the baseline has 0) fails the
  check, and its new errors are printed;
- a file whose count **fell** is fine: the lower baseline is printed (and written with
  ``--update``) for the pull request to commit, so the count can only go down;
- a module made strict in ``pyproject.toml`` starts at 0 and stays there.

Usage:
  typecheck.py             check (exit 1 when any file's count rose)
  typecheck.py --update    check, then write the baseline with today's counts (only lowers it,
                           unless --reset)
  typecheck.py --reset     write the baseline from today's counts, rises included (the day the
                           check lands, or a mypy upgrade; say so in the pull request)

Standard library only; mypy is the one in the venv next to this interpreter (``uv sync --extra
dev``).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE = Path(__file__).resolve().parent / "typecheck_baseline.json"
LINE = re.compile(r"^(?P<path>[^:\s]+\.py):(?P<line>\d+): error: (?P<words>.*)$")


def run_mypy(root: Path = ROOT) -> tuple[list[str], str]:
    """mypy's error lines (path:line: error: ...) and its whole output."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-pretty",
            "--no-error-summary",
            "--show-error-codes",
            "--no-color-output",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    if proc.returncode not in (0, 1):  # 2: mypy itself failed (a config error, a crash)
        raise SystemExit(f"mypy did not run (exit {proc.returncode}):\n{out}")
    return [ln for ln in out.splitlines() if LINE.match(ln)], out


def counts(lines: list[str]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for ln in lines:
        m = LINE.match(ln)
        if m:
            c[m.group("path")] += 1
    return dict(sorted(c.items()))


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {str(k): int(v) for k, v in data.get("files", {}).items()}


def write_baseline(files: dict[str, int], path: Path = BASELINE) -> None:
    body = {
        "about": "mypy errors per file; a count may only fall (scripts/ci/typecheck.py)",
        "total": sum(files.values()),
        "files": dict(sorted((k, v) for k, v in files.items() if v > 0)),
    }
    path.write_text(json.dumps(body, indent=1, sort_keys=False) + "\n")


def compare(
    now: dict[str, int], base: dict[str, int]
) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
    """(files whose count rose: (baseline, now), files whose count fell: (baseline, now))."""
    rose = {f: (base.get(f, 0), n) for f, n in now.items() if n > base.get(f, 0)}
    fell = {f: (b, now.get(f, 0)) for f, b in base.items() if now.get(f, 0) < b}
    return rose, fell


def lowered(now: dict[str, int], base: dict[str, int]) -> dict[str, int]:
    """The baseline with every fallen count lowered and nothing raised."""
    return {f: min(b, now.get(f, 0)) for f, b in base.items() if min(b, now.get(f, 0)) > 0}


def main(argv: list[str]) -> int:
    lines, _ = run_mypy()
    now = counts(lines)
    base = load_baseline()
    if "--reset" in argv:
        write_baseline(now)
        print(f"typecheck: baseline written: {sum(now.values())} errors in {len(now)} files")
        return 0
    rose, fell = compare(now, base)
    print(
        f"typecheck: {sum(now.values())} mypy errors in {len(now)} files "
        f"(baseline {sum(base.values())})"
    )
    for f, (b, n) in sorted(rose.items()):
        print(f"::error file={f}::mypy errors in {f} rose from {b} to {n}")
        for ln in lines:
            if ln.startswith(f + ":"):
                print(f"  {ln}")
    if fell:
        new = lowered(now, base)
        for f, (b, n) in sorted(fell.items()):
            print(f"  {f}: {b} -> {n} (fell; commit the lower baseline)")
        if "--update" in argv:
            write_baseline(new)
            print(
                f"typecheck: baseline lowered to {sum(new.values())}: commit "
                f"{BASELINE.relative_to(ROOT)}"
            )
        else:
            print(
                "typecheck: run `python scripts/ci/typecheck.py --update` and commit "
                f"{BASELINE.relative_to(ROOT)} to keep the lower counts"
            )
    if rose:
        print(
            "typecheck: FAILED: fix the new errors above (a count may only fall; a strict "
            "module's is 0)"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
