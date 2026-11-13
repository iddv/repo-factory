"""Settings › Prompts (``custom_prompts``): the build's prompts, house rules, additions, full
overrides with versions, and preview."""

from __future__ import annotations

import re

from ..http import HttpError
from .base import Prefix, ok, refuse, route

PROMPT_PATH = re.compile(
    r"/api/prompts/(repo_factory)/([a-z_]{1,40})"
    r"(?:/(reset|restore)|/versions/(\d{1,6})(/note)?)?"
)
HOUSE_VERSION = re.compile(r"/api/prompts/house_rules/versions/(\d{1,6})(/note)?")


def _cp():
    from ... import custom_prompts as cp

    return cp


def _prompts(app, r) -> tuple[int, str, bytes]:
    cp = _cp()
    try:
        return route(PROMPT_ROUTES, app, r)
    except cp.PromptError as e:
        raise HttpError(e.code, str(e))


def _house_rules_version(app, r):
    m = HOUSE_VERSION.fullmatch(r.path)
    if bool(m.group(2)) != (r.method == "POST"):
        raise HttpError(405, "POST only" if m.group(2) else "GET only")
    n = int(m.group(1))
    return _cp().set_house_rules_note(n, r.data) if m.group(2) else _cp().house_rules_version(n)


def _prompt(app, r):
    m = PROMPT_PATH.fullmatch(r.path)
    repo, pid, act, n, note = m.group(1), m.group(2), m.group(3) or "", m.group(4), bool(m.group(5))
    if n is not None:
        if note != (r.method == "POST"):
            raise HttpError(405, "POST only" if note else "GET only")
        return app.prompt_version(repo, pid, int(n), r.data if note else None)
    if r.method == "GET":
        if act:
            raise HttpError(405, "POST only")
        return app.prompt_get(repo, pid)
    return app.prompt_post(repo, pid, r.data, act)


PROMPT_ROUTES = [
    ("GET", ("/api/prompts", "/api/prompts/"), ok(lambda a, r: a.prompts())),
    ("*", ("/api/prompts", "/api/prompts/"), refuse(405, "GET only")),
    ("GET", "/api/prompts/house_rules", ok(lambda a, r: _cp().house_rules_view())),
    (
        "*",
        "/api/prompts/house_rules",
        ok(lambda a, r: _cp().set_house_rules(r.data.get("text"), note=r.data.get("note"))),
    ),
    (
        "POST",
        "/api/prompts/house_rules/restore",
        ok(lambda a, r: _cp().restore_house_rules(r.data)),
    ),
    ("*", "/api/prompts/house_rules/restore", refuse(405, "POST only")),
    ("*", HOUSE_VERSION.fullmatch, ok(_house_rules_version)),
    (
        "POST",
        "/api/prompts/license",
        ok(lambda a, r: _cp().set_license(r.data.get("license"), a.loop_home)),
    ),
    ("POST", "/api/prompts/preview", ok(lambda a, r: a.prompt_preview(r.data))),
    ("*", PROMPT_PATH.fullmatch, ok(_prompt)),
]

ROUTES = [
    ("GET", Prefix("/api/prompts"), _prompts),
    ("POST", Prefix("/api/prompts"), _prompts),
]
