"""What repo-factory is doing: the activity read of the build loop, and the owner's Stop of
a run."""

from __future__ import annotations

from .base import ok

ROUTES = [
    # ?subject=project:p1 &kind= &limit= &all=1 &hours=
    ("GET", "/api/activity", ok(lambda a, r: a.activity(r.data))),
    ("POST", "/api/activity/stop", ok(lambda a, r: a.activity_stop(r.data))),
]
