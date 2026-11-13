"""The scenario runner's sandbox has a HOME (found by the capabilities acceptance run, 2026-10-11:
a Node CLI that calls os.homedir() failed every harness run with "uv_os_homedir returned
ENOENT", while the verifier, whose session had a HOME, saw it pass; 53 scenarios became
"environment" and no flow counted as working). Offline: pytest in the sandbox, no network."""

from __future__ import annotations

from repo_factory.harness.venv import ensure_venv, run_pytest


def test_tests_run_with_a_writable_home(tmp_path):
    src = tmp_path / "src"
    (src / "tests").mkdir(parents=True)
    (src / "tests" / "test_home.py").write_text(
        "import os\nfrom pathlib import Path\n\n\ndef test_home():\n"
        "    home = Path(os.environ['HOME'])\n"
        "    assert home.is_dir() and Path.home() == home\n"
        "    (home / '.probe').write_text('x')\n"
    )
    r = run_pytest(src, {}, ["tests"], ensure_venv([]), network=False)
    assert r["tests"] == {"tests/test_home.py::test_home": "passed"}, r.get("details")
