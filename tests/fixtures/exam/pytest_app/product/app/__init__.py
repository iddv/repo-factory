"""A tally of named counters."""


class Tally:
    def __init__(self):
        self.counts = {}

    def add(self, name, n=1):
        self.counts[name] = self.counts.get(name, 0) + n
        return self.counts[name]

    def total(self):
        return sum(self.counts.values())
