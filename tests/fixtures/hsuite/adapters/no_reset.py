from counter import Counter
from driver import Driver, Result


class Adapter(Driver):
    """Doesn't implement reset: the base class raises Unsupported("counter.reset")."""

    def __init__(self):
        self.c = Counter()

    def inc(self):
        try:
            return Result(True, 0, {"value": self.c.inc()})
        except ValueError as e:
            return Result(False, 1, {}, str(e))

    def value(self):
        return self.c.n
