"""Why a build failed, in one line, from its ``build.log`` (UI-v4.md 2.2 and 2.5; CLICKS S13).

``fail_line(path)`` returns ``{"stage", "line", "offset"}``: the last error line the build
printed, the v5 stage it was printed in (the last ``[stage]`` marker before it, the form
``repo-factory build``'s progress lines take), and its 1-based line number in the file, which the
build page's log viewer highlights. ``None`` when nothing in the log reads as an error.

What counts as an error line, best first: a failed test (pytest's ``FAILED``/``ERROR``
summary lines), the exception that ended a traceback, a non-zero exit, then any line that
says error or failed. The last one of the best kind wins: that is the one the build ended on.
"""

from __future__ import annotations

import re
from pathlib import Path

STAGE = re.compile(r"\[([a-z][a-z0-9_-]{1,30})\]\s*$")
PYTEST = re.compile(r"^(FAILED|ERROR) \S")
EXCEPTION = re.compile(
    r"^([A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Failure)|AssertionError)\b(:.*)?$"
)
EXIT = re.compile(
    r"(exit(ed)? (with )?(code|status) [1-9]\d*|non-zero exit status \d+|"
    r"returned [1-9]\d* exit|exit code [1-9]\d*)",
    re.I,
)
ANY = re.compile(r"\b(error|failed|failure|fatal)\b|✗", re.I)
MAX_BYTES = 2_000_000  # the tail is enough: a build ends where it failed


def _lines(path: Path) -> tuple[list[str], int]:
    """The file's lines (those in its last MAX_BYTES) and the line number of the first."""
    start = max(0, path.stat().st_size - MAX_BYTES)
    newlines = 0
    with open(path, "rb") as f:
        while f.tell() < start:
            chunk = f.read(min(1 << 16, start - f.tell()))
            if not chunk:
                break
            newlines += chunk.count(b"\n")
        data = f.read().decode(errors="replace")
    lines = data.split("\n")
    if not start:
        return lines, 1
    return lines[1:], newlines + 2  # the line cut at ``start`` is left out


def parse(lines: list[str], first: int = 1) -> dict | None:
    """The error line of ``lines`` (numbered from ``first``), as ``fail_line`` returns it."""
    found: dict[int, tuple[int, str]] = {}  # rank -> (index, text): the last of each kind
    in_trace = False
    for n, raw in enumerate(lines):
        text = raw.rstrip()
        s = text.strip()
        if not s:
            continue
        if s.startswith("Traceback (most recent call last)"):
            in_trace = True
            continue
        if PYTEST.match(s):
            found[0] = (n, s)
        elif in_trace and not raw[:1].isspace() and EXCEPTION.match(s):
            found[1] = (n, s)
            in_trace = False
        elif EXIT.search(s):
            found[2] = (n, s)
        elif ANY.search(s) and not s.startswith(("✓", "·")):
            found[3] = (n, s)
    if not found:
        return None
    n, text = found[min(found)]
    stage = None
    for raw in reversed(lines[: n + 1]):
        m = STAGE.search(raw.rstrip())
        if m:
            stage = m.group(1)
            break
    return {"stage": stage, "line": text[:400], "offset": first + n}


def fail_line(path: str | Path) -> dict | None:
    p = Path(path)
    try:
        lines, first = _lines(p)
    except OSError:
        return None
    return parse(lines, first)


def window(path: str | Path, start: int, n: int = 200) -> str:
    """Lines ``start`` to ``start + n - 1`` (1-based) of the file, for the log viewer that
    highlights a fail line; empty past the end."""
    out = []
    try:
        with open(path, errors="replace") as f:
            for i, line in enumerate(f, 1):
                if i >= start + n:
                    break
                if i >= start:
                    out.append(line.rstrip("\n"))
    except OSError:
        return ""
    return "\n".join(out)
