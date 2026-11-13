"""The exam's integrity (EX-PLAN §3.6): it is read-only where it runs and in a repair session,
and a change to it fails the stage."""

import pytest

from repo_factory.harness.isolation import isolation
from repo_factory.rules.model import Rule
from repo_factory.v5 import exam

from .exam_fixture import FakeRun, product


@pytest.mark.skipif(isolation() != "bwrap", reason="needs bubblewrap")
def test_a_write_under_work_exam_fails_inside_the_sandbox(tmp_path):
    run = FakeRun(tmp_path, "stdlib_unittest_lib", skip=("c1", "c2", "x1"))
    tree = product("stdlib_unittest_lib", tmp_path / "tree")
    code = (
        "import pathlib\n\n\ndef test_ro():\n"
        "    try:\n        pathlib.Path('/work/exam/_verify/x.txt').write_text('x')\n"
        "        wrote = True\n    except OSError:\n        wrote = False\n"
        "    assert not wrote\n"
    )
    r = Rule(id="ro", title="ro", kind="edge", source="", code=code, status="active")
    run.store.rules["ro"] = r
    rec = exam.run(run, tree, rules=[r])
    assert rec["scenarios"]["ro"]["status"] == "passed", rec["scenarios"]["ro"]["detail"]


def test_a_tampered_exam_fails_the_stage(tmp_path):
    from repo_factory.v5.pipeline import StageFailed

    run = FakeRun(tmp_path, "stdlib_unittest_lib")
    d = exam.visible(run)
    before = exam.digest(d)
    exam.check_unchanged(d, before, "repair")
    (d / "_verify" / "scenarios" / "test_rule_c1.py").write_text("def test_c1():\n    pass\n")
    with pytest.raises(StageFailed, match="the exam changed during the session"):
        exam.check_unchanged(d, before, "repair")


def test_a_repair_session_gets_the_exam_read_only_and_the_guard_its_flag(tmp_path):
    m = exam.session_mounts(tmp_path, rw=False, proposals=tmp_path / "p")
    assert [(x["kind"], x["dst"]) for x in m] == [
        ("ro", "/work/exam"),
        ("rw", "/work/exam-proposals"),
    ]
    assert (tmp_path / "p").is_dir()
