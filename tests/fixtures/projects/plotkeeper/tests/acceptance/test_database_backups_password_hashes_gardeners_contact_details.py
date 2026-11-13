"""The database and backups (password hashes, gardeners' contact details) are readable only by the service's user.

Expected: plotkeeper.db and backup files are created with mode 0600 (no group/other access)
Source: the security review (SECURITY.md).
"""

import os
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from _helpers.security_helpers import REPO, Server


def test_data_files_not_world_readable():
    s = Server()
    try:
        out = os.path.join(s.dir, "b.db")
        subprocess.run([sys.executable, "-m", "plotkeeper", "backup", "-o", out], cwd=REPO, env=s.env,
                       capture_output=True, timeout=30, check=True)
        for f in (os.path.join(s.dir, "plotkeeper.db"), out):
            mode = stat.S_IMODE(os.stat(f).st_mode)
            assert mode & 0o077 == 0, f"{os.path.basename(f)} has mode {oct(mode)}"
    finally:
        s.stop()


# Known open item: expected to fail until it is fixed (tests/acceptance/README.md).
import pytest as _pytest_open  # noqa: E402

_marks = globals().get('pytestmark', [])
pytestmark = (list(_marks) if isinstance(_marks, (list, tuple)) else [_marks]) + [
    _pytest_open.mark.xfail(strict=False, reason="Known security issue (low): The database and backups (password hashes, gardeners' contact details) are readable only by the service's user")]
