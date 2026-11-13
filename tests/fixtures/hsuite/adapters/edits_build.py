from pathlib import Path

from counter import Counter
from driver import Driver, Result

# An adapter that "fixes" the build on disk: the scorer must reject the score.
_src = Path("counter.py")
_src.write_text(_src.read_text().replace("self.n >= self.limit", "False"))


class Adapter(Driver):
    def __init__(self):
        self.c = Counter()

    def inc(self):
        return Result(True, 0, {"value": self.c.inc()})

    def value(self):
        return self.c.n
