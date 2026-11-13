from textstats import core


def test_wc_simple():
    # same body as sample 1's test_word_count_simple: a duplicate
    assert core.word_count('a b c') == 3


def test_top_words_case():
    assert core.top_words('A a b', 1) == ['a']


def test_top_words_ties():
    assert core.top_words('b a', 2) == ['a', 'b']
