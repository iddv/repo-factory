from textstats.core import char_count


def test_char_count():
    assert char_count('abc') == 3
