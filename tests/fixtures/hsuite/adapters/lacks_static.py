from counter import Counter
from driver import Driver, Result


class Adapter(Driver):
    """Declares the capability missing but implements it anyway: LACKS decides the rows."""
    LACKS = ("counter.reset",)

    def __init__(self):
        self.c = Counter()

    def inc(self):
        try:
            return Result(True, 0, {"value": self.c.inc()})
        except ValueError as e:
            return Result(False, 1, {}, str(e))

    def value(self):
        return self.c.n

    def reset(self):
        self.c.reset()
        return Result(True, 0)
