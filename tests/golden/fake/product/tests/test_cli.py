from wordtally.cli import count_words, main


def test_count_words():
    assert count_words("one two  three\nfour") == 4
    assert count_words("") == 0


def test_main_prints_each_file_and_a_total(tmp_path, capsys):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one two")
    b.write_text("three")
    assert main([str(a), str(b)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"2\t{a}", f"1\t{b}", "3\ttotal"]


def test_a_missing_file_is_an_error(tmp_path, capsys):
    assert main([str(tmp_path / "nope.txt")]) == 1
    assert "nope.txt" in capsys.readouterr().err
