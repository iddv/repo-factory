# rule: a hyphenated word counts twice
# expect: 2
# claim: explore
from _verify.driver import count


def test_x1():
    assert count("well-known") == 2
