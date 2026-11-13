"""CI's two lanes, `-m "not slow"` and `-m slow`, run every test exactly once between them."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collected(*args: str) -> int:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    m = re.search(r"(\d+)(?:/\d+)? tests? collected", r.stdout)
    assert m, r.stdout[-500:]
    return int(m[1])


def test_the_fast_and_slow_lanes_partition_the_suite():
    everything = collected()
    slow, fast = collected("-m", "slow"), collected("-m", "not slow")
    assert slow > 0 and fast > 0
    assert slow + fast == everything
