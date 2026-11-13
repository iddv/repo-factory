"""Details: the record behind a row or a dialog, the drawer's read (ui/details.py)."""

from __future__ import annotations

from .. import details
from .base import ok

ROUTES = [
    # ?sel=b<build key> | i<task id>
    ("GET", "/api/details", ok(lambda a, r: details.record(a, r.data))),
]
