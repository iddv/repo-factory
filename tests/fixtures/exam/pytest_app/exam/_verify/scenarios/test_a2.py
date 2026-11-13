# rule: a negative amount is refused
# claim: c2
import pytest
from _verify.driver import tally


def test_a2():
    with pytest.raises(ValueError):
        tally().add("x", -1)
