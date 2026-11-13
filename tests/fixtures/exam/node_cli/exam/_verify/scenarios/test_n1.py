# rule: the command line counts words on standard input
# expect: 3
# claim: c1
from _verify.driver import countcli


def test_n1():
    assert countcli("a b c\n") == (0, "3")
