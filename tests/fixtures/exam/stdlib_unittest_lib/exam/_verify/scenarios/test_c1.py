# rule: three words count as three
# expect: 3
# claim: c1
from _verify.driver import count


def test_c1():
    assert count("a b c") == 3
