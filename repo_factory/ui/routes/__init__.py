"""``api.handle``'s routes, one module per area. ``ROUTES`` is every area's in turn; no two
areas' patterns overlap, so only the order within an area, and ``builds.LAST`` coming last,
decide anything (tests/test_api_routes.py pins every route). The handlers call ``api.App``; a
new area adds a module here."""

from . import (
    actions,
    activity,
    builds,
    details,
    github,
    loop,
    maintain,
    projects,
    prompts,
    settings,
    static,
)

ROUTES = [
    *static.ROUTES,
    *activity.ROUTES,
    *details.ROUTES,
    *loop.ROUTES,
    *settings.ROUTES,
    *prompts.ROUTES,
    *actions.ROUTES,
    *maintain.ROUTES,
    *github.ROUTES,
    *projects.ROUTES,
    *builds.ROUTES,
    *builds.LAST,
]
