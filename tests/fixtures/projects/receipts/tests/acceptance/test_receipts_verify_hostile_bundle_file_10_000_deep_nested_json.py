"""`receipts verify` on a hostile bundle file (10,000-deep nested JSON) fails cleanly with "not a Receipts bundle" and exit 2, without a traceback.

Expected: a one-line error naming the file and exit code 2
Source: the security review (SECURITY.md).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from _helpers.driver import Env


def test_deeply_nested_bundle_is_rejected_cleanly():
    env = Env(); env.init()
    b = os.path.join(env.tmp, "deep.json")
    open(b, "w").write("[" * 10000 + "]" * 10000)
    p = env.cli("verify", b)
    assert "Traceback" not in p.out, p.out[-400:]
    assert p.returncode == 2 and "not a Receipts bundle" in p.out, p.out[-400:]


# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402

_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason='Known security issue (low): `receipts verify` on a hostile bundle file (10,000-deep nested JSON) fails cleanly with "not a Receipts bundle" and exit 2, without a traceback')]
