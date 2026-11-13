# rule: an empty text has no words
# expect: 0
# claim: c2
from _verify.driver import count


def test_c2():
    assert count("") == 0
