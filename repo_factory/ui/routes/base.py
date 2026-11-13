"""The route table's parts: a route is ``(method, pattern, handler)``, tried in
order; the first match answers, else 404 (tests/test_api_routes.py pins every route). A pattern
is a path, a tuple of paths, a ``Prefix``, or a predicate on the path; the method ``"*"``
matches any. A handler takes the app and the request (``Req``: method, path, data, who) and
returns ``(status, content type, body)``; data is the query for a GET and the JSON body for a
POST, who the caller's identity."""

from __future__ import annotations

import json
from typing import NamedTuple

from ..http import HttpError, _json


class Req(NamedTuple):
    method: str
    path: str
    data: dict
    who: dict | None = None


class Prefix(str):
    """A pattern that matches every path that starts with it."""


def _matches(pattern, path: str) -> bool:
    if isinstance(pattern, Prefix):
        return path.startswith(pattern)
    if isinstance(pattern, str):
        return path == pattern
    if isinstance(pattern, tuple):
        return path in pattern
    return bool(pattern(path))


def route(routes: list, app, r: Req) -> tuple[int, str, bytes]:
    for method, pattern, handler in routes:
        if method in (r.method, "*") and _matches(pattern, r.path):
            return handler(app, r)
    raise HttpError(404, "not found")


def ok(fn):
    """A handler that answers 200 with what ``fn(app, request)`` returns, as JSON."""
    return lambda app, r: _json(200, fn(app, r))


def refuse(code: int, why: str):
    def handler(app, r):
        raise HttpError(code, why)

    return handler


def jsonish(v):
    """A query value that may be JSON (a number, true/false, a list), else the text."""
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def since(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        raise HttpError(400, "since must be a number")
