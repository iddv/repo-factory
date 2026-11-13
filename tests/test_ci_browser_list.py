"""No test runs in no CI job: ``fast`` skips a browser test ("the web UI is not built"), so
every browser test file is named in ``scripts/ci/browser.sh``; and a skip for a missing
import is told apart from the others, so CI can fail it (``REPO_FACTORY_NO_IMPORT_SKIPS``,
tests/conftest.py)."""

import re
from pathlib import Path

from .conftest import IMPORT_SKIP_OK, import_skip

ROOT = Path(__file__).resolve().parent.parent


def test_every_browser_test_file_runs_in_the_browser_job():
    named = set(
        re.findall(r"tests/(test_browser\w*\.py)", (ROOT / "scripts/ci/browser.sh").read_text())
    )
    files = {p.name for p in (ROOT / "tests").glob("test_browser*.py")}
    assert files - named == set(), "add these to scripts/ci/browser.sh, or they run in no CI job"
    assert named - files == set(), "scripts/ci/browser.sh names a file that is gone"


def test_a_skip_for_a_missing_import_is_told_apart():
    # pytest's skip report: (path, line, "Skipped: could not import 'x': No module named 'x'")
    assert import_skip(("t.py", 6, "Skipped: could not import 'yaml': No module named 'yaml'"))
    assert import_skip(("t.py", 6, "Skipped: the web UI is not built")) is None
    assert import_skip("Skipped: could not import 'x'")
    assert IMPORT_SKIP_OK == {}  # no exception: every missing import fails CI
