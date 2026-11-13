import pytest
from app import Tally


@pytest.fixture
def tally():
    return Tally()
