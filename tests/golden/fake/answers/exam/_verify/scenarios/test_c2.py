# rule: two files print each count, then the total
# claim: c2
# flow: F2
from _verify.driver import run


def test_c2(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("one two")
    b.write_text("three")
    code, out, _ = run(str(a), str(b))
    assert code == 0
    assert out.splitlines()[-1] == "3\ttotal"
