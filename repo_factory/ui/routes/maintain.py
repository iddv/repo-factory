"""Maintenance, per repository: what it does and waits for, its switches, Pause, Retry, and
Build this proposal. Every POST here is the signed-in page's or the command line's (an agent's
token only runs actions), and none reads GitHub except the proposal's one issue."""

from __future__ import annotations

from .base import ok

ROUTES = [
    ("GET", "/api/maintenance", ok(lambda a, r: a.maintenance())),
    ("POST", "/api/maintain/settings", ok(lambda a, r: a.set_maintain(r.data))),
    ("POST", "/api/maintenance/switch", ok(lambda a, r: a.maintenance_switch(r.data))),
    ("POST", "/api/maintenance/merge", ok(lambda a, r: a.maintenance_merge(r.data))),
    ("POST", "/api/maintenance/pause", ok(lambda a, r: a.maintenance_pause(r.data))),
    ("POST", "/api/maintenance/retry", ok(lambda a, r: a.maintenance_retry(r.data))),
    ("POST", "/api/maintenance/proposal", ok(lambda a, r: a.maintenance_build_proposal(r.data))),
]
