# rule: a file of four words prints 4 and its name
# claim: c1
# flow: F1
from _verify.driver import run


def test_c1(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("one two three four")
    code, out, _ = run(str(f))
    assert code == 0
    assert out.split("\t")[0] == "4"
