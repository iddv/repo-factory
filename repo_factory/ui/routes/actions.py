"""The registry's actions on tasks and builds: what a subject offers, its last presses, and
one press."""

from __future__ import annotations

from .base import ok

ROUTES = [
    ("GET", "/api/actions", ok(lambda a, r: a.actions(r.data.get("kind"), r.data.get("id")))),
    ("GET", "/api/outcomes", ok(lambda a, r: a.outcomes(r.data.get("kind"), r.data.get("id")))),
    ("POST", "/api/act", ok(lambda a, r: a.act(r.data, r.who))),
]
