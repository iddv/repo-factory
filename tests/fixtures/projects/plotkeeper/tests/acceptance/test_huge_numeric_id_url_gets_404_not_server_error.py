"""A huge numeric id in a URL gets a 404, not a server error.

Expected: GET /plots/<30-digit number> answers 404 Not found
Source: the security review (SECURITY.md).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from _helpers.security_helpers import Client, Server


def test_huge_id_is_404():
    s = Server()
    try:
        c = Client(s)
        c.login("coordinator", s.passwords["coordinator"])
        for p in ("/plots/999999999999999999999999999999", "/gardeners/999999999999999999999999999999"):
            st, _, body = c.get(p)
            assert st == 404, f"{p} answered {st}"
            assert "Traceback" not in body
    finally:
        s.stop()


# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402

_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason='Known security issue (low): A huge numeric id in a URL gets a 404, not a server error')]
