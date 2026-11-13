"""Projects (off and unlisted), their settings, and the change ideas."""

from __future__ import annotations

from .base import jsonish, ok

ROUTES = [
    ("GET", "/api/projects", ok(lambda a, r: a.projects())),
    ("GET", "/api/project", ok(lambda a, r: a.project(str(r.data.get("id") or "")))),
    ("GET", "/api/projects/settings", ok(lambda a, r: a.projects_settings())),
    # Ideas' Changes chip: every change idea; how many ideas would flow with a setting
    ("GET", "/api/changes", ok(lambda a, r: a.changes())),
    (
        "GET",
        "/api/projects/preview",
        ok(
            lambda a, r: a.projects_preview(
                str(r.data.get("key") or ""),
                jsonish(r.data.get("value")),
                r.data.get("project") or None,
            )
        ),
    ),
    # a charter version's text, the versions, the diff
    (
        "GET",
        "/api/project/charter",
        ok(lambda a, r: a.project_charter(str(r.data.get("id") or ""), r.data.get("v"))),
    ),
    # factory-wide, or one project's override
    ("POST", "/api/projects/settings", ok(lambda a, r: a.set_projects_settings(r.data))),
]
