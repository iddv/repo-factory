"""A toy build for the hidden-suite tests: a counter that refuses to go past its limit."""


class Counter:
    def __init__(self, limit: int = 3):
        self.n = 0
        self.limit = limit

    def inc(self) -> int:
        if self.n >= self.limit:
            raise ValueError("limit reached")
        self.n += 1
        return self.n

    def reset(self) -> None:
        self.n = 0
