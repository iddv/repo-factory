# rule: a missing file is named on stderr, exit code 1
# claim: c3
# flow: F3
from _verify.driver import run


def test_c3(tmp_path):
    code, _, err = run(str(tmp_path / "missing.txt"))
    assert code == 1
    assert "missing.txt" in err
