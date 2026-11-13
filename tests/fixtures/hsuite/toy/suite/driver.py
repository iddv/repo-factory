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


class Driver:
    REQUIRED = ("inc", "value")
    OPTIONAL = ("reset",)
    LACKS: tuple = ()

    def inc(self) -> Result:
        raise NotImplementedError

    def value(self) -> int:
        raise NotImplementedError

    def reset(self) -> Result:
        raise Unsupported("counter.reset")
