# rule: adding to a counter returns the new count
# claim: c1
from _verify.driver import tally


def test_a1():
    t = tally()
    assert t.add("x", 2) == 2
