from textstats import core
from textstats.core import char_count


def test_word_count_simple():
    assert core.word_count('a b c') == 3


def test_word_count_empty():
    assert core.word_count('') == 0


def test_top_words_basic():
    assert core.top_words('b a b', 1) == ['b']


def test_char_count_still_works():
    # passes on the seed: must be filtered out
    assert char_count('xy') == 2


def test_word_count_spaces():
    assert core.word_count('  a   b ') == 2
