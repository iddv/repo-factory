"""Maintenance (off and unlisted): the summary, lessons, maintained repos and their settings."""

from __future__ import annotations

from ..http import HttpError
from .base import Prefix, ok


def _maintain_box(app, r):
    from ..api import IDEA_ID

    iid = r.path.rsplit("/", 1)[1]
    if not IDEA_ID.fullmatch(iid):
        raise HttpError(404, "not found")
    return app.maintain_box(iid)


ROUTES = [
    ("GET", "/api/maintain/summary", ok(lambda a, r: a.maintain_summary())),
    ("GET", "/api/maintain/lessons", ok(lambda a, r: a.maintain_lessons())),
    ("GET", Prefix("/api/maintain/"), ok(_maintain_box)),
    ("POST", "/api/maintain/settings", ok(lambda a, r: a.set_maintain(r.data))),
    ("POST", "/api/maintain/act", ok(lambda a, r: a.maintain_act(r.data))),
]
