from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    ok: bool
    status: int | None
    body: dict = field(default_factory=dict)
    error: str | None = None
    raw: Any = None


class Unsupported(Exception):
    pass


class Refused(Exception):
    pass


class Driver:
    REQUIRED = ("word_count", "top_words")
    OPTIONAL = ("configure",)
    LACKS: tuple = ()
    FUZZ = {"text": ["a b", "a  b", " a ", "", "B a b"], "n": [0, 1, 2]}

    def word_count(self, text: str) -> Result:
        """body: value (int)"""
        raise NotImplementedError

    def top_words(self, text: str, n: int) -> Result:
        """body: value (list of words)"""
        raise NotImplementedError

    def configure(self, rule_id: str, value) -> None:
        raise Unsupported(f"config.{rule_id}")
