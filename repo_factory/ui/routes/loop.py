"""The build loop: its state and settings, and the ``repo-factory loop`` verbs the page runs."""

from __future__ import annotations

from .base import Prefix, ok

ROUTES = [
    ("GET", "/api/loop", ok(lambda a, r: a.loop_state())),
    ("GET", "/api/loop/settings", ok(lambda a, r: a.loop().cfg.data)),
    # the one settings writer: {key, value} or {key, clear: true}
    ("GET", "/api/loop/rules", ok(lambda a, r: a.loop_rules())),
    ("POST", "/api/loop/settings", ok(lambda a, r: a.set_setting(r.data))),
    ("POST", Prefix("/api/loop/"), ok(lambda a, r: a.loop_act(r.path.rsplit("/", 1)[1], r.data))),
]
