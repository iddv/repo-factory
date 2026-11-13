"""What the product ships (EX-PLAN §4, PR 2): nothing of repo-factory's in the tree, the evidence
beside it, adoption of the fixes checked from git, and the files earlier versions of repo-factory
wrote into a tree recognised and moved out."""

import json
import subprocess
import tarfile

from repo_factory.v5 import exam

from .exam_fixture import FakeRun, product

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def git(ws, *a):
    return subprocess.run([*GIT, *a], cwd=ws, check=True, capture_output=True, text=True).stdout


def commit(ws, msg, files: dict):
    for rel, text in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", msg)


def test_adoption_every_fix_comes_with_a_test_of_the_products_own(tmp_path):
    ws = product("pytest_app", tmp_path / "ws")
    git(ws, "init", "-q")
    commit(ws, "v5: the base build", {})
    run = FakeRun(tmp_path / "run", "pytest_app")
    run.ws = ws
    commit(ws, "fix(c1): a negative amount is refused", {"app/__init__.py": "# refused\n"})
    commit(
        ws,
        "fix(c2,s1): totals and a setting",
        {"app/__init__.py": "# totals\n", "tests/test_totals.py": "def test_t(): pass\n"},
    )
    a = exam.adoption(run)
    assert a["mode"] == "fixes" and sorted(a["fixed"]) == ["c1", "c2", "s1"]
    assert a["missing"] == ["c1"] and sorted(a["with"]) == ["c2", "s1"]
    # the readiness repair adds the missing test in a commit naming the finding
    commit(ws, "fix(c1): a test of my own", {"tests/test_negative.py": "def test_n(): pass\n"})
    assert exam.adoption(run)["missing"] == []
    # a reverted fix is no longer counted
    commit(ws, "fix(c9): a guess", {"app/__init__.py": "# guess\n"})
    commit(ws, "revert(c9): undo", {"app/__init__.py": "# totals\n"})
    assert "c9" not in exam.adoption(run)["fixed"]
    run.rules["exam"] = {"adopt": "off"}
    assert exam.adoption(run)["fixed"] == []
    for path, want in (
        ("tests/test_a.py", True),
        ("src/foo_test.go", True),
        ("web/app.spec.ts", True),
        ("__tests__/x.js", True),
        ("app/core.py", False),
        ("docs/testing.md", False),
    ):
        assert exam.is_test_path(path) is want, path


def test_what_earlier_versions_wrote_into_a_tree_moves_out(tmp_path):
    root = product("stdlib_unittest_lib", tmp_path / "tree")
    acc = root / "tests" / "acceptance"
    (acc / "_helpers").mkdir(parents=True)
    (acc / "conftest.py").write_text(
        '"""Acceptance tests: run from the repository root with\n'
        '`python -m pytest tests/acceptance` (see README.md here)."""\n'
    )
    (acc / "test_counts.py").write_text("def test_x():\n    pass\n")
    (root / "READINESS.md").write_text(
        "# Readiness\n\nWhere Wordlib stands on the way to production: ...\n"
    )
    (root / "ASSUMPTIONS.md").write_text(
        "# Assumptions\n\nThe specification leaves these questions open. ...\n"
    )
    (root / "SECURITY.md").write_text(
        "# Security\n\nHow Wordlib was reviewed for security before its first release, ...\n"
    )
    readme = root / "README.md"
    # the README's own test section also ran the suite (Repo Recall's did)
    readme.write_text(
        readme.read_text().rstrip() + "\n\n```sh\npython3 -m unittest discover "
        "-s tests\npython3 -m pytest tests/acceptance\n```\n\nIf it is there, "
        "`python3 -m pytest tests/acceptance` runs the scenarios too.\n"
    )
    readme.write_text(
        readme.read_text().rstrip() + "\n\n## Acceptance tests\n\nEnd-to-end "
        "checks of the product's behaviour are in `tests/acceptance/` (they need "
        "pytest):\n\n    python -m pytest tests/acceptance\n\nAll of them pass "
        "on this version.\n"
    )
    assert exam.legacy_files(root) == [
        "tests/acceptance",
        "READINESS.md",
        "ASSUMPTIONS.md",
        "SECURITY.md",
    ]
    gone = exam.strip_legacy(root)
    assert "README.md (its acceptance-tests section)" in gone
    for rel in ("tests/acceptance", "READINESS.md", "ASSUMPTIONS.md", "SECURITY.md"):
        assert not (root / rel).exists(), rel
    assert "Acceptance tests" not in readme.read_text() and "## Tests" in readme.read_text()
    assert "README.md (its commands for the acceptance suite)" in gone
    assert "pytest tests/acceptance" not in readme.read_text()
    assert "python3 -m unittest discover -s tests\n```" in readme.read_text()
    assert (root / "tests" / "test_wordlib.py").exists()  # the product's own stay


def test_a_products_own_security_md_and_suite_are_left_alone(tmp_path):
    root = product("stdlib_unittest_lib", tmp_path / "tree")
    (root / "SECURITY.md").write_text("# Security\n\nReport problems privately.\n")
    (root / "tests" / "acceptance").mkdir()
    (root / "tests" / "acceptance" / "test_mine.py").write_text("def test_m(): pass\n")
    assert exam.legacy_files(root) == [] and exam.strip_legacy(root) == []


def test_the_evidence_and_its_tarball(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    exam.materialise(run)
    rec = {"tree": "t1", "key": "k1", "scenarios": {"c1": {"status": "passed"}}}
    ev = exam.evidence(
        run,
        rec,
        {"readiness.md": "# Readiness\n", "security.md": "# S\n", "assumptions.md": "# A\n"},
        "Wordlib",
    )
    for name in (
        "README.md",
        "results.json",
        "readiness.md",
        "security.md",
        "assumptions.md",
        "manifest.json",
        "exam/_verify/driver.py",
        "exam/_verify/scenarios/test_rule_c1.py",
    ):
        assert (ev / name).is_file(), name
    assert "REPO_FACTORY_PRODUCT_DIR=/path/to/checkout" in (ev / "README.md").read_text()
    exam.set_commit(run, "abc123")
    man = json.loads((ev / "manifest.json").read_text())
    assert man["commit"] == "abc123" and man["tree"] == "t1" and man["exam_hash"]
    tb = exam.evidence_tarball(ev, tmp_path / "w-0.1.0-checks.tar.gz", "w-0.1.0")
    tb2 = exam.evidence_tarball(ev, tmp_path / "again.tar.gz", "w-0.1.0")
    with tarfile.open(tb) as t:
        names = t.getnames()
        assert all(m.uid == 0 and m.mtime == 0 for m in t.getmembers())
    assert "w-0.1.0-checks/results.json" in names
    with tarfile.open(tb2) as t:
        assert t.getnames() == names  # reproducible order
