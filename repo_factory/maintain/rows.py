"""A maintained repo's rows as the pages show them: each doctor row with its Re-check's
(``recheck.overlay``), read when the page asks, so a press shows at once."""

from __future__ import annotations

from . import recheck


def overlay(home, rows: list[dict]) -> list[dict]:
    """The Re-check's state on each doctor row."""
    return recheck.overlay(home, rows)
