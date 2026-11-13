def test_add(tally):
    assert tally.add("a") == 1
    assert tally.add("a", 2) == 3


def test_total(tally):
    tally.add("a")
    tally.add("b", 4)
    assert tally.total() == 5
