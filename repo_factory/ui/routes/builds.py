"""Builds: the state the pages read, the header, one build's page, events and texts, a typed
task (new), and a build's own writes (start, answer, stop, verdict)."""

from __future__ import annotations

from ..http import HttpError, _json
from .base import ok, since


def _info(app, r):
    from ..api import API_VERSION, DEFAULT_POLICY, POLICIES

    return {
        "root": str(app.root),
        "harness": app.harness,
        "policies": list(POLICIES),
        "api_version": API_VERSION,
        "policy": DEFAULT_POLICY,
    }


def _text(app, r):
    q = r.data
    return (
        200,
        "text/plain; charset=utf-8",
        app.text(app.task_dir(q.get("dir")), q.get("what", ""), since(q.get("start"))).encode(),
    )


def _new(app, r):
    """A typed task (or a file's contents) into the ledger: ``{id}``; its brief comes next."""
    b = r.data
    return app.new(b.get("text") or b.get("sentence", ""))


# a build's own writes: its dir is resolved first, so any other POST without one is a 400
TASK_POSTS = {
    "/api/start": lambda app, d, b: app.start(d, b.get("policy") or None),
    "/api/answer": lambda app, d, b: app.answer(d, str(b.get("qid", "")), b.get("text") or ""),
    "/api/stop": lambda app, d, b: app.stop(d),
    "/api/verdict": lambda app, d, b: app.verdict(d, b.get("verdict", ""), b.get("note", "")),
}


def _task_post(app, r):
    d = app.task_dir(r.data.get("dir"))
    if r.path not in TASK_POSTS:
        raise HttpError(404, "not found")
    return _json(200, TASK_POSTS[r.path](app, d, r.data))


ROUTES = [
    ("GET", "/api/state", ok(lambda a, r: a.snapshot())),
    ("GET", "/api/header", ok(lambda a, r: a.header())),
    ("GET", "/api/build", ok(lambda a, r: a.build_page(a.task_dir(r.data.get("dir"))))),
    ("GET", "/api/build/runs", ok(lambda a, r: a.build_runs(a.task_dir(r.data.get("dir"))))),
    (
        "GET",
        "/api/run",
        ok(lambda a, r: a.build_run(a.task_dir(r.data.get("dir")), r.data.get("n"))),
    ),
    ("GET", "/api/info", ok(_info)),
    ("GET", "/api/builds", ok(lambda a, r: a.builds())),
    (
        "GET",
        "/api/events",
        ok(lambda a, r: a.events(a.task_dir(r.data.get("dir")), int(r.data.get("since") or 0))),
    ),
    ("GET", "/api/text", _text),
    ("POST", "/api/new", ok(_new)),
]

# last of all: any other POST is a build's own write
LAST = [("POST", lambda path: True, _task_post)]
